"""P1 structural integrity audit (integrity.py) — focused regressions.

The MAUD count-consistency check compares the GT-side annotation count against
the blind-side ``maud_categories`` sum. After the v9.1 B1 fix moved
``maud_label_count`` out of blind metadata into ``gt_fields``, the audit kept
reading blind metadata and silently shipped ``0/152`` (a false regression).
This pins the corrected cross-surface read.
"""

from __future__ import annotations

import pandas as pd
import pytest

from mailroom_eda import integrity


def test_maud_count_consistency_reads_gt_side(snapshot_rows, snapshot_metadata):
    """GT ``maud_label_count`` == sum(blind ``maud_categories``) on all 152
    MAUD rows (the B1 relocation must not zero this check)."""
    if not snapshot_rows:
        pytest.skip("local HF snapshot absent (data/parquet) — fetch via run_all.py P0")

    gt = pd.DataFrame(snapshot_rows)
    # blind metadata lives in the default config; align to the GT row order so
    # the audit's positional ``iloc`` pairing is exactly what integrity.run uses
    blind = pd.DataFrame(
        [{"filename": r["filename"],
          "metadata": snapshot_metadata.get(r["filename"], {})}
         for r in snapshot_rows]
    )
    report = integrity.audit_maud_labels(blind, gt)

    assert report["metadata_count_checked"] == 152
    assert report["metadata_count_consistency_ok"] == 152
    assert report["rows_with_labels"] == 152
    # the semantics string names the GT-side source of truth
    assert "gt_fields.maud_label_count" in report["metadata_count_semantics"]


def test_maud_count_not_read_from_blind_metadata():
    """Regression guard: a row whose blind metadata lacks the count but whose
    GT carries it still verifies (the pre-fix read returned 0)."""
    blind = pd.DataFrame(
        [{"filename": "m.txt", "metadata": {"maud_categories": '{"No-Shop": 2}'}}]
    )
    gt = pd.DataFrame(
        [{"filename": "m.txt", "maud_clause_labels": '{"No-Shop": ["a", "b"]}',
          "maud_label_count": "2"}]
    )
    report = integrity.audit_maud_labels(blind, gt)
    assert report["metadata_count_checked"] == 1
    assert report["metadata_count_consistency_ok"] == 1


from mailroom_eda import integrity as itg  # noqa: E402


def test_metadata_coverage_treats_empty_string_as_absent():
    # Metadata is cast-safe (KANBAN-076): every row carries the full key
    # union, with '' standing in for "not applicable to this row" -- never
    # a real NaN. A naive .notna() check would report 100% fill on a field
    # that is empty on every row of a doc_type; is_populated-based coverage
    # must report 0%.
    blind = pd.DataFrame({
        "filename": ["a.txt", "b.txt", "c.txt"],
        "metadata": [
            {"adjuster": "", "insurer": "Acme Mutual"},
            {"adjuster": "", "insurer": "Acme Mutual"},
            {"adjuster": "J. Rivera", "insurer": ""},
        ],
    })
    gt = pd.DataFrame({
        "filename": ["a.txt", "b.txt", "c.txt"],
        "expected": ["insurance_claim", "insurance_claim", "insurance_claim"],
    })
    cov = itg.metadata_coverage(blind, gt)
    assert cov.loc["insurance_claim", "adjuster"] == 1 / 3
    assert cov.loc["insurance_claim", "insurer"] == 2 / 3


def test_metadata_coverage_json_no_item_markers_count_as_absent():
    blind = pd.DataFrame({
        "filename": ["a.txt", "b.txt"],
        "metadata": [
            {"tags": "[]"},
            {"tags": '["urgent"]'},
        ],
    })
    gt = pd.DataFrame({
        "filename": ["a.txt", "b.txt"],
        "expected": ["correspondence", "correspondence"],
    })
    cov = itg.metadata_coverage(blind, gt)
    assert cov.loc["correspondence", "tags"] == 0.5


@pytest.mark.parametrize("second", [{}, {"tags": float("nan")}, {"tags": None}, {"tags": pd.NA}])
def test_metadata_coverage_excludes_sparse_and_null_cells(second):
    blind = pd.DataFrame({"filename": ["a.txt", "b.txt"],
                          "metadata": [{"tags": "present"}, second]})
    gt = pd.DataFrame({"filename": ["a.txt", "b.txt"],
                       "expected": ["correspondence", "correspondence"]})
    assert itg.metadata_coverage(blind, gt).loc["correspondence", "tags"] == 0.5
