"""Post-publication release verification (mailroom_eda.release_verify).

Offline: every invariant is proven to catch its failure on a synthetic
snapshot; the frozen specs are checked for shape and for agreement with the
pin. Against the pinned local snapshot (``run_all.py --phases P0``): the
parquet facts equal the frozen spec. Live Hub runs are opt-in
(``MAILROOM_LIVE_HUB=1``): every frozen release is re-verified on the Hub.
"""
from __future__ import annotations

import json
import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mailroom_eda import release_verify as rv
from mailroom_eda.config import DATA_DIR, REPO_REVISION, REPO_TAG
from mailroom_eda.eval_contract import SPECIALIST_BY_CLASS
from mailroom_eda.identity import content_sha256

CARD = """---
configs:
""" + "".join(
    f"- config_name: {c}\n  data_files:\n  - split: train\n    path: parquet/{c}/train/*\n"
    f"  - split: test\n    path: parquet/{c}/test/*\n" for c in rv.CONFIGS
) + "---\n# card\n"


def _names():
    train = [f"doc{i}.txt" for i in range(40) if not rv.is_test(f"doc{i}.txt")][:4]
    test = [f"doc{i}.txt" for i in range(40) if rv.is_test(f"doc{i}.txt")][:2]
    return {"train": train, "test": test}


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _gt_row(name, split, cls="contract"):
    text = f"text of {name}"
    return {"filename": name, "expected": cls, "expected_subclass": "nda", "split": split,
            "gt_fields": json.dumps({"keywords": ["a"]}), "document_id": f"id-{name}",
            "content_sha256": content_sha256(text), "expected_specialist": SPECIALIST_BY_CLASS[cls],
            "annotation_method": "source_native"}


def make_snapshot(root, mutate=None):
    """A minimal, fully valid release snapshot. ``mutate(rows)`` edits rows
    per (config, split) before writing."""
    rows = {}
    for split, names in _names().items():
        rows["default", split] = [{"filename": n, "doc_text": f"text of {n}", "prompt": "",
                                   "metadata": {"source": "x"}} for n in names]
        rows["ground_truth", split] = [_gt_row(n, split) for n in names]
        for cfg in ("bundles", "streams", "fixtures"):
            rows[cfg, split] = [_gt_row(f"{cfg}-{n}", split) for n in names[:1]]
    if mutate:
        mutate(rows)
    for (cfg, split), rs in rows.items():
        _write(root / "parquet" / cfg / split / f"{split}-00000-of-00001.parquet", rs)
    gt = rows["ground_truth", "train"] + rows["ground_truth", "test"]
    sidecars = {"ground_truth_hardened.jsonl": gt}
    for cfg in ("bundles", "streams", "fixtures"):
        sidecars[f"{cfg}.jsonl"] = rows[cfg, "train"] + rows[cfg, "test"]
    for name, rs in sidecars.items():
        (root / name).write_text("".join(json.dumps(r) + "\n" for r in rs), encoding="utf-8")
    (root / "README.md").write_text(CARD, encoding="utf-8")
    write_manifest(root)
    return root


def write_manifest(root):
    lines = [f"  {f}  {rv.sha256_path(root / f)}\n" for f in rv.served_files(root)
             if f not in rv.UNHASHED]
    (root / "manifest.txt").write_text("rows : n\nsha256     :\n" + "".join(lines), encoding="utf-8")


def checks(root):
    return [f.check for f in rv.check_invariants(root)]


def test_a_valid_snapshot_passes_every_invariant(tmp_path):
    assert rv.check_invariants(make_snapshot(tmp_path)) == []


def test_manifest_catches_changed_unlisted_and_missing_files(tmp_path):
    snap = make_snapshot(tmp_path)
    (snap / "README.md").write_text(CARD + "edited\n", encoding="utf-8")
    assert [str(f) for f in rv.check_manifest(snap)] == ["[manifest] sha256 mismatch: README.md"]
    write_manifest(snap)
    (snap / "extra.json").write_text("{}", encoding="utf-8")
    assert "served file not in the manifest: extra.json" in str(rv.check_manifest(snap))
    write_manifest(snap)
    (snap / "extra.json").unlink()
    assert "does not serve: extra.json" in str(rv.check_manifest(snap))


def test_card_must_declare_every_parquet_dir(tmp_path):
    snap = make_snapshot(tmp_path)
    _write(snap / "parquet" / "secret" / "train" / "x.parquet", [{"a": 1}])
    assert "parquet file in no card config: parquet/secret/train/x.parquet" in str(rv.check_card_configs(snap))


@pytest.mark.parametrize("split", ["train", "test"])
def test_blind_config_rejects_a_label_column(tmp_path, split):
    def add_label(rows):
        for r in rows["default", split]:
            r["expected"] = "contract"
    assert "blind" in checks(make_snapshot(tmp_path, add_label))


@pytest.mark.parametrize("key", ["intent", "expected_specialist", "cuad_clause_labels", "entity_name"])
def test_blind_metadata_rejects_a_label_key(tmp_path, key):
    def leak(rows):
        for split in ("train", "test"):
            for r in rows["default", split]:
                r["metadata"] = {"source": "x", key: "v"}
    assert f"label keys ['{key}']" in str(rv.check_blind(make_snapshot(tmp_path, leak)))


def test_split_rule_is_enforced(tmp_path):
    def swap(rows):
        moved = rows["default", "train"].pop()
        rows["default", "test"].append(moved)
    found = rv.check_splits_and_identity(make_snapshot(tmp_path, swap))
    assert any(f.check == "split" for f in found) and any(f.check == "identity" for f in found)


def test_content_hash_must_match_the_blind_text(tmp_path):
    def drift(rows):
        rows["default", "train"][0]["doc_text"] = "changed"
    assert "content_sha256 != default doc_text on 1 rows" in str(
        rv.check_splits_and_identity(make_snapshot(tmp_path, drift)))


def test_document_ids_must_be_unique(tmp_path):
    def dup(rows):
        rows["ground_truth", "train"][1]["document_id"] = rows["ground_truth", "train"][0]["document_id"]
    assert "not unique" in str(rv.check_splits_and_identity(make_snapshot(tmp_path, dup)))


def test_a_class_must_route_to_one_specialist(tmp_path):
    def misroute(rows):
        rows["fixtures", "train"][0]["expected_specialist"] = "other_specialist"
    assert [f.check for f in rv.check_routing(make_snapshot(tmp_path, misroute))] == ["routing"]


def test_sidecars_must_be_line_safe_and_complete(tmp_path):
    snap = make_snapshot(tmp_path)
    path = snap / "bundles.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("bundles-", "bun\u2028dles-"), encoding="utf-8")
    assert "raw line separators ['0x2028']" in str(rv.check_sidecars(snap))
    lines = (snap / "streams.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
    (snap / "streams.jsonl").write_text("".join(lines[:-1]), encoding="utf-8")
    assert "streams.jsonl has 1 rows, streams parquet has 2" in str(rv.check_sidecars(snap))


def test_facts_are_compared_exactly(tmp_path):
    snap = make_snapshot(tmp_path)
    spec = rv.make_spec("v0.1", "abc", rv.collect_facts(snap))
    assert rv.compare_facts(rv.collect_facts(snap), spec) == []
    spec["rows"]["default"]["test"] += 1
    spec["routing"]["contract"] = {"contracts_specialist": 1}
    found = [str(f) for f in rv.compare_facts(rv.collect_facts(snap), spec)]
    assert found == ["[spec] rows.default: expected {'train': 4, 'test': 3}, got {'train': 4, 'test': 2}",
                     ("[spec] routing.contract: expected {'contracts_specialist': 1}, "
                      "got {'contracts_specialist': 12}")]


def test_accepted_findings_need_a_reason_and_must_keep_occurring():
    f = rv.Finding("manifest", "x")
    with pytest.raises(ValueError, match="reason"):
        rv.make_spec("v0.1", "abc", {}, accepted=[f])
    spec = rv.make_spec("v0.1", "abc", {}, accepted=[f], reason="immutable")
    assert rv.apply_accepted([f, rv.Finding("blind", "y")], spec) == [rv.Finding("blind", "y")]
    assert rv.apply_accepted([], spec) == [rv.Finding("spec", "accepted finding no longer occurs: [manifest] x")]


def test_resolve_tag_returns_the_commit_not_the_tag_object():
    from types import SimpleNamespace as NS

    class Api:
        def list_repo_refs(self, repo, repo_type):
            return NS(tags=[NS(name="v9.3", target_commit="tagobject")])

        def dataset_info(self, repo, revision):
            assert revision == "v9.3"
            return NS(sha="commit")

    assert rv.resolve_tag(Api(), "v9.3") == "commit"
    with pytest.raises(LookupError):
        rv.resolve_tag(Api(), "v9.9")


def test_versions_sort_numerically():
    assert sorted(["v9.10", "v9.2", "v10.0", "v9.9"], key=rv.version_key) == ["v9.2", "v9.9", "v9.10", "v10.0"]


# --- the frozen specs ------------------------------------------------------

def test_the_pinned_release_has_a_frozen_spec():
    """Re-pinning config.py to a new release without freezing its spec fails here."""
    spec = rv.load_spec(REPO_TAG)
    assert spec["tag"] == REPO_TAG and spec["revision"] == REPO_REVISION


def test_the_pinned_release_routes_every_class_to_its_own_specialist():
    routing = rv.load_spec(REPO_TAG)["routing"]
    assert {c: list(by) for c, by in routing.items()} == {c: [s] for c, s in SPECIALIST_BY_CLASS.items()}


@pytest.mark.parametrize("tag", rv.frozen_tags())
def test_frozen_specs_are_well_formed_and_label_free(tag):
    spec = rv.load_spec(tag)
    assert spec["format"] == rv.SPEC_FORMAT and spec["tag"] == tag and len(spec["revision"]) == 40
    allowed = set(rv.SPEC_META) | {"rows", "columns", "classes", "subclasses", "annotation_method",
                                   "gt_fields_keys", "routing", "blind_metadata_keys", "files"}
    assert set(spec) <= allowed  # aggregates only: no per-row values can ride in a spec
    assert spec["columns"]["default"] == list(rv.BLIND_COLUMNS)
    assert not rv.LABEL_KEYS & set(spec["blind_metadata_keys"])
    assert all(a["reason"].strip() for a in spec.get("accepted_findings", []))


def test_the_local_pinned_snapshot_matches_the_frozen_spec():
    marker = DATA_DIR / ".hub_revision"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != REPO_REVISION:
        pytest.skip("pinned Hub snapshot not fetched (run_all.py --phases P0)")
    spec = rv.load_spec(REPO_TAG)
    facts = rv.collect_facts(DATA_DIR)
    parquet_facts = ("rows", "columns", "classes", "subclasses", "annotation_method",
                     "gt_fields_keys", "routing", "blind_metadata_keys")
    assert rv.compare_facts({k: facts[k] for k in parquet_facts}, {k: spec[k] for k in parquet_facts}) == []
    assert (rv.check_presence(DATA_DIR) + rv.check_blind(DATA_DIR) + rv.check_splits_and_identity(DATA_DIR)
            + rv.check_routing(DATA_DIR) + rv.check_routing_matches_registry(DATA_DIR)) == []
    local = {f: rv.sha256_path(DATA_DIR / f) for f in spec["files"] if f.startswith("parquet/")}
    assert local == {f: s for f, s in spec["files"].items() if f.startswith("parquet/")}


@pytest.mark.skipif(os.environ.get("MAILROOM_LIVE_HUB") != "1", reason="live Hub check (MAILROOM_LIVE_HUB=1)")
@pytest.mark.parametrize("tag", rv.frozen_tags())
def test_live_release_on_the_hub(tag, tmp_path):
    from huggingface_hub import HfApi

    assert rv.verify_release(HfApi(), tag, cache_dir=tmp_path) == []


# --- review findings (stand-in review of 9363de2) ---------------------------

def test_card_must_map_each_config_to_its_own_dirs(tmp_path):
    snap = make_snapshot(tmp_path)
    card = CARD.replace("path: parquet/default/train/*", "path: parquet/ground_truth/train/*")
    (snap / "README.md").write_text(card, encoding="utf-8")
    assert "config default maps to" in str(rv.check_card_configs(snap))
    swapped = CARD.replace("parquet/bundles/train/*", "TMP").replace(
        "parquet/bundles/test/*", "parquet/bundles/train/*").replace("TMP", "parquet/bundles/test/*")
    (snap / "README.md").write_text(swapped, encoding="utf-8")
    assert "config bundles maps to" in str(rv.check_card_configs(snap))


def test_blind_metadata_must_be_a_struct(tmp_path):
    def stringify(rows):
        for r in rows["default", "test"]:
            r["metadata"] = json.dumps({"intent": "notice"})
    assert "metadata is not a struct on 2 rows" in str(rv.check_blind(make_snapshot(tmp_path, stringify)))


@pytest.mark.parametrize("key,label", [("gt_intent", True), ("expected_class", True), ("doc_label", True),
                                       ("routing_specialist", True), ("source", False), ("subject", False)])
def test_label_shaped_keys_are_caught(key, label):
    assert rv.is_label_key(key) is label


def test_every_config_must_be_present_with_rows(tmp_path):
    import shutil

    snap = make_snapshot(tmp_path)
    shutil.rmtree(snap / "parquet" / "streams" / "test")
    _write(snap / "parquet" / "fixtures" / "test" / "test-00000-of-00001.parquet", [{"filename": "x"}])
    found = [str(f) for f in rv.check_presence(snap)]
    assert "[presence] streams/test: no parquet files" in found
    assert any(f.startswith("[schema] fixtures: shards disagree") for f in found)


def test_an_empty_split_is_reported(tmp_path):
    snap = make_snapshot(tmp_path)
    path = snap / "parquet" / "bundles" / "test" / "test-00000-of-00001.parquet"
    pq.write_table(pq.read_table(path).slice(0, 0), path)
    assert "[presence] bundles/test: no rows" in [str(f) for f in rv.check_presence(snap)]


def test_ground_truth_needs_its_split_column(tmp_path):
    def drop(rows):
        for s in ("train", "test"):
            for r in rows["ground_truth", s]:
                del r["split"]
    found = [str(f) for f in rv.check_invariants(make_snapshot(tmp_path, drop))]
    assert "[schema] ground_truth/train has no split column" in found
    assert any("split column disagrees" in f for f in found)


def test_routed_rows_need_a_specialist(tmp_path):
    def blank(rows):
        rows["streams", "test"][0]["expected_specialist"] = ""
    assert "[routing] streams/test: 1 rows without a class or specialist" in [
        str(f) for f in rv.check_routing(make_snapshot(tmp_path, blank))]


def test_a_new_release_must_route_per_the_registry(tmp_path):
    def old(rows):
        for rs in rows.values():
            for r in rs:
                if "expected_specialist" in r:
                    r["expected_specialist"] = "legacy_specialist"
    snap = make_snapshot(tmp_path, old)
    assert rv.check_routing(snap) == []  # consistent, so the invariant passes
    assert "the registry says 'contracts_specialist'" in str(rv.check_routing_matches_registry(snap))


def test_an_observed_fact_the_spec_does_not_pin_fails(tmp_path):
    snap = make_snapshot(tmp_path)
    spec = rv.make_spec("v0.1", "abc", rv.collect_facts(snap))
    del spec["routing"]
    assert [str(f) for f in rv.compare_facts(rv.collect_facts(snap), spec)] == [
        "[spec] observed fact 'routing' is not pinned by the spec"]


def test_only_manifest_findings_can_be_accepted():
    with pytest.raises(ValueError, match=r"\['blind'\] can never be accepted"):
        rv.make_spec("v0.1", "abc", {}, accepted=[rv.Finding("blind", "x")], reason="r")


def test_fetch_takes_only_a_commit_and_never_reuses_a_leftover(tmp_path, monkeypatch):
    import huggingface_hub

    with pytest.raises(ValueError, match="not a commit sha"):
        rv.fetch("", cache_dir=tmp_path)
    sha = "a" * 40
    (tmp_path / sha).mkdir()
    (tmp_path / sha / "stale.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda **k: None)
    assert not (rv.fetch(sha, cache_dir=tmp_path) / "stale.txt").exists()


SHA = "c" * 40


class _Api:
    def __init__(self, sha=SHA):
        self.sha = sha

    def list_repo_refs(self, repo, repo_type):
        from types import SimpleNamespace as NS
        return NS(tags=[NS(name="v1.0", target_commit="tagobject"), NS(name="v1.1", target_commit="t2")])

    def dataset_info(self, repo, revision):
        from types import SimpleNamespace as NS
        return NS(sha=self.sha)


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """Specs in tmp; fetch copies a synthetic snapshot; no network card check."""
    import shutil

    monkeypatch.setattr(rv, "SPEC_DIR", tmp_path / "specs")
    monkeypatch.setattr(rv, "check_card_on_hub", lambda snap: [])
    source = {"snap": make_snapshot(tmp_path / "source")}

    def fake_fetch(revision, repo_id=rv.REPO_ID, cache_dir=rv.CACHE_DIR):
        dest = tmp_path / "cache" / revision
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(source["snap"], dest)
        return dest

    monkeypatch.setattr(rv, "fetch", fake_fetch)
    return source


def test_freeze_then_verify_round_trip_and_cleanup(tmp_path, hub):
    spec, _ = rv.freeze_release(_Api(), "v1.0")
    assert spec["revision"] == SHA and spec["tag"] == "v1.0"
    rv.write_spec(spec)
    assert rv.verify_release(_Api(), "v1.0", cache_dir=tmp_path / "cache") == []
    assert not (tmp_path / "cache" / SHA).exists()


def test_verify_reports_a_moved_tag_and_a_foreign_spec(tmp_path, hub):
    rv.write_spec(rv.freeze_release(_Api(), "v1.0")[0])
    found = [str(f) for f in rv.verify_release(_Api("d" * 40), "v1.0", repo_id="someone/else")]
    assert found[0] == f"[spec] spec is for {rv.REPO_ID}, not someone/else"
    assert found[1] == f"[tag] v1.0 resolves to {'d' * 40}, spec pins {SHA}"


def test_freeze_refuses_an_existing_spec_without_refreeze(hub):
    rv.write_spec(rv.freeze_release(_Api(), "v1.0")[0])
    spec, lines = rv.freeze_release(_Api(), "v1.0")
    assert spec is None and "already exists" in lines[0]
    spec, lines = rv.freeze_release(_Api(), "v1.0", refreeze=True)
    assert spec is not None and "changes since the existing spec:" in lines


def test_freeze_refuses_a_failing_release(tmp_path, hub):
    def leak(rows):
        for r in rows["default", "test"]:
            r["metadata"] = {"source": "x", "intent": "notice"}
    hub["snap"] = make_snapshot(tmp_path / "leaky", leak)
    spec, lines = rv.freeze_release(_Api(), "v1.0", accept_reason="please")
    assert spec is None and any("label keys ['intent']" in ln for ln in lines)


def test_freeze_records_reviewed_manifest_findings(tmp_path, hub):
    snap = hub["snap"]
    (snap / "notes.txt").write_text("x", encoding="utf-8")  # served but unhashed
    spec, lines = rv.freeze_release(_Api(), "v1.0")
    assert spec is None and "--accept-findings" in lines[-1]
    spec, lines = rv.freeze_release(_Api(), "v1.0", accept_reason="immutable")
    assert spec["accepted_findings"] == [{"finding": "[manifest] served file not in the manifest: notes.txt",
                                          "reason": "immutable"}]


def test_freeze_refuses_new_blind_metadata_keys(tmp_path, hub):
    rv.write_spec(rv.freeze_release(_Api(), "v1.0")[0])

    def add_key(rows):
        for s in ("train", "test"):
            for r in rows["default", s]:
                r["metadata"] = {"source": "x", "category": "Distributor"}
    hub["snap"] = make_snapshot(tmp_path / "next", add_key)
    spec, lines = rv.freeze_release(_Api("e" * 40), "v1.1")
    assert spec is None and "adds blind metadata keys ['category']" in lines[0]
    spec, _ = rv.freeze_release(_Api("e" * 40), "v1.1", allow_new_blind_keys=True)
    assert spec["blind_metadata_keys"] == ["category", "source"]
