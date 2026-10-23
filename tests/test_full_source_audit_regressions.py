"""Identity and staging regressions found by the full independent source audit."""

import json

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mailroom_eda import composition, integrity, matter, visualizations
from mailroom_eda.v9_3_revision import verify_staged_parquet_gt_fields


@pytest.fixture
def reordered_configs():
    """Independent configurations share filenames, not row order or indexes."""
    blind = pd.DataFrame({"filename": ["b", "a"], "split": ["train", "train"],
                          "doc_text": ["BB", "AAAAAAAA"],
                          "metadata": [{"tags": "", "source": "B", "source_dataset": "B"},
                                       {"tags": "present", "source": "A", "source_dataset": "A"}]}, index=[9, 4])
    gt = pd.DataFrame({"filename": ["a", "b"], "split": ["train", "train"],
                       "expected": ["contract", "correspondence"],
                       "expected_subclass": ["license", "email"]}, index=[100, 200])
    return blind, gt


def test_metadata_coverage_joins_by_filename(reordered_configs):
    """A populated contract field cannot be reassigned to correspondence."""
    result = integrity.metadata_coverage(*reordered_configs)
    assert result.loc["contract", "tags"] == 1.0
    assert result.loc["correspondence", "tags"] == 0.0


def test_text_and_provenance_follow_filename_identity(reordered_configs):
    """Both downstream report consumers retain the correct text and source."""
    text = visualizations._text_frame(*reordered_configs).set_index("filename")
    assert (text.loc["a", "doc_type"], text.loc["a", "chars"]) == ("contract", 8)
    assert (text.loc["b", "doc_type"], text.loc["b", "chars"]) == ("correspondence", 2)
    provenance = composition.provenance(*reordered_configs).set_index("doc_type")
    assert provenance.loc["contract", "source_values"] == "A"
    assert provenance.loc["correspondence", "source_values"] == "B"


def test_temporal_reports_follow_filename_identity(reordered_configs):
    """Temporal charts must attach independently ordered years to their own files."""
    blind, gt = reordered_configs
    blind.at[9, "metadata"]["date"] = "2002-01-01"
    blind.at[4, "metadata"]["date"] = "2001-01-01"
    frame = visualizations._temporal_frame(blind, gt, integrity._meta_series(blind)).set_index("filename")
    assert (frame.loc["a", "doc_type"], frame.loc["a", "year"]) == ("contract", 2001)
    assert (frame.loc["b", "doc_type"], frame.loc["b", "year"]) == ("correspondence", 2002)


def test_metadata_label_check_uses_filename_join(reordered_configs):
    """Correct blind routing metadata agrees despite independent indexes."""
    blind, gt = reordered_configs
    blind.at[9, "metadata"]["expected_doc_type"] = "correspondence"
    blind.at[4, "metadata"]["expected_doc_type"] = "contract"
    assert integrity.audit_metadata_label_consistency(blind, gt)["agree_rate_on_filled"] == 1.0


def test_maud_count_check_uses_filename_join(reordered_configs):
    """Two distinct annotation counts stay paired with their own metadata."""
    blind, gt = reordered_configs
    blind.at[9, "metadata"]["maud_categories"] = '{"No-Shop": 1}'
    blind.at[4, "metadata"]["maud_categories"] = '{"No-Shop": 2}'
    gt["maud_clause_labels"] = ['{"No-Shop": ["a"]}', '{"No-Shop": ["b"]}']
    gt["maud_label_count"] = ["2", "1"]
    result = integrity.audit_maud_labels(blind, gt)
    assert result["metadata_count_checked"] == result["metadata_count_consistency_ok"] == 2


def test_claim_source_split_check_uses_filename_join(reordered_configs):
    """A claims record ID is read from its own blind row, not a GT index."""
    blind, gt = reordered_configs
    blind.at[9, "metadata"]["record_id"] = "claim-record"
    blind.at[4, "metadata"]["record_id"] = ""
    gt["expected"] = ["contract", "insurance_claim"]
    assert integrity.audit_split_rule(blind, gt)["claims_source_rule"]["checked"] == 1


@pytest.mark.parametrize("invalid", ["duplicate", "missing", "null"])
def test_cross_config_reports_refuse_ambiguous_identity(reordered_configs, invalid):
    """Unknown or duplicate identities fail instead of inventing a class join."""
    blind, gt = reordered_configs
    if invalid == "duplicate":
        gt["filename"] = ["a", "a"]
    elif invalid == "missing":
        gt["filename"] = ["a", "c"]
    else:
        gt.loc[gt.index[0], "filename"] = None
    with pytest.raises(ValueError, match="filename"):
        integrity.metadata_coverage(blind, gt)


@pytest.mark.parametrize("column,value", [("expected", "contract"),
                                          ("expected_subclass", "rights_instrument")])
def test_staged_parquet_routing_matches_jsonl(tmp_path, column, value):
    """Unchanged GT values do not excuse a changed class or subclass."""
    rows = [{"filename": "a", "expected": "corporate_record", "expected_subclass": "bylaws",
             "split": "train", "gt_fields": {"entity_name": "Acme"}},
            {"filename": "b", "expected": "insurance_claim", "expected_subclass": "auto",
             "split": "test", "gt_fields": {"claim_number": "X"}}]
    for row in rows:
        rec = dict(row, gt_fields=json.dumps(row["gt_fields"]))
        if row["filename"] == "a":
            rec[column] = value
        path = tmp_path / "parquet" / "ground_truth" / row["split"] / "part.parquet"
        path.parent.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist([rec]), path)
    with pytest.raises(AssertionError, match=rf"a:.*{column}"):
        verify_staged_parquet_gt_fields(rows, tmp_path)


@pytest.mark.parametrize("reverse", [False, True])
def test_native_thread_detection_and_guard_ignore_row_order(reverse):
    """A reply before its parent must still trigger the unexpected-thread guard."""
    rows = [{"filename": "a", "metadata": {"custodian": "k", "message_id": "<a@x>", "date": "2001-01-01"}},
            {"filename": "b", "metadata": {"custodian": "k", "message_id": "<b@x>",
                                                "in_reply_to": "<a@x>", "date": "2001-01-02"}}]
    if reverse:
        rows.reverse()
    detected = matter.source_native_threads(rows)
    assert {r["filename"] for r in detected} == {"a", "b"}
    assert len({r["matter_id"] for r in detected}) == 1
    with pytest.raises(AssertionError, match="source_native_thread groups found"):
        matter.enrich_rows(rows)
