"""Size limits, header injection and injection-style inputs."""

import sqlite3
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.conftest import SMALL_LIMIT, make_client


def _chunks(total: int) -> Iterator[bytes]:
    yield b'{"url":"https://hooks.example.com/","description":"'
    sent = 0
    while sent < total:
        yield b"x" * 50
        sent += 50
    yield b'"}'


# every route has a body limit, not only /events


def test_endpoints_route_rejects_oversized_body(small_limit_client: TestClient) -> None:
    body = b'{"url":"https://hooks.example.com/","description":"' + b"x" * 500 + b'"}'
    assert small_limit_client.post("/endpoints", content=body).status_code == 413
    assert small_limit_client.get("/endpoints").json() == []


def test_endpoints_route_rejects_oversized_chunked_body(small_limit_client: TestClient) -> None:
    r = small_limit_client.post("/endpoints", content=_chunks(2000))
    assert "content-length" not in r.request.headers
    assert r.status_code == 413
    assert small_limit_client.get("/endpoints").json() == []


def test_retry_route_rejects_oversized_body(small_limit_client: TestClient) -> None:
    r = small_limit_client.post("/deliveries/x/retry", content=b"x" * (SMALL_LIMIT + 1))
    assert r.status_code == 413


def test_default_limit_is_64_kib(client: TestClient) -> None:
    ok = b'{"type":"t","payload":{"d":"' + b"x" * 60_000 + b'"}}'
    big = b'{"type":"t","payload":{"d":"' + b"x" * 70_000 + b'"}}'
    assert client.post("/events", content=ok).status_code == 201
    assert client.post("/events", content=big).status_code == 413


def test_non_numeric_content_length_is_400(client: TestClient) -> None:
    r = client.post("/events", content=b"{}", headers={"Content-Length": "abc"})
    assert r.status_code == 400


# header injection through event type


@pytest.mark.parametrize(
    "event_type",
    [
        "a\r\nX-Evil: 1",
        "a\nb",
        "order.paid\n",
        "order.paid\r\n",
        "order.paid ",
        " order.paid",
        "a/b",
        "a:b",
        "ｏｒｄｅｒ",
        "",
    ],
)
def test_event_type_cannot_carry_control_characters(client: TestClient, event_type: str) -> None:
    r = client.post("/events", json={"type": event_type, "payload": {}})
    assert r.status_code == 422


def test_outgoing_headers_contain_no_line_breaks(tmp_path) -> None:  # type: ignore[no-untyped-def]
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200)

    s = Settings(db_path=str(tmp_path / "h.db"), retry_interval_seconds=0)
    for c in make_client(s, transport=httpx.MockTransport(handler)):
        c.post("/endpoints", json={"url": "https://hooks.example.com/r"})
        c.post(
            "/events", json={"type": "order.paid", "payload": {}}, headers={"Idempotency-Key": "k"}
        )
    assert len(captured) == 1
    for name, value in captured[0].headers.items():
        assert "\r" not in name + value and "\n" not in name + value


# injection-style inputs are stored and returned literally


SQLI = "x'); DROP TABLE endpoints;--"


def test_sql_text_in_fields_is_data(client: TestClient, settings: Settings) -> None:
    created = client.post(
        "/endpoints", json={"url": "https://hooks.example.com/", "description": SQLI}
    )
    assert created.status_code == 201
    assert client.get("/endpoints").json()[0]["description"] == SQLI
    event = client.post(
        "/events", json={"type": "t", "payload": {"q": SQLI}}, headers={"Idempotency-Key": "k-1"}
    ).json()
    assert client.get(f"/events/{event['id']}").json()["payload"] == {"q": SQLI}
    conn = sqlite3.connect(settings.db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"endpoints", "events", "deliveries", "delivery_attempts"} <= tables


@pytest.mark.parametrize("bad_id", [SQLI, "1 OR 1=1", "%", "' OR ''='", "../../etc/passwd"])
def test_sql_text_in_path_ids_is_a_plain_miss(client: TestClient, bad_id: str) -> None:
    assert client.get(f"/events/{bad_id}").status_code in (404, 405)
    assert client.get(f"/deliveries/{bad_id}").status_code in (404, 405)
    assert client.post(f"/deliveries/{bad_id}/retry").status_code in (404, 405)


@pytest.mark.parametrize("param", ["event_id", "endpoint_id"])
def test_sql_text_in_filters_matches_nothing(client: TestClient, param: str) -> None:
    client.post("/endpoints", json={"url": "https://hooks.example.com/"})
    client.post("/events", json={"type": "t", "payload": {}})
    r = client.get("/deliveries", params={param: "' OR '1'='1"})
    assert r.status_code == 200 and r.json() == []


def test_unknown_routes_and_methods(client: TestClient) -> None:
    assert client.get("/admin").status_code == 404
    assert client.delete("/endpoints").status_code == 405
    assert client.put("/events").status_code == 405
