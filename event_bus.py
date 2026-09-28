"""
Bus de eventos interno. Local, in-process (o vía IPC hacia Electron en el
producto final) — no requiere red, como dice el documento de contrato.

Cualquier módulo (mascota, XP, memoria, logs) se suscribe aquí en vez de
hablar directo con el loop del agente.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from events import EventEnvelope, EventType

Handler = Callable[[EventEnvelope], None]


class EventBus:
    def __init__(self):
        self._handlers: dict[EventType | None, list[Handler]] = defaultdict(list)

    def subscribe(self, handler: Handler, event_types: list[EventType] | None = None) -> None:
        """
        event_types=None => se suscribe a TODOS los eventos (útil para logs).
        """
        if event_types is None:
            self._handlers[None].append(handler)
        else:
            for t in event_types:
                self._handlers[t].append(handler)

    def publish(self, envelope: EventEnvelope) -> None:
        for handler in self._handlers.get(envelope.type, []):
            handler(envelope)
        for handler in self._handlers.get(None, []):
            handler(envelope)
