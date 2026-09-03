"""Export data from docclass corpus to HF/Braintrust export format."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import DOC_TYPES, JSONL_PATH, PARQUET_DIR, CHARS_PER_TOKEN


LINE_BOUNDARY_HAZARDS = ("\u2028", "\u2029", "\u0085")


def sanitize_line_boundary_chars(s: str) -> str:
    """Neutralize line-boundary hazard characters for safe JSONL writing."""
    return s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029").replace("\u0085", "\\u0085")


def safe_jsonl_line(row: dict) -> str:
    """Write a JSONL line with line-boundary safety."""
    return sanitize_line_boundary_chars(json.dumps(row, default=str, ensure_ascii=False))


def normalize_metadata_rows(rows: list[dict]) -> list[dict]:
    """KANBAN-076: make metadata cast-safe for Hub loader.

    - Union of all metadata keys on EVERY row (missing -> empty string, never null)
    - Nested dicts AND lists -> compact sorted-key JSON strings
    - Scalars -> strings
    """
    if not rows:
        return rows
    union = sorted({k for r in rows for k in (r.get("metadata") or {})})
    for r in rows:
        md = r.get("metadata") or {}
        flat = {}
        for k in union:
            v = md.get(k, "")
            if isinstance(v, (dict, list)):
                v = json.dumps(v, sort_keys=True, ensure_ascii=False)
            else:
                v = "" if v is None else str(v)
            flat[k] = v
        r["metadata"] = flat
    return rows


def assign_split(filename: str) -> str:
    """Deterministic 90/10 train/test split keyed on filename (md5 % 10 == 0 -> test)."""
    digest = int(hashlib.md5(filename.strip().encode("utf-8")).hexdigest(), 16)
    return "test" if digest % 10 == 0 else "train"


def estimate_tokens(text: str, chars_per_token: float = CHARS_PER_TOKEN) -> float:
    """Estimate token count using chars-per-token heuristic."""
    return len(text) / chars_per_token


def add_token_estimates(rows: list[dict]) -> list[dict]:
    """Add token_estimate field to each row."""
    for r in rows:
        r["token_estimate"] = int(estimate_tokens(r.get("doc_text", "")))
    return rows


def export_to_jsonl(rows: list[dict], path: Path) -> dict:
    """Write rows to JSONL with KANBAN-088 line-boundary safety."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(safe_jsonl_line(row) + "\n")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": str(path), "rows": len(rows), "sha256": sha}


def stage_parquet(
    rows: list[dict],
    stage_dir: Path,
    gt_scalar_keys: list[str] | None = None,
) -> dict[tuple[str, str], int]:
    """Stage parquet configs for default + ground_truth splits.

    Args:
        rows: List of merged docclass rows with keys:
            filename, doc_text, prompt, expected, expected_subclass, split, metadata, gt_fields
        stage_dir: Output directory for parquet files
        gt_scalar_keys: Ground truth scalar column names

    Returns:
        Dict of (config, split) -> row count
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    if gt_scalar_keys is None:
        gt_scalar_keys = [
            "label_evidence", "content_topic", "topic_evidence",
            "sentiment_score", "sentiment_label", "sentiment_evidence",
            "claim_number", "policy_number", "insurer", "insured_party",
            "claim_type", "date_of_loss", "date_filed", "claimed_amount",
            "adjuster", "damages_description", "coverage_determination",
            "denial_reasons", "supporting_documents",
            "cuad_clause_labels", "maud_clause_labels",
            "intent", "subject_matter", "keywords",
            "intent_source", "intent_confidence", "intent_status",
        ]

    def _blind_row(r: dict) -> dict:
        return {
            "filename": r["filename"],
            "doc_text": r["doc_text"],
            "prompt": r.get("prompt") or "",
            "metadata": dict(r.get("metadata") or {}),
        }

    def _gt_row(r: dict) -> dict:
        out = {
            "filename": r["filename"],
            "expected": r["expected"],
            "expected_subclass": r["expected_subclass"],
            "split": r["split"],
        }
        gf = r.get("gt_fields") or {}
        for k in gt_scalar_keys:
            v = gf.get(k)
            out[k] = None if v is None else str(v)
        return out

    blind = [_blind_row(r) for r in rows]
    normalize_metadata_rows(blind)

    gt_names = ["filename", "expected", "expected_subclass", "split"] + gt_scalar_keys
    gt_schema = pa.schema([(k, pa.string()) for k in gt_names])

    counts = {}
    for split in ("train", "test"):
        subset_b = [r for r, src in zip(blind, rows) if src["split"] == split]
        subset_g = [_gt_row(r) for r in rows if r["split"] == split]

        bdir = stage_dir / "parquet" / "default" / split
        gdir = stage_dir / "parquet" / "ground_truth" / split
        bdir.mkdir(parents=True, exist_ok=True)
        gdir.mkdir(parents=True, exist_ok=True)

        pq.write_table(
            pa.Table.from_pylist(subset_b),
            bdir / f"{split}-00000-of-00001.parquet",
        )
        pq.write_table(
            pa.Table.from_pylist(subset_g, schema=gt_schema),
            gdir / f"{split}-00000-of-00001.parquet",
        )
        counts[("default", split)] = len(subset_b)
        counts[("ground_truth", split)] = len(subset_g)
    return counts


def build_manifest(
    rows: list[dict],
    counts: dict[tuple[str, str], int],
    append_stats: dict,
    file_stats: dict,
    stripped_n: int,
    intent_stats: dict | None = None,
) -> str:
    """Generate manifest.txt content."""
    from collections import Counter

    types = Counter(r["expected"] for r in rows)
    strata = Counter((r["expected"], r["expected_subclass"]) for r in rows)
    purpose_n = sum(1 for r in rows if (r.get("gt_fields") or {}).get("intent"))
    fb = file_stats.get("by_class", {})

    ins_n = append_stats.get("ins_n", 0)
    ins_types = append_stats.get("ins_types", "")
    corr_n = append_stats.get("corr_n", 0)
    pool_n = append_stats.get("pool_n", 247413)
    corr_overrides = append_stats.get("corr_overrides", 0)

    if ins_n > 0:
        ins_segment = (
            f"                   +{ins_n} insurance_claim rows (DE-SynPUF Sample-1 "
            f"re-render via\n                   Exios66/claims-data-eda, verbatim GT "
            f"contract, existing 400\n                   record_ids excluded) — "
            f"subtypes {ins_types}.\n"
        )
    else:
        ins_segment = (
            f"                   +{ins_n} insurance_claim rows this revision — no "
            f"new claims beyond the parent 400 (v6 rev2 published 600; "
            f"composition derives from the fused dump, never hand-typed).\n"
        )

    if intent_stats:
        corr_rows = intent_stats.get("correspondence_rows") or intent_stats.get("rows_total", 0)
        manual_n = intent_stats.get("manual_total", 0)
        aeslc_n = intent_stats.get("aeslc_join_total", intent_stats.get("aeslc_joined", 0))
        llm_n = intent_stats.get("llm_zero_shot_total") or intent_stats.get("llm_zero_shot", 0)
        intent_segment = (
            f"intent_backfill : v7 correspondence intent hydration (issue #5): "
            f"{corr_rows} rows, "
            f"{intent_stats.get('coverage_pct', 0)}% non-null intent;\n"
            f"                   intent_source = hydration PATH (disjoint values "
            f"summing to {corr_rows}):\n"
            f"                   manual {manual_n} (purpose-GT push), aeslc_join {aeslc_n} "
            f"(sha256 exact-body\n                   join-assisted pass vs "
            f"snoop2head/enron_aeslc_emails +\n                   Yale-LILY/aeslc — the "
            f"mirrors carry NO intent annotations; the\n                   join supplies "
            f"provenance + recovered subject_line as constrained context),\n"
            f"                   llm_zero_shot {llm_n}; flagged_review "
            f"{intent_stats.get('flagged_review', 0)};\n"
            f"                   columns intent_source / intent_confidence / "
            f"intent_status ride the\n                   ground_truth config "
            f"(KANBAN issue #5 Phase 4).\n"
        )
    else:
        intent_segment = ""

    built = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""mailroom-corpus manifest — schema v7 (issue #5 intent hydration)
=================================================
built_utc        : {built}
schema_version   : 7
rows_total       : {len(rows)} ({dict(sorted(types.items()))})
rows_by_config   : default train={counts[("default", "train")]} test={counts[("default", "test")]}; ground_truth train={counts[("ground_truth", "train")]} test={counts[("ground_truth", "test")]}
strata           : {len(strata)} (expected x expected_subclass)
v5_base_revision : 1d4753578d91aae09033b359bc32dc1b431e4c20
v6_additions     : +{corr_n} correspondence rows (stratified sha256-filename draw
                    of {corr_n} from {pool_n:,} dedup GT rows after excluding the 110
                    existing; 3-labeler verification pass GREEN — subclass/topic/
                    sentiment reproduce the Hub GT on every row; KANBAN-103 overrides
                    honored, {corr_overrides} override hits) from
                    Lucius-Morningstar/enron-correspondence-dedup;
{ins_segment}{intent_segment}original_files   : {file_stats.get("n", 0)} upstream originals under files/ ({file_stats.get("bytes", 0) // 1048576} MB) —
                    contract {fb.get("contract", 0)} CUAD source PDFs (theatticusproject/cuad),
                    merger_agreement {fb.get("merger_agreement", 0)} MAUD contract_N.txt (Zenodo 7500064),
                    corporate_record {fb.get("corporate_record", 0)} EDGAR exhibit originals.
                    metadata.original_file carries the Hub-relative path ("" when the
                    corpus has none: correspondence = maildir text, insurance_claim =
                    synthetic renders — the render IS the original). Sha256 per file:
                    original_files_mapping.jsonl sidecar.
purpose_gt       : intent/subject_matter/keywords on {purpose_n} rows (the
                    llm-mailroom purpose-GT push of 2026-08-30 covers the v5 train
                    purpose-class rows); new append rows are EMPTY until the
                    incremental labeler pass fills them in a follow-up revision.
blind_repair     : v6 strips the label equivalents (expected_doc_type /
                    expected_subclass) that the v4-era flat dump rode inside
                    blind metadata and v5 carried onto the default config
                    verbatim — the default config is now truly agent-blind per
                    the card contract ("NO label columns"); labels live ONLY in
                    the ground_truth config. No repo consumer read the blind
                    metadata labels (mirrors take labels from the GT config /
                    top-level fields); {stripped_n} rows repaired.
family_split     : md5(filename) % 10 == 0 -> test; recomputed and asserted for
                    every append row at fusion.
legacy_files     : docclass_merged.jsonl retained UNTOUCHED (describes v4; kept for
                    pinned consumers). Parquet shards supersede it.
builder          : mailroom_eda.dataset_export @ Mailroom-Corpus-EDA
"""


def budget_coverage(token_lengths: np.ndarray, budgets: list[int] | None = None) -> pd.DataFrame:
    """Compute % of docs fitting in each token budget."""
    if budgets is None:
        budgets = [4_096, 8_192, 16_384, 32_768, 65_536, 131_072, 200_000]
    df = pd.DataFrame({"token_len": token_lengths})
    out = []
    for b in budgets:
        count = int((token_lengths <= b).sum())
        out.append({"budget": b, "count": count, "pct": 100 * count / len(token_lengths)})
    return pd.DataFrame(out)


def compute_token_stats(rows: list[dict], by_type: bool = True) -> pd.DataFrame:
    """Compute token length statistics per doc_type or overall."""
    data = []
    for r in rows:
        tokens = r.get("token_estimate", estimate_tokens(r.get("doc_text", "")))
        data.append({"doc_type": r.get("expected", "unknown"), "tokens": tokens, "chars": len(r.get("doc_text", ""))})
    df = pd.DataFrame(data)
    if by_type:
        stats = df.groupby("doc_type")["tokens"].describe(percentiles=[0.25, 0.5, 0.75, 0.9, 0.95, 0.99]).round(1)
        return stats.reset_index()
    return df["tokens"].describe(percentiles=[0.25, 0.5, 0.75, 0.9, 0.95, 0.99]).to_frame().T