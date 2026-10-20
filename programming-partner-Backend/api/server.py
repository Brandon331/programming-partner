"""API HTTP local para Electron y clientes de desarrollo."""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from domain.errors import ApiError
from infrastructure.auth import authenticate, hash_password, issue_token, verify_password
from infrastructure.database import WORKSPACE_ROOT, connect, initialize
from infrastructure.github import GitHubClient
from proxy_client import OpenAISDKProxyClient
from services.task_manager import TaskManager

github = GitHubClient()
proxy_url = os.environ.get("PROXY_URL")
proxy_token = os.environ.get("PROXY_SESSION_TOKEN")
proxy = OpenAISDKProxyClient(proxy_url, proxy_token) if proxy_url and proxy_token else None
tasks = TaskManager(proxy_client=proxy)


def _safe_workspace(relative_path: str) -> Path:
    candidate_part = Path(relative_path)
    if candidate_part.is_absolute() or ".." in candidate_part.parts:
        raise ApiError(400, "invalid_workspace", "La ruta del proyecto debe ser relativa a WORKSPACE_ROOT.")
    root = WORKSPACE_ROOT.resolve()
    candidate = (root / candidate_part).resolve()
    if candidate == root or root not in candidate.parents:
        raise ApiError(400, "invalid_workspace", "La ruta debe permanecer dentro de WORKSPACE_ROOT.")
    return candidate


class Handler(BaseHTTPRequestHandler):
    server_version = "ProgrammingPartner/0.2"

    def _cors(self) -> None:
        origin = self.headers.get("Origin")
        allowed = {item.strip() for item in os.environ.get(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
        ).split(",") if item.strip()}
        if origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Last-Event-ID")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 1_000_000:
                raise ApiError(413, "payload_too_large", "El límite de solicitud es 1 MB.")
            payload = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("object expected")
            return payload
        except (ValueError, json.JSONDecodeError):
            raise ApiError(400, "invalid_json", "Envía un objeto JSON válido.")

    def _user(self) -> dict:
        return authenticate(self.headers.get("Authorization"))

    def _handle(self, action) -> None:
        try:
            action()
        except ApiError as exc:
            self._send(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except Exception:
            self._send(500, {"error": {"code": "internal_error", "message": "Ocurrió un error interno."}})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/v1/health":
            return self._send(200, {"status": "ok", "service": "programming-partner-backend", "version": "0.2.0"})

        def action():
            user = self._user()
            if path == "/api/v1/auth/me":
                return self._send(200, {"id": user["sub"], "email": user["email"], "role": user["role"]})
            if path == "/api/v1/projects":
                with connect() as db:
                    rows = db.execute(
                        "SELECT id,name,workspace_path,repository_url,created_at FROM projects WHERE owner_id=? ORDER BY id DESC",
                        (user["sub"],),
                    ).fetchall()
                return self._send(200, {"projects": [dict(row) for row in rows]})
            if path == "/api/v1/tools":
                from tools import TOOL_RISK_LEVELS
                return self._send(200, {"tools": [
                    {"name": name, "risk_level": risk.value}
                    for name, risk in TOOL_RISK_LEVELS.items()
                ]})
            if path == "/api/v1/github/status":
                return self._send(200, {"configured": github.configured, "writes_enabled": False})
            if path == "/api/v1/github/repositories":
                return self._send(200, {"repositories": github.repositories()})
            event_match = re.fullmatch(r"/api/v1/tasks/([^/]+)/events", path)
            if event_match:
                record = tasks.get(event_match.group(1), user["sub"])
                if not record:
                    raise ApiError(404, "task_not_found", "No se encontró la tarea.")
                return self._stream_events(record)
            task_match = re.fullmatch(r"/api/v1/tasks/([^/]+)", path)
            if task_match:
                record = tasks.get(task_match.group(1), user["sub"])
                if not record:
                    raise ApiError(404, "task_not_found", "No se encontró la tarea.")
                return self._send(200, {"task_id": record.task_id, "state": record.state,
                                        "events_url": f"/api/v1/tasks/{record.task_id}/events"})
            raise ApiError(404, "not_found", "No se encontró la ruta solicitada.")
        self._handle(action)

    def _stream_events(self, record) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        self.close_connection = True
        while True:
            try:
                item = record.events.get(timeout=15)
            except queue.Empty:
                self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
                continue
            if item is None:
                break
            payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            event_name = item.get("type", "message")
            event_id = item.get("event_id", "")
            chunk = f"id: {event_id}\nevent: {event_name}\ndata: {payload}\n\n".encode("utf-8")
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")

        def action():
            body = self._body()
            if path in ("/api/v1/auth/register", "/api/v1/auth/login"):
                email, password = body.get("email"), body.get("password")
                if not isinstance(email, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or not isinstance(password, str):
                    raise ApiError(400, "invalid_credentials", "Proporciona un correo válido y una contraseña.")
                email = email.lower().strip()
                if path.endswith("register"):
                    if len(password) < 10:
                        raise ApiError(400, "weak_password", "La contraseña debe tener al menos 10 caracteres.")
                    try:
                        with connect() as db:
                            cursor = db.execute("INSERT INTO users(email,password_hash) VALUES(?,?)", (email, hash_password(password)))
                            row = db.execute("SELECT id,email,role FROM users WHERE id=?", (cursor.lastrowid,)).fetchone()
                    except Exception as exc:
                        if "UNIQUE" in str(exc):
                            raise ApiError(409, "email_exists", "Ya existe una cuenta con ese correo.")
                        raise
                else:
                    with connect() as db:
                        row = db.execute("SELECT id,email,role,password_hash FROM users WHERE email=?", (email,)).fetchone()
                    if not row or not verify_password(password, row["password_hash"]):
                        raise ApiError(401, "invalid_credentials", "El correo o la contraseña son incorrectos.")
                user = {"id": row["id"], "email": row["email"], "role": row["role"]}
                access_token = issue_token(user)
                return self._send(201 if path.endswith("register") else 200, {
                    # access_token is the documented client contract; token is
                    # retained as a temporary compatibility alias.
                    "access_token": access_token, "token": access_token,
                    "token_type": "Bearer", "expires_in": 86400, "user": user,
                })

            user = self._user()
            if path == "/api/v1/projects":
                name = body.get("name")
                repository_url = body.get("repository_url")
                if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
                    raise ApiError(400, "invalid_project", "El nombre es obligatorio y admite hasta 120 caracteres.")
                if repository_url is not None and (
                    not isinstance(repository_url, str)
                    or not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", repository_url)
                ):
                    raise ApiError(400, "invalid_repository", "La URL debe ser un repositorio HTTPS de GitHub.")
                relative_path = body.get("path") or f"project-{uuid.uuid4().hex[:12]}"
                if not isinstance(relative_path, str):
                    raise ApiError(400, "invalid_workspace", "path debe ser una ruta relativa.")
                workspace = _safe_workspace(relative_path)
                workspace.mkdir(parents=True, exist_ok=True)
                with connect() as db:
                    cursor = db.execute(
                        "INSERT INTO projects(owner_id,name,workspace_path,repository_url) VALUES(?,?,?,?)",
                        (user["sub"], name.strip(), str(workspace), repository_url),
                    )
                    row = db.execute("SELECT id,name,workspace_path,repository_url,created_at FROM projects WHERE id=?", (cursor.lastrowid,)).fetchone()
                return self._send(201, {"project": dict(row)})

            project_task_match = re.fullmatch(r"/api/v1/projects/(\d+)/tasks", path)
            if project_task_match:
                message = body.get("message")
                if not isinstance(message, str) or not message.strip() or len(message) > 20_000:
                    raise ApiError(400, "invalid_message", "La tarea requiere un mensaje de hasta 20,000 caracteres.")
                with connect() as db:
                    project = db.execute(
                        "SELECT id,workspace_path FROM projects WHERE id=? AND owner_id=?",
                        (int(project_task_match.group(1)), user["sub"]),
                    ).fetchone()
                if not project:
                    raise ApiError(404, "project_not_found", "No se encontró el proyecto.")
                workspace = Path(project["workspace_path"]).resolve()
                if not workspace.is_dir():
                    raise ApiError(400, "workspace_unavailable", "La carpeta del proyecto ya no está disponible.")
                record = tasks.start(user["sub"], workspace, message.strip())
                return self._send(202, {
                    "task_id": record.task_id, "state": record.state,
                    "events_url": f"/api/v1/tasks/{record.task_id}/events",
                })

            permission_match = re.fullmatch(r"/api/v1/tasks/([^/]+)/permissions/([^/]+)", path)
            if permission_match:
                record = tasks.get(permission_match.group(1), user["sub"])
                if not record:
                    raise ApiError(404, "task_not_found", "No se encontró la tarea.")
                approved = body.get("approved")
                if not isinstance(approved, bool):
                    raise ApiError(400, "invalid_permission", "approved debe ser true o false.")
                if not record.broker.resolve(permission_match.group(2), approved):
                    raise ApiError(409, "permission_expired", "La solicitud ya respondió o expiró.")
                return self._send(200, {"resolved": True, "approved": approved})
            raise ApiError(404, "not_found", "No se encontró la ruta solicitada.")
        self._handle(action)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")


def serve() -> None:
    initialize()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    ws_host = os.environ.get("WS_HOST", host)
    ws_port = int(os.environ.get("WS_PORT", "8001"))
    # Import before starting HTTP so a missing WebSocket dependency fails clearly.
    from api.websocket_server import run_websocket_server
    threading.Thread(
        target=run_websocket_server, args=(ws_host, ws_port),
        name="programming-partner-websocket", daemon=True,
    ).start()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"API local disponible en http://{host}:{port} (Ctrl+C para detener)")
    print(f"WebSocket local: ws://{ws_host}:{ws_port}/ws")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
