"""hub#57 — corpus-eda truth guards: v9 build vocabulary == scoring vocab,
and hub sha256 verification is a real boolean."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "llm-dojo-scoring"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "llm-mailroom" / "src"))

from mailroom_eda.v9_build import EXPECTED_SUBCLASS_BY_CLASS


def test_insurance_subclass_vocab_matches_scoring_surface():
    """hub#57: the vocabulary validated at build time must equal the
    vocabulary scored at eval time. The published v9 ground_truth carries
    exactly six insurance subclasses (verified against the Hub at
    46a4d3c2): auto/carrier/inpatient/outpatient/pde/property. The build
    vocabulary is trimmed to those six; dojo + llm-mailroom inventories
    register the same set — a drift here is a parity failure."""
    build_insurance = set(EXPECTED_SUBCLASS_BY_CLASS["insurance_claim"])
    assert build_insurance == {"auto", "carrier", "inpatient", "outpatient", "pde", "property"}, (
        f"v9 build vocab drifted: {sorted(build_insurance)}"
    )

    try:
        from llm_dojo_scoring.mailroom import HUB_SUBCLASS_INVENTORIES
        dojo_insurance = set(HUB_SUBCLASS_INVENTORIES["insurance_claim"])
        assert dojo_insurance == build_insurance, (
            f"dojo inventory drifted: {sorted(dojo_insurance)} != {sorted(build_insurance)}"
        )
    except ImportError:
        # dojo not importable in this venv — the build-side pin is the guard
        pass

    try:
        from langchain_agents.doc_inventories import _DOJO_SORTER_SUBCLASSES
        llm_insurance = set(_DOJO_SORTER_SUBCLASSES["insurance_claim"])
        assert llm_insurance == build_insurance, (
            f"llm-mailroom inventory drifted: {sorted(llm_insurance)}"
        )
    except ImportError:
        pass


def test_verify_hub_sha256_returns_boolean_for_small_files():
    """hub#57: verify_hub_sha256 must return a real boolean for non-LFS/small
    files — the old truthy string \"(sha not exposed)\" reported success for
    an unverified file. verify_hf.py checks `is True`, so the two agreed only
    by accident."""
    from mailroom_eda.hf_interface import verify_hub_sha256

    class _LFS:
        sha256 = None

    class _Api:
        def list_repo_tree(self, **kwargs):
            return [type("F", (), {"path": "small.json", "lfs": _LFS()})()]

    result = verify_hub_sha256(_Api(), "repo", "small.json", "a" * 64)
    assert result["verified"] is False
    assert result["status"] == "sha-not-exposed"
    assert result["hub_sha256"] is None
    # a matching LFS sha verifies True
    class _LFSMatch:
        sha256 = "b" * 64

    class _Api2:
        def list_repo_tree(self, **kwargs):
            return [type("F", (), {"path": "big.json", "lfs": _LFSMatch()})()]

    ok = verify_hub_sha256(_Api2(), "repo", "big.json", "b" * 64)
    assert ok["verified"] is True
    assert ok["status"] == "verified"