# Mailroom-Corpus-EDA

Full HF LLM-Mailroom Corpus Exploratory Data Analysis (DocClass Merged, v7).

Standalone mirror of the `Exios66/Mailroom-Corpus-EDA` repo; inside the
[mailroom-dev](https://github.com/Exios66/mailroom-dev) monorepo it lives at
`packages/mailroom-corpus-eda` as a virtual uv workspace member (no build).
Develop here, sync via `scripts/sync_packages.py` in mailroom-dev.

## Quick Start

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_all.py                 # P0-P6 full pipeline
.venv/bin/python run_all.py --phases P3 P4  # figures only
```

## Pipeline (run_all.py)

| Phase | What | Output |
|---|---|---|
| P0 | corpus download + manifest validation | `data/parquet/**` |
| P1 | structural integrity & provenance audit | `reports/tables/integrity_report.json` |
| P2 | composition: strata, imbalance, provenance | `strata_counts.csv`, `imbalance_metrics.json` |
| P3 | static PNG figures + EDA tables | `reports/figures/`, `reports/tables/` |
| P4 | interactive Plotly HTML figures | `reports/figures_interactive/` |
| P5 | cast-safe JSONL + parquet staging helpers | `data/staging/` |
| P6 | correspondence intent coverage & provenance audit (issue #5) | `reports/SUMMARY_REPORT.json` |

## Reports

- `reports/SUMMARY_REPORT.md` — narrative summary of all findings
- `reports/figures/` — static PNGs (text/token, CUAD, MAUD, claims,
  correspondence, imbalance, temporal, metadata)
- `reports/figures_interactive/` — Plotly HTML figures
- `reports/tables/` — CSV/JSON tables

## Dataset cards

Per-source documentation for the five corpora integrated into
[docclass-merged](https://huggingface.co/datasets/Lucius-Morningstar/docclass-merged)
— full context, source material, attribution, and purpose of each — lives in
[`docs/`](docs/README.md) (`docs/dataset-cards/`):

- [CUAD contracts](docs/dataset-cards/cuad-contracts.md) (`contract`, 509)
- [MAUD merger agreements](docs/dataset-cards/maud-merger-agreements.md) (`merger_agreement`, 152)
- [S-1 corporate records](docs/dataset-cards/s1-corporate-records.md) (`corporate_record`, 39)
- [Enron correspondence](docs/dataset-cards/enron-correspondence.md) (`correspondence`, 350)
- [CMS DE-SynPUF insurance claims](docs/dataset-cards/cms-desynpuf-insurance-claims.md) (`insurance_claim`, 600)

## HF Hub Interface (centralized from llm-entity-extraction)

The upload/publish helpers previously living in
[llm-entity-extraction](https://github.com/Exios66/llm-entity-extraction) are
centralized here (see the `huggingface` opencode skill for full docs):

- `src/mailroom_eda/hf_interface.py` — Hub client (upload, sha256 verify, repo mgmt)
- `src/mailroom_eda/dataset_export.py` — cast-safe metadata (KANBAN-076),
  JSONL line-boundary safety (KANBAN-088), parquet staging, manifests, splits
- `src/mailroom_eda/docclass_uploader.py` — docclass v7 publish, surgical card
  rendering (render_card_v7), blind-label strip, GT leak guard
- `src/mailroom_eda/intent_backfill.py` — correspondence intent hydration
  (issue #5): cross-walk, Enron/AESLC sha256 join, constrained LLM pass,
  provenance columns
- `src/mailroom_eda/token_budget.py` — token estimation & budget coverage

### CLIs

```bash
# correspondence intent backfill (issue #5; needs OPENROUTER_API_KEY for the LLM pass)
.venv/bin/python scripts/backfill_intent.py --check
.venv/bin/python scripts/backfill_intent.py --join-only
.venv/bin/python scripts/backfill_intent.py            # full Phases 1-5

# stage the v7 dump into the Hub tree (no upload)
.venv/bin/python scripts/publish_docclass.py --rows data/v7_rows.jsonl \
    --stage /tmp/stage --intent-stats data/v7_intent_stats.json

# publish to https://huggingface.co/datasets/Lucius-Morningstar/docclass-merged
HF_TOKEN=hf_... .venv/bin/python scripts/publish_docclass.py \
    --rows data/v7_rows.jsonl --stage /tmp/stage \
    --intent-stats data/v7_intent_stats.json \
    --commit-message "issue #5: v7 intent hydration" --publish

# byte-verify a local export against the Hub
.venv/bin/python scripts/verify_hf.py --repo Lucius-Morningstar/docclass-merged \
    --jsonl data/hf_export/mailroom-cuad-contracts-full.jsonl
```