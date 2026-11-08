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
  # --no-public-fallback             # old behavior: fail with no packets
```

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
  with the task, models and reasoning of that type's labeling campaign entry, capped at **$0.25**.
  Contracts use `cuad` / `pending` (the SEC EDGAR contracts without CUAD labels), mergers
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
(`model_outputs`), so reviewers can compare models. A disagreement has no candidate and an issue
sentence says who answered what. Evidence is `{start, end, quote}` into the full text, located
in the source; a quote that cannot be found is reported in `issues`, never highlighted.
A re-export overwrites the packet (atomically); the portal reopens only the fields whose
target changed. Within one labels root the lexically last packet wins per (document, field).

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
Corporate new-draw identities are explicitly REVIEW-ONLY, with unassigned splits.
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
