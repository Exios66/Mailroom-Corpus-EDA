"""Corporate-record purpose backfill: grounded subject_matter, provenance-free keywords."""
from __future__ import annotations

from mailroom_eda import v9_build as vb


def _row(subclass="bylaws", filer="DentonX Inc  (DNTX)  (CIK 0002093375)"):
    return {
        "expected": "corporate_record",
        "expected_subclass": subclass,
        "gt_fields": {"intent_status": ""},
        "metadata": {"filer": filer, "exhibit_type": "EX-3.2", "form": "S-1/A", "exhibit_description": "BYLAWS"},
    }


def test_clean_filer_strips_cik_and_ticker():
    assert vb._clean_filer("DentonX Inc  (DNTX)  (CIK 0002093375)") == "DentonX Inc"
    assert vb._clean_filer(None) == ""


def test_backfill_writes_grounded_labels_without_provenance_ids():
    rows = [_row()]
    vb.backfill_purpose_gt(rows)
    gt = rows[0]["gt_fields"]
    assert gt["subject_matter"] == "Bylaws of DentonX Inc."
    assert gt["keywords"] == '["Bylaws", "DentonX Inc"]'
    assert gt["entity_name"] == "DentonX Inc"
    assert "EDGAR" not in gt["subject_matter"] and "EX-3.2" not in gt["keywords"]
    assert gt["intent"] == "governance_rules"


def test_backfill_never_overwrites_existing_values():
    rows = [_row()]
    rows[0]["gt_fields"] = {"subject_matter": "kept", "keywords": '["kept"]', "entity_name": "Kept Co"}
    vb.backfill_purpose_gt(rows)
    gt = rows[0]["gt_fields"]
    assert (gt["subject_matter"], gt["keywords"], gt["entity_name"]) == ("kept", '["kept"]', "Kept Co")
