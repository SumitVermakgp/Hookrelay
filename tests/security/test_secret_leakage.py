"""Signing secrets must leave the system only once: in the response that creates them."""

import logging
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.core.config import Settings
from tests.conftest import make_client
from tools.local_receiver import ReceiverServer


@pytest.fixture
def receivers() -> Iterator[list[ReceiverServer]]:
    servers = [ReceiverServer(slow_seconds=1.0).start(), ReceiverServer().start()]
    yield servers
    for s in servers:
        s.stop()


def test_secret_appears_only_in_its_creation_response(
    tmp_path: Path, receivers: list[ReceiverServer], caplog: pytest.LogCaptureFixture
) -> None:
    main, closed = receivers
    closed_url = closed.url("/ok")
    closed.stop()  # nothing listens on this port any more: connection errors

    settings = Settings(
        db_path=str(tmp_path / "leak.db"),
        test_allowed_hosts=frozenset({"127.0.0.1"}),
        delivery_timeout_seconds=0.4,
        max_attempts=2,
        retry_base_seconds=0.0,
        retry_interval_seconds=0,
        auto_deliver=True,
    )
    other_responses: list[str] = []

    def seen(r: Response) -> Response:
        other_responses.append(r.text + repr(sorted(r.headers.items())))
        return r

    secrets: list[str] = []
    with caplog.at_level(logging.DEBUG):
        for c in make_client(settings):
            assert isinstance(c, TestClient)
            for path in ("/ok", "/fail", "/slow", "/redirect", "/status/404"):
                created = c.post("/endpoints", json={"url": main.url(path)})
                assert created.status_code == 201
                secrets.append(created.json()["secret"])
            secrets.append(c.post("/endpoints", json={"url": closed_url}).json()["secret"])
            main.secret = secrets[0]

            event = seen(
                c.post(
                    "/events",
                    json={"type": "t", "payload": {"k": "v"}},
                    headers={"Idempotency-Key": "leak-1"},
                )
            ).json()
            seen(
                c.post(
                    "/events",
                    json={"type": "t", "payload": {"k": "v"}},
                    headers={"Idempotency-Key": "leak-1"},
                )
            )
            seen(
                c.post(
                    "/events",
                    json={"type": "other", "payload": {}},
                    headers={"Idempotency-Key": "leak-1"},
                )
            )
            seen(c.post("/events", json={"type": "bad type", "payload": {}}))
            seen(c.post("/events", content=b"x" * 100_000))
            seen(c.post("/endpoints", json={"url": "https://127.0.0.1/"}))
            seen(c.post("/endpoints", json={"url": "https://a.example.com", "secret": "x"}))
            seen(c.get(f"/events/{event['id']}"))
            seen(c.get("/events/missing"))
            seen(c.get("/endpoints"))
            seen(c.get("/deliveries"))
            seen(c.get("/health"))
            seen(c.get("/openapi.json"))
            for d in event["deliveries"]:
                seen(c.get(f"/deliveries/{d['id']}"))
                seen(c.post(f"/deliveries/{d['id']}/retry"))
                seen(c.post(f"/deliveries/{d['id']}/retry"))
                seen(c.post(f"/deliveries/{d['id']}/retry"))
            seen(c.post("/deliveries/missing/retry"))

            conn = sqlite3.connect(settings.db_path)
            stored = repr(conn.execute("SELECT * FROM delivery_attempts").fetchall())
            stored += repr(conn.execute("SELECT * FROM deliveries").fetchall())
            stored += repr(conn.execute("SELECT * FROM events").fetchall())
            conn.close()

    assert len(set(secrets)) == 6
    assert "hookrelay.delivery" in caplog.text  # the flow really produced our log lines
    on_the_wire = repr([(r.headers, r.body) for r in main.received])
    for secret in secrets:
        assert not any(secret in t for t in other_responses), "secret in an API response"
        assert secret not in caplog.text, "secret in logs"
        assert secret not in stored, "secret in attempt/delivery/event rows"
        assert secret not in on_the_wire, "secret sent to a receiver"
    assert time.time() > 0


def test_secret_not_in_validation_errors_for_other_fields(client: TestClient) -> None:
    created = client.post("/endpoints", json={"url": "https://hooks.example.com/"}).json()
    r = client.post("/endpoints", json={"url": 123, "description": "d"})
    assert r.status_code == 422
    assert created["secret"] not in r.text


def test_client_supplied_secret_is_rejected_not_stored(
    client: TestClient, settings: Settings
) -> None:
    r = client.post(
        "/endpoints", json={"url": "https://hooks.example.com/", "secret": "whsec_mine"}
    )
    assert r.status_code == 422
    conn = sqlite3.connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) FROM endpoints").fetchone()[0] == 0
    conn.close()
