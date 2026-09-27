"""v9.1 quality revision — LLM-safe formatting pass (mailroom-issues#196,
Phase B: "Modal + LLM-ingestion formatting").

This module fixes the concrete, code-level portion of the open data-quality
epic that does NOT require new document sourcing or Modal/GPU/LLM
credentials (Phase A — the EX-10 CUAD clause-annotation pass — stays out of
scope; it is tracked separately and is `pending_annotation`, not silently
absent, as of this revision):

- **B1 (weak-indirect signal removal)** — ``metadata.clause_count`` and
  ``metadata.maud_label_count`` rode the blind ``default`` config on every
  contract/merger_agreement row. Both are a direct function of the (hidden)
  clause-label GT — a non-zero count leaks "this document has labeled
  clauses" and a rough magnitude to anything scoring the blind config. They
  are moved into ``gt_fields`` (GT-only) and dropped from blind metadata.
- **B2 (row-level GT presence codes)** — every corpus scorer had to
  reimplement its own "is this field a real gap or a documented absence"
  heuristic (see the pre-revision duplication between
  ``scripts/audit/coverage_matrix.py`` and ad-hoc mirror-side logic
  referenced by mailroom-issues#196). This revision publishes the single,
  closed-vocabulary classification (``mailroom_eda.gt_presence``) as a new
  ``gt_fields.gt_presence`` JSON column so every consumer reads the same
  codes instead of re-deriving them: ``populated`` / ``schema_documented_absence``
  / ``pending_annotation`` / ``not_applicable`` — and asserts, at publish
  time, that **zero** rows are ``genuine_gap`` (a genuine gap must never
  ship silently).
- **B4 (context-window budget bands)** — ``gt_fields.token_estimate`` and
  ``gt_fields.context_window_band`` (``<=4k`` / ``<=16k`` / ``<=32k`` /
  ``>32k``) let evaluation harnesses filter/report by context budget without
  recomputing the heuristic per-consumer.

Zero-drift law (per repo convention, mirroring ``v9_build.verify_v8_drift``):
every column this revision does not exist to change — ``document_id``,
``content_sha256``, ``doc_text``, every identity/eval-contract/matter
column, every pre-existing ``gt_fields``/``metadata`` key — must be
byte-identical before and after. Only ``metadata.{clause_count,
maud_label_count}`` (removed) and ``gt_fields.{clause_count,
maud_label_count,gt_presence,token_estimate,context_window_band}`` (added)
may differ. ``bundles``/``streams``/``fixtures`` are untouched and are never
re-staged or re-uploaded by this revision.
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

from .config import CHARS_PER_TOKEN, DATA_DIR, REPO_ID
from .dataset_export import normalize_metadata_rows, safe_jsonl_line
from .gt_presence import (
    ALL_GT_FIELDS,
    CONTEXT_WINDOW_BANDS,
    CONTEXT_WINDOW_OVERFLOW,
    PRESENCE_CODES,
    context_window_band,
    row_gt_presence,
    strip_weak_indirect_signals,
)
from .release_sections import CONTRACT_FIELDS, IDENTITY_FIELDS, MATTER_LISTS, MATTER_SCALARS

V9_1_VERSION = "v9.1"
ISSUE_REF = "mailroom-issues#196"

LIVE_SNAPSHOT_DIR = DATA_DIR / "v9_1" / "snapshot"
STAGE_DIR_DEFAULT = DATA_DIR / "v9_1" / "stage"

# This revision reads/rewrites ONLY default + ground_truth + the hardened
# sidecar + the card/manifest; bundles/streams/fixtures are pulled read-only
# (never restaged) so nothing here can accidentally touch them.
ALLOW_PATTERNS = [
    "parquet/default/*",
    "parquet/ground_truth/*",
    "ground_truth_hardened.jsonl",
    "README.md",
    "manifest.txt",
]

# The fixed 36-column `ground_truth` schema (hardened.stage_configs' §84
# column set) — single-sourced from release_sections, never re-declared.
GT_BASE_FIELDS = (
    "filename", "prompt", "expected", "expected_subclass", "split",
    "gt_fields", "_published",
)
GT_ROW_SCHEMA_FIELDS = (
    GT_BASE_FIELDS + IDENTITY_FIELDS + CONTRACT_FIELDS + MATTER_SCALARS + MATTER_LISTS
)

WEAK_INDIRECT_KEYS = ("clause_count", "maud_label_count")


def download_live_snapshot(force: bool = False) -> Path:
    """Snapshot-download the LIVE (unpinned) Hub tip.

    A *revision* must operate on the currently published state, never the
    historical ``config.REPO_REVISION`` pin the P0 EDA pipeline uses for
    reproducible reads — so this intentionally omits ``revision=``.
    """
    from huggingface_hub import snapshot_download

    marker = LIVE_SNAPSHOT_DIR / "ground_truth_hardened.jsonl"
    if marker.exists() and not force:
        return LIVE_SNAPSHOT_DIR
    if LIVE_SNAPSHOT_DIR.exists():
        shutil.rmtree(LIVE_SNAPSHOT_DIR)
    LIVE_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        allow_patterns=ALLOW_PATTERNS,
        local_dir=LIVE_SNAPSHOT_DIR,
    )
    return LIVE_SNAPSHOT_DIR


def _load_matter_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return [str(x) for x in parsed] if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return [str(x) for x in v]


def load_live_merged_rows(snapshot_dir: Path = LIVE_SNAPSHOT_DIR) -> list[dict]:
    """Join `ground_truth` (all 36 cols) + `default` (doc_text/metadata) by
    filename — the row shape the parquet stagers below consume."""
    import pandas as pd

    gt = pd.concat(
        [
            pd.read_parquet(f)
            for split in ("train", "test")
            for f in sorted((snapshot_dir / "parquet" / "ground_truth" / split).glob("*.parquet"))
        ],
        ignore_index=True,
    )
    blind = pd.concat(
        [
            pd.read_parquet(f)
            for split in ("train", "test")
            for f in sorted((snapshot_dir / "parquet" / "default" / split).glob("*.parquet"))
        ],
        ignore_index=True,
    )
    doc_by_fn = dict(zip(blind["filename"], blind["doc_text"]))
    md_by_fn = dict(zip(blind["filename"], blind["metadata"]))

    rows = gt.to_dict("records")
    for r in rows:
        fn = r["filename"]
        r["doc_text"] = str(doc_by_fn.get(fn, ""))
        r["metadata"] = dict(md_by_fn.get(fn) or {})
        raw_gt = r.get("gt_fields")
        if isinstance(raw_gt, str) and raw_gt.strip():
            r["gt_fields"] = json.loads(raw_gt)
        elif not isinstance(raw_gt, dict):
            r["gt_fields"] = {}
        for lk in MATTER_LISTS:
            r[lk] = _load_matter_list(r.get(lk))
    return rows


def load_live_sidecar_rows(snapshot_dir: Path = LIVE_SNAPSHOT_DIR) -> list[dict]:
    """Load the published `ground_truth_hardened.jsonl` sidecar (native
    dict `gt_fields`/`metadata` — JSONL preserves them, unlike parquet)."""
    rows = []
    with (snapshot_dir / "ground_truth_hardened.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def revise_row(row: dict) -> dict:
    """Apply the B1/B2/B4 transforms to one row. Returns a fresh dict; the
    input is never mutated. Every field this revision does not exist to
    touch is copied through verbatim."""
    out = dict(row)
    gt = dict(row.get("gt_fields") or {})
    metadata = dict(row.get("metadata") or {})
    doc_text = str(row.get("doc_text") or "")

    # B1: weak-indirect signals move OUT of blind metadata, INTO GT-only
    # fields (they are a direct function of the hidden clause labels).
    cleaned_metadata, extracted = strip_weak_indirect_signals(metadata)
    for key in WEAK_INDIRECT_KEYS:
        gt[key] = extracted.get(key, "")

    # B4: token-budget bands.
    token_estimate = int(len(doc_text) / CHARS_PER_TOKEN)
    gt["token_estimate"] = str(token_estimate)
    gt["context_window_band"] = context_window_band(token_estimate)

    # B2: row-level GT presence codes — computed AFTER B1/B4 so the codes
    # reflect the fields as they will actually ship (classify_field only
    # reads FIELD_KEYS_BY_CLASS keys + doc_text/metadata, so gt_presence /
    # token_estimate / context_window_band never self-reference).
    presence_probe = dict(gt)
    presence_probe["expected"] = row.get("expected")
    presence_probe["doc_text"] = doc_text
    presence_probe["metadata"] = cleaned_metadata
    presence = row_gt_presence(presence_probe)
    gt["gt_presence"] = json.dumps(presence, sort_keys=True, ensure_ascii=False)

    out["gt_fields"] = gt
    out["metadata"] = cleaned_metadata
    return out


def revise_rows(rows: list[dict]) -> list[dict]:
    return [revise_row(r) for r in rows]


def verify_zero_drift(old_rows: list[dict], new_rows: list[dict]) -> None:
    """Every field except the ones this revision exists to change must be
    byte-identical old vs new (mirrors ``v9_build.verify_v8_drift``)."""
    assert len(old_rows) == len(new_rows), f"{len(old_rows)} != {len(new_rows)}"
    old_by_fn = {r["filename"]: r for r in old_rows}
    assert len(old_by_fn) == len(old_rows), "duplicate filename in old rows"

    for new in new_rows:
        fn = new["filename"]
        old = old_by_fn.get(fn)
        assert old is not None, f"{fn}: not present in old rows (row-set drift)"

        for col in old:
            if col in ("gt_fields", "metadata"):
                continue
            assert old.get(col) == new.get(col), f"{fn}: {col!r} drifted"

        old_gt, new_gt = (old.get("gt_fields") or {}), (new.get("gt_fields") or {})
        for k, v in old_gt.items():
            if k in WEAK_INDIRECT_KEYS:
                continue  # deliberately (re)populated by this revision
            assert new_gt.get(k) == v, f"{fn}: gt_fields[{k!r}] drifted"

        old_md, new_md = (old.get("metadata") or {}), (new.get("metadata") or {})
        for k in WEAK_INDIRECT_KEYS:
            assert k not in new_md, f"{fn}: metadata[{k!r}] not stripped"
        for k, v in old_md.items():
            if k in WEAK_INDIRECT_KEYS:
                continue
            assert new_md.get(k) == v, f"{fn}: metadata[{k!r}] drifted"
    print(f"verify_zero_drift OK — {len(new_rows)} rows, 0 identity/content drift")


def verify_presence_codes(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {c: 0 for c in PRESENCE_CODES}
    for row in rows:
        gt = row.get("gt_fields") or {}
        presence = json.loads(gt.get("gt_presence") or "{}")
        assert set(presence) == set(ALL_GT_FIELDS), f"{row['filename']}: presence key set mismatch"
        for code in presence.values():
            assert code in PRESENCE_CODES, f"{row['filename']}: unknown presence code {code!r}"
            assert code != "genuine_gap", f"{row['filename']}: genuine_gap must never ship"
            counts[code] += 1
    print(f"verify_presence_codes OK — {counts}")
    return counts


def verify_context_bands(rows: list[dict]) -> dict[str, int]:
    allowed = {label for _, label in CONTEXT_WINDOW_BANDS} | {CONTEXT_WINDOW_OVERFLOW}
    counts: dict[str, int] = {label: 0 for label in allowed}
    for row in rows:
        gt = row.get("gt_fields") or {}
        band = gt.get("context_window_band")
        assert band in allowed, f"{row['filename']}: bad context_window_band {band!r}"
        assert str(gt.get("token_estimate", "")).isdigit(), f"{row['filename']}: bad token_estimate"
        md = row.get("metadata") or {}
        for k in WEAK_INDIRECT_KEYS:
            assert k not in md, f"{row['filename']}: metadata still carries {k!r}"
        counts[band] += 1
    print(f"verify_context_bands OK — {len(rows)} rows, weak-indirect signals stripped; bands {counts}")
    return counts


def stage_default_and_ground_truth(rows: list[dict], stage_dir: Path) -> dict[tuple[str, str], int]:
    """Rebuild ``parquet/default`` (blind, 4 cols) + ``parquet/ground_truth``
    (36 cols) from revised rows. Schemas are single-sourced from
    ``release_sections`` / the v7-era blind shape — never re-declared."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    counts: dict[tuple[str, str], int] = {}
    gt_schema = pa.schema(
        [
            pa.field(name, pa.list_(pa.string()) if name in MATTER_LISTS else pa.string())
            for name in GT_ROW_SCHEMA_FIELDS
        ]
    )
    for split in ("train", "test"):
        subset = [r for r in rows if r["split"] == split]

        blind = [
            {
                "filename": r["filename"],
                "doc_text": r["doc_text"],
                "prompt": r.get("prompt") or "",
                "metadata": dict(r.get("metadata") or {}),
            }
            for r in subset
        ]
        normalize_metadata_rows(blind)
        bdir = stage_dir / "parquet" / "default" / split
        bdir.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(blind), bdir / f"{split}-00000-of-00001.parquet")
        counts[("default", split)] = len(blind)

        gt_rows = []
        for r in subset:
            rec = {}
            for name in GT_ROW_SCHEMA_FIELDS:
                if name in MATTER_LISTS:
                    rec[name] = [str(x) for x in (r.get(name) or [])]
                elif name == "gt_fields":
                    rec[name] = json.dumps(r.get("gt_fields") or {}, ensure_ascii=False, sort_keys=True)
                else:
                    v = r.get(name)
                    rec[name] = "" if v is None else str(v)
            gt_rows.append(rec)
        gdir = stage_dir / "parquet" / "ground_truth" / split
        gdir.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pylist(gt_rows, schema=gt_schema),
            gdir / f"{split}-00000-of-00001.parquet",
        )
        counts[("ground_truth", split)] = len(gt_rows)
    return counts


def verify_blind_label_free(stage_dir: Path) -> None:
    import pandas as pd

    for split in ("train", "test"):
        for f in sorted((stage_dir / "parquet" / "default" / split).glob("*.parquet")):
            df = pd.read_parquet(f)
            assert set(df.columns) == {"filename", "doc_text", "prompt", "metadata"}, \
                f"blind config leaked columns: {sorted(df.columns)}"
    print("verify_blind_label_free OK — blind config is exactly 4 label-free columns")


def write_hardened_jsonl(rows: list[dict], path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            clean = {k: v for k, v in row.items() if not k.startswith("_")}
            fh.write(safe_jsonl_line(clean) + "\n")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": str(path), "rows": len(rows), "sha256": sha}


def render_readme_addendum(
    presence_counts: dict[str, int], band_counts: dict[str, int], n_rows: int
) -> str:
    heading = f"## {V9_1_VERSION} quality revision ({ISSUE_REF} Phase B)"
    total_presence = sum(presence_counts.values())
    presence_lines = "\n".join(
        f"  | `{code}` | {presence_counts.get(code, 0):,} | "
        f"{100 * presence_counts.get(code, 0) / total_presence:.1f}% |"
        for code in PRESENCE_CODES
    )
    band_lines = "\n".join(
        f"  | `{label}` | {band_counts.get(label, 0):,} |"
        for label in [lbl for _, lbl in CONTEXT_WINDOW_BANDS] + [CONTEXT_WINDOW_OVERFLOW]
    )
    return (
        f"{heading}\n\n"
        f"Code-only revision (no new document sourcing; {n_rows:,} rows, "
        f"zero identity/content drift — `document_id` / `content_sha256` / "
        f"`doc_text` unchanged on every row). Addresses the LLM-safe-"
        f"formatting portion of {ISSUE_REF}:\n\n"
        f"- **B1 — weak-indirect signal removal**: `metadata.clause_count` "
        f"and `metadata.maud_label_count` (a direct function of the hidden "
        f"clause-label GT — non-zero leaks \"this doc has labeled clauses\" "
        f"to anything scoring the blind config) are removed from the "
        f"`default` config and now live GT-only, in "
        f"`gt_fields.clause_count` / `gt_fields.maud_label_count`.\n"
        f"- **B2 — row-level GT presence codes**: every row's `gt_fields` "
        f"gained a `gt_presence` JSON column — one of `populated` / "
        f"`schema_documented_absence` / `pending_annotation` / "
        f"`not_applicable` per label-bearing field (closed vocabulary; "
        f"`mailroom_eda.gt_presence`, single-sourced with "
        f"`scripts/audit/coverage_matrix.py`). **Zero rows are "
        f"`genuine_gap`** — verified at publish time.\n\n"
        f"  | presence code | fields | share |\n  |---|---:|---:|\n{presence_lines}\n\n"
        f"  Note: `contract.cuad_clause_labels` on the 91 SEC EDGAR EX-10 "
        f"rows is `pending_annotation` (blocked on the CUAD-clause LLM pass, "
        f"{ISSUE_REF} Phase A) — no longer a silently-empty `{{}}`.\n"
        f"- **B4 — context-window budget bands**: `gt_fields.token_estimate` "
        f"and `gt_fields.context_window_band` (`<=4k` / `<=16k` / `<=32k` / "
        f"`>32k`) let evaluation harnesses filter by context budget without "
        f"recomputing the heuristic.\n\n"
        f"  | band | rows |\n  |---|---:|\n{band_lines}\n\n"
    )


def upsert_readme_addendum(card: str, addendum: str) -> str:
    from .docclass_uploader import upsert_section

    heading = addendum.split("\n", 1)[0]
    if heading in card:
        start = card.find(heading)
        nxt = card.find("\n## ", start + 1)
        end = len(card) if nxt < 0 else nxt + 1
        return card[:start] + addendum + card[end:]
    sep = "" if card.endswith("\n\n") else ("\n" if card.endswith("\n") else "\n\n")
    return card + sep + addendum


def render_manifest_v9_1(old_manifest: str, sha_updates: dict[str, str], built_utc: str) -> str:
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
        if not ln.startswith("built_utc") and not ln.startswith("revision   :")
    ]
    for i, ln in enumerate(header):
        if ln.startswith("split_rule"):
            header = header[: i + 1] + [
                f"built_utc  : {built_utc}",
                f"revision   : {V9_1_VERSION} quality revision ({ISSUE_REF} Phase B — "
                f"LLM-safe formatting: weak-indirect-signal removal, GT presence codes, "
                f"context-window bands; row set / identity / content unchanged)",
            ] + header[i + 1 :]
            break
    return "\n".join(header + ["sha256     :", *[f"  {k}  {v}" for k, v in sorted(merged_shas.items())], ""])


def build_all(
    stage_dir: Path = STAGE_DIR_DEFAULT,
    *,
    force_download: bool = False,
    publish: bool = False,
    commit_message: str = "",
) -> dict:
    snapshot_dir = download_live_snapshot(force=force_download)

    old_merged = load_live_merged_rows(snapshot_dir)
    old_sidecar = load_live_sidecar_rows(snapshot_dir)
    print(f"loaded live snapshot: {len(old_merged)} GT rows, {len(old_sidecar)} sidecar rows")

    new_merged = revise_rows(old_merged)
    new_sidecar = revise_rows(old_sidecar)

    verify_zero_drift(old_merged, new_merged)
    verify_zero_drift(old_sidecar, new_sidecar)

    presence_counts = verify_presence_codes(new_merged)
    band_counts = verify_context_bands(new_merged)

    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    counts = stage_default_and_ground_truth(new_merged, stage_dir)
    verify_blind_label_free(stage_dir)

    jsonl_stats = write_hardened_jsonl(new_sidecar, stage_dir / "ground_truth_hardened.jsonl")

    old_card = (snapshot_dir / "README.md").read_text(encoding="utf-8")
    addendum = render_readme_addendum(presence_counts, band_counts, len(new_merged))
    new_card = upsert_readme_addendum(old_card, addendum)
    (stage_dir / "README.md").write_text(new_card, encoding="utf-8")

    sha_updates = {
        "README.md": hashlib.sha256(new_card.encode("utf-8")).hexdigest(),
        "ground_truth_hardened.jsonl": jsonl_stats["sha256"],
    }
    for cfg in ("default", "ground_truth"):
        for split in ("train", "test"):
            p = stage_dir / "parquet" / cfg / split / f"{split}-00000-of-00001.parquet"
            sha_updates[str(p.relative_to(stage_dir))] = hashlib.sha256(p.read_bytes()).hexdigest()

    built_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_manifest = (snapshot_dir / "manifest.txt").read_text(encoding="utf-8")
    new_manifest = render_manifest_v9_1(old_manifest, sha_updates, built_utc)
    (stage_dir / "manifest.txt").write_text(new_manifest, encoding="utf-8")

    result = {
        "status": "staged",
        "stage_dir": str(stage_dir),
        "counts": counts,
        "presence_counts": presence_counts,
        "band_counts": band_counts,
    }
    print(f"staged {sum(counts.values())} rows across {len(counts)} config/split shards under {stage_dir}")

    if publish:
        from .hf_interface import get_hf_api, upload_folder

        api = get_hf_api()
        upload_folder(
            api,
            stage_dir,
            REPO_ID,
            commit_message or (
                f"{V9_1_VERSION} quality revision ({ISSUE_REF} Phase B): strip weak-indirect "
                f"metadata signals, publish row-level GT presence codes + context-window bands"
            ),
            allow_patterns=[
                "parquet/default/*",
                "parquet/ground_truth/*",
                "ground_truth_hardened.jsonl",
                "README.md",
                "manifest.txt",
            ],
        )
        result["status"] = "published"
        result["repo"] = f"https://huggingface.co/datasets/{REPO_ID}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-dir", type=Path, default=STAGE_DIR_DEFAULT)
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--commit-message", default="")
    args = parser.parse_args()
    result = build_all(
        args.stage_dir,
        force_download=args.force_download,
        publish=args.publish,
        commit_message=args.commit_message,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
