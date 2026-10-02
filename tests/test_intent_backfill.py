"""Network-free unit tests for mailroom_eda.intent_backfill's pure helpers."""
from __future__ import annotations

from mailroom_eda import intent_backfill as ib


class TestPreserveExistingProvenance:
    def test_heuristic_source_preserved_verbatim(self):
        # v9's heuristic-sourced draws must never be relabeled "manual" if
        # the backfill script is re-run over a later corpus revision.
        out = ib.preserve_existing_provenance(
            intent="notice", intent_source="heuristic",
            intent_confidence="0.7", intent_status="auto_labeled",
        )
        assert out == {
            "intent": "notice",
            "intent_source": "heuristic",
            "intent_confidence": "0.7",
            "intent_status": "auto_labeled",
        }

    def test_aeslc_join_source_preserved_verbatim(self):
        out = ib.preserve_existing_provenance(
            intent="update", intent_source="aeslc_join",
            intent_confidence="1.0", intent_status="manual",
        )
        assert out["intent_source"] == "aeslc_join"

    def test_legacy_row_with_no_provenance_backfills_manual(self):
        # Pre-provenance-era rows (intent populated, intent_source empty)
        # keep the historical "manual" assumption.
        out = ib.preserve_existing_provenance(
            intent="request", intent_source="", intent_confidence="", intent_status="",
        )
        assert out == {
            "intent": "request",
            "intent_source": "manual",
            "intent_confidence": 1.0,
            "intent_status": "manual",
        }

    def test_empty_intent_stays_empty(self):
        out = ib.preserve_existing_provenance(
            intent="", intent_source="", intent_confidence="", intent_status="",
        )
        assert out == {
            "intent": "",
            "intent_source": "",
            "intent_confidence": "",
            "intent_status": "",
        }

    def test_none_intent_source_treated_as_empty(self):
        out = ib.preserve_existing_provenance(
            intent="notice", intent_source=None, intent_confidence=None, intent_status=None,
        )
        assert out["intent_source"] == "manual"
        assert out["intent_confidence"] == 1.0
