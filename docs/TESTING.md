# Testing and pre-PR quality gates

The repository is tested at four levels. Nothing here spends money, calls
OpenRouter, or writes to the Hub; the paid labels path has its own tests and is
never exercised by CI.

| Level | Command | What it proves |
|:--|:--|:--|
| Lint and syntax | `make lint` | no undefined names or invalid syntax anywhere (`ruff --select E9,F63,F7,F82`); every module byte-compiles |
| Focused review tests | `make test-review` | the review page, portal, export, corporate exporter, checkpoint and recovery contracts |
| Full offline suite | `make test` | every unit/integration test in `tests/` (live-Hub tests skip unless `MAILROOM_LIVE_HUB=1`) |
| Browser suites | `make check-all` | the shipped page in real headless Chrome, including the real portal server, receipts, journal, tracker, revision queue, launcher preflight and axe-core |

## Before you open a PR

```bash
make install            # .venv + test/lint/browser dependencies (once)
make axe                # axe-core under .cache/axe (once; needs npm)
make check-all          # lint + harness + full pytest + both browser suites
```

`make check` is the fast gate (lint, harness build, full pytest). Run
`make check-all` when the review interface, launcher, packet exporter or
campaign tracker changed. A PR that changes a behavior must carry its tests in
the same commit.

Enable the local hooks so a staged change is checked before it becomes a commit:

```bash
.venv/bin/pre-commit install
```

The hooks fix lint on the staged files, run the focused review tests, and build
the browser harness when a review file changed. They are a convenience, not a
replacement for `make check-all`.

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
CI uploads it when the job fails.

```bash
make browser            # both suites
make browser-real       # the real portal suite only
```

Chrome defaults to `google-chrome`/`chromium` when found, else the macOS app
path; override with `CHROME=/path/to/chrome`. In containers set
`CHROME_NO_SANDBOX=1`. Without `AXE_PATH` the axe step is skipped, but the
other 32 steps still run; CI installs axe-core and runs it.

## CI jobs

`.github/workflows/ci.yml` runs on every PR and on `main`:

| Job | Content |
|:--|:--|
| `lint` | whole-tree undefined-name/invalid-syntax gate + `compileall` |
| `test` (3.12 and 3.13) | installs pinned dependencies, pins the sibling `llm-mailroom` at `9e8f30b2…`, full `pytest`, `pip check` |
| `browser` | installs axe-core, runs the synthetic page smoke and the real portal E2E, uploads evidence on failure |

`tests/test_ci_tooling.py` fails if the workflow regresses to a stub or stops
running either browser suite, so the gate cannot be quietly removed.

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
Every journal event records `reviewer_kind` as `human` or `model` (receipts keep the identity string);
a model identity is attribution, not a human countersign, and downstream
promotion gates must check that kind. The browser E2E uses `Haiku-5.5` so this
path is exercised on every run.

## Troubleshooting

* **No Chrome** — the Python suite and `make check` still pass; CI and
  `make check-all` need Chrome. Set `CHROME` or install one.
* **axe skipped** — run `make axe` (or set `AXE_PATH` to a vendored
  `axe.min.js`); the rest of the browser suite is independent.
* **Sibling taxonomy tests skip** — clone `Exios66/llm-mailroom` at
  `9e8f30b279e7626c056b03511f823105760f80d7`, the exact CI pin.
