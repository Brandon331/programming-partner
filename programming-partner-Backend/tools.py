"""
Definición y ejecución de tools del agente.

Cubre solo las 3 tools del esqueleto semana 1-2 (read_file, edit_file,
run_command), tal como dice la planeación. list_files, search, run_tests
y git se agregan en semana 3-4 siguiendo exactamente el mismo patrón:
1) agregar su JSON Schema a TOOL_SCHEMAS
2) agregar su risk_level a TOOL_RISK_LEVELS
3) agregar su implementación a TOOL_IMPLEMENTATIONS

El módulo es el único responsable de no dejar salir la ejecución de la
carpeta del proyecto (path traversal), como marca la nota de implementación
del documento de contrato.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from events import RiskLevel


class ToolError(Exception):
    """Error esperado de una tool (argumentos inválidos, ambigüedad, etc.)."""


# ---------------------------------------------------------------------------
# Sandboxing de rutas
# ---------------------------------------------------------------------------

def resolve_safe_path(project_root: Path, relative_path: str) -> Path:
    """
    Resuelve `relative_path` contra `project_root` y garantiza que el
    resultado no se salga de esa carpeta (protección contra path traversal).
    """
    root = project_root.resolve()
    candidate = (root / relative_path).resolve()
    if root != candidate and root not in candidate.parents:
        raise ToolError(f"Ruta fuera del proyecto: {relative_path!r}")
    return candidate


# ---------------------------------------------------------------------------
# JSON Schema de cada tool (formato function calling)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Lee el contenido de un archivo dentro del proyecto confinado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Ruta relativa al proyecto."},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Edita un archivo reemplazando un fragmento único (no reescribe "
                "el archivo completo). Falla si old_str no es único."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string", "description": "Fragmento exacto y único."},
                    "new_str": {"type": "string"},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Ejecuta un comando de shell dentro del proyecto, con timeout y salida truncada.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_ms": {"type": "integer", "default": 30000},
                },
                "required": ["command"],
            },
        },
    },
]

TOOL_RISK_LEVELS: dict[str, RiskLevel] = {
    "read_file": RiskLevel.VERDE,
    "edit_file": RiskLevel.AMARILLO,
    "run_command": RiskLevel.AMARILLO,  # patrones destructivos se reclasifican abajo
}

# Comandos que, aunque run_command sea amarillo por defecto, siempre deben
# confirmarse (equivalente a la reclasificación a rojo del documento de tools).
DESTRUCTIVE_COMMAND_MARKERS = ("rm -rf", "sudo ", ":(){ :|:& };:", "mkfs", "dd if=")

MAX_OUTPUT_CHARS = 4000


def classify_run_command_risk(command: str) -> RiskLevel:
    lowered = command.lower()
    if any(marker in lowered for marker in DESTRUCTIVE_COMMAND_MARKERS):
        return RiskLevel.ROJO
    return RiskLevel.AMARILLO


# ---------------------------------------------------------------------------
# Implementaciones
# ---------------------------------------------------------------------------

def _read_file(project_root: Path, path: str, start_line: int | None = None,
                end_line: int | None = None) -> dict[str, Any]:
    target = resolve_safe_path(project_root, path)
    if not target.is_file():
        raise ToolError(f"No existe el archivo: {path!r}")

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    if start_line or end_line:
        start = (start_line or 1) - 1
        end = end_line if end_line else len(lines)
        lines = lines[max(start, 0):end]

    return {"path": path, "content": "\n".join(lines)}


def _edit_file(project_root: Path, path: str, old_str: str, new_str: str) -> dict[str, Any]:
    target = resolve_safe_path(project_root, path)
    if not target.is_file():
        raise ToolError(f"No existe el archivo: {path!r}")

    original = target.read_text(encoding="utf-8")
    occurrences = original.count(old_str)
    if occurrences == 0:
        raise ToolError("old_str no se encontró en el archivo.")
    if occurrences > 1:
        raise ToolError(
            f"old_str no es único en el archivo ({occurrences} ocurrencias); "
            "agrega más contexto para que sea inequívoco."
        )

    updated = original.replace(old_str, new_str, 1)
    target.write_text(updated, encoding="utf-8")
    return {"path": path, "bytes_written": len(updated)}


def _run_command(project_root: Path, command: str, timeout_ms: int = 30000) -> dict[str, Any]:
    timeout_s = max(timeout_ms, 1) / 1000
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"Comando excedió el timeout de {timeout_ms}ms.")

    def truncate(s: str) -> str:
        return s if len(s) <= MAX_OUTPUT_CHARS else s[:MAX_OUTPUT_CHARS] + "\n...[truncado]"

    return {
        "command": command,
        "exit_code": proc.returncode,
        "stdout": truncate(proc.stdout),
        "stderr": truncate(proc.stderr),
    }


TOOL_IMPLEMENTATIONS: dict[str, Callable[..., dict[str, Any]]] = {
    "read_file": _read_file,
    "edit_file": _edit_file,
    "run_command": _run_command,
}


def execute_tool(project_root: Path, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Despacha una tool por nombre. Lanza ToolError en fallos esperados."""
    impl = TOOL_IMPLEMENTATIONS.get(tool_name)
    if impl is None:
        raise ToolError(f"Tool desconocida: {tool_name!r}")
    return impl(project_root, **arguments)
