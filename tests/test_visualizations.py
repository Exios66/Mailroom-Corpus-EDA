"""Network-free unit tests for mailroom_eda.visualizations's data-shaping
helpers (no matplotlib rendering / no HF snapshot required)."""
from __future__ import annotations

import json

import pandas as pd

from mailroom_eda import visualizations as viz


def _gt_row(filename, cuad_clause_labels, gt_presence):
    return {
        "filename": filename,
        "expected": "contract",
        "cuad_clause_labels": cuad_clause_labels,
        "gt_presence": json.dumps(gt_presence, sort_keys=True),
    }


def test_pending_annotation_filenames_flags_only_pending():
    gt = pd.DataFrame([
        _gt_row("a.txt", "{}", {"cuad_clause_labels": "pending_annotation"}),
        _gt_row("b.txt", '{"Governing Law": ["ny"]}', {"cuad_clause_labels": "populated"}),
        _gt_row("c.txt", "{}", {"cuad_clause_labels": "not_applicable"}),
    ])
    pending = viz._pending_annotation_filenames(gt, "cuad_clause_labels")
    assert pending == {"a.txt"}


def test_pending_annotation_filenames_empty_without_gt_presence_column():
    gt = pd.DataFrame([{"filename": "a.txt", "expected": "contract", "cuad_clause_labels": "{}"}])
    assert viz._pending_annotation_filenames(gt, "cuad_clause_labels") == set()


def test_cuad_matrix_excludes_pending_annotation_rows():
    # A contract with a genuinely empty label set ("b.txt") must still be
    # counted as a zero-clause contract; only the catalogued pending-
    # annotation row ("a.txt", the SEC EDGAR EX-10 issue #30 case) is
    # excluded from the matrix entirely -- it must never be folded into
    # the "0 spans" bucket, which would understate coverage/mean stats.
    gt = pd.DataFrame([
        _gt_row("a.txt", "{}", {"cuad_clause_labels": "pending_annotation"}),
        _gt_row("b.txt", "{}", {"cuad_clause_labels": "schema_documented_absence"}),
        _gt_row("c.txt", '{"Governing Law": ["ny law"]}', {"cuad_clause_labels": "populated"}),
    ])
    mat = viz._cuad_matrix(gt)
    assert set(mat.index) == {"b.txt", "c.txt"}
    assert mat.loc["c.txt", "Governing Law"] == 1
    assert mat.loc["b.txt", "Governing Law"] == 0


def test_cuad_top_clauses_coverage_denominator_excludes_pending(tmp_path, monkeypatch):
    # fig_cuad_top_clauses's "% of contracts containing clause" must be
    # computed over annotated contracts only (n=1 here), not n=2 (which
    # would happen if the pending row silently counted as a 0-clause
    # contract and diluted the coverage percentage).
    monkeypatch.setattr(viz, "FIG_DIR", tmp_path)
    gt = pd.DataFrame([
        _gt_row("a.txt", "{}", {"cuad_clause_labels": "pending_annotation"}),
        _gt_row("c.txt", '{"Governing Law": ["ny law"]}', {"cuad_clause_labels": "populated"}),
    ])
    viz.fig_cuad_top_clauses(gt)
    mat = viz._cuad_matrix(gt)
    coverage = (mat > 0).mean()
    assert coverage["Governing Law"] == 1.0
    assert len(mat) == 1
