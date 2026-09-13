"""Centralized HuggingFace Hub interface for docclass corpus."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi, hf_hub_download

from .config import DATA_DIR, REPO_ID, REPO_URL

HF_USERNAME = os.environ.get("HF_USERNAME", "Lucius-Morningstar")


def sha256_file(path: Path) -> str:
    """Compute SHA256 of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_local_sha(jsonl_path: Path, manifest_sha: str) -> bool:
    """Verify local JSONL SHA256 matches manifest."""
    actual = sha256_file(jsonl_path)
    return actual == manifest_sha


def get_hf_api(token: str | None = None) -> HfApi:
    """Get authenticated HF API client."""
    return HfApi(token=token or os.environ.get("HF_TOKEN"))


def create_dataset_repo(api: HfApi, repo_id: str, private: bool = False) -> dict:
    """Create dataset repository on HF Hub."""
    api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True)
    return {"repo_id": repo_id, "url": f"https://huggingface.co/datasets/{repo_id}"}


def upload_folder(
    api: HfApi,
    folder_path: Path,
    repo_id: str,
    commit_message: str,
    path_in_repo: str | None = None,
) -> dict:
    """Upload a local folder to HF dataset repo."""
    api.upload_folder(
        folder_path=str(folder_path),
        repo_id=repo_id,
        repo_type="dataset",
        path_in_repo=path_in_repo,
        commit_message=commit_message,
    )
    return {"status": "uploaded", "repo": f"https://huggingface.co/datasets/{repo_id}"}


def upload_jsonl(
    api: HfApi,
    jsonl_path: Path,
    repo_id: str,
    commit_message: str,
) -> dict:
    """Upload a JSONL file to HF dataset repo."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / jsonl_path.name).write_text(jsonl_path.read_text(encoding="utf-8"), encoding="utf-8")
        api.upload_folder(
            folder_path=str(tmpdir),
            repo_id=repo_id,
            repo_type="dataset",
            commit_message=commit_message,
        )
    return {"status": "uploaded", "repo": f"https://huggingface.co/datasets/{repo_id}"}


def verify_hub_sha256(api: HfApi, repo_id: str, filename: str, local_sha: str) -> dict:
    """Verify Hub LFS SHA256 matches local SHA256."""
    info = api.list_repo_tree(repo_id=repo_id, repo_type="dataset", recursive=True)
    hub_files = {f.path: getattr(f, "lfs", None) for f in info}
    jsonl_hub = hub_files.get(filename)
    hub_sha = jsonl_hub.sha256 if jsonl_hub is not None else "(non-LFS/small file)"
    verified = (hub_sha == local_sha) if isinstance(hub_sha, str) and len(hub_sha) == 64 else "(sha not exposed)"
    return {
        "filename": filename,
        "local_sha256": local_sha[:12],
        "hub_sha256": str(hub_sha)[:12],
        "verified": verified,
    }


def publish_dataset(
    api: HfApi,
    jsonl_path: Path,
    manifest_path: Path,
    repo_id: str,
    commit_message: str,
) -> dict:
    """Publish dataset with JSONL, manifest, and README card."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    local_sha = manifest["sha256"]
    actual_sha = sha256_file(jsonl_path)
    if actual_sha != local_sha:
        return {"disposition": "ABORT_local_sha_mismatch", "manifest": local_sha[:12], "actual": actual_sha[:12]}

    rows = manifest["rows"]
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / jsonl_path.name).write_text(jsonl_path.read_text(encoding="utf-8"), encoding="utf-8")
        (tmpdir / "manifest.json").write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
        api.upload_folder(
            folder_path=str(tmpdir),
            repo_id=repo_id,
            repo_type="dataset",
            commit_message=commit_message,
        )

    verify = verify_hub_sha256(api, repo_id, jsonl_path.name, local_sha)
    return {
        "disposition": "published",
        "repo": f"https://huggingface.co/datasets/{repo_id}",
        "rows": rows,
        "sha256": local_sha[:12],
        "hub_sha256": str(verify["hub_sha256"])[:12],
        "verified": verify["verified"],
    }


def list_repo_files(api: HfApi, repo_id: str) -> list[str]:
    """List all files in a dataset repo."""
    info = api.list_repo_tree(repo_id=repo_id, repo_type="dataset", recursive=True)
    return [f.path for f in info]


def download_file(api: HfApi, repo_id: str, filename: str, local_path: Path) -> Path:
    """Download a file from HF dataset repo."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    return Path(hf_hub_download(repo_id=repo_id, filename=filename, repo_type="dataset", local_dir=local_path.parent))