"""P4 corpus-expansion priorities generator (plan §89, §40–41; HUB-022).

Reads the committed coverage matrix
(``docs/reports/audits/docclass_coverage_matrix.json`` — the §40–41
instrument) and emits the PRIORITIZED expansion backlog at
``docs/reports/audits/docclass_expansion_priorities.{md,json}``.

§89's discipline is explicit: **do not optimize for raw row count**. Each
expansion family is therefore scored by evaluation value, not volume:

- ``field_gaps`` — GT fields at zero or partial coverage (from the matrix's
  class_view.field_coverage vs. eval_contract.expected_gt_fields).
- ``scenario_zero`` — the §40 scenario columns (tested/regression/challenge/
  multi_document) that are still zero for the class.
- ``scarcity`` — classes far below the corpus median row count (a 39-row
  corporate_record class cannot absorb stratified eval noise).

Priority = high when a family attacks a zero-coverage field or an
all-zero scenario axis for a load-bearing class; medium for partial gaps;
low for format/long-tail work whose value is real but not blocking.

Deterministic: sorted output, no timestamps, committed artifacts are the
canonical bytes. Regenerate with:

    uv run python packages/mailroom-corpus-eda/scripts/expansion_priorities.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mailroom_eda.eval_contract import expected_gt_fields  # noqa: E402

REPO_ROOT = ROOT.parent.parent
MATRIX_PATH = REPO_ROOT / "docs" / "reports" / "audits" / "docclass_coverage_matrix.json"
OUT_MD = REPO_ROOT / "docs" / "reports" / "audits" / "docclass_expansion_priorities.md"
OUT_JSON = REPO_ROOT / "docs" / "reports" / "audits" / "docclass_expansion_priorities.json"

SCENARIO_COLUMNS = ("tested", "regression", "challenge", "multi_document")

PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

#: §89 expansion families → the classes/axes they attack. ``rationale``
#: states the Mailroom need (the plan's "expand according to Mailroom needs").
EXPANSION_FAMILIES: tuple[dict[str, Any], ...] = (
    {
        "family": "corporate_records",
        "title": "Corporate records (§89: corporate records)",
        "classes": ("corporate_record",),
        "rationale": (
            "Smallest live class (39 rows vs 2,000 corpus-wide) carrying the "
            "sharpest field gap (intent 0%) — too few rows for stratified "
            "eval noise to average out; expansion is load-bearing for the "
            "corporate_records_specialist route."
        ),
    },
    {
        "family": "grouping_scenarios",
        "title": "Grouping scenarios (§89: grouping scenarios; §14)",
        "classes": tuple(),
        "rationale": (
            "multi_document is zero for every class. §14A verified the honest "
            "source-field baseline (19/350 subject-thread rows; header threads "
            "structurally absent) — real multi-document behavior is only "
            "reachable via the synthetic bundle scaffold (bundles.py) or "
            "family-sampled expansion. Highest-leverage axis in the corpus."
        ),
    },
    {
        "family": "adversarial_ambiguous",
        "title": "Adversarial/ambiguous cases (§89; §30, §68)",
        "classes": tuple(),
        "rationale": (
            "challenge is zero for every class and the §68/§70/§72A fixture "
            "builders (fixtures.py) are scaffold-only until populated with "
            "real adversarial documents — the sorter's 'know when not to "
            "guess' behavior (§67) is currently untested against reality."
        ),
    },
    {
        "family": "correspondence_contexts",
        "title": "Correspondence contexts (§89: correspondence contexts)",
        "classes": ("correspondence",),
        "rationale": (
            "intent is 100% (v7 hydration) but subject_matter/keywords sit at "
            "27% — the purpose-GT axis the correspondence_specialist is "
            "evaluated on is two-thirds dark; contexts (notices, demands, "
            "threads) target exactly that."
        ),
    },
    {
        "family": "insurance_workflow",
        "title": "Insurance workflow documents (§89: insurance workflow)",
        "classes": ("insurance_claim",),
        "rationale": (
            "Largest class (600 rows) with adjuster at 0% and partial "
            "coverage on denial_reasons/supporting_documents — the workflow "
            "fields that distinguish a claim decision from a claim intake."
        ),
    },
    {
        "family": "legal_document_families",
        "title": "Legal document families (§89: legal document families)",
        "classes": ("contract",),
        "rationale": (
            "Clause coverage is at 100% (cuad labels) and contract rows are "
            "not scarce — the family axis (contract+amendment+exhibit) is "
            "owned by grouping_scenarios, where the bundle scaffold must be "
            "paired with family-sampled anchors; growth here refines anchor "
            "diversity for those grouping evals, it closes no field gap."
        ),
    },
    {
        "family": "contract_subclasses",
        "title": "Contract subclasses (§89: contract subclasses)",
        "classes": ("contract",),
        "rationale": (
            "509 rows across 26 strata is the deepest subclass spread in the "
            "corpus; growth here is refinement, not gap-closing — valuable "
            "for routing confusion matrices, not blocking."
        ),
    },
    {
        "family": "merger_documents",
        "title": "Merger documents (§89: merger documents)",
        "classes": ("merger_agreement",),
        "rationale": (
            "Second-smallest class (152 rows vs a 350-row median class) and "
            "single-source (MAUD): the objective scarcity rule drives the "
            "priority — the merger route of contracts_specialist needs "
            "statistical depth, and format diversity within the class is "
            "the natural way to add it."
        ),
    },
    {
        "family": "format_diversity",
        "title": "Format diversity (§89: format diversity; §58 ingestion)",
        "classes": tuple(),
        "priority_override": "low",
        "rationale": (
            "The P3 ingestion failure stage needs genuinely unreadable/"
            "non-text sources (scans, broken encodings) that the current "
            "text-native corpus cannot supply; valuable once P3 fixtures "
            "graduate to the published suite — sequencing, not a corpus-gap "
            "judgment (explicit override, the only one in this backlog)."
        ),
    },
)


def _field_gaps(class_view: dict[str, Any], doc_class: str) -> dict[str, Any]:
    """Zero/partial GT fields for a class from the matrix's coverage facts."""
    covered = class_view.get("field_coverage") or {}
    rows = int(class_view.get("rows") or 0)
    zero, partial = [], []
    for field in expected_gt_fields(doc_class):
        count = int(covered.get(field, 0))
        if count == 0:
            zero.append(field)
        elif count < rows:
            partial.append(f"{field} {count}/{rows}")
    return {"zero": zero, "partial": partial}


def _priority(item: dict[str, Any], spec: dict[str, Any]) -> str:
    # Corpus-level families (no class scope) own the §40 scenario axes —
    # those are all-zero corpus-wide and are exactly what these families
    # attack, so they are high by construction unless sequencing says
    # otherwise (documented override, never silently computed).
    if "priority_override" in spec:
        return spec["priority_override"]
    if not item["classes"]:
        return "high"
    # Class-scoped families score on field gaps + scarcity. The per-class
    # scenario columns are all zero today; letting them force HIGH here
    # would flatten every family (the overstatement §14A warns about) —
    # they are reported as evidence but owned by the corpus-level families.
    if item["field_gaps"]["zero"] or item["scarcity"]:
        return "high"
    if item["field_gaps"]["partial"]:
        return "medium"
    return "low"


def build_priorities() -> dict[str, Any]:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    class_view = matrix["class_view"]
    median_rows = sorted(v["rows"] for v in class_view.values())[len(class_view) // 2]

    items = []
    for spec in EXPANSION_FAMILIES:
        gaps: dict[str, Any] = {"zero": [], "partial": []}
        scenario_zero: list[str] = []
        scarcity: list[str] = []
        for doc_class in spec["classes"]:
            cv = class_view.get(doc_class) or {}
            g = _field_gaps(cv, doc_class)
            gaps["zero"] += [f"{doc_class}.{f}" for f in g["zero"]]
            gaps["partial"] += [f"{doc_class}.{f}" for f in g["partial"]]
            scenario_zero += [
                f"{doc_class}.{col}" for col in SCENARIO_COLUMNS
                if int(cv.get(col) or 0) == 0
            ]
            if int(cv.get("rows") or 0) < median_rows // 2:
                scarcity.append(f"{doc_class} rows={cv.get('rows')} (median {median_rows})")
        item = {
            "family": spec["family"],
            "title": spec["title"],
            "priority": "",
            "rationale": spec["rationale"],
            "classes": list(spec["classes"]),
            "field_gaps": gaps,
            "scenario_zero": scenario_zero,
            "scarcity": scarcity,
        }
        item["priority"] = _priority(item, spec)
        items.append(item)

    items.sort(key=lambda i: (PRIORITY_RANK[i["priority"]], i["family"]))
    return {
        "generated_from": str(MATRIX_PATH.relative_to(REPO_ROOT)),
        "discipline": "do not optimize for raw row count (§89) — priority is evaluation value",
        "rows_total": matrix["rows_total"],
        "median_class_rows": median_rows,
        "items": items,
    }


MD_LINES_HEADER = """# docclass expansion priorities (P4, plan §89)

Generated by `scripts/expansion_priorities.py` from the committed §40–41
coverage matrix — deterministic; the committed file is canonical. Priority
is **evaluation value, not raw row count** (§89's own discipline): a family
is high-priority when it attacks a zero-coverage GT field or an all-zero
scenario axis for a load-bearing class.

"""


def write_md(payload: dict[str, Any]) -> str:
    lines = [MD_LINES_HEADER]
    lines.append(f"Corpus: {payload['rows_total']} rows; median class size "
                 f"{payload['median_class_rows']} rows. Priorities: "
                 f"high {sum(1 for i in payload['items'] if i['priority'] == 'high')}, "
                 f"medium {sum(1 for i in payload['items'] if i['priority'] == 'medium')}, "
                 f"low {sum(1 for i in payload['items'] if i['priority'] == 'low')}.\n")
    for item in payload["items"]:
        lines.append(f"## [{item['priority'].upper()}] {item['title']}")
        lines.append("")
        lines.append(item["rationale"])
        lines.append("")
        evidence = []
        if item["field_gaps"]["zero"]:
            evidence.append("zero-coverage fields: " + ", ".join(item["field_gaps"]["zero"]))
        if item["field_gaps"]["partial"]:
            evidence.append("partial fields: " + ", ".join(item["field_gaps"]["partial"]))
        if item["scenario_zero"]:
            evidence.append("zero scenario axes: " + ", ".join(item["scenario_zero"]))
        if item["scarcity"]:
            evidence.append("scarcity: " + "; ".join(item["scarcity"]))
        if evidence:
            lines.append("Evidence: " + " — ".join(evidence) + ".")
            lines.append("")
    return "\n".join(lines)


def main() -> None:
    payload = build_priorities()
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(write_md(payload), encoding="utf-8")
    print(f"wrote {OUT_JSON.relative_to(REPO_ROOT)}")
    print(f"wrote {OUT_MD.relative_to(REPO_ROOT)}")
    for item in payload["items"]:
        print(f"  [{item['priority'].upper():6}] {item['family']}")


if __name__ == "__main__":
    main()
