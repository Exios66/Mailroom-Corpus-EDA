"""Reproducible private corporate review packets from staged sources, never gold.

No audit-temp manifest, model calls, published identities, or inferred split is used.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .registry import dojo_snapshot
from .review_packet import DECISION_KEY, _csv, _fence, _journal, _json
from .review_page import PACKET_SCHEMA, packet_id, validate_packet

PACKAGE = Path("docs/reports/audits/v10_labels/10.1_corporate-gold-additions")
GOLD = Path("data/v10/kl3m/gold")
# Stable source locators select the audited sample without storing source text.
SAMPLE_IDS = tuple("s3://data.kl3m.ai/documents/edgar/" + suffix for suffix in (
    "0000217346/0000217346-96-000008/18.json",
    "0000892569/0000892569-08-000173/7.json",
    "0001047469/0001047469-08-000291/4.json",
    "0001354488/0001354488-10-003001/2.json",
    "0001193125/0001193125-08-057490/20.json",
))
SCOPE = ("AI candidates; human approval pending. Source checks are not formal corporate "
         "9a gating, legal correctness, completeness, or gold eligibility. "
         "New additions have no published document identity or assigned split.")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(root: Path, relative: str | Path) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("source path escapes configured root")
    return path


def _index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        key = row["kl3m_identifier"]
        if key in result:
            raise ValueError(f"duplicate source identifier: {key}")
        result[key] = row
    return result


def _table(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def build_packet(root: Path, *, source_ids: Sequence[str] = SAMPLE_IDS) -> dict[str, Any]:
    """Read actual staged source, resolved labels and saved (possibly revised) drafts."""
    root = Path(root).resolve()
    package, gold = _inside(root, PACKAGE), _inside(root, GOLD)
    labels = _index(_journal(package / "corporate-labels.jsonl"))
    documents = _index(_table(package / "documents.csv"))
    reviews = _index(_table(package / "review.csv"))
    provenance = json.loads((package / "provenance.json").read_text(encoding="utf-8"))
    schema = json.loads((gold / "schema_snapshot.json").read_text(encoding="utf-8"))
    pinned = dojo_snapshot()
    drafts = {}
    # Exclude before-fix copies and concatenated exports: retain exact saved batch locators.
    for path in sorted((package / "drafts").glob("*.drafts.jsonl")):
        for line, draft in enumerate(_journal(path), 1):
            key = draft["kl3m_identifier"]
            if key in drafts:
                raise ValueError(f"ambiguous saved draft: {key}")
            drafts[key] = (draft, str(path.relative_to(root)), line, _sha(path.read_bytes()))
    records = []
    for source_id in source_ids:
        label, source, review = labels[source_id], documents[source_id], reviews[source_id]
        draft, draft_path, draft_line, draft_sha = drafts[source_id]
        path = _inside(gold, label["doc_file"])
        text = path.read_bytes().decode("utf-8")
        sha = _sha(text.encode("utf-8"))
        if not text or sha != label["text_sha256"] or sha != source["text_sha256"]:
            raise ValueError(f"source hash mismatch: {source_id}")
        leaf = label["leaf"]
        spec = schema["leaves"][leaf]
        if spec != pinned["corporate_subclass_schemas"][leaf]:
            raise ValueError(f"staged leaf differs from pinned Dojo schema: {leaf}")
        types = {key: schema["core"]["fields"][key] for key in spec["core_applicable"]}
        types.update({item["key"]: item for item in spec["entities"]})
        if set(label["fields"]) != set(types):
            raise ValueError(f"resolved fields differ from schema: {source_id}")
        doc_id = "REVIEW-ONLY-" + _sha(source_id.encode())
        common = {
            "document_id": doc_id, "published_document_id": None,
            "filename": path.name, "doc_class": "corporate_record", "subclass": leaf,
            "split": "unassigned", "population": "new_additions_NOT_current_producer",
            "document_sha256": sha, "full_document_text": text,
            "document_link": f"documents/{doc_id}.txt", "source_identifier": source_id,
            "source_local_path": str(path.relative_to(root)), "source_record": source,
            "source_url": f"https://huggingface.co/datasets/{source['source_corpus']}/tree/{source['source_revision']}",
            "original_sec_exhibit_url": None, "original_pdf": None,
            "text_completeness": "Entire stored decoded text; original SEC exhibit not independently verified",
            "record_form": label["record_form"], "dojo_pin": pinned["dojo_commit"],
            "human_approval": "pending", "human_decision": "NOT_REVIEWED",
            "corrected_labels_json": "", "reviewer_initials": "", "reviewer_notes": "",
            "eligible_for_gold": False, "formal_corporate_9a_gate": "not_run",
            "provenance": {
                "declared_labeler": draft.get("labeler"), "served_model": None,
                "provider": None, "prompt_sha256": None, "request_id": None, "winner": None,
                "instrumented_provenance_status": "unavailable",
                "artifact_kind": "Saved, possibly curated/revised Claude draft; not raw API output",
                "saved_draft_path": draft_path, "saved_draft_line": draft_line,
                "saved_draft_file_sha256": draft_sha,
                "package_provenance": provenance, "stored_review": review,
                "warning": "Stored human_annotated attribution is not proof of human approval; exact prompt and served model unavailable",
            },
        }
        for field, candidate in label["fields"].items():
            if field in ("record_type", "intent"):
                continue  # Schema constants are context, not entity-extraction targets.
            evidence = []
            for span in candidate["evidence"]:
                start, end = span["start"], span["end"]
                if (type(start) is not int or type(end) is not int or
                        not 0 <= start < end <= len(text) or text[start:end] != span["text"]):
                    raise ValueError(f"invalid exact source span: {source_id}.{field}")
                evidence.append({"start": start, "end": end, "quote": span["text"],
                                 "offset_convention": "Python Unicode code points; zero-based, end exclusive"})
            value, native = candidate["value"], types[field]["type"]
            correction_type = "array" if native.startswith("entity_list") else "number" if native == "money" else "string"
            saved = draft["fields"][field]
            # Literal value-in-quote checks only: dates, summaries and absences need human interpretation.
            normalize = lambda s: " ".join(s.split()).casefold()
            items = value if isinstance(value, list) else [value]
            quoted = normalize(" ".join(e["quote"] for e in evidence))
            grounded = (native in ("name", "id", "entity_list:name") and bool(evidence)
                        and bool(items) and all(isinstance(v, str) and v and normalize(v) in quoted for v in items)
                        and candidate["presence"] == "populated" and saved.get("value") == value)
            records.append({**common, "field": field, "kind": "entity_extraction",
                            "candidate_labels": value, "candidate_presence": candidate["presence"],
                            "saved_claude_value": saved.get("value"), "saved_claude_field": saved,
                            "same_value_as_saved_draft": saved.get("value") == value,
                            "correction_type": correction_type, "dojo_type": native,
                            "nullable": types[field].get("absence") != "never_absent",
                            "absence_policy": types[field].get("absence", "required_with_documented_absence_note" if types[field].get("required") else "absent_ok"),
                            "schema_status": "pinned_leaf", "evidence": evidence,
                            "candidate_status": "source_checks_passed" if grounded else "needs_source_review",
                            "issues": [] if grounded else ["Value/absence requires source interpretation or differs from saved draft"],
                            "schema_constants": {k: label["fields"][k] for k in ("record_type", "intent")}})
        for field, value in (("subclass", leaf), ("v10_subclass", label["v10_subclass"])):
            records.append({**common, "field": field, "kind": "classification",
                            "candidate_labels": value, "candidate_presence": "populated",
                            "saved_claude_value": draft.get("leaf" if field == "subclass" else field),
                            "saved_claude_field": {"record_form": draft.get("record_form")},
                            "correction_type": "string", "nullable": False, "absence_policy": "never_absent",
                            "schema_status": "schema_pending" if field == "v10_subclass" else "pinned_leaf",
                            "candidate_status": "needs_source_review", "evidence": [],
                            "issues": ["Proposed family schema pending" if field == "v10_subclass" else
                                       "Pinned vocabulary membership does not validate source classification"]})
    key = {**DECISION_KEY, "CORRECT": "Supply a complete replacement JSON value of correction_type; null only when nullable and absence policy permits."}
    packet = {"schema": PACKET_SCHEMA, "packet_id": packet_id(records), "decision_key": key,
              "scope": SCOPE, "records": records}
    validate_packet(packet)
    return packet


def _workbook(rows: list[dict[str, str]], decision_key: dict[str, str]) -> bytes:
    """Native corporate values need their own instructions, not the checklist-only workbook."""
    from openpyxl import Workbook
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.properties.created = wb.properties.modified = datetime(2000, 1, 1)
    ws = wb.active
    ws.title = "Corporate review"
    columns = list(rows[0])
    for row in [columns, *[list(r.values()) for r in rows]]:
        for value in row:
            if len(value.encode("utf-16-le")) // 2 > 32767 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", value):
                raise ValueError("Excel cannot preserve cell; source is never truncated or rewritten")
        ws.append(row)
    key = wb.create_sheet("Decision key")
    key.append(["Scope", SCOPE])
    for code, meaning in decision_key.items():
        key.append([code, meaning])
    for sheet in wb:
        sheet.freeze_panes = "A2"
        for row in sheet:
            for cell in row:
                cell.data_type = "s"
    ws.auto_filter.ref = ws.dimensions
    dropdown = DataValidation(type="list", formula1='"' + ','.join(decision_key) + '"',
                              allow_blank=True, showErrorMessage=True, errorStyle="stop")
    ws.add_data_validation(dropdown)
    letter = get_column_letter(columns.index("human_decision") + 1)
    dropdown.add(f"{letter}2:{letter}{ws.max_row}")
    output = io.BytesIO()
    wb.save(output)
    stable = io.BytesIO()
    with zipfile.ZipFile(output) as source, zipfile.ZipFile(stable, "w") as target:
        for name in sorted(source.namelist()):
            content = source.read(name).replace(b"\r", b"&#13;") if name.endswith(".xml") else source.read(name)
            if name == "docProps/core.xml":
                content = re.sub(rb"(<dcterms:modified\b[^>]*>).*?(</dcterms:modified>)",
                                 rb"\g<1>2000-01-01T00:00:00Z\g<2>", content)
            info = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, content)
    return stable.getvalue()


def export_packet(root: Path, dest: Path, *, source_ids: Sequence[str] = SAMPLE_IDS) -> dict[str, Any]:
    packet = build_packet(root, source_ids=source_ids)
    records = packet["records"]
    columns = list(dict.fromkeys(k for r in records for k in r))
    rows = [{k: r.get(k, "") if isinstance(r.get(k, ""), str) else _json(r[k]) for k in columns} for r in records]
    regular = _csv(rows)
    sheets = _csv(rows)
    workbook = _workbook(rows, packet["decision_key"])
    md = ["# Corporate review\n", SCOPE, "\n## Decisions\n", _fence(_json(packet["decision_key"]), "json"),
          "CSV escaping: remove one leading apostrophe only for columns listed in escaped_columns. "
          "Sheets CSV is UTF-8 BOM; disable automatic formula/date conversion. "
          "CORRECT uses each target's native JSON type and absence policy; no automatic ingestion."]
    contents = {}
    for doc_id in dict.fromkeys(r["document_id"] for r in records):
        targets = [r for r in records if r["document_id"] == doc_id]
        link = targets[0]["document_link"]
        contents[link] = targets[0]["full_document_text"].encode("utf-8")
        md += [f"\n## {doc_id}\n", f"[Full local source]({link})\n",
               _fence(targets[0]["full_document_text"], "text")]
        for target in targets:
            md += [f"\n### {target['field']}\n", _fence(_json({k: v for k, v in target.items() if k != "full_document_text"}), "json")]
    contents.update({"corporate-review.csv": regular.encode("utf-8"),
                     "corporate-review-google-sheets.csv": sheets.encode("utf-8-sig"),
                     "corporate-review.xlsx": workbook,
                     "corporate-review.md": "\n".join(md).encode("utf-8"),
                     "corporate-review-data.json": (_json(packet) + "\n").encode("utf-8")})
    # Validate every representation before writing any output.
    for name, content in contents.items():
        path = _inside(Path(dest), name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return {"packet_id": packet["packet_id"], "documents": len(set(r["document_id"] for r in records)),
            "targets": len(records), "source_checks_passed": sum(r["candidate_status"] == "source_checks_passed" for r in records),
            "files": [str(Path(dest) / name) for name in contents]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    print(_json(export_packet(args.root, args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
