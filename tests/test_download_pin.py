"""download_corpus re-fetches when the local snapshot is not at the pinned revision."""
import pytest

from mailroom_eda import download as d


@pytest.fixture
def snap(tmp_path, monkeypatch):
    calls = []
    parquet = tmp_path / "parquet"
    monkeypatch.setattr(d, "DATA_DIR", tmp_path)
    monkeypatch.setattr(d, "PARQUET_DIR", parquet)
    monkeypatch.setattr(d, "REVISION_MARKER", tmp_path / ".hub_revision")
    monkeypatch.setattr(d, "REPO_REVISION", "pin-new")
    monkeypatch.setattr(d, "snapshot_download", lambda **kw: calls.append(kw["revision"]))
    (parquet / "ground_truth" / "train").mkdir(parents=True)
    (parquet / "ground_truth" / "train" / "train-00000-of-00001.parquet").write_bytes(b"x")
    return tmp_path, calls


def test_a_snapshot_without_a_marker_is_refetched(snap):
    root, calls = snap
    d.download_corpus()
    assert calls == ["pin-new"]
    assert (root / ".hub_revision").read_text().strip() == "pin-new"


def test_a_snapshot_at_an_old_pin_is_refetched(snap):
    root, calls = snap
    (root / ".hub_revision").write_text("pin-old\n")
    d.download_corpus()
    assert calls == ["pin-new"]


def test_a_snapshot_at_the_pin_is_reused(snap):
    root, calls = snap
    (root / ".hub_revision").write_text("pin-new\n")
    d.download_corpus()
    assert calls == []


def test_parse_manifest_joins_continuation_lines(tmp_path):
    """A wrapped manifest value is joined instead of crashing on an undefined key."""
    path = tmp_path / "manifest.txt"
    path.write_text("Title: Corpus\nVersion: v9.3\nnotes: first line\n  second line\n\nLicense: CC-BY-4.0\n")
    manifest = d.parse_manifest(path)
    assert manifest["notes"] == "first line second line"
    assert manifest["License"] == "CC-BY-4.0"
