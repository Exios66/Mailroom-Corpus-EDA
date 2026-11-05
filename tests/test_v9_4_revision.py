"""Unit tests for mailroom_eda.v9_4_revision (dedup + label completion).

Everything except the last test runs network-free on synthetic rows. The
end-to-end test stages from ``data/v9_3/stage`` and skips when that stage
has not been built (``scripts/build/build_v9_3_corporate_presence.py``)."""
from __future__ import annotations

import json

import pytest

from mailroom_eda import v9_4_revision as v94
from mailroom_eda.dataset_export import assign_split
from mailroom_eda.gt_presence import BASE_GT_FIELDS

VOCAB = tuple(sorted(f"Cat{i:02d}" for i in range(37))) + (
    "Agreement Date", "Document Name", "Governing Law", "Parties")
VOCAB = tuple(sorted(VOCAB))


def _presence(**over) -> str:
    """A presence map with every base field ``not_applicable``, overridden by ``over``."""
    m = {k: "not_applicable" for k in BASE_GT_FIELDS}
    m.update(over)
    return json.dumps(m, sort_keys=True)


def _row(filename: str, sha: str = "s1", split: str | None = None, **gt) -> dict:
    """A minimal staged-row dict; ``gt`` keys become gt_fields, except a few that set top-level columns."""
    return {
        "filename": filename,
        "split": split or assign_split(filename),
        "expected": gt.pop("expected", "corporate_record"),
        "expected_subclass": "bylaws",
        "source_corpus": gt.pop("source_corpus", "sec_edgar"),
        "content_sha256": sha,
        "normalized_text_sha256": gt.pop("norm", sha),
        "document_id": f"DOC-{filename}",
        "doc_text": gt.pop("doc_text", ""),
        "metadata": {"source_dataset": "sec_edgar"},
        "related_document_ids": [],
        "relationships": [],
        "group_id": "",
        "matter_id": "",
        "gt_fields": {"gt_presence": _presence(), **gt},
    }


# ---------------------------------------------------------------------------
# dedup
# ---------------------------------------------------------------------------


def test_copy_marker_loses_when_annotations_tie():
    """With equal annotations, the copy without a ``(1)`` marker survives."""
    rows = [_row("A(1).PDF"), _row("A.PDF")]
    (g,) = v94.plan_dedup(rows)
    assert g.survivor == "A.PDF" and g.removed == ["A(1).PDF"]


def test_richer_annotation_wins_over_copy_marker():
    """More ground truth outranks the copy marker, and the differing labels are a conflict."""
    rich = _row("A(1).PDF", cuad_clause_labels=json.dumps({"X": [{"start": 0, "text": "ab"}] * 3}))
    poor = _row("A.PDF", cuad_clause_labels=json.dumps({"X": [{"start": 0, "text": "ab"}]}))
    (g,) = v94.plan_dedup([poor, rich])
    assert g.survivor == "A(1).PDF"
    assert "gt_fields.cuad_clause_labels" in g.conflict_fields and g.label_conflict


def test_cross_split_group_keeps_a_train_copy():
    """A group spanning splits keeps a train member, so no test text is lost to train."""
    rows = [_row("b.htm", split="test"), _row("c.htm", split="train"), _row("a.htm", split="test")]
    (g,) = v94.plan_dedup(rows)
    assert g.survivor == "c.htm"
    assert sorted(g.removed) == ["a.htm", "b.htm"]


def test_keywords_variance_is_not_a_label_conflict():
    """Differing ``keywords`` alone are bookkeeping, not a label conflict."""
    a = _row("x1.htm", keywords='["acc-1"]')
    b = _row("x2.htm", keywords='["acc-2"]')
    (g,) = v94.plan_dedup([a, b])
    assert not g.label_conflict


def test_unique_rows_form_no_group():
    """Rows with distinct content_sha256 never form a dedup group."""
    assert v94.plan_dedup([_row("a", "s1"), _row("b", "s2")]) == []


_OV = {"sD": {"survivor": "m148.txt", "removed": ["m149.txt"], "note": "upstream copy"}}


def test_upstream_defect_override_beats_richness():
    """A pinned upstream-defect survivor wins even when the other copy carries more labels."""
    poor = _row("m148.txt", "sD", maud_label_count="156")
    rich = _row("m149.txt", "sD", maud_label_count="187")
    (g,) = v94.plan_dedup([rich, poor], overrides=_OV)
    assert g.survivor == "m148.txt" and g.removed == ["m149.txt"]
    assert g.survivor_basis == "upstream_defect" and g.survivor_note == "upstream copy"
    assert g.as_dict()["survivor_basis"] == "upstream_defect"
    (plain,) = v94.plan_dedup([rich, poor], overrides={})
    assert plain.survivor == "m149.txt" and plain.survivor_basis == "rule"


def test_override_naming_a_non_member_fails_loudly():
    """An override whose names no longer match the group raises instead of falling back to the rule."""
    rows = [_row("m148.txt", "sD"), _row("other.txt", "sD")]
    with pytest.raises(AssertionError, match="override"):
        v94.plan_dedup(rows, overrides=_OV)


def test_the_maud_override_is_pinned_to_contract_148():
    """The shipped override keeps contract_148, whose MAUD evidence matches the shared text."""
    (ov,) = v94.UPSTREAM_DEFECT_SURVIVORS.values()
    assert ov["survivor"] == "contract_148_merger_agreement.txt"
    assert ov["removed"] == ["contract_149_merger_agreement.txt"]
    assert ov["evidence"] == {"contract_148": [57, 71], "contract_149": [0, 75]}


def test_maud_evidence_alignment_ignores_punctuation_and_skips_short_fragments():
    """Excerpts match through quote/space normalization, split on <omitted>, and dedupe fragments."""
    text = "Section 2.1  “Merger Consideration” means $10.00 in cash per Share, without interest, payable."
    long_hit = '"Merger Consideration" means $10.00 in cash per share, without interest'
    miss = "Each share shall be converted into 0.5 shares of Parent Common Stock at closing"
    excerpts = [f"{long_hit} <omitted> short", long_hit, miss]
    assert v94.maud_evidence_alignment(text, excerpts) == (1, 2)
    assert v94.maud_evidence_alignment(text, []) == (0, 0)


def test_dangling_references_found_in_rows_and_sidecars():
    """Removed identifiers are found in kept rows' link fields and in sidecar text."""
    kept = [_row("keep.htm", "s1")]
    kept[0]["related_document_ids"] = ["DOC-gone.htm"]
    removed = [_row("gone.htm", "s1")]
    hits = v94.dangling_references(kept, removed, {"bundles.jsonl": '{"anchor": "gone.htm"}'})
    assert any("related_document_ids" in h for h in hits)
    assert any("bundles.jsonl" in h for h in hits)
    assert v94.dangling_references([_row("keep.htm")], removed, {"b": "{}"}) == []


def test_near_duplicates_report_only_differing_bytes():
    """Near-duplicates group on normalized-text sha only and flag cross-split groups."""
    rows = [_row("a", "s1", norm="n1"), _row("b", "s2", norm="n1"), _row("c", "s3", norm="n3")]
    (g,) = v94.near_duplicate_groups(rows)
    assert [m["filename"] for m in g["members"]] == ["a", "b"]
    assert g["cross_split"] is (len({m["split"] for m in g["members"]}) > 1)


# ---------------------------------------------------------------------------
# placeholders
# ---------------------------------------------------------------------------


def test_scrub_placeholders_keeps_presence_and_na_marker():
    """nan/None placeholders become empty while a verbatim source 'N/A' stays."""
    row = _row("ins.txt", expected="insurance_claim", sentiment_label="nan", content_topic="None",
               date_of_loss="N/A")
    row["gt_fields"]["gt_presence"] = json.dumps(
        v94.row_gt_presence(v94.presence_probe(row)), sort_keys=True)
    changed = v94.scrub_placeholders(row)
    assert sorted(changed) == ["content_topic", "sentiment_label"]
    assert row["gt_fields"]["sentiment_label"] == ""
    assert row["gt_fields"]["date_of_loss"] == "N/A"  # verbatim source marker stays


# ---------------------------------------------------------------------------
# CUAD verification
# ---------------------------------------------------------------------------

DOC = ("CONSULTING AGREEMENT\n\nThis Agreement is made on March 3, 2015 between ACME Inc. and Bob. "
       "This Agreement shall be governed by the laws of the State of Texas.")


def _span(text: str) -> dict:
    """A CUAD span for the first occurrence of ``text`` in DOC."""
    return {"start": DOC.index(text), "text": text}


def _labels(**cats) -> dict:
    """A full-vocabulary CUAD label map, empty except the given categories."""
    out = {n: [] for n in VOCAB}
    out.update(cats)
    return out


GOOD = _labels(**{
    "Document Name": [_span("CONSULTING AGREEMENT")],
    "Agreement Date": [_span("March 3, 2015")],
    "Parties": [_span("ACME Inc.")],
    "Governing Law": [_span("the laws of the State of Texas")],
})


def test_verified_labels_pass_unchanged():
    """Verbatim, in-bounds, on-topic spans pass verification untouched."""
    clean, dropped, problems = v94.verify_cuad_labels(DOC, GOOD, VOCAB)
    assert clean == GOOD and dropped == [] and problems == []


def test_offset_mismatch_is_dropped_not_repaired():
    """A span whose text is not at its offset is dropped, never re-anchored."""
    bad = json.loads(json.dumps(GOOD))
    bad["Parties"] = [{"start": 0, "text": "ACME Inc."}]
    clean, dropped, problems = v94.verify_cuad_labels(DOC, bad, VOCAB)
    assert clean["Parties"] == [] and any("not verbatim" in d for d in dropped)
    assert any("floor" in p for p in problems)  # 3 categories left


def test_hallucinated_span_is_dropped():
    """A span whose text does not occur in the document is dropped."""
    bad = json.loads(json.dumps(GOOD))
    bad["Parties"].append({"start": 5, "text": "Globex Corporation"})
    clean, dropped, _ = v94.verify_cuad_labels(DOC, bad, VOCAB)
    assert len(clean["Parties"]) == 1 and dropped


def test_semantic_probe_drops_wrong_category():
    """A verbatim span that fails its category's semantic probe is dropped."""
    bad = json.loads(json.dumps(GOOD))
    bad["Governing Law"] = [_span("ACME Inc. and Bob")]
    clean, dropped, _ = v94.verify_cuad_labels(DOC, bad, VOCAB)
    assert clean["Governing Law"] == [] and any("semantic probe" in d for d in dropped)


def test_offset_outside_document_is_dropped():
    # DOC[-10:-7] is a real slice, so without the bounds check a negative
    # offset would pass as "verbatim"
    """Negative or past-the-end offsets are dropped even if the slice would match."""
    for start, text in ((-10, DOC[-10:-7]), (len(DOC) + 1, "x y")):
        bad = json.loads(json.dumps(GOOD))
        bad["Parties"].append({"start": start, "text": text})
        clean, dropped, _ = v94.verify_cuad_labels(DOC, bad, VOCAB)
        assert len(clean["Parties"]) == 1 and any("outside the document" in d for d in dropped)


def test_heading_only_span_is_dropped():
    """A span that is only the clause heading carries no clause and is dropped."""
    doc = DOC + " Governing Law. "
    bad = json.loads(json.dumps(GOOD))
    bad["Governing Law"].append({"start": len(DOC) + 1, "text": "Governing Law."})
    clean, dropped, _ = v94.verify_cuad_labels(doc, bad, VOCAB)
    assert len(clean["Governing Law"]) == 1 and any("heading only" in d for d in dropped)


@pytest.mark.parametrize("name,wrong,right", [
    ("Audit Rights", "Buyer will pay all applicable sales taxes.",
     "Licensor may audit the books and records of Licensee."),
    ("Insurance", "Employer will reimburse reasonable business expenses.",
     "Supplier shall maintain general liability insurance."),
    ("Covenant Not To Sue", "Provisions that by their nature survive will survive.",
     "Licensee shall not challenge or contest the validity of the Patents."),
    ("Affiliate License-Licensee", "Licensee will assist Licensor to protect the Licensed Mark.",
     "The license extends to Licensee's Affiliates."),
    ("Affiliate License-Licensor", "Employer may assign this Agreement without consent.",
     "Licensor and its Affiliates grant Licensee a license."),
    ("Source Code Escrow", "Seller will transfer the Website registration to Buyer.",
     "Licensor shall deposit the source code with the escrow agent."),
])
def test_new_semantic_probes(name, wrong, right):
    """Each added semantic probe rejects a look-alike sentence and accepts a real clause."""
    probe = v94.SEMANTIC_PROBES[name]
    assert not probe.search(wrong) and probe.search(right)


def test_unknown_category_is_a_problem():
    """A label map with categories outside the vocabulary is reported as a problem."""
    bad = dict(GOOD, **{"Made Up": []})
    _, _, problems = v94.verify_cuad_labels(DOC, bad, VOCAB)
    assert any("unknown categories" in p for p in problems)


def _ex10_row(fn: str) -> dict:
    """An EX-10 contract row whose CUAD labels are pending annotation."""
    row = _row(fn, expected="contract", doc_text=DOC, cuad_clause_labels="{}",
               label_evidence="EX-10 exhibit", clause_count="")
    row["gt_fields"]["gt_presence"] = _presence(cuad_clause_labels="pending_annotation")
    return row


def test_ingest_fills_pending_and_records_provenance():
    """An accepted journal record fills the row, updates presence and records the model."""
    rows = [_ex10_row("e1.htm")]
    journal = [{"filename": "e1.htm", "accepted": True, "model": v94.LABEL_MODEL, "cuad_clause_labels": GOOD}]
    rep = v94.ingest_cuad_journal(rows, journal, VOCAB)
    assert rep["counts"] == {"ingested": 1}
    gt = rows[0]["gt_fields"]
    assert json.loads(gt["cuad_clause_labels"]) == GOOD
    assert gt["clause_count"] == str(len(VOCAB))
    assert gt["label_evidence"].startswith("CUAD-annotated clauses: Agreement Date")
    assert json.loads(gt["gt_presence"])["cuad_clause_labels"] == "populated"
    assert rows[0]["annotation_method"] == "llm_zero_shot" and rows[0]["annotation_model"] == v94.LABEL_MODEL


def test_ingest_uses_removed_duplicate_record_and_skips_rejects():
    """A survivor may take a removed duplicate's record; labeler rejects leave rows pending."""
    rows = [_ex10_row("keep.htm"), _ex10_row("other.htm")]
    journal = [
        {"filename": "keep.htm", "accepted": False, "model": v94.LABEL_MODEL, "problems": ["floor"]},
        {"filename": "gone.htm", "accepted": True, "model": v94.LABEL_MODEL, "cuad_clause_labels": GOOD},
        {"filename": "other.htm", "accepted": False, "model": v94.LABEL_MODEL, "problems": ["floor"]},
    ]
    rep = v94.ingest_cuad_journal(rows, journal, VOCAB, sha_aliases={"keep.htm": ["gone.htm"]})
    by = {v["filename"]: v for v in rep["verdicts"]}
    assert by["keep.htm"]["status"] == "ingested" and by["keep.htm"]["via_duplicate"]
    assert by["other.htm"]["status"] == "rejected_by_labeler"
    assert json.loads(rows[1]["gt_fields"]["gt_presence"])["cuad_clause_labels"] == "pending_annotation"


def test_ingest_rejects_journal_record_for_another_document():
    """A journal record whose document_id names another document is not ingested."""
    rows = [_ex10_row("e1.htm")]
    journal = [{"filename": "e1.htm", "document_id": "DOC-other", "accepted": True, "model": v94.LABEL_MODEL,
                "cuad_clause_labels": GOOD}]
    rep = v94.ingest_cuad_journal(rows, journal, VOCAB, document_ids={"e1.htm": "DOC-e1.htm"})
    (v,) = rep["verdicts"]
    assert v["status"] == "failed_verification" and "document_id" in v["reasons"][0]
    assert json.loads(rows[0]["gt_fields"]["gt_presence"])["cuad_clause_labels"] == "pending_annotation"
    journal[0]["document_id"] = "DOC-e1.htm"
    rep = v94.ingest_cuad_journal(rows, journal, VOCAB, document_ids={"e1.htm": "DOC-e1.htm"})
    assert rep["counts"] == {"ingested": 1}


def test_journal_display_path_hides_machine_layout(tmp_path):
    """Recorded journal paths start at the repo name, not the local machine layout."""
    p = tmp_path / "x" / "local-mailroom-sandbox" / "reports" / "gt-labeler" / "j.jsonl"
    assert v94.journal_display_path(p) == "local-mailroom-sandbox/reports/gt-labeler/j.jsonl"
    # outside a reports/ tree the caller's path is kept, not reduced to a bare name
    other = tmp_path / "elsewhere" / "j.jsonl"
    assert v94.journal_display_path(other) == str(other)


def test_stage_refuses_to_overwrite_its_base(tmp_path):
    """A stage dir equal to, above or inside the base stage is refused."""
    base = tmp_path / "v9_3" / "stage"
    base.mkdir(parents=True)
    for stage in (base, base.parent, base / "inner"):
        with pytest.raises(SystemExit, match="overlaps the base"):
            v94.refuse_overlapping_stage(base, stage)
    assert base.exists()
    v94.refuse_overlapping_stage(base, tmp_path / "v9_4" / "stage")


# ---------------------------------------------------------------------------
# gate (b): allowed drift
# ---------------------------------------------------------------------------


def test_gate_d_catches_stale_pending_over_populated_labels():
    """Gate (d) recomputes presence, so a stale pending code over real labels fails."""
    row = _ex10_row("e1.htm")
    v94.ingest_cuad_journal([row], [{"filename": "e1.htm", "accepted": True, "model": v94.LABEL_MODEL,
                                     "cuad_clause_labels": GOOD}], VOCAB)
    assert v94.presence_gate_problems([row]) == []
    stale = json.loads(row["gt_fields"]["gt_presence"])
    stale["cuad_clause_labels"] = "pending_annotation"  # would make gate (e) skip the row
    row["gt_fields"]["gt_presence"] = json.dumps(stale, sort_keys=True)
    assert any("differs from a recompute" in p for p in v94.presence_gate_problems([row]))


def test_drift_allows_documented_fills_only_where_they_apply():
    """A CUAD fill is allowed drift on EX-10 rows only, never on golden CUAD rows."""
    base = _ex10_row("e1.htm")
    new = json.loads(json.dumps(base))
    v94.ingest_cuad_journal([new], [{"filename": "e1.htm", "accepted": True, "model": v94.LABEL_MODEL,
                                     "cuad_clause_labels": GOOD}], VOCAB)
    assert v94.drift_problems(base, new) == []

    golden = _row("g.txt", expected="contract", source_corpus=v94.GOLDEN_CUAD_SOURCE,
                  cuad_clause_labels="{}")
    edited = json.loads(json.dumps(golden))
    edited["gt_fields"]["cuad_clause_labels"] = json.dumps(GOOD)
    assert any("unexpected gt_fields change" in p for p in v94.drift_problems(golden, edited))


def test_drift_scrub_must_be_placeholder_to_empty():
    """A scrub may only turn a placeholder into empty; blanking a real value is drift."""
    base = _row("i.txt", expected="insurance_claim", sentiment_label="nan", insurer="Acme")
    ok = json.loads(json.dumps(base))
    ok["gt_fields"]["sentiment_label"] = ""
    assert v94.drift_problems(base, ok) == []
    bad = json.loads(json.dumps(base))
    bad["gt_fields"]["insurer"] = ""
    assert v94.drift_problems(base, bad)


def test_drift_rejects_annotation_change_without_fill_and_text_change():
    """Provenance changes need a label fill, and top-level text never changes."""
    base = _row("c.htm")
    prov = json.loads(json.dumps(base))
    prov["annotation_model"] = "m"
    assert any("without a label fill" in p for p in v94.drift_problems(base, prov))
    text = json.loads(json.dumps(base))
    text["doc_text"] = "changed"
    assert any("top-level" in p for p in v94.drift_problems(base, text))


def test_drift_intent_fill_only_on_flagged_correspondence():
    """Intent may change on flagged_review correspondence only, never on manual labels."""
    flagged = _row("m.", expected="correspondence", intent="notice", intent_status="flagged_review")
    new = json.loads(json.dumps(flagged))
    new["gt_fields"].update(intent="request", intent_status="auto_labeled")
    new["annotation_model"] = "m"
    assert v94.drift_problems(flagged, new) == []
    manual = _row("n.", expected="correspondence", intent="notice", intent_status="manual")
    edited = json.loads(json.dumps(manual))
    edited["gt_fields"]["intent"] = "request"
    assert v94.drift_problems(manual, edited)


def test_gates_survive_python_optimize():
    """Gates raise via ``_gate``, not ``assert``, so ``python -O`` cannot disable them."""
    with pytest.raises(AssertionError, match="boom"):
        v94._gate(False, "boom")


def test_fold_journal_keeps_accepted_record():
    """When a document has several journal records, the accepted one wins."""
    recs = [{"filename": "a", "accepted": True, "span_count": 1},
            {"filename": "a", "accepted": False, "span_count": 9}]
    assert v94.fold_journal(recs)["a"]["accepted"] is True


# ---------------------------------------------------------------------------
# intents + calibration
# ---------------------------------------------------------------------------

IV = ("request", "notice", "other")


def test_intent_decision_rules():
    """Intent changes need two agreeing, confident passes inside the vocabulary."""
    a = {"intent": "request", "confidence": 0.6}
    assert v94.intent_decision("request", a, a, IV)[0]  # confirms original
    assert not v94.intent_decision("notice", a, a, IV)[0]  # changed, not confident
    hi = {"intent": "request", "confidence": 0.9}
    assert v94.intent_decision("notice", hi, hi, IV)[0]
    assert not v94.intent_decision("notice", hi, {"intent": "other", "confidence": 0.9}, IV)[0]
    assert not v94.intent_decision("x", {"intent": "bogus", "confidence": 1}, hi, IV)[0]


def test_ingest_intent_keeps_flag_on_rejection():
    """Agreeing passes relabel the row; disagreeing passes leave it flagged."""
    r1 = _row("m1.", expected="correspondence", intent="notice", intent_status="flagged_review")
    r2 = _row("m2.", expected="correspondence", intent="notice", intent_status="flagged_review")
    j = [{"filename": "m1.", "model": v94.LABEL_MODEL, "pass_a": {"intent": "request", "confidence": 0.95},
          "pass_b": {"intent": "request", "confidence": 0.85}},
         {"filename": "m2.", "model": v94.LABEL_MODEL, "pass_a": {"intent": "request", "confidence": 0.95},
          "pass_b": {"intent": "other", "confidence": 0.85}}]
    rep = v94.ingest_intent_journal([r1, r2], j, IV)
    assert rep["counts"] == {"accepted": 1, "kept_flagged": 1}
    assert r1["gt_fields"]["intent"] == "request" and r1["gt_fields"]["intent_status"] == "auto_labeled"
    assert r1["gt_fields"]["intent_confidence"] == "0.85"
    assert r2["gt_fields"]["intent_status"] == "flagged_review"


def test_f1_and_kappa():
    """F1 and Cohen's kappa edge cases: no hits, perfect agreement, chance agreement."""
    assert v94.f1(0, 3, 3) == 0.0
    assert v94.f1(2, 0, 0) == 1.0
    assert v94.cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    assert v94.cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == 0.0


def test_cuad_calibration_scores_presence_and_overlap():
    """CUAD calibration scores micro presence precision/recall and span overlap."""
    gold = _row("g.txt", expected="contract", source_corpus=v94.GOLDEN_CUAD_SOURCE, doc_text=DOC,
                cuad_clause_labels=json.dumps(GOOD))
    pred = json.loads(json.dumps(GOOD))
    pred["Governing Law"] = []
    res = v94.score_cuad_calibration([gold], [{"filename": "g.txt", "accepted": True,
                                               "cuad_clause_labels": pred}], VOCAB)
    assert res["documents"] == 1 and res["micro_precision"] == 1.0
    assert res["micro_recall"] == 0.75 and res["span_overlap_rate"] == 1.0


M = "Qwen/Qwen3-32B-FP8"
FULL_CUAD = {"documents": 40, "expected_documents": 40, "models": [M],
             "micro_f1": 0.8, "micro_precision": 0.85}


def test_calibration_status_gates_publish():
    """Publishing needs every required calibration to pass."""
    assert v94.calibration_status(None, None, need_intent=False)["publish_ready"] is False
    ok = v94.calibration_status(FULL_CUAD, None, need_intent=False, cuad_label_models={M})
    assert ok["publish_ready"] is True and ok["cuad"]["status"] == "pass"
    bad = v94.calibration_status(FULL_CUAD, {"documents": 96, "expected_documents": 96, "models": [M],
                                             "cohen_kappa": 0.2}, need_intent=True)
    assert bad["publish_ready"] is False and bad["intent"]["status"] == "fail"


def test_calibration_floors_reject_a_small_model_score():
    # the sandbox's 8B/14B-class CUAD presence F1 (0.57-0.64) cleared the old 0.6 bar
    """The F1 and precision floors reject small-model scores and high-recall noise."""
    weak = dict(FULL_CUAD, micro_f1=0.64, micro_precision=0.9)
    assert v94.calibration_status(weak, None, need_intent=False)["cuad"]["status"] == "fail"
    # high recall cannot buy a pass when precision is low: wrong labels are worse than none
    noisy = dict(FULL_CUAD, micro_f1=0.8, micro_precision=0.7)
    assert v94.calibration_status(noisy, None, need_intent=False)["cuad"]["status"] == "fail"


def test_calibration_needs_the_whole_sample():
    """A partial or unsized calibration sample is incomplete, never a pass."""
    part = dict(FULL_CUAD, documents=5)
    st = v94.calibration_status(part, None, need_intent=False)
    assert st["cuad"]["status"] == "incomplete" and st["publish_ready"] is False
    # a result that does not say what it was scored against is never complete
    st = v94.calibration_status({"documents": 5, "micro_f1": 0.9}, None, need_intent=False)
    assert st["cuad"]["status"] == "incomplete"


def test_calibration_must_score_the_labeling_model():
    """Calibration scored on a different model than the labels is model_mismatch."""
    other = dict(FULL_CUAD, models=["Qwen/Qwen3-14B-AWQ"])
    st = v94.calibration_status(other, None, need_intent=False, cuad_label_models={M})
    assert st["cuad"]["status"] == "model_mismatch" and st["publish_ready"] is False


def test_label_model_is_the_sandbox_quality_posture():
    """The only approved labeler is Qwen3-32B-FP8; the retired 14B-AWQ is not."""
    assert v94.LABEL_MODEL == M and v94.APPROVED_LABEL_MODELS == {M}
    assert "Qwen/Qwen3-14B-AWQ" not in v94.APPROVED_LABEL_MODELS


def test_ingest_ignores_records_from_unapproved_models():
    """CUAD records from an unapproved model are counted and ignored."""
    row = _ex10_row("e1.htm")
    journal = [{"filename": "e1.htm", "accepted": True, "model": "Qwen/Qwen3-14B-AWQ",
                "document_id": "DOC-e1.htm", "cuad_clause_labels": GOOD}]
    rep = v94.ingest_cuad_journal([row], journal, VOCAB)
    assert rep["unapproved_records"] == 1 and rep["counts"] == {"no_journal_record": 1}
    assert v94.is_pending_cuad(row)


def test_retired_model_record_cannot_outrank_an_approved_one():
    """An approved model's reject beats a retired model's accept for the same row."""
    row = _ex10_row("e1.htm")
    journal = [{"filename": "e1.htm", "accepted": True, "model": "Qwen/Qwen3-14B-AWQ",
                "cuad_clause_labels": GOOD},
               {"filename": "e1.htm", "accepted": False, "model": M, "problems": ["floor"]}]
    rep = v94.ingest_cuad_journal([row], journal, VOCAB)
    assert rep["counts"] == {"rejected_by_labeler": 1} and v94.is_pending_cuad(row)


def test_intent_journal_from_unapproved_model_is_ignored():
    """Intent records from an unapproved model leave the row flagged."""
    r = _row("m1.", expected="correspondence", intent="request", intent_status="flagged_review")
    j = [{"filename": "m1.", "model": "Qwen/Qwen3-14B-AWQ",
          "pass_a": {"intent": "request", "confidence": 0.95},
          "pass_b": {"intent": "request", "confidence": 0.95}}]
    rep = v94.ingest_intent_journal([r], j, ("request", "notice", "other"))
    assert rep["counts"] == {"no_journal_record": 1} and r["gt_fields"]["intent_status"] == "flagged_review"


def test_cuad_calibration_counts_skipped_and_rejected_documents():
    """Labeler rejects count as scored documents with zero recall; missing ones stay unscored."""
    gold = [_row(f"g{i}.txt", expected="contract", source_corpus=v94.GOLDEN_CUAD_SOURCE, doc_text=DOC,
                 cuad_clause_labels=json.dumps(GOOD)) for i in range(2)]
    res = v94.score_cuad_calibration(gold, [{"filename": "g0.txt", "accepted": False, "model": "m",
                                             "cuad_clause_labels": GOOD}], VOCAB)
    assert res["documents"] == 1 and res["expected_documents"] == 2
    assert res["micro_recall"] == 0.0 and res["models"] == ["m"]


# ---------------------------------------------------------------------------
# end-to-end over the local v9.3 stage
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not (v94.BASE_STAGE_DIR / "ground_truth_hardened.jsonl").exists(),
                    reason="v9.3 stage not built (scripts/build/build_v9_3_corporate_presence.py)")
def test_end_to_end_stage_passes_gates(tmp_path):
    """A fresh stage from v9.3 passes gates (a)-(g), and tampering makes the gates fail."""
    stage = tmp_path / "stage"
    report = v94.build_all(stage, use_cuad_journal=False, write_audit=False)
    assert report["dedup"]["stats"]["rows"] == v94.EXPECTED_BASE_ROWS - v94.EXPECTED_REMOVED_ROWS
    assert report["placeholders"]["cells"] > 0
    result = v94.verify_stage(stage, v94.BASE_STAGE_DIR)
    assert all(v.startswith("PASS") for k, v in result["gates"].items() if k != "h_calibration")

    # MAUD contract_148/149: the evidence-backed copy survives, and gate (a) pins it
    names = {r["filename"] for r in v94.load_jsonl(stage / "ground_truth_hardened.jsonl")}
    assert "contract_148_merger_agreement.txt" in names
    assert "contract_149_merger_agreement.txt" not in names
    dpath = stage / "dedup_removed.jsonl"
    dedup_original = dpath.read_text(encoding="utf-8")
    groups = [json.loads(ln) for ln in dedup_original.splitlines()]
    for g in groups:
        if g["content_sha256"] in v94.UPSTREAM_DEFECT_SURVIVORS:
            assert g["survivor_basis"] == "upstream_defect"
            g["survivor"], g["removed"] = g["removed"][0], [g["survivor"]]
    dpath.write_text("".join(json.dumps(g) + "\n" for g in groups), encoding="utf-8")
    with pytest.raises(AssertionError, match="upstream-defect override"):
        v94.verify_stage(stage, v94.BASE_STAGE_DIR)
    dpath.write_text(dedup_original, encoding="utf-8")

    # the report is bound to the staged bytes
    rpath = stage.parent / "v9_4_report.json"
    rep = json.loads(rpath.read_text())
    rpath.write_text(json.dumps(dict(rep, stage_digest="0" * 64)))
    with pytest.raises(AssertionError, match="not written for these staged bytes"):
        v94.verify_stage(stage, v94.BASE_STAGE_DIR)
    rpath.write_text(json.dumps(rep))

    # a stale presence map on an untouched row fails the stage (drift gate b, then the gate d recompute)
    gt_path = stage / "ground_truth_hardened.jsonl"
    original = gt_path.read_text(encoding="utf-8")
    lines = original.splitlines()
    for i, ln in enumerate(lines):
        r = json.loads(ln)
        pres = json.loads(r["gt_fields"]["gt_presence"])
        if pres.get("cuad_clause_labels") == "populated" and r["source_corpus"] == v94.GOLDEN_CUAD_SOURCE:
            pres["cuad_clause_labels"] = "pending_annotation"
            r["gt_fields"]["gt_presence"] = json.dumps(pres, sort_keys=True)
            lines[i] = json.dumps(r, ensure_ascii=False)
            break
    gt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        v94.verify_stage(stage, v94.BASE_STAGE_DIR)
    gt_path.write_text(original, encoding="utf-8")


@pytest.mark.skipif(not (v94.BASE_STAGE_DIR / "ground_truth_hardened.jsonl").exists(),
                    reason="v9.3 stage not built (scripts/build/build_v9_3_corporate_presence.py)")
def test_approved_journal_is_held_until_calibration_passes(tmp_path):
    """An approved-model journal is not ingested until CUAD calibration passes."""
    base = v94.load_jsonl(v94.BASE_STAGE_DIR / "ground_truth_hardened.jsonl")
    pending = next(r for r in base if r["expected"] == "contract" and v94.is_pending_cuad(r))
    journal = tmp_path / "j.jsonl"
    journal.write_text(json.dumps({"filename": pending["filename"], "document_id": pending["document_id"],
                                   "accepted": True, "model": v94.LABEL_MODEL,
                                   "cuad_clause_labels": {}}) + "\n")
    report = v94.build_all(tmp_path / "stage", cuad_journal=str(journal), write_audit=False)
    assert report["cuad"]["held"].startswith("CUAD calibration not_run")
    assert report["cuad"]["counts"] == {}
    assert report["remaining"]["pending_cuad"] == 89
