# Docclass Merged Corpus — EDA Summary Report

Generated: 2026-08-31 · Pipeline: `run_all.py` (P0–P6) · Data: `Lucius-Morningstar/mailroom-corpus` (v7; **v8 supersedes — see note below**)

> **v8 note (HUB-028, 2026-09-02)**: this report's figures describe the v7
> snapshot (1,650 rows). The published v8 corpus is **2,000 rows**:
> insurance_claim 950 (carrier/inpatient/outpatient/pde 600 CMS DE-SynPUF +
> property 200 GNOTHEIA + auto 150 BDR motor), contract 509, correspondence
> 350, merger_agreement 152, corporate_record 39 — 50 strata. The EDA
> pipeline's P0–P6 run against v8 has not been re-executed; figure
> regeneration is a follow-up (data: `Lucius-Morningstar/mailroom-corpus` v8,
> tip `bba2f750`). Double-hardened since: the §84 release (HUB-022/HUB-032)
> adds the 60-col evaluation-contract `ground_truth`, the `bundles`
> (50 rows), `streams` (62 rows — §27–§29/§48 STREAM tier) and `fixtures`
> (32 rows) configs at `eafe1ab4` — the §40–41 coverage matrix
> (`docs/reports/audits/docclass_coverage_matrix.md`) and the P5 surface
> ledger (`docclass_p5_surfaces.md`) are the current-coverage instruments.

## Executive Summary

The corpus is a **1,650-document** legal classification surface across **five
doc_types** and **48 strata** (doc_type × expected_subclass). The dataset is
fully joinable (blind ↔ ground_truth, 100% filename-set agreement), the split
rule (md5(filename) % 10 → test) is byte-exact with zero mismatches, and all
annotation offsets validate (CUAD 13,753/13,753 span matches = 100%).

| doc_type | n | share | source |
|---|---|---|---|
| insurance_claim | 600 | 36.4% | CMS DE-SynPUF 2008–2010 renders |
| contract | 509 | 30.8% | CUAD v1 (theatticusproject/cuad) |
| correspondence | 350 | 21.2% | CMU Enron maildir (dedup) |
| merger_agreement | 152 | 9.2% | MAUD v1 (Zenodo 7500064) |
| corporate_record | 39 | 2.4% | SEC EDGAR S-1 exhibits |

**Imbalance:** max/min ratio 15.4× at type level, 150× at stratum level
(min stratum `corporate_record/other` = 1 row). Type entropy = 1.97 bits.

## Key Findings

### 1. Text & token geometry
- Merger agreements are by far the longest (mean ~356k chars ≈ 89k tokens;
  max 1.0M chars ≈ 252k tokens) — **exceed common 32k/65k contexts**.
- Insurance claims are uniformly short (~1,200 chars, σ=186) — a tight,
  homogeneous block.
- Token budget coverage: 65% of the corpus fits ≤4k tokens, 73% ≤8k, 82%
  ≤16k, 88% ≤32k, 99.7% ≤131k.

### 2. CUAD annotations (509 contracts, 41 clause types)
- Every contract carries annotations; 13,753 spans with **100% exact offset
  match** against doc_text.
- Most-annotated: `Parties` (508 docs, mean 5.0 spans), `Agreement Date`
  (469), `Governing Law` (436), `Expiration Date` (412).
- 14 clauses appear in <15% of contracts (long-tail annotation).

### 3. MAUD annotations (152 merger agreements, 22 tasks)
- 3 tasks annotated on every agreement; coverage ranges 11–152 docs.
- Mean 16.4 labels per agreement (range 11–20).
- Metadata count consistency: 152/152 rows match
  `maud_label_count == sum(maud_categories) == upstream count`.

### 4. Insurance claims (600 rows)
- 9/13 fields 100% filled; `adjuster` 59%, `claimed_amount` 99.7% (v6 rev2
  boosted 400→600 with balanced subtypes: carrier/inpatient/outpatient/pde
  × 150).
- Coverage determination & denial reasons fully populated → ready for
  coverage-classification supervision.
- Loss→filing delay: median 0 days (425/597 same-day; max 35 days) — see
  `18_claim_dates_timeline.png`.

### 5. Correspondence (350 rows)
- Enron source: subclass (8), content-topic (11), intent (8, canonical closed
  set), sentiment (neutral 178 / positive 97 / negative 75) — the only
  sentiment-labeled subset.
- **Intent is 100% hydrated** (issue #5 / v7): all 350 rows carry a canonical
  intent — `intent_source` records the hydration path (disjoint, sums to 350):
  96 manual + 162 aeslc_join (sha256 exact-body join-assisted pass vs the
  Enron/AESLC mirrors; the mirrors carry no intent annotations — the join
  supplies provenance + recovered subject as context) + 92 llm_zero_shot
  (deepseek-chat via OpenRouter, closed 8-class vocabulary, confidence
  threshold 0.85), 1 flagged_review. Every canonical intent class appears in
  the 10% test split.

### 6. Split integrity
- 90/10 train/test: 1,474/176. Per-stratum test shares deviate from 10%
  (0%–26.7%) — small strata often have zero test rows (flagged in
  `24_strata_imbalance_ratio.png`).

## Artifacts

### Static figures — `reports/figures/` (30 PNGs)
| # | figure | insight |
|---|---|---|
| 01–03 | type/subclass/strata/metadata heatmap | composition & coverage |
| 04–07 | text length violin, token budgets, ECDF, subclass lengths | context-window fit |
| 08–12 | CUAD presence/span/co-occurrence | annotation density & structure |
| 13–15 | MAUD frequency/answers/categories | task coverage |
| 16–19 | claim amounts, coverage, dates, subtype fill | claim supervision readiness |
| 20–22 | correspondence topic/intent/sentiment | Enron subset character |
| 23–25 | treemap, strata ratios, minority strata | imbalance risk map |
| 26–28 | temporal, source proportions, date spans | provenance & time |
| 29–30 | metadata correlation/cardinality | field structure |

> Static figure counts are nominal; regenerate with `run_all.py --phases P3`.

### Interactive figures — `reports/figures_interactive/` (18 HTML)
Plotly versions with hover/zoom: lengths, budgets, CUAD, MAUD, claims,
treemap, strata, timeline, sources, metadata.

### Tables — `reports/tables/` (17 files)
`integrity_report.json`, `strata_counts.csv`, `metadata_coverage_by_type.csv`,
`provenance_by_type.csv`, `imbalance_metrics.json`, `text_length_stats_by_type.csv`,
`token_budget_coverage.csv`, `cuad_clause_stats.csv`, `cuad_cooccurrence_matrix.csv`,
`maud_task_stats.csv`, `claim_amount_stats.csv`, `claim_field_coverage.csv`,
`correspondence_topic_intent.csv`, `strata_imbalance_detailed.csv`,
`minority_strata_report.csv`, `temporal_summary.csv`, `provenance_detailed.csv`.

## HF Interface (centralized, was llm-entity-extraction)

- `src/mailroom_eda/hf_interface.py` — Hub client: upload, sha verify, repo mgmt
- `src/mailroom_eda/dataset_export.py` — KANBAN-076 cast-safe metadata,
  KANBAN-088 JSONL safety, parquet staging, manifests, splits
- `src/mailroom_eda/docclass_uploader.py` — v7 publish, surgical card render,
  blind-label strip, leak guard
- `src/mailroom_eda/intent_backfill.py` — correspondence intent hydration
  (issue #5): cross-walk, Enron/AESLC sha256 join, constrained LLM pass,
  provenance columns
- `scripts/publish_docclass.py` / `backfill_intent.py` / `export_docclass.py` /
  `verify_hf.py` — CLIs

## ML-readiness recommendations

1. **Long docs**: merger_agreement requires 131k+ context or chunking;
   contract median fits 32k.
2. **Minority strata** (7 strata < 10 rows): consider stratification-aware
   sampling or merging (e.g., `bylaws`/`powers_of_attorney` → corporate_record
   rollup) for training stability.
3. **Zero-test strata** (14 strata): add a per-stratum test floor for the
   next corpus revision.
4. **Sentiment labels** cover all correspondence (350 rows); intent is fully
   hydrated (350/350, canonical 8-class set with `intent_source` /
   `intent_confidence` / `intent_status` provenance, issue #5 / v7) — a
   ready multi-task head target (intent + sentiment + topic).
5. **Claims block** is near-uniform in length/subtype — synthetic-data
   caveats apply (PAID only, health LOB).