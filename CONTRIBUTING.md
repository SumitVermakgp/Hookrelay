# Contributing to HookRelay

Thanks for taking a look. Please read this page before opening an issue or a pull request;
HookRelay has a narrower scope than its subject matter suggests.

## What HookRelay is

A small, local-first webhook delivery service, built as the subject of a hands-on
experiment in automated code review (see [EXPERIMENT_LOG.md](EXPERIMENT_LOG.md)).

It is **not production software**, and making it production-ready is not a goal. There is
no authentication, signing secrets are stored in plaintext in SQLite, and it is meant to
run on `127.0.0.1` only. Changes that assume a deployed, multi-tenant or
internet-reachable service are out of scope unless they are discussed first.

Welcome contributions: correctness fixes, clearer failure modes, tighter tests, better
documentation, and security hardening of what already exists.

## Reporting security problems

Do not open a public issue. Follow [SECURITY.md](SECURITY.md), and keep exploit details,
secrets and private URLs out of issues and pull requests.

## Development setup

Requires Python 3.12 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints.txt -e ".[dev]"
```

`constraints.txt` pins the dependency versions the suite has been verified with. Install
with `-c constraints.txt` so you reproduce them rather than resolving your own.

## Checks

All four must pass before a pull request is ready. CI runs exactly these
([.github/workflows/ci.yml](.github/workflows/ci.yml)):

```bash
ruff check .
ruff format --check .
mypy
pytest -q
```

- `mypy` runs in strict mode over `app/`. New code is fully annotated.
- `ruff` uses a 100-character line length, configured in `pyproject.toml`.
- Some tests use real sockets on `127.0.0.1` with short timeouts of a few hundred
  milliseconds. On a very slow machine they can fail for timing reasons; re-run before
  assuming a real failure.

## Writing changes

**Keep pull requests small and single-purpose.** One behavioural change per pull request,
with its tests, is far easier to review than a batch.

**Every behavioural change needs a test.** Put it where the suite already covers that
layer: `tests/unit/`, `tests/api/`, `tests/integration/`, `tests/security/` or
`tests/regression/`.

**Name tests after behaviour**, not after identifiers from an issue or a defect list. For
the same reason, do not name branches or commit messages after defect IDs.

**Prefer a database constraint to a read-then-write check** where a rule has to hold under
concurrency. The existing uniqueness rules on `events.idempotency_key` and `endpoints.url`
work this way, and new invariants should match.

**Match the surrounding style.** The code favours plain functions, explicit arguments over
globals, and a docstring that explains *why* where the reason is not obvious from the
code.

**Update the documentation in the same pull request** when behaviour changes: the API
table and behaviour notes in [README.md](README.md), the flow in
[docs/architecture.md](docs/architecture.md), the variable tables in README.md and
[.env.example](.env.example), and [SECURITY.md](SECURITY.md) where a control changes.

**Schema changes need a migration.** `app/db/schema.sql` uses `CREATE TABLE IF NOT EXISTS`,
so an existing database keeps its old shape. Add the upgrade to `_migrate` in
`app/db/database.py` with a test in `tests/unit/test_migration.py`, and make it idempotent.
Where existing rows cannot satisfy a new constraint, fail startup with a message naming the
problem rather than leaving the invariant unenforced.

## Commit messages

Write a short imperative subject line, then a body explaining *why* the change is needed
and anything a reviewer would otherwise have to reconstruct. Describe the change, not who
or what produced it.

```
Reject duplicate endpoint urls with 409

Registering the same destination twice created two endpoints with different
secrets, so every event was delivered to it twice and either secret verified.
```

## Never commit

- Real secrets, tokens or signing keys. `.env.example` holds placeholders only.
- A real `.env`, a `*.db` database file, or anything under `experiment-private/`.
- Local editor or agent configuration.

`.gitignore` covers these. `.claude/approvals.yml` is deliberately **not** ignored, because
the approval policy has to be committed to the default branch to take effect.

## Opening a pull request

1. Branch from `main`.
2. Run the four checks locally.
3. Fill in the pull request template, including how you verified the change.
4. Keep the branch up to date with `main`.
