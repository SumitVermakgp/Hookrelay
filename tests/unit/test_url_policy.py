import pytest

from app.core.url_policy import (
    MAX_URL_LENGTH,
    UrlPolicyError,
    default_resolver,
    is_blocked_ip,
    validate_destination_url,
)


def _public(_: str) -> list[str]:
    return ["93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/hook",
        "https://127.1.2.3/hook",
        "https://10.0.0.1/",
        "https://172.16.0.1/",
        "https://192.168.1.1/",
        "https://169.254.169.254/latest/meta-data",
        "https://100.64.0.1/",
        "https://0.0.0.0/",
        "https://224.0.0.1/",
        "https://[::1]/",
        "https://[::]/",
        "https://[fe80::1]/",
        "https://[fd00::1]/",
        "https://[::ffff:127.0.0.1]/",
        "https://[::ffff:10.0.0.1]/",
        "https://[64:ff9b::7f00:1]/",
        "https://[2002:7f00:1::1]/",
    ],
)
def test_blocks_non_public_ip_literals(url: str) -> None:
    with pytest.raises(UrlPolicyError, match="not allowed"):
        validate_destination_url(url)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("ftp://93.184.216.34/", "scheme"),
        ("file:///etc/passwd", "scheme"),
        ("gopher://93.184.216.34/", "scheme"),
        ("//93.184.216.34/", "scheme"),
        ("https://user:pw@93.184.216.34/", "credentials"),
        ("https://user@93.184.216.34/", "credentials"),
        ("https:///path", "no host"),
        ("https://93.184.216.34:99999/", "malformed"),
        ("https://93.184.216.34:abc/", "malformed"),
        ("https://93.184.216.34/a b", "invalid characters"),
        ("https://93.184.216.34/\n", "invalid characters"),
        ("https://evil.com\\@93.184.216.34/", "invalid characters"),
        ("http://93.184.216.34/", "https is required"),
        ("https://" + "a" * MAX_URL_LENGTH, "too long"),
    ],
)
def test_rejects_malformed_or_unsafe_urls(url: str, reason: str) -> None:
    with pytest.raises(UrlPolicyError, match=reason):
        validate_destination_url(url, resolver=_public)


def test_accepts_public_ip_literal() -> None:
    validate_destination_url("https://93.184.216.34/hook")


def test_accepts_host_resolving_to_public_address() -> None:
    validate_destination_url("https://hooks.example.com/x", resolver=_public)


def test_rejects_host_resolving_to_private_address() -> None:
    with pytest.raises(UrlPolicyError, match="not allowed"):
        validate_destination_url("https://evil.example.com/", resolver=lambda _: ["10.0.0.5"])


def test_rejects_host_with_any_private_address() -> None:
    with pytest.raises(UrlPolicyError, match="not allowed"):
        validate_destination_url(
            "https://mixed.example.com/", resolver=lambda _: ["93.184.216.34", "127.0.0.1"]
        )


def test_rejects_unresolvable_host() -> None:
    def fail(_: str) -> list[str]:
        raise UrlPolicyError("host could not be resolved")

    with pytest.raises(UrlPolicyError, match="resolved"):
        validate_destination_url("https://nope.example.com/", resolver=fail)


def test_rejects_empty_resolution() -> None:
    with pytest.raises(UrlPolicyError, match="resolved"):
        validate_destination_url("https://nope.example.com/", resolver=lambda _: [])


@pytest.mark.parametrize("url", ["https://2130706433/", "https://0x7f.1/", "https://017700000001/"])
def test_obfuscated_loopback_forms_are_rejected(url: str) -> None:
    # Uses the real resolver on purpose. Either resolution to loopback or
    # failure to resolve must end in rejection.
    with pytest.raises(UrlPolicyError):
        validate_destination_url(url, resolver=default_resolver)


def test_allowlisted_host_may_use_http_and_private_address() -> None:
    validate_destination_url("http://127.0.0.1:9000/hook", allowed_hosts={"127.0.0.1"})


def test_allowlist_is_case_insensitive_and_ignores_trailing_dot() -> None:
    validate_destination_url(
        "http://LocalHost./hook", allowed_hosts={"localhost"}, resolver=lambda _: ["127.0.0.1"]
    )


def test_allowlist_does_not_cover_other_hosts() -> None:
    with pytest.raises(UrlPolicyError):
        validate_destination_url("http://127.0.0.2/", allowed_hosts={"127.0.0.1"})
    with pytest.raises(UrlPolicyError, match="not allowed"):
        validate_destination_url("https://10.0.0.1/", allowed_hosts={"127.0.0.1"})


def test_is_blocked_ip_treats_garbage_as_blocked() -> None:
    assert is_blocked_ip("not-an-ip")
    assert not is_blocked_ip("93.184.216.34")


def test_returns_validated_addresses() -> None:
    assert validate_destination_url("https://hooks.example.com/", resolver=_public) == [
        "93.184.216.34"
    ]
    assert validate_destination_url("https://93.184.216.34/") == ["93.184.216.34"]
    assert validate_destination_url("http://127.0.0.1:9/", allowed_hosts={"127.0.0.1"}) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1.example.com/",
        "http://evil-127.0.0.1/",
        "http://127.0.0.12/",
        "http://a127.0.0.1/",
        "http://localhost.evil.com/",
    ],
)
def test_allowlist_requires_an_exact_host_match(url: str) -> None:
    with pytest.raises(UrlPolicyError):
        validate_destination_url(url, allowed_hosts={"127.0.0.1", "localhost"}, resolver=_public)


def test_internationalised_host_is_resolved_in_ascii_form() -> None:
    seen: list[str] = []

    def resolver(host: str) -> list[str]:
        seen.append(host)
        return ["93.184.216.34"]

    assert validate_destination_url("https://b\u00fccher.example/x", resolver=resolver)
    assert seen == ["xn--bcher-kva.example"]


@pytest.mark.parametrize(
    "url",
    ["https://a..b.example/", "https://" + "a" * 64 + ".example/"],
)
def test_hosts_that_cannot_be_encoded_are_rejected(url: str) -> None:
    with pytest.raises(UrlPolicyError):
        validate_destination_url(url, resolver=_public)


def test_resolver_keeps_the_operating_system_order(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def fake(host: str, *_: object, **__: object) -> list[tuple[object, ...]]:
        return [
            (socket.AF_INET6, 0, 0, "", ("2606:2800:220:1::1", 0, 0, 0)),
            (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
            (socket.AF_INET6, 0, 0, "", ("2606:2800:220:1::1", 0, 0, 0)),  # duplicate
            (socket.AF_INET6, 0, 0, "", ("fe80::1%eth0", 0, 0, 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert default_resolver("h.example.com") == ["2606:2800:220:1::1", "93.184.216.34", "fe80::1"]
    fake_v4_first = [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: fake_v4_first)
    assert default_resolver("h.example.com") == ["93.184.216.34"]
