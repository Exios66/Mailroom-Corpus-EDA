#!/usr/bin/env python3
"""Run the complete Mailroom docclass EDA pipeline (P0-P5).

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
    print("P3: static visualizations (27 figures + tables)")
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
        })
    staged = dataset_export.stage_parquet(rows, ROOT / "data" / "staging")
    staged_serializable = {f"{a}/{b}": n for (a, b), n in staged.items()}
    print(f"  staged parquet: {staged_serializable}")
    return {"staged": staged_serializable}


PHASES = {
    "P0": p0_download,
    "P1": p1_integrity,
    "P2": p2_composition,
    "P3": p3_visualizations,
    "P4": p4_interactive,
    "P5": p5_export,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phases", default="P0,P1,P2,P3,P4,P5",
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
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w") as fh:
        json.dump(results, fh, indent=2, default=str)
    print(f"\nSummary written -> {summary_path}")

    failed = [p for p, r in results.items() if "error" in r]
    if failed:
        print(f"FAILED phases: {failed}")
        return 1
    print(f"All {len(results)} phases completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())