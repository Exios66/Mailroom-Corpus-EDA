"""v9.2 relations & provenance revision (code-only, over the live v9.1 data
commit).

Plan: ``docs/plans/v9.2-relations-provenance-revision.md``. This module is
the shippable Workstream C of that plan (C1–C5); Workstream S
(bundles/streams/fixtures column completion) is deferred to v9.3 per D8 and
is NOT implemented here.

What it fixes (all verified against the live Hub tip ``bc9eab28`` on
2026-10-05):

- **C1 — annotation provenance (P1 correctness).** ``eval_contract`` read a
  *top-level* ``intent_source``, but v9/v9.1 nest it inside ``gt_fields`` —
  so the published ``ground_truth`` config shipped only ``source_native`` +
  ``synthetic`` instead of the six §20 regimes. This revision recomputes the
  seven ``annotation_*`` columns from the nested reader and aligns
  ``annotation_source`` to the published ``source_corpus`` (EDGAR EX-10 rows
  become ``sec_edgar``).
- **C2 — build-internal ``_published`` removal.** The v8→v9 migration map
  leaked into the published GT parquet on 2,000 rows. Dropped, and a
  ``_``-prefix filter in ``hardened.stage_configs``/``verify_stage`` keeps it
  from ever returning.
- **C3 — relation producer + mirror SSOT.** ``related_document_ids`` gains a
  producer (all-pairs ``document_id`` targets on the 9 heuristic matter
  threads); the nested ``gt_fields.relationships`` /
  ``gt_fields.related_document_ids`` mirrors become canonical copies written
  by ``matter.enrich_rows`` (the permanent-``"[]"`` split-brain is gone).
- **C4 — ``source_revision`` override gate.** EDGAR EX-10 contracts join the
  LOB insurance rows in copying ``metadata.source_revision`` where the source
  is that feeder's own draw. Zero live effect today (no EDGAR row carries a
  feeder revision); forward-proofs the field.
- **C5 — GT registry single-sourcing.** ``release_sections.GT_SCALAR_KEYS``
  (32) / ``LEGACY_V7_GT_KEYS`` (27) replace the three stale inline 27-key
  lists so a rebuild can never silently drop the five v9.1 keys.

Zero-drift law: ``document_id``, ``content_sha256``,
``normalized_text_sha256``, ``doc_text``, every metadata key, and every
non-enumerated GT column are byte-identical before/after. Only the seven
``annotation_*`` columns, the two matter lists (``relationships`` /
``related_document_ids``, top-level and nested), and the removed
``_published`` may differ. ``bundles`` / ``streams`` / ``fixtures`` are read
read-only and never restaged by this revision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import eval_contract as ec
from . import matter
from .config import DATA_DIR, REPO_ID
from .dataset_export import normalize_metadata_rows, safe_jsonl_line
from .release_sections import (
    CONTRACT_FIELDS,
    IDENTITY_FIELDS,
    MATTER_LISTS,
    MATTER_SCALARS,
)
from .v9_1_revision import (
    _load_matter_list,
    load_live_merged_rows,
    load_live_sidecar_rows,
    upsert_readme_addendum,
    write_hardened_jsonl,
)

V9_2_VERSION = "v9.2"
PLAN_REF = "docs/plans/v9.2-relations-provenance-revision.md"

SNAPSHOT_DIR = DATA_DIR / "v9_2" / "snapshot"
STAGE_DIR_DEFAULT = DATA_DIR / "v9_2" / "stage"

# Workstream C reads/rewrites ONLY default + ground_truth + the hardened
# sidecar + the card/manifest; bundles/streams/fixtures are pulled read-only.
ALLOW_PATTERNS = [
    "parquet/default/*",
    "parquet/ground_truth/*",
    "ground_truth_hardened.jsonl",
    "README.md",
    "manifest.txt",
]

# The fixed 35-column `ground_truth` schema = v9.1's 36 minus the removed,
# build-internal `_published`. Single-sourced from release_sections.
GT_BASE_FIELDS = (
    "filename", "prompt", "expected", "expected_subclass", "split", "gt_fields",
)
GT_ROW_SCHEMA_FIELDS = (
    GT_BASE_FIELDS + IDENTITY_FIELDS + CONTRACT_FIELDS + MATTER_SCALARS + MATTER_LISTS
)

REMOVED_FIELDS = ("_published",)
CHANGED_FIELDS = (
    "annotation_source", "annotation_method", "annotation_model",
    "annotation_prompt_version", "annotation_confidence", "annotation_reviewer",
    "annotation_timestamp", "relationships", "related_document_ids",
)

#: The six §20 regimes the published `ground_truth` config must carry after
#: C1 (verified live count — see plan §3 C1).
EXPECTED_REGIMES = {
    "verified_join": 162, "llm_zero_shot": 637, "human_annotated": 96,
    "synthetic": 1100, "heuristic": 646, "source_native": 661,
}


def download_live_snapshot(force: bool = False) -> Path:
    """Snapshot-download the LIVE (unpinned) Hub tip into ``SNAPSHOT_DIR``.

    A revision operates on the currently published state, never the
    historical ``config.REPO_REVISION`` pin the P0 EDA pipeline uses — so
    this intentionally omits ``revision=``. v9.1's downloader is hardwired to
    its own ``LIVE_SNAPSHOT_DIR``; this binds the same body to v9.2's dir.
    """
    from huggingface_hub import snapshot_download

    marker = SNAPSHOT_DIR / "ground_truth_hardened.jsonl"
    if marker.exists() and not force:
        return SNAPSHOT_DIR
    if SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        allow_patterns=ALLOW_PATTERNS,
        local_dir=SNAPSHOT_DIR,
    )
    return SNAPSHOT_DIR


def revise_row(row: dict) -> dict:
    """Apply C2 + C1 to one row. Returns a fresh dict; the input is never
    mutated. Every field this revision does not exist to change is copied
    through verbatim. (C3's mirror sync runs earlier, in ``matter.enrich_rows``.)"""
    out = dict(row)
    for key in REMOVED_FIELDS:
        out.pop(key, None)
    # never alias the caller's nested dict (zero-drift integrity + the
    # non-mutation contract): revise_row must be safe in isolation.
    if isinstance(out.get("gt_fields"), dict):
        out["gt_fields"] = dict(out["gt_fields"])
    provenance = ec.annotation_provenance(row)  # C1 nested read + source_corpus
    out["annotation_source"] = provenance["source"]
    out["annotation_method"] = provenance["method"]
    out["annotation_model"] = provenance["model"]
    out["annotation_prompt_version"] = provenance["prompt_version"]
    out["annotation_confidence"] = provenance["confidence"]
    out["annotation_reviewer"] = provenance["reviewer"]
    out["annotation_timestamp"] = provenance["timestamp"]
    return out


def revise_rows(rows: list[dict]) -> list[dict]:
    return [revise_row(r) for r in rows]


def verify_zero_drift(old_rows: list[dict], new_rows: list[dict]) -> None:
    """Every field except the enumerated changed/removed ones must be
    byte-identical old vs new (extends the v9.1 checker with the removal +
    mirror allowlists)."""
    assert len(old_rows) == len(new_rows), f"{len(old_rows)} != {len(new_rows)}"
    old_by_fn = {r["filename"]: r for r in old_rows}
    assert len(old_by_fn) == len(old_rows), "duplicate filename in old rows"

    def _norm(v: Any) -> Any:
        # the published GT surface is string-typed for scalars; the matter
        # producer emits native ints for thread_position/thread_size. Compare
        # canonical string forms so that is not mistaken for drift — a real
        # value change still fails.
        if v is None:
            return ""
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v]
        return str(v)

    for new in new_rows:
        fn = new["filename"]
        old = old_by_fn.get(fn)
        assert old is not None, f"{fn}: not present in old rows (row-set drift)"

        for col in old:
            if col in ("gt_fields", "metadata"):
                continue
            if col in REMOVED_FIELDS:            # absent in `new` by design
                continue
            if col in CHANGED_FIELDS:            # deliberate recompute
                continue
            assert _norm(old.get(col)) == _norm(new.get(col)), f"{fn}: {col!r} drifted"

        old_gt, new_gt = (old.get("gt_fields") or {}), (new.get("gt_fields") or {})
        for k, v in old_gt.items():
            if k in ("relationships", "related_document_ids"):
                continue                          # C3 mirror sync
            assert new_gt.get(k) == v, f"{fn}: gt_fields[{k!r}] drifted"
        for k in REMOVED_FIELDS:
            assert k not in new_gt, f"{fn}: gt_fields[{k!r}] not removed"

        old_md, new_md = (old.get("metadata") or {}), (new.get("metadata") or {})
        for k, v in old_md.items():
            assert new_md.get(k) == v, f"{fn}: metadata[{k!r}] drifted"
    print(f"verify_zero_drift OK — {len(new_rows)} rows, 0 identity/content drift")


def verify_annotation_regimes(rows: list[dict]) -> dict[str, int]:
    """C1: the published annotation_method must carry the six §20 regimes."""
    methods = Counter(str(r.get("annotation_method") or "") for r in rows)
    assert dict(methods) == EXPECTED_REGIMES, (
        f"annotation_method regimes diverged: {dict(methods)} != {EXPECTED_REGIMES}"
    )
    # annotation_source: EDGAR EX-10 rows align to sec_edgar (541 total =
    # 450 corporate_record + 91 contract); CUAD contracts drop to 509.
    sources = Counter(str(r.get("annotation_source") or "") for r in rows)
    assert sources.get("theatticusproject/cuad") == 509, sources.get("theatticusproject/cuad")
    assert sources.get("sec_edgar") == 541, sources.get("sec_edgar")
    # model/reviewer populated only on their regime rows.
    llm_models = {str(r.get("annotation_model") or "") for r in rows
                  if r.get("annotation_method") == "llm_zero_shot"}
    assert llm_models == {"deepseek-chat"}, llm_models
    human_reviewers = {str(r.get("annotation_reviewer") or "") for r in rows
                       if r.get("annotation_method") == "human_annotated"}
    assert human_reviewers == {"human"}, human_reviewers
    print(f"verify_annotation_regimes OK — {dict(methods)}; "
          f"annotation_source cuad=509 sec_edgar=541")
    return dict(methods)


def verify_relations(rows: list[dict]) -> dict[str, int]:
    """C3: relation targets are real document_ids, mirrors are canonical, and
    every matter member carries at least one target; symmetry holds."""
    known = {str(r.get("document_id") or "") for r in rows}
    matter_rows = [r for r in rows if str(r.get("matter_id") or "")]
    assert len(matter_rows) == 23, f"expected 23 heuristic matter rows, got {len(matter_rows)}"

    rel: dict[str, set[str]] = {}
    nonempty = 0
    for row in rows:
        targets = [str(x) for x in (row.get("related_document_ids") or [])]
        gt = row.get("gt_fields")
        if isinstance(gt, dict):
            assert json.loads(gt.get("relationships") or "[]") == \
                [str(x) for x in (row.get("relationships") or [])], row["filename"]
            assert json.loads(gt.get("related_document_ids") or "[]") == targets, row["filename"]
        if targets:
            nonempty += 1
            rel[str(row["document_id"])] = set(targets)

    for row in matter_rows:
        targets = [str(x) for x in (row.get("related_document_ids") or [])]
        assert targets, f"{row['filename']}: matter row carries no relation target"
        assert set(targets) <= known, f"{row['filename']}: target not a known document_id"
    for doc, targets in rel.items():
        for t in targets:
            assert doc in rel.get(t, set()), f"relation not symmetric: {doc} -> {t}"
    print(f"verify_relations OK — {len(matter_rows)} matter rows, "
          f"{nonempty} rows with targets, mirrors canonical, symmetric")
    return {"matter_rows": len(matter_rows), "rows_with_targets": nonempty}


def verify_parity(merged: list[dict], sidecar: list[dict]) -> None:
    """The sidecar is the byte-verified artifact — it must carry the same 35
    GT columns + nested gt_fields keys as the parquet config after JSON
    normalization."""
    side_by_fn = {r["filename"]: r for r in sidecar}
    assert len(side_by_fn) == len(sidecar), "duplicate filename in sidecar"
    for row in merged:
        s = side_by_fn.get(row["filename"])
        assert s is not None, f"{row['filename']}: missing from sidecar"
        for name in GT_ROW_SCHEMA_FIELDS:
            if name in ("gt_fields",):
                continue
            if name in MATTER_LISTS:
                a = [str(x) for x in (row.get(name) or [])]
                b = [str(x) for x in (s.get(name) or [])]
            else:
                a = "" if row.get(name) is None else str(row.get(name))
                b = "" if s.get(name) is None else str(s.get(name))
            assert a == b, f"{row['filename']}: {name!r} parquet/sidecar parity"
        a_gt = json.dumps(row.get("gt_fields") or {}, sort_keys=True, ensure_ascii=False)
        b_gt = json.dumps(s.get("gt_fields") or {}, sort_keys=True, ensure_ascii=False)
        assert a_gt == b_gt, f"{row['filename']}: gt_fields parquet/sidecar parity"
    print(f"verify_parity OK — {len(merged)} rows, 35 GT columns + gt_fields match sidecar")


def verify_source_revision_unchanged(old_rows: list[dict], new_rows: list[dict]) -> None:
    """C4: zero live drift — the override gate changes nothing on the current
    corpus (no EDGAR row carries metadata.source_revision)."""
    old = {r["filename"]: str(r.get("source_revision") or "") for r in old_rows}
    for r in new_rows:
        assert str(r.get("source_revision") or "") == old[r["filename"]], \
            f"{r['filename']}: source_revision drifted"
    print("verify_source_revision_unchanged OK — 0 live drift")


def stage_default_and_ground_truth(rows: list[dict], stage_dir: Path) -> dict[tuple[str, str], int]:
    """Rebuild ``parquet/default`` (blind, 4 cols) + ``parquet/ground_truth``
    (35 cols) from revised rows."""
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
        if blind:  # never write a schema-less shard for an empty split
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
        if gt_rows:
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


def render_readme_addendum(regime_counts: dict[str, int], relation_stats: dict[str, int],
                           n_rows: int) -> str:
    heading = f"## {V9_2_VERSION} relations & provenance revision (2026-10-05)"
    regime_lines = "\n".join(
        f"  | `{m}` | {regime_counts.get(m, 0):,} |" for m in EXPECTED_REGIMES
    )
    return (
        f"{heading}\n\n"
        f"Code-only revision (no new document sourcing; {n_rows:,} rows, "
        f"zero row-set/identity/content drift — `document_id` / "
        f"`content_sha256` / `doc_text` / every metadata key byte-identical). "
        f"Completes the relations & provenance layer of the §84 evaluation "
        f"contract:\n\n"
        f"- **provenance correction (C1)**: the published `annotation_method` "
        f"now carries the six §20 regimes — previously it shipped only "
        f"`source_native` + `synthetic` because the derivation read a "
        f"top-level `intent_source` that v9 nests inside `gt_fields`. "
        f"`annotation_source` is aligned to the published `source_corpus` "
        f"(the 91 SEC EDGAR EX-10 contracts move from "
        f"`theatticusproject/cuad` to `sec_edgar`; CUAD 600→509).\n\n"
        f"  | `annotation_method` | rows |\n  |---|---:|\n{regime_lines}\n\n"
        f"- **relations (C3)**: `related_document_ids` now has a producer — "
        f"the {relation_stats.get('matter_rows', 0)} heuristic matter rows "
        f"({relation_stats.get('rows_with_targets', 0)} rows carry targets) "
        f"get all-pairs `document_id` targets; the nested "
        f"`gt_fields.relationships` / `gt_fields.related_document_ids` "
        f"mirrors are now canonical copies (no more permanent `\"[]\"`).\n"
        f"- **build-internal cleanup (C2)**: removed the undocumented "
        f"`_published` (v8→v9 migration map) from the published "
        f"`ground_truth` parquet; the staging filter now rejects any "
        f"`_`-prefixed column so it cannot return.\n"
        f"- **registry single-sourcing (C5)**: the three stale 27-key GT "
        f"lists were replaced by `release_sections.GT_SCALAR_KEYS` (32) / "
        f"`LEGACY_V7_GT_KEYS` (27), so a rebuild can never silently drop the "
        f"five v9.1 keys.\n"
        f"- **`source_revision` gate (C4)**: EDGAR EX-10 contracts join the "
        f"insurance LOB rows in copying `metadata.source_revision` where the "
        f"source is that feeder's own draw (zero live effect today).\n\n"
        f"- schema: `ground_truth` now carries **35** top-level columns "
        f"(v9.1's 36 minus `_published`); the nested `gt_fields` union is "
        f"**34** keys (32 scalar + 2 matter lists).\n"
        f"- plan: `{PLAN_REF}`\n\n"
    )


#: Stale v8-inherited card text (plan C6.2) — the B1 fix made these keys
#: GT-only, so the blind-metadata warning is obsolete.
_V8_METADATA_BLOCK = (
    "> **v8-inherited metadata aggregates**: 509 v8 contract rows carry a\n"
    "> `clause_count` and 152 v8 merger rows a `maud_label_count` integer inside\n"
    "> their `metadata` blob (inherited verbatim from `mailroom-corpus` v8 to\n"
    "> preserve zero identity drift). These are label-*derived aggregate counts*,\n"
    "> not labels, and are not present in any v9 expansion row. Consumers that\n"
    "> require strict label-free metadata may treat them as weak indirect signals;\n"
    "> they cannot be stripped without violating the zero-drift mandate."
)
_V9_1_METADATA_BLOCK = (
    "> **v9.1 B1 — weak-indirect signals are GT-only**: `clause_count` and\n"
    "> `maud_label_count` are a direct function of the hidden clause-label GT, so\n"
    "> the v9.1 quality revision (mailroom-issues#196 Phase B) **removed them from\n"
    "> the blind `metadata` blob** and moved them into `gt_fields` (GT-only). The\n"
    "> blind `default` config no longer carries any label-derived aggregate; the\n"
    "> v9.2 revision recomputed the published provenance and relations surfaces on\n"
    "> top of that clean base (see the v9.2 section below)."
)
_OLD_CONFIG_ROW = (
    "| `ground_truth` | 3,302 | labels (27-key schema), identity/hashes, "
    "evaluation contract, matter/group |"
)
_NEW_CONFIG_ROW = (
    "| `ground_truth` | 3,302 | labels (`gt_fields` JSON: 34-key union — "
    "32 scalar + 2 matter lists), identity/hashes, evaluation contract, matter/group |"
)


def polish_card(card: str) -> str:
    """Replace the stale v8-inherited card text with the v9.1/v9.2 facts
    (plan C6.2). Idempotent: a card already carrying the new text is
    unchanged."""
    if _V8_METADATA_BLOCK in card:
        card = card.replace(_V8_METADATA_BLOCK, _V9_1_METADATA_BLOCK)
    if _OLD_CONFIG_ROW in card:
        card = card.replace(_OLD_CONFIG_ROW, _NEW_CONFIG_ROW)
    return card


def render_manifest_v9_2(old_manifest: str, sha_updates: dict[str, str], built_utc: str) -> str:
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
                f"revision   : {V9_2_VERSION} relations & provenance revision "
                f"(annotation provenance six-regime fix, relation producer + "
                f"mirror SSOT, _published removal; row set / identity / "
                f"content unchanged)",
            ] + header[i + 1 :]
            break
    return "\n".join(header + ["sha256     :", *[f"  {k}  {v}" for k, v in sorted(merged_shas.items())], ""])


def build_all(
    stage_dir: Path = STAGE_DIR_DEFAULT,
    *,
    force_download: bool = False,
    publish: bool = False,
    with_stage_configs: bool = False,
    commit_message: str = "",
) -> dict:
    if with_stage_configs:
        raise SystemExit(
            "Workstream S (bundles/streams/fixtures overlay) is deferred to "
            "v9.3 per decision D8 (plan §5/§2) — run v9.2 without "
            "--with-stage-configs."
        )
    snapshot_dir = download_live_snapshot(force=force_download)

    old_merged = load_live_merged_rows(snapshot_dir)
    old_sidecar = load_live_sidecar_rows(snapshot_dir)
    print(f"loaded live snapshot: {len(old_merged)} GT rows, {len(old_sidecar)} sidecar rows")

    # C3 first: re-derive matter + sync the nested mirrors. The §14A
    # never-mix guard inside matter.enrich_rows raises if a source-native
    # header thread ever appears.
    matter_merged = matter.enrich_rows(old_merged)
    matter_sidecar = matter.enrich_rows(old_sidecar)

    # C1 + C2 (+ C4 is identity-side; verified unchanged below).
    new_merged = revise_rows(matter_merged)
    new_sidecar = revise_rows(matter_sidecar)

    verify_zero_drift(old_merged, new_merged)
    verify_zero_drift(old_sidecar, new_sidecar)
    verify_annotation_regimes(new_merged)
    relation_stats = verify_relations(new_merged)
    verify_parity(new_merged, new_sidecar)
    verify_source_revision_unchanged(old_merged, new_merged)

    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    counts = stage_default_and_ground_truth(new_merged, stage_dir)
    verify_blind_label_free(stage_dir)

    jsonl_stats = write_hardened_jsonl(new_sidecar, stage_dir / "ground_truth_hardened.jsonl")

    old_card = (snapshot_dir / "README.md").read_text(encoding="utf-8")
    addendum = render_readme_addendum(
        Counter(str(r.get("annotation_method") or "") for r in new_merged),
        relation_stats,
        len(new_merged),
    )
    new_card = upsert_readme_addendum(old_card, addendum)
    new_card = polish_card(new_card)
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
    new_manifest = render_manifest_v9_2(old_manifest, sha_updates, built_utc)
    (stage_dir / "manifest.txt").write_text(new_manifest, encoding="utf-8")

    result = {
        "status": "staged",
        "stage_dir": str(stage_dir),
        "counts": counts,
        "relation_stats": relation_stats,
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
                f"{V9_2_VERSION} relations & provenance revision: six-regime "
                f"annotation provenance, relation producer + mirror SSOT, "
                f"_published removal (plan {PLAN_REF})"
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
    parser.add_argument("--with-stage-configs", action="store_true",
                        help="Workstream S overlay (deferred to v9.3 — raises)")
    parser.add_argument("--commit-message", default="")
    args = parser.parse_args()
    result = build_all(
        args.stage_dir,
        force_download=args.force_download,
        publish=args.publish,
        with_stage_configs=args.with_stage_configs,
        commit_message=args.commit_message,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
