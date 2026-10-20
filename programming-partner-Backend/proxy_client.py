"""
Cliente del proxy.

Este es el módulo "aislado" que menciona la planeación: "La llamada al proxy
vive aislada en un solo módulo del backend local, para poder ajustar el
proveedor o el contrato del proxy sin tocar el resto del sistema."

Usa el SDK oficial de `openai`, pero apuntando a nuestro proxy (mismo formato
de request/response) en vez de a la API pública. El token que viaja en el
header Authorization es el token de sesión del usuario (login), NUNCA la
API key del modelo — esa vive solo en el servidor del proxy.

Requiere `pip install openai` en el backend local real. Para el esqueleto,
la interfaz (ProxyClient) es lo importante: agent_loop.py solo depende de
`ProxyClient.chat(...)`, no de la librería openai directamente.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ChatResult:
    """Resultado normalizado de una llamada al proxy, sin importar el SDK."""
    text: str | None
    tool_calls: list[dict[str, Any]]  # [{id, name, arguments: dict}]
    raw: Any = None
    tokens_used: int = 0


class ProxyClient(Protocol):
    """Interfaz que agent_loop.py necesita. Cualquier implementación sirve."""

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ChatResult:
        ...


class OpenAISDKProxyClient:
    """
    Implementación real: usa el SDK de `openai` apuntando a nuestro proxy.

        POST {base_url}/v1/chat   Authorization: Bearer <session_token>

    El `base_url` se configura como la URL del proxy propio (no api.openai.com),
    y el `api_key` que le pasamos al SDK es en realidad el token de sesión del
    usuario — el proxy lo valida y es quien agrega la key real de OpenAI antes
    de reenviar a la API pública.
    """

    def __init__(self, base_url: str, session_token: str, model: str = "gpt-4.1"):
        import openai  # import perezoso: no es dependencia dura del esqueleto

        self._client = openai.OpenAI(base_url=base_url, api_key=session_token)
        self._model = model

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ChatResult:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tools,
        )
        choice = response.choices[0]
        tool_calls = []
        for tc in (choice.message.tool_calls or []):
            import json
            tool_calls.append({
                "id": tc.id,
                "name": tc.function.name,
                "arguments": json.loads(tc.function.arguments or "{}"),
            })

        return ChatResult(
            text=choice.message.content,
            tool_calls=tool_calls,
            raw=response,
            tokens_used=getattr(response.usage, "total_tokens", 0) if response.usage else 0,
        )
