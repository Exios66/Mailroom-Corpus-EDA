#!/usr/bin/env python
"""Measure real line and branch coverage without relying on GitHub Actions.

Why this exists: on this Python 3.13 + numpy build, ``coverage run -m pytest``
fails while a lazy C extension is imported under the tracer ("cannot load module
more than once per process"). Pre-importing the heavy dependency tree before the
tracer starts is the workaround, and this script packages it so coverage is a
first-class, repeatable local command rather than an unavailable CI feature.

Usage::

    PYTHONPATH=src .venv/bin/python scripts/ci/coverage_report.py
    PYTHONPATH=src .venv/bin/python scripts/ci/coverage_report.py \
        --tests tests/test_labeling_review_page.py --fail-under 70

The JSON report is written under ``.cache/quality/`` by default (git-ignored)
and a compact per-module table is printed. The exit code is pytest's, then 1
when ``--fail-under`` is not met.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

# Import these before coverage starts. Their C extensions cannot be initialized
# under the tracer on this interpreter; after they are cached, tests import them
# normally. Missing optional packages are harmless (the suite skips them).
PREIMPORT = (
    "numpy", "numpy.fft", "numpy.linalg", "numpy.random", "numpy.polynomial",
    "numpy.ma", "numpy.testing",
    "pandas", "pyarrow", "scipy", "sklearn", "matplotlib", "seaborn", "statsmodels",
    "PIL", "openpyxl", "httpx", "datasets", "tokenizers", "tiktoken", "polars",
    "yaml",
    "mailroom_eda", "mailroom_eda.labeling.review_portal",
    "mailroom_eda.labeling.review_export", "mailroom_eda.labeling.review_packet",
    "mailroom_eda.labeling.review_corporate", "mailroom_eda.labeling.review_production",
    "mailroom_eda.labeling.launch", "mailroom_eda.labeling.runner",
    "mailroom_eda.labeling.operate", "mailroom_eda.labeling.checkpoint",
    "mailroom_eda.labeling.local_backup",
)


def preimport() -> list[str]:
    """Warm the dependency tree; return the names that could not be imported."""
    failed = []
    for name in PREIMPORT:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 - an optional dependency must not stop coverage
            failed.append(name)
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests", nargs="+", default=["tests/"],
                        help="pytest arguments (default: tests/)")
    parser.add_argument("--source", default="mailroom_eda.labeling",
                        help="coverage source package (default: mailroom_eda.labeling)")
    parser.add_argument("--out", type=Path, default=ROOT / ".cache" / "quality" / "coverage.json",
                        help="JSON report path (default: .cache/quality/coverage.json)")
    parser.add_argument("--fail-under", type=float, default=None,
                        help="exit 1 when total coverage is below this percentage")
    parser.add_argument("--no-branch", action="store_true", help="line coverage only")
    args = parser.parse_args(argv)

    failed = preimport()
    import coverage
    import pytest

    args.out.parent.mkdir(parents=True, exist_ok=True)
    data_file = args.out.parent / ".coverage"
    cov = coverage.Coverage(source=[args.source], branch=not args.no_branch,
                            data_file=str(data_file))
    cov.start()
    try:
        rc = pytest.main([*args.tests, "-q"])
    finally:
        cov.stop()
        cov.save()

    json_path = args.out
    cov.json_report(outfile=str(json_path), pretty_print=True)
    report = json.loads(json_path.read_text(encoding="utf-8"))
    totals = report.get("totals", {})
    total = float(totals.get("percent_covered", 0.0))
    rows = []
    for filename, entry in (report.get("files") or {}).items():
        summary = entry.get("summary") or {}
        if not summary.get("num_statements"):
            continue
        try:
            display = Path(filename).resolve().relative_to(ROOT).as_posix()
        except ValueError:
            display = Path(filename).as_posix()
        if not display.startswith("src/"):
            continue
        rows.append((float(summary.get("percent_covered", 0.0)), display,
                     summary.get("num_statements", 0), summary.get("missing_lines", 0),
                     summary.get("num_branches", 0), summary.get("num_partial_branches", 0)))
    rows.sort()
    print("\nCoverage (statements + branches):")
    print(f"{'cover':>6}  {'stmts':>5}  {'miss':>5}  {'br':>4}  {'part':>4}  module")
    for percent, name, statements, missing, branches, partial in rows:
        print(f"{percent:6.1f}%  {statements:5d}  {missing:5d}  {branches:4d}  {partial:4d}  {name}")
    print(f"\nTOTAL {total:.2f}%  ({totals.get('covered_lines', 0)}/"
          f"{totals.get('num_statements', 0)} statements, "
          f"{totals.get('num_branches', 0)} branches)")
    print(f"JSON: {json_path}")
    print(f"tests: {' '.join(args.tests)}")
    print(f"generated: {datetime.now(UTC).isoformat()}")
    if failed:
        print(f"pre-import skipped: {', '.join(failed)}")
    if rc != 0:
        return int(rc)
    if args.fail_under is not None and total < args.fail_under:
        print(f"FAIL: coverage {total:.2f}% is below --fail-under {args.fail_under:.2f}%")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
