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
    """A minimal stage row."""
    return {"filename": fn or f"{cls}_{sub}_1.txt", "expected": cls, "expected_subclass": sub,
            "doc_text": text, "gt_fields": gt or {}, "split": "test", **extra}


@pytest.fixture()
def rows():
    """One fixture row per class."""
    return [
        _row("contract", "Consulting Agreements", CONTRACT_TEXT, document_id="c1"),
        _row("merger_agreement", "all_cash", MERGER_TEXT, document_id="m1"),
        _row("correspondence", "attorney_demand", EMAIL_TEXT, document_id="e1"),
        _row("insurance_claim", "outpatient", CLAIM_TEXT, document_id="i1"),
        _row("corporate_record", "bylaws", CORP_TEXT, {"entity_name": "Acme Corp."}, document_id="k1"),
    ]


# ---------------------------------------------------------------- registry and Dojo parity

def test_fields_match_pinned_dojo_types():
    """Fields match pinned dojo types."""
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
    """Fields have valid policy and source."""
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
    """Corporate subclass fields follow dojo."""
    schemas = dojo_snapshot()["corporate_subclass_schemas"]
    for sub, schema in schemas.items():
        fields = class_fields("corporate_record", sub)
        for ent in schema.get("entities", []):
            assert ent["key"] in fields, (sub, ent["key"])
    with pytest.raises(KeyError):
        class_fields("corporate_record", "no_such_subclass")


def test_snapshot_pins_match_fields_yaml():
    """Snapshot pins match fields yaml."""
    snap = dojo_snapshot()
    pin = fields_registry()["dojo_pin"]
    assert (snap["dojo_version"], snap["dojo_commit"]) == (pin["version"], pin["commit"])


@pytest.mark.skipif(not DOJO_SRC.exists(), reason="vendored llm-dojo-scoring not present")
def test_snapshot_is_current_against_vendored_dojo():
    """Snapshot is current against vendored dojo."""
    py = Path("/home/user/local-mailroom-sandbox/.venv/bin/python")
    proc = subprocess.run([str(py if py.exists() else sys.executable),
                           str(ROOT / "scripts/labeling/snapshot_dojo.py"), "--dojo-src", str(DOJO_SRC),
                           "--check"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_models_have_prices_and_providers():
    """Models have prices and providers."""
    reg = models_registry()
    for name, spec in reg["models"].items():
        assert spec["provider"] in providers.PROVIDERS
        if spec["provider"] == "openrouter":
            assert spend.price(name)[0] > 0
    for name in reg.get("comparison", {}):
        assert spend.price(name)[1] > 0


def test_campaign_chunks_are_runnable():
    """Campaign chunks are runnable."""
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
    """Prompt render rejects missing and extra values."""
    p = Prompt("t", "Hello ${name}, from ${place}.")
    assert p.placeholders == {"name", "place"}
    assert p.render({"name": "A", "place": "B"}) == "Hello A, from B."
    with pytest.raises(KeyError):
        p.render({"name": "A"})
    with pytest.raises(ValueError):
        p.render({"name": "A", "place": "B", "x": 1})


def test_prompt_sha_is_content_hash():
    """Prompt sha is content hash."""
    assert Prompt("a", "x").sha256 != Prompt("a", "y").sha256
    assert prompt("extract_rules").sha256 == prompt("extract_rules").sha256


def test_plan_doc_quotes_prompts_verbatim():
    """Plan doc quotes prompts verbatim."""
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
    """Builders render every task."""
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
    """Intent pass b hides subject."""
    row = rows[2]
    a = tasks.build("intent", row, "a")
    b = tasks.build("intent", row, "b")
    assert "Q3 invoice" in a.messages[1]["content"]
    assert "Subject:" not in b.messages[1]["content"]


def test_cuad_repair_replays_first_answer(rows):
    """Cuad repair replays first answer."""
    req = tasks.build_cuad(rows[0], repair_problems=["Parties: not verbatim"], prior_answer='{"clauses": []}')
    assert req.variant == "repair"
    assert [m["role"] for m in req.messages] == ["system", "user", "assistant", "user"]
    assert req.messages[2]["content"] == '{"clauses": []}'
    assert req.prompt_sha256 != tasks.build_cuad(rows[0]).prompt_sha256


def test_gold_fields_are_only_asked_when_empty(rows):
    """Gold fields are only asked when empty."""
    corp = rows[4]
    assert "entity_name" not in tasks._task_fields(corp, "corporate")
    assert "entity_name" in tasks._task_fields({**corp, "gt_fields": {}}, "corporate")


def test_window_keeps_head_and_tail():
    """Window keeps head and tail."""
    text = "A" * 100 + "B" * 100
    shown, desc = tasks.window(text, 50, 20)
    assert shown.startswith("A" * 50) and shown.endswith("B" * 20)
    assert "first 50" in desc and "last 20" in desc
    assert tasks.window("short", 50, 20)[0] == "short"


def test_parse_completion_records_cached_prompt_tokens():
    """Cached prompt tokens are read from usage, so prompt caching can be verified per call."""
    comp = providers.parse_completion({"choices": [{"message": {"content": '{"a": 1}'}}],
                                       "usage": {"prompt_tokens": 900,
                                                 "prompt_tokens_details": {"cached_tokens": 640}}},
                                      used_schema=True)
    assert comp.cached_tokens == 640
    bare = providers.parse_completion({"choices": [{"message": {"content": '{"a": 1}'}}]}, used_schema=True)
    assert bare.cached_tokens == 0


def test_cuad_prompt_puts_static_text_before_the_document_header(rows):
    """Prompt caching: the user message opens with the static category list, so two
    documents share a prefix through the last category; per-document text comes after."""
    a, b = (tasks.build_cuad(r).messages for r in rows[:2])
    assert a[0] == b[0]
    ua, ub = a[1]["content"], b[1]["content"]
    header = ua.index("document_id:")
    assert ua.index("The 41 categories:") < header
    assert ua[:header] == ub[:ub.index("document_id:")]


def test_cuad_family_maps_every_contract_subclass():
    """Cuad family maps every contract subclass."""
    fams = {f["key"] for f in dojo_snapshot()["cuad_families"]}
    assert tasks.cuad_family("Consulting Agreements")["key"] in fams
    assert tasks.cuad_family("no such family") is None


# ---------------------------------------------------------------- verify

def test_locate_exact_and_folded():
    """Locate exact and folded."""
    doc = "The **Term** is\n\ntwo   years."
    assert verify.locate(doc, "two   years") == (doc.index("two"), doc.index("two") + 11)
    start, end = verify.locate(doc, "the term is two years")
    assert doc[start:end].startswith("The") and doc[start:end].endswith("years")
    assert verify.locate(doc, "three years") is None


def test_normalize_and_agree_by_type():
    """Normalize and agree by type."""
    assert verify.agree("March 5, 2004", "2004-03-05", "date")
    assert verify.agree("$1,500,000", "1.5 million", "money")
    assert verify.agree("Acme Corp.", "Acme Corp", "name")
    assert not verify.agree("Acme", "Beta", "name")
    assert verify.agree(["Acme Corp.", "Beta LLC"], ["Beta LLC", "Acme Corp"], "entity_list:name")
    assert not verify.agree(["Acme"], ["Acme", "Beta", "Gamma"], "entity_list:name")
    assert verify.agree("all_cash", "All Cash", "label")


def test_grounding_rejects_values_not_in_document():
    """Grounding rejects values not in document."""
    doc = verify.DocIndex.build(CONTRACT_TEXT)
    assert verify.grounded("Acme Corp.", "name", doc)
    assert not verify.grounded("Gamma Inc.", "name", doc)
    assert verify.grounded("2004-03-05", "date", doc)
    assert not verify.grounded("2011-03-05", "date", doc)
    assert verify.grounded("email", "label", doc, vocab=["email", "memo"])
    assert not verify.grounded("fax", "label", doc, vocab=["email", "memo"])


def test_check_field_and_decide():
    """Check field and decide."""
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
    """Check field coerces unknown status."""
    doc = verify.DocIndex.build(CONTRACT_TEXT)
    r = verify.check_field({"value": "x", "status": "maybe"}, "name", doc)
    assert r.status == "not_found" and r.value is None


# ---------------------------------------------------------------- providers (mocked HTTP)

def _ok(content, **usage):
    """A successful OpenRouter reply."""
    return 200, {}, {"model": "m-served", "provider": "P",
                     "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5, **usage}}


def test_payload_shapes_per_provider():
    """Payload shapes per provider."""
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
    """Backend from env."""
    b = providers.Backend.from_env("modal", {"VLLM_BASE_URL": "https://x.modal.run/", "VLLM_API_KEY": "sekrit-key"})
    assert b.base_url == "https://x.modal.run/v1"
    assert "sekrit" not in repr(b)
    with pytest.raises(providers.ProviderError):
        providers.Backend.from_env("openrouter", {})
    with pytest.raises(providers.ProviderError):
        providers.Backend.from_env("modal", {"VLLM_API_KEY": "sekrit-key"})


def test_chat_retries_with_retry_after_then_succeeds():
    """Chat retries with retry after then succeeds."""
    calls, waits = [], []
    replies = [(429, {"Retry-After": "7"}, {}), (503, {}, {}), _ok('{"a": 1}', cost=0.002)]

    def post(url, key, body, timeout):
        """Fake HTTP post."""
        calls.append(body)
        return replies[len(calls) - 1]

    b = providers.Backend("openrouter", "https://or/api/v1", "k")
    c = providers.chat(b, "a/b", [{"role": "user", "content": "x"}], {"type": "object"},
                       post=post, sleep=waits.append)
    assert c.parsed == {"a": 1} and c.cost_usd == 0.002 and c.served_model == "m-served"
    assert len(calls) == 3 and waits[0] == 7.0


def test_chat_falls_back_to_json_mode_on_schema_rejection():
    """Chat falls back to json mode on schema rejection."""
    seen = []

    def post(url, key, body, timeout):
        """Fake HTTP post."""
        seen.append(body["response_format"]["type"])
        return (400, {}, {"error": "schema"}) if len(seen) == 1 else _ok('{"ok": true}')

    b = providers.Backend("openrouter", "https://or/api/v1", "k")
    c = providers.chat(b, "a/b", [{"role": "user", "content": "x"}], {"type": "object"},
                       post=post, sleep=lambda s: None)
    assert seen == ["json_schema", "json_object"] and c.used_schema is False


def test_chat_network_errors_and_hard_failures():
    """Chat network errors and hard failures."""
    def boom(url, key, body, timeout):
        """Raise a network error."""
        raise urllib.error.URLError("down")

    b = providers.Backend("openrouter", "https://or/api/v1", "k")
    with pytest.raises(providers.ProviderError, match="gave up"):
        providers.chat(b, "a/b", [], None, post=boom, sleep=lambda s: None, attempts=2)
    with pytest.raises(providers.ProviderError, match="HTTP 401"):
        providers.chat(b, "a/b", [], None, post=lambda *a: (401, {}, {}), sleep=lambda s: None)


def test_parse_completion_rejects_truncated_and_non_json():
    """Parse completion rejects truncated and non json."""
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
    """Run flags need human initials cap and wall."""
    spend.require_run_flags(initials="JB", cap_usd=1.0, max_wall_seconds=60)
    for bad in ({"initials": None, "cap_usd": 1.0, "max_wall_seconds": 60},
                {"initials": "jb", "cap_usd": 1.0, "max_wall_seconds": 60},
                {"initials": "JB", "cap_usd": 0, "max_wall_seconds": 60},
                {"initials": "JB", "cap_usd": 1.0, "max_wall_seconds": None}):
        with pytest.raises(spend.SpendError):
            spend.require_run_flags(**bad)


def test_key_headroom_keeps_reserve():
    """Key headroom keeps reserve."""
    assert spend.check_key_headroom({"limit_remaining": None}, 50) == float("inf")
    assert spend.check_key_headroom({"limit_remaining": 10.0}, 5.0) == pytest.approx(7.0)
    with pytest.raises(spend.SpendError):
        spend.check_key_headroom({"limit_remaining": 4.0}, 2.0)


def test_budget_stops_before_the_cap():
    """Budget stops before the cap."""
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
    """Csv round trip escapes and rejects unknown."""
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
    """Journal ignores torn last line."""
    path = tmp_path / "calls.jsonl"
    export.append_journal(path, {"a": 1})
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"a": 2')
    assert export.read_journal(path) == [{"a": 1}]


# ---------------------------------------------------------------- runner

def test_chunk_slices_cover_everything():
    """Chunk slices cover everything."""
    items = list(range(10))
    parts = [runner.chunk(items, f"{i}/3") for i in (1, 2, 3)]
    assert sum(parts, []) == items and [len(p) for p in parts] == [4, 4, 2]
    with pytest.raises(SystemExit):
        runner.chunk(items, "4/3")


def test_estimate_prices_every_model(rows):
    """Estimate prices every model."""
    est = runner.estimate([rows[2]], "correspondence",
                          ["deepseek/deepseek-v4.1-flash", "minimax/minimax-m3.1-flash", "not/a-model"])
    assert est["calls"] == 1 and est["prompt_tokens"] > 0
    assert est["cost_usd"]["deepseek/deepseek-v4.1-flash"] > 0 and est["cost_usd"]["not/a-model"] is None


def _fake_chat(answers):
    """A chat stub that answers each model with a fixed JSON object."""
    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        """Fake chat call."""
        content = json.dumps(answers[model])
        return providers.Completion(content, answers[model], model, "P", 100, 50, 0, 0.0001, "stop", True)
    return chat


def test_run_and_review_two_model_extraction(tmp_path, rows):
    """Run and review two model extraction."""
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
    assert doc["cost_usd"] == "" and doc["document_tokens"] == str(spend.estimate_tokens(row["doc_text"]))
    assert doc["status"] == "needs_review" and doc["model"] == ",".join(models)
    assert summary["documents"] == 1


def test_run_stops_at_cap_and_keeps_work(tmp_path, rows):
    """Run stops at cap and keeps work."""
    row = rows[2]
    model = "deepseek/deepseek-v4.1-flash"
    res = runner.run_chunk([row], task="correspondence", models=[model],
                           backend=providers.Backend("openrouter", "u", "k"), reasoning="off",
                           budget=spend.Budget(1e-9, 3600), out_dir=tmp_path, chat=_fake_chat({}))
    assert res["stopped"] == 1 and "cap" in res["stop_reason"]
    assert not (tmp_path / "calls.jsonl").exists()


def test_cuad_run_repairs_and_review_writes_v94_records(tmp_path, rows):
    """Cuad run repairs and review writes v94 records."""
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
        """Fake chat call."""
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
    doc = export.read_csv(tmp_path / "documents.csv")[0]  # usage covers the first call and the repair
    assert (doc["prompt_tokens"], doc["completion_tokens"]) == ("200", "100")
    assert doc["cost_usd"] == ""  # cost stays in calls.jsonl, never in the CSVs
    assert doc["document_tokens"] == str(spend.estimate_tokens(row["doc_text"]))


def test_intent_review_pairs_two_passes(tmp_path, rows):
    """Intent review pairs two passes."""
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
    doc = export.read_csv(tmp_path / "documents.csv")[0]  # usage covers both passes
    assert (doc["prompt_tokens"], doc["completion_tokens"]) == ("200", "100")
    assert doc["cost_usd"] == ""  # cost stays in calls.jsonl, never in the CSVs
    assert doc["document_tokens"] == str(spend.estimate_tokens(row["doc_text"]))


def test_cli_refuses_paid_run_without_initials(tmp_path, monkeypatch, rows):
    """Cli refuses paid run without initials."""
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
    """Costs md is current."""
    stage = runner.DEFAULT_STAGE / "ground_truth_hardened.jsonl"
    if not stage.exists():
        pytest.skip("v9.4 stage not built (data/ is gitignored)")
    assert runner.main(["costs", "--check"]) == 0


def test_review_keeps_reviewer_verdicts(tmp_path, rows):
    """Review keeps reviewer verdicts."""
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


def test_merger_consideration_is_its_own_task(rows):
    """Merger consideration is its own task."""
    from mailroom_eda.labeling import derive

    m = rows[1]
    maud = {"Type of Consideration": {"answer": "All Cash"}}
    confirmed = {**m, "gt_fields": {"maud_clause_labels": json.dumps(maud)}}
    assert derive.merger_consideration(confirmed) == "all_cash"
    unknown = {**m, "expected_subclass": "other"}
    assert derive.merger_consideration(unknown) is None
    for r in (confirmed, unknown):  # merger_entities never asks it; the new task always does
        assert "merger_consideration" not in tasks.build("merger_entities", r).fields
        req = tasks.build("merger_consideration", r)
        assert list(req.fields) == ["merger_consideration"]
        assert "mixed_cash_stock_election" in req.messages[1]["content"]
    assert "consideration_line" not in prompt("merger_entities").placeholders
    picked = {s: [r["filename"] for r in runner.select([confirmed, {**unknown, "filename": "u.txt"}],
                                                        "merger_consideration", s)]
              for s in ("calibration", "pending")}
    assert picked == {"calibration": [confirmed["filename"]], "pending": ["u.txt"]}
    with pytest.raises(ValueError):
        derive.merger_consideration({**confirmed, "expected_subclass": "all_stock"})


def test_consideration_window_keeps_conversion_clause():
    """Consideration window keeps conversion clause."""
    filler = "Lorem ipsum dolor sit amet. " * 4000
    text = ("AGREEMENT AND PLAN OF MERGER\n" + filler + "\nARTICLE II\nEFFECT OF THE MERGER ON CAPITAL STOCK\n"
            "Each Share shall be converted into the right to receive $10.00 in cash.\n" + filler)
    win, desc = tasks.consideration_window(text)
    assert len(win) < 20_000 and "converted into the right to receive $10.00" in win
    assert "Conversion of Shares" in desc


def test_derived_subclasses_and_specialists(rows):
    """Derived subclasses and specialists."""
    from mailroom_eda.labeling import derive

    assert derive.canonical_subclass(rows[0]) == tasks.cuad_family("Consulting Agreements")["key"]
    assert derive.communication_type(rows[2]) == "attorney_demand"
    assert derive.communication_type({**rows[2], "expected_subclass": "fax"}) == "other"
    assert derive.specialist(rows[1]) == "merger_agreement_specialist"


# ---------------------------------------------------------------- OpenRouter routing (models.yaml)

def test_openrouter_routes_pin_providers_and_only_send_supported_seed():
    """Openrouter routes pin providers and only send supported seed."""
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
    """A fake OpenRouter catalog, optionally missing one parameter."""
    reg = models_registry()

    def fetch(url):
        """Fake catalog fetch."""
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
    """Price report accepts current catalog and flags missing parameters."""
    ok = operate.price_report(fetch=_catalog_fetch())
    assert ok["problems"] == []
    assert all(m["serving_endpoints"] for m in ok["models"].values())
    bad = operate.price_report(fetch=_catalog_fetch(drop_param="response_format"))
    assert bad["problems"] and all("no pinned endpoint" in p for p in bad["problems"])


# ---------------------------------------------------------------- concurrency and the budget

def test_budget_counts_in_flight_reservations():
    """Budget counts in flight reservations."""
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
    """Parallel workers match serial run."""
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
    """Campaign needs order and output dirs."""
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
    """Winner placeholders resolve from approvals."""
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
    """Ping sends one strict request per model."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    sent = []

    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        """Fake chat call."""
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

LEASE_DIR = ROOT / "src/mailroom_eda/labeling/integrations/examples/commercial_lease"


def test_live_registry_validates():
    """Live registry validates."""
    from mailroom_eda.labeling.registry import validate_registry
    assert validate_registry(fields_registry()) == []


def test_validator_reports_each_problem(tmp_path):
    """Validator reports each problem."""
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
    """Lease example checks clean and leaves registry untouched."""
    problems, samples = operate.check_class(LEASE_DIR)
    assert problems == []
    text = samples["lease_entities"]
    assert "- landlord (name):" in text and "One of: gross | modified_gross" in text
    assert "${" not in text
    assert "commercial_lease" not in fields_registry()["classes"]
    assert "lease_entities" not in tasks.BUILDERS
    assert prompt("extract_rules").text  # the live prompt cache still points at the live prompts


def test_check_class_reports_a_broken_folder(tmp_path):
    """Check class reports a broken folder."""
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
    """Find touchpoints flags registries."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "classes.py").write_text(
        'DOC = ["contract", "merger_agreement", "corporate_record"]\n')
    (tmp_path / "pkg" / "one.py").write_text('if row["expected"] == "correspondence":\n    pass\n')
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "skip.py").write_text('X = ["contract", "merger_agreement", "corporate_record"]\n')
    out = tmp_path / "report.md"
    script = ROOT / "src/mailroom_eda/labeling/integrations/find_touchpoints.py"
    subprocess.run([sys.executable, str(script), "--repo", str(tmp_path), "--markdown", str(out)], check=True,
                   capture_output=True)
    text = out.read_text()
    assert "`pkg/classes.py`" in text.split("Class-specific code")[0]
    assert "`pkg/one.py`" in text.split("Class-specific code")[1]
    assert "skip.py" not in text


# ---------------------------------------------------------------- PR 42 review regressions

def test_journal_keeps_records_with_unicode_line_separators(tmp_path):
    """Journal keeps records with unicode line separators."""
    path = tmp_path / "calls.jsonl"
    export.append_journal(path, {"document_id": "d1", "content": "a\u2028b\u2029c\u0085d"})
    export.append_journal(path, {"document_id": "d2"})
    assert [r["document_id"] for r in export.read_journal(path)] == ["d1", "d2"]
    assert export.read_journal(path)[0]["content"] == "a\u2028b\u2029c\u0085d"


def test_csv_cells_cannot_start_a_formula(tmp_path):
    """Csv cells cannot start a formula."""
    rows = [{"document_id": "d", "field": f"f{i}", "model_a_value": v}
            for i, v in enumerate(["=HYPERLINK(1)", "+1", "-2", "@x", "'q", "plain"])]
    export.write_csv(tmp_path / "x.csv", export.FIELD_COLUMNS, rows)
    raw = (tmp_path / "x.csv").read_text()
    assert '"=HYPERLINK' not in raw and '"\'=HYPERLINK' in raw
    back = export.read_csv(tmp_path / "x.csv")
    assert [r["model_a_value"] for r in back] == ["=HYPERLINK(1)", "+1", "-2", "@x", "'q", "plain"]


def test_field_accuracy_gate_tolerates_fields_with_no_scored_values(tmp_path, rows):
    """Field accuracy gate tolerates fields with no scored values."""
    row = rows[0]
    export.write_csv(tmp_path / "fields.csv", export.FIELD_COLUMNS, [])
    res = operate.gate_field_accuracy([row], tmp_path, 0.85)
    assert res["passed"] is False and all(v["accuracy"] is None for v in res["fields"].values())


def test_chunk_refuses_external_requires_without_ack(tmp_path, monkeypatch):
    """Chunk refuses external requires without ack."""
    monkeypatch.setattr(operate, "load_approvals", lambda name: [])
    import argparse

    args = argparse.Namespace(campaign="v10", id="5a", arm=None, dry_run=False, ack_requires=False,
                              provider="openrouter", model_map=[], stage=tmp_path, initials="JB",
                              cap_usd=None, out=tmp_path, workers=1)
    with pytest.raises(SystemExit, match="--ack-requires"):
        operate.cmd_chunk(args)
    lines = operate.next_chunks("v10", [{"chunk": c} for c in ("1", "2a", "2b", "2c", "3", "4")])
    assert any("5a" in line and "external" in line for line in lines)
    assert not any(line.startswith("next chunk: 5a") for line in lines)


def test_modal_fallback_maps_models_and_is_priced():
    """Modal fallback maps models and is priced."""
    modal = [m for m, s in models_registry()["models"].items() if s["provider"] == "modal"]
    assert len(modal) == 1
    assert operate.provider_models(["z-ai/glm-5.3-flashx-legal"], "modal") == modal
    with pytest.raises(SystemExit, match="second modal model"):
        operate.provider_models(["z-ai/glm-5.3-flashx-legal", "deepseek/deepseek-v4.1-flash"], "modal")
    with pytest.raises(SystemExit, match="model-map"):
        operate.provider_models(["a/b"], "modal", ["a/b=not-approved"])
    assert spend.token_cost(modal[0], 1_000_000, 250_000) > 0


def test_agreement_gate_blocks_a_partial_chunk(tmp_path, rows):
    """Agreement gate blocks a partial chunk."""
    row = rows[2]
    req = tasks.build("correspondence", row)
    ans = {"fields": {f: {"value": None, "status": "not_found", "evidence": ""} for f in req.fields}}
    models = ["deepseek/deepseek-v4.1-flash", "minimax/minimax-m3.1-flash"]
    runner.run_chunk([row], task="correspondence", models=models[:1],
                     backend=providers.Backend("openrouter", "u", "k"), reasoning="off",
                     budget=spend.Budget(1.0, 3600), out_dir=tmp_path, chat=_fake_chat({models[0]: ans}))
    runner.review([row], "correspondence", tmp_path)
    doc = tasks._ids(row)[0]
    res = operate.gate_agreement(tmp_path, True, {(doc, m) for m in models})
    assert res["unanswered_calls"] == 1 and res["passed"] is False


def test_billed_failures_are_settled_and_journaled(tmp_path, rows):
    """Billed failures are settled and journaled."""
    row = rows[2]
    model = "deepseek/deepseek-v4.1-flash"

    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        """Fake chat call."""
        raise providers.ProviderError("truncated", usage={"prompt_tokens": 1000, "completion_tokens": 500,
                                                           "cost_usd": 0.003})

    budget = spend.Budget(1.0, 3600)
    res = runner.run_chunk([row], task="correspondence", models=[model],
                           backend=providers.Backend("openrouter", "u", "k"), reasoning="off",
                           budget=budget, out_dir=tmp_path, chat=chat)
    assert res["errors"] == 1
    rec = export.read_journal(tmp_path / "calls.jsonl")[0]
    assert rec["error"] and rec["cost_usd"] > 0
    assert res["spent_usd"] > 0


def test_parse_completion_bills_and_rejects_non_object_json():
    """Parse completion bills and rejects non object json."""
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001}
    for content, finish in (("[1, 2]", "stop"), ("hi", "stop"), ("{", "length")):
        with pytest.raises(providers.ProviderError) as err:
            providers.parse_completion({"choices": [{"message": {"content": content}, "finish_reason": finish}],
                                        "usage": usage}, used_schema=False)
        assert err.value.usage and err.value.usage.get("prompt_tokens") == 10


def test_chat_converts_unreadable_bodies_to_provider_error():
    """Chat converts unreadable bodies to provider error."""
    import http.client

    def post(url, key, body, timeout):
        """Fake HTTP post."""
        raise http.client.IncompleteRead(b"{", 10)

    b = providers.Backend("openrouter", "https://or/api/v1", "k")
    with pytest.raises(providers.ProviderError, match="could not be read"):
        providers.chat(b, "a/b", [], None, post=post, sleep=lambda s: None)


def test_prompt_sha_changes_with_vocabulary_and_field_specs(rows, monkeypatch):
    """Prompt sha changes with vocabulary and field specs."""
    row = rows[2]
    before = tasks.build("correspondence", row).prompt_sha256
    reg = fields_registry()
    vocab = list(reg["vocabularies"]["urgency"])
    monkeypatch.setitem(reg["vocabularies"], "urgency", vocab + ["someday"])
    assert tasks.build("correspondence", row).prompt_sha256 != before


def test_resume_resends_after_a_prompt_change(tmp_path, rows):
    """Resume resends after a prompt change."""
    row = rows[2]
    req = tasks.build("correspondence", row)
    ans = {"fields": {f: {"value": None, "status": "not_found", "evidence": ""} for f in req.fields}}
    model = "deepseek/deepseek-v4.1-flash"
    kw = dict(task="correspondence", models=[model], backend=providers.Backend("openrouter", "u", "k"),
              reasoning="off", budget=spend.Budget(1.0, 3600), out_dir=tmp_path)
    runner.run_chunk([row], chat=_fake_chat({model: ans}), **kw)
    calls = export.read_journal(tmp_path / "calls.jsonl")
    calls[0]["prompt_sha256"] = "old"
    (tmp_path / "calls.jsonl").write_text(json.dumps(calls[0]) + "\n")
    again = runner.run_chunk([row], chat=_fake_chat({model: ans}), **kw)
    assert again["calls"] == 1


def test_failed_cuad_repair_is_retried(tmp_path, rows):
    """Failed cuad repair is retried."""
    row = {**rows[0], "id": "c1"}
    vocab = [c["name"] for c in dojo_snapshot()["cuad_categories"]]
    bad = {"clauses": [{"category": "Governing Law", "text": "governed by the laws of Ohio"}]}
    good = {"clauses": [{"category": "Governing Law", "text": "governed by the laws of the State of Delaware"}]}
    model = "z-ai/glm-5.3-flashx-legal"
    seen = []

    def failing(backend, m, messages, schema, *, reasoning, max_tokens):
        """First answer succeeds; the repair call fails."""
        seen.append(len(messages))
        if len(messages) > 2:
            raise providers.ProviderError("down")
        return providers.Completion(json.dumps(bad), bad, m, "P", 100, 50, 0, 0.0001, "stop", True)

    kw = dict(task="cuad", models=[model], backend=providers.Backend("openrouter", "u", "k"), reasoning="off",
              budget=spend.Budget(1.0, 3600), out_dir=tmp_path, vocab=vocab)
    runner.run_chunk([row], chat=failing, **kw)
    assert seen == [2, 4]
    seen.clear()

    def fixing(backend, m, messages, schema, *, reasoning, max_tokens):
        """Every call returns the fixed answer."""
        seen.append(len(messages))
        return providers.Completion(json.dumps(good), good, m, "P", 100, 50, 0, 0.0001, "stop", True)

    runner.run_chunk([row], chat=fixing, **kw)
    assert seen == [4]  # only the repair is re-sent


def test_review_applies_reviewer_verdicts(tmp_path, rows):
    """Review applies reviewer verdicts."""
    row = rows[2]
    req = tasks.build("correspondence", row)
    base = {f: {"value": None, "status": "not_found", "evidence": ""} for f in req.fields}
    a = {"fields": {**base, "urgency": {"value": "time-sensitive", "status": "present", "evidence": "by Friday"}}}
    b = {"fields": {**base, "urgency": {"value": "urgent", "status": "present", "evidence": "by Friday"}}}
    models = ["deepseek/deepseek-v4.1-flash", "minimax/minimax-m3.1-flash"]
    runner.run_chunk([row], task="correspondence", models=models, backend=providers.Backend("openrouter", "u", "k"),
                     reasoning="off", budget=spend.Budget(1.0, 3600), out_dir=tmp_path,
                     chat=_fake_chat(dict(zip(models, (a, b)))))
    runner.review([row], "correspondence", tmp_path)
    for verdict, extra, decision, value in (("b", {}, "accept", "urgent"),
                                            ("value", {"reviewer_value": "routine"}, "accept", "routine"),
                                            ("reject", {}, "unlabeled", "")):
        adj = export.read_csv(tmp_path / "adjudication.csv")
        adj[0].update({"reviewer_verdict": verdict, **extra})
        export.write_csv(tmp_path / "adjudication.csv", export.FIELD_COLUMNS, adj)
        runner.review([row], "correspondence", tmp_path)
        f = {r["field"]: r for r in export.read_csv(tmp_path / "fields.csv")}["urgency"]
        assert (f["final_decision"], f["final_value"]) == (decision, value)


def test_run_flags_reject_nan_and_infinite_limits():
    """Run flags reject nan and infinite limits."""
    for cap, wall in ((float("nan"), 60), (float("inf"), 60), (1.0, float("nan")), (1.0, float("inf"))):
        with pytest.raises(spend.SpendError):
            spend.require_run_flags(initials="JB", cap_usd=cap, max_wall_seconds=wall)


def test_corporate_row_without_subclass_gets_other_fields():
    """Corporate row without subclass gets other fields."""
    row = _row("corporate_record", "", CORP_TEXT, document_id="k0")
    assert set(tasks._task_fields(row, "corporate")) == set(tasks._task_fields(
        {**row, "expected_subclass": "other"}, "corporate"))


def test_fold_keeps_offsets_when_lowercase_expands():
    """Fold keeps offsets when lowercase expands."""
    doc = "İstanbul Holdings A.Ş. agrees"
    folded, idx = verify.fold(doc)
    assert len(folded) == len(idx)
    assert verify.locate(doc, "holdings a.ş. AGREES") == (doc.index("Holdings"), len(doc))


def test_money_keeps_sign_and_decimals():
    """Money keeps sign and decimals."""
    assert verify.parse_money("-$75") == -75 and verify.parse_money("($1,250.50)") == -1250.5
    doc = verify.DocIndex.build("The fee is $12.50 per share and $1,000.00 in total.")
    assert verify.grounded("$12.50", "money", doc)
    assert verify.grounded("$1,000", "money", doc)
    assert not verify.grounded("$12", "money", doc)
    assert not verify.grounded("$12.99", "money", doc)


def test_dates_need_a_full_written_match():
    """Dates need a full written match."""
    doc = verify.DocIndex.build("This Agreement is dated as of the 5th day of March, 2004. Term ends 3/31/2006.")
    assert verify.grounded("2004-03-05", "date", doc)
    assert verify.grounded("March 31, 2006", "date", doc)
    assert not verify.grounded("2004-03-17", "date", doc)
    assert not verify.grounded("--", "date", doc)
    assert not verify.grounded("...", "name", doc)


def test_never_absent_field_quoted_as_absent_goes_to_adjudication():
    """Never absent field quoted as absent goes to adjudication."""
    doc = verify.DocIndex.build("No intent is stated in this letter.")
    raw = {"value": None, "status": "documented_absent", "evidence": "No intent is stated"}
    a = verify.check_field(raw, "label", doc)
    assert verify.decide(a, a, "label", "never_absent")[0] == "adjudicate"
    assert verify.decide(a, a, "label", "absent_ok")[0] == "absent"


def test_value_must_appear_in_its_own_evidence():
    """Value must appear in its own evidence."""
    doc = verify.DocIndex.build("Parent shall pay $25,000,000. The Company shall pay $10,000,000.")
    ok = verify.check_field({"value": "$25,000,000", "status": "present", "evidence": "Parent shall pay $25,000,000"},
                            "money", doc)
    bad = verify.check_field({"value": "$25,000,000", "status": "present",
                              "evidence": "The Company shall pay $10,000,000"}, "money", doc)
    assert ok.usable and not bad.usable


def test_contract_calibration_hides_expert_span_hints(rows):
    """Contract calibration hides expert span hints."""
    labels = {"Parties": [{"text": "Acme Corp."}], "Governing Law": [{"text": "laws of the State of Delaware"}]}
    row = {**rows[0], "gt_fields": {"cuad_clause_labels": json.dumps(labels)}}
    assert "Expert CUAD spans" in tasks.build("contract_entities", row, set_name="pilot").messages[1]["content"]
    cal = tasks.build("contract_entities", row, set_name="calibration")
    assert "Expert CUAD spans" not in cal.messages[1]["content"]


def test_consideration_gate_scores_models_against_maud(tmp_path, rows):
    """Consideration gate scores models against maud."""
    m = rows[1]
    rws = []
    for i, ans in enumerate(["All Cash", "All Stock"]):
        rws.append({**m, "document_id": f"m{i}", "filename": f"m{i}.txt",
                    "gt_fields": {"maud_clause_labels": json.dumps({"Type of Consideration": {"answer": ans}})}})
    models = ["z-ai/glm-5.3-flashx-legal", "deepseek/deepseek-v4.1-flash"]
    ev = "Each Share shall be converted into the right to receive $12.50 in cash."

    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        """Fake chat call."""
        val = "all_cash" if model == models[0] or "m0" in messages[1]["content"] else "all_stock"
        a = {"fields": {"merger_consideration": {"value": val, "status": "present", "evidence": ev}}}
        return providers.Completion(json.dumps(a), a, model, "P", 100, 50, 0, 0.0001, "stop", True)

    runner.run_chunk(rws, task="merger_consideration", models=models,
                     backend=providers.Backend("openrouter", "u", "k"), reasoning="off",
                     budget=spend.Budget(1.0, 3600), out_dir=tmp_path, chat=chat, set_name="calibration")
    runner.review(rws, "merger_consideration", tmp_path)
    res = operate.gate_consideration_accuracy(rws, tmp_path, 0.95, models)
    assert res["models"][models[0]]["accuracy"] == 0.5
    assert res["models"][models[0]]["confusion"]["all_stock"] == {"allcash": 1}
    assert res["passed"] is False


def test_consideration_chunks_gate_the_merger_entities_chunks():
    """Consideration chunks gate the merger entities chunks."""
    chunks = {str(c["id"]): c for c in campaign("v10")["chunks"]}
    assert chunks["6c0"]["gate"]["kind"] == "consideration_accuracy"
    assert chunks["6c1"]["needs"] == ["6c0"]
    for cid in ("6a", "6b", "6c"):
        assert "6c1" in chunks[cid]["needs"]


def test_find_touchpoints_extra_classes_nested_data_and_missing_repo(tmp_path):
    """Find touchpoints extra classes nested data and missing repo."""
    (tmp_path / "schemas" / "data").mkdir(parents=True)
    (tmp_path / "schemas" / "data" / "status.json").write_text(
        '{"contract": 1, "merger_agreement": 2, "corporate_record": 3}\n')
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "lease.py").write_text('if row["expected"] == "commercial_lease":\n    pass\n')
    script = ROOT / "src/mailroom_eda/labeling/integrations/find_touchpoints.py"
    out = tmp_path / "r.md"
    subprocess.run([sys.executable, str(script), "--repo", str(tmp_path), "--class", "commercial_lease",
                    "--markdown", str(out)], check=True, capture_output=True)
    text = out.read_text()
    assert "schemas/data/status.json" in text and "pkg/lease.py" in text
    missing = subprocess.run([sys.executable, str(script), "--repo", str(tmp_path / "nope")], capture_output=True)
    assert missing.returncode == 1


def _write(path, text):
    """Write ``text`` to ``path``, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_check_lockstep_reports_drift(tmp_path):
    """Check lockstep reports drift."""
    eda, mr, sb = tmp_path / "eda", tmp_path / "mr", tmp_path / "sb"
    sha = "1cd4aba76d82eea20c6e8dfcc0d760881bf84924"
    _write(eda / "src/mailroom_eda/labeling/fields.yaml", f'dojo_pin: {{version: "0.28.0", commit: "{sha}"}}\n')
    _write(eda / "src/mailroom_eda/config.py", 'REPO_TAG = "v10"\n')
    _write(mr / "pyproject.toml", '"llm-dojo-scoring @ git+https://github.com/x/llm-dojo-scoring.git@v0.28.0",\n')
    _write(mr / "src/pipeline/hf_corpora.py", 'FULL_CORPUS_REVISION = "v10"\n"docclass-merged": {\n "n_docs": 3292,\n')
    _write(sb / "src/mailroom_sandbox/cli.py",
           f'    ("llm-dojo-scoring", "v0.28.0",\n     "https://github.com/x/llm-dojo-scoring.git", "{sha}"),\n')
    _write(sb / "requirements/dojo-release.txt", f"llm-dojo-scoring @ git+https://github.com/x/llm-dojo-scoring.git@{sha}\n")
    _write(sb / "vendor/llm-dojo-scoring/src/llm_dojo_scoring/__init__.py", '__version__ = "0.28.0"\n')
    _write(sb / "src/mailroom_sandbox/job/spec.py", 'FAMILY_HF_TAG = "v10"\nFAMILY_CORPUS_SIZE = 3292\n')
    script = ROOT / "src/mailroom_eda/labeling/integrations/check_lockstep.py"
    args = [sys.executable, str(script), "--eda", str(eda), "--mailroom", str(mr), "--sandbox", str(sb)]
    assert subprocess.run(args + ["--rows", "3292"], capture_output=True).returncode == 0
    _write(sb / "src/mailroom_sandbox/job/spec.py", 'FAMILY_HF_TAG = "v9.1"\nFAMILY_CORPUS_SIZE = 3302\n')
    lag = subprocess.run(args, capture_output=True, text=True)
    assert lag.returncode == 1 and "Dataset tag" in lag.stderr and "Corpus size" in lag.stderr


def test_integration_docs_exist_and_old_folder_points_to_them():
    """Integration docs exist and old folder points to them."""
    base = ROOT / "src/mailroom_eda/labeling"
    for name in ("README.md", "GUIDE_new_doc_type.md", "CHECKLIST_new_doc_type.md",
                 "CHECKLIST_expanded_labeling.md", "CANDIDATES.md"):
        assert (base / "integrations" / name).is_file(), name
    assert "integrations" in (base / "new_doc_types/README.md").read_text()
    assert "CC BY-NC-SA 4.0" not in (base / "integrations/examples/commercial_lease/README.md").read_text()


# ---------------------------------------------------------------- second CodeRabbit round

def test_field_accuracy_gate_keeps_plain_strings_that_start_with_a_bracket(tmp_path, rows):
    """Field accuracy gate keeps plain strings that start with a bracket."""
    labels = {"Document Name": [{"text": "[Draft] Consulting Agreement"}]}
    row = {**rows[0], "gt_fields": {"cuad_clause_labels": json.dumps(labels)}}
    field = next(k for k, v in operate.CUAD_GOLD_FIELDS.items() if v == "Document Name")
    export.write_csv(tmp_path / "fields.csv", export.FIELD_COLUMNS, [
        {"document_id": tasks._ids(row)[0], "field": field, "type": "name", "decision": "accept",
         "final_decision": "accept", "final_value": "[Draft] Consulting Agreement"}])
    res = operate.gate_field_accuracy([row], tmp_path, 0.85)
    assert res["fields"][field]["correct"] == 1


def test_gate_scores_the_models_the_chunk_ran(tmp_path, monkeypatch, rows):
    """Gate scores the models the chunk ran."""
    import argparse

    entry = {"id": "7a", "task": "correspondence", "set": "pilot", "models": ["deepseek/deepseek-v4.1-flash"],
             "reasoning": "off", "cap_usd": 0.25, "gate": {"kind": "human_verdicts", "precision": 0.9}}
    modal = next(m for m, s in models_registry()["models"].items() if s["provider"] == "modal")
    row = rows[2]
    monkeypatch.setattr(runner, "load_stage", lambda stage: [row])
    monkeypatch.setattr(operate, "chunk_entry", lambda name, cid, arm: (entry, None))
    out_dir = operate.chunk_dir(tmp_path, entry, None)
    req = tasks.build("correspondence", row)
    ans = {"fields": {f: {"value": None, "status": "not_found", "evidence": ""} for f in req.fields}}
    runner.run_chunk([row], task="correspondence", models=[modal], backend=providers.Backend("modal", "u", "k"),
                     reasoning="off", budget=spend.Budget(1.0, 3600), out_dir=out_dir, chat=_fake_chat({modal: ans}))
    export.append_journal(out_dir / "runs.jsonl", {"chunk": "7a", "models": [modal], "provider": "modal",
                                                   "started_utc": "2026-11-06T00:00:00Z"})
    res = operate.gate_result("v10", entry, None, tmp_path, tmp_path)
    assert res["models"] == [modal] and res["provider"] == "modal"
    args = argparse.Namespace(campaign="v10", id="7a", arm=None, out=tmp_path, stage=tmp_path, signed_off=False,
                              provider="openrouter", approve=False, initials=None, replace=False)
    with pytest.raises(SystemExit, match="ran on modal"):
        operate.cmd_gate(args)


def test_list_items_check_their_vocabulary_part():
    """List items check their vocabulary part."""
    doc = verify.DocIndex.build("Claim CLM-1. Deductible $500 applies. Acme Corp. licenses software to Beta LLC.")
    cats = vocabulary("claim_checklist_category")
    assert verify.grounded(["Deductible: $500 applies"], "entity_list:free_text", doc, vocab=cats, vocab_part="head")
    assert not verify.grounded(["Made Up: $500 applies"], "entity_list:free_text", doc, vocab=cats, vocab_part="head")
    roles = vocabulary("contract_party_role")
    assert verify.grounded(["Acme Corp.: licensor"], "entity_list:free_text", doc, vocab=roles, vocab_part="tail")
    assert not verify.grounded(["Acme Corp.: wizard"], "entity_list:free_text", doc, vocab=roles, vocab_part="tail")
    assert not verify.grounded(["Acme Corp."], "entity_list:free_text", doc, vocab=roles, vocab_part="tail")
    spec = class_fields("insurance_claim")["claim_checklist"]
    assert spec["vocabulary_part"] == "head"


def test_validator_rejects_vocabulary_part_without_vocabulary():
    """Validator rejects vocabulary part without vocabulary."""
    import copy

    reg = copy.deepcopy(fields_registry())
    reg["classes"]["contract"]["fields"]["document_name"]["vocabulary_part"] = "head"
    problems = __import__("mailroom_eda.labeling.registry", fromlist=["validate_registry"]).validate_registry(reg)
    assert any("vocabulary_part" in p for p in problems)


def test_find_touchpoints_escapes_class_keys_and_reads_fields_yaml():
    """Find touchpoints escapes class keys and reads fields yaml."""
    sys.path.insert(0, str(ROOT / "src/mailroom_eda/labeling/integrations"))
    try:
        import find_touchpoints as ft
    finally:
        sys.path.pop(0)
    pats = ft.patterns({"a.b": ""})
    assert pats["a.b"].search('"a.b"') and not pats["a.b"].search('"axb"')
    assert set(ft.registry_classes()) == {"contract", "merger_agreement", "corporate_record", "correspondence",
                                          "insurance_claim"}


def test_check_lockstep_names_missing_files(tmp_path):
    """Check lockstep names missing files."""
    for d in ("eda", "mr", "sb"):
        (tmp_path / d).mkdir()
    script = ROOT / "src/mailroom_eda/labeling/integrations/check_lockstep.py"
    res = subprocess.run([sys.executable, str(script), "--eda", str(tmp_path / "eda"), "--mailroom",
                          str(tmp_path / "mr"), "--sandbox", str(tmp_path / "sb")], capture_output=True, text=True)
    assert res.returncode == 1 and "missing:" in res.stderr and "job/spec.py" in res.stderr


def test_reasoning_calls_get_a_reasoning_allowance_on_top_of_the_answer_budget(tmp_path, rows):
    """Reasoning tokens count against max_tokens, so a reasoning call must not be cut off."""
    assert runner.max_tokens("intent", "off") == runner.MAX_TOKENS["intent"]
    assert runner.max_tokens("intent", "low") == runner.MAX_TOKENS["intent"] + runner.REASONING_ALLOWANCE
    seen = []

    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        """Fake chat call that records the budget it was given."""
        seen.append(max_tokens)
        ans = {"intent": "update", "confidence": 0.9, "evidence": "x"}
        return providers.Completion(json.dumps(ans), ans, model, "P", 100, 50, 0, 0.0001, "stop", True)

    runner.run_chunk([rows[2]], task="intent", models=["deepseek/deepseek-v4.1-flash"],
                     backend=providers.Backend("openrouter", "u", "k"), reasoning="low",
                     budget=spend.Budget(1.0, 3600), out_dir=tmp_path, chat=chat)
    assert seen and set(seen) == {runner.MAX_TOKENS["intent"] + runner.REASONING_ALLOWANCE}


def test_identical_answers_from_other_journals_are_reused_not_bought_again(tmp_path, rows):
    """An answer already paid for under the labels root, with the same configuration, is copied."""
    ans = {"intent": "update", "confidence": 0.9, "evidence": "x"}
    common = dict(task="intent", models=["deepseek/deepseek-v4.1-flash"],
                  backend=providers.Backend("openrouter", "u", "k"), budget=spend.Budget(1.0, 3600))
    runner.run_chunk([rows[2]], reasoning="off", out_dir=tmp_path / "smoke" / "a",
                     chat=_fake_chat({"deepseek/deepseek-v4.1-flash": ans}), **common)

    def no_call(*a, **k):
        """Any paid call here is a failure."""
        raise AssertionError("a reusable answer was bought again")

    res = runner.run_chunk([rows[2]], reasoning="off", out_dir=tmp_path / "3_intent", chat=no_call,
                           reuse_from=[tmp_path], **common)
    assert res.get("calls", 0) == 0 and res["reused"] == 2  # passes a and b
    recs = export.read_journal(tmp_path / "3_intent" / "calls.jsonl")
    assert len(recs) == 2 and all(r["reused_from"].endswith("smoke/a/calls.jsonl") for r in recs)
    runner.review([rows[2]], "intent", tmp_path / "3_intent")
    assert export.read_journal(tmp_path / "3_intent" / "intent-labels.jsonl")[0]["pass_a"]["intent"] == "update"


def test_answers_from_a_different_configuration_are_not_reused(tmp_path, rows):
    """A different reasoning level is a different configuration, so it is sent again."""
    ans = {"intent": "update", "confidence": 0.9, "evidence": "x"}
    common = dict(task="intent", models=["deepseek/deepseek-v4.1-flash"],
                  backend=providers.Backend("openrouter", "u", "k"), budget=spend.Budget(1.0, 3600),
                  chat=_fake_chat({"deepseek/deepseek-v4.1-flash": ans}))
    runner.run_chunk([rows[2]], reasoning="off", out_dir=tmp_path / "a", **common)
    res = runner.run_chunk([rows[2]], reasoning="low", out_dir=tmp_path / "b", reuse_from=[tmp_path], **common)
    assert res["calls"] == 2 and res.get("reused", 0) == 0


def test_review_scores_only_answers_to_the_current_prompt(tmp_path, rows):
    """After a prompt edit, answers to the old prompt stay in the journal but are not scored."""
    row = rows[2]
    new = {"intent": "update", "confidence": 0.9, "evidence": "x"}
    runner.run_chunk([row], task="intent", models=["deepseek/deepseek-v4.1-flash"],
                     backend=providers.Backend("openrouter", "u", "k"), reasoning="off",
                     budget=spend.Budget(1.0, 3600), out_dir=tmp_path,
                     chat=_fake_chat({"deepseek/deepseek-v4.1-flash": new}))
    journal = export.read_journal(tmp_path / "calls.jsonl")
    stale = [{**c, "prompt_sha256": "0" * 64, "content": json.dumps({**new, "intent": "notice"}),
              "response": {**new, "intent": "notice"}, "labeled_utc": "2099-01-01T00:00:00Z"} for c in journal]
    for c in stale:  # appended last, so a naive "latest answer wins" review would pick them
        export.append_journal(tmp_path / "calls.jsonl", c)
    runner.review([row], "intent", tmp_path)
    (rec,) = export.read_journal(tmp_path / "intent-labels.jsonl")
    assert rec["pass_a"]["intent"] == rec["pass_b"]["intent"] == "update"
    assert len(export.read_journal(tmp_path / "calls.jsonl")) == 4  # nothing deleted


def test_funding_status_compares_usable_credit_with_the_remaining_caps():
    """Usable credit (after the reserve) must cover the next chunk; a shortfall on the rest is reported."""
    chunks = [{"id": "1", "cap_usd": 0.75}, {"id": "1", "cap_usd": 0.40}, {"id": "3", "cap_usd": 2.0}]
    ok, msg = operate.funding_status(8.0, 3.0, chunks, approved={"1"})
    assert ok and "$5.00 usable" in msg and "$2.00 in remaining caps" in msg and "short" not in msg
    ok, msg = operate.funding_status(4.2, 3.0, chunks, approved=set())
    assert ok and "short by $1.95" in msg  # both arms of chunk 1 ($1.15) fit; the rest does not
    ok, msg = operate.funding_status(3.5, 3.0, chunks, approved=set())
    assert not ok and "next chunk 1 needs $1.15" in msg
    ok, msg = operate.funding_status(None, 3.0, chunks, approved=set())
    assert ok and "no credit limit" in msg


def test_ping_problems_need_a_recent_passing_ping_per_route():
    """Each (model, reasoning) a chunk uses needs a passing ping within the window; the latest wins."""
    from datetime import datetime, timezone
    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    ok = {"response": {"ok": True, "word": "mailroom"}, "used_schema": True}
    log = [
        {"model": "a", "reasoning": "off", "utc": "2026-11-06T06:00:00Z", **ok},
        {"model": "b", "reasoning": "off", "utc": "2026-11-06T06:00:00Z", **ok},
        {"model": "b", "reasoning": "off", "utc": "2026-11-06T07:00:00Z", "error": "HTTP 400: Reasoning is mandatory"},
        {"model": "c", "reasoning": "low", "utc": "2026-11-04T06:00:00Z", **ok},
    ]
    probs = operate.ping_problems(log, [("a", "off"), ("b", "off"), ("c", "low"), ("d", "off")], now=now)
    assert len(probs) == 3
    assert any("b reasoning=off" in p and "Reasoning is mandatory" in p for p in probs)
    assert any("c reasoning=low" in p and "older than 24h" in p for p in probs)
    assert any("d reasoning=off" in p and "never pinged" in p for p in probs)


def test_chunk_refuses_a_paid_run_on_an_unpinged_route(tmp_path, monkeypatch):
    """A paid chunk run stops before any call when a route has no recent passing ping."""
    import argparse
    monkeypatch.setattr(operate, "load_approvals", lambda name: [])
    args = argparse.Namespace(campaign="v10", id="3", arm=1, dry_run=False, ack_requires=False,
                              provider="openrouter", model_map=[], stage=tmp_path, initials="JB",
                              cap_usd=None, out=tmp_path, workers=1)
    with pytest.raises(SystemExit, match="labeling ping"):
        operate.cmd_chunk(args)


def test_route_can_pin_providers_per_reasoning_level():
    """by_reasoning narrows the provider pin for one reasoning level and leaves the others alone."""
    route = {"only": ["deepinfra", "coreweave", "inference-net"], "seed": True,
             "by_reasoning": {"low": {"only": ["coreweave"]}}}
    low = providers.build_payload("openrouter", "m", [{"role": "user", "content": "x"}], None,
                                  reasoning="low", route=route)
    off = providers.build_payload("openrouter", "m", [{"role": "user", "content": "x"}], None,
                                  reasoning="off", route=route)
    assert low["provider"]["only"] == ["coreweave"]
    assert off["provider"]["only"] == ["deepinfra", "coreweave", "inference-net"]
    assert "by_reasoning" not in low["provider"]


def test_served_providers_outside_the_pin_are_named():
    """Served display names (CoreWeave, Z.AI) are matched to route slugs (coreweave, z-ai)."""
    route = {"only": ["deepinfra", "coreweave", "inference-net", "z-ai"],
             "by_reasoning": {"low": {"only": ["coreweave"]}}}
    assert providers.served_outside_route(route, "off", {"CoreWeave", "InferenceNet", "Z.AI"}) == []
    assert providers.served_outside_route(route, "low", {"CoreWeave", "DeepInfra"}) == ["DeepInfra"]


def test_pin_violations_list_answers_served_outside_their_route(monkeypatch):
    """A gate fails when any scored answer was served by a provider outside its model's pin."""
    monkeypatch.setattr(providers, "model_route", lambda m: {"only": ["coreweave", "deepinfra"],
                                                            "by_reasoning": {"low": {"only": ["coreweave"]}}})
    calls = [{"model": "m", "served_provider": "CoreWeave"}, {"model": "m", "served_provider": "DeepInfra"}]
    assert operate.pin_violations(calls, "off") == []
    assert operate.pin_violations(calls, "low") == ["m via DeepInfra"]


def test_ping_per_provider_pins_each_provider_and_records_reasoning_tokens(tmp_path, monkeypatch):
    """--per-provider sends one request pinned to each provider of a route and logs its reasoning tokens."""
    import argparse
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(operate, "campaign", lambda name: {"chunks": [
        {"id": "3", "models": ["deepseek/deepseek-v4.1-flash"], "reasoning": "low"}]})
    sent = []

    def chat(backend, model, messages, schema, *, reasoning, max_tokens, route=None):
        """Fake chat: reasoning only on coreweave."""
        sent.append((model, reasoning, tuple((route or {}).get("only") or ())))
        rt = 200 if route and route.get("only") == ["coreweave"] else 0
        return providers.Completion('{"ok": true, "word": "mailroom"}', {"ok": True, "word": "mailroom"},
                                    model, (route or {}).get("only", ["?"])[0], 20, 5, rt, 0.00001, "stop", True)

    args = argparse.Namespace(campaign="v10", initials="JB", cap_usd=0.05, out=tmp_path, per_provider=True)
    assert operate.cmd_ping(args, chat=chat, key_status=lambda b: {"limit_remaining": 10}) == 0
    pinned = [s for s in sent if s[0] == "deepseek/deepseek-v4.1-flash" and s[1] == "low" and len(s[2]) == 1]
    assert {s[2][0] for s in pinned} == {"atlas-cloud", "coreweave", "inference-net"}  # low pin excludes deepinfra
    log = export.read_journal(tmp_path / "ping.jsonl")
    cw = [r for r in log if r.get("pinned") == "coreweave" and r["reasoning"] == "low"]
    assert cw and cw[0]["reasoning_tokens"] == 200
    # per-provider records never decide whether the route as a whole is verified
    assert operate.ping_problems([r for r in log if r.get("pinned")], [("deepseek/deepseek-v4.1-flash", "low")])
