"""Offline regression tests for labeling checks, distinct from official Dojo metrics."""
from __future__ import annotations

import copy
import argparse
import json
from pathlib import Path

import pytest

from mailroom_eda.labeling import export, operate, runner, scoring, tasks, verify
from mailroom_eda.labeling.registry import fields_registry

NOTICE = "Date of Report: 2026-02-25"
OUTCOME = "COVERAGE DETERMINATION: APPROVED - this claim has been adjudicated and paid."
PROOF = "Police Report Filed: Yes"
MODELS = ["z-ai/glm-5.3-flashx-legal", "minimax/minimax-m3.1-flash"]


def _score(text: str, items: object, evidence: str | None = None) -> scoring.ChecklistScore:
    return scoring.score_checklist(items, verify.DocIndex.build(text), evidence=evidence)


def _codes(score: scoring.ChecklistScore) -> set[str]:
    return {f.code for f in score.findings}


@pytest.mark.parametrize("value", [None, [], "Deductible: $500", {}, 5, True])
def test_checklist_requires_nonempty_list(value):
    score = _score("Deductible: $500", value)
    assert not score.passed and score.complete_line_rate == 0


@pytest.mark.parametrize("value,code", [
    ([{}], "category_quote_string_required"),
    ([42], "category_quote_string_required"),
    (["Deductible"], "category_quote_string_required"),
    (["Unknown: " + NOTICE], "unknown_category"),
    (["Timely Notice: " + NOTICE] * 2, "duplicate_item"),
])
def test_malformed_items_and_duplicates_fail(value, code):
    assert code in _codes(_score(NOTICE, value))


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_complete_lines_keep_offsets_and_allow_spaces_tabs(newline):
    text = "Title" + newline + "  Part B deductible:\t $500.00  " + newline
    score = _score(text, ["Deductible: Part B deductible: $500.00"])
    assert score.passed and score.complete_line_rate == 1
    span = score.evidence[0]
    assert text[span.start:span.end] == "Part B deductible:\t $500.00"


@pytest.mark.parametrize("quote", ["$500 applies", "Deductible $500", "deductible $500 applies."])
def test_fragments_and_case_changes_are_not_complete_printed_lines(quote):
    assert "complete_source_line_required" in _codes(_score("Deductible $500 applies.", ["Deductible: " + quote]))


@pytest.mark.parametrize("quote", ["Total cost: $100 Paid: $80", "Total cost: $100\nPaid: $80"])
def test_whitespace_folded_multiline_quotes_are_not_one_printed_line(quote):
    assert "stitched_lines" in _codes(_score("Total cost: $100\nPaid: $80", ["Amount Consistency: " + quote]))


def test_each_amount_line_is_allowed_separately_not_stitched():
    assert _score("Total cost: $100\nPaid: $80", ["Amount Consistency: Total cost: $100",
                                                  "Amount Consistency: Paid: $80"]).passed


@pytest.mark.parametrize("line", [
    "This claim strictly adheres to Property Household Insurance coverage terms.",
    "Insurance covers rental loss for documented repair periods.",
    "This claim pertains exclusively to rental income loss and structural repairs.",
    "COVERAGE DETERMINATION: APPROVED if policy conditions are met.",
    "Your claim is approved subject to receipt of proof of loss.",
    "COVERAGE DETERMINATION: APPROVED provided that the requested invoices are received.",
    "COVERAGE DETERMINATION: APPROVED for review, not for payment.",
    "The claim may be approved following investigation.",
])
def test_scope_general_coverage_and_conditional_statements_are_not_outcomes(line):
    score = _score(line, ["Coverage Determination: " + line])
    assert score.complete_line_rate == 1
    assert "explicit_claim_outcome_required" in _codes(score)


@pytest.mark.parametrize("line", [
    OUTCOME, "COVERAGE DETERMINATION: PENDING", "Your claim has been denied.",
    "COVERAGE DETERMINATION: APPROVED on May 3, 2026.",
    "COVERAGE DETERMINATION: APPROVED. If you disagree, contact the insurer.",
    "COVERAGE DETERMINATION: APPROVED - this fill was covered under the plan.",
])
def test_explicit_claim_outcomes_are_reviewable(line):
    assert _score(line, ["Coverage Determination: " + line], line).passed


def test_bookkeeping_is_blocked_and_real_source_occurrence_is_preferred():
    text = "CLAIM RECORD EXTRACT (system)\n" + NOTICE + "\nCoverage determination: pending\n"
    assert "corpus_bookkeeping" in _codes(_score(text, ["Timely Notice: " + NOTICE]))
    text += "CLAIM NARRATIVE (source text)\n" + NOTICE
    score = _score(text, ["Timely Notice: " + NOTICE])
    assert score.passed and score.evidence[0].start == text.rindex(NOTICE)
    echo = "Stated outcome (verbatim): approved"
    assert "corpus_bookkeeping" in _codes(_score(echo, ["Coverage Determination: " + echo]))


@pytest.mark.parametrize("line,category,code", [
    ("Police Report Filed: No", "Proof Of Loss", "negative_proof_is_not_present_proof"),
    ("Fire Brigade Report Filed: No", "Proof Of Loss", "negative_proof_is_not_present_proof"),
    ("Beneficiary deductible: N/A", "Deductible", "missing_deductible_is_not_present_value"),
])
def test_obvious_absence_does_not_become_positive_evidence(line, category, code):
    assert code in _codes(_score(line, [category + ": " + line]))


def test_zero_deductible_is_a_real_value():
    assert _score("Blood deductible: $0.00", ["Deductible: Blood deductible: $0.00"]).passed


def test_field_evidence_must_be_earliest_item_line_and_not_category_prefixed():
    text = NOTICE + "\n" + PROOF
    items = ["Proof Of Loss: " + PROOF, "Timely Notice: " + NOTICE]
    assert _score(text, items, NOTICE).passed
    for bad in (PROOF, "Timely Notice: " + NOTICE, "", "\n" + NOTICE + "\n" + PROOF):
        assert "earliest_item_evidence_required" in _codes(_score(text, items, bad))
    long = "Notes: " + "word " * 65
    assert "earliest_item_evidence_required" in _codes(_score(long, ["Proof Of Loss: " + long], long))


def test_pinned_contract_is_checked_for_every_registry_field():
    for cls, block in fields_registry()["classes"].items():
        for name, spec in block["fields"].items():
            assert scoring.dojo_type(cls, name) == spec["type"]


def test_catalog_drift_fails_closed(monkeypatch):
    changed = copy.deepcopy(fields_registry())
    changed["classes"]["insurance_claim"]["fields"]["claim_checklist"]["type"] = "free_text"
    monkeypatch.setattr(scoring, "fields_registry", lambda: changed)
    with pytest.raises(ValueError, match="pinned Dojo catalog"):
        scoring.dojo_type("insurance_claim", "claim_checklist")


def test_addon_does_not_change_baseline_verifier_or_claim_metric_parity():
    text = "Notes: Insurance covers rental loss for documented repair periods."
    raw = {"value": ["Coverage Determination: " + text], "status": "present", "evidence": text}
    doc = verify.DocIndex.build(text)
    baseline = verify.check_field(raw, "entity_list:free_text", doc)
    checked, score = scoring.check_field("insurance_claim", "claim_checklist", raw, doc)
    assert baseline.usable and not checked.usable and score and not score.passed
    assert verify.check_field(raw, "entity_list:free_text", doc) == baseline
    raw = {"value": "POL-9", "status": "present", "evidence": "Policy: POL-9"}
    doc = verify.DocIndex.build("Policy: POL-9")
    checked, score = scoring.check_field("insurance_claim", "policy_number", raw, doc)
    assert checked == verify.check_field(raw, "id", doc) and score is None


def _review_fixture(root: Path, text: str, items: list[str]) -> dict:
    row = {"document_id": "DOC-TEST", "filename": "fixture.txt", "expected": "insurance_claim",
           "expected_subclass": "property", "doc_text": text, "split": "train", "gt_fields": {}}
    req = tasks.build("claim_checklist", row)
    for model in MODELS:
        export.append_journal(root / "calls.jsonl", {
            "document_id": row["document_id"], "filename": row["filename"], "model": model,
            "provider": "openrouter", "reasoning": "low", "prompt_sha256": req.prompt_sha256,
            "labeled_utc": "2026-11-08T00:00:00Z", "response": {"fields": {"claim_checklist": {
                "value": items, "status": "present", "evidence": text.splitlines()[0]}}},
        })
    return row


def test_review_blocks_agreed_bad_outcomes_and_rechecks_custom_overrides(tmp_path):
    bad = "Insurance covers rental loss for documented repair periods."
    row = _review_fixture(tmp_path, NOTICE + "\n" + bad, ["Coverage Determination: " + bad])
    calls = (tmp_path / "calls.jsonl").read_bytes()
    result = runner.review([row], "claim_checklist", tmp_path, reasoning="low")
    assert result["final_decisions"] == {"adjudicate": 1}
    adj = export.read_csv(tmp_path / "adjudication.csv")
    adj[0].update(reviewer_verdict="value", reviewer_value=json.dumps(["Coverage Determination: " + bad]))
    export.write_csv(tmp_path / "adjudication.csv", export.FIELD_COLUMNS, adj)
    result = runner.review([row], "claim_checklist", tmp_path, reasoning="low")
    assert result["final_decisions"] == {"adjudicate": 1}
    assert "final labeling: explicit_claim_outcome_required" in export.read_csv(tmp_path / "fields.csv")[0]["reason"]
    adj[0]["reviewer_value"] = json.dumps(["Timely Notice: " + NOTICE])
    export.write_csv(tmp_path / "adjudication.csv", export.FIELD_COLUMNS, adj)
    assert runner.review([row], "claim_checklist", tmp_path, reasoning="low")["final_decisions"] == {"accept": 1}
    before = (tmp_path / "fields.csv").read_bytes()
    runner.review([row], "claim_checklist", tmp_path, reasoning="low")
    assert (tmp_path / "fields.csv").read_bytes() == before
    assert (tmp_path / "calls.jsonl").read_bytes() == calls
    audit = json.loads((tmp_path / "labeling-scores.json").read_text())
    assert audit["accepted_checked"] == 1 and audit["blocked_accepted"] == 0
    assert audit["dojo_scoring"].startswith("not_run") and audit["human_approval"] == "not_assessed"


def test_review_reopens_previously_correct_checklist_after_quality_rejection(tmp_path):
    bad = "Insurance covers rental loss for documented repair periods."
    row = _review_fixture(tmp_path, bad, ["Coverage Determination: " + bad])
    raw = {"value": ["Coverage Determination: " + bad], "status": "present", "evidence": bad}
    assert verify.check_field(raw, "entity_list:free_text", verify.DocIndex.build(bad)).usable
    export.write_csv(tmp_path / "fields.csv", export.FIELD_COLUMNS, [{
        "document_id": row["document_id"], "field": "claim_checklist", "reviewer_verdict": "correct",
        "reviewer_note": "Previously confirmed under the old checks.",
    }])
    result = runner.review([row], "claim_checklist", tmp_path, reasoning="low")
    assert result["final_decisions"] == {"adjudicate": 1}
    f = export.read_csv(tmp_path / "adjudication.csv")[0]
    assert not f["reviewer_verdict"] and "Prior 'correct'" in f["reviewer_note"]
    assert "Previously confirmed" in f["reviewer_note"]
    assert (tmp_path / "labeling-scores.json").exists()
    before = (tmp_path / "fields.csv").read_bytes()
    runner.review([row], "claim_checklist", tmp_path, reasoning="low")
    assert (tmp_path / "fields.csv").read_bytes() == before


def test_review_exports_invalid_override_of_initially_accepted_checklist(tmp_path):
    bad = "Insurance covers rental loss for documented repair periods."
    row = _review_fixture(tmp_path, NOTICE + "\n" + bad, ["Timely Notice: " + NOTICE])
    assert runner.review([row], "claim_checklist", tmp_path, reasoning="low")["decisions"] == {"accept": 1}
    fs = export.read_csv(tmp_path / "fields.csv")
    fs[0].update(reviewer_verdict="value", reviewer_value=json.dumps(["Coverage Determination: " + bad]))
    export.write_csv(tmp_path / "fields.csv", export.FIELD_COLUMNS, fs)
    result = runner.review([row], "claim_checklist", tmp_path, reasoning="low")
    assert result["decisions"] == {"accept": 1} and result["final_decisions"] == {"adjudicate": 1}
    assert export.read_csv(tmp_path / "adjudication.csv")[0]["document_id"] == row["document_id"]


@pytest.mark.parametrize("invalid", [False, True])
def test_gate_veto_and_approval_quality_metadata(tmp_path, monkeypatch, invalid):
    root = tmp_path / "labels"
    entry = dict(operate.chunk_entry("v10", "8a", None)[0], gate={"kind": "agreement"})
    d = operate.chunk_dir(root, entry, None)
    bad = "Insurance covers rental loss for documented repair periods."
    row = _review_fixture(d, NOTICE + "\n" + bad, ["Timely Notice: " + NOTICE])
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "ground_truth_hardened.jsonl").write_text(json.dumps(row) + "\n")
    runner.review([row], "claim_checklist", d, reasoning="low")
    if invalid:
        fs = export.read_csv(d / "fields.csv")
        fs[0].update(reviewer_verdict="value", reviewer_value=json.dumps(["Coverage Determination: " + bad]))
        export.write_csv(d / "fields.csv", export.FIELD_COLUMNS, fs)
    assert operate.gate_agreement(d, signed_off=True)["passed"]
    writes = []
    monkeypatch.setattr(operate, "chunk_entry", lambda *args: (entry, None))
    monkeypatch.setattr(operate, "load_approvals", lambda name: [])
    monkeypatch.setattr(operate, "load_second_opinions", lambda name: [])
    def save(name, approvals, opinions):
        writes.extend(approvals)
        return tmp_path / "synthetic-approval.yaml"
    monkeypatch.setattr(operate, "_write_approvals", save)
    args = argparse.Namespace(campaign="v10", id="8a", arm=None, out=root, stage=stage,
                              signed_off=True, provider="openrouter", approve=True, initials="TEST",
                              replace=False, no_restore=True, no_checkpoint=True)
    if invalid:
        with pytest.raises(SystemExit, match="gate did not pass"):
            operate.cmd_gate(args)
        assert not writes
        res = json.loads((d / "gate.json").read_text())
        assert res["labeling_quality"]["blocked_accepted"] == 0
        assert res["labeling_quality"]["invalid_final_fields"] == 1
    else:
        assert operate.cmd_gate(args) == 0 and len(writes) == 1
        assert writes[0]["labeling_quality"] == {
            "policy_version": scoring.POLICY_VERSION, "accepted_checked": 1,
            "blocked_accepted": 0, "invalid_final_fields": 0,
        }


def test_audit_counts_final_states_and_ignores_withheld_old_values():
    row = {"document_id": "d", "filename": "f", "expected_subclass": "property", "doc_text": NOTICE}
    f = {"document_id": "d", "doc_class": "insurance_claim", "field": "claim_checklist",
         "final_decision": "unlabeled", "final_value": "not JSON"}
    result = scoring.audit_fields([row], [f])
    assert result["accepted_checked"] == 0 and result["by_subclass"]["property"]["unlabeled"] == 1
    f["final_decision"] = "accept"
    result = scoring.audit_fields([row], [f])
    assert result["accepted_checked"] == result["blocked_accepted"] == 1


def test_cli_exits_nonzero_for_invalid_or_empty_accepted_set(tmp_path, capsys):
    stage = tmp_path / "stage"
    stage.mkdir()
    row = {"document_id": "d", "filename": "f", "expected_subclass": "property", "doc_text": NOTICE}
    (stage / "ground_truth_hardened.jsonl").write_text(json.dumps(row) + "\n")
    f = {"document_id": "d", "doc_class": "insurance_claim", "field": "claim_checklist",
         "final_decision": "accept", "final_value": "not JSON"}
    for value, state, exit_code in (("not JSON", "accept", 1),
                                    (json.dumps(["Timely Notice: " + NOTICE]), "accept", 0),
                                    ("", "unlabeled", 1)):
        f.update(final_value=value, final_decision=state)
        export.write_csv(tmp_path / "fields.csv", export.FIELD_COLUMNS, [f])
        assert scoring.main([str(tmp_path), "--stage", str(stage)]) == exit_code
        assert json.loads(capsys.readouterr().out)["human_approval"] == "not_assessed"


@pytest.mark.parametrize("items,valid", [
    (["Coverage Determination: Insurance covers rental loss for documented repair periods."], False),
    (["Timely Notice: " + NOTICE], True),
])
def test_build_rechecks_ingested_checklists_even_when_reviewer_supplied_no_evidence(items, valid):
    from mailroom_eda import v10_build as v10
    from mailroom_eda.labeling.ingest import Label
    from test_v10_build import make_row

    text = NOTICE + "\nInsurance covers rental loss for documented repair periods."
    row = v10.derive_row(make_row("p.txt", "insurance_claim", "property", text))
    row["labels"] = {"claim_checklist": Label(items, "populated", (), "reviewer:TEST+value", "8a")}
    if not valid:
        with pytest.raises(ValueError, match="explicit_claim_outcome_required"):
            v10.class_record(row)
    else:
        rec = v10.class_record(row)
        assert rec["claim_checklist"] == items
        assert rec["evidence"]["claim_checklist"][0]["text"] == NOTICE


def test_build_preserves_validated_source_occurrence_not_system_echo():
    from mailroom_eda import v10_build as v10
    from mailroom_eda.labeling.ingest import Label
    from test_v10_build import make_row

    text = "CLAIM RECORD EXTRACT (system)\n" + NOTICE + "\nCLAIM NARRATIVE (source text)\n" + NOTICE
    row = v10.derive_row(make_row("repeat.txt", "insurance_claim", "property", text))
    row["labels"] = {"claim_checklist": Label(["Timely Notice: " + NOTICE], "populated")}
    rec = v10.class_record(row)
    ev = rec["evidence"]["claim_checklist"][0]
    assert ev["start"] == text.rindex(NOTICE) and ev["start"] != text.index(NOTICE)
    assert text[ev["start"]:ev["end"]] == ev["text"] == NOTICE
