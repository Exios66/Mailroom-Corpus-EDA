#!/usr/bin/env python3
"""v10 conformance gates 1-13 (plan Part C3); see ``mailroom_eda.v10_conformance``.

    python scripts/audit/verify_v10_conformance.py --stage data/v10/stage --gates 1-8,12,13   # chunk 0
    python scripts/audit/verify_v10_conformance.py --stage data/v10/stage                      # chunk 11

Prints a JSON report and exits 1 when any gate fails. Offline; runs the same under -O.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mailroom_eda.v10_conformance import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
