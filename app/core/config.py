"""Runtime configuration loaded from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str = "hookrelay.db"
    max_attempts: int = 3
    max_endpoints: int = 100
    max_payload_bytes: int = 65_536
    test_allowed_hosts: frozenset[str] = frozenset()
    delivery_timeout_seconds: float = 10.0
    auto_deliver: bool = True
    retry_base_seconds: float = 30.0
    retry_max_delay_seconds: float = 3600.0
    retry_interval_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20")
        if not 1 <= self.max_endpoints <= 10_000:
            raise ValueError("max_endpoints must be between 1 and 10000")
        if not 0 <= self.retry_base_seconds <= self.retry_max_delay_seconds:
            raise ValueError("retry_base_seconds must be between 0 and retry_max_delay_seconds")
        if not 0 <= self.retry_interval_seconds <= 3600:
            raise ValueError("retry_interval_seconds must be between 0 and 3600 (0 disables)")
        if self.max_payload_bytes < 1:
            raise ValueError("max_payload_bytes must be at least 1")
        if not 0 < self.delivery_timeout_seconds <= 120:
            raise ValueError("delivery_timeout_seconds must be between 0 and 120")


def _parse_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"not a boolean: {value!r}")


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if env is None else env
    hosts = {
        h.strip().lower()
        for h in source.get("HOOKRELAY_TEST_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    }
    return Settings(
        db_path=source.get("HOOKRELAY_DB_PATH", "hookrelay.db"),
        max_attempts=int(source.get("HOOKRELAY_MAX_ATTEMPTS", "3")),
        max_endpoints=int(source.get("HOOKRELAY_MAX_ENDPOINTS", "100")),
        max_payload_bytes=int(source.get("HOOKRELAY_MAX_PAYLOAD_BYTES", "65536")),
        test_allowed_hosts=frozenset(hosts),
        delivery_timeout_seconds=float(source.get("HOOKRELAY_DELIVERY_TIMEOUT", "10")),
        auto_deliver=_parse_bool(source.get("HOOKRELAY_AUTO_DELIVER", "true")),
        retry_base_seconds=float(source.get("HOOKRELAY_RETRY_BASE_SECONDS", "30")),
        retry_max_delay_seconds=float(source.get("HOOKRELAY_RETRY_MAX_DELAY_SECONDS", "3600")),
        retry_interval_seconds=float(source.get("HOOKRELAY_RETRY_INTERVAL_SECONDS", "15")),
    )
