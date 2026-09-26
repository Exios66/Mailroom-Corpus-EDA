"""Unit tests for mailroom_eda.v9_1_revision (mailroom-issues#196 Phase B).

Network-free: exercises revise_row / verify_zero_drift / manifest+card
rendering against synthetic rows, so the transform and its safety checks are
verified regardless of whether a live Hub snapshot is present.
"""
from __future__ import annotations

import json

import pytest

from mailroom_eda import v9_1_revision as rev
from mailroom_eda.config import REPO_ID, V8_REPO_ID
from mailroom_eda.gt_presence import ALL_GT_FIELDS, PRESENCE_CODES


def _gt_row(**over) -> dict:
    row = {
        "filename": "cuad_001.txt",
        "prompt": "",
        "expected": "contract",
        "expected_subclass": "License Agreements",
        "split": "train",
        "gt_fields": {
            "cuad_clause_labels": '{"Governing Law": ["New York law"]}',
            "maud_clause_labels": "{}",
            "intent": "", "subject_matter": "", "keywords": "",
        },
        "_published": "",
        "document_id": "DOC-abc123",
        "source_corpus": "theatticusproject/cuad",
        "source_document_id": "cuad_001.txt",
        "source_filename": "cuad_001.txt",
        "source_revision": "",
        "content_sha256": "deadbeef",
        "normalized_text_sha256": "deadbeef2",
        "expected_specialist": "contract_specialist",
        "expected_stage": "",
        "review_expected": "",
        "review_reason": "",
        "retry_expected": "",
        "expected_post_retry_state": "",
        "annotation_source": "cuad",
        "annotation_method": "source_native",
        "annotation_model": "",
        "annotation_prompt_version": "",
        "annotation_confidence": "",
        "annotation_reviewer": "",
        "annotation_timestamp": "",
        "matter_id": "",
        "matter_construction": "",
        "group_id": "",
        "group_role": "",
        "thread_position": "",
        "thread_size": "",
        "thread_evidence": "",
        "relationships": [],
        "related_document_ids": [],
        "doc_text": "This Consulting Agreement is governed by New York law.",
        "metadata": {
            "clause_count": "1",
            "maud_label_count": "",
            "source_dataset": "theatticusproject/cuad",
            "filer": "",
        },
    }
    row.update(over)
    return row


class TestRevisesTargetV9Dataset:
    """This revision must target Lucius-Morningstar/mailroom-dataset (v1,
    canonically v9) — never the frozen v8 mailroom-corpus baseline."""

    def test_repo_id_is_v9_dataset(self):
        assert rev.REPO_ID == REPO_ID == "Lucius-Morningstar/mailroom-dataset"

    def test_module_never_references_v8_repo(self):
        import inspect

        src = inspect.getsource(rev)
        assert V8_REPO_ID not in src
        assert "mailroom-corpus" not in src


class TestReviseRow:
    def test_strips_weak_indirect_signals_from_metadata(self):
        out = rev.revise_row(_gt_row())
        assert "clause_count" not in out["metadata"]
        assert "maud_label_count" not in out["metadata"]
        assert out["metadata"]["filer"] == ""  # untouched keys survive

    def test_moves_weak_indirect_signals_into_gt_fields(self):
        out = rev.revise_row(_gt_row())
        assert out["gt_fields"]["clause_count"] == "1"
        assert out["gt_fields"]["maud_label_count"] == ""

    def test_adds_token_estimate_and_band(self):
        out = rev.revise_row(_gt_row())
        assert out["gt_fields"]["token_estimate"].isdigit()
        assert out["gt_fields"]["context_window_band"] in {"<=4k", "<=16k", "<=32k", ">32k"}

    def test_adds_gt_presence_with_closed_vocabulary(self):
        out = rev.revise_row(_gt_row())
        presence = json.loads(out["gt_fields"]["gt_presence"])
        assert set(presence) == set(ALL_GT_FIELDS)
        assert set(presence.values()) <= set(PRESENCE_CODES)

    def test_ex10_row_is_pending_annotation_not_silent_empty(self):
        row = _gt_row(
            gt_fields={"cuad_clause_labels": "{}", "maud_clause_labels": "{}",
                      "intent": "", "subject_matter": "", "keywords": ""},
            metadata={"clause_count": "", "maud_label_count": "",
                      "source_dataset": "sec_edgar", "filer": "Acme"},
        )
        out = rev.revise_row(row)
        presence = json.loads(out["gt_fields"]["gt_presence"])
        assert presence["cuad_clause_labels"] == "pending_annotation"

    def test_does_not_mutate_input_row(self):
        row = _gt_row()
        original_metadata = dict(row["metadata"])
        original_gt = dict(row["gt_fields"])
        rev.revise_row(row)
        assert row["metadata"] == original_metadata
        assert row["gt_fields"] == original_gt

    def test_untouched_fields_pass_through_verbatim(self):
        row = _gt_row()
        out = rev.revise_row(row)
        for col in (
            "filename", "document_id", "content_sha256", "doc_text",
            "source_corpus", "matter_id", "relationships",
        ):
            assert out[col] == row[col]

    def test_deterministic_on_repeated_calls(self):
        row = _gt_row()
        a = rev.revise_row(row)
        b = rev.revise_row(row)
        assert a["gt_fields"] == b["gt_fields"]
        assert a["metadata"] == b["metadata"]


class TestVerifyZeroDrift:
    def test_passes_on_correctly_revised_rows(self):
        old = [_gt_row()]
        new = rev.revise_rows(old)
        rev.verify_zero_drift(old, new)  # must not raise

    def test_fails_if_doc_text_drifted(self):
        old = [_gt_row()]
        new = rev.revise_rows(old)
        new[0]["doc_text"] = "TAMPERED"
        with pytest.raises(AssertionError):
            rev.verify_zero_drift(old, new)

    def test_fails_if_document_id_drifted(self):
        old = [_gt_row()]
        new = rev.revise_rows(old)
        new[0]["document_id"] = "DOC-different"
        with pytest.raises(AssertionError):
            rev.verify_zero_drift(old, new)

    def test_fails_if_preexisting_gt_field_drifted(self):
        old = [_gt_row()]
        new = rev.revise_rows(old)
        new[0]["gt_fields"]["cuad_clause_labels"] = "TAMPERED"
        with pytest.raises(AssertionError):
            rev.verify_zero_drift(old, new)

    def test_fails_if_weak_signal_not_stripped(self):
        old = [_gt_row()]
        new = rev.revise_rows(old)
        new[0]["metadata"]["clause_count"] = "1"  # re-inject
        with pytest.raises(AssertionError):
            rev.verify_zero_drift(old, new)

    def test_allows_unrelated_metadata_keys_through(self):
        old = [_gt_row()]
        new = rev.revise_rows(old)
        assert new[0]["metadata"]["source_dataset"] == "theatticusproject/cuad"


class TestVerifyPresenceCodes:
    def test_passes_on_revised_rows(self):
        rows = rev.revise_rows([_gt_row(), _gt_row(filename="cuad_002.txt")])
        counts = rev.verify_presence_codes(rows)
        assert sum(counts.values()) == len(rows) * len(ALL_GT_FIELDS)

    def test_fails_on_genuine_gap(self):
        row = _gt_row(
            gt_fields={"cuad_clause_labels": "{}", "maud_clause_labels": "{}",
                      "intent": "", "subject_matter": "", "keywords": ""},
            metadata={"clause_count": "", "maud_label_count": "",
                      "source_dataset": "theatticusproject/cuad"},
        )
        revised = rev.revise_row(row)
        with pytest.raises(AssertionError, match="genuine_gap"):
            rev.verify_presence_codes([revised])


class TestVerifyContextBands:
    def test_passes_on_revised_rows(self):
        rows = rev.revise_rows([_gt_row()])
        rev.verify_context_bands(rows)  # must not raise

    def test_fails_if_weak_signal_survives(self):
        rows = rev.revise_rows([_gt_row()])
        rows[0]["metadata"]["clause_count"] = "5"
        with pytest.raises(AssertionError):
            rev.verify_context_bands(rows)


class TestRenderReadmeAddendum:
    def test_starts_with_heading(self):
        addendum = rev.render_readme_addendum(
            {c: 1 for c in PRESENCE_CODES}, {"<=4k": 1, "<=16k": 1, "<=32k": 1, ">32k": 1}, 10
        )
        assert addendum.startswith(f"## {rev.V9_1_VERSION} quality revision")
        assert rev.ISSUE_REF in addendum

    def test_ends_with_blank_line(self):
        addendum = rev.render_readme_addendum(
            {c: 1 for c in PRESENCE_CODES}, {"<=4k": 1, "<=16k": 1, "<=32k": 1, ">32k": 1}, 10
        )
        assert addendum.endswith("\n\n")


class TestUpsertReadmeAddendum:
    def test_appends_when_absent(self):
        card = "# Title\n\nsome content\n"
        addendum = "## v9.1 quality revision (mailroom-issues#196 Phase B)\n\nbody\n\n"
        out = rev.upsert_readme_addendum(card, addendum)
        assert out.startswith(card)
        assert out.endswith(addendum)

    def test_idempotent_on_repeated_apply(self):
        card = "# Title\n\nsome content\n"
        addendum = "## v9.1 quality revision (mailroom-issues#196 Phase B)\n\nbody\n\n"
        once = rev.upsert_readme_addendum(card, addendum)
        twice = rev.upsert_readme_addendum(once, addendum)
        assert once == twice

    def test_replaces_when_present_and_updates_body(self):
        card = (
            "# Title\n\nsome content\n\n"
            "## v9.1 quality revision (mailroom-issues#196 Phase B)\n\nOLD body\n\n"
            "## Later section\n\nkept\n"
        )
        addendum = "## v9.1 quality revision (mailroom-issues#196 Phase B)\n\nNEW body\n\n"
        out = rev.upsert_readme_addendum(card, addendum)
        assert "OLD body" not in out
        assert "NEW body" in out
        assert "## Later section\n\nkept" in out


class TestRenderManifestV91:
    _SHA_A = "a" * 64
    _SHA_B = "b" * 64
    _SHA_C = "c" * 64
    OLD_MANIFEST = (
        "name       : Lucius-Morningstar/mailroom-dataset\n"
        "rows       : 3302 (train 2979, test 323)\n"
        "split_rule : md5(filename) % 10 == 0 -> test (evaluation partition, NOT ML semantics)\n"
        "built_utc  : 2026-09-13T22:48:20Z\n"
        "sha256     :\n"
        f"  README.md  {_SHA_A}\n"
        f"  bundles.jsonl  {_SHA_B}\n"
        f"  parquet/default/train/train-00000-of-00001.parquet  {_SHA_C}\n"
    )

    def test_preserves_row_counts_line(self):
        out = rev.render_manifest_v9_1(self.OLD_MANIFEST, {}, "2026-09-27T00:00:00Z")
        assert "rows       : 3302 (train 2979, test 323)" in out

    def test_updates_built_utc(self):
        out = rev.render_manifest_v9_1(self.OLD_MANIFEST, {}, "2026-09-27T00:00:00Z")
        assert "built_utc  : 2026-09-27T00:00:00Z" in out
        assert "2026-09-13T22:48:20Z" not in out

    def test_adds_revision_line_once(self):
        out = rev.render_manifest_v9_1(self.OLD_MANIFEST, {}, "2026-09-27T00:00:00Z")
        assert out.count("revision   :") == 1
        assert rev.ISSUE_REF in out

    def test_idempotent_revision_line_on_reapply(self):
        once = rev.render_manifest_v9_1(self.OLD_MANIFEST, {}, "2026-09-27T00:00:00Z")
        twice = rev.render_manifest_v9_1(once, {}, "2026-09-28T00:00:00Z")
        assert twice.count("revision   :") == 1
        assert twice.count("built_utc  :") == 1

    def test_updates_only_touched_shas(self):
        new_sha = "d" * 64
        out = rev.render_manifest_v9_1(
            self.OLD_MANIFEST,
            {"parquet/default/train/train-00000-of-00001.parquet": new_sha},
            "2026-09-27T00:00:00Z",
        )
        assert f"  parquet/default/train/train-00000-of-00001.parquet  {new_sha}" in out
        assert f"  README.md  {self._SHA_A}" in out  # untouched sha survives
        assert f"  bundles.jsonl  {self._SHA_B}" in out  # untouched sha survives

    def test_adds_new_sha_entries(self):
        new_sha = "e" * 64
        out = rev.render_manifest_v9_1(
            self.OLD_MANIFEST,
            {"ground_truth_hardened.jsonl": new_sha},
            "2026-09-27T00:00:00Z",
        )
        assert f"  ground_truth_hardened.jsonl  {new_sha}" in out


class TestGtRowSchemaFields:
    def test_exactly_36_fields(self):
        assert len(rev.GT_ROW_SCHEMA_FIELDS) == 36

    def test_no_duplicate_fields(self):
        assert len(set(rev.GT_ROW_SCHEMA_FIELDS)) == len(rev.GT_ROW_SCHEMA_FIELDS)

    def test_excludes_doc_text_and_metadata(self):
        assert "doc_text" not in rev.GT_ROW_SCHEMA_FIELDS
        assert "metadata" not in rev.GT_ROW_SCHEMA_FIELDS


class TestStageDefaultAndGroundTruth:
    def test_blind_config_is_exactly_four_columns(self, tmp_path):
        rows = rev.revise_rows([_gt_row(), _gt_row(filename="cuad_002.txt", split="test")])
        rev.stage_default_and_ground_truth(rows, tmp_path)
        rev.verify_blind_label_free(tmp_path)  # must not raise

    def test_ground_truth_has_36_columns(self, tmp_path):
        import pandas as pd

        rows = rev.revise_rows([_gt_row()])
        rev.stage_default_and_ground_truth(rows, tmp_path)
        df = pd.read_parquet(tmp_path / "parquet" / "ground_truth" / "train" / "train-00000-of-00001.parquet")
        assert len(df.columns) == 36
        gt = json.loads(df.iloc[0]["gt_fields"])
        assert "gt_presence" in gt
        assert "clause_count" in gt

    def test_row_counts_match_split(self, tmp_path):
        rows = rev.revise_rows(
            [_gt_row(filename="a.txt", split="train"),
            _gt_row(filename="b.txt", split="test")]
        )
        counts = rev.stage_default_and_ground_truth(rows, tmp_path)
        assert counts[("default", "train")] == 1
        assert counts[("default", "test")] == 1
        assert counts[("ground_truth", "train")] == 1
        assert counts[("ground_truth", "test")] == 1
