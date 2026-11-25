"""One test per defect found while building the baseline. Each failed before its fix."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.conftest import make_client
from tools.local_receiver import ReceiverServer


@pytest.fixture
def receiver() -> Iterator[ReceiverServer]:
    server = ReceiverServer().start()
    yield server
    server.stop()


def test_request_urls_with_query_tokens_never_reach_logs(
    tmp_path: Path, receiver: ReceiverServer, caplog: pytest.LogCaptureFixture
) -> None:
    """httpx logs the full URL at INFO. A token in the query string leaked into logs."""
    s = Settings(
        db_path=str(tmp_path / "r.db"),
        test_allowed_hosts=frozenset({"127.0.0.1"}),
        retry_interval_seconds=0,
    )
    with caplog.at_level(logging.DEBUG):
        for c in make_client(s):
            c.post("/endpoints", json={"url": receiver.url("/ok?token=SUPERSECRETTOKEN")})
            c.post("/events", json={"type": "t", "payload": {}})
    assert receiver.paths() == ["/ok"]
    assert "SUPERSECRETTOKEN" not in caplog.text


def test_endpoint_registration_enforces_the_body_limit(small_limit_client: TestClient) -> None:
    """Only /events had a size limit; /endpoints read and parsed bodies of any size."""
    body = b'{"url":"https://hooks.example.com/","description":"' + b"x" * 5000 + b'"}'
    assert small_limit_client.post("/endpoints", content=body).status_code == 413


def test_event_type_with_trailing_newline_is_rejected(client: TestClient) -> None:
    """Guards the regex end-anchor: a type ending in a newline would become a header value."""
    assert client.post("/events", json={"type": "order.paid\n", "payload": {}}).status_code == 422


def test_deliveries_are_listed_in_a_stable_order(client: TestClient) -> None:
    """Rows created in the same instant must not reorder between calls."""
    for i in range(4):
        client.post("/endpoints", json={"url": f"https://h{i}.example.com/"})
    client.post("/events", json={"type": "t", "payload": {}})
    first = [d["id"] for d in client.get("/deliveries").json()]
    for _ in range(5):
        assert [d["id"] for d in client.get("/deliveries").json()] == first


def test_internationalised_hostnames_can_be_delivered(tmp_path: Path) -> None:
    """Registration accepted them but delivery raised UnicodeEncodeError (internal_error)."""
    import httpx

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    s = Settings(db_path=str(tmp_path / "idn.db"), retry_interval_seconds=0)
    for c in make_client(s, transport=httpx.MockTransport(handler)):
        assert (
            c.post("/endpoints", json={"url": "https://b\u00fccher.example/hook"}).status_code
            == 201
        )
        event = c.post("/events", json={"type": "t", "payload": {}}).json()
        d = c.get(f"/deliveries/{event['deliveries'][0]['id']}").json()
        assert (d["status"], d["attempts"][0]["error"]) == ("succeeded", None)
    assert len(seen) == 1


def test_resolver_order_is_not_string_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Addresses were sorted as strings, which always put IPv6 before IPv4."""
    import socket

    from app.core.url_policy import default_resolver

    rows = [
        (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
        (socket.AF_INET6, 0, 0, "", ("2606:2800:220:1::1", 0, 0, 0)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: rows)
    assert default_resolver("h.example.com")[0] == "93.184.216.34"


def test_database_file_holding_secrets_is_not_world_readable(tmp_path: Path) -> None:
    """The SQLite file (plaintext signing secrets) was created 0644 under the usual umask."""
    import os
    import stat

    old = os.umask(0o022)
    try:
        s = Settings(
            db_path=str(tmp_path / "mode.db"), auto_deliver=False, retry_interval_seconds=0
        )
        for c in make_client(s):
            c.post("/endpoints", json={"url": "https://hooks.example.com/"})
        assert stat.S_IMODE(os.stat(s.db_path).st_mode) == 0o600
    finally:
        os.umask(old)
