import json
import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.conftest import SMALL_LIMIT

BODY = {"type": "order.paid", "payload": {"order_id": 42, "amount": 1999}}


def _endpoint(client: TestClient, n: int = 0) -> dict[str, str]:
    r = client.post("/endpoints", json={"url": f"https://hooks{n}.example.com/r"})
    assert r.status_code == 201
    body: dict[str, str] = r.json()
    return body


def _count(settings: Settings, table: str) -> int:
    conn = sqlite3.connect(settings.db_path)
    n: int = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return n


def test_create_event_without_endpoints(client: TestClient) -> None:
    r = client.post("/events", json=BODY)
    assert r.status_code == 201
    out = r.json()
    assert out["type"] == "order.paid"
    assert out["payload"] == BODY["payload"]
    assert out["idempotency_key"] is None
    assert out["deliveries"] == []
    assert client.get(f"/events/{out['id']}").json() == out


def test_creates_one_pending_delivery_per_endpoint(client: TestClient) -> None:
    eps = [_endpoint(client, i) for i in range(3)]
    out = client.post("/events", json=BODY).json()
    assert sorted(d["endpoint_id"] for d in out["deliveries"]) == sorted(e["id"] for e in eps)
    assert {d["status"] for d in out["deliveries"]} == {"pending"}


def test_endpoint_registered_after_event_gets_no_delivery(client: TestClient) -> None:
    out = client.post("/events", json=BODY).json()
    _endpoint(client)
    assert client.get(f"/events/{out['id']}").json()["deliveries"] == []


def test_responses_never_contain_endpoint_secrets(client: TestClient) -> None:
    ep = _endpoint(client)
    out = client.post("/events", json=BODY)
    got = client.get(f"/events/{out.json()['id']}")
    listing = client.get("/deliveries")
    for r in (out, got, listing):
        assert "secret" not in r.text
        assert "whsec_" not in r.text
    assert ep["id"] in listing.text


def test_unknown_event_is_404(client: TestClient) -> None:
    assert client.get("/events/does-not-exist").status_code == 404


def test_unicode_payload_round_trips(client: TestClient) -> None:
    payload = {"name": "Zoë ☃", "nested": {"list": [1, 2.5, None, True]}}
    out = client.post("/events", json={"type": "t", "payload": payload}).json()
    assert client.get(f"/events/{out['id']}").json()["payload"] == payload


# idempotency


def test_same_key_same_body_replays_original(client: TestClient, settings: Settings) -> None:
    _endpoint(client)
    h = {"Idempotency-Key": "abc-123"}
    first = client.post("/events", json=BODY, headers=h)
    second = client.post("/events", json=BODY, headers=h)
    assert first.status_code == 201
    assert "idempotent-replayed" not in first.headers
    assert second.status_code == 200
    assert second.headers["idempotent-replayed"] == "true"
    assert second.json() == first.json()
    assert _count(settings, "events") == 1
    assert _count(settings, "deliveries") == 1


def test_key_order_in_payload_does_not_cause_conflict(client: TestClient) -> None:
    h = {"Idempotency-Key": "k"}
    a = client.post("/events", content='{"type":"t","payload":{"a":1,"b":2}}', headers=h)
    b = client.post("/events", content='{"payload":{"b":2,"a":1},"type":"t"}', headers=h)
    assert a.status_code == 201
    assert b.status_code == 200
    assert a.json()["id"] == b.json()["id"]


@pytest.mark.parametrize(
    "other",
    [
        {"type": "order.paid", "payload": {"order_id": 43, "amount": 1999}},
        {"type": "order.refunded", "payload": {"order_id": 42, "amount": 1999}},
    ],
)
def test_same_key_different_body_is_409_and_creates_nothing(
    client: TestClient, settings: Settings, other: dict[str, object]
) -> None:
    _endpoint(client)
    h = {"Idempotency-Key": "k"}
    client.post("/events", json=BODY, headers=h)
    r = client.post("/events", json=other, headers=h)
    assert r.status_code == 409
    assert _count(settings, "events") == 1
    assert _count(settings, "deliveries") == 1


def test_different_keys_create_different_events(client: TestClient) -> None:
    a = client.post("/events", json=BODY, headers={"Idempotency-Key": "a"}).json()
    b = client.post("/events", json=BODY, headers={"Idempotency-Key": "b"}).json()
    assert a["id"] != b["id"]


def test_without_key_each_request_is_a_new_event(client: TestClient, settings: Settings) -> None:
    client.post("/events", json=BODY)
    client.post("/events", json=BODY)
    assert _count(settings, "events") == 2


@pytest.mark.parametrize("key", ["", "has space", "tab\there", "x" * 256, "caf\u00e9"])
def test_invalid_idempotency_keys_rejected(client: TestClient, key: str) -> None:
    value = key.encode("latin-1")  # raw bytes so non-ASCII values reach the server
    r = client.post("/events", json=BODY, headers={"Idempotency-Key": value})
    assert r.status_code == 422


def test_max_length_key_accepted(client: TestClient) -> None:
    r = client.post("/events", json=BODY, headers={"Idempotency-Key": "x" * 255})
    assert r.status_code == 201


# validation and size limits


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"type": "t"},
        {"payload": {}},
        {"type": "", "payload": {}},
        {"type": "bad type!", "payload": {}},
        {"type": "x" * 101, "payload": {}},
        {"type": "t", "payload": []},
        {"type": "t", "payload": "string"},
        {"type": "t", "payload": {}, "extra": 1},
    ],
)
def test_invalid_bodies_return_422(client: TestClient, settings: Settings, body: object) -> None:
    assert client.post("/events", json=body).status_code == 422
    assert _count(settings, "events") == 0


@pytest.mark.parametrize(
    "raw",
    ["not json", "", "[1,2]", '{"type":"t","payload":{"a":NaN}}', '{"type":"t","payload":{"a":'],
)
def test_malformed_json_returns_422(client: TestClient, raw: str) -> None:
    assert client.post("/events", content=raw).status_code == 422


def test_extremely_deep_nesting_is_rejected_not_500(client: TestClient) -> None:
    depth = 5000
    objects = '{"type":"t","payload":' + '{"a":' * depth + "1" + "}" * depth + "}"
    arrays = '{"type":"t","payload":{"a":' + "[" * depth + "]" * depth + "}}"
    assert client.post("/events", content=objects).status_code == 422
    assert client.post("/events", content=arrays).status_code == 422


def test_moderately_deep_nesting_round_trips(client: TestClient) -> None:
    depth = 100
    raw = '{"type":"t","payload":' + '{"a":' * depth + "1" + "}" * depth + "}"
    r = client.post("/events", content=raw)
    assert r.status_code == 201
    assert client.get(f"/events/{r.json()['id']}").status_code == 200


def _body_of_size(size: int) -> bytes:
    prefix = '{"type":"t","payload":{"d":"'
    suffix = '"}}'
    pad = size - len(prefix) - len(suffix)
    raw = (prefix + "x" * pad + suffix).encode()
    assert len(raw) == size
    return raw


def test_body_exactly_at_limit_is_accepted(small_limit_client: TestClient) -> None:
    r = small_limit_client.post("/events", content=_body_of_size(SMALL_LIMIT))
    assert r.status_code == 201


def test_body_one_byte_over_limit_is_413(small_limit_client: TestClient) -> None:
    r = small_limit_client.post("/events", content=_body_of_size(SMALL_LIMIT + 1))
    assert r.status_code == 413


def test_oversized_chunked_body_without_content_length_is_413(
    small_limit_client: TestClient,
) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"type":"t","payload":{"d":"'
        for _ in range(10):
            yield b"x" * 100
        yield b'"}}'

    r = small_limit_client.post("/events", content=chunks())
    assert "content-length" not in r.request.headers
    assert r.status_code == 413


def test_oversized_request_creates_no_rows(
    small_limit_client: TestClient, tmp_path: object
) -> None:
    small_limit_client.post("/events", content=_body_of_size(SMALL_LIMIT * 10))
    assert small_limit_client.get("/deliveries").json() == []


def test_invalid_content_length_is_400(client: TestClient) -> None:
    r = client.post("/events", content=b"{}", headers={"Content-Length": "abc"})
    assert r.status_code in (400, 422)


# deliveries listing


def test_deliveries_listing_filters_and_pagination(client: TestClient) -> None:
    eps = [_endpoint(client, i) for i in range(2)]
    e1 = client.post("/events", json=BODY).json()
    e2 = client.post("/events", json=BODY).json()
    allrows = client.get("/deliveries").json()
    assert len(allrows) == 4
    assert all(r["attempt_count"] == 0 and r["status"] == "pending" for r in allrows)
    assert len(client.get("/deliveries", params={"event_id": e1["id"]}).json()) == 2
    assert len(client.get("/deliveries", params={"endpoint_id": eps[0]["id"]}).json()) == 2
    one = client.get("/deliveries", params={"event_id": e2["id"], "endpoint_id": eps[1]["id"]})
    assert len(one.json()) == 1
    assert client.get("/deliveries", params={"status": "failed"}).json() == []
    pages = [client.get("/deliveries", params={"limit": 2, "offset": o}).json() for o in (0, 2, 4)]
    assert [r["id"] for p in pages for r in p] == [r["id"] for r in allrows]


def test_deliveries_filter_values_are_parameterised(client: TestClient) -> None:
    _endpoint(client)
    client.post("/events", json=BODY)
    r = client.get("/deliveries", params={"event_id": "x' OR '1'='1"})
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"status": "bogus"}]
)
def test_deliveries_bad_params_422(client: TestClient, params: dict[str, object]) -> None:
    assert client.get("/deliveries", params=params).status_code == 422


def test_openapi_documents_event_request_body(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/events"]["post"]
    schema = op["requestBody"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["type", "payload"]
    assert {"200", "201", "409", "413"} <= set(op["responses"])
    assert json.dumps(spec)
