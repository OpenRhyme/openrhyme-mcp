"""The MCP server: a thin, read-only door to the engine's event table (MCP spec §§2-6)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from . import __version__
from .config import SUPPORTED_SCHEMA, resolve
from .engine import EngineError, run_cli
from .store import StoreError, open_readonly, schema_version
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


def _resource_error(code: str, message: str, hint: str | None) -> ResourceError:
    text = f"{code}: {message}"
    if hint:
        text += f" (hint: {hint})"
    return ResourceError(text)


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
) -> dict[str, Any]:
    """Raw activity events between `since` and `until`, oldest first.

    Returns {"events": [...rows...], "count": N}; each row's keys are the event columns
    (bundle_id, app_name, window_title, kind, value, ...) and a local-ISO "time" field. Read
    result["events"] for the rows.

    Times accept `2h` / `30m` / `1d` (that long ago), unix seconds, or ISO-8601.
    `kinds` filters by event kind (e.g. `app.activated`, `window.focused`,
    `context.snapshot`, `element.value_changed`); `app` by bundle identifier.
    `value`/`selected_text` are redacted by the engine and cut to `max_value_chars` (0 = full
    text). `limit` ≤ 2000.
    """
    _parse_time("since", since)
    _parse_time("until", until)
    args: list[str] = ["events", "--since", since]
    if until is not None:
        args += ["--until", until]
    for kind in kinds or []:
        args += ["--kind", kind]
    if app is not None:
        args += ["--app", app]
    args += ["--limit", str(limit), "--max-value-chars", str(max_value_chars)]
    data = _engine(args)
    return {"events": data.get("events", []), "count": data.get("count", 0)}


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


@mcp.resource("openrhyme://events/recent", mime_type="application/x-ndjson")
def recent_events() -> str:
    """The last 15 minutes of raw events as JSON Lines (values cut to 500 chars)."""
    settings = resolve()
    since = datetime.now(UTC).timestamp() - 15 * 60
    args = ["events", "--since", str(since), "--limit", "500", "--max-value-chars", "500"]
    try:
        data = run_cli(args, settings=settings)
    except EngineError as exc:
        raise _resource_error(exc.code, exc.message, exc.hint) from exc
    rows = data.get("events", [])
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
        try:
            conn = open_readonly(settings.db_path)
        except StoreError as exc:
            if exc.code == "schema_too_new":
                sys.stderr.write(
                    f"openrhyme-mcp: {exc.code} — {exc.message}. Upgrade openrhyme-mcp.\n"
                )
                sys.exit(5)
            # db_not_found (no database yet): no schema known, proceed.
        else:
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
