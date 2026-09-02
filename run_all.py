#!/usr/bin/env python3
"""Run the complete Mailroom docclass EDA pipeline (P0-P6).

Usage:
    python run_all.py                 # everything
    python run_all.py --phases P1 P2  # subset
    python run_all.py --no-interactive
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from mailroom_eda.config import ensure_dirs, setup_matplotlib  # noqa: E402
from mailroom_eda.download import download_corpus, validate_against_manifest  # noqa: E402
from mailroom_eda import integrity, composition  # noqa: E402


def phase_timer(fn):
    def wrapper(*args, **kwargs):
        t0 = time.time()
        result = fn(*args, **kwargs)
        print(f"  [{fn.__name__}] {time.time() - t0:.1f}s")
        return result
    return wrapper


@phase_timer
def p0_download() -> dict:
    print("P0: corpus download & manifest validation")
    download_corpus()
    report = validate_against_manifest()
    print(f"  manifest rows_total: {report.get('manifest_rows_total')}")
    return report


@phase_timer
def p1_integrity() -> dict:
    print("P1: structural integrity & provenance audit")
    return integrity.run()


@phase_timer
def p2_composition() -> dict:
    print("P2: corpus composition (strata, imbalance, provenance)")
    return composition.run()


@phase_timer
def p3_visualizations() -> dict:
    print("P3: static visualizations (30 figures + tables)")
    from mailroom_eda import visualizations
    return visualizations.run()


@phase_timer
def p4_interactive() -> dict:
    print("P4: interactive HTML visualizations")
    from mailroom_eda import visualizations_interactive
    return visualizations_interactive.run()


@phase_timer
def p5_export() -> dict:
    print("P5: dataset export helpers (JSONL + parquet staging)")
    from mailroom_eda import dataset_export
    from mailroom_eda.download import load_default, load_ground_truth

    blind = load_default()
    gt = load_ground_truth()
    gt_keys = (
        "label_evidence", "content_topic", "topic_evidence",
        "sentiment_score", "sentiment_label", "sentiment_evidence",
        "claim_number", "policy_number", "insurer", "insured_party",
        "claim_type", "date_of_loss", "date_filed", "claimed_amount",
        "adjuster", "damages_description", "coverage_determination",
        "denial_reasons", "supporting_documents",
        "cuad_clause_labels", "maud_clause_labels",
        "intent", "subject_matter", "keywords",
        "intent_source", "intent_confidence", "intent_status",
    )
    rows = []
    for _, r in gt.iterrows():
        b = blind[blind["filename"] == r["filename"]].iloc[0]
        rows.append({
            "filename": r["filename"],
            "doc_text": b["doc_text"],
            "prompt": b.get("prompt", ""),
            "expected": r["expected"],
            "expected_subclass": r["expected_subclass"],
            "split": r["split"],
            "metadata": b["metadata"],
            "gt_fields": {k: ("" if pd.isna(r.get(k)) else r.get(k)) for k in gt_keys},
        })
    staged = dataset_export.stage_parquet(rows, ROOT / "data" / "staging")
    staged_serializable = {f"{a}/{b}": n for (a, b), n in staged.items()}
    print(f"  staged parquet: {staged_serializable}")
    return {"staged": staged_serializable}


@phase_timer
def p6_intent_coverage() -> dict:
    print("P6: correspondence intent coverage & provenance audit (issue #5)")
    from mailroom_eda import intent_backfill as ib
    from mailroom_eda.download import load_ground_truth

    gt = load_ground_truth()
    if "intent_source" not in gt.columns:
        report = {
            "status": "pre-backfill",
            "note": "ground_truth carries no intent provenance columns yet — "
                    "run scripts/backfill_intent.py and publish v7, then re-run.",
            "intent_covered": int(gt.loc[gt["expected"] == "correspondence",
                                         "intent"].fillna("").str.strip().ne("").sum()),
        }
        print(f"  {report}")
        return report
    report = ib.validate_intent_coverage(gt)
    test_report = ib.test_split_intent_coverage(gt)
    print(f"  coverage: {report}")
    print(f"  test split: {test_report}")
    return {"coverage": report, "test_split": test_report}


PHASES = {
    "P0": p0_download,
    "P1": p1_integrity,
    "P2": p2_composition,
    "P3": p3_visualizations,
    "P4": p4_interactive,
    "P5": p5_export,
    "P6": p6_intent_coverage,
}

# Canonical full-pipeline phase set — the summary-write gate compares against
# this fixed snapshot, never against a (potentially patched) PHASES mapping.
FULL_PIPELINE = tuple(sorted(PHASES))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phases", default="P0,P1,P2,P3,P4,P5,P6",
                        help="comma-separated phases to run (default: all)")
    parser.add_argument("--no-interactive", action="store_true",
                        help="skip P4 interactive HTML figures")
    args = parser.parse_args()

    ensure_dirs()
    setup_matplotlib()

    wanted = [p.strip().upper() for p in args.phases.split(",") if p.strip()]
    if args.no_interactive:
        wanted = [p for p in wanted if p != "P4"]

    results = {}
    for phase in wanted:
        if phase not in PHASES:
            print(f"SKIP unknown phase: {phase}")
            continue
        try:
            results[phase] = PHASES[phase]()
        except Exception as exc:
            print(f"ERROR phase {phase}: {exc}")
            results[phase] = {"error": str(exc)}

    # Summary
    summary_path = ROOT / "reports" / "SUMMARY_REPORT.json"

    def _rel(p):
        if isinstance(p, Path):
            try:
                return str(p.relative_to(ROOT))
            except ValueError:
                return str(p)
        return p

    results = json.loads(json.dumps(results, default=str))

    def _walk(d):
        if isinstance(d, dict):
            return {k: _walk(v) for k, v in d.items()}
        if isinstance(d, list):
            return [_walk(v) for v in d]
        if isinstance(d, str) and d.startswith(str(ROOT)):
            return _rel(Path(d))
        return d

    results = _walk(results)

    # Summary — HUB-009: SUMMARY_REPORT.json is written ONLY by a complete,
    # error-free full-pipeline run (all seven phases). Subset runs — and
    # --no-interactive, whose summary would be missing the P4 section — leave
    # the existing full-corpus summary untouched; per-phase results print to
    # stdout only.
    failed = [p for p, r in results.items() if "error" in r]
    full_run = tuple(sorted(results)) == FULL_PIPELINE and not failed
    if full_run:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(summary_path, "w") as fh:
            json.dump(results, fh, indent=2, default=str)
        print(f"\nSummary written -> {summary_path}")
    elif failed:
        print(f"\nSummary NOT written — phase errors: {failed} "
              f"(existing summary left untouched)")
    else:
        print(f"\nSummary NOT written — subset run ({', '.join(sorted(results))}); "
              f"only full-pipeline runs (P0-P6) write the summary")

    if failed:
        print(f"FAILED phases: {failed}")
        return 1
    print(f"All {len(results)} phases completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())