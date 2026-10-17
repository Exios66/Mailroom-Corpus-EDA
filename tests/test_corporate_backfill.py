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
    """Spot-check on the synthetic row: document terms like "EDGAR"
    (absent from subject_matter) and exhibit tokens (absent from keywords) —
    this asserts these fixtures only, not a global non-leak guarantee."""
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


def test_clean_filer_conservative_tails_are_kept_intentionally():
    """Pinned conservative behaviors — kept tails are intended, not gaps.

    Verified-ticker stripping currently handles single-token uppercase tails
    backed by a CIK ( "(CIK n)" inline or an authoritative metadata cik) and
    the _TICKER_BY_CIK registry only:

    - Multi-token uppercase tails like "(AG, AG-PR)" are KEPT even when a
      token is the filer's verified ticker: stripping requires exactly one
      token, so an ambiguous tail never loses name material.
    - Lowercase tails like "(ag)" never match the uppercase-only tail regex
      and are KEPT regardless of CIK backing.
    """
    # multi-token: strip path requires len(tokens) == 1 — "(AG, AG-PR)" stays
    assert (
        vb._clean_filer("First Majestic Silver Corp (AG, AG-PR) (CIK 0001308648)")
        == "First Majestic Silver Corp (AG, AG-PR)"
    )
    # lowercase-only tails fall outside _TICKER_TAIL entirely — kept
    assert (
        vb._clean_filer("First Majestic Silver Corp (ag) (CIK 0001308648)")
        == "First Majestic Silver Corp (ag)"
    )
    # the kept tails flow through the backfill verbatim (conservative by design)
    rows = [_row(subclass="rights_instrument", filer="First Majestic Silver Corp (ag)")]
    vb.backfill_purpose_gt(rows)
    gt = rows[0]["gt_fields"]
    assert gt["entity_name"] == "First Majestic Silver Corp (ag)"
    assert gt["subject_matter"] == "Rights instrument of First Majestic Silver Corp (ag)."


def test_metadata_cik_enables_verified_ticker_disambiguation_without_inline_cik():
    """The row's separate metadata ``cik`` key reaches _clean_filer.

    When the filer string lacks "(CIK n)" (cik parses to 0), an authoritative
    metadata CIK still lets the verified _TICKER_BY_CIK match strip "(AG)":
    """
    md_cik = {"cik": 1308648}
    assert vb._row_metadata_cik(md_cik) == 1308648
    assert vb._row_metadata_cik({"cik": "1308648"}) == 1308648
    assert vb._row_metadata_cik({"cik": ""}) is None
    assert vb._row_metadata_cik({"cik": None}) is None
    assert vb._row_metadata_cik({"cik": True}) is None  # booleans are not CIKs
    assert vb._row_metadata_cik({}) is None
    # the same filer that leaked "(AG)" under the legacy string-only parse
    # is cleaned when the metadata CIK is present...
    assert vb._clean_filer("First Majestic Silver Corp (AG)", cik=1308648) == "First Majestic Silver Corp"
    rows = [_row(subclass="rights_instrument", filer="First Majestic Silver Corp (AG)")]
    rows[0]["metadata"]["cik"] = "1308648"
    vb.backfill_purpose_gt(rows)
    gt = rows[0]["gt_fields"]
    assert gt["entity_name"] == "First Majestic Silver Corp"
    assert gt["subject_matter"] == "Rights instrument of First Majestic Silver Corp."
    assert gt["keywords"] == '["Rights instrument", "First Majestic Silver Corp"]'


def test_clean_filer_without_any_cik_keeps_legal_form_tail_and_leak_is_explicit():
    """Documented conservative limitation, pinned: with no CIK anywhere —
    no "(CIK n)" in the filer string and no separate metadata ``cik`` key —
    _clean_filer parses cik 0 and cannot verify an "(AG)" ticker, so the
    legal-form-looking tail is KEPT. That tail then intentionally leaks into
    the three generated backfill fields (entity_name / subject_matter /
    keywords); we pin the behavior here so any silent tightening (or a
    future data-driven change) surfaces in CI instead of shipping quietly.
    """
    rows = [_row(subclass="rights_instrument", filer="Some Holdings Corp (AG)")]
    vb.backfill_purpose_gt(rows)
    gt = rows[0]["gt_fields"]
    assert gt["entity_name"] == "Some Holdings Corp (AG)"
    assert gt["subject_matter"] == "Rights instrument of Some Holdings Corp (AG)."
    assert gt["keywords"] == '["Rights instrument", "Some Holdings Corp (AG)"]'


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
