"""Read-only access to the engine's `events.sqlite` (engine spec §7)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from .config import SUPPORTED_SCHEMA

COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "ts",
    "kind",
    "pid",
    "bundle_id",
    "app_name",
    "window_title",
    "document",
    "url",
    "role",
    "subrole",
    "identifier",
    "element_title",
    "value",
    "selected_text",
    "extra",
)
MAX_LIMIT: Final = 2000
TEXT_COLUMNS: Final = ("value", "selected_text")


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


def query_events(
    conn: sqlite3.Connection,
    *,
    since: float,
    until: float | None = None,
    kinds: Sequence[str] | None = None,
    app: str | None = None,
    limit: int = 200,
    max_value_chars: int = 2000,
) -> list[dict[str, Any]]:
    sql = f"SELECT {', '.join(COLUMNS)} FROM events WHERE ts >= ?"
    params: list[object] = [since]
    if until is not None:
        sql += " AND ts <= ?"
        params.append(until)
    if kinds:
        sql += f" AND kind IN ({', '.join('?' * len(kinds))})"
        params.extend(kinds)
    if app:
        sql += " AND bundle_id = ?"
        params.append(app)
    sql += " ORDER BY ts, id LIMIT ?"
    params.append(min(max(limit, 1), MAX_LIMIT))
    return [shape_row(row, max_value_chars) for row in conn.execute(sql, params)]


def shape_row(row: sqlite3.Row, max_value_chars: int) -> dict[str, Any]:
    """Column names as keys, NULLs omitted, `extra` parsed, `time` added, text capped."""
    out: dict[str, Any] = {}
    for column in COLUMNS:
        value = row[column]
        if value is None:
            continue
        if column == "extra":
            out[column] = json.loads(value)
        elif column in TEXT_COLUMNS:
            out[column] = _truncate(str(value), max_value_chars)
        else:
            out[column] = value
    out["time"] = datetime.fromtimestamp(row["ts"]).astimezone().isoformat(timespec="seconds")
    return out


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}…[truncated {len(text) - max_chars} chars]"
