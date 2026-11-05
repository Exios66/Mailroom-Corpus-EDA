#!/usr/bin/env python3
"""Formal v9.4 gates, re-derived from the staged bytes in ``data/v9_4/stage``.

(a) row set: no duplicate content_sha256, count = v9.3 minus removed, md5 split rule
(b) zero drift: survivors byte-identical to v9.3 except the documented fill allowlist
(c) no nan/None/null placeholder cells in gt_fields
(d) presence key-set law on every row, no genuine_gap
(e) every populated EX-10 CUAD map re-verifies verbatim at its offsets
(f) ground_truth parquet == JSONL; dataset_info features == parquet columns
(g) manifest sha table recomputes from the staged bytes
(h) calibration against expert labels (PENDING blocks publishing, not staging)

Exit 0 when (a)-(g) pass; ``--require-publish-ready`` also requires (h).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_b = Path(__file__).resolve()
while not (_b / "_bootstrap.py").is_file():
    _b = _b.parent
sys.path.insert(0, str(_b))
from _bootstrap import ROOT  # noqa: E402,F401

from mailroom_eda.v9_4_revision import BASE_STAGE_DIR, STAGE_DIR_DEFAULT, verify_stage  # noqa: E402


def main() -> int:
    """Print each gate verdict and readiness; exit 1 only if ``--require-publish-ready`` is set and (h) is not PASS.

    A failed conformance gate raises ``AssertionError`` from :func:`verify_stage`.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage-dir", type=Path, default=STAGE_DIR_DEFAULT)
    ap.add_argument("--base-dir", type=Path, default=BASE_STAGE_DIR)
    ap.add_argument("--require-publish-ready", action="store_true")
    args = ap.parse_args()
    result = verify_stage(args.stage_dir, args.base_dir)
    for name, verdict in result["gates"].items():
        print(f"{name:20s} {verdict}")
    print(f"publish_ready        {result['publish_ready']}")
    if args.require_publish_ready and not result["publish_ready"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
