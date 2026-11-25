"""Retry policy: which failures are retried and how long to wait.

``max_attempts`` is the TOTAL number of attempts, first try included. With
max_attempts=3 a delivery gets at most 3 attempt records.
"""

_TRANSIENT_ERRORS = frozenset({"timeout", "connection_error"})
_RETRYABLE_STATUS = frozenset({408, 429})
_MAX_EXPONENT = 62


def retry_delay_seconds(attempt_number: int, base: float, max_delay: float) -> float:
    """Delay before the attempt that follows failed attempt ``attempt_number``.

    Exponential (base, 2*base, 4*base, ...) capped at max_delay. No jitter, so the
    schedule is deterministic and testable.
    """
    if attempt_number < 1:
        raise ValueError("attempt_number starts at 1")
    exponent = min(attempt_number - 1, _MAX_EXPONENT)
    return min(base * (2.0**exponent), max_delay)


def is_retryable(status_code: int | None, error: str | None) -> bool:
    """Transient failures only: timeouts, connection errors, 408, 429 and 5xx.

    Not retried: other 4xx, redirects, destinations blocked by policy, internal errors.
    """
    if error is None:
        return False
    if error in _TRANSIENT_ERRORS:
        return True
    if error == "http_error" and status_code is not None:
        return status_code in _RETRYABLE_STATUS or 500 <= status_code <= 599
    return False
