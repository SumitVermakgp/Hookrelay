import pytest

from app.core.retry import is_retryable, retry_delay_seconds


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [(1, 30), (2, 60), (3, 120), (4, 240), (5, 480), (6, 960), (7, 1920), (8, 3600), (9, 3600)],
)
def test_exponential_delay_with_cap(attempt: int, expected: float) -> None:
    assert retry_delay_seconds(attempt, base=30, max_delay=3600) == expected


def test_zero_base_means_immediate_retry() -> None:
    assert retry_delay_seconds(5, base=0, max_delay=3600) == 0


def test_huge_attempt_number_does_not_overflow() -> None:
    assert retry_delay_seconds(10_000, base=30, max_delay=3600) == 3600


@pytest.mark.parametrize("attempt", [0, -1])
def test_attempt_number_must_be_positive(attempt: int) -> None:
    with pytest.raises(ValueError):
        retry_delay_seconds(attempt, base=30, max_delay=3600)


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (None, "timeout", True),
        (None, "connection_error", True),
        (500, "http_error", True),
        (502, "http_error", True),
        (503, "http_error", True),
        (599, "http_error", True),
        (408, "http_error", True),
        (429, "http_error", True),
        (400, "http_error", False),
        (401, "http_error", False),
        (403, "http_error", False),
        (404, "http_error", False),
        (410, "http_error", False),
        (422, "http_error", False),
        (302, "redirect_not_followed", False),
        (None, "destination_blocked", False),
        (None, "internal_error", False),
        (200, None, False),
        (None, None, False),
        (None, "http_error", False),
    ],
)
def test_retryable_classification(status: int | None, error: str | None, expected: bool) -> None:
    assert is_retryable(status, error) is expected
