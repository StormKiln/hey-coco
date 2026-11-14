"""SQLite storage with a tiny versioned migration runner.

Append new migrations to ``MIGRATIONS`` with the next version number; never edit or
reorder an applied one. Never name a column ``metadata``.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DB_FILENAME = "coco-hub.sqlite3"


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


MIGRATIONS: list[Migration] = [
    Migration(
        1,
        "create meta",
        """
        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """,
    ),
    Migration(
        2,
        "create timers",
        """
        CREATE TABLE timers (
            id TEXT PRIMARY KEY,
            name TEXT,
            duration_s INTEGER NOT NULL CHECK (duration_s > 0),
            created_at TEXT NOT NULL,
            ends_at TEXT NOT NULL,
            state TEXT NOT NULL
                CHECK (state IN ('running', 'ringing', 'dismissed', 'cancelled')),
            updated_at TEXT NOT NULL
        );
        CREATE INDEX timers_state_ends_at ON timers (state, ends_at);
        """,
    ),
    Migration(
        3,
        "create youtube quota and cache",
        """
        CREATE TABLE youtube_quota (
            day TEXT PRIMARY KEY,
            units INTEGER NOT NULL CHECK (units >= 0)
        );
        CREATE TABLE youtube_cache (
            query_key TEXT PRIMARY KEY,
            results TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        );
        """,
    ),
]

_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)
"""


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(_SCHEMA_MIGRATIONS)
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0])


def migrate(conn: sqlite3.Connection, migrations: Sequence[Migration] = MIGRATIONS) -> list[int]:
    """Apply pending migrations, each in its own transaction. Returns versions applied."""
    versions = [m.version for m in migrations]
    if versions != sorted(set(versions)) or any(v < 1 for v in versions):
        raise ValueError("migration versions must be positive and strictly increasing")

    applied: list[int] = []
    current = current_version(conn)
    for migration in migrations:
        if migration.version <= current:
            continue
        # executescript commits any open transaction first, then runs the script as-is;
        # wrapping it in BEGIN/COMMIT makes the schema change and its record atomic.
        try:
            conn.executescript(
                "BEGIN;\n"
                f"{migration.sql.rstrip().rstrip(';')};\n"
                "INSERT INTO schema_migrations (version, name) VALUES "
                f"({int(migration.version)}, '{migration.name.replace("'", "''")}');\n"
                "COMMIT;"
            )
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(migration.version)
    return applied


def open_db(data_dir: Path) -> sqlite3.Connection:
    """Open (creating if needed) the Hub database in ``data_dir`` and migrate it."""
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / DB_FILENAME, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn
