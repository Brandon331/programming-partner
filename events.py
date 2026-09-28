"""
Contrato de eventos del bus interno.

Basado en "Contrato de eventos y definición de tools" (sección 1).
Este módulo es la fuente de verdad de la FORMA de cada evento. Nadie más
en el equipo debería construir un evento a mano fuera de estas funciones:
así, si el contrato cambia, cambia en un solo lugar.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Tipos de evento (tabla 1.2 del documento de contrato)
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    TASK_STARTED = "task_started"
    AGENT_THINKING = "agent_thinking"
    TOOL_USE = "tool_use"
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESPONSE = "permission_response"
    TOOL_RESULT = "tool_result"
    TEST_RUN = "test_run"
    ERROR = "error"
    MEMORY_SAVED = "memory_saved"
    DONE = "done"


class RiskLevel(str, Enum):
    VERDE = "verde"
    AMARILLO = "amarillo"
    ROJO = "rojo"


# ---------------------------------------------------------------------------
# Payloads por tipo (sección 1.3 + los que faltaban por completar el catálogo)
# ---------------------------------------------------------------------------

class TaskStartedData(BaseModel):
    user_message: str


class AgentThinkingData(BaseModel):
    delta: Optional[str] = None  # fragmento de texto en streaming, si aplica


class ToolUseData(BaseModel):
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    risk_level: RiskLevel


class PermissionRequestData(BaseModel):
    tool_call_id: str
    tool_name: str
    summary: str
    risk_level: RiskLevel
    options: list[str] = Field(default_factory=lambda: ["permitir", "cancelar"])


class PermissionResponseData(BaseModel):
    tool_call_id: str
    approved: bool


class ToolResultData(BaseModel):
    tool_call_id: str
    tool_name: str
    ok: bool
    result: Optional[Any] = None
    error: Optional[str] = None


class TestRunData(BaseModel):
    tool_call_id: str
    passed: int
    failed: int
    summary: str


class ErrorData(BaseModel):
    source: Literal["tool", "api", "system"]
    tool_name: Optional[str] = None
    message: str
    recoverable: bool = True


class MemorySavedData(BaseModel):
    key: str
    value: str


class DoneData(BaseModel):
    status: Literal["success", "cancelled", "max_iterations", "error"]
    summary: str
    tools_used: int
    tokens_used: int = 0
    duration_ms: int = 0


EventData = Union[
    TaskStartedData,
    AgentThinkingData,
    ToolUseData,
    PermissionRequestData,
    PermissionResponseData,
    ToolResultData,
    TestRunData,
    ErrorData,
    MemorySavedData,
    DoneData,
]


# ---------------------------------------------------------------------------
# Envolvente común (sección 1.1)
# ---------------------------------------------------------------------------

class EventEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    type: EventType
    session_id: str
    task_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict[str, Any]

    def to_json(self) -> str:
        return self.model_dump_json()


def make_event(
    type_: EventType,
    session_id: str,
    task_id: str,
    data: BaseModel,
) -> EventEnvelope:
    """
    Punto único de construcción de eventos. Se le pasa el modelo de payload
    ya validado (p. ej. ToolUseData(...)) y arma la envolvente completa.

    Mantener esto centralizado evita que cada módulo (mascota, XP, memoria,
    permission gate) invente su propia forma de evento.
    """
    return EventEnvelope(
        type=type_,
        session_id=session_id,
        task_id=task_id,
        data=data.model_dump(mode="json"),
    )
