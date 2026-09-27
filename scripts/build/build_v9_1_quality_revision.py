#!/usr/bin/env python3
"""CLI: build (and optionally publish) the v9.1 quality revision.

Addresses the LLM-safe-formatting portion of the open mailroom-issues#196
epic — no new document sourcing, no Modal/GPU/LLM credentials required.
Downloads the LIVE (unpinned) Lucius-Morningstar/mailroom-dataset tip,
strips weak-indirect metadata signals (B1), publishes closed-vocabulary
row-level GT presence codes (B2), and publishes context-window budget
bands (B4). Stage-only by default; --publish uploads the verified tree.

Usage:
    .venv/bin/python scripts/build/build_v9_1_quality_revision.py               # stage under data/v9_1/stage
    .venv/bin/python scripts/build/build_v9_1_quality_revision.py --publish     # stage + publish
"""
from __future__ import annotations

from pathlib import Path

import sys
_b = Path(__file__).resolve()
while not (_b / "_bootstrap.py").is_file():
    _b = _b.parent
sys.path.insert(0, str(_b))
from _bootstrap import ROOT  # noqa: E402

from mailroom_eda.v9_1_revision import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
