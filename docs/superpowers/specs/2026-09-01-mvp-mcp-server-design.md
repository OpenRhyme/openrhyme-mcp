# openrhyme-mcp MVP — server design

**Status:** approved design, 2026-09-01.
**Depends on:** the engine MVP spec, `OpenRhyme/docs/superpowers/specs/2026-09-01-mvp-capture-engine-design.md` — specifically §5 (event model), §7.1 (schema v1), §8 (paths), §9 (CLI JSON envelope). This server implements that contract; it defines nothing of its own on disk.

## 1. Goal and non-goals

**Goal.** Let any MCP-capable agent host read the raw event table the engine writes, so a model can be asked questions about the user's day from unprocessed data. This is the "stream it to the model and see what happens" half of the MVP.

**Non-goals:** live push / subscriptions, summarisation, search beyond simple filters, writing to the store, any capture, any macOS permission, any network call.

## 2. Process model

The agent host spawns `openrhyme-mcp` over **stdio** for the duration of a session. It:
- opens `events.sqlite` **read-only** (`file:<path>?mode=ro`, URI mode) — the engine daemon is the single writer; WAL makes this safe with no coordination;
- runs `openrhyme <cmd> --json` via `subprocess` for anything that needs engine logic or state;
- holds no macOS permission and never touches the Accessibility API.

Works whether or not the daemon is running; if the database is missing, `events` returns a clear error naming the path.

## 3. Modules

```
src/openrhyme_mcp/
  __init__.py     package docstring, __version__
  config.py       data dir + binary resolution (§7), SUPPORTED_SCHEMA = 1
  store.py        read-only sqlite3 access: open, schema check, query_events()
  engine.py       run_cli(args) -> dict: locate binary, run with --json, parse envelope, map errors
  timespec.py     parse "2h" / ISO-8601 / unix seconds → float unix seconds (mirrors the engine's TimeSpec)
  server.py       MCPServer("openrhyme"), tools + resource, main()
tests/
  conftest.py     fixtures: temp data dir with events.sqlite built from schema v1 DDL, fake `openrhyme` script on PATH
  test_store.py · test_timespec.py · test_engine.py · test_server.py
```
`store.py` and `engine.py` are pure functions over their inputs so they are testable without MCP.

## 4. Tools

All tools are `@mcp.tool()` functions with type hints (the SDK derives the schema) and docstrings written for the model, not for developers.

| Tool | Signature | Backed by | Notes |
|---|---|---|---|
| `events` | `(since, until=None, kinds=None, app=None, limit=200, max_value_chars=2000) -> dict` returning `{"events": [...rows...], "count": n}` | `store.query_events` | Rows ordered by `ts, id`; `limit` capped at 2000. `value` and `selected_text` are **truncated to `max_value_chars`** with a `…[truncated N chars]` suffix — the store holds full text, tool results must not blow the model's context. `max_value_chars=0` disables truncation |
| `status` | `() -> dict` | `openrhyme status --json` | The engine's `data` object verbatim, plus `mcp: {version, schema_supported, db_path, db_present}` |
| `apps` | `() -> dict` | `openrhyme apps list --json` + `apps running --json` | `{allowlist: [...], running: [...]}` |
| `allow_app` / `deny_app` | `(bundle_id: str) -> dict` | `openrhyme apps allow\|deny` | The only state-changing tools; they go through the CLI, never the config file |

Row shape returned by `events`: the schema v1 column names as keys, `null`s omitted, `extra` parsed into an object, `ts` as a float **and** `time` as an ISO-8601 string in the local zone (models reason better with readable timestamps).

**Resource:** `openrhyme://events/recent` → the last 15 minutes as JSONL text with `max_value_chars=500`, for hosts that surface resources.

## 5. Time arguments
`since`/`until` accept exactly what the engine CLI accepts: ISO-8601 (with or without zone; without → local), unix seconds, or a relative duration `30m` / `2h` / `1d` meaning "that long ago". Parsing lives in `timespec.py` and its test table mirrors the engine's so the two never drift.

## 6. Error handling
- Database missing → tool error `db_not_found` with the path and the hint "start `openrhyme daemon` and allow an app".
- `meta.schema_version` > `SUPPORTED_SCHEMA` → the server **refuses to start** with a clear message (`schema_too_new`); older is fine.
- Binary not found → `events` still works; `status`/`apps`/`allow_app`/`deny_app` return `engine_not_found` with the lookup order tried.
- Engine envelope `ok: false` → the engine's `error.code`/`message`/`hint` are passed through unchanged.
- `subprocess` timeout 10 s → `engine_timeout`.
Errors are raised as `mcp.server.mcpserver.exceptions.ToolError` so the host shows them to the model as tool failures, not crashes.

## 7. Discovery and configuration

| Setting | Resolution order |
|---|---|
| Data dir | `OPENRHYME_DATA_DIR` → `~/Library/Application Support/OpenRhyme/` |
| Engine binary | `OPENRHYME_BIN` → `openrhyme` on `PATH` |
| Startup handshake | if the binary is found: `openrhyme version --json`; refuse if `schema > SUPPORTED_SCHEMA`. If not found: check `meta.schema_version` in the database instead |

No config file of its own.

## 8. Packaging and use
- Console script `openrhyme-mcp = "openrhyme_mcp.server:main"` (enable the commented entry in `pyproject.toml`); `main()` calls `mcp.run()` (stdio default).
- Intended registration once published: `claude mcp add openrhyme -- uvx openrhyme-mcp`; during development: `claude mcp add openrhyme -- uv run --directory <repo> openrhyme-mcp`.
- Runtime dependency stays `mcp>=2.1,<3`; no others.

## 9. Testing
- `conftest.py` builds a temp data dir: `events.sqlite` from the **schema v1 DDL copied verbatim from the engine spec §7.1** (a comment marks the source; a test asserts `PRAGMA table_info(events)` on the fixture matches the column list hard-coded in `store.py`, so the fixture and the reader cannot drift from each other — cross-repo drift against the real engine is caught by the dogfood milestone), seeded with ~30 events across two apps and several kinds; and a fake `openrhyme` shell script on `PATH` that answers `version`, `status`, `apps …` with canned envelopes and records its argv.
- `test_store.py`: filters, ordering, limit cap, truncation, `time` formatting, read-only enforcement (an `INSERT` through the connection must fail).
- `test_engine.py`: envelope parsing, error mapping, missing binary, timeout.
- `test_server.py`: exercise the tools through the SDK's in-memory client — `mcp.client.Client(server)` accepts the `MCPServer` instance directly (mcp 2.1.1, `mcp/client/client.py`) — so the tool schema and results the model sees are what is tested. Tool failures are raised as `mcp.server.mcpserver.exceptions.ToolError`.
- Never requires the real engine or macOS; CI stays green on Ubuntu.

## 10. Milestones
1. `timespec`, `store`, `config` with tests (no MCP yet).
2. `engine` runner with the fake binary.
3. `server` with `events` + `status`; in-memory client tests; console script enabled.
4. `apps`, `allow_app`, `deny_app`, the resource; README usage section.
5. Dogfood: register with Claude Code against a day of real engine data; record what the model does with raw events — that write-up feeds the engine's compaction spec.

## 11. References
Engine MVP spec (contract) · `OpenRhyme/docs/engine-interface.md` (topology, envelope) · MCP Python SDK v2 README (`MCPServer`, `@mcp.tool()`, `@mcp.resource()`, `mcp.run()`).
