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


def _scoped_write(scoped: Iterable[Any], repo_id: str) -> bool:
    """A fine-grained token grants ``repo.write`` on the repo itself or on its namespace."""
    namespace = repo_id.split("/")[0]
    for s in scoped or []:
        ent = s.get("entity") or {}
        perms = set(s.get("permissions") or [])
        if "repo.write" not in perms:
            continue
        if ent.get("type") in ("user", "org") and ent.get("name") == namespace:
            return True
        if ent.get("type") == "dataset" and ent.get("name") == repo_id:
            return True
    return False


def write_access(repo_id: str = STAGING_REPO, *, api: Any = None) -> tuple[bool, str]:
    """Whether the HF token can write ``repo_id``, from ``whoami`` (no upload, no repo creation).

    A ``write`` token must belong to the namespace's user or an org it may write in; a
    fine-grained token needs ``repo.write`` on the namespace or the repo. An existing repo
    must be private (``ensure_private`` refuses a public one at upload time anyway).
    """
    if api is None and not token_ready():
        return False, "no HF token (set HF_TOKEN or run `huggingface-cli login` with a write token)"
    api = api or hf_interface.get_hf_api()
    info = api.whoami()
    user = info.get("name")
    namespace = repo_id.split("/")[0]
    orgs = {o.get("name"): o.get("roleInOrg") for o in info.get("orgs") or []}
    tok = ((info.get("auth") or {}).get("accessToken") or {})
    role = tok.get("role")
    if role == "fineGrained":
        ok = _scoped_write((tok.get("fineGrained") or {}).get("scoped") or [], repo_id)
        why = "fine-grained token with repo.write" if ok else f"fine-grained token lacks repo.write on {namespace}"
    elif role == "write":
        ok = namespace == user or (namespace in orgs and orgs[namespace] not in ("read", None))
        why = (f"write token for {user}" if ok
               else f"write token for {user}, who cannot write in the {namespace} namespace")
    else:
        ok, why = False, f"token role is {role or 'unknown'}; checkpoints need a write token"
    if not ok:
        return False, why
    try:
        private = api.repo_info(repo_id=repo_id, repo_type="dataset").private
    except Exception as exc:  # noqa: BLE001 - a repo that does not exist yet is created private on upload
        if type(exc).__name__ == "RepositoryNotFoundError":
            return True, f"{why}; {repo_id} will be created private on the first checkpoint"
        raise
    if not private:
        return False, f"{repo_id} is public; labeling checkpoints go only to a private repo"
    return True, f"{why}; {repo_id} is private"
