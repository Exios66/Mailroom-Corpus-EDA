"""P1 coverage matrix (plan §40–§41, HUB-022; issue #28).

The Mailroom corpus coverage report: per document class × subclass stratum —
row counts, source coverage, specialist routing, per-field ground-truth
population (§41: which extraction fields lack meaningful evaluation
coverage) — plus the §40 scenario columns (tested/regression/challenge/
multi-document), which stay at zero until the P2 (matter/grouping) and P3
(recovery) fixture families land. Reads the LOCAL snapshot only
(network-free); mirrors the EDA phase conventions (read-only, no writes
outside docs/reports/audits/).

Issue #28 (epic #27): §41 coverage is reported over **eligible rows only**.
Every unpopulated cell is classified as ``schema_documented_absence`` (the
v8_build/v9 conformance law documents the field empty on this row — e.g.
``adjuster`` on CMS/GNOTHEIA/INSURBIAS rows, ``denial_reasons`` on non-denied
claims) or ``genuine_gap`` (the field should be populated but is not — e.g.
``cuad_clause_labels`` on the 91 EDGAR EX-10 contracts, any
``supporting_documents`` row outside a documented rule). Documented absences
are tallied (``absence_classification`` per class/field + the
``absence_rules`` verification section) but never counted as gaps, so the
matrix no longer mis-scopes closure work on by-design-empty rows.

Usage (via run_all conventions or standalone):
    python scripts/audit/coverage_matrix.py            # write md + json
    python scripts/audit/coverage_matrix.py --check    # print only
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import importlib.util
import sys
_b = Path(__file__).resolve()
while not (_b / "_bootstrap.py").is_file():
    _b = _b.parent
sys.path.insert(0, str(_b))
from _bootstrap import ROOT  # noqa: E402

from mailroom_eda.config import PARQUET_DIR  # noqa: E402
from mailroom_eda.eval_contract import SOURCE_BY_CLASS, specialist_registry  # noqa: E402


def _audits_dir() -> Path:
    """docs/reports/audits — this repo's own docs tree (standalone layout),
    else the nearest ancestor carrying it (Digital-Mailroom monorepo layout,
    where the artifacts consolidate at the repo root)."""
    for root in (ROOT, *ROOT.parents):
        cand = root / "docs" / "reports" / "audits"
        if cand.exists():
            return cand
    return ROOT / "docs" / "reports" / "audits"


OUT_DIR = _audits_dir()

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

#: Documented-absence rules (issue #28): (class, field) -> rule. A rule cites
#: the code that documents the absence and supplies the predicate deciding
#: whether a row's unpopulated cell is a schema-documented absence. Where a
#: (class, field) pair has NO rule here, every unpopulated cell is a genuine
#: gap — the issue's discipline: derive rules from code + schema, verify
#: against the snapshot, never invent.
#:
#: Verified against the v9 snapshot (3,302 rows) by build(): the rule's
#: ``documented_absence`` count must reproduce the data (see the JSON's
#: ``absence_rules`` section, which also reports zero populated rows matching
#: an absence predicate — conformance-clean).
ABSENCE_RULES: dict[tuple[str, str], dict[str, Any]] = {
    ("insurance_claim", "adjuster"): {
        "rule": (
            "v8_build.py ALLOWED_EMPTY = {'adjuster'} + module docstring "
            "('' only where the schema documents absence, e.g. adjuster on "
            "property/CMS rows); v9_build.py ALLOWED_EMPTY['insurance_claim'] "
            "= {'adjuster'}; conform_rows: 'the only documented scalar "
            "allowance is insurance_claim.adjuster (source-absent on CMS / "
            "GNOTHEIA / INSURBIAS)' — only the BDR auto rows carry adjuster "
            "pseudonyms (v8_build.py _auto_row: _pseudo_adjuster(claim_id))."
        ),
        # source-absent on every insurance subclass EXCEPT the BDR auto draw
        # (metadata.source_dataset mirrors v8_build.BDR_AUTO_REPO).
        "is_documented_absence": lambda r: (
            str((r.get("metadata") or {}).get("source_dataset") or "")
            != "bdr-ai-org/insurance-motor-claims-decision-v1"
        ),
    },
    ("insurance_claim", "denial_reasons"): {
        "rule": (
            "v8_build.py _auto_row: reasons = _auto_denial_reasons(r) if "
            "determination == 'denied' else [] — denial reasons exist only on "
            "denied claims; non-denied rows ship '[]' (a complete no-items "
            "answer per v9_build.py LIST_GT_FIELDS: 'a valid JSON array is "
            "the COMPLETE answer — [] means no items (honest), never a "
            "missing value')."
        ),
        # the only rows eligible for denial reasons are denied determinations
        "is_documented_absence": lambda r: (
            str(r.get("coverage_determination") or "").strip().lower() != "denied"
        ),
    },
}


def _is_documented_absence(doc_class: str, key: str, row: dict) -> bool:
    """True when a documented conformance rule says this row's field is empty
    (issue #28). No rule => False (absence is then a genuine gap)."""
    rule = ABSENCE_RULES.get((doc_class, key))
    if rule is None:
        return False
    return bool(rule["is_documented_absence"](row))


def _classify(doc_class: str, key: str, row: dict) -> str:
    """Classify one (class, field) cell for §41 coverage (issue #28):

    - ``populated`` — carries ground truth,
    - ``schema_documented_absence`` — the v8_build/v9 conformance law says
      the field is empty on this row,
    - ``genuine_gap`` — should be populated but is not (no documented rule
      covers the absence).
    """
    if _populated(row.get(key)):
        return "populated"
    if _is_documented_absence(doc_class, key, row):
        return "schema_documented_absence"
    return "genuine_gap"


def load_rows() -> list[dict]:
    import pandas as pd

    from mailroom_eda.docclass_uploader import GT_SCALAR_KEYS
    from mailroom_eda.download import _expand_gt_fields

    def _read(cfg: str) -> pd.DataFrame:
        frames = []
        for split in ("train", "test"):
            for f in sorted((PARQUET_DIR / cfg / split).glob("*.parquet")):
                frames.append(pd.read_parquet(f))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    df = _read("ground_truth")
    if df.empty:
        raise SystemExit(
            f"snapshot missing at {PARQUET_DIR}/ground_truth — fetch via run_all.py P0"
        )
    if "gt_fields" in df.columns:
        # v9 schema: label keys live inside the nested gt_fields JSON
        # (sparse per-class, 12–29 keys). Expand to flat keys so §41 field
        # coverage reads real values; keep canonical top-level matter
        # columns (relationships/related_document_ids mirror in gt_fields).
        df = _expand_gt_fields(df)
        df = df.loc[:, ~df.columns.duplicated(keep="first")]
        df[list(GT_SCALAR_KEYS)] = df[list(GT_SCALAR_KEYS)].fillna("")
    blind = _read("default")
    if not blind.empty:
        # metadata (source_dataset & co.) lives in the blind config — join it
        # so the absence rules can key on the actual source (issue #28).
        df = df.merge(blind[["filename", "metadata"]], on="filename", how="left")
    return df.to_dict("records")


def _populated(v) -> bool:
    """v9 complete-GT convention: '' is absent and the JSON-encoded no-item
    markers ``'[]'`` / ``'{}'`` carry no ground truth (the 91 EDGAR EX-10
    contracts ship ``cuad_clause_labels = '{}'`` — no CUAD annotation)."""
    if v is None:
        return False
    if isinstance(v, str):
        v = v.strip()
        if not v or v in ("[]", "{}"):
            return False
    return v not in ("", [], {})


def build(rows: list[dict]) -> dict:
    registry = specialist_registry()
    strata: Counter = Counter(
        (r["expected"], str(r.get("expected_subclass") or "")) for r in rows
    )
    field_coverage: dict[str, dict[str, int]] = {}
    # issue #28: per (class, field) classification of every unpopulated cell.
    absence_classification: dict[str, dict[str, dict[str, int]]] = {}
    for doc_class, keys in FIELD_KEYS_BY_CLASS.items():
        class_rows = [r for r in rows if r["expected"] == doc_class]
        field_coverage[doc_class] = {
            key: sum(1 for r in class_rows if _populated(r.get(key))) for key in keys
        }
        absence_classification[doc_class] = {}
        for key in keys:
            tally = Counter(_classify(doc_class, key, r) for r in class_rows)
            populated = tally["populated"]
            documented = tally["schema_documented_absence"]
            genuine = tally["genuine_gap"]
            eligible = populated + genuine
            absence_classification[doc_class][key] = {
                "populated": populated,
                "eligible": eligible,
                "documented_absence": documented,
                "genuine_gap": genuine,
                "coverage_pct": round(populated / eligible * 100) if eligible else 0,
            }
    # rule verification: each documented rule is re-checked against the live
    # snapshot — documented_absence reproduces the count the predicate claims,
    # and zero populated rows may match an absence predicate (a conformance
    # anomaly would show up here, not as silent misclassification).
    absence_rules: dict[str, dict[str, Any]] = {}
    for (doc_class, key), rule in ABSENCE_RULES.items():
        class_rows = [r for r in rows if r["expected"] == doc_class]
        absence_rules[f"{doc_class}.{key}"] = {
            "rule": rule["rule"],
            "documented_absence": sum(
                1 for r in class_rows
                if not _populated(r.get(key)) and rule["is_documented_absence"](r)
            ),
            "populated_matching_absence_predicate": sum(
                1 for r in class_rows
                if _populated(r.get(key)) and rule["is_documented_absence"](r)
            ),
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
                # source per §8) but insurance now spans four: note the
                # v8 LOB expansion + the v9 INSURBIAS draw in the report's
                # source cell (HUB-028 / issue #3).
                "source": (
                    SOURCE_BY_CLASS.get(doc_class, "")
                    + (
                        " (+ GNOTHEIA, BDR, INSURBIAS)"
                        if doc_class == "insurance_claim" else ""
                    )
                ),
                "specialist": registry.get(doc_class, ""),
                "field_coverage": field_coverage[doc_class],
                # issue #28: eligibility-based absence classification — this is
                # the honest §41 coverage basis (see coverage_basis_note).
                "absence_classification": absence_classification[doc_class],
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
        "absence_rules": absence_rules,
        "coverage_basis_note": (
            "§41 coverage is reported over ELIGIBLE rows only (populated / "
            "eligible). Unpopulated cells are classified "
            "schema_documented_absence — the v8_build/v9 conformance law says "
            "the field is empty on this row (e.g. adjuster on CMS/GNOTHEIA/"
            "INSURBIAS rows, denial_reasons on non-denied claims) — or "
            "genuine_gap — the field should be populated but is not (e.g. "
            "cuad_clause_labels on the 91 EDGAR EX-10 contracts, the 150 "
            "INSURBIAS rows shipping no supporting_documents). Documented "
            "absences are tallied in absence_classification / absence_rules "
            "but never counted as gaps (issue #28, epic #27)."
        ),
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
    lines += [
        "Coverage is reported over **eligible rows only** (populated / "
        "eligible). Unpopulated cells are classified "
        "`schema_documented_absence` — the v8_build/v9 conformance law says "
        "the field is empty on this row (e.g. `adjuster` on CMS/GNOTHEIA/"
        "INSURBIAS rows, `denial_reasons` on non-denied claims) — or "
        "`genuine_gap` — the field should be populated but is not (e.g. "
        "`cuad_clause_labels` on the 91 EDGAR EX-10 contracts, the 150 "
        "INSURBIAS rows shipping no `supporting_documents`). Documented "
        "absences are tallied but never counted as gaps (issue #28).",
        "",
    ]
    for doc_class in sorted(coverage["class_view"]):
        view = coverage["class_view"][doc_class]
        fields = view.get("field_coverage") or {}
        if not fields:
            continue
        total = view["rows"]
        ac = view.get("absence_classification") or {}
        lines.append(f"### `{doc_class}` ({total} rows, `{view['specialist']}`)")
        lines += [
            "",
            "| field | populated | eligible | documented-absent | genuine gap | coverage |",
            "|---|---|---|---|---|---|",
        ]
        for key in sorted(fields):
            a = ac.get(key) or {}
            n = a.get("populated", 0)
            eligible = a.get("eligible", 0)
            doc_abs = a.get("documented_absence", 0)
            gap = a.get("genuine_gap", 0)
            pct = f"{a.get('coverage_pct', 0)}%" if eligible else "—"
            lines.append(
                f"| `{key}` | {n} | {eligible} | {doc_abs} | {gap} | {pct} |"
            )
        lines.append("")
    absence_rules = coverage.get("absence_rules") or {}
    if absence_rules:
        lines += [
            "## Documented absences (§v8_build/v9 conformance law)",
            "",
            "Rows classified `schema_documented_absence` are **not coverage "
            "gaps**: the conformance law documents the field empty on them. "
            "Rules (with code citations; full text in the JSON):",
            "",
        ]
        for key in sorted(absence_rules):
            rule = absence_rules[key]
            lines.append(
                f"- **`{key}`** — {rule['documented_absence']} rows classified "
                "documented-absence; "
                f"{rule['populated_matching_absence_predicate']} populated rows "
                "match the absence predicate (0 = conformance-clean). "
                f"{rule['rule']}"
            )
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
