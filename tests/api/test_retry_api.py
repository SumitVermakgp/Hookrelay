import sqlite3
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.conftest import make_client

URL = "https://hooks.example.com/secret-path?token=abc123"


class Script:
    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        code = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        return httpx.Response(code)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=str(tmp_path / "r.db"),
        max_attempts=3,
        auto_deliver=False,
        retry_interval_seconds=0,
        retry_base_seconds=3600,
    )


@pytest.fixture
def script() -> Script:
    return Script(200)


@pytest.fixture
def client(settings: Settings, script: Script) -> Iterator[TestClient]:
    yield from make_client(settings, transport=httpx.MockTransport(script))


@pytest.fixture
def delivery_id(client: TestClient) -> str:
    assert client.post("/endpoints", json={"url": URL}).status_code == 201
    event = client.post("/events", json={"type": "t", "payload": {"a": 1}}).json()
    return str(event["deliveries"][0]["id"])


def test_detail_before_any_attempt(client: TestClient, delivery_id: str) -> None:
    d = client.get(f"/deliveries/{delivery_id}").json()
    assert (d["status"], d["attempt_count"], d["attempts"]) == ("pending", 0, [])
    assert d["next_attempt_at"] is None


def test_retry_makes_a_distinct_recorded_attempt(
    client: TestClient, delivery_id: str, script: Script
) -> None:
    r = client.post(f"/deliveries/{delivery_id}/retry")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "succeeded"
    assert d["attempt_count"] == 1
    (a,) = d["attempts"]
    assert (a["attempt_number"], a["status_code"], a["error"]) == (1, 200, None)
    assert a["started_at"] and a["finished_at"]
    assert client.get(f"/deliveries/{delivery_id}").json() == d
    assert script.calls == 1


def test_history_lists_every_attempt_in_order(
    client: TestClient, delivery_id: str, script: Script
) -> None:
    script.statuses = [404, 500, 200]
    for _ in range(3):
        assert client.post(f"/deliveries/{delivery_id}/retry").status_code == 200
    d = client.get(f"/deliveries/{delivery_id}").json()
    assert [(a["attempt_number"], a["status_code"], a["error"]) for a in d["attempts"]] == [
        (1, 404, "http_error"),
        (2, 500, "http_error"),
        (3, 200, None),
    ]
    assert d["status"] == "succeeded"
    assert d["next_attempt_at"] is None


def test_retryable_failure_exposes_next_attempt_time(
    client: TestClient, delivery_id: str, script: Script
) -> None:
    script.statuses = [503]
    d = client.post(f"/deliveries/{delivery_id}/retry").json()
    assert d["status"] == "pending"
    assert d["next_attempt_at"] is not None
    listed = client.get("/deliveries").json()[0]
    assert listed["next_attempt_at"] == d["next_attempt_at"]


def test_retry_refused_after_the_maximum(
    client: TestClient, delivery_id: str, script: Script
) -> None:
    script.statuses = [404]
    for _ in range(3):
        assert client.post(f"/deliveries/{delivery_id}/retry").status_code == 200
    r = client.post(f"/deliveries/{delivery_id}/retry")
    assert r.status_code == 409
    assert r.json()["detail"] == "maximum attempts reached"
    assert script.calls == 3
    d = client.get(f"/deliveries/{delivery_id}").json()
    assert (d["status"], d["attempt_count"]) == ("failed", 3)


def test_retry_refused_after_success(client: TestClient, delivery_id: str, script: Script) -> None:
    assert client.post(f"/deliveries/{delivery_id}/retry").status_code == 200
    r = client.post(f"/deliveries/{delivery_id}/retry")
    assert r.status_code == 409
    assert r.json()["detail"] == "delivery already succeeded"
    assert script.calls == 1


def test_retry_refused_while_an_attempt_is_in_flight(
    client: TestClient, settings: Settings, delivery_id: str, script: Script
) -> None:
    conn = sqlite3.connect(settings.db_path)
    conn.execute(
        "INSERT INTO delivery_attempts (id, delivery_id, attempt_number, started_at) "
        "VALUES ('inflight', ?, 1, 'now')",
        (delivery_id,),
    )
    conn.commit()
    conn.close()
    r = client.post(f"/deliveries/{delivery_id}/retry")
    assert r.status_code == 409
    assert r.json()["detail"] == "an attempt is already in progress"
    assert script.calls == 0


def test_unknown_delivery_is_404(client: TestClient, script: Script) -> None:
    assert client.get("/deliveries/nope").status_code == 404
    assert client.post("/deliveries/nope/retry").status_code == 404
    assert script.calls == 0


def test_responses_expose_no_secret_url_or_token(client: TestClient, delivery_id: str) -> None:
    secret = sqlite3.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    stored = secret.execute("SELECT secret FROM endpoints").fetchone()[0]
    secret.close()
    texts = [
        client.post(f"/deliveries/{delivery_id}/retry").text,
        client.get(f"/deliveries/{delivery_id}").text,
        client.get("/deliveries").text,
    ]
    for t in texts:
        assert stored not in t
        assert "abc123" not in t
        assert "hooks.example.com" not in t


def test_openapi_documents_retry_route(client: TestClient) -> None:
    op = client.get("/openapi.json").json()["paths"]["/deliveries/{delivery_id}/retry"]["post"]
    assert {"200", "409"} <= set(op["responses"])
