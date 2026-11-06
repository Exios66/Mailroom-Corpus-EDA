#!/usr/bin/env python3
"""CLI: guarded publish of the v10 stage (dry run unless --tag, --initials and --confirm).

See ``src/mailroom_eda/publish_v10.py`` for the guards.
"""
from __future__ import annotations

import sys
from pathlib import Path

_b = Path(__file__).resolve()
while not (_b / "_bootstrap.py").is_file():
    _b = _b.parent
sys.path.insert(0, str(_b))
from _bootstrap import ROOT  # noqa: E402,F401

from mailroom_eda.publish_v10 import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
