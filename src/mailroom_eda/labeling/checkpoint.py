"""Checkpoint labeling output to the private Hugging Face staging copy.

Every paid answer is credit already spent, so each ``chunk``/``run``, ``review`` and
``gate --approve`` uploads the labels root (``docs/reports/audits/v10_labels``) plus the
campaign's ``approved.yaml`` to ``STAGING_REPO``, a **private** dataset repo, and
sha-verifies every file it sent. ``restore`` pulls missing files back, so a lost
checkout re-reads the staged answers (``runner.import_reusable``) instead of buying
them again.

What it never does:

- publish: it refuses a staging repo that is public, and never touches ``REPO_ID``;
- publish spend: costs stay in the journals, because this repo is a private backup of the
  logs (2026-11-06: a stripped backup lost the spending records when it was restored); the
  published dataset never carries them;
- build a blind config: the card declares one ``labels`` config (GT side only), so the
  Hub never auto-builds a ``default`` config from label files.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .. import hf_interface

STAGING_REPO = "Lucius-Morningstar/mailroom-dataset-v10-staging"
LABELS_DIR = "labels"
APPROVALS_FILE = "campaign/approved.yaml"
SUFFIXES = {".jsonl", ".json", ".csv", ".md", ".yaml"}
CARD = """---
license: cc-by-4.0
configs:
- config_name: labels
  data_files:
  - split: train
    path: "labels/**/*-labels.jsonl"
---

# mailroom-dataset v10 labeling staging (private)

Work-in-progress v10 label checkpoints for `Lucius-Morningstar/mailroom-dataset`, written by
`mailroom_eda.labeling.checkpoint` after each run, review and gate approval. **Not a release.**
Ground-truth side only: nothing here belongs in the blind `default` config. Journals are kept
byte-for-byte, spend included: this is a private backup of the run logs, never published.
"""


def _sources(labels_root: Path, approvals: Path | None) -> Iterable[tuple[str, Path]]:
    """``(path_in_repo, local file)`` for everything a checkpoint carries."""
    for p in sorted(labels_root.rglob("*")):
        if p.is_file() and p.suffix in SUFFIXES and "__pycache__" not in p.parts:
            yield f"{LABELS_DIR}/{p.relative_to(labels_root).as_posix()}", p
    if approvals is not None and approvals.exists():
        yield APPROVALS_FILE, approvals


def stage(labels_root: Path, approvals: Path | None, dest: Path) -> dict[str, Path]:
    """Copy the checkpoint tree under ``dest`` byte-for-byte; returns ``{path_in_repo: file}``."""
    out: dict[str, Path] = {}
    for rel, src in _sources(labels_root, approvals):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(src.read_bytes())
        out[rel] = target
    (dest / "README.md").write_text(CARD, encoding="utf-8")
    out["README.md"] = dest / "README.md"
    return out


def git_blob_sha(data: bytes) -> str:
    """The git blob id the Hub reports for a non-LFS file."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def verify(api: Any, repo_id: str, staged: dict[str, Path]) -> list[str]:
    """Paths whose Hub copy is missing or differs (LFS sha256, else git blob sha1)."""
    hub = {f.path: f for f in api.list_repo_tree(repo_id=repo_id, repo_type="dataset", recursive=True)}
    bad = []
    for rel, path in sorted(staged.items()):
        f = hub.get(rel)
        data = path.read_bytes()
        lfs = getattr(f, "lfs", None) if f is not None else None
        ok = f is not None and (lfs.sha256 == hashlib.sha256(data).hexdigest() if lfs is not None
                                else getattr(f, "blob_id", None) == git_blob_sha(data))
        if not ok:
            bad.append(rel)
    return bad


def ensure_private(api: Any, repo_id: str) -> None:
    """Create the staging repo private; refuse one that exists and is public."""
    hf_interface.create_dataset_repo(api, repo_id, private=True)
    if not api.repo_info(repo_id=repo_id, repo_type="dataset").private:
        raise SystemExit(f"{repo_id} is public; labeling checkpoints go only to a private repo")


def checkpoint(labels_root: Path, approvals: Path | None, message: str, *, api: Any = None,
               repo_id: str = STAGING_REPO) -> dict[str, Any]:
    """Upload the labels root to the private staging repo and verify every file."""
    if repo_id == hf_interface.REPO_ID:
        raise SystemExit("checkpoints never go to the published dataset repo")
    api = api or hf_interface.get_hf_api()
    ensure_private(api, repo_id)
    with tempfile.TemporaryDirectory() as tmp:
        staged = stage(Path(labels_root), approvals, Path(tmp))
        hf_interface.upload_folder(api, tmp, repo_id, message)
        bad = verify(api, repo_id, staged)
    if bad:
        raise SystemExit(f"checkpoint upload did not verify: {', '.join(bad[:10])}")
    return {"repo": repo_id, "files": len(staged), "verified": True}


def restore(labels_root: Path, *, api: Any = None, repo_id: str = STAGING_REPO) -> list[Path]:
    """Download staged label files missing locally (never overwrites); returns what it wrote."""
    api = api or hf_interface.get_hf_api()
    prefix = f"{LABELS_DIR}/"
    wrote = []
    for f in api.list_repo_tree(repo_id=repo_id, repo_type="dataset", recursive=True):
        path = getattr(f, "path", "")
        if not path.startswith(prefix) or not hasattr(f, "blob_id"):
            continue
        target = Path(labels_root) / path[len(prefix):]
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        local = hf_interface.download_file(api, repo_id, path, Path(tempfile.mkdtemp()) / path)
        target.write_bytes(Path(local).read_bytes())
        wrote.append(target)
    return wrote


def token_ready() -> bool:
    """An HF token is in the environment (``HF_TOKEN``) or the huggingface_hub cache."""
    if os.environ.get("HF_TOKEN"):
        return True
    from huggingface_hub import get_token  # noqa: PLC0415

    return bool(get_token())
