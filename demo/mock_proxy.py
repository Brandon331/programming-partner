"""
Proxy falso (mock) que implementa la misma interfaz que ProxyClient
(ver proxy_client.py) pero sin red: simula turno por turno lo que GPT haría,
para poder probar agent_loop.py de punta a punta sin depender de la API real
ni del proxy en la nube.

Sirve como prueba de que el loop, los eventos y el permission gate funcionan
antes de conectar el proxy real (útil desde la semana 1, en paralelo).
"""

from __future__ import annotations

from typing import Any

from proxy_client import ChatResult


class ScriptedMockProxy:
    """
    Se le da un guion de turnos: cada turno es o bien una lista de tool_calls
    a "pedir", o bien un texto final. Va devolviendo un turno por cada
    llamada a `chat`.
    """

    def __init__(self, script: list[dict[str, Any]]):
        self._script = script
        self._i = 0

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ChatResult:
        if self._i >= len(self._script):
            return ChatResult(text="(sin más pasos en el guion)", tool_calls=[])

        step = self._script[self._i]
        self._i += 1

        if "tool_calls" in step:
            return ChatResult(
                text=step.get("text"),
                tool_calls=step["tool_calls"],
                tokens_used=step.get("tokens", 150),
            )
        return ChatResult(text=step.get("text", ""), tool_calls=[], tokens_used=step.get("tokens", 80))
