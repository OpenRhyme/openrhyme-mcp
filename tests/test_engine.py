import stat
from pathlib import Path

import pytest

from openrhyme_mcp.config import resolve
from openrhyme_mcp.engine import EngineError, run_cli


def test_returns_data_and_appends_json_flag(fake_engine: Path) -> None:
    settings = resolve()
    data = run_cli(["version"], settings=settings)
    assert data == {"engine": "0.1.0", "schema": 1}
    assert fake_engine.read_text().strip() == "version --json"


def test_passes_arguments_through(fake_engine: Path) -> None:
    data = run_cli(["apps", "allow", "com.apple.TextEdit"], settings=resolve())
    assert data["changed"] is True
    assert "com.apple.TextEdit" in data["allowlist"]
    assert fake_engine.read_text().strip() == "apps allow com.apple.TextEdit --json"


def test_engine_error_is_passed_through(fake_engine: Path) -> None:
    with pytest.raises(EngineError) as info:
        run_cli(["fail"], settings=resolve())
    assert (info.value.code, info.value.message, info.value.hint) == (
        "not_trusted",
        "no",
        "grant it",
    )


def test_missing_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("OPENRHYME_BIN", raising=False)
    with pytest.raises(EngineError) as info:
        run_cli(["version"], settings=resolve())
    assert info.value.code == "engine_not_found"
    assert "OPENRHYME_BIN" in (info.value.hint or "")


def test_timeout(fake_engine: Path) -> None:
    with pytest.raises(EngineError) as info:
        run_cli(["hang"], settings=resolve(), timeout=0.5)
    assert info.value.code == "engine_timeout"


def test_bad_output(fake_engine: Path) -> None:
    with pytest.raises(EngineError) as info:
        run_cli(["garbage"], settings=resolve())
    assert info.value.code == "engine_bad_output"


def _fake_binary(path: Path, stdout: str) -> Path:
    path.write_text(f"#!/bin/sh\necho '{stdout}'\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_error_field_not_a_dict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _fake_binary(tmp_path / "openrhyme", '{"ok": false, "error": "boom"}')
    monkeypatch.setenv("OPENRHYME_BIN", str(script))
    with pytest.raises(EngineError) as info:
        run_cli(["version"], settings=resolve())
    assert info.value.code == "engine_error"


def test_error_code_null_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _fake_binary(tmp_path / "openrhyme", '{"ok": false, "error": {"code": null}}')
    monkeypatch.setenv("OPENRHYME_BIN", str(script))
    with pytest.raises(EngineError) as info:
        run_cli(["version"], settings=resolve())
    assert info.value.code == "engine_error"


def test_non_executable_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "openrhyme"
    script.write_text("#!/bin/sh\necho hi\n")
    monkeypatch.setenv("OPENRHYME_BIN", str(script))
    with pytest.raises(EngineError) as info:
        run_cli(["version"], settings=resolve())
    assert info.value.code == "engine_not_found"


def test_non_dict_data_is_wrapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _fake_binary(tmp_path / "openrhyme", '{"ok": true, "data": [1, 2, 3]}')
    monkeypatch.setenv("OPENRHYME_BIN", str(script))
    data = run_cli(["version"], settings=resolve())
    assert data == {"result": [1, 2, 3]}


def test_top_level_array_is_bad_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _fake_binary(tmp_path / "openrhyme", "[1, 2]")
    monkeypatch.setenv("OPENRHYME_BIN", str(script))
    with pytest.raises(EngineError) as info:
        run_cli(["version"], settings=resolve())
    assert info.value.code == "engine_bad_output"
