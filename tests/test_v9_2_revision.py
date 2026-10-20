"""Unit tests for mailroom_eda.v9_2_revision (plan
docs/plans/v9.2-relations-provenance-revision.md).

Network-free: exercises revise_row / verify_zero_drift / verify_annotation_regimes
/ verify_relations / stage_default_and_ground_truth / manifest rendering
against synthetic rows, so the transform and its safety checks are verified
regardless of whether a live Hub snapshot is present.
"""
from __future__ import annotations

import json

import pytest

from mailroom_eda import v9_2_revision as rev
from mailroom_eda.config import REPO_ID, V8_REPO_ID


def _gt_row(**over) -> dict:
    row = {
        "filename": "enron_x_010.txt",
        "prompt": "",
        "expected": "correspondence",
        "expected_subclass": "notice",
        "split": "train",
        "gt_fields": {
            "intent": "notice", "subject_matter": "x", "keywords": "y",
            "intent_source": "llm_zero_shot", "intent_confidence": "0.91",
            "relationships": "[]", "related_document_ids": "[]",
        },
        "_published": "{\"document_id\": \"DOC-old\"}",
        "document_id": "DOC-abc123",
        "source_corpus": "Lucius-Morningstar/enron-correspondence-dedup",
        "source_document_id": "enron_x_010.txt",
        "source_filename": "enron_x_010.txt",
        "source_revision": "",
        "content_sha256": "deadbeef",
        "normalized_text_sha256": "deadbeef2",
        "expected_specialist": "correspondence_specialist",
        "expected_stage": "archived",
        "review_expected": "false",
        "review_reason": "",
        "retry_expected": "false",
        "expected_post_retry_state": "",
        "annotation_source": "Lucius-Morningstar/enron-correspondence-dedup",
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
        "doc_text": "Subject: Q3 numbers\n\nbody",
        "metadata": {"custodian": "k", "date": "2001-05-01"},
    }
    row.update(over)
    return row


class TestTargetsV9Dataset:
    def test_repo_id_is_v9_dataset(self):
        assert rev.REPO_ID == REPO_ID == "Lucius-Morningstar/mailroom-dataset"

    def test_module_never_targets_v8_repo(self):
        import inspect

        src = inspect.getsource(rev)
        # the historical v8 baseline may be named in prose (the card-replacement
        # text), but the module must never target or import the v8 repo id.
        assert V8_REPO_ID not in src
        assert "V8_REPO_ID" not in src


class TestSchema:
    def test_gt_schema_is_35_columns_without_published(self):
        assert len(rev.GT_ROW_SCHEMA_FIELDS) == 35
        assert len(set(rev.GT_ROW_SCHEMA_FIELDS)) == 35  # no duplicate labels
        assert "_published" not in rev.GT_ROW_SCHEMA_FIELDS
        assert rev.GT_BASE_FIELDS == (
            "filename", "prompt", "expected", "expected_subclass", "split", "gt_fields",
        )

    def test_expected_regimes_constant_matches_plan_table(self):
        assert rev.EXPECTED_REGIMES == {
            "verified_join": 162, "llm_zero_shot": 637, "human_annotated": 96,
            "synthetic": 1100, "heuristic": 646, "source_native": 661,
        }
        assert sum(rev.EXPECTED_REGIMES.values()) == 3302


class TestReviseRow:
    def test_pops_published(self):
        out = rev.revise_row(_gt_row())
        assert "_published" not in out

    def test_recomputes_annotation_from_nested_gt_fields(self):
        out = rev.revise_row(_gt_row())
        assert out["annotation_method"] == "llm_zero_shot"
        assert out["annotation_model"] == "deepseek-chat"
        assert out["annotation_confidence"] == "0.91"

    def test_manual_row_gets_human_reviewer(self):
        row = _gt_row(gt_fields={"intent_source": "manual", "intent_confidence": "1.0",
                                 "relationships": "[]", "related_document_ids": "[]"})
        out = rev.revise_row(row)
        assert out["annotation_method"] == "human_annotated"
        assert out["annotation_reviewer"] == "human"

    def test_source_corpus_precedence_on_edgar_contract(self):
        row = _gt_row(expected="contract", source_corpus="sec_edgar",
                      gt_fields={"cuad_clause_labels": "{}", "relationships": "[]",
                                 "related_document_ids": "[]"})
        out = rev.revise_row(row)
        assert out["annotation_source"] == "sec_edgar"

    def test_does_not_mutate_input(self):
        row = _gt_row()
        before = json.dumps(row, sort_keys=True, default=str)
        rev.revise_row(row)
        assert json.dumps(row, sort_keys=True, default=str) == before


class TestZeroDrift:
    def test_passes_on_revised_row(self):
        old = _gt_row()
        new = rev.revise_row(old)
        rev.verify_zero_drift([old], [new])  # no raise

    def test_tolerates_published_removal_and_mirror_sync(self):
        old = _gt_row()
        new = rev.revise_row(old)
        # mirror sync is allowed to change the two nested keys
        new["gt_fields"]["relationships"] = '["responds_to"]'
        new["gt_fields"]["related_document_ids"] = '["DOC-zzz"]'
        new["relationships"] = ["responds_to"]
        new["related_document_ids"] = ["DOC-zzz"]
        rev.verify_zero_drift([old], [new])  # no raise

    @pytest.mark.parametrize("col", ["document_id", "doc_text", "content_sha256"])
    def test_fails_on_identity_or_content_drift(self, col):
        old = _gt_row()
        new = rev.revise_row(old)
        new[col] = "TAMPERED"
        with pytest.raises(AssertionError):
            rev.verify_zero_drift([old], [new])

    def test_fails_on_matter_scalar_drift(self):
        old = _gt_row(matter_id="MATTER-1", matter_construction="heuristic_reconstructed")
        new = rev.revise_row(old)
        new["matter_id"] = "MATTER-2"
        with pytest.raises(AssertionError):
            rev.verify_zero_drift([old], [new])

    def test_fails_on_non_mirror_gt_key_drift(self):
        old = _gt_row()
        new = rev.revise_row(old)
        new["gt_fields"]["intent"] = "TAMPERED"
        with pytest.raises(AssertionError):
            rev.verify_zero_drift([old], [new])


class TestVerifiers:
    def test_annotation_regimes_rejects_wrong_counts(self):
        rows = [rev.revise_row(_gt_row())]
        with pytest.raises(AssertionError):
            rev.verify_annotation_regimes(rows)

    def test_relations_requires_23_matter_rows(self):
        with pytest.raises(AssertionError):
            rev.verify_relations([rev.revise_row(_gt_row())])


class TestStaging:
    def test_stages_35_gt_columns_and_4_blind(self, tmp_path):
        import pandas as pd

        rows = [rev.revise_row(_gt_row())]
        counts = rev.stage_default_and_ground_truth(rows, tmp_path)
        assert counts[("default", "train")] == 1
        gt = pd.read_parquet(tmp_path / "parquet" / "ground_truth" / "train"
                             / "train-00000-of-00001.parquet")
        assert len(gt.columns) == 35
        assert "_published" not in gt.columns
        blind = pd.read_parquet(tmp_path / "parquet" / "default" / "train"
                                / "train-00000-of-00001.parquet")
        assert set(blind.columns) == {"filename", "doc_text", "prompt", "metadata"}
        rev.verify_blind_label_free(tmp_path)  # no raise


class TestManifest:
    def test_version_line_says_v9_2_and_is_idempotent(self):
        old = "header : x\nsplit_rule : md5\nsha256     :\n  a  0" + "0" * 63 + "\n"
        up = {"README.md": "1" * 64}
        m1 = rev.render_manifest_v9_2(old, up, "2026-10-05T00:00:00Z")
        assert "v9.2 relations & provenance revision" in m1
        assert "README.md" in m1
        m2 = rev.render_manifest_v9_2(m1, up, "2026-10-05T00:00:00Z")
        assert m2 == m1


class TestVerifierDetection:
    def test_verify_parity_detects_sidecar_divergence(self):
        merged = [rev.revise_row(_gt_row())]
        side = [rev.revise_row(_gt_row())]
        side[0]["annotation_method"] = "TAMPERED"
        with pytest.raises(AssertionError):
            rev.verify_parity(merged, side)

    def test_source_revision_unchanged_detects_drift(self):
        old = [_gt_row()]
        new = [rev.revise_row(_gt_row())]
        new[0]["source_revision"] = "CHANGED"
        with pytest.raises(AssertionError):
            rev.verify_source_revision_unchanged(old, new)


class TestHardenedStagingFilter:
    def test_stage_configs_drops_underscore_keys(self, tmp_path):
        """v9.2 C2 / R5: any `_`-prefixed build-internal column (e.g. the v8
        `_published` map) must never reach a published config."""
        import pandas as pd

        from mailroom_eda import hardened

        row = _gt_row()
        row["_published"] = '{"document_id": "DOC-old"}'
        hardened.stage_configs([row], [], [], [], tmp_path)
        gt = pd.read_parquet(tmp_path / "parquet" / "ground_truth" / "train"
                             / "train-00000-of-00001.parquet")
        assert not any(c.startswith("_") for c in gt.columns)
        assert "_published" not in gt.columns


class TestStageParquetDefault:
    def test_default_surface_is_current_gt_scalar_keys(self, tmp_path):
        """v9.2 C5: the default `stage_parquet` surface is the 32-key current
        set (so a rebuild can never silently drop the five v9.1 keys)."""
        import pandas as pd

        from mailroom_eda import dataset_export
        from mailroom_eda.release_sections import GT_SCALAR_KEYS

        row = {"filename": "x.txt", "doc_text": "d", "prompt": "", "expected": "contract",
               "expected_subclass": "s", "split": "train",
               "metadata": {"source_dataset": "theatticusproject/cuad"}, "gt_fields": {}}
        dataset_export.stage_parquet([row], tmp_path)
        gt = pd.read_parquet(tmp_path / "parquet" / "ground_truth" / "train"
                             / "train-00000-of-00001.parquet")
        assert len(gt.columns) == 4 + len(GT_SCALAR_KEYS) == 36


class TestWorkstreamSDeferred:
    def test_with_stage_configs_defers_to_v9_3(self):
        with pytest.raises(SystemExit):
            rev.build_all(with_stage_configs=True)
