#!/usr/bin/env python3
"""CLI: export a docclass JSONL to cast-safe JSONL + parquet staging (no Hub).

Prepares the exact tree that publish_docclass.py uploads, so staging can be
reviewed/verified locally before any HF write.

Usage:
    python scripts/export_docclass.py --v6 data/datasets/docclass_merged_v6.jsonl --out /tmp/stage
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mailroom_eda.dataset_export import (  # noqa: E402
    export_to_jsonl,
    normalize_metadata_rows,
    stage_parquet,
)
from mailroom_eda.docclass_uploader import load_v6, strip_blind_labels  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v6", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rows = load_v6(args.v6)
    strip_blind_labels(rows)
    normalize_metadata_rows(rows)

    counts = stage_parquet(rows, args.out)
    jl = export_to_jsonl(rows, args.out / "docclass_merged_v6.jsonl")
    print(f"parquet staged: {dict(counts)}")
    print(f"jsonl staged: {jl['rows']} rows -> {jl['path']} (sha {jl['sha256'][:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())