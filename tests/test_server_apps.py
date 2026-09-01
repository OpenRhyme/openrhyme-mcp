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
