import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.core.retry import retry_delay_seconds
from app.core.timeutil import utc_iso
from app.db.database import connect, init_db
from app.services.delivery import (
    attempt_delivery,
    claim_attempt,
    run_due_retries,
)
from app.services.endpoints import create_endpoint
from app.services.events import create_event

T0 = 1_700_000_000.0


def public(_: str) -> list[str]:
    return ["93.184.216.34"]


class Script:
    """Transport handler returning scripted statuses (last one repeats)."""

    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            idx = min(self.calls, len(self.statuses) - 1)
            self.calls += 1
        return httpx.Response(self.statuses[idx])


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class World:
    def __init__(self, tmp_path: Path, **overrides: object) -> None:
        kw: dict[str, object] = {
            "db_path": str(tmp_path / "w.db"),
            "max_attempts": 3,
            "retry_base_seconds": 30.0,
            "retry_max_delay_seconds": 3600.0,
            "auto_deliver": True,
        }
        kw.update(overrides)
        self.settings = Settings(**kw)  # type: ignore[arg-type]
        init_db(self.settings.db_path)
        self.db = connect(self.settings.db_path)
        create_endpoint(self.db, "https://hooks.example.com/r", None)
        create_event(self.db, "t", "{}", None)
        self.delivery_id: str = self.db.execute("SELECT id FROM deliveries").fetchone()["id"]
        self.clock = Clock()

    def attempt(
        self, handler: Callable[[httpx.Request], httpx.Response], *, manual: bool = False
    ) -> bool | None:
        return attempt_delivery(
            self.db,
            self.settings,
            self.delivery_id,
            manual=manual,
            resolver=public,
            transport=httpx.MockTransport(handler),
            clock=self.clock,
        )

    def sweep(self, handler: Callable[[httpx.Request], httpx.Response]) -> int:
        return run_due_retries(
            self.settings,
            resolver=public,
            transport=httpx.MockTransport(handler),
            clock=self.clock,
        )

    def delivery(self) -> sqlite3.Row:
        row: sqlite3.Row = self.db.execute(
            "SELECT * FROM deliveries WHERE id = ?", (self.delivery_id,)
        ).fetchone()
        return row

    def attempts(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM delivery_attempts WHERE delivery_id = ? ORDER BY attempt_number",
            (self.delivery_id,),
        ).fetchall()


@pytest.fixture
def w(tmp_path: Path) -> World:
    return World(tmp_path)


# scheduling


def test_retryable_failure_schedules_next_attempt_with_backoff(w: World) -> None:
    assert w.attempt(Script(500)) is False
    d = w.delivery()
    assert d["status"] == "pending"
    assert d["next_attempt_at"] == utc_iso(T0 + retry_delay_seconds(1, 30, 3600))


def test_backoff_grows_per_attempt(w: World) -> None:
    h = Script(500)
    expected = []
    for n in (1, 2):
        w.clock.now = T0 + 10_000 * n
        w.attempt(h)
        expected.append(utc_iso(w.clock.now + retry_delay_seconds(n, 30, 3600)))
        assert w.delivery()["next_attempt_at"] == expected[-1]
    assert expected[1] != expected[0]


def test_not_claimed_before_due_time_and_claimed_exactly_at_it(w: World) -> None:
    h = Script(500)
    w.attempt(h)
    due = T0 + 30
    w.clock.now = due - 0.001
    assert w.attempt(h) is None
    assert len(w.attempts()) == 1
    w.clock.now = due
    assert w.attempt(h) is False
    assert len(w.attempts()) == 2


def test_sweeper_runs_only_due_retries(w: World) -> None:
    h = Script(500)
    w.attempt(h)
    assert w.sweep(h) == 0  # not due yet
    w.clock.now = T0 + 30
    assert w.sweep(h) == 1
    assert len(w.attempts()) == 2


# bounds (the "off by one" family)


@pytest.mark.parametrize("max_attempts", [1, 2, 3, 5])
def test_never_more_attempts_than_configured(tmp_path: Path, max_attempts: int) -> None:
    w = World(tmp_path, max_attempts=max_attempts, retry_base_seconds=0.0)
    h = Script(500)
    for _ in range(max_attempts + 5):
        w.sweep(h)
        w.attempt(h)  # also try the direct path
    assert len(w.attempts()) == max_attempts
    assert [a["attempt_number"] for a in w.attempts()] == list(range(1, max_attempts + 1))
    assert h.calls == max_attempts
    assert w.delivery()["status"] == "failed"
    assert w.delivery()["next_attempt_at"] is None


def test_max_attempts_one_means_no_retry(tmp_path: Path) -> None:
    w = World(tmp_path, max_attempts=1)
    assert w.attempt(Script(500)) is False
    assert w.delivery()["status"] == "failed"
    assert w.delivery()["next_attempt_at"] is None


def test_claim_refuses_at_the_boundary(tmp_path: Path) -> None:
    w = World(tmp_path, max_attempts=2, retry_base_seconds=0.0)
    h = Script(500)
    w.attempt(h)
    assert claim_attempt(w.db, w.delivery_id, 2, clock=w.clock) is not None  # 2nd of 2
    # attempt 2 is in flight; once finished at the cap nothing more may start
    w.db.execute("UPDATE delivery_attempts SET finished_at = 'x' WHERE attempt_number = 2")
    w.db.commit()
    assert claim_attempt(w.db, w.delivery_id, 2, clock=w.clock) is None
    assert claim_attempt(w.db, w.delivery_id, 2, manual=True, clock=w.clock) is None


def test_exhausted_delivery_is_failed_and_sweeper_ignores_it(w: World) -> None:
    h = Script(500)
    for i in range(3):
        w.clock.now = T0 + 10_000 * (i + 1)
        w.attempt(h)
    assert w.delivery()["status"] == "failed"
    w.clock.now = T0 + 10_000_000
    assert w.sweep(h) == 0
    assert h.calls == 3


# outcomes across attempts


def test_success_on_later_attempt_stops_retrying(tmp_path: Path) -> None:
    w = World(tmp_path, retry_base_seconds=0.0)
    h = Script(500, 503, 200, 500)
    while w.sweep(h):
        pass
    assert h.calls == 3
    assert w.delivery()["status"] == "succeeded"
    assert w.delivery()["next_attempt_at"] is None
    assert [a["status_code"] for a in w.attempts()] == [500, 503, 200]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
def test_client_errors_are_not_retried_automatically(w: World, status: int) -> None:
    h = Script(status)
    w.attempt(h)
    assert w.delivery()["status"] == "failed"
    w.clock.now = T0 + 100_000
    assert w.sweep(h) == 0
    assert h.calls == 1


def test_redirect_is_terminal(w: World) -> None:
    h = Script(302)
    w.attempt(h)
    assert w.delivery()["status"] == "failed"
    w.clock.now = T0 + 100_000
    assert w.sweep(h) == 0
    assert h.calls == 1


def test_blocked_destination_is_terminal(w: World) -> None:
    h = Script(200)
    result = attempt_delivery(
        w.db,
        w.settings,
        w.delivery_id,
        resolver=lambda _: ["10.0.0.1"],
        transport=httpx.MockTransport(h),
        clock=w.clock,
    )
    assert result is False
    assert (h.calls, w.delivery()["status"]) == (0, "failed")
    assert w.attempts()[0]["error"] == "destination_blocked"


def test_internal_error_is_terminal(w: World) -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise RuntimeError("boom")

    w.attempt(boom)
    assert w.delivery()["status"] == "failed"
    assert w.attempts()[0]["error"] == "internal_error"


# manual retry


def test_manual_retry_creates_a_distinct_attempt_for_terminal_failure(w: World) -> None:
    h = Script(404, 200)
    w.attempt(h)
    assert w.delivery()["status"] == "failed"
    assert w.attempt(h, manual=True) is True
    assert w.delivery()["status"] == "succeeded"
    assert [(a["attempt_number"], a["status_code"]) for a in w.attempts()] == [(1, 404), (2, 200)]
    assert len({a["id"] for a in w.attempts()}) == 2


def test_manual_retry_skips_the_wait_on_a_scheduled_retry(w: World) -> None:
    h = Script(500, 200)
    w.attempt(h)
    assert w.attempt(h) is None  # not due
    assert w.attempt(h, manual=True) is True


def test_manual_retry_respects_the_maximum(w: World) -> None:
    h = Script(404)
    w.attempt(h)  # 1
    assert w.attempt(h, manual=True) is False  # 2
    assert w.attempt(h, manual=True) is False  # 3
    assert w.attempt(h, manual=True) is None  # would be 4
    assert len(w.attempts()) == 3
    assert h.calls == 3


def test_manual_retry_refused_after_success(w: World) -> None:
    h = Script(200)
    w.attempt(h)
    assert w.attempt(h, manual=True) is None
    assert h.calls == 1


def test_manual_retry_refused_while_attempt_in_flight(w: World) -> None:
    assert claim_attempt(w.db, w.delivery_id, 3, clock=w.clock) is not None
    assert w.attempt(Script(200), manual=True) is None
    assert len(w.attempts()) == 1


def test_manual_failure_with_attempts_left_is_rescheduled(w: World) -> None:
    h = Script(404, 500)
    w.attempt(h)
    w.attempt(h, manual=True)  # 500 is retryable and attempt 2 < 3
    d = w.delivery()
    assert d["status"] == "pending"
    assert d["next_attempt_at"] == utc_iso(T0 + retry_delay_seconds(2, 30, 3600))


# sweeper scope


def test_sweeper_ignores_never_attempted_deliveries_when_auto_deliver_is_off(
    tmp_path: Path,
) -> None:
    w = World(tmp_path, auto_deliver=False)
    h = Script(200)
    assert w.sweep(h) == 0
    assert h.calls == 0


def test_sweeper_picks_up_never_attempted_deliveries_when_auto_deliver_is_on(w: World) -> None:
    h = Script(200)
    assert w.sweep(h) == 1
    assert w.delivery()["status"] == "succeeded"


def test_sweeper_still_handles_scheduled_retries_when_auto_deliver_is_off(tmp_path: Path) -> None:
    w = World(tmp_path, auto_deliver=False)
    h = Script(500, 200)
    w.attempt(h, manual=True)  # first attempt made by hand
    w.clock.now = T0 + 30
    assert w.sweep(h) == 1
    assert w.delivery()["status"] == "succeeded"


# concurrency


def _run_threads(n: int, target: Callable[[], None]) -> None:
    barrier = threading.Barrier(n)

    def wrapped() -> None:
        barrier.wait()
        target()

    threads = [threading.Thread(target=wrapped) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_concurrent_sweepers_never_exceed_the_maximum(tmp_path: Path) -> None:
    w = World(tmp_path, max_attempts=3, retry_base_seconds=0.0)
    h = Script(500)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(10):
                w.sweep(h)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    _run_threads(12, work)
    assert errors == []
    attempts = w.attempts()
    assert [a["attempt_number"] for a in attempts] == [1, 2, 3]
    assert h.calls == 3  # one request per recorded attempt, none duplicated
    assert w.delivery()["status"] == "failed"
    assert all(a["finished_at"] is not None for a in attempts)


def test_concurrent_manual_claims_start_exactly_one_attempt(w: World) -> None:
    results: list[str | None] = []
    lock = threading.Lock()

    def work() -> None:
        conn = connect(w.settings.db_path)
        try:
            r = claim_attempt(conn, w.delivery_id, 3, manual=True, clock=w.clock)
            with lock:
                results.append(r)
        finally:
            conn.close()

    _run_threads(16, work)
    assert sum(r is not None for r in results) == 1
    assert len(w.attempts()) == 1


def test_concurrent_manual_retries_of_a_failed_delivery_do_not_double_send(
    tmp_path: Path,
) -> None:
    w = World(tmp_path, max_attempts=3)
    w.attempt(Script(404))  # terminal failure, 2 attempts remain
    h = Script(404)
    outcomes: list[bool | None] = []
    lock = threading.Lock()

    def work() -> None:
        conn = connect(w.settings.db_path)
        try:
            r = attempt_delivery(
                conn,
                w.settings,
                w.delivery_id,
                manual=True,
                resolver=public,
                transport=httpx.MockTransport(h),
                clock=w.clock,
            )
            with lock:
                outcomes.append(r)
        finally:
            conn.close()

    _run_threads(16, work)
    total = len(w.attempts())
    assert total <= 3  # never past the maximum
    assert h.calls == total - 1  # one request per recorded attempt after the first
    assert [a["attempt_number"] for a in w.attempts()] == list(range(1, total + 1))
