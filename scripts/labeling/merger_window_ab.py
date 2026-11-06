"""Score the merger-window A/B (plan 2026-11-06 Task 8, step 7; the cheaper three-arm form).

Arms (DeepSeek v4.1 Flash, reasoning off, ``labeling run``):

- ``merger_consideration-calibration``: located window on the 95 agreements with an expert
  MAUD Type of Consideration answer; scored here against that answer (gate floor 0.95).
- ``merger_entities-pilot`` vs ``merger_entities-pilot-fulltext``: the 13 pilot agreements,
  located window vs whole agreement. Every field whose answer differs is written to
  ``ab-diff.csv`` in the full-text directory, to be read against the agreement.

Adoption rule for merger_entities: full text only if a fact is present only in the full-text
answer in >= 2 of the 13 agreements, confirmed by reading the agreement.

Usage: ``PYTHONPATH=src .venv/bin/python scripts/labeling/merger_window_ab.py``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _bootstrap  # noqa: E402,F401

from mailroom_eda.labeling import export, operate, runner  # noqa: E402

MODEL = "deepseek/deepseek-v4.1-flash"
ROOT = runner.DEFAULT_OUT


def consideration(rows: list[dict]) -> dict:
    """Located-window Type of Consideration accuracy against the expert MAUD answers."""
    picked = runner.select(rows, "merger_consideration", "calibration")
    res = operate.gate_consideration_accuracy(picked, ROOT / "merger_consideration-calibration", 0.95, [MODEL])
    return res["models"][MODEL] | {"gold_rows": res["gold_rows"]}


def entities_diff() -> dict:
    """Fields whose located and full-text answers differ; written to ab-diff.csv."""
    def answers(d: Path) -> dict[tuple[str, str], dict]:
        return {(f["document_id"], f["field"]): f for f in export.read_csv(d / "fields.csv")}
    located = answers(ROOT / "merger_entities-pilot")
    full_dir = ROOT / "merger_entities-pilot-fulltext"
    full = answers(full_dir)
    diff = []
    for key in sorted(set(located) | set(full)):
        a, b = located.get(key, {}), full.get(key, {})
        va = (a.get("model_a_status"), a.get("model_a_value"))
        vb = (b.get("model_a_status"), b.get("model_a_value"))
        if va != vb:
            diff.append({"document_id": key[0], "field": key[1],
                         "located_status": va[0] or "", "located_value": va[1] or "",
                         "located_evidence": a.get("model_a_evidence", ""),
                         "full_status": vb[0] or "", "full_value": vb[1] or "",
                         "full_evidence": b.get("model_a_evidence", ""),
                         "only_in_full": "", "reviewer_note": ""})
    cols = list(diff[0]) if diff else ["document_id", "field"]
    export.write_csv(full_dir / "ab-diff.csv", cols, diff)
    only_full = {d["document_id"] for d in diff if d["full_status"] == "present" and d["located_status"] != "present"}
    return {"fields_compared": len(set(located) | set(full)), "fields_differ": len(diff),
            "agreements_with_a_full_only_value": len(only_full), "diff": str(full_dir / "ab-diff.csv")}


def main() -> int:
    rows = runner.load_stage(runner.DEFAULT_STAGE)
    print(json.dumps({"consideration_located": consideration(rows), "entities": entities_diff()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
