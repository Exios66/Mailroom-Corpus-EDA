"""Row-level ground-truth presence codes (v9.1 quality revision, Phase B2).

Context: `mailroom-issues#196 <https://github.com/LLM-Mailroom-Services/mailroom-issues/issues/196>`_
(the open "mailroom-dataset v9.1 quality revision" epic) and the linked
plan ``docs/plans/v9.1-data-quality-hf-revision.md`` diagnose that the
Modal-sandbox extraction floor (``overall_extraction_score ~= 0.087``,
local-mailroom-sandbox#21/#32) is partly caused by scorers treating an
empty-but-legitimate ground-truth field as a miss, because the absence
semantics only ever lived in this repo's *local audit* code
(``scripts/audit/coverage_matrix.py``) — never in the published rows a
Modal/offline scorer actually loads.

This module is the SINGLE source of that classification (never
re-declared): both the audit CLI and the Hub publish path
(``scripts/build/build_v9_1_quality_revision.py``) import it, so the rule
can never drift between "what the audit reports" and "what ships".

Closed vocabulary — every ``(doc_class, field)`` cell classifies as exactly
one of:

- ``populated`` — the field carries ground truth.
- ``schema_documented_absence`` — a v8_build/v9 conformance law documents
  this field empty on this row (e.g. ``adjuster`` on CMS/GNOTHEIA/INSURBIAS
  rows, ``denial_reasons`` on non-denied claims).
- ``not_applicable`` — the field is not part of this row's document class
  (e.g. ``cuad_clause_labels`` on an ``insurance_claim`` row).
- ``pending_annotation`` — a genuine, catalogued annotation gap blocked on
  a specific external dependency (currently: ``cuad_clause_labels`` on the
  91 SEC EDGAR EX-10 contracts — issue #30 — blocked on a Modal/vLLM
  clause-pass credential). Never a silent ``{}``.

There is deliberately no fifth "unexplained gap" code in the shipped
vocabulary: ``classify_field`` returns the sentinel ``"genuine_gap"`` only
as a defensive signal that some cell matches none of the known rules, and
callers that publish data (the revision builder) MUST treat any
``genuine_gap`` as a hard build failure rather than shipping it — the
audit CLI already verified (issue #28/#29/#30) that the live corpus has
zero such cells.
"""
from __future__ import annotations

from typing import Any

#: The four codes a published `gt_presence` cell may carry.
PRESENCE_CODES = (
    "populated",
    "schema_documented_absence",
    "not_applicable",
    "pending_annotation",
)

#: §41 field-coverage map: class -> GT columns that feed the specialist's
#: expected_fields surface (mirrors conftest's EXTRACTION_GT_BY_CLASS +
#: the enrichment keys). Single-sourced here; scripts/audit/coverage_matrix.py
#: imports this constant instead of re-declaring it.
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

#: Union of every class-relevant field across all classes — the uniform key
#: set every row's `gt_presence` map carries (cast-safe: same keys on every
#: row regardless of class, KANBAN-076 convention).
ALL_GT_FIELDS: tuple[str, ...] = tuple(
    sorted({key for keys in FIELD_KEYS_BY_CLASS.values() for key in keys})
)

BDR_AUTO_SOURCE = "bdr-ai-org/insurance-motor-claims-decision-v1"


def _insurbias_supporting_doc_absent(doc_text: str) -> bool:
    """The real ``v9_build`` predicate, imported lazily to dodge a module-
    load-order cycle (v9_build does a one-time ``sys.path`` bootstrap at
    import time) while keeping the rule single-sourced — no local
    reimplementation, no drift risk."""
    from .v9_build import _insurbias_supporting_doc_absent as _impl

    return _impl(doc_text)


INSURBIAS_SOURCE = "feihuangfh/INSURBIAS"  # mirrors v9_build.INSURBIAS_SOURCE (verified by test)


#: Documented-absence rules (issue #28), single-sourced from
#: scripts/audit/coverage_matrix.py's original ABSENCE_RULES — every
#: schema-documented-empty (class, field) cell, verified against the v9
#: snapshot. Deliberately EXCLUDES ("contract", "cuad_clause_labels"): that
#: cell is a catalogued *pending* annotation gap (issue #30), not a
#: by-design absence — see PENDING_ANNOTATION_RULES below.
ABSENCE_RULES: dict[tuple[str, str], dict[str, Any]] = {
    ("insurance_claim", "adjuster"): {
        "rule": (
            "v8_build.py ALLOWED_EMPTY = {'adjuster'}; v9_build.py "
            "ALLOWED_EMPTY['insurance_claim'] = {'adjuster'} — source-absent "
            "on every insurance subclass except the BDR auto draw, which "
            "carries adjuster pseudonyms."
        ),
        "is_documented_absence": lambda r: (
            str((r.get("metadata") or {}).get("source_dataset") or "") != BDR_AUTO_SOURCE
        ),
    },
    ("insurance_claim", "denial_reasons"): {
        "rule": (
            "v8_build.py _auto_row: denial reasons exist only on denied "
            "claims; non-denied rows ship '[]' (a complete no-items answer "
            "per v9_build.LIST_GT_FIELDS)."
        ),
        "is_documented_absence": lambda r: (
            str(r.get("coverage_determination") or "").strip().lower() != "denied"
        ),
    },
    ("insurance_claim", "supporting_documents"): {
        "rule": (
            "v9_build.py complete_gt_fields + _insurbias_supporting_documents "
            "/ _insurbias_supporting_doc_absent (issue #29): INSURBIAS rows "
            "ship claim narratives only; supporting_documents is derived "
            "from referenced features. '[]' is a documented absence only "
            "when the narrative grounds no such feature (bare accident "
            "report, 6/150 on the v9 draw)."
        ),
        "is_documented_absence": lambda r: (
            str((r.get("metadata") or {}).get("source_dataset") or "") == INSURBIAS_SOURCE
            and _insurbias_supporting_doc_absent(str(r.get("doc_text") or ""))
        ),
    },
}

#: Pending-annotation rules (issue #30): a genuine, catalogued gap blocked
#: on a specific external dependency — NOT a by-design absence. Kept
#: separate from ABSENCE_RULES so `gt_presence` can honestly distinguish
#: "this will never be filled" from "this is filled as soon as the Modal/
#: vLLM clause-pass credential (DMR-052 provider seam) is available".
PENDING_ANNOTATION_RULES: dict[tuple[str, str], dict[str, Any]] = {
    ("contract", "cuad_clause_labels"): {
        "rule": (
            "issue #30 (dated 2026-09-13): the 91 SEC EDGAR EX-10 contract "
            "rows ship no CUAD clause annotation — the clause-classification "
            "pass could not run (no working OPENROUTER_API_KEY / "
            "VLLM_BASE_URL / Modal endpoint at build time). '{}' is a "
            "complete no-annotations answer per v9_build.DICT_GT_FIELDS, "
            "but the gap is a catalogued pending annotation, not a "
            "schema-documented absence: it closes as soon as a working "
            "clause-pass credential re-runs the DMR-052 provider seam over "
            "exactly these 91 filenames "
            "(docs/reports/audits/cuad_ex10_annotation_review.md)."
        ),
        "is_pending": lambda r: (
            str((r.get("metadata") or {}).get("source_dataset") or "") == "sec_edgar"
        ),
    },
}


def is_populated(v: object) -> bool:
    """v9 complete-GT convention: '' is absent and the JSON no-item markers
    ``'[]'`` / ``'{}'`` carry no ground truth."""
    if v is None:
        return False
    if isinstance(v, str):
        v = v.strip()
        if not v or v in ("[]", "{}"):
            return False
        return True
    return v not in ("", [], {})


def classify_field(doc_class: str, key: str, row: dict[str, Any]) -> str:
    """Classify one ``(doc_class, key)`` cell on ``row`` (issue #28/§41).

    ``row`` must carry at minimum the field values in ``FIELD_KEYS_BY_CLASS``
    plus ``metadata`` (dict), ``doc_text`` (str), and
    ``coverage_determination`` (for the insurance denial-reasons rule).
    """
    if key not in FIELD_KEYS_BY_CLASS.get(doc_class, ()):
        return "not_applicable"
    if is_populated(row.get(key)):
        return "populated"
    pending = PENDING_ANNOTATION_RULES.get((doc_class, key))
    if pending is not None and pending["is_pending"](row):
        return "pending_annotation"
    absence = ABSENCE_RULES.get((doc_class, key))
    if absence is not None and absence["is_documented_absence"](row):
        return "schema_documented_absence"
    return "genuine_gap"  # must never ship — see module docstring


def row_gt_presence(row: dict[str, Any]) -> dict[str, str]:
    """The full, uniform ``field -> code`` map for one row (all classes'
    fields; fields outside this row's class are ``not_applicable``)."""
    doc_class = str(row.get("expected") or "")
    return {key: classify_field(doc_class, key, row) for key in ALL_GT_FIELDS}


#: Metadata keys that are label-*derived aggregate counts* (weak indirect
#: signals), not labels themselves, but which the v9.1 revision (Phase B1)
#: moves out of the blind `default.metadata` surface into GT-only fields —
#: the v9 card's own caveat: "Consumers that require strict label-free
#: metadata may treat them as weak indirect signals". Moving them does NOT
#: touch `doc_text`, so it cannot drift `document_id` / `content_sha256`
#: (both derived from doc_text only, per identity.py) — the zero-drift
#: mandate that originally blocked stripping them is not implicated.
WEAK_INDIRECT_METADATA_KEYS = frozenset({"clause_count", "maud_label_count"})


def strip_weak_indirect_signals(metadata: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Split ``metadata`` into (blind-safe metadata, extracted GT-only values).

    Returns a new metadata dict with ``WEAK_INDIRECT_METADATA_KEYS`` removed,
    and a dict of the extracted values (each cast to ``str``, ``""`` if
    absent — the corpus-wide absence convention) for the caller to fold into
    the row's `ground_truth`-only fields.
    """
    cleaned = dict(metadata)
    extracted = {}
    for key in WEAK_INDIRECT_METADATA_KEYS:
        v = cleaned.pop(key, "")
        extracted[key] = "" if v is None else str(v)
    return cleaned, extracted


#: Token/context bands (Phase B4) aligned to the live Modal L4 serving
#: envelope (plan §2.2/§3.1: Modal jobs pin `max_model_len` 16k-32k; merger
#: docs blow past 32k). Published so consumers do not silently truncate.
CONTEXT_WINDOW_BANDS: tuple[tuple[float, str], ...] = (
    (4_096, "<=4k"),
    (16_384, "<=16k"),
    (32_768, "<=32k"),
)
CONTEXT_WINDOW_OVERFLOW = ">32k"


def context_window_band(token_estimate: float) -> str:
    """The smallest published band this many estimated tokens fits in."""
    for budget, label in CONTEXT_WINDOW_BANDS:
        if token_estimate <= budget:
            return label
    return CONTEXT_WINDOW_OVERFLOW
