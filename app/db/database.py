"""SQLite connection and schema setup."""

import contextlib
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from fastapi import Request

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: str) -> sqlite3.Connection:
    # Each connection serves one request at a time; FastAPI may create and use it on
    # different worker threads, so the same-thread check is disabled.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases created by earlier phases up to the current schema."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(deliveries)")}
    if "next_attempt_at" not in columns:
        conn.execute("ALTER TABLE deliveries ADD COLUMN next_attempt_at TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_deliveries_due ON deliveries (status, next_attempt_at)"
    )


def _restrict_permissions(db_path: str) -> None:
    """The file holds plaintext signing secrets: make it owner read/write only.

    The file is created with mode 0600 (not created with the default umask and fixed
    afterwards), and a file left over from an earlier run is tightened.
    """
    if db_path == ":memory:" or db_path.startswith("file:"):
        return
    os.close(os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600))
    with contextlib.suppress(OSError):
        os.chmod(db_path, 0o600)


def init_db(db_path: str) -> None:
    _restrict_permissions(db_path)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()
