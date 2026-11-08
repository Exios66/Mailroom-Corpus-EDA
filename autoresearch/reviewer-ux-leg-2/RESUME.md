# Reviewer UX leg 2 — checkpoint

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
