"""v9.3 merger routing amendment: one value changes, nothing else does."""
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mailroom_eda import v9_3_merger_routing as m


def _line(**kw):
    row = {"filename": "f.txt", "expected": "merger_agreement",
           "expected_specialist": "contracts_specialist", "doc_text": "a   b"}
    row.update(kw)
    return json.dumps(row, ensure_ascii=False)


def test_merger_line_is_rerouted_in_place():
    old = _line()
    new, changed = m.reroute_jsonl_line(old)
    assert changed
    assert new == old.replace('"contracts_specialist"', '"merger_agreement_specialist"')
    assert json.loads(new)["expected_specialist"] == "merger_agreement_specialist"


@pytest.mark.parametrize("row", [
    {"expected": "contract"},
    {"expected": "merger_agreement", "expected_specialist": "merger_agreement_specialist"},
    {"expected": "correspondence", "expected_specialist": "correspondence_specialist"},
])
def test_other_lines_are_kept_byte_for_byte(row):
    old = _line(**row)
    assert m.reroute_jsonl_line(old) == (old, False)


def test_reroute_jsonl_keeps_trailing_newline(tmp_path):
    src, dst = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    src.write_text(_line() + "\n" + _line(expected="contract") + "\n", encoding="utf-8")
    assert m.reroute_jsonl(src, dst) == 1
    out = dst.read_text(encoding="utf-8")
    assert out.endswith("\n") and out.count("\n") == 2
    assert out.split("\n")[1] == _line(expected="contract")


def test_reroute_parquet_changes_only_merger_specialist(tmp_path):
    schema = pa.schema([("filename", pa.string()), ("expected", pa.string()),
                        ("expected_specialist", pa.string()), ("gt_fields", pa.string())])
    t = pa.Table.from_pylist([
        {"filename": "a", "expected": "merger_agreement", "expected_specialist": "contracts_specialist", "gt_fields": "{}"},
        {"filename": "b", "expected": "contract", "expected_specialist": "contracts_specialist", "gt_fields": "{\"x\": 1}"},
        {"filename": "c", "expected": "insurance_claim", "expected_specialist": "insurance_claims_specialist", "gt_fields": None},
    ], schema=schema)
    src, dst = tmp_path / "a.parquet", tmp_path / "b.parquet"
    pq.write_table(t, src)
    assert m.reroute_parquet(src, dst) == 1
    back = pq.read_table(dst)
    assert back.schema == schema
    assert back.column("expected_specialist").to_pylist() == [
        "merger_agreement_specialist", "contracts_specialist", "insurance_claims_specialist"]
    for col in ("filename", "expected", "gt_fields"):
        assert back.column(col).equals(t.column(col))


def test_registry_routes_every_class_to_its_own_specialist():
    m.check_registry()
    assert m.SPECIALIST_BY_CLASS == {
        "contract": "contracts_specialist",
        "merger_agreement": "merger_agreement_specialist",
        "corporate_record": "corporate_records_specialist",
        "correspondence": "correspondence_specialist",
        "insurance_claim": "insurance_claims_specialist",
    }


def test_verify_routing_rejects_any_misrouted_class(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "EXPECTED_CHANGES", {"x.jsonl": 0})
    (tmp_path / "x.jsonl").write_text(
        _line(expected="correspondence", expected_specialist="contracts_specialist") + "\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="correspondence"):
        m.verify_routing(tmp_path)


def test_manifest_adds_one_amendment_line_and_recomputes_shas(tmp_path):
    (tmp_path / "a.txt").write_text("new", encoding="utf-8")
    old = ("name : x\nlabels_version: v9.3 corporate\nsha256     :\n"
           "  a.txt  " + "0" * 64 + "\n")
    out = m.render_manifest(old, tmp_path)
    assert out.count("amendment  :") == 1
    assert out.index("labels_version") < out.index("amendment  :")
    assert m.render_manifest(out, tmp_path).count("amendment  :") == 1
    (tmp_path / "manifest.txt").write_text(out, encoding="utf-8")
    assert m.verify_manifest(tmp_path) == 1


def test_manifest_refuses_a_changed_file_set(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "extra.txt").write_text("y", encoding="utf-8")
    old = "labels_version: v\nsha256     :\n  a.txt  " + "0" * 64 + "\n"
    with pytest.raises(AssertionError, match="file set"):
        m.render_manifest(old, tmp_path)


@pytest.mark.parametrize("stage", ["same", "inside_base", "contains_base"])
def test_build_refuses_overlapping_dirs(tmp_path, stage):
    base = tmp_path / "base"
    base.mkdir()
    s = {"same": base, "inside_base": base / "stage", "contains_base": tmp_path}[stage]
    with pytest.raises(SystemExit, match="overlaps"):
        m.check_dirs(base, s)
    assert base.is_dir()


def _stage(tmp_path, files):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    old = "labels_version: v\nsha256     :\n" + "".join(f"  {n}  {'0' * 64}\n" for n in files)
    (tmp_path / "manifest.txt").write_text(m.render_manifest(old, tmp_path), encoding="utf-8")


def test_publish_check_rejects_a_doc_changed_after_the_manifest(tmp_path):
    _stage(tmp_path, {"README.md": "card", "CHANGELOG.md": "log"})
    m.check_manifest_covers(tmp_path, m.DOC_FILES)
    (tmp_path / "README.md").write_text("edited later", encoding="utf-8")
    with pytest.raises(AssertionError, match="README.md"):
        m.check_manifest_covers(tmp_path, m.DOC_FILES)


def test_publish_check_rejects_an_unlisted_doc(tmp_path):
    _stage(tmp_path, {"README.md": "card"})
    with pytest.raises(SystemExit, match="CHANGELOG.md"):
        m.check_manifest_covers(tmp_path, m.DOC_FILES)


class _FakeHub:
    """Tags only: delete/create/list, with an optional failing create."""

    def __init__(self, tags, fail_on=None):
        self.tags, self.fail_on = dict(tags), fail_on

    def list_repo_refs(self, repo, repo_type):
        from types import SimpleNamespace as NS
        return NS(tags=[NS(name=n) for n in self.tags])

    def dataset_info(self, repo, revision):
        from types import SimpleNamespace as NS
        return NS(sha=self.tags[revision])

    def delete_tag(self, repo, tag, repo_type):
        del self.tags[tag]

    def create_tag(self, repo, tag, revision, repo_type, tag_message=None):
        if revision == self.fail_on:
            raise RuntimeError("hub error")
        if tag in self.tags:  # the Hub refuses to overwrite a tag
            raise RuntimeError(f"tag {tag} exists")
        self.tags[tag] = revision


def test_move_tag_moves_it():
    hub = _FakeHub({"v9.3": "old"})
    m.move_tag(hub, "new")
    assert hub.tags == {"v9.3": "new"}


def test_move_tag_restores_the_old_target_when_create_fails():
    hub = _FakeHub({"v9.3": "old"}, fail_on="new")
    with pytest.raises(SystemExit, match="old target old, new target new, tag now at old"):
        m.move_tag(hub, "new")
    assert hub.tags == {"v9.3": "old"}


def test_move_tag_creates_a_missing_tag():
    hub = _FakeHub({})
    m.move_tag(hub, "new")
    assert hub.tags == {"v9.3": "new"}


def test_move_tag_is_a_no_op_when_already_there():
    hub = _FakeHub({"v9.3": "same"}, fail_on="same")
    m.move_tag(hub, "same")
    assert hub.tags == {"v9.3": "same"}


def test_move_tag_reports_when_the_restore_also_fails():
    hub = _FakeHub({"v9.3": "old"}, fail_on="new")
    real_create = hub.create_tag

    def create(repo, tag, revision, repo_type, tag_message=None):
        if revision == "old":
            raise RuntimeError("restore refused")
        return real_create(repo, tag, revision, repo_type, tag_message)

    hub.create_tag = create
    with pytest.raises(SystemExit, match=r"old target old, new target new, tag now at unknown \(restore failed"):
        m.move_tag(hub, "new")


def test_move_tag_reports_when_the_new_tag_resolves_elsewhere():
    hub = _FakeHub({"v9.3": "old"})
    real_info = hub.dataset_info
    calls = {"n": 0}

    def info(repo, revision):
        calls["n"] += 1
        out = real_info(repo, revision)
        if calls["n"] == 2:  # the post-create check
            from types import SimpleNamespace as NS
            return NS(sha="elsewhere")
        return out

    hub.dataset_info = info
    with pytest.raises(SystemExit, match="old target old, new target new"):
        m.move_tag(hub, "new")
    assert hub.tags == {"v9.3": "new"}  # the tag exists, so nothing is restored over it


def test_build_checks_dirs_before_deleting(tmp_path, monkeypatch):
    base = tmp_path / "base"
    (base / "keep.txt").parent.mkdir(parents=True)
    (base / "keep.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(m, "check_registry", lambda: None)
    with pytest.raises(SystemExit, match="overlaps"):
        m.build(base, base / "stage")
    assert (base / "keep.txt").is_file()


def test_check_dirs_refuses_a_stage_outside_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    with pytest.raises(SystemExit, match="not a directory inside"):
        m.check_dirs(tmp_path / "data" / "base", tmp_path / "repo")
    with pytest.raises(SystemExit, match="not a directory inside"):
        m.check_dirs(tmp_path / "base", tmp_path / "data")
    m.check_dirs(tmp_path / "data" / "base", tmp_path / "data" / "x_stage")


def test_check_dirs_refuses_the_pinned_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DATA_DIR", tmp_path)
    with pytest.raises(SystemExit, match=r"not named \*_stage"):
        m.check_dirs(tmp_path / "base", tmp_path / "parquet")
    (tmp_path / "real_stage").mkdir()
    (tmp_path / "link_stage").symlink_to(tmp_path / "real_stage")
    with pytest.raises(SystemExit, match="is a symlink"):
        m.check_dirs(tmp_path / "base", tmp_path / "link_stage")


def test_check_dirs_sees_through_symlinks(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DATA_DIR", tmp_path)
    base = tmp_path / "base"
    base.mkdir()
    (tmp_path / "base" / "x_stage").mkdir()
    (tmp_path / "alias").symlink_to(base)
    with pytest.raises(SystemExit, match="overlaps"):
        m.check_dirs(base, tmp_path / "alias" / "x_stage")


def test_manifest_covers_every_staged_file_by_default(tmp_path):
    _stage(tmp_path, {"README.md": "card"})
    m.check_manifest_covers(tmp_path)
    (tmp_path / "new.parquet").write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit, match="new.parquet"):
        m.check_manifest_covers(tmp_path)


class _PathsHub:
    def __init__(self, shas):
        self.shas = shas

    def get_paths_info(self, repo, paths, repo_type, revision):
        from types import SimpleNamespace as NS
        return [NS(path=p, lfs=NS(sha256=self.shas[p])) for p in paths if p in self.shas]


def test_docs_only_parent_must_hold_the_manifest_shas(tmp_path):
    _stage(tmp_path, {"README.md": "card", "data.parquet": "rows"})
    good = m.sha256_file(tmp_path / "data.parquet")
    assert m.check_parent_matches_manifest(_PathsHub({"data.parquet": good}), tmp_path, "p", m.DOC_FILES) == 1
    with pytest.raises(SystemExit, match="data.parquet: manifest sha"):
        m.check_parent_matches_manifest(_PathsHub({"data.parquet": "0" * 64}), tmp_path, "p", m.DOC_FILES)
    with pytest.raises(SystemExit, match="not at p"):
        m.check_parent_matches_manifest(_PathsHub({}), tmp_path, "p", m.DOC_FILES)


def test_publish_checks_before_committing(tmp_path, monkeypatch):
    import huggingface_hub

    _stage(tmp_path, {"README.md": "card", "CHANGELOG.md": "log"})
    (tmp_path / "README.md").write_text("edited after the manifest", encoding="utf-8")

    class _NoCommit:
        def __init__(self, token=None):
            pass

        def create_commit(self, *a, **k):
            raise AssertionError("create_commit reached with a stale manifest")

    monkeypatch.setattr(huggingface_hub, "HfApi", _NoCommit)
    with pytest.raises(AssertionError, match="manifest sha mismatch: README.md"):
        m.publish(tmp_path, "tok", parent="p", only=m.DOC_FILES)


class _ParentHub:
    """HfApi stand-in: a parent whose data file does not match the manifest."""

    token = "tok"

    def __init__(self, token=None, sha="0" * 64, lfs=True):
        self.sha, self.lfs = sha, lfs

    def get_paths_info(self, repo, paths, repo_type, revision):
        from types import SimpleNamespace as NS
        return [NS(path=p, lfs=NS(sha256=self.sha) if self.lfs else None) for p in paths]

    def create_commit(self, *a, **k):
        raise AssertionError("create_commit reached although the parent differs from the manifest")


def test_publish_runs_the_parent_check_before_committing(tmp_path, monkeypatch):
    import huggingface_hub

    _stage(tmp_path, {"README.md": "card", "CHANGELOG.md": "log", "data.parquet": "rows"})
    m.check_manifest_covers(tmp_path, m.DOC_FILES)  # the stage itself is valid
    monkeypatch.setattr(huggingface_hub, "HfApi", _ParentHub)
    with pytest.raises(SystemExit, match="data.parquet: manifest sha"):
        m.publish(tmp_path, "tok", parent="p", only=m.DOC_FILES)


def test_parent_check_hashes_a_non_lfs_file(tmp_path, monkeypatch):
    import huggingface_hub

    _stage(tmp_path, {"README.md": "card", "small.json": "{}"})
    served = tmp_path / "served.json"
    seen = {}

    def fake_download(repo, rel, repo_type, revision, token=None):
        seen.update(rel=rel, revision=revision, token=token)
        return str(served)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    served.write_text("{}", encoding="utf-8")
    assert m.check_parent_matches_manifest(_ParentHub(lfs=False), tmp_path, "p", m.DOC_FILES) == 1
    assert seen == {"rel": "small.json", "revision": "p", "token": "tok"}
    served.write_text("{ }", encoding="utf-8")
    with pytest.raises(SystemExit, match="small.json: manifest sha"):
        m.check_parent_matches_manifest(_ParentHub(lfs=False), tmp_path, "p", m.DOC_FILES)
