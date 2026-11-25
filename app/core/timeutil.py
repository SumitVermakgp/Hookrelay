from datetime import UTC, datetime


def utc_iso(ts: float) -> str:
    """Fixed-width UTC timestamp, so string comparison orders correctly."""
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="microseconds")
