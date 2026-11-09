# it5 draft — cuad span verbatim rule from the 1.3 calibration failures

## Mined evidence (offline, $0)
`docs/reports/audits/v10_labels/1.3_cuad-calibration/spans.csv`: 725 spans, **14
verbatim_ok=false** (1.9%). Failure-mode histogram against the stage doc_text:

- **13/14**: the model **smoothed scrub/deletion artifacts away when copying** — the
  source carries junk the doc-class doc text contains (digit-only placeholders like
  `2\n\n\n\n\n\n`, runs of `----`, runs of blank lines) and the quote omits them
  ("or [else] otherwise", "without [6] giving", "race ---- of").
- 1/14: the 30-char prefix itself does not occur in the doc (different failure —
  re-copy does not fix it; keep as a one-off).

## Candidate rule (cuad_system.md, appended to point 2)
> Copy the bytes as printed: placeholder or deletion artifacts (digit-only lines, runs of
> `----`, runs of blank lines) stay in the quote exactly where they appear; never smooth
> them away.

Byte cost ≈ +210; verify through the 1.4x cap-headroom guard before recording.

## Verification vehicle + acceptance (REQUIRED before any keep)
- Record the contract smoke fixture with the new prompt hash
  (`labeling.smoke_fixtures record --types contract`), then `check` + `replay` green → keep; otherwise discard.
- Paid cost estimate: ~$0.002–0.004 (fixture cap $1 covers).
- Rubric extension: only AFTER the fixture verifies (the rubric certifies
  live-verified themes only — see it4's kappa failure).

## Not in scope
- Scorer logic (`field_scoring`/verbatim checker) — llm-dojo-scoring owns it; a prompt
  cannot relax a byte-verbatim checker.
- The 1 prefix-miss span.
