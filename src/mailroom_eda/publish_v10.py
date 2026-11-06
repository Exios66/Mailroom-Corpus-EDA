"""Guarded publish of the v10 stage to the Hub (G4). A dry run unless every guard passes.

    python scripts/publish/publish_v10.py --stage data/v10/stage \
        --mailroom ../llm-mailroom --sandbox ../local-mailroom-sandbox          # dry run
    python scripts/publish/publish_v10.py ... --tag v10 --initials XX --confirm  # real

**Guards**, all checked before anything is sent:

1. conformance gates 1-13 pass on ``--stage`` (``v10_conformance.run``);
2. the lockstep table agrees for the release being cut (``check_lockstep.table`` with
   ``--rows``, default 3292). This repository's ``config.REPO_TAG`` is the one pin bumped
   *by* the publish, so the dataset-tag row needs every other repository at ``--tag``;
3. the git tree is clean (``git status --porcelain`` is empty);
4. ``--tag`` matches ``v10`` or ``v10.N``, ``--initials`` are 2-6 capital letters (a
   human's, never invented), and ``--confirm`` is given;
5. with ``--confirm`` only (the dry run makes no network call): the tag does not exist on
   the Hub yet. **Existing tags are never moved.**

Then it uploads exactly the files in the stage's ``manifest.json`` (plus the manifest)
in one commit, replacing the files under each v10 config folder, creates the tag at that
commit, rewrites ``config.REPO_TAG``, and prints the follow-up pin bumps for
llm-mailroom and the sandbox (``docs/plans/v10-release-train.md``).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from . import config

ROOT = Path(__file__).resolve().parents[2]
TAG_RE = re.compile(r"v10(\.\d+)?")
INITIALS_RE = re.compile(r"[A-Z]{2,6}")
DEFAULT_ROWS = 3292


def _lockstep_module():
    """``labeling/integrations/check_lockstep.py`` (a standalone script, not a package module)."""
    path = Path(__file__).parent / "labeling/integrations/check_lockstep.py"
    spec = importlib.util.spec_from_file_location("check_lockstep", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def lockstep_check(eda: Path, mailroom: Path, sandbox: Path, dojo: Path | None, rows: int,
                   tag: str) -> tuple[bool, str]:
    """Every lockstep row agrees; the dataset-tag row with this repository's tag taken as ``tag``."""
    cl = _lockstep_module()
    result = cl.table(eda, mailroom, sandbox, dojo, rows)
    bad = []
    for check, values, ok in result:
        if check == "Dataset tag":
            others = {k: v for k, v in values.items() if not k.startswith("EDA")}
            ok = all(v == tag for v in others.values())
            if not ok:
                bad.append(f"{check}: " + ", ".join(f"{k}={v}" for k, v in others.items() if v != tag))
        elif not ok:
            bad.append(f"{check}: " + ", ".join(f"{k}={v}" for k, v in values.items()))
    return (not bad), ("; ".join(bad) if bad else f"every pin agrees for {tag} ({rows} rows)")


def clean_tree(root: Path, *, run: Callable[..., Any] = subprocess.run) -> tuple[bool, str]:
    """``git status --porcelain`` is empty."""
    try:
        res = run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, check=False)
    except OSError as e:
        return False, f"git: {e}"
    if res.returncode != 0:
        return False, f"git status failed: {(res.stderr or '').strip()[:200]}"
    dirty = [line for line in res.stdout.splitlines() if line.strip()]
    return (not dirty), (f"{len(dirty)} uncommitted paths, e.g. {dirty[0].strip()}" if dirty else "clean")


def conformance_check(stage: Path, source: Path, labels_root: Path | None, campaign: str) -> tuple[bool, str]:
    """All 13 gates pass."""
    from . import v10_conformance

    res = v10_conformance.run(stage, source, None, labels_root=labels_root, campaign=campaign)
    failed = [f"{g} {v['name']}" for g, v in res["gates"].items() if not v["passed"]]
    return (not failed), ("gates 1-13 pass" if not failed else "failed gates: " + ", ".join(failed))


def upload_plan(stage: Path) -> dict[str, Any]:
    """What would be sent: the manifest's files, its configs and their total size."""
    manifest_path = Path(stage) / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"{manifest_path}: missing; build the stage first (python -m mailroom_eda.v10_build)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = sorted(manifest.get("files") or {}) + ["manifest.json"]
    missing = [f for f in files if not (Path(stage) / f).is_file()]
    if missing:
        raise SystemExit(f"stage files listed in the manifest are missing: {missing[:5]}")
    return {"files": files, "configs": list(manifest.get("configs") or []),
            "bytes": sum((Path(stage) / f).stat().st_size for f in files), "rows": manifest.get("rows") or {}}


def hub_tags(api: Any, repo: str) -> set[str]:
    """Tag names that already exist on the Hub repo."""
    refs = api.list_repo_refs(repo, repo_type="dataset")
    return {t.name for t in refs.tags}


def delete_patterns(files: list[str], configs: list[str]) -> list[str]:
    """``<dir>/**`` for the directory each config's files live in (``parquet/<config>`` on a real stage)."""
    out = set()
    for f in files:
        parts = f.split("/")[:-1]
        for c in configs:
            if c in parts:
                out.add("/".join(parts[:parts.index(c) + 1]) + "/**")
    return sorted(out)


def bump_repo_tag(tag: str, path: Path | None = None) -> bool:
    """Rewrite ``REPO_TAG = "..."`` in ``config.py``; True when it changed."""
    path = path or Path(__file__).with_name("config.py")
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(r'^REPO_TAG\s*=\s*"[^"]*"', f'REPO_TAG = "{tag}"', text, count=1, flags=re.M)
    if n != 1:
        raise SystemExit(f"{path}: no REPO_TAG line to bump")
    if new != text:
        path.write_text(new, encoding="utf-8")
    return new != text


def follow_ups(tag: str, rows: int) -> list[str]:
    """The pin bumps the other repositories need once the tag exists."""
    return [
        f"this repo: commit config.REPO_TAG = \"{tag}\" (rewritten just now)",
        f"llm-mailroom: src/pipeline/hf_corpora.py FULL_CORPUS_REVISION = \"{tag}\", n_docs {rows}",
        f"local-mailroom-sandbox: src/mailroom_sandbox/job/spec.py FAMILY_HF_TAG = \"{tag}\", "
        f"FAMILY_CORPUS_SIZE = {rows}",
        "then re-run check_lockstep.py with --rows; see docs/plans/v10-release-train.md",
    ]


def publish(args: argparse.Namespace, *, api: Any = None, run: Callable[..., Any] = subprocess.run,
            checks: dict[str, Callable[[], tuple[bool, str]]] | None = None) -> int:
    """Check every guard; with ``--confirm`` and all guards passing, upload and tag."""
    plan = upload_plan(args.stage)
    checks = checks or {
        "conformance": lambda: conformance_check(args.stage, args.source, args.labels_root, args.campaign),
        "lockstep": lambda: lockstep_check(args.eda, args.mailroom, args.sandbox, args.dojo, args.rows,
                                           args.tag or "v10"),
        "clean tree": lambda: clean_tree(args.eda, run=run),
    }
    results = {name: fn() for name, fn in checks.items()}
    results["tag"] = (bool(args.tag and TAG_RE.fullmatch(args.tag)), f"--tag {args.tag or '(missing)'}")
    results["initials"] = (bool(args.initials and INITIALS_RE.fullmatch(args.initials)),
                           f"--initials {args.initials or '(missing)'}")
    print(f"would upload {len(plan['files'])} files ({plan['bytes'] / 1e6:.1f} MB), configs "
          f"{', '.join(plan['configs'])} to {args.repo}" + (f" and tag {args.tag}" if args.tag else ""))
    for name, (ok, detail) in results.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if not all(ok for ok, _ in results.values()):
        print("refusing to publish: a guard failed", file=sys.stderr)
        return 1
    if not args.confirm:
        print("dry run: every guard passes; add --confirm to publish")
        return 0
    if api is None:
        from .hf_interface import get_hf_api

        api = get_hf_api()
    if args.tag in hub_tags(api, args.repo):
        print(f"refusing to publish: tag {args.tag} already exists on {args.repo}; tags are never moved",
              file=sys.stderr)
        return 1
    commit = api.upload_folder(repo_id=args.repo, repo_type="dataset", folder_path=str(args.stage),
                               allow_patterns=plan["files"],
                               delete_patterns=delete_patterns(plan["files"], plan["configs"]),
                               commit_message=f"v10 release {args.tag} ({args.initials})")
    revision = getattr(commit, "oid", None)
    if not revision:
        print("upload returned no commit id; not tagging. Check the repo before retrying.", file=sys.stderr)
        return 1
    api.create_tag(args.repo, tag=args.tag, revision=revision, repo_type="dataset", exist_ok=False,
                   tag_message=f"v10 release, published by {args.initials}")
    print(f"published {args.repo}@{args.tag} ({revision})")
    bump_repo_tag(args.tag, Path(args.eda) / "src" / "mailroom_eda" / "config.py")
    for line in follow_ups(args.tag, args.rows):
        print(f"next: {line}")
    return 0


def parser() -> argparse.ArgumentParser:
    """CLI arguments."""
    from .labeling import runner

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stage", type=Path, default=Path("data/v10/stage"))
    ap.add_argument("--source", type=Path, default=runner.DEFAULT_STAGE, help="the v9.4 stage it was built from")
    ap.add_argument("--labels-root", type=Path, default=runner.DEFAULT_OUT)
    ap.add_argument("--campaign", default="v10")
    ap.add_argument("--repo", default=config.REPO_ID)
    ap.add_argument("--eda", type=Path, default=ROOT)
    ap.add_argument("--mailroom", type=Path, required=True, help="llm-mailroom checkout")
    ap.add_argument("--sandbox", type=Path, required=True, help="local-mailroom-sandbox checkout")
    ap.add_argument("--dojo", type=Path, help="llm-dojo-scoring checkout (optional)")
    ap.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    ap.add_argument("--tag", help="the new tag, v10 or v10.N")
    ap.add_argument("--initials", help="the publishing human's initials (2-6 capital letters)")
    ap.add_argument("--confirm", action="store_true", help="actually publish (otherwise a dry run)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    return publish(parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
