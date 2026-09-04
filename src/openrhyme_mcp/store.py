"""Read-only access to the engine's `events.sqlite` (engine spec §7).

Event reads go through `openrhyme events --json` (see `engine.py`) so the engine's
redaction applies; this module now only backs the startup schema handshake.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import SUPPORTED_SCHEMA


class StoreError(Exception):
    """A stable-coded failure reading the store."""

    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def open_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise StoreError(
            "db_not_found",
            f"No event database at {path}",
            "Start `openrhyme daemon` and allow an app with `openrhyme apps allow <bundle-id>`",
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    found = schema_version(conn)
    if found > SUPPORTED_SCHEMA:
        conn.close()
        raise StoreError(
            "schema_too_new",
            f"Database schema {found} is newer than this server supports ({SUPPORTED_SCHEMA})",
            "Upgrade openrhyme-mcp",
        )
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if has_meta is None:
        return 0
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row is not None else 0
