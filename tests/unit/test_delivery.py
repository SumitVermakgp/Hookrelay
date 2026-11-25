import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.core.security import SIGNATURE_HEADER, verify_signature
from app.db.database import connect, init_db
from app.services.delivery import (
    attempt_delivery,
    claim_attempt,
    deliver_event,
    pinned_target,
)
from app.services.endpoints import create_endpoint
from app.services.events import create_event

PUBLIC = "93.184.216.34"
NOW = 1_700_000_000.0
PAYLOAD = '{"a":1,"name":"Zoë"}'
MAX = 3


def public(_: str) -> list[str]:
    return [PUBLIC]


class Recorder:
    def __init__(self, status: int = 200, exc: Exception | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.exc = exc

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        return httpx.Response(self.status, headers={"Location": "/elsewhere"})


def transport_for(rec: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(rec)


class Env:
    def __init__(self, tmp_path: Path, urls: list[str], allowed: frozenset[str] = frozenset()):
        self.settings = Settings(db_path=str(tmp_path / "d.db"), test_allowed_hosts=allowed)
        init_db(self.settings.db_path)
        self.db = connect(self.settings.db_path)
        self.endpoints = [create_endpoint(self.db, u, None) for u in urls]
        self.event, _ = create_event(self.db, "order.paid", PAYLOAD, None)
        rows = self.db.execute("SELECT id, endpoint_id FROM deliveries ORDER BY created_at, id")
        self.delivery_ids = [r["id"] for r in rows]

    def attempts(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM delivery_attempts ORDER BY started_at, id").fetchall()

    def status(self, delivery_id: str) -> str:
        row = self.db.execute("SELECT status FROM deliveries WHERE id = ?", (delivery_id,))
        return str(row.fetchone()["status"])


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path, ["https://hooks.example.com/r?x=1&y=2"])


def run(env: Env, rec: Callable[[httpx.Request], httpx.Response], idx: int = 0) -> bool | None:
    return attempt_delivery(
        env.db,
        env.settings,
        env.delivery_ids[idx],
        resolver=public,
        transport=transport_for(rec),
        clock=lambda: NOW,
    )


# request construction and pinning


def test_request_is_pinned_to_validated_address_with_original_host(env: Env) -> None:
    rec = Recorder()
    assert run(env, rec) is True
    req = rec.requests[0]
    assert req.url.host == PUBLIC
    assert req.url.scheme == "https"
    assert req.url.path == "/r"
    assert req.url.query == b"x=1&y=2"
    assert req.headers["host"] == "hooks.example.com"
    assert req.extensions["sni_hostname"] == "hooks.example.com"


def test_pinned_target_keeps_port_and_formats_ipv6() -> None:
    url, headers, ext = pinned_target("https://h.example.com:8443/p", ["2606:2800:220:1::1"])
    assert url == "https://[2606:2800:220:1::1]:8443/p"
    assert headers == {"Host": "h.example.com:8443"}
    assert ext == {"sni_hostname": "h.example.com"}


def test_pinned_target_leaves_ip_literals_and_allowlisted_hosts_alone() -> None:
    assert pinned_target("https://93.184.216.34/x", ["93.184.216.34"]) == (
        "https://93.184.216.34/x",
        {},
        {},
    )
    assert pinned_target("http://localhost:9/x", []) == ("http://localhost:9/x", {}, {})


def test_http_destination_gets_no_sni_extension() -> None:
    _, _, ext = pinned_target("http://h.example.com/x", ["93.184.216.34"])
    assert ext == {}


def test_body_headers_and_signature(env: Env) -> None:
    rec = Recorder()
    run(env, rec)
    req = rec.requests[0]
    secret = env.endpoints[0]["secret"]
    assert req.content == PAYLOAD.encode("utf-8")  # exactly the stored bytes
    assert req.headers["content-type"] == "application/json"
    assert req.headers["hookrelay-event-id"] == env.event["id"]
    assert req.headers["hookrelay-event-type"] == "order.paid"
    assert req.headers["hookrelay-delivery-id"] == env.delivery_ids[0]
    assert req.headers["hookrelay-attempt"] == "1"
    assert verify_signature(secret, req.content, req.headers[SIGNATURE_HEADER], now=NOW)
    assert not verify_signature("whsec_other", req.content, req.headers[SIGNATURE_HEADER], now=NOW)
    assert secret not in json.dumps(dict(req.headers))


# outcomes


@pytest.mark.parametrize("status", [200, 201, 204, 299])
def test_2xx_is_success(env: Env, status: int) -> None:
    assert run(env, Recorder(status)) is True
    assert env.status(env.delivery_ids[0]) == "succeeded"
    a = env.attempts()[0]
    assert (a["attempt_number"], a["status_code"], a["error"]) == (1, status, None)
    assert a["finished_at"] is not None


@pytest.mark.parametrize("status", [400, 404, 410, 429, 500, 503])
def test_non_2xx_is_failure(env: Env, status: int) -> None:
    assert run(env, Recorder(status)) is False
    retryable = status in (429, 500, 503)
    assert env.status(env.delivery_ids[0]) == ("pending" if retryable else "failed")
    a = env.attempts()[0]
    assert (a["status_code"], a["error"]) == (status, "http_error")


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_are_not_followed(env: Env, status: int) -> None:
    rec = Recorder(status)
    assert run(env, rec) is False
    assert len(rec.requests) == 1
    a = env.attempts()[0]
    assert (a["status_code"], a["error"]) == (status, "redirect_not_followed")


@pytest.mark.parametrize(
    ("exc", "category", "delivery_status"),
    [
        (httpx.ReadTimeout("x"), "timeout", "pending"),
        (httpx.ConnectTimeout("x"), "timeout", "pending"),
        (httpx.ConnectError("x"), "connection_error", "pending"),
        (httpx.RemoteProtocolError("x"), "connection_error", "pending"),
        (RuntimeError("boom"), "internal_error", "failed"),
    ],
)
def test_transport_errors_map_to_fixed_categories(
    env: Env, exc: Exception, category: str, delivery_status: str
) -> None:
    assert run(env, Recorder(exc=exc)) is False
    a = env.attempts()[0]
    assert (a["status_code"], a["error"]) == (None, category)
    assert env.status(env.delivery_ids[0]) == delivery_status
    assert "boom" not in str(a["error"])


def test_destination_resolving_to_private_address_at_send_time_is_blocked(env: Env) -> None:
    # Registered while public, now resolves to an internal address (DNS rebinding).
    rec = Recorder()
    result = attempt_delivery(
        env.db,
        env.settings,
        env.delivery_ids[0],
        resolver=lambda _: ["169.254.169.254"],
        transport=transport_for(rec),
        clock=lambda: NOW,
    )
    assert result is False
    assert rec.requests == []  # nothing was sent
    assert env.attempts()[0]["error"] == "destination_blocked"


def test_one_failing_delivery_does_not_stop_the_others(tmp_path: Path) -> None:
    e = Env(tmp_path, ["https://a.example.com/", "https://b.example.com/"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "a.example.com":
            raise RuntimeError("boom")
        return httpx.Response(200)

    deliver_event(
        e.settings,
        e.event["id"],
        resolver=public,
        transport=transport_for(handler),
        clock=lambda: NOW,
    )
    rows = e.db.execute(
        "SELECT ep.url AS url, d.status AS status FROM deliveries d "
        "JOIN endpoints ep ON ep.id = d.endpoint_id"
    ).fetchall()
    assert {r["url"]: r["status"] for r in rows} == {
        "https://a.example.com/": "failed",
        "https://b.example.com/": "succeeded",
    }


# claiming and duplicate prevention


def test_delivery_is_not_attempted_twice(env: Env) -> None:
    rec = Recorder()
    assert run(env, rec) is True
    assert run(env, rec) is None
    assert len(rec.requests) == 1
    assert len(env.attempts()) == 1


def test_failed_delivery_is_not_reattempted_by_deliver_event(env: Env) -> None:
    rec = Recorder(500)
    deliver_event(
        env.settings,
        env.event["id"],
        resolver=public,
        transport=transport_for(rec),
        clock=lambda: NOW,
    )
    deliver_event(
        env.settings,
        env.event["id"],
        resolver=public,
        transport=transport_for(rec),
        clock=lambda: NOW,
    )
    assert len(rec.requests) == 1


def test_claim_refused_while_an_attempt_is_in_flight(env: Env) -> None:
    first = claim_attempt(env.db, env.delivery_ids[0], MAX)
    assert first is not None
    assert env.status(env.delivery_ids[0]) == "pending"  # still pending, but active
    assert claim_attempt(env.db, env.delivery_ids[0], MAX) is None
    assert len(env.attempts()) == 1


def test_claim_unknown_delivery_returns_none(env: Env) -> None:
    assert claim_attempt(env.db, "nope", MAX) is None


def test_concurrent_claims_produce_exactly_one_attempt(env: Env) -> None:
    workers = 16
    barrier = threading.Barrier(workers)
    results: list[str | None] = []
    lock = threading.Lock()

    def work() -> None:
        conn = connect(env.settings.db_path)
        try:
            barrier.wait()
            r = claim_attempt(conn, env.delivery_ids[0], MAX)
            with lock:
                results.append(r)
        finally:
            conn.close()

    threads = [threading.Thread(target=work) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == workers
    assert sum(r is not None for r in results) == 1
    assert len(env.attempts()) == 1


# logging


def test_logs_contain_no_secret_url_or_payload(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        run(env, Recorder(500))
        run(env, Recorder(exc=RuntimeError("hooks.example.com " + env.endpoints[0]["secret"])))
    text = caplog.text
    assert "hookrelay.delivery" in text  # our own log line is present and clean
    assert "93.184.216.34" not in text
    assert env.endpoints[0]["secret"] not in text
    assert "hooks.example.com" not in text
    assert "Zoë" not in text
    assert "x=1" not in text


def test_internationalised_endpoint_is_delivered_with_ascii_host_and_sni(tmp_path: Path) -> None:
    e = Env(tmp_path, ["https://b\u00fccher.example:8443/hook"])
    rec = Recorder()
    assert (
        attempt_delivery(
            e.db,
            e.settings,
            e.delivery_ids[0],
            resolver=public,
            transport=transport_for(rec),
            clock=lambda: NOW,
        )
        is True
    )
    req = rec.requests[0]
    assert req.url.host == PUBLIC
    assert req.headers["host"] == "xn--bcher-kva.example:8443"
    assert req.extensions["sni_hostname"] == "xn--bcher-kva.example"


def test_pinned_target_uses_ascii_host_for_allowlisted_names() -> None:
    url, headers, ext = pinned_target("http://b\u00fccher.example:9/x", [])
    assert url == "http://xn--bcher-kva.example:9/x"
    assert (headers, ext) == ({}, {})
