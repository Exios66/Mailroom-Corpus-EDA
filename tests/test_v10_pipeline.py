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
