# Security

## Status

HookRelay is a local experiment. It is not hardened for production or for exposure to a
network. It has no authentication and no tenant isolation. Run it on `127.0.0.1` with
throwaway data only.

## Reporting a problem

This is a public repository. Report security problems privately to the repository owner,
through GitHub's **Report a vulnerability** button on the Security tab, or by a direct
message if you already have a channel. Do not open a public issue, and do not put exploit
details, secrets or private URLs in issues or pull requests.

Because HookRelay is an experiment that is explicitly not hardened (see Status above), the
known weaknesses listed on this page are not vulnerabilities. A report is most useful when
it shows a control that is documented as working but does not.

## What is implemented

| Control | Where | Checked by |
| --- | --- | --- |
| HMAC-SHA256 signing over `"<t>." + body`, constant-time verification, timestamp tolerance | `app/core/security.py` | `tests/unit/test_signing.py`, `tests/security/test_signing_end_to_end.py` |
| Destination policy: scheme, credentials, https required, every resolved address must be globally routable | `app/core/url_policy.py` | `tests/unit/test_url_policy.py`, `tests/security/test_ssrf.py` |
| Policy re-checked at send time; connection pinned to the validated address; URL rebuilt from validated parts | `app/services/delivery.py` | `tests/unit/test_delivery.py`, `tests/security/test_ssrf.py` |
| Redirects never followed; proxy environment variables ignored | `app/services/delivery.py` | `tests/unit/test_delivery.py`, `tests/integration/` |
| Secret returned once at creation (`Cache-Control: no-store`), never in list, detail or error responses | `app/api/schemas.py` | `tests/api/test_endpoints.py`, `tests/security/test_secret_leakage.py` |
| No secrets, URLs or payloads in logs; httpx URL logging silenced | `app/services/delivery.py` | `tests/security/test_secret_leakage.py`, `tests/regression/` |
| Only fixed error categories stored; response bodies never read or stored | `app/services/delivery.py` | `tests/unit/test_delivery.py` |
| Request body limit on every route (header check and streaming count) | `app/core/body_limit.py` | `tests/security/test_request_hardening.py` |
| Bounded attempts and no concurrent duplicate attempts (single-statement claim) | `app/services/delivery.py` | `tests/unit/test_retries.py` |
| Idempotency by database unique constraint | `app/services/events.py` | `tests/unit/test_events_service.py` |
| All SQL parameterised | `app/services/` | `tests/security/test_request_hardening.py` |
| Database file created owner-only (0600) because it holds plaintext secrets | `app/db/database.py` | `tests/unit/test_db.py`, `tests/regression/` |

## Known limitations

- **No authentication or authorisation.** Any client that can reach the port can create
  endpoints and read all events, deliveries and attempts. Tenant scoping is not built.
- **Secrets are stored in plaintext** in SQLite. Signing needs the raw secret. There is no
  encryption at rest and no secret rotation.
- **Send-time re-validation narrows DNS rebinding but is not a proof.** The connection goes
  to one validated address, but only the first address is tried.
- **TLS to a pinned address is only tested through a mock transport.** No test completes a
  real handshake against a pinned IP.
- **The test allowlist is host-name based and ignores the port.** It must stay empty
  outside tests and demos.
- **No destination port restriction.** Public hosts on any port are accepted, so HTTP
  requests can reach non-HTTP services.
- **DNS resolution has no timeout** and blocks a worker thread.
- **Two special-purpose addresses are allowed** because Python treats them as globally
  reachable: `192.0.0.9` and `192.88.99.1`.
- **Swagger UI loads assets from a public CDN.**
- **Unsigned headers.** The signature covers the timestamp and body only. Event id, type,
  delivery id and attempt number are not authenticated.
- **A crash during an attempt leaves it unfinished** and blocks that delivery. There is no
  lease or recovery.
- **Background delivery and the retry sweeper run inside the API process.** Tasks are lost
  if the process dies. Concurrency was tested with threads on one SQLite file, not with
  several server processes.
- **No rate limiting, no cap on endpoints or events, no idempotency key expiry.**
- **Dependencies are pinned by `constraints.txt` but not scanned** for known
  vulnerabilities.

## What the tests do not prove

Passing tests show the listed behaviours on one machine and one set of package versions.
They do not show the absence of other defects. Mutation checks were run by hand during
development; they are not part of CI.

## Verifying signatures

See the receiver example in [README.md](README.md#verifying-a-signature-receiver-side).
