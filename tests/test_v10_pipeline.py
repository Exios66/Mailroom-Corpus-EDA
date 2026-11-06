"""The v10 build driver, ingest, conformance gates and card on a tiny synthetic v9.4 stage.

Network-free: parquet, ``datasets`` and the gates all run on files under ``tmp_path``."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mailroom_eda import card_v10
from mailroom_eda import v10_build as v10
from mailroom_eda import v10_conformance as conf
from mailroom_eda.labeling import export, ingest, operate

from test_v10_build import contract_row, insurance_row, make_row, merger_row

ROOT = Path(__file__).resolve().parents[1]
MAUD_OTHER = {"Absence of Litigation Closing Condition": {"answer": "Pending", "category": "Conditions to Closing"}}
OTHER_TEXT = "AGREEMENT AND PLAN OF MERGER. Each share shall be converted into 0.5 shares of Parent stock."


def _opaque(row):
    row["document_id"] = "DOC-" + hashlib.md5(row["filename"].encode()).hexdigest()[:16]
    return row


def _rows():
    other = merger_row("m2.htm", "other", MAUD_OTHER)
    other["doc_text"] = OTHER_TEXT
    other["content_sha256"] = v10.sha256_text(OTHER_TEXT)
    rows = [contract_row(), merger_row(), other, insurance_row(),
            make_row("e1.txt", "correspondence", "email", "From: a@x.com\nTo: b@y.com\n\nPlease pay."),
            make_row("e2.txt", "correspondence", "email", "Hello\r\nthere"),
            make_row("k1.txt", "corporate_record", "bylaws", "BYLAWS OF ACME CORP.")]
    return [_opaque(r) for r in rows]


def _stage(root: Path) -> Path:
    """A v9.4-shaped stage: ground_truth_hardened.jsonl plus one sidecar config."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    root.mkdir(parents=True)
    with (root / "ground_truth_hardened.jsonl").open("w", encoding="utf-8") as fh:
        for r in _rows():
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    for split, text in (("train", "bundle one\r\n"), ("test", "bundle two\n")):
        d = root / "parquet" / "bundles" / split
        d.mkdir(parents=True)
        t = pa.Table.from_pylist([{"filename": f"b-{split}", "doc_text": text,
                                   "content_sha256": v10.sha256_text(text)}])
        pq.write_table(t, d / f"{split}-00000-of-00001.parquet")
    return root


@pytest.fixture()
def stage(tmp_path, monkeypatch):
    monkeypatch.setattr(operate, "load_approvals", lambda name: [])
    return _stage(tmp_path / "v9_4")


def _gates(out, source, gates, **kw):
    return conf.run(out, source, gates, **kw)["gates"]


def test_build_is_byte_identical_and_passes_the_chunk0_gates(tmp_path, stage):
    a, b = tmp_path / "a", tmp_path / "b"
    v10.build(stage, a)
    v10.build(stage, b)
    files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    assert all((a / f).read_bytes() == (b / f).read_bytes() for f in files)
    res = conf.run(a, stage, list(conf.CHUNK0_GATES))
    assert res["passed"], {g: v["detail"] for g, v in res["gates"].items() if not v["passed"]}
    m = json.loads((a / "manifest.json").read_text())
    assert m["configs"][0] == "default" and "labels" not in m["inputs"]


def test_default_is_blind_and_bundles_are_cleaned(tmp_path, stage):
    import pyarrow.parquet as pq
    v10.build(stage, tmp_path / "out")
    t = pq.read_table(tmp_path / "out/parquet/default/train/train-00000-of-00001.parquet")
    assert t.column_names == list(v10.DEFAULT_COLUMNS)
    b = pq.read_table(tmp_path / "out/parquet/bundles/train/train-00000-of-00001.parquet").to_pylist()[0]
    assert b["doc_text"] == "bundle one\n" and b["content_sha256"] == v10.sha256_text("bundle one\n")
    assert b["source_content_sha256"] == v10.sha256_text("bundle one\r\n")
    with pytest.raises(SystemExit, match="never publishes"):
        v10.main(["--stage", str(stage), "--out", str(tmp_path / "x"), "--publish"])


def _rewrite(path: Path, fn):
    import pyarrow as pa
    import pyarrow.parquet as pq
    t = pq.read_table(path)
    rows = fn(t.to_pylist())
    pq.write_table(pa.Table.from_pylist(rows), path)


@pytest.mark.parametrize("gate,tamper", [
    (1, lambda out: _rewrite(out / "parquet/routing/train/train-00000-of-00001.parquet", lambda r: r[1:])),
    (2, lambda out: _rewrite(out / "parquet/default/train/train-00000-of-00001.parquet",
                             lambda r: [{**r[0], "doc_text": r[0]["doc_text"] + "!"}] + r[1:])),
    (3, lambda out: _rewrite(out / "parquet/cuad_clauses/train/train-00000-of-00001.parquet",
                             lambda r: [{**x, "start": x["start"] + 1} for x in r])),
    (7, lambda out: _rewrite(out / "parquet/default/train/train-00000-of-00001.parquet",
                             lambda r: [{**x, "filename": "leak"} for x in r])),
    (8, lambda out: (out / "parquet/extra.json").write_text("{}")),
    (12, lambda out: (out / "README.md").write_text("hand-edited\n")),
    (13, lambda out: (out / "parquet/routing/train/train-00000-of-00001.parquet").write_bytes(b"not parquet")),
])
def test_each_gate_fails_on_its_tamper(tmp_path, stage, gate, tamper):
    out = tmp_path / "out"
    v10.build(stage, out)
    assert _gates(out, stage, [gate])[gate]["passed"]
    if gate == 3 and not any(r for r in (out / "parquet/cuad_clauses").rglob("*.parquet")):
        pytest.skip("no cuad rows in this split")
    tamper(out)
    assert not _gates(out, stage, [gate])[gate]["passed"]


def test_gates_10_and_11_fail_before_labeling(tmp_path, stage):
    out = tmp_path / "out"
    v10.build(stage, out)
    g = _gates(out, stage, [9, 10, 11])
    assert g[9]["passed"] and "no llm labels" in g[9]["detail"]
    assert not g[10]["passed"] and "no merger_consideration" in g[10]["detail"]
    assert not g[11]["passed"]


def test_gates_give_the_same_answer_under_python_O(tmp_path, stage):
    out = tmp_path / "out"
    v10.build(stage, out)
    cmd = [str(ROOT / "scripts/audit/verify_v10_conformance.py"), "--stage", str(out), "--source", str(stage),
           "--gates", "1-11"]
    env = {"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin", "HF_DATASETS_OFFLINE": "1"}
    plain = subprocess.run([sys.executable, *cmd], capture_output=True, text=True, env=env)
    opt = subprocess.run([sys.executable, "-O", *cmd], capture_output=True, text=True, env=env)
    assert plain.returncode == opt.returncode == 1
    p, o = json.loads(plain.stdout), json.loads(opt.stdout)
    assert {g: v["passed"] for g, v in p["gates"].items()} == {g: v["passed"] for g, v in o["gates"].items()}


def test_card_check_detects_a_stale_card(tmp_path, stage):
    out = tmp_path / "out"
    v10.build(stage, out)
    assert card_v10.check(out)[0]
    text = (out / "README.md").read_text()
    assert text.startswith("---\npretty_name:") and "- config_name: default" in text
    assert "| **all** | " in text
    (out / "README.md").write_text(text.replace("| **all** |", "| **every** |"))
    assert not card_v10.check(out)[0]


# ---------------------------------------------------------------- ingest

A, B = "deepseek/deepseek-v4.1-flash", "z-ai/glm-5.3-flashx-legal"


def _approval(chunk, task, set_name, shas=("p1",)):
    return {"chunk": chunk, "arm": None, "task": task, "set": set_name, "models": [A, B],
            "provider": "openrouter", "reasoning": "off", "temperature": {A: 0, B: 0},
            "prompt_sha256": list(shas), "approved_by": "JB"}


def _labels_root(tmp_path, rows):
    """Chunk 6c1's fields.csv: a merger_consideration for the 'other' merger and for the expert one."""
    root = tmp_path / "labels"
    cal = operate.chunk_dir(root, *operate.chunk_entry("v10", "6c0", None))
    cal.mkdir(parents=True)
    (cal / "calls.jsonl").write_text("{}\n")
    export.write_csv(cal / "fields.csv", export.FIELD_COLUMNS, [])
    entry, arm = operate.chunk_entry("v10", "6c1", None)
    d = operate.chunk_dir(root, entry, arm)
    d.mkdir(parents=True)
    (d / "calls.jsonl").write_text("{}\n")
    other = next(r for r in rows if r["filename"] == "m2.htm")
    expert = next(r for r in rows if r["filename"] == "m1.htm")
    base = {"doc_class": "merger_agreement", "field": "merger_consideration", "type": "name",
            "model_a": A, "model_b": B, "decision": "accept", "final_decision": "accept"}
    export.write_csv(d / "fields.csv", export.FIELD_COLUMNS, [
        {**base, "document_id": other["document_id"], "final_value": "all_stock",
         "model_a_evidence": "converted into 0.5 shares of Parent stock", "model_b_evidence": ""},
        {**base, "document_id": expert["document_id"], "final_value": "all_stock"},
    ])
    return root


def test_ingest_refuses_an_uncalibrated_configuration(tmp_path):
    rows = _rows()
    root = _labels_root(tmp_path, rows)
    with pytest.raises(SystemExit, match="uncalibrated"):
        ingest.ingest("v10", root, [_approval("6c1", "merger_consideration", "pending")])
    with pytest.raises(SystemExit, match="uncalibrated"):  # a different prompt is a different configuration
        ingest.ingest("v10", root, [_approval("6c0", "merger_consideration", "calibration", ("p0",)),
                                    _approval("6c1", "merger_consideration", "pending")])
    got = ingest.ingest("v10", root, [_approval("6c0", "merger_consideration", "calibration"),
                                      _approval("6c1", "merger_consideration", "pending")])
    lab = got.labels[rows[2]["document_id"]]["merger_consideration"]
    assert lab.value == "all_stock" and lab.provenance.startswith(f"llm:{A}@openrouter/reasoning=off")
    assert "+llm:" in lab.provenance and got.chunks == ["6c0", "6c1"]


def test_reviewer_provenance_names_the_chosen_source(tmp_path):
    rows = _rows()
    root = _labels_root(tmp_path, rows)
    d = operate.chunk_dir(root, *operate.chunk_entry("v10", "6c1", None))
    fields = export.read_csv(d / "fields.csv")
    fields[0].update(reviewer_verdict="a")
    fields[1].update(reviewer_verdict="interim", final_source="interim:extraction-gate-luna-v1.3.0")
    export.write_csv(d / "fields.csv", export.FIELD_COLUMNS, fields)
    got = ingest.ingest("v10", root, [_approval("6c0", "merger_consideration", "calibration"),
                                      _approval("6c1", "merger_consideration", "pending")])
    a = got.labels[rows[2]["document_id"]]["merger_consideration"].provenance
    i = got.labels[rows[1]["document_id"]]["merger_consideration"].provenance
    assert a.startswith(f"reviewer:JB+llm:{A}@") and i == "reviewer:JB+interim:extraction-gate-luna-v1.3.0"


def test_labelled_build_sets_the_other_merger_subclass_and_keeps_gold(tmp_path, stage, monkeypatch):
    rows = _rows()
    root = _labels_root(tmp_path, rows)
    approvals = [_approval("6c0", "merger_consideration", "calibration"),
                 _approval("6c1", "merger_consideration", "pending")]
    monkeypatch.setattr(operate, "load_approvals", lambda name: approvals)
    out = tmp_path / "out"
    m = v10.build(stage, out, labels_root=root)
    assert m["inputs"]["labels"]["chunks"] == ["6c0", "6c1"]
    st = conf.Stage(out, stage)
    mergers = {r["document_id"]: r for r in st.rows("gt_merger_agreement")}
    other, expert = mergers[rows[2]["document_id"]], mergers[rows[1]["document_id"]]
    assert other["subclass"] == other["merger_consideration"] == "all_stock"
    assert other["source_subclass"] == "other" and other["provenance"]["subclass"].startswith("llm:")
    assert other["evidence"]["merger_consideration"][0]["text"] == "converted into 0.5 shares of Parent stock"
    assert expert["merger_consideration"] == expert["subclass"] == "all_cash"  # the expert answer wins
    legacy = {r["document_id"]: r for r in st.rows("ground_truth")}
    assert legacy[rows[2]["document_id"]]["expected_subclass"] == "other"
    assert json.loads(legacy[rows[2]["document_id"]]["gt_fields"])["merger_consideration"] == "all_stock"
    g = _gates(out, stage, [3, 6, 9, 10])
    assert g[3]["passed"] and g[6]["passed"] and g[9]["passed"], g
    assert g[10]["passed"], g[10]["detail"]


def test_accepted_residue_lets_gate_11_pass(tmp_path, stage, monkeypatch):
    out = tmp_path / "out"
    v10.build(stage, out)
    residue = conf.coverage_residue(conf.Stage(out, stage))
    real = operate.campaign
    monkeypatch.setattr("mailroom_eda.labeling.registry.campaign",
                        lambda name: {**real(name), "accepted_residue": dict(residue)})
    assert _gates(out, stage, [11])[11]["passed"]


def test_step_0_gate_runs_the_chunk0_conformance_gates(tmp_path, stage):
    out = tmp_path / "out"
    v10.build(stage, out)
    entry = {**operate.chunk_entry("v10", "0", None)[0], "stage": str(out)}
    assert entry["task"] == "build" and 9 not in entry["gate"]["gates"]
    res = operate.gate_result("v10", entry, None, tmp_path / "labels", stage)
    assert res["passed"] and res["kind"] == "conformance", res["gates"]
    assert (tmp_path / "labels" / "build-0" / "gate.json").exists()
    eleven = {**operate.chunk_entry("v10", "11", None)[0], "stage": str(out)}
    res = operate.gate_result("v10", eleven, None, tmp_path / "labels", stage)
    assert not res["passed"] and res["unapproved_needs"] == ["0", "10"]
    assert not res["gates"][10]["passed"]


def test_scalar_cells_that_look_like_json_stay_strings():
    """Only list fields decode JSON; a scalar value such as '[sic]' or '{x}' stays text."""
    assert ingest.decode("[sic] Acme", "name") == "[sic] Acme"
    assert ingest.decode('["a"]', "free_text") == '["a"]'
    assert ingest.decode('["a"]', "entity_list:name") == ["a"]
    with pytest.raises(SystemExit):
        ingest.decode("[not json", "entity_list:name")


def test_interrupted_build_keeps_the_last_stage(tmp_path, stage, monkeypatch):
    """A failure mid-write leaves the previous complete stage in place."""
    out = tmp_path / "out"
    v10.build(stage, out)
    before = (out / "manifest.json").read_bytes()
    import pyarrow.parquet as pq

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(pq, "write_table", boom)
    with pytest.raises(OSError):
        v10.build(stage, out)
    assert (out / "manifest.json").read_bytes() == before


def test_gate_1_fails_on_a_duplicated_row(tmp_path, stage):
    out = tmp_path / "out"
    v10.build(stage, out)
    _rewrite(out / "parquet/routing/train/train-00000-of-00001.parquet", lambda r: r + r[:1])
    assert not _gates(out, stage, [1])[1]["passed"]


def test_gate_3_refuses_negative_span_offsets(tmp_path, stage):
    import pyarrow.parquet as pq
    out = tmp_path / "out"
    v10.build(stage, out)
    spans = sorted((out / "parquet/cuad_clauses").rglob("*.parquet"))
    if not spans:
        pytest.skip("no cuad rows")
    split = spans[0].parent.name
    text = {r["document_id"]: r["doc_text"] for r in
            pq.read_table(out / f"parquet/default/{split}/{split}-00000-of-00001.parquet").to_pylist()}

    def neg(rows):
        n = len(text[rows[0]["document_id"]])
        return [{**rows[0], "start": rows[0]["start"] - n, "end": rows[0]["end"] - n}] + rows[1:]
    _rewrite(spans[0], neg)
    assert not _gates(out, stage, [3])[3]["passed"]


def test_gate_6_catches_a_renamed_cuad_category(tmp_path, stage):
    out = tmp_path / "out"
    v10.build(stage, out)
    hit = []

    def rename(rows):
        res = []
        for r in rows:
            g = json.loads(r["gt_fields"])
            labels = json.loads(g.get("cuad_clause_labels") or "{}") if isinstance(g.get("cuad_clause_labels"), str) \
                else g.get("cuad_clause_labels") or {}
            if labels and not hit:
                k = next(iter(labels))
                labels = {**{kk: vv for kk, vv in labels.items() if kk != k}, k + " (renamed)": labels[k]}
                g["cuad_clause_labels"] = json.dumps(labels)
                r = {**r, "gt_fields": json.dumps(g)}
                hit.append(r["document_id"])
            res.append(r)
        return res
    for p in sorted((out / "parquet/ground_truth").rglob("*.parquet")):
        _rewrite(p, rename)
    if not hit:
        pytest.skip("no CUAD-labelled row")
    assert not _gates(out, stage, [6])[6]["passed"]


# ---------------------------------------------------------------- relabel overrides

H = "anthropic/claude-haiku-5.5"


def _relabel_approval(chunk="4a"):
    return {"chunk": chunk, "arm": None, "task": "intent", "set": "relabel", "models": [A, H],
            "provider": "openrouter", "reasoning": "off", "temperature": {A: None, H: None},
            "prompt_sha256": ["p1"], "approved_by": "JJB"}


def _calibration(models, arm):
    return {"chunk": "3", "arm": arm, "task": "intent", "set": "calibration", "models": models,
            "provider": "openrouter", "reasoning": "off", "temperature": {m: None for m in models},
            "prompt_sha256": ["p1"], "approved_by": "JJB"}


def _relabel_root(tmp_path, decisions, corrections=None):
    root = tmp_path / "labels"
    entry, arm = operate.chunk_entry("v10", "4a", None)
    d = operate.chunk_dir(root, entry, arm)
    d.mkdir(parents=True)
    (d / "calls.jsonl").write_text("{}\n")
    cols = ["document_id", "filename", "split", "existing", "decision", "new_intent", "evidence",
            "reviewer_decision", "reviewed_by"]
    export.write_csv(d / "decisions.csv", cols, [{c: r.get(c, "") for c in cols} for r in decisions])
    if corrections is not None:
        cols = ["document_id", "filename", "field", "gold_now", "decided_by"]
        export.write_csv(root / operate.GOLD_CORRECTIONS, cols, corrections)
    return root


DECISIONS = [
    {"document_id": "D1", "split": "train", "existing": "notice", "decision": "change", "new_intent": "request",
     "evidence": "please send"},
    {"document_id": "D2", "split": "train", "existing": "notice", "decision": "confirm", "new_intent": "notice"},
    {"document_id": "D3", "split": "train", "existing": "notice", "decision": "adjudicate",
     "reviewer_decision": "update", "reviewed_by": "JJB"},
    {"document_id": "D4", "split": "train", "existing": "notice", "decision": "adjudicate",
     "reviewer_decision": "keep", "reviewed_by": "JJB"},
    {"document_id": "D5", "split": "test", "existing": "notice", "decision": "change", "new_intent": "request"},
    {"document_id": "D6", "split": "test", "existing": "notice", "decision": "adjudicate",
     "reviewer_decision": "analysis", "reviewed_by": "JJB"},
    {"document_id": "D7", "split": "test", "existing": "notice", "decision": "adjudicate",
     "reviewer_decision": "analysis", "reviewed_by": ""},
]


def test_relabel_writes_changes_as_overrides_and_holds_test_proposals(tmp_path, monkeypatch):
    monkeypatch.setattr(operate, "load_second_opinions", lambda name: [_calibration([H], 5)])
    root = _relabel_root(tmp_path, DECISIONS, [{"document_id": "D8", "filename": "f", "field": "intent",
                                                "gold_now": "other", "decided_by": "JJB"}])
    got = ingest.ingest("v10", root, [_calibration([A], 3), _relabel_approval()])
    labs = {d: f["intent"] for d, f in got.labels.items()}
    assert {d: lab.value for d, lab in labs.items()} == {"D1": "request", "D3": "update", "D6": "analysis",
                                                         "D8": "other"}
    assert all(lab.override for lab in labs.values())
    assert labs["D1"].provenance == (f"override:relabel:4a+llm:{A}@openrouter/reasoning=off/prompt=p1/t=None"
                                     f"+llm:{H}@openrouter/reasoning=off/prompt=p1/t=None")
    assert labs["D1"].evidence == ("please send",)
    assert labs["D3"].provenance == labs["D6"].provenance == "override:reviewer:JJB+relabel:4a"
    assert labs["D8"].provenance == "override:gold_correction:JJB"
    assert got.skipped == {"relabel_unchanged": 2, "relabel_test_proposal": 1, "relabel_test_without_initials": 1}
    assert got.chunks == ["4a", "gold-corrections"]


def test_relabel_needs_the_second_opinion_calibrated(tmp_path, monkeypatch):
    monkeypatch.setattr(operate, "load_second_opinions", lambda name: [])
    root = _relabel_root(tmp_path, DECISIONS)
    with pytest.raises(SystemExit, match=f"(?s)uncalibrated.*{H}"):
        ingest.ingest("v10", root, [_calibration([A], 3), _relabel_approval()])


def test_gate_second_opinion_keeps_the_winner(tmp_path, monkeypatch):
    path = tmp_path / "approved.yaml"
    monkeypatch.setattr(operate, "approvals_path", lambda name: path)
    winner = _calibration([A], 3)
    operate._write_approvals("v10", [winner], [_calibration([H], 5)])
    assert operate.load_approvals("v10") == [winner]
    assert operate.load_second_opinions("v10") == [_calibration([H], 5)]
    operate._write_approvals("v10", [winner, _relabel_approval()], operate.load_second_opinions("v10"))
    assert operate.load_second_opinions("v10") == [_calibration([H], 5)]  # a later approval keeps it


def test_labelled_build_overrides_gold_intent_and_gate_6_allows_it(tmp_path, monkeypatch):
    import yaml

    rows = _rows()
    for r in rows:
        if r["filename"] == "e1.txt":
            r["gt_fields"]["intent"] = "notice"
            pres = json.loads(r["gt_fields"]["gt_presence"])
            pres["intent"] = "populated"
            r["gt_fields"]["gt_presence"] = json.dumps(pres, sort_keys=True)
            target = r
    src = tmp_path / "v9_4"
    monkeypatch.setattr(sys.modules[__name__], "_rows", lambda: rows)
    _stage(src)
    split = "test" if target["split"] == "test" else "train"
    root = _relabel_root(tmp_path, [{"document_id": target["document_id"], "split": split, "existing": "notice",
                                     "decision": "adjudicate", "reviewer_decision": "request",
                                     "reviewed_by": "JJB"}])
    approvals = [_calibration([A], 3), _relabel_approval()]
    monkeypatch.setattr(operate, "load_approvals", lambda name: approvals)
    monkeypatch.setattr(operate, "load_second_opinions", lambda name: [_calibration([H], 5)])
    out = tmp_path / "out"
    v10.build(src, out, labels_root=root)
    st = conf.Stage(out, src)
    mail = {r["document_id"]: r for r in st.rows("gt_correspondence")}[target["document_id"]]
    assert mail["intent"] == "request" and mail["provenance"]["intent"] == "override:reviewer:JJB+relabel:4a"
    legacy = {r["document_id"]: r for r in st.rows("ground_truth")}[target["document_id"]]
    assert json.loads(legacy["gt_fields"])["intent"] == "request"
    blind = [r for c in ("default",) for r in st.rows(c)]
    assert all("request" not in json.dumps({k: v for k, v in r.items() if k != "doc_text"}) for r in blind)
    gt = json.loads(legacy["gt_fields"])
    assert (gt["intent_source"], gt["intent_status"], gt["intent_confidence"]) == ("manual", "manual", "1.0")
    assert (legacy["annotation_method"], legacy["annotation_reviewer"], legacy["annotation_model"],
            legacy["annotation_confidence"]) == ("human_annotated", "human", "", "1.0")
    g = _gates(out, src, [6, 9])
    assert g[6]["passed"], g[6]["detail"]
    assert g[9]["passed"], g[9]["detail"]
    # gate 6 checks the legacy provenance matches the override, not just that it changed
    _rewrite_ground_truth(out, target["document_id"], lambda r: r.update(annotation_method="llm_zero_shot"))
    g = _gates(out, src, [6])
    assert not g[6]["passed"] and "annotation_method" in g[6]["detail"]


def _rewrite_ground_truth(out, document_id, change):
    import pyarrow as pa
    import pyarrow.parquet as pq

    for f in (out / "parquet" / "ground_truth").rglob("*.parquet"):
        t = pq.read_table(f)
        rows = t.to_pylist()
        if any(r["document_id"] == document_id for r in rows):
            for r in rows:
                if r["document_id"] == document_id:
                    change(r)
            pq.write_table(pa.Table.from_pylist(rows, schema=t.schema), f)


def test_override_annotation_names_the_relabel_models():
    prov = (f"override:relabel:4a+llm:{A}@openrouter/reasoning=off/prompt=5297a08341c4/t=None"
            f"+llm:{H}@openrouter/reasoning=off/prompt=5297a08341c4/t=None")
    top, gt = v10.override_annotation(prov)
    assert top == {"annotation_method": "llm_zero_shot", "annotation_model": f"{A}+{H}",
                   "annotation_prompt_version": "5297a08341c4", "annotation_confidence": "",
                   "annotation_reviewer": ""}
    assert gt == {"intent_source": "llm_zero_shot", "intent_status": "auto_labeled", "intent_confidence": ""}
    human = ({"annotation_method": "human_annotated", "annotation_model": "", "annotation_prompt_version": "",
              "annotation_confidence": "1.0", "annotation_reviewer": "human"},
             {"intent_source": "manual", "intent_status": "manual", "intent_confidence": "1.0"})
    assert v10.override_annotation("override:reviewer:JJB+relabel:4a") == human
    assert v10.override_annotation("override:gold_correction:JJB") == human
