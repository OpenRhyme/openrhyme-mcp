import sqlite3
from pathlib import Path

import pytest

from openrhyme_mcp.store import (
    COLUMNS,
    MAX_LIMIT,
    StoreError,
    open_readonly,
    query_events,
    schema_version,
)
from tests.conftest import BASE_TS


def test_columns_match_fixture_ddl(seeded_data_dir: Path) -> None:
    conn = open_readonly(seeded_data_dir / "events.sqlite")
    names = [row[1] for row in conn.execute("PRAGMA table_info(events)")]
    assert tuple(names) == COLUMNS


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


def test_query_filters_and_shapes_rows(seeded_data_dir: Path) -> None:
    conn = open_readonly(seeded_data_dir / "events.sqlite")
    rows = query_events(conn, since=BASE_TS + 200, until=BASE_TS + 230)
    assert [r["kind"] for r in rows] == [
        "element.focused",
        "element.value_changed",
        "element.focused",
        "element.value_changed",
    ]
    first = rows[0]
    assert first["bundle_id"] == "com.apple.TextEdit"
    assert first["extra"] == {"valueHash": "abc", "truncated": False, "length": 6}
    assert first["time"].startswith("2025-09-01T")
    assert "url" not in first  # NULL columns are omitted

    assert len(query_events(conn, since=0, kinds=["app.activated"])) == 3
    assert len(query_events(conn, since=0, app="com.apple.Safari")) == 12
    assert len(query_events(conn, since=0, limit=5)) == 5
    assert [r["id"] for r in query_events(conn, since=0, limit=3)] == [1, 2, 3]


def test_value_truncation(seeded_data_dir: Path) -> None:
    conn = open_readonly(seeded_data_dir / "events.sqlite")
    long_rows = [
        r
        for r in query_events(conn, since=0, max_value_chars=100)
        if r.get("value", "").startswith("xxxx")
    ]
    assert len(long_rows) == 1
    assert long_rows[0]["value"].endswith("…[truncated 4900 chars]")
    assert len(long_rows[0]["value"]) == 100 + len("…[truncated 4900 chars]")
    full = [
        r
        for r in query_events(conn, since=0, max_value_chars=0)
        if r.get("value", "").startswith("xxxx")
    ]
    assert len(full[0]["value"]) == 5000


def test_limit_is_capped(seeded_data_dir: Path) -> None:
    conn = open_readonly(seeded_data_dir / "events.sqlite")
    assert MAX_LIMIT == 2000
    assert len(query_events(conn, since=0, limit=10_000)) == 27
