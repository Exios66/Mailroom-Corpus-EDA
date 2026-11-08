# Reviewer UX — leg 2 (stacked on PR #58)

Parent: `ar/prompt-loop-1` at `5c9c578`. Branch: `ar/reviewer-ux-leg-2`.
Merge this PR into its parent branch first, or retarget after #58 merges;
do not merge the parent automatically. Two bounded experiments, no paid calls.

## Pinned evaluation

Primary: number of successful tasks out of **4**, denominator fixed before edits:

1. Search `ux-b intent` narrows three review targets to exactly the intended one.
2. An unmatched search shows an empty state without changing the review cursor.
3. Alternating alpha/beta/alpha targets yield exactly two batch groups.
4. Saving a valid decision while another remains unfinished retains the draft
   and the unsaved-state warning (never signals everything is durable).

Verify: `autoresearch/reviewer-ux-leg-2/verify.sh <evidence-dir>`.
Higher is better. Fixtures are synthetic; no human decisions are submitted.
Task assertions live in `tests/browser_review_page.mjs`; results in `ux-tasks.json`.

Guards: the inherited browser checks must all pass, axe must report zero
violations when supplied, and relevant Python tests must pass. Test counts are
coverage guards, **not** the optimization metric. No claims about measured human
throughput or general legal accuracy. Keep only improving candidates with green
guards; record misses/discards as well as successes.

## Checkpoints and safety

Each kept experiment: commit, push stacked branch, record results, mirror loop
artifacts to Downloads/labels-staging. Keep source documents, receipts, tokens,
and reviewer PII out of public commits. Preserve audit history and source/schema
validation. Do not submit human approvals, spend money, upload to HF, or restart
the live review process during experiments.

Evidence screenshots are local under `/private/tmp/opencode/reviewer-ux-leg-2/`.
No unattended/unbounded continuation is implied by this two-experiment leg.
