"""WebSocket local: autentica la UI y relaya eventos del loop del agente."""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import websockets

from domain.errors import ApiError
from events import ErrorData, EventType, make_event
from infrastructure.auth import authenticate
from infrastructure.database import connect
from api.server import tasks


async def _send_json(websocket, payload: dict, lock: asyncio.Lock) -> None:
    async with lock:
        await websocket.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


async def _send_error(websocket, lock: asyncio.Lock, session_id: str, task_id: str, message: str) -> None:
    event = make_event(
        EventType.ERROR,
        session_id,
        task_id,
        ErrorData(source="api", message=message, recoverable=True),
    )
    await _send_json(websocket, event.model_dump(mode="json"), lock)


async def _relay_task_events(websocket, record, lock: asyncio.Lock) -> None:
    while True:
        item = await asyncio.to_thread(record.events.get)
        if item is None:
            return
        await _send_json(websocket, item, lock)


async def _handler(websocket) -> None:
    path = websocket.request.path.split("?", 1)[0]
    if path != "/ws":
        await websocket.close(code=4404, reason="Ruta WebSocket no encontrada")
        return

    session_id = f"session_{uuid.uuid4().hex[:12]}"
    try:
        first = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
        if not isinstance(first, dict) or first.get("type") != "authenticate":
            raise ValueError("authentication_required")
        token = first.get("access_token") or first.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("token_required")
        user = authenticate(token if token.startswith("Bearer ") else f"Bearer {token}")
    except (asyncio.TimeoutError, ValueError, json.JSONDecodeError, ApiError):
        await websocket.close(code=4401, reason="Autenticación requerida o inválida")
        return

    send_lock = asyncio.Lock()
    # Control de sesión; los eventos del agente posteriores usan EventEnvelope.
    await _send_json(websocket, {"type": "session_ready", "session_id": session_id}, send_lock)

    active_record = None
    relay_task = None
    try:
        async for raw in websocket:
            try:
                command = json.loads(raw)
                if not isinstance(command, dict):
                    raise ValueError("object_expected")
            except (ValueError, json.JSONDecodeError):
                await _send_error(websocket, send_lock, session_id, "task_protocol", "Envía un objeto JSON válido.")
                continue

            command_type = command.get("type")
            if command_type == "start_task":
                if active_record and active_record.state in {"queued", "running"}:
                    await _send_error(websocket, send_lock, session_id, active_record.task_id,
                                      "Esta conexión ya tiene una tarea en curso.")
                    continue
                if relay_task:
                    await relay_task
                    relay_task = None
                project_id = command.get("project_id")
                message = command.get("message")
                if not isinstance(project_id, int) or isinstance(project_id, bool):
                    await _send_error(websocket, send_lock, session_id, "task_protocol", "project_id debe ser un entero.")
                    continue
                if not isinstance(message, str) or not message.strip() or len(message) > 20_000:
                    await _send_error(websocket, send_lock, session_id, "task_protocol", "message es obligatorio y admite hasta 20,000 caracteres.")
                    continue
                with connect() as db:
                    project = db.execute(
                        "SELECT workspace_path FROM projects WHERE id=? AND owner_id=?",
                        (project_id, user["sub"]),
                    ).fetchone()
                if not project:
                    await _send_error(websocket, send_lock, session_id, "task_protocol", "No se encontró el proyecto.")
                    continue
                workspace = Path(project["workspace_path"]).resolve()
                if not workspace.is_dir():
                    await _send_error(websocket, send_lock, session_id, "task_protocol", "La carpeta del proyecto ya no está disponible.")
                    continue
                active_record = tasks.start(user["sub"], workspace, message.strip())
                relay_task = asyncio.create_task(_relay_task_events(websocket, active_record, send_lock))
                continue

            if command_type == "permission_response":
                task_id = command.get("task_id")
                tool_call_id = command.get("tool_call_id")
                approved = command.get("approved")
                if not isinstance(task_id, str) or not isinstance(tool_call_id, str) or not isinstance(approved, bool):
                    await _send_error(websocket, send_lock, session_id, "task_protocol",
                                      "permission_response requiere task_id, tool_call_id y approved booleano.")
                    continue
                record = tasks.get(task_id, user["sub"])
                if not record or record is not active_record:
                    await _send_error(websocket, send_lock, session_id, task_id, "No se encontró la tarea activa de esta sesión.")
                    continue
                if not record.broker.resolve(tool_call_id, approved):
                    await _send_error(websocket, send_lock, session_id, task_id, "La solicitud de permiso expiró o ya fue respondida.")
                # El loop emite permission_response como EventEnvelope tras resolver.
                continue

            await _send_error(websocket, send_lock, session_id,
                              active_record.task_id if active_record else "task_protocol",
                              "Comando desconocido. Usa start_task o permission_response.")
    except websockets.ConnectionClosed:
        pass
    finally:
        if relay_task and not relay_task.done():
            relay_task.cancel()


async def _serve(host: str, port: int) -> None:
    async with websockets.serve(_handler, host, port, max_size=1_000_000, ping_interval=20, ping_timeout=20):
        print(f"WebSocket local disponible en ws://{host}:{port}/ws")
        await asyncio.Future()


def run_websocket_server(host: str, port: int) -> None:
    asyncio.run(_serve(host, port))
