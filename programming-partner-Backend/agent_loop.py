"""
Loop del agente.

Implementa el ciclo descrito en la planeación (sección "El ciclo del agente"):

  1. El usuario da una orden.
  2. El backend local envía el mensaje + definición de tools al proxy.
  3. El proxy reenvía a GPT.
  4. GPT responde pidiendo ejecutar una tool (o responde texto final).
  5. La orden pasa por el permission gate.
  6. Se ejecuta localmente y el resultado vuelve a GPT vía el proxy.
  7. Se repite hasta "listo" o máximo de iteraciones.

Cada paso emite su evento correspondiente al bus (sección 1.2 del contrato).
Este módulo NO sabe nada de Electron, SQLite ni HTTP: solo orquesta los
otros módulos. Eso es lo que permite testearlo sin la app completa.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from event_bus import EventBus
from events import (
    DoneData,
    ErrorData,
    EventType,
    PermissionRequestData,
    PermissionResponseData,
    RiskLevel,
    ToolResultData,
    ToolUseData,
    TaskStartedData,
    make_event,
)
from permission_gate import PermissionGate
from proxy_client import ProxyClient
from tools import (
    TOOL_RISK_LEVELS,
    TOOL_SCHEMAS,
    ToolError,
    classify_run_command_risk,
    execute_tool,
)

SYSTEM_PROMPT = (
    "Eres el agente de un compañero de programación con mascota. Tienes acceso "
    "a tools para leer, editar y correr comandos dentro del proyecto del "
    "usuario. Explora con las tools antes de asumir la estructura del "
    "proyecto. Cuando termines la tarea, responde con un resumen breve y sin "
    "pedir más tools."
)

DEFAULT_MAX_ITERATIONS = 10


class AgentLoop:
    def __init__(
        self,
        proxy_client: ProxyClient,
        event_bus: EventBus,
        permission_gate: PermissionGate,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ):
        self._proxy = proxy_client
        self._bus = event_bus
        self._gate = permission_gate
        self._max_iterations = max_iterations

    def run(
        self,
        session_id: str,
        user_message: str,
        project_root: Path,
        task_id: str | None = None,
    ) -> DoneData:
        task_id = task_id or f"task_{uuid.uuid4().hex[:8]}"
        started_at = time.monotonic()
        tools_used = 0
        tokens_used = 0

        def emit(type_: EventType, data) -> None:
            self._bus.publish(make_event(type_, session_id, task_id, data))

        emit(EventType.TASK_STARTED, TaskStartedData(user_message=user_message))

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        for _ in range(self._max_iterations):
            result = self._proxy.chat(messages=messages, tools=TOOL_SCHEMAS)
            tokens_used += result.tokens_used

            if not result.tool_calls:
                # GPT respondió texto final: la tarea terminó.
                done = DoneData(
                    status="success",
                    summary=result.text or "",
                    tools_used=tools_used,
                    tokens_used=tokens_used,
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                )
                emit(EventType.DONE, done)
                return done

            # El mensaje del asistente con tool_calls entra al historial tal
            # cual, para que el siguiente turno tenga contexto completo.
            messages.append({
                "role": "assistant",
                "content": result.text,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in result.tool_calls
                ],
            })

            for tool_call in result.tool_calls:
                tools_used += 1
                outcome = self._handle_tool_call(
                    tool_call, project_root, session_id, task_id, emit
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": str(outcome),
                })

        done = DoneData(
            status="max_iterations",
            summary="Se alcanzó el máximo de iteraciones sin terminar la tarea.",
            tools_used=tools_used,
            tokens_used=tokens_used,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )
        emit(EventType.DONE, done)
        return done

    def _handle_tool_call(
        self,
        tool_call: dict[str, Any],
        project_root: Path,
        session_id: str,
        task_id: str,
        emit,
    ) -> dict[str, Any]:
        tool_name = tool_call["name"]
        arguments = tool_call["arguments"]
        tool_call_id = tool_call["id"]

        risk_level = TOOL_RISK_LEVELS.get(tool_name, RiskLevel.ROJO)
        if tool_name == "run_command":
            risk_level = classify_run_command_risk(arguments.get("command", ""))

        emit(EventType.TOOL_USE, ToolUseData(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
            risk_level=risk_level,
        ))

        if self._gate.requires_confirmation(risk_level):
            summary = f"Ejecutar {tool_name} con {arguments!r}"
            emit(EventType.PERMISSION_REQUEST, PermissionRequestData(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                summary=summary,
                risk_level=risk_level,
            ))
            approved = self._gate.check(tool_name, arguments, risk_level)
            emit(EventType.PERMISSION_RESPONSE, PermissionResponseData(
                tool_call_id=tool_call_id, approved=approved,
            ))
            if not approved:
                error_payload = {"cancelled_by_user": True}
                emit(EventType.TOOL_RESULT, ToolResultData(
                    tool_call_id=tool_call_id, tool_name=tool_name,
                    ok=False, error="Cancelado por el usuario.",
                ))
                return error_payload

        try:
            result = execute_tool(project_root, tool_name, arguments)
            emit(EventType.TOOL_RESULT, ToolResultData(
                tool_call_id=tool_call_id, tool_name=tool_name,
                ok=True, result=result,
            ))
            return result
        except ToolError as exc:
            emit(EventType.ERROR, ErrorData(
                source="tool", tool_name=tool_name, message=str(exc), recoverable=True,
            ))
            emit(EventType.TOOL_RESULT, ToolResultData(
                tool_call_id=tool_call_id, tool_name=tool_name,
                ok=False, error=str(exc),
            ))
            return {"error": str(exc)}
