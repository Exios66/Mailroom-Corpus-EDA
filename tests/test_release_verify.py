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
    assert rv.compare_facts(facts, {k: spec[k] for k in parquet_facts}) == []
    assert rv.check_blind(DATA_DIR) + rv.check_splits_and_identity(DATA_DIR) + rv.check_routing(DATA_DIR) == []
    local = {f: rv.sha256_path(DATA_DIR / f) for f in spec["files"] if f.startswith("parquet/")}
    assert local == {f: s for f, s in spec["files"].items() if f.startswith("parquet/")}


@pytest.mark.skipif(os.environ.get("MAILROOM_LIVE_HUB") != "1", reason="live Hub check (MAILROOM_LIVE_HUB=1)")
@pytest.mark.parametrize("tag", rv.frozen_tags())
def test_live_release_on_the_hub(tag, tmp_path):
    from huggingface_hub import HfApi

    assert rv.verify_release(HfApi(), tag, cache_dir=tmp_path) == []
