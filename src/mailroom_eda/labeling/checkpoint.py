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
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .. import hf_interface

STAGING_REPO = "Lucius-Morningstar/mailroom-dataset-v10-staging"
LABELS_DIR = "labels"
APPROVALS_FILE = "campaign/approved.yaml"
SUFFIXES = {".jsonl", ".json", ".csv", ".md", ".yaml", ".txt", ".xlsx", ".html"}
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
`mailroom_eda.labeling.checkpoint` after each run, review save, gate approval and ping
(auto-checkpoint), plus explicit reviewer checkpoints from the review portal
(`--hf-checkpoint` / Checkpoint button). **Not a release.**

## What lives here

- `labels/<run>/**` — the labels root staged: chunk/run journals (calls, runs, gates),
  `*-review-data.json` review packets, interim overlays, decisions and receipts, and the
  run directories including verification re-runs (e.g. the 2026-11-08 `3.3r` kappa
  verification whose result discarded an unverified prompt change).
- `campaign/approved.yaml` — gate approvals signed off by reviewer initials.

## Reviewer ethics & requirements

- Provenance is human: provenance columns, initial and Save remain human acts, agents
  accelerate (per-label picker, bulk "Accept verified in batch", next-pending) but never
  forge initials; every staged decision carries its reviewer's initials.
- Agent-access: `GET /api/contract` on the review portal; nothing publishes automatically.
- Restore is the mirrored flow: `labeling review-portal --restore-revision <sha>` or
  `labeling doctor` for readiness. Every file is uploaded and sha-verified, commits are
  immutable; a lost checkout re-reads staged answers instead of re-paying them.
- Method provenance: `labeling/` runbooks at
  [Exios66/Mailroom-Corpus-EDA](https://github.com/Exios66/Mailroom-Corpus-EDA)
  (`docs/labeler-review-viewer.md`), loop ledgers mirrored at
  [Exios66/labels-staging](https://github.com/Exios66/labels-staging).

## Not a release

Ground-truth side only; nothing here belongs in the blind `default` config; journals stay
byte-for-byte, spend included: a private backup of run logs, never published. The card
declares the labels config only, so the Hub never auto-builds a blind config from them.
"""


def _sources(labels_root: Path, approvals: Path | None) -> Iterable[tuple[str, Path]]:
    """Yield repository paths and local files with supported checkpoint suffixes.

    Include an existing approvals file separately. Raise SystemExit for symlinks
    in the scanned paths or their parents inside the checkpoint tree."""
    _reject_symlinks(labels_root)
    for p in sorted(labels_root.rglob("*")):
        _reject_symlinks(p, labels_root)
        if p.is_file() and p.suffix in SUFFIXES and "__pycache__" not in p.parts:
            yield f"{LABELS_DIR}/{p.relative_to(labels_root).as_posix()}", p
    if approvals is not None and approvals.exists():
        _reject_symlinks(approvals)
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


def _reject_symlinks(path: Path, root: Path | None = None) -> None:
    """Raise SystemExit if ``path``, or any component between it and ``root``, is a symlink.

    Only the checkpoint tree is the trust boundary: ancestors above ``root`` (a symlinked
    home, data mount or macOS ``/tmp``) are not checked. Without ``root`` only ``path``
    itself is checked."""
    chain = [path]
    if root is not None:
        chain += [p for p in path.parents if p == root or root in p.parents]
    if any(p.is_symlink() for p in chain):
        raise SystemExit(f"checkpoint refuses symlink: {path}")


def _matches(file: Any, data: bytes) -> bool:
    """Compare bytes with Hub metadata using LFS SHA-256 or the Git blob ID."""
    lfs = getattr(file, "lfs", None)
    sha = lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None)
    return (sha == hashlib.sha256(data).hexdigest() if lfs is not None
            else getattr(file, "blob_id", None) == git_blob_sha(data))


def _snapshot(api: Any, repo_id: str, revision: str | None = None) -> str:
    """Resolve a private checkpoint ref to a full commit SHA.

    None selects the default revision. Raise SystemExit for the published
    repository, a public repository, or an invalid SHA; Hub errors propagate."""
    if repo_id == hf_interface.REPO_ID:
        raise SystemExit("checkpoints never use the published dataset repo")
    info = api.repo_info(repo_id=repo_id, repo_type="dataset", **({"revision": revision} if revision else {}))
    if not info.private:
        raise SystemExit(f"{repo_id} is public; labeling checkpoints go only to a private repo")
    sha = getattr(info, "sha", None)
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit("checkpoint could not resolve an immutable revision")
    return sha


def verify(api: Any, repo_id: str, staged: dict[str, Path], *, revision: str | None = None) -> list[str]:
    """Return sorted staged paths whose Hub copy is missing or differs.

    Resolve ``revision`` once, then compare LFS SHA-256 or Git blob SHA-1.
    SystemExit from private-snapshot checks and Hub or local read errors propagate."""
    revision = _snapshot(api, repo_id, revision)
    hub = {f.path: f for f in api.list_repo_tree(repo_id=repo_id, repo_type="dataset", recursive=True,
                                               revision=revision)}
    bad = []
    for rel, path in sorted(staged.items()):
        f = hub.get(rel)
        data = path.read_bytes()
        ok = f is not None and _matches(f, data)
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
    """Upload the labels root and optional approvals to a private checkpoint.

    Return the repository, file count, verified flag, and uploaded commit SHA.
    Raise SystemExit for unsafe paths, a forbidden repository, an unavailable
    commit SHA, or mismatched uploaded bytes. Hub and filesystem errors propagate."""
    if repo_id == hf_interface.REPO_ID:
        raise SystemExit("checkpoints never go to the published dataset repo")
    api = api or hf_interface.get_hf_api()
    ensure_private(api, repo_id)
    with tempfile.TemporaryDirectory() as tmp:
        staged = stage(Path(labels_root), approvals, Path(tmp))
        result = hf_interface.upload_folder(api, tmp, repo_id, message)
        revision = result.get("revision") if isinstance(result, dict) else None
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise SystemExit("checkpoint upload returned no immutable commit revision")
        revision = _snapshot(api, repo_id, revision)
        bad = verify(api, repo_id, staged, revision=revision)
    if bad:
        raise SystemExit(f"checkpoint upload did not verify: {', '.join(bad[:10])}")
    return {"repo": repo_id, "files": len(staged), "verified": True, "revision": revision}


def restore(labels_root: Path, *, api: Any = None, repo_id: str = STAGING_REPO,
            revision: str | None = None) -> list[Path]:
    """Restore missing label files from one private Hub snapshot; return written paths.

    ``revision`` selects a ref, or the default when None. Existing identical files
    are skipped. Raise SystemExit for conflicting files, unsafe paths, symlinks,
    or failed snapshot or hash checks. Download and verify all missing files before
    writing any targets; filesystem and Hub errors propagate. Write failures can
    leave a partial restore. Campaign approvals outside labels/ are not restored."""
    api = api or hf_interface.get_hf_api()
    revision = _snapshot(api, repo_id, revision)
    labels_root = Path(labels_root)
    _reject_symlinks(labels_root)
    prefix = f"{LABELS_DIR}/"
    wrote = []
    missing = []
    for f in api.list_repo_tree(repo_id=repo_id, repo_type="dataset", recursive=True, revision=revision):
        path = getattr(f, "path", "")
        if not path.startswith(prefix) or not hasattr(f, "blob_id"):
            continue
        rel = path[len(prefix):]
        if any(part in ("", ".", "..") for part in rel.split("/")) or "\\" in rel:
            raise SystemExit(f"unsafe checkpoint path: {path}")
        if str(getattr(f, "mode", "")) == "120000" or getattr(f, "type", "") == "symlink":
            raise SystemExit(f"checkpoint refuses symlink: {path}")
        target = labels_root / rel
        _reject_symlinks(target, labels_root)
        if target.exists():
            if not target.is_file() or not _matches(f, target.read_bytes()):
                raise SystemExit(f"checkpoint restore conflict: {target}")
            continue
        missing.append((f, target))
    with tempfile.TemporaryDirectory() as tmp:
        downloads = []
        for f, target in missing:
            local = hf_interface.download_file(api, repo_id, f.path, Path(tmp).resolve() / f.path,
                                               revision=revision)
            _reject_symlinks(local, Path(tmp).resolve())
            if not _matches(f, local.read_bytes()):
                raise SystemExit(f"checkpoint download did not verify: {f.path}")
            downloads.append((local, target))
        for local, target in downloads:
            _reject_symlinks(target, labels_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(local.read_bytes())
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
