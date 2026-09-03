#!/usr/bin/env python3
"""CLI: build + stage (optionally publish) the mailroom-corpus v8 release.

Usage:
    python scripts/build_v8.py --stage-dir /tmp/v8 --dry-run
    python scripts/build_v8.py --stage-dir /tmp/v8 --publish

Pipeline:
 1. Reconstruct v7 rows from the Hub parquet (default + ground_truth joined
    on filename) — the published snapshot is the ancestor of v8.
 2. Backfill the 600 CMS insurance rows (intent / subject_matter / keywords).
 3. Add stratified synthetic property rows (GNOTHEIA, Apache-2.0) and
    auto rows (BDR motor, MIT) with full 27-key GT + metadata.
 4. Validate: GT population, verbatim contract, no None, test-split
    nullification, cast-safe metadata.
 5. Stage parquet configs (default blind / ground_truth), manifest, card.
 6. --publish uploads via the centralized hf_interface and prints the sha
    table for post-upload verification.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from mailroom_eda import v8_build as vb  # noqa: E402
from mailroom_eda.config import REPO_ID  # noqa: E402
from mailroom_eda.dataset_export import stage_parquet, safe_jsonl_line  # noqa: E402
from mailroom_eda.docclass_uploader import (  # noqa: E402
    build_manifest,
    render_card_v7,
    strip_blind_labels,
)
from mailroom_eda.hf_interface import get_hf_api, sha256_file, upload_folder  # noqa: E402


def load_v7_rows() -> list[dict]:
    """Reconstruct v7 row dicts from the Hub parquet (default + gt join)."""
    base = Path(
        "/Users/luciusjmorningstar/.cache/huggingface/hub/datasets--Lucius-Morningstar--mailroom-corpus"
        "/snapshots/bb57c5ad00333d239ea456fe3f2298c3ba5b5108/parquet"
    )
    rows: list[dict] = []
    for split in ("train", "test"):
        d = pd.read_parquet(base / "default" / split / f"{split}-00000-of-00001.parquet")
        g = pd.read_parquet(base / "ground_truth" / split / f"{split}-00000-of-00001.parquet")
        for _, gd in g.iterrows():
            fn = gd["filename"]
            dd = d[d["filename"] == fn]
            if len(dd) == 0:
                raise RuntimeError(f"default/gt join failed for {fn}")
            dd = dd.iloc[0]
            gt = {k: gd[k] for k in g.columns if k not in
                  ("filename", "expected", "expected_subclass", "split")}
            # un-stringify list columns that came from JSON
            for k in ("denial_reasons", "supporting_documents", "keywords"):
                v = gt.get(k)
                if isinstance(v, str) and v.startswith("["):
                    try:
                        gt[k] = json.loads(v.replace("'", '"'))
                    except Exception:
                        pass
            rows.append({
                "filename": fn,
                "doc_text": dd["doc_text"],
                "prompt": dd.get("prompt", "") or "",
                "expected": gd["expected"],
                "expected_subclass": gd["expected_subclass"],
                "split": gd["split"],
                "metadata": dict(dd["metadata"] or {}),
                "gt_fields": gt,
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage-dir", type=Path, default=Path("/tmp/v8-stage"))
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--skip-publish-verify", action="store_true")
    args = ap.parse_args()

    # 1. load v7 rows from Hub snapshot
    rows = load_v7_rows()
    print(f"v7 rows loaded: {len(rows)} "
          f"({Counter(r['expected'] for r in rows)})")

    # 2. reconstruct the CMS insurance subclass field from metadata (carrier/
    #    inpatient/outpatient/pde come from metadata.claim_subtype)
    ins_rows = [r for r in rows if r["expected"] == "insurance_claim"]
    for r in ins_rows:
        sub = (r.get("metadata") or {}).get("claim_subtype", "") or r["expected_subclass"]
        r["expected_subclass"] = sub
    print(f"insurance rows from v7: {len(ins_rows)}")

    # 3. backfill the CMS rows (intent + subject/keywords)
    n_backfilled = 0
    for r in ins_rows:
        vb.backfill_cms_row(r)
        n_backfilled += 1
    print(f"CMS rows backfilled: {n_backfilled}")

    # 4. add synthetic property rows (GNOTHEIA)
    from huggingface_hub import hf_hub_download
    gp = hf_hub_download(vb.GNOTHEIA_REPO, "data.parquet", repo_type="dataset",
                         revision=vb.GNOTHEIA_REV)
    gdf = pd.read_parquet(gp)
    prop_rows = vb.build_property_rows(gdf, vb.GNOTHEIA_REV)
    print(f"property rows built: {len(prop_rows)}")

    # 5. add synthetic auto rows (BDR)
    bp = hf_hub_download(vb.BDR_AUTO_REPO,
                         "data/insurance_motor_claims_decision_v1_1.csv",
                         repo_type="dataset", revision=vb.BDR_AUTO_REV)
    bdf = pd.read_csv(bp, low_memory=False)
    auto_rows = vb.build_auto_rows(bdf, vb.BDR_AUTO_REV)
    print(f"auto rows built: {len(auto_rows)}")

    # 6. merge + split + validate
    all_rows = rows + prop_rows + auto_rows
    for r in all_rows:
        r["split"] = assign_split_ = (r.get("split") or vb.assign_split(r["filename"]))
    report = vb.validate_rows(all_rows)
    print(f"validation: insurance_rows={report['insurance_rows']} "
          f"errors={len(report['errors'])} empty_allowed={report['empty_allowed']}")
    for e in report["errors"][:20]:
        print("  ERR:", e)
    if report["errors"]:
        print("ABORT: validation errors — no staging")
        return 1

    # 7. canonicalize for export
    rows_exp = vb.canonicalize_for_export(all_rows)
    print(f"corpus total: {len(rows_exp)} "
          f"({Counter(r['expected'] for r in rows_exp)})")
    ins_counts = Counter(r["expected_subclass"] for r in rows_exp if r["expected"] == "insurance_claim")
    print(f"insurance strata: {dict(ins_counts)}")

    # 8. stage
    stage = args.stage_dir
    if stage.exists():
        import shutil
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    counts = stage_parquet(rows_exp, stage)
    print(f"parquet staged: {dict(counts)}")

    # JSONL sidecar
    with (stage / "docclass_merged_v8.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows_exp:
            fh.write(safe_jsonl_line(r) + "\n")

    # manifest (v8) — accurate append stats
    new_ins = len(prop_rows) + len(auto_rows)
    append_stats = {
        "corr_n": 0, "pool_n": 247413, "corr_overrides": 0,
        "ins_n": new_ins,
        "ins_types": ", ".join(sorted(ins_counts)),
        "ins_total": len(ins_rows) + new_ins,
        "purpose_total": sum(
            1 for r in rows_exp
            if (r.get("gt_fields") or {}).get("intent")
        ),
        "new_attr": {
            "property": len(prop_rows),
            "auto": len(auto_rows),
        },
    }
    manifest = build_manifest(rows_exp, counts, append_stats, {"n": 0, "bytes": 0, "by_class": {}}, 0)
    manifest = manifest.replace("schema v7 (issue #5 intent hydration)",
                                "schema v8 (insurance LOB expansion)")
    manifest = manifest.replace("schema_version   : 7", "schema_version   : 8")
    # splice in a clean v8 additions block (replace the whole v6 segment through
    # the blank line before the original_files line)
    start = manifest.find("v6_additions")
    end = manifest.find("original_files")
    v8_block = (
        f"v8_additions     : +{new_ins} insurance_claim rows (GNOTHEIA property "
        f"{len(prop_rows)} + BDR auto {len(auto_rows)}) — synthetic LOB expansion\n"
        f"                    per HUB-028, license-verified (Apache-2.0 / MIT); "
        f"the 600 CMS parent rows are\n"
        f"                    untouched in content (GT backfilled — see purpose_gt); "
        f"insurance strata:\n"
        f"                    {append_stats['ins_types']}.\n"
    )
    manifest = manifest[:start] + v8_block + manifest[end:]
    manifest = manifest.replace(
        f"purpose_gt       : intent/subject_matter/keywords on {append_stats['purpose_total']} rows (the\n"
        f"                    llm-mailroom purpose-GT push of 2026-08-30 covers the v5 train\n"
        f"                    purpose-class rows); new append rows are EMPTY until the\n"
        f"                    incremental labeler pass fills them in a follow-up revision.",
        f"purpose_gt       : intent/subject_matter/keywords on "
        f"{append_stats['purpose_total']} rows — v8 full GT\n"
        f"                    conformance: every insurance_claim row carries the three\n"
        f"                    purpose keys + intent_source/confidence/status provenance\n"
        f"                    (CMS rows template-derived; property/auto rows authored at\n"
        f"                    build time).",
    )
    (stage / "manifest.txt").write_text(manifest, encoding="utf-8")

    # card
    card = render_card_v7(rows_exp, append_stats, {"n": 0, "bytes": 0, "by_class": {}},
                          intent_stats=None)
    card = card.replace('pretty_name: "Docclass Merged Corpus v7 (',
                        'pretty_name: "Docclass Merged Corpus v8 (')
    purpose_ok = append_stats["purpose_total"]
    v8_section = f"""## Schema v8 additions (HUB-028, 2026-09-02)

* **Insurance LOB expansion**: +{len(prop_rows)} `property` rows (GNOTHEIA
  synthetic polycontexts, Apache-2.0 — FNOL documents stratified by loss
  event: fire/water/storm/burglary/…) and +{len(auto_rows)} `auto` rows
  (BDR motor-claims decisions, MIT — decision letters stratified by accident
  type × `APPROVE`/`REVIEW`/`REJECT`, all reject rows included). Both carry
  the full 27-key GT: claim ref, policy, insured party, dates, claimed
  amount, adjuster pseudonym (auto), damages narrative, coverage
  determination (property = pending, honest — no adjudication in the source;
  auto = approved/pending/denied), feature-grounded denial reasons (auto
  reject), intent, subject_matter, keywords + provenance.
* **Full GT conformance**: all {purpose_ok} rows now carry
  intent / subject_matter / keywords / intent provenance — including all
  {len(ins_rows)} CMS DE-SynPUF rows (was 246/600 missing
  subject/keywords, 600/600 missing intent).
* **Metadata for all entries**: source_dataset / source_revision /
  source_row_id / lob / peril / license ride every row; union-normalized
  cast-safe metadata.
* **Test-split nullification**: every test-split row carries fully populated
  applicable GT (no None/NaN; '' allowed only for schema-documented absence,
  e.g. adjuster on CMS/property rows).

"""
    card = card.replace("## Original files (KANBAN-105 addendum, 2026-08-30)",
                        v8_section + "## Original files (KANBAN-105 addendum, 2026-08-30)")
    (stage / "README.md").write_text(card, encoding="utf-8")

    print(f"staged: {stage}")
    print(f"  manifest sha: {sha256_file(stage / 'manifest.txt')[:12]}")

    if args.publish:
        api = get_hf_api()
        upload_folder(api, stage, REPO_ID,
                      commit_message=f"schema v8: insurance LOB expansion (HUB-028) — {len(rows_exp)} rows")
        print("published:", f"https://huggingface.co/datasets/{REPO_ID}")
        for f in ("parquet/default/train/train-00000-of-00001.parquet",
                  "parquet/ground_truth/train/train-00000-of-00001.parquet"):
            p = stage / f
            print(f"  {f}: local {sha256_file(p)[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
