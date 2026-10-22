from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_loop import AgentLoop
from event_bus import EventBus
from events import ErrorData, EventType, make_event
from permission_gate import PermissionGate
from proxy_client import ChatResult


class OfflineProxy:
    """Respuesta local para validar API/eventos sin credenciales de IA."""

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ChatResult:
        return ChatResult(
            text="El backend local está conectado. Configura PROXY_URL para activar el modelo.",
            tool_calls=[], tokens_used=0,
        )


class PermissionBroker:
    def __init__(self, timeout_seconds: int = 120):
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._pending: dict[str, threading.Event] = {}
        self._answers: dict[str, bool] = {}

    def register(self, tool_call_id: str) -> None:
        with self._lock:
            self._pending[tool_call_id] = threading.Event()

    def resolve(self, tool_call_id: str, approved: bool) -> bool:
        with self._lock:
            pending = self._pending.get(tool_call_id)
            if pending is None or pending.is_set():
                return False
            self._answers[tool_call_id] = approved
            pending.set()
            return True

    def confirm(self, tool_name: str, arguments: dict, risk_level, tool_call_id: str) -> bool:
        with self._lock:
            pending = self._pending.get(tool_call_id)
        if pending is None:
            return False
        if not pending.wait(self.timeout_seconds):
            with self._lock:
                self._pending.pop(tool_call_id, None)
                self._answers.pop(tool_call_id, None)
            return False
        with self._lock:
            approved = self._answers.pop(tool_call_id, False)
            self._pending.pop(tool_call_id, None)
        return approved


@dataclass
class TaskRecord:
    task_id: str
    owner_id: int
    session_id: str
    broker: PermissionBroker
    events: queue.Queue = field(default_factory=queue.Queue)
    state: str = "queued"


class TaskManager:
    def __init__(self, proxy_client=None):
        self.proxy_client = proxy_client or OfflineProxy()
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskRecord] = {}

    def start(self, owner_id: int, project_root: Path, message: str) -> TaskRecord:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        record = TaskRecord(task_id, owner_id, f"session_{uuid.uuid4().hex[:12]}", PermissionBroker())
        with self._lock:
            self._tasks[task_id] = record

        def run() -> None:
            with self._lock:
                record.state = "running"
            bus = EventBus()
            gate = _TaskPermissionGate(record.broker)

            def publish(event) -> None:
                if event.type == EventType.PERMISSION_REQUEST:
                    record.broker.register(event.data["tool_call_id"])
                    gate.bind_call_id(event.data["tool_call_id"])
                if event.type == EventType.DONE:
                    with self._lock:
                        record.state = event.data["status"]
                record.events.put(event.model_dump(mode="json"))

            bus.subscribe(publish)
            loop = AgentLoop(self.proxy_client, bus, gate)
            try:
                loop.run(record.session_id, message, project_root, task_id=task_id)
            except Exception as exc:
                with self._lock:
                    record.state = "error"
                bus.publish(make_event(
                    EventType.ERROR, record.session_id, task_id,
                    ErrorData(source="api", message=str(exc), recoverable=False),
                ))
            finally:
                record.events.put(None)

        threading.Thread(target=run, name=f"agent-{task_id}", daemon=True).start()
        return record

    def get(self, task_id: str, owner_id: int) -> TaskRecord | None:
        with self._lock:
            record = self._tasks.get(task_id)
        return record if record and record.owner_id == owner_id else None


class _TaskPermissionGate(PermissionGate):
    def __init__(self, broker: PermissionBroker):
        super().__init__(confirm_callback=None)
        self._broker = broker
        self._current_call_id: str | None = None

    def check(self, tool_name: str, arguments: dict, risk_level) -> bool:
        return self._broker.confirm(tool_name, arguments, risk_level, self._current_call_id or "")

    def bind_call_id(self, tool_call_id: str) -> None:
        self._current_call_id = tool_call_id
