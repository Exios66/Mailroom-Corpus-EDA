# Reviewer UX leg 2 — checkpoint

## Field outcomes and review-comment follow-up

- Added per-document field navigation and explicit field-only approve/correct/
  reject/defer actions. Draft intentions are labelled not saved; saved and
  checkpointed outcomes remain distinct. Schema-blocked review never claims gold
  approval. No whole-document approval is implied.
- Lists retain keep/drop/edit granularity. An unreconciled saved array correction
  now disables the original-item picker rather than risking overwriting edits.
- All three #59 comments validated: exact grouping, meaningful empty-state text,
  and real partial-autosave HTTP/visible-warning coverage implemented.
- Current guards: 37 browser checks, 48 focused Python tests, 4/4 fixed tasks,
  zero axe violations (3 manual-review items). Evidence `field-outcomes/`.
- Parent #58 review threads were inventoried separately. Its picker issue is
  fixed by this stacked branch; other parent probe/accounting/backup-runner issues
  are not silently marked resolved. Parent changes require a separate follow-up.

## Requested follow-up: progress and resume

The label-review panel now displays saved decisions / total targets, local drafts,
remaining saved reviews and checkpointed fields. Counts update after edits and
save-state hydration. Saved reviews include reject/defer decisions; the UI says
explicitly that reviewed is not the same as approved.

Position is stored locally as `(document_id, field)` independently of the packet
hash, so queue reordering does not change its meaning. On startup, after restoring
server reviews and local drafts, retain an unfinished target or advance from a
saved target to the next pending one. Without a valid position, use the first
pending target from restored server decisions. All-reviewed queues retain the
last position. Clearing browser storage loses the exact cursor but not server
review receipts.

Verification: existing fixed UX score remains **4/4**; **35** synthetic browser
checks, **48** focused Python tests, axe **0** violations (2 manual-review items).
Evidence: `/private/tmp/opencode/reviewer-ux-leg-2/progress-resume/`.
Manual-save race fixture now snapshots request bytes before delaying delivery,
so a newer edit cannot accidentally change the simulated in-flight request.

- PR #59 is stacked on #58: base `ar/prompt-loop-1` at `5c9c578`.
- Two experiments kept: task score **0/4 → 3/4 → 4/4**.
- Keep commits: `56806ce` (search/navigation); `1ad0d97` (draft/save safety).
- Public source changes contain no corpus data or reviewer decisions.
- Synthetic browser evidence: `/private/tmp/opencode/reviewer-ux-leg-2/`.
- Synthetic logs and loop records mirrored into Downloads/labels-staging/reviewer-ux-leg-2.
- No paid calls, HF checkpoint, user review submission or live-server restart performed.

## What improved

1. Search by document ID, filename, field, class and batch; token matching narrows
   the target dropdown while leaving the current review open. Clear search restores
   it. Alternating batch records no longer create duplicate groups.
2. Checkbox sizing and empty-value contrast fixed; axe now scans the review panel,
   not only the launcher. Real screenshots inspected locally.
3. Partial autosaves no longer declare unfinished work saved. A previously saved
   decision cannot overwrite an excluded draft, and its history is preserved.
4. Text entered while a manual Save is in flight survives the response. Journal
   rows describe notes/vote/metadata changes rather than just ACCEPT → ACCEPT.

## Reproduce

```sh
AXE_PATH=/private/tmp/opencode/axe.min.js sh autoresearch/reviewer-ux-leg-2/verify.sh /private/tmp/opencode/reviewer-ux-leg-2/confirm
```

The script prints the fixed task score; browser assertions and focused pytest
must also pass. No timing/human-throughput claim is made. Browser evidence is
synthetic, not an approval of any real label.

## Next bounded experiments

- Review persistent local-backup retries: document-level duplicate refusal must
  not block subsequent fields or a legitimate retry after an HF outage.
- Exercise browser/server offline recovery, typed validation and multi-tab
  conflicts with fixed fixtures before adding more automation.
- Measure navigation tasks on a large synthetic queue; do not use ever-growing
  assertion count as a claimed productivity gain.

Do not switch the live review server to experimental backend code or restart it
while the human may have unsaved work. Source HTML is read dynamically, so a new
page load in this checkout can see current HTML even without a process restart.
For strict deployment separation, run future experiments from a separate worktree.

## Correction ergonomics and live tracker (follow-up 5)

- **No hand-written JSON for approval.** "Approve displayed value" needs no
  replacement text at all. "Approve a correction" prefills the displayed candidate
  so only the wrong part is edited. A model value can still be inserted with one
  click. Only when no candidate exists does the editor offer an explicit example
  template that is NOT stored as a decision and cannot autosave until replaced
  with a real value (numbers must be JSON numbers, not template text).
- Saved corrections are never overwritten by prefill: an existing
  `corrected_labels_json` is preserved when switching to CORRECT.
- **Campaign tracker is live.** Summary shows saved reviews / total, local drafts
  awaiting save, checkpointed count and remaining from the current session;
  per-task reviewed counts use loaded records when a task maps to them, falling
  back to the server snapshot otherwise. Counters refresh on every decision and
  after every applied save.
- Verification: 4/4 fixed tasks under the strengthened assertions; 39 browser
  checks incl. CORRECT-prefill/no-JSON and live-tracker regressions; 48 focused
  Python tests; axe zero violations (3 manual-review items).
- Correction help copy now describes the prefilled editor instead of the old
  "required only for CORRECT" wording.

## Parent review fixes (round 6)

- quality_probe: provider setup errors print cleanly (exit 1); billed usage from
  failed/truncated calls counts against the cap.
- grade_corporate returns nonzero when real violations remain.
- verify_all counts pytest errors and empty summaries as failures, and records the
  axe dimension before returning a failing exit status.
- local_backup: later snapshots of the same document id are allowed (checkpoints,
  HF retries); different documents with identical content are still refused.
- review_portal: a failed local mirror is reported in the receipt and never blocks
  the private staging upload.
- Corporate batch test now uses a synthetic fixture (no private staged data).
- Rubric comment labels the two correspondence themes fixture-recorded; the 3.3r
  run log points at the archived packet beside it.
- Evidence: 39 browser checks, axe clean, 97/97 portal+launch, 201/201 labeling,
  48 focused Python tests.
