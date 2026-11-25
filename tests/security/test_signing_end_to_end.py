"""What a real receiver sees, verified the way a customer would verify it."""

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.security import SIGNATURE_HEADER, SIGNATURE_TOLERANCE_SECONDS, verify_signature
from tests.conftest import make_client
from tools.local_receiver import ReceiverServer


@pytest.fixture
def receiver() -> Iterator[ReceiverServer]:
    server = ReceiverServer().start()
    yield server
    server.stop()


def _deliver(tmp_path: Path, receiver: ReceiverServer) -> str:
    s = Settings(
        db_path=str(tmp_path / "sig.db"),
        test_allowed_hosts=frozenset({"127.0.0.1"}),
        retry_interval_seconds=0,
    )
    secret = ""
    for c in make_client(s):
        secret = c.post("/endpoints", json={"url": receiver.url("/ok")}).json()["secret"]
        c.post("/events", json={"type": "t", "payload": {"amount": 10}})
    return secret


def test_receiver_verifies_with_the_right_secret_only(
    tmp_path: Path, receiver: ReceiverServer
) -> None:
    secret = _deliver(tmp_path, receiver)
    (got,) = receiver.received
    header = got.headers[SIGNATURE_HEADER.lower()]
    now = time.time()
    assert verify_signature(secret, got.body, header, now)
    assert not verify_signature(secret + "x", got.body, header, now)
    assert not verify_signature("whsec_wrong", got.body, header, now)


def test_modified_body_or_header_fails_verification(
    tmp_path: Path, receiver: ReceiverServer
) -> None:
    secret = _deliver(tmp_path, receiver)
    (got,) = receiver.received
    header = got.headers[SIGNATURE_HEADER.lower()]
    now = time.time()
    assert not verify_signature(secret, got.body.replace(b"10", b"99"), header, now)
    assert not verify_signature(secret, got.body, header.replace("v1=", "v1=0"), now)
    assert not verify_signature(secret, got.body, "", now)


def test_captured_request_cannot_be_replayed_later(
    tmp_path: Path, receiver: ReceiverServer
) -> None:
    secret = _deliver(tmp_path, receiver)
    (got,) = receiver.received
    header = got.headers[SIGNATURE_HEADER.lower()]
    later = time.time() + SIGNATURE_TOLERANCE_SECONDS + 60
    assert not verify_signature(secret, got.body, header, later)
