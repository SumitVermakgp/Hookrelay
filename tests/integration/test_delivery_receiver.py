"""End-to-end: real HTTP to a local loopback receiver, through the API."""

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import SIGNATURE_HEADER, verify_signature
from tests.conftest import make_client
from tools.local_receiver import ReceiverServer

BODY = {"type": "order.paid", "payload": {"order_id": 42, "name": "Zoë"}}


@pytest.fixture
def receiver() -> Iterator[ReceiverServer]:
    server = ReceiverServer(slow_seconds=1.5).start()
    yield server
    server.stop()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=str(tmp_path / "int.db"),
        test_allowed_hosts=frozenset({"127.0.0.1"}),
        delivery_timeout_seconds=0.4,
        auto_deliver=True,
        retry_base_seconds=3600,  # retries are scheduled far ahead, never due in a test
        retry_interval_seconds=0,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    yield from make_client(settings)


def _register(client: TestClient, url: str) -> dict[str, str]:
    r = client.post("/endpoints", json={"url": url})
    assert r.status_code == 201, r.text
    out: dict[str, str] = r.json()
    return out


def _rows(settings: Settings, sql: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _deliveries(client: TestClient) -> list[dict[str, object]]:
    out: list[dict[str, object]] = client.get("/deliveries").json()
    return out


def test_successful_delivery_is_signed_and_recorded(
    client: TestClient, receiver: ReceiverServer, settings: Settings
) -> None:
    ep = _register(client, receiver.url("/ok"))
    receiver.secret = ep["secret"]
    event = client.post("/events", json=BODY).json()

    assert receiver.paths() == ["/ok"]
    got = receiver.received[0]
    assert got.signature_valid is True
    assert got.headers["hookrelay-event-id"] == event["id"]
    assert got.headers["hookrelay-attempt"] == "1"
    assert got.body == '{"name":"Zoë","order_id":42}'.encode()
    assert verify_signature(
        ep["secret"], got.body, got.headers[SIGNATURE_HEADER.lower()], now=time.time()
    )

    (d,) = _deliveries(client)
    assert d["status"] == "succeeded"
    assert d["attempt_count"] == 1
    (a,) = _rows(settings, "SELECT * FROM delivery_attempts")
    assert (a["attempt_number"], a["status_code"], a["error"]) == (1, 200, None)


def test_retryable_failure_is_recorded_and_scheduled(
    client: TestClient, receiver: ReceiverServer, settings: Settings
) -> None:
    _register(client, receiver.url("/fail"))
    client.post("/events", json=BODY)
    assert receiver.paths() == ["/fail"]
    (d,) = _deliveries(client)
    assert (d["status"], d["attempt_count"]) == ("pending", 1)
    assert d["next_attempt_at"] is not None
    (a,) = _rows(settings, "SELECT * FROM delivery_attempts")
    assert (a["status_code"], a["error"]) == (500, "http_error")


def test_success_and_failure_for_different_endpoints(
    client: TestClient, receiver: ReceiverServer
) -> None:
    ok = _register(client, receiver.url("/ok"))
    bad = _register(client, receiver.url("/status/503"))
    client.post("/events", json=BODY)
    by_endpoint = {d["endpoint_id"]: d["status"] for d in _deliveries(client)}
    assert by_endpoint == {ok["id"]: "succeeded", bad["id"]: "pending"}


def test_redirect_is_not_followed(client: TestClient, receiver: ReceiverServer) -> None:
    _register(client, receiver.url("/redirect"))
    client.post("/events", json=BODY)
    assert receiver.paths() == ["/redirect"]  # /ok was never requested
    assert _deliveries(client)[0]["status"] == "failed"


def test_slow_receiver_times_out(
    client: TestClient, receiver: ReceiverServer, settings: Settings
) -> None:
    _register(client, receiver.url("/slow"))
    client.post("/events", json=BODY)
    (a,) = _rows(settings, "SELECT * FROM delivery_attempts")
    assert (a["status_code"], a["error"]) == (None, "timeout")
    assert _deliveries(client)[0]["status"] == "pending"  # timeouts are retried


def test_connection_refused_is_recorded(
    client: TestClient, receiver: ReceiverServer, settings: Settings
) -> None:
    url = receiver.url("/ok")
    receiver.stop()
    _register(client, url)
    client.post("/events", json=BODY)
    (a,) = _rows(settings, "SELECT * FROM delivery_attempts")
    assert a["error"] == "connection_error"


def test_idempotent_replay_does_not_send_again(
    client: TestClient, receiver: ReceiverServer
) -> None:
    _register(client, receiver.url("/ok"))
    h = {"Idempotency-Key": "once"}
    assert client.post("/events", json=BODY, headers=h).status_code == 201
    assert client.post("/events", json=BODY, headers=h).status_code == 200
    assert client.post("/events", json=BODY, headers=h).status_code == 200
    assert receiver.paths() == ["/ok"]


def test_conflicting_idempotency_key_does_not_send(
    client: TestClient, receiver: ReceiverServer
) -> None:
    _register(client, receiver.url("/ok"))
    h = {"Idempotency-Key": "k"}
    client.post("/events", json=BODY, headers=h)
    other = {"type": "other", "payload": {}}
    assert client.post("/events", json=other, headers=h).status_code == 409
    assert receiver.paths() == ["/ok"]


def test_rejected_requests_send_nothing(client: TestClient, receiver: ReceiverServer) -> None:
    _register(client, receiver.url("/ok"))
    assert client.post("/events", json={"type": "bad type", "payload": {}}).status_code == 422
    assert receiver.paths() == []


def test_proxy_environment_variables_are_ignored(
    client: TestClient, receiver: ReceiverServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(var, "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    _register(client, receiver.url("/ok"))
    client.post("/events", json=BODY)
    assert receiver.paths() == ["/ok"]


def test_endpoint_secret_never_appears_in_any_api_response(
    client: TestClient, receiver: ReceiverServer
) -> None:
    ep = _register(client, receiver.url("/ok"))
    ev = client.post("/events", json=BODY)
    texts = [ev.text, client.get("/deliveries").text, client.get("/endpoints").text]
    assert all(ep["secret"] not in t for t in texts)


def test_receiver_refuses_non_loopback_bind() -> None:
    with pytest.raises(ValueError):
        ReceiverServer(host="0.0.0.0")


def test_destination_not_allowlisted_is_blocked_at_send_time(
    tmp_path: Path, receiver: ReceiverServer
) -> None:
    # Registered while allowlisted, then the allowlist is removed: send-time policy blocks it.
    path = str(tmp_path / "x.db")
    with_allow = Settings(
        db_path=path, test_allowed_hosts=frozenset({"127.0.0.1"}), auto_deliver=False
    )
    for c in make_client(with_allow):
        _register(c, receiver.url("/ok"))
    without = Settings(db_path=path, auto_deliver=True)
    for c in make_client(without):
        c.post("/events", json=BODY)
        (a,) = _rows(without, "SELECT * FROM delivery_attempts")
        assert a["error"] == "destination_blocked"
    assert receiver.paths() == []
