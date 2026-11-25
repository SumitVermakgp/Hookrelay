import sqlite3
import uuid
from datetime import UTC, datetime

from app.core.security import generate_secret


class EndpointExists(Exception):
    """The url is already registered."""


class EndpointLimitReached(Exception):
    """The table already holds the configured maximum number of endpoints."""


def create_endpoint(
    db: sqlite3.Connection, url: str, description: str | None, max_endpoints: int
) -> sqlite3.Row:
    """Register one destination url and return the stored row, secret included.

    Duplicates are detected by the UNIQUE constraint on endpoints.url rather than a
    read-then-write check, so two concurrent requests cannot both register the same
    url. The request that loses the race rolls back and stores nothing.

    The count that enforces max_endpoints is taken inside the same transaction, after
    the insert, so concurrent requests cannot both read a count below the limit and
    then write. Counting after the insert includes the new row, hence the strict
    comparison. Inserting first also means a repeat registration is reported as a
    duplicate rather than as a full table.
    """
    endpoint_id = uuid.uuid4().hex
    try:
        with db:
            db.execute(
                "INSERT INTO endpoints (id, url, secret, description, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (endpoint_id, url, generate_secret(), description, datetime.now(UTC).isoformat()),
            )
            if db.execute("SELECT COUNT(*) FROM endpoints").fetchone()[0] > max_endpoints:
                raise EndpointLimitReached
    except sqlite3.IntegrityError as exc:
        if "endpoints.url" not in str(exc):
            raise
        raise EndpointExists from None
    row: sqlite3.Row = db.execute("SELECT * FROM endpoints WHERE id = ?", (endpoint_id,)).fetchone()
    return row


def list_endpoints(db: sqlite3.Connection, limit: int, offset: int) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT * FROM endpoints ORDER BY created_at, id LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
