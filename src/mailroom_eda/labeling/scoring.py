"""Labeling-quality checks layered on the pinned Dojo catalog, not its metrics.

Inputs are field answers and the document text, never hidden ground truth. The
existing evidence checks stay intact; checklist additions require complete source
lines and reject obvious semantic false positives. Passing means structurally
reviewable, not human-approved gold. No model, network, or embedding calls occur.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import export, verify
from .registry import dojo_snapshot, fields_registry, vocabulary

POLICY_VERSION = "claim-checklist-v1"
MAX_EVIDENCE_CHARS = 300
_SPACE = re.compile(r"[ \t]+")
_OUTCOME = re.compile(
    r"(?:coverage determination\s*:\s*(?:approved|denied|pending)"
    r"(?:\s*[-–—]\s*this (?:claim has been adjudicated and paid|fill was covered under the plan))?|"
    r"(?:this|your|the) (?:insurance )?(?:claim|fill) "
    r"(?:is|was|has been) (?:approved|denied|rejected|adjudicated and paid|covered under the plan))"
    r"(?: on (?P<decision_date>\d{4}-\d{2}-\d{2}|[a-z]{3,9} \d{1,2},? \d{4}))?[.!?]?",
    re.IGNORECASE,
)
_SYSTEM = re.compile(r"(?:claim )?record extract.*system|system.*record extract", re.IGNORECASE)
_NARRATIVE = re.compile(r"claim narrative.*source text", re.IGNORECASE)
_NEGATIVE_PROOF = re.compile(
    r"^(?:police|fire brigade) report filed\s*:\s*(?:no|not filed)\b", re.IGNORECASE,
)
_NA_DEDUCTIBLE = re.compile(r"\bdeductible\s*:\s*(?:n/a|not available|not applicable)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    """A stable machine-readable issue, optionally attached to a zero-based item."""

    code: str
    item: int | None = None


@dataclass(frozen=True)
class ItemEvidence:
    """One complete source line; offsets are exclusive-end document characters."""

    item: int
    category: str
    quote: str
    start: int
    end: int


@dataclass(frozen=True)
class ChecklistScore:
    """Deterministic checks only; line rate is not semantic precision or Dojo recall."""

    item_count: int
    complete_line_count: int
    evidence: tuple[ItemEvidence, ...]
    findings: tuple[Finding, ...]

    @property
    def passed(self) -> bool:
        """Whether this candidate passed the implemented labeling checks."""
        return not self.findings

    @property
    def complete_line_rate(self) -> float:
        """Complete printed-line matches divided by proposed items; empty is zero."""
        return self.complete_line_count / self.item_count if self.item_count else 0.0


def _line(text: str) -> str:
    return _SPACE.sub(" ", text.strip())


def _source_lines(text: str) -> dict[str, list[tuple[int, int, bool]]]:
    """Index complete lines and mark explicit corpus-system sections."""
    out: dict[str, list[tuple[int, int, bool]]] = defaultdict(list)
    offset, system = 0, False
    for raw in text.splitlines(keepends=True):
        line = _line(raw)
        if _SYSTEM.search(line):
            system = True
        elif _NARRATIVE.search(line):
            system = False
        start = offset + len(raw) - len(raw.lstrip())
        end = offset + len(raw.rstrip())
        if line:
            out[line].append((start, end, system))
        offset += len(raw)
    return out


def score_checklist(value: Any, doc: verify.DocIndex, *, evidence: str | None = None) -> ChecklistScore:
    """Check present-only category/quote strings without rewriting or deleting items.

    An optional field evidence quote must be the earliest item source line and at
    most 300 characters. Omit it only for custom reviewer values, whose evidence is
    recovered from the items. Unknown determination wording is sent to review,
    not certified by a broad coverage-keyword regex. Other semantics still require
    review, including invoice comparability, contradictions and omitted categories.
    """
    if not isinstance(value, list) or not value:
        return ChecklistScore(0, 0, (), (Finding("nonempty_string_list_required"),))
    lines = _source_lines(doc.text)
    categories = set(vocabulary("claim_checklist_category"))
    findings: list[Finding] = []
    found: list[ItemEvidence] = []
    seen: set[tuple[str, str]] = set()
    complete = 0
    for i, item in enumerate(value):
        if not isinstance(item, str) or ": " not in item:
            findings.append(Finding("category_quote_string_required", i))
            continue
        category, quote = item.split(": ", 1)
        quote = _line(quote)
        if category not in categories:
            findings.append(Finding("unknown_category", i))
        if (category, quote) in seen:
            findings.append(Finding("duplicate_item", i))
        seen.add((category, quote))
        matches = lines.get(quote, []) if "\n" not in quote and "\r" not in quote else []
        if not matches:
            span = verify.locate(doc.text, quote, doc.folded)
            code = ("stitched_lines" if span and "\n" in doc.text[span[0]:span[1]]
                    else "complete_source_line_required")
            findings.append(Finding(code, i))
            continue
        complete += 1
        # Prefer a real source occurrence when an identical line is echoed in a system section.
        start, end, system = next((m for m in matches if not m[2]), matches[0])
        found.append(ItemEvidence(i, category, quote, start, end))
        if system or "stated outcome (verbatim)" in quote.lower():
            findings.append(Finding("corpus_bookkeeping", i))
        if category == "Coverage Determination":
            # Match the whole outcome sentence, not a prefix or unrelated appeal instructions.
            sentence = re.split(r"(?<=[.!?])\s+", quote, maxsplit=1)[0]
            outcome = _OUTCOME.fullmatch(sentence)
            if not outcome or (outcome["decision_date"] and verify.parse_date(outcome["decision_date"]) is None):
                findings.append(Finding("explicit_claim_outcome_required", i))
        if category == "Proof Of Loss" and _NEGATIVE_PROOF.search(quote):
            findings.append(Finding("negative_proof_is_not_present_proof", i))
        if category == "Deductible" and _NA_DEDUCTIBLE.search(quote):
            findings.append(Finding("missing_deductible_is_not_present_value", i))
    if evidence is not None:
        earliest = min(found, key=lambda item: item.start).quote if found else None
        if not isinstance(evidence, str) or len(evidence) > MAX_EVIDENCE_CHARS or _line(evidence) != earliest:
            findings.append(Finding("earliest_item_evidence_required"))
    return ChecklistScore(len(value), complete, tuple(found), tuple(findings))


def dojo_type(doc_class: str, field: str) -> str:
    """Fail closed on catalog drift; this is type parity, not numeric scoring parity."""
    spec = fields_registry()["classes"][doc_class]["fields"][field]
    snapshot = dojo_snapshot()
    pinned = snapshot["default_field_types"].get(doc_class, {}).get(field)
    if pinned is None and doc_class == "corporate_record":
        types = {e["type"] for schema in snapshot["corporate_subclass_schemas"].values()
                 for e in schema["entities"] if e["key"] == field}
        pinned = next(iter(types)) if len(types) == 1 else None
    pin = {"version": snapshot["dojo_version"], "commit": snapshot["dojo_commit"]}
    if pinned is None or pinned != spec["type"] or fields_registry()["dojo_pin"] != pin:
        raise ValueError(f"{doc_class}.{field}: labeling registry differs from pinned Dojo catalog")
    return pinned


def check_field(doc_class: str, field: str, raw: Mapping[str, Any] | None,
                doc: verify.DocIndex) -> tuple[verify.FieldResult, ChecklistScore | None]:
    """Existing evidence checks plus labeling additions; never call this a Dojo metric."""
    type_ = dojo_type(doc_class, field)
    spec = fields_registry()["classes"][doc_class]["fields"][field]
    base = verify.check_field(raw, type_, doc,
                              vocab=vocabulary(spec["vocabulary"]) if spec.get("vocabulary") else None,
                              vocab_part=spec.get("vocabulary_part"))
    score = score_checklist(base.value, doc, evidence=base.evidence) if field == "claim_checklist" and base.status == "present" else None
    return (replace(base, grounded_ok=base.grounded_ok and score.passed) if score else base), score


def audit_fields(rows: Sequence[Mapping[str, Any]], fields: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Source-bound checklist report for accepted values; withheld rows stay withheld."""
    from .tasks import _ids

    by_id = {_ids(r)[0]: r for r in rows}
    checked, blocked = 0, 0
    per: dict[str, Counter] = defaultdict(Counter)
    reports: list[dict[str, Any]] = []
    for f in fields:
        if f["field"] != "claim_checklist":
            continue
        dojo_type(str(f["doc_class"]), str(f["field"]))
        row = by_id[str(f["document_id"])]
        state = f.get("final_decision")
        per[str(row["expected_subclass"])][str(state)] += 1
        if state != "accept":
            continue
        checked += 1
        try:
            value = json.loads(f["final_value"]) if isinstance(f["final_value"], str) else f["final_value"]
        except (ValueError, TypeError):
            value = None
        score = score_checklist(value, verify.DocIndex.build(str(row["doc_text"])))
        blocked += not score.passed
        per[str(row["expected_subclass"])]["blocked"] += not score.passed
        reports.append({"document_id": f["document_id"], "passed": score.passed,
                        "complete_line_rate": score.complete_line_rate, **asdict(score)})
    return {"policy_version": POLICY_VERSION, "dojo_catalog": fields_registry()["dojo_pin"],
            "dojo_scoring": "not_run; catalog compatibility only, not official Dojo metrics",
            "accepted_checked": checked, "blocked_accepted": blocked, "human_approval": "not_assessed",
            "by_subclass": dict(per), "fields": reports}


def main(argv: Sequence[str] | None = None) -> int:
    """Offline CLI; invalid accepted candidates return nonzero, never buy new answers."""
    from . import runner

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="one chunk directory containing fields.csv")
    parser.add_argument("--stage", type=Path, default=runner.DEFAULT_STAGE)
    args = parser.parse_args(argv)
    result = audit_fields(runner.load_stage(args.stage), export.read_csv(args.out / "fields.csv"))
    print(json.dumps(result, indent=2))
    return int(result["blocked_accepted"] > 0 or result["accepted_checked"] == 0)


if __name__ == "__main__":
    raise SystemExit(main())
