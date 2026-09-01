# openrhyme-mcp

A thin [Model Context Protocol](https://modelcontextprotocol.io) server that exposes the timeline captured by the [OpenRhyme engine](https://github.com/OpenRhyme/OpenRhyme) to any agent — Claude, local models, your own scripts — over stdio.

> **Status:** pre-implementation. The workspace is scaffolded; no server code exists yet. The engine it depends on is at the same stage.

## What it is, and is not

OpenRhyme is a local-first, open-source "Computer History" for macOS: a Swift daemon reads your activity through the accessibility API, stores it in tiered SQLite, and compacts it without any bundled model. This repository is the **agent-facing door** to that data. It:

- speaks MCP over **stdio**, so any agent host (Claude Desktop, Claude Code, …) can spawn it;
- reads the engine's SQLite tiers **read-only**;
- shells out to the `openrhyme` CLI (`openrhyme <cmd> --json`) for control commands;
- contains **no capture logic**, holds **no macOS permissions**, and makes **no network calls**.

Everything stays on your machine. The agent host that spawns this server is the trust boundary — it sees exactly what the tools return, nothing more.

## How the pieces fit

```
 openrhyme daemon (Swift, launchd)  ──writes──▶  ~/Library/Application Support/OpenRhyme/*.sqlite
                                                              │
                                                   read-only  │        `openrhyme … --json`
                                                              ▼                 │
                          agent host ──stdio──▶  openrhyme-mcp (this repo) ─────┘
```

The full process topology, the CLI/JSON contract, and the store layout are specified in the engine repo: [`docs/engine-interface.md`](https://github.com/OpenRhyme/OpenRhyme/blob/main/docs/engine-interface.md). That document is the contract this server implements; it is not duplicated here.

## Planned tools

| MCP tool | Backed by |
|---|---|
| `timeline(since, until)` | sessions in `warm.sqlite` |
| `search(query)` | FTS5 `MATCH` over `warm.sqlite` (embeddings later) |
| `now()` | the last few minutes of `hot.sqlite` |
| `status()` | `openrhyme status --json` |
| `allow_app(bundle_id)` / `deny_app(bundle_id)` | `openrhyme apps … --json` |
| `compact()` | `openrhyme compact --json` |

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

## License

MIT — see [LICENSE](LICENSE).
