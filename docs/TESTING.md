# Testing and pre-PR quality gates

The repository is tested with local gates. **GitHub Actions are intentionally
not used**; `.github/workflows/` is empty, and the commands below are the CI
substitute. Nothing here spends money, calls OpenRouter, or writes to the Hub;
the paid labels path has its own tests and is never exercised by these gates.

| Level | Command | What it proves |
|:--|:--|:--|
| Lint and syntax | `make lint` | no undefined names or invalid syntax anywhere (`ruff --select E9,F63,F7,F82`); every module byte-compiles |
| Focused review tests | `make test-review` | the review page, portal, export, corporate exporter, checkpoint and recovery contracts |
| Full offline suite | `make test` | every unit/integration test in `tests/` (live-Hub tests skip unless `MAILROOM_LIVE_HUB=1`) |
| Harness build | `make harness` | the real browser harness packets, registry and campaign still load (no browser needed) |
| Browser suites | `make check-all` | the shipped page in real headless Chrome, including the real portal server, receipts, journal, tracker, revision queue, launcher preflight and axe-core |
| Coverage | `make coverage` | real line + branch coverage per `mailroom_eda` module from the unit tests |

## Before you open a PR

```bash
make install            # .venv, dependencies, and both git hook types (once)
make axe                # axe-core under .cache/axe (once; needs npm)
make check-all          # lint + harness + full pytest + both browser suites
make coverage           # per-module coverage table (JSON in .cache/quality/)
```

`make check` is the fast gate (lint, harness build, full pytest). Run
`make check-all` when the review interface, launcher, packet exporter or
campaign tracker changed. A PR that changes a behavior must carry its tests in
the same commit.

`make install` installs both hook types with pre-commit:

* **pre-commit** fixes lint on the staged files, runs the focused review tests,
  and builds the browser harness when a review file changed.
* **pre-push** runs `make check` (lint + harness + full pytest) before anything
  leaves the machine.

They are convenience gates, not a replacement for `make check-all`. To install
them manually:

```bash
.venv/bin/pre-commit install
.venv/bin/pre-commit install --hook-type pre-push
```

## Coverage: the real numbers, locally

`coverage run -m pytest` is not usable on this Python 3.13 + numpy build: a lazy
C extension imported under the tracer raises *"cannot load module more than once
per process"*. `scripts/ci/coverage_report.py` works around it by pre-importing
the dependency tree before starting coverage, then runs pytest through
`coverage.Coverage` and writes a JSON report plus a per-module table.

```bash
make coverage                     # full suite; writes .cache/quality/coverage.json
make coverage-review              # focused review-interface tests only
make coverage-check               # full suite + fail under COVERAGE_MIN (default 70%)
```

The report covers `mailroom_eda.labeling` with branch coverage enabled. Baseline
at the time this gate was added (full suite, 1062 tests):

| module | coverage |
|:--|--:|
| `review_page.py` | 74.0% |
| `review_portal.py` | 75.4% |
| `launch.py` | 75.3% |
| `checkpoint.py` | 77.8% |
| `review_export.py` | 81.1% |
| `review_packet.py` | 82.7% |
| `local_backup.py` | 66.9% |
| `spend.py` | 68.7% |
| **all `mailroom_eda.labeling`** | **74.98%** (5,394/7,367 statements, 2,750 branches) |

Coverage numbers move; rerun the command rather than trusting this table.
`tests/test_coverage_report.py` runs the tool against a focused test and checks
the JSON contains real covered statements, so a broken tracer cannot silently
turn into a fake percentage.

## The two browser suites

### Synthetic integrated page smoke — `tests/browser_review_page.mjs`

One synthetic packet, a mock HTTP server, no corpus and no network. It pins the
page contract itself: safe source rendering, evidence offsets, model answer
tables, per-label keep/drop/edit, autosave, 409 draft retention, checkpoint,
launch panel, tabs, keyboard and localStorage resume.

### Real portal end to end — `tests/browser_review_portal.mjs`

This drives the **shipped** HTTP handler and page, not a hand-written mock:

* `tests/portal_browser_harness.py` builds a labels root from the committed
  live-smoke fixtures plus production/schema-pending/unsafe-link/picker cases,
  starts the real `ReviewPortal` on loopback, and serves a test-only control
  plane (state snapshots, add packet, second-writer save, forced Hub failure).
* Only the paid edges are seamed: an in-memory private HF checkpoint repo and
  the launcher's OpenRouter chat / key-status / public-dataset calls.
* The suite walks 33 named steps: API contract and security guards; source and
  evidence rendering; tabs/search/batch navigation; per-field outcomes; model
  answers, pass-level attribution and Use value; per-label editing and guards;
  saves, immutable receipts, journal and revision queue; blocked ACCEPT and
  empty candidates; CORRECT/REJECT/NEEDS_SOURCE; schema-pending confirmation;
  resume after reload; sticky identity and export; autosave and unfinished
  drafts; 409 conflict and explicit Discard; checkpoint success/failure and the
  local mirror; campaign tracker; launcher preflight, wrong-cap 409 and a real
  public-trial run; storage failure; responsive layout; axe-core WCAG A/AA.

Evidence lands in `.cache/browser-evidence/real/` (ignored by git):
`results.json`, one PNG per checkpoint, `axe.json` and the exported decisions.
It stays on the machine; attach the paths or paste the result when reporting.

```bash
make browser            # both suites
make browser-real       # the real portal suite only
```

Chrome defaults to `google-chrome`/`chromium` when found, else the macOS app
path; override with `CHROME=/path/to/chrome`. In containers set
`CHROME_NO_SANDBOX=1`. Without `AXE_PATH` the axe step is skipped, but the
other 32 steps still run; `make axe` installs it locally.

## Why there is no CI job

The host account cannot run GitHub Actions (jobs are refused with a billing
lock), and the repository standard is now local gates. The old workflow stub was
removed, `tests/test_ci_tooling.py` asserts that no workflow files return, and
the same commands are available to every maintainer:

```bash
make check          # pre-commit / pre-push plus the fast pre-PR gate
make check-all      # the full local equivalent of a CI run
make coverage       # the coverage report a CI service would have produced
```

A local run can still be recorded as evidence: `make check-all` leaves
`results.json`, screenshots and `axe.json` under `.cache/browser-evidence/`;
`make coverage` leaves the JSON under `.cache/quality/`. Both are git-ignored.

## Fixtures and live-Hub tests

The five `tests/fixtures/live_smoke/<doc_type>/` recordings are offline-gold
examples: `check` validates the current plan/prompts/models against the
manifest, `replay` pushes the recorded answers through the real runner and
diffs the packet. Re-record only when the fixture is stale, and in the same PR
that changed the prompt/registry/model. See `docs/labeler-review-viewer.md`.

```bash
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.smoke_fixtures check
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.smoke_fixtures replay
MAILROOM_LIVE_HUB=1 PYTHONPATH=src .venv/bin/python -m pytest tests/test_release_verify.py
```

`MAILROOM_LIVE_HUB=1` tests re-download public Hub files; they are opt-in so a
network outage never masquerades as a code failure.

## Automated reviewer identity

A model integration identifies itself with its **short display identity**, for
example `Haiku-5.5` or `DeepSeek-V4.1-Flash` — never the provider route
(`Anthropic/Haiku-5.5`). Human reviewers keep 2–6 capital initials (`JB`).
Every journal event records `reviewer_kind` as `human` or `model` (receipts keep
the identity string); a model identity is attribution, not a human countersign,
and downstream promotion gates must check that kind. The browser E2E uses
`Haiku-5.5` so this path is exercised on every run.

## Troubleshooting

* **No Chrome** — the Python suite, `make check` and `make coverage` still pass;
  `make check-all` needs Chrome. Set `CHROME` or install one.
* **axe skipped** — run `make axe` (or set `AXE_PATH` to a vendored
  `axe.min.js`); the rest of the browser suite is independent.
* **Sibling taxonomy tests skip** — clone `Exios66/llm-mailroom` at
  `9e8f30b279e7626c056b03511f823105760f80d7`, the pin the contract tests expect.
* **Coverage import error** — use `make coverage`, not `coverage run -m pytest`;
  the script's pre-import is the fix for the Python 3.13 + numpy tracer bug.
