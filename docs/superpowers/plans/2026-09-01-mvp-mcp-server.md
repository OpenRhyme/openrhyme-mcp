# openrhyme-mcp MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stdio MCP server that exposes the engine's raw `events` table to any agent host, read-only, plus `status`/`apps`/`allow_app`/`deny_app` tools that shell out to the `openrhyme` CLI.

**Architecture:** Five small modules under `src/openrhyme_mcp/`: `timespec` (time grammar), `config` (paths + binary discovery), `store` (read-only SQLite queries and row shaping), `engine` (subprocess runner for `openrhyme … --json`), `server` (`MCPServer` with tools and one resource). Tests run entirely in-process: a fixture SQLite built from the engine's schema v1 DDL and a fake `openrhyme` shell script on `PATH`.

**Tech Stack:** Python ≥3.12, `mcp>=2.1,<3` (`MCPServer`, `mcp.client.Client` for tests), stdlib `sqlite3`/`subprocess`, pytest + anyio plugin, ruff, mypy `--strict`, uv.

**Spec:** `docs/superpowers/specs/2026-09-01-mvp-mcp-server-design.md`. Contract it implements: engine spec `../OpenRhyme/docs/superpowers/specs/2026-09-01-mvp-capture-engine-design.md` §5 (event model), §7.1 (schema v1 DDL), §8 (paths), §9 (CLI JSON envelope).

## Global Constraints

- Runtime dependency stays exactly `mcp>=2.1,<3`. Dev group: `ruff`, `pytest`, `mypy` (already pinned in `pyproject.toml`). No other packages.
- `mypy --strict` clean; ruff rules as configured; line length 100. `make check` is the gate for every commit.
- **Read-only.** The SQLite file is opened with `file:<path>?mode=ro` (URI mode). Never `INSERT`/`UPDATE`/`CREATE`. State changes go through `openrhyme <cmd> --json` only.
- No capture, no macOS permission requests, no network.
- Schema support: `SUPPORTED_SCHEMA = 1`; refuse to serve a newer schema.
- Discovery: `OPENRHYME_DATA_DIR` → `~/Library/Application Support/OpenRhyme/`; `OPENRHYME_BIN` → `openrhyme` on `PATH`.
- Column names (schema v1): `id, ts, kind, pid, bundle_id, app_name, window_title, document, url, role, subrole, identifier, element_title, value, selected_text, extra`.
- Tool results must not flood a model's context: `value`/`selected_text` are truncated to `max_value_chars` (default 2000) with the suffix `…[truncated N chars]`; `0` disables.
- Commit messages: short, single line, no trailers.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/openrhyme_mcp/timespec.py` | `parse(text, now, tz) -> float` — mirrors the engine's `TimeSpec` |
| `src/openrhyme_mcp/config.py` | `Settings`, `resolve(env)`; `SUPPORTED_SCHEMA` |
| `src/openrhyme_mcp/store.py` | `open_readonly`, `schema_version`, `query_events`, `shape_row`, `StoreError` |
| `src/openrhyme_mcp/engine.py` | `run_cli(args, settings)`, `EngineError` |
| `src/openrhyme_mcp/server.py` | `mcp = MCPServer("openrhyme")`, tools, resource, `main()` |
| `tests/conftest.py` | `DDL`, `seeded_data_dir`, `fake_engine`, `anyio_backend` fixtures |
| `tests/test_timespec.py`, `test_config.py`, `test_store.py`, `test_engine.py`, `test_server.py` | tests |

---

### Task 1: `timespec.py`

**Files:**
- Create: `src/openrhyme_mcp/timespec.py`
- Test: `tests/test_timespec.py`

**Interfaces:**
- Produces: `class TimeSpecError(ValueError)`; `def parse(text: str, *, now: float | None = None, tz: tzinfo | None = None) -> float` returning unix seconds.

- [ ] **Step 1: Write the failing test**

`tests/test_timespec.py`:
```python
from datetime import timezone
from zoneinfo import ZoneInfo

import pytest

from openrhyme_mcp.timespec import TimeSpecError, parse

NOW = 1_756_710_000.0  # 2025-09-01T07:00:00Z


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30s", NOW - 30),
        ("30m", NOW - 1800),
        ("2h", NOW - 7200),
        ("1d", NOW - 86400),
        ("1.5h", NOW - 5400),
        ("1756700000", 1_756_700_000.0),
        ("1756700000.5", 1_756_700_000.5),
        ("2025-09-01T07:00:00Z", NOW),
        ("2025-09-01T07:00:00.250Z", NOW + 0.25),
        ("2025-09-01T09:00:00+02:00", NOW),
        ("2025-09-01T07:00:00", NOW),  # local == UTC in this test
        ("2025-09-01 07:00", NOW),
        ("2025-09-01", 1_756_684_800.0),
    ],
)
def test_parses(text: str, expected: float) -> None:
    assert parse(text, now=NOW, tz=timezone.utc) == pytest.approx(expected, abs=1e-3)


@pytest.mark.parametrize("text", ["", "yesterday", "2h30m", "1e5", "2025-13-01", "5w"])
def test_rejects(text: str) -> None:
    with pytest.raises(TimeSpecError):
        parse(text, now=NOW, tz=timezone.utc)


def test_local_time_uses_given_zone() -> None:
    assert parse("2025-09-01T16:00:00", now=NOW, tz=ZoneInfo("Asia/Tokyo")) == NOW
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_timespec.py -q`
Expected: `ModuleNotFoundError: No module named 'openrhyme_mcp.timespec'`.

- [ ] **Step 3: Implement**

`src/openrhyme_mcp/timespec.py`:
```python
"""The `<time>` grammar shared with the engine CLI (engine spec §9).

Relative durations (`30s`, `2h`, `1d`, decimals allowed) mean "that long ago"; otherwise
unix seconds, ISO-8601 with a zone, or a local date/time without a zone.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, tzinfo

_RELATIVE = re.compile(r"^(\d+(?:\.\d+)?)([smhd])$")
_UNIX = re.compile(r"^\d+(?:\.\d+)?$")
_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_LOCAL_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


class TimeSpecError(ValueError):
    """Raised for text that matches none of the accepted forms."""


def parse(text: str, *, now: float | None = None, tz: tzinfo | None = None) -> float:
    """Return unix seconds for `text`. `now` and `tz` exist for deterministic tests."""
    s = text.strip()
    if not s:
        raise TimeSpecError(text)

    relative = _RELATIVE.match(s)
    if relative:
        base = now if now is not None else datetime.now(timezone.utc).timestamp()
        return base - float(relative.group(1)) * _UNITS[relative.group(2)]

    if _UNIX.match(s):
        return float(s)

    try:
        aware = datetime.fromisoformat(s)
    except ValueError:
        aware = None
    if aware is not None and aware.tzinfo is not None:
        return aware.timestamp()

    local_zone = tz or datetime.now().astimezone().tzinfo
    for fmt in _LOCAL_FORMATS:
        try:
            naive = datetime.strptime(s, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=local_zone).timestamp()

    raise TimeSpecError(text)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_timespec.py -q`
Expected: 20 passed.

- [ ] **Step 5: Lint, type-check, commit**

```bash
make check
git add src/openrhyme_mcp/timespec.py tests/test_timespec.py
git commit -m "Add timespec parsing"
```

---

### Task 2: `config.py`

**Files:**
- Create: `src/openrhyme_mcp/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `SUPPORTED_SCHEMA: Final = 1`; `@dataclass(frozen=True) class Settings: data_dir: Path; engine_bin: Path | None` with properties `db_path`, `config_path`; `def resolve(env: Mapping[str, str] | None = None) -> Settings`.

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
import stat
from pathlib import Path

from openrhyme_mcp.config import SUPPORTED_SCHEMA, resolve


def test_supported_schema_is_one() -> None:
    assert SUPPORTED_SCHEMA == 1


def test_env_overrides_win(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin" / "openrhyme"
    fake_bin.parent.mkdir()
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IEXEC)
    settings = resolve({"OPENRHYME_DATA_DIR": str(tmp_path / "data"), "OPENRHYME_BIN": str(fake_bin)})
    assert settings.data_dir == tmp_path / "data"
    assert settings.db_path == tmp_path / "data" / "events.sqlite"
    assert settings.config_path == tmp_path / "data" / "config.json"
    assert settings.engine_bin == fake_bin


def test_default_data_dir_and_path_lookup(tmp_path: Path) -> None:
    on_path = tmp_path / "openrhyme"
    on_path.write_text("#!/bin/sh\n")
    on_path.chmod(on_path.stat().st_mode | stat.S_IEXEC)
    settings = resolve({"PATH": str(tmp_path), "HOME": str(tmp_path)})
    assert settings.data_dir == tmp_path / "Library" / "Application Support" / "OpenRhyme"
    assert settings.engine_bin == on_path


def test_missing_binary_is_none(tmp_path: Path) -> None:
    settings = resolve({"PATH": str(tmp_path), "HOME": str(tmp_path)})
    assert settings.engine_bin is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_config.py -q`
Expected: `ModuleNotFoundError: No module named 'openrhyme_mcp.config'`.

- [ ] **Step 3: Implement**

`src/openrhyme_mcp/config.py`:
```python
"""Where the engine's files and binary are (engine spec §8; MCP spec §7)."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SUPPORTED_SCHEMA: Final = 1


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    engine_bin: Path | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "events.sqlite"

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.json"


def resolve(env: Mapping[str, str] | None = None) -> Settings:
    """Resolve settings from `env` (defaults to the process environment)."""
    source = os.environ if env is None else env
    override = source.get("OPENRHYME_DATA_DIR", "").strip()
    if override:
        data_dir = Path(override).expanduser()
    else:
        home = Path(source.get("HOME", "")).expanduser() if source.get("HOME") else Path.home()
        data_dir = home / "Library" / "Application Support" / "OpenRhyme"

    engine_bin: Path | None = None
    explicit = source.get("OPENRHYME_BIN", "").strip()
    if explicit:
        engine_bin = Path(explicit).expanduser()
    else:
        found = shutil.which("openrhyme", path=source.get("PATH"))
        engine_bin = Path(found) if found else None

    return Settings(data_dir=data_dir, engine_bin=engine_bin)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -q`
Expected: 4 passed.

- [ ] **Step 5: Lint, type-check, commit**

```bash
make check
git add src/openrhyme_mcp/config.py tests/test_config.py
git commit -m "Add settings resolution"
```

---

### Task 3: `store.py` and the fixture database

**Files:**
- Create: `src/openrhyme_mcp/store.py`
- Modify: `tests/conftest.py` (replace the docstring-only stub)
- Create: `tests/__init__.py` (empty, so tests can import `tests.conftest`)
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `COLUMNS: Final[tuple[str, ...]]`; `class StoreError(Exception)` with `.code: str` and `.hint: str | None`; `def open_readonly(path: Path) -> sqlite3.Connection`; `def schema_version(conn) -> int`; `def query_events(conn, *, since: float, until: float | None = None, kinds: Sequence[str] | None = None, app: str | None = None, limit: int = 200, max_value_chars: int = 2000) -> list[dict[str, Any]]`; `def shape_row(row: sqlite3.Row, max_value_chars: int) -> dict[str, Any]`; `MAX_LIMIT: Final = 2000`.
- Produces (tests): fixtures `seeded_data_dir(tmp_path) -> Path` (with `events.sqlite`), `anyio_backend`, and the constant `DDL`.

- [ ] **Step 1: Write the fixture and the failing test**

```bash
touch tests/__init__.py
```

`tests/conftest.py`:
```python
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
# 2026-09-01-mvp-capture-engine-design.md). Keep in sync by hand; test_store checks that
# the columns here match store.COLUMNS.
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
    rows.append((BASE_TS, "daemon.started", None, None, None, None, None, None, None, None,
                 None, None, None, None, json.dumps({"version": "0.1.0", "schema": 1})))
    for i in range(12):
        ts = BASE_TS + 10 * i
        rows.append((ts, "app.activated" if i % 4 == 0 else "context.snapshot", 10,
                     "com.apple.Safari", "Safari", f"Page {i}", None, f"https://example.com/{i}",
                     "AXWebArea", None, None, None, f"body text {i}", None,
                     json.dumps({"reason": "heartbeat"})))
    for i in range(12):
        ts = BASE_TS + 200 + 10 * i
        rows.append((ts, "element.value_changed" if i % 2 else "element.focused", 20,
                     "com.apple.TextEdit", "TextEdit", "notes.md", "file:///Users/me/notes.md",
                     None, "AXTextArea", None, None, None, "x" * 5000 if i == 3 else f"line {i}",
                     None, json.dumps({"valueHash": "abc", "truncated": False, "length": 6})))
    rows.append((BASE_TS + 400, "element.focused", 20, "com.apple.TextEdit", "TextEdit",
                 "Login", None, None, "AXTextField", "AXSecureTextField", None, None, None,
                 None, None))
    rows.append((BASE_TS + 500, "idle.started", None, None, None, None, None, None, None,
                 None, None, None, None, None, json.dumps({"idleSeconds": 130})))
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
```

`tests/test_store.py`:
```python
import sqlite3
from pathlib import Path

import pytest

from openrhyme_mcp.store import COLUMNS, MAX_LIMIT, StoreError, open_readonly, query_events, schema_version
from tests.conftest import BASE_TS


def test_columns_match_fixture_ddl(seeded_data_dir: Path) -> None:
    conn = open_readonly(seeded_data_dir / "events.sqlite")
    names = [row[1] for row in conn.execute("PRAGMA table_info(events)")]
    assert tuple(names) == COLUMNS


def test_schema_version_and_read_only(seeded_data_dir: Path) -> None:
    conn = open_readonly(seeded_data_dir / "events.sqlite")
    assert schema_version(conn) == 1
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO events (ts, kind) VALUES (1, 'x')")


def test_missing_db_raises_store_error(tmp_path: Path) -> None:
    with pytest.raises(StoreError) as info:
        open_readonly(tmp_path / "events.sqlite")
    assert info.value.code == "db_not_found"
    assert "openrhyme daemon" in (info.value.hint or "")


def test_newer_schema_is_refused(seeded_data_dir: Path) -> None:
    rw = sqlite3.connect(seeded_data_dir / "events.sqlite")
    rw.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    rw.commit()
    rw.close()
    with pytest.raises(StoreError) as info:
        open_readonly(seeded_data_dir / "events.sqlite")
    assert info.value.code == "schema_too_new"


def test_query_filters_and_shapes_rows(seeded_data_dir: Path) -> None:
    conn = open_readonly(seeded_data_dir / "events.sqlite")
    rows = query_events(conn, since=BASE_TS + 200, until=BASE_TS + 230)
    assert [r["kind"] for r in rows] == ["element.focused", "element.value_changed",
                                          "element.focused", "element.value_changed"]
    first = rows[0]
    assert first["bundle_id"] == "com.apple.TextEdit"
    assert first["extra"] == {"valueHash": "abc", "truncated": False, "length": 6}
    assert first["time"].startswith("2025-09-01T")
    assert "url" not in first  # NULL columns are omitted

    assert len(query_events(conn, since=0, kinds=["app.activated"])) == 3
    assert len(query_events(conn, since=0, app="com.apple.Safari")) == 12
    assert len(query_events(conn, since=0, limit=5)) == 5
    assert [r["id"] for r in query_events(conn, since=0, limit=3)] == [1, 2, 3]


def test_value_truncation(seeded_data_dir: Path) -> None:
    conn = open_readonly(seeded_data_dir / "events.sqlite")
    long_rows = [r for r in query_events(conn, since=0, max_value_chars=100) if r.get("value", "").startswith("xxxx")]
    assert len(long_rows) == 1
    assert long_rows[0]["value"].endswith("…[truncated 4900 chars]")
    assert len(long_rows[0]["value"]) == 100 + len("…[truncated 4900 chars]")
    full = [r for r in query_events(conn, since=0, max_value_chars=0) if r.get("value", "").startswith("xxxx")]
    assert len(full[0]["value"]) == 5000


def test_limit_is_capped(seeded_data_dir: Path) -> None:
    conn = open_readonly(seeded_data_dir / "events.sqlite")
    assert MAX_LIMIT == 2000
    assert len(query_events(conn, since=0, limit=10_000)) == 27
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_store.py -q`
Expected: `ModuleNotFoundError: No module named 'openrhyme_mcp.store'`.

- [ ] **Step 3: Implement**

`src/openrhyme_mcp/store.py`:
```python
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
    "id", "ts", "kind", "pid", "bundle_id", "app_name", "window_title", "document", "url",
    "role", "subrole", "identifier", "element_title", "value", "selected_text", "extra",
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -q`
Expected: 7 passed.

- [ ] **Step 5: Lint, type-check, commit**

```bash
make check
git add src/openrhyme_mcp/store.py tests
git commit -m "Add read-only store access and fixtures"
```

---

### Task 4: `engine.py` — running `openrhyme … --json`

**Files:**
- Create: `src/openrhyme_mcp/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `Settings`, the `fake_engine` fixture.
- Produces: `class EngineError(Exception)` with `.code`, `.message`, `.hint`; `def run_cli(args: Sequence[str], *, settings: Settings, timeout: float = 10.0) -> dict[str, Any]` returning the envelope's `data`.

- [ ] **Step 1: Write the failing test**

`tests/test_engine.py`:
```python
from pathlib import Path

import pytest

from openrhyme_mcp.config import resolve
from openrhyme_mcp.engine import EngineError, run_cli


def test_returns_data_and_appends_json_flag(fake_engine: Path) -> None:
    settings = resolve()
    data = run_cli(["version"], settings=settings)
    assert data == {"engine": "0.1.0", "schema": 1}
    assert fake_engine.read_text().strip() == "version --json"


def test_passes_arguments_through(fake_engine: Path) -> None:
    data = run_cli(["apps", "allow", "com.apple.TextEdit"], settings=resolve())
    assert data["changed"] is True
    assert "com.apple.TextEdit" in data["allowlist"]
    assert fake_engine.read_text().strip() == "apps allow com.apple.TextEdit --json"


def test_engine_error_is_passed_through(fake_engine: Path) -> None:
    with pytest.raises(EngineError) as info:
        run_cli(["fail"], settings=resolve())
    assert (info.value.code, info.value.message, info.value.hint) == ("not_trusted", "no", "grant it")


def test_missing_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("OPENRHYME_BIN", raising=False)
    with pytest.raises(EngineError) as info:
        run_cli(["version"], settings=resolve())
    assert info.value.code == "engine_not_found"
    assert "OPENRHYME_BIN" in (info.value.hint or "")


def test_timeout(fake_engine: Path) -> None:
    with pytest.raises(EngineError) as info:
        run_cli(["hang"], settings=resolve(), timeout=0.5)
    assert info.value.code == "engine_timeout"


def test_bad_output(fake_engine: Path) -> None:
    with pytest.raises(EngineError) as info:
        run_cli(["garbage"], settings=resolve())
    assert info.value.code == "engine_bad_output"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_engine.py -q`
Expected: `ModuleNotFoundError: No module named 'openrhyme_mcp.engine'`.

- [ ] **Step 3: Implement**

`src/openrhyme_mcp/engine.py`:
```python
"""Run the engine CLI and unwrap its JSON envelope (engine spec §9)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from typing import Any

from .config import Settings


class EngineError(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def run_cli(args: Sequence[str], *, settings: Settings, timeout: float = 10.0) -> dict[str, Any]:
    """Run `openrhyme <args> --json` and return the envelope's `data`.

    Raises `EngineError` with the engine's own code when it reports `ok: false`, and with
    `engine_not_found`, `engine_timeout` or `engine_bad_output` for local failures.
    """
    if settings.engine_bin is None:
        raise EngineError(
            "engine_not_found",
            "The `openrhyme` binary was not found",
            "Set OPENRHYME_BIN or put `openrhyme` on PATH (lookup order: OPENRHYME_BIN, PATH)",
        )
    command = [str(settings.engine_bin), *args, "--json"]
    try:
        completed = subprocess.run(  # noqa: S603 — arguments are ours, no shell
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise EngineError("engine_not_found", f"Cannot execute {settings.engine_bin}") from exc
    except subprocess.TimeoutExpired as exc:
        raise EngineError("engine_timeout", f"`{' '.join(args)}` took longer than {timeout}s") from exc

    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise EngineError(
            "engine_bad_output",
            f"`{' '.join(args)}` did not return JSON (exit {completed.returncode})",
            completed.stderr.strip() or None,
        ) from exc

    if not isinstance(envelope, dict):
        raise EngineError("engine_bad_output", "Envelope is not an object")
    if envelope.get("ok") is True:
        data = envelope.get("data")
        return data if isinstance(data, dict) else {"result": data}
    error = envelope.get("error") or {}
    raise EngineError(
        str(error.get("code", "engine_error")),
        str(error.get("message", "engine reported a failure")),
        error.get("hint"),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -q`
Expected: 6 passed (the timeout test takes ~0.5 s).

- [ ] **Step 5: Lint, type-check, commit**

```bash
make check
git add src/openrhyme_mcp/engine.py tests/test_engine.py
git commit -m "Add engine CLI runner"
```

---

### Task 5: `server.py` — `events`, `status`, the resource, `main()`

**Files:**
- Create: `src/openrhyme_mcp/server.py`
- Modify: `pyproject.toml` (enable the `[project.scripts]` entry), `src/openrhyme_mcp/__init__.py` (add `__version__`)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `resolve`, `open_readonly`, `query_events`, `StoreError`, `run_cli`, `EngineError`, `timespec.parse`.
- Produces: module-level `mcp = MCPServer("openrhyme", instructions=…)`; tools `events(since, until, kinds, app, limit, max_value_chars) -> list[dict[str, Any]]`, `status() -> dict[str, Any]`; resource `openrhyme://events/recent -> str`; `def main() -> None`; console script `openrhyme-mcp`.

- [ ] **Step 1: Write the failing test**

`tests/test_server.py`:
```python
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from mcp.types import TextContent

from openrhyme_mcp.server import mcp


def payload(result: Any) -> Any:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return json.loads(content.text)


@pytest.mark.anyio
async def test_lists_tools_with_descriptions() -> None:
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert {"events", "status"} <= set(tools)
    assert "since" in json.dumps(tools["events"].inputSchema)
    assert tools["events"].description and "2h" in tools["events"].description


@pytest.mark.anyio
async def test_events_reads_store(seeded_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENRHYME_DATA_DIR", str(seeded_data_dir))
    async with Client(mcp) as client:
        result = await client.call_tool("events", {"since": "1756710200", "until": "1756710230"})
        assert not result.is_error
        rows = payload(result)
        assert [r["kind"] for r in rows] == ["element.focused", "element.value_changed",
                                             "element.focused", "element.value_changed"]
        assert rows[0]["app_name"] == "TextEdit"
        assert rows[0]["time"].startswith("2025-09-01T")

        capped = payload(await client.call_tool("events", {"since": "0", "max_value_chars": 10, "app": "com.apple.TextEdit"}))
        long_value = next(r["value"] for r in capped if r.get("value", "").startswith("xxxx"))
        assert long_value.endswith("[truncated 4990 chars]")


@pytest.mark.anyio
async def test_events_missing_db_is_tool_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENRHYME_DATA_DIR", str(tmp_path))
    async with Client(mcp) as client:
        result = await client.call_tool("events", {"since": "1h"})
    assert result.is_error
    text = result.content[0]
    assert isinstance(text, TextContent)
    assert "db_not_found" in text.text and "openrhyme daemon" in text.text


@pytest.mark.anyio
async def test_events_bad_time_is_tool_error() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("events", {"since": "yesterday"})
    assert result.is_error


@pytest.mark.anyio
async def test_status_merges_engine_and_mcp_info(fake_engine: Path, seeded_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENRHYME_DATA_DIR", str(seeded_data_dir))
    async with Client(mcp) as client:
        data = payload(await client.call_tool("status", {}))
    assert data["trusted"] is True
    assert data["event_count"] == 27
    assert data["mcp"]["schema_supported"] == 1
    assert data["mcp"]["db_present"] is True
    assert data["mcp"]["db_path"].endswith("events.sqlite")


@pytest.mark.anyio
async def test_status_without_engine_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("OPENRHYME_BIN", raising=False)
    async with Client(mcp) as client:
        result = await client.call_tool("status", {})
    assert result.is_error
    text = result.content[0]
    assert isinstance(text, TextContent)
    assert "engine_not_found" in text.text


@pytest.mark.anyio
async def test_recent_resource(seeded_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENRHYME_DATA_DIR", str(seeded_data_dir))
    async with Client(mcp) as client:
        resources = (await client.list_resources()).resources
        assert any(str(r.uri) == "openrhyme://events/recent" for r in resources)
        read = await client.read_resource("openrhyme://events/recent")
    body = read.contents[0]
    assert hasattr(body, "text")
    # The fixture is dated 2025, so "recent" (last 15 minutes) is empty JSONL.
    assert getattr(body, "text") == ""
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_server.py -q`
Expected: `ModuleNotFoundError: No module named 'openrhyme_mcp.server'`.

- [ ] **Step 3: Implement**

`src/openrhyme_mcp/__init__.py` — append after the docstring:
```python
__version__ = "0.1.0"
```

`src/openrhyme_mcp/server.py`:
```python
"""The MCP server: a thin, read-only door to the engine's event table (MCP spec §§2–6)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__
from .config import SUPPORTED_SCHEMA, resolve
from .engine import EngineError, run_cli
from .store import StoreError, open_readonly, query_events, schema_version
from .timespec import TimeSpecError, parse

mcp = MCPServer(
    "openrhyme",
    instructions=(
        "OpenRhyme records the user's activity on their Mac (allowlisted apps only) as raw "
        "events. Use `events` to read what they were doing in a time range; timestamps are "
        "local ISO-8601 in `time`. Values are truncated by default; pass max_value_chars=0 "
        "for full text when you really need it."
    ),
)


def _tool_error(code: str, message: str, hint: str | None) -> ToolError:
    text = f"{code}: {message}"
    if hint:
        text += f" (hint: {hint})"
    return ToolError(text)


def _parse_time(name: str, text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return parse(text)
    except TimeSpecError as exc:
        raise _tool_error(
            "usage", f"Cannot parse {name}={text!r}", "use 2h, 30m, unix seconds, or ISO-8601"
        ) from exc


@mcp.tool()
def events(
    since: str,
    until: str | None = None,
    kinds: list[str] | None = None,
    app: str | None = None,
    limit: int = 200,
    max_value_chars: int = 2000,
) -> list[dict[str, Any]]:
    """Raw activity events between `since` and `until`, oldest first.

    Times accept `2h` / `30m` / `1d` (that long ago), unix seconds, or ISO-8601.
    `kinds` filters by event kind (e.g. `app.activated`, `window.focused`,
    `context.snapshot`, `element.value_changed`); `app` by bundle identifier.
    `value`/`selected_text` are cut to `max_value_chars` (0 = full text). `limit` ≤ 2000.
    """
    settings = resolve()
    since_ts = _parse_time("since", since)
    until_ts = _parse_time("until", until)
    assert since_ts is not None
    try:
        conn = open_readonly(settings.db_path)
    except StoreError as exc:
        raise _tool_error(exc.code, exc.message, exc.hint) from exc
    try:
        return query_events(
            conn, since=since_ts, until=until_ts, kinds=kinds, app=app, limit=limit,
            max_value_chars=max_value_chars,
        )
    finally:
        conn.close()


@mcp.tool()
def status() -> dict[str, Any]:
    """Engine status (trust, daemon liveness, event count, allowlist) plus this server's
    view: supported schema, database path and whether it exists."""
    settings = resolve()
    try:
        data = run_cli(["status"], settings=settings)
    except EngineError as exc:
        raise _tool_error(exc.code, exc.message, exc.hint) from exc
    data["mcp"] = {
        "version": __version__,
        "schema_supported": SUPPORTED_SCHEMA,
        "db_path": str(settings.db_path),
        "db_present": settings.db_path.exists(),
    }
    return data


@mcp.resource("openrhyme://events/recent", mime_type="application/x-ndjson")
def recent_events() -> str:
    """The last 15 minutes of raw events as JSON Lines (values cut to 500 chars)."""
    settings = resolve()
    try:
        conn = open_readonly(settings.db_path)
    except StoreError as exc:
        raise _tool_error(exc.code, exc.message, exc.hint) from exc
    try:
        since = datetime.now(timezone.utc).timestamp() - 15 * 60
        rows = query_events(conn, since=since, limit=500, max_value_chars=500)
    finally:
        conn.close()
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


def _handshake() -> None:
    """Refuse to serve a store newer than we understand (MCP spec §7)."""
    settings = resolve()
    found: int | None = None
    if settings.engine_bin is not None:
        try:
            version = run_cli(["version"], settings=settings)
            found = int(version.get("schema", 0))
        except EngineError:
            found = None
    if found is None and settings.db_path.exists():
        conn = open_readonly(settings.db_path)
        try:
            found = schema_version(conn)
        finally:
            conn.close()
    if found is not None and found > SUPPORTED_SCHEMA:
        sys.stderr.write(
            f"openrhyme-mcp: schema_too_new — engine schema {found}, supported {SUPPORTED_SCHEMA}. "
            "Upgrade openrhyme-mcp.\n"
        )
        sys.exit(5)


def main() -> None:
    _handshake()
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
```

`pyproject.toml` — replace the commented block with:
```toml
[project.scripts]
openrhyme-mcp = "openrhyme_mcp.server:main"
```
Then `uv sync --all-groups` so the console script is installed into `.venv`.

Note on `open_readonly` in `_handshake`: it already raises `StoreError("schema_too_new")` for a newer store; let that propagate as a startup failure — it is the same refusal.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_server.py -q`
Expected: 7 passed. Then `uv run openrhyme-mcp --help 2>&1 | head -2 || true` just to confirm the console script resolves (it will start serving stdio and wait; Ctrl-C).

- [ ] **Step 5: Lint, type-check, commit**

```bash
make check
git add src/openrhyme_mcp pyproject.toml uv.lock tests/test_server.py
git commit -m "Add MCP server with events and status tools"
```

---

### Task 6: `apps`, `allow_app`, `deny_app` tools; README and CLAUDE.md

**Files:**
- Modify: `src/openrhyme_mcp/server.py`, `README.md` (Development / Using it sections), `CLAUDE.md` (State)
- Test: `tests/test_server_apps.py`

**Interfaces:**
- Produces: tools `apps() -> dict[str, Any]` (`{"allowlist": [...], "running": [...]}`), `allow_app(bundle_id: str) -> dict[str, Any]`, `deny_app(bundle_id: str) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing test**

`tests/test_server_apps.py`:
```python
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from mcp.types import TextContent

from openrhyme_mcp.server import mcp


def payload(result: Any) -> Any:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return json.loads(content.text)


@pytest.mark.anyio
async def test_apps_merges_list_and_running(fake_engine: Path) -> None:
    async with Client(mcp) as client:
        data = payload(await client.call_tool("apps", {}))
    assert data["allowlist"] == ["com.apple.Safari"]
    assert data["running"][0]["bundle_id"] == "com.apple.Safari"
    calls = fake_engine.read_text().splitlines()
    assert calls == ["apps list --json", "apps running --json"]


@pytest.mark.anyio
async def test_allow_and_deny_go_through_the_cli(fake_engine: Path) -> None:
    async with Client(mcp) as client:
        allowed = payload(await client.call_tool("allow_app", {"bundle_id": "com.apple.TextEdit"}))
        denied = payload(await client.call_tool("deny_app", {"bundle_id": "com.apple.Safari"}))
    assert allowed["changed"] is True and "com.apple.TextEdit" in allowed["allowlist"]
    assert denied["allowlist"] == []
    calls = fake_engine.read_text().splitlines()
    assert calls == ["apps allow com.apple.TextEdit --json", "apps deny com.apple.Safari --json"]


@pytest.mark.anyio
async def test_allow_rejects_non_bundle_identifier(fake_engine: Path) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("allow_app", {"bundle_id": "TextEdit"})
    assert result.is_error
    assert fake_engine.exists() is False or fake_engine.read_text() == ""
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_server_apps.py -q`
Expected: FAIL — unknown tool `apps`.

- [ ] **Step 3: Implement**

Append to `src/openrhyme_mcp/server.py` (after `status`):
```python
def _validated_bundle_id(bundle_id: str) -> str:
    candidate = bundle_id.strip()
    if "." not in candidate or " " in candidate:
        raise _tool_error(
            "usage",
            f"{bundle_id!r} is not a bundle identifier",
            "expected e.g. com.apple.TextEdit; call `apps` to see running apps",
        )
    return candidate


def _engine(args: list[str]) -> dict[str, Any]:
    try:
        return run_cli(args, settings=resolve())
    except EngineError as exc:
        raise _tool_error(exc.code, exc.message, exc.hint) from exc


@mcp.tool()
def apps() -> dict[str, Any]:
    """The capture allowlist and the currently running apps (with bundle identifiers, whether
    each is allowlisted, and whether it is an Electron app)."""
    allowlist = _engine(["apps", "list"]).get("allowlist", [])
    running = _engine(["apps", "running"]).get("apps", [])
    return {"allowlist": allowlist, "running": running}


@mcp.tool()
def allow_app(bundle_id: str) -> dict[str, Any]:
    """Add an app to the capture allowlist by bundle identifier (e.g. com.apple.Safari).
    Takes effect within a few seconds; the daemon reloads its config on each heartbeat."""
    return _engine(["apps", "allow", _validated_bundle_id(bundle_id)])


@mcp.tool()
def deny_app(bundle_id: str) -> dict[str, Any]:
    """Remove an app from the capture allowlist by bundle identifier."""
    return _engine(["apps", "deny", _validated_bundle_id(bundle_id)])
```

`README.md` — replace the **Development** and **Using it with an agent (planned)** sections with:
```markdown
## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
make sync        # uv sync --all-groups
make check       # ruff + mypy (strict) + pytest
uv run openrhyme-mcp   # serve on stdio (Ctrl-C to stop)
```

Tests run in-process against a fixture database and a fake `openrhyme` script; they never need the real engine or a macOS permission. CI runs on Ubuntu and macOS 26.

## Using it with an agent

With the engine built (`../OpenRhyme`, `make build`) and on `PATH` — or pointed to via `OPENRHYME_BIN`:

```sh
claude mcp add openrhyme -- uv run --directory /path/to/openrhyme-mcp openrhyme-mcp
```

Then ask: *"What was I doing between 2 and 3 pm?"* — the model calls `events(since="…", until="…")`. Tools: `events`, `status`, `apps`, `allow_app`, `deny_app`; resource: `openrhyme://events/recent`. Environment: `OPENRHYME_DATA_DIR` (engine data dir), `OPENRHYME_BIN` (engine binary).
```

`CLAUDE.md` — in **State**, replace "Workspace scaffolded, no implementation." with "Implemented per the MVP plan: `timespec`, `config`, `store`, `engine`, `server` with tools `events`/`status`/`apps`/`allow_app`/`deny_app` and the `openrhyme://events/recent` resource."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all tests pass (≈ 40).

- [ ] **Step 5: Lint, type-check, commit**

```bash
make check
git add src/openrhyme_mcp/server.py tests/test_server_apps.py README.md CLAUDE.md
git commit -m "Add apps tools and usage docs"
```

---

### Task 7: Dogfood against real engine data

This is the MCP spec's milestone 5 and the engine spec's milestone 6 — a manual task with a written outcome, not code.

**Files:**
- Create: `docs/superpowers/notes/2026-09-XX-raw-events-dogfood.md` (date it the day you run it)

- [ ] **Step 1: Capture a few hours with the engine** (engine Part 1 must be implemented): `openrhyme apps allow …` for 3–5 apps, `make run` in the engine repo, work normally.

- [ ] **Step 2: Register this server with Claude Code**

```sh
claude mcp add openrhyme -- uv run --directory "$(pwd)" openrhyme-mcp
```

- [ ] **Step 3: Ask the six questions and save the transcripts**

1. "What was I doing between 14:00 and 15:00 today?"
2. "Which documents or pages did I spend the most time in this afternoon?"
3. "Summarise my day so far in five bullets."
4. "When was I idle for more than 10 minutes?"
5. "What did I write in TextEdit?" (tests full-text handling)
6. "Which app did I switch to most often?"

- [ ] **Step 4: Write the note**

For each question record: whether the answer was correct (check against your memory and `openrhyme events`), how many `events` calls the model made and with what arguments, where it got confused (duplicated snapshots? truncated values? missing app switches?), and the total rows it pulled. End with a section "What compaction must do" listing the concrete transformations that would have made the answers better — this is the input to the compaction spec.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/notes
git commit -m "Record raw-events dogfood findings"
```

---

## Self-review (against the MCP spec)

| Spec section | Task |
|---|---|
| §2 process model, read-only URI open, works without daemon | 3, 5 |
| §3 modules | 1–5 |
| §4 tools `events`/`status`/`apps`/`allow_app`/`deny_app`, row shape (`time`, NULLs omitted, `extra` parsed), truncation, resource | 3, 5, 6 |
| §5 time grammar mirrored | 1 |
| §6 error handling (`db_not_found`, `schema_too_new` refusal, `engine_not_found`, envelope pass-through, `engine_timeout`) — as `ToolError` | 3, 4, 5 |
| §7 discovery + startup handshake | 2, 5 |
| §8 console script, `uvx`/`claude mcp add` | 5, 6 |
| §9 fixture DDL + column-drift test, fake binary, in-process `Client(server)` tests, Ubuntu CI | 3, 4, 5 |
| §10 milestones 1–5 | 1–7 |

**Placeholder scan:** none. **Type consistency:** `resolve() -> Settings` (`data_dir`, `engine_bin`, `db_path`, `config_path`), `open_readonly(path)`, `query_events(conn, since=, until=, kinds=, app=, limit=, max_value_chars=)`, `run_cli(args, settings=, timeout=)`, `EngineError`/`StoreError` with `.code/.message/.hint`, `parse(text, now=, tz=)` are used identically across tasks. The fake engine's canned responses (`version`, `status`, `apps list|running|allow|deny`, `fail`, `hang`, `garbage`) cover every `run_cli` path the tools exercise.
