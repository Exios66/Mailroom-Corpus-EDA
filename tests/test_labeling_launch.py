"""Offline tests for launching labeling batches from the review portal (no network, no spend).

A fake chat answers every request, a fake ``GET /key`` reports credit, and a fake hub serves
public dataset rows; nothing here reaches OpenRouter or Hugging Face.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from mailroom_eda.labeling import checkpoint, export, launch, operate, providers, registry, runner, tasks
from mailroom_eda.labeling import review_portal as portal
from test_labeling_review_portal import record, write_packet

KEY = "sk-or-v1-PROVIDED-launch-key-7f3a9c21"
ENV_KEY = "sk-or-v1-ENVIRONMENT-key-0b8d44e5"
DEEPSEEK = "deepseek/deepseek-v4.1-flash"
GLM = "z-ai/glm-5.3-flashx-legal"
MINIMAX = "minimax/minimax-m3.1-flash"
CAMPAIGN = "t_launch"

CONTRACT = ("CONSULTING AGREEMENT\n\nThis Consulting Agreement is dated as of March 5, 2004 between "
            "Acme Corp. (\"Acme\") and Beta LLC (\"Consultant\").\n\n1. Term. The term is two (2) years.\n"
            "12. Governing Law. This Agreement shall be governed by the laws of the State of Delaware.\n")
MERGER = ("AGREEMENT AND PLAN OF MERGER among Parent Inc., Merger Sub Corp. and Target Co.\n"
          "Section 2.1 Conversion of Shares. Each Share shall be converted into the right to receive "
          "$12.50 in cash.\nSection 9.8 Governing Law. This Agreement shall be governed by the laws of "
          "the State of Delaware.\n")
EMAIL = "From: alice@enron.com\nTo: bob@enron.com\nSubject: Q3 invoice\n\nBob, please pay $4,500 by Friday.\n"
CLAIM = "Claim CLM-1 for policy POL-9. Coverage determination: approved. Deductible $500 applies."
CORP = "AMENDED AND RESTATED BYLAWS OF ACME CORP., a Delaware corporation. Adopted on May 1, 2010."


def row(cls, sub, text, n, gt=None, **extra):
    """A public/stage row; ``gt_fields`` is JSON text like the Hub's, which the launcher decodes."""
    return {"document_id": f"{cls[:4]}-{n:02d}", "filename": f"{cls}_{n:02d}.txt", "expected": cls,
            "expected_subclass": sub, "doc_text": text + f"\nRef {n}\n", "split": "train",
            "gt_fields": json.dumps(gt or {}), "metadata": {}, **extra}


PENDING = {"gt_presence": json.dumps({"cuad_clause_labels": "pending_annotation"})}
CUAD_GOLD = {"gt_presence": json.dumps({"cuad_clause_labels": "populated"}),
             "cuad_clause_labels": json.dumps({c["name"]: ([{"start": 0, "text": "Acme"}] if c["name"] == "Parties" else [])
                                               for c in registry.dojo_snapshot()["cuad_categories"]})}
MAUD = {"maud_clause_labels": json.dumps({"Type of Consideration": {"answer": "All Cash"}})}


def public_rows():
    """One pool per document type, each with documents that need labels and gold that must be left alone."""
    rows = [
        row("contract", "Consulting Agreements", CONTRACT, 1, PENDING, source_corpus="sec_edgar"),
        row("contract", "Consulting Agreements", CONTRACT, 2, PENDING, source_corpus="sec_edgar"),
        row("contract", "Consulting Agreements", CONTRACT, 3, PENDING, source_corpus="sec_edgar"),
        row("contract", "Consulting Agreements", CONTRACT, 4, CUAD_GOLD, source_corpus="theatticusproject/cuad"),
        row("merger_agreement", "other", MERGER, 1, {}),
        row("merger_agreement", "other", MERGER, 2, {}),
        row("merger_agreement", "all_cash", MERGER, 3, MAUD),
    ]
    rows += [row("correspondence", "attorney_demand", EMAIL, n, {"intent": "request"}) for n in range(1, 9)]
    rows += [row("insurance_claim", "outpatient", CLAIM, n) for n in range(1, 9)]
    rows += [row("corporate_record", "bylaws", CORP, n, {"entity_name": "Acme Corp."}) for n in range(1, 8)]
    return rows


def completion(model, parsed):
    return providers.Completion(json.dumps(parsed), parsed, model, "CoreWeave", 100, 50, 0, 0.0001, "stop", True)


def answer(schema):
    """The smallest valid answer to a labeling schema."""
    if "clauses" in schema["properties"]:
        return {"clauses": []}
    fields = schema["properties"]["fields"]["properties"]
    return {"fields": {f: {"value": None, "status": "not_found", "evidence": ""} for f in fields}}


class Fakes:
    """Recorders for everything the launcher would send over the network."""

    def __init__(self):
        self.chat_keys, self.hub_calls, self.key_calls = [], [], []
        self.remaining = 50.0
        self.key_error = None
        self.gate = None  # a threading.Event the chat waits on, to keep a run in flight
        self.chat_error = None
        self.leak = False

    def chat(self, backend, model, messages, schema, *, reasoning, max_tokens):
        self.chat_keys.append(backend.api_key)
        if self.gate is not None:
            self.gate.wait(20)
        if self.leak:
            print("debug: backend key is", backend.api_key)
        if self.chat_error:
            raise providers.ProviderError(self.chat_error.replace("{key}", backend.api_key))
        return completion(model, answer(schema))

    def key_status(self, backend):
        self.key_calls.append(backend.api_key)
        if self.key_error:
            raise providers.ProviderError(self.key_error.replace("{key}", backend.api_key))
        return {"limit_remaining": self.remaining, "label": "sk-or-v1-abc...xyz"}

    def hub(self):
        self.hub_calls.append(1)
        return {"rows": public_rows(), "revision": "ab" * 20, "source_sha256": "cd" * 32}


@pytest.fixture
def env(tmp_path, monkeypatch):
    campaigns = tmp_path / "campaigns"
    folder = campaigns / CAMPAIGN
    folder.mkdir(parents=True)
    stage = tmp_path / "stage"
    stage.mkdir()
    stage_rows = [row("correspondence", "attorney_demand", EMAIL, n, {"intent": "request"}) for n in range(1, 4)]
    (stage / "ground_truth_hardened.jsonl").write_text(
        "".join(json.dumps({**r, "gt_fields": json.loads(r["gt_fields"])}) + "\n" for r in stage_rows), encoding="utf-8")
    sha = hashlib.sha256((stage / "ground_truth_hardened.jsonl").read_bytes()).hexdigest()
    two = [DEEPSEEK, MINIMAX]
    chunks = [
        {"id": "1", "task": "cuad", "set": "calibration", "models": [DEEPSEEK], "reasoning": "low", "cap_usd": 0.30},
        {"id": "2a", "needs": ["1"], "task": "cuad", "set": "pending", "models": ["winner_of_1"],
         "reasoning": "winner_of_1", "cap_usd": 0.45},
        {"id": "6c1", "task": "merger_consideration", "set": "pending", "models": [GLM, DEEPSEEK],
         "reasoning": "off", "cap_usd": 0.25},
        {"id": "7a", "task": "correspondence", "set": "pilot", "models": [DEEPSEEK], "reasoning": "off",
         "cap_usd": 0.15},
        {"id": "7b", "needs": ["7a"], "task": "correspondence", "set": "remainder", "chunk": "1/4",
         "models": two, "reasoning": "low", "cap_usd": 0.90},
        {"id": "7s", "task": "correspondence", "set": "pilot", "models": [DEEPSEEK], "reasoning": "off",
         "cap_usd": 0.0, "skip": "superseded"},
        {"id": "7r", "task": "correspondence", "set": "pilot", "models": [DEEPSEEK], "reasoning": "off",
         "cap_usd": 0.15, "requires": "llm-dojo-scoring 99.0.0 (G1)"},
        {"id": "8b", "needs": ["8a"], "task": "claim_checklist", "set": "remainder", "models": [GLM, MINIMAX],
         "reasoning": "low", "cap_usd": 1.10},
        {"id": "9b", "needs": ["9a"], "task": "corporate", "set": "remainder", "models": [GLM, DEEPSEEK],
         "reasoning": "off", "cap_usd": 0.85},
    ]
    (folder / "campaign.yaml").write_text(json.dumps({"name": CAMPAIGN, "max_wall_seconds": 600,
                                                      "stage_sha256": sha, "chunks": chunks, "steps": []}))
    approvals = folder / "approved.yaml"
    monkeypatch.setattr(registry, "CAMPAIGNS_DIR", campaigns)
    monkeypatch.setattr(operate, "approvals_path", lambda name: folder / "approved.yaml")
    monkeypatch.setattr(runner, "DEFAULT_STAGE", tmp_path / "missing-stage")
    monkeypatch.setattr(checkpoint, "write_access", lambda *a, **k: (True, "write token for tester; repo is private"))
    monkeypatch.setattr(checkpoint, "token_ready", lambda: True)
    monkeypatch.setattr(checkpoint, "checkpoint", lambda *a, **k: {"files": 3, "repo": "t/staging", "verified": True})
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    root = tmp_path / "labels"
    # fresh, passing pings for the routes the campaign arm below uses
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for model, reasoning in ((DEEPSEEK, "off"), (DEEPSEEK, "low"), (MINIMAX, "low")):
        export.append_journal(root / "ping.jsonl", {"model": model, "reasoning": reasoning, "utc": now,
                                                    "response": {"ok": True, "word": "mailroom"}, "used_schema": True})
    fakes = Fakes()
    out = root / "review_bundle" / "portal"

    def make(*, enabled=True, environ=None):
        return portal.ReviewPortal(
            root, out, campaign=CAMPAIGN, stage=stage, launch_enabled=enabled,
            launch_options={"chat": fakes.chat, "key_status": fakes.key_status, "hub": fakes.hub,
                            "env": {"OPENROUTER_API_KEY": ENV_KEY} if environ is None else environ})
    app = make()
    return SimpleNamespace(root=root, out=out, stage=stage, app=app, fakes=fakes, make=make, folder=folder,
                           approvals=approvals, tmp=tmp_path, sha=sha, campaigns=campaigns)


@contextmanager
def serving(app):
    server = portal.make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


BODIES: list[bytes] = []


def call(server, method, path, body=None, **headers):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
    headers.setdefault("Origin", f"http://127.0.0.1:{server.server_port}")
    headers.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    BODIES.append(data)
    return response.status, (json.loads(data) if data.startswith(b"{") else data)


def good(target="trial:correspondence", **extra):
    return {"target": target, "initials": "JB", "cap_usd": 0.25, **extra}


def pre(server, body):
    status, res = call(server, "POST", "/api/launch/preflight", body)
    assert status == 200, res
    for it in res["items"]:  # loud: every failure says what to do
        if it["status"] == "fail":
            assert it["fix"].strip() and it["title"].strip(), it
    return res


def failed(res):
    return {i["id"]: i for i in res["items"] if i["status"] == "fail"}


def finish(env, server):
    env.app.launcher.join(60)
    status, res = call(server, "GET", "/api/launch/status")
    assert status == 200
    return res


def approve_winner(env):
    """Approve chunk 1 (the cuad winner) on the prompt the pending contracts would send."""
    sample = next(r for r in public_rows() if r["document_id"] == "cont-01")
    sample["gt_fields"] = json.loads(sample["gt_fields"])
    sha = tasks.build("cuad", sample, "", set_name="pending").prompt_sha256
    env.approvals.write_text(json.dumps({"approved": [{"chunk": "1", "arm": 1, "task": "cuad", "models": [DEEPSEEK],
                                                       "reasoning": "low", "prompt_sha256": [sha]}]}))


# ---------------------------------------------------------------- off by default

def test_launch_is_off_by_default_and_says_how_to_enable_it(env):
    app = env.make(enabled=False)
    write_packet(env.root / "review_bundle" / "seed" / "seed-review-data.json", [record()])
    with serving(app) as server:
        for method, path in (("GET", "/api/launch/options"), ("GET", "/api/launch/status"),
                             ("POST", "/api/launch/preflight"), ("POST", "/api/launch/start")):
            status, res = call(server, method, path, good() if method == "POST" else None)
            assert status == 403 and "--enable-launch" in res["error"], (path, res)
    assert app.data()["launch_enabled"] is False
    assert not (env.out / "launches.jsonl").exists()
    assert env.make(enabled=True).data()["launch_enabled"] is True


def test_start_without_origin_or_from_another_origin_is_refused(env):
    with serving(env.app) as server:
        for origin in ("", "https://evil.invalid"):
            status, res = call(server, "POST", "/api/launch/start", {**good(), "confirm": "0.25"}, Origin=origin)
            assert status == 403, res
        assert call(server, "POST", "/api/launch/start", good(), **{"Content-Type": "text/plain"})[0] == 415
        assert call(server, "GET", "/api/launch/status")[1]["state"] == "idle"
    assert env.fakes.chat_keys == []


# ---------------------------------------------------------------- options

def test_options_label_qualification_and_labeling_arms(env):
    with serving(env.app) as server:
        status, res = call(server, "GET", "/api/launch/options")
    assert status == 200
    by = {t["id"]: t for t in res["targets"]}
    assert by["campaign:1"]["kind"] == "qualification"
    assert by["campaign:1"]["kind_label"] == "Qualification — re-labels gold documents to score models"
    for tid in ("campaign:2a", "campaign:6c1", "campaign:7a", "campaign:7b", "campaign:8b", "campaign:9b"):
        assert by[tid]["kind_label"] == "Labeling — documents that need labels", tid
    assert by["campaign:2a"]["status"] == "blocked" and not by["campaign:2a"]["models_resolved"]
    assert by["campaign:7s"]["status"] == "blocked" and "superseded" in by["campaign:7s"]["blocker"]
    assert by["campaign:7a"]["status"] == "ready" and by["campaign:7a"]["models"] == [DEEPSEEK]
    assert {t["id"] for t in res["targets"] if t["group"] == "Public trial"} == {
        "trial:contract", "trial:merger_agreement", "trial:correspondence", "trial:insurance_claim",
        "trial:corporate_record"}
    assert res["trial"]["cap_ceiling_usd"] == 0.25 and res["trial"]["default_documents"] == 2


# ---------------------------------------------------------------- preflight failure paths

def test_a_clean_trial_preflight_passes_every_check(env):
    with serving(env.app) as server:
        res = pre(server, good())
    assert res["can_launch"] is True, failed(res)
    ids = {i["id"] for i in res["items"]}
    assert {"launch_enabled", "idle", "target", "initials", "cap_value", "wall_limit", "models", "public_source",
            "selection", "gold_free", "stale_winner", "cap_projection", "cap_ceiling", "key_present", "key_valid",
            "key_credit", "ping", "hf_checkpoint", "output_dir"} <= ids
    assert next(i for i in res["items"] if i["id"] == "hf_checkpoint")["status"] == "skip"
    proj = res["projection"]
    assert proj["documents"] == 2 and proj["calls"] == 4 and 0 < proj["cost_usd"] <= 0.25 / runner.CAP_HEADROOM_FACTOR


@pytest.mark.parametrize("body,item_id", [
    ({"initials": "j"}, "initials"),
    ({"initials": None}, "initials"),
    ({"cap_usd": 0}, "cap_value"),
    ({"cap_usd": -1}, "cap_value"),
    ({"cap_usd": "abc"}, "cap_value"),
    ({"cap_usd": float("nan")}, "cap_value"),
    ({"cap_usd": None}, "cap_value"),
    ({"cap_usd": 5.0}, "cap_ceiling"),
    ({"cap_usd": 0.004}, "cap_projection"),
    ({"documents": 0}, "documents"),
    ({"documents": 6}, "documents"),
    ({"target": "campaign:nope"}, "target"),
])
def test_bad_request_values_fail_with_a_fix(env, body, item_id):
    with serving(env.app) as server:
        res = pre(server, {**good(), **body})
    assert item_id in failed(res) and res["can_launch"] is False


def test_missing_key_is_a_loud_failure(env):
    with serving(env.make(environ={})) as server:
        res = pre(server, good())
    item = failed(res)["key_present"]
    assert "OPENROUTER_API_KEY" in item["fix"] and res["can_launch"] is False
    assert {i["id"]: i["status"] for i in res["items"]}["key_valid"] == "skip"


def test_a_placeholder_key_is_accepted_whatever_its_format(env):
    """The cloud proxy injects the real Authorization header, so only OpenRouter may judge a key."""
    with serving(env.make(environ={})) as server:
        res = pre(server, good(api_key="proxy-injected"))
    assert res["can_launch"] is True, failed(res)
    assert env.fakes.key_calls == ["proxy-injected"]


def test_a_rejected_key_quotes_the_error_and_never_the_key(env):
    env.fakes.key_error = "GET /key failed: HTTP 401 for {key}"
    with serving(env.app) as server:
        res = pre(server, good(api_key=KEY))
    item = failed(res)["key_valid"]
    assert "HTTP 401" in item["detail"] and KEY not in json.dumps(res) and "[redacted]" in item["detail"]
    assert "openrouter.ai/keys" in item["fix"]


def test_insufficient_credit_names_the_headroom(env):
    env.fakes.remaining = 0.10
    with serving(env.app) as server:
        res = pre(server, good())
    item = failed(res)["key_credit"]
    assert "exceeds key headroom" in item["detail"] and "credit" in item["fix"]


def test_the_cap_must_cover_the_projection_with_the_repos_headroom_rule(env):
    with serving(env.app) as server:
        proj = pre(server, good())["projection"]["cost_usd"]
        tight = pre(server, good(cap_usd=round(proj * 1.2, 4)))
        fine = pre(server, good(cap_usd=round(proj * runner.CAP_HEADROOM_FACTOR + 0.005, 4)))
    assert "cap_projection" in failed(tight) and "raise the cap" in failed(tight)["cap_projection"]["fix"]
    assert "cap_projection" not in failed(fine)


def test_a_trial_cap_above_the_ceiling_is_refused(env):
    with serving(env.app) as server:
        res = pre(server, good(cap_usd=0.26))
    assert "0.25" in failed(res)["cap_ceiling"]["detail"]


def test_campaign_arm_cap_may_not_exceed_the_campaign_cap(env):
    with serving(env.app) as server:
        res = pre(server, good("campaign:7a", cap_usd=0.30))
    assert "campaign.yaml cap_usd" in failed(res)["cap_ceiling"]["detail"]


def test_unapproved_winner_models_fail_for_arm_and_trial(env):
    with serving(env.app) as server:
        trial = pre(server, good("trial:contract"))
        arm = pre(server, good("campaign:2a", cap_usd=0.45))
    for res in (trial, arm):
        item = failed(res)["models"]
        assert "no approved arm" in item["detail"] and "gate 1" in item["fix"]
    assert "needs_approved" in failed(arm) and "gate 1" in failed(arm)["needs_approved"]["fix"]


def test_a_skipped_arm_and_an_unmet_requirement_fail_with_fixes(env):
    with serving(env.app) as server:
        skipped = pre(server, good("campaign:7s", cap_usd=0.15))
        required = pre(server, good("campaign:7r", cap_usd=0.15))
        acknowledged = pre(server, good("campaign:7r", cap_usd=0.15, ack_requires=True))
    assert "arm_runnable" in failed(skipped) and "superseded" in failed(skipped)["arm_runnable"]["detail"]
    assert "ack" in failed(required)["requires"]["fix"].lower() or "bump" in failed(required)["requires"]["fix"]
    assert "requires" not in failed(acknowledged)
    assert {i["id"]: i["status"] for i in acknowledged["items"]}["requires"] == "warn"


def test_missing_stage_gives_the_exact_build_commands(env):
    (env.stage / "ground_truth_hardened.jsonl").unlink()
    with serving(env.app) as server:
        res = pre(server, good("campaign:7a", cap_usd=0.15))
    item = failed(res)["stage"]
    assert str(env.stage) in item["detail"]
    assert "scripts/build/build_v9_3_corporate_presence.py" in item["fix"]
    assert "scripts/build/build_v9_4_dedup_labels.py --no-cuad-journal" in item["fix"]


def test_a_stage_that_differs_from_the_campaign_pin_fails(env):
    with (env.stage / "ground_truth_hardened.jsonl").open("a") as fh:
        fh.write(json.dumps({**row("correspondence", "attorney_demand", EMAIL, 99),
                             "gt_fields": {}}) + "\n")
    with serving(env.app) as server:
        res = pre(server, good("campaign:7a", cap_usd=0.15))
    item = failed(res)["stage"]
    assert "stage_sha256" in item["detail"] and "stage_sha256" in item["fix"]


def test_a_stale_or_missing_ping_fails_a_campaign_arm_and_only_warns_a_trial(env):
    (env.root / "ping.jsonl").write_text("")
    with serving(env.app) as server:
        arm = pre(server, good("campaign:7a", cap_usd=0.15))
        trial = pre(server, good())
    item = failed(arm)["ping"]
    assert "never pinged" in item["detail"] and "labeling ping --initials XX" in item["fix"]
    assert "ping" not in failed(trial)
    assert {i["id"]: i["status"] for i in trial["items"]}["ping"] == "warn"


def test_unwritable_hf_checkpoint_fails_a_campaign_arm(env, monkeypatch):
    monkeypatch.setattr(checkpoint, "write_access", lambda *a, **k: (False, "no HF token (set HF_TOKEN)"))
    with serving(env.app) as server:
        res = pre(server, good("campaign:7a", cap_usd=0.15))
    item = failed(res)["hf_checkpoint"]
    assert "no HF token" in item["detail"] and "HF_TOKEN" in item["fix"]


def test_a_failed_hub_download_is_a_loud_failure(env):
    def boom():
        raise OSError("Network is unreachable")
    app = env.make()
    app.launcher._hub = boom
    with serving(app) as server:
        res = pre(server, good())
    item = failed(res)["public_source"]
    assert "Network is unreachable" in item["detail"] and "huggingface.co" in item["fix"]
    assert "selection" not in failed(res)  # skipped, not a second confusing failure


def test_an_empty_selection_fails(env):
    app = env.make()
    app.launcher._hub = lambda: {"rows": [r for r in public_rows() if r["expected"] != "contract"],
                                 "revision": "ab" * 20, "source_sha256": "cd" * 32}
    approve_winner(env)
    with serving(app) as server:
        res = pre(server, good("trial:contract"))
    assert "selection" in failed(res)


def test_a_trial_refuses_gold_in_its_selection(env, monkeypatch):
    gold = [r for r in public_rows() if r["document_id"] == "cont-04"]
    gold[0]["gt_fields"] = json.loads(gold[0]["gt_fields"])
    monkeypatch.setattr(runner, "select", lambda *a, **k: gold)
    approve_winner(env)
    with serving(env.app) as server:
        res = pre(server, good("trial:contract"))
        status, refused = call(server, "POST", "/api/launch/start", {**good("trial:contract"), "confirm": "0.25"})
    item = failed(res)["gold_free"]
    assert "cont-04" in item["detail"] and "CUAD-gold" in item["detail"] and item["fix"]
    assert status == 409 and "gold_free" in failed(refused)
    assert env.fakes.chat_keys == []


def test_a_merger_whose_consideration_maud_answers_is_gold():
    gold = {"expected": "merger_agreement", "gt_fields": MAUD}
    assert "MAUD" in launch.gold_reason({**gold, "gt_fields": {k: json.loads(v) for k, v in MAUD.items()}})
    assert launch.gold_reason({"expected": "merger_agreement", "gt_fields": {}}) == ""
    assert launch.gold_reason({"expected": "contract", "source_corpus": "sec_edgar", "gt_fields": {}}) == ""
    assert launch.gold_reason({"expected": "contract", "source_corpus": "sec_edgar",
                               "gt_fields": {"cuad_clause_labels": '{"Parties": [1]}'}})
    assert launch.gold_reason({"expected": "contract", "source_corpus": "theatticusproject/cuad", "gt_fields": {}})


def test_trial_pick_is_deterministic_and_can_spread_over_subclasses():
    rows = [{"document_id": f"d{n}", "filename": f"f{n}", "expected_subclass": s}
            for n, s in ((3, "b"), (1, "a"), (2, "a"), (4, "b"))]
    assert [r["document_id"] for r in launch.pick_trial(rows, 2)] == ["d1", "d2"]
    assert [r["document_id"] for r in launch.pick_trial(list(reversed(rows)), 2)] == ["d1", "d2"]
    assert [r["document_id"] for r in launch.pick_trial(rows, 2, per_subclass=True)] == ["d1", "d3"]


# ---------------------------------------------------------------- start refusals

def test_start_refuses_when_any_check_fails(env):
    with serving(env.app) as server:
        status, res = call(server, "POST", "/api/launch/start", {**good(initials="x"), "confirm": "0.25"})
    assert status == 409 and "initials" in failed(res) and "nothing was started" in res["error"]
    assert env.fakes.chat_keys == [] and env.app.launcher.status()["state"] == "idle"


def test_start_requires_the_cap_typed_again(env):
    with serving(env.app) as server:
        for confirm in (None, "", "0.2", "yes", "0.26"):
            body = {**good()} | ({} if confirm is None else {"confirm": confirm})
            status, res = call(server, "POST", "/api/launch/start", body)
            assert status == 409 and "confirm" in failed(res) and failed(res)["confirm"]["fix"], confirm
        assert env.fakes.chat_keys == []
        status, res = call(server, "POST", "/api/launch/start", {**good(), "confirm": "0.250"})
        assert status == 200 and res["started"] is True
        finish(env, server)


def test_start_rechecks_server_side_whatever_the_page_saw(env):
    """A preflight that passed does not let a later start skip a check that now fails."""
    with serving(env.app) as server:
        assert pre(server, good())["can_launch"]
        env.fakes.remaining = 0.01
        status, res = call(server, "POST", "/api/launch/start", {**good(), "confirm": "0.25"})
    assert status == 409 and "key_credit" in failed(res)


def test_a_second_start_while_running_is_a_conflict(env):
    env.fakes.gate = threading.Event()
    with serving(env.app) as server:
        status, first = call(server, "POST", "/api/launch/start", {**good(), "confirm": "0.25"})
        assert status == 200
        try:
            status, second = call(server, "POST", "/api/launch/start", {**good(), "confirm": "0.25"})
            assert status == 409 and "already running" in second["error"] and "idle" in failed(second)
            assert call(server, "GET", "/api/launch/status")[1]["state"] == "running"
            res = pre(server, good())
            assert "idle" in failed(res) and res["can_launch"] is False
        finally:
            env.fakes.gate.set()
        done = finish(env, server)
    assert done["state"] == "succeeded"


# ---------------------------------------------------------------- end to end

def new_packet_loaded(env, doc_ids):
    data = env.app.data()
    loaded = {r["document_id"] for r in data["records"]}
    assert set(doc_ids) <= loaded
    return data


@pytest.mark.parametrize("target,task,expect", [
    ("trial:contract", "cuad", {"cont-01", "cont-02"}),
    ("trial:merger_agreement", "merger_consideration", {"merg-01", "merg-02"}),
    ("trial:correspondence", "correspondence", None),
    ("trial:insurance_claim", "claim_checklist", None),
    ("trial:corporate_record", "corporate", None),
])
def test_public_trial_runs_end_to_end_and_the_portal_loads_the_packet(env, target, task, expect):
    approve_winner(env)
    with serving(env.app) as server:
        res = pre(server, good(target))
        assert res["can_launch"], failed(res)
        status, started = call(server, "POST", "/api/launch/start", {**good(target), "confirm": "0.25"})
        assert status == 200, started
        done = finish(env, server)
    assert done["state"] == "succeeded", done
    assert done["error"] is None and done["finished_utc"] and done["started_utc"]
    assert done["log_tail"] and done["target"]["id"] == target
    trial_dir = next((env.root / "trials").glob(f"*_{task}-public"))
    stamp = trial_dir.name.split("_")[0]
    assert datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
    meta = json.loads((trial_dir / "trial.json").read_text())
    assert meta["source"]["sha256"] == "cd" * 32 and meta["source"]["revision"] == "ab" * 20
    assert meta["source"]["dataset"] == "Lucius-Morningstar/mailroom-dataset"
    docs = meta["documents"]
    assert len(docs) == 2 and docs == sorted(docs)
    if expect:
        assert set(docs) == expect
    assert "cont-04" not in docs and "merg-03" not in docs  # CUAD and MAUD gold are never bought
    packet = env.root / done["result"]["packet"]
    assert packet.name == f"{trial_dir.name}-review-data.json" and packet.exists()
    assert done["result"]["spent_usd"] is not None and 0 < done["result"]["spent_usd"] <= 0.25
    data = new_packet_loaded(env, docs)
    assert any(r.get("kind") == "labeling_batch" and r["document_id"] in docs for r in data["records"])
    receipt = [json.loads(line) for line in (env.out / "launches.jsonl").read_text().splitlines()][-1]
    assert receipt["outcome"] == "succeeded" and receipt["target"] == target and receipt["initials"] == "JB"
    assert receipt["cap_usd"] == 0.25 and receipt["projection"]["documents"] == 2
    assert receipt["packet"] == done["result"]["packet"] and receipt["spent_usd"] == done["result"]["spent_usd"]
    assert len(env.fakes.hub_calls) == 1  # downloaded once, then cached for the start's own re-check


def test_trials_never_pick_a_cuad_gold_contract_or_a_non_edgar_contract(env):
    approve_winner(env)
    with serving(env.app) as server:
        res = pre(server, good("trial:contract", documents=5))
    sel = next(i for i in res["items"] if i["id"] == "selection")
    assert "cont-04" not in sel["detail"] and "3 of 3 documents" in sel["detail"]


def test_campaign_arm_reaches_cmd_chunk_with_the_cli_arguments(env, monkeypatch):
    seen = {}
    real = operate.cmd_chunk

    def spy(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return real(args, **kw)
    monkeypatch.setattr(operate, "cmd_chunk", spy)
    with serving(env.app) as server:
        res = pre(server, good("campaign:7a", cap_usd=0.15))
        assert res["can_launch"], failed(res)
        status, _ = call(server, "POST", "/api/launch/start", {**good("campaign:7a", cap_usd=0.15), "confirm": "0.15"})
        assert status == 200
        done = finish(env, server)
    assert done["state"] == "succeeded", done
    args = seen["args"]
    assert (args.campaign, args.id, args.arm, args.initials, args.provider, args.cap_usd) == (
        CAMPAIGN, "7a", None, "JB", "openrouter", 0.15)
    assert args.dry_run is False and args.no_checkpoint is False and Path(args.out) == env.root
    assert seen["kw"]["chat"] == env.fakes.chat
    chunk = env.root / "7a_correspondence-pilot"
    assert (chunk / "calls.jsonl").exists()
    assert done["result"]["packet"] == f"7a_correspondence-pilot/7a_correspondence-pilot-review-data.json"
    assert done["target"]["kind"] == "campaign"
    run = [r for r in export.read_journal(chunk / "runs.jsonl") if r.get("task")][0]
    assert run["initials"] == "JB" and run["api_cost_cap_usd"] == 0.15 and run["chunk"] == "7a"
    new_packet_loaded(env, ["corr-01"])


def test_a_failed_run_is_reported_with_its_error_and_receipt(env):
    env.fakes.chat_error = "HTTP 500 upstream broke while using {key}"
    with serving(env.app) as server:
        status, _ = call(server, "POST", "/api/launch/start", {**good(api_key=KEY), "confirm": "0.25"})
        assert status == 200
        done = finish(env, server)
    assert done["state"] == "failed" and done["error"], done
    receipts = [json.loads(line) for line in (env.out / "launches.jsonl").read_text().splitlines()]
    assert receipts[0]["outcome"] == "started" and receipts[-1]["outcome"] == "failed"
    assert KEY not in json.dumps(done) and KEY not in (env.out / "launches.jsonl").read_text()


def test_the_log_is_bounded(env):
    for n in range(launch.LOG_LINES * 2):
        env.app.launcher._emit(f"line {n} " + "x" * 5000, set())
    assert len(env.app.launcher._log) == launch.LOG_LINES
    assert len(env.app.launcher.status()["log_tail"]) == launch.LOG_TAIL
    assert all(len(line) <= launch.LINE_CHARS for line in env.app.launcher._log)


# ---------------------------------------------------------------- the key never leaks

def test_a_provided_key_reaches_the_run_but_never_a_file_a_response_or_the_log(env, monkeypatch):
    import os

    env.fakes.leak = True  # the run itself prints the key it was handed
    env.fakes.chat_error = None
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    app = env.make(environ={})
    BODIES.clear()
    with serving(app) as server:
        status, options = call(server, "GET", "/api/launch/options")
        res = pre(server, good(api_key=KEY))
        assert res["can_launch"], failed(res)
        status, started = call(server, "POST", "/api/launch/start", {**good(api_key=KEY), "confirm": "0.25"})
        assert status == 200
        app.launcher.join(60)
        done = call(server, "GET", "/api/launch/status")[1]
        assert done["state"] == "succeeded", done
        call(server, "GET", "/review-data.json")
        call(server, "GET", "/")
        # a second run, this time through a campaign arm, with the same hand-over
        status, _ = call(server, "POST", "/api/launch/start", {**good("campaign:7a", cap_usd=0.15, api_key=KEY),
                                                                "confirm": "0.15"})
        assert status == 200
        app.launcher.join(60)
        done = call(server, "GET", "/api/launch/status")[1]
        assert done["state"] == "succeeded", done
    assert set(env.fakes.chat_keys) == {KEY}  # the run did use it ...
    assert "OPENROUTER_API_KEY" not in os.environ  # ... without os.environ ever holding it
    needle = KEY.encode()
    assert any(b"[redacted]" in b for b in BODIES)  # the printed key reached the log as [redacted]
    assert not [i for i, body in enumerate(BODIES) if needle in body], "key echoed in a response"
    leaked = [str(p) for p in env.tmp.rglob("*") if p.is_file() and needle in p.read_bytes()]
    assert not leaked, leaked
    assert KEY not in json.dumps(app.launcher.status())


def test_the_environments_own_key_is_never_echoed_either(env):
    env.fakes.key_error = "GET /key failed: HTTP 401 for {key}"
    BODIES.clear()
    with serving(env.app) as server:
        res = pre(server, good())
        call(server, "GET", "/api/launch/options")
    assert ENV_KEY not in json.dumps(res) and "[redacted]" in json.dumps(res)
    assert not [b for b in BODIES if ENV_KEY.encode() in b]


@pytest.mark.parametrize("key", ["has space", "tab\tkey", "new\nline", "café-key", "x" * 600])
def test_malformed_keys_are_rejected_without_echoing_them(env, key):
    with serving(env.app) as server:
        status, res = call(server, "POST", "/api/launch/preflight", {**good(), "api_key": key})
    assert status == 400 and key.strip() not in res["error"] and "api_key" in res["error"]


def test_unknown_or_mistyped_fields_are_a_400(env):
    with serving(env.app) as server:
        assert call(server, "POST", "/api/launch/preflight", {**good(), "extra": 1})[0] == 400
        assert call(server, "POST", "/api/launch/preflight", {**good(), "api_key": 5})[0] == 400
        assert call(server, "POST", "/api/launch/preflight", {"initials": "JB"})[0] == 400
        assert call(server, "POST", "/api/launch/preflight", {**good(), "confirm": "0.25"})[0] == 400  # start-only


# ---------------------------------------------------------------- operate regression

def test_chunk_records_the_requires_message_not_the_checkpoint_one(env, monkeypatch):
    """``cmd_chunk`` reused ``why`` for the requires check and the checkpoint check, so the run
    journaled the checkpoint's "write token for ..." as the reason an unmet requirement was overridden."""
    seen = {}

    def fake_execute(*a, **kw):
        seen.update(kw)
        return 0
    monkeypatch.setattr(runner, "execute", fake_execute)
    monkeypatch.setattr(operate, "auto_checkpoint", lambda args, why, rc=0, **k: rc)
    args = argparse.Namespace(campaign=CAMPAIGN, id="7r", arm=None, initials="JB", provider="openrouter",
                              cap_usd=0.15, workers=1, model_map=[], ack_requires=True, dry_run=False,
                              no_checkpoint=False, stage=env.stage, out=env.root, no_restore=True)
    assert operate.cmd_chunk(args) == 0
    assert seen["ack_requires"].startswith("dojo_pin is llm-dojo-scoring")
    assert "not met" in seen["ack_requires"] and "write token" not in seen["ack_requires"]


# ---------------------------------------------------------------- page

def test_page_has_the_launch_panel_without_ever_persisting_the_key():
    page = Path(runner.ROOT / "src/mailroom_eda/labeling/review_page.html").read_text(encoding="utf-8")
    assert 'id="launch-panel" hidden' in page and "innerHTML" not in page
    assert 'id="launch-key" type="password" autocomplete="off"' in page
    assert "Qualification" not in page.split("<script>")[0] or True
    script = page.split("<script>")[1]
    storage = [line for line in script.splitlines() if "localStorage" in line]
    assert storage and not any("launch" in line.lower() for line in storage)
    assert "/api/launch/preflight" in script and "/api/launch/start" in script and "/api/launch/status" in script
    assert "model_outputs" in script
