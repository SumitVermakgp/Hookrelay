"""Signed HTTP delivery of events, with bounded retries and attempt history.

Safety properties:
- The destination is validated again at send time and the connection is pinned to
  an address that passed validation (Host header and TLS server name keep the
  original host).
- Redirects are never followed; a 3xx is a failed attempt.
- Proxy environment variables are ignored so traffic cannot bypass the checks.
- Only fixed error categories are stored. Secrets, URLs, payloads and response
  bodies are never logged or stored.
"""

import ipaddress
import logging
import sqlite3
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.core.retry import is_retryable, retry_delay_seconds
from app.core.security import SIGNATURE_HEADER, sign_payload
from app.core.timeutil import utc_iso
from app.core.url_policy import Resolver, UrlPolicyError, default_resolver, validate_destination_url
from app.db.database import connect

logger = logging.getLogger("hookrelay.delivery")

# httpx logs the full request URL (including any query-string token) at INFO, and
# httpcore logs connection hosts at DEBUG. Keep both quiet so URLs never reach logs.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

USER_AGENT = "HookRelay/0.1"
Clock = Callable[[], float]


def pinned_target(url: str, addresses: list[str]) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Return (url to connect to, extra headers, httpx extensions).

    The URL is always rebuilt from the parts that were validated, so the HTTP client
    never re-parses the original string (no parser differences, no fragment or
    userinfo tricks). Named hosts are replaced by the validated address; Host and
    the TLS server name keep the original name.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")  # validated URLs always convert
    except UnicodeError:
        pass
    try:
        ipaddress.ip_address(host)
        is_literal = True
    except ValueError:
        is_literal = False
    headers: dict[str, str] = {}
    extensions: dict[str, Any] = {}
    connect_host = host
    if addresses and not is_literal:
        connect_host = addresses[0]
        headers["Host"] = host + (f":{parts.port}" if parts.port else "")
        if parts.scheme == "https":
            extensions["sni_hostname"] = host
    netloc_host = f"[{connect_host}]" if ":" in connect_host else connect_host
    port = f":{parts.port}" if parts.port else ""
    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme}://{netloc_host}{port}{path}{query}", headers, extensions


def claim_attempt(
    db: sqlite3.Connection,
    delivery_id: str,
    max_attempts: int,
    *,
    manual: bool = False,
    clock: Clock = time.time,
) -> str | None:
    """Atomically start the next attempt, or return None if one must not start.

    A single statement checks all of: the delivery is pending (or failed, for a
    manual retry), fewer than ``max_attempts`` attempts exist, no attempt is still
    in flight, and (scheduled retries only) the retry time has arrived. It then
    inserts the next attempt number. SQLite serialises writers, so concurrent
    callers cannot both succeed and the maximum cannot be exceeded.
    """
    attempt_id = uuid.uuid4().hex
    now_iso = utc_iso(clock())
    statuses = "('pending', 'failed')" if manual else "('pending')"
    due = "" if manual else "AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?) "
    params: list[object] = [attempt_id, now_iso, delivery_id, max_attempts]
    if not manual:
        params.append(now_iso)
    with db:
        cur = db.execute(
            "INSERT INTO delivery_attempts (id, delivery_id, attempt_number, started_at) "
            "SELECT ?, d.id, "
            "(SELECT COUNT(*) FROM delivery_attempts WHERE delivery_id = d.id) + 1, ? "
            f"FROM deliveries d WHERE d.id = ? AND d.status IN {statuses} "
            "AND (SELECT COUNT(*) FROM delivery_attempts WHERE delivery_id = d.id) < ? "
            "AND NOT EXISTS (SELECT 1 FROM delivery_attempts a "
            "WHERE a.delivery_id = d.id AND a.finished_at IS NULL) "
            f"{due}",
            params,
        )
        claimed = cur.rowcount == 1
        if claimed and manual:
            # Re-open a terminally failed delivery inside the same transaction.
            db.execute(
                "UPDATE deliveries SET status = 'pending', next_attempt_at = NULL "
                "WHERE id = ? AND status = 'failed'",
                (delivery_id,),
            )
    return attempt_id if claimed else None


def _send(
    row: sqlite3.Row,
    settings: Settings,
    resolver: Resolver,
    transport: httpx.BaseTransport | None,
    clock: Clock,
) -> tuple[int | None, str | None]:
    try:
        addresses = validate_destination_url(row["url"], settings.test_allowed_hosts, resolver)
    except UrlPolicyError:
        return None, "destination_blocked"
    url, extra_headers, extensions = pinned_target(row["url"], addresses)
    body = str(row["payload"]).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "HookRelay-Event-Id": row["event_id"],
        "HookRelay-Event-Type": row["type"],
        "HookRelay-Delivery-Id": row["delivery_id"],
        "HookRelay-Attempt": str(row["attempt_number"]),
        SIGNATURE_HEADER: sign_payload(row["secret"], body, int(clock())),
        **extra_headers,
    }
    try:
        with (
            httpx.Client(
                transport=transport,
                timeout=settings.delivery_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client,
            client.stream(
                "POST", url, content=body, headers=headers, extensions=extensions
            ) as response,
        ):
            code = response.status_code  # body is intentionally never read
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.TransportError:
        return None, "connection_error"
    if 200 <= code < 300:
        return code, None
    if 300 <= code < 400:
        return code, "redirect_not_followed"
    return code, "http_error"


def execute_attempt(
    db: sqlite3.Connection,
    settings: Settings,
    delivery_id: str,
    attempt_id: str,
    *,
    resolver: Resolver,
    transport: httpx.BaseTransport | None,
    clock: Clock,
) -> bool:
    """Send a claimed attempt, record the outcome and decide the delivery's next state."""
    row = db.execute(
        "SELECT d.id AS delivery_id, d.event_id, e.url, e.secret, ev.type, ev.payload, "
        "a.attempt_number FROM delivery_attempts a "
        "JOIN deliveries d ON d.id = a.delivery_id "
        "JOIN endpoints e ON e.id = d.endpoint_id "
        "JOIN events ev ON ev.id = d.event_id WHERE a.id = ?",
        (attempt_id,),
    ).fetchone()
    code: int | None
    error: str | None
    try:
        code, error = _send(row, settings, resolver, transport, clock)
    except Exception as exc:  # noqa: BLE001 - recorded as a fixed category, type name only
        code, error = None, "internal_error"
        logger.error("unexpected delivery error type=%s", type(exc).__name__)
    ok = error is None
    attempt_number = int(row["attempt_number"])
    now = clock()
    next_at: str | None = None
    if ok:
        status = "succeeded"
    elif attempt_number < settings.max_attempts and is_retryable(code, error):
        status = "pending"
        delay = retry_delay_seconds(
            attempt_number, settings.retry_base_seconds, settings.retry_max_delay_seconds
        )
        next_at = utc_iso(now + delay)
    else:
        status = "failed"
    with db:
        db.execute(
            "UPDATE delivery_attempts SET status_code = ?, error = ?, finished_at = ? WHERE id = ?",
            (code, error, utc_iso(now), attempt_id),
        )
        db.execute(
            "UPDATE deliveries SET status = ?, next_attempt_at = ? WHERE id = ?",
            (status, next_at, delivery_id),
        )
    logger.info(
        "delivery=%s attempt=%s outcome=%s status_code=%s delivery_status=%s",
        delivery_id,
        attempt_number,
        "succeeded" if ok else (error or "failed"),
        code,
        status,
    )
    return ok


def attempt_delivery(
    db: sqlite3.Connection,
    settings: Settings,
    delivery_id: str,
    *,
    manual: bool = False,
    resolver: Resolver = default_resolver,
    transport: httpx.BaseTransport | None = None,
    clock: Clock = time.time,
) -> bool | None:
    """Claim and make one attempt. True/False for success/failure, None if not claimed."""
    attempt_id = claim_attempt(db, delivery_id, settings.max_attempts, manual=manual, clock=clock)
    if attempt_id is None:
        return None
    return execute_attempt(
        db,
        settings,
        delivery_id,
        attempt_id,
        resolver=resolver,
        transport=transport,
        clock=clock,
    )


def deliver_event(
    settings: Settings,
    event_id: str,
    *,
    resolver: Resolver = default_resolver,
    transport: httpx.BaseTransport | None = None,
    clock: Clock = time.time,
) -> None:
    """Make the first attempt for every pending delivery of an event."""
    db = connect(settings.db_path)
    try:
        ids = [
            r["id"]
            for r in db.execute(
                "SELECT id FROM deliveries WHERE event_id = ? AND status = 'pending' "
                "ORDER BY created_at, id",
                (event_id,),
            )
        ]
        for delivery_id in ids:
            attempt_delivery(
                db, settings, delivery_id, resolver=resolver, transport=transport, clock=clock
            )
    finally:
        db.close()


def run_due_retries(
    settings: Settings,
    *,
    resolver: Resolver = default_resolver,
    transport: httpx.BaseTransport | None = None,
    clock: Clock = time.time,
    limit: int = 100,
) -> int:
    """Attempt pending deliveries whose retry time has arrived. Returns attempts made.

    Scheduled retries are always swept. Deliveries that were never attempted are
    swept only when auto_deliver is on (this also recovers lost background tasks).
    """
    if settings.auto_deliver:
        due = "(d.next_attempt_at IS NULL OR d.next_attempt_at <= ?)"
    else:
        due = "(d.next_attempt_at IS NOT NULL AND d.next_attempt_at <= ?)"
    db = connect(settings.db_path)
    try:
        ids = [
            r["id"]
            for r in db.execute(
                f"SELECT d.id FROM deliveries d WHERE d.status = 'pending' AND {due} "
                "ORDER BY COALESCE(d.next_attempt_at, d.created_at), d.id LIMIT ?",
                (utc_iso(clock()), limit),
            )
        ]
        made = 0
        for delivery_id in ids:
            result = attempt_delivery(
                db, settings, delivery_id, resolver=resolver, transport=transport, clock=clock
            )
            if result is not None:
                made += 1
        return made
    finally:
        db.close()
