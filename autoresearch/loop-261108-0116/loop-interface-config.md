# Interface loop — labeler review + annotation (v1)

```
Goal: Maximize review efficiency and per-label control: approve/drop/edit individual
      labels on array candidates (keep correct labels, remove wrong ones), a fast
      "next undecided target" loop for reviewers, and a machine contract so agents
      can read the packet and save decisions without scraping the page.
Scope: src/mailroom_eda/labeling/review_page.html, review_portal.py,
       docs/labeler-review-viewer.md, tests/browser_review_page.mjs,
       tests/test_labeling_review_portal.py
Metric: agent-drivable surface count (documented + executed endpoints and affordances),
        higher is better. Baseline 3 (packet GET, reviews POST, revision queue GET);
        per-label items and /api/contract and Next pending raise it.
Verify: pytest subset (review_portal + review_export + production) + browser smoke
        CHROME=... node tests/browser_review_page.mjs <evidence-dir>
Guard: .venv/bin/python -m pytest tests/test_labeling_review_portal.py -q
Iterations: 15 (bounded)
```

## Independent acceptance (above the working metric)

- Browser smoke PASS with per-label scenario + Next pending + /api/contract mock
  (evidence dir with screenshots 01-08; 06 shows the per-label picker).
- `GET /api/contract` payload asserted against DECISION_KEY PACKET_SCHEMA and rules.
- Per-label round trip: portal save accepts CORRECT with the kept array
  (`test_correct_can_drop_a_single_label_item`), receipts carry it verbatim.
- Visual: Review pane screenshot of the picker state for the user.
