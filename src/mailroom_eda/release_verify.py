"""Post-publication release verification for ``mailroom-dataset``.

Checks what the Hub actually serves at a release tag, after publication,
in two layers:

* **Invariants** (code; every release): the manifest sha table covers every
  served file and matches its bytes; the card's ``configs`` cover every
  parquet dir; the blind ``default`` config carries only the four blind
  columns and no label key, in either split; the split rule holds; the
  ``default`` and ``ground_truth`` row sets, ids and content hashes agree;
  ``gt_fields`` parses on every row; each class routes to exactly one
  specialist; the sidecar JSONL is line-boundary safe and matches the
  ground-truth row count.
* **Release spec** (data; one frozen JSON per tag in ``release_specs/``):
  the tag's commit, row counts per config and split, columns, class and
  subclass counts, routing, ``gt_fields`` key union, blind metadata keys,
  provenance distribution, and every file's sha256. A spec holds only
  aggregates (counts, names, hashes), never a row's label, so it is safe in
  git.

A new release extends the checks by freezing its spec from the Hub
(``--freeze``) and reviewing the printed diff against the previous release.
``--all`` re-verifies every frozen release, which also catches a tag that
moved without its spec being re-frozen.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from .config import DATA_DIR, REPO_ID
from .identity import content_sha256
from .release_sections import GT_SCALAR_KEYS

SPEC_DIR = Path(__file__).resolve().parent / "release_specs"
CACHE_DIR = DATA_DIR / "release_verify"
SPEC_FORMAT = 1

CONFIGS = ("default", "ground_truth", "bundles", "streams", "fixtures")
SPLITS = ("train", "test")
BLIND_COLUMNS = ("filename", "doc_text", "prompt", "metadata")
JSONL_SIDECARS = ("ground_truth_hardened.jsonl", "bundles.jsonl",
                  "streams.jsonl", "fixtures.jsonl")
#: Served files the manifest does not (and cannot) hash.
UNHASHED = {"manifest.txt", ".gitattributes"}
#: Label-bearing names that must never appear as a blind column or a blind
#: ``metadata`` key: every published ``gt_fields`` key plus the label columns.
LABEL_KEYS = frozenset(GT_SCALAR_KEYS) | {
    "expected", "expected_subclass", "expected_specialist", "expected_stage",
    "gt_fields", "entity_name", "record_type", "relationships",
    "related_document_ids", "review_expected", "review_reason",
    "retry_expected", "expected_post_retry_state", "annotation_method",
    "annotation_source", "annotation_model", "annotation_confidence",
}
LINE_BREAKERS = ("\u2028", "\u2029", "\u0085")

_ENTRY_RE = re.compile(r"^[ \t]*(\S+)[ \t]+([0-9a-f]{64})[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class Finding:
    check: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.detail}"


# --------------------------------------------------------------------------
# reading a snapshot
# --------------------------------------------------------------------------

def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def served_files(snap: Path) -> list[str]:
    """Repo-relative paths of every file in the snapshot (no HF cache dirs)."""
    out = []
    for p in sorted(snap.rglob("*")):
        rel = p.relative_to(snap).as_posix()
        if p.is_file() and not rel.startswith(".cache/") and not rel.startswith(".huggingface/"):
            out.append(rel)
    return out


def manifest_entries(snap: Path) -> dict[str, str]:
    path = snap / "manifest.txt"
    if not path.is_file():
        return {}
    return dict(_ENTRY_RE.findall(path.read_text(encoding="utf-8")))


def read_rows(snap: Path, config: str, split: str) -> list[dict[str, Any]]:
    files = sorted((snap / "parquet" / config / split).glob("*.parquet"))
    rows: list[dict[str, Any]] = []
    for f in files:
        rows.extend(pq.read_table(f).to_pylist())
    return rows


def read_columns(snap: Path, config: str, split: str) -> list[str]:
    files = sorted((snap / "parquet" / config / split).glob("*.parquet"))
    return pq.read_schema(files[0]).names if files else []


def card_configs(snap: Path) -> dict[str, list[str]]:
    """config_name -> data_files paths, from the README YAML header."""
    import yaml

    text = (snap / "README.md").read_text(encoding="utf-8") if (snap / "README.md").is_file() else ""
    if not text.startswith("---"):
        return {}
    header = yaml.safe_load(text.split("---", 2)[1]) or {}
    out: dict[str, list[str]] = {}
    for cfg in header.get("configs") or []:
        files = cfg.get("data_files") or []
        if isinstance(files, str):
            files = [files]
        out[cfg["config_name"]] = [f["path"] if isinstance(f, dict) else f for f in files]
    return out


def is_test(filename: str) -> bool:
    """The published split rule: md5(filename) % 10 == 0 -> test."""
    return int(hashlib.md5(filename.encode("utf-8")).hexdigest(), 16) % 10 == 0


def _gt_fields(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("gt_fields")
    return raw if isinstance(raw, dict) else json.loads(raw)


# --------------------------------------------------------------------------
# facts (what a spec freezes)
# --------------------------------------------------------------------------

def collect_facts(snap: Path) -> dict[str, Any]:
    """Aggregate, label-free facts about a snapshot. Missing parts are absent."""
    facts: dict[str, Any] = {"rows": {}, "columns": {}}
    for cfg in CONFIGS:
        if not (snap / "parquet" / cfg).is_dir():
            continue
        facts["rows"][cfg] = {s: len(read_rows(snap, cfg, s)) for s in SPLITS}
        facts["columns"][cfg] = read_columns(snap, cfg, "train")

    gt = [r for s in SPLITS for r in read_rows(snap, "ground_truth", s)]
    if gt:
        facts["classes"] = dict(sorted(Counter(r["expected"] for r in gt).items()))
        facts["subclasses"] = dict(sorted(Counter(
            f"{r['expected']}/{r['expected_subclass']}" for r in gt).items()))
        facts["annotation_method"] = dict(sorted(Counter(r["annotation_method"] for r in gt).items()))
        keys: set[str] = set()
        for r in gt:
            keys |= set(_gt_fields(r))
        facts["gt_fields_keys"] = sorted(keys)

    routing = collect_routing(snap)
    if routing:
        facts["routing"] = {c: dict(sorted(v.items())) for c, v in sorted(routing.items())}

    default = [r for s in SPLITS for r in read_rows(snap, "default", s)]
    if default:
        mkeys: set[str] = set()
        for r in default:
            if isinstance(r.get("metadata"), dict):
                mkeys |= set(r["metadata"])
        facts["blind_metadata_keys"] = sorted(mkeys)

    files = {f: sha256_path(snap / f) for f in served_files(snap) if f not in UNHASHED}
    if files:
        facts["files"] = files
    return facts


# --------------------------------------------------------------------------
# invariants (every release)
# --------------------------------------------------------------------------

def check_manifest(snap: Path) -> list[Finding]:
    out: list[Finding] = []
    entries = manifest_entries(snap)
    if not entries:
        return [Finding("manifest", "manifest.txt missing or has no sha256 table")]
    served = set(served_files(snap)) - UNHASHED
    for name in sorted(served - set(entries)):
        out.append(Finding("manifest", f"served file not in the manifest: {name}"))
    for name, sha in sorted(entries.items()):
        if name not in served:
            out.append(Finding("manifest", f"manifest lists a file the Hub does not serve: {name}"))
        elif sha256_path(snap / name) != sha:
            out.append(Finding("manifest", f"sha256 mismatch: {name}"))
    return out


def check_card_configs(snap: Path) -> list[Finding]:
    out: list[Finding] = []
    configs = card_configs(snap)
    if not configs:
        return [Finding("card", "README.md has no YAML configs")]
    served = served_files(snap)
    for name, paths in configs.items():
        for pat in paths:
            if not any(fnmatch.fnmatch(f, pat) for f in served):
                out.append(Finding("card", f"config {name}: {pat} matches no served file"))
    declared = [pat for paths in configs.values() for pat in paths]
    for f in served:
        if f.endswith(".parquet") and not any(fnmatch.fnmatch(f, p) for p in declared):
            out.append(Finding("card", f"parquet file in no card config: {f}"))
    return out


def check_blind(snap: Path) -> list[Finding]:
    out: list[Finding] = []
    for s in SPLITS:
        cols = read_columns(snap, "default", s)
        if tuple(cols) != BLIND_COLUMNS:
            out.append(Finding("blind", f"default/{s} columns {cols} != {list(BLIND_COLUMNS)}"))
        for r in read_rows(snap, "default", s):
            meta = r.get("metadata") or {}
            leaked = sorted(LABEL_KEYS & set(meta)) if isinstance(meta, dict) else []
            if leaked:
                out.append(Finding("blind", f"default/{s} metadata carries label keys {leaked}"))
                break
    return out


def check_splits_and_identity(snap: Path) -> list[Finding]:
    out: list[Finding] = []
    for s in SPLITS:
        default = read_rows(snap, "default", s)
        gt = read_rows(snap, "ground_truth", s)
        for cfg, rows in (("default", default), ("ground_truth", gt)):
            wrong = [r["filename"] for r in rows if is_test(r["filename"]) != (s == "test")]
            if wrong:
                out.append(Finding("split", f"{cfg}/{s}: {len(wrong)} rows break md5 rule, e.g. {wrong[0]}"))
        bad_col = [r["filename"] for r in gt if r.get("split") not in (None, s)]
        if bad_col:
            out.append(Finding("split", f"ground_truth/{s}: split column disagrees on {len(bad_col)} rows"))
        dnames = [r["filename"] for r in default]
        gnames = [r["filename"] for r in gt]
        if len(set(dnames)) != len(dnames):
            out.append(Finding("identity", f"default/{s}: duplicate filenames"))
        if set(dnames) != set(gnames):
            out.append(Finding("identity", f"{s}: default and ground_truth filename sets differ "
                                           f"({len(set(dnames) ^ set(gnames))} names)"))
        text = {r["filename"]: r["doc_text"] for r in default}
        drift = [r["filename"] for r in gt if r["filename"] in text
                 and r.get("content_sha256") != content_sha256(text[r["filename"]] or "")]
        if drift:
            out.append(Finding("identity", f"{s}: content_sha256 != default doc_text on {len(drift)} rows, "
                                           f"e.g. {drift[0]}"))
        for r in gt:
            try:
                _gt_fields(r)
            except (TypeError, ValueError):
                out.append(Finding("gt_fields", f"{s}: gt_fields does not parse on {r['filename']}"))
                break
    ids = [r["document_id"] for s in SPLITS for r in read_rows(snap, "ground_truth", s)]
    if any(not i for i in ids):
        out.append(Finding("identity", "ground_truth has empty document_id values"))
    if len(set(ids)) != len(ids):
        out.append(Finding("identity", "ground_truth document_id values are not unique"))
    return out


def check_routing(snap: Path) -> list[Finding]:
    routing = collect_routing(snap)
    return [Finding("routing", f"{cls} routes to several specialists: {by}")
            for cls, by in sorted(routing.items()) if len(by) != 1]


def collect_routing(snap: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for cfg in ("ground_truth", "bundles", "streams", "fixtures"):
        if not (snap / "parquet" / cfg).is_dir():
            continue
        for s in SPLITS:
            for r in read_rows(snap, cfg, s):
                if r.get("expected_specialist"):
                    by = out.setdefault(r["expected"], {})
                    by[r["expected_specialist"]] = by.get(r["expected_specialist"], 0) + 1
    return out


def check_sidecars(snap: Path) -> list[Finding]:
    out: list[Finding] = []
    for name in JSONL_SIDECARS:
        path = snap / name
        if not path.is_file():
            out.append(Finding("sidecar", f"{name} not served"))
            continue
        text = path.read_text(encoding="utf-8")
        raw = [c for c in LINE_BREAKERS if c in text]
        if raw:
            out.append(Finding("sidecar", f"{name} carries raw line separators {[hex(ord(c)) for c in raw]}"))
        lines = [ln for ln in text.split("\n") if ln.strip()]
        for i, ln in enumerate(lines):
            try:
                json.loads(ln)
            except ValueError:
                out.append(Finding("sidecar", f"{name} line {i + 1} is not JSON"))
                break
        cfg = {"ground_truth_hardened.jsonl": "ground_truth"}.get(name, name.removesuffix(".jsonl"))
        n = sum(len(read_rows(snap, cfg, s)) for s in SPLITS)
        if len(lines) != n:
            out.append(Finding("sidecar", f"{name} has {len(lines)} rows, {cfg} parquet has {n}"))
    return out


INVARIANTS = (check_manifest, check_card_configs, check_blind,
              check_splits_and_identity, check_routing, check_sidecars)


def check_invariants(snap: Path) -> list[Finding]:
    out: list[Finding] = []
    for check in INVARIANTS:
        out.extend(check(snap))
    return out


# --------------------------------------------------------------------------
# specs
# --------------------------------------------------------------------------

def spec_path(tag: str) -> Path:
    return SPEC_DIR / f"{tag}.json"


def load_spec(tag: str) -> dict[str, Any]:
    return json.loads(spec_path(tag).read_text(encoding="utf-8"))


def version_key(tag: str) -> tuple[int, ...]:
    """``v9.3`` -> (9, 3); orders tags numerically (v9.10 after v9.9)."""
    return tuple(int(x) for x in re.findall(r"\d+", tag))


def frozen_tags() -> list[str]:
    return sorted((p.stem for p in SPEC_DIR.glob("*.json")), key=version_key)


def previous_tag(tag: str) -> str | None:
    earlier = [t for t in frozen_tags() if version_key(t) < version_key(tag)]
    return earlier[-1] if earlier else None


SPEC_META = ("format", "repo_id", "tag", "revision", "accepted_findings")


def make_spec(tag: str, revision: str, facts: dict[str, Any], repo_id: str = REPO_ID,
              accepted: list[Finding] = (), reason: str = "") -> dict[str, Any]:
    """``accepted``: invariant failures of an already-published release that
    were reviewed and cannot be fixed in place; each carries ``reason``."""
    if accepted and not reason.strip():
        raise ValueError("accepted findings need a reason")
    spec = {"format": SPEC_FORMAT, "repo_id": repo_id, "tag": tag, "revision": revision, **facts}
    if accepted:
        spec["accepted_findings"] = [{"finding": str(f), "reason": reason} for f in accepted]
    return spec


def apply_accepted(findings: list[Finding], spec: dict[str, Any]) -> list[Finding]:
    """Drop the spec's accepted findings. A published release cannot change,
    so an accepted finding that no longer occurs is itself a finding."""
    accepted = {a["finding"] for a in spec.get("accepted_findings", [])}
    seen = {str(f) for f in findings}
    out = [f for f in findings if str(f) not in accepted]
    out += [Finding("spec", f"accepted finding no longer occurs: {a}") for a in sorted(accepted - seen)]
    return out


def write_spec(spec: dict[str, Any]) -> Path:
    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    path = spec_path(spec["tag"])
    path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def compare_facts(facts: dict[str, Any], spec: dict[str, Any]) -> list[Finding]:
    """Every fact the spec pins must equal the observed fact."""
    out: list[Finding] = []
    for key, want in spec.items():
        if key in SPEC_META:
            continue
        got = facts.get(key)
        if got == want:
            continue
        if isinstance(want, dict) and isinstance(got, dict):
            for k in sorted(set(want) | set(got)):
                if want.get(k) != got.get(k):
                    out.append(Finding("spec", f"{key}.{k}: expected {want.get(k)!r}, got {got.get(k)!r}"))
        else:
            out.append(Finding("spec", f"{key}: expected {want!r}, got {got!r}"))
    return out


def diff_specs(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Human-readable changes between two releases' specs (for --freeze review)."""
    lines = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                if a.get(k) != b.get(k):
                    lines.append(f"{key}.{k}: {a.get(k)!r} -> {b.get(k)!r}")
        else:
            lines.append(f"{key}: {a!r} -> {b!r}")
    return lines


# --------------------------------------------------------------------------
# the Hub side
# --------------------------------------------------------------------------

def resolve_tag(api, tag: str, repo_id: str = REPO_ID) -> str:
    """The commit a tag points at. ``list_repo_refs`` gives an annotated tag's
    own object sha, not its commit, so it only proves the tag exists."""
    refs = api.list_repo_refs(repo_id, repo_type="dataset")
    if not any(ref.name == tag for ref in refs.tags):
        raise LookupError(f"{repo_id} has no tag {tag!r}")
    return api.dataset_info(repo_id, revision=tag).sha


def fetch(revision: str, repo_id: str = REPO_ID, cache_dir: Path = CACHE_DIR) -> Path:
    """Download every file the Hub serves at ``revision`` (a commit sha)."""
    from huggingface_hub import snapshot_download

    dest = cache_dir / revision
    snapshot_download(repo_id=repo_id, repo_type="dataset", revision=revision, local_dir=dest)
    return dest


def check_card_on_hub(snap: Path) -> list[Finding]:
    """The Hub's own dataset-card validation (network)."""
    from huggingface_hub import DatasetCard

    try:
        DatasetCard((snap / "README.md").read_text(encoding="utf-8")).validate(repo_type="dataset")
    except Exception as exc:  # noqa: BLE001 - report whatever the Hub rejects
        return [Finding("card", f"Hub card validation failed: {exc}")]
    return []


def verify_release(api, tag: str, *, repo_id: str = REPO_ID,
                   cache_dir: Path = CACHE_DIR, keep: bool = False) -> list[Finding]:
    """Full post-publication check of one frozen release, against the Hub."""
    spec = load_spec(tag)
    out: list[Finding] = []
    commit = resolve_tag(api, tag, repo_id)
    if commit != spec["revision"]:
        out.append(Finding("tag", f"{tag} resolves to {commit}, spec pins {spec['revision']}"))
    snap = fetch(commit, repo_id, cache_dir)
    try:
        out.extend(apply_accepted(check_invariants(snap), spec))
        out.extend(check_card_on_hub(snap))
        out.extend(compare_facts(collect_facts(snap), spec))
    finally:
        if not keep:
            shutil.rmtree(snap, ignore_errors=True)
    return out
