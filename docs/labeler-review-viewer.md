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
