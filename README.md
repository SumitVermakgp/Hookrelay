<div align="center">

# HookRelay

**A small, local-first webhook delivery service.**

Applications create events. HookRelay signs them, delivers them to registered HTTP
endpoints, retries transient failures, and records every attempt.

[![CI](https://github.com/SumitVermakgp/Hookrelay/actions/workflows/ci.yml/badge.svg)](https://github.com/SumitVermakgp/Hookrelay/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

---

> [!WARNING]
> **This is not production software.** There is no authentication, signing secrets are
> stored in plaintext in SQLite, and it is meant to run on `127.0.0.1` only. Read
> [SECURITY.md](SECURITY.md) before changing that.

HookRelay exists as the subject of a hands-on experiment with Claude Ship; see
[EXPERIMENT_LOG.md](EXPERIMENT_LOG.md) for the method and the running record.

## Contents

- [Quick start](#quick-start)
- [Demo: one successful and one failing delivery](#demo-one-successful-and-one-failing-delivery)
- [API](#api)
- [Behaviour](#behaviour)
- [Verifying a signature](#verifying-a-signature-receiver-side)
- [Configuration](#configuration)
- [Development](#development)

## Quick start

Requires Python 3.12 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints.txt -e ".[dev]"

uvicorn app.main:app --host 127.0.0.1 --port 8000
```

| Resource | Address |
| --- | --- |
| Health check | `curl http://127.0.0.1:8000/health` |
| OpenAPI document | <http://127.0.0.1:8000/openapi.json> — served locally, works offline |
| Interactive docs | <http://127.0.0.1:8000/docs> — Swagger UI loads its assets from a public CDN, so the browser needs internet access |

> [!CAUTION]
> Do not bind to `0.0.0.0`. Anyone who can reach the port can read and create everything.

## Demo: one successful and one failing delivery

By default HookRelay refuses private and loopback destinations. For a local demo, allow
the loopback host explicitly. This setting is for tests and demos only.

**Terminal 1** — the API, with short retry timings so the demo finishes quickly:

```bash
HOOKRELAY_TEST_ALLOWED_HOSTS=127.0.0.1 \
HOOKRELAY_RETRY_BASE_SECONDS=2 HOOKRELAY_RETRY_INTERVAL_SECONDS=1 \
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Terminal 2** — register an endpoint that always succeeds, and note its `secret`; it is
shown only in this response:

```bash
curl -s -X POST http://127.0.0.1:8000/endpoints \
  -H 'content-type: application/json' \
  -d '{"url":"http://127.0.0.1:9000/ok","description":"demo success"}'
```

**Terminal 3** — start the local receiver with that secret. It prints one line per request
and whether the signature verified:

```bash
python -m tools.local_receiver --port 9000 --secret whsec_...
```

Back in terminal 2, send an event, then register an endpoint that always returns 500 and
send a second event:

```bash
curl -s -X POST http://127.0.0.1:8000/events \
  -H 'content-type: application/json' -H 'Idempotency-Key: demo-1' \
  -d '{"type":"order.paid","payload":{"order_id":42}}'

curl -s -X POST http://127.0.0.1:8000/endpoints \
  -H 'content-type: application/json' \
  -d '{"url":"http://127.0.0.1:9000/fail"}'

curl -s -X POST http://127.0.0.1:8000/events \
  -H 'content-type: application/json' \
  -d '{"type":"order.refunded","payload":{"order_id":42}}'

curl -s http://127.0.0.1:8000/deliveries
```

Expected, after about ten seconds:

- The `/ok` deliveries are `succeeded` with one attempt each, and the receiver prints
  `signature=valid`.
- The `/fail` delivery is attempted three times (2 s, then 4 s apart with these settings),
  then ends as `failed`. `GET /deliveries/{id}` lists the three attempts.
- The receiver prints `signature=INVALID` for `/fail`. That is expected: the receiver
  checks one secret, and the `/fail` endpoint has its own, different secret.
- Re-sending the first event with the same `Idempotency-Key` returns `200` with an
  `Idempotent-Replayed: true` header and sends nothing.
- Re-registering either url returns `409`; each destination is registered once.
- `POST /deliveries/{id}/retry` on the exhausted delivery returns `409`
  (`maximum attempts reached`).

Receiver paths: `/ok` (200), `/fail` (500), `/status/<n>`, `/redirect` (302), `/slow`
(sleeps, to trigger a timeout). The receiver only binds to loopback.

## API

| Method and path | Purpose |
| --- | --- |
| `POST /endpoints` | Register a destination. Returns the signing secret once. `409` if the url is already registered or the endpoint limit is reached. |
| `GET /endpoints` | List endpoints, never including secrets. `limit`, `offset`. |
| `POST /events` | Create an event. Optional `Idempotency-Key` header. Creates one delivery per registered endpoint and sends in the background. |
| `GET /events/{event_id}` | Event with its payload and delivery summaries. |
| `GET /deliveries` | List deliveries. Filters: `event_id`, `endpoint_id`, `status`. |
| `GET /deliveries/{id}` | One delivery with its full attempt history. |
| `POST /deliveries/{id}/retry` | Make one more attempt now. `409` if already succeeded, at the maximum, or an attempt is in progress. |
| `GET /health` | Liveness and database check. |

Events are sent only to endpoints that exist when the event is created.

## Behaviour

**Endpoints.** A destination url is registered once; a repeat returns `409`. Urls are
compared exactly as given, so two paths on one host are two endpoints, and a trailing
slash makes a different one. Uniqueness is enforced by a database constraint, so
concurrent requests cannot both register the same url. At most
`HOOKRELAY_MAX_ENDPOINTS` endpoints exist at a time; registration past that returns `409`
and stores nothing.

**Idempotency.** The same `Idempotency-Key` with the same type and payload returns the
original event (`200`, `Idempotent-Replayed: true`) and sends nothing. The same key with a
different type or payload returns `409`. Duplicates are prevented by a database unique
constraint, so concurrent requests cannot both create an event. Keys are global and never
expire.

**Delivery.** Each delivery sends one `POST` whose body is the stored canonical JSON
(sorted keys, compact). Headers: `HookRelay-Signature`, `HookRelay-Event-Id`,
`HookRelay-Event-Type`, `HookRelay-Delivery-Id`, `HookRelay-Attempt`. 2xx is success.
Redirects are not followed. Response bodies are not read or stored.

**Retries.** `HOOKRELAY_MAX_ATTEMPTS` is the total number of attempts, first try included.
Timeouts, connection errors, `408`, `429` and `5xx` are retried after `base * 2^(n-1)`
seconds, capped. Other `4xx`, redirects, blocked destinations and internal errors are not
retried automatically. A manual retry can re-open a delivery that failed that way, but it
can never exceed the maximum.

**Destination policy.** Only `http`/`https`, no credentials in the url. Hosts must use
`https` unless explicitly allowlisted. Every address a host resolves to must be globally
routable; private, loopback, link-local, multicast and tunnelling ranges are refused. The
check runs again at send time and the connection goes to the validated address.

### Verifying a signature (receiver side)

`HookRelay-Signature` is `t=<unix seconds>,v1=<hex>`. The MAC is HMAC-SHA256 over
`"<t>." + raw body`, keyed with the endpoint secret. Verify against the raw bytes before
parsing JSON, compare in constant time, and reject old timestamps.

```python
import hashlib
import hmac
import time


def verify_hookrelay_signature(
    secret: str, raw_body: bytes, header: str, tolerance: int = 300
) -> bool:
    timestamps, signatures = [], []
    for part in header.split(","):
        key, _, value = part.partition("=")
        if key == "t":
            timestamps.append(value)
        elif key == "v1":
            signatures.append(value)
    if len(timestamps) != 1 or not signatures:
        return False
    if not (timestamps[0].isascii() and timestamps[0].isdigit()):
        return False
    timestamp = int(timestamps[0])
    if abs(time.time() - timestamp) > tolerance:
        return False
    signed = f"{timestamp}.".encode() + raw_body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(s.encode(), expected.encode()) for s in signatures)
```

## Configuration

Environment variables only. A `.env` file is not loaded automatically; see
[.env.example](.env.example) for the full list.

| Variable | Default | Meaning |
| --- | --- | --- |
| `HOOKRELAY_DB_PATH` | `hookrelay.db` | SQLite file |
| `HOOKRELAY_MAX_ATTEMPTS` | `3` | Total attempts per delivery (1 to 20) |
| `HOOKRELAY_MAX_ENDPOINTS` | `100` | Most endpoints registered at once (1 to 10000) |
| `HOOKRELAY_MAX_PAYLOAD_BYTES` | `65536` | Request body limit for every route |
| `HOOKRELAY_TEST_ALLOWED_HOSTS` | empty | Hosts exempt from the destination policy. Tests and demos only |
| `HOOKRELAY_DELIVERY_TIMEOUT` | `10` | Seconds per attempt (greater than 0, at most 120) |
| `HOOKRELAY_AUTO_DELIVER` | `true` | Send in the background when an event is created |
| `HOOKRELAY_RETRY_BASE_SECONDS` | `30` | First retry delay |
| `HOOKRELAY_RETRY_MAX_DELAY_SECONDS` | `3600` | Backoff cap |
| `HOOKRELAY_RETRY_INTERVAL_SECONDS` | `15` | How often due retries are swept (`0` disables) |

A database from an earlier version is upgraded in place at startup. Because the file holds
signing secrets in plaintext, it is created with owner-only permissions (`0600`).

> [!NOTE]
> Endpoint urls were not always unique. A database that already holds the same url twice
> cannot be upgraded: startup stops and reports how many urls are affected, so the extra
> rows can be removed first.

## Development

```bash
ruff check .
ruff format --check .
mypy
pytest -q
```

CI runs these same four commands on every pull request
([.github/workflows/ci.yml](.github/workflows/ci.yml)). `constraints.txt` pins the
dependency versions the suite was verified with.

### Layout

```
app/api/              routes and response models
app/core/             config, signing, url policy, retry policy, body limit
app/db/               schema.sql and SQLite access
app/services/         endpoint, event, delivery and retry logic
tools/                local test receiver (loopback only)
tests/                unit, api, integration, security, regression
docs/                 architecture notes
experiment-evidence/  evidence folders for the Claude Ship experiment
```

Some tests use real sockets on `127.0.0.1` with short timeouts of a few hundred
milliseconds. On a very slow machine they can fail for timing reasons.

## License

[MIT](LICENSE).

