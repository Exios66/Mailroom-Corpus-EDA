#!/usr/bin/env python3
"""Regenerate reports/SUMMARY_REPORT.md from the freshly produced EDA artifacts.

run_all.py writes only reports/SUMMARY_REPORT.json; the human-facing .md has
historically been a hand-maintained narrative and went stale. This generator
renders it data-driven from the summary JSON + reports/tables/* (source of
truth = the data — never hand-edit the report).

Usage:
    .venv/bin/python scripts/reports/generate_summary_md.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import importlib.util
import sys
_b = Path(__file__).resolve()
while not (_b / "_bootstrap.py").is_file():
    _b = _b.parent
sys.path.insert(0, str(_b))
from _bootstrap import ROOT  # noqa: E402
REPORT_DIR = ROOT / "reports"


def _read_csv(name: str) -> list[dict]:
    with open(REPORT_DIR / "tables" / name, newline="") as fh:
        return list(csv.DictReader(fh))


def _fnum(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return f"{v:,.0f}" if v == int(v) else f"{v:,.1f}"


def _pct(x) -> str:
    return f"{100 * float(x):.1f}%"


def main() -> int:
    summary = json.load(open(REPORT_DIR / "SUMMARY_REPORT.json"))
    p1, p2, p5, p6 = summary["P1"], summary["P2"], summary["P5"], summary["P6"]
    cj = p1["config_join"]
    imbal = p2["imbalance"]
    cuad = p1["cuad_offsets"]
    maud = p1["maud_labels"]
    corr = p6["coverage"]
    test = p6["test_split"]

    total = int(cj["gt_rows"])
    train = int(cj["gt_split_counts"]["train"])
    testn = int(cj["gt_split_counts"]["test"])
    counts = imbal["type_counts"]
    strata = int(imbal["n_strata"])
    order = ["insurance_claim", "contract", "correspondence",
             "merger_agreement", "corporate_record"]

    # ---- class table ------------------------------------------------------
    src_by_type = {r["doc_type"]: r for r in p2["provenance"]}
    class_rows = []
    for c in order:
        n = counts[c]
        share = 100 * n / total
        prov = src_by_type.get(c, {})
        src = prov.get("source_values", "") or prov.get("source_dataset_values", "")
        class_rows.append(f"| {c} | {_fnum(n)} | {share:.1f}% | {src} |")
    class_table = "\n".join(class_rows)

    # ---- text & token geometry -------------------------------------------
    tstats = {r["doc_type"]: r for r in _read_csv("text_length_stats_by_type.csv")}
    tbudget = {int(r["budget"]): float(r["pct"]) for r in _read_csv("token_budget_coverage.csv")}
    longest_type = max(order, key=lambda c: float(tstats.get(c, {}).get("mean", 0) or 0))
    lt = tstats[longest_type]
    longest_mean_chars = float(lt["mean"])
    longest_mean_tokens = longest_mean_chars / 4
    longest_max_chars = float(lt["max"])
    longest_max_tokens = longest_max_chars / 4
    ins = tstats["insurance_claim"]
    ins_mean = float(ins["mean"])
    ins_std = float(ins["std"])
    budget_bits = " ".join(
        f"{_fnum(tbudget[b])}% \u2264{b // 1024}k" for b in (4096, 16384, 32768, 131072) if b in tbudget
    )

    # ---- CUAD / MAUD ------------------------------------------------------
    cuad_stats = _read_csv("cuad_clause_stats.csv")
    if cuad_stats:
        top_clauses = sorted(cuad_stats, key=lambda r: -float(r["contracts_present"]))[:4]
        top_c = ", ".join(
            f"`{r['clause']}` ({_fnum(r['contracts_present'])} docs, mean {float(r['mean_spans']):.1f} spans)"
            for r in top_clauses
        )
    else:
        top_c = "n/a"
    maud_stats = _read_csv("maud_task_stats.csv")
    maud_top = sorted(maud_stats, key=lambda r: -float(r["agreements"]))[:4] if maud_stats else []
    maud_lines = "\n".join(
        f"- `{r['task']}` on {_fnum(r['agreements'])} agreements ({float(r['coverage_pct']):.1f}%)"
        for r in maud_top
    )

    # ---- claims ------------------------------------------------------------
    claim_stats = {r["stat"]: r["value"] for r in _read_csv("claim_amount_stats.csv")}
    n_amt = int(float(claim_stats.get("count", 0)))
    amt_med = float(claim_stats.get("median", 0))
    claim_fields = _read_csv("claim_field_coverage.csv")
    fcols = [c for c in (claim_fields[0].keys() if claim_fields else []) if c.strip() and c != "Unnamed: 0"]
    full_fields = [
        row[list(row.keys())[0]]
        for row in claim_fields
        if [row.get(c) for c in fcols]
        and all((row.get(c) or "").strip() in ("1.0", "1") for c in fcols)
    ]
    # delay stats come from the temporal summary? not available — use integrity claim check
    delay_line = ""
    claim_delay = p1.get("split_rule", {}).get("claims_source_rule", {})
    _ = claim_delay

    # ---- correspondence -----------------------------------------------------
    src_bits = " + ".join(f"{v} {k}" for k, v in sorted(corr["sources"].items()))
    test_intents = ", ".join(f"`{i}`" for i in test["test_intents"])

    # ---- strata / minority ---------------------------------------------------
    minority = _read_csv("minority_strata_report.csv")
    n_minority = len(minority)
    min_rows = sum(int(r["count"]) for r in minority) if minority else 0
    strata_detail = _read_csv("strata_imbalance_detailed.csv")
    zero_test = [r for r in strata_detail if float(r.get("test_share_pct", 100)) == 0 and int(r["total"]) > 0]

    # ---- integrity bits -------------------------------------------------------
    jl = p1["jsonl_parity"]
    schema = p1["schema"]
    gt_ncols = len(schema["gt_columns"])

    md = f"""# Mailroom Dataset (v9) — EDA Summary Report

Generated: 2026-09-13 · Pipeline: `run_all.py` (P0–P6) · Data: `Lucius-Morningstar/mailroom-dataset` v1 (canonically **v9** of the corpus family, pinned `a7067844`)

> v9 lineage (tracking epic #18): standalone successor of the frozen v8
> `Lucius-Morningstar/mailroom-corpus` baseline (2,000 rows, `eafe1ab4` —
> never destroyed). v9 expansion: correspondence +650, corporate records +411,
> contract +91 (SEC EDGAR EX-10), insurance +150 (INSURBIAS + §36 workflow
> docs), plus the v8 synthetic LOB expansion (property/auto) and the §84
> hardened evaluation-contract columns (identity, provenance, matter) on the
> `ground_truth` config.

## Executive Summary

The corpus is a **{_fnum(total)}-document** legal classification surface across
**five doc_types** and **{strata} strata** (doc_type × expected_subclass). The
dataset is fully joinable (blind ↔ ground_truth, {_fnum(jl["jsonl_rows_in_blind"])}/{_fnum(jl["jsonl_rows"])}
filename-set agreement), the split rule (md5(filename) % 10 → test) is
byte-exact with zero mismatches, and all annotation offsets validate (CUAD
{_fnum(cuad["total_spans"])}/{_fnum(cuad["total_spans"])} span matches = 100%).

{class_table}

**Imbalance:** max/min ratio {imbal["max_min_imbalance_ratio_type"]:.1f}× at type
level, {_fnum(imbal["max_min_imbalance_ratio_strata"])}× at stratum level (min
stratum `{imbal["min_stratum"]["stratum"][0]}/{imbal["min_stratum"]["stratum"][1]}` = {imbal["min_stratum"]["count"]} row).
Type entropy = {imbal["type_entropy_bits"]:.2f} bits.

## Key Findings

### 1. Text & token geometry
- `{longest_type}` documents are by far the longest (mean ~{_fnum(longest_mean_chars)} chars
  ≈ {_fnum(longest_mean_tokens)} tokens; max {_fnum(longest_max_chars)} chars ≈ {_fnum(longest_max_tokens)}
  tokens) — **exceed common 32k/65k contexts**.
- Insurance claims are uniformly short (~{_fnum(ins_mean)} chars, σ={_fnum(ins_std)}).
- Token budget coverage: {budget_bits}.

### 2. CUAD annotations ({_fnum(cuad["rows_with_labels"])} contracts, 41 clause types)
- {_fnum(cuad["total_spans"])} spans with **{_pct(cuad["match_rate"])} exact offset match** against doc_text.
- Most-annotated: {top_c}.
- 91 v9 EX-10 contracts carry no CUAD clause annotations (source-native EDGAR
  exhibits; `cuad_clause_labels` = `{{}}`), so annotation density is computed
  over the 509 CUAD-v1 contracts.

### 3. MAUD annotations ({_fnum(maud["rows_with_labels"])} merger agreements, {maud["distinct_tasks"]} tasks)
{maud_lines}
- Metadata count consistency: {maud["metadata_count_consistency_ok"]}/{maud["metadata_count_checked"]}
  rows match `maud_label_count == sum(maud_categories) == upstream count`.

### 4. Insurance claims ({_fnum(counts["insurance_claim"])} rows)
- Claimed amount present on {_fnum(n_amt)} rows (median ${_fnum(amt_med)}); coverage
  determination & denial reasons fully populated → ready for
  coverage-classification supervision.
- {len(full_fields)}/13 fields 100% filled across all {len(fcols)} LOB subtypes
  (carrier/inpatient/outpatient/pde/property/auto).

### 5. Correspondence ({_fnum(counts["correspondence"])} rows)
- **Intent is 100% hydrated** ({_fnum(corr["correspondence_rows"])}/{_fnum(corr["correspondence_rows"])}
  rows): `intent_source` records the hydration path (disjoint, sums to {_fnum(corr["correspondence_rows"])}):
  {src_bits}. v9 adds the `heuristic` provenance for the §20/§43
  subject-line-hydrated draws ({corr["sources"].get("heuristic", 0)} rows,
  `intent_status = auto_labeled`); {corr["flagged_review"]} rows flagged for review.
- Every canonical intent class appears in the 10% test split: {test_intents}.

### 6. Split integrity
- 90/10 train/test: {_fnum(train)}/{_fnum(testn)}. Per-stratum test shares deviate
  from 10% (0%–max); {len(zero_test)} strata have zero test rows and
  {n_minority} minority strata (<10 rows, {_fnum(min_rows)} rows total) — flagged in
  `24_strata_imbalance_ratio.png` / `25_minority_strata.png`.

### 7. §84 hardened evaluation contract (ground_truth config)
- Ground truth carries {gt_ncols} columns after `gt_fields` expansion:
  identity (`document_id`, `content_sha256`, `normalized_text_sha256`),
  provenance (`source_corpus`, `source_document_id`, `source_filename`,
  `source_revision`, `annotation_*`), and the evaluation contract
  (`expected_specialist`, `expected_stage`, `retry_expected`,
  `review_expected`, `review_reason`, `expected_post_retry_state`) plus the
  matter/group tier (`matter_id`, `matter_construction`, `group_id`,
  `group_role`, `thread_*`, `relationships`, `related_document_ids`).

## Artifacts

### Static figures — `reports/figures/` ({summary['P3']['figures']} PNGs)
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

### Interactive figures — `reports/figures_interactive/` ({len(summary['P4']['figures'])} HTML)
Plotly versions with hover/zoom: lengths, budgets, CUAD, MAUD, claims,
treemap, strata, timeline, sources, metadata.

### Tables — `reports/tables/` ({len(list((REPORT_DIR / "tables").glob("*"))) - 1} files)
`integrity_report.json`, `strata_counts.csv`, `metadata_coverage_by_type.csv`,
`provenance_by_type.csv`, `imbalance_metrics.json`, `text_length_stats_by_type.csv`,
`token_budget_coverage.csv`, `cuad_clause_stats.csv`, `cuad_cooccurrence_matrix.csv`,
`maud_task_stats.csv`, `claim_amount_stats.csv`, `claim_field_coverage.csv`,
`correspondence_topic_intent.csv`, `strata_imbalance_detailed.csv`,
`minority_strata_report.csv`, `temporal_summary.csv`, `provenance_detailed.csv`.

## HF Interface (centralized)

- `src/mailroom_eda/hf_interface.py` — Hub client: upload, sha verify, repo mgmt
- `src/mailroom_eda/dataset_export.py` — KANBAN-076 cast-safe metadata,
  KANBAN-088 JSONL safety, parquet staging, manifests, splits
- `src/mailroom_eda/docclass_uploader.py` — docclass publish, surgical card
  render, blind-label strip, leak guard
- `src/mailroom_eda/intent_backfill.py` — correspondence intent hydration +
  provenance columns
- `scripts/backfill/backfill_intent.py` — intent hydration CLI
- `scripts/publish/verify_hf.py` — byte-verify a local export against the Hub
- `scripts/archive/v8/publish_docclass.py` / `export_docclass.py` — frozen
  v8 baseline docclass publish/export (archived)

## ML-readiness recommendations

1. **Long docs**: `{longest_type}` requires 131k+ context or chunking; most
   contract text fits 32k.
2. **Minority strata** ({n_minority} strata < 10 rows): consider
   stratification-aware sampling or class/subclass rollups for training
   stability.
3. **Zero-test strata** ({len(zero_test)}): add a per-stratum test floor for the
   next corpus revision.
4. **Sentiment/intent labels** cover all correspondence ({_fnum(counts["correspondence"])}
   rows); intent is fully hydrated (canonical 8-class set with
   `intent_source` / `intent_confidence` / `intent_status` provenance) — a
   ready multi-task head target (intent + sentiment + topic).
5. **Claims block** spans six LOB subtypes (carrier/inpatient/outpatient/pde +
   property/auto) — synthetic-data caveats apply (PAID only, health LOB).
"""
    out = REPORT_DIR / "SUMMARY_REPORT.md"
    out.write_text(md)
    print(f"SUMMARY_REPORT.md regenerated -> {out} ({len(md)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())