"""Shared fixtures: a schema-v1 SQLite store with seeded events and a fake engine binary.

Nothing here needs the real engine or macOS.
"""

from __future__ import annotations

import json
import sqlite3
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

# Copied verbatim from the engine spec §7.1 (OpenRhyme/docs/superpowers/specs/
# 2026-09-01-mvp-capture-engine-design.md). Keep in sync by hand.
DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO meta VALUES ('schema_version', '1');
CREATE TABLE IF NOT EXISTS events (
  id            INTEGER PRIMARY KEY,
  ts            REAL    NOT NULL,
  kind          TEXT    NOT NULL,
  pid           INTEGER,
  bundle_id     TEXT,
  app_name      TEXT,
  window_title  TEXT,
  document      TEXT,
  url           TEXT,
  role          TEXT,
  subrole       TEXT,
  identifier    TEXT,
  element_title TEXT,
  value         TEXT,
  selected_text TEXT,
  extra         TEXT
);
CREATE INDEX IF NOT EXISTS events_ts      ON events (ts);
CREATE INDEX IF NOT EXISTS events_kind_ts ON events (kind, ts);
CREATE INDEX IF NOT EXISTS events_app_ts  ON events (bundle_id, ts);
"""

BASE_TS = 1_756_710_000.0  # 2025-09-01T07:00:00Z


def seed(conn: sqlite3.Connection) -> None:
    """~30 events: Safari and TextEdit, several kinds, one long value, one secure field."""
    rows: list[tuple[object, ...]] = []
    rows.append(
        (
            BASE_TS,
            "daemon.started",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            json.dumps({"version": "0.1.0", "schema": 1}),
        )
    )
    for i in range(12):
        ts = BASE_TS + 10 * i
        rows.append(
            (
                ts,
                "app.activated" if i % 4 == 0 else "context.snapshot",
                10,
                "com.apple.Safari",
                "Safari",
                f"Page {i}",
                None,
                f"https://example.com/{i}",
                "AXWebArea",
                None,
                None,
                None,
                f"body text {i}",
                None,
                json.dumps({"reason": "heartbeat"}),
            )
        )
    for i in range(12):
        ts = BASE_TS + 200 + 10 * i
        rows.append(
            (
                ts,
                "element.value_changed" if i % 2 else "element.focused",
                20,
                "com.apple.TextEdit",
                "TextEdit",
                "notes.md",
                "file:///Users/me/notes.md",
                None,
                "AXTextArea",
                None,
                None,
                None,
                "x" * 5000 if i == 3 else f"line {i}",
                None,
                json.dumps({"valueHash": "abc", "truncated": False, "length": 6}),
            )
        )
    rows.append(
        (
            BASE_TS + 400,
            "element.focused",
            20,
            "com.apple.TextEdit",
            "TextEdit",
            "Login",
            None,
            None,
            "AXTextField",
            "AXSecureTextField",
            None,
            None,
            None,
            None,
            None,
        )
    )
    rows.append(
        (
            BASE_TS + 500,
            "idle.started",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            json.dumps({"idleSeconds": 130}),
        )
    )
    conn.executemany(
        "INSERT INTO events (ts, kind, pid, bundle_id, app_name, window_title, document, url, "
        "role, subrole, identifier, element_title, value, selected_text, extra) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()


@pytest.fixture
def seeded_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = sqlite3.connect(data_dir / "events.sqlite")
    conn.executescript(DDL)
    seed(conn)
    conn.close()
    return data_dir


@pytest.fixture
def fake_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A shell script named `openrhyme` on PATH that answers with canned envelopes and
    appends its argv to `calls.log`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    script = bin_dir / "openrhyme"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log}"\n'
        'case "$1 $2" in\n'
        '  "version --json") echo \'{"ok":true,"data":{"engine":"0.1.0","schema":1}}\' ;;\n'
        '  "status --json") echo \'{"ok":true,"data":{"trusted":true,"state":"active",'
        '"daemon_running":false,"event_count":27,"allowlist":["com.apple.Safari"],'
        '"opaque_apps":[]}}\' ;;\n'
        '  "apps list") echo \'{"ok":true,"data":{"allowlist":["com.apple.Safari"]}}\' ;;\n'
        '  "apps running") echo \'{"ok":true,"data":{"apps":[{"pid":10,"bundle_id":'
        '"com.apple.Safari","name":"Safari","allowlisted":true,"is_electron":false}]}}\' ;;\n'
        '  "apps allow") echo \'{"ok":true,"data":{"allowlist":["com.apple.Safari","\'"$3"\'"],'
        '"changed":true}}\' ;;\n'
        '  "apps deny") echo \'{"ok":true,"data":{"allowlist":[],"changed":true}}\' ;;\n'
        '  events*) echo \'{"ok":true,"data":{"events":[{"id":1,"kind":"app.activated",'
        '"pid":10,"bundle_id":"com.apple.Safari","app_name":"Safari","value":"hi",'
        '"time":"2025-09-01T00:00:00-07:00"}],"count":1}}\' ;;\n'
        '  "fail --json") echo \'{"ok":false,"error":{"code":"not_trusted","message":"no",'
        '"hint":"grant it"}}\'; exit 3 ;;\n'
        '  "hang --json") sleep 30 ;;\n'
        '  "garbage --json") echo "not json" ;;\n'
        '  *) echo \'{"ok":false,"error":{"code":"usage","message":"unknown"}}\'; exit 2 ;;\n'
        "esac\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.delenv("OPENRHYME_BIN", raising=False)
    yield log


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
