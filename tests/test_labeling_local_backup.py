"""Checks for the labels-staging mirror of reviewed labels."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mailroom_eda.labeling import local_backup


def _packet(root: Path, name: str, packet_id: str, document_id: str, sha: str) -> Path:
    path = root / "packets" / f"{name}-review-data.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": "mailroom-review-packet/v1",
        "packet_id": packet_id,
        "records": [{"document_id": document_id, "field": "entity_name",
                     "document_sha256": sha, "doc_class": "corporate_record"}],
    }) + "\n", encoding="utf-8")
    return path


def _journal(root: Path, document_id: str, field: str = "entity_name") -> None:
    out = root / "portal"
    out.mkdir(exist_ok=True)
    (out / "confirmed-fields.jsonl").write_text(json.dumps({
        "event_id": "e" * 64, "receipt_id": "r" * 64, "packet_id": "p1",
        "document_id": document_id, "field": field, "decision": "ACCEPT",
        "reviewer": "LM", "server_received_at": "2026-11-08T20:00:00+00:00",
    }) + "\n", encoding="utf-8")


def test_backup_copies_work_and_refuses_a_duplicate_review(tmp_path: Path) -> None:
    labels_root, dest = tmp_path / "labels", tmp_path / "labels-staging"
    sha = "a" * 64
    _packet(labels_root, "corporate-A", "p1", "doc-1", sha)
    _journal(labels_root, "doc-1")

    first = local_backup.backup(labels_root, dest, out=labels_root / "portal",
                                now=datetime(2026, 11, 8, 20, 30, tzinfo=timezone.utc))

    assert first["documents"] == 1 and first["decisions"] == 1
    manifest = json.loads((Path(first["path"]) / local_backup.MANIFEST).read_text())
    assert manifest["reviewed_documents"][0]["fields"][0] == {
        "field": "entity_name", "decision": "ACCEPT", "reviewer": "LM",
        "server_received_at": "2026-11-08T20:00:00+00:00", "receipt_id": "r" * 64,
        "verified_models": [], "verified_outputs": [], "manual_source_verification": False,
    }
    assert (Path(first["path"]) / "packets" / "packets" / "corporate-A-review-data.json").is_file()
    assert (Path(first["path"]) / "portal" / "confirmed-fields.jsonl").is_file()
    assert (Path(first["path"]) / "SUMMARY.md").is_file()

    # A different packet that reviews the same document content is a duplicate.
    _packet(labels_root, "corporate-B", "p2", "doc-2", sha)
    _journal(labels_root, "doc-2")
    with pytest.raises(ValueError, match="already reviewed in an earlier backup: doc-2"):
        local_backup.backup(labels_root, dest, out=labels_root / "portal")

    allowed = local_backup.backup(labels_root, dest, out=labels_root / "portal", allow_re_review=True)
    assert allowed["re_review_allowed"] is True


def test_later_snapshot_of_same_document_is_not_a_duplicate(tmp_path: Path) -> None:
    labels_root, dest = tmp_path / "labels", tmp_path / "labels-staging"
    sha = "b" * 64
    _packet(labels_root, "corporate-A", "p1", "doc-1", sha)
    _journal(labels_root, "doc-1")
    first = local_backup.backup(labels_root, dest, out=labels_root / "portal",
                                now=datetime(2026, 11, 8, 20, 30, tzinfo=timezone.utc))
    # A second checkpoint of the SAME document is a snapshot, not an independent duplicate.
    second = local_backup.backup(labels_root, dest, out=labels_root / "portal",
                                 now=datetime(2026, 11, 8, 21, 30, tzinfo=timezone.utc))
    assert second["documents"] == 1 and Path(second["path"]) != Path(first["path"])
