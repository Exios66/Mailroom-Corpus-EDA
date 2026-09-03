# AGENTS.md — Mailroom-Corpus-EDA

Exploratory data analysis (and the centralized HF upload helpers) for the
[`Lucius-Morningstar/mailroom-corpus`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-corpus)
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
  - `identity.py` — P0 document identity (document_id, content hashes,
    source provenance — cast-safe, absence is '')
  - `eval_contract.py` — P1 evaluation-contract derivations (§59 routing,
    §57–58 stage, §31 review/retry, §43 provenance) + closed vocabularies
    (fixture kinds, calibration quartet, matter/group, failure stages)
  - `matter.py` — P2 grouping derivations (§14A: header threads — verified
    absent here; subject+custodian+window reconstruction; never-mix guard)
  - `bundles.py` — P2 §14 synthetic bundle-family generator (flagged
    scaffold over real anchors; publish rides §84)
  - `fixtures.py` — §68–§72A fixture content (calibration quartet at live
    bands, arbiter scenarios, failure-stage matrix; publish rides §84)
- `scripts/` — CLI wrappers: `publish_docclass.py`, `backfill_intent.py`,
  `export_docclass.py`, `verify_hf.py`, `coverage_matrix.py` (→
  `docs/reports/audits/docclass_coverage_matrix.*`), `expansion_priorities.py`
  (→ `docs/reports/audits/docclass_expansion_priorities.*`)
- `run_all.py` — 7-phase pipeline (P0 download → P6 intent coverage audit)
- `reports/` — generated artifacts (figures/, figures_interactive/, tables/, SUMMARY_REPORT.md)
  - ALL of `reports/` is tracked in full per human directive (HUB-008) — never
    prune it and never commit regenerated variants of `figures_interactive/`:
    each Plotly HTML embeds a random per-render div UUID, so regenerated
    figures can never be byte-identical. The committed files are the canonical
    upstream bytes; treat local regeneration as scratch output only.

## Commands

```bash
.venv/bin/python run_all.py                      # full pipeline P0-P6
.venv/bin/python run_all.py --phases P3 P4       # figures only
.venv/bin/python scripts/backfill_intent.py --check
.venv/bin/python scripts/publish_docclass.py --help
```

**Summary writes**: `reports/SUMMARY_REPORT.json` is written only by a
full-pipeline run (all seven phases). `--phases` subset runs — and
`--no-interactive`, whose summary would be missing the P4 section — leave the
summary untouched; per-phase results print to stdout only. (HUB-009: subset
runs used to clobber the full-corpus summary with phase-partial stats.)

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

## HF facts (verified 2026-09-02, schema v8)

- Repo: `Lucius-Morningstar/mailroom-corpus` (v8, 2,000 rows; data tip
  `bba2f750`; hardened release rebuilt on v8 at `eafe1ab4`).
- Composition: insurance_claim 950 (carrier/inpatient/outpatient/pde 600 CMS
  DE-SynPUF + property 200 GNOTHEIA + auto 150 BDR motor),
  contract 509, correspondence 350, merger_agreement 152,
  corporate_record 39.
- Configs: `default` (blind, 4 cols) + `ground_truth` (60 cols incl. labels,
  intent provenance `intent_source`/`intent_confidence`/`intent_status`, AND
  the §84 hardened columns — identity/hashes, evaluation contract,
  matter/group) + `bundles` (38 cols, 50 rows) + `streams` (39 cols, 62 rows
  — §27–§29/§48 STREAM tier: `RUN-SIM-001` interleaved ingress stream over
  the bundle matters, 12 no-matter distractors) + `fixtures` (30 cols, 32
  rows). Built via `scripts/publish_hardened.py` (HUB-022) on the v8 base:
  v7 `document_id`s unchanged (0 drift), v8 LOB rows carry their own
  `source_corpus`/`annotation_source` (GNOTHEIA/BDR) + pinned
  `source_revision` via `metadata.source_dataset` / `.source_revision`
  (identity / eval_contract precedence: class map stays authoritative except
  the insurance LOB override — never churn published document_ids).
- Split: train 1,792 / test 208 on both configs; filename sets equal.
- v8 insurance LOB expansion (HUB-028): property rows from
  `gratex/GNOTHEIA-synthetic-insurance-dataset` (Apache-2.0) — FNOL bundles
  stratified by loss event, determination `pending` (no adjudication in
  source); auto rows from
  `bdr-ai-org/insurance-motor-claims-decision-v1` (MIT) — decision letters
  stratified by accident type × APPROVE/REVIEW/REJECT (all reject rows
  included), feature-grounded denial reasons, adjuster pseudonyms. Full GT
  conformance: all 950 insurance rows carry intent/subject/keywords +
  provenance (CMS template-derived backfill); claimed_amount recovered from
  doc text on 10 v7 gap rows; 3 train-only outpatient `:2` date gaps
  documented as source-N/A; test-split nullification enforced (zero empty
  class-relevant keys).
- v7 intent hydration (issue #5): 350/350 correspondence rows carry a
  canonical 8-class intent (payment_demand, notice, analysis, request, update,
  meeting_invite, press_communication, other); 96 manual + 254 llm_zero_shot
  (deepseek-chat, OpenRouter), 162 sha256-exact-body AESLC/Enron joins,
  1 flagged_review. All 8 classes present in the test split.
- Related: `enron-correspondence-dedup`, `mailroom-cuad-contracts-full`,
  `mailroom-s1-corporate-records`, `mailroom-maud-contracts`.

License note: the corpus card is CC-BY-4.0; v8 additions are Apache-2.0
(GNOTHEIA) + MIT (BDR). XpertSystems ins001/ins007/hlt015 samples are
CC-BY-NC-4.0 and were excluded; INSURBIAS (CC-BY-4.0) is deferred to v9
(narratives only, no decision GT).

See the `huggingface` opencode skill for the full Hub-interfacing workflow.