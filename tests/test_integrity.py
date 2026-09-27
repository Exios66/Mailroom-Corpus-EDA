"""Network-free unit tests for mailroom_eda.integrity's data-shaping helpers."""
from __future__ import annotations

import pandas as pd

from mailroom_eda import integrity as itg


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
