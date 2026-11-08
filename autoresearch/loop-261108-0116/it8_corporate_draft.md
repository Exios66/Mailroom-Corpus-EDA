# it8 draft — corporate entity extraction: mined candidates from the 10.1 SEC draw

## Evidence surface (offline, $0)
- `corporate-labels.jsonl`: 110 reviewed rows, 102 with free-text notes citing numbered rules.
  Rule-hit histogram across notes: **rule 6 ×15** (effective_date = adoption date of the
  certified resolutions, not the certificate date), **rule 5 ×8** (bundled POA/other docs are
  not part of the record; 20 notes say "Bundles", 19 say "not labelled"), **conformed-typed
  signature ×10**, plus 66 un-numbered note judgements (their clusters below).
- `verify/review5_findings.jsonl`: the second pass found **3 errors + 14 doubts**, each with a
  quote and a spec basis, concentrated in: `signatories` ×4, `effective_date` ×4, `jurisdiction` ×4,
  `keywords` ×2, `record_form` ×2, `filing_number` ×1.

## Candidate prompt rules (all pending the paid `corporate_record` fixture verify — no prompt edit until then)

1. `effective_date` (the biggest confusion: 15+8 notes, 4 verify rows):
   - certified resolutions carry their OWN adoption dates → the record's effective date is the
     earliest adoption date, never the certificate's own date (rule 6; examples: FORD MOTOR
     COMPANY, Gorman-Rupp).
   - a "dated as of X" certificate with an effective time the next day → the 'as of' date wins
     (example: 2002-12-17 vs 2002-12-18).
   - a reissued warrant prints original-issue and reissue dates → the original issue date.
   - a printed date that contradicts the filing year → absent, not the contradiction
     ('4 day of November, 2004' after a 2004-02-20 filing).
2. `jurisdiction` (4 doubts): the governing state comes from the EXECUTED body — never from an
   attached "Form of" exhibit (a Michigan-cited exhibit inside a Delaware charter) and not from a
   minutes sentence that merely discusses a state-of-incorporation change (the change applies only
   when the minutes approve it). A tribal filing office header acts like the state filing office.
3. `signatories` (already heavily ruled; new edge cases): typed names on unsigned, undated minutes
   lines are NOT signatories even when the minutes carry a meeting date; a registered agent /
   trustee acceptance signer is a signatory of their own block only when it is that block, never of
   the corporate action (Diane Stout vs JPMorgan trustee acceptance).
4. `keywords` (2 doubts): drop venue names and anything that appears only inside a trading-
   restriction legend — keywords are parties, agents, trustees, counterparties, instruments.
5. `filing_number` (1 doubt): an unlabelled Delaware stamp is not a file number unless the
   document itself assigns that label to the number.
6. `record_form` (2 doubts — SPEC-level, heavier): fully attached charters/by-laws pages may change
   the record_form classification (certificate over amended Articles = charter family?). Withhold
   from the prompt; raise as a SPEC amendment question next draw.

## Verification vehicle
`smoke_fixtures record --types corporate_record` re-record (key needed), plus reuse
`quality_probe.py` per-row grading on the 17 verify rows (extend: `--task corporate`).
