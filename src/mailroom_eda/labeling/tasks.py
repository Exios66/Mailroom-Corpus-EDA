"""Labeling tasks: turn one dataset row into messages and a strict JSON schema.

Tasks:

* ``cuad``: CUAD clause spans for an EX-10 contract (one model; calibrated on the 40
  golden CUAD contracts; ingest re-verifies spans with ``v9_4_revision.verify_cuad_labels``).
* ``intent``: correspondence intent in two passes (A sees the subject line, B does not).
* ``contract_entities``, ``merger_entities``, ``correspondence``, ``claim_checklist``,
  ``corporate``: field extraction with evidence, run by two models whose answers are
  compared field by field (``verify.decide``).
* ``merger_consideration``: the merger Type of Consideration label from the conversion
  clauses. It is asked on every merger, so the 95 rows with the expert MAUD answer act as
  calibration gold for the 56 without it.

Rows are v9.4/v10 stage rows (``expected``, ``expected_subclass``, ``doc_text``,
``gt_fields`` as a dict or JSON string). The window rules are fixed, so a replay sees the
same text: long documents show the first 48,000 and last 16,000 characters; merger
agreements show the opening plus the located sections each field needs.
"""
from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .derive import cuad_family as _derive_cuad_family
from .derive import deterministic_value
from .registry import class_fields, dojo_snapshot, prompt, prompt_bundle_sha, vocabulary
from . import sections
from .verify import STATUSES

HEAD_CHARS = 48_000
TAIL_CHARS = 16_000
#: CUAD sees the whole contract: the golden calibration contracts were chosen to fit 32k
#: tokens, and a 64k-character window cut the middle out of 6 of 40 (2026-11-06). Only a
#: document past about 100k tokens (one 616k-character EX-10 exhibit) is still windowed.
CUAD_HEAD_CHARS = 360_000
CUAD_TAIL_CHARS = 40_000
#: Contract entities read as much as CUAD does: a 64k-character window cut the middle out of
#: 140 of 597 contracts, where a clause both models then miss was recorded as absent (2026-11-06).
CONTRACT_HEAD_CHARS = CUAD_HEAD_CHARS
CONTRACT_TAIL_CHARS = CUAD_TAIL_CHARS
MERGER_HEAD_CHARS = 24_000
CONSIDERATION_HEAD_CHARS = 6_000
#: Tasks whose ``deterministic`` field is asked on every row, so the derivable rows serve
#: as calibration gold for the rest.
CALIBRATED_DETERMINISTIC = {"merger_consideration": "merger_consideration"}


def gt_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    """The row's ``gt_fields`` as a dict."""
    g = row.get("gt_fields") or {}
    return json.loads(g) if isinstance(g, str) else dict(g)


def _json_obj(value: Any) -> Any:
    """Decode a JSON-string value; pass containers through."""
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def window(text: str, head: int = HEAD_CHARS, tail: int = TAIL_CHARS) -> tuple[str, str]:
    """The text the model sees, and a description of it."""
    if len(text) <= head + tail:
        return text, "the full document"
    return (text[:head] + "\n\n[... middle of the document omitted ...]\n\n" + text[-tail:],
            f"the first {head:,} and last {tail:,} characters (the middle is omitted)")


#: The merger tasks that see a located window rather than the whole agreement.
WINDOWED_MERGER_TASKS = frozenset({"merger_entities", "merger_consideration"})
#: Tasks sent the whole document instead of their window (``run --full-text``; A/B only).
FULL_TEXT_TASKS: set[str] = set()
MERGER_WINDOW_CAP = 120_000
CONSIDERATION_WINDOW_CAP = 40_000


def _located_window(text: str, needs, head: int, cap: int, tail: int) -> tuple[str, str]:
    """The opening pages, each needed section whole (``sections.locate``) and the last
    ``tail`` characters, at most ``cap`` characters in all."""
    if len(text) <= cap:
        return text, "the full document"
    sep, signature = "\n\n", "[... signature pages ...]\n" + text[-tail:] if tail else ""
    # every part after the first costs its separator too, the signature pages included
    budget = cap - head - (len(sep) + len(signature) if signature else 0)
    parts, found = [text[:head]], []
    for label, excerpt in sections.locate(text, needs):
        room = budget - len(sep)
        if room <= 0:
            break
        parts.append(f"[... section located for {label} ...]\n{excerpt}"[:room])
        budget -= len(sep) + len(parts[-1])
        found.append(label)
    if signature:
        parts.append(signature)
    desc = (f"the first {head:,} characters, the located sections ({', '.join(found) or 'none'})"
            + (" and the signature pages" if tail else ""))
    return sep.join(parts), desc


def merger_window(text: str) -> tuple[str, str]:
    """Opening pages, each section the merger fields need (whole, with its definitions) and
    the signature pages."""
    if "merger_entities" in FULL_TEXT_TASKS:
        return text, "the full document"
    return _located_window(text, sections.MERGER_NEEDS, MERGER_HEAD_CHARS, MERGER_WINDOW_CAP, 8_000)


def consideration_window(text: str) -> tuple[str, str]:
    """Opening pages plus the conversion, merger-consideration and election sections."""
    if "merger_consideration" in FULL_TEXT_TASKS:
        return text, "the full document"
    return _located_window(text, sections.CONSIDERATION_NEEDS, CONSIDERATION_HEAD_CHARS,
                           CONSIDERATION_WINDOW_CAP, 0)


def window_config(task: str) -> dict[str, Any]:
    """What shapes a merger task's window, hashed into its prompt sha."""
    if task in FULL_TEXT_TASKS:
        return {"window": "full"}
    cap = MERGER_WINDOW_CAP if task == "merger_entities" else CONSIDERATION_WINDOW_CAP
    return {"window": {"locator": sections.VERSION, "cap": cap}}


cuad_family = _derive_cuad_family


def _value_schema(spec: Mapping[str, Any]) -> dict[str, Any]:
    """JSON schema for one field's ``value``."""
    t = spec["type"]
    if t.startswith("entity_list"):
        return {"type": ["array", "null"], "items": {"type": "string"}}
    if spec.get("vocabulary") and spec.get("retype", t) == "label":
        return {"type": ["string", "null"], "enum": [*vocabulary(spec["vocabulary"]), None]}
    return {"type": ["string", "null"]}


def extraction_schema(fields: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Strict schema: ``{"fields": {name: {"value", "status", "evidence"}}}`` with every field required."""
    props = {
        name: {"type": "object", "additionalProperties": False,
               "required": ["value", "status", "evidence"],
               "properties": {"value": _value_schema(spec),
                              "status": {"type": "string", "enum": list(STATUSES)},
                              "evidence": {"type": "string"}}}
        for name, spec in fields.items()
    }
    return {"type": "object", "additionalProperties": False, "required": ["fields"],
            "properties": {"fields": {"type": "object", "additionalProperties": False,
                                      "required": list(fields), "properties": props}}}


@dataclass(frozen=True)
class Request:
    """Everything needed to send and later verify one call."""

    task: str
    document_id: str
    messages: list[dict[str, str]]
    schema: dict[str, Any]
    fields: dict[str, dict[str, Any]]
    prompt_sha256: str
    variant: str = ""


def _ids(row: Mapping[str, Any]) -> tuple[str, str]:
    """(document_id, filename)."""
    return str(row.get("document_id") or row.get("id") or row["filename"]), str(row["filename"])


def _task_fields(row: Mapping[str, Any], task: str) -> dict[str, dict[str, Any]]:
    """Registry fields this task fills for this row.

    ``llm`` fields of the task always; ``gold`` fields that name the task only where the
    row has no value yet (e.g. the 38 corporate rows without ``entity_name``); and
    ``deterministic`` fields that name the task where they cannot be derived. The
    calibrated ``merger_consideration`` field is included even with an expert MAUD
    answer, so those rows can be used for calibration.
    """
    # A corporate row with no subclass gets the ``other`` schema, never every subclass's.
    sub = (row.get("expected_subclass") or "other") if row["expected"] == "corporate_record" else None
    gt = gt_fields(row)
    out = {}
    for k, v in class_fields(row["expected"], sub).items():
        if v.get("task") != task:
            continue
        src = v.get("source")
        if (src == "llm" or (src == "gold" and gt.get(k) in (None, "", "[]", []))
                or (src == "deterministic" and (CALIBRATED_DETERMINISTIC.get(task) == k
                                                or deterministic_value(row, k) is None))):
            out[k] = v
    return out


def input_sha256(messages: Sequence[Mapping[str, str]]) -> str:
    """Hash of the exact rendered messages a call sends. ``prompt_sha256`` covers the
    templates and config only, so a changed document text needs this to stay unreused."""
    return hashlib.sha256(json.dumps(list(messages), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_cuad(row: Mapping[str, Any], *, repair_problems: Sequence[str] = (),
               prior_answer: str = "") -> Request:
    """Messages for the CUAD clause task.

    With ``repair_problems``, the model's first answer (``prior_answer``) is replayed as
    the assistant turn and the repair prompt follows it (variant ``repair``).
    """
    doc_id, filename = _ids(row)
    fam = cuad_family(row.get("expected_subclass") or "") or {}
    cats = dojo_snapshot()["cuad_categories"]
    text, desc = window(str(row["doc_text"]), CUAD_HEAD_CHARS, CUAD_TAIL_CHARS)
    user = prompt("cuad_user").render({
        "document_id": doc_id, "filename": filename,
        "subclass": fam.get("key") or row.get("expected_subclass") or "unknown",
        "family_definition": fam.get("definition") or "no family definition",
        "n_chars": f"{len(row['doc_text']):,}", "window_description": desc,
        "category_list": "\n".join(f"- {c['name']}: {c['description']}" for c in cats),
        "doc_text_window": text,
    })
    msgs = [{"role": "system", "content": prompt("cuad_system").render({})},
            {"role": "user", "content": user}]
    names = ["cuad_system", "cuad_user"]
    if repair_problems:
        msgs.append({"role": "assistant", "content": prior_answer})
        msgs.append({"role": "user", "content": prompt("cuad_repair").render(
            {"document_id": doc_id, "problems": "\n".join(f"- {p}" for p in repair_problems)})})
        names.append("cuad_repair")
    schema = {"type": "object", "additionalProperties": False, "required": ["clauses"],
              "properties": {"clauses": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False, "required": ["category", "text"],
                  "properties": {"category": {"type": "string", "enum": [c["name"] for c in cats]},
                                 "text": {"type": "string"}}}}}}
    return Request("cuad", doc_id, msgs, schema, {}, prompt_bundle_sha(names, {"categories": cats,
                                                                  "window": [CUAD_HEAD_CHARS, CUAD_TAIL_CHARS]}),
                   "repair" if repair_problems else "")


def build_intent(row: Mapping[str, Any], variant: str) -> Request:
    """Pass ``a`` (subject line shown) or ``b`` (body only) of the intent task."""
    from ..intent_backfill import extract_subject, strip_email_headers  # noqa: PLC0415

    if variant not in ("a", "b"):
        raise ValueError("intent variant is 'a' or 'b'")
    doc_id, _ = _ids(row)
    raw = str(row["doc_text"])
    body = raw if variant == "a" else strip_email_headers(raw)
    subject = extract_subject(raw) if variant == "a" else ""
    text, _ = window(body, 6_000, 2_000)
    user = prompt("intent_user").render({"document_id": doc_id,
                                         "subject_line": f"Subject: {subject}" if subject else "",
                                         "doc_text_window": text})
    vocab = vocabulary("correspondence_intent")
    schema = {"type": "object", "additionalProperties": False,
              "required": ["intent", "confidence", "evidence"],
              "properties": {"intent": {"type": "string", "enum": vocab},
                             "confidence": {"type": "number"},
                             "evidence": {"type": "string"}}}
    return Request("intent", doc_id,
                   [{"role": "system", "content": prompt("intent_system").render({})},
                    {"role": "user", "content": user}],
                   schema, {}, prompt_bundle_sha(["intent_system", "intent_user"], {"vocabulary": vocab}),
                   variant)


def _extraction(row: Mapping[str, Any], task: str, template: str, values: dict[str, Any],
                text_window: tuple[str, str] | None = None, extra_config: Mapping[str, Any] | None = None) -> Request:
    """Shared builder for the extraction tasks."""
    doc_id, _ = _ids(row)
    fields = _task_fields(row, task)
    if not fields:
        raise ValueError(f"{task}: no fields to label for {row['expected']} / {row.get('expected_subclass')}")
    text, desc = text_window or window(str(row["doc_text"]))
    tpl = prompt(template)
    vals = {k: v for k, v in {**values, "doc_text_window": text, "window_description": desc}.items()
            if k in tpl.placeholders}
    user = tpl.render(vals)
    system = prompt("extract_rules").render({})
    return Request(task, doc_id, [{"role": "system", "content": system},
                                  {"role": "user", "content": user}],
                   extraction_schema(fields), fields,
                   prompt_bundle_sha(["extract_rules", template],
                                     {**_task_config(fields), **(extra_config or {})}))


def _task_config(fields: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """What besides the templates shapes a task's prompt and schema: the field specs and
    the vocabularies they name. Hashed into ``prompt_sha256`` so a re-run after an edit to
    either does not reuse old answers."""
    vocabs = sorted({str(f["vocabulary"]) for f in fields.values() if f.get("vocabulary")})
    return {"fields": {k: dict(v) for k, v in sorted(fields.items())},
            "vocabularies": {v: vocabulary(v) for v in vocabs}}


def build_contract_entities(row: Mapping[str, Any], *, expert_hints: bool = True) -> Request:
    """ContractExtraction v2 entity fields (the CUAD checklist is the ``cuad`` task).

    ``expert_hints=False`` (the calibration set) leaves out the expert CUAD spans: those
    rows are scored against the same spans, so showing them would leak the answer.
    """
    fam = cuad_family(row.get("expected_subclass") or "") or {}
    hints = "; ".join(f"{f['name']}: {f['description']}" for f in fam.get("family_fields", [])) or "none"
    spans = _expert_span_hints(row) if expert_hints else ""
    return _extraction(row, "contract_entities", "contract_entities", {
        "subclass": fam.get("key") or row.get("expected_subclass") or "unknown",
        "family_definition": fam.get("definition") or "no family definition",
        "party_roles": ", ".join(vocabulary("contract_party_role")),
        "family_field_hints": hints, "expert_span_hints": spans,
    }, window(str(row["doc_text"]), CONTRACT_HEAD_CHARS, CONTRACT_TAIL_CHARS),
        {"window": [CONTRACT_HEAD_CHARS, CONTRACT_TAIL_CHARS]})


def reads_in_part(row: Mapping[str, Any], task: str) -> bool:
    """True when ``task`` shows the model only part of ``row``'s text, so finding nothing
    is not evidence of absence (contract entities only, as approved 2026-11-06)."""
    return task == "contract_entities" and len(str(row["doc_text"])) > CONTRACT_HEAD_CHARS + CONTRACT_TAIL_CHARS


_SPAN_HINT_CATEGORIES = ("Document Name", "Parties", "Agreement Date", "Effective Date", "Expiration Date",
                         "Renewal Term", "Notice Period To Terminate Renewal", "Governing Law")


def _expert_span_hints(row: Mapping[str, Any]) -> str:
    """Expert CUAD spans for the categories that map onto entity fields (CUAD rows only)."""
    labels = _json_obj(gt_fields(row).get("cuad_clause_labels"))
    if not isinstance(labels, dict) or not any(labels.values()):
        return ""
    lines = []
    for cat in _SPAN_HINT_CATEGORIES:
        for span in labels.get(cat) or []:
            text = span.get("text") if isinstance(span, dict) else span
            if text:
                lines.append(f"{cat}: {str(text)[:400]}")
    if not lines:
        return ""
    return ("Expert CUAD spans for this document (prefer values stated in these spans):\n"
            + "\n".join(lines))


def build_merger_entities(row: Mapping[str, Any]) -> Request:
    """MergerAgreementExtraction v2 entity fields (consideration is its own task)."""
    return _extraction(row, "merger_entities", "merger_entities", {
        "merger_intents": " | ".join(vocabulary("merger_intent")),
    }, merger_window(str(row["doc_text"])), window_config("merger_entities"))


def build_merger_consideration(row: Mapping[str, Any]) -> Request:
    """The merger Type of Consideration label, from a window of the conversion clauses."""
    return _extraction(row, "merger_consideration", "merger_consideration", {
        "consideration_options": " | ".join(vocabulary("merger_consideration")),
    }, consideration_window(str(row["doc_text"])), window_config("merger_consideration"))


def build_correspondence(row: Mapping[str, Any]) -> Request:
    """Correspondence entity fields."""
    return _extraction(row, "correspondence", "correspondence", {
        "subclass": row.get("expected_subclass") or "email",
        "urgencies": " | ".join(vocabulary("urgency")),
    }, window(str(row["doc_text"]), 12_000, 4_000))


def build_claim_checklist(row: Mapping[str, Any]) -> Request:
    """The insurance claim checklist."""
    return _extraction(row, "claim_checklist", "claim_checklist", {
        "subclass": row.get("expected_subclass") or "claim",
        "checklist_categories": ", ".join(vocabulary("claim_checklist_category")),
    })


def build_corporate(row: Mapping[str, Any]) -> Request:
    """Corporate core and subclass entity fields for the row's subclass."""
    sub = str(row.get("expected_subclass") or "other")
    schema = dojo_snapshot()["corporate_subclass_schemas"].get(sub, {})
    ents = {e["key"]: e for e in schema.get("entities", [])}
    fields = _task_fields(row, "corporate")
    lines = []
    for name, spec in fields.items():
        e = ents.get(name)
        desc = f"{e['description']} Where to look: {e['where']}" if e else _CORPORATE_CORE.get(name, "")
        lines.append(f"- {name} ({spec['type']}): {desc}")
    return _extraction(row, "corporate", "corporate", {
        "subclass": sub, "subclass_description": schema.get("summary", ""),
        "field_list": "\n".join(lines),
    })


_CORPORATE_CORE = {
    "entity_name": "The corporation or entity the record governs, legal name as printed.",
    "effective_date": "When the record takes effect (adoption, filing or execution date as stated).",
    "signatories": "People who sign the record, as printed.",
    "jurisdiction": "State or country of incorporation or governing law, as printed.",
    "filing_number": "The state filing office's file, document or entity number.",
}

BUILDERS: dict[str, Callable[..., Request]] = {
    "cuad": build_cuad,
    "contract_entities": build_contract_entities,
    "merger_entities": build_merger_entities,
    "merger_consideration": build_merger_consideration,
    "correspondence": build_correspondence,
    "claim_checklist": build_claim_checklist,
    "corporate": build_corporate,
}
TASK_CLASSES = {
    "cuad": "contract", "intent": "correspondence", "contract_entities": "contract",
    "merger_entities": "merger_agreement", "merger_consideration": "merger_agreement",
    "correspondence": "correspondence",
    "claim_checklist": "insurance_claim", "corporate": "corporate_record",
}
TWO_MODEL_TASKS = frozenset({"contract_entities", "merger_entities", "merger_consideration",
                             "correspondence", "claim_checklist", "corporate"})




def build_generic(row: Mapping[str, Any], task: str) -> Request:
    """Extraction task declared in ``fields.yaml`` ``tasks:`` (no Python builder needed).

    The prompt template receives ``subclass``, ``field_list`` (one line per field from
    its ``description`` and ``where``), ``window_description`` and ``doc_text_window``.
    """
    spec = generic_tasks()[task]
    fields = _task_fields(row, task)
    lines = [f"- {name} ({f.get('retype') or f['type']}): {str(f.get('description', '')).rstrip('.')}."
             + (f" Where to look: {f['where']}" if f.get("where") else "")
             + (f" One of: {' | '.join(vocabulary(f['vocabulary']))}." if f.get("vocabulary") else "")
             for name, f in fields.items()]
    head, tail = spec.get("window", [HEAD_CHARS, TAIL_CHARS])
    return _extraction(row, task, spec.get("prompt", task), {
        "subclass": row.get("expected_subclass") or "unknown", "field_list": "\n".join(lines),
    }, window(str(row["doc_text"]), head, tail))


def generic_tasks() -> dict[str, dict[str, Any]]:
    """Tasks declared in ``fields.yaml`` under ``tasks:`` (see ``integrations/``)."""
    from .registry import fields_registry  # noqa: PLC0415

    return dict(fields_registry().get("tasks") or {})


def _register_generic_tasks() -> None:
    """Add the ``fields.yaml`` tasks to the dispatch tables."""
    global TWO_MODEL_TASKS
    for name, spec in generic_tasks().items():
        if name in BUILDERS or name == "intent":
            raise ValueError(f"fields.yaml task {name!r} shadows a built-in task")
        TASK_CLASSES[name] = spec["class"]
        BUILDERS[name] = lambda row, _t=name: build_generic(row, _t)
        if spec.get("two_model", True):
            TWO_MODEL_TASKS = TWO_MODEL_TASKS | {name}


_register_generic_tasks()


def build(task: str, row: Mapping[str, Any], variant: str = "", *, set_name: str = "") -> Request:
    """Dispatch to the task builder. ``set_name`` matters only for ``contract_entities``,
    whose calibration set is built without the expert span hints."""
    if task == "intent":
        return build_intent(row, variant or "a")
    if task == "contract_entities":
        return build_contract_entities(row, expert_hints=set_name != "calibration")
    return BUILDERS[task](row)
