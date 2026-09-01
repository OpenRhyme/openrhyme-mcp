# Contributing

Read the engine's spec and contract first — most design questions are answered there:

- https://github.com/OpenRhyme/OpenRhyme/blob/main/docs/computer-history-spec.md
- https://github.com/OpenRhyme/OpenRhyme/blob/main/docs/engine-interface.md

## Prerequisites

Python 3.12+ and [uv](https://docs.astral.sh/uv/). `uv sync --all-groups` installs everything, including the dev tools.

## Workflow

```sh
make check   # ruff check + ruff format --check + mypy --strict + pytest
make format  # rewrite files with ruff
```

CI runs `make check` equivalents on Ubuntu and macOS 26 with `uv sync --locked`, so commit `uv.lock` whenever dependencies change.

## Conventions

- Python 3.12+, fully typed, `mypy --strict` clean. No `Any` leaking out of the store layer.
- The engine's SQLite files are opened **read-only** (`file:…?mode=ro`, URI mode). This server never writes to the store; anything that changes engine state goes through `openrhyme … --json`.
- No capture logic, no macOS permission requests, no outbound network calls. If a change needs any of those, it belongs in the engine or nowhere.
- Tests run against a fixture SQLite database and a fake `openrhyme` binary on `PATH`; they never require the real engine or a macOS permission grant.
- Prefer the official `mcp` SDK's `MCPServer` (v2) with the stdio transport; do not hand-roll protocol handling.

## Commits and pull requests

- One logical change per commit, short single-line message describing what changed. No attribution trailers or generated-by footers.
- If a change relies on a schema or CLI field the engine does not ship yet, land the engine side first and link it.
