"""v9.3 amendment: merger agreements route to ``merger_agreement_specialist``.

v9.3 shipped the 152 merger agreements (and the bundle, stream and fixture
rows built on them) with ``expected_specialist = contracts_specialist``, the
pre-2026-10-17 routing. ``llm-mailroom`` routes the class to its own
``merger_agreement_specialist`` (taxonomy.yaml; ``eval_contract`` aligned
2026-10-17). This amendment corrects that one value, inside v9.3: the tag is
re-pointed to the amended commit, so a ``revision="v9.3"`` pin gets it.

Gates, all STOP-not-force:

* only ``expected_specialist`` changes, and only on ``merger_agreement`` rows
  that read ``contracts_specialist``; every other cell (parquet) and every
  other line byte (JSONL) is unchanged;
* afterwards every row's ``expected_specialist`` equals the
  ``eval_contract`` registry value for its class;
* the per-file change counts equal ``EXPECTED_CHANGES``;
* the manifest sha table recomputes from the staged bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR, REPO_ID
from .eval_contract import SPECIALIST_BY_CLASS, expected_specialist

OLD = "contracts_specialist"
NEW = "merger_agreement_specialist"
CLASS = "merger_agreement"

#: The v9.3 commit this amends (the rewritten card + CHANGELOG on data 6889230c).
BASE_REVISION = "1f50293a0141e4d1f4fe771405d01897783fc6a8"
TAG = "v9.3"

#: Rows that change, per file.
EXPECTED_CHANGES = {
    "ground_truth_hardened.jsonl": 152,
    "bundles.jsonl": 8,
    "streams.jsonl": 8,
    "fixtures.jsonl": 4,
    "parquet/ground_truth/train/train-00000-of-00001.parquet": 135,
    "parquet/ground_truth/test/test-00000-of-00001.parquet": 17,
    "parquet/bundles/train/train-00000-of-00001.parquet": 7,
    "parquet/bundles/test/test-00000-of-00001.parquet": 1,
    "parquet/streams/train/train-00000-of-00001.parquet": 7,
    "parquet/streams/test/test-00000-of-00001.parquet": 1,
    "parquet/fixtures/train/train-00000-of-00001.parquet": 3,
    "parquet/fixtures/test/test-00000-of-00001.parquet": 1,
}

#: Card and changelog sources in this repo (published as README.md / CHANGELOG.md).
CARD_SRC = Path(__file__).resolve().parents[2] / "docs" / "dataset-cards" / "mailroom-dataset.md"
CHANGELOG_SRC = CARD_SRC.with_name("mailroom-dataset-CHANGELOG.md")

AMENDMENT_LINE = (
    "amendment  : 2026-11-06 merger routing: the 152 merger_agreement rows (and their "
    "bundle, stream and fixture rows) route to merger_agreement_specialist, not "
    "contracts_specialist; no other value changed"
)

BASE_DIR_DEFAULT = DATA_DIR / "v9_3_merger_base"
STAGE_DIR_DEFAULT = DATA_DIR / "v9_3_merger_stage"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_registry() -> None:
    """The class map this amendment publishes must be the live routing."""
    if SPECIALIST_BY_CLASS.get(CLASS) != NEW:
        raise AssertionError(f"eval_contract routes {CLASS} to {SPECIALIST_BY_CLASS.get(CLASS)!r}")
    if expected_specialist({"expected": CLASS}) != NEW:
        raise AssertionError("taxonomy.yaml registry does not route merger_agreement to "
                             f"{NEW}; check the llm-mailroom sibling pin")


def reroute_jsonl_line(line: str) -> tuple[str, bool]:
    """Return (line, changed). Edits the one value in place, so every other
    byte of the line is kept; the parsed result must differ only there."""
    row = json.loads(line)
    if row.get("expected") != CLASS or row.get("expected_specialist") != OLD:
        return line, False
    needle = f'"expected_specialist": "{OLD}"'
    if line.count(needle) != 1:
        raise AssertionError(f"expected_specialist not found exactly once in {row.get('filename')}")
    new_line = line.replace(needle, f'"expected_specialist": "{NEW}"')
    if json.loads(new_line) != {**row, "expected_specialist": NEW}:
        raise AssertionError(f"in-place edit changed more than expected_specialist: {row.get('filename')}")
    return new_line, True


def reroute_jsonl(src: Path, dst: Path) -> int:
    changed = 0
    with open(src, encoding="utf-8", newline="") as f:
        lines = f.read().split("\n")
    out = []
    for line in lines:
        if not line:
            out.append(line)
            continue
        new, did = reroute_jsonl_line(line)
        out.append(new)
        changed += did
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(out))
    return changed


def reroute_parquet(src: Path, dst: Path) -> int:
    """Rewrite one column; schema, row order and every other column are kept."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    t = pq.read_table(src)
    exp = t.column("expected").to_pylist()
    spec = t.column("expected_specialist").to_pylist()
    new_spec = [NEW if (e == CLASS and s == OLD) else s for e, s in zip(exp, spec)]
    changed = sum(a != b for a, b in zip(spec, new_spec))
    i = t.schema.get_field_index("expected_specialist")
    out = t.set_column(i, t.schema.field(i), pa.array(new_spec, type=t.schema.field(i).type))
    dst.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(out, dst)

    back = pq.read_table(dst)
    if back.schema != t.schema:
        raise AssertionError(f"{src.name}: schema changed")
    for name in t.column_names:
        if name != "expected_specialist" and not back.column(name).equals(t.column(name)):
            raise AssertionError(f"{src.name}: column {name} changed")
    return changed


def verify_routing(stage_dir: Path) -> dict[str, int]:
    """Every row with a class carries that class's registry specialist."""
    import pyarrow.parquet as pq

    seen: dict[str, int] = {}
    for rel in EXPECTED_CHANGES:
        p = stage_dir / rel
        if p.suffix == ".parquet":
            t = pq.read_table(p, columns=["expected", "expected_specialist"])
            rows = t.to_pylist()
        else:
            # split on "\n" only: str.splitlines() also breaks on U+2028/U+2029/NEL
            rows = [json.loads(l) for l in p.read_text(encoding="utf-8").split("\n") if l]
        for r in rows:
            want = expected_specialist(r)
            if want and r.get("expected_specialist") != want:
                raise AssertionError(f"{rel}: {r.get('expected')} routes to "
                                     f"{r.get('expected_specialist')!r}, registry says {want!r}")
            if r.get("expected") == CLASS:
                seen[rel] = seen.get(rel, 0) + 1
    return seen


def render_manifest(old: str, stage_dir: Path) -> str:
    lines = old.splitlines()
    sha_idx = next(i for i, ln in enumerate(lines) if ln.strip().rstrip(":").strip() == "sha256")
    header = [ln for ln in lines[:sha_idx] if not ln.startswith("amendment  :")]
    i = next(i for i, ln in enumerate(header) if ln.startswith("labels_version"))
    header = header[: i + 1] + [AMENDMENT_LINE] + header[i + 1 :]
    shas = {
        str(p.relative_to(stage_dir)): sha256_file(p)
        for p in sorted(stage_dir.rglob("*"))
        if p.is_file() and p.name != "manifest.txt"
    }
    old_rows = [m.group(1) for ln in lines[sha_idx + 1 :]
                if (m := re.match(r"^\s*(\S+)\s+[0-9a-f]{64}\s*$", ln))]
    if sorted(old_rows) != sorted(shas):
        raise AssertionError(f"manifest file set changed: {sorted(set(old_rows) ^ set(shas))}")
    return "\n".join(header + ["sha256     :", *[f"  {k}  {v}" for k, v in sorted(shas.items())], ""])


def verify_manifest(stage_dir: Path) -> int:
    table = re.findall(r"^\s*(\S+)\s+([0-9a-f]{64})\s*$",
                       (stage_dir / "manifest.txt").read_text(encoding="utf-8"), flags=re.M)
    for rel, sha in table:
        if sha256_file(stage_dir / rel) != sha:
            raise AssertionError(f"manifest sha mismatch: {rel}")
    return len(table)


def check_manifest_covers(stage_dir: Path, only: tuple[str, ...] | None = None) -> None:
    """Before an upload: the manifest recomputes from the staged bytes and lists
    every staged file (or every file in ``only`` except the manifest itself)."""
    verify_manifest(stage_dir)
    listed = set(re.findall(r"^\s*(\S+)\s+[0-9a-f]{64}\s*$",
                            (stage_dir / "manifest.txt").read_text(encoding="utf-8"), flags=re.M))
    staged = {str(p.relative_to(stage_dir)) for p in stage_dir.rglob("*")
              if p.is_file() and p.name != "manifest.txt"}
    want = (set(only) - {"manifest.txt"}) if only is not None else staged
    if missing := sorted(want - listed):
        raise SystemExit(f"manifest does not list {missing}; re-render it before publishing")


def download_base(base_dir: Path) -> Path:
    from huggingface_hub import snapshot_download

    snapshot_download(REPO_ID, repo_type="dataset", revision=BASE_REVISION, local_dir=str(base_dir))
    return base_dir


def check_dirs(base_dir: Path, stage_dir: Path) -> None:
    """``build`` deletes ``stage_dir``: refuse any overlap with the base."""
    b, s = Path(base_dir).resolve(), Path(stage_dir).resolve()
    if b == s or b.is_relative_to(s) or s.is_relative_to(b):
        raise SystemExit(f"stage dir {s} overlaps base dir {b}; refusing to delete it")


def build(base_dir: Path, stage_dir: Path) -> dict:
    check_registry()
    check_dirs(base_dir, stage_dir)
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    shutil.copytree(base_dir, stage_dir, ignore=shutil.ignore_patterns(".cache", ".gitattributes"))

    changes = {}
    for rel in EXPECTED_CHANGES:
        src, dst = base_dir / rel, stage_dir / rel
        changes[rel] = (reroute_parquet if src.suffix == ".parquet" else reroute_jsonl)(src, dst)
    if changes != EXPECTED_CHANGES:
        raise AssertionError(f"change counts {changes} != expected {EXPECTED_CHANGES}")
    seen = verify_routing(stage_dir)
    if seen != EXPECTED_CHANGES:
        raise AssertionError(f"merger rows per file {seen} != {EXPECTED_CHANGES}")

    shutil.copyfile(CARD_SRC, stage_dir / "README.md")
    shutil.copyfile(CHANGELOG_SRC, stage_dir / "CHANGELOG.md")
    card = (stage_dir / "README.md").read_text(encoding="utf-8")
    if f"route to `{OLD}`" in card or NEW not in card:
        raise AssertionError("the card still describes the old merger routing")

    untouched = [p for p in base_dir.rglob("*") if p.is_file() and ".cache" not in p.parts
                 and str(p.relative_to(base_dir)) not in EXPECTED_CHANGES
                 and p.name not in ("README.md", "CHANGELOG.md", "manifest.txt", ".gitattributes")]
    for p in untouched:
        if sha256_file(p) != sha256_file(stage_dir / p.relative_to(base_dir)):
            raise AssertionError(f"untouched file changed: {p.relative_to(base_dir)}")

    (stage_dir / "manifest.txt").write_text(
        render_manifest((base_dir / "manifest.txt").read_text(encoding="utf-8"), stage_dir),
        encoding="utf-8")
    n = verify_manifest(stage_dir)
    return {"changes": changes, "untouched_identical": len(untouched), "manifest_entries": n}


DOC_FILES = ("README.md", "CHANGELOG.md", "manifest.txt")


def publish(stage_dir: Path, token: str, *, parent: str = BASE_REVISION,
            only: tuple[str, ...] | None = None,
            message: str = "v9.3 amendment: merger agreements route to merger_agreement_specialist") -> str:
    """One commit on top of ``parent`` (refused if main has moved), then
    re-point the v9.3 tag to it. Every uploaded file is sha-checked on the Hub.
    ``only`` limits the upload (a docs-only follow-up passes ``DOC_FILES``)."""
    from huggingface_hub import CommitOperationAdd, HfApi, hf_hub_download

    api = HfApi(token=token)
    check_manifest_covers(stage_dir, only)
    files = [p for p in sorted(stage_dir.rglob("*")) if p.is_file()
             and (only is None or str(p.relative_to(stage_dir)) in only)]
    if only is not None and len(files) != len(only):
        raise AssertionError(f"missing staged files: {only}")
    ci = api.create_commit(
        REPO_ID, repo_type="dataset", parent_commit=parent,
        operations=[CommitOperationAdd(str(p.relative_to(stage_dir)), str(p)) for p in files],
        commit_message=message,
    )
    for p in files:
        rel = str(p.relative_to(stage_dir))
        got = sha256_file(Path(hf_hub_download(REPO_ID, rel, repo_type="dataset", revision=ci.oid,
                                               token=token, force_download=True)))
        if got != sha256_file(p):
            raise AssertionError(f"Hub copy of {rel} differs; tag NOT moved")
    move_tag(api, ci.oid)
    return ci.oid


def _tag_target(api, tag: str) -> str | None:
    refs = api.list_repo_refs(REPO_ID, repo_type="dataset")
    if not any(t.name == tag for t in refs.tags):
        return None
    return api.dataset_info(REPO_ID, revision=tag).sha


def move_tag(api, new_sha: str, tag: str = TAG) -> None:
    """Re-point ``tag`` to ``new_sha``. The Hub cannot move a tag in place, so
    this deletes and re-creates it; on failure the old target is restored (if
    the tag is missing) and both SHAs are reported."""
    old_sha = _tag_target(api, tag)
    if old_sha == new_sha:
        return
    try:
        if old_sha is not None:
            api.delete_tag(REPO_ID, tag=tag, repo_type="dataset")
        api.create_tag(REPO_ID, tag=tag, revision=new_sha, repo_type="dataset",
                       tag_message="v9.3: corporate presence layer, merger routing amendment, card and CHANGELOG")
    except Exception as exc:
        now = _tag_target(api, tag)
        if now is None and old_sha is not None:
            api.create_tag(REPO_ID, tag=tag, revision=old_sha, repo_type="dataset")
            now = _tag_target(api, tag)
        raise SystemExit(f"moving {tag} failed ({exc}); old target {old_sha}, new target {new_sha}, "
                         f"tag now at {now}") from exc
    if _tag_target(api, tag) != new_sha:
        raise SystemExit(f"{tag} is not at {new_sha} after the move (old target {old_sha})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-dir", type=Path, default=BASE_DIR_DEFAULT)
    ap.add_argument("--stage-dir", type=Path, default=STAGE_DIR_DEFAULT)
    ap.add_argument("--download", action="store_true", help="fetch the v9.3 base from the Hub")
    ap.add_argument("--publish", action="store_true", help="commit to the Hub and move the v9.3 tag")
    ap.add_argument("--docs-only", metavar="PARENT",
                    help="skip the build; publish README/CHANGELOG/manifest from --stage-dir on top of PARENT")
    args = ap.parse_args()
    if args.docs_only:
        import os

        print("published", publish(args.stage_dir, os.environ["HF_TOKEN"], parent=args.docs_only,
                                   only=DOC_FILES, message="card: address review (license metadata, model input, "
                                   "placeholders, EDGAR rights); manifest sha refresh"))
        return 0
    if args.download:
        download_base(args.base_dir)
    print(build(args.base_dir, args.stage_dir))
    if args.publish:
        import os

        print("published", publish(args.stage_dir, os.environ["HF_TOKEN"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
