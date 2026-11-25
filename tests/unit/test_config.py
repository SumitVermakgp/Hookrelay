import pytest

from app.core.config import Settings, load_settings


def test_defaults() -> None:
    s = load_settings({})
    assert s.max_attempts == 3
    assert s.max_payload_bytes == 65_536
    assert s.test_allowed_hosts == frozenset()


def test_env_overrides() -> None:
    s = load_settings(
        {
            "HOOKRELAY_DB_PATH": "x.db",
            "HOOKRELAY_MAX_ATTEMPTS": "5",
            "HOOKRELAY_TEST_ALLOWED_HOSTS": " 127.0.0.1 , Localhost ,",
        }
    )
    assert s.db_path == "x.db"
    assert s.max_attempts == 5
    assert s.test_allowed_hosts == frozenset({"127.0.0.1", "localhost"})


def test_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError):
        Settings(max_attempts=0)


def test_rejects_non_numeric_attempts() -> None:
    with pytest.raises(ValueError):
        load_settings({"HOOKRELAY_MAX_ATTEMPTS": "many"})


def test_delivery_defaults_and_overrides() -> None:
    d = load_settings({})
    assert d.delivery_timeout_seconds == 10.0
    assert d.auto_deliver is True
    s = load_settings({"HOOKRELAY_DELIVERY_TIMEOUT": "2.5", "HOOKRELAY_AUTO_DELIVER": "No"})
    assert s.delivery_timeout_seconds == 2.5
    assert s.auto_deliver is False


@pytest.mark.parametrize("value", ["0", "-1", "121", "nan"])
def test_rejects_bad_timeout(value: str) -> None:
    with pytest.raises(ValueError):
        load_settings({"HOOKRELAY_DELIVERY_TIMEOUT": value})


def test_rejects_bad_boolean() -> None:
    with pytest.raises(ValueError):
        load_settings({"HOOKRELAY_AUTO_DELIVER": "maybe"})


def test_retry_defaults_and_overrides() -> None:
    d = load_settings({})
    assert (d.retry_base_seconds, d.retry_max_delay_seconds, d.retry_interval_seconds) == (
        30.0,
        3600.0,
        15.0,
    )
    s = load_settings(
        {
            "HOOKRELAY_RETRY_BASE_SECONDS": "1",
            "HOOKRELAY_RETRY_MAX_DELAY_SECONDS": "10",
            "HOOKRELAY_RETRY_INTERVAL_SECONDS": "0",
        }
    )
    assert (s.retry_base_seconds, s.retry_max_delay_seconds, s.retry_interval_seconds) == (1, 10, 0)


@pytest.mark.parametrize(
    "env",
    [
        {"HOOKRELAY_MAX_ATTEMPTS": "21"},
        {"HOOKRELAY_RETRY_BASE_SECONDS": "-1"},
        {"HOOKRELAY_RETRY_BASE_SECONDS": "100", "HOOKRELAY_RETRY_MAX_DELAY_SECONDS": "10"},
        {"HOOKRELAY_RETRY_INTERVAL_SECONDS": "-1"},
        {"HOOKRELAY_RETRY_INTERVAL_SECONDS": "3601"},
    ],
)
def test_rejects_bad_retry_settings(env: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        load_settings(env)
