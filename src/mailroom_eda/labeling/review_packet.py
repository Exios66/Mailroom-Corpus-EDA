"""Offline, full-source checklist candidates for human review; never gold or ingest."""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import html
import io
import json
import re
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import export, scoring, tasks, verify
from .registry import fields_registry

MODEL = "z-ai/glm-5.3-flashx-legal"
FIELD = "claim_checklist"
DECISION_VERSION = "review-decision-v1"
DECISION_KEY = {
    "NOT_REVIEWED": "No decision; does not approve anything. Blank has the same meaning.",
    "ACCEPT": "Accept the displayed reviewed candidate, NOT a raw-model precision 'correct'.",
    "CORRECT": "Supply a complete replacement JSON list in corrected_labels_json; required, never implied.",
    "REJECT": "Leave this field unlabeled; do not promote the candidate.",
    "NEEDS_SOURCE": "Postpone the decision for further source/policy review; no approval.",
}


def _json(value: Any) -> str:
    """Serialize Unicode JSON with sorted object keys."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def correction_type(kind: str) -> str:
    """The JSON type a reviewer supplies for a registry field type.

    ``array`` for entity lists, ``number`` for money, otherwise ``string``. Every packet
    producer uses this one mapping, so the review page and ``validate_return`` agree."""
    return "array" if kind.startswith("entity_list") else "number" if kind == "money" else "string"


def issue_sentence(code: str, item: int | None = None) -> str:
    """A checklist finding as a plain sentence for the review page (``item`` is zero-based)."""
    where = "Checklist" if item is None else f"Checklist item {item + 1}"
    return f"{where}: {code.replace('_', ' ')}."


def _run_config(chunk_dir: Path) -> tuple[str, str]:
    """The (model, reasoning) a checklist packet is built from.

    Read from the last start record of ``runs.jsonl`` that lists models: ``MODEL`` when it
    ran in the chunk, otherwise the chunk's first model. Without such a record the
    original pair, ``MODEL`` at ``low``, applies. Torn journal lines are skipped."""
    starts = [r for r in export.read_journal(chunk_dir / "runs.jsonl")
              if r.get("task") == FIELD and r.get("models")]
    if not starts:
        return MODEL, "low"
    models = [str(m) for m in starts[-1]["models"]]
    return (MODEL if MODEL in models else models[0]), str(starts[-1].get("reasoning") or "low")


def _journal(path: Path) -> list[dict[str, Any]]:
    # A review packet must not silently skip a corrupt journal record.
    """Read nonblank UTF-8 JSONL records, requiring each record to be an object.

    Malformed JSON and non-object records raise ValueError; read errors propagate."""
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]
    if any(not isinstance(record, dict) for record in records):
        raise ValueError(f"{path.name}: journal records must be objects")
    return records


def _fence(text: str, language: str = "") -> str:
    """Wrap text in a Markdown code fence longer than any backtick run in the text."""
    fence = "`" * max(3, 1 + max((len(m[0]) for m in re.finditer(r"`+", text)), default=0))
    return f"{fence}{language}\n{text}\n{fence}\n"


def _csv(rows: Sequence[Mapping[str, str]]) -> str:
    """Render nonempty rows as quoted CSV with reversible formula protection.

    ``escaped_columns`` lists cells prefixed with one protective apostrophe.
    Raise ValueError when any escaped cell exceeds 50,000 UTF-16 code units."""
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=[*rows[0], "escaped_columns"],
                            quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        escaped = [key for key, text in row.items() if text and (
            text[0] in "=+-@'" or text[0].isspace() or unicodedata.category(text[0]).startswith("C"))]
        safe = {key: "'" + text if key in escaped else text for key, text in row.items()}
        safe["escaped_columns"] = _json(escaped)
        for key, text in safe.items():
            # Sheets counts UTF-16 code units, including the protective apostrophe.
            if len(text.encode("utf-16-le")) // 2 > 50_000:
                raise ValueError(f"Google Sheets 50000-character cell limit: {key}; source is not truncated")
        writer.writerow(safe)
    return out.getvalue()


def _workbook(rows: Sequence[Mapping[str, str]]) -> bytes:
    """Literal text cells plus a native decision dropdown; validate before Excel can truncate."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    editable = ("human_decision", "corrected_labels_json", "reviewer_initials", "reviewer_notes")
    first = ("document_id", "filename", "field", "candidate_status", *editable,
             "reviewed_candidate_labels", "full_document_text", "source_evidence_json")
    columns = [*first, *(key for key in rows[0] if key not in first)]
    wb = Workbook()
    wb.properties.created = wb.properties.modified = datetime(2000, 1, 1)
    ws = wb.active
    ws.title = "Checklist review"
    key = wb.create_sheet("Decision key")
    key_rows = [["code / field", "meaning / instructions"],
                ["decision_contract_version", DECISION_VERSION],
                *([code, meaning] for code, meaning in DECISION_KEY.items()),
                ["Editable cells (light yellow)", ", ".join(editable)],
                ["Scope", "claim_checklist only; legacy GT is separate context, not GLM output. "
                 "All human approvals are pending. No automatic ingestion or gold staging."],
                ["CORRECT example — synthetic FORMAT ONLY", 'corrected_labels_json: ["Deductible: Deductible: $500"] ; '
                 'reviewer_initials: AB ; reviewer_notes: Replaced the displayed candidate after full-source review.'],
                ["Return mapping", "Keep document_id, field, split, source hashes and provenance unchanged. "
                 "Decisions concern reviewed candidates, not raw GLM precision. Neither blank nor NOT_REVIEWED approves."],
                ["Checks", "Source checks are not legal correctness, completeness or official Dojo scoring. "
                 "Blocked/source-policy examples remain needs_source_review; existing final decisions are unchanged."],
                ["Full text", "full_document_text is unchanged literal text, including original newlines. "
                 "Workbook cells above 32767 UTF-16 units are refused, never trimmed. "
                 "No formulas are stored; no recalculation is required."],
                ["Review", "Read the entire source and quoted evidence, choose a decision, and record actual human initials. "
                 "Expand the row or use the formula bar to inspect long wrapped cells."]]
    for sheet, table in ((ws, [columns, *([row[c] for c in columns] for row in rows)]), (key, key_rows)):
        for values in table:
            for value in values:
                if len(value.encode("utf-16-le")) // 2 > 32_767:
                    raise ValueError("Excel 32767-character cell limit; full source is not truncated")
                if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", value):
                    raise ValueError("Excel cannot preserve illegal XML control characters; source is not rewritten")
            sheet.append(values)
        for row in sheet:
            for cell in row:
                cell.data_type = "s"  # Even a leading '=' is literal text, never a formula.
                cell.number_format = "@"
                cell.font = Font(name="Arial", size=11, color="172B4D")
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                if cell.row == 1:
                    cell.font = Font(name="Arial", size=11, color="FFFFFF", bold=True)
                    cell.fill = PatternFill("solid", fgColor="245A81")
        sheet.row_dimensions[1].height = 32
        sheet.freeze_panes = "D2" if sheet == ws else "A2"
    ws.auto_filter.ref = ws.dimensions
    for i, column in enumerate(columns, 1):
        ws.column_dimensions[get_column_letter(i)].width = (
            90 if column == "full_document_text" else 65 if column in (
                "reviewed_candidate_labels", "source_evidence_json") else 35)
        if column in editable:
            for cells in ws.iter_cols(min_col=i, max_col=i, min_row=2):
                for cell in cells:
                    cell.fill = PatternFill("solid", fgColor="FFF2CC")
    for i in range(2, ws.max_row + 1):
        ws.row_dimensions[i].height = 120
    key.column_dimensions["A"].width = 44
    key.column_dimensions["B"].width = 110
    for i in range(2, key.max_row + 1):
        key.row_dimensions[i].height = 55
    dropdown = DataValidation(type="list", formula1='"' + ",".join(DECISION_KEY) + '"',
                              allow_blank=True, showDropDown=False, showErrorMessage=True,
                              errorStyle="stop", errorTitle="Choose a listed decision",
                              error="Use NOT_REVIEWED, ACCEPT, CORRECT, REJECT or NEEDS_SOURCE.")
    ws.add_data_validation(dropdown)
    letter = get_column_letter(columns.index("human_decision") + 1)
    dropdown.add(f"{letter}2:{letter}{ws.max_row}")
    output = io.BytesIO()
    wb.save(output)
    # XLSX ZIP/member timestamps otherwise change on every identical offline export.
    stable = io.BytesIO()
    with zipfile.ZipFile(output) as source, zipfile.ZipFile(stable, "w") as target:
        for name in sorted(source.namelist()):
            content = source.read(name)
            if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                # XML newline normalization must not turn original CRLF source into LF.
                content = content.replace(b"\r", b"&#13;")
            if name == "docProps/core.xml":
                content = re.sub(rb"(<dcterms:modified\b[^>]*>).*?(</dcterms:modified>)",
                                 rb"\g<1>2000-01-01T00:00:00Z\g<2>", content)
            info = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, content)
    return stable.getvalue()


def export_packet(rows: Sequence[Mapping[str, Any]], chunk_dir: Path, dest: Path, *,
                  model: str | None = None) -> dict[str, Any]:
    """Validate local candidates before writing CSV, Markdown and dropdown workbook files.

    CSVs use real quoted newlines, not export.py's backslash encoding. To recover an
    original cell, remove exactly one leading apostrophe ONLY for columns named by
    that row's escaped_columns JSON. Neither file is an ingestion interface.

    Also write a JSON packet and return its ID, counts, and output paths. A
    review-candidates.json sidecar includes held examples without accepting them;
    without it only accepted fields are exported. At least one accepted field is
    required. ``model`` is the answer the packet compares with the reviewed candidate;
    by default it and the reasoning level come from the chunk's ``runs.jsonl`` (``MODEL``
    at ``low`` when the chunk has none). Raise ValueError for invalid source/provenance or unrepresentable
    spreadsheet cells before writing outputs. Missing-data KeyError, optional
    workbook ImportError, and filesystem errors propagate; existing files are
    overwritten, and an I/O failure can leave partial output."""
    chunk_dir, dest = Path(chunk_dir), Path(dest)
    run_model, reasoning = _run_config(chunk_dir)
    model = model or run_model
    label = "GLM" if model == MODEL else model
    fields = export.read_csv(chunk_dir / "fields.csv")
    calls = _journal(chunk_dir / "calls.jsonl")
    side_path = chunk_dir / "review-candidates.json"
    sidecar = None
    if side_path.exists():
        candidates = json.loads(side_path.read_text(encoding="utf-8"))
        if not isinstance(candidates, list) or any(not isinstance(c, dict) for c in candidates):
            raise ValueError("review-candidates.json must be a list of candidate objects")
        sidecar = {c["document_id"]: c for c in candidates}
        if len(sidecar) != len(candidates) or set(sidecar) != {f["document_id"] for f in fields}:
            raise ValueError("review-candidates.json document set differs from fields.csv")
    by_id = {}
    for row in rows:
        doc_id = tasks._ids(row)[0]
        if doc_id in by_id:
            raise ValueError(f"duplicate stage document_id: {doc_id}")
        by_id[doc_id] = row
    type_ = scoring.dojo_type("insurance_claim", FIELD)
    pin = fields_registry()["dojo_pin"]
    field_types = {name: scoring.dojo_type("insurance_claim", name)
                   for name in fields_registry()["classes"]["insurance_claim"]["fields"]}
    packet, excluded, seen = [], [], set()
    for field in sorted(fields, key=lambda f: (f["document_id"], f["field"])):
        doc_id = field["document_id"]
        if doc_id in seen:
            raise ValueError(f"duplicate field row: {doc_id}")
        seen.add(doc_id)
        state = field.get("final_decision", "")
        if state != "accept":
            excluded.append({"document_id": doc_id, "final_decision": state,
                             "reason": field.get("reason", ""), "review_note": field.get("reviewer_note", "")})
            if sidecar is None:
                continue
        row = by_id.get(doc_id)
        if row is None:
            raise ValueError(f"{doc_id}: candidate document missing from stage")
        if (field.get("field") != FIELD or field.get("doc_class") != "insurance_claim"
                or row.get("expected") != "insurance_claim" or field.get("type") != type_
                or field.get("subclass") != row.get("expected_subclass")):
            raise ValueError(f"{doc_id}: accepted field identity/type/subclass mismatch")
        if row.get("split") not in ("train", "test"):
            raise ValueError(f"{doc_id}: unknown source split")
        text = row.get("doc_text")
        if not isinstance(text, str) or not text:
            raise ValueError(f"{doc_id}: full document text required")
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        candidate = sidecar[doc_id] if sidecar is not None else None
        if candidate is not None:
            if (candidate.get("doc_sha256") != sha or candidate.get("split") != row["split"]
                    or candidate.get("subclass") != row["expected_subclass"]
                    or candidate.get("reviewer_type") != "AI" or candidate.get("human_approved") is not False
                    or candidate.get("hold") is not (state != "accept")):
                raise ValueError(f"{doc_id}: sidecar source hash/identity/AI hold metadata mismatch")
            value = candidate["items"]
            if state == "accept" and value != json.loads(field["final_value"]):
                raise ValueError(f"{doc_id}: accepted final value differs from AI sidecar")
        else:
            value = json.loads(field["final_value"])
        score = scoring.score_checklist(value, verify.DocIndex.build(text))
        if candidate is not None:
            located = [{"category": e.category, "quote": e.quote, "start": e.start, "end": e.end}
                       for e in score.evidence]
            earliest = min(score.evidence, key=lambda e: e.start).quote[:300] if score.evidence else ""
            if candidate.get("located") != located or candidate.get("evidence") != earliest:
                raise ValueError(f"{doc_id}: sidecar evidence/offsets differ from current source checks")
        if not score.passed and state == "accept":
            raise ValueError(f"{doc_id}: accepted checklist failed source checks: "
                             + ", ".join(f.code for f in score.findings))
        req = tasks.build(FIELD, row)
        input_sha = tasks.input_sha256(req.messages)
        matching = [c for c in calls if c.get("document_id") == doc_id and c.get("model") == model
                    and c.get("reasoning") == reasoning and not c.get("variant") and not c.get("error")
                    and c.get("prompt_sha256") == req.prompt_sha256]
        if not matching:
            raise ValueError(f"{doc_id}: no successful current prompt {label} {reasoning} call")
        call = matching[-1]
        if call.get("filename") != row["filename"] or call.get("task", FIELD) != FIELD:
            raise ValueError(f"{doc_id}: {label} call identity mismatch")
        if "input_sha256" in call and call["input_sha256"] != input_sha:
            raise ValueError(f"{doc_id}: {label} input_sha256 mismatch")
        for key in ("content_sha256", "doc_text_sha256", "document_sha256", "source_sha256"):
            if key in call and call[key] != sha:
                raise ValueError(f"{doc_id}: {label} {key} mismatch")
        response = call.get("response")
        if not isinstance(response, dict) or not isinstance(response.get("fields"), dict):
            raise ValueError(f"{doc_id}: corrupt {label} response")
        raw = response["fields"].get(FIELD)
        if not isinstance(raw, dict) or "value" not in raw or "status" not in raw or "evidence" not in raw:
            raise ValueError(f"{doc_id}: corrupt {label} field response")
        slots = [s for s in ("a", "b") if field.get(f"model_{s}") == model]
        if len(slots) != 1:
            raise ValueError(f"{doc_id}: accepted field does not identify one {label} answer")
        slot = slots[0]
        if (json.loads(field[f"model_{slot}_value"]) != raw["value"]
                or field[f"model_{slot}_status"] != raw["status"]
                or field[f"model_{slot}_evidence"] != raw["evidence"]):
            raise ValueError(f"{doc_id}: fields.csv differs from latest current {label} answer")
        changed = value != raw["value"]
        if changed and candidate is None and not (field.get("reviewer_verdict") or field.get("final_source")):
            raise ValueError(f"{doc_id}: changed candidate has no correction provenance")
        starts, offset = [], 0
        for line in text.splitlines(keepends=True):
            starts.append(offset)
            offset += len(line)
        evidence = [{"item": e.item, "category": e.category,
                     "line": bisect.bisect_right(starts, e.start), "start": e.start, "end": e.end,
                     "quote": text[e.start:e.end]} for e in score.evidence]
        packet.append({
            "document_id": doc_id, "filename": row["filename"], "doc_class": "insurance_claim",
            "source_url": str(row.get("source_url") or (
                row.get("metadata", {}).get("source_url", "") if isinstance(row.get("metadata"), dict) else "")),
            "subclass": row["expected_subclass"], "split": row["split"], "field": FIELD,
            "dojo_type": type_, "dojo_pin_json": _json(pin), "policy": scoring.POLICY_VERSION,
            "existing_gt_fields_json": _json(row.get("gt_fields", {})),
            "dojo_field_types_json": _json(field_types),
            "status": "source_checks_passed" if score.passed else "needs_source_review",
            "candidate_status": "source_checks_passed" if score.passed else "needs_source_review",
            "source_check_issues_json": _json([{"code": f.code, "item": f.item} for f in score.findings]),
            "human_approval": "pending", "final_decision": state,
            "candidate_source": "AI review-candidates.json" if candidate is not None else "fields.csv final_value",
            "held_reason": field.get("reason", "") if state != "accept" else "",
            "ai_sidecar_review_json": _json({k: candidate.get(k) for k in (
                "notes", "reviewer_type", "human_approved", "hold", "hold_reason")}) if candidate is not None else "",
            "dojo_scoring": "not_run; catalog compatibility only", "document_sha256": sha,
            "model": model, "provider": str(call.get("provider") or ""),
            "served_provider": str(call.get("served_provider") or ""),
            "served_model": str(call.get("served_model") or ""), "reasoning": reasoning,
            "prompt_sha256": req.prompt_sha256, "input_sha256": str(call.get("input_sha256") or ""),
            "expected_input_sha256": input_sha,
            "input_binding": "journal_hash_matched" if "input_sha256" in call else "legacy_journal_hash_unavailable",
            "labeled_utc": str(call.get("labeled_utc") or ""),
            "raw_glm_field_json": _json(raw), "raw_glm_labels_json": _json(raw["value"]),
            "reviewed_candidate_labels_json": _json(value),
            "candidate_attribution": (f"AI source-review correction; not raw {label}" if changed
                                      else f"unchanged raw {label} candidate"),
            "correction_provenance_json": _json({k: field.get(k, "") for k in (
                "reviewer_verdict", "reviewer_value", "reviewer_note", "final_source", "reason")}),
            "source_evidence_json": _json(evidence), "full_document_text": text,
            "decision_contract_version": DECISION_VERSION, "decision_key_json": _json(DECISION_KEY),
            "human_decision": "NOT_REVIEWED", "corrected_labels_json": "", "reviewer_initials": "", "reviewer_notes": "",
        })
    if not packet or not any(r["final_decision"] == "accept" for r in packet):
        raise ValueError("empty accepted set; no review packet written")
    regular = _csv(packet)
    readable = [{**r, "reviewed_candidate_labels": "\n".join(json.loads(r["reviewed_candidate_labels_json"]))}
                for r in packet]
    sheets = _csv(readable)
    workbook = _workbook(readable)
    spec = fields_registry()["classes"]["insurance_claim"]["fields"][FIELD]
    # The portal contract shared with review_export.py: typed corrections, plain-sentence issues.
    records = [{**r, "candidate_labels": json.loads(r["reviewed_candidate_labels_json"]),
                "raw_glm_labels": json.loads(r["raw_glm_labels_json"]),
                "evidence": json.loads(r["source_evidence_json"]),
                "issues": [issue_sentence(i["code"], i["item"]) for i in json.loads(r["source_check_issues_json"])],
                "existing_gt_fields": json.loads(r["existing_gt_fields_json"]),
                "dojo_field_types": json.loads(r["dojo_field_types_json"]),
                "task": FIELD, "chunk": chunk_dir.name.split("_")[0], "models": [r["model"]], "correction_type": correction_type(spec["type"]),
                "candidate_presence": "populated", "absence_policy": spec["absence"],
                "nullable": spec["absence"] != "never_absent", "eligible_for_gold": True,
                "schema_status": "pinned_registry", "schema_alignment": "aligned",
                "review_provenance": (
                    f"Claim checklist from {r['model']} (reasoning {r['reasoning']}); {r['candidate_attribution']}. "
                    "Human approval is pending and nothing here is gold."),
                "model_outputs": [{"model": r["model"], "value": json.loads(r["raw_glm_labels_json"]),
                                   "status": json.loads(r["raw_glm_field_json"])["status"],
                                   "evidence": json.loads(r["raw_glm_field_json"])["evidence"],
                                   "evidence_spans": []}]} for r in packet]
    canonical = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    envelope = {"schema": "mailroom-review-packet/v1",
                "packet_id": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "decision_key": DECISION_KEY, "records": records}
    prefix = chunk_dir.name.split("_")[0]
    if not re.fullmatch(r"[A-Za-z0-9.-]+", prefix) or prefix in (".", ".."):
        raise ValueError("chunk directory must have a safe filename prefix")
    passed = [r for r in packet if r["candidate_status"] == "source_checks_passed"]
    blocked = [r for r in packet if r["candidate_status"] == "needs_source_review"]
    summary = {"accepted": sum(r["final_decision"] == "accept" for r in packet),
               "documents": len(packet), "source_checks_passed": len(passed), "needs_source_review": len(blocked),
               "held": len(excluded), "excluded": len(excluded) if sidecar is None else 0,
               "human_approval": "pending"}
    md = [f"# {html.escape(prefix)} checklist review\n",
          "**Source-check candidates only — no human approvals, no gold, no automatic ingest/staging.**\n",
          f"Documents: {len(packet)}; root final accepted: {summary['accepted']}; "
          f"source checks passed: {len(passed)}; blocked/source-policy examples: {len(blocked)}; "
          f"root held: {len(excluded)}; omitted: {summary['excluded']}. Human approval: pending.\n",
          "Checks do not establish legal correctness, completeness, or official Dojo scores. "
          f"This {label} batch fills claim_checklist only. Existing GT entity fields/presence are separate "
          "legacy context, not GLM-generated labels. Changed final lists are AI source-review corrections, "
          "not raw GLM outputs. The blind dataset config is not touched.\n",
          "## CSV import and reversible escaping\n",
          "Both CSVs contain the entire original text with real quoted multiline cells. "
          "The Google Sheets file is UTF-8 BOM, comma-delimited, QUOTE_ALL. "
          "Import locally; disable conversion to numbers, dates and formulas, wrap cells and freeze the header. "
          "CSV cannot encode fonts or colors. No Google Drive file is created.\n",
          "For each column listed in `escaped_columns` (JSON), remove exactly one leading apostrophe "
          "to recover its original string. Do not unescape other cells or interpret backslashes. "
          "Hash the recovered full_document_text as UTF-8 to verify document_sha256. "
          "Cells over the Sheets 50,000-character limit are refused, never trimmed. "
          "Legacy calls without input hashes are explicitly marked; current prompt and available hashes are checked.\n",
          "Fill human_decision, corrected_labels_json (a complete replacement JSON list), reviewer_initials "
          "and reviewer_notes only after actual human review. Existing reviewer metadata is AI reconciliation, "
          "not a human approval. These files are not auto-ingested.\n",
          "## Shared decision key — " + DECISION_VERSION + "\n",
          "Stable mapping: document_id + field identifies the review; keep split and source hashes unchanged. "
          "CSV decision_key_json repeats this exact mapping. The XLSX companion has a native dropdown; "
          "CSV and Markdown cannot persist dropdown controls. Yellow workbook cells are editable. "
          "NOT_REVIEWED is the default, not an approval; blank also never approves.\n",
          *[f"- **{code}**: {meaning}\n" for code, meaning in DECISION_KEY.items()],
          "## Summary index\n"]
    for i, record in enumerate(packet, 1):
        md.append(f"- [Document {i}](#doc-{i}): " + html.escape(record["document_id"]) + "\n")
    md += ["\n## Held / excluded manifest\n",
           "Root held decisions remain held, including test counterparts. With an AI sidecar they are "
           "included below as provisional candidates or blocked examples, never promoted to accepted/gold.\n",
           _fence(_json({"held_count": len(excluded), "omitted_count": summary["excluded"], "rows": excluded}), "json")]
    for status, title in (("source_checks_passed", "Source-checked candidates — human review pending"),
                          ("needs_source_review", "Blocked / source-policy examples — NOT source-check passed")):
        md.append(f"\n## {title}\n")
        for i, record in enumerate(packet, 1):
            if record["candidate_status"] != status:
                continue
            _document_markdown(md, i, record)
    contents = {f"{prefix}-review.csv": regular.encode("utf-8"),
                f"{prefix}-review-google-sheets.csv": sheets.encode("utf-8-sig"),
                f"{prefix}-review.md": "\n".join(md).encode("utf-8"),
                f"{prefix}-review.xlsx": workbook,
                f"{prefix}-review-data.json": (_json(envelope) + "\n").encode("utf-8")}
    # All source, provenance, rendering and cell-size validation precedes any writes.
    dest.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        (dest / name).write_bytes(content)
    return {**summary, "packet_id": envelope["packet_id"], "files": [str(dest / name) for name in contents]}


def _document_markdown(md: list[str], i: int, record: Mapping[str, str]) -> None:
    """Append one document section with metadata, legacy context, and full source to md."""
    metadata = {k: json.loads(v) if k.endswith("_json") and v else v
                for k, v in record.items() if k not in (
                    "full_document_text", "existing_gt_fields_json", "dojo_field_types_json")}
    context = {"existing_gt_fields": json.loads(record["existing_gt_fields_json"]),
               "dojo_field_types": json.loads(record["dojo_field_types_json"])}
    md += [f'\n<a id="doc-{i}"></a>\n<h3>{html.escape(record["document_id"])}</h3>\n',
           "- [ ] Human reviewed the full source and every candidate\n"
           "- [ ] Human decision and initials recorded (currently blank)\n",
           "<details open>\n<summary>Labels, evidence, corrections and provenance</summary>\n\n",
           _fence(json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2), "json"),
           "\n</details>\n\n<details>\n<summary>Existing GT entity labels / presence and pinned Dojo types "
           "— legacy context, NOT GLM output</summary>\n\n",
           _fence(json.dumps(context, ensure_ascii=False, sort_keys=True, indent=2), "json"),
           "\n</details>\n\n<details>\n<summary>Entire original document text — unchanged</summary>\n\n",
           _fence(record["full_document_text"], "text"), "\n</details>\n"]


def main(argv: Sequence[str] | None = None) -> int:
    """Read local stage/journals only; no provider or paid model call."""
    from .runner import load_stage

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--chunk-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = export_packet(load_stage(args.stage), args.chunk_dir, args.out)
    except (ValueError, KeyError, TypeError, OSError, ImportError) as err:
        parser.exit(1, f"review packet refused: {err}\n")
    print(_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
