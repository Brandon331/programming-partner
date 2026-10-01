from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from infrastructure.database import session_secret
from domain.errors import ApiError

TOKEN_TTL_SECONDS = 24 * 60 * 60
PBKDF2_ROUNDS = 310_000


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"{_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_part, digest_part = stored.split("$", 1)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _unb64(salt_part), PBKDF2_ROUNDS)
        return hmac.compare_digest(actual, _unb64(digest_part))
    except (ValueError, TypeError):
        return False


def issue_token(user: dict) -> str:
    payload = _b64(json.dumps({
        "sub": user["id"], "email": user["email"], "role": user["role"],
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }, separators=(",", ":")).encode("utf-8"))
    signature = _b64(hmac.new(session_secret().encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def authenticate(header: str | None) -> dict:
    if not header or not header.startswith("Bearer "):
        raise ApiError(401, "unauthorized", "Se requiere una sesión válida.")
    try:
        payload_part, signature = header[7:].split(".", 1)
        expected = _b64(hmac.new(session_secret().encode(), payload_part.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(_unb64(payload_part))
        if int(payload["exp"]) < time.time():
            raise ValueError("expired")
        return payload
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise ApiError(401, "invalid_token", "La sesión no es válida o ha expirado.")
