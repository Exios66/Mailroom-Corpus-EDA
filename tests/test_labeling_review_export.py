"""Offline coverage for review_export: every labeling batch becomes a portal packet.

Chunk directories come from the real ``runner.run_chunk`` and ``runner.review`` with a
fake chat, so the files the exporter reads are the files production writes.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest
from test_labeling import _fake_chat, _row, rows  # noqa: F401  (rows is a fixture)
from test_labeling_review_packet import NOTICE, fixture as checklist_fixture, write_fields

from mailroom_eda.labeling import export, providers, registry, review_export, review_packet, runner, spend, tasks
from mailroom_eda.labeling import review_page, review_portal as portal
from mailroom_eda.labeling.registry import dojo_snapshot

TIME = "2026-11-08T12:00:00+00:00"
GLM, DEEPSEEK, MINIMAX = "z-ai/glm-5.3-flashx-legal", "deepseek/deepseek-v4.1-flash", "minimax/minimax-m3.1-flash"
BACKEND = providers.Backend("openrouter", "u", "k")
#: keys every exporter's records carry, so the page and portal never branch on the producer
CONTRACT = {"document_id", "field", "filename", "doc_class", "subclass", "split", "full_document_text",
            "document_sha256", "candidate_labels", "candidate_status", "evidence", "existing_gt_fields",
            "issues", "task", "chunk", "models", "reasoning", "prompt_sha256", "correction_type",
            "dojo_type", "candidate_presence", "absence_policy", "nullable", "eligible_for_gold",
            "schema_status", "review_provenance", "model_outputs"}
CHECKLIST_TEXT = "Date of Report: 2026-02-25\nPart B deductible: $500.00\n"
CHECKLIST = ["Timely Notice: Date of Report: 2026-02-25", "Deductible: Part B deductible: $500.00"]


def _start(out: Path, task: str, models: list[str], reasoning: str, set_name: str, chunk_id: str) -> None:
    """The start record ``runner.execute`` journals before it calls run_chunk."""
    export.append_journal(out / "runs.jsonl", {
        "chunk": chunk_id, "task": task, "set": set_name, "slice": None, "models": models,
        "provider": "openrouter", "reasoning": reasoning, "initials": "AB", "documents": 1,
        "started_utc": "2026-11-08T00:00:00Z"})


def _batch(tmp_path, picked, *, task, models, chat, reasoning="off", set_name="pilot", chunk_id="1",
           vocab=(), review=True):
    """Run a chunk with the fake chat and, by default, review it as ``labeling review`` does."""
    out = tmp_path / "labels" / f"{chunk_id}_{task}-{set_name}"
    _start(out, task, models, reasoning, set_name, chunk_id)
    runner.run_chunk(picked, task=task, models=models, backend=BACKEND, reasoning=reasoning,
                     budget=spend.Budget(1.0, 3600), out_dir=out, vocab=vocab, chat=chat, set_name=set_name)
    if review:
        runner.review(picked, task, out, reasoning=reasoning)
    return out


def _packet(out: Path) -> dict:
    return json.loads((out / f"{out.name}-review-data.json").read_text(encoding="utf-8"))


def _correspondence_chat(models=(DEEPSEEK, MINIMAX)):
    """Two models that agree on the amount, disagree on urgency and both find no sender."""
    row = _row("correspondence", "attorney_demand", "x")
    base = {f: {"value": None, "status": "not_found", "evidence": ""} for f in tasks.build("correspondence", row).fields}
    a = {"fields": {**base, "demand_amount": {"value": "$4,500", "status": "present",
                                              "evidence": "outstanding $4,500 by Friday"},
                    "urgency": {"value": "time-sensitive", "status": "present", "evidence": "by Friday"}}}
    b = {"fields": {**base, "demand_amount": {"value": "4500", "status": "present", "evidence": "$4,500"},
                    "urgency": {"value": "urgent", "status": "present", "evidence": "by Friday"}}}
    return _fake_chat(dict(zip(models, (a, b))))


def _checklist_chat(answers):
    """Answers per model: a list of checklist items, or None for the same items as the first."""
    return _fake_chat({m: {"fields": {"claim_checklist": {"value": v, "status": "present",
                                                          "evidence": NOTICE}}} for m, v in answers.items()})


# ---------------------------------------------------------------- portal harness

@pytest.fixture
def app_for(tmp_path, monkeypatch):
    """A ReviewPortal over tmp_path/labels whose campaign and stage are synthetic."""
    def make(picked, entries):
        folder = tmp_path / "campaigns" / "test"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "campaign.yaml").write_text(json.dumps({"name": "test", "chunks": entries, "steps": []}))
        monkeypatch.setattr(registry, "CAMPAIGNS_DIR", tmp_path / "campaigns")
        stage = tmp_path / "stage"
        stage.mkdir(exist_ok=True)
        (stage / "ground_truth_hardened.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in picked), encoding="utf-8")
        return portal.ReviewPortal(tmp_path / "labels", tmp_path / "labels" / "portal", campaign="test",
                                   stage=stage)
    return make


def _decide(data, doc, field, code, corrected=None):
    """A return file with one decision, as the page would export it."""
    target = next(r for r in data["records"] if (r["document_id"], r["field"]) == (doc, field))
    decision = {"document_id": doc, "field": field, "document_sha256": target["document_sha256"],
                "decision": code, "corrected_labels_json": json.dumps(corrected) if code == "CORRECT" else "",
                "reviewer_initials": "AB", "reviewer_notes": "Synthetic test decision", "updated_at": TIME}
    return {"schema": review_page.RETURN_SCHEMA, "packet_id": data["packet_id"],
            "state_revision": data.get("state_revision", ""), "exported_at": TIME, "decisions": [decision],
            "history": [{"document_id": doc, "field": field, "at": TIME, "before": {},
                         "after": copy.deepcopy(decision)}]}


def _status(data, doc, field):
    return next(s["status"] for s in data["review_states"] if (s["document_id"], s["field"]) == (doc, field))


def _evidence_is_located(record):
    """Every evidence span is a verbatim, exclusive-end slice of the full text."""
    text = record["full_document_text"]
    assert record["evidence"], record["field"]
    for e in record["evidence"]:
        assert 0 <= e["start"] < e["end"] <= len(text) and text[e["start"]:e["end"]] == e["quote"]


# ---------------------------------------------------------------- field tasks

def test_field_task_packet_compares_both_models_and_saves_an_accept(tmp_path, rows, app_for):
    picked = [rows[2]]
    out = _batch(tmp_path, picked, task="correspondence", models=[DEEPSEEK, MINIMAX],
                 chat=_correspondence_chat(), chunk_id="3")
    packet = _packet(out)
    targets = review_page.validate_packet(packet)
    fields = {f["field"]: f for f in export.read_csv(out / "fields.csv")}
    assert {k[1] for k in targets} == set(fields)
    doc = export.read_csv(out / "documents.csv")[0]
    amount, urgency, sender = (targets["e1", n] for n in ("demand_amount", "urgency", "sender"))
    assert CONTRACT <= set(amount)
    assert (amount["task"], amount["chunk"], amount["models"], amount["reasoning"]) == (
        "correspondence", "3", [DEEPSEEK, MINIMAX], "off")
    assert amount["prompt_sha256"] == doc["prompt_sha256"] and amount["batch"] == out.name
    assert amount["candidate_labels"] == 4500.0 and amount["correction_type"] == "number"
    assert amount["dojo_type"] == "money" and amount["candidate_status"] == "source_checks_passed"
    assert amount["candidate_presence"] == "populated" and amount["eligible_for_gold"] is True
    assert amount["schema_status"] == "pinned_registry"
    assert amount["nullable"] is (amount["absence_policy"] != "never_absent")
    assert "Batch 3 (correspondence, pilot set)" in amount["review_provenance"]
    assert [(o["model"], o["status"]) for o in amount["model_outputs"]] == [
        (DEEPSEEK, "present"), (MINIMAX, "present")]
    assert amount["model_outputs"][0]["value"] == 4500 or amount["model_outputs"][0]["value"] == "$4,500"
    _evidence_is_located(amount)
    assert urgency["candidate_status"] == "needs_source_review" and urgency["candidate_labels"] is None
    assert any("disagree" in i and DEEPSEEK in i and MINIMAX in i for i in urgency["issues"])
    assert {o["value"] for o in urgency["model_outputs"]} == {"time-sensitive", "urgent"}
    assert sender["candidate_presence"] == "schema_documented_absence" and sender["candidate_labels"] is None
    assert sender["candidate_status"] == "source_checks_passed"

    app = app_for(picked, [{"id": "3", "task": "correspondence", "set": "pilot"}])
    data = app.data()
    assert data["tracker"]["tasks"][0]["loaded_target_fields"] == len(fields)  # the tracker sees the task
    saved = app.save(_decide(data, "e1", "demand_amount", "ACCEPT"))
    assert saved["changed_fields"] == 1
    assert _status(saved["data"], "e1", "demand_amount") == "human_confirmed"
    refused = saved["data"]
    with pytest.raises(ValueError, match="blocked candidate"):
        review_page.validate_return(refused, _decide(refused, "e1", "urgency", "ACCEPT"))
    corrected = app.save(_decide(refused, "e1", "urgency", "CORRECT", "urgent"))
    assert _status(corrected["data"], "e1", "urgency") == "human_confirmed"


def test_unlocatable_evidence_becomes_an_issue_not_an_offset(tmp_path, rows):
    picked = [rows[2]]
    out = _batch(tmp_path, picked, task="correspondence", models=[DEEPSEEK, MINIMAX],
                 chat=_correspondence_chat(), review=True)
    rewritten = export.read_csv(out / "fields.csv")
    for f in rewritten:
        if f["field"] == "urgency":
            f["model_a_evidence"] = "text that is nowhere in the document"
    export.write_csv(out / "fields.csv", export.FIELD_COLUMNS, rewritten)
    record = next(r for r in review_export.build_packet(out, picked)["records"] if r["field"] == "urgency")
    assert any("could not be located" in i and DEEPSEEK in i for i in record["issues"])
    assert all(e["quote"] != "text that is nowhere in the document" for e in record["evidence"])
    assert record["model_outputs"][0]["evidence"] == "text that is nowhere in the document"
    assert record["model_outputs"][0]["evidence_spans"] == []


def test_a_candidate_the_portal_would_reject_is_marked_before_it_reaches_the_reviewer(tmp_path, rows):
    """A reviewer-supplied value that is not in the source cannot be accepted, and says why."""
    picked = [rows[2]]
    out = _batch(tmp_path, picked, task="correspondence", models=[DEEPSEEK, MINIMAX], chat=_correspondence_chat())
    rewritten = export.read_csv(out / "fields.csv")
    for f in rewritten:
        if f["field"] == "urgency":
            f.update(reviewer_verdict="value", reviewer_value="catastrophic")
    export.write_csv(out / "fields.csv", export.FIELD_COLUMNS, rewritten)
    # review would regenerate the CSV from calls.jsonl and re-apply the verdict from it
    runner.review(picked, "correspondence", out)
    record = next(r for r in _packet(out)["records"] if r["field"] == "urgency")
    assert record["candidate_labels"] == "catastrophic" and record["candidate_status"] == "needs_source_review"
    assert any("cannot be accepted as is" in i for i in record["issues"])
    assert any("earlier review recorded the verdict 'value'" in i for i in record["issues"])


def test_export_is_idempotent_and_a_changed_review_rewrites_atomically(tmp_path, rows):
    picked = [rows[2]]
    out = _batch(tmp_path, picked, task="correspondence", models=[DEEPSEEK, MINIMAX], chat=_correspondence_chat())
    path = out / f"{out.name}-review-data.json"
    first, stamp = path.read_bytes(), path.stat().st_mtime_ns
    assert review_export.export_chunk(out, rows=picked) == path
    assert path.read_bytes() == first and path.stat().st_mtime_ns == stamp  # identical bytes: untouched
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    fields = export.read_csv(out / "fields.csv")
    for f in fields:
        if f["field"] == "urgency":
            f["reviewer_verdict"] = "a"
    export.write_csv(out / "fields.csv", export.FIELD_COLUMNS, fields)
    runner.review(picked, "correspondence", out)
    after = json.loads(path.read_bytes())
    urgency = next(r for r in after["records"] if r["field"] == "urgency")
    assert path.read_bytes() != first and urgency["candidate_labels"] == "time-sensitive"
    assert after["packet_id"] != json.loads(first)["packet_id"]
    assert not list(out.glob("*.tmp"))  # no partial file is left for the portal's glob


def test_review_writes_the_packet_and_reports_a_refusal_without_undoing_the_review(tmp_path, rows, monkeypatch):
    picked = [rows[2]]
    out = _batch(tmp_path, picked, task="correspondence", models=[DEEPSEEK, MINIMAX],
                 chat=_correspondence_chat(), review=False)
    result = runner.review(picked, "correspondence", out)
    assert result["review_packet"] == str(out / f"{out.name}-review-data.json")
    assert (out / "fields.csv").exists()
    monkeypatch.setattr(review_export, "export_chunk", lambda *a, **k: (_ for _ in ()).throw(ValueError("nope")))
    refused = runner.review(picked, "correspondence", out)
    assert refused["review_packet_error"] == "ValueError: nope" and "review_packet" not in refused
    assert refused["documents"] == 1


def test_a_directory_without_a_start_record_gets_no_packet(tmp_path, rows):
    out = tmp_path / "ad-hoc"
    runner.run_chunk([rows[2]], task="correspondence", models=[DEEPSEEK, MINIMAX], backend=BACKEND, reasoning="off",
                     budget=spend.Budget(1.0, 3600), out_dir=out, chat=_correspondence_chat())
    assert "review_packet" not in runner.review([rows[2]], "correspondence", out)
    assert not list(out.glob("*-review-data.json"))
    with pytest.raises(ValueError, match="no start record"):
        review_export.export_chunk(out, rows=[rows[2]])


def test_stage_without_the_document_is_refused_and_nothing_is_written(tmp_path, rows):
    out = _batch(tmp_path, [rows[2]], task="correspondence", models=[DEEPSEEK, MINIMAX],
                 chat=_correspondence_chat())
    (out / f"{out.name}-review-data.json").unlink()
    with pytest.raises(ValueError, match="not in the stage rows"):
        review_export.export_chunk(out, rows=[rows[0]])
    assert not list(out.glob("*-review-data.json"))


# ---------------------------------------------------------------- claim_checklist

def _claim_rows():
    return [_row("insurance_claim", "property", CHECKLIST_TEXT, fn="agree.txt", document_id="i-agree"),
            _row("insurance_claim", "property", CHECKLIST_TEXT + "Second claim.\n", fn="differ.txt",
                document_id="i-differ")]


def test_claim_checklist_packet_with_two_models_is_not_tied_to_glm(tmp_path, app_for):
    picked = _claim_rows()
    answers = {DEEPSEEK: CHECKLIST, MINIMAX: CHECKLIST}
    base = _checklist_chat(answers)

    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        # the second document's reader B stops after the first item
        if model == MINIMAX and "Second claim." in messages[1]["content"]:
            return _checklist_chat({MINIMAX: CHECKLIST[:1]})(backend, model, messages, schema,
                                                             reasoning=reasoning, max_tokens=max_tokens)
        return base(backend, model, messages, schema, reasoning=reasoning, max_tokens=max_tokens)

    out = _batch(tmp_path, picked, task="claim_checklist", models=[DEEPSEEK, MINIMAX], chat=chat,
                 reasoning="low", chunk_id="8a")
    packet = _packet(out)
    targets = review_page.validate_packet(packet)
    agree, differ = targets["i-agree", "claim_checklist"], targets["i-differ", "claim_checklist"]
    assert CONTRACT <= set(agree) and agree["task"] == "claim_checklist" and agree["reasoning"] == "low"
    assert agree["candidate_labels"] == CHECKLIST and agree["candidate_status"] == "source_checks_passed"
    assert agree["correction_type"] == "array" and agree["dojo_type"] == "entity_list:free_text"
    assert [o["model"] for o in agree["model_outputs"]] == [DEEPSEEK, MINIMAX]
    assert {e["category"] for e in agree["evidence"] if "category" in e} == {"Timely Notice", "Deductible"}
    _evidence_is_located(agree)
    assert differ["candidate_status"] == "needs_source_review" and differ["candidate_labels"] is None
    assert [len(o["value"]) for o in differ["model_outputs"]] == [2, 1]
    assert any("disagree" in i for i in differ["issues"])

    app = app_for(picked, [{"id": "8a", "task": "claim_checklist", "set": "pilot"}])
    data = app.data()
    task = data["tracker"]["tasks"][0]
    assert (task["loaded_target_fields"], task["documents_total"], task["reviewed"]) == (2, 2, 0)
    saved = app.save(_decide(data, "i-agree", "claim_checklist", "ACCEPT"))
    assert _status(saved["data"], "i-agree", "claim_checklist") == "human_confirmed"
    task = saved["data"]["tracker"]["tasks"][0]
    assert (task["reviewed"], task["remaining"]) == (1, 1)


def test_legacy_checklist_bundle_takes_its_model_from_the_run_journal(tmp_path):
    """review_packet no longer needs GLM to be one of the two models."""
    picked = _claim_rows()[:1]
    out = _batch(tmp_path, picked, task="claim_checklist", models=[DEEPSEEK, MINIMAX],
                 chat=_checklist_chat({DEEPSEEK: CHECKLIST, MINIMAX: CHECKLIST}), reasoning="low", chunk_id="8a")
    result = review_packet.export_packet(picked, out, tmp_path / "bundle")
    data = json.loads((tmp_path / "bundle" / "8a-review-data.json").read_text())
    record = data["records"][0]
    assert result["accepted"] == 1 and record["model"] == DEEPSEEK and record["reasoning"] == "low"
    assert record["models"] == [DEEPSEEK] and record["candidate_attribution"] == f"unchanged raw {DEEPSEEK} candidate"
    # an explicit model overrides the journal, at its own recorded level
    review_packet.export_packet(picked, out, tmp_path / "other", model=MINIMAX)
    assert json.loads((tmp_path / "other" / "8a-review-data.json").read_text())["records"][0]["model"] == MINIMAX
    with pytest.raises(ValueError, match="another/model did not run in this chunk"):
        review_packet.export_packet(picked, out, tmp_path / "none", model="another/model")


def test_legacy_checklist_override_runs_at_the_overridden_models_own_level(tmp_path):
    """A mixed-level run: the packet for an explicit model uses that model's level, not the default's."""
    picked = _claim_rows()[:1]
    out = _batch(tmp_path, picked, task="claim_checklist", models=[DEEPSEEK, MINIMAX],
                 chat=_checklist_chat({DEEPSEEK: CHECKLIST, MINIMAX: CHECKLIST}),
                 reasoning={DEEPSEEK: "low", MINIMAX: "off"}, chunk_id="8a")
    review_packet.export_packet(picked, out, tmp_path / "by-model", model=MINIMAX)
    record = json.loads((tmp_path / "by-model" / "8a-review-data.json").read_text())["records"][0]
    assert record["model"] == MINIMAX and record["reasoning"] == "off"


def test_intent_pair_missing_one_model_is_not_unanimous(tmp_path, rows):
    """A configured model with no record for a document leaves that document without a candidate."""
    base = {"intent": "payment_demand", "confidence": 0.9, "evidence": "please pay the outstanding $4,500"}
    out = _intent_batch(tmp_path, rows, _fake_chat({DEEPSEEK: base, MINIMAX: base}),
                        models=(DEEPSEEK, MINIMAX))
    path = out / "intent-labels.jsonl"
    kept = [r for r in export.read_journal(path) if r["model"] == DEEPSEEK]
    path.write_text("\n".join(json.dumps(r) for r in kept) + "\n")
    (record,) = review_page.validate_packet(review_export.build_packet(out, [rows[2]])).values()
    assert record["candidate_labels"] is None and record["candidate_status"] == "needs_source_review"
    assert any("incomplete" in i and MINIMAX in i for i in record["issues"])


def test_legacy_checklist_bundle_records_follow_the_shared_contract(tmp_path):
    chunk = tmp_path / "8a_claim_checklist-pilot"
    row, field, _ = checklist_fixture(chunk)
    write_fields(chunk, [field])
    review_packet.export_packet([row], chunk, tmp_path / "out")
    record = json.loads((tmp_path / "out" / "8a-review-data.json").read_text())["records"][0]
    assert CONTRACT <= set(record)
    assert record["task"] == "claim_checklist" and record["chunk"] == "8a" and record["correction_type"] == "array"
    assert record["issues"] == [] and all(isinstance(i, str) for i in record["issues"])
    assert record["model_outputs"][0]["model"] == review_packet.MODEL
    review_page.validate_packet(json.loads((tmp_path / "out" / "8a-review-data.json").read_text()))


# ---------------------------------------------------------------- cuad

def test_cuad_packet_lists_located_clauses_and_stays_schema_pending(tmp_path, rows, app_for):
    row = {**rows[0], "id": "c1"}
    vocab = [c["name"] for c in dojo_snapshot()["cuad_categories"]]
    answer = {"clauses": [{"category": "Document Name", "text": "CONSULTING AGREEMENT"},
                          {"category": "Parties", "text": "Acme Corp. (\"Acme\")"},
                          {"category": "Parties", "text": "Beta LLC (\"Consultant\")"},
                          {"category": "Agreement Date", "text": "dated as of March 5, 2004"},
                          {"category": "Governing Law",
                           "text": "This Agreement shall be governed by the laws of the State of Delaware."}]}
    out = _batch(tmp_path, [row], task="cuad", models=[GLM], chat=_fake_chat({GLM: answer}), set_name="calibration",
                 chunk_id="1.3", vocab=vocab)
    (record,) = review_page.validate_packet(_packet(out)).values()
    assert CONTRACT <= set(record) and record["field"] == "cuad_clauses" and record["task"] == "cuad"
    assert record["candidate_labels"] == [
        "Document Name: CONSULTING AGREEMENT", "Agreement Date: dated as of March 5, 2004",
        "Parties: Acme Corp. (\"Acme\")", "Parties: Beta LLC (\"Consultant\")",
        "Governing Law: This Agreement shall be governed by the laws of the State of Delaware."]
    assert record["eligible_for_gold"] is False and record["schema_status"] == "schema_pending"
    assert record["schema_alignment"] == "proposal_pending" and record["correction_type"] == "array"
    assert record["absence_policy"] == "never_absent" and record["nullable"] is False
    assert {e["category"] for e in record["evidence"]} == {
        "Document Name", "Parties", "Agreement Date", "Governing Law"}
    _evidence_is_located(record)
    assert [o["model"] for o in record["model_outputs"]] == [GLM] and len(record["model_outputs"][0]["value"]) == 5
    assert "Batch 1.3 (cuad, calibration set)" in record["review_provenance"]
    assert record["candidate_status"] == "source_checks_passed" and record["issues"] == []
    app = app_for([row], [{"id": "1.3", "task": "cuad", "set": "calibration"}])
    saved = app.save(_decide(app.data(), "c1", "cuad_clauses", "ACCEPT"))
    # a clause proposal is reviewed but needs the schema decision before it can count as confirmed
    assert _status(saved["data"], "c1", "cuad_clauses") == "reviewed_needs_schema"


def test_cuad_answer_with_rejected_spans_is_flagged(tmp_path, rows):
    row = {**rows[0], "id": "c1"}
    vocab = [c["name"] for c in dojo_snapshot()["cuad_categories"]]
    answer = {"clauses": [{"category": "Parties", "text": "Acme Corp. (\"Acme\")"},
                          {"category": "Governing Law", "text": "governed by the laws of Ohio"}]}
    out = _batch(tmp_path, [row], task="cuad", models=[GLM], chat=_fake_chat({GLM: answer}),
                 set_name="calibration", vocab=vocab)
    (record,) = _packet(out)["records"]
    assert record["candidate_status"] == "needs_source_review"
    assert any("present categories (floor 4)" in i for i in record["issues"])
    assert any("Governing Law clause was dropped (not verbatim)" in i and "Ohio" in i for i in record["issues"])
    assert record["candidate_labels"] == ["Parties: Acme Corp. (\"Acme\")"]


# ---------------------------------------------------------------- intent

def _intent_batch(tmp_path, rows, chat, models=(DEEPSEEK,)):
    return _batch(tmp_path, [rows[2]], task="intent", models=list(models), chat=chat, reasoning="low",
                  set_name="relabel", chunk_id="3.1")


def test_intent_packet_agrees_across_passes_and_saves(tmp_path, rows, app_for):
    answer = {"intent": "payment_demand", "confidence": 0.9, "evidence": "please pay the outstanding $4,500"}
    out = _intent_batch(tmp_path, rows, _fake_chat({DEEPSEEK: answer}))
    (record,) = review_page.validate_packet(_packet(out)).values()
    assert CONTRACT <= set(record) and record["field"] == "intent" and record["task"] == "intent"
    assert record["candidate_labels"] == "payment_demand" and record["candidate_status"] == "source_checks_passed"
    assert record["correction_type"] == "string" and record["dojo_type"] == "label"
    assert record["reasoning"] == "low" and record["absence_policy"] == "never_absent"
    assert [(o["model"], o["pass"], o["value"]) for o in record["model_outputs"]] == [
        (DEEPSEEK, "a", "payment_demand"), (DEEPSEEK, "b", "payment_demand")]
    assert record["model_outputs"][0]["confidence"] == 0.9
    _evidence_is_located(record)
    app = app_for([rows[2]], [{"id": "3.1", "task": "intent", "set": "relabel"}])
    data = app.data()
    assert data["tracker"]["tasks"][0]["reviewed"] == 0
    saved = app.save(_decide(data, "e1", "intent", "ACCEPT"))
    assert _status(saved["data"], "e1", "intent") == "human_confirmed"
    task = saved["data"]["tracker"]["tasks"][0]
    assert (task["reviewed"], task["remaining"], task["loaded_target_fields"]) == (1, 0, 1)
    assert saved["data"]["revision_queue"] == []  # not a production-dataset record


def test_intent_passes_that_disagree_have_no_candidate(tmp_path, rows):
    base = {"confidence": 0.8, "evidence": "please pay the outstanding $4,500"}

    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        label = "payment_demand" if "Subject: Q3 invoice" in messages[1]["content"] else "request"
        return providers.Completion(json.dumps({**base, "intent": label}), {**base, "intent": label}, model, "P",
                                    100, 50, 0, 0.0001, "stop", True)

    out = _intent_batch(tmp_path, rows, chat)
    (record,) = _packet(out)["records"]
    assert record["candidate_labels"] is None and record["candidate_status"] == "needs_source_review"
    assert record["candidate_presence"] == "pending_annotation"
    assert any("disagree" in i and "pass a: payment_demand" in i and "pass b: request" in i for i in record["issues"])
    with pytest.raises(ValueError, match="blocked candidate"):
        review_page.validate_return(_packet(out), _decide(_packet(out), "e1", "intent", "ACCEPT"))


# ---------------------------------------------------------------- portal fixes and CLI

def test_portal_reads_the_revision_of_nested_and_flat_packets(tmp_path, app_for):
    text = "Example LLC\n"
    base = {"document_id": "D1", "field": "entity_name", "filename": "s.txt", "doc_class": "corporate_record",
            "subclass": "bylaws", "split": "train", "candidate_labels": "Example LLC",
            "candidate_status": "source_checks_passed", "evidence": [], "full_document_text": text,
            "document_sha256": __import__("hashlib").sha256(text.encode()).hexdigest()}
    from test_labeling_review_portal import write_packet

    app = app_for([], [{"id": "9", "task": "corporate", "set": "pilot"}])
    write_packet(tmp_path / "labels" / "a" / "a-review-data.json", [base], source={"revision": "nested-rev"})
    assert app.data()["tracker"]["source_revisions"] == ["nested-rev"]
    write_packet(tmp_path / "labels" / "b" / "b-review-data.json", [{**base, "field": "other"}],
                 source_revision="flat-rev")
    assert app.data()["tracker"]["source_revisions"] == ["flat-rev", "nested-rev"]


def test_cli_and_subcommand_export_a_chunk(tmp_path, rows, capsys):
    picked = [rows[2]]
    out = _batch(tmp_path, picked, task="correspondence", models=[DEEPSEEK, MINIMAX],
                 chat=_correspondence_chat(), review=False)
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "ground_truth_hardened.jsonl").write_text(json.dumps(picked[0]) + "\n")
    runner.review(picked, "correspondence", out)
    packet = out / f"{out.name}-review-data.json"
    packet.unlink()
    assert review_export.main(["--chunk-dir", str(out), "--stage", str(stage)]) == 0
    assert capsys.readouterr().out.strip() == str(packet) and packet.exists()
    packet.unlink()
    assert runner.main(["export-review", str(out), "--stage", str(stage)]) == 0
    assert capsys.readouterr().out.strip() == str(packet) and packet.exists()
    review_page.validate_packet(json.loads(packet.read_text()))
    with pytest.raises(SystemExit) as refused:
        review_export.main(["--chunk-dir", str(tmp_path / "nowhere"), "--stage", str(stage)])
    assert refused.value.code == 1 and "review export refused" in capsys.readouterr().err
    assert not os.path.exists(tmp_path / "nowhere")
