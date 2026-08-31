# AGENTS.md — Mailroom-Corpus-EDA

Exploratory data analysis (and the centralized HF upload helpers) for the
[`Lucius-Morningstar/docclass-merged`](https://huggingface.co/datasets/Lucius-Morningstar/docclass-merged)
corpus — 1,650 legal documents across 5 doc_types (insurance_claim,
merger_agreement, contract, correspondence, corporate_record), 48 strata.

Mirror of the standalone `Exios66/Mailroom-Corpus-EDA` repo; in the monorepo
it lives at `packages/mailroom-corpus-eda` as a virtual uv member (no build).
Never edit it from both places in one session — develop here, sync via
`scripts/sync_packages.py` in mailroom-dev.

## Layout

- `src/mailroom_eda/` — the EDA + HF library (imports as `mailroom_eda`)
  - `config.py` — paths, doc types, colors, token budgets, matplotlib setup
  - `download.py` — corpus acquisition (HF snapshot) + manifest parsing
  - `integrity.py` — P1 structural integrity & provenance audit
  - `composition.py` — P2 strata / imbalance / provenance / metadata coverage
  - `visualizations.py` — P3 static PNG figures (30) + EDA tables
  - `visualizations_interactive.py` — P4 Plotly HTML figures (18)
  - `hf_interface.py` — centralized Hub client (upload, sha256 verify)
  - `dataset_export.py` — cast-safe JSONL (KANBAN-076/088), parquet staging, manifests
  - `docclass_uploader.py` — docclass v7 publish, surgical card render, leak guard
  - `intent_backfill.py` — correspondence intent hydration (issue #5):
    cross-walk, Enron/AESLC sha256 join, constrained LLM pass, provenance
  - `token_budget.py` — token estimation & budget coverage
- `scripts/` — CLI wrappers: `publish_docclass.py`, `backfill_intent.py`,
  `export_docclass.py`, `verify_hf.py`
- `run_all.py` — 7-phase pipeline (P0 download → P6 intent coverage audit)
- `reports/` — generated artifacts (figures/, figures_interactive/, tables/, SUMMARY_REPORT.md)

## Commands

```bash
.venv/bin/python run_all.py                      # full pipeline P0-P6
.venv/bin/python run_all.py --phases P3 P4       # figures only
.venv/bin/python scripts/backfill_intent.py --check
.venv/bin/python scripts/publish_docclass.py --help
```

## Conventions

- **Data never commits**: `data/` and `.venv/` are gitignored; `data/parquet`
  is re-fetched from the Hub by `download.py`.
- **HF uploads** use the centralized modules in `mailroom_eda.hf_interface`
  / `dataset_export` / `docclass_uploader` — never ad-hoc upload code.
  Metadata must be cast-safe (uniform keys, string-typed), JSONL must be
  line-boundary-safe (U+2028/U+2029/NEL), and labels NEVER ride in the blind
  `default` config.
- **Interactive HTML figures** are TRACKED IN FULL per human directive
  (HUB-008): each Plotly HTML embeds a random per-render div UUID, so
  regenerating can never be byte-identical — the committed files are the
  canonical upstream bytes; treat local regeneration as scratch only.
- **Intent backfill** (issue #5): never hand-edit `data/backfill/intent_labels.jsonl`;
  re-run `scripts/backfill_intent.py` (checkpointed — the LLM pass skips rows
  already in the sidecar). The canonical vocabulary is the closed 8-class set
  in `intent_backfill.CANONICAL_INTENTS`; `other` is the explicit fallback,
  never null.
- **Split rule**: md5(filename) % 10 == 0 → test (90/10), stable across rebuilds.
- **Determinism**: `RANDOM_STATE = 42`; rebuilds of JSONL/parquet must be
  byte-identical (sorted rows, deterministic order).

## HF facts (verified 2026-08-31)

- Repo: `Lucius-Morningstar/docclass-merged` (v7, 1,650 rows, rev `fc1f211c`;
  data tip `1acd2600` + card-only pretty_name bump).
- Composition: insurance_claim 600, contract 509, correspondence 350,
  merger_agreement 152, corporate_record 39.
- Configs: `default` (blind, 4 cols) + `ground_truth` (31 cols incl. labels +
  intent provenance `intent_source`/`intent_confidence`/`intent_status`).
- Split: train 1,474 / test 176 on both configs; filename sets equal.
- v7 intent hydration (issue #5): 350/350 correspondence rows carry a
  canonical 8-class intent (payment_demand, notice, analysis, request, update,
  meeting_invite, press_communication, other); 96 manual + 254 llm_zero_shot
  (deepseek-chat, OpenRouter), 162 sha256-exact-body AESLC/Enron joins,
  1 flagged_review. All 8 classes present in the test split.
- Related: `enron-correspondence-dedup`, `mailroom-cuad-contracts-full`,
  `mailroom-s1-corporate-records`, `mailroom-maud-contracts`.

See the `huggingface` opencode skill for the full Hub-interfacing workflow.