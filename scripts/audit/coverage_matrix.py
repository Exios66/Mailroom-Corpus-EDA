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
from mailroom_eda.gt_presence import (  # noqa: E402
    ABSENCE_RULES,
    FIELD_KEYS_BY_CLASS,
    PENDING_ANNOTATION_RULES,
    classify_field,
    is_populated,
)


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

#: §41 field-coverage map (class -> GT columns) and the documented-absence /
#: pending-annotation rules (issue #28/#30) are now single-sourced in
#: ``mailroom_eda.gt_presence`` — the same module the Hub publish path
#: (``scripts/build/build_v9_1_quality_revision.py``) uses to compute the
#: row-level ``gt_presence`` column, so the audit and the shipped data can
#: never drift apart (mailroom-issues#196 Phase B2). ``classify_field``
#: returns one of ``populated`` / ``schema_documented_absence`` /
#: ``pending_annotation`` / ``genuine_gap`` for a ``(class, field)`` cell
#: that IS class-relevant (this CLI never asks about a not-applicable cell,
#: since it only iterates ``FIELD_KEYS_BY_CLASS[doc_class]``).


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
        # doc_text rides the same join: the insurance_claim.supporting_documents
        # absence rule (issue #29) inspects the INSURBIAS narrative to decide
        # whether a row is a documented absence (bare accident report).
        df = df.merge(
            blind[["filename", "doc_text", "metadata"]], on="filename", how="left"
        )
    return df.to_dict("records")


def build(rows: list[dict]) -> dict:
    registry = specialist_registry()
    strata: Counter = Counter(
        (r["expected"], str(r.get("expected_subclass") or "")) for r in rows
    )
    field_coverage: dict[str, dict[str, int]] = {}
    # issue #28/#30: per (class, field) classification of every unpopulated
    # cell — populated / schema_documented_absence / pending_annotation /
    # genuine_gap, via the single-sourced mailroom_eda.gt_presence rules.
    absence_classification: dict[str, dict[str, dict[str, int]]] = {}
    for doc_class, keys in FIELD_KEYS_BY_CLASS.items():
        class_rows = [r for r in rows if r["expected"] == doc_class]
        field_coverage[doc_class] = {
            key: sum(1 for r in class_rows if is_populated(r.get(key))) for key in keys
        }
        absence_classification[doc_class] = {}
        for key in keys:
            tally = Counter(classify_field(doc_class, key, r) for r in class_rows)
            populated = tally["populated"]
            documented = tally["schema_documented_absence"]
            pending = tally["pending_annotation"]
            genuine = tally["genuine_gap"]
            # "eligible" = rows where the field should eventually carry GT:
            # populated + genuine (undiagnosed) + pending (diagnosed, but
            # still a real gap pending a dependency) — documented absences
            # are excluded, they are absent by design and never a coverage
            # target (issue #28's original discipline, unchanged).
            eligible = populated + genuine + pending
            absence_classification[doc_class][key] = {
                "populated": populated,
                "eligible": eligible,
                "documented_absence": documented,
                "pending_annotation": pending,
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
                if not is_populated(r.get(key)) and rule["is_documented_absence"](r)
            ),
            "populated_matching_absence_predicate": sum(
                1 for r in class_rows
                if is_populated(r.get(key)) and rule["is_documented_absence"](r)
            ),
        }
    # issue #30: pending-annotation rules get the same live re-verification —
    # a genuine, catalogued gap blocked on a dependency, distinct from a
    # by-design documented absence (mailroom-issues#196 Phase B2).
    pending_rules: dict[str, dict[str, Any]] = {}
    for (doc_class, key), rule in PENDING_ANNOTATION_RULES.items():
        class_rows = [r for r in rows if r["expected"] == doc_class]
        pending_rules[f"{doc_class}.{key}"] = {
            "rule": rule["rule"],
            "pending_annotation": sum(
                1 for r in class_rows
                if not is_populated(r.get(key)) and rule["is_pending"](r)
            ),
            "populated_matching_pending_predicate": sum(
                1 for r in class_rows
                if is_populated(r.get(key)) and rule["is_pending"](r)
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
        "pending_rules": pending_rules,
        "coverage_basis_note": (
            "§41 coverage is reported over ELIGIBLE rows only (populated / "
            "eligible, where eligible = populated + genuine_gap + "
            "pending_annotation). Unpopulated cells are classified "
            "schema_documented_absence — the v8_build/v9 conformance law says "
            "the field is empty on this row (e.g. adjuster on CMS/GNOTHEIA/"
            "INSURBIAS rows, denial_reasons on non-denied claims, "
            "supporting_documents on the 6 INSURBIAS bare-accident narratives "
            "that reference no supporting-document feature — issue #29) and "
            "excluded from eligible entirely (absent by design, never a "
            "coverage target) — or pending_annotation — a genuine, "
            "catalogued gap blocked on a specific dependency "
            "(cuad_clause_labels on the 91 SEC EDGAR EX-10 contracts — issue "
            "#30: the LLM clause pass could not run on 2026-09-13, no "
            "working provider credential; see pending_rules for the "
            "re-verified count) — or genuine_gap — the field should be "
            "populated and no rule explains the absence. Documented absences "
            "are tallied in absence_classification / absence_rules but never "
            "counted as gaps; pending annotations ARE counted against "
            "coverage_pct (honest: they close only when the dependency "
            "clears, mailroom-issues#196 Phase B2) (issue #28, epic #27)."
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
        "eligible, eligible = populated + genuine_gap + pending_annotation). "
        "Unpopulated cells are classified "
        "`schema_documented_absence` — the v8_build/v9 conformance law says "
        "the field is empty on this row (e.g. `adjuster` on CMS/GNOTHEIA/"
        "INSURBIAS rows, `denial_reasons` on non-denied claims, "
        "`supporting_documents` on the 6 INSURBIAS bare-accident narratives "
        "that reference no supporting-document feature — issue #29), "
        "excluded from eligible (absent by design, never a coverage target) "
        "— or `pending_annotation` — a genuine, catalogued gap blocked on a "
        "dependency (`cuad_clause_labels` on the 91 SEC EDGAR EX-10 "
        "contracts — issue #30: the LLM clause pass could not run on "
        "2026-09-13, no working provider credential; see the pending_rules "
        "entry), counted AGAINST coverage_pct — or `genuine_gap` — the "
        "field should be populated and no rule explains the absence "
        "(mailroom-issues#196 Phase B2: after issues #28/#29 the corpus "
        "reports zero undiagnosed genuine gaps; the EX-10 clause hole is now "
        "honestly `pending_annotation`, not silently folded into "
        "`schema_documented_absence`). Documented absences are tallied but "
        "never counted as gaps (issue #28).",
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
            "| field | populated | eligible | documented-absent | pending-annotation | genuine gap | coverage |",
            "|---|---|---|---|---|---|---|",
        ]
        for key in sorted(fields):
            a = ac.get(key) or {}
            n = a.get("populated", 0)
            eligible = a.get("eligible", 0)
            doc_abs = a.get("documented_absence", 0)
            pending = a.get("pending_annotation", 0)
            gap = a.get("genuine_gap", 0)
            pct = f"{a.get('coverage_pct', 0)}%" if eligible else "—"
            lines.append(
                f"| `{key}` | {n} | {eligible} | {doc_abs} | {pending} | {gap} | {pct} |"
            )
        lines.append("")
    absence_rules = coverage.get("absence_rules") or {}
    if absence_rules:
        lines += [
            "## Documented absences (§v8_build/v9 conformance law)",
            "",
            "Rows classified `schema_documented_absence` are **not coverage "
            "gaps**: the conformance law documents the field empty on them "
            "by design — they never close. "
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
    pending_rules = coverage.get("pending_rules") or {}
    if pending_rules:
        lines += [
            "## Pending annotations (catalogued gaps blocked on a dependency)",
            "",
            "Rows classified `pending_annotation` ARE coverage gaps — they "
            "count against `coverage_pct` above — but the gap is diagnosed "
            "and catalogued (never a silent `{}`), and closes as soon as the "
            "named dependency clears (mailroom-issues#196 Phase B2):",
            "",
        ]
        for key in sorted(pending_rules):
            rule = pending_rules[key]
            lines.append(
                f"- **`{key}`** — {rule['pending_annotation']} rows classified "
                "pending-annotation; "
                f"{rule['populated_matching_pending_predicate']} populated rows "
                "match the pending predicate (0 = conformance-clean). "
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
