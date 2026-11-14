import sqlite3

import pytest

from coco_hub import db


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_open_creates_file_in_data_dir(tmp_path):
    data_dir = tmp_path / "nested" / "data"
    conn = db.open_db(data_dir)
    try:
        assert (data_dir / db.DB_FILENAME).exists()
        assert db.current_version(conn) == db.MIGRATIONS[-1].version
    finally:
        conn.close()


def test_initial_migration_creates_meta_table(tmp_path):
    conn = db.open_db(tmp_path)
    try:
        conn.execute("INSERT INTO meta (key, value) VALUES ('k', 'v')")
        assert conn.execute("SELECT value FROM meta WHERE key = 'k'").fetchone()[0] == "v"
    finally:
        conn.close()


def test_migrate_is_idempotent(tmp_path):
    db.open_db(tmp_path).close()
    conn = db.open_db(tmp_path)
    try:
        rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        assert [r[0] for r in rows] == [m.version for m in db.MIGRATIONS]
        assert db.migrate(conn) == []
    finally:
        conn.close()


def test_only_pending_migrations_run(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    first = db.Migration(1, "one", "CREATE TABLE a (x INTEGER)")
    second = db.Migration(2, "two", "CREATE TABLE b (y INTEGER)")
    try:
        assert db.migrate(conn, [first]) == [1]
        assert db.current_version(conn) == 1
        assert db.migrate(conn, [first, second]) == [2]
        assert db.current_version(conn) == 2
        assert {"a", "b", "schema_migrations"} <= table_names(conn)
    finally:
        conn.close()


def test_failed_migration_rolls_back(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    good = db.Migration(1, "good", "CREATE TABLE a (x INTEGER)")
    bad = db.Migration(2, "bad", "CREATE TABLE b (y INTEGER); THIS IS NOT SQL")
    try:
        with pytest.raises(sqlite3.Error):
            db.migrate(conn, [good, bad])
        assert db.current_version(conn) == 1
        assert "b" not in table_names(conn)
    finally:
        conn.close()


def test_migrations_must_be_strictly_increasing(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        with pytest.raises(ValueError):
            db.migrate(conn, [db.Migration(2, "x", "SELECT 1"), db.Migration(1, "y", "SELECT 1")])
    finally:
        conn.close()


def test_no_column_named_metadata(tmp_path):
    conn = db.open_db(tmp_path)
    try:
        for table in table_names(conn):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            assert "metadata" not in cols
    finally:
        conn.close()
