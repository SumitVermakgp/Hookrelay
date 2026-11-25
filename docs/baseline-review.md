# Baseline review (Phase 8)

Date: 2026-11-25. Scope: every file under `app/`, `tools/`, `tests/`, the CI workflow and the
docs, plus a set of targeted probes. The reviewer was the same assistant that wrote the code,
so this is a self-review. It is not independent and will have blind spots.

## Result

No known blocking issue remains. This review found three defects, all fixed with regression
tests: internationalised hostnames passed registration but could never be delivered,
resolved addresses were sorted as strings (which always preferred IPv6), and the SQLite file,
which holds plaintext signing secrets, was created world-readable (0644). Earlier phases found
three more (request-URL logging, an unbounded body on `POST /endpoints`, and a second URL
parser for IP-literal destinations). All six are listed in EXPERIMENT_LOG.md.

## What was checked

| Area | How |
| --- | --- |
| Lint, format, strict types, full suite | Clean virtual environment built with the CI install command |
| Route coverage | Every route in the OpenAPI document has tests. Branch coverage of `app/` is about 98% |
| Secret handling | End-to-end test over every route, error body, log line, database row and receiver request |
| Destination policy | Real-resolver tests for loopback spellings, rebinding and redirect tests at send time |
| Concurrency | Thread tests on one SQLite file for event creation and attempt claiming |
| Runtime behaviour | The README demo, run against real uvicorn and the local receiver |

## Remaining risks

"Probed" means a script confirmed the behaviour. "Reasoned" means it follows from reading the
code and was not tested.

| ID | Risk | Severity | Basis | Suggested handling |
| --- | --- | --- | --- | --- |
| R1 | Swagger UI at `/docs` loads its JavaScript and CSS from a public CDN. It is blank without internet, and the browser contacts a third party. `/openapi.json` is local. | Low | Probed | Document, or vendor the assets |
| R2 | No restriction on destination ports. Public hosts on ports such as 22, 25 or 6379 are accepted, so HookRelay can send HTTP requests to non-HTTP services. | Medium | Probed | Restrict to 80 and 443 with a configurable list |
| R3 | Only the first validated address is tried. A host with one unreachable address fails on every attempt. | Low | Code reading | Try the remaining addresses |
| R4 | DNS resolution has no timeout and blocks a worker thread, at registration and at send time. Slow resolvers can exhaust the thread pool. | Medium | Reasoned | Resolve with a timeout |
| R5 | Two special-purpose addresses that Python treats as globally reachable are allowed: `192.0.0.9` and `192.88.99.1`. | Low | Probed | Add them to the extra block list |
| R6 | SQLite runs in default journal mode. Write contention across processes can surface as "database is locked" (5 second wait). | Low | Reasoned | WAL mode if run with several workers |
| R7 | An attempt left unfinished by a crash blocks its delivery. No lease or recovery. | Medium | Documented | Add a stale-attempt timeout |
| R8 | The retry sweeper and background delivery run inside the API process. Tasks are lost if it dies. | Low | Documented | Separate worker |
| R9 | No authentication, no tenant isolation. | High if exposed | Design | Bind to loopback only |
| R10 | Signing secrets are stored in plaintext. | Medium | Design | Encrypt at rest, support rotation |
| R11 | No rate limits. No cap on the number of endpoints. `GET /events/{id}` returns all deliveries. | Low | Code reading | Add caps and paging |
| R12 | Idempotency keys are global and never expire. | Low | Design | Scope per tenant, add a TTL |
| R13 | Hostnames use Python's IDNA 2003 codec. Some characters map differently under IDNA 2008. | Low | Reasoned | Use an IDNA 2008 library if needed |
| R14 | The signature does not cover event id, type or attempt number. | Low | Design | Include them in the signed content |
| R15 | TLS to a pinned address is only tested through a mock transport. | Low | Documented | Add a test with a local TLS server |
| R16 | A few tests use real sockets and sub-second timeouts and could flake on a slow runner. None seen in repeated local runs. | Low | Reasoned | Raise timeouts if CI is flaky |

## How to use this list in the experiment

A reviewer may report some of these. A correct finding that matches an entry here is a valid
unseeded finding. Count it separately from true positives for seeded defects and from false
positives (see EXPERIMENT_LOG.md).
