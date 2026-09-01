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
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (FileNotFoundError, PermissionError) as exc:
        raise EngineError("engine_not_found", f"Cannot execute {settings.engine_bin}") from exc
    except subprocess.TimeoutExpired as exc:
        raise EngineError(
            "engine_timeout", f"`{' '.join(args)}` took longer than {timeout}s"
        ) from exc

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

    error_obj = envelope.get("error")
    error = error_obj if isinstance(error_obj, dict) else {}
    code = error.get("code")
    code = str(code) if isinstance(code, str) else "engine_error"
    message = error.get("message")
    message = message if isinstance(message, str) else "engine reported a failure"
    hint = error.get("hint")
    hint = hint if isinstance(hint, str) else None
    raise EngineError(code, message, hint)
