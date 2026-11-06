"""Jev intent QA: request shape, spend bounds, answer reuse, calibration gate (offline)."""

import json

import pytest

from mailroom_eda.labeling import jev, spend

CFG = {"model": "typesafe/jev-1.14", "price_per_m_input_usd": 0.042, "total_cap_usd": 0.30,
       "kappa_floor": 0.75, "flag_below": 0.30}
PROMPT, SHA = jev.load_prompt()


def _row(i, intent="request", text="Please send the signed copy by Friday."):
    return {"filename": f"x/{i}.", "document_id": f"DOC-{i}", "split": "train", "doc_text": text,
            "gt_fields": {"intent": intent}}


def _answer(choice):
    probs = {k: 0.0 for k in PROMPT["criteria"]}
    probs[choice] = 1.0
    return {"answers": {"intent": {"type": "choice", "choice": choice, "probabilities": probs,
                                   "confidence": 1.0}},
            "usage": {"cost": 0.00003}, "model": "typesafe/jev-1.14-20261014", "id": "gen-1"}


def test_body_sends_full_text_and_never_the_label():
    row = _row(1, intent="payment_demand", text="A" * 50_000)
    body = jev.build_body(row, PROMPT, CFG["model"])
    assert body["state"]["email"] == row["doc_text"]
    assert "payment_demand" not in json.dumps(body["state"])
    assert body["questions"]["intent"]["type"] == "choice"
    assert set(body["questions"]["intent"]["criteria"]) == {
        "payment_demand", "notice", "analysis", "request", "update", "meeting_invite",
        "press_communication", "other"}
    assert body["provider"]["data_collection"] == "deny"


def test_answers_are_never_bought_twice(tmp_path):
    calls = []
    post = lambda b: calls.append(b) or _answer("request")  # noqa: E731
    rows = [_row(i) for i in range(3)]
    kw = dict(cap_usd=0.01, max_wall_seconds=60, cfg=CFG, prompt=PROMPT, sha=SHA, post=post)
    assert jev.ask(rows, tmp_path, "calibrate", **kw)["asked"] == 3
    assert jev.ask(rows, tmp_path, "calibrate", **kw)["asked"] == 0
    assert len(calls) == 3


def test_run_cap_refuses_before_any_call(tmp_path):
    calls = []
    rows = [_row(i, text="x" * 200_000) for i in range(50)]
    with pytest.raises(spend.SpendError, match="run cap"):
        jev.ask(rows, tmp_path, "calibrate", cap_usd=0.001, max_wall_seconds=60, cfg=CFG, prompt=PROMPT,
                sha=SHA, post=lambda b: calls.append(b))
    assert calls == []


def test_campaign_jev_budget_counts_every_journal(tmp_path):
    j = tmp_path / "jev_intent-audit" / "journal.jsonl"
    j.parent.mkdir()
    j.write_text(json.dumps({"filename": "y", "prompt_sha": "old", "answer": {}, "cost_usd": 0.30}) + "\n")
    with pytest.raises(spend.SpendError, match="budget left"):
        jev.ask([_row(1)], tmp_path, "calibrate", cap_usd=0.05, max_wall_seconds=60, cfg=CFG, prompt=PROMPT,
                sha=SHA, post=lambda b: _answer("request"))


def test_calibration_gate_needs_full_coverage_and_floor(tmp_path):
    rows = [_row(i, intent="request") for i in range(4)]
    jev.ask(rows, tmp_path, "calibrate", cap_usd=0.01, max_wall_seconds=60, cfg=CFG, prompt=PROMPT, sha=SHA,
            post=lambda b: _answer("request"))
    rep = jev.score_calibration(rows, jev.read_journal(jev.journal_path(tmp_path, "calibrate")), SHA)
    assert rep["n"] == rep["of"] == 4 and rep["accuracy"] == 1.0
    path = tmp_path / "jev_intent-calibrate" / f"calibration-{SHA[:12]}.json"
    path.write_text(json.dumps({**rep, "kappa": 0.74}))
    assert jev.calibrated(tmp_path, SHA, 0.75) is None
    path.write_text(json.dumps({**rep, "kappa": 0.80, "n": 3}))
    assert jev.calibrated(tmp_path, SHA, 0.75) is None  # an answer missing: not calibrated
    path.write_text(json.dumps({**rep, "kappa": 0.80}))
    assert jev.calibrated(tmp_path, SHA, 0.75)
    assert jev.calibrated(tmp_path, "0" * 64, 0.75) is None  # another prompt sha never inherits it


def test_flags_low_probability_on_the_agreed_label():
    journal = [{"filename": "a", "prompt_sha": SHA, "answer": _answer("update")["answers"]["intent"]},
               {"filename": "b", "prompt_sha": SHA, "answer": _answer("request")["answers"]["intent"]}]
    fl = jev.flags(journal, {"a": "request", "b": "request"}, SHA, 0.30)
    assert [f["filename"] for f in fl] == ["a"]


def test_agreed_labels_read_train_decisions_only(tmp_path):
    d = tmp_path / "4a_intent-relabel-1of3"
    d.mkdir()
    cols = ["filename", "split", "existing", "decision", "new_intent", "reviewer_decision"]
    lines = [",".join(cols),
             "c1,train,update,confirm,update,",
             "c2,train,notice,change,request,",
             "c3,train,update,change,request,keep",
             "c4,train,update,adjudicate,,analysis",
             "c5,test,update,adjudicate,,request"]
    (d / "decisions.csv").write_text("\n".join(lines) + "\n")
    assert jev.agreed_labels(tmp_path) == {"c1": "update", "c2": "request", "c3": "update", "c4": "analysis"}
