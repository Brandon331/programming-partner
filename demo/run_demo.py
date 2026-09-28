"""
Demo de punta a punta del esqueleto del loop del agente.

Simula: "arregla el saludo en main.py" -> el agente lee el archivo,
lo edita, corre un comando, y responde que terminó.

Uso:
    python demo/run_demo.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agent_skeleton/ en el path

from event_bus import EventBus  # noqa: E402
from events import EventEnvelope  # noqa: E402
from permission_gate import PermissionGate  # noqa: E402
from agent_loop import AgentLoop  # noqa: E402
from demo.mock_proxy import ScriptedMockProxy  # noqa: E402


def print_event(event: EventEnvelope) -> None:
    print(f"[{event.type.value:20s}] {event.data}")


def auto_approve(tool_name: str, arguments: dict, risk_level) -> bool:
    print(f"  (permission gate: auto-aprobando {tool_name}, riesgo={risk_level.value})")
    return True


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / "main.py").write_text('print("hola mundo")\n', encoding="utf-8")

        script = [
            {
                "tool_calls": [
                    {"id": "call_1", "name": "read_file", "arguments": {"path": "main.py"}},
                ],
            },
            {
                "tool_calls": [
                    {
                        "id": "call_2",
                        "name": "edit_file",
                        "arguments": {
                            "path": "main.py",
                            "old_str": 'print("hola mundo")',
                            "new_str": 'print("hola, mundo con mascota!")',
                        },
                    },
                ],
            },
            {
                "tool_calls": [
                    {"id": "call_3", "name": "run_command", "arguments": {"command": "python3 main.py"}},
                ],
            },
            {"text": "Listo, actualicé el saludo en main.py y verifiqué que corre bien."},
        ]

        bus = EventBus()
        bus.subscribe(print_event)  # log de todos los eventos

        loop = AgentLoop(
            proxy_client=ScriptedMockProxy(script),
            event_bus=bus,
            permission_gate=PermissionGate(confirm_callback=auto_approve),
        )

        result = loop.run(
            session_id="sess_demo",
            user_message="Arregla el saludo en main.py",
            project_root=project_root,
        )

        print("\n--- resultado final ---")
        print(result)
        print("\n--- contenido final de main.py ---")
        print((project_root / "main.py").read_text())


if __name__ == "__main__":
    main()
