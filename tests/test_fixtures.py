"""§70 calibration quartet + §72A review/arbiter fixtures — tests (HUB-022)."""
from __future__ import annotations

from collections import Counter

import pytest

from mailroom_eda import eval_contract as ec
from mailroom_eda import fixtures as fx
from conftest import FIVE_CLASSES, load_fixture_rows


@pytest.fixture(scope="module")
def suite():
    return fx.build_fixture_suite()


def test_filenames_use_fixture_namespace(suite):
    for row in suite:
        assert row["filename"].startswith(fx.FIXTURE_NS)
        assert row["synthetic"] == "true"


def test_classes_and_kinds_in_closed_vocabularies(suite):
    for row in suite:
        assert row["expected"] in FIVE_CLASSES
        assert row["fixture_kind"] in ec.FIXTURE_KINDS
        if row["failure_stage"]:
            assert row["failure_stage"] in ec.FAILURE_STAGES
        reason = row["review_reason"]
        assert reason == "" or reason in ec.REVIEW_REASONS
        if row["retry_expected"] == "true":
            assert row["expected_post_retry_state"] in ec.POST_RETRY_STATES


def test_quartet_covers_all_cells_once_per_class(suite):
    quartet_rows = [r for r in suite if r["calibration_cell"]]
    classes = sorted({r["expected"] for r in quartet_rows})
    assert classes == sorted(ec.SPECIALIST_BY_CLASS)
    counts = Counter((r["expected"], r["calibration_cell"]) for r in quartet_rows)
    assert set(counts.values()) == {1}
    assert {c for _, c in counts} == set(ec.CALIBRATION_QUARTET)


def test_quartet_kinds_match_closed_mapping(suite):
    for row in suite:
        if row["calibration_cell"]:
            assert row["fixture_kind"] == ec.CONFIDENCE_CELL_FIXTURE_KIND[row["calibration_cell"]]


def test_quartet_probes_live_band_edges(suite):
    bands = ec.confidence_bands()
    for row in suite:
        if not row["calibration_cell"]:
            continue
        band = bands["by_class"][row["expected"]]
        probe = float(row["probes_confidence"])
        if row["calibration_cell"] in ("correct_high", "wrong_high"):
            assert probe >= band["high"]          # just inside the archive band
            assert probe < band["high"] + 0.01
        else:
            assert probe < band["low"]            # just inside the review band
            assert probe > band["low"] - 0.01


def test_quartet_expectations_derived_not_hardcoded(suite):
    for row in suite:
        if not row["calibration_cell"]:
            continue
        review, reason = ec.review_expected(row)
        assert row["review_expected"] == str(review).lower()
        assert row["review_reason"] == (reason if reason else row["review_reason"])
        retry, post = ec.retry_expected(row)
        assert row["retry_expected"] == str(retry).lower()
        if retry:
            assert row["expected_post_retry_state"] == post


def test_review_correction_scenario(suite):
    row = next(r for r in suite if r["filename"] == f"{fx.FIXTURE_NS}review-correction-scan")
    assert row["review_expected"] == "true"
    assert row["review_reason"] == "unreadable_source"   # override applied
    assert row["failure_stage"] in ec.FAILURE_STAGES
    assert row["expected_post_correction_state"] in ec.TERMINAL_STAGES


def test_arbiter_scenarios_cover_closed_outcomes(suite):
    arb = {r["arbiter_outcome"]: r for r in suite if r["arbiter_outcome"]}
    assert set(arb) == set(fx.ARBITER_OUTCOMES)
    assert arb["stands"]["expected_post_retry_state"] == "archived"
    assert arb["re_extract"]["expected_post_retry_state"] == "archived"
    assert arb["escalate_human_review"]["expected_post_retry_state"] == "human_review"


def test_failure_stage_matrix_covers_every_stage(suite):
    stages = {r["failure_stage"] for r in suite if r["failure_stage"]}
    assert stages == set(ec.FAILURE_STAGES)


def test_specialist_routing_present(suite):
    for row in suite:
        assert row["expected_specialist"] == ec.SPECIALIST_BY_CLASS[row["expected"]]


def test_no_collision_with_snapshot_filenames(suite):
    snap = {r["filename"] for r in load_fixture_rows()}
    assert snap.isdisjoint({r["filename"] for r in suite})
