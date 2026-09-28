"""
Permission gate.

Semana 1-2 (este esqueleto): solo confirmar / no confirmar, sin niveles finos.
Semana 3-4: se agregan los 3 niveles completos (verde/amarillo/rojo) cruzados
con el "modo de trabajo" del usuario ("pregúntame todo" vs "sé autónomo").
La interfaz de abajo ya está pensada para ese crecimiento: `requires_confirmation`
es el único método que el loop necesita, así que cambiar la política interna
no obliga a tocar agent_loop.py.
"""

from __future__ import annotations

from typing import Callable, Optional

from events import RiskLevel

# Firma: (tool_name, arguments, risk_level) -> bool (True = usuario permitió)
ConfirmCallback = Callable[[str, dict, RiskLevel], bool]


class PermissionGate:
    def __init__(self, confirm_callback: Optional[ConfirmCallback] = None):
        """
        confirm_callback: función que le pregunta al usuario (vía la mascota,
        en el producto final) si permite la acción. Si no se provee, el gate
        se comporta en modo "auto-permitir" (útil para pruebas/demo).
        """
        self._confirm_callback = confirm_callback

    def requires_confirmation(self, risk_level: RiskLevel) -> bool:
        # Semana 1-2: todo lo que no sea verde pide confirmación siempre.
        return risk_level != RiskLevel.VERDE

    def check(self, tool_name: str, arguments: dict, risk_level: RiskLevel) -> bool:
        """
        Devuelve True si la tool puede ejecutarse.
        Verde: pasa directo. Amarillo/rojo: pregunta, salvo que no haya callback.
        """
        if not self.requires_confirmation(risk_level):
            return True
        if self._confirm_callback is None:
            return True
        return self._confirm_callback(tool_name, arguments, risk_level)
