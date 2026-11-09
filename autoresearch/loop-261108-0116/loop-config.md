# Loop config — labeling prompt hill (v1)

```
Goal: Improve labeling-prompt agreement/quality per document type and subclass, starting
      with the observed 34/90 adjudications ("models disagree") in the claim_checklist
      pilot; keep every kept change independently verified on paid runs before it reaches
      main.
Scope: src/mailroom_eda/labeling/prompts/*.md  (full templates read, all 13)
Metric: rubric % (higher is better) — autoresearch/loop-261108-0116/rubric_prompt.py,
        calibrated to the real failure taxonomy mined from the pilot rows
        (docs/reports/audits/v10_labels/**/fields.csv).
Verify: .venv/bin/python autoresearch/loop-261108-0116/rubric_prompt.py    # prints a number
Guard: .venv/bin/python -m pytest tests/test_labeling.py tests/test_labeling_reasoning.py \
           tests/test_labeling_review_portal.py -q
Iterations: 25 (bounded)
```

## Independent acceptance (the judge above the working metric)

The rubric is the fast search signal only. Acceptance of any kept prompt change is:

1. `smoke_fixtures check` must fail-with-message → the recorded fixture now disagrees with the
   edited prompt (expected staleness).
2. Re-record the affected type (`record --types <t> --cap-usd <share> --replace`, ~$0.01/type)
   → `check` and `replay` both green.
3. Read the new `expected-review-data.json` + a rerun of the pilot disagreement taxonomy:
   fewer "the evidence quote ... could not be located" issues and fewer duplicate/repeated item
   sets than the recorded fixture; record the before/after counts in the TSV note.

Cumulative spend cap for this loop: **$1.00 total across all re-records** (the recording budget
previously authorized; $0.0159 spent so far). Stop the loop when the cap is reached.

## Rules honored from the skill

- one focused change per iteration; commit before verify with `experiment:` prefix
- keep / discard / crash / no-op status per iteration in TSV
- fork-and-flush at 33%/66% of budget if 5+ iterations pass without a keep
- prompt edits stay on this loop branch (`ar/prompt-loop-1`); merged only after the paid verify
