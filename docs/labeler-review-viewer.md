# Local labeler review viewer

This interface records human decisions; it does **not** publish a dataset, sign
campaign approvals, or turn a reviewed sample into a completed campaign.

## Start with existing review packets

From this checkout (Python environment must include the project's dependencies):

```bash
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.review_portal
```

Open <http://127.0.0.1:8765/>. Finished `*-review-data.json` files under
`docs/reports/audits/v10_labels/` are discovered automatically. Use Reload batches
after generating or restoring a new packet. Raw, incomplete model journals are
never silently treated as review-ready packets.

For overlapping targets, the lexically last packet path wins, with selected and
superseded provenance retained. Use ordered batch directory names (for example
`batch-0001`, `batch-0002`); mtimes are deliberately ignored because restores change
them. Changed source/proposal content invalidates old confirmations.

The page contains full source text, source evidence, field-level decisions, and
the campaign tracker. The tracker reads the tracked campaign definition at
`src/mailroom_eda/labeling/campaigns/v10/campaign.yaml`, local gate results, and
`approved.yaml`. Completed tasks remain in history; review alone does not grant
campaign approval. Unknown inventory totals are shown as unknown, not zero.

## Default: public insurance claims

When no finished packet exists under `--labels-root` yet, the portal falls back to
the published dataset instead of refusing to start. It downloads
`ground_truth_hardened.jsonl` from `Lucius-Morningstar/mailroom-dataset` at the pinned
v9.3 revision, picks two insurance claims per claim type (auto, property, carrier,
inpatient, outpatient, pde), and writes them to
`review_bundle/production-insurance/production-review-data.json`. Every registry field
is a target; published values are candidates, and `claim_checklist` shows as
`pending_annotation` until the v10 campaign labels it. Once real labeling packets
exist, the fallback is skipped.

```bash
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.review_portal \
  --public-per-subclass 3            # more claims per type
  # --public-revision v9.2           # another published revision
  # --no-public-fallback             # skip the fallback entirely
```

A failed fallback (no network, a Hub outage, a refused export) never blocks the page: the
portal prints one line naming `--no-public-fallback` and starts anyway, showing "no finished
packets" until packets exist. The fallback is also skipped when it would be written inside
`--out`, because the portal hides everything under its own output directory — the same one
line tells you, with the flag to silence it.

The same packet can be built without the portal:

```bash
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.review_production \
  --out docs/reports/audits/v10_labels/review_bundle/production-insurance \
  --doc-class insurance_claim --per-subclass 2
```

Nothing is uploaded and the public dataset is never edited.

## Save, export, and checkpoint

- Save writes immutable receipts under `review_bundle/portal/returns/` and an
  incremental field ledger under the same portal folder. Reload/server restart
  restores these decisions. Browser storage is only a draft convenience.
- Export downloads machine-readable JSON for the agent. It is not an approval
  to overwrite the published ground truth.
- To enable the explicit private checkpoint button, set `HF_TOKEN` in your shell
  and start with `--hf-checkpoint`. Never paste tokens into the viewer or files.
- Only a successful byte-verified private upload changes human-confirmed fields
  to checkpointed. Failed uploads leave local decisions intact.

## Launch a labeling batch from the page (off by default)

Launching spends real money, so it is **off** unless you start the portal with `--enable-launch`.
Without the flag every `/api/launch/*` route answers 403 with this instruction and the page hides
the panel.

```bash
export OPENROUTER_API_KEY=...        # or paste a key in the panel; see "The API key" below
export HF_TOKEN=...                  # campaign arms only: paid answers checkpoint to the private staging repo
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.review_portal --enable-launch
```

The **Launch a labeling batch** panel offers two kinds of target:

* **Campaign arm**: every chunk arm of `campaign.yaml`, with its models, reasoning, cap and status
  (`ready`, `approved`, `blocked`). Run through `operate.cmd_chunk`, exactly as
  `labeling chunk <id> [--arm N]` does. Arms are labeled by what they bill for:
  *Labeling - documents that need labels* (pending, remainder, pilot, flagged and relabel sets) or
  *Qualification - re-labels gold documents to score models* (calibration and holdout sets: they
  re-label CUAD, MAUD and manual-intent gold on purpose, to score models, and the preflight warns).
* **Public trial**: 1 to 5 documents (default 2) of one type from the public dataset
  (`config.REPO_ID` at `config.REPO_REVISION`; the file's sha256 is recorded in `trial.json`), labeled
  with the task and models of that type's labeling campaign entry, capped at **$0.25**. Reasoning is
  per model: **DeepSeek v4.1 Flash always runs at `off`** in a trial (at `low` it spirals, 336 to over 2,300
  reasoning tokens on one email, and 96% of its output was reasoning in the claim_checklist pilot), every
  other model keeps the campaign's level. **Contracts** run `z-ai/glm-5.3-flashx-legal` at `low` beside
  `deepseek/deepseek-v4.1-flash` at `off` whatever chunk 1's winner is (the campaign's own cuad labeling arms
  say `winner_of_1`, which has no approval yet, so they stay blocked; the trial does not wait). Both models
  answer, side by side, as proposals for review. The target's *Notes* say this too. Trial document ids are
  namespaced `public-v9.3:<id>` (the tag of `config.REPO_REVISION`), with the dataset's own id kept as
  `published_document_id`, so a trial packet never collides with a stage packet of the same document (the
  portal refuses one document id with two source texts). Contracts use `cuad` / `pending` (the SEC EDGAR contracts without CUAD labels), mergers
  `merger_consideration` / `pending` (consideration MAUD does not answer), correspondence
  `correspondence` / `remainder`, insurance claims `claim_checklist` / `remainder`, corporate records
  `corporate` / `remainder`. Documents come from `runner.select`, then the lowest `document_id`s (or one per
  subclass in turn with `per_subclass`). A trial never bills CUAD or MAUD gold, and refuses if its selection
  contains any. It needs no campaign approval and writes no HF checkpoint. Output goes to
  `<labels root>/trials/<UTC stamp>_<task>-public/` and ends in a review packet the portal loads.

Pick a target, enter your initials and a cap, optionally a key, **Run preflight**, fix every red item,
then **Launch** (it asks you to type the cap again). The server re-runs the whole preflight itself, refuses
with `409` and the failing items if anything fails, if the typed cap differs, or if a run is already in
progress, and otherwise starts one background run. The panel polls the status every 2 s, shows the log tail,
and reloads the batches when the run succeeds. Every launch appends a receipt (target, initials, cap,
projection, outcome, spend, packet path; never the key) to `<portal out>/launches.jsonl`.

API: `GET /api/launch/options`, `POST /api/launch/preflight`, `POST /api/launch/start`,
`GET /api/launch/status` (writes need the same-origin `Origin` header and a JSON body, like every portal write).

### The API key

Use `OPENROUTER_API_KEY` from the portal's environment, or paste one in the password field. A pasted key lives
in server memory for that one preflight or start call and reaches the run through an environment mapping
(`Backend.from_env(provider, env)`); `os.environ` is never changed. It is wiped from the field after every call
(re-enter it before Launch), never stored in the browser, and redacted from every response, log line, receipt
and error. The code never judges a key by its format (a proxy may inject the real header), only OpenRouter does.

### Preflight items and their fixes

Every failure names what is wrong, quotes the underlying error and says what to run or set.

| id | Checks | Fix when it fails |
|:---|:---|:---|
| `launch_enabled` | portal started with `--enable-launch` | restart with the flag |
| `idle` | no run in progress | wait; watch Run status |
| `target`, `kind` | known target; qualification arms warn | pick a listed target |
| `initials` | 2-6 capital letters | enter your initials |
| `cap_value` | positive, finite number | enter a cap in USD |
| `wall_limit` | `max_wall_seconds` positive | fix `campaign.yaml` |
| `arm_runnable` | arm not `skip:` | pick another arm |
| `needs_approved` | prerequisite chunks approved | `labeling gate <id> --arm N --approve --initials XX` |
| `already_approved` (warn) | re-running an approved arm buys labels twice | launch the next chunk |
| `requires` | external release landed (`requires:`) | bump the Dojo pin, or tick the acknowledge box (becomes a warning) |
| `models` | `winner_of_<id>` resolved, models approved in `models.yaml` on OpenRouter, model-count rules | approve the winner's chunk; use the CLI for Modal or mixed arms |
| `reasoning` | each model's level and `max_tokens` (answer budget plus its measured reasoning allowance); informational | none (it explains the cap and the projection) |
| `stage` | `data/v9_4/stage` present and equal to `stage_sha256` | `scripts/build/build_v9_3_corporate_presence.py` then `scripts/build/build_v9_4_dedup_labels.py --no-cuad-journal` (or `labeling stage`) |
| `public_source` (trial) | public dataset downloaded | check network to huggingface.co |
| `documents` (trial) | 1 to 5 | choose a size in range |
| `selection` | non-empty | pick another arm or type |
| `gold_free` (trial) | no CUAD or MAUD gold in the selection | report the ids; nothing is launched |
| `stale_winner` | approved winner was scored on the prompt this run sends | re-run, gate and `--replace` the winner |
| `cap_projection` | cap >= 1.4x the projected cost (`runner.CAP_HEADROOM_FACTOR`) | raise the cap (trials: select fewer documents) |
| `cap_ceiling` | cap <= the arm's `cap_usd` or the $0.25 trial ceiling | lower the cap |
| `key_present` | a key in the environment or the field | set `OPENROUTER_API_KEY` or paste a key |
| `key_valid` | `GET /key` accepted | new key; or check network, `HTTPS_PROXY`, `OPENROUTER_BASE_URL` |
| `key_credit` | key credit minus `key_reserve_usd` covers the cap | lower the cap or add credit |
| `ping` | a passing strict-schema ping under 24 h old (`<labels root>/ping.jsonl`); warning only for trials | `labeling ping --initials XX` |
| `hf_checkpoint` | HF token can write the private staging repo (campaign arms; skipped for trials) | set `HF_TOKEN` |
| `output_dir` | labels root writable | fix permissions or `--labels-root` |

Not run from the page: Modal and mixed-provider arms, `--no-checkpoint`, sweeps and approvals. Qualification
and approval decisions stay on the command line.

## Fresh clone / private staged examples

Get the immutable private checkpoint SHA from the checkpoint receipt, then:

```bash
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.review_portal \
  --labels-root data/review-restored \
  --restore-revision FULL_40_CHARACTER_HF_COMMIT_SHA \
  --hf-checkpoint
```

This authenticates to `Lucius-Morningstar/mailroom-dataset-v10-staging`, verifies
downloaded bytes, then serves restored packets. Use a fresh directory: conflicting
existing files cause an explicit error, not overwrites. The displayed private
source records the restored revision. The tracker conservatively reports unknown
whole-campaign inventories if local source staging is absent.

The upload's verification receipt is written after the upload itself. A restore
of that upload recovers saved human decisions but may conservatively show them
as human-confirmed until a later backup also contains the verification receipt.
No review is lost; do not infer official staging from either state.

## Every labeling batch becomes a packet

`labeling review` (and therefore every `labeling run`/`chunk`, which ends with it) writes
`<chunk_dir>/<chunk_dir.name>-review-data.json` beside the batch's CSVs, so a reviewed batch
appears in the viewer with no extra step. To rebuild one by hand (no network):

```bash
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling export-review \
  docs/reports/audits/v10_labels/3.1_intent-calibration
# or: python -m mailroom_eda.labeling.review_export --chunk-dir DIR [--stage data/v9_4/stage]
```

The exporter (`review_export.py`) handles every task: one record per document and field for the
field tasks, one `cuad_clauses` record per contract (schema-pending, never gold-eligible) and one
`intent` record per correspondence. Records carry the run (`task`, `chunk`, `models`,
`reasoning`, `prompt_sha256`), the registry policy, the candidate and each model's own answer
(`model_outputs`), so reviewers can compare models. `reasoning` is one level, or `{model: level}` when
the two models ran at different levels. A call that was cut off at the token limit with 80% or more of
its output spent on reasoning is a **reasoning runaway**: it is billed, journaled as a failed call
(`reasoning_runaway: true`, retried by the next run), and the record says "Reasoning ran away on
`<model>`: N of M output tokens were reasoning ..." in `issues` and in that model's `model_outputs` entry.
A disagreement has no candidate and an issue
sentence says who answered what — and so does a configured model with no record for that
document ("The intent readings are incomplete: `<model>` has no record for this document.");
one model's two passes are never counted as an agreement. Evidence is `{start, end, quote}` into the full text, located
in the source; a quote that cannot be found is reported in `issues`, never highlighted.
A re-export overwrites the packet (atomically); the portal reopens only the fields whose
target changed. Within one labels root the lexically last packet wins per (document, field).

## Agent interface (machine)

The portal is built to be driven by review agents as well as by people. GET
`/api/contract` returns the machine contract in one document: the packet and
return schemas, the decision key, the routes an agent may call and the rules
it must honor (`decision_key`, `corrected_labels_json` shape, initials and
timestamp requirements, the per-label rule, the 409 state-revision guard).

```bash
curl -s http://127.0.0.1:8765/api/contract | python -m json.tool   # the one page an agent reads
curl -s http://127.0.0.1:8765/review-data.json                      # packet + saved decisions
curl -s -X POST http://127.0.0.1:8765/api/reviews -d '{"schema":"mailroom-human-review/v1", ...}'
```

Per-label decisions: a record whose `correction_type` is `array` (claim_checklist,
entity lists) may keep or drop any individual label by posting `CORRECT` with
`corrected_labels_json` set to exactly the JSON array of kept labels — edited items
in place. The review page's per-item checkboxes build the same array and send the
same one-decision-per-(document, field) contract; nothing else changes server-side.
Annotated item text travels inside the kept array; notes carry the reviewer's
justification in `reviewer_notes`.

The page's toolbar adds **Next pending** (keyboard `N`): jump straight to the next
target without a decision, which is the loop reviewers and agents repeat.

## Gold smoke fixtures

`tests/fixtures/live_smoke/<doc_type>/` holds one small, real labeling run for each of the five document
types (`contract`, `merger_agreement`, `corporate_record`, `insurance_claim`, `correspondence`), recorded once
from the public dataset and committed. Each directory has the documents exactly as downloaded
(`documents.jsonl`, full text), every model call the run made (`calls.jsonl`: prompt, input and answer hashes,
answers, tokens, cost), the review packet that run produced (`expected-review-data.json`), a `manifest.json`
(dataset repo, revision and tag, the public file's sha256, task, set, models, per-model reasoning and
`max_tokens`, prompt / input / response-schema hashes, who recorded it, when and for how much) and a short
README. They are the gold examples the labeling path is tested against: `tests/test_live_smoke_fixtures.py`
replays them offline on every run.

**Only documents that need labels are recorded.** The recorder builds each type's plan exactly as the trial
launcher does (same `runner.select` pool of `pending` / `remainder` documents, same models, per-model
reasoning and `max_tokens`) and refuses any CUAD or MAUD gold document, so nothing already expert-labeled is
billed: contracts are SEC EDGAR EX-10 without CUAD labels, mergers are those MAUD does not answer. The test
suite checks the stored documents against the same rule.

### What runs in CI

All offline, none spends anything:

```bash
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.smoke_fixtures check    # is each fixture still current?
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.smoke_fixtures replay   # replay the answers, diff the packet
```

* `check` recomputes with the current code the trial plan (task, set, models, reasoning, `max_tokens`), the
  prompt and exact-input sha256 of every (document, model, variant) built from the stored documents, the
  response-schema sha256, the dataset revision (`config.REPO_REVISION`) and each document's text hash, and
  compares them with the manifest. A mismatch exits 1 and says what changed and how to fix it: *"Gold fixture
  contract is stale: models was [...], now [...]. The recorded model answers no longer match what the code
  would send. Re-record with: ... record --types contract --initials XX --cap-usd 0.40 --replace (projected
  ~$0.18)"*.
* `replay` pushes the recorded answers through the real `runner.execute` (so `run_chunk`, the CUAD repair
  call, `review` and `review_export` all run) with a fake chat that answers from `calls.jsonl`, keyed by the
  model and the hash of the exact messages sent. A request the fixture has no answer for is a stale-fixture
  error, never a network call. The resulting packet is normalized (timestamps, temp paths and durations
  replaced; values, evidence offsets, statuses, issues and `model_outputs` kept) and diffed against
  `expected-review-data.json`; a difference prints one line per differing path.
* The test file also checks that each packet validates and opens in `ReviewPortal` (an ACCEPT on a
  `source_checks_passed` record saves), that no stored document is gold, and that no fixture file carries a
  credential or a local path (`Authorization`, `Bearer`, `sk-or-` in any metadata field of `calls.jsonl`,
  the manifest or the README; a key / header / bearer-token pattern anywhere). The fixture tests skip with
  *"no live smoke fixtures recorded yet; run smoke_fixtures record"* until the fixtures exist.
* `MAILROOM_LIVE_HUB=1` adds `verify-hub`: it re-downloads the public file at the manifest revision and
  confirms the file sha256 and each stored document's text sha256.

### When a fixture goes stale, and re-recording it

A fixture is stale when a prompt template, the field registry, a model, a reasoning level, a `max_tokens`
allowance, a response schema, the dataset revision pin or a document's text changes: the recorded answers
were made for requests the code no longer sends. Any such change fails `check` until the affected type is
re-recorded, in the same pull request. Re-recording is paid and is a human decision:

```bash
# 1. what would run and what it would cost; downloads the public file, sends nothing
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.smoke_fixtures record --initials XX --plan-only
# 2. record (OPENROUTER_API_KEY must be set; type the total cap again to confirm)
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.smoke_fixtures record --initials XX --cap-usd 1.00
# one stale type, replacing the old fixture
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.smoke_fixtures record --types contract --initials XX \
    --cap-usd 0.40 --replace
```

`record` runs the launcher's preflight for every type and aborts before the first request if any item fails,
printing each failing item with its fix. `--plan-only` prints, per type, the selected documents (namespaced
and published id, subclass, characters, text sha256), the models with their reasoning and `max_tokens`, the
projected calls and cost, the per-type cap and the gold check. The total cap (default $1.00) is split
`cuad` 0.40, `corporate` 0.20, `merger_consideration` 0.15, `claim_checklist` 0.15, `correspondence` 0.10
(scaled for another `--cap-usd`, or for a subset of `--types`, where the cap you give is the cap of those types
together; repeated `--types` values dedupe, order preserved — a type typed twice cannot spend its cap twice);
a type whose projection times the repo's 1.4x headroom exceeds its share is refused. The contract
share is above the portal's $0.25 trial ceiling; the recorder applies its own cap, not that ceiling. A real
run needs the total cap typed again on stdin, runs one worker per type (so which model is "A" and which "B" is
the same every time), writes `tests/fixtures/live_smoke/<doc_type>/` atomically, and immediately runs `check`
and `replay` on what it wrote. It refuses to overwrite a fixture without `--replace`, never writes outside the
fixture folder and a temp directory, and keeps the temp directory (printing its path) when a run is stopped
at the cap or a file would carry a credential. Cost is the sum of the calls in `calls.jsonl`
(`spent_usd` in each manifest); the projection that `--plan-only` prints is a ceiling-style estimate, not a quote.

## Export local source artifacts into packets

```bash
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.review_packet \
  --stage data/v9_4/stage \
  --chunk-dir docs/reports/audits/v10_labels/8a_claim_checklist-pilot \
  --out docs/reports/audits/v10_labels/review_bundle/insurance
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.review_corporate \
  --root . --out docs/reports/audits/v10_labels/review_bundle/corporate
```

Workbook export requires `openpyxl==3.1.5` (the `review` optional dependency).
An explicit model override (the `export_packet(model=...)` / `build_packet(model=...)`
argument) names a model the chunk actually ran, and in a mixed-level run the packet uses
that model's own recorded level, not the default model's; naming a model the chunk did not
run is refused before any packet is built. Corporate new-draw identities are
explicitly REVIEW-ONLY, with unassigned splits.
Family proposals outside the pinned schema remain schema-pending. Saved model
drafts are not represented as human-approved gold or verified best-model output.

Do not commit generated packets, receipts, sources or credentials to the public
repository. This PR is the viewer-first slice: paid labeling and official promotion
remain separate campaign work.

## Production → private v10 revision queue

Production review packets carry the public repository, immutable revision,
document ID, original split and text hash. Confirmed field decisions appear in
the viewer's revision queue; `GET /revision-queue.json` exports that queue for
the staging agent. The receipts and journal are the durable source of truth,
and are included in private checkpoints. A later REJECT removes a field from
the active queue without deleting its history.

The queue is **pending staging/deployment gates**, not a deployment command.
The public dataset is never edited by the viewer. After a production source
changes, regenerate its packet; old confirmations cannot apply to changed text
or proposals.
