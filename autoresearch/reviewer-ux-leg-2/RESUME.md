# Reviewer UX leg 2 — checkpoint

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
