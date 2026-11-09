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
- Spend ~$0.37 of the operator's authorized $5 (includes the $0.2322 corporate scan; no key currently stored). Cap bumps authorized: 8c→1.12, 8e→1.16
  (7c-e 0.88 earlier). Fixture record cap is $0.10 per record.
- Corporate leg (it8): mined the 10.1 SEC draw OFFLINE ($0) — 110 reviewed rows / 102 notes
  (rule-6 ×15, rule-5 ×8, conformed-sig ×10) + 17 verify findings (3 errors / 14 doubts) →
  6 candidate rules in `it8_corporate_draft.md`; `record_form` withheld as a SPEC-amendment question.
- Tooling committed (`f989af3`): `quality_probe.py` corporate mode (sha-verified gold docs,
  scalar/list printed-rungrading, pre-v10 subclass alias map, row shims) and
  `grade_corporate.py` (field-type-aware: ISO dates re-graded against 18 printed spellings;
  self-check pins the oracle on human gold: 99 populated dates print, 21 correct absences, 0 false flags).

## Corporate scan: COMPLETED (2026-11-08 ~13:4x local)
- 110 rows x 2 models, cap $0.25, spent **$0.2322**. **209/220 clean, ZERO printed-run violations**
  (no phantom names/dates/numbers from either model; the date-aware re-grade in grade_corporate.py
  had nothing to reclassify). All 11 bad calls = deepseek-v4.1-flash finish_reason=length 8k-output
  truncation on 11 large docs, zero on minimax - the documented runtime window policy (it7), not a prompt defect.
- Decision: **no it8 rule patch justified by live evidence** - the drafted candidates wait until a run
  shows the classes; no prompt edit, so no fixture re-record and no quote re-sync. The key file was
  deleted immediately after the verdict per protocol.
- Honest limit: extraction ACCURACY vs the human gold is not measured in this run (it predates the
  parsed-capture fix; quoting oracle only). Accuracy lands on the next corporate verify with parsed answers.
- Final results pushed to labels-staging scan-results/ (report + grade stdout + completed status marker).
- Runtime follow-up recorded: if deepseek serves corporate at scale, raise per-task max_tokens past 8k
  (task/runner knob; never a prompt edit).

## Standing rules (never break)
- Paid prompt changes land ONLY with live verification (fixture re-record `check`+`replay`
  green, or a live probe). No unverified prompt edits, ever.
- Agents accelerate review but never forge initials; staging advances only through the review
  portal's explicit human Checkpoint (initials + one Save).
- Prune nothing behind a wall of abstraction: smallest diff that verifies; keep fixtures
  hash-pinned; any prompt-file edit forces a fixture re-record.

## Corporate review viewer: CONFIGURED (interface it11)
- `review_corporate --batch pilot|batch_A..E` produces review packets for every staged SEC addition: 110 documents / 1,302 targets / 29 fields; oversize text is a linked pointer in spreadsheet cells (JSON/Markdown keep complete originals); `ReviewPortal._collect()` returns all 110 corporate records cleanly, including the 16 too-large documents. `verify_all` is green except the still-prepaid human evidence gates.

## Next campaign steps
- Current corporate production arms 9a-9e are a separate fresh-labeling campaign; 9a (29 documents) is pending, needs the operator's next OpenRouter key, then human verdicts against the 0.90 precision gate before 9b-e.
- The 110 SEC additions remain review-only (unassigned split; not formal 9a gate evidence and not auto-ingested) until a human reviewer saves decisions with initials and an explicit Checkpoint.
- After 9a, use the portal tracker for 9b-9e agreement gates; if deepseek is retained, raise its corporate completion-output ceiling through runner configuration before the sweep.

## Queued after it8 closes
- interface it12 candidates (from the ledger's weakest dimension), offline mining legs,
  human gates unchanged (88 claim + 7a portal sign-offs, GLM@off re-plan).
