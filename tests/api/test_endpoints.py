import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_resolver
from app.core.config import Settings

URL = "https://hooks.example.com/receive"


def _url(n: int) -> str:
    """A distinct destination per call: endpoint urls must now be unique."""
    return f"https://hooks{n}.example.com/receive"


def _create(client: TestClient, url: str = URL, **extra: object) -> dict[str, object]:
    r = client.post("/endpoints", json={"url": url, **extra})
    assert r.status_code == 201, r.text
    body: dict[str, object] = r.json()
    return body


def test_create_returns_secret_once(client: TestClient) -> None:
    r = client.post("/endpoints", json={"url": URL, "description": "prod"})
    assert r.status_code == 201
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["url"] == URL
    assert body["description"] == "prod"
    assert body["id"]
    assert body["created_at"]
    assert str(body["secret"]).startswith("whsec_")
    assert len(str(body["secret"])) > 30


def test_secrets_are_unique(client: TestClient) -> None:
    assert _create(client, _url(1))["secret"] != _create(client, _url(2))["secret"]


def test_list_never_contains_secret(client: TestClient) -> None:
    created = [_create(client, _url(i)) for i in range(3)]
    r = client.get("/endpoints")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 3
    for item in items:
        assert "secret" not in item
    for c in created:
        assert str(c["secret"]) not in r.text
    assert "whsec_" not in r.text


def test_duplicate_url_is_rejected(client: TestClient) -> None:
    first = _create(client)
    r = client.post("/endpoints", json={"url": URL})
    assert r.status_code == 409
    assert r.json()["detail"] == "url is already registered"
    assert [e["id"] for e in client.get("/endpoints").json()] == [first["id"]]


def test_duplicate_url_leaves_the_first_secret_untouched(
    client: TestClient, settings: Settings
) -> None:
    """A rejected re-registration must not rotate or re-reveal the stored secret."""
    first = _create(client)
    r = client.post("/endpoints", json={"url": URL, "description": "second try"})
    assert r.status_code == 409
    assert str(first["secret"]) not in r.text
    rows = sqlite3.connect(settings.db_path).execute("SELECT secret, description FROM endpoints")
    assert rows.fetchall() == [(first["secret"], None)]


def test_duplicate_url_is_rejected_regardless_of_description(client: TestClient) -> None:
    _create(client, URL, description="first")
    assert client.post("/endpoints", json={"url": URL, "description": "other"}).status_code == 409


def test_distinct_paths_on_one_host_are_separate_endpoints(client: TestClient) -> None:
    a = _create(client, "https://hooks.example.com/a")
    b = _create(client, "https://hooks.example.com/b")
    assert a["id"] != b["id"]
    assert len(client.get("/endpoints").json()) == 2


def test_secret_is_stored_for_later_signing(client: TestClient, settings: Settings) -> None:
    created = _create(client)
    row = sqlite3.connect(settings.db_path).execute("SELECT secret FROM endpoints").fetchone()
    assert row[0] == created["secret"]


def test_secret_not_in_logs(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        created = _create(client)
        client.get("/endpoints")
    assert str(created["secret"]) not in caplog.text


def test_client_cannot_supply_secret(client: TestClient) -> None:
    r = client.post("/endpoints", json={"url": URL, "secret": "whsec_chosen"})
    assert r.status_code == 422
    assert client.get("/endpoints").json() == []


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/",
        "http://93.184.216.34/",
        "ftp://93.184.216.34/",
        "file:///etc/passwd",
        "https://user:pw@93.184.216.34/",
    ],
)
def test_prohibited_destinations_are_rejected_and_not_stored(client: TestClient, url: str) -> None:
    r = client.post("/endpoints", json={"url": url})
    assert r.status_code == 422
    assert r.json()["detail"].startswith("url rejected:")
    assert client.get("/endpoints").json() == []


def test_host_resolving_to_private_address_is_rejected(client: TestClient) -> None:
    client.app.dependency_overrides[get_resolver] = lambda: lambda _h: ["10.1.2.3"]  # type: ignore[attr-defined]
    r = client.post("/endpoints", json={"url": "https://internal.example.com/"})
    assert r.status_code == 422
    assert client.get("/endpoints").json() == []


def test_allowlisted_local_receiver_is_accepted(allowlisted_client: TestClient) -> None:
    r = allowlisted_client.post("/endpoints", json={"url": "http://127.0.0.1:9000/hook"})
    assert r.status_code == 201


def test_non_allowlisted_local_address_still_rejected(allowlisted_client: TestClient) -> None:
    r = allowlisted_client.post("/endpoints", json={"url": "http://127.0.0.2:9000/hook"})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"url": ""},
        {"url": 123},
        {"url": URL, "description": "x" * 201},
        {"url": URL, "unknown": 1},
        {"url": "https://example.com/" + "a" * 2100},
    ],
)
def test_invalid_bodies_return_422(client: TestClient, body: dict[str, object]) -> None:
    assert client.post("/endpoints", json=body).status_code == 422


def test_pagination(client: TestClient) -> None:
    ids = [_create(client, _url(i))["id"] for i in range(5)]
    page1 = client.get("/endpoints", params={"limit": 2}).json()
    page2 = client.get("/endpoints", params={"limit": 2, "offset": 2}).json()
    page3 = client.get("/endpoints", params={"limit": 2, "offset": 4}).json()
    assert [e["id"] for e in page1 + page2 + page3] == ids


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_pagination_bounds(client: TestClient, params: dict[str, int]) -> None:
    assert client.get("/endpoints", params=params).status_code == 422


def test_empty_list(client: TestClient) -> None:
    assert client.get("/endpoints").json() == []
