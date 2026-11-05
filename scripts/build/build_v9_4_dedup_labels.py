#!/usr/bin/env python3
"""CLI: stage the v9.4 dedup + label-completion revision.

Input is the v9.3 stage (run ``scripts/build/build_v9_3_corporate_presence.py``
first). Plan: ``docs/plans/v9.4-dedup-label-completion.md``; module:
``src/mailroom_eda/v9_4_revision.py``. STAGING only: ``--publish`` raises.

Usage:
    .venv/bin/python scripts/build/build_v9_4_dedup_labels.py                      # dedup + scrub + verified EX-10 labels
    .venv/bin/python scripts/build/build_v9_4_dedup_labels.py --no-cuad-journal    # dedup + scrub only
    .venv/bin/python scripts/build/build_v9_4_dedup_labels.py \\
        --cuad-calibration-journal data/v9_4/llm_raw/cuad_calibration.jsonl \\
        --run-intent-pass                                                          # after the Modal runbook
    .venv/bin/python scripts/audit/verify_v9_4_conformance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_b = Path(__file__).resolve()
while not (_b / "_bootstrap.py").is_file():
    _b = _b.parent
sys.path.insert(0, str(_b))
from _bootstrap import ROOT  # noqa: E402,F401

from mailroom_eda import v9_4_revision  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(v9_4_revision.main())
