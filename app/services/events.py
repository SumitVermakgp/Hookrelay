import sqlite3
import uuid
from datetime import UTC, datetime


class IdempotencyConflict(Exception):
    """The idempotency key was already used with a different request."""


def create_event(
    db: sqlite3.Connection,
    event_type: str,
    payload_json: str,
    idempotency_key: str | None,
) -> tuple[sqlite3.Row, bool]:
    """Insert an event and one pending delivery per registered endpoint.

    Returns (event, created). Duplicate detection relies on the UNIQUE constraint on
    events.idempotency_key, not on a read-then-write check, so concurrent requests
    with the same key cannot both insert. The event and its deliveries are written in
    one transaction: a request that loses the race rolls back and creates nothing.
    """
    event_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    try:
        with db:
            db.execute(
                "INSERT INTO events (id, type, payload, idempotency_key, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_id, event_type, payload_json, idempotency_key, now),
            )
            endpoint_ids = [
                r["id"] for r in db.execute("SELECT id FROM endpoints ORDER BY created_at, id")
            ]
            db.executemany(
                "INSERT INTO deliveries (id, event_id, endpoint_id, status, created_at) "
                "VALUES (?, ?, ?, 'pending', ?)",
                [(uuid.uuid4().hex, event_id, ep, now) for ep in endpoint_ids],
            )
    except sqlite3.IntegrityError as exc:
        if idempotency_key is None or "events.idempotency_key" not in str(exc):
            raise
        existing = _by_key(db, idempotency_key)
        if existing is None:
            raise
        if existing["type"] != event_type or existing["payload"] != payload_json:
            raise IdempotencyConflict from None
        return existing, False
    event = get_event(db, event_id)
    assert event is not None
    return event, True


def _by_key(db: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = db.execute(
        "SELECT * FROM events WHERE idempotency_key = ?", (key,)
    ).fetchone()
    return row


def get_event(db: sqlite3.Connection, event_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = db.execute(
        "SELECT * FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    return row


def deliveries_for_event(db: sqlite3.Connection, event_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT id, endpoint_id, status FROM deliveries WHERE event_id = ? ORDER BY created_at, id",
        (event_id,),
    ).fetchall()
