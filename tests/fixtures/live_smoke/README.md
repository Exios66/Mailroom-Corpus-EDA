# Gold smoke fixtures

One directory per document type (`contract`, `merger_agreement`, `corporate_record`,
`insurance_claim`, `correspondence`), each a small **real** labeling run recorded once from the
public dataset and replayed offline by `tests/test_live_smoke_fixtures.py`. They are the gold
examples for the labeling path: the exact documents, the exact model answers and the review-portal
packet those answers produce.

| File | What it holds |
|:---|:---|
| `manifest.json` | dataset repo / revision / tag and the public file's sha256, task, set, models, per-model reasoning and `max_tokens`, the prompt, input and response-schema sha256 of every (document, model, variant), the documents (id, subclass, split, text sha256), who recorded it and when, and what it cost |
| `documents.jsonl` | the selected public rows exactly as downloaded, full text included |
| `calls.jsonl` | the run's call journal, reduced to deterministic fields (no timestamps, nothing that can hold a credential or a local path) |
| `expected-review-data.json` | the review packet the run produced, normalized; the replay must reproduce it |
| `README.md` | one paragraph: what it is, when and by whom it was recorded, what it cost, how to re-record |

Only documents that **still need labels** are recorded (the launcher's trial rule): no CUAD or MAUD
gold, so nothing already expert-labeled is billed. The documents are public corpus text; no client
or private document belongs here.

## Commands

```bash
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.smoke_fixtures check     # offline: still current?
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.smoke_fixtures replay    # offline: replay answers, diff the packet
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.smoke_fixtures record --initials XX --plan-only   # downloads the public file, spends nothing
PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.smoke_fixtures record --initials XX --cap-usd 1.00  # PAID
MAILROOM_LIVE_HUB=1 .venv/bin/python -m pytest tests/test_live_smoke_fixtures.py   # also re-checks the text against the Hub
```

`record` is the only command that spends money. See `docs/labeler-review-viewer.md`, "Gold smoke
fixtures", for when to re-record and what it costs. A fixture that `check` calls stale is never
edited by hand: it is re-recorded with `--replace`.

Nothing under this folder is a test module, so pytest collects nothing from it.
