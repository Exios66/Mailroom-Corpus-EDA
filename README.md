# Mailroom-Corpus-EDA

Full HF LLM-Mailroom Corpus Exploratory Data Analysis (DocClass Merged, v6).

## Quick Start

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_all.py                 # P0-P5 full pipeline
.venv/bin/python run_all.py --phases P3 P4  # figures only
```

## Pipeline (run_all.py)

| Phase | What | Output |
|---|---|---|
| P0 | corpus download + manifest validation | `data/parquet/**` |
| P1 | structural integrity & provenance audit | `reports/tables/integrity_report.json` |
| P2 | composition: strata, imbalance, provenance | `strata_counts.csv`, `imbalance_metrics.json` |
| P3 | 27 static PNG figures + EDA tables | `reports/figures/`, `reports/tables/` |
| P4 | 18 interactive Plotly HTML figures | `reports/figures_interactive/` |
| P5 | cast-safe JSONL + parquet staging helpers | `data/staging/` |

## Reports

- `reports/SUMMARY_REPORT.md` — narrative summary of all findings
- `reports/figures/` — 30 static PNGs (text/token, CUAD, MAUD, claims,
  correspondence, imbalance, temporal, metadata)
- `reports/figures_interactive/` — 18 Plotly HTML figures
- `reports/tables/` — 17 CSV/JSON tables

## HF Hub Interface (centralized from llm-entity-extraction)

The upload/publish helpers previously living in
[llm-entity-extraction](https://github.com/Exios66/llm-entity-extraction) are
centralized here (see the `huggingface` opencode skill for full docs):

- `src/mailroom_eda/hf_interface.py` — Hub client (upload, sha256 verify, repo mgmt)
- `src/mailroom_eda/dataset_export.py` — cast-safe metadata (KANBAN-076),
  JSONL line-boundary safety (KANBAN-088), parquet staging, manifests, splits
- `src/mailroom_eda/docclass_uploader.py` — docclass v6 publish, surgical card
  rendering, blind-label strip, GT leak guard
- `src/mailroom_eda/token_budget.py` — token estimation & budget coverage

### CLIs

```bash
# stage a v6 dump into the Hub tree (no upload)
.venv/bin/python scripts/export_docclass.py --v6 data/datasets/docclass_merged_v6.jsonl --out /tmp/stage

# publish to https://huggingface.co/datasets/Lucius-Morningstar/docclass-merged
HF_TOKEN=hf_... .venv/bin/python scripts/publish_docclass.py \
    --v6 data/datasets/docclass_merged_v6.jsonl --stage /tmp/stage \
    --commit-message "KANBAN-105: schema v6 revN" --publish

# byte-verify a local export against the Hub
.venv/bin/python scripts/verify_hf.py --repo Lucius-Morningstar/docclass-merged \
    --jsonl data/hf_export/mailroom-cuad-contracts-full.jsonl
```