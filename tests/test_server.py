import json
import sqlite3
import stat
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent

from openrhyme_mcp import server
from openrhyme_mcp.server import _handshake, mcp
from tests.conftest import DDL


def payload(result: Any) -> Any:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return json.loads(content.text)


def _error_binary(path: Path, code: str, message: str, hint: str) -> Path:
    """A fake `openrhyme` that always answers `--json` calls with one error envelope."""
    path.write_text(
        "#!/bin/sh\n"
        f'echo \'{{"ok":false,"error":{{"code":"{code}","message":"{message}",'
        f'"hint":"{hint}"}}}}\'\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.mark.anyio
async def test_lists_tools_with_descriptions() -> None:
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert {"events", "status"} <= set(tools)
    assert "since" in json.dumps(tools["events"].input_schema)
    assert tools["events"].description and "2h" in tools["events"].description


@pytest.mark.anyio
async def test_events_reads_via_call_tool(fake_engine: Path) -> None:
    """End-to-end: MCP `call_tool("events", ...)` runs the (fake) CLI and passes its
    response straight through, byte-identical `{"events": [...], "count": N}` shape."""
    async with Client(mcp) as client:
        result = await client.call_tool(
            "events", {"since": "1h", "app": "com.apple.Safari", "max_value_chars": 100}
        )
        assert not result.is_error
        body = payload(result)
    assert body == {
        "events": [
            {
                "id": 1,
                "kind": "app.activated",
                "pid": 10,
                "bundle_id": "com.apple.Safari",
                "app_name": "Safari",
                "value": "hi",
                "time": "2025-09-01T00:00:00-07:00",
            }
        ],
        "count": 1,
    }
    call = fake_engine.read_text().strip().splitlines()[-1].split()
    assert call[0] == "events"
    assert call[call.index("--since") + 1] == "1h"
    assert call[call.index("--app") + 1] == "com.apple.Safari"
    assert call[call.index("--limit") + 1] == "200"
    assert call[call.index("--max-value-chars") + 1] == "100"


@pytest.mark.anyio
async def test_events_missing_db_is_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `db_not_found` engine failure surfaces as a ToolError, not an empty result."""
    _error_binary(
        tmp_path / "openrhyme",
        "db_not_found",
        "No event database",
        "Start `openrhyme daemon`",
    )
    monkeypatch.setenv("OPENRHYME_BIN", str(tmp_path / "openrhyme"))
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


def test_events_reads_through_the_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run_cli(args: Any, *, settings: Any, timeout: float = 10.0) -> dict[str, Any]:
        calls.append(list(args))
        return {"events": [{"id": 1, "kind": "context.snapshot", "value": "hi"}], "count": 1}

    monkeypatch.setattr(server, "run_cli", fake_run_cli)
    result = server.events(since="1h", app="com.apple.Safari", limit=50, max_value_chars=100)

    assert result == {"events": [{"id": 1, "kind": "context.snapshot", "value": "hi"}], "count": 1}
    assert calls[0][0] == "events"
    assert "--since" in calls[0] and "1h" in calls[0]
    assert "--app" in calls[0] and "com.apple.Safari" in calls[0]
    assert "--limit" in calls[0] and "50" in calls[0]
    assert "--max-value-chars" in calls[0] and "100" in calls[0]


def test_events_passes_each_kind_as_its_own_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run_cli(args: Any, *, settings: Any, timeout: float = 10.0) -> dict[str, Any]:
        calls.append(list(args))
        return {"events": [], "count": 0}

    monkeypatch.setattr(server, "run_cli", fake_run_cli)
    server.events(since="1h", kinds=["window.focused", "app.activated"])
    assert calls[0].count("--kind") == 2
    assert "window.focused" in calls[0] and "app.activated" in calls[0]


def test_events_no_longer_opens_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("events must not open SQLite directly")

    monkeypatch.setattr(server, "open_readonly", explode, raising=False)
    monkeypatch.setattr(
        server, "run_cli", lambda args, *, settings, timeout=10.0: {"events": [], "count": 0}
    )
    assert server.events(since="1h") == {"events": [], "count": 0}


@pytest.mark.anyio
async def test_status_merges_engine_and_mcp_info(
    fake_engine: Path, seeded_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENRHYME_DATA_DIR", str(seeded_data_dir))
    async with Client(mcp) as client:
        data = payload(await client.call_tool("status", {}))
    assert data["trusted"] is True
    assert data["event_count"] == 27
    assert data["mcp"]["schema_supported"] == 1
    assert data["mcp"]["db_present"] is True
    assert data["mcp"]["db_path"].endswith("events.sqlite")


@pytest.mark.anyio
async def test_status_without_engine_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("OPENRHYME_BIN", raising=False)
    async with Client(mcp) as client:
        result = await client.call_tool("status", {})
    assert result.is_error
    text = result.content[0]
    assert isinstance(text, TextContent)
    assert "engine_not_found" in text.text


@pytest.mark.anyio
async def test_recent_resource(fake_engine: Path) -> None:
    async with Client(mcp) as client:
        resources = (await client.list_resources()).resources
        assert any(str(r.uri) == "openrhyme://events/recent" for r in resources)
        read = await client.read_resource("openrhyme://events/recent")
    body = read.contents[0]
    assert hasattr(body, "text")
    lines = [json.loads(line) for line in body.text.splitlines() if line]
    assert lines == [
        {
            "id": 1,
            "kind": "app.activated",
            "pid": 10,
            "bundle_id": "com.apple.Safari",
            "app_name": "Safari",
            "value": "hi",
            "time": "2025-09-01T00:00:00-07:00",
        }
    ]
    call = fake_engine.read_text().strip().splitlines()[-1].split()
    assert call[0] == "events"
    assert call[call.index("--limit") + 1] == "500"
    assert call[call.index("--max-value-chars") + 1] == "500"


@pytest.mark.anyio
async def test_recent_resource_missing_db_raises_resource_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _error_binary(
        tmp_path / "openrhyme",
        "db_not_found",
        "No event database",
        "Start `openrhyme daemon`",
    )
    monkeypatch.setenv("OPENRHYME_BIN", str(tmp_path / "openrhyme"))
    async with Client(mcp) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.read_resource("openrhyme://events/recent")
    assert "db_not_found" in str(exc_info.value)


def test_handshake_exits_5_on_schema_too_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = sqlite3.connect(data_dir / "events.sqlite")
    conn.executescript(DDL)
    conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("OPENRHYME_DATA_DIR", str(data_dir))
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.delenv("OPENRHYME_BIN", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        _handshake()
    assert exc_info.value.code == 5


def test_handshake_ignores_missing_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("OPENRHYME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.delenv("OPENRHYME_BIN", raising=False)

    _handshake()  # must not raise: no database yet is not fatal
