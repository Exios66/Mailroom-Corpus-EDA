"""Turn any labeling batch into the review portal's packet; never gold or ingest.

``labeling run`` and ``labeling review`` leave a chunk directory (``runs.jsonl``,
``calls.jsonl``, ``fields.csv`` or ``cuad-labels.jsonl`` or ``intent-labels.jsonl``). This
module writes ``<chunk_dir>/<chunk_dir.name>-review-data.json`` from it, in the packet
contract the portal loads (``review_page.validate_packet``), so every batch reaches the
reviewers in one format:

* field tasks (``contract_entities``, ``merger_entities``, ``merger_consideration``,
  ``correspondence``, ``corporate``, ``claim_checklist``): one record per (document, field).
  The candidate is the two-model resolved ``final_value``; a disagreement or an open
  adjudication has no candidate and says why in ``issues``.
* ``cuad``: one record per document, field ``cuad_clauses``, candidate ``"Category: text"``
  strings, schema-pending and not gold-eligible (the same policy as the production packets).
* ``intent``: one record per document, field ``intent``, candidate the label every pass of
  every model agreed on.

Every record also carries the run (``task``, ``chunk``, ``models``, ``reasoning``,
``prompt_sha256``), the registry policy (``correction_type``, ``dojo_type``, ``nullable``,
``absence_policy``, ``eligible_for_gold``, ``schema_status``) and ``model_outputs``, each
model's own value, status and evidence, so a reviewer can compare the models. Evidence is
always ``{start, end, quote}`` into ``full_document_text`` (Unicode code points, exclusive
end), located with ``verify.locate``; a quote that cannot be located becomes an issue
sentence, never invented offsets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import export, scoring, tasks, verify
from .registry import class_fields
from .review_packet import DECISION_KEY, _json, correction_type
from .review_page import PACKET_SCHEMA, packet_id, validate_packet
from .review_portal import accept_blocker
from .review_production import CLAUSE_FIELDS

PRODUCER = "labeling_batch_review/v1"
CUAD_FIELD = "cuad_clauses"
INTENT_FIELD = "intent"
SCOPE = ("Labeling batch output; every candidate is a proposal. ACCEPT keeps the displayed value "
         "for the private v10 queue and CORRECT supplies the replacement. Nothing here is gold, "
         "staged or deployed until a human decides and the v10 gates pass.")


def _short(value: Any, limit: int = 120) -> str:
    """A value as one short line for an issue sentence."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _start_record(chunk_dir: Path) -> dict[str, Any]:
    """The last start record of ``runs.jsonl`` (it names the task and models).

    Raise ValueError when the chunk has none. Torn journal lines are skipped."""
    starts = [r for r in export.read_journal(chunk_dir / "runs.jsonl") if r.get("task") and r.get("models")]
    if not starts:
        raise ValueError(f"{chunk_dir.name}: runs.jsonl has no start record naming the task and models")
    return starts[-1]


class _Batch:
    """What the run journal says about one chunk directory."""

    def __init__(self, chunk_dir: Path):
        start = _start_record(chunk_dir)
        self.task = str(start["task"])
        self.batch = chunk_dir.name
        self.chunk = str(start.get("chunk") or chunk_dir.name.split("_")[0])
        self.models = [str(m) for m in start["models"]]
        self.reasoning = str(start.get("reasoning") or "off")
        self.set_name = str(start.get("set") or "")
        self.slice = start.get("slice")

    def provenance(self, how: str) -> str:
        """One plain sentence on where a record came from and what its candidate is."""
        where = f"{self.task}, {self.set_name} set" if self.set_name else self.task
        if self.slice:
            where += f", slice {self.slice}"
        who = " and ".join(self.models)
        return (f"Batch {self.chunk} ({where}), labeled by {who} at reasoning {self.reasoning}. "
                f"The candidate is {how}. It is a proposal for human review, not a gold label.")


def _decode(text: Any, kind: str) -> Any:
    """The native value of a ``fields.csv`` cell: a JSON list for entity lists, a JSON
    number for money, otherwise the text. A cell that does not decode is returned as text."""
    if text in (None, ""):
        return None
    if not isinstance(text, str):
        return text
    if kind.startswith("entity_list") or kind == "money":
        try:
            value = json.loads(text)
        except ValueError:
            return text
        if kind.startswith("entity_list"):
            return value if isinstance(value, list) else text
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else text
    return text


def _number(value: Any) -> Any:
    """A money candidate as a number when its text parses as an amount."""
    if isinstance(value, str):
        parsed = verify.parse_money(value)
        return parsed if parsed is not None else value
    return value


def _span(index: verify.DocIndex, quote: str) -> dict[str, Any] | None:
    """The first location of ``quote`` as ``{start, end, quote}``, or None.

    The quote is the document's own text at the span (``verify.locate`` also matches
    across whitespace, case and markdown marks, which the page needs verbatim)."""
    if not isinstance(quote, str) or not quote.strip():
        return None
    found = verify.locate(index.text, quote, index.folded)
    if found is None or not 0 <= found[0] < found[1] <= len(index.text):
        return None
    return {"start": found[0], "end": found[1], "quote": index.text[found[0]:found[1]]}


def _merge(spans: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Distinct spans (first occurrence of each start and end wins), in document order."""
    seen: dict[tuple[int, int], dict[str, Any]] = {}
    for span in spans:
        seen.setdefault((span["start"], span["end"]), dict(span))
    return [seen[key] for key in sorted(seen)]


def _dojo_type(doc_class: str, field: str, fallback: str) -> str:
    """The pinned Dojo type, or ``fallback`` (the registry type) when the catalog has no entry."""
    try:
        return scoring.dojo_type(doc_class, field)
    except (ValueError, KeyError):
        return fallback


def _record(batch: _Batch, row: Mapping[str, Any], field: str, spec: Mapping[str, Any], *, candidate: Any,
            presence: str, status: str, issues: list[str], evidence: list[dict[str, Any]],
            outputs: list[dict[str, Any]], prompt_sha256: str, how: str) -> dict[str, Any]:
    """One portal target with the shared record contract; ``accept_blocker`` can still demote it.

    Clause fields (``CLAUSE_FIELDS``) are schema-pending proposals, never gold-eligible."""
    clause = field in CLAUSE_FIELDS
    text = str(row["doc_text"])
    doc_id, filename = tasks._ids(row)
    gt = row.get("gt_fields")
    meta = row["metadata"] if isinstance(row.get("metadata"), dict) else {}
    kind = spec["type"]
    record = {
        "document_id": doc_id, "field": field, "filename": filename, "doc_class": row["expected"],
        "subclass": row.get("expected_subclass") or "", "split": row.get("split") or "",
        "source_url": str(row.get("source_url") or meta.get("source_url") or ""),
        "full_document_text": text, "document_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "kind": "labeling_batch", "task": batch.task, "chunk": batch.chunk, "batch": batch.batch,
        "set": batch.set_name, "slice": batch.slice, "models": list(batch.models),
        "reasoning": batch.reasoning, "prompt_sha256": prompt_sha256,
        "candidate_labels": candidate, "candidate_presence": presence, "candidate_status": status,
        "correction_type": correction_type(kind), "dojo_type": _dojo_type(row["expected"], field, kind),
        "nullable": spec["absence"] != "never_absent", "absence_policy": spec["absence"],
        "eligible_for_gold": not clause, "schema_status": "schema_pending" if clause else "pinned_registry",
        "schema_alignment": "proposal_pending" if clause else "aligned",
        "evidence": _merge(evidence), "issues": issues, "model_outputs": outputs,
        "existing_gt_fields": gt if gt is not None else {}, "human_approval": "pending",
        "review_provenance": batch.provenance(how),
    }
    if status == "source_checks_passed" and (blocker := accept_blocker(record)):
        record["candidate_status"] = "needs_source_review"
        issues.append(f"Source checks failed for this candidate, so it cannot be accepted as is: {blocker}.")
    return record


def _stage_index(rows: Sequence[Mapping[str, Any]], needed: Sequence[str]) -> dict[str, Mapping[str, Any]]:
    """Stage rows by document id for the documents a chunk answered.

    Raise ValueError for a document the stage lacks or one without text."""
    by_id = {tasks._ids(r)[0]: r for r in rows}
    missing = sorted({d for d in needed if d not in by_id})
    if missing:
        raise ValueError(f"{len(missing)} chunk document(s) are not in the stage rows, e.g. {missing[:3]}")
    empty = sorted(d for d in set(needed) if not isinstance(by_id[d].get("doc_text"), str) or not by_id[d]["doc_text"])
    if empty:
        raise ValueError(f"stage rows without document text: {empty[:3]}")
    return by_id


def _registry_spec(row: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    """The registry spec of ``field`` for this row's class and subclass (as ``tasks`` selects it).

    Raise ValueError when the field does not apply to the row."""
    cls = row["expected"]
    sub = (row.get("expected_subclass") or "other") if cls == "corporate_record" else None
    try:
        return class_fields(cls, sub)[field]
    except KeyError:
        raise ValueError(f"{tasks._ids(row)[0]}: {cls}.{field} is not in the pinned field registry") from None


# ------------------------------------------------------------------ field tasks

def _field_records(batch: _Batch, chunk_dir: Path, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One record per ``fields.csv`` row, with both models' answers side by side."""
    path = chunk_dir / "fields.csv"
    if not path.exists():
        raise ValueError(f"{chunk_dir.name}: fields.csv is missing; run `labeling review` first")
    fields = export.read_csv(path)
    docs = ({d["document_id"]: d for d in export.read_csv(chunk_dir / "documents.csv")}
            if (chunk_dir / "documents.csv").exists() else {})
    by_id = _stage_index(rows, [f["document_id"] for f in fields])
    indexes: dict[str, verify.DocIndex] = {}
    out = []
    for f in fields:
        row = by_id[f["document_id"]]
        index = indexes.setdefault(f["document_id"], verify.DocIndex.build(str(row["doc_text"])))
        name = f["field"]
        spec = _registry_spec(row, name)
        kind = spec["type"]
        issues: list[str] = []
        spans: list[dict[str, Any]] = []
        outputs = []
        for slot in ("a", "b"):
            model = f.get(f"model_{slot}")
            if not model:
                continue
            status = f.get(f"model_{slot}_status") or "not_found"
            quote = f.get(f"model_{slot}_evidence") or ""
            span = _span(index, quote)
            if quote and span is None:
                issues.append(f"The evidence quote from {model} could not be located in the document "
                              "text, so it is not highlighted.")
            spans += [span] if span else []
            outputs.append({"model": model, "status": status, "evidence": quote,
                            "value": _decode(f.get(f"model_{slot}_value"), kind) if status == "present" else None,
                            "evidence_spans": [span] if span else []})
        decision, reason = f.get("final_decision") or "adjudicate", f.get("reason") or ""
        candidate, presence, status = None, "pending_annotation", "needs_source_review"
        if decision == "accept":
            candidate = _decode(f.get("final_value"), kind)
            candidate = _number(candidate) if kind == "money" else candidate
            presence, status = "populated", "source_checks_passed"
            how = "the value the models agreed on after the evidence and grounding checks"
            if f.get("reviewer_verdict"):
                how = f"the value fixed by an earlier reviewer verdict ({f['reviewer_verdict']})"
        elif decision == "absent":
            presence, status = "schema_documented_absence", "source_checks_passed"
            how = "a documented absence: the field has no value in the source"
        elif decision == "unlabeled":
            how = "empty: no label was recorded"
            issues.append(f"No label was recorded ({reason}). Read the source: correct it with a value, "
                          "or leave the field unlabeled.")
        else:
            how = "left open because no value was agreed"
            answered = [o for o in outputs if o["status"] == "present"]
            if len(outputs) == 2 and reason == "models disagree":
                issues.append(f"The models disagree: {outputs[0]['model']} answered "
                              f"{_short(outputs[0]['value'])}, {outputs[1]['model']} answered "
                              f"{_short(outputs[1]['value'])}.")
            elif len(outputs) == 1 and answered:
                issues.append(f"Only {outputs[0]['model']} answered ({_short(outputs[0]['value'])}); "
                              "a second reading or a reviewer must confirm it.")
            else:
                issues.append(f"Needs a reviewer decision: {reason or 'no value was agreed'}.")
        if f.get("reviewer_verdict"):
            note = f" ({_short(f['reviewer_note'])})" if f.get("reviewer_note") else ""
            issues.append(f"An earlier review recorded the verdict '{f['reviewer_verdict']}'{note}; "
                          "it is not a human approval.")
        if name == "claim_checklist" and isinstance(candidate, list):
            score = scoring.score_checklist(candidate, index)
            spans = [{"start": e.start, "end": e.end, "quote": index.text[e.start:e.end],
                      "category": e.category, "item": e.item} for e in score.evidence] + spans
            if not score.passed:
                status = "needs_source_review"
                issues += [f"Checklist item {x.item + 1}: {x.code.replace('_', ' ')}." if x.item is not None
                           else f"Checklist: {x.code.replace('_', ' ')}." for x in score.findings]
        out.append(_record(batch, row, name, spec, candidate=candidate, presence=presence, status=status,
                           issues=issues, evidence=spans, outputs=outputs, how=how,
                           prompt_sha256=(docs.get(f["document_id"]) or {}).get("prompt_sha256", "")))
    return out


# ------------------------------------------------------------------ cuad

def _clauses(rec: Mapping[str, Any]) -> list[tuple[int, str, str]]:
    """(start, category, text) of every kept CUAD span of one journal record, in document order."""
    return sorted((int(s["start"]), str(cat), str(s["text"]))
                  for cat, spans in (rec.get("cuad_clause_labels") or {}).items() for s in spans)


def _cuad_records(batch: _Batch, chunk_dir: Path, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One record per document from ``cuad-labels.jsonl``; clauses are proposals, never gold."""
    path = chunk_dir / "cuad-labels.jsonl"
    if not path.exists():
        raise ValueError(f"{chunk_dir.name}: cuad-labels.jsonl is missing; run `labeling review` first")
    per_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in export.read_journal(path):
        per_doc[str(rec["document_id"])].append(rec)
    by_id = _stage_index(rows, list(per_doc))
    dropped: dict[str, list[dict[str, str]]] = defaultdict(list)  # spans.csv: clauses that never made a record
    if (chunk_dir / "spans.csv").exists():
        for s in export.read_csv(chunk_dir / "spans.csv"):
            if s.get("kept") == "false":
                dropped[s["document_id"]].append(s)
    out = []
    for doc_id in sorted(per_doc):
        row, index = by_id[doc_id], verify.DocIndex.build(str(by_id[doc_id]["doc_text"]))
        recs = sorted(per_doc[doc_id], key=lambda r: batch.models.index(r["model"]) if r["model"] in batch.models
                      else len(batch.models))
        spec = _registry_spec(row, CUAD_FIELD)
        issues: list[str] = []
        outputs, per_model = [], []
        for rec in recs:
            spans, items = [], []
            for start, cat, text in _clauses(rec):
                if index.text[start:start + len(text)] != text:
                    issues.append(f"A {cat} span from {rec['model']} does not match the document text at "
                                  f"offset {start} and is left out.")
                    continue
                spans.append({"start": start, "end": start + len(text), "quote": text, "category": cat})
                items.append(f"{cat}: {text}")
            per_model.append((rec, spans, items))
            outputs.append({"model": rec["model"], "value": items, "status": "present" if items else "not_found",
                            "evidence": "", "evidence_spans": spans, "accepted": bool(rec.get("accepted")),
                            "span_count": int(rec.get("span_count") or 0),
                            "repaired": bool(rec.get("repaired"))})
        chosen = next((p for p in per_model if p[0].get("accepted")), per_model[0])
        rec, spans, items = chosen
        status = "source_checks_passed" if rec.get("accepted") and items else "needs_source_review"
        for problem in list(rec.get("problems") or []) + list(rec.get("dropped") or []):
            issues.append(f"Clause check for {rec['model']}: {_short(problem, 200)}.")
        issues += [f"A {s['category']} clause was dropped ({s['drop_reason'] or 'failed verification'}): "
                   f"{_short(s['text'], 160)}" for s in dropped.get(doc_id, [])]
        if not items:
            issues.append(f"{rec['model']} produced no clause that verified against the document.")
        if len(per_model) > 1 and len({tuple(p[2]) for p in per_model}) > 1:
            status = "needs_source_review"
            issues.append("The models disagree on the clauses: " + "; ".join(
                f"{p[0]['model']} found {len(p[2])}" for p in per_model) + ".")
        out.append(_record(batch, row, CUAD_FIELD, spec, candidate=items or None,
                           presence="populated" if items else "pending_annotation", status=status,
                           issues=issues, evidence=spans, outputs=outputs,
                           prompt_sha256=str(rec.get("prompt_sha256") or ""),
                           how=f"the clauses {rec['model']} proposed, each located in the text"))
    return out


# ------------------------------------------------------------------ intent

def _intent_records(batch: _Batch, chunk_dir: Path, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One record per document from ``intent-labels.jsonl``; both passes of every model are shown."""
    path = chunk_dir / "intent-labels.jsonl"
    if not path.exists():
        raise ValueError(f"{chunk_dir.name}: intent-labels.jsonl is missing; run `labeling review` first")
    per_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in export.read_journal(path):
        per_doc[str(rec["document_id"])].append(rec)
    by_id = _stage_index(rows, list(per_doc))
    out = []
    for doc_id in sorted(per_doc):
        row, index = by_id[doc_id], verify.DocIndex.build(str(by_id[doc_id]["doc_text"]))
        spec = _registry_spec(row, INTENT_FIELD)
        issues: list[str] = []
        spans, outputs, votes = [], [], []
        for rec in sorted(per_doc[doc_id], key=lambda r: r["model"]):
            for variant in ("a", "b"):
                got = rec.get(f"pass_{variant}") or {}
                label, quote = got.get("intent"), str(got.get("evidence") or "")
                span = _span(index, quote)
                if quote and span is None:
                    issues.append(f"The evidence quote from {rec['model']} pass {variant} could not be "
                                  "located in the document text, so it is not highlighted.")
                spans += [span] if span else []
                votes.append((rec["model"], variant, label))
                outputs.append({"model": rec["model"], "pass": variant, "value": label,
                                "status": "present" if label else "not_found", "evidence": quote,
                                "evidence_spans": [span] if span else [], "confidence": got.get("confidence")})
        labels = {v[2] for v in votes}
        agreed = next(iter(labels)) if len(labels) == 1 and None not in labels else None
        if agreed is None:
            issues.append("The intent readings disagree or are missing: " + "; ".join(
                f"{m} pass {v}: {l or 'none'}" for m, v, l in votes) + ".")
        prompt = next((r.get("prompt_sha256") for r in per_doc[doc_id] if r.get("prompt_sha256")), "")
        out.append(_record(batch, row, INTENT_FIELD, spec, candidate=agreed,
                           presence="populated" if agreed else "pending_annotation",
                           status="source_checks_passed" if agreed else "needs_source_review",
                           issues=issues, evidence=spans, outputs=outputs, prompt_sha256=str(prompt or ""),
                           how="the label every pass of every model agreed on"))
    return out


# ------------------------------------------------------------------ packet

def _stage_dir(stage: Path | None) -> Path:
    """The stage directory (``ground_truth_hardened.jsonl`` inside); a path to the file also works."""
    from .runner import DEFAULT_STAGE  # noqa: PLC0415

    path = Path(stage) if stage is not None else DEFAULT_STAGE
    return path.parent if path.is_file() else path


def build_packet(chunk_dir: Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The portal packet for one chunk directory and its stage rows (nothing is written).

    Raise ValueError for a chunk without a start record or derived files, documents
    missing from the stage, fields outside the registry, or an invalid packet.
    Registry KeyError and read errors propagate."""
    chunk_dir = Path(chunk_dir)
    batch = _Batch(chunk_dir)
    if batch.task == "cuad":
        records = _cuad_records(batch, chunk_dir, rows)
    elif batch.task == "intent":
        records = _intent_records(batch, chunk_dir, rows)
    else:
        records = _field_records(batch, chunk_dir, rows)
    if not records:
        raise ValueError(f"{chunk_dir.name}: no answered documents to review")
    records.sort(key=lambda r: (r["document_id"], r["field"]))
    packet = {"schema": PACKET_SCHEMA, "producer": PRODUCER, "decision_key": DECISION_KEY, "scope": SCOPE,
              "records": records, "packet_id": packet_id(records),
              "source": {"kind": "labeling_batch", "task": batch.task, "chunk": batch.chunk,
                         "batch": batch.batch, "models": batch.models, "reasoning": batch.reasoning}}
    validate_packet(packet)
    return packet


def export_chunk(chunk_dir: Path, *, stage: Path | None = None,
                 rows: Sequence[Mapping[str, Any]] | None = None) -> Path:
    """Write ``<chunk_dir>/<chunk_dir.name>-review-data.json`` and return its path.

    Read the task, models and reasoning from ``runs.jsonl`` and the answers from the files
    ``labeling review`` writes. Stage rows come from ``rows`` or from ``stage`` (default
    ``runner.DEFAULT_STAGE``; a directory, or the ``ground_truth_hardened.jsonl`` in it).
    Identical bytes leave the file untouched. Different bytes replace it: the portal
    invalidates a confirmation whose target changed, so a re-export after a re-review
    reopens exactly the fields that changed. The replacement is atomic, so a running portal
    never reads half a packet. Raise ValueError as ``build_packet`` does and refuse a symlinked
    path; filesystem and stage-read errors propagate."""
    chunk_dir = Path(chunk_dir)
    if rows is None:
        from .runner import load_stage  # noqa: PLC0415

        rows = load_stage(_stage_dir(stage))
    content = (_json(build_packet(chunk_dir, rows)) + "\n").encode("utf-8")
    path = chunk_dir / f"{chunk_dir.name}-review-data.json"
    if path.is_symlink():
        raise ValueError("review packet path must not be a symlink")
    if path.exists() and path.read_bytes() == content:
        return path
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(content)
    tmp.chmod(0o600)
    os.replace(tmp, path)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    """Export one chunk directory as a portal packet; reads local files only, never calls a model."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--chunk-dir", required=True, type=Path)
    parser.add_argument("--stage", type=Path, default=None, help="stage directory (default: data/v9_4/stage)")
    args = parser.parse_args(argv)
    try:
        print(export_chunk(args.chunk_dir, stage=args.stage))
    except (ValueError, KeyError, TypeError, OSError) as err:
        parser.exit(1, f"review export refused: {err}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
