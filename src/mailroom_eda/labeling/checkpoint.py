"""Checkpoint labeling output to the private Hugging Face staging copy.

Every paid answer is credit already spent, so each ``chunk``/``run``, ``review`` and
``gate --approve`` uploads the labels root (``docs/reports/audits/v10_labels``) plus the
campaign's ``approved.yaml`` to ``STAGING_REPO``, a **private** dataset repo, and
sha-verifies every file it sent. ``restore`` pulls missing files back, so a lost
checkout re-reads the staged answers (``runner.import_reusable``) instead of buying
them again.

What it never does:

- publish: it refuses a staging repo that is public, and never touches ``REPO_ID``;
- ship spend: keys and CSV columns naming cost, USD, spend or price are dropped;
- build a blind config: the card declares one ``labels`` config (GT side only), so the
  Hub never auto-builds a ``default`` config from label files.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .. import hf_interface

STAGING_REPO = "Lucius-Morningstar/mailroom-dataset-v10-staging"
LABELS_DIR = "labels"
APPROVALS_FILE = "campaign/approved.yaml"
SUFFIXES = {".jsonl", ".json", ".csv", ".md", ".yaml"}
#: spend stays in the local logs (user directive): never staged. Matches snake_case spend
#: keys (``cost_usd``, ``api_cost_cap_usd``, ``spent_usd``), not label names such as the
#: CUAD category ``Price Restrictions``.
SPEND_KEY = re.compile(r"(^|_)(cost|usd|spend|spent|price|headroom)(_|$)")
#: lines of a markdown or yaml note that state spend
SPEND_LINE = re.compile(r"\$\d|\bUSD\b|(^|_)(cost|spent|headroom)(_|$|\b)", re.I)

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
Ground-truth side only: nothing here belongs in the blind `default` config. Spend fields are
stripped; costs live only in the local run logs.
"""


def scrub(obj: Any) -> Any:
    """Drop spend keys at any depth."""
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if not SPEND_KEY.search(str(k))}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


def scrub_text(name: str, text: str) -> str:
    """A spend-free copy of one output file's text."""
    suffix = Path(name).suffix
    if suffix == ".jsonl":
        return "".join(json.dumps(scrub(json.loads(line)), sort_keys=True, ensure_ascii=False) + "\n"
                       for line in text.splitlines() if line.strip())
    if suffix == ".json":
        return json.dumps(scrub(json.loads(text)), indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if suffix == ".csv":
        rows = list(csv.DictReader(io.StringIO(text)))
        cols = [c for c in (csv.DictReader(io.StringIO(text)).fieldnames or []) if not SPEND_KEY.search(c)]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", quoting=csv.QUOTE_ALL, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        return buf.getvalue()
    return "\n".join(line for line in text.splitlines() if not SPEND_LINE.search(line)) + "\n"


def _sources(labels_root: Path, approvals: Path | None) -> Iterable[tuple[str, Path]]:
    """``(path_in_repo, local file)`` for everything a checkpoint carries."""
    for p in sorted(labels_root.rglob("*")):
        if p.is_file() and p.suffix in SUFFIXES and "__pycache__" not in p.parts:
            yield f"{LABELS_DIR}/{p.relative_to(labels_root).as_posix()}", p
    if approvals is not None and approvals.exists():
        yield APPROVALS_FILE, approvals


def stage(labels_root: Path, approvals: Path | None, dest: Path) -> dict[str, Path]:
    """Write the scrubbed checkpoint tree under ``dest``; returns ``{path_in_repo: file}``."""
    out: dict[str, Path] = {}
    for rel, src in _sources(labels_root, approvals):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(scrub_text(rel, src.read_text(encoding="utf-8")), encoding="utf-8")
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
    """Upload the scrubbed labels root to the private staging repo and verify every file."""
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
    """Download staged label files missing locally (never overwrites); returns what it wrote.

    Restored journals carry no spend, so reusing them records ``reused_cost_usd: null``."""
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
