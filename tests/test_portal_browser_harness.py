"""The browser harness builds the real portal before any browser test starts.

This is the fast, no-browser guard: if a packet, registry or campaign change
breaks the harness, CI fails here instead of in a headless Chrome job.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_real_portal_harness_builds_and_loads_its_packets(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "portal_browser_harness.py"),
         "--root", str(tmp_path / "harness"), "--check"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-4000:]
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary["schema"] == "mailroom-review-browser-harness/v1"
    assert summary["records"] == 35 and summary["documents"] == 11
    assert summary["launch_enabled"] is True and summary["checkpoint_enabled"] is True
    assert {"claim_checklist", "signatories", "jurisdiction", "proposed_new_field"} <= set(summary["fields"])
    assert summary["batches"] == ["", "7a", "8a", "8b"]
    packet = Path(tmp_path / "harness" / "labels" / "batch-0001" / "browser-corporate-review-data.json")
    assert packet.exists() and packet.read_text(encoding="utf-8")
