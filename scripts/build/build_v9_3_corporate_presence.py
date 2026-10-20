#!/usr/bin/env python3
"""CLI: stage the v9.3 corporate presence-layer revision (labels-only).

Label-only revision over the published ``corporate-labels-v9.2.1`` branch
(plan: ``docs/plans/v9.3-corporate-presence-revision.md``; module:
``src/mailroom_eda/v9_3_revision.py``). The EDA-side relabel equivalents re-
derive the 450 corporate rows' ``gt_presence`` maps byte-identically from
the v9.2 base (precedence honesty: only facts the v9.2.1 relabel already
derived — no invented labels), the manifest adopts ``labels_version``
alongside the dataset ``version``, and the card documents the presence-code
vocabulary verbatim from the dojo docs.

This is a STAGING revision: ``--publish`` deliberately raises. Do not push
tags or the HF Hub from here.

Usage:
    .venv/bin/python scripts/build/build_v9_3_corporate_presence.py                 # stage under data/v9_3/stage
    .venv/bin/python scripts/build/build_v9_3_corporate_presence.py --base-path /tmp/gt92/hardened.jsonl
    .venv/bin/python scripts/audit/verify_v9_3_conformance.py                       # formal gates report
"""
from __future__ import annotations

import sys
from pathlib import Path

_b = Path(__file__).resolve()
while not (_b / "_bootstrap.py").is_file():
    _b = _b.parent
sys.path.insert(0, str(_b))
from _bootstrap import ROOT  # noqa: E402,F401

from mailroom_eda.v9_3_revision import *  # noqa: E402,F401,F403
from mailroom_eda import v9_3_revision  # noqa: E402


def main() -> int:
    return v9_3_revision.main()


if __name__ == "__main__":
    raise SystemExit(main())
