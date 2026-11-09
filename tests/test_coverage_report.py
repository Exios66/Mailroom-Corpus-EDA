"""The coverage report is part of the local gate, and it must really measure code.

This is intentionally an integration test: it runs the shipped coverage tool in
a subprocess against one review test file, then checks the JSON contains real
covered statements for ``review_page.py``. A future interpreter/dependency that
breaks the pre-import workaround fails here instead of leaving maintainers with
an unverifiable coverage claim.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("coverage")

ROOT = Path(__file__).resolve().parents[1]


def test_the_coverage_tool_measures_real_line_and_branch_coverage(tmp_path):
    out = tmp_path / "coverage.json"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ci" / "coverage_report.py"),
         "--tests", "tests/test_labeling_review_page.py", "--out", str(out)],
        check=False, cwd=ROOT, env=env, capture_output=True, text=True, timeout=240)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["totals"]["num_statements"] > 1000
    page = next(entry for name, entry in report["files"].items() if name.endswith("review_page.py"))
    assert page["summary"]["percent_covered"] > 50.0
    assert "TOTAL" in result.stdout and "JSON:" in result.stdout
