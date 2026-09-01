"""Where the engine's files and binary are (engine spec §8; MCP spec §7)."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SUPPORTED_SCHEMA: Final = 1


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    engine_bin: Path | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "events.sqlite"

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.json"


def _expand(path_str: str, source: Mapping[str, str]) -> Path:
    if path_str == "~" or path_str.startswith("~/"):
        home = source.get("HOME") or str(Path.home())
        return Path(home) / path_str[2:] if path_str.startswith("~/") else Path(home)
    return Path(path_str)


def resolve(env: Mapping[str, str] | None = None) -> Settings:
    """Resolve settings from `env` (defaults to the process environment)."""
    source = os.environ if env is None else env
    override = source.get("OPENRHYME_DATA_DIR", "").strip()
    if override:
        data_dir = _expand(override, source)
    else:
        home = Path(source.get("HOME", "")).expanduser() if source.get("HOME") else Path.home()
        data_dir = home / "Library" / "Application Support" / "OpenRhyme"

    engine_bin: Path | None = None
    explicit = source.get("OPENRHYME_BIN", "").strip()
    if explicit:
        engine_bin = _expand(explicit, source)
    else:
        found = shutil.which("openrhyme", path=source.get("PATH", ""))
        engine_bin = Path(found) if found else None

    return Settings(data_dir=data_dir, engine_bin=engine_bin)
