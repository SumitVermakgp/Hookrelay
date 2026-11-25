<!--
Please read CONTRIBUTING.md first. Keep one behavioural change per pull request.
Do not include secrets, real signing keys or private URLs anywhere in this description.
-->

## What this changes

<!-- The behaviour before and after, in a sentence or two. -->

## Why

<!-- The problem being fixed. What goes wrong today, concretely? -->

## How it works

<!-- Only where a reviewer would otherwise have to reconstruct it: the approach, and any
     alternative you rejected and why. Delete if the diff speaks for itself. -->

## Verification

- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] `mypy`
- [ ] `pytest -q`
- [ ] New or changed behaviour is covered by a test

<!-- Name the tests you added, and anything you checked by hand. -->

## Impact

- [ ] Changes the HTTP API (status codes, request or response shape)
- [ ] Changes the database schema, and `_migrate` plus a migration test are updated
- [ ] Changes or adds a configuration variable, and README.md and `.env.example` are updated
- [ ] Touches signing, the destination policy, secret handling or request limits
- [ ] Documentation updated in this pull request

## Notes for the reviewer

<!-- Anything you are unsure about, or a decision worth a second opinion. -->
