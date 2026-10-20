from __future__ import annotations

import os
import secrets
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.environ.get("APP_DATA_DIR", "data")).resolve()
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", str(DATA_DIR / "assistant.db"))).resolve()
WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "workspaces")).resolve()


def connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize() -> None:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                workspace_path TEXT NOT NULL,
                repository_url TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        db.execute(
            "INSERT OR IGNORE INTO app_settings(key, value) VALUES('session_secret', ?)",
            (secrets.token_hex(32),),
        )


def session_secret() -> str:
    with connect() as db:
        row = db.execute(
            "SELECT value FROM app_settings WHERE key='session_secret'"
        ).fetchone()
    return row["value"]
