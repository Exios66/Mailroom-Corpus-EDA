"""Unit tests for mailroom_eda.corporate_presence (v9.3 presence layer).

Network-free except the optional live-dojo parity test (imports the sibling
``llm-dojo-scoring`` checkout when present, skip-safe otherwise — the hub#57
test convention)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mailroom_eda import corporate_presence as cp
from mailroom_eda import gt_presence as gp
from mailroom_eda import v9_1_revision as rev  # for the superset interplay

ROOT = Path(__file__).resolve().parents[1]


class TestRegistry:
    def test_counts(self):
        # 11 subclasses, 6 entity-core keys, 31 distinct extension keys
        assert len(cp.SUBCLASS_ORDER) == 11
        assert len(cp.CORPORATE_ENTITY_CORE_KEYS) == 6
        extension = {
            k
            for keys in cp.CORPORATE_SUBCLASS_ENTITY_KEYS.values()
            for k in keys
        }
        assert len(extension) == 31
        assert tuple(sorted({*cp.CORPORATE_ENTITY_CORE_KEYS, *extension})) == cp.CORPORATE_ENTITY_KEYS
        assert len(cp.CORPORATE_ENTITY_KEYS) == 37

    def test_record_type_enum(self):
        assert cp.RECORD_TYPES == (
            "articles_of_incorporation", "bylaws", "powers_of_attorney",
            "rights_instrument", "other",
        )

    def test_subclass_maps_consistent(self):
        for sub in cp.SUBCLASS_ORDER:
            assert sub in cp.SUBCLASS_RECORD_TYPES
            assert cp.SUBCLASS_RECORD_TYPES[sub] in cp.RECORD_TYPES
            assert cp.SUBCLASS_INTENTS[sub]
            # every subclass applies the semantic trio
            assert set(cp.CORPORATE_BASE_KEYS) <= cp.SUBCLASS_CORE_APPLICABLE[sub]

    def test_notable_core_inapplicabilities(self):
        assert "filing_number" not in cp.SUBCLASS_CORE_APPLICABLE["bylaws"]
        not_sl = cp.SUBCLASS_CORE_APPLICABLE["subsidiary_list"]
        assert {"signatories", "jurisdiction", "filing_number"}.isdisjoint(not_sl)
        # articles/subclass-of-formation carry everything
        assert len(cp.SUBCLASS_CORE_APPLICABLE["articles_of_incorporation"]) == 9

    def test_normalize_subclass(self):
        assert cp.normalize_subclass("bylaws") == "bylaws"
        assert cp.normalize_subclass("Board Resolution") == "board_resolution"
        assert cp.normalize_subclass("nonexistent") == "other"
        assert cp.normalize_subclass(None) == "other"

    def test_gt_presence_extension_is_registry_driven(self):
        # the corporate FIELD_KEYS entry must be exactly trio + registry keys
        assert gp.FIELD_KEYS_BY_CLASS["corporate_record"] == (
            *cp.CORPORATE_BASE_KEYS, *cp.CORPORATE_ENTITY_KEYS
        )


class TestLiveDojoParity:
    """The EDA mirror must equal the live dojo data module. Enforced by
    sha-pinning the mirrored sources; the facet comparison below runs when
    the module imports (its full package needs structlog etc. installed)."""

    def _dojo_root(self) -> Path | None:
        dojo = ROOT.parent / "llm-dojo-scoring"
        return dojo if (dojo / "llm_dojo_scoring" / "corporate_record_schema.py").is_file() else None

    def test_pinned_sha_guard(self):
        dojo = self._dojo_root()
        if dojo is None:
            pytest.skip("llm-dojo-scoring checkout not present")
        import hashlib

        def sha(p: Path) -> str:
            return hashlib.sha256(p.read_bytes()).hexdigest()

        assert sha(dojo / "llm_dojo_scoring" / "corporate_record_schema.py") == cp.DOJO_SCHEMA_SHA256
        assert sha(dojo / "llm_dojo_scoring" / "corporate_record_relabel.py") == cp.DOJO_RELABEL_SHA256

    def test_mirror_matches_live_dojo_module(self):
        dojo = self._dojo_root()
        if dojo is None:
            pytest.skip("llm-dojo-scoring checkout not present")
        if str(dojo) not in sys.path:
            sys.path.insert(0, str(dojo))
        try:
            from llm_dojo_scoring.corporate_record_schema import (
                ENTITY_CORE_FIELDS,
                RECORD_TYPES,
                SUBCLASS_SCHEMAS,
            )
        except ImportError:
            pytest.skip("dojo deps not importable in this venv")
        assert RECORD_TYPES == cp.RECORD_TYPES
        assert ENTITY_CORE_FIELDS == cp.CORPORATE_ENTITY_CORE_KEYS
        assert set(SUBCLASS_SCHEMAS) == set(cp.SUBCLASS_ORDER)
        for sub, sch in SUBCLASS_SCHEMAS.items():
            assert sch.record_type == cp.SUBCLASS_RECORD_TYPES[sub]
            assert sch.intent == cp.SUBCLASS_INTENTS[sub]
            assert {e.key for e in sch.entities} == set(cp.CORPORATE_SUBCLASS_ENTITY_KEYS[sub])
            assert len(sch.entities) == len(cp.CORPORATE_SUBCLASS_ENTITY_KEYS[sub])
            assert [e.key for e in sch.entities] == list(cp.CORPORATE_SUBCLASS_ENTITY_KEYS[sub])
            assert set(sch.core_applicable) == cp.SUBCLASS_CORE_APPLICABLE[sub]

    @pytest.mark.parametrize("subclass", cp.SUBCLASS_ORDER)
    @pytest.mark.parametrize("intent_source", ["", "heuristic", "human_annotated"])
    def test_valid_relabel_semantics_match_dojo(self, subclass, intent_source):
        dojo = self._dojo_root()
        if dojo is None:
            pytest.skip("llm-dojo-scoring checkout not present")
        from llm_dojo_scoring.corporate_record_relabel import relabel_row
        from llm_dojo_scoring.gt_metadata import parse_gt_fields

        row = _row({
            "subject_matter": f"{subclass} of ACME CORP (CIK 123) (SEC EDGAR exhibit).",
            "keywords": '["EX-3.2", "ACME CORP"]',
            "intent": "adjudicated_intent", "intent_source": intent_source,
            "signatories": "[]", "jurisdiction": "", "filing_number": "",
        }, subclass=subclass)
        actual, actual_changes = cp.relabel_row(row)
        expected, expected_changes = relabel_row(row)
        assert parse_gt_fields(actual["gt_fields"]) == parse_gt_fields(expected["gt_fields"])
        assert actual["expected_subclass"] == expected["expected_subclass"]
        assert actual_changes == expected_changes

    def test_missing_gt_payload_retains_documented_producer_boundary(self):
        dojo = self._dojo_root()
        if dojo is None:
            pytest.skip("llm-dojo-scoring checkout not present")
        from llm_dojo_scoring.corporate_record_relabel import relabel_row
        from llm_dojo_scoring.gt_metadata import parse_gt_fields

        row = _row({})
        row["gt_fields"] = None
        actual, changes = next(cp.relabel_rows([row]))
        assert actual == row
        assert changes == ["gt_unparseable"]
        expected, _ = relabel_row(row)
        assert parse_gt_fields(expected["gt_fields"])["record_type"] == "bylaws"


class TestCompanyFromTemplate:
    def test_plain(self):
        assert cp.company_from_template(
            "bylaws of NINELINE TELECOM (CIK 0001234567) (SEC EDGAR exhibit (EX-3.2))."
        ) == "NINELINE TELECOM"

    def test_real_ticker_and_state_suffix_is_filer_metadata(self):
        # template form is "<one word> of <company> (CIK …) (SEC EDGAR exhibit…)"
        assert cp.company_from_template(
            "bylaws of ACME US CORP (ACME) (CIK 0001234567) (SEC EDGAR exhibit)."
        ) == "ACME US CORP"

    def test_entity_form_suffix_is_not_a_ticker(self):
        # (LLC) is a legal form, not a ticker; and (AG) — the First Majestic
        # case — must never be treated as one either (eda#22 finding guard).
        assert cp.company_from_template(
            "articles of First Majestic Silver LLC (AG) (CIK 0001234567) (SEC EDGAR exhibit)."
        ) == "First Majestic Silver LLC (AG)"

    def test_two_letter_country_code_is_stripped_like_a_state(self):
        # The DOJO relabel convention (the published v9.2.1 source, verified
        # 450/450 by the port) strips ANY trailing /[A-Z]{2}/ code — including
        # country codes like /UK/ — from entity_name. The eda#22 CodeRabbit
        # finding about /UK/ lives in the separate `_clean_filer` EDGAR
        # backfill path, not here.
        assert cp.company_from_template(
            "articles of BROKER LLC /UK/ (CIK 0001234567) (SEC EDGAR exhibit)."
        ) == "BROKER LLC"

    def test_not_a_template(self):
        grounded = "Bylaws of Acme Corp. — amended 2019."
        assert cp.company_from_template(grounded) is None
        assert cp.company_from_template("") is None


class TestSubjectConflicts:
    def test_bylaws_under_articles_conflict(self):
        assert cp.subject_conflicts(
            "articles_of_incorporation", "Bylaws of Acme Corp. as amended."
        ) == ["bylaws"]

    def test_template_subject_never_conflicts(self):
        assert cp.subject_conflicts(
            "articles_of_incorporation",
            "articles_of_incorporation of Acme Corp (CIK 1) (SEC EDGAR exhibit).",
        ) == []

    def test_own_cue_no_conflict(self):
        assert cp.subject_conflicts("bylaws", "Bylaws of Acme Corp. as amended.") == []


def _row(gt_fields: dict, expected: str = "corporate_record",
         subclass: str = "bylaws") -> dict:
    return {
        "filename": "f.htm",
        "expected": expected,
        "expected_subclass": subclass,
        "gt_fields": gt_fields,
        "doc_text": "",
        "metadata": {},
        "coverage_determination": "",
    }


class TestRelabelRow:
    def _base_row(self) -> dict:
        return _row({
            "subject_matter": "bylaws of ACME CORP (CIK 0001234567) (SEC EDGAR exhibit).",
            "keywords": '["EX-3.2", "8-K", "(CIK 0001234567)", "mev", "ACME CORP"]',
            "intent": "governance_rules",
            "intent_source": "heuristic",
            "gt_presence": json.dumps({k: "not_applicable" for k in gp.BASE_GT_FIELDS}),
        })

    def test_relabels_basics(self):
        new, changes = cp.relabel_row(self._base_row())
        assert set(changes) == {"entity_name", "subject_matter", "keywords", "record_type"}
        out = new["gt_fields"]
        assert out["entity_name"] == "ACME CORP"
        assert out["record_type"] == "bylaws"
        assert out["subject_matter"] == "Bylaws of ACME CORP."
        kws = json.loads(out["keywords"])
        assert kws[0] == "Bylaws" and kws[-1] == "ACME CORP"
        assert "(CIK 0001234567)" not in kws and "EX-3.2" not in kws and "8-K" not in kws
        presence = json.loads(out["gt_presence"])
        assert presence["entity_name"] == "populated"
        assert presence["record_type"] == "populated"
        # bylaws cannot be filed with a state office
        assert presence["filing_number"] == "not_applicable"
        # applicable-but-blank core and extension keys are UNSTATED-ABSENT:
        assert "jurisdiction" not in presence
        assert "certifying_officer" not in presence

    def test_non_corporate_row_untouched(self):
        row = _row({"gt_presence": "{}"}, expected="insurance_claim")
        new, changes = cp.relabel_row(row)
        assert new["gt_fields"] == {"gt_presence": "{}"} or not changes
        assert json.dumps(new, sort_keys=True, default=str) == json.dumps(row, sort_keys=True, default=str)

    def test_does_not_mutate_input(self):
        row = self._base_row()
        before = json.dumps(row, sort_keys=True, default=str)
        cp.relabel_row(row)
        assert json.dumps(row, sort_keys=True, default=str) == before

    def test_blank_where_company_unparseable_no_entity_pin(self):
        row = self._base_row()
        row["gt_fields"]["subject_matter"] = "Grounded sentence without a template tail."
        new, changes = cp.relabel_row(row)
        new_gt = new["gt_fields"]
        assert "entity_name" not in new_gt
        presence = json.loads(new_gt["gt_presence"])
        assert "entity_name" not in presence

    def test_never_invents_label_facts(self):
        row = self._base_row()
        row["gt_fields"]["jurisdiction"] = ""  # blank stays blank (value) and
        # unstated-absent (presence) — the relabel never derives a fact here
        new, _ = cp.relabel_row(row)
        presence = json.loads(new["gt_fields"]["gt_presence"])
        assert "jurisdiction" not in presence
        assert new["gt_fields"].get("jurisdiction") == ""

    def test_gt_unparseable_yields_audit_flag_not_crash(self):
        row = _row({"gt_presence": "{bad"})
        row["gt_fields"] = 42  # non-mapping, non-string
        for new, changes in cp.relabel_rows([row]):
            assert changes == ["gt_unparseable"]
            assert new["gt_fields"] == 42  # untouched, audit flags it

    def test_insertion_order_stays_dojo_stable(self):
        """New keys append in the relabel order (entity_name then
        record_type) so the rebuild serializes like the published branch."""
        new, _ = cp.relabel_row(self._base_row())
        keys = list(new["gt_fields"].keys())
        assert keys[-2:] == ["entity_name", "record_type"]


class TestRelabelAllRowsShape:
    """relabel_all_rows (v9_3_revision) inherits the row-shape allowlist."""

    def _corp(self) -> dict:
        return _row({
            "subject_matter": "bylaws of ACME CORP (CIK 0001234567) (SEC EDGAR exhibit).",
            "keywords": "[]",
            "intent": "governance_rules",
            "intent_source": "heuristic",
            "gt_presence": json.dumps({k: "not_applicable" for k in gp.BASE_GT_FIELDS}),
        })

    def test_non_corporate_rows_pass_through(self):
        from mailroom_eda.v9_3_revision import relabel_all_rows

        non = {"filename": "n.txt", "expected": "contract", "expected_subclass": "License",
               "gt_fields": {"cuad_clause_labels": "{}"}, "extra": "x"}
        out = relabel_all_rows([self._corp(), non])
        assert out[1] == non
        assert out[0]["gt_fields"] != self._corp()["gt_fields"]  # relabeled
