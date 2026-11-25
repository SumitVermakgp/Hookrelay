import sqlite3

_SELECT = (
    "SELECT d.id, d.event_id, d.endpoint_id, d.status, d.created_at, d.next_attempt_at, "
    "(SELECT COUNT(*) FROM delivery_attempts a WHERE a.delivery_id = d.id) AS attempt_count "
    "FROM deliveries d"
)


def list_deliveries(
    db: sqlite3.Connection,
    event_id: str | None,
    endpoint_id: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[object] = []
    for column, value in (("event_id", event_id), ("endpoint_id", endpoint_id), ("status", status)):
        if value is not None:
            clauses.append(f"d.{column} = ?")  # column names are fixed literals above
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"{_SELECT}{where} ORDER BY d.created_at, d.id LIMIT ? OFFSET ?"
    return db.execute(sql, (*params, limit, offset)).fetchall()


def get_delivery(db: sqlite3.Connection, delivery_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = db.execute(f"{_SELECT} WHERE d.id = ?", (delivery_id,)).fetchone()
    return row


def attempts_for_delivery(db: sqlite3.Connection, delivery_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT attempt_number, status_code, error, started_at, finished_at "
        "FROM delivery_attempts WHERE delivery_id = ? ORDER BY attempt_number",
        (delivery_id,),
    ).fetchall()


def conflict_reason(db: sqlite3.Connection, delivery_id: str, max_attempts: int) -> str:
    """Explain why a manual retry was refused (informational, not used for the decision)."""
    row = get_delivery(db, delivery_id)
    if row is None:
        return "delivery not found"
    if row["status"] == "succeeded":
        return "delivery already succeeded"
    if row["attempt_count"] >= max_attempts:
        return "maximum attempts reached"
    return "an attempt is already in progress"
