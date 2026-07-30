"""SQLite connection helpers: connection-per-unit-of-work, WAL, helpers.

The database lives in <project>/data/itr.db. Override the data folder with
the ITR_PORTAL_DATA_DIR environment variable (used by the test suite).

sqlite3 connections have thread affinity and Streamlit runs each rerun on a
worker thread, so connections are opened per unit of work and never cached
or shared. Transactions must never span user think-time.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    override = os.environ.get("ITR_PORTAL_DATA_DIR")
    path = Path(override) if override else PROJECT_ROOT / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "itr.db"


def now() -> str:
    """Local timestamp string used across the schema (ISO, seconds)."""
    return datetime.now().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    # isolation_level=None → autocommit; tx() issues explicit BEGIN IMMEDIATE.
    conn = sqlite3.connect(db_path(), timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def read() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Write unit of work: BEGIN IMMEDIATE … COMMIT/ROLLBACK, then close."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with read() as conn:
        return conn.execute(sql, params).fetchall()


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    with read() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql: str, params: tuple = ()) -> int:
    """Single-statement write in its own transaction; returns lastrowid."""
    with tx() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


def executemany(sql: str, seq_of_params) -> int:
    with tx() as conn:
        cur = conn.executemany(sql, seq_of_params)
        return cur.rowcount


def backup(dest: Path | None = None) -> Path:
    """WAL-safe online backup via the sqlite backup API."""
    if dest is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = data_dir() / "backups" / f"itr-{stamp}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with read() as src:
        with sqlite3.connect(dest) as dst:
            src.backup(dst)
    return dest
