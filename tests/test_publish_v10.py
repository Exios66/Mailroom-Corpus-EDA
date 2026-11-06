"""The guarded v10 publish: refusals, the offline dry run, and the tag rule."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mailroom_eda import publish_v10

ROOT = Path(__file__).resolve().parents[1]
PASS = {"conformance": lambda: (True, "ok"), "lockstep": lambda: (True, "ok"), "clean tree": lambda: (True, "ok")}


class FakeApi:
    """Records Hub calls; the dry run must make none."""

    def __init__(self, tags=()):
        self.calls, self.tags = [], set(tags)

    def list_repo_refs(self, repo, repo_type):
        self.calls.append("list_repo_refs")
        return SimpleNamespace(tags=[SimpleNamespace(name=t) for t in self.tags])

    def upload_folder(self, **kw):
        self.calls.append("upload_folder")
        self.upload = kw
        return SimpleNamespace(oid="abc123")

    def create_tag(self, repo, **kw):
        self.calls.append("create_tag")
        self.tag = kw


@pytest.fixture
def stage(tmp_path):
    """A stage folder with a manifest that lists its files."""
    st = tmp_path / "stage"
    (st / "default").mkdir(parents=True)
    (st / "default/train-00000-of-00001.parquet").write_bytes(b"x")
    (st / "README.md").write_text("card")
    (st / "manifest.json").write_text(json.dumps({"configs": ["default"], "rows": {},
                                                  "files": {"README.md": "h", "default/train-00000-of-00001.parquet": "h"}}))
    return st


def _args(stage, **kw):
    base = dict(stage=stage, source=stage, labels_root=None, campaign="v10", repo="o/r", eda=ROOT,
                mailroom=stage, sandbox=stage, dojo=None, rows=3292, tag="v10", initials="JB", confirm=False)
    return SimpleNamespace(**{**base, **kw})


@pytest.mark.parametrize("failing", ["conformance", "lockstep", "clean tree"])
def test_publish_refuses_when_a_guard_fails(stage, failing, capsys):
    api = FakeApi()
    checks = {**PASS, failing: lambda: (False, "nope")}
    assert publish_v10.publish(_args(stage, confirm=True), api=api, checks=checks) == 1
    assert api.calls == [] and "refusing" in capsys.readouterr().err


@pytest.mark.parametrize("kw", [{"initials": None}, {"initials": "jb"}, {"initials": "J"}, {"tag": None},
                                {"tag": "v9.3"}, {"tag": "latest"}])
def test_publish_refuses_bad_tag_or_initials(stage, kw):
    api = FakeApi()
    assert publish_v10.publish(_args(stage, confirm=True, **kw), api=api, checks=PASS) == 1
    assert api.calls == []


def test_dry_run_touches_no_network(stage, capsys, monkeypatch):
    monkeypatch.setattr("mailroom_eda.hf_interface.get_hf_api", lambda *a, **k: pytest.fail("network"))
    api = FakeApi()
    assert publish_v10.publish(_args(stage), api=api, checks=PASS) == 0
    out = capsys.readouterr().out
    assert api.calls == [] and "would upload 3 files" in out and "add --confirm" in out


def test_existing_tag_is_never_moved(stage, capsys):
    api = FakeApi(tags={"v10"})
    assert publish_v10.publish(_args(stage, confirm=True), api=api, checks=PASS) == 1
    assert api.calls == ["list_repo_refs"] and "never moved" in capsys.readouterr().err


def test_confirmed_publish_uploads_the_manifest_files_then_tags(stage, monkeypatch, tmp_path):
    cfg = tmp_path / "config.py"
    cfg.write_text('REPO_ID = "o/r"\nREPO_TAG = "v9.2"\n')
    real = publish_v10.bump_repo_tag
    seen = []
    monkeypatch.setattr(publish_v10, "bump_repo_tag", lambda tag, path=None: seen.append(path) or real(tag, cfg))
    api = FakeApi()
    assert publish_v10.publish(_args(stage, confirm=True), api=api, checks=PASS) == 0
    assert api.calls == ["list_repo_refs", "upload_folder", "create_tag"]
    assert api.upload["allow_patterns"] == ["README.md", "default/train-00000-of-00001.parquet", "manifest.json"]
    assert api.upload["delete_patterns"] == ["default/**"]
    assert api.tag["revision"] == "abc123" and api.tag["exist_ok"] is False
    assert 'REPO_TAG = "v10"' in cfg.read_text()
    assert seen == [Path(_args(stage, confirm=True).eda) / "src" / "mailroom_eda" / "config.py"]


def test_delete_patterns_cover_the_real_parquet_layout():
    files = ["README.md", "manifest.json", "parquet/default/train/train-00000-of-00001.parquet",
             "parquet/default/dataset_info.json", "parquet/ground_truth/test/test-00000-of-00001.parquet"]
    assert publish_v10.delete_patterns(files, ["default", "ground_truth"]) == \
        ["parquet/default/**", "parquet/ground_truth/**"]


def test_lockstep_guard_takes_this_repos_tag_as_the_one_being_cut(tmp_path):
    sys.path.insert(0, str(ROOT / "tests"))
    from test_labeling import _lockstep_fixture

    sys.path.pop(0)
    eda, mr, sb, _ = _lockstep_fixture(tmp_path, version_slot="v0.28.0")
    (eda / "src/mailroom_eda/config.py").write_text('REPO_TAG = "v9.2"\n')  # bumped by the publish itself
    assert publish_v10.lockstep_check(eda, mr, sb, None, 3292, "v10")[0]
    ok, detail = publish_v10.lockstep_check(eda, mr, sb, None, 3302, "v10")
    assert not ok and "Corpus size" in detail
    assert not publish_v10.lockstep_check(eda, mr, sb, None, 3292, "v10.1")[0]


def test_clean_tree_reads_git_status():
    def run(out, code=0):
        return lambda *a, **k: SimpleNamespace(returncode=code, stdout=out, stderr="boom")

    assert publish_v10.clean_tree(ROOT, run=run(""))[0]
    assert not publish_v10.clean_tree(ROOT, run=run(" M src/x.py\n"))[0]
    assert not publish_v10.clean_tree(ROOT, run=run("", 128))[0]


def test_script_dry_run_refuses_without_a_stage(tmp_path):
    res = subprocess.run([sys.executable, str(ROOT / "scripts/publish/publish_v10.py"), "--stage",
                          str(tmp_path / "none"), "--mailroom", str(tmp_path), "--sandbox", str(tmp_path)],
                         capture_output=True, text=True)
    assert res.returncode != 0 and "build the stage first" in res.stderr
