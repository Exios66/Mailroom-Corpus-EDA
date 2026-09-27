"""Unit tests for mailroom_eda.gt_presence (v9.1 quality revision, Phase B2).

Network-free: exercises the classification rules against synthetic rows so
the closed-vocabulary contract is verified regardless of whether the local
HF snapshot (data/parquet) is present.
"""
from __future__ import annotations

import json

from mailroom_eda import gt_presence as gp
from mailroom_eda import v9_build


def _row(**over) -> dict:
    base = {
        "expected": "contract",
        "metadata": {},
        "doc_text": "",
        "coverage_determination": "",
        "cuad_clause_labels": "{}",
        "maud_clause_labels": "{}",
        "intent": "",
        "subject_matter": "",
        "keywords": "",
        "sentiment_label": "",
        "content_topic": "",
        "claim_number": "",
        "policy_number": "",
        "insurer": "",
        "insured_party": "",
        "claim_type": "",
        "date_of_loss": "",
        "date_filed": "",
        "claimed_amount": "",
        "adjuster": "",
        "damages_description": "",
        "denial_reasons": "[]",
        "supporting_documents": "[]",
    }
    base.update(over)
    return base


class TestIsPopulated:
    def test_empty_string_absent(self):
        assert gp.is_populated("") is False

    def test_none_absent(self):
        assert gp.is_populated(None) is False

    def test_empty_json_list_absent(self):
        assert gp.is_populated("[]") is False

    def test_empty_json_dict_absent(self):
        assert gp.is_populated("{}") is False

    def test_non_empty_string_populated(self):
        assert gp.is_populated("payment_demand") is True

    def test_non_empty_json_populated(self):
        assert gp.is_populated('{"Governing Law": ["clause text"]}') is True


class TestClosedVocabulary:
    def test_presence_codes_are_exactly_four(self):
        assert gp.PRESENCE_CODES == (
            "populated", "schema_documented_absence",
            "not_applicable", "pending_annotation",
        )

    def test_row_gt_presence_values_are_closed(self):
        row = _row(expected="insurance_claim", metadata={"source_dataset": ""})
        presence = gp.row_gt_presence(row)
        assert set(presence.values()) <= set(gp.PRESENCE_CODES) | {"genuine_gap"}

    def test_row_gt_presence_uniform_keys_every_class(self):
        for doc_class in ("contract", "merger_agreement", "corporate_record",
                          "correspondence", "insurance_claim"):
            row = _row(expected=doc_class)
            assert tuple(sorted(gp.row_gt_presence(row))) == tuple(sorted(gp.ALL_GT_FIELDS))


class TestNotApplicable:
    def test_cuad_not_applicable_off_contract(self):
        row = _row(expected="insurance_claim")
        assert gp.classify_field("insurance_claim", "cuad_clause_labels", row) == "not_applicable"

    def test_intent_not_applicable_on_contract(self):
        row = _row(expected="contract")
        assert gp.classify_field("contract", "intent", row) == "not_applicable"

    def test_maud_not_applicable_off_merger(self):
        row = _row(expected="contract")
        assert gp.classify_field("contract", "maud_clause_labels", row) == "not_applicable"


class TestPopulated:
    def test_populated_cuad(self):
        row = _row(expected="contract",
                    cuad_clause_labels='{"Governing Law": ["New York law"]}')
        assert gp.classify_field("contract", "cuad_clause_labels", row) == "populated"

    def test_populated_intent(self):
        row = _row(expected="correspondence", intent="notice")
        assert gp.classify_field("correspondence", "intent", row) == "populated"


class TestPendingAnnotation:
    def test_ex10_cuad_pending(self):
        row = _row(expected="contract", cuad_clause_labels="{}",
                    metadata={"source_dataset": "sec_edgar"})
        assert gp.classify_field("contract", "cuad_clause_labels", row) == "pending_annotation"

    def test_cuad_source_cuad_is_not_pending_when_empty(self):
        # a CUAD-sourced row with an empty cuad_clause_labels has NO covering
        # rule (should never occur on the real corpus) -> genuine_gap, never
        # silently reclassified as pending.
        row = _row(expected="contract", cuad_clause_labels="{}",
                    metadata={"source_dataset": "theatticusproject/cuad"})
        assert gp.classify_field("contract", "cuad_clause_labels", row) == "genuine_gap"


class TestSchemaDocumentedAbsence:
    def test_adjuster_absent_non_bdr(self):
        row = _row(expected="insurance_claim", adjuster="",
                    metadata={"source_dataset": "cms-de-synpuf"})
        assert gp.classify_field("insurance_claim", "adjuster", row) == "schema_documented_absence"

    def test_adjuster_populated_bdr(self):
        row = _row(expected="insurance_claim", adjuster="J. Rivera",
                    metadata={"source_dataset": gp.BDR_AUTO_SOURCE})
        assert gp.classify_field("insurance_claim", "adjuster", row) == "populated"

    def test_adjuster_genuine_gap_if_bdr_empty(self):
        # BDR rows are expected to always carry an adjuster pseudonym; an
        # empty one has no covering absence rule -> genuine_gap (never
        # silently forgiven).
        row = _row(expected="insurance_claim", adjuster="",
                    metadata={"source_dataset": gp.BDR_AUTO_SOURCE})
        assert gp.classify_field("insurance_claim", "adjuster", row) == "genuine_gap"

    def test_denial_reasons_absent_non_denied(self):
        row = _row(expected="insurance_claim", denial_reasons="[]",
                    coverage_determination="approved")
        assert gp.classify_field("insurance_claim", "denial_reasons", row) == "schema_documented_absence"

    def test_denial_reasons_genuine_gap_if_denied_empty(self):
        row = _row(expected="insurance_claim", denial_reasons="[]",
                    coverage_determination="denied")
        assert gp.classify_field("insurance_claim", "denial_reasons", row) == "genuine_gap"

    def test_supporting_documents_insurbias_bare_accident(self):
        row = _row(
            expected="insurance_claim", supporting_documents="[]",
            metadata={"source_dataset": gp.INSURBIAS_SOURCE},
            doc_text="CLAIM NARRATIVE (source text): The vehicles collided at the intersection.",
        )
        assert gp.classify_field(
            "insurance_claim", "supporting_documents", row
        ) == "schema_documented_absence"

    def test_supporting_documents_insurbias_with_damage_is_genuine_gap_if_empty(self):
        row = _row(
            expected="insurance_claim", supporting_documents="[]",
            metadata={"source_dataset": gp.INSURBIAS_SOURCE},
            doc_text="CLAIM NARRATIVE (source text): The front bumper was badly damaged.",
        )
        assert gp.classify_field(
            "insurance_claim", "supporting_documents", row
        ) == "genuine_gap"


class TestInsurbiasMirrorMatchesV9Build:
    """The gt_presence absence predicate for INSURBIAS supporting_documents
    delegates to the real v9_build implementation (no local reimplementation
    that could drift) — this test locks that delegation in place."""

    def test_delegates_to_v9_build(self):
        text = "CLAIM NARRATIVE (source text): No damage, no injuries, nothing to report."
        assert (
            gp._insurbias_supporting_doc_absent(text)
            == v9_build._insurbias_supporting_doc_absent(text)
        )

    def test_source_constant_matches(self):
        assert gp.INSURBIAS_SOURCE == v9_build.INSURBIAS_SOURCE


class TestStripWeakIndirectSignals:
    def test_strips_known_keys(self):
        cleaned, extracted = gp.strip_weak_indirect_signals(
            {"clause_count": "12", "maud_label_count": "", "filer": "Acme Corp"}
        )
        assert "clause_count" not in cleaned
        assert "maud_label_count" not in cleaned
        assert cleaned == {"filer": "Acme Corp"}
        assert extracted == {"clause_count": "12", "maud_label_count": ""}

    def test_missing_keys_default_empty(self):
        cleaned, extracted = gp.strip_weak_indirect_signals({"filer": "Acme Corp"})
        assert cleaned == {"filer": "Acme Corp"}
        assert extracted == {"clause_count": "", "maud_label_count": ""}

    def test_does_not_mutate_input(self):
        original = {"clause_count": "3"}
        gp.strip_weak_indirect_signals(original)
        assert original == {"clause_count": "3"}


class TestContextWindowBand:
    def test_small_doc(self):
        assert gp.context_window_band(1000) == "<=4k"

    def test_exactly_at_4k_boundary(self):
        assert gp.context_window_band(4096) == "<=4k"

    def test_between_4k_and_16k(self):
        assert gp.context_window_band(8000) == "<=16k"

    def test_between_16k_and_32k(self):
        assert gp.context_window_band(20000) == "<=32k"

    def test_over_32k(self):
        assert gp.context_window_band(63000) == ">32k"


class TestJsonRoundTrip:
    """gt_presence must serialize cleanly for the gt_fields JSON blob."""

    def test_json_dumps_loads_round_trip(self):
        row = _row(expected="merger_agreement")
        presence = gp.row_gt_presence(row)
        s = json.dumps(presence, sort_keys=True, ensure_ascii=False)
        assert json.loads(s) == presence
