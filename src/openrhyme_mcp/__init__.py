"""OpenRhyme MCP server.

A thin Model Context Protocol server, spoken over stdio, that exposes the timeline
captured by the OpenRhyme engine (https://github.com/OpenRhyme/OpenRhyme) to any agent.

It contains no capture logic and holds no macOS permissions. It reads the engine's
SQLite tiers read-only and shells out to the ``openrhyme`` CLI for control commands.
The contract it depends on is documented in the engine repo: docs/engine-interface.md.
"""
