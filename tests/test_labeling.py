"""Offline tests for the v10 labeling package (no network: HTTP is mocked)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from mailroom_eda.labeling import export, operate, providers, runner, spend, tasks, verify
from mailroom_eda.labeling.registry import (
    PROMPTS_DIR,
    Prompt,
    campaign,
    class_fields,
    dojo_snapshot,
    fields_registry,
    models_registry,
    prompt,
    vocabulary,
)

ROOT = Path(__file__).resolve().parents[1]
DOJO_SRC = Path("/home/user/local-mailroom-sandbox/vendor/llm-dojo-scoring/src")

# ---------------------------------------------------------------- fixture rows

CONTRACT_TEXT = (
    "CONSULTING AGREEMENT\n\nThis Consulting Agreement is dated as of March 5, 2004 between "
    "Acme Corp. (\"Acme\") and Beta LLC (\"Consultant\").\n\n1. Term. The term is two (2) years.\n"
    "12. Governing Law. This Agreement shall be governed by the laws of the State of Delaware.\n"
    "IN WITNESS WHEREOF\nBy: /s/ Jane Roe\nName: Jane Roe\n"
)
MERGER_TEXT = (
    "AGREEMENT AND PLAN OF MERGER among Parent Inc., Merger Sub Corp. and Target Co.\n"
    "ARTICLE I THE MERGER\nSection 1.2 Effective Time. The Merger shall become effective at the time "
    "the Certificate of Merger is filed.\nSection 2.1 Conversion of Shares. Each Share shall be "
    "converted into the right to receive $12.50 in cash.\nSection 8.3 Termination Fee. The Company "
    "shall pay Parent a fee of $25,000,000.\nSection 9.8 Governing Law. This Agreement shall be "
    "governed by the laws of the State of Delaware.\n"
)
EMAIL_TEXT = ("From: alice@enron.com\nTo: bob@enron.com\nSubject: Q3 invoice\n\nBob, please pay the "
              "outstanding $4,500 by Friday.\n\nAlice\n")
CLAIM_TEXT = "Claim CLM-1 for policy POL-9. Coverage determination: approved. Deductible $500 applies."
CORP_TEXT = ("AMENDED AND RESTATED BYLAWS OF ACME CORP., a Delaware corporation. Adopted by the Board "
             "of Directors on May 1, 2010.")


def _row(cls, sub, text, gt=None, fn=None, **extra):
    return {"filename": fn or f"{cls}_{sub}_1.txt", "expected": cls, "expected_subclass": sub,
            "doc_text": text, "gt_fields": gt or {}, "split": "test", **extra}


@pytest.fixture()
def rows():
    return [
        _row("contract", "Consulting Agreements", CONTRACT_TEXT, document_id="c1"),
        _row("merger_agreement", "all_cash", MERGER_TEXT, document_id="m1"),
        _row("correspondence", "attorney_demand", EMAIL_TEXT, document_id="e1"),
        _row("insurance_claim", "outpatient", CLAIM_TEXT, document_id="i1"),
        _row("corporate_record", "bylaws", CORP_TEXT, {"entity_name": "Acme Corp."}, document_id="k1"),
    ]


# ---------------------------------------------------------------- registry and Dojo parity

def test_fields_match_pinned_dojo_types():
    dft = dojo_snapshot()["default_field_types"]
    reg = fields_registry()["classes"]
    assert set(reg) == set(dft)
    for cls, types in dft.items():
        fields = reg[cls]["fields"]
        for name, spec in fields.items():
            if spec.get("dojo") == "v2" or spec.get("subclass_entity"):
                assert name not in types or spec.get("dojo") != "v2", (cls, name)
                continue
            assert name in types, f"{cls}.{name} is neither a Dojo field nor marked dojo: v2"
            assert spec["type"] == types[name], (cls, name, spec["type"], types[name])
        missing = set(types) - set(fields)
        # The parse-compat merger fields on the contract map are dropped by G1.
        expected_missing = {"merger_consideration", "maud_clauses"} if cls == "contract" else set()
        assert missing == expected_missing, (cls, missing)


def test_fields_have_valid_policy_and_source():
    reg = fields_registry()
    for cls, block in reg["classes"].items():
        for name, spec in block["fields"].items():
            assert spec["source"] in {"deterministic", "gold", "llm"}, (cls, name)
            assert spec["absence"] in {"absent_ok", "evidence_only", "never_absent"}, (cls, name)
            if spec["source"] == "llm":
                assert spec.get("task") in tasks.TASK_CLASSES, (cls, name)
                assert tasks.TASK_CLASSES[spec["task"]] == cls, (cls, name)
            if spec.get("vocabulary"):
                assert vocabulary(spec["vocabulary"])


def test_corporate_subclass_fields_follow_dojo():
    schemas = dojo_snapshot()["corporate_subclass_schemas"]
    for sub, schema in schemas.items():
        fields = class_fields("corporate_record", sub)
        for ent in schema.get("entities", []):
            assert ent["key"] in fields, (sub, ent["key"])
    with pytest.raises(KeyError):
        class_fields("corporate_record", "no_such_subclass")


def test_snapshot_pins_match_fields_yaml():
    snap = dojo_snapshot()
    pin = fields_registry()["dojo_pin"]
    assert (snap["dojo_version"], snap["dojo_commit"]) == (pin["version"], pin["commit"])


@pytest.mark.skipif(not DOJO_SRC.exists(), reason="vendored llm-dojo-scoring not present")
def test_snapshot_is_current_against_vendored_dojo():
    py = Path("/home/user/local-mailroom-sandbox/.venv/bin/python")
    proc = subprocess.run([str(py if py.exists() else sys.executable),
                           str(ROOT / "scripts/labeling/snapshot_dojo.py"), "--dojo-src", str(DOJO_SRC),
                           "--check"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_models_have_prices_and_providers():
    reg = models_registry()
    for name, spec in reg["models"].items():
        assert spec["provider"] in providers.PROVIDERS
        if spec["provider"] == "openrouter":
            assert spend.price(name)[0] > 0
    for name in reg.get("comparison", {}):
        assert spend.price(name)[1] > 0


def test_campaign_chunks_are_runnable():
    camp = campaign("v10")
    approved = models_registry()["models"]
    for ch in camp["chunks"]:
        assert ch["task"] in tasks.TASK_CLASSES
        assert ch["set"] in runner.SETS
        assert str(ch["reasoning"]) in providers.REASONING + ("winner_of_1", "winner_of_3")
        assert ch["cap_usd"] > 0 and "kind" in ch["gate"]
        for m in ch["models"]:
            assert m in approved or m.startswith("winner_of_"), m
        if ch["task"] in tasks.TWO_MODEL_TASKS:
            assert len(ch["models"]) == 2, ch["id"]


# ---------------------------------------------------------------- prompts

def test_prompt_render_rejects_missing_and_extra_values():
    p = Prompt("t", "Hello ${name}, from ${place}.")
    assert p.placeholders == {"name", "place"}
    assert p.render({"name": "A", "place": "B"}) == "Hello A, from B."
    with pytest.raises(KeyError):
        p.render({"name": "A"})
    with pytest.raises(ValueError):
        p.render({"name": "A", "place": "B", "x": 1})


def test_prompt_sha_is_content_hash():
    assert Prompt("a", "x").sha256 != Prompt("a", "y").sha256
    assert prompt("extract_rules").sha256 == prompt("extract_rules").sha256


def test_plan_doc_quotes_prompts_verbatim():
    plan = (ROOT / "docs/plans/v10-structure-and-label-completion.md").read_text(encoding="utf-8")
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        block = f"### `{path.name}`"
        assert block in plan, path.name
        body = plan.split(block, 1)[1]
        quoted = re.search(r"```text\n(.*?)\n```", body, re.S)[1]
        assert quoted == path.read_text(encoding="utf-8").rstrip("\n"), path.name


# ---------------------------------------------------------------- task builders

@pytest.mark.parametrize("task", sorted(tasks.TASK_CLASSES))
def test_builders_render_every_task(rows, task):
    row = next(r for r in rows if r["expected"] == tasks.TASK_CLASSES[task])
    req = tasks.build(task, row, "a" if task == "intent" else "")
    assert req.document_id == row["document_id"]
    assert req.messages[0]["role"] == "system"
    text = "\n".join(m["content"] for m in req.messages)
    assert "${" not in text
    assert re.fullmatch(r"[0-9a-f]{64}", req.prompt_sha256)
    if task in tasks.TWO_MODEL_TASKS:
        props = req.schema["properties"]["fields"]["properties"]
        assert set(props) == set(req.fields)
        assert req.schema["properties"]["fields"]["additionalProperties"] is False


def test_intent_pass_b_hides_subject(rows):
    row = rows[2]
    a = tasks.build("intent", row, "a")
    b = tasks.build("intent", row, "b")
    assert "Q3 invoice" in a.messages[1]["content"]
    assert "Subject:" not in b.messages[1]["content"]


def test_cuad_repair_replays_first_answer(rows):
    req = tasks.build_cuad(rows[0], repair_problems=["Parties: not verbatim"], prior_answer='{"clauses": []}')
    assert req.variant == "repair"
    assert [m["role"] for m in req.messages] == ["system", "user", "assistant", "user"]
    assert req.messages[2]["content"] == '{"clauses": []}'
    assert req.prompt_sha256 != tasks.build_cuad(rows[0]).prompt_sha256


def test_gold_fields_are_only_asked_when_empty(rows):
    corp = rows[4]
    assert "entity_name" not in tasks._task_fields(corp, "corporate")
    assert "entity_name" in tasks._task_fields({**corp, "gt_fields": {}}, "corporate")


def test_window_keeps_head_and_tail():
    text = "A" * 100 + "B" * 100
    shown, desc = tasks.window(text, 50, 20)
    assert shown.startswith("A" * 50) and shown.endswith("B" * 20)
    assert "first 50" in desc and "last 20" in desc
    assert tasks.window("short", 50, 20)[0] == "short"


def test_cuad_family_maps_every_contract_subclass():
    fams = {f["key"] for f in dojo_snapshot()["cuad_families"]}
    assert tasks.cuad_family("Consulting Agreements")["key"] in fams
    assert tasks.cuad_family("no such family") is None


# ---------------------------------------------------------------- verify

def test_locate_exact_and_folded():
    doc = "The **Term** is\n\ntwo   years."
    assert verify.locate(doc, "two   years") == (doc.index("two"), doc.index("two") + 11)
    start, end = verify.locate(doc, "the term is two years")
    assert doc[start:end].startswith("The") and doc[start:end].endswith("years")
    assert verify.locate(doc, "three years") is None


def test_normalize_and_agree_by_type():
    assert verify.agree("March 5, 2004", "2004-03-05", "date")
    assert verify.agree("$1,500,000", "1.5 million", "money")
    assert verify.agree("Acme Corp.", "Acme Corp", "name")
    assert not verify.agree("Acme", "Beta", "name")
    assert verify.agree(["Acme Corp.", "Beta LLC"], ["Beta LLC", "Acme Corp"], "entity_list:name")
    assert not verify.agree(["Acme"], ["Acme", "Beta", "Gamma"], "entity_list:name")
    assert verify.agree("all_cash", "All Cash", "label")


def test_grounding_rejects_values_not_in_document():
    doc = verify.DocIndex.build(CONTRACT_TEXT)
    assert verify.grounded("Acme Corp.", "name", doc)
    assert not verify.grounded("Gamma Inc.", "name", doc)
    assert verify.grounded("2004-03-05", "date", doc)
    assert not verify.grounded("2011-03-05", "date", doc)
    assert verify.grounded("email", "label", doc, vocab=["email", "memo"])
    assert not verify.grounded("fax", "label", doc, vocab=["email", "memo"])


def test_check_field_and_decide():
    doc = verify.DocIndex.build(CONTRACT_TEXT)
    good = {"value": "Acme Corp.", "status": "present", "evidence": "Acme Corp. (\"Acme\")"}
    fake = {"value": "Acme Corp.", "status": "present", "evidence": "Acme Corporation of Ohio"}
    a = verify.check_field(good, "name", doc)
    b = verify.check_field(good, "name", doc)
    assert a.usable
    assert verify.decide(a, b, "name", "evidence_only")[0] == "accept"
    assert not verify.check_field(fake, "name", doc).usable
    assert verify.decide(a, verify.check_field(fake, "name", doc), "name", "evidence_only")[0] == "adjudicate"
    nf = verify.check_field({"value": None, "status": "not_found", "evidence": ""}, "money", doc)
    assert verify.decide(nf, nf, "money", "absent_ok")[0] == "absent"
    assert verify.decide(nf, nf, "money", "evidence_only")[0] == "unlabeled"
    assert verify.decide(nf, nf, "label", "never_absent")[0] == "adjudicate"
    assert verify.decide(a, None, "name", "evidence_only")[0] == "adjudicate"
    other = verify.check_field({"value": "Beta LLC", "status": "present", "evidence": "Beta LLC"}, "name", doc)
    assert verify.decide(a, other, "name", "evidence_only") == ("adjudicate", None, "models disagree")


def test_check_field_coerces_unknown_status():
    doc = verify.DocIndex.build(CONTRACT_TEXT)
    r = verify.check_field({"value": "x", "status": "maybe"}, "name", doc)
    assert r.status == "not_found" and r.value is None


# ---------------------------------------------------------------- providers (mocked HTTP)

def _ok(content, **usage):
    return 200, {}, {"model": "m-served", "provider": "P",
                     "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5, **usage}}


def test_payload_shapes_per_provider():
    msgs = [{"role": "user", "content": "x"}]
    schema = {"type": "object"}
    o = providers.build_payload("openrouter", "a/b", msgs, schema, reasoning="off")
    assert o["temperature"] == 0 and o["seed"] == 42
    assert o["response_format"]["json_schema"]["strict"] is True
    assert o["provider"] == {"require_parameters": True, "allow_fallbacks": False}
    assert o["reasoning"] == {"enabled": False} and o["usage"] == {"include": True}
    assert providers.build_payload("openrouter", "a/b", msgs, schema, reasoning="low")["reasoning"] == {
        "effort": "low"}
    m = providers.build_payload("modal", "q", msgs, None, reasoning="off")
    assert m["response_format"] == {"type": "json_object"}
    assert m["chat_template_kwargs"] == {"enable_thinking": False}
    assert "provider" not in m and "reasoning" not in m
    with pytest.raises(ValueError):
        providers.build_payload("openrouter", "a/b", msgs, schema, reasoning="high")


def test_backend_from_env():
    b = providers.Backend.from_env("modal", {"VLLM_BASE_URL": "https://x.modal.run/", "VLLM_API_KEY": "sekrit-key"})
    assert b.base_url == "https://x.modal.run/v1"
    assert "sekrit" not in repr(b)
    with pytest.raises(providers.ProviderError):
        providers.Backend.from_env("openrouter", {})
    with pytest.raises(providers.ProviderError):
        providers.Backend.from_env("modal", {"VLLM_API_KEY": "sekrit-key"})


def test_chat_retries_with_retry_after_then_succeeds():
    calls, waits = [], []
    replies = [(429, {"Retry-After": "7"}, {}), (503, {}, {}), _ok('{"a": 1}', cost=0.002)]

    def post(url, key, body, timeout):
        calls.append(body)
        return replies[len(calls) - 1]

    b = providers.Backend("openrouter", "https://or/api/v1", "k")
    c = providers.chat(b, "a/b", [{"role": "user", "content": "x"}], {"type": "object"},
                       post=post, sleep=waits.append)
    assert c.parsed == {"a": 1} and c.cost_usd == 0.002 and c.served_model == "m-served"
    assert len(calls) == 3 and waits[0] == 7.0


def test_chat_falls_back_to_json_mode_on_schema_rejection():
    seen = []

    def post(url, key, body, timeout):
        seen.append(body["response_format"]["type"])
        return (400, {}, {"error": "schema"}) if len(seen) == 1 else _ok('{"ok": true}')

    b = providers.Backend("openrouter", "https://or/api/v1", "k")
    c = providers.chat(b, "a/b", [{"role": "user", "content": "x"}], {"type": "object"},
                       post=post, sleep=lambda s: None)
    assert seen == ["json_schema", "json_object"] and c.used_schema is False


def test_chat_network_errors_and_hard_failures():
    def boom(url, key, body, timeout):
        raise urllib.error.URLError("down")

    b = providers.Backend("openrouter", "https://or/api/v1", "k")
    with pytest.raises(providers.ProviderError, match="gave up"):
        providers.chat(b, "a/b", [], None, post=boom, sleep=lambda s: None, attempts=2)
    with pytest.raises(providers.ProviderError, match="HTTP 401"):
        providers.chat(b, "a/b", [], None, post=lambda *a: (401, {}, {}), sleep=lambda s: None)


def test_parse_completion_rejects_truncated_and_non_json():
    with pytest.raises(providers.ProviderError, match="truncated"):
        providers.parse_completion({"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]},
                                   used_schema=True)
    with pytest.raises(providers.ProviderError, match="not JSON"):
        providers.parse_completion({"choices": [{"message": {"content": "hi"}}]}, used_schema=True)
    fenced = providers.parse_completion({"choices": [{"message": {"content": '```json\n{"a": 2}\n```'}}],
                                         "usage": {"completion_tokens_details": {"reasoning_tokens": 3}}},
                                        used_schema=True)
    assert fenced.parsed == {"a": 2} and fenced.reasoning_tokens == 3


# ---------------------------------------------------------------- spend

def test_run_flags_need_human_initials_cap_and_wall():
    spend.require_run_flags(initials="JB", cap_usd=1.0, max_wall_seconds=60)
    for bad in ({"initials": None, "cap_usd": 1.0, "max_wall_seconds": 60},
                {"initials": "jb", "cap_usd": 1.0, "max_wall_seconds": 60},
                {"initials": "JB", "cap_usd": 0, "max_wall_seconds": 60},
                {"initials": "JB", "cap_usd": 1.0, "max_wall_seconds": None}):
        with pytest.raises(spend.SpendError):
            spend.require_run_flags(**bad)


def test_key_headroom_keeps_reserve():
    assert spend.check_key_headroom({"limit_remaining": None}, 50) == float("inf")
    assert spend.check_key_headroom({"limit_remaining": 10.0}, 5.0) == pytest.approx(7.0)
    with pytest.raises(spend.SpendError):
        spend.check_key_headroom({"limit_remaining": 4.0}, 2.0)


def test_budget_stops_before_the_cap():
    model = "deepseek/deepseek-v4.1-flash"
    b = spend.Budget(cap_usd=0.01, max_wall_seconds=3600)
    b.reserve(model, 1000, 1000)
    b.settle(model, 1000, 1000, None)
    assert b.spent_usd == pytest.approx(spend.token_cost(model, 1000, 1000))
    b.settle(model, 0, 0, 0.0099)
    with pytest.raises(spend.SpendError, match="cap"):
        b.reserve(model, 1000, 1000)
    w = spend.Budget(cap_usd=1, max_wall_seconds=-1)
    with pytest.raises(spend.SpendError, match="wall"):
        w.reserve(model, 1, 1)


# ---------------------------------------------------------------- export

def test_csv_round_trip_escapes_and_rejects_unknown(tmp_path):
    rows = [{"document_id": "d1", "field": "x", "model_a_value": "line1\nline2\r\\end",
             "agree": True, "decision": "accept"},
            {"document_id": "d2", "field": "y", "model_a_value": ["a", "b"]}]
    path = tmp_path / "fields.csv"
    assert export.write_csv(path, export.FIELD_COLUMNS, rows) == 2
    raw = path.read_text(encoding="utf-8")
    assert raw.count("\n") == 3  # header + two rows, embedded newlines escaped
    back = export.read_csv(path)
    assert back[0]["model_a_value"] == "line1\nline2\r\\end"
    assert json.loads(back[1]["model_a_value"]) == ["a", "b"]
    with pytest.raises(ValueError):
        export.write_csv(path, export.FIELD_COLUMNS, [{"nope": 1}])


def test_journal_ignores_torn_last_line(tmp_path):
    path = tmp_path / "calls.jsonl"
    export.append_journal(path, {"a": 1})
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"a": 2')
    assert export.read_journal(path) == [{"a": 1}]


# ---------------------------------------------------------------- runner

def test_chunk_slices_cover_everything():
    items = list(range(10))
    parts = [runner.chunk(items, f"{i}/3") for i in (1, 2, 3)]
    assert sum(parts, []) == items and [len(p) for p in parts] == [4, 4, 2]
    with pytest.raises(SystemExit):
        runner.chunk(items, "4/3")


def test_estimate_prices_every_model(rows):
    est = runner.estimate([rows[2]], "correspondence",
                          ["deepseek/deepseek-v4.1-flash", "minimax/minimax-m3.1-flash", "not/a-model"])
    assert est["calls"] == 1 and est["prompt_tokens"] > 0
    assert est["cost_usd"]["deepseek/deepseek-v4.1-flash"] > 0 and est["cost_usd"]["not/a-model"] is None


def _fake_chat(answers):
    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        content = json.dumps(answers[model])
        return providers.Completion(content, answers[model], model, "P", 100, 50, 0, 0.0001, "stop", True)
    return chat


def test_run_and_review_two_model_extraction(tmp_path, rows):
    row = rows[2]
    req = tasks.build("correspondence", row)
    base = {f: {"value": None, "status": "not_found", "evidence": ""} for f in req.fields}
    a = {"fields": {**base, "demand_amount": {"value": "$4,500", "status": "present",
                                              "evidence": "outstanding $4,500 by Friday"},
                    "urgency": {"value": "time-sensitive", "status": "present", "evidence": "by Friday"}}}
    b = {"fields": {**base, "demand_amount": {"value": "4500", "status": "present", "evidence": "$4,500"},
                    "urgency": {"value": "urgent", "status": "present", "evidence": "by Friday"}}}
    models = ["deepseek/deepseek-v4.1-flash", "minimax/minimax-m3.1-flash"]
    backend = providers.Backend("openrouter", "https://or/api/v1", "k")
    budget = spend.Budget(1.0, 3600)
    res = runner.run_chunk([row], task="correspondence", models=models, backend=backend, reasoning="off",
                           budget=budget, out_dir=tmp_path, chat=_fake_chat(dict(zip(models, (a, b)))))
    assert res["calls"] == 2 and res["spent_usd"] == pytest.approx(0.0002)
    again = runner.run_chunk([row], task="correspondence", models=models, backend=backend, reasoning="off",
                             budget=budget, out_dir=tmp_path, chat=_fake_chat({}))
    assert again["skipped"] == 2  # resumable: answered calls are not re-sent
    summary = runner.review([row], "correspondence", tmp_path)
    fields = {f["field"]: f for f in export.read_csv(tmp_path / "fields.csv")}
    assert fields["demand_amount"]["decision"] == "accept"
    assert fields["urgency"]["decision"] == "adjudicate"
    assert fields["sender"]["decision"] == "absent"
    adj = export.read_csv(tmp_path / "adjudication.csv")
    assert {r["field"] for r in adj} == {"urgency"}
    doc = export.read_csv(tmp_path / "documents.csv")[0]
    assert doc["status"] == "needs_review" and doc["model"] == ",".join(models)
    assert summary["documents"] == 1


def test_run_stops_at_cap_and_keeps_work(tmp_path, rows):
    row = rows[2]
    model = "deepseek/deepseek-v4.1-flash"
    res = runner.run_chunk([row], task="correspondence", models=[model],
                           backend=providers.Backend("openrouter", "u", "k"), reasoning="off",
                           budget=spend.Budget(1e-9, 3600), out_dir=tmp_path, chat=_fake_chat({}))
    assert res["stopped"] == 1 and "cap" in res["stop_reason"]
    assert not (tmp_path / "calls.jsonl").exists()


def test_cuad_run_repairs_and_review_writes_v94_records(tmp_path, rows):
    row = {**rows[0], "id": "c1"}
    vocab = [c["name"] for c in dojo_snapshot()["cuad_categories"]]
    first = {"clauses": [{"category": "Parties", "text": "Acme Corp. (\"Acme\")"},
                         {"category": "Governing Law", "text": "governed by the laws of Ohio"}]}
    fixed = {"clauses": [{"category": "Parties", "text": "Acme Corp. (\"Acme\")"},
                         {"category": "Parties", "text": "Beta LLC (\"Consultant\")"},
                         {"category": "Governing Law",
                          "text": "This Agreement shall be governed by the laws of the State of Delaware."}]}
    sent = []

    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        sent.append([m["role"] for m in messages])
        ans = first if len(sent) == 1 else fixed
        return providers.Completion(json.dumps(ans), ans, model, "P", 100, 50, 0, None, "stop", True)

    res = runner.run_chunk([row], task="cuad", models=["z-ai/glm-5.3-flashx-legal"],
                           backend=providers.Backend("openrouter", "u", "k"), reasoning="off",
                           budget=spend.Budget(1.0, 3600), out_dir=tmp_path, vocab=vocab, chat=chat)
    assert res["repairs"] == 1 and sent[1] == ["system", "user", "assistant", "user"]
    runner.review([row], "cuad", tmp_path)
    rec = export.read_journal(tmp_path / "cuad-labels.jsonl")[0]
    assert rec["repaired"] is True and rec["span_count"] == 3
    gl = rec["cuad_clause_labels"]["Governing Law"][0]
    assert row["doc_text"][gl["start"]:gl["start"] + len(gl["text"])] == gl["text"]
    assert {"filename", "model", "accepted", "span_count", "cuad_clause_labels"} <= set(rec)
    assert export.read_csv(tmp_path / "spans.csv")


def test_intent_review_pairs_two_passes(tmp_path, rows):
    row = rows[2]
    ans = {"intent": "payment_demand", "confidence": 0.9, "evidence": "please pay"}
    runner.run_chunk([row], task="intent", models=["deepseek/deepseek-v4.1-flash"],
                     backend=providers.Backend("openrouter", "u", "k"), reasoning="low",
                     budget=spend.Budget(1.0, 3600), out_dir=tmp_path,
                     chat=_fake_chat({"deepseek/deepseek-v4.1-flash": ans}))
    runner.review([row], "intent", tmp_path)
    rec = export.read_journal(tmp_path / "intent-labels.jsonl")[0]
    assert rec["pass_a"]["intent"] == rec["pass_b"]["intent"] == "payment_demand"
    assert rec["reasoning"] == "low"


def test_cli_refuses_paid_run_without_initials(tmp_path, monkeypatch, rows):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "ground_truth_hardened.jsonl").write_text(
        "\n".join(json.dumps({**r, "gt_fields": json.dumps(r["gt_fields"])}) for r in rows) + "\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    base = ["run", "--task", "correspondence", "--set", "all", "--stage", str(stage), "--out", str(tmp_path),
            "--provider", "openrouter", "--model", "deepseek/deepseek-v4.1-flash",
            "--second-model", "minimax/minimax-m3.1-flash"]
    assert runner.main(base + ["--dry-run"]) == 0
    with pytest.raises(spend.SpendError):
        runner.main(base + ["--api-cost-cap-usd", "1", "--max-wall-seconds", "60"])
    with pytest.raises(SystemExit):
        runner.main(base[:-2])  # extraction outside a pilot needs a second model
    with pytest.raises(SystemExit):
        runner.main(base[:-4] + ["--model", "openai/gpt-6-luna", "--dry-run"])  # not approved


def test_costs_md_is_current():
    stage = runner.DEFAULT_STAGE / "ground_truth_hardened.jsonl"
    if not stage.exists():
        pytest.skip("v9.4 stage not built (data/ is gitignored)")
    assert runner.main(["costs", "--check"]) == 0


def test_review_keeps_reviewer_verdicts(tmp_path, rows):
    row = rows[2]
    req = tasks.build("correspondence", row)
    ans = {"fields": {f: {"value": None, "status": "not_found", "evidence": ""} for f in req.fields}}
    ans["fields"]["urgency"] = {"value": "urgent", "status": "present", "evidence": "by Friday"}
    other = json.loads(json.dumps(ans))
    other["fields"]["urgency"]["value"] = "routine"
    models = ["deepseek/deepseek-v4.1-flash", "minimax/minimax-m3.1-flash"]
    runner.run_chunk([row], task="correspondence", models=models,
                     backend=providers.Backend("openrouter", "u", "k"), reasoning="off",
                     budget=spend.Budget(1.0, 3600), out_dir=tmp_path,
                     chat=_fake_chat(dict(zip(models, (ans, other)))))
    runner.review([row], "correspondence", tmp_path)
    adj = export.read_csv(tmp_path / "adjudication.csv")
    adj[0]["reviewer_verdict"] = "b"
    export.write_csv(tmp_path / "adjudication.csv", export.FIELD_COLUMNS, adj)
    runner.review([row], "correspondence", tmp_path)
    assert export.read_csv(tmp_path / "adjudication.csv")[0]["reviewer_verdict"] == "b"
    adj[0]["reviewer_verdict"] = "maybe"
    export.write_csv(tmp_path / "adjudication.csv", export.FIELD_COLUMNS, adj)
    with pytest.raises(SystemExit):
        runner.review([row], "correspondence", tmp_path)


def test_merger_consideration_is_asked_only_without_maud_answer(rows):
    from mailroom_eda.labeling import derive

    m = rows[1]
    maud = {"Type of Consideration": {"answer": "All Cash"}}
    confirmed = {**m, "gt_fields": {"maud_clause_labels": json.dumps(maud)}}
    assert derive.merger_consideration(confirmed) == "all_cash"
    assert "merger_consideration" not in tasks._task_fields(confirmed, "merger_entities")
    unknown = {**m, "expected_subclass": "other"}
    assert derive.merger_consideration(unknown) is None
    req = tasks.build("merger_entities", unknown)
    assert "merger_consideration" in req.fields
    assert "- merger_consideration (label)" in req.messages[1]["content"]
    with pytest.raises(ValueError):
        derive.merger_consideration({**confirmed, "expected_subclass": "all_stock"})


def test_derived_subclasses_and_specialists(rows):
    from mailroom_eda.labeling import derive

    assert derive.canonical_subclass(rows[0]) == tasks.cuad_family("Consulting Agreements")["key"]
    assert derive.communication_type(rows[2]) == "attorney_demand"
    assert derive.communication_type({**rows[2], "expected_subclass": "fax"}) == "other"
    assert derive.specialist(rows[1]) == "merger_agreement_specialist"


# ---------------------------------------------------------------- OpenRouter routing (models.yaml)

def test_openrouter_routes_pin_providers_and_only_send_supported_seed():
    msgs = [{"role": "user", "content": "x"}]
    schema = {"type": "object"}
    glm = providers.build_payload("openrouter", "z-ai/glm-5.3-flashx-legal", msgs, schema,
                                  route=providers.model_route("z-ai/glm-5.3-flashx-legal"))
    assert "seed" not in glm  # z-ai's endpoint does not list seed; require_parameters would reject it
    assert glm["provider"]["only"] == ["z-ai"] and glm["provider"]["require_parameters"] is True
    ds = providers.build_payload("openrouter", "deepseek/deepseek-v4.1-flash", msgs, schema,
                                 route=providers.model_route("deepseek/deepseek-v4.1-flash"))
    assert ds["seed"] == 42
    assert ds["provider"]["quantizations"] == ["fp8"] and ds["provider"]["allow_fallbacks"] is True
    assert set(ds["provider"]["max_price"]) == {"prompt", "completion"}
    for model, spec in models_registry()["models"].items():
        if spec["provider"] == "openrouter":
            assert spec.get("route", {}).get("only"), f"{model}: pin its providers in models.yaml"


def _catalog_fetch(drop_param=None):
    reg = models_registry()

    def fetch(url):
        if url == "https://openrouter.ai/api/v1/models":
            ids = [m for m, s in reg["models"].items() if s["provider"] == "openrouter"]
            ids += list(reg.get("comparison") or {})
            return {"data": [{"id": m, "context_length": 131072,
                              "pricing": {"prompt": "0.0000001", "completion": "0.0000002"}} for m in ids]}
        model = url.split("/models/", 1)[1].removesuffix("/endpoints")
        spec = reg["models"][model]
        route = spec.get("route") or {}
        params = set(operate.REQUIRED_PARAMETERS) | {"seed"}
        params.discard(drop_param)
        return {"data": {"endpoints": [{
            "tag": f"{route['only'][0]}/{(route.get('quantizations') or ['fp8'])[0]}",
            "quantization": (route.get("quantizations") or ["fp8"])[0], "context_length": 131072,
            "pricing": {"prompt": str(spec["prompt"] / 1e6), "completion": str(spec["completion"] / 1e6)},
            "supported_parameters": sorted(params)}]}}
    return fetch


def test_price_report_accepts_current_catalog_and_flags_missing_parameters():
    ok = operate.price_report(fetch=_catalog_fetch())
    assert ok["problems"] == []
    assert all(m["serving_endpoints"] for m in ok["models"].values())
    bad = operate.price_report(fetch=_catalog_fetch(drop_param="response_format"))
    assert bad["problems"] and all("no pinned endpoint" in p for p in bad["problems"])


# ---------------------------------------------------------------- concurrency and the budget

def test_budget_counts_in_flight_reservations():
    model = "deepseek/deepseek-v4.1-flash"
    worst = spend.token_cost(model, 1000, 1000)
    b = spend.Budget(cap_usd=worst * 2.5, max_wall_seconds=3600)
    r1, r2 = b.reserve(model, 1000, 1000), b.reserve(model, 1000, 1000)
    with pytest.raises(spend.SpendError, match="in flight"):
        b.reserve(model, 1000, 1000)  # two calls in flight already hold the headroom
    b.release(r1)
    b.settle(model, 1000, 10, None, reserved=r2)
    assert b.in_flight_usd == pytest.approx(0.0)
    b.reserve(model, 1000, 1000)


def test_parallel_workers_match_serial_run(tmp_path, rows):
    base_row = rows[2]
    many = [{**base_row, "filename": f"mail_{i}.txt", "document_id": f"m{i}"} for i in range(12)]
    req = tasks.build("correspondence", many[0])
    answer = {"fields": {f: {"value": None, "status": "not_found", "evidence": ""} for f in req.fields}}
    models = ["deepseek/deepseek-v4.1-flash", "minimax/minimax-m3.1-flash"]
    backend = providers.Backend("openrouter", "https://or/api/v1", "k")
    out = {}
    for workers in (1, 4):
        d = tmp_path / f"w{workers}"
        res = runner.run_chunk(many, task="correspondence", models=models, backend=backend, reasoning="off",
                               budget=spend.Budget(1.0, 3600), out_dir=d,
                               chat=_fake_chat(dict.fromkeys(models, answer)), workers=workers)
        assert res["calls"] == 24 and not res.get("errors")
        runner.review(many, "correspondence", d)
        out[workers] = sorted((r["document_id"], r["field"], r["decision"])
                              for r in export.read_csv(d / "fields.csv"))
    assert out[1] == out[4]


# ---------------------------------------------------------------- campaign wiring

def test_campaign_needs_order_and_output_dirs():
    camp = campaign("v10")
    ids = list(dict.fromkeys(str(c["id"]) for c in camp["chunks"]))
    approved = set(models_registry()["models"])
    seen_dirs = set()
    for c in camp["chunks"]:
        assert c["task"] in tasks.BUILDERS or c["task"] == "intent", c
        assert c["cap_usd"] > 0
        assert str(c["reasoning"]) in ("off", "low") or str(c["reasoning"]).startswith("winner_of_")
        for need in c.get("needs") or []:
            assert need in ids and ids.index(need) < ids.index(str(c["id"])), (c["id"], need)
        for m in c["models"]:
            assert m in approved or str(m).startswith("winner_of_"), (c["id"], m)
    for cid in ids:
        entries = operate.chunk_entries("v10", cid)
        for arm in (range(1, len(entries) + 1) if len(entries) > 1 else [None]):
            entry, a = operate.chunk_entry("v10", cid, arm)
            d = operate.chunk_dir(Path("out"), entry, a)
            assert d not in seen_dirs, d
            seen_dirs.add(d)
    assert sum(c["cap_usd"] for c in camp["chunks"]) <= 30.0


def test_winner_placeholders_resolve_from_approvals(tmp_path, monkeypatch):
    path = tmp_path / "approved.yaml"
    monkeypatch.setattr(operate, "approvals_path", lambda name: path)
    entry = next(c for c in campaign("v10")["chunks"] if any(str(m).startswith("winner_of_") for m in c["models"]))
    with pytest.raises(SystemExit, match="no approved arm"):
        operate.resolve("v10", entry)
    ref = next(str(m) for m in entry["models"] if str(m).startswith("winner_of_")).removeprefix("winner_of_")
    path.write_text(f"approved:\n- {{chunk: '{ref}', models: [z-ai/glm-5.3-flashx-legal], reasoning: low}}\n")
    models, reasoning = operate.resolve("v10", entry)
    assert "z-ai/glm-5.3-flashx-legal" in models and not any(str(m).startswith("winner_of_") for m in models)
    if str(entry["reasoning"]).startswith("winner_of_"):
        assert reasoning == "low"


def test_ping_sends_one_strict_request_per_model(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    sent = []

    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        sent.append((model, reasoning))
        return providers.Completion("{}", {"ok": True, "word": "mailroom"}, model, "P", 20, 8, 0, 0.00001,
                                    "stop", True)
    import argparse
    args = argparse.Namespace(campaign="v10", initials="JB", cap_usd=0.05, out=tmp_path)
    assert operate.cmd_ping(args, chat=chat, key_status=lambda b: {"limit_remaining": None}) == 0
    assert len(sent) == len(set(sent)) and ("z-ai/glm-5.3-flashx-legal", "off") in sent
    assert len(export.read_journal(tmp_path / "ping.jsonl")) == len(sent)
    with pytest.raises(spend.SpendError):
        operate.cmd_ping(argparse.Namespace(campaign="v10", initials="jb", cap_usd=0.05, out=tmp_path),
                         chat=chat, key_status=lambda b: {"limit_remaining": None})


# ---------------------------------------------------------------- registry validation and new classes

LEASE_DIR = ROOT / "src/mailroom_eda/labeling/new_doc_types/examples/commercial_lease"


def test_live_registry_validates():
    from mailroom_eda.labeling.registry import validate_registry
    assert validate_registry(fields_registry()) == []


def test_validator_reports_each_problem(tmp_path):
    from mailroom_eda.labeling.registry import validate_registry
    (tmp_path / "t.md").write_text("${field_list} ${doc_text_window} ${surprise}")
    reg = {"vocabularies": {}, "tasks": {"t": {"class": "widget", "prompt": "t"}, "u": {"class": "widget"}},
           "classes": {"widget": {"specialist": "widgets", "fields": {
               "a": {"type": "number", "source": "llm", "task": "t", "absence": "absent_ok", "description": "d"},
               "b": {"type": "label", "source": "llm", "task": "t", "absence": "sometimes"},
               "c": {"type": "name", "source": "llm", "task": "cuad", "absence": "absent_ok"}}}}}
    problems = "\n".join(validate_registry(reg, prompts_dir=tmp_path))
    for needle in ("specialist must be", "not a Dojo scoring type", "absence must be", "closed vocabulary",
                   "need a description", "labels 'contract'", "missing prompt u.md", "cannot fill: ['surprise']"):
        assert needle in problems, needle


def test_lease_example_checks_clean_and_leaves_registry_untouched():
    problems, samples = operate.check_class(LEASE_DIR)
    assert problems == []
    text = samples["lease_entities"]
    assert "- landlord (name):" in text and "One of: gross | modified_gross" in text
    assert "${" not in text
    assert "commercial_lease" not in fields_registry()["classes"]
    assert "lease_entities" not in tasks.BUILDERS
    assert prompt("extract_rules").text  # the live prompt cache still points at the live prompts


def test_check_class_reports_a_broken_folder(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "extract_rules.md").write_text("x")
    (tmp_path / "fields.yaml").write_text(
        "classes:\n  contract: {specialist: x_specialist, fields: {}}\n"
        "tasks:\n  cuad: {class: contract}\n")
    problems, _ = operate.check_class(tmp_path)
    text = "\n".join(problems)
    assert "classes.contract already exists" in text and "shadows a built-in task" in text
    assert "would overwrite a live prompt" in text
    assert operate.check_class(tmp_path / "missing")[0][0].endswith("missing")


def test_find_touchpoints_flags_registries(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "classes.py").write_text(
        'DOC = ["contract", "merger_agreement", "corporate_record"]\n')
    (tmp_path / "pkg" / "one.py").write_text('if row["expected"] == "correspondence":\n    pass\n')
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "skip.py").write_text('X = ["contract", "merger_agreement", "corporate_record"]\n')
    out = tmp_path / "report.md"
    script = ROOT / "src/mailroom_eda/labeling/new_doc_types/find_touchpoints.py"
    subprocess.run([sys.executable, str(script), "--repo", str(tmp_path), "--markdown", str(out)], check=True,
                   capture_output=True)
    text = out.read_text()
    assert "`pkg/classes.py`" in text.split("Class-specific code")[0]
    assert "`pkg/one.py`" in text.split("Class-specific code")[1]
    assert "skip.py" not in text
