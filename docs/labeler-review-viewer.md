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
