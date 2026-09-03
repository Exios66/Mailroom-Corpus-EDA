"""§63/§64 P1 evaluation-contract tests (HUB-022, plan §86/§31/§43/§58/§59).

Every derived field is validated against a closed vocabulary and against the
row's existing provenance columns; the full-snapshot run (data/parquet)
re-verifies over all 1,650 rows."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mailroom_eda import eval_contract as ec


# ---------------------------------------------------------------- fixtures

CANONICAL_ROW = {
    "filename": "cuad_contract_001.pdf",
    "expected": "contract",
    "expected_subclass": "Consulting Agreements",
    "intent_source": "",
    "intent_confidence": "",
}

CORR_LLM = {
    "filename": "enron_x_001",
    "expected": "correspondence",
    "expected_subclass": "email",
    "intent": "request",
    "intent_source": "llm_zero_shot",
    "intent_confidence": "0.95",
}

CORR_JOIN = {
    "filename": "enron_x_002",
    "expected": "correspondence",
    "expected_subclass": "letter",
    "intent_source": "aeslc_join",
    "intent_confidence": "1.0",
}

CORR_MANUAL = {
    "filename": "enron_x_003",
    "expected": "correspondence",
    "expected_subclass": "notice",
    "intent_source": "manual",
    "intent_confidence": "1.0",
}

INSURANCE = {
    "filename": "carrier_001.txt",
    "expected": "insurance_claim",
    "expected_subclass": "carrier",
    "intent_source": "",
}


def test_expected_specialist_registry_mapping():
    assert ec.expected_specialist(CANONICAL_ROW) == "contracts_specialist"
    # §6: merger_agreement is a distinct class that routes to the contracts
    # specialist — the mapping must keep both facts visible.
    assert ec.expected_specialist({"expected": "merger_agreement"}) == "contracts_specialist"
    assert ec.expected_specialist({"expected": "corporate_record"}) == "corporate_records_specialist"
    assert ec.expected_specialist({"expected": "correspondence"}) == "correspondence_specialist"
    assert ec.expected_specialist({"expected": "insurance_claim"}) == "insurance_claims_specialist"
    assert ec.expected_specialist({"expected": "court_opinion"}) == ""  # retired: no routing


def test_expected_specialist_matches_live_taxonomy_yaml():
    registry = ec.specialist_registry()
    assert set(registry) == set(ec.SPECIALIST_BY_CLASS)
    for doc_class, specialist in ec.SPECIALIST_BY_CLASS.items():
        assert registry[doc_class] == specialist, doc_class


def test_expected_stage_vocabulary():
    assert ec.expected_stage(CANONICAL_ROW) == "archived"
    assert ec.TERMINAL_STAGES == ("archived", "review", "failed")
    assert ec.expected_stage(CANONICAL_ROW, review_route=True) == "review"


def test_review_retry_expectations_canonical_rows_are_clean():
    review, reason = ec.review_expected(CANONICAL_ROW)
    assert review is False and reason == ""
    retry, post = ec.retry_expected(CANONICAL_ROW)
    assert retry is False and post == ""


def test_review_retry_fixture_kinds():
    row = {"fixture_kind": "ood_unknown"}
    assert ec.review_expected(row) == (True, "taxonomy_unknown")
    assert ec.review_expected({"fixture_kind": "low_confidence"}) == (True, "low_confidence")
    assert ec.review_expected({"fixture_kind": "incomplete"}) == (True, "incomplete_extraction")
    assert ec.review_expected({"fixture_kind": "conflicting"}) == (True, "conflicting_information")
    assert ec.retry_expected({"fixture_kind": "retry"}) == (True, "archived")
    assert ec.retry_expected({"fixture_kind": "retry_review"}) == (True, "human_review")


def test_annotation_provenance_source_native_class_labels():
    p = ec.annotation_provenance(CANONICAL_ROW)
    assert p["source"] == "theatticusproject/cuad"
    assert p["method"] == "source_native"
    assert p["model"] == "" and p["prompt_version"] == ""


def test_annotation_provenance_intent_regimes():
    p = ec.annotation_provenance(CORR_LLM)
    assert p["method"] == "llm_zero_shot"
    assert p["model"] == "deepseek-chat"
    assert p["confidence"] == "0.95"
    assert ec.annotation_provenance(CORR_JOIN)["method"] == "verified_join"
    p = ec.annotation_provenance(CORR_MANUAL)
    assert p["method"] == "human_annotated"
    assert p["reviewer"] == "human"


def test_annotation_provenance_synthetic_insurance():
    # CMS DE-SynPUF is synthetic by design — §4A/§39 mandate the flag.
    p = ec.annotation_provenance(INSURANCE)
    assert p["method"] == "synthetic"
    assert p["source"] == "cms_desynpuf"


def test_enrich_row_field_contract():
    out = ec.enrich_row(CORR_LLM)
    assert out["expected_specialist"] == "correspondence_specialist"
    assert out["expected_stage"] == "archived"
    assert out["review_expected"] == "false" and out["review_reason"] == ""
    assert out["retry_expected"] == "false" and out["expected_post_retry_state"] == ""
    assert out["annotation_method"] == "llm_zero_shot"
    # input row never mutated (identity.py pattern)
    assert "expected_specialist" not in CORR_LLM


def test_vocabularies_are_closed():
    assert set(ec.SPECIALISTS) == set(ec.SPECIALIST_BY_CLASS.values())
    assert set(ec.ANNOTATION_METHODS) >= set(ec.INTENT_SOURCE_METHOD.values())
    assert set(ec.REVIEW_REASONS) >= {
        "taxonomy_unknown", "low_confidence", "incomplete_extraction",
        "conflicting_information",
    }
    assert set(ec.POST_RETRY_STATES) >= {"archived", "human_review"}


def test_row_level_invariants_over_fixture_rows(fixture_rows):
    for row in ec.enrich_rows(fixture_rows):
        assert row["expected_specialist"] in ec.SPECIALISTS
        assert row["expected_stage"] in ec.TERMINAL_STAGES
        assert row["review_expected"] in {"true", "false"}
        assert row["retry_expected"] in {"true", "false"}
        assert row["annotation_method"] in ec.ANNOTATION_METHODS
        if row["review_expected"] == "true":
            assert row["review_reason"] in ec.REVIEW_REASONS
        if row["retry_expected"] == "true":
            assert row["expected_post_retry_state"] in ec.POST_RETRY_STATES


def test_enrichment_over_full_snapshot(snapshot_rows):
    """§84B: 100% of rows carry a valid evaluation contract (v8 full corpus)."""
    enriched = ec.enrich_rows(snapshot_rows)
    assert len(enriched) == 2000
    for row in enriched:
        assert row["expected_specialist"] in ec.SPECIALISTS
        assert row["expected_stage"] in ec.TERMINAL_STAGES
        assert row["annotation_method"] in ec.ANNOTATION_METHODS
        assert row["annotation_source"] != ""
    # provenance regimes over the real corpus: intent-derived methods on the
    # 350 correspondence rows (verified 162 joins / 92 llm / 96 manual),
    # synthetic on all 950 insurance rows (600 DE-SynPUF + 200 GNOTHEIA +
    # 150 BDR — v8 LOB expansion, HUB-028), source_native elsewhere.
    methods = {m: sum(1 for r in enriched if r["annotation_method"] == m) for m in
               ("verified_join", "llm_zero_shot", "human_annotated", "synthetic")}
    assert methods == {
        "verified_join": 162, "llm_zero_shot": 92,
        "human_annotated": 96, "synthetic": 950,
    }


def test_fixture_kind_vocabulary():
    assert "ood_unknown" in ec.FIXTURE_KINDS
    assert "ood_retired_class" in ec.FIXTURE_KINDS  # §60/§68: former classes → unknown
    assert set(ec.CALIBRATION_QUARTET) == {
        "correct_high", "correct_low", "wrong_high", "wrong_low",
    }
    assert set(ec.MATTER_CONSTRUCTION) == {
        "source_native_thread", "heuristic_reconstructed", "synthetic_constructed",
    }
    assert "duplicate_of" in ec.RELATIONSHIP_TYPES
    assert "exact_duplicate" in ec.DUPLICATE_TYPES
    assert "archival" in ec.FAILURE_STAGES and "grouping" in ec.FAILURE_STAGES


def test_confidence_bands_read_live_taxonomy():
    bands = ec.confidence_bands()
    # live config truth (not the plan's illustrative 0.95/0.70)
    assert bands["global"]["high"] == 0.97 and bands["global"]["low"] == 0.88
    for doc_class, band in bands["by_class"].items():
        assert band["low"] < band["judge_band_high"] < band["high"], doc_class
    assert bands["by_class"]["contract"]["high"] == 0.98
    assert bands["by_class"]["correspondence"]["high"] == 0.95
