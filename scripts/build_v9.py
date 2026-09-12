#!/usr/bin/env python3
"""CLI: build (and optionally publish) the v9 standalone mailroom-dataset.

Developed in the dedicated Mailroom-Corpus-EDA repo (issue #3). Stage-only by
default; --publish creates Lucius-Morningstar/mailroom-dataset and uploads
the verified tree via the centralized hf_interface.

Usage:
    .venv/bin/python scripts/build_v9.py                 # stage under data/v9/stage
    .venv/bin/python scripts/build_v9.py --publish       # stage + publish v1
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mailroom_eda.v9_build import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())