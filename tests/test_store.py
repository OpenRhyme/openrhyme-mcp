import sqlite3
from pathlib import Path

import pytest

from openrhyme_mcp.store import StoreError, open_readonly, schema_version


def test_schema_version_and_read_only(seeded_data_dir: Path) -> None:
    conn = open_readonly(seeded_data_dir / "events.sqlite")
    assert schema_version(conn) == 1
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO events (ts, kind) VALUES (1, 'x')")


def test_missing_db_raises_store_error(tmp_path: Path) -> None:
    with pytest.raises(StoreError) as info:
        open_readonly(tmp_path / "events.sqlite")
    assert info.value.code == "db_not_found"
    assert "openrhyme daemon" in (info.value.hint or "")


def test_newer_schema_is_refused(seeded_data_dir: Path) -> None:
    rw = sqlite3.connect(seeded_data_dir / "events.sqlite")
    rw.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    rw.commit()
    rw.close()
    with pytest.raises(StoreError) as info:
        open_readonly(seeded_data_dir / "events.sqlite")
    assert info.value.code == "schema_too_new"
