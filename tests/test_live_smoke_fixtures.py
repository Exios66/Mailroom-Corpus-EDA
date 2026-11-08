"""Gold smoke fixtures (``tests/fixtures/live_smoke/<doc_type>/``) and the tool that records them.

Two halves. The first runs over every fixture that has been recorded (one directory per document
type): it must pass ``check`` and ``replay``, hold only documents that needed labels, load in the
review portal and carry no credentials. Until a fixture is recorded those tests skip with the
command that records it. The second half is synthetic and always runs: a fake hub, a fake chat and
temporary directories exercise ``record`` end to end, every way ``check`` and ``replay`` declare a
fixture stale, and every reason ``record`` refuses before spending. Nothing here reaches the network.
"""
from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
from pathlib import Path

import pytest
from test_labeling_launch import public_rows
from test_labeling_review_export import _decide, _status

from mailroom_eda import config
from mailroom_eda.labeling import launch, providers, registry, review_page, runner
from mailroom_eda.labeling import review_portal as portal
from mailroom_eda.labeling import smoke_fixtures as sf

REPO = Path(__file__).resolve().parents[1]
REAL_ROOT = sf.FIXTURE_ROOT
NOT_RECORDED = "no live smoke fixtures recorded yet; run smoke_fixtures record"
RECORDED = [t for t in sf.DOC_TYPES if (REAL_ROOT / t).is_dir()]
GLM, DEEPSEEK = launch.GLM, launch.DEEPSEEK


def need_recorded() -> None:
    if not RECORDED:
        pytest.skip(NOT_RECORDED)


# ======================================================================================
# the fixtures that exist
# ======================================================================================

def test_the_fixture_tree_holds_nothing_pytest_could_collect():
    stray = [p.relative_to(REPO) for p in (REPO / "tests" / "fixtures").rglob("*")
             if p.is_file() and (p.name.startswith("test_") or p.name.endswith("_test.py") or p.name == "conftest.py")]
    assert stray == []
    assert (REAL_ROOT / "README.md").is_file()


def test_every_document_type_is_recorded():
    need_recorded()
    assert RECORDED == list(sf.DOC_TYPES), f"missing fixtures: {sorted(set(sf.DOC_TYPES) - set(RECORDED))}"


@pytest.mark.parametrize("doc_type", sf.DOC_TYPES)
def test_recorded_fixture_is_current(doc_type):
    need_recorded()
    mismatches, fx = sf.check_fixture(REAL_ROOT / doc_type)
    assert not mismatches, "\n".join(sf.stale_message(fx, m) for m in mismatches[:3])


@pytest.mark.parametrize("doc_type", sf.DOC_TYPES)
def test_recorded_fixture_replays_to_its_packet(doc_type):
    need_recorded()
    assert sf.replay_fixture(REAL_ROOT / doc_type) == []


@pytest.mark.parametrize("doc_type", sf.DOC_TYPES)
def test_recorded_packet_validates_and_saves_an_accept(doc_type, tmp_path, monkeypatch):
    need_recorded()
    packet = json.loads((REAL_ROOT / doc_type / "expected-review-data.json").read_text(encoding="utf-8"))
    targets = review_page.validate_packet(packet)
    assert targets and sf.normalize_packet(packet) == packet
    root = tmp_path / "labels"
    (root / "smoke").mkdir(parents=True)
    shutil.copyfile(REAL_ROOT / doc_type / "expected-review-data.json", root / "smoke" / "smoke-review-data.json")
    monkeypatch.setattr(runner, "DEFAULT_STAGE", tmp_path / "missing-stage")
    app = portal.ReviewPortal(root, root / "portal", campaign="v10")
    data = app.data()
    assert {(r["document_id"], r["field"]) for r in data["records"]} == set(targets)
    passed = next((r for r in data["records"] if r["candidate_status"] == "source_checks_passed"), None)
    if passed is None:  # every candidate needs a reviewer: the portal must refuse a plain ACCEPT
        first = data["records"][0]
        with pytest.raises(ValueError):
            app.save(_decide(data, first["document_id"], first["field"], "ACCEPT"))
        return
    saved = app.save(_decide(data, passed["document_id"], passed["field"], "ACCEPT"))
    assert saved["changed_fields"] == 1
    state = _status(saved["data"], passed["document_id"], passed["field"])
    assert state in ("human_confirmed", "reviewed_needs_schema")


@pytest.mark.parametrize("doc_type", sf.DOC_TYPES)
def test_recorded_fixture_bills_no_gold(doc_type):
    need_recorded()
    fx = sf.load_fixture(REAL_ROOT / doc_type)
    assert fx.manifest["documents"], "a fixture without documents proves nothing"
    for row in fx.rows():
        assert launch.gold_reason(row) == "", row["document_id"]
        if doc_type == "contract":
            assert row["source_corpus"] == "sec_edgar"
            labels = (row["gt_fields"] or {}).get("cuad_clause_labels")
            # Published values are JSON text: '{}' means no CUAD labels.
            assert not (json.loads(labels) if isinstance(labels, str) and labels.strip() else labels)
        if doc_type == "merger_agreement":
            assert not launch.derive.maud_consideration_answer(row)


@pytest.mark.parametrize("doc_type", sf.DOC_TYPES)
def test_recorded_fixture_holds_no_credentials(doc_type):
    need_recorded()
    assert sf.fixture_credential_problems(REAL_ROOT / doc_type) == []


@pytest.mark.skipif(os.environ.get("MAILROOM_LIVE_HUB") != "1", reason="live Hub check (MAILROOM_LIVE_HUB=1)")
def test_recorded_documents_match_the_public_file():
    need_recorded()
    assert sf.verify_hub() == []


# ======================================================================================
# synthetic: the recorder, with a fake hub and a fake chat
# ======================================================================================

class Hub:
    """Serves the synthetic public rows and counts downloads."""

    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.calls = rows, fail, []

    def __call__(self, revision):
        self.calls.append(revision)
        if self.fail:
            raise OSError("network is down")
        return {"rows": copy.deepcopy(self.rows if self.rows is not None else public_rows()),
                "revision": config.REPO_REVISION, "source_sha256": "cd" * 32}


class Chat:
    """Answers every request offline; counts them."""

    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def __call__(self, backend, model, messages, schema, *, reasoning, max_tokens):
        self.sent.append((model, reasoning, max_tokens))
        if self.fail:
            err = self.fail(model, len(self.sent))
            if err:
                raise err
        if "clauses" in schema["properties"]:
            answer = {"clauses": []}
        else:
            fields = schema["properties"]["fields"]["properties"]
            answer = {"fields": {f: {"value": None, "status": "not_found", "evidence": ""} for f in fields}}
        return providers.Completion(json.dumps(answer), answer, model, "CoreWeave", 100, 50, 0, 0.0001, "stop", True)


ENV = {"OPENROUTER_API_KEY": "proxy-injected"}
NO_CREDIT_LIMIT = lambda backend: {"limit_remaining": None}  # noqa: E731


def run_record(root, *, hub=None, chat=None, stdin=None, env=None, types=None, cap="1.00", extra=()):
    """``smoke_fixtures record`` with fakes; returns (exit code, output, hub, chat)."""
    hub, chat, out = hub or Hub(), chat or Chat(), io.StringIO()
    stdin = cap + "\n" if stdin is None else stdin
    argv = ["record", "--initials", "AB", "--cap-usd", cap, "--root", str(root), *extra]
    if types:
        argv += ["--types", *types]
    rc = sf.main(argv, hub=hub, chat=chat, key_status=NO_CREDIT_LIMIT, env=ENV if env is None else env,
                 stdin=io.StringIO(stdin), out=out)
    return rc, out.getvalue(), hub, chat


@pytest.fixture(autouse=True)
def no_real_ping_journal(tmp_path, monkeypatch):
    """The preflight reads a ping journal from the labels root; never the checkout's own."""
    monkeypatch.setattr(runner, "DEFAULT_OUT", tmp_path / "no-labels")


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    """All five types recorded once with the fakes; tests copy the tree before changing it."""
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("recorded")
    mp.setattr(runner, "DEFAULT_OUT", base / "no-labels")
    root = base / "live_smoke"
    rc, out, hub, chat = run_record(root)
    mp.undo()
    assert rc == 0, out
    return root, out, hub, chat


@pytest.fixture
def root(recorded, tmp_path):
    """A private copy of the recorded tree."""
    dest = tmp_path / "live_smoke"
    shutil.copytree(recorded[0], dest)
    return dest


def check_out(root, types=None):
    out = io.StringIO()
    return sf.check(types, root=root, out=out), out.getvalue()


def edit_json(path, change):
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    path.write_text(json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def edit_jsonl(path, change):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]
    change(rows)
    path.write_text("".join(sf._line(r) + "\n" for r in rows), encoding="utf-8")


def test_record_writes_a_fixture_per_type_that_check_and_replay_accept(recorded, root):
    _, out, hub, chat = recorded
    assert sorted(p.name for p in root.iterdir()) == sorted(sf.DOC_TYPES)  # no staging leftovers
    for t in sf.DOC_TYPES:
        assert sorted(p.name for p in (root / t).iterdir()) == sorted(sf.FILES)
        fx = sf.load_fixture(root / t)
        m = fx.manifest
        assert (m["schema_version"], m["doc_type"], m["initials"]) == (1, t, "AB")
        assert (m["task"], m["set"]) == launch.TRIAL_TYPES[t]
        assert m["dataset"] == {"repo": config.REPO_ID, "revision": config.REPO_REVISION, "tag": config.REPO_TAG,
                                "file": "ground_truth_hardened.jsonl", "file_sha256": "cd" * 32}
        assert set(m["reasoning"]) == set(m["models"]) == set(m["max_tokens"])
        assert len(m["documents"]) == 2 and m["recorder_commit"] and m["recorded_utc"].endswith("Z")
        assert m["spent_usd"] == pytest.approx(sum(m["spent_by_model"].values()))
        assert {c["document_id"] for c in fx.calls} == {d["document_id"] for d in m["documents"]}
        assert {k for c in fx.calls for k in c} <= set(sf.CALL_KEYS) and not any("labeled_utc" in c for c in fx.calls)
        assert fx.raw_rows == [r for r in public_rows() if r["document_id"] in
                               {d["published_document_id"] for d in m["documents"]}]  # exactly as downloaded
        assert (root / t / "README.md").read_text().count("\n\n") >= 1
    assert hub.calls == [None]  # one download for all five types
    assert len(chat.sent) == sum(sf.load_fixture(root / t).manifest["calls"]["answered"] for t in sf.DOC_TYPES)
    assert check_out(root)[0] == 0
    assert sf.replay(root=root, out=io.StringIO()) == 0
    assert sf.fixture_credential_problems(root / "contract") == []


def test_the_recorded_run_is_the_launchers_plan(recorded, root):
    assert {m for m, _, _ in recorded[3].sent} == {GLM, DEEPSEEK, "minimax/minimax-m3.1-flash"}
    contract = sf.load_fixture(root / "contract").manifest
    assert contract["models"] == [GLM, DEEPSEEK]
    assert contract["reasoning"] == {GLM: "low", DEEPSEEK: "off"}  # TRIAL_PAIRS: DeepSeek off, GLM low
    assert contract["max_tokens"] == {m: runner.max_tokens("cuad", lvl, m) for m, lvl in contract["reasoning"].items()}
    assert sf.per_type_caps(sf.DOC_TYPES, 1.0) == {
        "contract": 0.4, "corporate_record": 0.2, "merger_agreement": 0.15, "insurance_claim": 0.15,
        "correspondence": 0.1}
    assert sf.per_type_caps(["contract"], 0.4) == {"contract": 0.4}  # a lone type gets the whole cap
    assert sf.per_type_caps(sf.DOC_TYPES, 2.0)["contract"] == pytest.approx(0.8)
    assert sf.load_fixture(root / "correspondence").manifest["cap_usd"]["this_type"] == pytest.approx(0.1)


def test_plan_only_prints_the_plan_and_makes_no_chat_call(tmp_path):
    root = tmp_path / "live_smoke"
    rc, out, hub, chat = run_record(root, extra=["--plan-only"], stdin="", env={})
    assert rc == 0 and chat.sent == [] and hub.calls == [None]
    assert not root.exists()
    for t in sf.DOC_TYPES:
        assert f"== {t} (" in out
    assert "text sha256 " in out and "gold check: pass" in out and "public-v9.3:" in out and "published as" in out
    assert "max_tokens" in out and "reasoning low" in out and "reasoning off" in out
    assert "total: 5 type(s), 20 calls" in out and "cap $0.40" in out and "nothing was spent" in out


def test_a_failing_preflight_item_aborts_before_any_chat_call(tmp_path):
    root = tmp_path / "live_smoke"
    rc, out, hub, chat = run_record(root, env={})  # no OPENROUTER_API_KEY
    assert rc == 1 and chat.sent == [] and not root.exists()
    assert "FAIL [contract] OpenRouter API key" in out and "fix: export OPENROUTER_API_KEY" in out
    assert "FAIL [correspondence]" in out  # every failing item of every type, not only the first
    assert "Nothing was sent and nothing was spent" in out
    # a cap too small for the projection (the repo's headroom rule) fails the same way
    rc, out, _, chat = run_record(root, cap="0.01")
    assert rc == 1 and chat.sent == [] and "raise the cap" in out
    assert "Cap covers the projected cost with headroom" in out
    # so does a dataset that cannot be downloaded, and malformed initials
    rc, out, _, chat = run_record(root, hub=Hub(fail=True))
    assert rc == 1 and chat.sent == [] and "Public dataset could not be downloaded" in out
    sf_out = io.StringIO()
    rc = sf.main(["record", "--initials", "ab", "--cap-usd", "1", "--root", str(root)], hub=Hub(), chat=Chat(),
                 key_status=NO_CREDIT_LIMIT, env=ENV, stdin=io.StringIO("1\n"), out=sf_out)
    assert rc == 1 and "Operator initials" in sf_out.getvalue() and "2-6 capital letters" in sf_out.getvalue()


def test_the_launchers_trial_ceiling_does_not_block_the_larger_cuad_share(tmp_path):
    # cuad's share is $0.40; the portal's trial ceiling is $0.25. The recorder splits its own cap.
    assert sf.default_share("contract") > launch.TRIAL_CAP_CEILING
    rc, out, _, _ = run_record(tmp_path / "r", extra=["--plan-only"], types=["contract"], cap="0.40", env={})
    assert rc == 0 and "cap $0.40" in out


def test_a_gold_document_in_the_selection_aborts_the_plan(tmp_path):
    rows = public_rows()
    for r in rows:  # every contract the cuad set would hold is now CUAD gold (labels present)
        if r["expected"] == "contract" and r["source_corpus"] == "sec_edgar":
            r["gt_fields"] = json.dumps({"cuad_clause_labels": json.dumps({"Parties": [{"start": 0, "text": "Acme"}]}),
                                         "gt_presence": json.dumps({"cuad_clause_labels": "pending_annotation"})})
    rc, out, _, chat = run_record(tmp_path / "r", hub=Hub(rows), types=["contract"], cap="0.40")
    assert rc == 1 and chat.sent == [] and "expert gold" in out and "CUAD clause labels" in out


def test_a_mismatching_confirmation_aborts(tmp_path):
    for typed in ("0.5\n", "\n", "yes\n", "1.5\n"):
        rc, out, _, chat = run_record(tmp_path / "live_smoke", stdin=typed)
        assert rc == 1 and chat.sent == [] and "Nothing was spent" in out
        assert not (tmp_path / "live_smoke").exists()
    rc, _, _, chat = run_record(tmp_path / "live_smoke", stdin="1\n", types=["correspondence"], cap="1.0")
    assert rc == 0 and chat.sent  # "1" confirms 1.0: the same number


def test_record_refuses_to_overwrite_without_replace(recorded, root):
    before = (root / "contract" / "manifest.json").read_bytes()
    rc, out, hub, chat = run_record(root)
    assert rc == 1 and "already recorded" in out and "--replace" in out
    assert chat.sent == [] and hub.calls == []  # refused before even downloading
    assert (root / "contract" / "manifest.json").read_bytes() == before
    rc, out, _, chat = run_record(root, extra=["--replace"], types=["contract"], cap="0.40")
    assert rc == 0 and chat.sent
    assert sorted(p.name for p in root.iterdir()) == sorted(sf.DOC_TYPES)  # old copy removed, nothing left over
    assert check_out(root, ["contract"])[0] == 0


def test_a_stale_document_text_fails_check_loudly(root):
    path = root / "contract" / "documents.jsonl"
    edit_jsonl(path, lambda rows: rows[0].update(doc_text=rows[0]["doc_text"] + "An added sentence.\n"))
    rc, out = check_out(root, ["contract"])
    assert rc == 1
    assert "Gold fixture contract is stale: text sha256 of cont-01 was " in out
    assert "The recorded model answers no longer match what the code would send." in out
    assert ("Re-record with: PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling.smoke_fixtures record "
            "--types contract --initials XX --cap-usd 0.40 --replace (projected ~$") in out
    assert "input_sha256" in out  # the rendered request changed too
    assert check_out(root, ["correspondence"])[0] == 0  # other types are unaffected


def test_a_changed_dataset_revision_fails_check(root, monkeypatch):
    monkeypatch.setattr(config, "REPO_REVISION", "f" * 40)
    rc, out = check_out(root, ["correspondence"])
    assert rc == 1 and "Gold fixture correspondence is stale: dataset revision was " in out
    assert config.REPO_REVISION in out and "--types correspondence" in out and "--cap-usd 0.10 --replace" in out


def test_changed_models_fail_check(root, monkeypatch):
    pair = {**launch.TRIAL_PAIRS["contract"], "models": [GLM, "minimax/minimax-m3.1-flash"],
            "reasoning": {GLM: "low", "minimax/minimax-m3.1-flash": "low"}}
    monkeypatch.setitem(launch.TRIAL_PAIRS, "contract", pair)
    rc, out = check_out(root, ["contract"])
    assert rc == 1 and "Gold fixture contract is stale: models was " in out and "minimax/minimax-m3.1-flash" in out
    assert "--types contract" in out and "projected ~$" in out


def test_changed_reasoning_fails_check(root, monkeypatch):
    pair = {**launch.TRIAL_PAIRS["contract"], "reasoning": {GLM: "low", DEEPSEEK: "low"}}
    monkeypatch.setitem(launch.TRIAL_PAIRS, "contract", pair)
    rc, out = check_out(root, ["contract"])
    assert rc == 1 and "Gold fixture contract is stale: reasoning was " in out
    assert "also stale: max_tokens was " in out  # reasoning at low adds an allowance


def test_a_changed_prompt_template_fails_check(root, tmp_path, monkeypatch):
    prompts = tmp_path / "prompts"
    shutil.copytree(registry.PROMPTS_DIR, prompts)
    (prompts / "cuad_system.md").write_text((prompts / "cuad_system.md").read_text() + "\nBe terse.\n")
    monkeypatch.setattr(registry, "PROMPTS_DIR", prompts)
    registry.prompt.cache_clear()
    try:
        rc, out = check_out(root, ["contract"])
    finally:
        monkeypatch.undo()
        registry.prompt.cache_clear()
    assert rc == 1 and "Gold fixture contract is stale: prompt_sha256 (cont-01, " in out
    assert "Re-record with:" in out and "--types contract" in out
    assert check_out(root, ["contract"])[0] == 0  # and the real templates are current again


def test_a_changed_response_schema_fails_check(root, monkeypatch):
    real = launch.tasks.build

    def build(task, row, variant="", *, set_name=""):
        req = real(task, row, variant, set_name=set_name)
        schema = copy.deepcopy(req.schema)
        schema["properties"]["extra"] = {"type": "string"}
        return type(req)(req.task, req.document_id, req.messages, schema, req.fields, req.prompt_sha256, req.variant)

    monkeypatch.setattr(sf.tasks, "build", build)
    rc, out = check_out(root, ["merger_agreement"])
    assert rc == 1 and "Gold fixture merger_agreement is stale: response schema sha256 (merg-01, " in out


def test_a_gold_document_in_a_stored_fixture_fails_check(root):
    def make_gold(rows):
        rows[0]["source_corpus"] = "theatticusproject/cuad"

    edit_jsonl(root / "contract" / "documents.jsonl", make_gold)
    rc, out = check_out(root, ["contract"])
    assert rc == 1 and "gold check of cont-01 was not CUAD or MAUD gold, now source_corpus is" in out
    assert "a CUAD-gold contract" in out


def test_check_names_a_missing_or_unreadable_fixture(root, tmp_path):
    shutil.rmtree(root / "insurance_claim")
    assert check_out(root, ["insurance_claim"])[0] == 1
    (root / "contract" / "calls.jsonl").unlink()
    rc, out = check_out(root, ["contract"])
    assert rc == 1 and "Gold fixture contract is unreadable" in out and "calls.jsonl" in out
    rc, out = check_out(tmp_path / "empty")
    assert rc == 0 and NOT_RECORDED in out


def test_replay_of_a_missing_answer_is_a_stale_error_and_never_a_network_call(root, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("replay must never reach the network")

    monkeypatch.setattr(providers, "_post", forbidden)
    monkeypatch.setattr(providers, "chat", forbidden)
    monkeypatch.setattr(providers, "key_status", forbidden)
    assert sf.replay_fixture(root / "correspondence") == []  # no network on the happy path either
    edit_jsonl(root / "correspondence" / "calls.jsonl", lambda rows: rows.pop(0))
    out = io.StringIO()
    assert sf.replay(["correspondence"], root=root, out=out) == 1
    assert "Gold fixture correspondence is stale:" in out.getvalue() and "has no recorded answer" in out.getvalue()
    assert "Re-record with:" in out.getvalue()


def test_replay_of_changed_code_is_a_stale_error(root, monkeypatch):
    real = sf.tasks.build

    def build(task, row, variant="", *, set_name=""):
        req = real(task, row, variant, set_name=set_name)
        msgs = [dict(m) for m in req.messages]
        msgs[-1]["content"] += "\nExtra instruction."
        return type(req)(req.task, req.document_id, msgs, req.schema, req.fields, req.prompt_sha256, req.variant)

    monkeypatch.setattr(runner.tasks, "build", build)
    with pytest.raises(sf.StaleFixture, match="no recorded answer"):
        sf.replay_fixture(root / "merger_agreement")


def test_replay_shows_a_readable_diff_when_the_packet_differs(root):
    def change(packet):
        packet["records"][0]["candidate_status"] = "something_else"
        packet["records"][0]["issues"].append("an extra issue")

    edit_json(root / "correspondence" / "expected-review-data.json", change)
    out = io.StringIO()
    assert sf.replay(["correspondence"], root=root, out=out) == 1
    text = out.getvalue()
    assert "REPLAY MISMATCH correspondence" in text
    assert "records[0].candidate_status: expected 'something_else'" in text.replace('"', "'") or \
        "records[0].candidate_status: expected" in text
    assert "replay produced" in text and "records[0].issues" in text


def test_replay_reproduces_billed_failures_and_reasoning_runaways(tmp_path):
    def fail(model, n):
        if n == 1:
            return providers.ProviderError("HTTP 500: upstream exploded", {
                "prompt_tokens": 120, "completion_tokens": 40, "cost_usd": 0.0002, "finish_reason": "length",
                "reasoning_tokens": 39})
        return providers.ProviderError("gave up after 5 attempts; last: network error: boom") if n == 3 else None

    root = tmp_path / "live_smoke"
    rc, out, _, chat = run_record(root, chat=Chat(fail), types=["correspondence"], cap="0.10")
    assert rc == 3 and "failed" in out and "WARNING" in out
    fx = sf.load_fixture(root / "correspondence")
    assert fx.manifest["calls"]["errors"] == 2 and fx.manifest["calls"]["reasoning_runaways"] == 1
    assert any(c.get("reasoning_runaway") and c["cost_usd"] == pytest.approx(0.0002) for c in fx.calls)
    assert check_out(root)[0] == 0 and sf.replay(root=root, out=io.StringIO()) == 0
    packet = json.loads((root / "correspondence" / "expected-review-data.json").read_text())
    assert any("Reasoning ran away" in i for r in packet["records"] for i in r["issues"])


def test_a_credential_in_a_journal_field_is_never_written(tmp_path):
    leak = providers.ProviderError("HTTP 401 for Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789")
    root = tmp_path / "live_smoke"
    rc, out, _, _ = run_record(root, chat=Chat(lambda model, n: leak if n == 1 else None), types=["correspondence"],
                               cap="0.10")
    assert rc == 1 and "NOT WRITTEN" in out and "holds a bearer token" in out and "(kept)" in out
    assert list(root.iterdir()) == []  # no fixture, no staging directory left behind
    kept = re.search(r"paid answers are in (\S+) \(kept\)", out)
    assert kept and (Path(kept.group(1)) / "smoke-correspondence" / "calls.jsonl").is_file()
    shutil.rmtree(Path(kept.group(1)).parent, ignore_errors=True)  # the kept work directory holds the paid answers


def test_the_credential_scan_allows_words_in_documents_and_model_answers(tmp_path):
    docs = tmp_path / "documents.jsonl"
    docs.write_text(sf._line({"doc_text": "Authorization of Shares. Bearer bonds and a risk-or-reward clause."}) + "\n")
    assert sf.credential_problems(docs) == []
    calls = tmp_path / "calls.jsonl"
    calls.write_text(sf._line({"model": "m", "content": "Authorization of Shares", "response": {"v": "Bearer notes"},
                               "document_id": "d"}) + "\n")
    assert sf.credential_problems(calls) == []
    calls.write_text(sf._line({"model": "m", "error": "failed: Authorization header was bad"}) + "\n")
    assert "contains 'Authorization'" in sf.credential_problems(calls)[0]
    calls.write_text(sf._line({"model": "m", "error": "key sk-or-v1-abc was rejected"}) + "\n")
    assert sf.credential_problems(calls)
    calls.write_text(sf._line({"model": "m", "content": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234"}) + "\n")
    assert len(sf.credential_problems(calls)) == 2  # the pattern is refused even inside an answer
    calls.write_text(sf._line({"model": "m", "filename": "/home/someone/doc.txt"}) + "\n")
    assert "/home/" in sf.credential_problems(calls)[0]
    calls.write_text(sf._line({"model": "m"}) + "\n")
    assert sf.credential_problems(calls, secrets=["real-secret-value"]) == []
    calls.write_text(sf._line({"model": "real-secret-value"}) + "\n")
    assert "secret value" in sf.credential_problems(calls, secrets=["real-secret-value"])[0]


def test_normalize_replaces_volatile_fields_and_leaves_content_alone(tmp_path):
    packet = {"schema": "s", "records": [{"full_document_text": "Signed 2026-11-07T10:00:00Z in /tmp/x/y",
                                          "labeled_utc": "2026-11-07T10:00:00Z", "updated_at": "now", "duration": 3.2,
                                          "issues": [f"see {tmp_path}/chunk/fields.csv and /tmp/abc123/fields.csv"],
                                          "candidate_labels": "2026-11-07", "evidence": [{"start": 1, "end": 4}],
                                          "existing_gt_fields": {"created_at": "keep"}}]}
    got = sf.normalize(packet, [str(tmp_path)])["records"][0]
    assert (got["labeled_utc"], got["updated_at"], got["duration"]) == ("<timestamp>", "<timestamp>", "<duration>")
    assert got["issues"] == ["see <tmp>/chunk/fields.csv and <tmp>"]
    assert got["full_document_text"] == packet["records"][0]["full_document_text"]
    assert got["existing_gt_fields"] == {"created_at": "keep"} and got["candidate_labels"] == "2026-11-07"
    assert got["evidence"] == [{"start": 1, "end": 4}]


def test_normalizing_a_real_packet_changes_nothing_and_keeps_it_valid(root):
    packet = json.loads((root / "contract" / "expected-review-data.json").read_text())
    assert sf.normalize_packet(packet) == packet
    stamped = copy.deepcopy(packet)
    stamped["records"][0]["labeled_utc"] = "2026-11-07T10:00:00Z"
    stamped["packet_id"] = review_page.packet_id(stamped["records"])
    fixed = sf.normalize_packet(stamped)
    assert fixed["records"][0]["labeled_utc"] == "<timestamp>" and review_page.validate_packet(fixed)


def test_the_stored_packet_opens_in_the_portal_and_an_accept_saves(root, tmp_path, monkeypatch):
    labels = tmp_path / "labels"
    (labels / "smoke").mkdir(parents=True)
    shutil.copyfile(root / "correspondence" / "expected-review-data.json", labels / "smoke" / "x-review-data.json")
    monkeypatch.setattr(runner, "DEFAULT_STAGE", tmp_path / "missing-stage")
    app = portal.ReviewPortal(labels, labels / "portal", campaign="v10")
    data = app.data()
    passed = next(r for r in data["records"] if r["candidate_status"] == "source_checks_passed")
    saved = app.save(_decide(data, passed["document_id"], passed["field"], "ACCEPT"))
    assert saved["changed_fields"] == 1
    assert _status(saved["data"], passed["document_id"], passed["field"]) == "human_confirmed"
    blocked = next((r for r in data["records"] if r["candidate_status"] != "source_checks_passed"), None)
    if blocked is not None:
        with pytest.raises(ValueError):
            app.save(_decide(data, blocked["document_id"], blocked["field"], "ACCEPT"))


def test_verify_hub_confirms_each_stored_document(root):
    hub = Hub()
    assert sf.verify_hub(root=root, hub=hub) == [] and hub.calls == [config.REPO_REVISION]
    out = io.StringIO()
    assert sf.main(["verify-hub", "--root", str(root)], hub=Hub(), out=out) == 0 and "ok:" in out.getvalue()
    rows = public_rows()
    rows[0]["doc_text"] += "changed upstream"
    out = io.StringIO()
    assert sf.main(["verify-hub", "--root", str(root), "--types", "contract"], hub=Hub(rows), out=out) == 1
    assert "the text of cont-01 differs from the public file" in out.getvalue()
    short = Hub([r for r in public_rows() if r["document_id"] != "corr-05"])
    missing = sf.verify_hub(["correspondence"], root=root, hub=short)
    assert any("corr-05 is not in the public file" in p for p in missing)
    moved = Hub()
    moved_rows = lambda rev: {**moved(rev), "source_sha256": "ee" * 32}  # noqa: E731
    assert any("has sha256" in p for p in sf.verify_hub(["contract"], root=root, hub=moved_rows))


def test_record_validates_its_arguments(tmp_path):
    rc, out, _, chat = run_record(tmp_path / "r", cap="0")
    assert rc == 1 and chat.sent == [] and "positive" in out
    rc, out, _, chat = run_record(tmp_path / "r", extra=["--documents", "9"], types=["correspondence"], cap="0.10")
    assert rc == 1 and chat.sent == [] and "Trial size" in out and "choose 1 to 5" in out
    with pytest.raises(SystemExit):
        sf.main(["record", "--initials", "AB", "--types", "not_a_type"])


def test_the_command_line_has_the_four_commands():
    parser = sf._parser()
    for argv in (["record", "--initials", "AB", "--plan-only"], ["check"], ["replay", "--types", "contract"],
                 ["verify-hub"]):
        assert parser.parse_args(argv).command == argv[0]
    assert parser.parse_args(["record", "--initials", "AB"]).cap_usd == 1.0
    assert parser.parse_args(["record", "--initials", "AB"]).documents == 2


def test_rebuild_refreshes_the_packet_from_recorded_answers_without_billing(root, monkeypatch):
    """Post-processing changed but the prompts did not: rebuild rewrites the expected packet offline."""
    def change(packet):
        packet["records"][0]["candidate_status"] = "something_else"
    edit_json(root / "correspondence" / "expected-review-data.json", change)
    assert sf.replay(["correspondence"], root=root, out=io.StringIO()) == 1
    out = io.StringIO()
    assert sf.rebuild(["correspondence"], root=root, out=out) == 0
    assert "rebuilt correspondence" in out.getvalue()
    assert sf.replay(["correspondence"], root=root, out=io.StringIO()) == 0


def test_rebuild_refuses_a_stale_fixture_and_names_the_re_record_command(root):
    def change(rows):
        rows[0]["doc_text"] += " changed"
    edit_jsonl(root / "correspondence" / "documents.jsonl", change)
    out = io.StringIO()
    assert sf.rebuild(["correspondence"], root=root, out=out) == 1
    assert "record --types correspondence" in out.getvalue()
