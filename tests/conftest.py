from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.url_policy import Resolver
from app.main import create_app

PUBLIC_IP = "93.184.216.34"
SMALL_LIMIT = 200


def public_resolver(_: str) -> list[str]:
    return [PUBLIC_IP]


def make_client(
    settings: Settings,
    resolver: Resolver = public_resolver,
    transport: httpx.BaseTransport | None = None,
) -> Iterator[TestClient]:
    app = create_app(settings, resolver=resolver, transport=transport)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Background delivery is off so API tests control when anything is sent.
    return Settings(db_path=str(tmp_path / "test.db"), auto_deliver=False, retry_interval_seconds=0)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """Client whose DNS resolver always returns a public address (no real DNS)."""
    yield from make_client(settings)


@pytest.fixture
def allowlisted_client(tmp_path: Path) -> Iterator[TestClient]:
    s = Settings(
        db_path=str(tmp_path / "allow.db"),
        test_allowed_hosts=frozenset({"127.0.0.1"}),
        auto_deliver=False,
        retry_interval_seconds=0,
    )
    yield from make_client(s)


@pytest.fixture
def small_limit_client(tmp_path: Path) -> Iterator[TestClient]:
    s = Settings(
        db_path=str(tmp_path / "small.db"),
        max_payload_bytes=SMALL_LIMIT,
        auto_deliver=False,
        retry_interval_seconds=0,
    )
    yield from make_client(s)
