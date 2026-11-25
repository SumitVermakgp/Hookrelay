import hashlib
import hmac

import pytest

from app.core import security
from app.core.security import (
    SIGNATURE_TOLERANCE_SECONDS,
    generate_secret,
    sign_payload,
    verify_signature,
)

SECRET = "whsec_test_secret"
BODY = b'{"a":1}'
TS = 1_700_000_000
# Computed independently of app code with hmac/hashlib over b"1700000000." + body.
KNOWN_MAC = "aecbc681fe3d2604c08ff8e880a0988d997a12367fcf9545e6a3b90c1dfc04b7"


def _header(ts: int = TS, secret: str = SECRET, body: bytes = BODY) -> str:
    return sign_payload(secret, body, ts)


def test_known_answer_vector() -> None:
    assert sign_payload(SECRET, BODY, TS) == f"t={TS},v1={KNOWN_MAC}"


def test_mac_is_hmac_sha256_over_timestamp_dot_body() -> None:
    expected = hmac.new(SECRET.encode(), f"{TS}.".encode() + BODY, hashlib.sha256).hexdigest()
    assert sign_payload(SECRET, BODY, TS).endswith(f"v1={expected}")


def test_valid_signature_verifies() -> None:
    assert verify_signature(SECRET, BODY, _header(), now=TS)


def test_secret_must_not_be_empty() -> None:
    with pytest.raises(ValueError):
        sign_payload("", BODY, TS)
    assert not verify_signature("", BODY, _header(), now=TS)


def test_wrong_secret_fails() -> None:
    assert not verify_signature("whsec_other", BODY, _header(), now=TS)


def test_tampered_body_fails() -> None:
    assert not verify_signature(SECRET, b'{"a":2}', _header(), now=TS)
    assert not verify_signature(SECRET, BODY + b" ", _header(), now=TS)


def test_tampered_timestamp_fails() -> None:
    mac = _header().split("v1=")[1]
    assert not verify_signature(SECRET, BODY, f"t={TS + 1},v1={mac}", now=TS)


def test_flipped_signature_character_fails() -> None:
    h = _header()
    flipped = h[:-1] + ("0" if h[-1] != "0" else "1")
    assert not verify_signature(SECRET, BODY, flipped, now=TS)


@pytest.mark.parametrize("header", [None, ""])
def test_missing_header_fails(header: str | None) -> None:
    assert not verify_signature(SECRET, BODY, header, now=TS)


@pytest.mark.parametrize(
    "header",
    [
        "garbage",
        "t=,v1=",
        f"t={TS}",
        f"v1={KNOWN_MAC}",
        f"t=abc,v1={KNOWN_MAC}",
        f"t=-{TS},v1={KNOWN_MAC}",
        f"t=+{TS},v1={KNOWN_MAC}",
        f"t= {TS},v1={KNOWN_MAC}",
        f"t={TS}.5,v1={KNOWN_MAC}",
        f"t=\u0661\u0662\u0663,v1={KNOWN_MAC}",  # non-ASCII digits
        f"t={TS},t={TS},v1={KNOWN_MAC}",  # duplicate timestamp
        f"t={TS},v1",
        f"t={TS},v1=",
        f"t={TS},v1=zz",
        f"t={TS},v1={KNOWN_MAC[:-2]}",  # truncated
        f"t={TS},v1={KNOWN_MAC}00",  # extended
        f"t={TS},v1={KNOWN_MAC.upper()}",
        f"t={TS},v1=\u00e9\u4e2d",
        f"t={TS},v2={KNOWN_MAC}",  # only an unknown version
        f"t={'9' * 40},v1={KNOWN_MAC}",
        "t=" + "1" * 2000,
        f",,,t={TS},v1={KNOWN_MAC}",
    ],
)
def test_malformed_headers_fail_without_raising(header: str) -> None:
    assert verify_signature(SECRET, BODY, header, now=TS) is False


def test_extra_unknown_keys_are_ignored() -> None:
    mac = _header().split("v1=")[1]
    assert verify_signature(SECRET, BODY, f"t={TS},v0=old,v1={mac},x=y", now=TS)


def test_any_matching_v1_is_accepted() -> None:
    mac = _header().split("v1=")[1]
    assert verify_signature(SECRET, BODY, f"t={TS},v1={'0' * 64},v1={mac}", now=TS)
    assert verify_signature(SECRET, BODY, f"t={TS},v1={mac},v1={'0' * 64}", now=TS)


def test_tolerance_boundaries() -> None:
    h = _header()
    tol = SIGNATURE_TOLERANCE_SECONDS
    assert verify_signature(SECRET, BODY, h, now=TS + tol)
    assert not verify_signature(SECRET, BODY, h, now=TS + tol + 1)
    assert verify_signature(SECRET, BODY, h, now=TS - tol)
    assert not verify_signature(SECRET, BODY, h, now=TS - tol - 1)


def test_custom_tolerance() -> None:
    assert not verify_signature(SECRET, BODY, _header(), now=TS + 11, tolerance=10)


def test_comparison_uses_hmac_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(security.hmac, "compare_digest", spy)
    assert verify_signature(SECRET, BODY, _header(), now=TS)
    assert len(calls) == 1


def test_all_candidates_are_compared_even_after_a_match(monkeypatch: pytest.MonkeyPatch) -> None:
    n = 0
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        nonlocal n
        n += 1
        return real(a, b)

    monkeypatch.setattr(security.hmac, "compare_digest", spy)
    mac = _header().split("v1=")[1]
    assert verify_signature(SECRET, BODY, f"t={TS},v1={mac},v1={'0' * 64},v1={'1' * 64}", now=TS)
    assert n == 3


def test_generated_secrets_are_prefixed_and_unique() -> None:
    a, b = generate_secret(), generate_secret()
    assert a != b
    assert a.startswith("whsec_") and len(a) > 30
