#!/usr/bin/env python3
"""CLI: publish the docclass-merged corpus to the HuggingFace Hub.

Centralized replacement for llm-entity-extraction's publish_docclass_v6.py —
stage parquet configs (default/ground_truth), render the README card, write
the manifest, and upload via the mailroom_eda modules.

Usage:
    python scripts/publish_docclass.py --v6 data/datasets/docclass_merged_v6.jsonl --stage /tmp/v6_stage
    python scripts/publish_docclass.py --v6 data/datasets/docclass_merged_v6.jsonl --stage /tmp/v6_stage --publish
    python scripts/publish_docclass.py --v6 data/datasets/docclass_merged_v6.jsonl --files-dir data/original_files --publish
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mailroom_eda.docclass_uploader import load_v6, publish_docclass  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v6", type=Path, required=True,
                        help="merged docclass JSONL (v6 shape)")
    parser.add_argument("--stage", type=Path, required=True,
                        help="staging directory for the Hub tree")
    parser.add_argument("--files-dir", type=Path, default=None,
                        help="original files staging root (files/ + mapping)")
    parser.add_argument("--commit-message", default="",
                        help="HF commit message (auto-generated if empty)")
    parser.add_argument("--publish", action="store_true",
                        help="upload to the Hub (requires HF_TOKEN)")
    args = parser.parse_args()

    if args.publish and not args.commit_message:
        parser.error("--publish requires --commit-message (HF history hygiene)")

    rows = load_v6(args.v6)
    print(f"Loaded {len(rows)} v6 rows")
    result = publish_docclass(
        rows,
        stage_dir=args.stage,
        files_dir=args.files_dir,
        commit_message=args.commit_message,
        publish=args.publish,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())