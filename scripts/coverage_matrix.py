"""P1 coverage matrix (plan §40–§41, HUB-022).

The Mailroom corpus coverage report: per document class × subclass stratum —
row counts, source coverage, specialist routing, per-field ground-truth
population (§41: which extraction fields lack meaningful evaluation
coverage) — plus the §40 scenario columns (tested/regression/challenge/
multi-document), which stay at zero until the P2 (matter/grouping) and P3
(recovery) fixture families land. Reads the LOCAL snapshot only
(network-free); mirrors the EDA phase conventions (read-only, no writes
outside docs/reports/audits/).

Usage (via run_all conventions or standalone):
    python scripts/coverage_matrix.py            # write md + json
    python scripts/coverage_matrix.py --check    # print only
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG_ROOT / "src"))

from mailroom_eda.config import PARQUET_DIR  # noqa: E402
from mailroom_eda.eval_contract import SOURCE_BY_CLASS, specialist_registry  # noqa: E402

MONOREPO_ROOT = PKG_ROOT.parents[1]
OUT_DIR = MONOREPO_ROOT / "docs" / "reports" / "audits"

#: §41 field-coverage map: class → GT columns that feed the specialist's
#: expected_fields surface (matches conftest's EXTRACTION_GT_BY_CLASS +
#: the enrichment keys; empty class maps = enrichment-GT classes).
FIELD_KEYS_BY_CLASS: dict[str, tuple[str, ...]] = {
    "contract": ("cuad_clause_labels",),
    "merger_agreement": ("maud_clause_labels",),
    "correspondence": (
        "intent", "subject_matter", "keywords", "sentiment_label",
        "content_topic",
    ),
    "corporate_record": ("intent", "subject_matter", "keywords"),
    "insurance_claim": (
        "claim_number", "policy_number", "insurer", "insured_party",
        "claim_type", "date_of_loss", "date_filed", "claimed_amount",
        "adjuster", "damages_description", "coverage_determination",
        "denial_reasons", "supporting_documents",
        "intent", "subject_matter", "keywords",
    ),
}


def load_rows() -> list[dict]:
    import pandas as pd

    frames = []
    for split in ("train", "test"):
        for f in sorted((PARQUET_DIR / "ground_truth" / split).glob("*.parquet")):
            frames.append(pd.read_parquet(f))
    if not frames:
        raise SystemExit(
            f"snapshot missing at {PARQUET_DIR}/ground_truth — fetch via run_all.py P0"
        )
    return pd.concat(frames, ignore_index=True).to_dict("records")


def build(rows: list[dict]) -> dict:
    registry = specialist_registry()
    strata: Counter = Counter(
        (r["expected"], str(r.get("expected_subclass") or "")) for r in rows
    )
    field_coverage: dict[str, dict[str, int]] = {}
    for doc_class, keys in FIELD_KEYS_BY_CLASS.items():
        class_rows = [r for r in rows if r["expected"] == doc_class]
        field_coverage[doc_class] = {
            key: sum(1 for r in class_rows if str(r.get(key) or "").strip()) for key in keys
        }
    classes = sorted({r["expected"] for r in rows})
    coverage = {
        "generated_from": "local snapshot @ ground_truth config (network-free)",
        "rows_total": len(rows),
        "classes": len(classes),
        "strata": len(strata),
        "class_view": {
            doc_class: {
                "rows": sum(1 for r in rows if r["expected"] == doc_class),
                "strata": sum(1 for (c, _) in strata if c == doc_class),
                # class view stays the old class-map source (honest primary
                # source per §8) but insurance now spans three: note the
                # v8 LOB expansion in the report's source cell (HUB-028).
                "source": (
                    SOURCE_BY_CLASS.get(doc_class, "")
                    + (" (+ GNOTHEIA, BDR)" if doc_class == "insurance_claim" else "")
                ),
                "specialist": registry.get(doc_class, ""),
                "field_coverage": field_coverage[doc_class],
                # §40 scenario columns — populated by later phases:
                "tested": 0, "regression": 0, "challenge": 0,
                "multi_document": 0,
            }
            for doc_class in classes
        },
        "strata_view": [
            {"class": c, "subclass": sc, "rows": n}
            for (c, sc), n in sorted(strata.items())
        ],
        "scenario_columns_note": (
            "tested/regression/challenge/multi-document are §40 template "
            "columns at zero: the sandbox/pilot fixtures (P1) and the "
            "matter/grouping (P2) + recovery (P3) families fill them at "
            "their phases — the corpus does not overstate coverage (§14A/§53)."
        ),
    }
    return coverage


def render_md(coverage: dict) -> str:
    lines = [
        "# docclass coverage matrix (plan §40–§41)",
        "",
        f"Generated from the local pinned snapshot — {coverage['rows_total']} rows, "
        f"{coverage['classes']} classes, {coverage['strata']} class × subclass strata.",
        "",
        "## Class view (§40)",
        "",
        "| class | rows | strata | source | specialist |",
        "|---|---|---|---|---|",
    ]
    for doc_class, view in sorted(coverage["class_view"].items()):
        lines.append(
            f"| `{doc_class}` | {view['rows']} | {view['strata']} "
            f"| `{view['source']}` | `{view['specialist']}` |"
        )
    lines += ["", "## Field coverage per specialist (§41)", ""]
    for doc_class in sorted(coverage["class_view"]):
        view = coverage["class_view"][doc_class]
        fields = view.get("field_coverage") or {}
        if not fields:
            continue
        total = view["rows"]
        lines.append(f"### `{doc_class}` ({total} rows, `{view['specialist']}`)")
        lines += ["", "| field | populated | coverage |", "|---|---|---|"]
        for key, n in sorted(fields.items()):
            pct = f"{(n / total * 100):.0f}%" if total else "—"
            lines.append(f"| `{key}` | {n} | {pct} |")
        lines.append("")
    lines += [
        "## Scenario columns (§40)",
        "",
        coverage["scenario_columns_note"],
        "",
        "## Strata (§40 rows × subclass)",
        "",
        "| class | subclass | rows |",
        "|---|---|---|",
    ]
    for entry in coverage["strata_view"]:
        lines.append(f"| `{entry['class']}` | `{entry['subclass']}` | {entry['rows']} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="print only, no writes")
    args = ap.parse_args()

    rows = load_rows()
    coverage = build(rows)
    if args.check:
        print(json.dumps(coverage["class_view"], indent=2))
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "docclass_coverage_matrix.json").write_text(
        json.dumps(coverage, indent=2) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "docclass_coverage_matrix.md").write_text(render_md(coverage), encoding="utf-8")
    print(f"coverage matrix -> {OUT_DIR / 'docclass_coverage_matrix.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
