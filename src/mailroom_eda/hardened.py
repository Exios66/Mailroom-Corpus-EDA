"""§84 hardened-release builders (HUB-022) — shared by the v9 builder and the
archived v8 release CLIs.

This module is the SINGLE implementation of the §84 release chain that the
standalone `scripts/archive/v8/publish_hardened.py` CLI used to own inline:

  load_snapshot_rows → enrich_gt_rows (identity → eval_contract → matter,
  the §84A dependency chain) → build_bundle_rows / build_stream_rows /
  build_fixture_rows → stage_configs → verify_stage.

`src/mailroom_eda/v9_build.py` (the live mailroom-dataset builder) consumes
the same functions here; the frozen-v8 tooling in
``scripts/archive/v8/publish_hardened.py`` is now a thin CLI over this module.
The column registries (identity / eval-contract / matter) live in
``release_sections.py`` — never re-declared here.

Laws enforced (from HUB-022):
- the blind `default` config is NEVER staged — labels never ride blind;
- every new scalar column is string-typed with '' for absence; list columns
  are list<string>;
- JSONL sidecars go through safe_jsonl_line (KANBAN-088 line-boundary law).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from . import bundles as bd
from . import eval_contract as ec
from . import fixtures as fx
from . import identity, matter
from .bundles import STREAM_FIELDS
from .config import DATA_DIR
from .dataset_export import assign_split
from .release_sections import (
    CONTRACT_FIELDS,
    IDENTITY_FIELDS,
    MATTER_LISTS,
    MATTER_SCALARS,
)

SNAPSHOT = DATA_DIR / "parquet"

FIXTURE_NOTE_FIELDS = (
    "fixture_note", "arbiter_note", "failure_note", "review_reason_override",
    "expected_correction", "expected_post_correction_state",
)
FIXTURE_FIELDS = (
    "filename", "doc_text", "expected", "expected_subclass", "split",
    "synthetic", "fixture_kind", "calibration_cell", "failure_stage",
    "arbiter_outcome", "probes_confidence",
) + CONTRACT_FIELDS + FIXTURE_NOTE_FIELDS

BUNDLE_FIELDS = (
    "filename", "doc_text", "expected", "expected_subclass", "split",
    "synthetic", "bundle_family", "bundle_anchor_filename", "duplicate_type",
) + IDENTITY_FIELDS + CONTRACT_FIELDS + MATTER_SCALARS + MATTER_LISTS


def load_snapshot_rows() -> list[dict]:
    """GT rows (all columns) + doc_text/metadata joined from default."""
    import pandas as pd

    frames = [
        pd.read_parquet(f)
        for split in ("train", "test")
        for f in sorted((SNAPSHOT / "ground_truth" / split).glob("*.parquet"))
    ]
    blind = [
        pd.read_parquet(f, columns=["filename", "doc_text", "metadata"])
        for split in ("train", "test")
        for f in sorted((SNAPSHOT / "default" / split).glob("*.parquet"))
    ]
    b = pd.concat(blind, ignore_index=True)
    text_by_fn = dict(zip(b["filename"], b["doc_text"]))
    md_by_fn = dict(zip(b["filename"], b["metadata"]))
    df = pd.concat(frames, ignore_index=True)
    rows = df.to_dict("records")
    out = []
    for r in rows:
        fn = r["filename"]
        r["doc_text"] = str(text_by_fn.get(fn, ""))
        r["metadata"] = md_by_fn.get(fn) or {}
        out.append(r)
    return out


def enrich_gt_rows(rows: list[dict]) -> list[dict]:
    """identity → evaluation contract → §14A matter/grouping (in that order
    — the §84A dependency chain)."""
    rows = identity.enrich_rows(rows)
    rows = ec.enrich_rows(rows)
    return matter.enrich_rows(rows)


def build_bundle_rows(rows: list[dict]) -> tuple[list[dict], dict]:
    bundle_rows, manifest = bd.synthetic_bundles(
        rows, seed=42, anchors_per_family=2, with_duplicates=True
    )
    out = []
    for row in bundle_rows:
        enriched = identity.enrich_row(row)
        enriched = ec.enrich_row(enriched)
        if str(enriched.get("synthetic") or "") == "true":
            # manufactured rows carry NO source provenance and synthetic
            # annotation — never claim the anchor class's source corpus
            enriched["source_corpus"] = ""
            enriched["source_document_id"] = ""
            enriched["annotation_method"] = "synthetic"
            enriched["annotation_source"] = ""
        enriched["split"] = assign_split(str(enriched["filename"]))
        out.append(enriched)
    return out, manifest


def build_stream_rows(bundle_rows: list[dict], rows: list[dict]) -> tuple[list[dict], dict]:
    """§27–§29/§48 STREAM eval tier: interleave the bundle matters into one
    reproducible ingress stream with distractors (real, no-matter rows)."""
    bundle_fns = {r["filename"] for r in bundle_rows}
    # distractors: ANTIBUNDLE rows (never in any matter) — draw from the
    # non-bundle corpus rows of OTHER classes so the distractor is honestly
    # unrelated to every matter in the stream.
    distractor_pool = [
        dict(r) for r in rows
        if r["filename"] not in bundle_fns
    ]
    stream_rows, manifest = bd.build_streams(
        bundle_rows, run_id="RUN-SIM-001", distractor_every=4,
        distractor_pool=distractor_pool,
    )
    out = []
    for row in stream_rows:
        enriched = identity.enrich_row(row)
        enriched = ec.enrich_row(enriched)
        if str(enriched.get("synthetic") or "") == "true":
            enriched["source_corpus"] = ""
            enriched["source_document_id"] = ""
            enriched["annotation_method"] = "synthetic"
            enriched["annotation_source"] = ""
        if enriched.get("stream_role") == bd.DISTRACTOR_ROLE:
            # distractors belong to NO matter — strip any bundle-adjacent
            # fields and keep honest identity/provenance
            enriched["matter_id"] = ""
            enriched["group_id"] = ""
            enriched["group_role"] = ""
            enriched["matter_construction"] = ""
            enriched["bundle_anchor_filename"] = ""
        enriched["split"] = assign_split(str(enriched["filename"]))
        out.append(enriched)
    return out, manifest


def build_fixture_rows() -> list[dict]:
    out = []
    for row in fx.build_fixture_suite():
        fixed = {k: row.get(k, "") for k in FIXTURE_FIELDS}
        fixed["split"] = assign_split(str(fixed["filename"]))
        out.append(fixed)
    return out


def _scalar(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list, tuple)):
        # JSON, not str() — gt_fields is a dict and must remain json.loads-able
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return str(v)


def stage_configs(rows: list[dict], bundle_rows: list[dict], fixture_rows: list[dict],
                  stream_rows: list[dict], stage_dir: Path) -> dict:
    gt_dir = stage_dir / "parquet" / "ground_truth"
    counts: dict[tuple[str, str], int] = {}
    original_cols = [c for c in rows[0] if c not in
                     {"doc_text", "metadata"} | set(IDENTITY_FIELDS)
                     | set(CONTRACT_FIELDS) | set(MATTER_SCALARS) | set(MATTER_LISTS)]

    for split in ("train", "test"):
        for config, subset in (
            ("ground_truth", [r for r in rows if r["split"] == split]),
            ("bundles", [r for r in bundle_rows if r["split"] == split]),
            ("streams", [r for r in stream_rows if r["split"] == split]),
            ("fixtures", [r for r in fixture_rows if r["split"] == split]),
        ):
            if config == "ground_truth":
                names = original_cols + list(IDENTITY_FIELDS) + list(CONTRACT_FIELDS) \
                    + list(MATTER_SCALARS) + list(MATTER_LISTS)
                fields: list[pa.Field] = []
                for name in names:
                    if name in MATTER_LISTS:
                        fields.append(pa.field(name, pa.list_(pa.string())))
                    else:
                        fields.append(pa.field(name, pa.string()))
                schema = pa.schema(fields)
                table_rows = []
                for r in subset:
                    rec = {}
                    for name in names:
                        if name in MATTER_LISTS:
                            rec[name] = [str(x) for x in (r.get(name) or [])]
                        else:
                            rec[name] = _scalar(r.get(name))
                    table_rows.append(rec)
            elif config in ("bundles", "streams"):
                names = STREAM_FIELDS if config == "streams" else BUNDLE_FIELDS
                schema = pa.schema(
                    [pa.field(name, pa.list_(pa.string()) if name in MATTER_LISTS else pa.string())
                     for name in names]
                )
                table_rows = []
                for r in subset:
                    rec = {}
                    for name in names:
                        rec[name] = (
                            [str(x) for x in (r.get(name) or [])] if name in MATTER_LISTS
                            else _scalar(r.get(name))
                        )
                    table_rows.append(rec)
            else:
                schema = pa.schema([pa.field(name, pa.string()) for name in FIXTURE_FIELDS])
                table_rows = [
                    {name: _scalar(r.get(name)) for name in FIXTURE_FIELDS} for r in subset
                ]

            out_dir = stage_dir / "parquet" / config / split
            out_dir.mkdir(parents=True, exist_ok=True)
            pq.write_table(
                pa.Table.from_pylist(table_rows, schema=schema),
                out_dir / f"{split}-00000-of-00001.parquet",
            )
            counts[(config, split)] = len(table_rows)
    return counts


def verify_stage(rows: list[dict], bundle_rows: list[dict], fixture_rows: list[dict],
                 stream_rows: list[dict], stage_dir: Path, counts: dict) -> None:
    """Hard verification before anything leaves the machine."""
    import pandas as pd

    assert not (stage_dir / "parquet" / "default").exists(), "blind config must NEVER be staged"

    # ground_truth: the original columns are value-identical to the snapshot
    original_cols = [c for c in rows[0] if c not in
                     {"doc_text", "metadata"} | set(IDENTITY_FIELDS)
                     | set(CONTRACT_FIELDS) | set(MATTER_SCALARS) | set(MATTER_LISTS)]
    staged = pd.concat([
        pd.read_parquet(f)
        for split in ("train", "test")
        for f in sorted((stage_dir / "parquet" / "ground_truth" / split).glob("*.parquet"))
    ], ignore_index=True)
    snap = pd.DataFrame([
        {c: ("" if r[c] is None else (str(r[c]) if not isinstance(r[c], list) else json.dumps(r[c])))
         for c in original_cols}
        for r in rows
    ])
    staged_cmp = staged[original_cols].fillna("")
    staged_cmp = staged_cmp.astype(str)
    snap_cmp = snap.reindex(columns=original_cols).astype(str).reset_index(drop=True)
    assert staged_cmp.reset_index(drop=True).equals(snap_cmp), "original GT columns drifted"

    # new columns: '' convention, never None; vocabulary membership
    for col in IDENTITY_FIELDS + CONTRACT_FIELDS + MATTER_SCALARS:
        assert staged[col].isna().sum() == 0, col
    assert set(staged["matter_construction"]).issubset({"", *ec.MATTER_CONSTRUCTION})
    assert set(staged["group_role"]).issubset({"", *ec.GROUP_ROLES})
    assert set(staged["expected_specialist"]).issubset({*ec.SPECIALIST_BY_CLASS.values()})
    n_docs = len(staged)
    assert staged["document_id"].nunique() == n_docs, "document_id not unique"
    grouped = staged[staged["matter_id"] != ""]
    assert set(grouped["matter_construction"]) == {"heuristic_reconstructed"}, \
        "header threads must be absent (verified structural fact)"

    # bundles: fully flagged, deterministic shape
    sb = pd.concat([
        pd.read_parquet(f)
        for split in ("train", "test")
        for f in sorted((stage_dir / "parquet" / "bundles" / split).glob("*.parquet"))
    ], ignore_index=True)
    assert len(sb) == len(bundle_rows) == counts[("bundles", "train")] + counts[("bundles", "test")]
    assert set(sb["matter_construction"]) == {"synthetic_constructed"}
    manufactured = sb[sb["synthetic"] == "true"]
    assert (manufactured["source_corpus"] == "").all(), "manufactured rows claim source provenance"
    assert (manufactured["annotation_method"] == "synthetic").all()
    assert sb["bundle_family"].ne("").all()
    assert all(bd.SYNTHETIC_FLAG_HEADER in t for t in manufactured["doc_text"])

    # fixtures: quartet × classes, every failure stage, closed vocabularies
    sf = pd.concat([
        pd.read_parquet(f)
        for split in ("train", "test")
        for f in sorted((stage_dir / "parquet" / "fixtures" / split).glob("*.parquet"))
    ], ignore_index=True)
    assert len(sf) == len(fixture_rows)
    quartet = sf[sf["calibration_cell"] != ""]
    assert len(quartet) == 4 * len(ec.SPECIALIST_BY_CLASS)
    assert set(sf["failure_stage"]) - {""} == set(ec.FAILURE_STAGES)
    assert set(sf["fixture_kind"]).issubset(set(ec.FIXTURE_KINDS))
    assert set(sf["arbiter_outcome"]) - {""} == set(fx.ARBITER_OUTCOMES)

    # streams (§27–§29/§48): reproducible interleave, distractors carry no
    # matter, sequence positions strictly increasing per run
    ss = pd.concat([
        pd.read_parquet(f)
        for split in ("train", "test")
        for f in sorted((stage_dir / "parquet" / "streams" / split).glob("*.parquet"))
    ], ignore_index=True)
    assert len(ss) == len(stream_rows) == counts[("streams", "train")] + counts[("streams", "test")]
    assert set(ss["stream_role"]) == {"member", bd.DISTRACTOR_ROLE}
    distractors = ss[ss["stream_role"] == bd.DISTRACTOR_ROLE]
    assert (distractors["matter_id"] == "").all(), "distractor carries a matter"
    assert (distractors["group_id"] == "").all()
    members = ss[ss["stream_role"] == "member"]
    assert set(members["matter_construction"]) == {"synthetic_constructed"}
    pos = ss["sequence_position"].astype(int).tolist()
    # the stream is one sequence: positions are unique and exactly 1..N
    # (the split configs scatter them — train/test are subsets of one run).
    assert len(pos) == len(set(pos)), "duplicate sequence positions"
    assert sorted(pos) == list(range(1, len(ss) + 1)), \
        "sequence positions not contiguous 1..N"
    assert ss["simulation_run_id"].nunique() == 1
    print(f"verify_stage OK — GT {len(staged)}, bundles {len(sb)} "
          f"({len(manufactured)} manufactured), streams {len(ss)} "
          f"({len(distractors)} distractors), fixtures {len(sf)}")