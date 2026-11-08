#!/usr/bin/env .venv/bin/python
"""Field-type-aware grading of quality_probe_report.json for the corporate task.

The probe's oracle is byte-only, so an ISO date answer ("1994-03-10") false-flags
against printed text ("March 10, 1994"). This grader re-checks date-typed
violations against printed variants (false-positive vs REAL); non-date
violations stay real candidates, since names/numbers must not paraphrase.

Usage:
  .venv/bin/python autoresearch/loop-*/grade_corporate.py                # grade
  .venv/bin/python autoresearch/loop-*/grade_corporate.py --self-check   # free
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LABELS = REPO / "docs/reports/audits/v10_labels/10.1_corporate-gold-additions/corporate-labels.jsonl"
DOCS = REPO / "data/v10/kl3m/gold/docs"
REPORT = Path(__file__).parent / "quality_probe_report.json"
#: date-typed corporate fields (ISO output, printed source) from fields.yaml
DATE_FIELDS = {"effective_date", "expiration_date", "filing_date"}

MONTHS = {"01": "January", "02": "February", "03": "March", "04": "April",
          "05": "May", "06": "June", "07": "July", "08": "August",
          "09": "September", "10": "October", "11": "November", "12": "December"}


def norm(s: str) -> str:
    s = re.sub(r"\s+([,.])", r"\1", s)  # "December        ," -> "December,"
    return re.sub(r"\s+", " ", s).lower().strip()


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def date_variants(iso: str) -> list[str]:
    """Printed spellings the fixtures actually use; the gold self-check pins the set."""
    parts = iso.split("-")
    if len(parts) != 3:
        return []
    y, m, d = parts
    mon, day = MONTHS.get(m), int(d) if d.isdigit() else 0
    if not mon or not (1 <= day <= 31):
        return []
    o = ordinal(day)
    return [f"{mon} {day}, {y}", f"{mon[:3]} {day}, {y}",      # March 23, 1994 · Mar 10, 2022
            f"{mon} {day} {y}", f"{mon[:3]} {day} {y}",        # FILED: Jul 10 2022
            f"{mon}. {day}, {y}", f"{mon} {o}, {y}",
            f"{o} day of {mon}, {y}", f"{o} day of {mon} {y}",  # 23rd day of March, 1994
            f"the {o} day of {mon}, {y}", f"the {o} day of {mon} {y}",
            f"this {o} day of {mon}, {y}",
            f"the {day} day of {mon}, {y}",
            f"{day} day of {mon}, {y}", f"{day} day of {mon} {y}",  # 21 day of March, 2002
            f"{o} {mon} {y}",                                   # 10th August 2023
            f"{day} {mon} {y}"]


def field_value(value) -> tuple:
    """labels store {'value','presence','evidence'}; the probe stores plain strings."""
    if isinstance(value, dict):
        return value.get("value"), value.get("presence") == "populated"
    return value, value not in (None, "", [])


def regrade(doc: str, violation: str) -> tuple[str, str]:
    """(field, verdict): date answers that print nowhere are still REAL."""
    field, _, value = violation.partition(": ")
    if field in DATE_FIELDS:
        return field, "date_prints" if any(norm(v) in doc for v in date_variants(value)) else "date_real"
    return field, "real"


def gold_rows() -> list[dict]:
    return [json.loads(l) for l in LABELS.open(encoding="utf-8")]


def self_check() -> int:
    """Offline: every gold date must print in its sha-verified doc."""
    fail = 0
    checks = Counter()
    for rec in gold_rows():
        doc = norm((DOCS / rec["doc_file"].removeprefix("docs/")).read_text(encoding="utf-8"))
        for field in sorted(DATE_FIELDS & rec["fields"].keys()):
            value, populated = field_value(rec["fields"][field])
            if not populated:
                checks[f"{field}:absent"] += 1
                continue
            prints = any(norm(v) in doc for v in date_variants(str(value)))
            checks[f"{field}:{'prints' if prints else 'MISS'}"] += 1
            if not prints:
                fail += 1
                print(f"  MISS {field}={value} :: {rec['doc_file'][:64]}")
    print("self-check:", dict(checks))
    return fail


def grade() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    docs = {r["doc_file"].removeprefix("docs/"):
            norm((DOCS / r["doc_file"].removeprefix("docs/")).read_text(encoding="utf-8"))
            for r in gold_rows()}
    verdicts = Counter()
    by_field_model: dict[tuple[str, str], Counter] = defaultdict(Counter)
    real_examples: dict[str, list] = defaultdict(list)
    for entry in report.get("answers", []):
        if entry.get("error") or "violations" not in entry:
            verdicts[entry.get("error", "?")[:60]] += 1
            continue
        for v in entry["violations"]:
            field, verdict = regrade(docs.get(entry["document_id"], ""), v)
            verdicts[verdict] += 1
            by_field_model[(field, entry["model"])][verdict] += 1
            if verdict != "date_prints":
                real_examples[v].append(entry["document_id"][-16:])
    print("verdicts:", dict(verdicts.most_common()))
    for (field, model), c in sorted(by_field_model.items()):
        real = c["real"] + c["date_real"]
        print(f"{field:24s} {model.split('/')[-1]:28s} real={real:3d} date_false_positive={c['date_prints']}")
        for v, ids in real_examples.items():
            if v.startswith(field + ":"):
                print(f"    {v[:96]}  [{ids[0]}]")
    if report.get("sha_mismatches"):
        print("SHA MISMATCHES:", report["sha_mismatches"])
    if any("parsed" in e for e in report.get("answers", [])):
        print("(parsed captured: run the gold-agreement pass on a future report)")
    else:
        print("(no parsed answers in this report — quoting oracle only; that was the plan)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    return self_check() if args.self_check else grade()


if __name__ == "__main__":
    raise SystemExit(main())
