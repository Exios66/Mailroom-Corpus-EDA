"""Per-claim-type smoke report for claim_checklist chunks.

Compares each document's resolved claim_checklist against the regex-derived
expected-category matrix (what the document actually prints), so systematic
per-stratum misses surface before chunks are gated:

    PYTHONPATH=src python scripts/labeling/claim_checklist_smoke.py \
        docs/reports/audits/v10_labels/8a_claim_checklist-pilot
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "data/v9_4/stage/ground_truth_hardened.jsonl"

EXPECTED: dict[str, tuple]|dict[str, Any] = {
    "Coverage Determination": lambda t: bool(re.search(
        r'(?i)coverage determination|adjudicated and paid|stated outcome', t)
        or re.search(r'(?i)insurance covers|strictly adheres|covered under the plan', t)),
    "Deductible": lambda t: bool(re.search(r'(?i)deductible|\(coins[^)]*ded\s+\$|\bded\s+\$\d', t)),
    "Amount Consistency": lambda t: (
        bool(re.search(r'(?i)allowed\s+\$\d+.*paid\s+\$\d+', t))
        or bool(re.search(r'(?i)claimed amount', t)) and bool(re.search(r'(?i)repair estimate', t))
        or bool(re.search(r'(?i)total drug cost', t)) and bool(re.search(r'(?i)patient pay', t))
        or bool(re.search(r'(?i)unit price', t)) and bool(re.search(r'(?i)total (excl\.?)? vat', t))
        or bool(re.search(r'(?i)billed', t)) and bool(re.search(r'(?i)allowed', t))),
    "Exclusions Cited": lambda t: bool(re.search(r'(?i)exclu|not covered|not includ|not claimed', t)),
    "Policy Limits": lambda t: bool(re.search(r'(?i)policy maximum|policy limit|indemnity period', t)),
    "Timely Notice": lambda t: bool(re.search(
        r'(?i)date of report|date of notice|reported to the insurer|within (two|24|48) hours', t)),
    "Proof Of Loss": lambda t: bool(re.search(
        r'(?i)proof of loss|i am submitting|submitted as requested|is supported by|report filed|documents? attached', t)),
}


def items(cell: str) -> list[str]:
    """Items of a checklist value cell (json, json-as-string, or csv-s quoted list)."""
    v = (cell or "").strip()
    if not v: return []
    try:
        x = json.loads(v)
        return x if isinstance(x, list) else [x]
    except Exception:
        s = v[1:-1] if v.startswith("[") and v.endswith("]") else v
        parts = re.split(r'"\s*,\s*"', s)
        return [p.strip().strip('"').replace('\\"', '"') for p in parts if p.strip().strip('"')]


def main(out_dir: Path) -> int:
    rows = [json.loads(l) for l in STAGE.open() if l.strip()]
    gt = {r["document_id"]: r for r in rows}
    fields = list(__import__("csv").DictReader((out_dir / "fields.csv").open()))
    per = defaultdict(Counter)
    missing: list[tuple[str, str]] = []
    for f in fields:
        r = gt[f["document_id"]]
        sub, text = r["expected_subclass"], r["doc_text"]
        have = {it.split(":", 1)[0].strip().lower() for it in items(f["final_value"])}
        for cat in EXPECTED:
            if EXPECTED[cat](text) and cat.lower() not in have:
                per[sub][f"miss:{cat}"] += 1
                missing.append((f["document_id"], cat))
        per[sub]["docs"] += 1
        if f["decision"] == "accept":
            per[sub]["accept"] += 1
        elif f["decision"] == "adjudicate":
            per[sub]["adjudicate"] += 1
    for sub in sorted(per):
        n = per[sub]["docs"]
        stats = {k.replace("miss:", ""): v for k, v in per[sub].items() if k.startswith("miss:")}
        print(f"{sub:12s} n={n:4d} accept={per[sub].get('accept',0):4d} "
              f"adjudicate={per[sub].get('adjudicate',0):3d}  "
              f"expected-misses={dict(stats) or '{}'}")
    if missing:
        print(f"\n{len(missing)} expected-category misses (doc: spec → category):")
        for did, cat in missing[:20]:
            r = gt[did]
            print(f"  {did} {r['expected_subclass']:10s} {cat}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")))
