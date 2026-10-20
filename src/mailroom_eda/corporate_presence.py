"""Corporate-record entity-key registry + EDA-side relabel equivalents (v9.3).

The v9.3 staging revision (``mailroom_eda.v9_3_revision``) un-freezes the
``corporate_record`` ``gt_presence`` key-set: published rows may now carry the
per-subclass entity fields that ``llm_dojo_scoring.corporate_record_schema``
defines, so a correct extraction stops scoring as a spurious fill (the
v9.2-and-earlier uniform 3-key set structurally could not label any of
``entity_name`` / ``record_type`` / ``effective_date`` / ``signatories`` /
``jurisdiction`` / ``filing_number``).

This module holds the two things the revision needs:

* **The registry** — the corporate entity keys (6 core + the per-subclass
  extension entities), *recorded verbatim* from the dojo data module, the
  single source of truth for the subclass→entity schema:
  ``llm_dojo_scoring/corporate_record_schema.py`` (``SUBCLASS_SCHEMAS``),
  mirrored here because the EDA producer cannot depend on the scoring repo.
  ``tests/test_corporate_presence.py`` proves mirror == live dojo module by
  byte-checking the pinned source sha and importing the sibling checkout
  when present (hub#57 test convention; skip-safe when the checkout is
  absent) — a silent drift between the two copies is a hard parity failure.
* **The relabel equivalents** — ``relabel_row``/``relabel_rows``: a faithful
  EDA port of ``llm_dojo_scoring.corporate_record_relabel`` (deterministic,
  derived-from-the-row-only; never invents a label fact). The v9.2.1 label
  repair (HF branch ``corporate-labels-v9.2.1``) was produced by the dojo
  module; this port reproduces its output byte-identically inside the EDA
  pipeline and is verified row-by-row against that published content in the
  v9.3 gates — if it ever diverges from the published v9.2.1 values, the
  build must STOP, not paper over the difference.

Precedence honesty (v9.3 plan decision P4): the entity presence carried on
the 450 corporate rows uses ONLY what the v9.2.1 relabel already derived
(the ``entity_name`` / ``record_type`` pins and the not-applicable core
marks). Everything the relabel left unlabeled stays **absent from the
presence map** — the dojo scorer-side unstated-blank ⇒ unlabeled regime
(``llm_dojo_scoring.gt_metadata`` / ``docs/CORPORATE_RECORD_SCHEMA.md``).

Presence-code vocabulary (published, verbatim from the dojo docs):
``populated`` / ``not_applicable`` / ``schema_documented_absence`` /
``pending_annotation`` — the codes this producer may put on a published row
— plus a *scorer-only* unstated-blank regime: a key with no presence entry
means "unlabeled", not "expected empty". This module never emits the
internal ``"unlabeled"`` sentinel into a published map; the absent key IS
the representation.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any, Iterator, Mapping

__all__ = [
    "CORPORATE_BASE_KEYS",
    "CORPORATE_ENTITY_KEYS",
    "CORPORATE_ENTITY_CORE_KEYS",
    "CORPORATE_SUBCLASS_ENTITY_KEYS",
    "CORPORATE_PRESENCE_KEYS",
    "RECORD_TYPES",
    "SUBCLASS_INTENTS",
    "SUBCLASS_CORE_APPLICABLE",
    "SUBCLASS_ORDER",
    "SUBCLASS_RECORD_TYPES",
    "company_from_template",
    "core_applicable_for_subclass",
    "entity_keys_for_subclass",
    "normalize_subclass",
    "relabel_row",
    "relabel_rows",
    "subject_conflicts",
    "subclass_for",
]

# ---------------------------------------------------------------------------
# Registry provenance: what was mirrored, from which exact bytes
# ---------------------------------------------------------------------------

#: Source-of-truth checkout for the pyproject-independent mirror below.
DOJO_SCHEMA_SHA256 = "92dbfa9c4d1f78e03a095898c878a099022099e99fe364b67490f4c43fc6398f"
DOJO_RELABEL_SHA256 = "d517e99515be2d017d35f6865b23545cc99d48c1875c747983a43267b0921658"
#: ``llm-dojo-scoring`` git revision the two sha256 pins were taken at.
DOJO_REV = "8e6566c1d1c52efeb5ebbb57ddee90a94586f7da"

RECORD_TYPES: tuple[str, ...] = (
    "articles_of_incorporation",
    "bylaws",
    "powers_of_attorney",
    "rights_instrument",
    "other",
)

#: The 6 core fields that name a real-world entity / fact in the document
#: (``ENTITY_CORE_FIELDS`` in the dojo schema). The flat 9-key
#: ``CorporateRecordExtraction`` core adds the semantic trio below.
CORPORATE_ENTITY_CORE_KEYS: tuple[str, ...] = (
    "entity_name",
    "record_type",
    "effective_date",
    "signatories",
    "jurisdiction",
    "filing_number",
)

CORPORATE_BASE_KEYS: tuple[str, ...] = ("intent", "subject_matter", "keywords")

#: The v9.2-era uniform key set (``ALL_GT_FIELDS`` before v9.3) — every
#: published row must carry these regardless of class; corporate rows may
#: add entity keys on top. See ``gt_presence.BASE_GT_FIELDS``.
#: (Re-declared only as documentation; the pipeline checks against the
#: derived constant in ``gt_presence``.)

#: Per-subclass extension-entity keys (dojo ``RecordEntity.key`` per
#: ``SubclassSchema``), canonical order preserved from ``SUBCLASS_SCHEMAS``.
CORPORATE_SUBCLASS_ENTITY_KEYS: dict[str, tuple[str, ...]] = {
    "bylaws": ("certifying_officer", "last_amended_date"),
    "articles_of_incorporation": ("incorporators", "registered_agent", "authorized_capital"),
    "certificate_of_formation": ("organizers", "registered_agent"),
    "charter_amendment": ("amended_provisions", "approval_date", "prior_entity_name"),
    "powers_of_attorney": (
        "principal", "attorneys_in_fact", "authority_scope", "expiration_date",
    ),
    "subsidiary_list": ("subsidiaries", "subsidiary_jurisdictions"),
    "rights_instrument": (
        "instrument_kind", "holder", "rights_agent", "securities_covered", "exercise_price",
    ),
    "indenture": (
        "trustee", "guarantors", "securities_series", "principal_amount", "maturity_date",
    ),
    "board_resolution": ("acting_body", "action_approved", "resolutions"),
    "officer_certificate": (
        "certifying_officer", "officer_title", "certified_matter", "addressee",
    ),
    "other": (),
}

#: Subclass → mapped Hub ``record_type`` (5-token enum from the schema).
SUBCLASS_RECORD_TYPES: dict[str, str] = {
    "bylaws": "bylaws",
    "articles_of_incorporation": "articles_of_incorporation",
    "certificate_of_formation": "articles_of_incorporation",
    "charter_amendment": "articles_of_incorporation",
    "powers_of_attorney": "powers_of_attorney",
    "subsidiary_list": "other",
    "rights_instrument": "rights_instrument",
    "indenture": "other",
    "board_resolution": "other",
    "officer_certificate": "other",
    "other": "other",
}

#: Subclass → GT-pinned intent (the value the published GT pins for the
#: subclass; the relabel never overrides an adjudicated intent).
SUBCLASS_INTENTS: dict[str, str] = {
    "bylaws": "governance_rules",
    "articles_of_incorporation": "entity_formation",
    "certificate_of_formation": "entity_formation",
    "charter_amendment": "entity_formation",
    "powers_of_attorney": "authority_delegation",
    "subsidiary_list": "other",
    "rights_instrument": "investor_rights",
    "indenture": "investor_rights",
    "board_resolution": "corporate_action_approval",
    "officer_certificate": "governance_rules",
    "other": "other",
}

#: Subclass → applicable CORE fields (of the 9-key flat core), membership
#: faithful to ``SubclassSchema.core_applicable`` (canonical key order is
#: ``CORE_FIELDS`` in the dojo module; membership is what the EDA pipeline
#: consumes — presence classification only ever tests membership).
SUBCLASS_CORE_APPLICABLE: dict[str, frozenset[str]] = {
    "bylaws": frozenset({
        *CORPORATE_BASE_KEYS, *CORPORATE_ENTITY_CORE_KEYS[:3],  # name/type/date
        "signatories", "jurisdiction",
    }),
    "articles_of_incorporation": frozenset({
        *CORPORATE_BASE_KEYS, *CORPORATE_ENTITY_CORE_KEYS,
    }),
    "certificate_of_formation": frozenset({
        *CORPORATE_BASE_KEYS, *CORPORATE_ENTITY_CORE_KEYS,
    }),
    "charter_amendment": frozenset({
        *CORPORATE_BASE_KEYS, *CORPORATE_ENTITY_CORE_KEYS,
    }),
    "powers_of_attorney": frozenset({
        *CORPORATE_BASE_KEYS, *CORPORATE_ENTITY_CORE_KEYS[:3],
        "signatories", "jurisdiction",
    }),
    "subsidiary_list": frozenset({
        *CORPORATE_BASE_KEYS, *CORPORATE_ENTITY_CORE_KEYS[:3],
        # a schedule is not executed or filed on its own: no signatories,
        # jurisdiction or filing_number.
    }),
    "rights_instrument": frozenset({
        *CORPORATE_BASE_KEYS, *CORPORATE_ENTITY_CORE_KEYS[:3],
        "signatories", "jurisdiction",
    }),
    "indenture": frozenset({
        *CORPORATE_BASE_KEYS, *CORPORATE_ENTITY_CORE_KEYS[:3],
        "signatories", "jurisdiction",
    }),
    "board_resolution": frozenset({
        *CORPORATE_BASE_KEYS, *CORPORATE_ENTITY_CORE_KEYS[:3],
        "signatories", "jurisdiction",
    }),
    "officer_certificate": frozenset({
        *CORPORATE_BASE_KEYS, *CORPORATE_ENTITY_CORE_KEYS[:3],
        "signatories", "jurisdiction",
    }),
    "other": frozenset({
        *CORPORATE_BASE_KEYS, *CORPORATE_ENTITY_CORE_KEYS,
    }),
}

#: Canonical subclass order (dict insertion order of ``SUBCLASS_SCHEMAS``,
#: residual ``other`` last — the order the prompt section renders).
SUBCLASS_ORDER: tuple[str, ...] = (
    "bylaws",
    "articles_of_incorporation",
    "certificate_of_formation",
    "charter_amendment",
    "powers_of_attorney",
    "subsidiary_list",
    "rights_instrument",
    "indenture",
    "board_resolution",
    "officer_certificate",
    "other",
)

#: All 37 corporate entity keys = 6 core + the 31 distinct extension keys
#: across the 11 subclasses (``registered_agent`` is shared by two
#: subclasses; ``certifying_officer`` by two; counted once each).
CORPORATE_ENTITY_KEYS: tuple[str, ...] = tuple(sorted({
    *CORPORATE_ENTITY_CORE_KEYS,
    *(keys for entities in CORPORATE_SUBCLASS_ENTITY_KEYS.values() for keys in entities),
}))

#: The corporate rows' extended ``gt_presence`` surface: base trio + entity
#: keys — exactly what ``FIELD_KEYS_BY_CLASS["corporate_record"]`` extends.
CORPORATE_PRESENCE_KEYS: tuple[str, ...] = tuple(sorted({*CORPORATE_BASE_KEYS, *CORPORATE_ENTITY_KEYS}))

# Registry invariants (import-time mirror guards; dojo raises the same way).
if set(SUBCLASS_ORDER) != set(SUBCLASS_RECORD_TYPES) or set(SUBCLASS_ORDER) != set(SUBCLASS_CORE_APPLICABLE):
    raise RuntimeError("corporate_presence subclass mirrors are inconsistent")
for _sub in SUBCLASS_ORDER:
    if SUBCLASS_RECORD_TYPES[_sub] not in RECORD_TYPES:
        raise RuntimeError(f"{_sub}: record_type not in the 5-token enum")
    _bad_core = SUBCLASS_CORE_APPLICABLE[_sub] - set(CORPORATE_BASE_KEYS) - set(CORPORATE_ENTITY_CORE_KEYS)
    if _bad_core:
        raise RuntimeError(f"{_sub}: core_applicable outside the 9-key core: {sorted(_bad_core)}")
    _shadow = set(CORPORATE_SUBCLASS_ENTITY_KEYS[_sub]) & set(CORPORATE_ENTITY_CORE_KEYS)
    if _shadow:
        raise RuntimeError(f"{_sub}: entity keys shadow the core: {sorted(_shadow)}")
del _sub

# ---------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------

_ALIAS_KEY_RE = re.compile(r"[^a-z0-9]")


def normalize_subclass(value: Any) -> str:
    """Coerce a raw subclass to a canonical key (unknown -> ``other``),
    mirroring the dojo ``normalize_corpus_subclass``/``normalize_doc_subclass``
    scoped behavior for ``corporate_record``."""
    if value is None:
        return "other"
    raw = str(value).strip()
    key = _ALIAS_KEY_RE.sub("", raw.lower())
    if not key:
        return "other"
    for candidate in SUBCLASS_ORDER:
        if key == _ALIAS_KEY_RE.sub("", candidate.lower()):
            return candidate
    return "other"


def subclass_for(subclass: Any) -> str:
    """Canonical subclass key for *subclass* (never raises)."""
    return normalize_subclass(subclass)


def entity_keys_for_subclass(subclass: Any) -> tuple[str, ...]:
    """Extension-entity keys of one subclass."""
    return CORPORATE_SUBCLASS_ENTITY_KEYS[subclass_for(subclass)]


def core_applicable_for_subclass(subclass: Any) -> frozenset[str]:
    """Applicable CORE fields of one subclass."""
    return SUBCLASS_CORE_APPLICABLE[subclass_for(subclass)]


# ---------------------------------------------------------------------------
# relabel equivalents — faithful EDA port of corporate_record_relabel
# ---------------------------------------------------------------------------

_TEMPLATE_RE = re.compile(r"^\s*\S+ of (?P<company>.+?)\s*\(CIK\s*\d+\)\s*\(SEC EDGAR exhibit[^)]*\)", re.S)
# Trailing ``(TICK)`` / ``(TICK, TICK-PA)`` and EDGAR ``/UT/`` state suffixes are
# filer metadata, not part of the legal name. Country / entity-form codes are not.
_TICKER_RE = re.compile(r"\s*\((?:[A-Z][A-Z0-9.\-]{0,5})(?:\s*,\s*[A-Z][A-Z0-9.\-]{0,5})*\)\s*$")
_STATE_RE = re.compile(r"\s*/[A-Z]{2}/\s*$")
_NOT_TICKERS = frozenset(
    {"UK", "US", "USA", "EU", "LLC", "LP", "LLP", "LTD", "INC", "CORP", "CO", "NV", "AG", "SA", "PLC"}
)

_PHRASE = {
    "bylaws": "Bylaws",
    "articles_of_incorporation": "Articles of incorporation",
    "certificate_of_formation": "Certificate of formation",
    "charter_amendment": "Charter amendment",
    "powers_of_attorney": "Power of attorney",
    "subsidiary_list": "List of subsidiaries",
    "rights_instrument": "Rights instrument",
    "indenture": "Indenture",
    "board_resolution": "Board resolution",
    "officer_certificate": "Officer certificate",
    "other": "Corporate record",
}

_TEMPLATE_MARK = "(SEC EDGAR exhibit"
_ID_KEYWORD = re.compile(r"^(EX-[\w.\-()]+|\d{10}-\d{2}-\d{6}|S-1(/A)?|8-K|10-[KQ](/A)?)$", re.I)
_CIK_KEYWORD = re.compile(r"\(CIK \d+\)")

#: Title cues per subclass, matched against *prose* ``subject_matter`` only
#: (template subject_matter just echoes the subclass label).
_SUBJECT_CUES: dict[str, re.Pattern[str]] = {
    "bylaws": re.compile(r"\bby-?laws\b", re.I),
    "articles_of_incorporation": re.compile(
        r"certificate of incorporation|articles of incorporation|memorandum and articles", re.I
    ),
    "powers_of_attorney": re.compile(r"power of attorney|powers of attorney", re.I),
    "rights_instrument": re.compile(
        r"\bwarrants?\b|registration rights|rights agreement|purchase option|"
        r"subscription agreement|specimen", re.I
    ),
    "indenture": re.compile(r"\bindenture\b", re.I),
    "officer_certificate": re.compile(r"(officer|secretary)'?s?'? certificate", re.I),
    "subsidiary_list": re.compile(r"\bsubsidiaries\b", re.I),
    "board_resolution": re.compile(r"\bminutes\b|\bresolutions?\b|written consent", re.I),
}


def company_from_template(subject_matter: str) -> str | None:
    """Company named in a template ``subject_matter``; ``None`` when not a
    template (mirrors the dojo relabel helper byte-for-byte, including the
    entity-form-not-a-ticker guard — ``First Majestic (AG)`` keeps ``AG``)."""
    m = _TEMPLATE_RE.match(subject_matter or "")
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group("company")).strip()
    while True:
        stripped = _STATE_RE.sub("", name).strip()
        m = _TICKER_RE.search(stripped)
        if m and not (set(re.findall(r"[A-Z0-9.\-]+", m.group(0))) & _NOT_TICKERS):
            stripped = stripped[: m.start()].strip()
        if stripped == name:
            break
        name = stripped
    return name or None


def _keywords_list(value: Any) -> list[str] | None:
    """Keyword list items as strings; ``None`` when the value is not a list."""
    if isinstance(value, list):
        return [str(v) for v in value]
    return None


def subject_conflicts(subclass: str, subject: str) -> list[str]:
    """Subclasses whose title cue appears in prose *subject* but not the row's
    own (mirrors the dojo schema helper)."""
    if not subject or _TEMPLATE_MARK in subject:
        return []
    own = _SUBJECT_CUES.get(subclass)
    if own is not None and own.search(subject):
        return []
    return [s for s, rx in _SUBJECT_CUES.items() if s != subclass and rx.search(subject)]


def relabel_row(row: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The EDA-side relabel equivalent of ``corporate_record_relabel.relabel_row``.

    Counts as honest (derived from the row itself, never invented) and
    reproduces the published v9.2.1 label repair exactly. ``gt_fields`` stays
    a **dict** (the sidecar shape; the dojo prototype stringified it for the
    parquet hop) and newly-inserted keys keep the dojo insertion order so the
    v9.3 rebuild is byte-identical to the published branch. Returns
    ``(new_row, changes)``; never mutates the input.
    """
    new = dict(row)
    changes: list[str] = []
    if new.get("expected") != "corporate_record":
        return new, changes
    gt = _coerce_gt_fields(new.get("gt_fields"))
    presence = _coerce_presence(gt)
    sub = subclass_for(new.get("expected_subclass"))

    # 1. subclass conflicts (prose subject_matter only)
    conflicts = subject_conflicts(sub, str(gt.get("subject_matter") or ""))
    reassigned = len(conflicts) == 1
    if reassigned:
        changes.append(f"expected_subclass:{sub}->{conflicts[0]}")
        sub = conflicts[0]
        new["expected_subclass"] = sub

    # 2. entity_name + subject_matter + keywords from the template
    subject = str(gt.get("subject_matter") or "")
    company = company_from_template(subject)
    if company:
        gt["entity_name"] = company
        presence["entity_name"] = "populated"
        gt["subject_matter"] = f"{_PHRASE[sub]} of {company}" + ("" if company.endswith(".") else ".")
        changes += ["entity_name", "subject_matter"]
    kws = _keywords_list(gt.get("keywords"))
    if kws is not None:
        own = sub.replace("_", " ")
        kept = [
            k for k in kws
            if not (_ID_KEYWORD.match(str(k).strip()) or _CIK_KEYWORD.search(str(k)))
        ]
        if company:
            # The grounded phrase replaces the bare subclass-name keyword.
            kept = [k for k in kept if str(k).strip().lower().replace("_", " ") != own]
            kept = [_PHRASE[sub], *kept]
            if company not in kept:
                kept.append(company)
        if kept != kws:
            gt["keywords"] = kept
            changes.append("keywords")

    # record_type: fill when blank/invalid or when the subclass was reassigned;
    # a valid existing token is a label, not a gap.
    current_rt = str(gt.get("record_type") or "")
    if current_rt not in RECORD_TYPES or reassigned:
        if current_rt != SUBCLASS_RECORD_TYPES[sub]:
            changes.append("record_type")
        gt["record_type"] = SUBCLASS_RECORD_TYPES[sub]
    if presence.get("record_type") != "populated":
        presence["record_type"] = "populated"

    # intent: re-pin only auto-labeled intents; never override an adjudicated one.
    if str(gt.get("intent_source") or "") in ("", "heuristic") and gt.get("intent") != SUBCLASS_INTENTS[sub]:
        changes.append(f"intent:{gt.get('intent')}->{SUBCLASS_INTENTS[sub]}")
        gt["intent"] = SUBCLASS_INTENTS[sub]

    # 3. presence extension: inapplicable core fields -> not_applicable;
    #    every other entity field stays UNSTATED-ABSENT (unlabeled).
    applicable = SUBCLASS_CORE_APPLICABLE[sub]
    for f in CORPORATE_ENTITY_CORE_KEYS:
        if f in applicable:
            continue
        if presence.get(f) == "populated" or _is_populated_value(gt.get(f)):
            continue  # a stated value is a label, whatever the subclass says
        presence[f] = "not_applicable"

    out = _relabel_gt_fields(new.get("gt_fields"), gt)
    out["gt_presence"] = json.dumps(presence, sort_keys=True)
    new["gt_fields"] = out
    return new, changes


def _coerce_gt_fields(raw: Any) -> dict[str, Any]:
    """Parse a row's ``gt_fields`` the way the scoring side does — the dojo
    ``gt_metadata.parse_gt_fields`` contract: a mapping, a JSON string, or a
    Python dict-repr string; stringified JSON containers parsed at the field
    level; blank → ``{}``; malformed input raises (relabel_rows catches)."""
    if isinstance(raw, Mapping):
        obj: Any = dict(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            try:
                obj = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                raise ValueError(
                    "gt_fields is neither JSON nor a Python dict literal"
                ) from None
    else:
        raise TypeError(f"gt_fields must be a mapping or string, got {type(raw).__name__}")
    if not isinstance(obj, Mapping):
        raise ValueError(f"gt_fields must decode to a mapping, got {type(obj).__name__}")
    return {str(k): _parse_json_container(v) for k, v in obj.items()}


def _coerce_presence(gt: dict[str, Any]) -> dict[str, str]:
    """The row's existing ``gt_presence`` map (mirror of the dojo
    ``gt_metadata.gt_presence_map``: stringified or parsed, or ``{}``)."""
    raw = gt.get("gt_presence") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _parse_json_container(value: Any) -> Any:
    """Parse a stringified JSON list/object; return *value* unchanged
    otherwise (mirrors ``gt_metadata.parse_json_container``)."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{" or stripped[-1] not in "]}":
        return value
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        return value
    return parsed if isinstance(parsed, (list, dict)) else value


_NULL_TOKENS = frozenset({"null", "none", "n/a", "n.a."})


def _is_empty_value(value: Any) -> bool:
    """Mirror of ``gt_metadata.is_empty_value``: ``None``, blank strings,
    JSON / placeholder absence tokens, and empty collections — including
    their stringified forms."""
    if isinstance(value, str):
        parsed = _parse_json_container(value)
        if parsed is not value:
            return _is_empty_value(parsed)
        stripped = value.strip()
        return not stripped or stripped.lower() in _NULL_TOKENS
    if value is None:
        return True
    if isinstance(value, (list, dict, tuple, set)) and len(value) == 0:
        return True
    return False


def _is_populated_value(v: Any) -> bool:
    """Inverse of :func:`_is_empty_value` — a stated value is a label."""
    return not _is_empty_value(v)


def _relabel_gt_fields(old: Any, gt: dict[str, Any]) -> dict[str, Any]:
    """Merge the relabeled values back into the row's ``gt_fields`` dict,
    preserving the original insertion order for existing keys and appending
    the relabel's new keys in the dojo insertion order (``entity_name`` then
    ``record_type``) so the v9.3 rebuild serializes byte-identically to the
    published v9.2.1 branch. Container values serialize via ``json.dumps``
    exactly like the dojo prototype."""
    base = _coerce_gt_fields(old)
    out = dict(base)
    for k, v in gt.items():
        out[k] = json.dumps(v) if isinstance(v, (list, dict)) else v
    return out


def relabel_rows(rows) -> Iterator[tuple[dict[str, Any], list[str]]]:
    """Relabel every row; yields ``(new_row, changes)`` (never mutates input)."""
    for row in rows:
        try:
            yield relabel_row(row)
        except (TypeError, ValueError):
            # Malformed gt_fields: leave the row untouched so the audit
            # flags it as ``gt_unparseable`` instead of aborting the batch.
            yield dict(row), ["gt_unparseable"]
