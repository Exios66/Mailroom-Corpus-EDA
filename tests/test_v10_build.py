"""Unit tests for mailroom_eda.v10_build (structure, cleanup, layout) and its gates.

Every test runs network-free on synthetic rows shaped like the v9.4 stage."""
from __future__ import annotations

import json

import pytest

from mailroom_eda import v10_build as v10
from mailroom_eda.dataset_export import assign_split
from mailroom_eda.gt_presence import BASE_GT_FIELDS

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


# ---------------------------------------------------------------------------
# T2: row derivation and typed records
# ---------------------------------------------------------------------------


MAUD_CASH = {"Type of Consideration": {"answer": "All Cash", "category": "General Information"},
             "Absence of Litigation Closing Condition": {"answer": "Pending",
                                                         "category": "Conditions to Closing"}}
SPECIALIST = {"contract": "contracts_specialist", "merger_agreement": "contracts_specialist",
              "correspondence": "correspondence_specialist",
              "insurance_claim": "insurance_claims_specialist",
              "corporate_record": "corporate_records_specialist"}


def make_row(filename: str, cls: str, subclass: str, doc_text: str = "Some text.", *,
             presence: dict | None = None, **gt) -> dict:
    """A v9.4-shaped staged row; ``gt`` keys land in gt_fields (JSON-encoded like the stage)."""
    pres = {k: "not_applicable" for k in BASE_GT_FIELDS}
    pres.update(presence or {})
    fields = {k: "" for k in BASE_GT_FIELDS}
    fields.update({"cuad_clause_labels": "{}", "maud_clause_labels": "{}", "denial_reasons": "[]",
                   "supporting_documents": "[]", "token_estimate": "10", "context_window_band": "<=4k"})
    for k, v in gt.items():
        fields[k] = v if isinstance(v, str) else json.dumps(v)
    fields["gt_presence"] = json.dumps(pres, sort_keys=True)
    return {
        "filename": filename, "split": assign_split(filename), "expected": cls,
        "expected_subclass": subclass, "expected_specialist": SPECIALIST[cls],
        "document_id": f"DOC-{filename}", "doc_text": doc_text,
        "content_sha256": v10.sha256_text(doc_text), "normalized_text_sha256": "n",
        "source_corpus": "src", "source_document_id": filename, "source_filename": filename,
        "source_revision": "", "prompt": "", "annotation_method": "source_native",
        "annotation_source": "src", "annotation_model": "", "annotation_prompt_version": "",
        "annotation_confidence": "1.0", "annotation_reviewer": "", "annotation_timestamp": "",
        "expected_stage": "archived", "review_expected": "false", "review_reason": "",
        "retry_expected": "false", "expected_post_retry_state": "", "matter_id": "",
        "matter_construction": "", "group_id": "", "group_role": "", "thread_position": "",
        "thread_size": "", "thread_evidence": "", "relationships": [], "related_document_ids": [],
        "metadata": {"source_dataset": "src", "built_by": "a script", "sample_method": "draw",
                     "source_provenance": "local", "maud_categories": "{}", "applicable_categories": "[]",
                     "subject": "hi"},
        "gt_fields": fields,
    }


def contract_row(**kw):
    text = "LICENSE AGREEMENT\r\nThis Agreement is governed by the laws of Delaware."
    labels = {"Document Name": [{"start": 0, "text": "LICENSE AGREEMENT"}],
              "Governing Law": [{"start": text.index("This"), "text": text[text.index("This"):]}],
              "Audit Rights": []}
    return make_row("c1.htm", "contract", "License_Agreements", text,
                    presence={"cuad_clause_labels": "populated"}, cuad_clause_labels=labels, **kw)


def merger_row(filename="m1.htm", subclass="all_cash", maud=MAUD_CASH):
    return make_row(filename, "merger_agreement", subclass, "AGREEMENT AND PLAN OF MERGER",
                    presence={"maud_clause_labels": "populated"}, maud_clause_labels=maud)


def insurance_row():
    return make_row(
        "i1.txt", "insurance_claim", "outpatient", "Claim 123",
        presence={k: "populated" for k in ("claim_number", "claimed_amount", "denial_reasons", "intent")}
        | {"date_of_loss": "schema_documented_absence", "adjuster": "schema_documented_absence"},
        claim_number="123", claimed_amount="30.0", date_of_loss="N/A", date_filed="2009-07-20",
        denial_reasons=["late filing"], intent="claim_data_record")


def test_derive_row_fixes_the_merger_specialist_and_sets_subclasses():
    r = v10.derive_row(merger_row())
    assert r["expected_specialist"] == "merger_agreement_specialist"
    assert r["subclass"] == "all_cash" and r["source_subclass"] == "all_cash"
    assert r["expected_subclass"] == "all_cash"
    assert r["derived"]["merger_consideration"] == "all_cash"


def test_derive_row_keeps_other_mergers_pending():
    """A merger without the expert answer keeps subclass ``other`` and gets no consideration label."""
    r = v10.derive_row(merger_row("m2.htm", "other", {"Absence of Litigation Closing Condition": {
        "answer": "Pending", "category": "Conditions to Closing"}}))
    assert r["subclass"] == "other" and r["derived"]["merger_consideration"] is None


def test_derive_row_refuses_a_subclass_that_contradicts_the_expert_answer():
    with pytest.raises(ValueError, match="disagrees"):
        v10.derive_row(merger_row("m3.htm", "all_stock"))


def test_derive_row_contract_family_and_correspondence_type():
    c = v10.derive_row(contract_row())
    assert c["subclass"] == "license" and c["source_subclass"] == "License_Agreements"
    assert c["derived"]["cuad_family"] == "license"
    assert c["doc_text"].count("\r") == 0  # cleanup ran
    e = v10.derive_row(make_row("e1.txt", "correspondence", "email"))
    assert e["derived"]["communication_type"] == "email" and e["subclass"] == "email"


def test_typed_values():
    assert v10.typed("30.0", "money") == 30.0
    assert v10.typed("$1,250,000", "money") == 1250000.0
    assert v10.typed('["a", "b"]', "entity_list:name") == ["a", "b"]
    assert v10.typed("[]", "entity_list") is None
    assert v10.typed("N/A", "date") is None
    assert v10.typed("2009-07-20", "date") == "2009-07-20"
    assert v10.typed("", "name") is None
    with pytest.raises(ValueError):
        v10.typed("not money", "money")


def test_insurance_record_is_typed_with_presence_and_provenance():
    rec = v10.class_record(v10.derive_row(insurance_row()))
    assert rec["claimed_amount"] == 30.0 and rec["claim_number"] == "123"
    assert rec["denial_reasons"] == ["late filing"]
    assert rec["date_of_loss"] is None
    assert rec["presence"]["date_of_loss"] == "schema_documented_absence"
    assert rec["presence"]["claim_checklist"] == "pending_annotation"
    assert rec["provenance"]["claim_number"] == "gold:source_native"
    assert rec["provenance"]["claim_checklist"] == ""
    assert set(rec["evidence"]) == set(rec["presence"])


def test_contract_record_has_spans_family_and_pending_llm_fields():
    rec = v10.class_record(v10.derive_row(contract_row()))
    assert rec["cuad_family"] == "license" and rec["presence"]["cuad_family"] == "populated"
    assert rec["provenance"]["cuad_family"] == "deterministic"
    names = [(s["category"], s["text"]) for s in rec["cuad_clauses"]]
    assert ("Document Name", "LICENSE AGREEMENT") in names
    doc = v10.derive_row(contract_row())["doc_text"]
    assert all(doc[s["start"]:s["end"]] == s["text"] for s in rec["cuad_clauses"])
    assert rec["parties"] is None and rec["presence"]["parties"] == "pending_annotation"
    assert "merger_consideration" not in rec and "maud_clauses" not in rec


def test_corporate_record_marks_other_subclass_fields_not_applicable():
    row = make_row("k1.htm", "corporate_record", "bylaws",
                   presence={"entity_name": "populated", "intent": "populated"},
                   entity_name="Acme Corp.", intent="governance_rules")
    rec = v10.class_record(v10.derive_row(row))
    assert rec["entity_name"] == "Acme Corp."
    assert rec["presence"]["certifying_officer"] == "pending_annotation"
    assert rec["presence"]["registered_agent"] == "not_applicable"
    assert rec["presence"]["filing_number"] == "not_applicable"  # not core-applicable for bylaws


def test_merger_record_answers_and_consideration():
    rec = v10.class_record(v10.derive_row(merger_row()))
    assert rec["merger_consideration"] == "all_cash"
    assert {a["question"] for a in rec["maud_clauses"]} == set(MAUD_CASH)
    rec2 = v10.class_record(v10.derive_row(merger_row("m2.htm", "other", {
        "Absence of Litigation Closing Condition": {"answer": "Pending", "category": "Conditions to Closing"}})))
    assert rec2["merger_consideration"] is None
    assert rec2["presence"]["merger_consideration"] == "pending_annotation"


def test_every_class_record_fits_its_arrow_schema():
    import pyarrow as pa
    rows = [contract_row(), merger_row(), insurance_row(),
            make_row("e1.txt", "correspondence", "email", presence={"intent": "populated"}, intent="other"),
            make_row("k1.htm", "corporate_record", "bylaws")]
    for r in rows:
        d = v10.derive_row(r)
        rec = v10.class_record(d)
        table = pa.Table.from_pylist([rec], schema=v10.class_schema(d["expected"]))
        assert table.num_rows == 1
        assert table.column_names == list(v10.class_schema(d["expected"]).names)


def test_default_record_is_blind_and_carries_no_metadata():
    """Metadata gives away class, subclass and some gold values, so the blind config has none."""
    rec = v10.default_record(v10.derive_row(contract_row()))
    assert list(rec) == ["filename", "document_id", "doc_text", "prompt"]


def test_source_metadata_record_drops_build_internal_and_label_derived_keys():
    rec = v10.source_metadata_record(v10.derive_row(contract_row()))
    assert list(rec) == ["document_id", "filename", "metadata"]
    for k in ("built_by", "sample_method", "source_provenance", "maud_categories", "applicable_categories"):
        assert k not in rec["metadata"]
    assert rec["metadata"]["subject"] == "hi"


def test_routing_record():
    rec = v10.routing_record(v10.derive_row(merger_row()))
    assert rec == {"document_id": "DOC-m1.htm", "filename": "m1.htm", "doc_class": "merger_agreement",
                   "subclass": "all_cash", "source_subclass": "all_cash",
                   "expected_specialist": "merger_agreement_specialist", "intent": None,
                   "label_status": "pending"}


def test_long_tables():
    d = v10.derive_row(contract_row())
    spans = v10.cuad_long_records(d)
    assert len(spans) == 2 and {s["category"] for s in spans} == {"Document Name", "Governing Law"}
    assert all(d["doc_text"][s["start"]:s["end"]] == s["text"] for s in spans)
    answers = v10.maud_long_records(v10.derive_row(merger_row()))
    assert {a["question"]: a["answer"] for a in answers}["Type of Consideration"] == "All Cash"


def test_legacy_row_adds_new_labels_and_keeps_the_rest():
    base = merger_row()
    leg = v10.legacy_row(v10.derive_row(base))
    assert leg["expected_specialist"] == "merger_agreement_specialist"
    assert leg["gt_fields"]["merger_consideration"] == "all_cash"
    assert json.loads(leg["gt_fields"]["gt_presence"])["merger_consideration"] == "populated"
    for k, v in base["gt_fields"].items():
        if k != "gt_presence":
            assert leg["gt_fields"][k] == v
    assert set(leg) == set(base)  # same top-level columns as v9.4
    c = v10.legacy_row(v10.derive_row(contract_row()))
    assert c["gt_fields"]["cuad_family"] == "license"
    e = v10.legacy_row(v10.derive_row(make_row("e1.txt", "correspondence", "email")))
    assert e["gt_fields"]["communication_type"] == "email"
