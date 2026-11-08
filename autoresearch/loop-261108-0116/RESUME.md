# RESUME.md — autoresearch loop 261108-0116 (interface + prompt hills, PR Exios66/Mailroom-Corpus-EDA#58)

## Where we are (2026-11-08, post it8 prep)
- Interface metric **29** (per-label picker, next-pending, /api/contract, batch grouping, sticky
  initials, per-batch progress, bulk "Accept verified in batch", tabbed page, ARIA tabs,
  a11y landmarks, batch-scoped next-pending + keyboard legend, batch-jump select). axe clean.
- Prompt rubric **100** (only live-verified themes certified). Fixtures 5/5 current, replays 5/5,
  cap-guard pass; labeling suite 201/201; portal+launch green; coverage pass.
- Prompt iterations KEPT: it2 (claim rewrite −165B), it3 (correspondence calibration),
  it6 (correspondence Ro1/TCM wording — both fixtures re-recorded), it7 (claim worked example,
  live-verified via quality_probe.py: 23 clean / 5 bad, all 5 = deepseek 8k-output truncation,
  runtime window policy — documented, not a prompt defect).
- it4 intent prompt: paid κ **0.7356 < 0.75 gate** → HONEST DISCARD (reverted; evidence in
  `docs/reports/audits/v10_labeling_verify/3.3r_intent-prompt-verify/`). Keep the rule: prompt
  candidates live or die on a live verification, never the mechanical rubric alone.
- Spend ~$0.139 of the operator's authorized $5. Cap bumps authorized: 8c→1.12, 8e→1.16
  (7c-e 0.88 earlier). Fixture record cap is $0.10 per record.
- Corporate leg (it8): mined the 10.1 SEC draw OFFLINE ($0) — 110 reviewed rows / 102 notes
  (rule-6 ×15, rule-5 ×8, conformed-sig ×10) + 17 verify findings (3 errors / 14 doubts) →
  6 candidate rules in `it8_corporate_draft.md`; `record_form` withheld as a SPEC-amendment question.
- Tooling committed (`f989af3`): `quality_probe.py` corporate mode (sha-verified gold docs,
  scalar/list printed-rungrading, pre-v10 subclass alias map, row shims) and
  `grade_corporate.py` (field-type-aware: ISO dates re-graded against 18 printed spellings;
  self-check pins the oracle on human gold: 99 populated dates print, 21 correct absences, 0 false flags).

## In flight RIGHT NOW
- Background shell **sh_1bc719a2200148Sr4zREhN2qsj**: the corporate LIVE probe — 110 rows ×
  2 models (`minimax/minimax-m3.1-flash`, `deepseek/deepseek-v4.1-flash`), cap $0.25, output →
  `autoresearch/loop-261108-0116/quality_probe_report.json`. The report currently on disk is the
  STALE it7 claim report (task=claim_checklist) — it is overwritten on completion. This run
  predates the parsed-capture fix, so it carries violations only (quoting oracle).
- **PAUSED for wifi loss, then RESUMED on the operator's "Please resume" (2026-11-08 ~13:1x local)**:
  python PID **21512** + wrapper **21509** were SIGSTOPped (~24 min of completed rows preserved
  in RAM) and are now running again (`kill -CONT 21512 21509`). The one in-flight API row may
  return an error entry — acceptable, single-row loss.
  - Key: `/private/tmp/opencode/orkey` (0600) stays on disk while paused; delete only after the
    run's report lands and passes grading (protocol).
- OPENROUTER key protocol: delete the moment the probe's report lands. Re-create ONLY on an
  explicit new key from the operator.

## On the probe's completion (exact steps, in order)
1. `rm /private/tmp/opencode/orkey` (after any single re-verify pass you intend to run, not before).
2. `.venv/bin/python autoresearch/loop-261108-0116/grade_corporate.py` →
   `date_prints` = the probe's byte-only false positives (do NOT count); `date_real`/`real` = REAL.
3. Map each REAL class to an it8 rule; patch a prompt ONLY where a confirmed failure class exists.
   Nothing confirmed → say so, skip to 4. Every prompt edit bundles, in the SAME key window:
   `smoke_fixtures record --types corporate_record --initials LM --cap-usd 0.10 --replace`
   (echo the cap), then `check`+`replay`, then suites.
4. `prompt_results.tsv` row (it9 note with the verdict), re-sync
   `docs/plans/v10-structure-and-label-completion.md` quotes if any prompt file changed,
   run `verify_all.sh` (AXE_PATH vendored) for the dimensions.tsv ledger row, commit+push.
5. Sync `~/Downloads/labels-staging/2026-11-08-session-1/loop-final-state/` + `manifest.sha256`,
   PR comment with the grading table, then delete the key file if not done in 1.

## Standing rules (never break)
- Paid prompt changes land ONLY with live verification (fixture re-record `check`+`replay`
  green, or a live probe). No unverified prompt edits, ever.
- Agents accelerate review but never forge initials; staging advances only through the review
  portal's explicit human Checkpoint (initials + one Save).
- Prune nothing behind a wall of abstraction: smallest diff that verifies; keep fixtures
  hash-pinned; any prompt-file edit forces a fixture re-record.

## Queued after it8 closes
- interface it12 candidates (from the ledger's weakest dimension), offline mining legs,
  human gates unchanged (88 claim + 7a portal sign-offs, GLM@off re-plan).
