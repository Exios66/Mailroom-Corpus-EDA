"""Offline tests for the v10 labeling package (no network: HTTP is mocked)."""
from __future__ import annotations

import json
from collections import Counter
import re
import subprocess
import sys
import urllib.error
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mailroom_eda.labeling import export, ingest, operate, providers, runner, spend, tasks, verify
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
            if spec.get("subclass_entity"):
                continue
            assert "dojo" not in spec and "retype" not in spec, (cls, name)  # G1 shipped in 0.28.0
            assert name in types, f"{cls}.{name} is not a Dojo field"
            assert spec["type"] == types[name], (cls, name, spec["type"], types[name])
        assert set(types) == {n for n, s in fields.items() if not s.get("subclass_entity")}, cls


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
        assert (ch["cap_usd"] > 0 or ch.get("skip")) and "kind" in ch["gate"]
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


def test_key_headroom_is_the_credit_left():
    """Key headroom is the credit left (no reserve); a cap above it is refused."""
    assert spend.check_key_headroom({"limit_remaining": None}, 50) == float("inf")
    assert spend.check_key_headroom({"limit_remaining": 10.0}, 5.0) == pytest.approx(10.0)
    with pytest.raises(spend.SpendError):
        spend.check_key_headroom({"limit_remaining": 4.0}, 5.0)


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


def _pin_served(model: str, reasoning: str) -> str:
    """A served-provider name inside ``model``'s route pin, so journal entries count as done."""
    only = providers.effective_route(providers.model_route(model), reasoning).get("only") or []
    return str(only[0]) if only else "synthetic"


def _fake_chat(answers):
    """A chat stub that answers each model with a fixed JSON object."""
    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        """Fake chat call."""
        content = json.dumps(answers[model])
        return providers.Completion(content, answers[model], model, _pin_served(model, reasoning),
                                    100, 50, 0, 0.0001, "stop", True)
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
            "Section 2.1 Conversion of Shares. Each Share issued and outstanding immediately prior to the "
            "Effective Time shall be converted into the right to receive $10.00 in cash.\n" + filler)
    win, desc = tasks.consideration_window(text)
    assert len(win) <= tasks.CONSIDERATION_WINDOW_CAP and "converted into the right to receive $10.00" in win
    assert "Conversion of Shares" in desc


def test_derived_subclasses_and_specialists(rows):
    """Derived subclasses and specialists."""
    from mailroom_eda.labeling import derive

    assert derive.canonical_subclass(rows[0]) == tasks.cuad_family("Consulting Agreements")["key"]
    assert derive.communication_type(rows[2]) == "attorney_demand"
    assert derive.communication_type({**rows[2], "expected_subclass": "fax"}) == "other"
    assert derive.specialist(rows[1]) == "merger_agreement_specialist"


# ---------------------------------------------------------------- OpenRouter routing (models.yaml)

def test_route_without_temperature_omits_it():
    """Claude Haiku 5.5 and GPT-6 Luna Pro endpoints reject temperature; with
    require_parameters a temperature 0 request would find no endpoint, so the route drops it."""
    msgs = [{"role": "user", "content": "x"}]
    body = providers.build_payload("openrouter", "m", msgs, {"type": "object"},
                                   route={"only": ["anthropic"], "temperature": False, "seed": False})
    assert "temperature" not in body and "seed" not in body
    assert providers.build_payload("openrouter", "m", msgs, None, route={"only": ["x"]})["temperature"] == 0


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
        assert c["cap_usd"] > 0 or c.get("skip"), c["id"]
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
    # $30 plus the 904-email intent relabel the user added on 2026-11-06 (caps $1.95); +$0.40
    # on 2026-11-08: the claim_checklist per-type split (8b-8e) needs measured-p75 caps
    assert sum(c["cap_usd"] for c in camp["chunks"]) <= 34.0


def test_every_cap_covers_its_projection_with_headroom():
    """A cap below 1.4x its projection stops the chunk before its last calls (the cap check
    reserves the worst case per call)."""
    stage = runner.DEFAULT_STAGE / "ground_truth_hardened.jsonl"
    if not stage.exists():
        pytest.skip("v9.4 stage not built (data/ is gitignored)")
    costs = runner.campaign_costs(runner.load_stage(runner.DEFAULT_STAGE), "v10")
    low = [(c["id"], c["projected_usd"], c["cap_usd"]) for c in costs["chunks"]
           if c["cap_usd"] < runner.CAP_HEADROOM_FACTOR * c["projected_usd"] - 1e-9]
    assert not low, low


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
    monkeypatch.setattr(operate, "fields_registry", lambda: {"dojo_pin": {"version": "0.27.2"}})  # before G1
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
        return providers.Completion(json.dumps(bad), bad, m, _pin_served(m, reasoning), 100, 50, 0, 0.0001,
                                    "stop", True)

    kw = dict(task="cuad", models=[model], backend=providers.Backend("openrouter", "u", "k"), reasoning="off",
              budget=spend.Budget(1.0, 3600), out_dir=tmp_path, vocab=vocab)
    runner.run_chunk([row], chat=failing, **kw)
    assert seen == [2, 4]
    seen.clear()

    def fixing(backend, m, messages, schema, *, reasoning, max_tokens):
        """Every call returns the fixed answer."""
        seen.append(len(messages))
        return providers.Completion(json.dumps(good), good, m, _pin_served(m, reasoning), 100, 50, 0, 0.0001,
                                    "stop", True)

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
                     reasoning="off", budget=spend.Budget(1.0, 3600), out_dir=out_dir, chat=_fake_chat({modal: ans}),
                     count_tokens=lambda *a, **kw: (100, 32768))
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
    # the model's own allowance (4 x its measured intent p75 of 900), not the flat ceiling
    assert seen and set(seen) == {runner.MAX_TOKENS["intent"] + 4 * 900} == {4000}


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


def test_unreasoned_providers_flag_a_provider_that_ignores_low():
    """On real documents, a provider whose low-reasoning answers mostly spent 0 reasoning tokens
    is not honouring the level; one easy document with 0 tokens is not enough to flag it."""
    calls = ([{"model": "m", "served_provider": "CoreWeave", "reasoning_tokens": 240}] * 9
             + [{"model": "m", "served_provider": "CoreWeave", "reasoning_tokens": 0}]
             + [{"model": "m", "served_provider": "DeepInfra", "reasoning_tokens": 0}] * 3)
    assert operate.unreasoned_providers(calls, "off") == {}
    assert operate.unreasoned_providers(calls, "low") == {"m via DeepInfra": "3/3"}


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


def test_ping_real_doc_sends_a_real_intent_document(tmp_path, monkeypatch, rows):
    """--real-doc pings with a real correspondence document and the intent schema, so the
    logged reasoning tokens show whether a provider reasons on real text."""
    import argparse
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(operate, "campaign", lambda name: {"chunks": [
        {"id": "3", "models": ["deepseek/deepseek-v4.1-flash"], "reasoning": "low"}]})
    monkeypatch.setattr(runner, "load_stage", lambda stage: rows)
    monkeypatch.setattr(runner, "select", lambda rs, task, set_name: [r for r in rs if r["expected"] == "correspondence"])
    seen = []

    def chat(backend, model, messages, schema, *, reasoning, max_tokens, route=None):
        """Fake chat answering the intent schema."""
        seen.append((messages, schema))
        ans = {"intent": "payment_demand", "confidence": 0.9, "evidence": "x"}
        return providers.Completion(json.dumps(ans), ans, model, "CoreWeave", 900, 40, 120, 0.0001, "stop", True)

    args = argparse.Namespace(campaign="v10", initials="JB", cap_usd=0.05, out=tmp_path, per_provider=False,
                              real_doc=True, stage=tmp_path)
    assert operate.cmd_ping(args, chat=chat, key_status=lambda b: {"limit_remaining": 10}) == 0
    assert seen and all("intent" in s["properties"] for _, s in seen)
    assert all(rows[2]["doc_text"][:40] in m[-1]["content"] for m, _ in seen)
    log = export.read_journal(tmp_path / "ping.jsonl")
    assert all(r["probe"] == "real_doc:e1" and r["reasoning_tokens"] == 120 for r in log if "error" not in r)


def test_cuad_window_sends_calibration_whole_and_caps_giants(rows):
    """Golden CUAD calibration contracts fit 32k tokens; all of them are sent whole.
    Only a document past 400k characters is windowed."""
    big = {**rows[0], "doc_text": "x" * 300_000}
    assert "the full document" in tasks.build_cuad(big).messages[1]["content"]
    giant = {**rows[0], "doc_text": "y" * 616_016}
    msg = tasks.build_cuad(giant).messages[1]["content"]
    assert "the full document" not in msg and len(msg) < 410_000


def test_cuad_window_is_part_of_the_prompt_sha(rows, monkeypatch):
    """A cached answer made on cut-off text is never reused after the window changes."""
    before = tasks.build_cuad(rows[0]).prompt_sha256
    monkeypatch.setattr(tasks, "CUAD_HEAD_CHARS", 48_000)
    assert tasks.build_cuad(rows[0]).prompt_sha256 != before


def test_costs_use_measured_reasoning_tokens():
    """Low reasoning is priced from the reasoning tokens each model was measured to spend,
    not a flat output multiplier (DeepSeek reasoned 250-1,600 tokens on one email)."""
    est = {"output_tokens": 1000, "calls": 192}
    ds = "deepseek/deepseek-v4.1-flash"
    assert runner.reasoning_tokens(ds, "intent", "off") == 0
    assert runner.reasoning_tokens(ds, "intent", "low") == 900
    assert runner.reasoning_tokens(ds, "corporate", "low") == 1200
    assert runner.reasoning_tokens("anthropic/claude-haiku-5.5", "cuad", "low") == 1200
    assert (runner.projected_output(ds, "intent", "low", est) - runner.projected_output(ds, "intent", "off", est)
            == round(900 * 192 * runner.RETRY_FACTOR))


def test_cuad_problems_restate_the_category_question_on_a_failed_semantic_probe(monkeypatch):
    """A failed semantic probe tells the repair call what the category asks, not just that it failed."""
    from mailroom_eda import v9_4_revision as v94
    monkeypatch.setattr(v94, "verify_cuad_labels",
                        lambda doc, labels, vocab: ({}, ["Audit Rights: failed semantic probe"], []))
    row = {"doc_text": "The Company may inspect the books once a year."}
    resp = {"clauses": [{"category": "Audit Rights", "text": "The Company may inspect the books"}]}
    (msg,) = runner.cuad_problems(row, resp, ["Audit Rights"])
    desc = next(c["description"] for c in dojo_snapshot()["cuad_categories"] if c["name"] == "Audit Rights")
    assert msg.startswith("Audit Rights: failed semantic probe") and desc in msg
    assert runner._probe_hint("Parties: not verbatim: 'x'") == "Parties: not verbatim: 'x'"


def test_cuad_prompt_excludes_winding_up_from_post_termination_services():
    """Five of seven smoke arms labeled a joint venture's liquidation clause as
    Post-Termination Services; the expert gold excludes it."""
    from mailroom_eda.labeling.registry import prompt
    assert "winding up" in prompt("cuad_system").render({}).lower()


def test_cuad_holdout_is_disjoint_from_calibration_and_stable():
    """The revised prompt must pass on 40 golden contracts it was not tuned on."""
    from mailroom_eda import v9_4_revision as v94
    if not runner.DEFAULT_STAGE.exists():
        pytest.skip("no local v9.4 stage")
    rows = runner.load_stage(runner.DEFAULT_STAGE)
    hold = runner.select(rows, "cuad", "holdout")
    cal = {r["filename"] for r in v94.cuad_calibration_sample(rows)}
    assert len(hold) == 40 and not cal & {r["filename"] for r in hold}
    assert [r["filename"] for r in hold] == [r["filename"] for r in runner.select(rows, "cuad", "holdout")]
    assert all(r["source_corpus"] == v94.GOLDEN_CUAD_SOURCE
               and (r["gt_fields"].get("context_window_band") or "") != ">32k" for r in hold)


# --- PR 43 review fixes -------------------------------------------------------------------

def _ping(model, reasoning, utc, **extra):
    return {"model": model, "reasoning": reasoning, "utc": utc, "used_schema": True,
            "response": {"ok": True, "word": "mailroom"}, **extra}


def test_ping_problems_ignore_real_doc_probes_and_require_strict_schema():
    """A later --real-doc probe answers a different question and never decides route health;
    a json_object fallback is not a passing ping."""
    now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
    route = [("m", "low")]
    log = [_ping("m", "low", "2026-11-06T10:00:00Z"),
           {**_ping("m", "low", "2026-11-06T11:00:00Z"), "probe": "real_doc:e1", "response": {"intent": "update"}}]
    assert operate.ping_problems(log, route, now=now) == []
    fallback = [_ping("m", "low", "2026-11-06T10:00:00Z", used_schema=False)]
    assert "latest ping failed" in operate.ping_problems(fallback, route, now=now)[0]


def test_ping_targets_only_the_chosen_chunks(tmp_path, monkeypatch):
    """--chunk limits the ping to the routes those chunks use; no route is forced in."""
    import argparse
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(operate, "campaign", lambda name: {"chunks": [
        {"id": "3", "models": ["deepseek/deepseek-v4.1-flash"], "reasoning": "low"},
        {"id": "5a", "models": ["z-ai/glm-5.3-flashx-legal"], "reasoning": "off"}]})
    sent = []

    def chat(backend, model, messages, schema, *, reasoning, max_tokens, route=None):
        sent.append((model, reasoning))
        return providers.Completion('{"ok": true, "word": "mailroom"}', {"ok": True, "word": "mailroom"},
                                    model, "CoreWeave", 20, 5, 9, 0.00001, "stop", True)

    args = argparse.Namespace(campaign="v10", initials="JB", cap_usd=0.05, out=tmp_path, chunks=["3"])
    assert operate.cmd_ping(args, chat=chat, key_status=lambda b: {"limit_remaining": 10}) == 0
    assert sent == [("deepseek/deepseek-v4.1-flash", "low")]


def test_served_outside_route_accepts_provider_display_aliases():
    """OpenRouter reports google-vertex as "Google" and amazon-bedrock as "Amazon Bedrock"."""
    route = {"only": ["anthropic", "google-vertex"]}
    assert providers.served_outside_route(route, "off", {"Google", "Anthropic"}) == []
    assert providers.served_outside_route(route, "off", {"Azure"}) == ["Azure"]


def _intent_journal(tmp_path, rows, reasoning="low"):
    ans = {"intent": "update", "confidence": 0.9, "evidence": "x"}
    common = dict(task="intent", models=["deepseek/deepseek-v4.1-flash"],
                  backend=providers.Backend("openrouter", "u", "k"), budget=spend.Budget(1.0, 3600))
    runner.run_chunk([rows[2]], reasoning=reasoning, out_dir=tmp_path / "smoke" / "a",
                     chat=_fake_chat({"deepseek/deepseek-v4.1-flash": ans}), **common)
    return common, ans


def _rewrite(path, **changes):
    recs = [{**c, **changes} for c in export.read_journal(path)]
    path.unlink()
    for c in recs:
        export.append_journal(path, c)


def test_reuse_skips_answers_served_outside_the_current_route(tmp_path, rows):
    """A low-reasoning answer DeepInfra served before the low pin dropped it is bought again."""
    common, ans = _intent_journal(tmp_path, rows)
    _rewrite(tmp_path / "smoke" / "a" / "calls.jsonl", served_provider="DeepInfra")
    res = runner.run_chunk([rows[2]], reasoning="low", out_dir=tmp_path / "3", reuse_from=[tmp_path],
                           chat=_fake_chat({"deepseek/deepseek-v4.1-flash": ans}), **common)
    assert res.get("reused", 0) == 0 and res["calls"] == 2


def test_reuse_requires_the_same_rendered_input(tmp_path, rows):
    """The prompt sha hashes templates, not the document: an answer to other text is not reused."""
    common, ans = _intent_journal(tmp_path, rows, reasoning="off")
    edited = {**rows[2], "doc_text": rows[2]["doc_text"] + "\nP.S. one more line."}
    res = runner.run_chunk([edited], reasoning="off", out_dir=tmp_path / "3", reuse_from=[tmp_path],
                           chat=_fake_chat({"deepseek/deepseek-v4.1-flash": ans}), **common)
    assert res.get("reused", 0) == 0 and res["calls"] == 2


def test_reused_answers_do_not_count_as_this_chunks_spend(tmp_path, rows):
    """A reused answer keeps what it cost in reused_cost_usd; this chunk spent nothing on it."""
    common, _ = _intent_journal(tmp_path, rows, reasoning="off")

    def no_call(*a, **k):
        raise AssertionError("a reusable answer was bought again")

    runner.run_chunk([rows[2]], reasoning="off", out_dir=tmp_path / "3", reuse_from=[tmp_path], chat=no_call, **common)
    recs = export.read_journal(tmp_path / "3" / "calls.jsonl")
    assert recs and all(r["cost_usd"] == 0 and r["reused_cost_usd"] > 0 for r in recs)
    runner.run_chunk([rows[2]], reasoning="off", out_dir=tmp_path / "4", reuse_from=[tmp_path / "3"],
                     chat=no_call, **common)
    again = export.read_journal(tmp_path / "4" / "calls.jsonl")
    assert again and all(r["cost_usd"] == 0 and r["reused_cost_usd"] == recs[0]["reused_cost_usd"] for r in again)


def test_unreasoned_providers_need_several_zero_token_answers():
    """One zero-token answer from a fallback provider is not evidence it ignores the level."""
    one = [{"model": "m", "served_provider": "AtlasCloud", "reasoning_tokens": 0}]
    assert operate.unreasoned_providers(one, "low") == {}


# ---------------------------------------------------------------- HF staging checkpoints

class _FakeHub:
    """Enough of HfApi for checkpoint: a dataset repo held in memory."""

    def __init__(self, private=True):
        self.private, self.files, self.created, self.commits = private, {}, [], []

    def create_repo(self, repo_id, repo_type, private, exist_ok):
        self.created.append((repo_id, private))

    def repo_info(self, repo_id, repo_type, revision=None):
        import types
        assert revision in (None, "a" * 40)
        return types.SimpleNamespace(private=self.private, sha="a" * 40)

    def upload_folder(self, folder_path, repo_id, repo_type, path_in_repo, commit_message, allow_patterns):
        root = Path(folder_path)
        for p in root.rglob("*"):
            if p.is_file():
                self.files[p.relative_to(root).as_posix()] = p.read_bytes()
        self.commits.append(commit_message)
        from types import SimpleNamespace
        return SimpleNamespace(oid="a" * 40)

    def list_repo_tree(self, repo_id, repo_type, recursive, revision=None):
        import types
        from mailroom_eda.labeling import checkpoint
        assert revision == "a" * 40
        return [types.SimpleNamespace(path=k, lfs=None, blob_id=checkpoint.git_blob_sha(v))
                for k, v in self.files.items()]


def _labels_tree(root):
    d = root / "1.3_cuad-calibration"
    d.mkdir(parents=True)
    (d / "calls.jsonl").write_text(json.dumps({"document_id": "DOC-1", "cost_usd": 0.01, "reused_cost_usd": 0.01,
                                               "input_sha256": "ab", "served_provider": "CoreWeave"}) + "\n")
    (d / "cuad-labels.jsonl").write_text(json.dumps({"document_id": "DOC-1",
                                                     "labels": {"Price Restrictions": [{"text": "x"}]}}) + "\n")
    (d / "documents.csv").write_text('"document_id","cost_usd","status"\n"DOC-1","0.01","accepted"\n')
    (d / "runs.jsonl").write_text(json.dumps({"api_cost_cap_usd": 0.4, "projection": {"cost_usd": {"m": 1}},
                                              "result": {"spent_usd": 0.08, "calls": 3}}) + "\n")
    return d


def test_checkpoint_backs_up_files_byte_for_byte_with_spend(tmp_path):
    """The private backup is part of the logs: a restore must bring back the spending records
    (2026-11-06: a stripped backup lost them), and the card declares no blind config."""
    from mailroom_eda.labeling import checkpoint
    d = _labels_tree(tmp_path / "labels")
    staged = checkpoint.stage(tmp_path / "labels", None, tmp_path / "out")
    for name in ("calls.jsonl", "cuad-labels.jsonl", "documents.csv", "runs.jsonl"):
        assert staged[f"labels/1.3_cuad-calibration/{name}"].read_bytes() == (d / name).read_bytes()
    assert '"cost_usd": 0.01' in staged["labels/1.3_cuad-calibration/calls.jsonl"].read_text()
    card = staged["README.md"].read_text()
    assert "config_name: labels" in card and "config_name: default" not in card


def test_checkpoint_uploads_privately_and_verifies(tmp_path):
    """The staging repo is created private, every file is verified, and the published repo is refused."""
    from mailroom_eda import hf_interface
    from mailroom_eda.labeling import checkpoint
    _labels_tree(tmp_path / "labels")
    hub = _FakeHub()
    res = checkpoint.checkpoint(tmp_path / "labels", None, "checkpoint: test", api=hub)
    assert res["verified"] and hub.created == [(checkpoint.STAGING_REPO, True)]
    assert "labels/1.3_cuad-calibration/cuad-labels.jsonl" in hub.files
    with pytest.raises(SystemExit, match="public"):
        checkpoint.checkpoint(tmp_path / "labels", None, "x", api=_FakeHub(private=False))
    with pytest.raises(SystemExit, match="published"):
        checkpoint.checkpoint(tmp_path / "labels", None, "x", api=hub, repo_id=hf_interface.REPO_ID)


def test_checkpoint_fails_loudly_when_the_upload_does_not_verify(tmp_path):
    """A Hub copy that differs from what was staged is an error, and the command exits 4."""
    import argparse
    from mailroom_eda.labeling import checkpoint

    class Corrupt(_FakeHub):
        def list_repo_tree(self, **kw):
            return [type(f)(path=f.path, lfs=None, blob_id="0" * 40) for f in super().list_repo_tree(**kw)]

    _labels_tree(tmp_path / "labels")
    with pytest.raises(SystemExit, match="did not verify"):
        checkpoint.checkpoint(tmp_path / "labels", None, "x", api=Corrupt())
    args = argparse.Namespace(out=tmp_path / "labels", campaign="v10", no_checkpoint=False)
    assert operate.auto_checkpoint(args, "test", 0, api=Corrupt()) == 4
    assert operate.auto_checkpoint(args, "test", 2, api=Corrupt()) == 2  # keeps the run's own code
    assert operate.auto_checkpoint(args, "test", 0, api=_FakeHub()) == 0


def test_restore_fills_only_missing_files(tmp_path, monkeypatch):
    """Restore brings back staged answers a checkout lost and never overwrites local files."""
    from mailroom_eda.labeling import checkpoint
    _labels_tree(tmp_path / "labels")
    hub = _FakeHub()
    checkpoint.checkpoint(tmp_path / "labels", None, "x", api=hub)

    def download(api, repo_id, filename, local_path, *, revision=None):
        assert revision == "a" * 40
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(hub.files[filename])
        return local_path

    monkeypatch.setattr(checkpoint.hf_interface, "download_file", download)
    fresh = tmp_path / "fresh"
    (fresh / "1.3_cuad-calibration").mkdir(parents=True)
    (fresh / "1.3_cuad-calibration" / "documents.csv").write_text("local\n")
    with pytest.raises(SystemExit, match="restore conflict"):
        checkpoint.restore(fresh, api=hub)
    assert (fresh / "1.3_cuad-calibration" / "documents.csv").read_text() == "local\n"
    (fresh / "1.3_cuad-calibration" / "documents.csv").write_bytes(
        hub.files["labels/1.3_cuad-calibration/documents.csv"])
    wrote = checkpoint.restore(fresh, api=hub)
    assert fresh / "1.3_cuad-calibration" / "calls.jsonl" in wrote
    assert not (fresh / "README.md").exists()


def test_paid_run_refuses_without_a_checkpoint_token(tmp_path, monkeypatch):
    """No paid call starts when its answers could not be checkpointed."""
    from mailroom_eda.labeling import checkpoint
    monkeypatch.setattr(checkpoint, "token_ready", lambda: False)
    rows = [_row("correspondence", None, "Please pay invoice 7 by Friday.")]
    with pytest.raises(SystemExit, match="HF token"):
        runner.execute(rows, rows, task="correspondence", set_name="all", chunk_spec=None,
                       models=["deepseek/deepseek-v4.1-flash", "minimax/minimax-m3.1-flash"],
                       provider="openrouter", reasoning="off", initials="JB", cap_usd=0.01,
                       max_wall_seconds=10, out_dir=tmp_path, require_checkpoint=True,
                       chat=lambda *a, **k: pytest.fail("no call may be sent"))


# ---------------------------------------------------------------- merger section locator

FILL = ("The parties further agree to the matters set out in this paragraph. " * 20) + "\n"
AGREEMENT = (
    "AGREEMENT AND PLAN OF MERGER\nTABLE OF CONTENTS\nSection 8.2 Termination by Either Party 70\n"
    "Section 8.6 Termination Fee 71\nSection 9.9 Governing Law 80\n" + FILL
    + "ARTICLE I DEFINITIONS\nSection 1.1 Definitions.\n"
    "“Parent Termination Fee” means $35,000,000.\n“Company Permit” has the meaning set forth in Section 4.12.\n"
    + FILL * 30
    + "Section 8.2 Termination by Either Party. This Agreement may be terminated if the Merger has not been "
    "consummated on or before June 30, 2027 (the “Outside Date”).\n" + FILL
    + "Section 8.6 Termination Fee. If this Agreement is terminated, Parent shall pay the Parent Termination Fee.\n"
    + FILL
    + "Section 9.9 Governing Law. This Agreement is governed by the laws of the State of Delaware.\n" + FILL
    + "EXHIBIT A\nSection 8.6 Termination Fee. EXHIBIT COPY.\n" + FILL)


def test_headings_skip_table_of_contents_and_later_exhibits():
    """TOC entries and exhibit copies of a section number are not the operative section."""
    from mailroom_eda.labeling import sections
    fee = [h for h in sections.headings(AGREEMENT) if h.number == "8.6"]
    assert len(fee) == 1
    assert AGREEMENT[fee[0].start:].startswith("Section 8.6 Termination Fee. If this Agreement")


def test_locate_takes_whole_operative_section_and_its_definition():
    """The fee amount can live only in the definitions article; it rides with the section."""
    from mailroom_eda.labeling import sections
    got = dict(sections.locate(AGREEMENT, sections.MERGER_NEEDS))
    assert "Parent shall pay the Parent Termination Fee" in got["Termination Fee"]
    assert "$35,000,000" in got["Termination Fee"] and "EXHIBIT COPY" not in got["Termination Fee"]
    assert "laws of the State of Delaware" in got["Governing Law"]


def test_outside_date_found_by_inline_definition():
    from mailroom_eda.labeling import sections
    got = dict(sections.locate(AGREEMENT, sections.MERGER_NEEDS))
    assert "June 30, 2027" in got["Outside Date"]


MERGER_SIGNALS = {
    "Conversion of Shares": r"converted into the right to receive|shall be converted",
    "Termination Fee": r"\$\s?[\d,.]+",
    "Governing Law": r"laws of the (state|commonwealth) of",
    "Outside Date": r"(january|february|march|april|may|june|july|august|september|october|november|december)"
                    r"\s+\d{1,2},\s+\d{4}|\d+\)?\s+(days|months)|\d+(st|nd|rd|th)\s+day",
}


def test_merger_locator_finds_every_needed_section():
    """On the 151 real mergers every needed section is located, and the excerpts carry the
    values the fields ask for (2026-11-06: the regex window found Conversion in 23)."""
    import re
    from mailroom_eda.labeling import sections
    if not runner.DEFAULT_STAGE.exists():
        pytest.skip("no local v9.4 stage")
    mergers = [r for r in runner.load_stage(runner.DEFAULT_STAGE) if r["expected"] == "merger_agreement"]
    located, signal, misses = Counter(), Counter(), []
    for r in mergers:
        t = str(r["doc_text"])
        assert len(tasks.merger_window(t)[0]) <= 120_000
        got = dict(sections.locate(t, sections.MERGER_NEEDS))
        for need in sections.MERGER_NEEDS:
            if need.label in got:
                located[need.label] += 1
                if need.label in MERGER_SIGNALS and re.search(MERGER_SIGNALS[need.label], got[need.label], re.I):
                    signal[need.label] += 1
                elif need.label in MERGER_SIGNALS:
                    misses.append((need.label, r["document_id"]))
            else:
                misses.append((need.label, r["document_id"]))
    print(sorted(Counter(m[0] for m in misses).items()))
    assert all(located[n.label] >= 140 for n in sections.MERGER_NEEDS), dict(located)
    assert all(signal[k] >= 130 for k in MERGER_SIGNALS), dict(signal)


def test_full_text_flag_sends_the_whole_agreement_and_changes_the_sha(tmp_path, monkeypatch):
    """`run --full-text` (the A/B arm) sends the whole merger agreement, hashes `window: full`
    into the prompt sha so its answers are never reused as located ones, and writes to its own
    directory."""
    big = AGREEMENT + FILL * 600
    row = _row("merger_agreement", None, big)
    located = tasks.build("merger_entities", row, "")
    assert len(located.messages[1]["content"]) < len(big)
    monkeypatch.setattr(tasks, "FULL_TEXT_TASKS", {"merger_entities"})
    full = tasks.build("merger_entities", row, "")
    assert big in full.messages[1]["content"] and full.prompt_sha256 != located.prompt_sha256
    assert runner.chunk_name("merger_entities", "pilot", None, full_text=True) == "merger_entities-pilot-fulltext"
    with pytest.raises(SystemExit, match="full-text applies"):
        runner.main(["run", "--task", "intent", "--set", "calibration", "--stage", str(tmp_path),
                     "--provider", "openrouter", "--model", "deepseek/deepseek-v4.1-flash", "--dry-run",
                     "--full-text"])


def test_one_model_may_run_a_calibration_set_but_not_a_labeling_set():
    """A calibration run is scored against expert gold, so the one-model A/B arms may run it;
    a set whose answers become labels still needs two agreeing models."""
    rows = [_row("merger_agreement", None, AGREEMENT)]
    ok = runner.execute(rows, rows, task="merger_consideration", set_name="calibration", chunk_spec=None,
                        models=["deepseek/deepseek-v4.1-flash"], provider="openrouter", reasoning="off",
                        initials=None, cap_usd=None, max_wall_seconds=None, out_dir=Path("/nonexistent"),
                        dry_run=True)
    assert ok == 0
    with pytest.raises(SystemExit, match="second-model"):
        runner.execute(rows, rows, task="merger_consideration", set_name="pending", chunk_spec=None,
                       models=["deepseek/deepseek-v4.1-flash"], provider="openrouter", reasoning="off",
                       initials=None, cap_usd=None, max_wall_seconds=None, out_dir=Path("/nonexistent"),
                       dry_run=True)


# ---------------------------------------------------------------- bake-off ranking (Task 5)

def _cuad_docs(n, tp, fp, fn, start=0):
    """n per-document CUAD records with the same counts."""
    return [{"document_id": f"DOC-{i:04d}", "tp": tp, "fp": fp, "fn": fn} for i in range(start, start + n)]


def _arm(model, docs, cost, reasoning="off", **gate):
    return {"gate": {"models": [model], "reasoning": reasoning, "passed": True, "documents": len(docs),
                     "expected_documents": len(docs), "answer_cost_usd": cost, "spent_usd": cost, **gate},
            "docs": docs}


def test_paired_bootstrap_is_deterministic_and_brackets_the_point():
    a = _cuad_docs(20, 8, 2, 2) + _cuad_docs(20, 6, 3, 3, start=20)
    b = _cuad_docs(20, 9, 1, 1) + _cuad_docs(20, 6, 3, 3, start=20)
    one = operate.paired_bootstrap(a, b, "micro_f1")
    assert one == operate.paired_bootstrap(a, b, "micro_f1")
    assert one[1] <= one[0] <= one[2] and one[0] > 0


def test_bakeoff_keeps_cheaper_deepseek_unless_costlier_is_significantly_better():
    import random
    rng = random.Random(1)
    base = [{"document_id": f"DOC-{i:04d}", "tp": rng.randint(5, 10), "fp": rng.randint(0, 3), "fn": rng.randint(0, 3)}
            for i in range(40)]
    # a small mixed gain (some documents better, some worse): not significant
    noisy = [dict(d, fp=max(0, d["fp"] + (-1 if i % 2 else 1) * (i % 3 == 0)), tp=d["tp"] + (i % 7 == 0))
             for i, d in enumerate(base)]
    cheap = _arm("deepseek/deepseek-v4.1-flash", base, 0.16)
    pricey = _arm("deepseek/deepseek-v4.1-flash-1017", noisy, 0.31)
    rank = operate.bakeoff_ranking([cheap, pricey, _arm("openai/gpt-6-luna-pro", base, 0.12)], "micro_f1")
    assert rank["primary"]["gate"]["models"] == ["deepseek/deepseek-v4.1-flash"]
    # a gain on every document is significant: the costlier snapshot wins
    better = _arm("deepseek/deepseek-v4.1-flash-1017", [dict(d, fp=0, fn=0) for d in base], 0.31)
    rank = operate.bakeoff_ranking([cheap, better, _arm("openai/gpt-6-luna-pro", base, 0.12)], "micro_f1")
    assert rank["primary"]["gate"]["models"] == ["deepseek/deepseek-v4.1-flash-1017"]


def test_bakeoff_second_opinion_must_be_non_inferior_and_another_vendor():
    base = _cuad_docs(40, 8, 1, 1)
    worse = _cuad_docs(40, 6, 3, 3)  # about 0.2 F1 worse on every document
    arms = [_arm("deepseek/deepseek-v4.1-flash", base, 0.16),
            _arm("deepseek/deepseek-v4.1-flash-1017", base, 0.31),
            _arm("anthropic/claude-haiku-5.5", worse, 0.05),
            _arm("openai/gpt-6-luna-pro", base, 0.12),
            _arm("minimax/minimax-m3.1-flash", base, 0.19, reasoning="low")]
    rank = operate.bakeoff_ranking(arms, "micro_f1")
    assert rank["second"]["gate"]["models"] == ["openai/gpt-6-luna-pro"]  # cheapest non-inferior
    row = next(r for r in rank["table"] if r["model"] == "anthropic/claude-haiku-5.5")
    assert row["excluded"] == "clearly worse"
    assert all(r["model"] != "deepseek/deepseek-v4.1-flash-1017" or not r["second"] for r in rank["table"])


def test_bakeoff_ignores_incomplete_and_unpinned_arms():
    base = _cuad_docs(40, 7, 2, 2)
    short = _arm("deepseek/deepseek-v4.1-flash-1017", _cuad_docs(30, 10, 0, 0), 0.01)
    short["gate"]["expected_documents"] = 40
    off_pin = _arm("openai/gpt-6-luna-pro", _cuad_docs(40, 10, 0, 0), 0.01, served_outside_pin=["Azure"])
    rank = operate.bakeoff_ranking([_arm("deepseek/deepseek-v4.1-flash", base, 0.16), short, off_pin,
                                    _arm("anthropic/claude-haiku-5.5", base, 0.12)], "micro_f1")
    assert rank["primary"]["gate"]["models"] == ["deepseek/deepseek-v4.1-flash"]
    reasons = {r["model"]: r["eligible"] for r in rank["table"]}
    assert reasons["deepseek/deepseek-v4.1-flash-1017"].startswith("incomplete")
    assert reasons["openai/gpt-6-luna-pro"].startswith("served outside pin")
    assert rank["second"]["gate"]["models"] == ["anthropic/claude-haiku-5.5"]


def test_bakeoff_has_no_second_when_none_is_non_inferior(tmp_path, monkeypatch, capsys):
    import argparse
    base = _cuad_docs(40, 8, 1, 1)
    arms = [_arm("deepseek/deepseek-v4.1-flash", base, 0.16), _arm("openai/gpt-6-luna-pro", _cuad_docs(40, 5, 4, 4), 0.12)]
    rank = operate.bakeoff_ranking(arms, "micro_f1")
    assert rank["second"] is None
    monkeypatch.setattr(operate, "bakeoff_arms", lambda name, chunk_id, out: (arms, "micro_f1"))
    args = argparse.Namespace(campaign="v10", id="1", out=tmp_path, margin=0.05)
    assert operate.cmd_bakeoff(args) == 1
    saved = json.loads((tmp_path / "1.bakeoff.json").read_text())
    assert saved["second"] is None and saved["primary"]["model"] == "deepseek/deepseek-v4.1-flash"


def test_intent_bootstrap_uses_kappa():
    gold = ["notice", "request", "update", "other"] * 24
    a = [{"document_id": f"D{i}", "gold": g, "pred": g if i % 5 else "other"} for i, g in enumerate(gold)]
    b = [{"document_id": f"D{i}", "gold": g, "pred": g} for i, g in enumerate(gold)]
    point, lo, hi = operate.paired_bootstrap(a, b, "cohen_kappa")
    assert lo > 0 and point > 0


def test_approving_a_bakeoff_arm_follows_the_bakeoff_pick(tmp_path):
    """The approved arm becomes winner_of_<id>: it must be the arm the bake-off picked."""
    entry = {"id": "1", "task": "cuad"}
    res = {"models": ["openai/gpt-6-luna-pro"], "reasoning": "off"}
    with pytest.raises(SystemExit, match="labeling bakeoff 1"):
        operate.bakeoff_guard("v10", entry, res, tmp_path)
    (tmp_path / "1.bakeoff.json").write_text(json.dumps(
        {"primary": {"model": "deepseek/deepseek-v4.1-flash", "reasoning": "off", "arm": 3}}))
    with pytest.raises(SystemExit, match="bake-off picked deepseek"):
        operate.bakeoff_guard("v10", entry, res, tmp_path)
    operate.bakeoff_guard("v10", {"id": "1h"}, {"models": ["deepseek/deepseek-v4.1-flash"], "reasoning": "off"},
                          tmp_path)  # the holdout follows chunk 1's pick
    operate.bakeoff_guard("v10", entry, res, tmp_path, override=True)
    operate.bakeoff_guard("v10", {"id": "4"}, res, tmp_path)  # one arm: not a bake-off


# ---------------------------------------------------------------- inspect report (Task 6)

def test_inspect_report_shows_gold_answers_and_quote_context():
    """Disagreements come first; both answers, the gold, the quote in context, and an
    unlocatable quote marked NOT FOUND."""
    agree = _row("correspondence", None, "Please pay invoice 7 by Friday or we will sue.",
                 gt={"intent": "payment_demand"}, fn="a.txt")
    split = _row("correspondence", None, "The board meeting moved to Tuesday at noon.",
                 gt={"intent": "update"}, fn="b.txt")
    recs = [
        {"document_id": tasks._ids(agree)[0], "filename": "a.txt", "model": "m",
         "pass_a": {"intent": "payment_demand", "confidence": 0.9, "evidence": "pay invoice 7"},
         "pass_b": {"intent": "payment_demand", "confidence": 0.9, "evidence": "pay invoice 7"}},
        {"document_id": tasks._ids(split)[0], "filename": "b.txt", "model": "m",
         "pass_a": {"intent": "meeting_invite", "confidence": 0.6, "evidence": "board meeting moved"},
         "pass_b": {"intent": "update", "confidence": 0.7, "evidence": "this sentence is not in the email"}}]
    calls = [{"document_id": tasks._ids(split)[0], "served_provider": "CoreWeave", "variant": "a"}]
    md = operate.inspect_report([agree, split], recs, "intent", n=2, calls=calls)
    first, second = md.index(tasks._ids(split)[0]), md.index(tasks._ids(agree)[0])
    assert first < second  # the disagreement is read first
    assert "meeting_invite" in md and "gold: `update`" in md and "CoreWeave" in md
    assert "The **board meeting moved** to Tuesday at noon" in md
    assert "NOT FOUND" in md


def test_skipped_arm_neither_runs_nor_costs_nor_ranks(tmp_path):
    """A bake-off arm marked skip: keeps its arm number (output directories stay stable),
    refuses to run, projects $0 and ranks as ineligible."""
    import argparse
    camp = campaign("v10")
    skipped = [(c, i) for i, c in enumerate((c for c in camp["chunks"] if str(c["id"]) == "3"), 1) if c.get("skip")]
    assert [c["models"][0] for c, _ in skipped] == ["deepseek/deepseek-v4.1-flash", "openai/gpt-6-luna-pro"]
    entry, arm = skipped[0]
    args = argparse.Namespace(campaign="v10", id="3", arm=arm, dry_run=True, ack_requires=False,
                              provider="openrouter", model_map=[], stage=tmp_path, initials="JB",
                              cap_usd=None, out=tmp_path, workers=1)
    with pytest.raises(SystemExit, match="skipped"):
        operate.cmd_chunk(args)
    arms, _ = operate.bakeoff_arms("v10", "3", tmp_path)
    assert operate._ineligible(arms[arm - 1]).startswith("skipped")


def test_full_text_lasts_one_invocation(monkeypatch):
    """`--full-text` must not leak into a later in-process run, even one that raised."""
    seen = []

    def fake_run(args, full_text):
        seen.append(set(tasks.FULL_TEXT_TASKS))
        if args.dry_run:
            raise RuntimeError("boom")
        return 0

    monkeypatch.setattr(runner, "_run", fake_run)
    argv = ["run", "--task", "merger_entities", "--set", "pilot", "--provider", "openrouter",
            "--model", "deepseek/deepseek-v4.1-flash", "--full-text"]
    assert runner.main(argv) == 0
    with pytest.raises(RuntimeError):
        runner.main(argv + ["--dry-run"])
    assert seen == [{"merger_entities"}, {"merger_entities"}] and tasks.FULL_TEXT_TASKS == set()


@pytest.mark.parametrize("cap", [9_000, 12_345, 20_000])
def test_located_window_never_exceeds_its_cap(monkeypatch, cap):
    """Separators and the signature-page marker count against the cap too."""
    from mailroom_eda.labeling import sections
    monkeypatch.setattr(sections, "locate", lambda text, needs: [(f"n{i}", "x" * 2_999) for i in range(12)])
    text = "y" * 200_000
    window, _ = tasks._located_window(text, (), 2_000, cap, 3_000)
    assert len(window) <= cap and window.endswith(text[-3_000:])
    window, _ = tasks._located_window(text, (), 2_000, cap, 0)
    assert len(window) <= cap


def test_locate_scans_the_headings_once(monkeypatch):
    from mailroom_eda.labeling import sections
    calls = []
    real = sections._all_headings
    monkeypatch.setattr(sections, "_all_headings", lambda text: calls.append(1) or real(text))
    sections.locate(AGREEMENT + FILL * 50, sections.MERGER_NEEDS)
    assert len(calls) == 1


def test_merger_ab_diff_reports_missing_documents_apart(tmp_path, monkeypatch):
    """A document one arm never answered is listed as missing, never counted a difference."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "merger_window_ab", Path(__file__).resolve().parents[1] / "scripts/labeling/merger_window_ab.py")
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)
    monkeypatch.setattr(ab, "ROOT", tmp_path)
    cols = ["document_id", "field", "model_a_status", "model_a_value", "model_a_evidence"]
    loc = [{"document_id": "D1", "field": "f", "model_a_status": "present", "model_a_value": "a",
            "model_a_evidence": "a"}]
    full = loc + [{"document_id": "D2", "field": "f", "model_a_status": "present", "model_a_value": "b",
                   "model_a_evidence": "b"}]
    for d, rows in (("merger_entities-pilot", loc), ("merger_entities-pilot-fulltext", full)):
        (tmp_path / d).mkdir()
        export.write_csv(tmp_path / d / "fields.csv", cols, rows)
    res = ab.entities_diff()
    assert res["fields_differ"] == 0 and res["agreements_with_a_full_only_value"] == 0
    assert res["documents_missing_from"] == {"located": ["D2"], "full": []}


def test_gold_corrections_apply_and_refuse_a_moved_gold(tmp_path):
    """Accepted corrections replace the gold the gate scores against; one whose gold_was no
    longer matches the row, or that names no decider, is refused."""
    row = {"filename": "a/1.", "gt_fields": {"intent": "update"}}
    fix = {"document_id": "D", "filename": "a/1.", "field": "intent", "gold_was": "update",
           "gold_now": "notice", "reason": "r", "decided_by": "user", "decided_utc": "t"}
    export.write_csv(tmp_path / operate.GOLD_CORRECTIONS, operate.GOLD_CORRECTION_COLUMNS, [fix])
    fixed, n = operate.corrected_gold([row, {"filename": "b/2.", "gt_fields": {"intent": "other"}}],
                                      operate.gold_corrections(tmp_path))
    assert n == 1 and fixed[0]["gt_fields"]["intent"] == "notice" and row["gt_fields"]["intent"] == "update"
    with pytest.raises(SystemExit, match="expected 'update'"):
        operate.corrected_gold([{"filename": "a/1.", "gt_fields": {"intent": "request"}}],
                               operate.gold_corrections(tmp_path))
    export.write_csv(tmp_path / operate.GOLD_CORRECTIONS, operate.GOLD_CORRECTION_COLUMNS,
                     [fix | {"decided_by": ""}])
    with pytest.raises(SystemExit, match="decided_by"):
        operate.gold_corrections(tmp_path)
    assert operate.gold_corrections(tmp_path / "none") == {}
# ---------------------------------------------------------------- preflight: HF write, restore, requires, Modal

class _WhoamiHub(_FakeHub):
    """A fake hub whose token is described by ``whoami``."""

    def __init__(self, role, *, name="Lucius-Morningstar", orgs=(), scoped=(), private=True, exists=True):
        super().__init__(private=private)
        self.info = {"name": name, "orgs": list(orgs),
                     "auth": {"accessToken": {"role": role, "fineGrained": {"scoped": list(scoped)}}}}
        self.exists = exists

    def whoami(self):
        return self.info

    def repo_info(self, repo_id, repo_type):
        if not self.exists:
            class RepositoryNotFoundError(Exception):
                pass
            raise RepositoryNotFoundError(repo_id)
        return super().repo_info(repo_id, repo_type)


def test_write_access_reads_the_token_role_and_namespace():
    """Only a token that can write the staging namespace passes; an existing public repo fails."""
    from mailroom_eda.labeling import checkpoint
    assert checkpoint.write_access(api=_WhoamiHub("write"))[0]
    assert not checkpoint.write_access(api=_WhoamiHub("read"))[0]
    assert not checkpoint.write_access(api=_WhoamiHub("write", name="someone-else"))[0]
    assert checkpoint.write_access(api=_WhoamiHub("write", name="x",
                                                  orgs=[{"name": "Lucius-Morningstar", "roleInOrg": "write"}]))[0]
    grant = [{"entity": {"type": "user", "name": "Lucius-Morningstar"}, "permissions": ["repo.write"]}]
    assert checkpoint.write_access(api=_WhoamiHub("fineGrained", scoped=grant))[0]
    assert not checkpoint.write_access(api=_WhoamiHub(
        "fineGrained", scoped=[{"entity": {"type": "user", "name": "Lucius-Morningstar"},
                                "permissions": ["repo.content.read"]}]))[0]
    ok, why = checkpoint.write_access(api=_WhoamiHub("write", exists=False))
    assert ok and "created private" in why
    ok, why = checkpoint.write_access(api=_WhoamiHub("write", private=False))
    assert not ok and "public" in why


def test_paid_chunk_refuses_without_checkpoint_write_access(tmp_path, monkeypatch):
    """A paid chunk stops before any call when its answers could not be staged."""
    import argparse
    from mailroom_eda.labeling import checkpoint
    monkeypatch.setattr(operate, "load_approvals", lambda name: [])
    monkeypatch.setattr(checkpoint, "write_access", lambda: (False, "token role is read"))
    args = argparse.Namespace(campaign="v10", id="1", arm=1, dry_run=False, ack_requires=False,
                              provider="modal", model_map=[], stage=tmp_path, initials="JB",
                              cap_usd=None, out=tmp_path, workers=1, no_checkpoint=False)
    with pytest.raises(SystemExit, match="checkpoint not writable"):
        operate.cmd_chunk(args)


def test_auto_restore_fills_missing_files_and_respects_opt_out(tmp_path, monkeypatch):
    """Commands parsed with --no-restore available pull missing staged files; opting out or no flag skips."""
    import argparse
    from mailroom_eda.labeling import checkpoint
    _labels_tree(tmp_path / "labels")
    hub = _FakeHub()
    checkpoint.checkpoint(tmp_path / "labels", None, "x", api=hub)

    def download(api, repo_id, filename, local_path, *, revision=None):
        assert revision == "a" * 40
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(hub.files[filename])
        return local_path

    monkeypatch.setattr(checkpoint.hf_interface, "download_file", download)
    fresh = tmp_path / "fresh"
    (fresh / "1.3_cuad-calibration").mkdir(parents=True)
    (fresh / "1.3_cuad-calibration" / "documents.csv").write_text("local\n")
    assert operate.auto_restore(argparse.Namespace(out=fresh), api=hub) == []  # hand-built: no flag, no restore
    assert operate.auto_restore(argparse.Namespace(out=fresh, no_restore=True), api=hub) == []
    with pytest.raises(SystemExit, match="restore conflict"):
        operate.auto_restore(argparse.Namespace(out=fresh, no_restore=False), api=hub)
    (fresh / "1.3_cuad-calibration" / "documents.csv").write_bytes(
        hub.files["labels/1.3_cuad-calibration/documents.csv"])
    wrote = operate.auto_restore(argparse.Namespace(out=fresh, no_restore=False), api=hub)
    assert fresh / "1.3_cuad-calibration" / "calls.jsonl" in wrote
    assert (fresh / "1.3_cuad-calibration" / "documents.csv").read_bytes() == hub.files[
        "labels/1.3_cuad-calibration/documents.csv"]

    class Broken(_FakeHub):
        def list_repo_tree(self, **kw):
            raise RuntimeError("offline")

    assert operate.auto_restore(argparse.Namespace(out=fresh, no_restore=False), api=Broken()) == []
    monkeypatch.setattr(checkpoint, "token_ready", lambda: False)
    assert operate.auto_restore(argparse.Namespace(out=fresh, no_restore=False)) == []


def test_ping_checkpoints_its_log(tmp_path, monkeypatch):
    """A ping's log is staged right after it is written, so another machine sees the verified routes."""
    import argparse
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")

    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        return providers.Completion("{}", {"ok": True, "word": "mailroom"}, model, "P", 20, 8, 0, 0.00001,
                                    "stop", True)
    hub = _FakeHub()
    args = argparse.Namespace(campaign="v10", initials="JB", cap_usd=0.05, out=tmp_path, chunks=["3"],
                              no_checkpoint=False, checkpoint_api=hub)
    assert operate.cmd_ping(args, chat=chat, key_status=lambda b: {"limit_remaining": None}) == 0
    assert "labels/ping.jsonl" in hub.files and hub.commits == ["checkpoint: ping by JB"]


def test_requires_is_met_by_the_dojo_pin(monkeypatch):
    """llm-dojo-scoring X.Y.Z is met once fields.yaml pins at least that Dojo version."""
    import mailroom_eda.labeling.operate as op
    monkeypatch.setattr(op, "fields_registry", lambda: {"dojo_pin": {"version": "0.27.2"}})
    assert not op.requires_met("llm-dojo-scoring 0.28.0 (G1)")[0]
    assert op.requires_met(None)[0]
    assert not op.requires_met("some other release")[0]
    monkeypatch.setattr(op, "fields_registry", lambda: {"dojo_pin": {"version": "0.28.0"}})
    assert op.requires_met("llm-dojo-scoring 0.28.0 (G1)")[0]
    monkeypatch.setattr(op, "fields_registry", lambda: {"dojo_pin": {"version": "0.30.1"}})
    assert op.requires_met("llm-dojo-scoring 0.28.0 (G1)")[0]
    lines = op.next_chunks("v10", [{"chunk": c} for c in ("1", "2a", "2b", "2c", "3", "4")])
    assert not any("external" in line for line in lines)


def test_modal_check_lists_the_served_model():
    """The Modal check needs both env vars, an app URL, and the models.yaml model on /v1/models."""
    want = operate.modal_models()
    env = {"VLLM_BASE_URL": "https://ws--vllm-serve.modal.run", "VLLM_API_KEY": "test-token-not-real"}
    seen = {}

    def fetch(url, headers):
        seen.update(url=url, headers=headers)
        return {"data": [{"id": m} for m in want]}

    ok, msg = operate.modal_check(env, fetch=fetch)
    assert ok and seen["url"].endswith(".modal.run/v1/models") and "test-token-not-real" not in msg
    ok, msg = operate.modal_check({"VLLM_BASE_URL": env["VLLM_BASE_URL"]}, fetch=fetch)
    assert not ok and "MODAL_VLLM_API_TOKEN" in msg
    assert not operate.modal_check(env, fetch=lambda url, headers: {"data": [{"id": "other"}]})[0]
    assert not operate.modal_check({**env, "VLLM_BASE_URL": "https://example.com"}, fetch=fetch)[0]


# ---------------------------------------------------------------- sweep

def test_sweep_plan_stops_at_an_unapproved_predecessor_or_requires(monkeypatch):
    """The plan follows campaign order, skips approved chunks and never runs past a missing approval."""
    monkeypatch.setattr(operate, "fields_registry", lambda: {"dojo_pin": {"version": "0.27.2"}})  # before G1
    plan, why = operate.sweep_plan("v10", [], "9e")
    assert {str(e["id"]) for e, _ in plan} == {"1"} and len(plan) == 7 and "1h" in why and "needs chunk 1" in why
    approved = [{"chunk": c} for c in ("1", "1h")]
    plan, why = operate.sweep_plan("v10", approved, "2c")
    assert [str(e["id"]) for e, _ in plan] == ["2a", "2b", "2c"] and why == "reached 2c"
    plan, why = operate.sweep_plan("v10", approved + [{"chunk": c} for c in ("2a", "2b", "2c", "3", "4a", "4b", "4c")], "9e")
    assert plan == [] and "5a" in why and "not met" in why
    plan, why = operate.sweep_plan("v10", [{"chunk": "7a"}], "7e", start="7b")
    assert [str(e["id"]) for e, _ in plan] == ["7b", "7c", "7d", "7e"]
    with pytest.raises(SystemExit, match="no such chunk"):
        operate.sweep_plan("v10", [], "99")


def _sweep_args(tmp_path, **kw):
    import argparse
    base = dict(campaign="v10", stage=tmp_path, out=tmp_path, through="7e", start="7b", initials="JB",
                provider="openrouter", workers=1, model_map=[], dry_run=False, no_checkpoint=False)
    return argparse.Namespace(**{**base, **kw})


def test_sweep_runs_each_arm_once_confirmed_and_never_approves(tmp_path, monkeypatch):
    """One yes runs the planned arms through cmd_chunk; a non-zero exit stops the sweep."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(operate, "load_approvals", lambda name: [{"chunk": "7a"}])
    monkeypatch.setattr(runner, "load_stage", lambda stage: [])
    ran = []
    assert operate.cmd_sweep(_sweep_args(tmp_path, dry_run=True), confirm=lambda q: pytest.fail("no prompt"),
                             run_chunk=lambda ns: pytest.fail("dry run")) == 0
    assert operate.cmd_sweep(_sweep_args(tmp_path), confirm=lambda q: "no",
                             run_chunk=lambda ns: pytest.fail("not confirmed")) == 1
    assert operate.cmd_sweep(_sweep_args(tmp_path), confirm=lambda q: "yes",
                             run_chunk=lambda ns: ran.append(ns.id) or 0) == 0
    assert ran == ["7b", "7c", "7d", "7e"]
    ran.clear()
    assert operate.cmd_sweep(_sweep_args(tmp_path), confirm=lambda q: "yes",
                             run_chunk=lambda ns: ran.append(ns.id) or (2 if ns.id == "7c" else 0)) == 2
    assert ran == ["7b", "7c"]
    with pytest.raises(SystemExit, match="initials"):
        operate.cmd_sweep(_sweep_args(tmp_path, initials=None), confirm=lambda q: "yes", run_chunk=lambda ns: 0)
    path = operate.approvals_path("v10")
    assert not path.exists() or "7b" not in path.read_text()  # nothing approved


# ---------------------------------------------------------------- adjudication step 10

def _approved_field_chunk(tmp_path, monkeypatch, decisions):
    """Chunk 7b approved, with a fields.csv whose final decisions are ``decisions``."""
    entry, arm = operate.chunk_entry("v10", "7b", None)
    d = operate.chunk_dir(tmp_path, entry, arm)
    d.mkdir(parents=True)
    (d / "calls.jsonl").write_text("{}\n")
    rows = [{"document_id": f"DOC-{i}", "field": "intent", "decision": dec, "final_decision": dec,
             "model_a_value": "x", "model_b_value": "y"} for i, dec in enumerate(decisions)]
    export.write_csv(d / "fields.csv", export.FIELD_COLUMNS, rows)
    export.write_csv(d / "adjudication.csv", export.FIELD_COLUMNS, [r for r in rows if r["decision"] == "adjudicate"])
    approvals = [{"chunk": "7b", "arm": None, "reasoning": "low", "approved_by": "JB"}]
    monkeypatch.setattr(operate, "load_approvals", lambda name: approvals)

    def review(rows_, task, out_dir, reasoning=None, interim=None):
        """Stand-in for runner.review: apply adjudication.csv verdicts to fields.csv."""
        adj = {(r["document_id"], r["field"]): r for r in export.read_csv(out_dir / "adjudication.csv")}
        fields = export.read_csv(out_dir / "fields.csv")
        for f in fields:
            v = adj.get((f["document_id"], f["field"]), {}).get("reviewer_verdict")
            if v:
                f["reviewer_verdict"], f["final_decision"] = v, "accept"
        export.write_csv(out_dir / "fields.csv", export.FIELD_COLUMNS, fields)

    monkeypatch.setattr(runner, "review", review)
    monkeypatch.setattr(runner, "load_stage", lambda stage: [])
    return d, approvals


def test_adjudicate_lists_open_items_and_reads_verdicts_back(tmp_path, monkeypatch):
    """open.csv lists every open item; a verdict typed there reaches the chunk and closes the item."""
    import argparse
    _approved_field_chunk(tmp_path, monkeypatch, ["accept", "adjudicate", "adjudicate"])
    args = argparse.Namespace(campaign="v10", out=tmp_path, stage=tmp_path, chunk=None, no_checkpoint=True)
    assert operate.cmd_adjudicate(args) == 0
    open_rows = export.read_csv(tmp_path / "adjudication" / "open.csv")
    assert [(r["chunk"], r["document_id"]) for r in open_rows] == [("7b", "DOC-1"), ("7b", "DOC-2")]
    open_rows[0]["reviewer_verdict"] = "a"
    export.write_csv(tmp_path / "adjudication" / "open.csv", operate.OPEN_COLUMNS, open_rows)
    items, copied = operate.adjudication_items("v10", tmp_path, tmp_path, rows=[])
    assert copied == 1 and [i["document_id"] for i in items] == ["DOC-2"]


def test_adjudicate_one_chunk_keeps_other_chunks_open_rows(tmp_path, monkeypatch):
    """`adjudicate --chunk X` rewrites only X's rows in open.csv; other chunks' verdicts survive."""
    import argparse
    _approved_field_chunk(tmp_path, monkeypatch, ["accept", "adjudicate"])
    other = {c: "" for c in operate.OPEN_COLUMNS}
    other.update(chunk="9b", document_id="DOC-9", field="entity_name", reviewer_verdict="b")
    (tmp_path / "adjudication").mkdir(parents=True, exist_ok=True)
    export.write_csv(tmp_path / "adjudication" / "open.csv", operate.OPEN_COLUMNS, [other])
    args = argparse.Namespace(campaign="v10", out=tmp_path, stage=tmp_path, chunk="7b", no_checkpoint=True)
    assert operate.cmd_adjudicate(args) == 0
    rows = export.read_csv(tmp_path / "adjudication" / "open.csv")
    assert ("9b", "DOC-9", "b") in [(r["chunk"], r["document_id"], r["reviewer_verdict"]) for r in rows]
    assert ("7b", "DOC-1") in [(r["chunk"], r["document_id"]) for r in rows]


def test_resolved_gate_passes_only_with_no_open_items(tmp_path, monkeypatch):
    """Step 10 fails while anything is open or a needed chunk is unapproved, and records counts."""
    entry, _ = operate.chunk_entry("v10", "10", None)
    assert entry["task"] == "adjudication" and "9e" in entry["needs"]
    _approved_field_chunk(tmp_path, monkeypatch, ["accept", "adjudicate"])
    res = operate.gate_result("v10", entry, None, tmp_path, tmp_path)
    assert not res["passed"] and res["open_items"] == 1 and res["per_chunk"] == {"7b": 1}
    assert "9e" in res["unapproved_needs"] and (tmp_path / "adjudication" / "gate.json").exists()
    every = [{"chunk": c, "arm": None, "reasoning": "low", "approved_by": "JB"} for c in [*entry["needs"], "0"]]
    monkeypatch.setattr(operate, "load_approvals", lambda name: every)
    monkeypatch.setattr(operate, "adjudication_items", lambda *a, **k: ([], 0))
    assert operate.gate_result("v10", entry, None, tmp_path, tmp_path)["passed"]
    assert any(line.startswith("next step: 10") for line in operate.next_chunks("v10", every))


def test_steps_stay_out_of_costs_ping_and_sweep():
    """Step 10 makes no model call: it is not a chunk to price, ping or sweep."""
    camp = campaign("v10")
    assert "10" not in {str(c["id"]) for c in camp["chunks"]}
    plan, why = operate.sweep_plan("v10", [{"chunk": "7a"}], "7e", start="7b")
    assert all(str(e["id"]) != "10" for e, _ in plan)


# ---------------------------------------------------------------- interim overlay cross-check (flag only)

def _overlay(root, records):
    """A sandbox checkout with an interim overlay of ``records`` (correspondence.jsonl only)."""
    import hashlib
    d = root / "data/labels/interim"
    d.mkdir(parents=True)
    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)
    (d / "correspondence.jsonl").write_text(body)
    manifest = {"schema": "mailroom-interim-labels/v1",
                "files": {"correspondence.jsonl": hashlib.sha256(body.encode()).hexdigest()}}
    (d / "MANIFEST.json").write_text(json.dumps(manifest))
    return hashlib.sha256((d / "MANIFEST.json").read_bytes()).hexdigest()


def _interim_rec(doc_id, sha, labels):
    return {"doc_class": "correspondence", "doc_id": doc_id, "doc_sha256": sha[:16], "labels": labels,
            "source": {"gate_run": "extraction-gate-luna-v1.3.0"}}


def test_interim_load_checks_integrity_and_matches_by_hash(tmp_path):
    """Manifest and file hashes are enforced; records match on content or source content sha; derived drops."""
    from mailroom_eda.labeling import interim
    rows = [_row("correspondence", "attorney_demand", EMAIL_TEXT, document_id="e1", content_sha256="ab" * 32),
            _row("correspondence", "attorney_demand", EMAIL_TEXT, document_id="e2", source_content_sha256="cd" * 32),
            _row("correspondence", "attorney_demand", EMAIL_TEXT, document_id="e3", content_sha256="ef" * 32)]
    sha = _overlay(tmp_path, [
        _interim_rec("e1", "ab" * 32, {"sender": {"status": "gap_fill", "value": "alice@enron.com"},
                                       "communication_type": {"status": "derived", "value": "email"},
                                       "not_a_field": {"status": "gap_fill", "value": "x"}}),
        _interim_rec("e2", "cd" * 32, {"intent": {"status": "disputed", "value": "request",
                                                  "hub_value": "payment_demand", "hub_source": "heuristic"}}),
        _interim_rec("e3", "00" * 32, {"sender": {"status": "gap_fill", "value": "x"}}),
        _interim_rec("zz", "00" * 32, {"sender": {"status": "gap_fill", "value": "x"}})])
    ov = interim.load(tmp_path, rows, manifest_sha256=sha)
    assert set(ov.labels) == {("e1", "sender"), ("e2", "intent")}
    assert ov.skipped == {"derived": 1, "unregistered_field": 1, "hash_mismatch": 1, "no_document": 1}
    assert interim.load(tmp_path / "nowhere", rows) is None and interim.load(None, rows) is None
    with pytest.raises(SystemExit, match="campaign.yaml interim_overlay"):
        interim.load(tmp_path, rows, manifest_sha256="0" * 64)
    (tmp_path / "data/labels/interim/correspondence.jsonl").write_text("{}\n")
    with pytest.raises(SystemExit, match="differs from MANIFEST.json"):
        interim.load(tmp_path, rows)


def _reviewed_with_overlay(tmp_path, rows, sender_value):
    """Run a correspondence chunk where both models disagree on urgency, review it with an overlay."""
    from mailroom_eda.labeling import interim
    row = {**rows[2], "content_sha256": "ab" * 32}
    req = tasks.build("correspondence", row)
    ans = {"fields": {f: {"value": None, "status": "not_found", "evidence": ""} for f in req.fields}}
    ans["fields"]["sender"] = {"value": "alice@enron.com", "status": "present", "evidence": "From: alice@enron.com"}
    other = json.loads(json.dumps(ans))
    other["fields"]["sender"] = {"value": "Alice", "status": "present", "evidence": "Alice"}
    models = ["deepseek/deepseek-v4.1-flash", "minimax/minimax-m3.1-flash"]
    out = tmp_path / "chunk"
    runner.run_chunk([row], task="correspondence", models=models,
                     backend=providers.Backend("openrouter", "u", "k"), reasoning="off",
                     budget=spend.Budget(1.0, 3600), out_dir=out, chat=_fake_chat(dict(zip(models, (ans, other)))))
    _overlay(tmp_path / "sandbox", [_interim_rec(row["document_id"], "ab" * 32,
                                                 {"sender": {"status": "gap_fill", "value": sender_value}})])
    ov = interim.load(tmp_path / "sandbox", [row])
    summary = runner.review([row], "correspondence", out, interim=ov)
    return row, out, ov, summary


def test_interim_columns_flag_without_changing_decisions(tmp_path, rows):
    """The overlay is shown beside the models; decision and adjudication are unchanged."""
    row, out, ov, summary = _reviewed_with_overlay(tmp_path, rows, "alice@enron.com")
    fields = {f["field"]: f for f in export.read_csv(out / "fields.csv")}
    s = fields["sender"]
    assert s["interim_status"] == "gap_fill" and s["interim_verified"] == "true" and s["interim_agree"] == "a"
    assert s["decision"] == "adjudicate" and s["final_decision"] == "adjudicate" and not s["final_source"]
    assert summary["interim"]["rows"] == 1 and summary["interim"]["verified"] == 1
    plain = tmp_path / "plain"
    plain.mkdir()
    for name in ("calls.jsonl",):
        (plain / name).write_bytes((out / name).read_bytes())
    runner.review([row], "correspondence", plain)
    assert [f["decision"] for f in export.read_csv(plain / "fields.csv")] == \
        [f["decision"] for f in export.read_csv(out / "fields.csv")]


def test_interim_verdict_needs_a_verified_overlay_value(tmp_path, rows):
    """reviewer_verdict interim takes the overlay value only when it is grounded, and records its source."""
    row, out, ov, _ = _reviewed_with_overlay(tmp_path, rows, "alice@enron.com")
    adj = export.read_csv(out / "adjudication.csv")
    adj[[a["field"] for a in adj].index("sender")]["reviewer_verdict"] = "interim"
    export.write_csv(out / "adjudication.csv", export.FIELD_COLUMNS, adj)
    runner.review([row], "correspondence", out, interim=ov)
    s = {f["field"]: f for f in export.read_csv(out / "fields.csv")}["sender"]
    assert s["final_decision"] == "accept" and s["final_value"] == "alice@enron.com"
    assert s["final_source"] == "interim:extraction-gate-luna-v1.3.0"
    with pytest.raises(SystemExit, match="--sandbox"):
        runner.review([row], "correspondence", out)  # the verdict needs the overlay loaded

    row2, out2, ov2, _ = _reviewed_with_overlay(tmp_path / "ungrounded", rows, "Mallory Not In Text")
    s2 = {f["field"]: f for f in export.read_csv(out2 / "fields.csv")}["sender"]
    assert s2["interim_verified"] == "false" and s2["interim_agree"] == "none"
    adj = export.read_csv(out2 / "adjudication.csv")
    adj[[a["field"] for a in adj].index("sender")]["reviewer_verdict"] = "interim"
    export.write_csv(out2 / "adjudication.csv", export.FIELD_COLUMNS, adj)
    with pytest.raises(SystemExit, match="not grounded"):
        runner.review([row2], "correspondence", out2, interim=ov2)


def test_interim_overlay_is_pinned_in_the_campaign():
    """campaign.yaml pins the overlay manifest the cross-check was written against."""
    pin = campaign("v10")["interim_overlay"]
    assert len(pin["manifest_sha256"]) == 64 and pin["sandbox_commit"]


def test_sweep_plan_leaves_out_skipped_arms_but_keeps_their_numbers():
    """Chunk 3's ``skip:`` arms are not planned (no spend, no refusal mid-sweep); the
    others keep the arm numbers their output folders use."""
    plan, _ = operate.sweep_plan("v10", [], "3", start="3")
    camp = [c for c in campaign("v10")["chunks"] if str(c["id"]) == "3"]
    expected = [i for i, c in enumerate(camp, 1) if not c.get("skip")]
    assert [arm for _, arm in plan] == expected and len(expected) < len(camp)
    assert not any(c.get("skip") for c, _ in plan)


def _lockstep_fixture(tmp_path, *, version_slot, vendored="0.28.0", mailroom_ref="v0.28.0", eda_commit=None):
    """Four fake checkouts that agree except where a test says otherwise."""
    eda, mr, sb = tmp_path / "eda", tmp_path / "mr", tmp_path / "sb"
    sha = "1cd4aba76d82eea20c6e8dfcc0d760881bf84924"
    _write(eda / "src/mailroom_eda/labeling/fields.yaml",
           f'dojo_pin: {{version: "0.28.0", commit: "{eda_commit or sha}"}}\n')
    _write(eda / "src/mailroom_eda/config.py", 'REPO_TAG = "v10"\n')
    _write(mr / "pyproject.toml", f'"llm-dojo-scoring @ git+https://github.com/x/llm-dojo-scoring.git@{mailroom_ref}",\n')
    _write(mr / "src/pipeline/hf_corpora.py", 'FULL_CORPUS_REVISION = "v10"\n"docclass-merged": {\n "n_docs": 3292,\n')
    _write(sb / "src/mailroom_sandbox/cli.py",
           f'    ("llm-dojo-scoring", "{version_slot}",\n     "https://github.com/x/llm-dojo-scoring.git", "{sha}"),\n')
    _write(sb / "requirements/dojo-release.txt", f"llm-dojo-scoring @ git+https://github.com/x/llm-dojo-scoring.git@{sha}\n")
    _write(sb / "vendor/llm-dojo-scoring/src/llm_dojo_scoring/__init__.py", f'__version__ = "{vendored}"\n')
    _write(sb / "src/mailroom_sandbox/job/spec.py", 'FAMILY_HF_TAG = "v10"\nFAMILY_CORPUS_SIZE = 3292\n')
    return eda, mr, sb, sha


def test_check_lockstep_reads_tag_and_commit_pins(tmp_path):
    """A ``_VENDOR_PINS`` commit pin takes its version from the vendored ``__version__``."""
    sys.path.insert(0, str(ROOT / "src/mailroom_eda/labeling/integrations"))
    try:
        import check_lockstep as cl
    finally:
        sys.path.pop(0)

    def rows(**kw):
        """``{check: agrees}`` for one fixture."""
        d = tmp_path / str(len(list(tmp_path.iterdir())))
        d.mkdir()
        eda, mr, sb, _ = _lockstep_fixture(d, **kw)
        return {c: ok for c, _, ok in cl.table(eda, mr, sb, rows=3292)}

    sha = "1cd4aba76d82eea20c6e8dfcc0d760881bf84924"
    assert all(rows(version_slot="v0.28.0").values())
    assert all(rows(version_slot=sha).values())                      # commit pin, vendored 0.28.0
    assert all(rows(version_slot=sha, mailroom_ref=sha).values())    # llm-mailroom pins the commit too
    assert not rows(version_slot=sha, vendored="0.27.2")["Dojo release version"]
    assert not rows(version_slot="f" * 40)["Dojo commit"]            # pin ref and frozen sha differ
    assert not rows(version_slot=sha, eda_commit=sha[:12])["Dojo commit"]  # full shas only


def test_inspect_reads_the_corrected_gold(tmp_path, monkeypatch):
    """inspect.md shows the gold the gate scores against: corrections in the labels root apply."""
    row = _row("correspondence", None, "Re-order your contact lenses online today.",
               gt={"intent": "request"}, fn="c/618.")
    d = tmp_path / "3.3_intent-calibration"
    d.mkdir()
    export.write_csv(tmp_path / operate.GOLD_CORRECTIONS, operate.GOLD_CORRECTION_COLUMNS,
                     [{"document_id": tasks._ids(row)[0], "filename": "c/618.", "field": "intent",
                       "gold_was": "request", "gold_now": "other", "reason": "marketing",
                       "decided_by": "user", "decided_utc": "t"}])
    rec = {"document_id": tasks._ids(row)[0], "filename": "c/618.", "model": "m",
           "pass_a": {"intent": "other", "confidence": 0.9, "evidence": "Re-order"},
           "pass_b": {"intent": "other", "confidence": 0.9, "evidence": "Re-order"}}
    (d / "intent-labels.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    monkeypatch.setattr(runner, "load_stage", lambda stage: [row])
    operate.cmd_inspect(type("A", (), {"dir": str(d), "stage": tmp_path, "n": 5})())
    md = (d / "inspect.md").read_text(encoding="utf-8")
    assert "gold: `other`" in md and "0 disagree" in md


@pytest.mark.parametrize("existing,subtype,answers,decision,new", [
    ("update", "email", ["notice"] * 4, "change", "notice"),
    ("notice", "notice", ["notice"] * 4, "confirm", "notice"),
    ("update", "press_release", ["update"] * 4, "adjudicate", ""),   # the subtype disagrees
    ("request", "demand", ["request"] * 4, "confirm", "request"),     # a demand need not be a payment
    ("payment_demand", "attorney_demand", ["request"] * 4, "change", "request"),
    ("update", "email", ["update", "notice", "update", "update"], "keep", "update"),
    ("other", "email", ["update", "notice", "update", "update"], "adjudicate", ""),
    ("update", "meeting_request", ["update", "meeting_invite"] * 2, "adjudicate", ""),
])
def test_relabel_decision(existing, subtype, answers, decision, new):
    """Only a unanimous answer that fits the subtype is written; a split keeps the existing
    label when one answer is it; a subtype conflict is read by a person."""
    got = operate.relabel_decision(existing, subtype, answers)
    assert got[:2] == (decision, new)


def test_relabel_set_is_every_machine_made_intent():
    rows = [_row("correspondence", "email", "x", {"intent_status": s}, fn=f"{s}.") for s in
            ("manual", "auto_labeled", "flagged_review")] + [_row("contract", None, "x", fn="c.")]
    assert [r["filename"] for r in runner.select(rows, "intent", "relabel")] == ["auto_labeled.", "flagged_review."]


def test_intent_relabel_gate_needs_both_models_and_every_adjudication_decided(tmp_path):
    a = _row("correspondence", "email", "x", {"intent": "update", "intent_source": "llm_zero_shot"}, fn="a.",
             split="train")
    b = _row("correspondence", "press_release", "y", {"intent": "update", "intent_source": "heuristic"}, fn="b.",
             split="train")
    models = ["deepseek/m", "anthropic/h"]

    def rec(row, model, x, y):
        return {"document_id": tasks._ids(row)[0], "filename": row["filename"], "model": model,
                "pass_a": {"intent": x, "evidence": "q"}, "pass_b": {"intent": y, "evidence": "q"}}
    recs = [rec(a, m, "notice", "notice") for m in models] + [rec(b, models[0], "update", "update")]
    path = tmp_path / "intent-labels.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    res = operate.gate_intent_relabel([a, b], tmp_path, models, signed_off=True)
    assert res["unanswered_documents"] == 1 and not res["passed"]
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec(b, models[1], "update", "update")) + "\n")
    res = operate.gate_intent_relabel([a, b], tmp_path, models, signed_off=True)
    assert res["decisions"] == {"change": 1, "adjudicate": 1} and res["open_adjudications"] == 1
    assert not res["passed"]
    rows = export.read_csv(tmp_path / "decisions.csv")
    for r in rows:
        if r["decision"] == "adjudicate":
            r["reviewer_decision"] = "press_communication"
    export.write_csv(tmp_path / "decisions.csv", operate.RELABEL_COLUMNS, rows)
    assert not operate.gate_intent_relabel([a, b], tmp_path, models, signed_off=False)["passed"]
    res = operate.gate_intent_relabel([a, b], tmp_path, models, signed_off=True)
    assert res["passed"]  # the reviewer's decision survived the re-gate


def test_relabel_never_writes_a_test_split_change_without_a_person(tmp_path):
    """The test split scores models later: a change the two models agree on is only proposed
    there, and its adjudication needs a person's initials."""
    t = _row("correspondence", "email", "x", {"intent": "update", "intent_source": "llm_zero_shot"}, fn="t.")
    models = ["deepseek/m", "anthropic/h"]
    recs = [{"document_id": tasks._ids(t)[0], "filename": "t.", "model": m,
             "pass_a": {"intent": "notice", "evidence": "q"}, "pass_b": {"intent": "notice", "evidence": "q"}}
            for m in models]
    (tmp_path / "intent-labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    res = operate.gate_intent_relabel([t], tmp_path, models, signed_off=True)
    row = export.read_csv(tmp_path / "decisions.csv")[0]
    assert res["decisions"] == {"adjudicate": 1} and row["new_intent"] == "" and "notice" in row["reason"]
    row["reviewer_decision"] = "notice"
    export.write_csv(tmp_path / "decisions.csv", operate.RELABEL_COLUMNS, [row])
    res = operate.gate_intent_relabel([t], tmp_path, models, signed_off=True)
    assert res["test_adjudications_without_initials"] == 1 and not res["passed"]
    row["reviewed_by"] = "JJB"
    export.write_csv(tmp_path / "decisions.csv", operate.RELABEL_COLUMNS, [row])
    assert operate.gate_intent_relabel([t], tmp_path, models, signed_off=True)["passed"]


def test_intent_relabel_runs_exactly_two_models():
    rows = [_row("correspondence", "email", "Please send the report.", {"intent_status": "auto_labeled"})]
    kw = dict(task="intent", chunk_spec=None, provider="openrouter", reasoning="off", initials=None, cap_usd=None,
              max_wall_seconds=None, out_dir=Path("/nonexistent"), dry_run=True)
    two = ["deepseek/deepseek-v4.1-flash-1017", "anthropic/claude-haiku-5.5"]
    assert runner.execute(rows, rows, set_name="relabel", models=two, **kw) == 0
    with pytest.raises(SystemExit, match="second-model"):
        runner.execute(rows, rows, set_name="relabel", models=two[:1], **kw)
    with pytest.raises(SystemExit, match="runs one model"):
        runner.execute(rows, rows, set_name="flagged", models=two, **kw)


def test_a_winner_approved_on_another_prompt_does_not_run(monkeypatch):
    """A prompt edit after a bake-off approval needs a new calibration before the winner is used."""
    row = _row("correspondence", "email", "Please send the report.", {"intent_status": "auto_labeled"})
    want = tasks.build("intent", row, "a", set_name="relabel").prompt_sha256
    entry = {"id": "4a", "task": "intent", "set": "relabel", "models": ["winner_of_3", "anthropic/claude-haiku-5.5"],
             "reasoning": "winner_of_3"}
    monkeypatch.setattr(operate, "load_approvals", lambda name: [{"chunk": "3", "prompt_sha256": ["old"]}])
    assert "re-run chunk 3" in operate.stale_winner("v10", entry, [row])
    monkeypatch.setattr(operate, "load_approvals", lambda name: [{"chunk": "3", "prompt_sha256": ["old", want]}])
    assert operate.stale_winner("v10", entry, [row]) == ""
    assert operate.stale_winner("v10", {**entry, "models": ["m"], "reasoning": "off"}, [row]) == ""


def test_cuad_inspect_reads_string_gold_spans():
    assert operate._span_text("the licensee shall not") == "the licensee shall not"
    assert operate._span_text({"text": "a span"}) == "a span" and operate._span_text(None) == ""


def test_no_step_or_chunk_needs_a_skipped_chunk():
    """A skipped chunk is never approved, so anything that needs it could never start."""
    camp = campaign("v10")
    skipped = {str(c["id"]) for c in camp["chunks"] if c.get("skip")
               and all(o.get("skip") for o in camp["chunks"] if str(o["id"]) == str(c["id"]))}
    for c in [*camp["chunks"], *(camp.get("steps") or [])]:
        assert not skipped & {str(n) for n in c.get("needs") or []}, (c["id"], skipped)


# ---------------------------------------------------------------- mixed providers (Modal + OpenRouter)

QWEN, DS = "Qwen/Qwen3-32B-FP8", "deepseek/deepseek-v4.1-flash"


def test_chat_timeout_follows_the_provider():
    """A Modal request may wait out a cold start before a long prefill; OpenRouter keeps 300 s."""
    seen = {}

    def post(url, key, body, timeout):
        seen[url] = timeout
        return 200, {}, {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": {}}

    providers.chat(providers.Backend("modal", "https://x.modal.run/v1", "k"), QWEN, [], None, post=post)
    providers.chat(providers.Backend("openrouter", "https://or/api/v1", "k"), DS, [], None, post=post)
    assert seen == {"https://x.modal.run/v1/chat/completions": providers.TIMEOUT_SECONDS["modal"],
                    "https://or/api/v1/chat/completions": 300.0}
    assert providers.TIMEOUT_SECONDS["modal"] >= 1200


def test_count_tokens_asks_vllm_tokenize():
    """The exact prompt length after the chat template, and the served window, from /tokenize."""
    seen = {}

    def post(url, key, body, timeout):
        seen.update(url=url, body=body)
        return 200, {}, {"count": 1234, "max_model_len": 32768, "tokens": []}

    msgs = [{"role": "user", "content": "hi"}]
    got = providers.count_tokens(providers.Backend("modal", "https://x.modal.run/v1", "k"), QWEN, msgs, post=post)
    assert got == (1234, 32768)
    assert seen["url"] == "https://x.modal.run/tokenize"  # vLLM serves it at the root, not under /v1
    assert seen["body"]["messages"] == msgs and seen["body"]["add_generation_prompt"] is True
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    with pytest.raises(providers.ProviderError):
        providers.count_tokens(providers.Backend("modal", "https://x.modal.run/v1", "k"), QWEN, msgs,
                               post=lambda *a: (503, {}, {"error": "booting"}))
    with pytest.raises(ValueError):
        providers.count_tokens(providers.Backend("openrouter", "https://or/api/v1", "k"), DS, msgs, post=post)


def test_budget_charges_modal_gpu_time_not_tokens():
    """A Modal model costs its GPU-seconds (first send to last answer, plus the scale-down tail)."""
    now = [0.0]
    b = spend.Budget(1.0, 3600, clock=lambda: now[0])
    rate, tail = spend.gpu_spec(QWEN)
    assert rate == pytest.approx(3.95 / 3600) and tail == 120
    assert spend.gpu_spec(DS) is None
    r = b.reserve(QWEN, 30_000, 3_000)
    assert r == 0.0  # no token reservation: the meter books the time
    now[0] = 100.0
    assert b.settle(QWEN, 30_000, 900, None, r) == 0.0
    assert b.spent_usd == 0.0 and b.gpu_seconds() == pytest.approx(220.0)
    assert b.total_usd() == pytest.approx(220 * 3.95 / 3600)
    b.reserve(DS, 1000, 100)
    b.settle(DS, 1000, 100, 0.01)
    assert b.total_usd() == pytest.approx(0.01 + 220 * 3.95 / 3600)
    tight = spend.Budget(0.20, 3600, clock=lambda: now[0])
    now[0] = 0.0
    tight.reserve(QWEN, 10, 10)
    now[0] = 60.0  # 60 s + the 120 s tail = $0.1975: the next call could pass the cap
    tight.settle(QWEN, 10, 10, None)
    now[0] = 70.0
    with pytest.raises(spend.SpendError, match="GPU"):
        tight.reserve(QWEN, 10, 10)


def _mixed_answers(rows):
    req = tasks.build("correspondence", rows[2])
    ans = {"fields": {f: {"value": None, "status": "not_found", "evidence": ""} for f in req.fields}}
    return {QWEN: ans, DS: ans}


def test_mixed_chunk_sends_each_model_to_its_own_backend(tmp_path, rows):
    """Qwen goes to Modal, DeepSeek to OpenRouter; each call records its provider; GPU time is booked."""
    sent = []
    answers = _mixed_answers(rows)

    def chat(backend, model, messages, schema, *, reasoning, max_tokens):
        sent.append((model, backend.provider))
        return providers.Completion(json.dumps(answers[model]), answers[model], model, "", 100, 50, 0,
                                    None if backend.provider == "modal" else 0.0001, "stop", True)

    backends = {QWEN: providers.Backend("modal", "https://x.modal.run/v1", "k"),
                DS: providers.Backend("openrouter", "https://or/api/v1", "k")}
    res = runner.run_chunk([rows[2]], task="correspondence", models=[QWEN, DS], backends=backends,
                           reasoning="off", budget=spend.Budget(1.0, 3600), out_dir=tmp_path, chat=chat,
                           count_tokens=lambda b, m, msgs, **kw: (500, 32768))
    assert set(sent) == {(DS, "openrouter"), (QWEN, "modal")} and len(sent) == 2
    calls = {c["model"]: c for c in export.read_journal(tmp_path / "calls.jsonl")}
    assert calls[QWEN]["provider"] == "modal" and calls[DS]["provider"] == "openrouter"
    assert res["calls"] == 2 and res["gpu_seconds"] >= 120 and res["gpu_usd"] > 0
    assert res["spent_usd"] == pytest.approx(0.0001 + res["gpu_usd"], abs=1e-4)


def test_over_context_request_is_reported_not_sent(tmp_path, rows):
    """A prompt that cannot fit the served window with its answer budget is journaled, never sent."""
    sent = []

    def chat(backend, model, *a, **kw):
        sent.append(model)
        raise AssertionError("must not be sent")

    res = runner.run_chunk([rows[2]], task="correspondence", models=[QWEN],
                           backends={QWEN: providers.Backend("modal", "https://x.modal.run/v1", "k")},
                           reasoning="off", budget=spend.Budget(1.0, 3600), out_dir=tmp_path, chat=chat,
                           count_tokens=lambda b, m, msgs, **kw: (32_000, 32_768))
    assert sent == [] and res["over_context"] == 1 and not res.get("errors")
    (rec,) = export.read_journal(tmp_path / "calls.jsonl")
    assert rec["error"].startswith("over_context: 32000 prompt") and "32768" in rec["error"]


def test_mixed_models_swaps_one_campaign_model_and_reads_each_provider():
    """--provider mixed --model-map A=B: B replaces A; each model's provider comes from models.yaml."""
    models, prov = operate.mixed_models(["z-ai/glm-5.3-flashx-legal", DS], [f"{DS}={QWEN}"])
    assert models == ["z-ai/glm-5.3-flashx-legal", QWEN]
    assert prov == {"z-ai/glm-5.3-flashx-legal": "openrouter", QWEN: "modal"}
    with pytest.raises(SystemExit, match="not a model of this chunk"):
        operate.mixed_models([DS], [f"not/a-model={QWEN}"])
    with pytest.raises(SystemExit, match="not an approved"):
        operate.mixed_models([DS], [f"{DS}=not/a-model"])
    with pytest.raises(SystemExit, match="one provider"):
        operate.mixed_models([DS], [])  # nothing mixed: run it on that provider instead


def test_calibration_matches_each_models_provider():
    """A mixed chunk is calibrated only by a calibration that ran each model on the same provider."""
    base = {"task": "merger_consideration", "reasoning": "off", "prompt_sha256": ["p1"],
            "temperature": {QWEN: 0, DS: 0}, "models": [QWEN, DS]}
    mixed = {**base, "chunk": "6c1", "set": "pending", "provider": "mixed",
             "providers": {QWEN: "modal", DS: "openrouter"}}
    cal = {**base, "chunk": "6c0", "set": "calibration", "provider": "mixed",
           "providers": {QWEN: "modal", DS: "openrouter"}}
    assert ingest.configuration(QWEN, mixed).startswith(f"{QWEN}@modal/")
    assert ingest.configuration(DS, mixed).startswith(f"{DS}@openrouter/")
    ingest.check_calibrated([cal, mixed])
    wrong = {**cal, "provider": "openrouter", "providers": None}
    with pytest.raises(SystemExit, match="uncalibrated"):
        ingest.check_calibrated([wrong, mixed])


def test_gate_and_approval_keep_each_models_provider(tmp_path, monkeypatch, rows):
    """A mixed chunk's gate and approval record where each model ran, so ingest can match it."""
    entry = {"id": "7a", "task": "correspondence", "set": "pilot", "models": [DS, "minimax/minimax-m3.1-flash"],
             "reasoning": "off", "cap_usd": 0.25, "gate": {"kind": "human_verdicts", "precision": 0.9}}
    row = rows[2]
    monkeypatch.setattr(runner, "load_stage", lambda stage: [row])
    monkeypatch.setattr(operate, "chunk_entry", lambda name, cid, arm: (entry, None))
    out_dir = operate.chunk_dir(tmp_path, entry, None)
    answers = _mixed_answers(rows)
    prov = {QWEN: "modal", DS: "openrouter"}
    runner.run_chunk([row], task="correspondence", models=[QWEN, DS], reasoning="off",
                     backends={QWEN: providers.Backend("modal", "https://x.modal.run/v1", "k"),
                               DS: providers.Backend("openrouter", "https://or/api/v1", "k")},
                     budget=spend.Budget(1.0, 3600), out_dir=out_dir, chat=_fake_chat(answers),
                     count_tokens=lambda *a, **kw: (100, 32768))
    export.append_journal(out_dir / "runs.jsonl", {"chunk": "7a", "models": [QWEN, DS], "provider": "mixed",
                                                   "providers": prov, "started_utc": "2026-11-06T00:00:00Z"})
    res = operate.gate_result("v10", entry, None, tmp_path, tmp_path)
    assert res["provider"] == "mixed" and res["providers"] == prov
    assert {ingest.configuration(m, res).split("@")[1].split("/")[0] for m in res["models"]} == {"modal",
                                                                                                    "openrouter"}


def test_modal_projection_is_gpu_time_and_a_cap_below_a_cold_start_is_refused(tmp_path, rows):
    """The dry run prices Modal by GPU time (cold start + per-call slots + tail); a cap that cannot
    cover the cold start would buy a boot and no labels, so the run is refused before it starts."""
    spec = models_registry()["models"][QWEN]
    est = runner.estimate([rows[2]], "correspondence", [QWEN, DS])
    seconds = spec["cold_boot_seconds"] + spec["scaledown_seconds"] + spec["seconds_per_call"] / spec["in_flight"]
    assert est["cost_usd"][QWEN] == pytest.approx(round(seconds * spec["gpu_usd_per_hour"] / 3600, 4))
    assert est["gpu_seconds"] == {QWEN: pytest.approx(seconds)}
    with pytest.raises(SystemExit, match="cold start"):
        runner.execute([rows[2]], [rows[2]], task="correspondence", set_name="pilot", chunk_spec=None,
                       models=[QWEN, DS], provider={QWEN: "modal", DS: "openrouter"}, reasoning="off",
                       initials="JB", cap_usd=0.35, max_wall_seconds=600, out_dir=tmp_path)


def test_sweep_plan_can_focus_on_tasks_and_a_blocked_task_does_not_stop_the_others():
    """--task keeps only those tasks' chunks; a chunk waiting on approval blocks only its own task."""
    plan, why = operate.sweep_plan("v10", [], "9e", tasks={"correspondence"})
    assert [str(e["id"]) for e, _ in plan] == ["7a"] and "7b" in why
    plan, why = operate.sweep_plan("v10", [], "9e", tasks={"correspondence", "claim_checklist", "corporate"})
    assert [str(e["id"]) for e, _ in plan] == ["7a", "8a", "9a"]
    plan, _ = operate.sweep_plan("v10", [{"chunk": "7a"}], "9e", tasks={"correspondence"})
    assert [str(e["id"]) for e, _ in plan] == ["7b", "7c", "7d", "7e"]
    with pytest.raises(SystemExit, match="no chunk of task"):
        operate.sweep_plan("v10", [], "9e", tasks={"nonsense"})


def test_sweep_runs_the_chunks_credit_likely_covers(tmp_path, monkeypatch, capsys):
    """Chunks are picked by projected cost; each runs capped at the live credit left, the rest wait."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    remaining = iter([1.80, 1.80, 1.20, 0.50])  # projection, then before 7b, 7c, 7d
    monkeypatch.setattr(providers, "key_status", lambda backend: {"limit_remaining": next(remaining)})
    monkeypatch.setattr(operate, "load_approvals", lambda name: [{"chunk": "7a"}])
    monkeypatch.setattr(runner, "load_stage", lambda stage: [])
    monkeypatch.setattr(operate, "_sweep_projection", lambda args, plan, rows: [
        {"chunk": str(e["id"]), "task": e["task"], "set": e["set"], "documents": 1, "calls": 2,
         "projected_usd": 0.60, "cap_usd": float(e["cap_usd"]), "reasoning": "off"} for e, _ in plan])
    ran = []
    assert operate.cmd_sweep(_sweep_args(tmp_path), confirm=lambda q: "yes",
                             run_chunk=lambda ns: ran.append((ns.id, ns.cap_usd)) or 0) == 0
    assert ran == [("7b", None), ("7c", None), ("7d", 0.50)]  # 3 x $0.60 projected fit $1.80
    out = capsys.readouterr().out
    assert "deferred" in out and "7e" in out
    monkeypatch.setattr(providers, "key_status", lambda backend: {"limit_remaining": 0.50})
    with pytest.raises(SystemExit, match="covers no chunk"):
        operate.cmd_sweep(_sweep_args(tmp_path), confirm=lambda q: "yes", run_chunk=lambda ns: 0)
def test_contract_entities_reads_long_contracts_in_full(rows):
    """Contract entities see up to the CUAD window, so a 100k-character middle is read."""
    filler = "x" * 50_000
    row = {**rows[0], "doc_text": rows[0]["doc_text"] + filler + " PAYMENT-MIDDLE-MARKER " + filler}
    assert "PAYMENT-MIDDLE-MARKER" in tasks.build("contract_entities", row, set_name="pilot").messages[1]["content"]
    huge = {**rows[0], "doc_text": rows[0]["doc_text"] + "x" * 500_000}
    assert "omitted" in tasks.build("contract_entities", huge, set_name="pilot").messages[1]["content"]


def test_a_contract_read_in_part_records_no_absence(rows):
    """Both models finding nothing in a windowed contract leaves the field pending, not absent."""
    models = ["z-ai/glm-5.3-flashx-legal", "deepseek/deepseek-v4.1-flash"]
    short = rows[0]
    long_ = {**rows[0], "document_id": "c2", "filename": "c2.txt", "doc_text": rows[0]["doc_text"] + "x" * 500_000}
    calls = []
    for r in (short, long_):
        fields = {n: {"value": None, "status": "not_found", "evidence": ""}
                  for n in tasks._task_fields(r, "contract_entities")}
        calls += [{"document_id": r["document_id"], "model": m, "variant": "", "provider": "openrouter", "reasoning": "off", "prompt_sha256": "p", "labeled_utc": "2026-11-06T00:00:00Z", "response": {"fields": fields}}
                  for m in models]
    field_rows, _ = runner.field_decisions([short, long_], calls, "contract_entities", models)
    absent_ok = {n for n, s in tasks._task_fields(short, "contract_entities").items() if s["absence"] == "absent_ok"}
    by = {(f["document_id"], f["field"]): f["decision"] for f in field_rows}
    assert {by[("c1", n)] for n in absent_ok} == {"absent"}
    assert {by[("c2", n)] for n in absent_ok} == {"unlabeled"}


def test_select_refuses_subclass_on_fixed_samples(rows):
    """A subclass filter on a fixed sample (intent sets, CUAD calibration) is refused, not ignored."""
    with pytest.raises(SystemExit, match="fixed sample"):
        runner.select(rows, "intent", "calibration", ["financial"])
    with pytest.raises(SystemExit, match="fixed sample"):
        runner.select(rows, "cuad", "holdout", "license")


def test_campaign_costs_counts_each_subclass_chunk(rows, monkeypatch):
    """Subclass-split chunks of one task/set each enter the whole-campaign totals."""
    claim = next(r for r in rows if r["expected"] == "insurance_claim")
    rows = [*rows, {**claim, "expected_subclass": "carrier", "document_id": "i2", "filename": "i2.txt"}]
    subs = ["carrier", "outpatient"]
    base = {"task": "claim_checklist", "set": "all", "models": ["minimax/minimax-m3.1-flash"],
            "reasoning": "off", "cap_usd": 1.0}
    camp = {"chunks": [{**base, "id": "x1", "subclass": [subs[0]]},
                       {**base, "id": "x2", "subclass": subs[1:]}]}
    monkeypatch.setattr(runner, "campaign", lambda name: camp)
    both = runner.campaign_costs(rows, "t")
    camp["chunks"] = camp["chunks"][:1]
    one = runner.campaign_costs(rows, "t")
    assert both["campaign_prompt_tokens"] > one["campaign_prompt_tokens"]
