"""Production reviews retain identities, source bytes, and native label provenance."""
import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mailroom_eda import config
from mailroom_eda.labeling import registry, review_production as production
from mailroom_eda.labeling.review_page import validate_packet, validate_return

SHA = "a" * 40
SOURCE_SHA = "b" * 64


def source_rows():
    subclasses = {"contract": "Co_Branding", "merger_agreement": "all_cash",
                  "insurance_claim": "carrier", "correspondence": "email",
                  "corporate_record": "bylaws"}
    return [{"document_id": f"{cls}-{i}", "filename": f"{cls}-{i}.txt",
             "expected": cls, "expected_subclass": sub, "split": "test" if i == 0 else "train",
             "doc_text": "😀 Acme Corp.\r\nAlice Smith\n$100\nOriginal full source.\n",
             "gt_fields": {"entity_name": "Acme Corp.", "sender": "Alice Smith",
                           "parties": '["Acme Corp."]', "claim_number": "Alice Smith",
                           "cuad_clause_labels": '{"Governing Law": ["Original"]}',
                           "maud_clause_labels": '{"Type of Consideration": "cash"}'}}
            for cls, sub in subclasses.items() for i in range(3)]


def build(rows=None, **kwargs):
    return production.build_packet(source_rows() if rows is None else rows,
                                   source_revision=SHA, source_sha256=SOURCE_SHA, **kwargs)


def test_five_classes_identity_split_full_text_and_determinism():
    rows = source_rows()
    before = copy.deepcopy(rows)
    packet = build(rows)
    assert packet == build(list(reversed(rows)))
    assert rows == before
    validate_packet(packet)
    selected = {r["document_id"] for r in packet["records"]}
    assert selected == {r["document_id"] for r in rows if not r["document_id"].endswith("-2")}
    by_id = {r["document_id"]: r for r in rows}
    assert {r["doc_class"] for r in packet["records"]} == set(registry.fields_registry()["classes"])
    for record in packet["records"]:
        source = by_id[record["document_id"]]
        assert record["published_document_id"] == source["document_id"]
        assert record["split"] == source["split"]
        assert record["full_document_text"] == source["doc_text"]
        assert record["document_sha256"] == hashlib.sha256(source["doc_text"].encode()).hexdigest()
        assert record["existing_gt_fields"] == source["gt_fields"]
        assert record["source_dataset"] == config.REPO_ID
        assert record["source_revision"] == SHA
        assert record["source_sha256"] == SOURCE_SHA
        assert record["human_approval"] == "pending"
        for evidence in record["evidence"]:
            assert source["doc_text"][evidence["start"]:evidence["end"]] == evidence["quote"]
    for doc_id in selected:
        source = by_id[doc_id]
        assert {r["field"] for r in packet["records"] if r["document_id"] == doc_id} == set(
            registry.class_fields(source["expected"], source["expected_subclass"]))


def test_raw_json_lists_missing_absence_and_complex_clauses():
    rows = source_rows()
    row = rows[0]
    row["gt_fields"].update(signatories=None, gt_presence={"signatories": "schema_documented_absence",
                                                       "expiration_date": "schema_documented_absence"})
    raw = row["gt_fields"] = json.dumps(row["gt_fields"])
    records = {r["field"]: r for r in build(rows, document_ids=[row["document_id"]])["records"]}
    assert all(r["existing_gt_fields"] == raw for r in records.values())
    parties = records["parties"]
    assert parties["candidate_labels"] == ["Acme Corp."]
    assert parties["original_gt_candidate"] == '["Acme Corp."]'
    assert parties["correction_type"] == "array"
    assert parties["candidate_status"] == "source_checks_passed"
    assert parties["evidence"][0]["start"] == 2  # Python characters, not UTF-16 units.
    assert records["signatories"]["candidate_presence"] == "schema_documented_absence"
    assert records["expiration_date"]["candidate_presence"] == "pending_annotation"
    assert records["expiration_date"]["candidate_labels"] is None
    clause = records["cuad_clauses"]
    assert clause["candidate_labels"] is None
    assert clause["eligible_for_gold"] is False
    assert clause["schema_alignment"] == "proposal_pending"
    assert clause["candidate_status"] == "needs_source_review"
    merger = build(document_ids=["merger_agreement-0"])
    assert next(r for r in merger["records"] if r["field"] == "maud_clauses")["eligible_for_gold"] is False


@pytest.mark.parametrize("value", ['{"name":"Acme Corp."}', "broken [", [1], {"party": "Acme Corp."}])
def test_mismatched_list_schemas_are_not_invented(value):
    rows = source_rows()
    rows[0]["gt_fields"]["parties"] = value
    r = next(r for r in build(rows)["records"] if r["document_id"] == "contract-0" and r["field"] == "parties")
    assert r["original_gt_candidate"] == value
    assert r["candidate_labels"] is None
    assert r["eligible_for_gold"] is False
    assert r["schema_status"] == "schema_pending"


def test_type_and_grounding_failures_require_review():
    rows = source_rows()
    rows[0]["gt_fields"].update(document_name="Fabricated Entity", contract_value=True)
    records = {r["field"]: r for r in build(rows, document_ids=["contract-0"])["records"]}
    assert records["document_name"]["candidate_labels"] == "Fabricated Entity"
    assert records["document_name"]["candidate_status"] == "needs_source_review"
    assert records["contract_value"]["candidate_labels"] is None
    assert records["contract_value"]["correction_type"] == "number"


def test_manual_correct_missing_scalar_is_accepted_by_return_contract():
    packet = build(document_ids=["contract-0"])
    r = next(r for r in packet["records"] if r["field"] == "document_name")
    decision = {"document_id": r["document_id"], "field": r["field"],
                "document_sha256": r["document_sha256"], "decision": "CORRECT",
                "corrected_labels_json": '"Original full source."', "reviewer_initials": "AB",
                "reviewer_notes": "Read full source", "updated_at": "2026-11-07T12:00:00Z"}
    returned = {"schema": "mailroom-human-review/v1", "packet_id": packet["packet_id"],
                "exported_at": decision["updated_at"], "decisions": [decision],
                "history": [{"document_id": r["document_id"], "field": r["field"],
                             "at": decision["updated_at"], "after": decision}]}
    validate_return(packet, returned)
    decision["decision"] = "ACCEPT"
    with pytest.raises(ValueError, match="blocked candidate"):
        validate_return(packet, returned)


@pytest.mark.parametrize("change", ["duplicate", "split", "gt_fields", "subclass", "text"])
def test_corrupt_source_refused(change):
    rows = source_rows()
    if change == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    else:
        key = {"split": "split", "gt_fields": "gt_fields", "subclass": "expected_subclass", "text": "doc_text"}[change]
        rows[0][key] = []
    with pytest.raises((ValueError, KeyError)):
        build(rows)


def test_selection_errors_and_explicit_ids():
    assert {r["document_id"] for r in build(document_ids=["correspondence-2"])["records"]} == {"correspondence-2"}
    with pytest.raises(ValueError, match="unknown document"):
        build(document_ids=["not-real"])
    with pytest.raises(ValueError, match="positive"):
        build(per_class=0)


def test_public_download_resolves_immutable_revision_and_writes_private_packet(tmp_path, monkeypatch):
    api = SimpleNamespace(token=None, repo_info=Mock(return_value=SimpleNamespace(sha=SHA)))
    get_api = Mock(return_value=api)
    monkeypatch.setattr(production.hf_interface, "get_hf_api", get_api)
    raw = ("\n".join(json.dumps(r, ensure_ascii=False) for r in source_rows()) + "\n").encode()

    def download(client, repo_id, filename, path, *, revision):
        assert client is api and client.token is None
        assert repo_id == config.REPO_ID and filename == production.SOURCE_FILE
        assert revision == SHA
        path.write_bytes(raw)
        return path

    monkeypatch.setattr(production.hf_interface, "download_file", download)
    root = tmp_path / "private"
    path = production.export_production(root)
    get_api.assert_called_once_with(token=None)
    api.repo_info.assert_called_once_with(repo_id=config.REPO_ID, repo_type="dataset", revision=config.REPO_REVISION)
    packet = json.loads(path.read_text())
    validate_packet(packet)
    assert path.name.endswith("-review-data.json")
    assert path.stat().st_mode & 0o777 == 0o600
    assert root.stat().st_mode & 0o777 == 0o700
    assert all(r["source_sha256"] == hashlib.sha256(raw).hexdigest() for r in packet["records"])
    assert production.export_production(root, revision="main") == path
    api.repo_info.assert_called_with(repo_id=config.REPO_ID, repo_type="dataset", revision="main")
    path.write_text("existing work")
    with pytest.raises(ValueError, match="refusing overwrite"):
        production.export_production(root)
