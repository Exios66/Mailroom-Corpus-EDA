"""Ground-truth quality gate outside golden CUAD and MAUD (v9.1)."""

from __future__ import annotations

import json

import pytest

from mailroom_eda import gt_quality as gq
from mailroom_eda.config import PARQUET_DIR, REPO_REVISION, REPO_TAG


def _base(**over) -> dict:
    row = {
        "filename": "row.txt",
        "expected": "correspondence",
        "expected_subclass": "email",
        "doc_text": "Please send the invoice.",
        "metadata": {"source_dataset": "Lucius-Morningstar/enron-correspondence-dedup"},
        "split": "train",
        "intent": "request",
        "subject_matter": "Request for the Friday invoice from the accounts desk.",
        "keywords": json.dumps(["invoice", "friday", "accounts"]),
        "content_topic": "billing",
        "topic_evidence": "invoice",
        "sentiment_label": "neutral",
        "sentiment_score": "0.0",
        "sentiment_evidence": "please send",
        "intent_source": "manual",
        "intent_confidence": "1.0",
        "intent_status": "manual",
        "token_estimate": "200",
        "context_window_band": "<=4k",
        "gt_presence": "",
    }
    row.update(over)
    return row


def _codes(row: dict) -> set[tuple[str, str, str]]:
    return {(f.severity, f.field, f.code) for f in gq.audit_row(row)}


def test_hub_pin_is_tag_v9_1():
    assert REPO_TAG == "v9.1"
    assert REPO_REVISION == "bc9eab280044befb51e19dda3071d290a8677f42"
    assert gq.HUB_REVISION == REPO_REVISION


def test_well_formed_correspondence_has_no_errors():
    findings = gq.audit_row(_base())
    assert not [f for f in findings if f.severity in ("error", "backfill")]


def test_golden_cuad_clause_map_is_not_graded():
    row = _base(
        expected="contract",
        metadata={"source_dataset": "mailroom-cuad-contracts-full"},
        intent="",
        subject_matter="",
        keywords="",
        content_topic="",
        sentiment_label="",
        sentiment_score="",
        sentiment_evidence="",
        intent_source="",
        intent_confidence="",
        intent_status="",
        cuad_clause_labels='{"Governing Law": [{"start": 1, "text": "Delaware"}]}',
        label_evidence="CUAD-annotated clauses: Governing Law",
    )
    assert gq.is_golden_cuad(row)
    assert gq.scope_fields(row) == ()
    assert not [f for f in gq.audit_row(row) if f.field == "cuad_clause_labels"]


def test_golden_maud_is_not_graded():
    row = _base(
        expected="merger_agreement",
        metadata={"source_dataset": "data/maud/contracts.jsonl"},
        intent="",
        maud_clause_labels='{"No-Shop": {"answer": "yes"}}',
    )
    assert gq.is_golden_maud(row)
    assert not [f for f in gq.audit_row(row) if f.field == "maud_clause_labels"]


def test_ex10_empty_clause_map_is_a_backfill():
    row = _base(
        filename="ex10.txt",
        expected="contract",
        metadata={"source_dataset": "sec_edgar"},
        intent="material_agreement",
        intent_source="heuristic",
        intent_confidence="0.9",
        intent_status="auto_labeled",
        cuad_clause_labels="{}",
        label_evidence="EX-10 exhibit (EX-10.1); described as: Example Agreement.",
        subject_matter="",
        keywords="",
        content_topic="",
        sentiment_label="",
        sentiment_score="",
        sentiment_evidence="",
    )
    codes = _codes(row)
    assert ("backfill", "cuad_clause_labels", "pending_annotation") in codes
    assert not [c for c in codes if c[0] == "error"]


def test_bad_intent_and_keywords_are_format_errors():
    row = _base(intent="not_a_label", keywords="invoice, friday")
    codes = _codes(row)
    assert ("error", "intent", "format") in codes
    assert ("error", "keywords", "format") in codes


def test_flagged_review_and_thin_subject_are_reviews():
    row = _base(
        subject_matter="Correspondence concerning general_business.",
        intent_status="flagged_review",
        intent_confidence="0.5",
        intent_source="llm_zero_shot",
    )
    codes = _codes(row)
    assert ("review", "subject_matter", "thin_subject") in codes
    assert ("review", "intent_status", "flagged_review") in codes
    assert not [c for c in codes if c[0] == "error"]


def test_spend_queue_skips_reviews():
    rows = [
        _base(filename="thin.txt", subject_matter="Correspondence concerning billing."),
        _base(
            filename="ex10.txt",
            expected="contract",
            metadata={"source_dataset": "sec_edgar"},
            intent="material_agreement",
            intent_source="heuristic",
            intent_confidence="0.9",
            intent_status="auto_labeled",
            cuad_clause_labels="{}",
            label_evidence="EX-10 exhibit described as Example.",
            subject_matter="",
            keywords="",
            content_topic="",
            sentiment_label="",
            sentiment_score="",
            sentiment_evidence="",
        ),
    ]
    findings = gq.audit_rows(rows)
    targets = gq.backfill_targets(rows, findings)
    assert [item["filename"] for item in targets] == ["ex10.txt"]
    assert targets[0]["fields"] == ["cuad_clause_labels"]
    assert gq.chunk_plan(targets)[0]["doc_count"] == 1


def test_insurance_amount_and_date_rules():
    good = _base(
        expected="insurance_claim",
        metadata={"source_dataset": "cms-de-synpuf-2008-2010-sample1"},
        intent="claim_data_record",
        claim_number="C1",
        policy_number="P1",
        insurer="CMS",
        insured_party="Ada",
        claim_type="health",
        date_of_loss="N/A",
        date_filed="N/A",
        claimed_amount="0.0",
        adjuster="",
        damages_description="outpatient visit",
        coverage_determination="approved",
        denial_reasons="[]",
        supporting_documents='["claim form"]',
        subject_matter="Outpatient claim C1 for Ada.",
        keywords=json.dumps(["outpatient", "claim", "Ada"]),
        content_topic="",
        sentiment_label="",
        sentiment_score="",
        sentiment_evidence="",
    )
    assert not [f for f in gq.audit_row(good) if f.severity == "error"]
    bad = dict(good)
    bad["date_of_loss"] = "March 2"
    bad["claimed_amount"] = "-5"
    bad["coverage_determination"] = "maybe"
    codes = _codes(bad)
    assert ("error", "date_of_loss", "format") in codes
    assert ("error", "claimed_amount", "format") in codes
    assert ("error", "coverage_determination", "format") in codes


@pytest.mark.skipif(
    not (PARQUET_DIR / "ground_truth" / "train").exists(),
    reason="v9.1 snapshot not fetched",
)
def test_v9_1_snapshot_queue_is_the_ex10_gap():
    rows = gq.load_snapshot()
    assert len(rows) == 3302
    findings = gq.audit_rows(rows)
    summary = gq.summarize(findings, n_rows=len(rows))
    assert summary["by_severity"].get("error", 0) == 0
    targets = gq.backfill_targets(rows, findings)
    assert len(targets) == 91
    assert {tuple(item["fields"]) for item in targets} == {("cuad_clause_labels",)}
    assert {item["source_dataset"] for item in targets} == {"sec_edgar"}
    assert all(item["doc_class"] == "contract" for item in targets)
    plan = gq.chunk_plan(targets)
    assert [chunk["doc_count"] for chunk in plan] == [40, 40, 11]
    reviews = [f for f in findings if f.code == "flagged_review"]
    assert len(reviews) == 25
