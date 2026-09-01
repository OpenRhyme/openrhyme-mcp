import stat
from pathlib import Path

from openrhyme_mcp.config import SUPPORTED_SCHEMA, resolve


def test_supported_schema_is_one() -> None:
    assert SUPPORTED_SCHEMA == 1


def test_env_overrides_win(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin" / "openrhyme"
    fake_bin.parent.mkdir()
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IEXEC)
    settings = resolve(
        {"OPENRHYME_DATA_DIR": str(tmp_path / "data"), "OPENRHYME_BIN": str(fake_bin)}
    )
    assert settings.data_dir == tmp_path / "data"
    assert settings.db_path == tmp_path / "data" / "events.sqlite"
    assert settings.config_path == tmp_path / "data" / "config.json"
    assert settings.engine_bin == fake_bin


def test_default_data_dir_and_path_lookup(tmp_path: Path) -> None:
    on_path = tmp_path / "openrhyme"
    on_path.write_text("#!/bin/sh\n")
    on_path.chmod(on_path.stat().st_mode | stat.S_IEXEC)
    settings = resolve({"PATH": str(tmp_path), "HOME": str(tmp_path)})
    assert settings.data_dir == tmp_path / "Library" / "Application Support" / "OpenRhyme"
    assert settings.engine_bin == on_path


def test_missing_binary_is_none(tmp_path: Path) -> None:
    settings = resolve({"PATH": str(tmp_path), "HOME": str(tmp_path)})
    assert settings.engine_bin is None


def test_tilde_data_dir_uses_injected_home(tmp_path: Path) -> None:
    settings = resolve({"OPENRHYME_DATA_DIR": "~/data", "HOME": str(tmp_path)})
    assert settings.data_dir == tmp_path / "data"


def test_tilde_engine_bin_uses_injected_home(tmp_path: Path) -> None:
    settings = resolve({"OPENRHYME_BIN": "~/bin/openrhyme", "HOME": str(tmp_path)})
    assert settings.engine_bin == tmp_path / "bin" / "openrhyme"


def test_omitted_path_does_not_leak_real_path(tmp_path: Path) -> None:
    on_path = tmp_path / "openrhyme"
    on_path.write_text("#!/bin/sh\n")
    on_path.chmod(on_path.stat().st_mode | stat.S_IEXEC)
    settings = resolve({"HOME": str(tmp_path)})
    assert settings.engine_bin is None
