"""The background sweeper against a real local receiver."""

import sqlite3
import time
from collections.abc import Callable, Iterator
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


def _settings(tmp_path: Path, **kw: object) -> Settings:
    base: dict[str, object] = {
        "db_path": str(tmp_path / "loop.db"),
        "test_allowed_hosts": frozenset({"127.0.0.1"}),
        "max_attempts": 3,
        "retry_base_seconds": 0.0,
        "auto_deliver": True,
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def _wait(cond: Callable[[], bool], timeout: float = 6.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _state(settings: Settings) -> tuple[str, int]:
    conn = sqlite3.connect(settings.db_path)
    try:
        row = conn.execute(
            "SELECT d.status, (SELECT COUNT(*) FROM delivery_attempts a WHERE a.delivery_id = d.id) "
            "FROM deliveries d"
        ).fetchone()
        return (str(row[0]), int(row[1])) if row else ("none", 0)
    finally:
        conn.close()


def test_loop_retries_until_the_maximum_then_stops(
    tmp_path: Path, receiver: ReceiverServer
) -> None:
    s = _settings(tmp_path, retry_interval_seconds=0.05)
    for c in make_client(s):
        c.post("/endpoints", json={"url": receiver.url("/fail")})
        c.post("/events", json={"type": "t", "payload": {}})
        assert _wait(lambda: _state(s) == ("failed", 3)), _state(s)
        time.sleep(0.4)  # give the loop every chance to overshoot
        assert _state(s) == ("failed", 3)
        assert receiver.paths() == ["/fail"] * 3


def test_each_retry_is_signed_and_numbered(tmp_path: Path, receiver: ReceiverServer) -> None:
    s = _settings(tmp_path, retry_interval_seconds=0.05)
    for c in make_client(s):
        ep = c.post("/endpoints", json={"url": receiver.url("/fail")}).json()
        receiver.secret = ep["secret"]
        c.post("/events", json={"type": "t", "payload": {}})
        assert _wait(lambda: _state(s) == ("failed", 3))
        assert [r.headers["hookrelay-attempt"] for r in receiver.received] == ["1", "2", "3"]
        assert all(r.signature_valid for r in receiver.received)


def test_loop_disabled_means_no_automatic_retry(tmp_path: Path, receiver: ReceiverServer) -> None:
    s = _settings(tmp_path, retry_interval_seconds=0)
    for c in make_client(s):
        c.post("/endpoints", json={"url": receiver.url("/fail")})
        c.post("/events", json={"type": "t", "payload": {}})
        time.sleep(0.4)
        assert _state(s) == ("pending", 1)


def test_manual_retry_route_against_real_receiver(tmp_path: Path, receiver: ReceiverServer) -> None:
    s = _settings(tmp_path, retry_interval_seconds=0, retry_base_seconds=3600)
    for c in make_client(s):
        c.post("/endpoints", json={"url": receiver.url("/status/404")})
        event = c.post("/events", json={"type": "t", "payload": {}}).json()
        did = event["deliveries"][0]["id"]
        assert _state(s) == ("failed", 1)  # 404 is not retried automatically
        assert c.post(f"/deliveries/{did}/retry").status_code == 200
        assert c.post(f"/deliveries/{did}/retry").status_code == 200
        assert c.post(f"/deliveries/{did}/retry").status_code == 409
        assert receiver.paths() == ["/status/404"] * 3


def test_lifespan_shuts_down_cleanly_with_loop_running(tmp_path: Path) -> None:
    s = _settings(tmp_path, retry_interval_seconds=0.05)
    start = time.time()
    for c in make_client(s):
        assert isinstance(c, TestClient)
        assert c.get("/health").status_code == 200
    assert time.time() - start < 3
