# Bounded result: 2 experiments kept

| Evaluation | Baseline | Experiment 1 | Experiment 2 |
|---|---:|---:|---:|
| Fixed reviewer tasks passed (of 4) | 0 | 3 | 4 |
| Find target by metadata tokens | fail | pass | pass |
| Empty search preserves position | fail | pass | pass |
| Consolidated batch groups | fail | pass | pass |
| Partial save preserves unfinished work | fail | fail | pass |

Final guards: 48 focused Python tests passed; inherited verification reports
33 browser checks, zero axe violations, 96/96 portal+launch tests, 201/201 labeling
tests, fixtures and replays 5/5, cap guard and read-only coverage audit passed.
Commands and score definitions are pinned in README.md. Screenshot inspected:
`/private/tmp/opencode/reviewer-ux-leg-2/iteration-2/09-review-ux-final.png`.

Experiment 1 initially failed the accessibility guard (two low-contrast empty
values, exposed by inspecting the review panel instead of the launcher); the
contrast correction passed on rerun before acceptance. No candidate with a red
guard was kept. No experimental candidate was reverted in this two-round leg.

Supplemental regression checks exercise edits typed while Save is in flight,
preservation of excluded drafts with older saved values, and retention of their
history. These are guards, not extra points added to the four-task score.

Limits: synthetic desktop browser tasks, not measured real-user productivity;
axe reports 2 items requiring manual review. No mobile or screen-reader trial,
remote HF failure injection, or real human label submission performed. Existing
private-backup deduplication/retry constraints remain a queued concern, not a
solved claim. Parent PR #58 remains open and is not auto-merged.
