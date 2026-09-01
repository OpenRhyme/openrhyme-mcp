# openrhyme-mcp — instructions for Claude Code

## What this is

A thin MCP server (Python, stdio) exposing the OpenRhyme engine's computer-history timeline to any agent. The engine — a Swift daemon that captures macOS activity via the Accessibility API — lives in a sibling repo: https://github.com/OpenRhyme/OpenRhyme (locally `../OpenRhyme`). This repo is the agent-facing door only.

## State (2026-09-01)

Workspace scaffolded, no implementation. `src/openrhyme_mcp/__init__.py` is a docstring-only stub. **The MVP design is approved: `docs/superpowers/specs/2026-09-01-mvp-mcp-server-design.md`** — read it before implementing; plans live in `docs/superpowers/plans/`. The engine is also pre-implementation; build against its MVP spec (`../OpenRhyme/docs/superpowers/specs/2026-09-01-mvp-capture-engine-design.md`, schema v1 in §7.1) and fixture data.

## Non-negotiables

- **Read-only on the store.** Open SQLite with `sqlite3.connect("file:…?mode=ro", uri=True)`. Never write; never create tables. State changes go through `openrhyme <cmd> --json` via `subprocess`.
- **No capture logic, no macOS permission requests, no network calls.** If a task needs any of those, stop and say so — it belongs in the engine.
- **The contract is the engine's.** The SQLite schema, the CLI subcommands, the JSON envelope (`{"ok": …, "data"|"error"}`) and the store path are defined in the engine repo's `docs/engine-interface.md`. Do not invent fields here; if something is missing, it is an engine change first.
- Handshake on start: `openrhyme version --json` → refuse to serve if `schema` is newer than this package understands.

## Layout

- `src/openrhyme_mcp/` — the package. Planned modules: `server.py` (MCPServer + tool definitions, `main()`), `store.py` (read-only SQLite queries), `engine.py` (locate + run the CLI, parse the envelope), `config.py` (`OPENRHYME_BIN`, store path).
- `tests/` — pytest; fixtures provide a temporary SQLite store and a fake `openrhyme` script on `PATH`. Tests must never need the real engine.
- Console entry point (to enable in `pyproject.toml` once `server.py` exists): `openrhyme-mcp = "openrhyme_mcp.server:main"`.

## Commands

`make sync` · `make lint` · `make typecheck` · `make test` · `make check` (all three). CI runs the same on `ubuntu-latest` and `macos-26` with `uv sync --locked`; commit `uv.lock` with any dependency change.

## Conventions

- Python 3.12+, `mypy --strict`, ruff (rules in `pyproject.toml`), line length 100.
- Use the official `mcp` SDK v2 (`MCPServer`, stdio transport). Pin stays `mcp>=2.1,<3`.
- Binary lookup order: `OPENRHYME_BIN` env var, then `openrhyme` on `PATH`.
- Commits: short single-line messages, no attribution trailers.
