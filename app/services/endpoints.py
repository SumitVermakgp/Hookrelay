import sqlite3
import uuid
from datetime import UTC, datetime

from app.core.security import generate_secret


def create_endpoint(db: sqlite3.Connection, url: str, description: str | None) -> sqlite3.Row:
    endpoint_id = uuid.uuid4().hex
    db.execute(
        "INSERT INTO endpoints (id, url, secret, description, created_at) VALUES (?, ?, ?, ?, ?)",
        (endpoint_id, url, generate_secret(), description, datetime.now(UTC).isoformat()),
    )
    db.commit()
    row: sqlite3.Row = db.execute("SELECT * FROM endpoints WHERE id = ?", (endpoint_id,)).fetchone()
    return row


def list_endpoints(db: sqlite3.Connection, limit: int, offset: int) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT * FROM endpoints ORDER BY created_at, id LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
