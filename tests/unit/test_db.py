import sqlite3
from pathlib import Path

import pytest

from app.db.database import connect, init_db

TABLES = {"endpoints", "events", "deliveries", "delivery_attempts"}


def test_init_creates_tables(tmp_path: Path) -> None:
    path = str(tmp_path / "t.db")
    init_db(path)
    conn = connect(path)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names


def test_init_is_idempotent(tmp_path: Path) -> None:
    path = str(tmp_path / "t.db")
    init_db(path)
    init_db(path)


def test_foreign_keys_enforced(tmp_path: Path) -> None:
    path = str(tmp_path / "t.db")
    init_db(path)
    conn = connect(path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO deliveries (id, event_id, endpoint_id, status, created_at) "
            "VALUES ('d', 'missing', 'missing', 'pending', 'now')"
        )


def test_idempotency_key_is_unique(tmp_path: Path) -> None:
    path = str(tmp_path / "t.db")
    init_db(path)
    conn = connect(path)
    sql = "INSERT INTO events (id, type, payload, idempotency_key, created_at) VALUES (?, 't', '{}', 'k', 'now')"
    conn.execute(sql, ("e1",))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, ("e2",))


def _mode(path: Path) -> int:
    import stat

    return stat.S_IMODE(path.stat().st_mode)


def test_database_file_is_owner_only_even_with_a_permissive_umask(tmp_path: Path) -> None:
    import os

    old = os.umask(0o022)
    try:
        path = tmp_path / "perm.db"
        init_db(str(path))
        assert _mode(path) == 0o600
        conn = connect(str(path))
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO endpoints (id, url, secret, created_at) VALUES ('a', 'u', 's', 'n')"
        )
        for sibling in tmp_path.glob("perm.db-*"):  # journal files, if present
            assert _mode(sibling) == 0o600
        conn.commit()
        conn.close()
        assert _mode(path) == 0o600
    finally:
        os.umask(old)


def test_existing_world_readable_database_is_tightened(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    path.write_bytes(b"")
    path.chmod(0o644)
    init_db(str(path))
    assert _mode(path) == 0o600
