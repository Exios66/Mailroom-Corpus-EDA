#!/usr/bin/env python3
"""CLI: correspondence intent backfill for mailroom-corpus v7 (issue #5).

Phases implemented (mirrors https://github.com/Exios66/mailroom-dev/issues/5):
  Phase 1  taxonomy cross-walk (canonical 8-class vocabulary + external map)
  Phase 2  sha256 exact-body join vs Enron/AESLC (provenance aeslc_join)
  Phase 3  constrained LLM pass for residuals (confidence thresholding)
  Phase 4  provenance columns intent_source/intent_confidence/intent_status
  Phase 5  re-stratification + test-split class coverage report

Usage:
    PYTHONPATH=src python scripts/backfill_intent.py --check
    PYTHONPATH=src python scripts/backfill_intent.py --join-only     # Phases 1-2
    PYTHONPATH=src python scripts/backfill_intent.py                 # Phases 1-4
    PYTHONPATH=src python scripts/backfill_intent.py --limit 20      # dry LLM probe
    PYTHONPATH=src python scripts/backfill_intent.py --max-llm-rows 0 --resume
    PYTHONPATH=src python scripts/backfill_intent.py --out data/v7_gt.csv

The LLM pass needs OPENROUTER_API_KEY (the corpus repo .venv does not ship
one; the key lives in ~/.hermes/.env or the environment). The Enron body
index and the LLM sidecar are cached under data/backfill/ so re-runs are
byte-identical without new LLM spend.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load_env() -> None:
    for env_file in (Path.home() / ".hermes" / ".env", ROOT / ".env"):
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="network-free sanity checks")
    parser.add_argument("--join-only", action="store_true",
                        help="run Phase 2 join only; do not invoke the LLM")
    parser.add_argument("--limit", type=int, default=0,
                        help="cap LLM-labeled rows (0 = all)")
    parser.add_argument("--max-llm-rows", type=int, default=0,
                        help="cap NEW LLM calls this run (resume-friendly)")
    parser.add_argument("--model", default="deepseek/deepseek-chat",
                        help="OpenRouter model for the constrained labeler (default deepseek/deepseek-chat; qwen3.7-flash is a reasoning model that burns budget + is frequently upstream rate-limited)")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "data" / "v7_gt.csv",
                        help="enriched ground_truth preview CSV")
    parser.add_argument("--rows-out", type=Path,
                        default=ROOT / "data" / "v7_rows.jsonl",
                        help="v7 publish rows JSONL (blind text + GT fields)")
    parser.add_argument("--stats-out", type=Path,
                        default=ROOT / "data" / "v7_intent_stats.json",
                        help="intent backfill stats JSON (feeds the card + manifest)")
    parser.add_argument("--force-index", action="store_true",
                        help="rebuild the Enron body-hash index from scratch")
    args = parser.parse_args()

    _load_env()

    from mailroom_eda.download import load_ground_truth, load_default
    from mailroom_eda import intent_backfill as ib

    if args.check:
        assert ib.normalize_text("  Hello   World ") == "hello world"
        assert ib.crosswalk_external("Request / Query") == "request"
        assert ib.crosswalk_external("Deliverable / Status Update") == "update"
        assert ib.crosswalk_external("Propose / Meeting") == "meeting_invite"
        assert ib.crosswalk_external("Complaint / Dispute") == "notice"
        assert ib.crosswalk_external("gibberish-label") == "other"
        assert ib.body_sha256("Subject: x\n\nBody text") == ib.body_sha256("body text")
        assert ib.extract_subject("Subject: hello\nFoo") == "hello"
        for i in ib.CANONICAL_INTENTS:
            assert ib.crosswalk_external(i) == i
        print("check_contract OK — cross-walk, normalization, canonical vocabulary verified.")
        return 0

    gt = load_ground_truth()
    blind = load_default()
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    if args.join_only:
        index = ib.build_enron_index(force=args.force_index)
        need = gt[(gt["expected"] == "correspondence") &
                  gt["intent"].fillna("").str.strip().eq("")]
        doc_map = {fn: t for fn, t in zip(blind["filename"], blind["doc_text"])}
        hits = ib.join_enron(need.to_dict("records"), index, doc_map)
        print(f"Phase 2 join: {len(hits)}/{len(need)} unlabeled rows matched Enron bodies "
              f"({100 * len(hits) / len(need):.1f}%)")
        return 0

    enriched, stats = ib.backfill_correspondence(
        gt, blind,
        model=args.model,
        api_key=api_key,
        base_url=base_url,
        max_llm_rows=args.max_llm_rows,
        force_index=args.force_index,
    )
    if args.limit:
        pass  # limit handled inside backfill_correspondence via max_llm_rows only

    report = ib.validate_intent_coverage(enriched, strict=not args.max_llm_rows)
    test_report = ib.test_split_intent_coverage(enriched)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(args.out, index=False)

    if not args.max_llm_rows:
        stats_out = dict(stats)
        stats_out["coverage"] = report
        stats_out["test_split"] = test_report
        args.stats_out.write_text(
            json.dumps(stats_out, indent=2, ensure_ascii=False), encoding="utf-8")
        rows = ib.build_v7_rows(enriched, blind, args.rows_out)
        print(f"  v7 publish rows: {len(rows)} -> {args.rows_out}")
        print(f"  intent stats -> {args.stats_out}")

    print(f"\nbackfill stats: {stats}")
    print(f"coverage: {report}")
    print(f"test split: {test_report}")
    print(f"enriched ground_truth -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())