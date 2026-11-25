import sqlite3
from pathlib import Path

import pytest

from app.db.database import DuplicateEndpointUrls, connect, init_db

PHASE4_DELIVERIES = """
CREATE TABLE endpoints (id TEXT PRIMARY KEY, url TEXT NOT NULL, secret TEXT NOT NULL,
    description TEXT, created_at TEXT NOT NULL);
CREATE TABLE events (id TEXT PRIMARY KEY, type TEXT NOT NULL, payload TEXT NOT NULL,
    idempotency_key TEXT UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE deliveries (id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
    endpoint_id TEXT NOT NULL REFERENCES endpoints(id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
    created_at TEXT NOT NULL, UNIQUE (event_id, endpoint_id));
CREATE TABLE delivery_attempts (id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL
    REFERENCES deliveries(id), attempt_number INTEGER NOT NULL, status_code INTEGER,
    error TEXT, started_at TEXT NOT NULL, finished_at TEXT,
    UNIQUE (delivery_id, attempt_number));
INSERT INTO endpoints VALUES ('ep', 'https://x.example.com', 's', NULL, 'now');
INSERT INTO events VALUES ('ev', 't', '{}', NULL, 'now');
INSERT INTO deliveries VALUES ('d1', 'ev', 'ep', 'pending', 'now');
"""


def test_phase4_database_is_upgraded_in_place(tmp_path: Path) -> None:
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.executescript(PHASE4_DELIVERIES)
    raw.commit()
    raw.close()

    init_db(path)
    init_db(path)  # idempotent

    conn = connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(deliveries)")}
    assert "next_attempt_at" in cols
    row = conn.execute("SELECT id, status, next_attempt_at FROM deliveries").fetchone()
    assert (row["id"], row["status"], row["next_attempt_at"]) == ("d1", "pending", None)
    indexes = {r["name"] for r in conn.execute("PRAGMA index_list(deliveries)")}
    assert "idx_deliveries_due" in indexes


def test_fresh_database_has_column_and_index(tmp_path: Path) -> None:
    path = str(tmp_path / "new.db")
    init_db(path)
    conn = connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(deliveries)")}
    assert "next_attempt_at" in cols


def _unique_url_indexes(conn: sqlite3.Connection) -> list[str]:
    names = []
    for index in conn.execute("PRAGMA index_list(endpoints)"):
        if not index["unique"]:
            continue
        columns = [r["name"] for r in conn.execute(f'PRAGMA index_info("{index["name"]}")')]
        if columns == ["url"]:
            names.append(index["name"])
    return names


def _old_db(tmp_path: Path, name: str, extra_sql: str = "") -> str:
    path = str(tmp_path / name)
    raw = sqlite3.connect(path)
    raw.executescript(PHASE4_DELIVERIES + extra_sql)
    raw.commit()
    raw.close()
    return path


def test_endpoint_url_uniqueness_is_added_to_an_old_database(tmp_path: Path) -> None:
    path = _old_db(tmp_path, "urls.db")
    init_db(path)
    init_db(path)  # idempotent

    conn = connect(path)
    assert _unique_url_indexes(conn) == ["idx_endpoints_url"]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO endpoints VALUES ('e2', 'https://x.example.com', 's', NULL, 'n')")


def test_fresh_database_is_not_given_a_redundant_url_index(tmp_path: Path) -> None:
    path = str(tmp_path / "fresh.db")
    init_db(path)
    assert len(_unique_url_indexes(connect(path))) == 1


def test_existing_duplicate_urls_stop_startup_with_a_clear_error(tmp_path: Path) -> None:
    path = _old_db(
        tmp_path,
        "dupes.db",
        "INSERT INTO endpoints VALUES ('ep2', 'https://x.example.com', 's2', NULL, 'now');",
    )
    with pytest.raises(DuplicateEndpointUrls, match="1 endpoint url"):
        init_db(path)
