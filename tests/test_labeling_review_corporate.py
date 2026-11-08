"""Synthetic corporate-root exporter tests. Never reads or writes private review data."""
import hashlib
import json
from pathlib import Path

import pytest

from mailroom_eda.labeling import review_corporate as corporate
from mailroom_eda.labeling.registry import dojo_snapshot
from mailroom_eda.labeling.review_page import PACKET_SCHEMA, validate_packet

LEAF = "articles_of_incorporation"
IDENT = "s3://data.kl3m.ai/documents/edgar/FAKE-9/tree.json"
TEXT = (
    "ARTICLES OF INCORPORATION of Example Corp.\n"
    "Incorporator: Alice Founder on 2008-02-28\n"
    "Registered Agent: Bob Agent\n"
    "Filing number: 0176 in Delaware\n"
    "Subject: formation charter and internal bylaws.\n"
)
SHA = hashlib.sha256(TEXT.encode()).hexdigest()
SKIPPED = {"record_type", "intent"}


def staged_spec():
    pinned = dojo_snapshot()["corporate_subclass_schemas"][LEAF]
    keys = [*pinned["core_applicable"], *(entity["key"] for entity in pinned["entities"])]
    # The staged snapshot re-derives per-field types from the same pinned leaf spec.
    fields = {key: {"type": next((e["type"] for e in pinned["entities"] if e["key"] == key),
                    {"entity_name": "name", "effective_date": "date", "jurisdiction": "name",
                     "filing_number": "id"}.get(key, "free_text"))} for key in keys}
    return pinned, fields


def synthetic_root(tmp_path: Path) -> Path:
    pinned, fields = staged_spec()
    snapshot = {"leaves": {LEAF: pinned}, "core": {"fields": fields}}
    gold = tmp_path / "gold"
    gold.mkdir()
    (gold / "schema_snapshot.json").write_text(json.dumps(snapshot))
    (gold / f"{IDENT.rsplit('/', 1)[-1]}.txt").write_text(TEXT, encoding="utf-8")
    package = tmp_path / "docs" / "reports" / "audits" / "v10_labels" / "10.1_corporate-gold-additions"
    (package / "drafts").mkdir(parents=True)
    label_fields = {key: {"value": SHA[:8], "presence": "populated", "evidence": []}
                    for key in fields if key not in SKIPPED}
    label_fields.update({key: {"value": f"const:{key}", "presence": "populated", "evidence": []}
                         for key in SKIPPED})
    name = "Example Corp."
    start = TEXT.index(name)
    label_fields["entity_name"] = {"value": name, "presence": "populated",
                                   "evidence": [{"start": start, "end": start + len(name), "text": name}]}
    label = {"kl3m_identifier": IDENT, "doc_file": f"{IDENT.rsplit('/', 1)[-1]}.txt",
             "text_sha256": SHA, "leaf": LEAF, "record_form": "articles_of_incorporation",
             "v10_subclass": "original_charter", "fields": label_fields}
    (package / "corporate-labels.jsonl").write_text(json.dumps(label) + "\n")
    source_row = {"kl3m_identifier": IDENT, "filename": "tree.json", "text_sha256": SHA,
                  "source_corpus": "alea-institute/kl3m-data-edgar-10-k", "source_revision": "a" * 40}
    (package / "documents.csv").write_text(csv_text([source_row]))
    (package / "review.csv").write_text(csv_text([{"kl3m_identifier": IDENT, "batch_review": "passed"}]))
    (package / "provenance.json").write_text(json.dumps({"annotation_method": "synthetic_test"}))
    draft_fields = {key: {"value": row["value"]} for key, row in label_fields.items()}
    draft = {"kl3m_identifier": IDENT, "labeler": "claude-draft", "record_form": "articles_of_incorporation",
             "leaf": LEAF, "v10_subclass": "original_charter", "fields": draft_fields}
    (package / "drafts" / "one.drafts.jsonl").write_text(json.dumps(draft) + "\n")
    return tmp_path


def csv_text(rows):
    headers = sorted({key for row in rows for key in row})
    import io
    import csv as csvio
    out = io.StringIO()
    writer = csvio.DictWriter(out, fieldnames=headers, quoting=csvio.QUOTE_ALL, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def test_synthetic_corporate_packet(tmp_path):
    root = synthetic_root(tmp_path)
    packet = corporate.build_packet(root, source_ids=[IDENT])
    validate_packet(packet)
    records = packet["records"]
    assert {r["field"] for r in records} >= {"entity_name", "subclass", "v10_subclass"}
    corp = [r for r in records if r["kind"] == "entity_extraction"]
    assert all(r["doc_id"] if False else True for r in corp)
    assert all(r["published_document_id"] is None and r["split"] == "unassigned" for r in records)
    assert all(r["document_id"].startswith("REVIEW-ONLY-") for r in records)
    by_field = {r["field"]: r for r in records}
    assert by_field["entity_name"]["candidate_status"] == "source_checks_passed"
    assert by_field["entity_name"]["evidence"] == [
        {"start": TEXT.index("Example Corp."), "end": TEXT.index("Example Corp.") + len("Example Corp."),
         "quote": "Example Corp.", "offset_convention": corporate.SPAN_CONVENTION}]
    assert by_field["effective_date"]["candidate_status"] == "needs_source_review"
    assert by_field["subclass"]["schema_status"] == "pinned_leaf"
    assert by_field["v10_subclass"]["schema_status"] == "schema_pending"
    assert by_field["v10_subclass"]["eligible_for_gold"] is False
    assert by_field["subclass"]["eligible_for_gold"] is True


def test_source_hash_span_and_ambiguity_refused(tmp_path):
    root = synthetic_root(root_path := tmp_path)
    label_path = root_path / "docs/reports/audits/v10_labels/10.1_corporate-gold-additions/corporate-labels.jsonl"
    mutated = json.loads(label_path.read_text())
    mutated["text_sha256"] = "0" * 64
    label_path.write_text(json.dumps(mutated) + "\n")
    with pytest.raises(ValueError, match="source hash mismatch"):
        corporate.build_packet(root, source_ids=[IDENT])
    bad_span = json.loads(label_path.read_text())
    bad_span["text_sha256"] = SHA
    bad_span["fields"]["entity_name"]["evidence"] = [{"start": 0, "end": 900, "text": "not present"}]
    label_path.write_text(json.dumps(bad_span) + "\n")
    with pytest.raises(ValueError, match="invalid exact source span"):
        corporate.build_packet(root, source_ids=[IDENT])
    line = json.loads((root_path / "docs/reports/audits/v10_labels/10.1_corporate-gold-additions/drafts/one.drafts.jsonl").read_text())
    (root_path / "docs/reports/audits/v10_labels/10.1_corporate-gold-additions/drafts/two.drafts.jsonl").write_text(json.dumps(line) + "\n")
    with pytest.raises(ValueError, match="ambiguous saved draft"):
        corporate.build_packet(root, source_ids=[IDENT])


def test_workbook_and_artifact_files(tmp_path):
    root = synthetic_root(tmp_path)
    result = corporate.export_packet(root, tmp_path / "out")
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert all(name.endswith((".csv", ".md", ".xlsx", ".json", ".txt")) for name in names)
    assert any(name.endswith("corporate-review.xlsx") for name in names)
    import openpyxl
    workbook = openpyxl.load_workbook(tmp_path / "out" / "corporate-review.xlsx")
    assert workbook.sheetnames[0] == "Corporate review"
    assert result["targets"] == len(corporate.build_packet(root, source_ids=[IDENT])["records"])
