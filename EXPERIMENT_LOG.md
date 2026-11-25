# Experiment log

Subject: HookRelay with Claude Ship (Code Review, PR Steward, Claude Approvals).
Rule: record raw evidence before writing conclusions. Record deviations here instead of
silently changing the plan. Separate vendor claims, direct observations and interpretation.

## Where the ground truth lives

The list of seeded defects and expected findings is **not stored in this repository**.
Keep it in a separate private file, written before each seeded-defect run, so the
reviewing agent cannot read it. `experiment-private/` is git-ignored as a safeguard.
Do not name tests, branches or commit messages after defect IDs.

## Baseline record (Run 0)

| Field | Value |
| --- | --- |
| Date | 2026-11-25 |
| Built by | Claude (chat session in the claude.ai app, model reported as Claude Sonnet 5.5), phase by phase with explicit approval. Not Claude Code. See deviations |
| Base commit SHA | fill in after the first commit |
| Python | 3.12.3 |
| Dependencies | pinned in `constraints.txt` |
| Local results | ruff check, ruff format --check and mypy clean. 396 tests passed in a clean virtual environment (after the Phase 8 review changes). Raw output in `experiment-evidence/00-baseline/` |
| CI | not run yet. The workflow is unverified until pushed |
| Claude Ship availability | vendor documentation checked on 2026-11-25 (see below). Access for your organisation and repository is still to be verified in the admin settings before PR 1 |

## Deviations from the brief

1. **Implementer.** The brief names Claude Code as implementer. The baseline was written in
   a Claude chat session with a code sandbox and delivered as zip files. Nothing was
   committed by an agent.
2. **Extra routes and behaviour** beyond the brief's route list: `GET /deliveries/{id}`
   (attempt history), a background sweeper loop for retries, automatic delivery on event
   creation, and a migration step in `init_db`.
3. **Secret shown once.** The signing secret is returned only in the `POST /endpoints`
   response. The brief only requires that list and detail responses omit it.
4. **Delivery body is the stored payload** (canonical JSON), not an envelope. Event id and
   type travel in unsigned headers.
5. **Retry policy choices.** Only transient failures are retried automatically. Manual
   retry is capped by the same maximum. `max_attempts` is limited to 1 to 20.
6. **Python 3.12 or newer** is required, because that is the only version the suite was run on.
7. **Test naming.** Tests describe behaviour and do not reference seeded defect IDs.
8. **Dependency pinning.** CI installs with `constraints.txt`, which the brief did not ask for.
9. **Baseline changed after the Phase 7 snapshot.** The Phase 8 review fixed three defects
   (below) and added the review document `docs/baseline-review.md`. The baseline is the state
   after these changes. Re-capture `experiment-evidence/00-baseline/` if you change anything else.
10. **Policy file not added.** No `.claude/approvals.yml` exists in the baseline. Adding one to
    the default branch turns on Claude Approvals and billing for the repository, so it is
    deferred to PR 6.

## Defects found in the baseline during implementation

Found by the implementer (not by Claude Ship) and fixed before the baseline was frozen.
Each has a regression test.

| Phase found | Defect | Fix |
| --- | --- | --- |
| 4 | httpx logs the full request URL at INFO, so a query-string token would reach logs | `httpx` and `httpcore` loggers set to WARNING |
| 6 | Only `POST /events` had a body size limit; `POST /endpoints` parsed bodies of any size | Body limit middleware on every route |
| 6 | IP-literal destinations were handed to httpx as the original string, so two URL parsers saw them | URL rebuilt from the validated parts |
| 8 | Internationalised hostnames passed registration but delivery raised an encoding error (recorded as `internal_error`, never sent) | Host converted to ASCII (punycode) for resolution, Host header and TLS server name |
| 8 | Resolved addresses were sorted as strings, which always put IPv6 first | Operating system order kept, duplicates removed |
| 8 | The SQLite file with plaintext secrets was created 0644 | Created 0600, existing files tightened |

## What this experiment cannot establish

- The implementer and the reviewer are both Claude models. They may share blind spots, so a
  clean review of this code is weaker evidence than a clean review by an unrelated reviewer.
- One small codebase, one author, one run per case. Detection rates will be small counts.
- Passing tests, a clean review or an approval does not show the code is correct.
- Vendor behaviour may change between runs. Record the product version and date each time.

## Claude Ship facts (vendor documentation, checked 2026-11-25)

These are the vendor's statements, not observations. Re-check before each run, because the
products are in beta and the documentation can change.

| Statement | Source |
| --- | --- |
| Claude Ship is Code Review (generally available) plus PR Steward and Claude Approvals (public beta), on Team and Enterprise plans, for GitHub repositories. An Owner role is needed to turn agents on, one at a time, in this order: Code Review, PR Steward, Approvals | https://code.claude.com/docs/en/claude-ship |
| Code Review reads the diff and surrounding code without building or running it. Findings are ranked by severity. The check run does not fail. A review with no findings does not mean the code is correct. Reviews run once on open, after every push, or only on request, and have Quick, Standard and Deep levels | https://code.claude.com/docs/en/code-review |
| PR Steward works on failing checks, merge conflicts and review feedback, and is designed not to approve or merge. It skips drafts, forks, pull requests opened by bots and authors without write access. It can be started manually with `@claude steward` and stopped with `@claude stop`. It has no spend limit until one is set | https://code.claude.com/docs/en/pr-steward |
| The Approvals policy file is `.claude/approvals.yml` on the default branch. The check blocks a merge only if it is a required check in GitHub. In observe mode it reports what it would decide. Observe mode is billed when a policy starts reviews or classifiers | https://code.claude.com/docs/en/approvals and https://code.claude.com/docs/en/approvals-setup |
| A change to the policy file in an open pull request is not applied to that pull request. Built-in protected files are policy files, `.claude/classifiers/`, `REVIEW.md`, `CODEOWNERS`, `.gitattributes`, symlinks and submodule pointers. Other sensitive paths such as `.github/` are not protected unless a rule covers them. The author can fill a seat unless excluded with `"!pr/authors"` | https://code.claude.com/docs/en/approvals-policy |
| Spend limits are set by an Owner under Organization settings, Usage | https://code.claude.com/docs/en/approvals-setup |

## Classifying findings

The brief counts true positives, false positives and misses. Add one more category, because
the baseline has known weaknesses (docs/baseline-review.md):

| Category | Meaning |
| --- | --- |
| True positive | Actionable, correct finding on the seeded defect |
| Valid, unseeded | Correct finding about the baseline or the clean part of the change, for example a risk listed in docs/baseline-review.md. Not a detection and not a false positive |
| False positive | Incorrect or unsupported finding |
| Duplicate | Repeats an earlier finding in the same run |
| Not applicable | Style, test coverage or praise that the scoring does not cover |
| Missed | Seeded defect with no actionable finding. A general comment does not count as detection |

Mark the category for every finding at the time you read it, before looking at the next run.

## Planned experiment pull requests (areas and clean changes only)

Each pull request is one small, realistic change. The seeded-defect material is kept in the
private ground-truth bundle, not here. Include at least two clean controls with no seeded
defect, so false positives can be measured on correct code.

| PR | Area | Clean change to implement | Capability exercised |
| --- | --- | --- | --- |
| 1 | Endpoint registration and validation | Reject duplicate endpoint URLs with `409`; cap the number of endpoints | Code Review |
| 2 | HMAC signing | Add a `verify_request` helper that takes a header mapping and the raw body; update the README example | Code Review |
| 3 | Delivery retry | Honour `Retry-After` on `429` and `503`, capped by the maximum delay | Code Review, PR Steward |
| 4 | Idempotency | Add API-level concurrent idempotency tests; no behaviour change | Code Review (control) |
| 5 (optional) | Tenant scoping | Per-tenant API keys; scope every query | Code Review |
| 6 | Approval policy | Add `.claude/approvals.yml` in observe mode, then a set of test pull requests across documentation, logic, security-sensitive and policy paths | Claude Approvals |
| C1, C2 | Controls | A documentation-only change and a clean logic change | Code Review, Approvals |

## Run log template

Copy one block per run.

| Field | Record |
| --- | --- |
| Run ID / date | |
| Claude Ship capability | Code Review / PR Steward / Claude Approvals |
| Model / configuration | exact visible model and settings, if shown |
| Repository commit | base SHA and PR head SHA |
| Seeded defect IDs | refer to the private ground-truth file, or "none" |
| Expected outcome | written before the run |
| Observed output | exact finding, action or decision |
| Correctness assessment | TP / FP / missed / partial / not applicable |
| Human intervention | what was changed manually and why |
| CI outcome | before and after |
| Latency and usage | measured values or "unavailable" |
| Evidence links | PR, CI run, screenshots, logs with secrets redacted |

## Metrics to compute at the end

Detection rate, false positives, fix success, regression count, policy agreement, human
interventions, latency, and cost where the product exposes it. Do not estimate missing
usage data. Count only actionable, correct findings as detections.

## Runs

None yet.
