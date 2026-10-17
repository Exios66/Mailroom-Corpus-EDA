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

v9.3 corporate extension (staging branch
``feat/v9.3-staging-corporate-presence``): ``corporate_record`` rows now
carry the per-subclass entity keys from
``llm_dojo_scoring.corporate_record_schema.SUBCLASS_SCHEMAS`` (mirrored,
sha-pinned, in ``corporate_presence``). Uniformity of the map keys is
therefore a SUPERSET law since v9.3: every row carries the
``BASE_GT_FIELDS`` keys; corporate rows may extend with entity keys whose
blank-and-undocumented cells are *omitted* (the dojo scorer-side
unstated-blank ⇒ unlabeled regime — the absent key is the published
representation of "unlabeled"). The internal ``unlabeled`` sentinel value
must never ship; ``verify_presence_codes`` (v9_1_revision) enforces both
directions, and the subclass-conditional hardening (a corporate row's
present extension keys must sit inside the entity-core keys + the row's OWN
subclass extension keys — foreign-subclass extensions ABSENT; unknown
subclass ⇒ core-only) lives in :func:`presence_law_problems` — the
single-sourced key-set law that ``v9_3_revision.verify_presence_laws`` and
the legacy audit (``gt_quality.audit_row``) both consume.
"""
from __future__ import annotations

from typing import Any

from .corporate_presence import (
    CORPORATE_BASE_KEYS,
    CORPORATE_ENTITY_CORE_KEYS,
    CORPORATE_ENTITY_KEYS,
    CORPORATE_PRESENCE_KEYS,
    CORPORATE_SUBCLASS_ENTITY_KEYS,
    core_applicable_for_subclass,
)

#: Public aliases: the per-subclass entity registry used by the audit and the
#: extended corporate presence surface (single-sourced in corporate_presence).
CORPORATE_ENTITY_KEYS_BY_SUBCLASS = CORPORATE_SUBCLASS_ENTITY_KEYS

#: The four codes a published `gt_presence` cell may carry.
PRESENCE_CODES = (
    "populated",
    "schema_documented_absence",
    "not_applicable",
    "pending_annotation",
)

#: Internal sentinel for the dojo scorer-side "unstated-blank ⇒ unlabeled"
#: regime (docs/CORPORATE_RECORD_SCHEMA.md): a corporate entity field that is
#: blank with no absence statement was NEVER labeled. It must never appear in
#: a published map — :func:`row_gt_presence` DROPS it, the absent key being
#: the representation. Not a member of PRESENCE_CODES; publishing it is a
#: hard verify failure.
UNLABELED = "unlabeled"

#: The v9.2-era uniform key set — the BASE presence law: every published row
#: (any class) carries these keys; corporate rows may extend them with the
#: entity keys (``CORPORATE_PRESENCE_KEYS``). Derived: the fields the v9
#: pipeline labeled before the v9.3 corporate extension (20 keys).
BASE_GT_FIELDS: tuple[str, ...] = (
    "adjuster",
    "claim_number",
    "claimed_amount",
    "claim_type",
    "content_topic",
    "coverage_determination",
    "cuad_clause_labels",
    "damages_description",
    "date_filed",
    "date_of_loss",
    "denial_reasons",
    "insured_party",
    "insurer",
    "intent",
    "keywords",
    "maud_clause_labels",
    "policy_number",
    "sentiment_label",
    "subject_matter",
    "supporting_documents",
)

#: §41 field-coverage map: class -> GT columns that feed the specialist's
#: expected_fields surface (mirrors conftest's EXTRACTION_GT_BY_CLASS +
#: the enrichment keys). Single-sourced here; scripts/audit/coverage_matrix.py
#: imports this constant instead of re-declaring it.
#: v9.3: ``corporate_record`` now lists the per-subclass entity keys on top of
#: the base trio — the per-subclass entity regime from
#: ``llm_dojo_scoring.corporate_record_schema.SUBCLASS_SCHEMAS`` (mirrored,
#: sha-pinned, in ``corporate_presence``). Entity fields are unlabeled-by-
#: absence on published rows; their codes live under the same vocabulary.
FIELD_KEYS_BY_CLASS: dict[str, tuple[str, ...]] = {
    "contract": ("cuad_clause_labels",),
    "merger_agreement": ("maud_clause_labels",),
    "correspondence": (
        "intent", "subject_matter", "keywords", "sentiment_label",
        "content_topic",
    ),
    "corporate_record": (
        *CORPORATE_BASE_KEYS,
        *CORPORATE_ENTITY_KEYS,
    ),
    "insurance_claim": (
        "claim_number", "policy_number", "insurer", "insured_party",
        "claim_type", "date_of_loss", "date_filed", "claimed_amount",
        "adjuster", "damages_description", "coverage_determination",
        "denial_reasons", "supporting_documents",
        "intent", "subject_matter", "keywords",
    ),
}

#: Union of every class-relevant field across all classes — the maximum key
#: set any row's `gt_presence` map may carry. Since the v9.3 corporate
#: extension this is a SUPERSET law: rows must carry the base keys and only
#: class-relevant extensions (see verify_presence_codes, v9_1_revision).
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
    Corporate entity keys (v9.3) classify under the dojo per-subclass regime
    (``docs/CORPORATE_RECORD_SCHEMA.md``): populated beats the subclass map;
    an inapplicable CORE key is ``not_applicable``; an applicable-but-blank
    core key and any blank extension key is the internal ``unlabeled``
    sentinel (dropped from published maps — unstated-blank ⇒ unlabeled).
    """
    if doc_class == "corporate_record" and key in CORPORATE_ENTITY_KEYS:
        return _classify_corporate_entity(key, row)
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


def _classify_corporate_entity(key: str, row: dict[str, Any]) -> str:
    """Dojo-regime classification for one corporate entity cell (v9.3).

    Mirrors ``corporate_record_relabel`` exactly: a stated value is a label
    (``populated``); an inapplicable CORE key is ``not_applicable``; a blank
    applicable core key and any blank extension key are UNLABELED (the
    annotation never ran) and drop out of the map. Base-registry keys that
    are not entity keys never enter here.
    """
    if is_populated(row.get(key)):
        return "populated"
    if key in CORPORATE_ENTITY_CORE_KEYS and key not in core_applicable_for_subclass(
        row.get("expected_subclass")
    ):
        return "not_applicable"
    if key in CORPORATE_ENTITY_CORE_KEYS:
        return UNLABELED  # applicable but blank: annotation never ran
    # extension entity of another subclass: the relabel never marks these —
    # they stay absent (unlabeled), not not_applicable.
    return UNLABELED


def row_gt_presence(row: dict[str, Any]) -> dict[str, str]:
    """The ``field -> code`` map for one row (all classes' fields; fields
    outside this row's class are ``not_applicable``).

    v9.3 key-set law (superset): every row carries the ``BASE_GT_FIELDS``
    keys — the uniform v9.1/v9.2 base — and CLASS-RELEVANT keys on top. Only
    ``corporate_record`` rows iterate the extended registry surface (the
    per-subclass entity keys), where blank-with-no-absence-statement cells
    are UNLABELED and are OMITTED from the map rather than shipped as a code
    (the dojo unstated-blank regime — the absent key is the published
    representation of "not labeled"). Non-corporate rows keep exactly the
    base map, so legacy regeneration stays byte-stable.
    """
    doc_class = str(row.get("expected") or "")
    keys = ALL_GT_FIELDS if doc_class == "corporate_record" else BASE_GT_FIELDS
    return {
        key: code
        for key in keys
        if (code := classify_field(doc_class, key, row)) != UNLABELED
    }


def presence_law_problems(
    presence: dict[str, str],
    doc_class: str,
    expected_subclass: str | None = None,
) -> list[str]:
    """The published presence key-set law for ONE parsed ``gt_presence`` map.

    Single-sourced here so every consumer derives the same verdict (the
    v9.3 audit path ``gt_quality.audit_row`` and the staging verifier
    ``v9_3_revision.verify_presence_laws`` both call this; neither
    re-declares the rule — ``gt_presence`` is the single source of the
    presence classification, provenance law, AND the key-set law).

    Superset law (relaxed from the v9.1 uniform-map equality so the
    corporate per-subclass entity regime is expressible):

    * keys ⊇ ``BASE_GT_FIELDS`` — the 20-key uniform v9.1/v9.2 base, a
      required floor that is never silently dropped;
    * keys ⊆ ``ALL_GT_FIELDS`` — keys outside the registry are rejected
      outright;
    * extension keys beyond the base are corporate-only (``corporate_record``
      rows may carry the entity keys, no other class may);
    * subclass-conditional law (v9.3 hardening): on corporate rows the
      present extension keys must sit inside the 6 ``ENTITY_CORE`` keys plus
      that row's OWN subclass's extension keys — a foreign subclass's
      extension key present in a row's map is a violation; an unknown
      ``expected_subclass`` restricts a row to core keys only;
    * every code ∈ ``PRESENCE_CODES`` — ``genuine_gap`` and the internal
      ``unlabeled`` (unstated-blank) sentinel never ship.

    Returns the problem list (empty = lawful). Row context (filename) is
    the caller's concern; messages are stable phrases asserted by tests.
    """
    problems: list[str] = []
    registry = set(ALL_GT_FIELDS)
    base = set(BASE_GT_FIELDS)
    keys = set(presence)
    unknown = sorted(keys - registry)
    if unknown:
        problems.append(f"presence keys outside ALL_GT_FIELDS: {unknown}")
    missing = sorted(base - keys)
    if missing:
        problems.append(f"base presence keys missing: {missing}")
    extension = keys - base
    if extension:
        if doc_class != "corporate_record":
            problems.append("non-corporate row carries entity presence keys")
        else:
            # v9.3 subclass-conditional hardening: core keys + the row's OWN
            # subclass extension keys; unknown subclass ⇒ core-only.
            own = CORPORATE_ENTITY_KEYS_BY_SUBCLASS.get(expected_subclass or "", ())
            foreign = sorted(extension - set(CORPORATE_ENTITY_CORE_KEYS) - set(own))
            if foreign:
                problems.append(
                    f"foreign-subclass extension keys (subclass "
                    f"{(expected_subclass or '')!r}): {foreign}"
                )
    bad_codes = sorted({code for code in presence.values() if code not in PRESENCE_CODES})
    if bad_codes:
        problems.append(f"codes outside the published vocabulary: {bad_codes}")
    return problems


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
