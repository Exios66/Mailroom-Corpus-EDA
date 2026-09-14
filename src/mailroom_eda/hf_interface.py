"""Centralized HuggingFace Hub interface for the mailroom-dataset corpus (v9)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download

from .config import REPO_ID

HF_USERNAME = os.environ.get("HF_USERNAME", "Lucius-Morningstar")


def sha256_file(path: Path) -> str:
    """Compute SHA256 of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_hf_api(token: str | None = None) -> HfApi:
    """Get authenticated HF API client."""
    return HfApi(token=token or os.environ.get("HF_TOKEN"))


def create_dataset_repo(api: HfApi, repo_id: str, private: bool = False) -> dict:
    """Create dataset repository on HF Hub."""
    api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True)
    return {"repo_id": repo_id, "url": f"https://huggingface.co/datasets/{repo_id}"}


def upload_folder(
    api: HfApi,
    folder_path: Path | str,
    repo_id: str,
    commit_message: str,
    path_in_repo: str | None = None,
    allow_patterns: list[str] | None = None,
) -> dict:
    """Upload a local folder to HF dataset repo."""
    api.upload_folder(
        folder_path=str(folder_path),
        repo_id=repo_id,
        repo_type="dataset",
        path_in_repo=path_in_repo,
        commit_message=commit_message,
        allow_patterns=allow_patterns,
    )
    return {"status": "uploaded", "repo": f"https://huggingface.co/datasets/{repo_id}"}


def verify_hub_sha256(api: HfApi, repo_id: str, filename: str, local_sha: str) -> dict:
    """Verify Hub LFS SHA256 matches local SHA256.

    hub#57: verification is a real boolean everywhere — the old code set
    ``verified = \"(sha not exposed)\"`` (a truthy STRING) for non-LFS/small
    files, so publish reported \"successful\" verification for an unverified
    file while ``verify_hf.py`` treated the same value as failure. Small
    files are now ``verified=False`` with an explicit ``status``.
    """
    info = api.list_repo_tree(repo_id=repo_id, repo_type="dataset", recursive=True)
    hub_files = {f.path: getattr(f, "lfs", None) for f in info}
    jsonl_hub = hub_files.get(filename)
    hub_sha = jsonl_hub.sha256 if jsonl_hub is not None else None
    if isinstance(hub_sha, str) and len(hub_sha) == 64:
        verified = hub_sha == local_sha
        status = "verified" if verified else "mismatch"
    else:
        verified = False
        status = "sha-not-exposed"
    return {
        "filename": filename,
        "local_sha256": local_sha[:12],
        "hub_sha256": str(hub_sha)[:12] if hub_sha else None,
        "verified": bool(verified),
        "status": status,
    }


def list_repo_files(api: HfApi, repo_id: str) -> list[str]:
    """List all files in a dataset repo."""
    info = api.list_repo_tree(repo_id=repo_id, repo_type="dataset", recursive=True)
    return [f.path for f in info]


def download_file(api: HfApi, repo_id: str, filename: str, local_path: Path) -> Path:
    """Download a file from HF dataset repo."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    return Path(hf_hub_download(repo_id=repo_id, filename=filename, repo_type="dataset", local_dir=local_path.parent))