import sqlite3
import threading
import uuid
from pathlib import Path

import pytest

from app.db.database import connect, init_db
from app.services.events import IdempotencyConflict, create_event


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "svc.db")
    init_db(path)
    return path


def _add_endpoints(path: str, n: int) -> None:
    conn = connect(path)
    for i in range(n):
        conn.execute(
            "INSERT INTO endpoints (id, url, secret, description, created_at) "
            "VALUES (?, ?, 's', NULL, ?)",
            (uuid.uuid4().hex, f"https://x{i}.example.com", f"2026-01-01T00:00:0{i}"),
        )
    conn.commit()
    conn.close()


def _count(path: str, table: str) -> int:
    conn = connect(path)
    n: int = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return n


def test_concurrent_same_key_creates_exactly_one_event(db_path: str) -> None:
    _add_endpoints(db_path, 3)
    workers = 16
    barrier = threading.Barrier(workers)
    results: list[tuple[str, bool]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def work() -> None:
        conn = connect(db_path)
        try:
            barrier.wait()
            event, created = create_event(conn, "order.paid", '{"a":1}', "key-1")
            with lock:
                results.append((event["id"], created))
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=work) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(results) == workers
    assert sum(1 for _, created in results if created) == 1
    assert len({event_id for event_id, _ in results}) == 1
    assert _count(db_path, "events") == 1
    assert _count(db_path, "deliveries") == 3  # not multiplied by concurrent callers


def test_concurrent_same_key_different_body_one_wins_rest_conflict(db_path: str) -> None:
    workers = 8
    barrier = threading.Barrier(workers)
    outcomes: list[str] = []
    lock = threading.Lock()

    def work(i: int) -> None:
        conn = connect(db_path)
        try:
            barrier.wait()
            try:
                create_event(conn, "t", f'{{"n":{i}}}', "shared")
                outcome = "created"
            except IdempotencyConflict:
                outcome = "conflict"
            with lock:
                outcomes.append(outcome)
        finally:
            conn.close()

    threads = [threading.Thread(target=work, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("created") == 1
    assert outcomes.count("conflict") == workers - 1
    assert _count(db_path, "events") == 1


def test_no_key_allows_duplicates(db_path: str) -> None:
    conn = connect(db_path)
    a, created_a = create_event(conn, "t", "{}", None)
    b, created_b = create_event(conn, "t", "{}", None)
    assert created_a and created_b
    assert a["id"] != b["id"]


def test_failed_insert_rolls_back_event_and_deliveries(db_path: str) -> None:
    _add_endpoints(db_path, 2)
    conn = connect(db_path)
    create_event(conn, "t", "{}", "k")
    with pytest.raises(IdempotencyConflict):
        create_event(conn, "t", '{"x":1}', "k")
    assert _count(db_path, "events") == 1
    assert _count(db_path, "deliveries") == 2


def test_unrelated_integrity_error_is_not_swallowed(db_path: str) -> None:
    conn = connect(db_path)
    conn.execute("DROP TABLE delivery_attempts")
    conn.execute("DROP TABLE deliveries")
    conn.execute(
        "CREATE TABLE deliveries (id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES "
        "events(id), endpoint_id TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO endpoints (id, url, secret, created_at) VALUES ('e', 'u', 's', 'n')")
    conn.execute("CREATE UNIQUE INDEX one_delivery ON deliveries(endpoint_id)")
    conn.commit()
    create_event(conn, "t", "{}", "k1")
    with pytest.raises(sqlite3.IntegrityError):
        create_event(conn, "t", "{}", "k2")
