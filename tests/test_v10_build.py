"""Unit tests for mailroom_eda.v10_build (structure, cleanup, layout) and its gates.

Every test runs network-free on synthetic rows shaped like the v9.4 stage."""
from __future__ import annotations

import json

import pytest

from mailroom_eda import v10_build as v10


# ---------------------------------------------------------------------------
# T1: doc_text cleanup and span remap
# ---------------------------------------------------------------------------


def test_clean_text_rules():
    """CRLF and lone CR become LF, C0 controls other than \\n/\\t go, NBSP becomes a space."""
    new, pos = v10.clean_text("a\r\nb\rc\x00d\x1fe\tf\xa0g\n")
    assert new == "a\nb\nc" + "de\tf g\n"
    assert len(pos) == len("a\r\nb\rc\x00d\x1fe\tf\xa0g\n") + 1
    assert pos[-1] == len(new)


def test_clean_text_is_identity_on_clean_text():
    """Text that needs no cleanup is returned unchanged, with the identity offset map."""
    new, pos = v10.clean_text("plain\ttext\n")
    assert new == "plain\ttext\n" and pos == list(range(len(new) + 1))


def test_clean_text_keeps_del_and_other_unicode():
    """Only C0 controls are removed; DEL and non-ASCII letters stay."""
    assert v10.clean_text("x\x7fé")[0] == "x\x7fé"


def test_remap_span_after_deleted_cr():
    """A span after a deleted CR moves left by one and still slices the new text."""
    old = "Title\r\nGoverning Law. This Agreement is governed by Delaware law."
    start = old.index("This")
    new, pos = v10.clean_text(old)
    span = v10.remap_span({"start": start, "text": old[start:start + 20]}, old, new, pos)
    assert span["start"] == start - 1
    assert new[span["start"]:span["start"] + len(span["text"])] == span["text"] == old[start:start + 20]


def test_remap_span_with_inner_crlf_and_end_key():
    """A span that contains CRLF is cleaned in place; an ``end`` key follows the new text."""
    old = "x\r\nParties:\r\nAcme Corp.\r\nend"
    start = old.index("Parties")
    text = old[start:old.index("end")]
    new, pos = v10.clean_text(old)
    span = v10.remap_span({"start": start, "end": start + len(text), "text": text}, old, new, pos)
    assert span["text"] == "Parties:\nAcme Corp.\n"
    assert new[span["start"]:span["end"]] == span["text"]


def test_remap_span_rejects_a_span_that_does_not_match_the_old_text():
    """A span that is not a verbatim slice of the old text is an error, not silently moved."""
    old = "abc\r\ndef"
    new, pos = v10.clean_text(old)
    with pytest.raises(ValueError, match="not a verbatim slice"):
        v10.remap_span({"start": 0, "text": "xyz"}, old, new, pos)


def test_remap_span_that_cleans_to_nothing_is_an_error():
    """A span made only of removed controls would be lost; that is an error."""
    old = "ab\x00\x01cd"
    new, pos = v10.clean_text(old)
    with pytest.raises(ValueError, match="empty"):
        v10.remap_span({"start": 2, "text": "\x00\x01"}, old, new, pos)


def test_remap_cuad_labels_keeps_every_span():
    """Every span of every category survives the remap and re-verifies."""
    old = "Agreement\r\ndated June 1, 1999\r\nbetween Acme\xa0Corp. and Beta LLC"
    labels = {
        "Agreement Date": [{"start": old.index("June"), "text": "June 1, 1999"}],
        "Parties": [{"start": old.index("Acme"), "text": "Acme\xa0Corp."},
                    {"start": old.index("Beta"), "text": "Beta LLC"}],
        "Audit Rights": [],
    }
    new, pos = v10.clean_text(old)
    out = v10.remap_cuad_labels(labels, old, new, pos)
    assert sorted(out) == sorted(labels)
    assert sum(map(len, out.values())) == 3
    assert out["Parties"][0]["text"] == "Acme Corp."
    for spans in out.values():
        for s in spans:
            assert new[s["start"]:s["start"] + len(s["text"])] == s["text"]


def test_cleanup_row_recomputes_hashes_and_keeps_the_source_hash():
    """A cleaned row gets a fresh content_sha256 and keeps the old one as source_content_sha256."""
    old = "Hello\r\nWorld"
    row = {"filename": "a.txt", "doc_text": old, "content_sha256": v10.sha256_text(old),
           "normalized_text_sha256": "n", "gt_fields": {"cuad_clause_labels": "{}"}}
    out, changed = v10.cleanup_row(row)
    assert changed
    assert out["doc_text"] == "Hello\nWorld"
    assert out["content_sha256"] == v10.sha256_text("Hello\nWorld")
    assert out["source_content_sha256"] == v10.sha256_text(old)
    assert row["doc_text"] == old  # the input row is not mutated


def test_cleanup_row_leaves_clean_rows_byte_identical():
    """A row with nothing to clean keeps its hash, and source_content_sha256 equals it."""
    row = {"filename": "b.txt", "doc_text": "clean", "content_sha256": v10.sha256_text("clean"),
           "normalized_text_sha256": "n", "gt_fields": {}}
    out, changed = v10.cleanup_row(row)
    assert not changed
    assert out["doc_text"] == "clean" and out["content_sha256"] == row["content_sha256"]
    assert out["source_content_sha256"] == row["content_sha256"]


def test_cleanup_row_remaps_the_cuad_map_in_gt_fields():
    """The CUAD map inside gt_fields follows the cleaned text."""
    old = "A\r\nB\r\nGoverned by New York law."
    start = old.index("Governed")
    labels = {"Governing Law": [{"start": start, "text": "Governed by New York law."}]}
    row = {"filename": "c.txt", "doc_text": old, "content_sha256": v10.sha256_text(old),
           "normalized_text_sha256": "n", "gt_fields": {"cuad_clause_labels": json.dumps(labels)}}
    out, _ = v10.cleanup_row(row)
    span = json.loads(out["gt_fields"]["cuad_clause_labels"])["Governing Law"][0]
    assert span["start"] == start - 2
    assert out["doc_text"][span["start"]:].startswith(span["text"])
