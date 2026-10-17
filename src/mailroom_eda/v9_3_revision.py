"""v9.3 corporate presence-layer revision — STAGING (code-only, labels-only).

Plan: ``docs/plans/v9.3-corporate-presence-revision.md`` (this branch:
``feat/v9.3-staging-corporate-presence``).

The v9.2.1 label repair (HF branch ``corporate-labels-v9.2.1``) proved the
per-subclass entity regime against the live corpus, but its producer lived
in ``llm-dojo-scoring`` — the EDA pipeline structurally could not re-derive
its ``gt_presence`` maps (the uniform 3-key corporate law) or even verify
them (``verify_presence_codes`` asserted exact key-set equality with a
20-key uniform set). This revision lifts that freeze, v9.2.1-style: the row
set / identity / content (and every populated label VALUE) are unchanged
from the published v9.2.1 content; what changes is the *presence-layer
convention* the pipeline applies and publishes:

* the platform registry: ``gt_presence`` / ``corporate_presence`` carry the
  37 corporate entity keys (6 core + 31 per-subclass extension) mirrored,
  sha-pinned, from ``llm_dojo_scoring.corporate_record_schema``;
* the EDA-side relabel equivalents (``corporate_presence.relabel_row``)
  reproduce the published v9.2.1 label repair byte-identically from the v9.2
  base — verified row-by-row below, STOP-not-force on any divergence;
* the manifest adopts ``labels_version`` alongside the dataset version (the
  FAIR practice: label waves that don't change rows get their own version);
* the published card documents the presence-code vocabulary verbatim from
  the dojo docs.

Zero-drift law (relative to the v9.2 base): ``document_id``,
``content_sha256``, ``normalized_text_sha256``, ``doc_text``, every metadata
key, and every non-corporate row are byte-identical. Corporate rows differ
from the base only in ``gt_fields`` + ``expected_subclass`` (12 verified
content-conflict re-assignments) and are byte-identical (raw line bytes) to
the published ``corporate-labels-v9.2.1`` content.

Precedence honesty: this revision invents NO label facts. The entity
presence on the 450 corporate rows is exactly what the v9.2.1 relabel
derived (``entity_name`` / ``record_type`` pins + not-applicable core
marks); everything else stays unstated-absent (unlabeled) for real
annotation. Nothing here pushes a tag, writes to the HF Hub, or publishes:
``build_all`` refuses ``--publish`` for a staging run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR, REPO_ID
from .corporate_presence import relabel_rows
from .gt_presence import (
    ALL_GT_FIELDS,
    BASE_GT_FIELDS,
    PRESENCE_CODES,
    presence_law_problems,
)
from .v9_1_revision import upsert_readme_addendum

V9_3_VERSION = "v9.3"
PLAN_REF = "docs/plans/v9.3-corporate-presence-revision.md"

#: The v9.3 label wave name (manifest ``labels_version`` line). Dataset rows
#: and their identities stay at the v9-family dataset version; labels (the
#: corporate presence layer) version independently per FAIR guidance.
LABELS_VERSION = "v9.3 corporate gt_presence presence-layer"

REVISION_LABEL = "v9.3 corporate presence-layer revision"

REVISION_LINE = (
    f"{REVISION_LABEL} (per-subclass gt_presence key-set for corporate_record, "
    f"registry from llm_dojo_scoring.corporate_record_schema; label-only over "
    f"v9.2.1 — row set / identity / content unchanged, populated values unchanged)"
)

RELEASE_BRANCH = "corporate-labels-v9.2.1"
BASE_TAG = "v9.2"

BRANCH_SNAPSHOT_DIR = DATA_DIR / "v9_3" / "branch_snapshot"
BASE_SNAPSHOT_DIR = DATA_DIR / "v9_3" / "base_snapshot"
STAGE_DIR_DEFAULT = DATA_DIR / "v9_3" / "stage"

#: Pinned sha256 of the v9.2 base sidecar (tag ``v9.2``, parent data commit
#: 1eb5b42c) and of the published v9.2.1 branch sidecar — the build refuses
#: to run over other bytes.
BASE_SIDECAR_SHA256 = "50390f78fcad83ce595764eab53cbba1704f6c35d7f8aeb8df80c6bed649ad9d"
BRANCH_SIDECAR_SHA256 = "dbe0c604c99cec15e04a2c7ebd623364043455e77080937d710fd4919d257459"

#: Corpus counts identical from v9.1 → v9.2 → v9.2.1 → v9.3 (row-set law).
EXPECTED_TOTAL_ROWS = 3302
EXPECTED_SPLIT_COUNTS = {"train": 2979, "test": 323}
EXPECTED_CORPORATE_ROWS = 450
#: Registry surface: 6 entity-core + 31 extension keys.
EXPECTED_CORPORATE_ENTITY_KEYS = 37

# bundles / streams / fixtures are pulled read-only (never restaged); the
# label surfaces (default/ground_truth parquet) come along as verified bytes.
ALLOW_PATTERNS = [
    "parquet/*",
    "ground_truth_hardened.jsonl",
    "README.md",
    "manifest.txt",
    "bundles.jsonl",
    "streams.jsonl",
    "fixtures.jsonl",
    "*/dataset_info.json",
]


def sha256_file(path: Path) -> str:
    """sha256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def ensure_branch_snapshot(force: bool = False) -> Path:
    """Snapshot-download the ``corporate-labels-v9.2.1`` branch payload —
    the verified source of truth for the relabeled GT (read-only)."""
    from huggingface_hub import snapshot_download

    marker = BRANCH_SNAPSHOT_DIR / "ground_truth_hardened.jsonl"
    if marker.exists() and sha256_file(marker) == BRANCH_SIDECAR_SHA256 and not force:
        return BRANCH_SNAPSHOT_DIR
    if BRANCH_SNAPSHOT_DIR.exists():
        shutil.rmtree(BRANCH_SNAPSHOT_DIR)
    BRANCH_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        revision=RELEASE_BRANCH,
        allow_patterns=ALLOW_PATTERNS,
        local_dir=BRANCH_SNAPSHOT_DIR,
    )
    got = sha256_file(BRANCH_SNAPSHOT_DIR / "ground_truth_hardened.jsonl")
    assert got == BRANCH_SIDECAR_SHA256, (
        f"branch sidecar sha {got} != pinned {BRANCH_SIDECAR_SHA256}"
    )
    return BRANCH_SNAPSHOT_DIR


def download_base_snapshot(force: bool = False) -> Path:
    """Snapshot-download the v9.2 base (tag ``v9.2``) sidecar — the rows the
    relabel equivalents run over."""
    from huggingface_hub import snapshot_download

    marker = BASE_SNAPSHOT_DIR / "ground_truth_hardened.jsonl"
    if marker.exists() and sha256_file(marker) == BASE_SIDECAR_SHA256 and not force:
        return BASE_SNAPSHOT_DIR
    if BASE_SNAPSHOT_DIR.exists():
        shutil.rmtree(BASE_SNAPSHOT_DIR)
    BASE_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        revision=BASE_TAG,
        allow_patterns=["ground_truth_hardened.jsonl"],
        local_dir=BASE_SNAPSHOT_DIR,
    )
    got = sha256_file(BASE_SNAPSHOT_DIR / "ground_truth_hardened.jsonl")
    assert got == BASE_SIDECAR_SHA256, (
        f"v9.2 base sidecar sha {got} != pinned {BASE_SIDECAR_SHA256}"
    )
    return BASE_SNAPSHOT_DIR


def resolve_base_path(explicit: str | None = None) -> Path:
    """Base sidecar path: explicit override (e.g. a verified /tmp artifact;
    the path is sha-checked so /tmp drift cannot enter the build), then the
    snapshot dir (downloading it if needed)."""
    if explicit:
        p = Path(explicit)
        got = sha256_file(p)
        assert got == BASE_SIDECAR_SHA256, (
            f"base sidecar {p} sha {got} != pinned v9.2 {BASE_SIDECAR_SHA256}"
        )
        return p
    return download_base_snapshot() / "ground_truth_hardened.jsonl"


# ---------------------------------------------------------------------------
# the relabel pass (EDA-side equivalents)
# ---------------------------------------------------------------------------


def relabel_all_rows(rows: list[dict]) -> list[dict]:
    """Run the EDA relabel equivalents over the v9.2 base rows.

    Non-corporate rows pass through untouched; corporate rows are re-labeled
    by ``corporate_presence.relabel_row`` (the byte-verified port of the dojo
    relabel that produced ``corporate-labels-v9.2.1``). No new label facts —
    whatever the equivalents cannot derive from the row itself stays as
    published."""
    new_rows = []
    for new, _changes in relabel_rows([dict(r) for r in rows]):
        new_rows.append(new)
    _guard_relabel_shape(rows, new_rows)
    return new_rows


#: Which corporate top-level columns the relabel equivalents may change
#: beyond ``gt_fields``: the 12 verified content-conflict re-assignments.
CORPORATE_RELABEL_ROW_CHANGES = ("expected_subclass",)

#: Which existing ``gt_fields`` keys the relabel equivalents may REWRITE on
#: corporate rows (nothing else may change; entity keys may only be added):
#: the presence map plus the five keys the v9.2.1 repair labels.
RELABEL_OWNED_GT_KEYS = frozenset({
    "gt_presence", "entity_name", "subject_matter", "keywords",
    "record_type", "intent",
})


def _guard_relabel_shape(old_rows: list[dict], new_rows: list[dict]) -> None:
    """Relabel equivalents may change ONLY corporate rows' ``gt_fields`` and
    ``expected_subclass`` — anything else is a hard STOP (precedence
    honesty: the labels carry only what the v9.2.1 relabel derived)."""
    old_by_fn = {r["filename"]: r for r in old_rows}
    assert len(old_by_fn) == len(old_rows)
    assert len({r["filename"] for r in new_rows}) == len(new_rows), "duplicate rows after relabel"
    for new in new_rows:
        fn = new["filename"]
        old = old_by_fn[fn]
        if new.get("expected") != "corporate_record":
            assert _json_digest(new) == _json_digest(old), (
                f"{fn}: non-corporate row was modified by the relabel pass"
            )
            continue
        for col in set(old) | set(new):
            if col == "gt_fields":
                continue
            ok = old.get(col) == new.get(col) or col in CORPORATE_RELABEL_ROW_CHANGES
            assert ok, f"{fn}: relabel pass changed {col!r} — outside the v9.2.1 allowlist"


# ---------------------------------------------------------------------------
# canonical + raw comparisons
# ---------------------------------------------------------------------------


def _json_digest(obj: Any) -> str:
    """Canonical JSON of a parsed value (key-order-insensitive). Raw-byte
    identity for whole lines is checked separately by
    :func:`verify_raw_line_equality` — insertion order is proven, not assumed."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def verify_row_set(rows: list[dict]) -> dict[str, int]:
    """Gate (a): the row set / split counts are unchanged."""
    splits: dict[str, int] = {}
    corporate = 0
    for row in rows:
        splits[row.get("split") or ""] = splits.get(row.get("split") or "", 0) + 1
        if row.get("expected") == "corporate_record":
            corporate += 1
    assert len(rows) == EXPECTED_TOTAL_ROWS, (
        f"row set drifted: {len(rows)} != {EXPECTED_TOTAL_ROWS}"
    )
    assert splits == EXPECTED_SPLIT_COUNTS, f"split counts drifted: {splits}"
    assert corporate == EXPECTED_CORPORATE_ROWS, (
        f"corporate row count drifted: {corporate} != {EXPECTED_CORPORATE_ROWS}"
    )
    print(f"verify_row_set OK — {len(rows):,} rows, splits {splits}, corporate {corporate}")
    return {"rows": len(rows), "splits": splits, "corporate": corporate}


def _presence_of(row: dict) -> dict[str, str]:
    gt = row.get("gt_fields") or {}
    raw = gt.get("gt_presence") or {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    return dict(raw)


def verify_presence_laws(rows: list[dict]) -> dict[str, Any]:
    """Gates (b) + (c): every row's presence keys sit inside the extended
    ``ALL_GT_FIELDS`` (unknown keys rejected) and contain the base keys;
    corporate rows are the only rows allowed to extend with entity keys; and
    every shipped value is a published-vocabulary code.

    Row verdicts come from the single-sourced law
    (``gt_presence.presence_law_problems``), which since this hardening also
    enforces the SUBCLASS-CONDITIONAL extension law (v9.3): a corporate
    row's present extension keys must be ⊆ the 6 entity-core keys + that
    row's OWN subclass's extension keys (registry mirror) — foreign-subclass
    extensions are ABSENT; unknown ``expected_subclass`` ⇒ core-only."""
    base = set(BASE_GT_FIELDS)
    corporate: dict[str, int] = {c: 0 for c in PRESENCE_CODES}
    extended_keys: set[str] = set()
    n_corporate = 0
    for row in rows:
        presence = _presence_of(row)
        fn = row["filename"]
        problems = presence_law_problems(
            presence, row.get("expected") or "", row.get("expected_subclass")
        )
        # Explicit raise (not a bare assert): the conformance script derives
        # gate statuses from this function's return values, and a `python -O`
        # run strips asserts — the law must not become unenforceable there.
        if problems:
            raise AssertionError(
                f"{fn}: " + "; ".join(problems)
            )
        extension = set(presence) - base
        if extension:
            extended_keys |= extension
        if row.get("expected") == "corporate_record":
            n_corporate += 1
            for code in presence.values():
                corporate[code] += 1
    stats = {
        "rows": len(rows),
        "corporate_rows": n_corporate,
        "corporate_codes": corporate,
        "extension_keys_seen": sorted(extended_keys),
        "all_gt_fields": len(ALL_GT_FIELDS),
        "base_gt_fields": len(BASE_GT_FIELDS),
    }
    print(
        f"verify_presence_laws OK — keys ⊆ {len(ALL_GT_FIELDS)}-key registry("
        f"extensions seen: {len(extended_keys)}) / ⊇ {len(BASE_GT_FIELDS)}-key base; "
        f"subclass-conditional extensions (core + own-subclass only; "
        f"unknown ⇒ core-only); corporate codes {corporate}"
    )
    return stats


def verify_zero_drift(old_rows: list[dict], new_rows: list[dict]) -> None:
    """v9.3 zero-drift, base-relative: ONLY corporate rows' ``gt_fields`` +
    ``expected_subclass`` may differ from the v9.2 base (the verified v9.2.1
    repair — never removed gt keys, never changed unchanged values)."""
    assert len(old_rows) == len(new_rows), f"{len(old_rows)} != {len(new_rows)}"
    old_by_fn = {r["filename"]: r for r in old_rows}
    assert len(old_by_fn) == len(old_rows)

    def _norm(v: Any) -> Any:
        if v is None:
            return ""
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v]
        return str(v)

    for new in new_rows:
        fn = new["filename"]
        old = old_by_fn[fn]
        corporate = old.get("expected") == "corporate_record"
        for col in old:
            if col == "gt_fields" or (corporate and col == "expected_subclass"):
                continue
            assert _norm(old.get(col)) == _norm(new.get(col)), (
                f"{fn}: {col!r} drifted"
            )
        old_gt, new_gt = old.get("gt_fields") or {}, new.get("gt_fields") or {}
        if not corporate:
            assert _json_digest(new_gt) == _json_digest(old_gt), (
                f"{fn}: non-corporate gt_fields drifted"
            )
        else:
            # the relabel equivalents may ADD entity keys and rewrite ONLY the
            # relabel-owned keys (presence map + the v9.2.1 repair keys); every
            # other pre-existing value is frozen.
            for k, v in old_gt.items():
                assert k in new_gt, (
                    f"{fn}: corporate gt_fields[{k!r}] removed by the relabel pass"
                )
                if k in RELABEL_OWNED_GT_KEYS:
                    continue
                assert new_gt[k] == v, (
                    f"{fn}: corporate gt_fields[{k!r}] altered by the relabel pass"
                )
    print(f"verify_zero_drift OK — {len(new_rows)} rows vs the v9.2 base, allowlist exact")


def verify_labels_equal_branch(new_rows: list[dict], branch_rows: list[dict]) -> dict[str, int]:
    """Gates (d) + (e), canonical form: every corporate row's reparsed
    ``gt_fields`` equals the v9.2.1 branch's (byte-identical values,
    insertion order proven separately); every non-corporate row is
    canonically identical row-for-row."""
    assert len(new_rows) == len(branch_rows), (
        f"{len(new_rows)} != {len(branch_rows)}: row count mismatch vs branch"
    )
    branch_by_fn = {r["filename"]: r for r in branch_rows}
    corp = 0
    for row in new_rows:
        fn = row["filename"]
        ref = branch_by_fn.get(fn)
        assert ref is not None, f"{fn}: missing from the v9.2.1 branch content"
        if row.get("expected") == "corporate_record":
            corp += 1
            assert _json_digest(row["gt_fields"]) == _json_digest(ref["gt_fields"]), (
                f"{fn}: corporate gt_fields diverges from the v9.2.1 source — STOP"
            )
        else:
            assert _json_digest(row) == _json_digest(ref), (
                f"{fn}: non-corporate row diverges from v9.2.1 — STOP"
            )
    stats = {"corporate": corp, "non_corporate": len(new_rows) - corp}
    print(
        f"verify_labels_equal_branch OK — {corp} corporate gt_fields byte-faithful to "
        f"v9.2.1, {stats['non_corporate']} non-corporate rows identical"
    )
    return stats


def verify_raw_line_equality(new_rows: list[dict], branch_path: Path) -> dict[str, int]:
    """Gates (d) + (e), raw form: rebuild every sidecar line the way the
    v9.2.1 branch's writer did (unsorted ``json.dumps`` + line-boundary
    sanitize — the EDA relabel preserves the dojo insertion order, so this is
    a real rebuild, not a copy) and require byte equality with the published
    file, corporate and non-corporate alike. Any divergence is a STOP."""
    from .dataset_export import sanitize_line_boundary_chars

    branch_lines = branch_path.read_bytes().split(b"\n")
    branch_by_fn = {json.loads(ln)["filename"]: ln for ln in branch_lines if ln}
    raw_corp = raw_noncorp = 0
    for row in new_rows:
        fn = row["filename"]
        rebuilt = (
            sanitize_line_boundary_chars(json.dumps(row, default=str, ensure_ascii=False))
            + "\n"
        ).encode()
        if rebuilt != branch_by_fn[fn] + b"\n":
            for i, (x, y) in enumerate(zip(rebuilt, branch_by_fn[fn])):
                if x != y:
                    raise AssertionError(
                        f"{fn}: rebuilt sidecar line diverges from v9.2.1 bytes "
                        f"(first diff at byte {i}, context "
                        f"{rebuilt[max(0, i - 60):i + 60]!r}) — STOP"
                    )
            raise AssertionError(
                f"{fn}: rebuilt sidecar line length diverges from v9.2.1 bytes — STOP"
            )
        if row.get("expected") == "corporate_record":
            raw_corp += 1
        else:
            raw_noncorp += 1
    print(
        f"verify_raw_line_equality OK — {raw_corp} corporate + {raw_noncorp} "
        f"non-corporate rows byte-identical to the v9.2.1 branch sidecar"
    )
    return {"corporate": raw_corp, "non_corporate": raw_noncorp}


def _canonical_gt_value(v: Any) -> str:
    """Canonical string of one parsed ``gt_fields`` value (key-order-
    insensitive for dicts/lists; strings pass through) — the comparison
    form for the staged-parquet ⟷ JSONL field-by-field check."""
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, ensure_ascii=False)
    return "" if v is None else str(v)


def verify_staged_parquet_gt_fields(rows: list[dict], stage_dir: Path) -> dict[str, Any]:
    """Gate (g): the staged ``parquet/ground_truth`` bundles (byte-copies of
    the ``corporate-labels-v9.2.1`` branch parquet — the EDA writer's bytes
    differ at container level, so this verifies CONTENT, not bytes) carry
    exactly the hardened JSONL ``gt_fields``:

    * row count and per-split counts match the JSONL (and the v9-family
      corpus law: 2,979 train / 323 test);
    * every filename joins 1:1 between parquet and JSONL;
    * every row's parsed parquet ``gt_fields`` equals the JSONL's field by
      field — for all rows, corporate and non-corporate alike (the corporate
      comparison is the substance: the staged maps carry the per-subclass
      entity keys the parquet must not have lost).

    Cheap (two arrow scans + dict joins); run after both payloads are laid
    down in ``build_all`` and as conformance gate (g)."""
    import pyarrow.parquet as pq

    jsonl_by_fn = {r["filename"]: r for r in rows}
    assert len(jsonl_by_fn) == len(rows), "duplicate JSONL filenames"
    jsonl_splits: dict[str, int] = {}
    for row in rows:
        split = str(row.get("split") or "")
        jsonl_splits[split] = jsonl_splits.get(split, 0) + 1
    parquet_n = 0
    parquet_splits: dict[str, int] = {}
    corporate = 0
    for split in ("train", "test"):
        files = sorted(
            (Path(stage_dir) / "parquet" / "ground_truth" / split).glob("*.parquet")
        )
        assert files, f"staged ground_truth parquet missing for split {split!r}"
        for f in files:
            table = pq.read_table(f)
            parquet_n += table.num_rows
            parquet_splits[split] = parquet_splits.get(split, 0) + table.num_rows
            for rec in table.to_pylist():
                fn = rec["filename"]
                ref = jsonl_by_fn.get(fn)
                assert ref is not None, (
                    f"{fn}: staged parquet row has no hardened JSONL counterpart"
                )
                assert str(rec.get("split") or "") == str(ref.get("split") or ""), (
                    f"{fn}: parquet split {rec.get('split')!r} != JSONL split "
                    f"{ref.get('split')!r}"
                )
                raw = rec.get("gt_fields")
                parquet_gt = (
                    json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
                )
                jsonl_gt = ref.get("gt_fields") or {}
                assert set(parquet_gt) == set(jsonl_gt), (
                    f"{fn}: gt_fields key set diverges between staged parquet and JSONL"
                )
                for key in sorted(parquet_gt):
                    a = _canonical_gt_value(parquet_gt.get(key))
                    b = _canonical_gt_value(jsonl_gt.get(key))
                    assert a == b, (
                        f"{fn}: gt_fields[{key!r}] diverges between staged parquet "
                        f"and JSONL ({a[:60]!r} != {b[:60]!r}) — STOP"
                    )
                if ref.get("expected") == "corporate_record":
                    corporate += 1
    assert parquet_n == len(rows), (
        f"row count drift: staged parquet {parquet_n} != JSONL {len(rows)}"
    )
    # Split counts must match the hardened JSONL exactly (the corpus law
    # 2979/323 itself is verify_row_set's business; here the two payloads
    # must agree split-for-split).
    assert parquet_splits == jsonl_splits, (
        f"split counts drifted vs the JSONL: {parquet_splits} != {jsonl_splits}"
    )
    jsonl_corporate = sum(1 for r in rows if r.get("expected") == "corporate_record")
    assert corporate == jsonl_corporate, (
        f"corporate row count drifted in parquet: {corporate} != {jsonl_corporate}"
    )
    stats = {"rows": parquet_n, "splits": parquet_splits, "corporate": corporate}
    print(
        f"verify_staged_parquet_gt_fields OK — {parquet_n:,} staged ground_truth "
        f"parquet rows (splits {parquet_splits}, corporate {corporate}) match the "
        f"hardened JSONL gt_fields field-by-field"
    )
    return stats


# ---------------------------------------------------------------------------
# manifest + card
# ---------------------------------------------------------------------------

def render_manifest_v9_3(old_manifest: str, sha_updates: dict[str, str], built_utc: str) -> str:
    """Rewrite the manifest header: ``revision`` line, NEW ``labels_version``
    line, refresh ``built_utc``; merge the sha table. Only the header lines
    this revision exists to change are touched (the dataset ``version`` line
    and the README/manifest parquet-config rows stay as-is — no new configs
    in this revision)."""
    lines = old_manifest.splitlines()
    sha_idx = next(i for i, ln in enumerate(lines) if ln.strip().rstrip(":").strip() == "sha256")
    header = lines[:sha_idx]
    old_shas: dict[str, str] = {}
    for ln in lines[sha_idx + 1 :]:
        m = re.match(r"^\s*(\S+)\s+([0-9a-f]{64})\s*$", ln)
        if m:
            old_shas[m.group(1)] = m.group(2)
    merged_shas = {**old_shas, **sha_updates}

    header = [
        ln for ln in header
        if not ln.startswith("built_utc")
        and not ln.startswith("revision   :")
        and not ln.startswith("labels_version")
    ]
    for i, ln in enumerate(header):
        if ln.startswith("split_rule"):
            header = header[: i + 1] + [
                f"built_utc  : {built_utc}",
                f"revision   : {REVISION_LINE}",
                f"labels_version: {LABELS_VERSION} "
                f"(corporate gt_presence per-subclass entity regime from "
                f"llm_dojo_scoring.corporate_record_schema; label-only — row set / "
                f"identity / content unchanged, populated values unchanged)",
            ] + header[i + 1 :]
            break
    return "\n".join(
        header + ["sha256     :", *[f"  {k}  {v}" for k, v in sorted(merged_shas.items())], ""]
    )


def render_readme_addendum(n_rows: int, corporate_code_counts: dict[str, int]) -> str:
    """The v9.3 card section. The presence-code vocabulary is VERBATIM from
    the dojo docs (``llm-dojo-scoring/docs/CORPORATE_RECORD_SCHEMA.md``,
    "Scoring rule — unlabeled vs. absent" + ``gt_metadata``'s value list,
    including the fifth scorer-only unstated-blank regime)."""
    count_lines = "\n".join(
        f"  | `{code}` | {corporate_code_counts.get(code, 0):,} |"
        for code in PRESENCE_CODES
    )
    return (
        f"## v9.3 corporate presence-layer labels (2026-10-17)\n\n"
        f"Label-only revision over v9.2.1 ({n_rows:,} rows; row set / identity / content "
        f"unchanged). The manifest now versions labels separately (`labels_version`) from "
        f"the dataset (`version`, unchanged — v9 family), so a label wave that does not "
        f"change rows no longer rides the dataset version. Manifest revision line now "
        f"reads: `{REVISION_LABEL}`.\n\n"
        f"The published `gt_presence` vocabulary (verbatim from the corporate-record "
        f"schema docs, `llm-dojo-scoring/docs/CORPORATE_RECORD_SCHEMA.md`):\n\n"
        f"> The four published codes — `populated` / `not_applicable` / "
        f"`schema_documented_absence` / `pending_annotation` — plus a fifth, scorer-only "
        f"*unstated-blank ⇒ unlabeled* regime. `gt_presence` values are: **populated** — "
        f"a real expected value for this document; **not_applicable** — the class never "
        f"carries this field; **schema_documented_absence** — the field exists in the "
        f"class schema but this document legitimately has none (e.g. `denial_reasons` on "
        f"an approved claim, `adjuster` on CMS rows); **pending_annotation** — the label "
        f"backfill has not run yet (the current corpus has 91 contract rows with "
        f"`cuad_clause_labels` in this state). A key-entity field that the dataset leaves "
        f"blank *without* documenting absence is **unlabeled**: it is excluded from both "
        f"sides of the comparison (reported as `unlabeled_fields` on the score payload), "
        f"so neither a filled nor an empty prediction is scored. On published rows that "
        f"regime is expressed as an *absent key* — unlabeled cells are never shipped as "
        f"a code.\n\n"
        f"Corporate rows now carry the per-subclass entity keys "
        f"({EXPECTED_CORPORATE_ENTITY_KEYS} registry keys = 6 core + per-subclass "
        f"extension, mirrored from "
        f"`llm_dojo_scoring.corporate_record_schema.SUBCLASS_SCHEMAS`). Presence codes "
        f"shipped on the {EXPECTED_CORPORATE_ROWS} corporate rows:\n\n"
        f"  | code | cells |\n  |---|---:|\n{count_lines}\n\n"
        f"`entity_name` / `record_type` pins are exactly the v9.2.1 relabel's derivations "
        f"from the row itself (EDGAR filer metadata; subclass map); no new label facts "
        f"were added in v9.3. Extension entities stay unlabeled until real annotation "
        f"runs; a stray extraction of them is never a spurious fill. Plan: "
        f"`{PLAN_REF}`.\n\n"
    )


def build_all(
    stage_dir: Path = STAGE_DIR_DEFAULT,
    *,
    branch_dir: Path | None = None,
    base_path: str | None = None,
    force_download: bool = False,
    publish: bool = False,
) -> dict:
    """Stage the v9.3 payload and run every revision gate.

    ``publish`` exists only as a deliberate gate that RAISES for a staging
    run — promoting the label content is a separate, explicit act (see the
    plan doc); nothing in this module ever uploads implicitly."""
    if publish:
        raise SystemExit(
            "v9.3 is a STAGING revision: publishing is deliberately gated off — "
            f"promote via the branch/runbook, not this flag (see {PLAN_REF})"
        )
    if branch_dir is None:
        branch_dir = ensure_branch_snapshot(force=force_download)
    branch_dir = Path(branch_dir)
    base_file = resolve_base_path(base_path)

    base_rows = _load_jsonl(base_file)
    print(f"loaded: {len(base_rows)} v9.2 base rows from {base_file}")

    # Gate (a) first: the row set never changes, neither base nor relabeled.
    verify_row_set(base_rows)
    new_rows = relabel_all_rows(base_rows)
    verify_row_set(new_rows)

    branch_rows = _load_jsonl(branch_dir / "ground_truth_hardened.jsonl")
    verify_row_set(branch_rows)

    # Gates: base-relative allowlist + faithful-to-published content.
    verify_zero_drift(base_rows, new_rows)
    labels_stats = verify_labels_equal_branch(new_rows, branch_rows)
    raw_stats = verify_raw_line_equality(new_rows, branch_dir / "ground_truth_hardened.jsonl")
    presence_stats = verify_presence_laws(new_rows)

    # Stage: the label content byte-transfers; README + manifest re-render.
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    for name in (
        "ground_truth_hardened.jsonl", "bundles.jsonl", "streams.jsonl", "fixtures.jsonl",
    ):
        shutil.copyfile(branch_dir / name, stage_dir / name)
    shutil.copytree(branch_dir / "parquet", stage_dir / "parquet")

    staged_sha = sha256_file(stage_dir / "ground_truth_hardened.jsonl")
    assert staged_sha == BRANCH_SIDECAR_SHA256, staged_sha

    # Gate (g): the byte-copied ground_truth parquet carries exactly the
    # hardened JSONL gt_fields (content, not container bytes).
    parquet_stats = verify_staged_parquet_gt_fields(new_rows, stage_dir)

    old_card = (branch_dir / "README.md").read_text(encoding="utf-8")
    addendum = render_readme_addendum(len(new_rows), presence_stats["corporate_codes"])
    new_card = upsert_readme_addendum(old_card, addendum)
    (stage_dir / "README.md").write_text(new_card, encoding="utf-8")

    # Gate (f): regenerate the manifest from the staged bytes themselves.
    sha_updates = {
        str(p.relative_to(stage_dir)): sha256_file(p)
        for p in sorted(stage_dir.rglob("*"))
        if p.is_file() and p.name != "manifest.txt"
    }
    built_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_manifest = (branch_dir / "manifest.txt").read_text(encoding="utf-8")
    new_manifest = render_manifest_v9_3(old_manifest, sha_updates, built_utc)
    (stage_dir / "manifest.txt").write_text(new_manifest, encoding="utf-8")
    _verify_manifest_sha_table(stage_dir)

    result = {
        "status": "staged",
        "stage_dir": str(stage_dir),
        "labels_version": LABELS_VERSION,
        "row_set": {"rows": len(new_rows), "splits": EXPECTED_SPLIT_COUNTS,
                    "corporate": EXPECTED_CORPORATE_ROWS},
        "labels_stats": labels_stats,
        "raw_line_stats": raw_stats,
        "presence_stats": presence_stats,
        "staged_parquet_stats": parquet_stats,
    }
    print(f"staged v9.3 payload under {stage_dir} (labels: {LABELS_VERSION})")
    return result


def _verify_manifest_sha_table(stage_dir: Path) -> None:
    """Gate (f), verdict: every sha256 row in the staged manifest recomputes
    from the staged bytes themselves (a regeneration, not a copy-paste)."""
    manifest = (stage_dir / "manifest.txt").read_text(encoding="utf-8")
    table = re.findall(r"^\s*(\S+)\s+([0-9a-f]{64})\s*$", manifest, flags=re.M)
    assert table, "manifest sha table missing"
    for rel, sha in table:
        p = stage_dir / rel
        assert p.is_file(), f"manifest lists missing file {rel}"
        got = sha256_file(p)
        assert got == sha, f"{rel}: manifest sha {sha} != recomputed {got}"
    assert f"labels_version: {LABELS_VERSION}" in manifest, "labels_version line missing"
    assert manifest.count("labels_version") == 1, "labels_version duplicated"
    print(f"verify_manifest_sha_table OK — {len(table)} entries recompute from staged bytes")


def main() -> int:
    parser = argparse.ArgumentParser(description="v9.3 corporate presence-layer staging build")
    parser.add_argument("--stage-dir", type=Path, default=STAGE_DIR_DEFAULT)
    parser.add_argument("--branch-dir", type=Path, default=None,
                        help="verified corporate-labels-v9.2.1 snapshot dir (default: download)")
    parser.add_argument("--base-path", default=None,
                        help="explicit sha-verified v9.2 base sidecar (skips the Hub download)")
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args()
    result = build_all(
        args.stage_dir,
        branch_dir=args.branch_dir,
        base_path=args.base_path,
        force_download=args.force_download,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
