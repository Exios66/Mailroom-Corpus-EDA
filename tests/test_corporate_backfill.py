"""Corporate-record purpose backfill: grounded subject_matter, provenance-free keywords."""
from __future__ import annotations

from mailroom_eda import v9_build as vb


def _row(subclass="bylaws", filer="DentonX Inc  (DNTX)  (CIK 0002093375)"):
    """One synthetic corporate_record row wired like the v8/EDGAR exhibits."""
    return {
        "expected": "corporate_record",
        "expected_subclass": subclass,
        "gt_fields": {"intent_status": ""},
        "metadata": {"filer": filer, "exhibit_type": "EX-3.2", "form": "S-1/A", "exhibit_description": "BYLAWS"},
    }


def test_clean_filer_strips_cik_and_ticker():
    """Basic CIK and ticker-tail stripping on a normal EDGAR filer string."""
    assert vb._clean_filer("DentonX Inc  (DNTX)  (CIK 0002093375)") == "DentonX Inc"
    assert vb._clean_filer(None) == ""


def test_backfill_writes_grounded_labels_without_provenance_ids():
    """Generated fields carry only grounded document terms — no EDGAR/exhibit tokens."""
    rows = [_row()]
    vb.backfill_purpose_gt(rows)
    gt = rows[0]["gt_fields"]
    assert gt["subject_matter"] == "Bylaws of DentonX Inc."
    assert gt["keywords"] == '["Bylaws", "DentonX Inc"]'
    assert gt["entity_name"] == "DentonX Inc"
    assert "EDGAR" not in gt["subject_matter"] and "EX-3.2" not in gt["keywords"]
    assert gt["intent"] == "governance_rules"


def test_backfill_never_overwrites_existing_values():
    """Only EMPTY fields are written; existing GT values win."""
    rows = [_row()]
    rows[0]["gt_fields"] = {"subject_matter": "kept", "keywords": '["kept"]', "entity_name": "Kept Co"}
    vb.backfill_purpose_gt(rows)
    gt = rows[0]["gt_fields"]
    assert (gt["subject_matter"], gt["keywords"], gt["entity_name"]) == ("kept", '["kept"]', "Kept Co")


def test_clean_filer_strips_verified_ticker_even_when_it_is_a_legal_form_suffix():
    """First Majestic's verified EDGAR ticker AG must not survive _clean_filer or the backfill."""
    # First Majestic Silver (EDGAR CIK 0001308648): its NYSE ticker AG
    # collides with the German AG legal form in _NOT_TICKERS — but the
    # parenthesized tail for THIS filer is the ticker and must be stripped.
    filer = "First Majestic Silver Corp (AG) (CIK 0001308648) /TX/"
    assert vb._clean_filer(filer) == "First Majestic Silver Corp"
    rows = [ _row(subclass="rights_instrument", filer=filer) ]  # fmt: skip
    vb.backfill_purpose_gt(rows)
    gt = rows[0]["gt_fields"]
    # the ticker must not backfill into any of the three generated fields
    assert gt["entity_name"] == "First Majestic Silver Corp"
    assert gt["subject_matter"] == "Rights instrument of First Majestic Silver Corp."
    assert gt["keywords"] == '["Rights instrument", "First Majestic Silver Corp"]'


def test_clean_filer_keeps_legal_form_suffix_without_verified_ticker():
    """(AG) stays a legal-form suffix for filers without a verified AG ticker."""
    # Without the verified AG ticker, an (AG) tail stays a legal-form suffix
    # — other companies legitimately end in such name tokens.
    assert vb._clean_filer("British American Holdings AG (CIK 1)") == "British American Holdings AG"


def test_clean_filer_keeps_country_and_non_us_state_tails():
    """(UK) name tails and /UK/-style non-US state codes are part of the name."""
    assert vb._clean_filer("Pfizer (UK) (CIK 1)") == "Pfizer (UK)"
    # /XX/ tails are stripped only for US state/territory codes — foreign
    # codes in the same EDGAR pattern are part of the name.
    assert vb._clean_filer("Foo Bar /UK/") == "Foo Bar /UK/"
    assert vb._clean_filer("Foo Bar /ON/") == "Foo Bar /ON/"  # Canadian province


def test_clean_filer_edge_cases():
    """Non-string inputs give '', and multi-ticker tails don't break stripping."""
    assert vb._clean_filer(float("nan")) == ""
    assert vb._clean_filer(123) == ""
    assert vb._clean_filer("Pfizer (UK) (CIK 1)") == "Pfizer (UK)"
    assert vb._clean_filer("Berkshire Hathaway Inc (BRK-A, BRK-B) (CIK 1)") == "Berkshire Hathaway Inc"
    assert vb._clean_filer("SOURCE ENERGY CORP /UT/") == "SOURCE ENERGY CORP"


def test_backfill_empty_company_writes_no_placeholder_keyword_or_entity():
    """No filer → no entity_name and no placeholder company in keywords."""
    rows = [_row(filer=None)]
    vb.backfill_purpose_gt(rows)
    gt = rows[0]["gt_fields"]
    assert gt["keywords"] == '["Bylaws"]'
    assert "entity_name" not in gt


def test_backfill_writes_into_empty_or_missing_gt_fields_and_is_idempotent():
    """Missing/non-dict gt_fields initialize, and a second pass changes nothing."""
    for empty in ({}, None):
        rows = [_row()]
        rows[0]["gt_fields"] = empty
        vb.backfill_purpose_gt(rows)
        assert rows[0]["gt_fields"]["subject_matter"] == "Bylaws of DentonX Inc."
        before = dict(rows[0]["gt_fields"])
        vb.backfill_purpose_gt(rows)
        assert rows[0]["gt_fields"] == before


def test_backfill_leaves_non_corporate_rows_alone_and_no_double_period():
    """Non-corporate rows are untouched; company names ending in '.' get no '..'."""
    other = {"expected": "contract", "expected_subclass": "x", "gt_fields": {"a": "b"}, "metadata": {"filer": "X"}}
    vb.backfill_purpose_gt([other])
    assert other["gt_fields"] == {"a": "b"}
    rows = [_row(filer="ClubCorp Holdings, Inc.")]
    vb.backfill_purpose_gt(rows)
    assert rows[0]["gt_fields"]["subject_matter"] == "Bylaws of ClubCorp Holdings, Inc."
