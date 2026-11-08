"""Offline immutable-revision and corrupt-backup recovery checks."""
from types import SimpleNamespace

import pytest

from mailroom_eda import hf_interface
from mailroom_eda.labeling import checkpoint


def test_upload_preserves_exact_commit_not_moving_head(tmp_path):
    revision = "a" * 40
    api = SimpleNamespace(upload_folder=lambda **kw: SimpleNamespace(oid=revision))
    result = hf_interface.upload_folder(api, tmp_path, "private/staging", "test")
    assert result["revision"] == revision


def test_restore_all_review_assets_and_reject_corruption(tmp_path, monkeypatch):
    files = {f"labels/review_bundle/sample{suffix}": b"private fixture"
             for suffix in (".json", ".jsonl", ".html", ".txt", ".xlsx")}
    revision = "b" * 40
    def tree(**kwargs):
        assert kwargs["revision"] == revision
        return [SimpleNamespace(path=p, blob_id=checkpoint.git_blob_sha(data)) for p, data in files.items()]
    api = SimpleNamespace(repo_info=lambda **kw: SimpleNamespace(private=True, sha=revision),
                          list_repo_tree=tree)
    corrupt = True
    def download(api, repo, filename, path, *, revision):
        assert revision == "b" * 40
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"corrupt" if corrupt else files[filename])
        return path
    monkeypatch.setattr(hf_interface, "download_file", download)
    root = tmp_path / "restored"
    with pytest.raises(SystemExit, match="did not verify"):
        checkpoint.restore(root, api=api, revision=revision)
    assert not root.exists()
    corrupt = False
    assert len(checkpoint.restore(root, api=api, revision=revision)) == len(files)
    assert checkpoint.restore(root, api=api, revision=revision) == []
    for p, data in files.items():
        assert (root / p.removeprefix("labels/")).read_bytes() == data


def test_central_download_passes_authentication_and_revision(tmp_path, monkeypatch):
    def download(**kwargs):
        assert kwargs["token"] == "test-only-token"
        assert kwargs["revision"] == "c" * 40
        return str(tmp_path / "source.txt")
    monkeypatch.setattr(hf_interface, "hf_hub_download", download)
    hf_interface.download_file(SimpleNamespace(token="test-only-token"), "private/staging",
                               "labels/source.txt", tmp_path / "source.txt", revision="c" * 40)
