import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent

from openrhyme_mcp.server import _handshake, mcp
from tests.conftest import DDL


def payload(result: Any) -> Any:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return json.loads(content.text)


@pytest.mark.anyio
async def test_lists_tools_with_descriptions() -> None:
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert {"events", "status"} <= set(tools)
    assert "since" in json.dumps(tools["events"].input_schema)
    assert tools["events"].description and "2h" in tools["events"].description


@pytest.mark.anyio
async def test_events_reads_store(seeded_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENRHYME_DATA_DIR", str(seeded_data_dir))
    async with Client(mcp) as client:
        result = await client.call_tool("events", {"since": "1756710200", "until": "1756710230"})
        assert not result.is_error
        body = payload(result)
        rows = body["events"]
        assert [r["kind"] for r in rows] == [
            "element.focused",
            "element.value_changed",
            "element.focused",
            "element.value_changed",
        ]
        assert rows[0]["app_name"] == "TextEdit"
        assert rows[0]["time"].startswith("2025-09-01T")
        assert body["count"] == len(rows)

        capped_result = await client.call_tool(
            "events", {"since": "0", "max_value_chars": 10, "app": "com.apple.TextEdit"}
        )
        capped = payload(capped_result)["events"]
        long_value = next(r["value"] for r in capped if r.get("value", "").startswith("xxxx"))
        assert long_value.endswith("[truncated 4990 chars]")


@pytest.mark.anyio
async def test_events_missing_db_is_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
async def test_recent_resource(seeded_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENRHYME_DATA_DIR", str(seeded_data_dir))
    async with Client(mcp) as client:
        resources = (await client.list_resources()).resources
        assert any(str(r.uri) == "openrhyme://events/recent" for r in resources)
        read = await client.read_resource("openrhyme://events/recent")
    body = read.contents[0]
    assert hasattr(body, "text")
    # The fixture is dated 2025, so "recent" (last 15 minutes) is empty JSONL.
    assert body.text == ""


@pytest.mark.anyio
async def test_recent_resource_missing_db_raises_resource_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENRHYME_DATA_DIR", str(tmp_path))
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
