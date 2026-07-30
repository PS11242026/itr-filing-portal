"""Numbered-SQL-file migrations with a schema_version table.

upgrade() is idempotent and runs on every app start and in test setup.
Before applying anything to an existing database it takes an online backup
(executescript autocommits, so a partially applied file cannot be rolled
back — the backup is the recovery path).
"""

import re
import sqlite3
from pathlib import Path

from itrcore.db import connection

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^(\d{4})_.+\.sql$")


def _migration_files() -> list[tuple[int, Path]]:
    out = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _NAME_RE.match(path.name)
        if m:
            out.append((int(m.group(1)), path))
    return out


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER PRIMARY KEY,"
        " filename TEXT NOT NULL,"
        " applied_at TEXT NOT NULL) STRICT")
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    return {r["version"] for r in rows}


def pending() -> list[Path]:
    with connection.read() as conn:
        applied = _applied_versions(conn)
    return [p for v, p in _migration_files() if v not in applied]


def upgrade() -> list[str]:
    """Apply pending migrations in order; returns applied filenames."""
    files = _migration_files()
    applied_names: list[str] = []
    with connection.read() as conn:
        applied = _applied_versions(conn)
        has_data = bool(conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT IN ('schema_version') LIMIT 1").fetchone())
    todo = [(v, p) for v, p in files if v not in applied]
    if not todo:
        return applied_names
    if has_data:
        connection.backup(connection.data_dir() / "backups" /
                          f"pre-migrate-{todo[0][0]:04d}.db")
    for version, path in todo:
        sql = path.read_text(encoding="utf-8")
        with connection.read() as conn:  # executescript manages its own txns
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_version (version, filename, applied_at) "
                "VALUES (?,?,?)", (version, path.name, connection.now()))
        applied_names.append(path.name)
    with connection.read() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    return applied_names
