"""Secret generation and HMAC-SHA256 request signing.

Header format: ``HookRelay-Signature: t=<unix seconds>,v1=<hex>``. The MAC covers
``"<t>." + <raw body bytes>`` so a captured request cannot be replayed outside the
tolerance window and the timestamp cannot be changed without invalidating it.
Receivers must verify against the raw body bytes, before any JSON parsing.
Headers other than the signature are not covered by the MAC.
"""

import hashlib
import hmac
import secrets

SECRET_PREFIX = "whsec_"
SIGNATURE_HEADER = "HookRelay-Signature"
SIGNATURE_TOLERANCE_SECONDS = 300
_MAX_HEADER_LENGTH = 1024
_MAX_TIMESTAMP_DIGITS = 12


def generate_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def _mac(secret: str, timestamp: int, body: bytes) -> str:
    signed = str(timestamp).encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def sign_payload(secret: str, body: bytes, timestamp: int) -> str:
    if not secret:
        raise ValueError("secret must not be empty")
    return f"t={timestamp},v1={_mac(secret, timestamp, body)}"


def _parse_header(header: str | None) -> tuple[int, list[str]] | None:
    if not header or len(header) > _MAX_HEADER_LENGTH:
        return None
    timestamps: list[str] = []
    signatures: list[str] = []
    for part in header.split(","):
        key, sep, value = part.partition("=")
        if not sep:
            return None
        if key == "t":
            timestamps.append(value)
        elif key == "v1":
            signatures.append(value)
        # Unknown keys are ignored so new signature versions can be added later.
    if len(timestamps) != 1 or not signatures:
        return None
    raw = timestamps[0]
    if not (raw.isascii() and raw.isdigit()) or len(raw) > _MAX_TIMESTAMP_DIGITS:
        return None
    return int(raw), signatures


def verify_signature(
    secret: str,
    body: bytes,
    header: str | None,
    now: float,
    tolerance: int = SIGNATURE_TOLERANCE_SECONDS,
) -> bool:
    """Return True only for a well-formed, fresh, matching signature. Never raises."""
    if not secret:
        return False
    parsed = _parse_header(header)
    if parsed is None:
        return False
    timestamp, candidates = parsed
    if abs(now - timestamp) > tolerance:
        return False
    expected = _mac(secret, timestamp, body).encode("ascii")
    matched = False
    for candidate in candidates:
        # Constant-time comparison; every candidate is checked (no early exit).
        if hmac.compare_digest(candidate.encode("utf-8", errors="replace"), expected):
            matched = True
    return matched
