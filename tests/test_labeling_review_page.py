"""Offline rendering and returned-review contract tests; never write campaign gold."""
from __future__ import annotations

import copy
import hashlib
import json
import re

import pytest

from mailroom_eda.labeling import review_page as page
from mailroom_eda.labeling.review_packet import DECISION_KEY


def packet():
    text = 'Full source\n</script><script>alert("not executed")</script>\nDate of Report: 2026-02-25'
    records = [{"document_id": "DOC-1", "field": "claim_checklist", "doc_class": "insurance_claim",
                "filename": "source.txt", "split": "test", "subclass": "property",
                "full_document_text": text, "document_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "candidate_labels": ["Timely Notice: Date of Report: 2026-02-25"],
                "raw_glm_labels": [], "candidate_status": "source_checks_passed", "source_url": "",
                "issues": [], "evidence": [], "existing_gt_fields": {}, "review_provenance": "AI only"}]
    return {"schema": page.PACKET_SCHEMA, "packet_id": page.packet_id(records),
            "decision_key": DECISION_KEY, "records": records}


def returned(p, decision="ACCEPT"):
    d = {"document_id": "DOC-1", "field": "claim_checklist",
         "document_sha256": p["records"][0]["document_sha256"], "decision": decision,
         "corrected_labels_json": "", "reviewer_initials": "TEST", "reviewer_notes": "Human reviewed.",
         "updated_at": "2026-11-08T00:00:00Z"}
    return {"schema": page.RETURN_SCHEMA, "packet_id": p["packet_id"],
            "exported_at": "2026-11-08T00:00:01Z", "decisions": [d],
            "history": [{"at": d["updated_at"], "document_id": d["document_id"], "field": d["field"],
                         "before": {"decision": "NOT_REVIEWED"}, "after": dict(d)}]}


def test_safe_data_island_preserves_complete_text_and_creates_only_private_return_instructions(tmp_path):
    p = packet()
    out = page.render_page(p, tmp_path / "review.html")
    text = out.read_text()
    assert '</script><script>alert' not in text
    embedded = re.search(r'<script[^>]*id=[\"\x27]packet[\"\x27][^>]*>(.*?)</script>', text, re.S)
    assert embedded and json.loads(embedded[1]) == p
    assert list((tmp_path / "returns").iterdir()) == [tmp_path / "returns/README.md"]


def test_changed_source_packet_and_wrong_highlight_are_refused():
    p = packet()
    p["records"][0]["full_document_text"] += "changed"
    with pytest.raises(ValueError, match="changed review packet"):
        page.validate_packet(p)
    p["packet_id"] = page.packet_id(p["records"])
    with pytest.raises(ValueError, match="source hash"):
        page.validate_packet(p)
    p = packet()
    p["records"][0]["evidence"] = [{"start": 0, "end": 4, "quote": "wrong"}]
    p["packet_id"] = page.packet_id(p["records"])
    with pytest.raises(ValueError, match="source highlight"):
        page.validate_packet(p)


@pytest.mark.parametrize("change,error", [
    (lambda r: r.update(packet_id="different"), "another packet"),
    (lambda r: r["decisions"][0].update(document_sha256="wrong"), "source hash"),
    (lambda r: r["decisions"].append(dict(r["decisions"][0])), "repeated"),
    (lambda r: r["decisions"][0].update(reviewer_initials=""), "initials"),
    (lambda r: r["decisions"][0].update(updated_at="not a date"), "timestamp"),
    (lambda r: r["decisions"][0].update(decision="SHIP"), "unknown decision"),
    (lambda r: r["decisions"][0].update(corrected_labels_json="[]"), "choose CORRECT"),
    (lambda r: r.update(history=[]), "change log"),
])
def test_bad_return_files_do_not_gain_authority(change, error):
    p = packet(); r = returned(p); change(r)
    with pytest.raises(ValueError, match=error):
        page.validate_return(p, r)


def test_blocked_accept_and_correction_shapes():
    p = packet(); p["records"][0]["candidate_status"] = "needs_source_review"
    p["packet_id"] = page.packet_id(p["records"])
    with pytest.raises(ValueError, match="blocked candidate"):
        page.validate_return(p, returned(p))
    for value in ("not JSON", "{}", '[{"nested":true}]'):
        r = returned(p, "CORRECT"); r["decisions"][0]["corrected_labels_json"] = value
        with pytest.raises(ValueError, match="correction"):
            page.validate_return(p, r)
    r = returned(p, "CORRECT"); r["decisions"][0]["corrected_labels_json"] = '[]'
    r["history"][-1]["after"] = copy.deepcopy(r["decisions"][0])
    page.validate_return(p, r)


def test_blocked_accept_allowed_after_manual_verification_with_model_attribution():
    p = packet(); p["records"][0]["candidate_status"] = "needs_source_review"
    p["records"][0]["model_outputs"] = [
        {"model": "synthetic/model-a", "value": ["x"], "status": "present", "evidence": ""},
        {"model": "synthetic/model-b", "value": ["x"], "status": "present", "evidence": ""},
    ]
    p["packet_id"] = page.packet_id(p["records"])
    r = returned(p)
    r["decisions"][0].update(manual_source_verification=True,
                             verified_models=["synthetic/model-a", "synthetic/model-b"])
    r["history"][-1]["after"] = copy.deepcopy(r["decisions"][0])
    page.validate_return(p, r)  # the reviewer verified the value against the full source

    r["decisions"][0]["verified_models"] = ["synthetic/model-c"]
    r["history"][-1]["after"] = copy.deepcopy(r["decisions"][0])
    with pytest.raises(ValueError, match="did not run"):
        page.validate_return(p, r)
    r["decisions"][0]["verified_models"] = "synthetic/model-a"
    r["history"][-1]["after"] = copy.deepcopy(r["decisions"][0])
    with pytest.raises(ValueError, match="verified_models must be a list"):
        page.validate_return(p, r)
    r["decisions"][0]["verified_models"] = ["synthetic/model-a"]
    r["decisions"][0]["manual_source_verification"] = "yes"
    r["history"][-1]["after"] = copy.deepcopy(r["decisions"][0])
    with pytest.raises(ValueError, match="manual_source_verification must be boolean"):
        page.validate_return(p, r)


def test_changed_correction_cannot_reuse_old_history():
    p = packet(); r = returned(p, "CORRECT")
    r["decisions"][0]["corrected_labels_json"] = '[]'
    with pytest.raises(ValueError, match="change log"):
        page.validate_return(p, r)


def test_blank_default_is_not_an_approval_and_receipts_are_idempotent(tmp_path):
    p = packet(); r = returned(p, "NOT_REVIEWED")
    r["decisions"][0].update(reviewer_initials="", updated_at=None)
    r["history"] = []
    packet_path = tmp_path / "review-data.json"; packet_path.write_text(json.dumps(p))
    upload = tmp_path / "download.json"; upload.write_text(json.dumps(r))
    before = packet_path.read_bytes()
    receipt = page.receive(packet_path, upload)
    assert receipt.parent == tmp_path / "returns" and receipt.read_bytes() == upload.read_bytes()
    assert page.receive(packet_path, upload) == receipt
    assert packet_path.read_bytes() == before
    assert json.loads(receipt.read_text())["decisions"][0]["decision"] == "NOT_REVIEWED"
