"""Audit v9.1 ground truth outside golden CUAD and MAUD labels.

Writes ``docs/reports/audits/gt_backfill_quality.md`` plus the JSON summary
and the chunked spend queue. ``--check`` prints the summary and exits 1 when
any structure or format error is present.

    python scripts/audit/gt_quality.py
    python scripts/audit/gt_quality.py --check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_b = Path(__file__).resolve()
while not (_b / "_bootstrap.py").is_file():
    _b = _b.parent
sys.path.insert(0, str(_b))
from _bootstrap import ROOT  # noqa: E402

from mailroom_eda.gt_quality import (  # noqa: E402
    audit_rows,
    backfill_targets,
    chunk_plan,
    load_snapshot,
    render_markdown,
    summarize,
)


def _audits_dir() -> Path:
    for root in (ROOT, *ROOT.parents):
        cand = root / "docs" / "reports" / "audits"
        if cand.exists():
            return cand
    return ROOT / "docs" / "reports" / "audits"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="print only; exit 1 on format errors")
    args = parser.parse_args()

    rows = load_snapshot()
    findings = audit_rows(rows)
    summary = summarize(findings, n_rows=len(rows))
    targets = backfill_targets(rows, findings)
    plan = chunk_plan(targets)
    payload = {**summary, "queue_docs": len(targets), "chunks": plan}
    errors = int((summary.get("by_severity") or {}).get("error") or 0)
    if args.check:
        print(json.dumps(payload, indent=2))
        return 1 if errors else 0

    out = _audits_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "gt_backfill_quality.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (out / "gt_backfill_queue.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in targets),
        encoding="utf-8",
    )
    (out / "gt_backfill_quality.md").write_text(
        render_markdown(summary, targets, plan), encoding="utf-8"
    )
    print(
        f"v9.1 quality -> {out / 'gt_backfill_quality.md'} "
        f"errors={errors} queue={len(targets)} chunks={len(plan)}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
