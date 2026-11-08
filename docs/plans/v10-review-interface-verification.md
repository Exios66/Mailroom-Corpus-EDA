# v10 review interface: work-in-progress checkpoint

Canonical branch: `claude/v10-claim-checklist-labeling`; review PR: #56.

This checkpoint is **not merge-ready**. No human approvals or official v10
publication are implied by the interface or its source checks.

## Durable locations

- Campaign definition: `src/mailroom_eda/labeling/campaigns/v10/campaign.yaml`.
- Interface code: `src/mailroom_eda/labeling/review_{packet,page,portal}.py`
  and `review_page.html` in the same directory.
- Private generated packets: `docs/reports/audits/v10_labels/review_bundle/`.
- Private backup: `Lucius-Morningstar/mailroom-dataset-v10-staging` on HF.
- Prior verified checkpoint (before this interface implementation):
  `8ef87ed153c6cfbcfd5ecc935b830a439886e5f0`.

The prior checkpoint does NOT establish that new interface receipts or assets
are backed up. Browser local storage is not a fresh-clone recovery mechanism.
Do not commit source documents, returned decisions, or tokens to the public repo.

## Extension requested (not started)

Hand labeling from the current pinned dataset snapshot (v9.3, `REPO_TAG` in
`src/mailroom_eda/config.py`):

- Build human-labeling packets for ALL FIVE document classes against their
  registry schemas (`labeling/registry.py::class_fields`: claim_checklist,
  intent, cuad/contract_entities, merger_entities, per-subclass corporate
  entities). Show the existing v9.3 GT fields, keep every document's existing
  train/test split, and add per-field decisions like the insurance packet.
- Incremental private checkpointing per confirmed field/chunk via
  `labeling/checkpoint.py`; a confirmed field is `human_confirmed` locally,
  `checkpointed` only after a verified staging write.
- Scope is still review/labeling: a sample review or hand label never clears a
  campaign task, and no official v10 publication happens automatically.

## Required before merge

- [ ] Corporate exporter reproducible from durable private source artifacts,
      not solely `/private/tmp/opencode/corporate-sample-audit`.
- [ ] Corporate scalar/list/absence and subclass correction validation.
- [ ] Returned decision values bound to their actual change-log snapshots.
- [ ] Portal restart, incremental field progress, stale writes and refresh tests.
- [ ] Immutable-revision, byte-verified private checkpoint and clean-root restore.
- [ ] Full review assets included in backup (including text/workbook assets).
- [ ] Actual browser evidence for save/export, highlights, reload and recovery.
- [ ] Final combined tests and independent review of the integrated changes.
- [ ] Update issue #51 and this checklist with actual evidence.

## Current verification blocker

### Recovery continuation

- Recovered implementation pushed as WIP commit `d173f67` on PR #56; PR is
  explicitly draft, not merge-ready. This includes the incomplete/empty HTML
  template so the failure is accounted for rather than silently omitted.
- The previous focused suite reported 367 passed, 7 failed, 1 skipped.
- Checkpoint repair now preserves the actual upload commit ID, downloads via
  the centralized authenticated helper at that revision, and verifies bytes
  before copying restored assets. Conflicting existing files stop restoration;
  they are never overwritten. macOS temporary-directory aliases are resolved
  for internally created scratch paths only; user-path symlink checks remain.
- Offline recovery tests cover corrupt downloads, all review asset extensions,
  idempotent clean-root restore, authentication and immutable revision forwarding.
- No live HF upload or paid labeling call in this continuation. New private
  interface artifacts are not yet claimed backed up.

### Cost-controlled labeling authorization

Additional GLM 5.3 Flash Legal calls are authorized, but only surgically:
verify the exact model/provider route and current balance first, use the cheapest
eligible short-document/field tasks, refine prompts from existing failure evidence,
then run a tiny capped smoke test before any batch. Validate schema, source
grounding and completeness before proceeding; stop on failed smoke checks.
Reuse saved successful outputs, retry only unresolved targets, and checkpoint
each verified batch. Keep proposed labels distinct from human-confirmed gold.
The next smoke's token estimate and hard dollar cap must be stated before execution.

The host filesystem reached 100% capacity (54 MiB available at inspection);
a validation worker failed with `database or disk is full`. Free disk space
before continuing writes or treating partially running workers as completed.
No private data or caches were deleted to work around this failure.
