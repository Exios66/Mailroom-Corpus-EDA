"""Tests for the ModernBERT training-set prep (modernbert/prep.py).

Runs against the committed synthetic fixture rows (no snapshot needed) plus
the full-corpus contract tests when data/parquet exists (gitignored — fetch
via ``run_all.py --phases P0``).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "modernbert"))

from prep import (  # noqa: E402
    DOC_TYPES,
    MAX_TOKENS,
    SUBCLASS_BY_CLASS,
    build_documents,
    build_windows,
    class_weights,
    label_maps,
    load_corpus_rows,
    normalize_subclass,
    stratified_split,
    verify_stage,
    window_document,
)

FIXTURE_PATH = ROOT / "tests" / "fixtures" / "sample_rows.jsonl"


def fixture_rows() -> list[dict]:
    with FIXTURE_PATH.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_normalize_subclass_contract_surfaces():
    """Every corpus contract surface resolves to its canonical key (DMR-066)."""
    cases = {
        "Affiliate_Agreements": "affiliate",
        "Agency Agreements": "agency",
        "Co_Branding": "co_branding",
        "Collaboration": "collaboration",
        "Consulting Agreements": "consulting",
        "Development": "development",
        "Distributor": "distributor",
        "Endorsement": "endorsement",
        "Franchise": "franchise",
        "Hosting": "hosting",
        "IP": "ip",
        "Joint Venture": "joint_venture",
        "Joint Venture _ Filing": "joint_venture",
        "License_Agreements": "license",
        "Maintenance": "maintenance",
        "Manufacturing": "manufacturing",
        "Marketing": "marketing",
        "Non_Compete_Non_Solicit": "non_compete_no_solicit",
        "Outsourcing": "outsourcing",
        "Promotion": "promotion",
        "Reseller": "reseller",
        "Service": "service",
        "Sponsorship": "sponsorship",
        "Strategic Alliance": "strategic_alliance",
        "Supply": "supply",
        "Transportation": "transportation",
    }
    for surface, expected in cases.items():
        assert normalize_subclass("contract", surface) == expected, surface


def test_normalize_subclass_other_classes():
    assert normalize_subclass("merger_agreement", "all_cash") == "all_cash"
    assert normalize_subclass("merger_agreement", "All Cash") == "all_cash"
    assert normalize_subclass("merger_agreement", "bogus") == "other"
    assert normalize_subclass("corporate_record", "bylaws") == "bylaws"
    assert normalize_subclass("correspondence", "email") == "email"
    assert normalize_subclass("insurance_claim", "outpatient") == "outpatient"
    assert normalize_subclass("insurance_claim", "") == "other"
    assert normalize_subclass("contract", None) == "other"


def test_stratified_split_deterministic_and_stratified():
    df = pd.DataFrame({
        "filename": [f"f{i:04d}" for i in range(100)],
        "doc_type": ["contract"] * 90 + ["merger_agreement"] * 10,
    })
    a = stratified_split(df)
    b = stratified_split(df)
    assert a.equals(b), "split must be byte-deterministic"
    assert set(a["split"]) == {"train", "validation"}
    for cls, grp in a.groupby("doc_type"):
        n_val = grp["split"].eq("validation").sum()
        assert n_val == round(len(grp) * 0.1), cls
    # no filename in two splits
    assert not a["filename"].duplicated().any()


def test_build_documents_fixtures():
    docs = build_documents(fixture_rows())
    assert len(docs) == len(fixture_rows())
    assert set(docs["doc_type"]) <= set(DOC_TYPES)
    # fixture rows: 1 test (cms_outpatient_001) — held out as test
    assert set(docs["split"]) <= {"train", "validation", "test"}
    assert (docs["split"] == "test").sum() == 1
    # canonical subclass keys only
    for cls, grp in docs.groupby("doc_type"):
        assert set(grp["subclass"]) <= set(SUBCLASS_BY_CLASS[cls]), cls
    # title-wins: subject beats filename (fixture rows carry no subject —
    # inject one to pin the rule)
    rows = fixture_rows()
    rows.append({
        "filename": "enron_subject_001.txt", "expected": "correspondence",
        "expected_subclass": "notice", "doc_text": "body",
        "metadata": {"subject": "Re: Enron", "original_file": ""},
        "gt_fields": {}, "prompt": "", "split": "train",
    })
    docs = build_documents(rows)
    enron = docs[docs["filename"] == "enron_subject_001.txt"].iloc[0]
    assert enron["title"] == "Re: Enron"
    fallback = docs[docs["filename"] == "enron_notice_001.txt"].iloc[0]
    assert fallback["title"] == "enron_notice_001.txt"


def test_label_maps_and_weights():
    docs = build_documents(fixture_rows())
    maps = label_maps(docs)
    assert maps["doc_type"]["labels"] == list(DOC_TYPES) + ["unknown"]
    assert maps["contract"]["labels"][0] == "affiliate"
    w = maps["doc_type"]["weights"]
    # inverse-frequency invariant: sum over classes of weight*count == total
    counts = docs[docs["split"] == "train"]["doc_type"].value_counts().to_dict()
    assert abs(sum(w[k] * counts[k] for k in w) - sum(counts.values())) < 1e-9
    # weights cover every training label
    for cls in DOC_TYPES:
        assert set(maps[cls]["weights"]) <= set(maps[cls]["labels"])


def test_window_document_single_and_multi():
    short = window_document("t", "x" * 100)
    assert len(short) == 1 and short[0].startswith("t")
    long_text = "word " * 200_000  # ~800K chars >> 8,192 tokens
    wins = window_document("t", long_text)
    assert len(wins) > 1
    assert all(w.startswith("t\n\n") for w in wins)
    # overlap: consecutive windows share tail content
    assert wins[0][-50:] in wins[1]


def test_build_windows_fixtures():
    docs = build_documents(fixture_rows())
    wins = build_windows(docs)
    # fixtures are tiny: the 10% val draw can be empty — but test is ALWAYS out
    assert set(wins["split"]) <= {"train", "validation"}
    assert "test" not in set(wins["split"])
    assert (wins["window_index"] < wins["n_windows"]).all()
    assert wins["filename"].nunique() == (docs["split"] != "test").sum()


def test_verify_stage(tmp_path):
    from prep import stage
    stats = stage(tmp_path, rows=fixture_rows(), with_windows=True)
    check = verify_stage(tmp_path)
    assert check["ok"], check["problems"]
    assert check["rows"] == len(fixture_rows())
    assert stats["counts"]["documents"]["test"] == 1


@pytest.mark.skipif(
    not (ROOT / "data" / "parquet" / "ground_truth" / "train").exists(),
    reason="local HF snapshot absent (data/parquet) — fetch via run_all.py P0",
)
def test_full_corpus_contract():
    """Full-corpus invariants: 3,302 rows, splits, no leakage, vocab."""
    rows = load_corpus_rows()
    assert len(rows) == 3302
    docs = build_documents(rows)
    # val = round(10% of each class's corpus-train count), banker's rounding
    tr = docs[docs["corpus_split"] == "train"]
    expected_val = {
        cls: int(round(n * 0.1)) for cls, n in tr.groupby("doc_type").size().items()}
    assert docs["split"].value_counts().to_dict() == {
        "train": len(tr) - sum(expected_val.values()),
        "validation": sum(expected_val.values()), "test": 323}
    assert docs[docs["split"] == "validation"].groupby("doc_type").size().to_dict() \
        == expected_val
    assert not docs["filename"].duplicated().any()
    for cls, grp in docs.groupby("doc_type"):
        assert set(grp["subclass"]) <= set(SUBCLASS_BY_CLASS[cls]), cls
    # every corpus subclass surface resolves to a canonical key (no 'other'
    # inflation beyond the corpus's own other-bucket rows)
    assert (docs["subclass"] == "other").sum() == (
        (docs["doc_type"] == "corporate_record") & (docs["subclass"] == "other")).sum() + \
        ((docs["doc_type"] == "merger_agreement") & (docs["subclass"] == "other")).sum()
    # windows: every train/validation doc has >= 1 window; every published
    # window re-tokenizes within the model budget WITH default specials
    # (the BPE round-trip clamp + title headroom)
    wins = build_windows(docs)
    per_doc = wins.groupby("filename").size()
    assert len(per_doc) == 2649 + 330
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained("answerdotai/ModernBERT-base")
    over = wins["text"].apply(
        lambda t: len(tok(t)["input_ids"]) > MAX_TOKENS)
    assert not over.any(), f"{int(over.sum())} windows exceed {MAX_TOKENS} tokens"
    # title prefix survives the clamp verbatim (QA 5b regression)
    bad_prefix = wins.apply(
        lambda r: not r["text"].startswith(
            docs[docs["filename"] == r["filename"]].iloc[0]["title"]), axis=1)
    assert not bad_prefix.any(), f"{int(bad_prefix.sum())} windows lost the title prefix"