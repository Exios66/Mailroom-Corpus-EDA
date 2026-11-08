# RESUME.md — autoresearch loop 261108-0116 (interface + prompt hills, PR Exios66/Mailroom-Corpus-EDA#58)

## Where we are
- Interface metric **27** (per-label picker, next-pending, /api/contract, batch grouping,
  sticky initials, per-batch progress, bulk "Accept verified in batch", tabbed page,
  ARIA tab keyboard + roving tabindex, a11y landmarks + labelled tab panels).
- Prompt rubric **100** with the claim + correspondence + intent bundles built from real
  disagreement evidence (8a pilot, 7a pilot, settled 4a-4c adjudications).
- Spend so far ~$0.07 (under the operator's authorized $5 line; no cap raised past
  7c/7d/7e 0.85->0.88, which the $5 already covered).

## Verify runners
- `verify_interface.sh [evidence]`   — interface smoke; prints executed checks count.
- `verify_all.sh [evidence]`         — ALL registered dimensions in one run (see
  checks_registry.tsv); appends a row to `dimensions.tsv` (the growing coverage ledger).
  `AXE_PATH=/private/tmp/opencode/axe.min.js` (vendored axe-core) enables the a11y scan;
  without it axe=skip is recorded honestly.
- `rubric_prompt.py`                 — mechanical prompt rubric (weighted theme coverage).

## How a new check joins the loop (dynamic coverage rule)
1. Implement the check (browser `check(...)`, pytest, CLI gate or rubric theme).
2. Register: wire its capture in `verify_all.sh` + a `checks_registry.tsv` row.
3. Iteration discipline: red → fix or discard (TSV `discard` row keeps evidence);
   green → `keep` row + commit on `ar/prompt-loop-1` (PR #58 grows) + labels-staging sync.

## Queued next
- interface it10 candidates (checked each iteration via the ledger), prompt it5+
  (more mined themes → more rubric coverage), and the human gates (portal sign-offs
  for 88 claim + 7a batch; GLM@off re-plan) unchanged.
