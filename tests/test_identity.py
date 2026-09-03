"""§9–§11 identity/provenance/hash derivations: determinism, stability,
uniqueness, and known-answer hash checks on the synthetic fixture."""
from __future__ import annotations

from mailroom_eda.identity import (
    content_sha256,
    document_id,
    enrich_rows,
    normalize_text,
    normalized_text_sha256,
    source_corpus,
    source_document_id,
)


def test_document_id_deterministic(fixture_rows):
    row = fixture_rows[0]
    assert document_id(row) == document_id(dict(row))
    assert document_id(row).startswith("DOC-")
    assert len(document_id(row)) == 4 + 16


def test_document_id_independent_of_row_order(fixture_rows):
    forward = {r["filename"]: document_id(r) for r in enrich_rows(fixture_rows)}
    backward = {r["filename"]: document_id(r) for r in enrich_rows(list(reversed(fixture_rows)))}
    assert forward == backward


def test_document_id_independent_of_split(fixture_rows):
    row = fixture_rows[0]
    flipped = dict(row, split="test" if row["split"] == "train" else "train")
    assert document_id(row) == document_id(flipped)


def test_document_id_unique_on_fixture(fixture_rows):
    ids = [document_id(r) for r in fixture_rows]
    assert len(ids) == len(set(ids))


def test_duplicate_content_distinct_identity(fixture_rows):
    dup_a = next(r for r in fixture_rows if r["filename"] == "cuad_dup_a.txt")
    dup_b = next(r for r in fixture_rows if r["filename"] == "cuad_dup_b.txt")
    # §12: duplicates share content hashes but never a document_id.
    assert document_id(dup_a) != document_id(dup_b)
    assert content_sha256(dup_a["doc_text"]) == content_sha256(dup_b["doc_text"])
    assert normalized_text_sha256(dup_a["doc_text"]) == normalized_text_sha256(dup_b["doc_text"])


def test_content_sha256_known_answer():
    # sha256("hello") — canonical-bytes rule (utf-8, no normalization).
    assert content_sha256("hello") == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_normalized_hash_ignores_whitespace_and_line_endings():
    a = "Hello  world.\r\n\r\nSecond   line.\n"
    b = "Hello world.\nSecond line."
    assert normalized_text_sha256(a) == normalized_text_sha256(b)
    assert content_sha256(a) != content_sha256(b)


def test_normalize_text_nfc():
    assert normalize_text("é") == "é"  # combining -> precomposed


def test_source_provenance_fields(fixture_rows):
    enriched = {r["filename"]: r for r in enrich_rows(fixture_rows)}
    assert enriched["cuad_consulting_001.txt"]["source_corpus"] == "theatticusproject/cuad"
    assert enriched["maud_all_cash_001.txt"]["source_corpus"] == "maud"
    assert enriched["edgar_bylaws_001.htm"]["source_corpus"] == "sec_edgar"
    assert enriched["enron_notice_001.txt"]["source_corpus"] == (
        "Lucius-Morningstar/enron-correspondence-dedup"
    )
    assert enriched["cms_outpatient_001.txt"]["source_corpus"] == "cms_desynpuf"
    # §10: source_document_id prefers the source-native id (CMS record_id).
    assert source_document_id(enriched["cms_outpatient_001.txt"]) == "R-00001"
    assert source_document_id(enriched["cuad_consulting_001.txt"]) == "cuad_consulting_001.txt"
    for row in enriched.values():
        assert row["source_filename"] == row["filename"]
        assert row["source_revision"] == ""  # cast-safe until a builder tracks it
        assert len(row["content_sha256"]) == 64
        assert len(row["normalized_text_sha256"]) == 64


def test_source_corpus_unknown_for_unexpected_class():
    assert source_corpus({"expected": "court_opinion"}) == "unknown"
