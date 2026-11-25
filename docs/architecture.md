# Architecture notes

## Flow

1. `POST /endpoints` validates the URL against the destination policy and stores the
   endpoint with a server-generated secret.
2. `POST /events` stores the event (canonical JSON) and, in one transaction, one `pending`
   delivery per existing endpoint. A unique constraint on `events.idempotency_key`
   decides duplicates.
3. After the response, a background task attempts each new delivery once.
4. A sweeper loop in the API process (`HOOKRELAY_RETRY_INTERVAL_SECONDS`) attempts
   deliveries whose `next_attempt_at` has passed.
5. `POST /deliveries/{id}/retry` runs one attempt immediately.

## Tables

| Table | Notes |
| --- | --- |
| `endpoints` | `url`, `secret` (plaintext), `description` |
| `events` | `type`, canonical JSON `payload`, nullable unique `idempotency_key` |
| `deliveries` | one per (event, endpoint). `status` is `pending`, `succeeded` or `failed`. `next_attempt_at` is set while a retry is scheduled |
| `delivery_attempts` | one row per attempt, unique on (`delivery_id`, `attempt_number`). `error` holds a fixed category, never exception text |

## Starting an attempt

`claim_attempt` is a single `INSERT ... SELECT` that only inserts when the delivery is
pending (or failed, for a manual retry), fewer than `max_attempts` attempts exist, no
attempt is unfinished, and (for scheduled retries) `next_attempt_at` has passed. SQLite
serialises writers, so two callers cannot both start an attempt.

## Delivery state after an attempt

| Outcome | Delivery status |
| --- | --- |
| 2xx | `succeeded` |
| Retryable failure and attempts remain | `pending`, `next_attempt_at` set |
| Anything else | `failed` |

## Known gaps

See [SECURITY.md](../SECURITY.md). Notably: an attempt left unfinished by a crash blocks its
delivery, and the sweeper runs inside the API process.
