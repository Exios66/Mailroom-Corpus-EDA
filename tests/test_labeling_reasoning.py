"""Per-model reasoning, the measured reasoning allowance and the runaway flag (no network, no spend).

A fake chat answers every request; nothing here reaches OpenRouter.
"""
from __future__ import annotations

import json

import pytest
from test_labeling import _row, rows  # noqa: F401  (rows is a fixture)
from test_labeling_review_export import _batch, _packet

from mailroom_eda.labeling import export, ingest, operate, providers, review_export, runner, spend, tasks
from mailroom_eda.labeling.registry import models_registry

GLM, DEEPSEEK, MINIMAX = "z-ai/glm-5.3-flashx-legal", "deepseek/deepseek-v4.1-flash", "minimax/minimax-m3.1-flash"
BACKEND = providers.Backend("openrouter", "u", "k")
MIXED = {GLM: "low", DEEPSEEK: "off"}


def _answers(task="correspondence"):
    """A not_found answer for every field of the task, so any model's call parses."""
    row = _row("correspondence", "attorney_demand", "Please pay $4,500 by Friday.")
    return {"fields": {f: {"value": None, "status": "not_found", "evidence": ""}
                       for f in tasks.build(task, row).fields}}


class Sent:
    """A fake chat that records (model, reasoning, max_tokens) of every request."""

    def __init__(self, answer=None):
        self.calls, self.answer = [], answer or _answers()

    def __call__(self, backend, model, messages, schema, *, reasoning, max_tokens):
        self.calls.append((model, reasoning, max_tokens))
        served = {GLM: "z-ai", DEEPSEEK: "CoreWeave"}.get(model, "P")  # inside each model's route pin
        return providers.Completion(json.dumps(self.answer), self.answer, model, served, 100, 50, 0, 0.0001,
                                    "stop", True)


# ---------------------------------------------------------------- normalizing

def test_reasoning_levels_accepts_one_level_or_a_mapping_per_model():
    assert runner.reasoning_levels("low", [GLM, DEEPSEEK]) == {GLM: "low", DEEPSEEK: "low"}
    assert runner.reasoning_levels({DEEPSEEK: "off", GLM: "low"}, [GLM, DEEPSEEK]) == MIXED
    assert list(runner.reasoning_levels({DEEPSEEK: "off", GLM: "low"}, [GLM, DEEPSEEK])) == [GLM, DEEPSEEK]
    for bad, why in ((("high", [GLM]), "reasoning must be one of"),
                     (({GLM: "low", "x/y": "off"}, [GLM]), "outside this run"),
                     (({GLM: "low"}, [GLM, DEEPSEEK]), "no level for"),
                     (({GLM: "medium"}, [GLM]), "reasoning must be one of")):
        with pytest.raises(ValueError, match=why):
            runner.reasoning_levels(*bad)


def test_reasoning_spec_collapses_a_shared_level_so_existing_records_keep_their_shape():
    assert runner.reasoning_spec({GLM: "low", DEEPSEEK: "low"}) == "low"
    assert runner.reasoning_spec(MIXED) == MIXED
    assert runner.reasoning_text("low") == "low"
    assert runner.reasoning_text(MIXED) == "glm-5.3-flashx-legal low, deepseek-v4.1-flash off"


def test_chunk_name_suffix_follows_model_order_and_single_levels_keep_their_names():
    name = lambda r, **k: runner.chunk_name("cuad", "pending", "1/3", r, **k)  # noqa: E731
    assert name("off") == "cuad-pending-1of3"
    assert name("low") == "cuad-pending-1of3-reasoning-low"
    assert name(MIXED, models=[GLM, DEEPSEEK]) == "cuad-pending-1of3-reasoning-low+off"
    assert name(MIXED, models=[DEEPSEEK, GLM]) == "cuad-pending-1of3-reasoning-off+low"
    assert name({GLM: "off", DEEPSEEK: "off"}) == "cuad-pending-1of3"
    assert name(("low", "off")) == "cuad-pending-1of3-reasoning-low+off"
    assert name(MIXED, models=[GLM, DEEPSEEK], full_text=True).endswith("-reasoning-low+off-fulltext")


def test_cli_run_takes_a_second_reasoning_for_the_second_model():
    args = runner._parser().parse_args([
        "run", "--task", "correspondence", "--set", "pilot", "--provider", "openrouter", "--model", GLM,
        "--second-model", DEEPSEEK, "--reasoning", "low", "--second-reasoning", "off"])
    assert (args.reasoning, args.second_reasoning) == ("low", "off")
    default = runner._parser().parse_args([
        "run", "--task", "correspondence", "--set", "pilot", "--provider", "openrouter", "--model", GLM])
    assert default.second_reasoning is None  # None: the second model follows --reasoning


# ---------------------------------------------------------------- the allowance

def test_allowance_is_four_times_the_measured_p75_clamped_to_floor_and_ceiling(monkeypatch):
    assert runner.reasoning_allowance(GLM, "claim_checklist", "low") == 1024  # 4 x 134 = 536: the floor
    assert runner.reasoning_allowance(DEEPSEEK, "cuad", "low") == 6000  # 4 x 1500
    assert runner.reasoning_allowance(GLM, "cuad", "low") == 1200  # the model's default p75 of 300
    assert runner.reasoning_allowance("not/measured", "cuad", "low") == runner.REASONING_ALLOWANCE == 6144
    assert runner.reasoning_allowance(DEEPSEEK, "cuad", "off") == 0
    # a noisy p75 never reserves more than the old flat allowance
    monkeypatch.setitem(models_registry()["models"], "t/huge", {"reasoning_tokens": {"low": {"cuad": 5000}}})
    assert runner.reasoning_allowance("t/huge", "cuad", "low") == runner.REASONING_ALLOWANCE


def test_max_tokens_uses_the_models_allowance_and_keeps_the_old_signature():
    base = runner.MAX_TOKENS["cuad"]
    assert runner.max_tokens("cuad", "off") == runner.max_tokens("cuad", "off", GLM) == base
    assert runner.max_tokens("cuad", "low") == base + runner.REASONING_ALLOWANCE  # no model: the ceiling
    assert runner.max_tokens("cuad", "low", GLM) == base + 1200
    assert runner.max_tokens("cuad", "low", DEEPSEEK) == base + 6000
    assert runner.max_tokens("claim_checklist", "low", GLM) == runner.MAX_TOKENS["claim_checklist"] + 1024


def test_a_cap_the_flat_allowance_refused_now_passes_reservation_for_a_cuad_sized_prompt():
    prompt_tokens = 20_000  # a CUAD contract with its prompt
    old = runner.max_tokens("cuad", "low")  # the flat allowance every call used to reserve
    new = runner.max_tokens("cuad", "low", GLM)
    assert old == 8192 + 6144 and new == 8192 + 1200
    cap = 0.022
    with pytest.raises(spend.SpendError, match="next call could cost"):
        spend.Budget(cap, 3600).reserve(GLM, prompt_tokens, old)
    reserved = spend.Budget(cap, 3600).reserve(GLM, prompt_tokens, new)
    assert reserved == pytest.approx(spend.token_cost(GLM, prompt_tokens, new)) and reserved < cap


# ---------------------------------------------------------------- a mixed pair through run_chunk

def _run(rows, out, chat, reasoning=MIXED, models=(GLM, DEEPSEEK), **kw):
    return runner.run_chunk([rows[2]], task="correspondence", models=list(models), backend=BACKEND,
                            reasoning=reasoning, budget=spend.Budget(1.0, 3600), out_dir=out, chat=chat, **kw)


def test_a_mixed_pair_sends_each_model_its_own_level_and_token_budget(tmp_path, rows):
    chat = Sent()
    res = _run(rows, tmp_path, chat)
    assert res["calls"] == 2
    base = runner.MAX_TOKENS["correspondence"]
    assert sorted(chat.calls) == sorted([(GLM, "low", base + 1200), (DEEPSEEK, "off", base)])
    journal = {c["model"]: c for c in export.read_journal(tmp_path / "calls.jsonl")}
    assert journal[GLM]["reasoning"] == "low" and journal[DEEPSEEK]["reasoning"] == "off"


def test_a_mixed_run_resumes_and_reuses_per_model(tmp_path, rows):
    _run(rows, tmp_path / "a", Sent())
    again = Sent()
    assert _run(rows, tmp_path / "a", again)["skipped"] == 2 and not again.calls  # resume by level
    # the same mapping reuses both answers from the other journal; a different one reuses neither
    reuse = Sent()
    res = _run(rows, tmp_path / "b", reuse, reuse_from=[tmp_path / "a"])
    assert res["reused"] == 2 and not reuse.calls
    flipped = Sent()
    _run(rows, tmp_path / "c", flipped, reasoning={GLM: "off", DEEPSEEK: "low"}, reuse_from=[tmp_path / "a"])
    assert {(m, r) for m, r, _ in flipped.calls} == {(GLM, "off"), (DEEPSEEK, "low")}
    # only DeepSeek's level changes: GLM's answer (still low) is not bought again
    partial = Sent()
    _run(rows, tmp_path / "d", partial, reasoning={GLM: "low", DEEPSEEK: "low"}, reuse_from=[tmp_path / "a"])
    assert [(m, r) for m, r, _ in partial.calls] == [(DEEPSEEK, "low")]


def test_review_keeps_both_models_of_a_mixed_pair_and_a_single_level_keeps_one(tmp_path, rows):
    _run(rows, tmp_path, Sent())
    runner.review([rows[2]], "correspondence", tmp_path, reasoning=MIXED)
    fields = export.read_csv(tmp_path / "fields.csv")
    assert {f["model_a"] for f in fields} | {f.get("model_b") for f in fields} >= {GLM, DEEPSEEK}
    docs = export.read_csv(tmp_path / "documents.csv")
    assert docs[0]["reasoning"] == "low+off"  # the levels in model order, as the directory name has them
    runner.review([rows[2]], "correspondence", tmp_path, reasoning="low")  # old behaviour: calls at low only
    assert {f.get("model_b") for f in export.read_csv(tmp_path / "fields.csv")} == {""}
    runner.review([rows[2]], "correspondence", tmp_path)  # None: every call
    assert {f["model_b"] for f in export.read_csv(tmp_path / "fields.csv")} == {DEEPSEEK}


def test_a_mapping_that_does_not_fit_the_run_is_refused_before_any_call(tmp_path, rows):
    chat = Sent()
    with pytest.raises(ValueError, match="no level for"):
        _run(rows, tmp_path, chat, reasoning={GLM: "low"})
    with pytest.raises(ValueError, match="outside this run"):
        _run(rows, tmp_path, chat, reasoning={GLM: "low", DEEPSEEK: "off", MINIMAX: "low"})
    assert not chat.calls


def test_execute_stores_the_mapping_in_the_start_record_and_uses_the_same_levels(tmp_path, rows):
    chat = Sent()
    out = tmp_path / "labels" / "chunk"
    rc = runner.execute(rows, [rows[2]], task="correspondence", set_name="pilot", chunk_spec=None,
                        models=[GLM, DEEPSEEK], provider="openrouter", reasoning=MIXED, initials="JB",
                        cap_usd=1.0, max_wall_seconds=60, out_dir=out, chat=chat, key_status=lambda b: {},
                        env={"OPENROUTER_API_KEY": "k"}, reuse_from=[])
    assert rc == 0
    start = next(r for r in export.read_journal(out / "runs.jsonl") if r.get("started_utc"))
    assert start["reasoning"] == MIXED and start["models"] == [GLM, DEEPSEEK]
    assert {(m, r) for m, r, _ in chat.calls} == {(GLM, "low"), (DEEPSEEK, "off")}
    packet = _packet(out)  # the review packet carries each model's level
    assert packet["source"]["reasoning"] == MIXED
    assert all(r["reasoning"] == MIXED and "low" in r["review_provenance"] for r in packet["records"])
    shared = tmp_path / "labels" / "shared"
    runner.execute(rows, [rows[2]], task="correspondence", set_name="pilot", chunk_spec=None,
                   models=[GLM, DEEPSEEK], provider="openrouter", reasoning="off", initials="JB", cap_usd=1.0,
                   max_wall_seconds=60, out_dir=shared, chat=chat, key_status=lambda b: {},
                   env={"OPENROUTER_API_KEY": "k"}, reuse_from=[])
    assert next(r for r in export.read_journal(shared / "runs.jsonl") if r.get("started_utc"))["reasoning"] == "off"


def test_projections_price_each_model_at_its_own_level():
    est = {"output_tokens": 1000, "calls": 10, "prompt_tokens": 50_000, "cost_usd": {}}
    glm_low = runner.projected_output(GLM, "cuad", MIXED, est)
    assert glm_low == 1000 + round(300 * 10 * runner.RETRY_FACTOR)  # GLM's default p75 of 300
    assert runner.projected_output(DEEPSEEK, "cuad", MIXED, est) == 1000  # off: no reasoning tokens
    assert runner.projected_output(DEEPSEEK, "cuad", "low", est) == 1000 + round(1500 * 10 * runner.RETRY_FACTOR)
    mixed = runner.projected_cost(est, [GLM, DEEPSEEK], "cuad", MIXED)
    expected = (spend.token_cost(GLM, 50_000, glm_low) + spend.token_cost(DEEPSEEK, 50_000, 1000))
    assert mixed == pytest.approx(expected)
    both_low = runner.projected_cost(est, [GLM, DEEPSEEK], "cuad", "low")
    assert both_low > mixed  # DeepSeek at off is cheaper than DeepSeek at low
    with pytest.raises(ValueError):
        runner.projected_cost(est, [GLM, DEEPSEEK], "cuad", {GLM: "low"})


def test_campaign_entries_may_give_a_reasoning_per_model(tmp_path, monkeypatch):
    approvals = {"approved": []}
    monkeypatch.setattr(operate, "load_approvals", lambda name: approvals["approved"])
    entry = {"id": "x", "task": "cuad", "set": "pending", "models": [GLM, DEEPSEEK],
             "reasoning": {GLM: "low", DEEPSEEK: "off"}}
    assert operate.resolve("v10", entry) == ([GLM, DEEPSEEK], MIXED)
    assert operate.resolve("v10", {**entry, "reasoning": "off"}) == ([GLM, DEEPSEEK], "off")
    assert operate.resolve("v10", {**entry, "reasoning": {GLM: "off", DEEPSEEK: "off"}})[1] == "off"
    with pytest.raises(SystemExit, match="no level for"):
        operate.resolve("v10", {**entry, "reasoning": {GLM: "low"}})
    assert operate.winner_refs({**entry, "models": ["winner_of_1"], "reasoning": {"winner_of_1": "winner_of_1"}}) \
        == {"winner_of_1"}
    # a swapped model (--model-map) keeps the level of the model it replaced
    assert operate.remap_reasoning(MIXED, [GLM, DEEPSEEK], ["a/b", "c/d"]) == {"a/b": "low", "c/d": "off"}
    assert operate.remap_reasoning("low", [GLM], ["a/b"]) == "low"


def test_approved_configurations_compare_per_model():
    base = {"task": "t", "set": "pilot", "provider": "openrouter", "prompt_sha256": ["p"],
            "temperature": {GLM: 0, DEEPSEEK: 0}}
    mixed = {**base, "models": [GLM, DEEPSEEK], "reasoning": MIXED}
    assert ingest.configuration(GLM, mixed).startswith(f"{GLM}@openrouter/reasoning=low")
    assert ingest.configuration(DEEPSEEK, mixed).startswith(f"{DEEPSEEK}@openrouter/reasoning=off")
    # an approval for GLM at low calibrates GLM at low inside a mixed chunk, not DeepSeek at low
    single = {**base, "models": [GLM], "reasoning": "low", "set": "calibration", "temperature": {GLM: 0}}
    final = {**mixed, "set": "remainder"}
    assert ingest._calibrated(GLM, final, [single])
    assert not ingest._calibrated(DEEPSEEK, final, [{**single, "models": [DEEPSEEK], "temperature": {DEEPSEEK: 0}}])
    ds_off = {**base, "models": [DEEPSEEK], "reasoning": "off", "set": "calibration", "temperature": {DEEPSEEK: 0}}
    assert ingest._calibrated(DEEPSEEK, final, [ds_off])


# ---------------------------------------------------------------- the runaway flag

RUNAWAY = "reasoning ran away: 900 of 1000 output tokens were reasoning; answer truncated"


def _truncating(model_that_runs_away, *, as_error):
    """Chat where one model is cut off at the token limit with 900 of its 1000 output tokens reasoning."""
    ok = Sent()

    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        if model != model_that_runs_away:
            return ok(backend, model, messages, schema, reasoning=reasoning, max_tokens=max_tokens)
        if as_error:  # what providers.parse_completion raises for finish_reason=length
            raise providers.ProviderError(
                "response truncated (finish_reason=length); raise max_tokens or shorten the window",
                {"prompt_tokens": 400, "completion_tokens": 1000, "cost_usd": 0.0009,
                 "reasoning_tokens": 900, "finish_reason": "length"})
        return providers.Completion("", {}, model, "P", 400, 1000, 900, 0.0009, "length", True)
    return chat


@pytest.mark.parametrize("as_error", [True, False])
def test_a_runaway_is_flagged_failed_and_billed(tmp_path, rows, as_error):
    budget = spend.Budget(1.0, 3600)
    res = runner.run_chunk([rows[2]], task="correspondence", models=[GLM, DEEPSEEK], backend=BACKEND,
                           reasoning=MIXED, budget=budget, out_dir=tmp_path,
                           chat=_truncating(GLM, as_error=as_error))
    assert res["errors"] == 1 and res["reasoning_runaways"] == 1 and res["calls"] == 1
    journal = {c["model"]: c for c in export.read_journal(tmp_path / "calls.jsonl")}
    bad = journal[GLM]
    assert bad["reasoning_runaway"] is True and bad["error"] == RUNAWAY
    assert bad["cost_usd"] == pytest.approx(0.0009) and bad["completion_tokens"] == 1000
    assert bad["reasoning_tokens"] == 900 and bad["finish_reason"] == "length" and bad["reasoning"] == "low"
    assert "reasoning_runaway" not in journal[DEEPSEEK] and not journal[DEEPSEEK].get("error")
    assert budget.spent_usd == pytest.approx(0.0009 + 0.0001)  # the truncated call is billed
    # a failed call is sent again on the next run, the answered one is not
    again = Sent()
    res = runner.run_chunk([rows[2]], task="correspondence", models=[GLM, DEEPSEEK], backend=BACKEND,
                           reasoning=MIXED, budget=spend.Budget(1.0, 3600), out_dir=tmp_path, chat=again)
    assert [(m, r) for m, r, _ in again.calls] == [(GLM, "low")] and res["skipped"] == 1


def test_a_truncated_answer_with_little_reasoning_is_not_flagged_as_a_runaway(tmp_path, rows):
    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        raise providers.ProviderError("response truncated (finish_reason=length)",
                                      {"prompt_tokens": 400, "completion_tokens": 1000, "cost_usd": 0.0009,
                                       "reasoning_tokens": 300, "finish_reason": "length"})
    runner.run_chunk([rows[2]], task="correspondence", models=[GLM], backend=BACKEND, reasoning="low",
                     budget=spend.Budget(1.0, 3600), out_dir=tmp_path, chat=chat)
    bad = export.read_journal(tmp_path / "calls.jsonl")[0]
    assert "reasoning_runaway" not in bad and bad["error"].startswith("response truncated")
    assert runner.reasoning_runaway("length", 800, 1000) and not runner.reasoning_runaway("length", 799, 1000)
    assert not runner.reasoning_runaway("stop", 900, 1000) and not runner.reasoning_runaway("length", 0, 0)
    assert runner.reasoning_runaway("max_tokens", 900, 1000)


def test_parse_completion_reports_reasoning_and_finish_on_a_truncated_answer():
    payload = {"choices": [{"finish_reason": "length", "message": {"content": '{"a":'}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 100, "cost": 0.001,
                         "completion_tokens_details": {"reasoning_tokens": 90}}}
    with pytest.raises(providers.ProviderError) as err:
        providers.parse_completion(payload, used_schema=True)
    assert err.value.usage["reasoning_tokens"] == 90 and err.value.usage["finish_reason"] == "length"


def test_the_exported_packet_surfaces_a_runaway_as_an_issue_and_a_model_output(tmp_path, rows):
    row = rows[2]
    out = _batch(tmp_path, [row], task="correspondence", models=[GLM, DEEPSEEK], reasoning=MIXED,
                 chat=_truncating(GLM, as_error=True))
    packet = _packet(out)
    assert packet["source"]["reasoning"] == MIXED
    assert packet["records"]
    for record in packet["records"]:
        assert record["reasoning"] == MIXED and record["models"] == [GLM, DEEPSEEK]
        said = [i for i in record["issues"] if i.startswith(f"Reasoning ran away on {GLM}:")]
        assert said and "900 of 1000 output tokens were reasoning" in said[0], record["issues"]
        flagged = [o for o in record["model_outputs"] if o.get("reasoning_runaway")]
        assert len(flagged) == 1 and flagged[0]["model"] == GLM and flagged[0]["status"] == "not_found"
        assert flagged[0]["error"] == RUNAWAY
        assert any(o["model"] == DEEPSEEK and not o.get("reasoning_runaway") for o in record["model_outputs"])
    # a retry that succeeds clears the flag on the next export
    runner.run_chunk([row], task="correspondence", models=[GLM, DEEPSEEK], backend=BACKEND, reasoning=MIXED,
                     budget=spend.Budget(1.0, 3600), out_dir=out, chat=Sent(), set_name="pilot")
    runner.review([row], "correspondence", out, reasoning=MIXED)
    healed = _packet(out)
    assert not any("ran away" in i for r in healed["records"] for i in r["issues"])


def test_a_runaway_on_an_old_reasoning_level_does_not_taint_a_new_run(tmp_path, rows):
    row = rows[2]
    out = tmp_path / "labels" / "1_correspondence-pilot"
    export.append_journal(out / "runs.jsonl", {
        "chunk": "1", "task": "correspondence", "set": "pilot", "slice": None, "models": [GLM],
        "provider": "openrouter", "reasoning": "off", "initials": "AB", "documents": 1,
        "started_utc": "2026-11-08T00:00:00Z"})
    export.append_journal(out / "calls.jsonl", {
        "task": "correspondence", "document_id": "e1", "model": GLM, "variant": "", "reasoning": "low",
        "error": RUNAWAY, "reasoning_runaway": True, "completion_tokens": 1000, "reasoning_tokens": 900})
    batch = review_export._Batch(out)
    assert review_export._runaways(out, batch) == {}
