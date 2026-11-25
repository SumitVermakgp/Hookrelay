"""Destination restrictions at registration time and at send time."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.url_policy import UrlPolicyError, default_resolver, validate_destination_url
from app.db.database import connect, init_db
from app.services.delivery import attempt_delivery
from app.services.endpoints import create_endpoint
from app.services.events import create_event
from tests.conftest import make_client
from tools.local_receiver import ReceiverServer

# These use the real resolver: localhost resolves from /etc/hosts, no network needed.
REAL_DNS_BLOCKED = [
    "https://localhost/",
    "https://localhost./",
    "https://LOCALHOST/",
    "https://\uff4c\uff4f\uff43\uff41\uff4c\uff48\uff4f\uff53\uff54/",  # full-width "localhost"
    "https://2130706433/",
    "https://0x7f.0.0.1/",
    "https://017700000001/",
    "https://127.1/",
    "https://[fe80::1%25eth0]/",
]


@pytest.mark.parametrize("url", REAL_DNS_BLOCKED)
def test_loopback_spellings_are_rejected_with_the_real_resolver(url: str) -> None:
    with pytest.raises(UrlPolicyError):
        validate_destination_url(url, resolver=default_resolver)


@pytest.mark.parametrize("url", REAL_DNS_BLOCKED[:4] + ["https://127.1/"])
def test_api_rejects_loopback_spellings_end_to_end(tmp_path: Path, url: str) -> None:
    s = Settings(db_path=str(tmp_path / "s.db"), auto_deliver=False, retry_interval_seconds=0)
    for c in make_client(s, resolver=default_resolver):
        assert c.post("/endpoints", json={"url": url}).status_code == 422
        assert c.get("/endpoints").json() == []


@pytest.mark.parametrize(
    "url",
    [
        "HTTPS://127.0.0.1/",
        "https://127.0.0.1@93.184.216.34/",
        "https://93.184.216.34@127.0.0.1/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "https://metadata.google.internal/",
    ],
)
def test_tricky_urls_never_reach_a_private_address(client: TestClient, url: str) -> None:
    metadata = lambda host: ["169.254.169.254"] if "metadata" in host else ["93.184.216.34"]
    for c in make_client(
        Settings(
            db_path=client.app.state.settings.db_path + ".x",
            auto_deliver=False,
            retry_interval_seconds=0,
        ),
        resolver=metadata,
    ):
        assert c.post("/endpoints", json={"url": url}).status_code == 422


def test_default_configuration_cannot_register_a_local_receiver(tmp_path: Path) -> None:
    server = ReceiverServer().start()
    try:
        s = Settings(db_path=str(tmp_path / "d.db"), auto_deliver=False, retry_interval_seconds=0)
        for c in make_client(s):
            assert c.post("/endpoints", json={"url": server.url("/ok")}).status_code == 422
    finally:
        server.stop()


# send time


class _Env:
    def __init__(self, tmp_path: Path) -> None:
        self.settings = Settings(db_path=str(tmp_path / "e.db"), max_attempts=3)
        init_db(self.settings.db_path)
        self.db = connect(self.settings.db_path)
        create_endpoint(self.db, "https://hooks.example.com/r", None)
        create_event(self.db, "t", "{}", None)
        self.delivery_id = self.db.execute("SELECT id FROM deliveries").fetchone()["id"]

    def send(self, resolver, handler):  # type: ignore[no-untyped-def]
        return attempt_delivery(
            self.db,
            self.settings,
            self.delivery_id,
            resolver=resolver,
            transport=httpx.MockTransport(handler),
        )

    def error(self) -> str | None:
        row = self.db.execute("SELECT error FROM delivery_attempts").fetchone()
        return None if row is None else row["error"]


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["10.0.0.1"],
        ["::1"],
        ["::ffff:127.0.0.1"],
        ["93.184.216.34", "10.0.0.1"],  # one public, one private
        [],
    ],
)
def test_rebinding_to_a_blocked_address_sends_nothing(tmp_path: Path, addresses: list[str]) -> None:
    env = _Env(tmp_path)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    assert env.send(lambda _h: addresses, handler) is False
    assert calls == []
    assert env.error() in {"destination_blocked"}


@pytest.mark.parametrize(
    "location",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:22/",
        "https://localhost/admin",
        "/internal",
    ],
)
def test_redirects_to_internal_targets_are_never_requested(tmp_path: Path, location: str) -> None:
    env = _Env(tmp_path)
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        return httpx.Response(302, headers={"Location": location})

    assert env.send(lambda _h: ["93.184.216.34"], handler) is False
    assert seen_hosts == ["93.184.216.34"]  # exactly one request, to the pinned address
    assert env.error() == "redirect_not_followed"


def test_fragment_trick_requests_only_the_public_host(tmp_path: Path) -> None:
    """Everything after # is a fragment: the host is the public one, never 127.0.0.1."""
    from app.services.delivery import pinned_target

    url = "https://93.184.216.34#@127.0.0.1/"
    assert validate_destination_url(url) == ["93.184.216.34"]
    target, headers, _ = pinned_target(url, ["93.184.216.34"])
    assert target == "https://93.184.216.34/"
    assert headers == {}


def test_delivery_url_is_rebuilt_from_validated_parts() -> None:
    from app.services.delivery import pinned_target

    target, _, _ = pinned_target("https://93.184.216.34:8443/a/b?x=1#frag", ["93.184.216.34"])
    assert target == "https://93.184.216.34:8443/a/b?x=1"
    target, _, _ = pinned_target("https://[2606:2800:220:1::1]/p", ["2606:2800:220:1::1"])
    assert target == "https://[2606:2800:220:1::1]/p"
