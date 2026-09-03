#!/usr/bin/env python3
"""CLI: HUB-032 — post-collision GT reconciliation for mailroom-corpus.

The interleaved publishes of HUB-028 (v8 LOB expansion, GT commit
``bba2f750``) and HUB-022 (§84 hardened GT, commit ``61c16645``) left the
Hub tip mixed: blind ``default`` = v8's 2,000 rows, ``ground_truth`` = the
hardened 1,650×60 — the 350 new v8 rows and HUB-028's GT conformance on the
950 insurance rows were absent from the published GT.

This script rebuilds ground_truth from HUB-028's GT revision (2,000 rows,
31 cols, v8 labels + 27-key conformance), applies the row-local hardening
chain (identity → evaluation contract → §14A matter), verifies the 31
original columns stay value-identical to ``bba2f750``, and stages:

- parquet/ground_truth/{train,test} (2,000 rows × 60 cols)
- manifest_hardened.txt (complete hardened-release sha table — bundles/
  fixtures are unchanged at tip and their live shas are folded in)
- README.md with ONLY the §84 card section's live numbers refreshed
  (HUB-028's v8 section is untouched)

Bundles/fixtures are additive configs already at tip and are NOT re-staged.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mailroom_eda import identity  # noqa: E402
from mailroom_eda import matter  # noqa: F401,E402
from mailroom_eda import eval_contract as ec  # noqa: E402
from mailroom_eda.config import REPO_ID  # noqa: E402
from mailroom_eda.dataset_export import safe_jsonl_line  # noqa: E402
from mailroom_eda.docclass_uploader import upsert_section  # noqa: E402
from mailroom_eda.release_sections import (  # noqa: E402
    CONTRACT_FIELDS,
    IDENTITY_FIELDS,
    MATTER_LISTS,
    MATTER_SCALARS,
    CARD_HEADING,
    fetch_live_card,
    replace_card_section,
)

V8_GT_REVISION = "bba2f750"  # HUB-028's v8 GT commit (historical, resolvable)
SNAPSHOT = ROOT / "data" / "parquet"


def fetch_v8_gt() -> list[dict]:
    import pandas as pd
    from huggingface_hub import hf_hub_download

    frames = []
    for split in ("train", "test"):
        path = Path(hf_hub_download(
            repo_id=REPO_ID,
            filename=f"parquet/ground_truth/{split}/{split}-00000-of-00001.parquet",
            repo_type="dataset", revision=V8_GT_REVISION,
        ))
        frames.append(pd.read_parquet(path))
    return pd.concat(frames, ignore_index=True).to_dict("records")


def fetch_tip_blind() -> tuple[dict[str, str], dict[str, dict]]:
    import pandas as pd
    from huggingface_hub import hf_hub_download

    frames = []
    for split in ("train", "test"):
        path = Path(hf_hub_download(
            repo_id=REPO_ID,
            filename=f"parquet/default/{split}/{split}-00000-of-00001.parquet",
            repo_type="dataset",
        ))
        frames.append(pd.read_parquet(path, columns=["filename", "doc_text", "metadata"]))
    df = pd.concat(frames, ignore_index=True)
    return (
        dict(zip(df["filename"], df["doc_text"])),
        dict(zip(df["filename"], df["metadata"])),
    )


def build_rows() -> list[dict]:
    gt_rows = fetch_v8_gt()
    text_by_fn, md_by_fn = fetch_tip_blind()
    assert set(r["filename"] for r in gt_rows) == set(text_by_fn), \
        "v8 GT and tip blind filename sets diverge — re-fetch before reconciling"
    rows = []
    for r in gt_rows:
        r = dict(r)
        r["doc_text"] = str(text_by_fn[r["filename"]])
        assert r["doc_text"], f"empty doc_text for {r['filename']}"
        r["metadata"] = md_by_fn.get(r["filename"]) or {}
        rows.append(r)
    rows = identity.enrich_rows(rows)
    rows = ec.enrich_rows(rows)
    return matter.enrich_rows(rows)


def stage(rows: list[dict], stage_dir: Path) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq

    original_cols = [c for c in rows[0] if c not in
                     {"doc_text", "metadata"} | set(IDENTITY_FIELDS)
                     | set(CONTRACT_FIELDS) | set(MATTER_SCALARS) | set(MATTER_LISTS)]
    names = original_cols + list(IDENTITY_FIELDS) + list(CONTRACT_FIELDS) \
        + list(MATTER_SCALARS) + list(MATTER_LISTS)
    schema = pa.schema([
        pa.field(n, pa.list_(pa.string()) if n in MATTER_LISTS else pa.string())
        for n in names
    ])
    counts = {}
    for split in ("train", "test"):
        subset = [r for r in rows if r["split"] == split]
        table_rows = []
        for r in subset:
            rec = {}
            for n in names:
                rec[n] = ([str(x) for x in (r.get(n) or [])] if n in MATTER_LISTS
                          else "" if r.get(n) is None else str(r.get(n)))
            table_rows.append(rec)
        out_dir = stage_dir / "parquet" / "ground_truth" / split
        out_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pylist(table_rows, schema=schema),
            out_dir / f"{split}-00000-of-00001.parquet",
        )
        counts[split] = len(table_rows)
    return counts


def verify(rows: list[dict], stage_dir: Path, counts: dict) -> dict:
    import pandas as pd

    original_cols = [c for c in rows[0] if c not in
                     {"doc_text", "metadata"} | set(IDENTITY_FIELDS)
                     | set(CONTRACT_FIELDS) | set(MATTER_SCALARS) | set(MATTER_LISTS)]
    staged = pd.concat([
        pd.read_parquet(f)
        for split in ("train", "test")
        for f in sorted((stage_dir / "parquet" / "ground_truth" / split).glob("*.parquet"))
    ], ignore_index=True)
    snap = pd.DataFrame([
        {c: "" if r[c] is None else str(r[c]) for c in original_cols} for r in rows
    ]).astype(str)
    assert staged[original_cols].fillna("").astype(str).reset_index(drop=True).equals(
        snap.reset_index(drop=True)), "v8 GT original columns drifted"

    assert staged["document_id"].nunique() == len(staged) == 2000
    assert not (stage_dir / "parquet" / "default").exists()
    assert not (stage_dir / "parquet" / "bundles").exists()
    assert not (stage_dir / "parquet" / "fixtures").exists()

    grouped = staged[staged["matter_construction"] != ""]
    matters = grouped["matter_id"].nunique()
    methods = Counter(staged["annotation_method"])
    facts = {
        "rows": len(staged),
        "grouped_rows": len(grouped),
        "threads": matters,
        "constructions": sorted(set(grouped["matter_construction"])),
        "annotation_methods": dict(methods),
        "insurance_n": int((staged["expected"] == "insurance_claim").sum()),
        "v8_rows_present": int((staged["source_corpus"] == "cms_desynpuf").sum()),
    }
    print("verify OK —", facts)
    return facts


CARD_BODY_TEMPLATE = """## Mailroom evaluation hardening (v0.2/v0.3/v0.4, 2026-09-02; reconciled onto the v8 base, HUB-032)

One revision delivering three §84 markers (plan §26: conceptual targets, not
forced releases), REBASED onto HUB-028's v8 {rows}-row ground_truth after the
interleaved-publish collision (their GT commit `bba2f750` is the value base;
the 31 original columns are asserted value-identical to it). Blind `default`
config stays label-free (4 columns, no labels).

**v0.2-mailroom-hardened** — new columns on `ground_truth`:
* Identity/hashes (§9–§12): `document_id` (stable `DOC-` + sha256[:16] over
  source corpus + source filename — never row order, never content),
  `source_corpus`, `source_document_id`, `source_filename`, `source_revision`
  ('' until tracked by a builder), `content_sha256` (canonical doc_text
  bytes), `normalized_text_sha256` (NFC, LF-folded, whitespace-collapsed —
  the duplicate-detection key).
* Evaluation contract (§31/§43/§57–59): `expected_specialist` (live
  taxonomy.yaml registry), `expected_stage` (archived for canonical rows),
  `review_expected`/`review_reason` (false/'' on canonical rows — closed
  §73 vocabulary reserved for fixtures), `retry_expected`/
  `expected_post_retry_state`, `annotation_source`/`annotation_method`
  ({methods}) /`annotation_model`/`annotation_prompt_version`/
  `annotation_confidence`/`annotation_reviewer`/`annotation_timestamp`.
  v8 note: the 350 GNOTHEIA/BDR rows carry HUB-028's builder-derived
  provenance (`intent_source='manual'`), rendered through the same closed
  mapping as the v7 correspondence rows.

**v0.3-matter-aware** — §14A methodology (verified 2026-09-02 against the
raw CMU maildir: In-Reply-To/References are structurally ABSENT — 0/350 raw
files, 0/247,523 upstream dedup rows — so header-thread ground truth cannot
exist in this corpus family):
* `ground_truth` gains `matter_id`, `matter_construction`, `group_id`,
  `group_role`, `relationships` (list), `related_document_ids` (list),
  `thread_position`, `thread_size`, `thread_evidence`. Populated ONLY via
  `heuristic_reconstructed` (normalized subject + custodian + 30-day window,
  degenerate subjects excluded): **{grouped} rows in {threads}
  multi-member threads; {unassigned} unassigned** — honest baseline, counted
  separately, never merged into a "matters" total. Thread keys are
  sha256-derived (stable across processes; PYTHONHASHSEED-salted ids were
  caught by the pre-publish determinism gate). `source_native_thread` stays
  implemented (guarded) for future feeds that carry real reply headers.
* `bundles` config (§14 synthetic families, flagged
  `synthetic_constructed`, unchanged at tip since the §84 publish): five
  family templates over REAL anchor rows (§14's legal/insurance worked
  examples span ≥2 classes); seed 42, 2 anchors per family, one
  `template_variant` duplicate per instance (§87). Manufactured siblings
  carry `synthetic: true`, an explicit scaffold doc_text header, and
  `MATTER-SYN-*` filenames (no snapshot collisions); they claim NO source
  provenance (`source_corpus` = '') and `annotation_method` =
  `synthetic`. Roles/relationships come ONLY from the closed
  GROUP_ROLES/RELATIONSHIP_TYPES vocabularies.

**v0.4-recovery-suite** — `fixtures` config (§68–§72A fixture content;
`fixture:` filename namespace, so these are evaluation scenarios, not
corpus rows; unchanged at tip since the §84 publish):
* §70 calibration quartet — correct_high / correct_low / wrong_high /
  wrong_low for ALL FIVE classes, each row probing the LIVE routing bands
  from llm-mailroom's taxonomy.yaml (probe sits just inside the band edge);
  cell→fixture-kind mapping is closed (wrong_high is the silent-archive
  failure mode under test).
* §72A arbiter scenarios — one per closed outcome (`stands`,
  `re_extract`, `escalate_human_review`) + a review-correction scenario.
* §58 failure-stage matrix — one minimal failure fixture per stage
  (ingestion → archival), the spine for first-pass vs. recovered success.
* All review/retry expectations are DERIVED through the same
  `mailroom_eda.eval_contract` module that produces the v0.2 columns — the
  fixtures and the corpus share one evaluation contract.

Splits: `md5(filename) % 10 == 0 → test` (HUB-028's published split
column is authoritative and preserved). Scaffold modules live in the
monorepo (`packages/mailroom-corpus-eda/src/mailroom_eda/`); methodology in
`docs/DOCCLASS_CONTRACT.md` §9/§9A/§9B/§9C.

"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-dir", type=Path, default=Path("/tmp/opencode/hub032_stage"))
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--commit-message", default=(
        "HUB-032: §84 hardening rebased onto the v8 GT base — reconciliation "
        "of the interleaved HUB-028/HUB-022 publishes"))
    args = parser.parse_args()

    rows = build_rows()
    facts = {r["matter_construction"] for r in rows}
    assert facts <= {"heuristic_reconstructed", ""}, facts

    stage_dir = args.stage_dir
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    counts = stage(rows, stage_dir)
    facts = verify(rows, stage_dir, counts)

    with (stage_dir / "ground_truth_hardened_v8.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(safe_jsonl_line(row) + "\n")

    # card: refresh ONLY the §84 section with live numbers (legacy title
    # present at tip — replace_card_section handles the rename)
    card = fetch_live_card(REPO_ID)
    assert card is not None, "live card unavailable"
    methods = ", ".join(
        f"`{k}` {v}" for k, v in sorted(facts["annotation_methods"].items())
    )
    body = CARD_BODY_TEMPLATE.format(
        rows=facts["rows"],
        grouped=facts["grouped_rows"],
        threads=facts["threads"],
        unassigned=facts["rows"] - facts["grouped_rows"],
        methods=methods,
    )
    (stage_dir / "README.md").write_text(replace_card_section(card, body), encoding="utf-8")

    # manifest: fresh GT shas + live tip shas for the unchanged configs
    from huggingface_hub import get_hf_file_metadata, hf_hub_url

    sha_lines = []
    for p in sorted(stage_dir.rglob("*")):
        if p.is_file() and p.name != "manifest_hardened.txt":
            sha_lines.append((str(p.relative_to(stage_dir)),
                              hashlib.sha256(p.read_bytes()).hexdigest()))
    for fn in ("parquet/bundles/train/train-00000-of-00001.parquet",
               "parquet/bundles/test/test-00000-of-00001.parquet",
               "parquet/fixtures/train/train-00000-of-00001.parquet",
               "parquet/fixtures/test/test-00000-of-00001.parquet",
               "bundles.jsonl", "fixtures.jsonl"):
        md = get_hf_file_metadata(hf_hub_url(REPO_ID, fn, repo_type="dataset"))
        # LFS objects carry the sha256 as the etag; non-LFS files expose the
        # git blob id (labeled below so the verification table stays honest)
        etag = (md.etag or "").strip('"')
        kind = "sha256" if len(etag) == 64 else "git-blob"
        sha_lines.append((f"{fn} (tip, unchanged, {kind})", etag))
    (stage_dir / "manifest_hardened.txt").write_text(
        "\n".join([
            "mailroom-corpus hardened-release manifest — v0.2/v0.3/v0.4 on the v8 base (HUB-032)",
            f"built_utc : {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"gt_base   : HUB-028 v8 GT revision {V8_GT_REVISION} (31 cols value-identical, asserted)",
            f"rows      : ground_truth {counts['train'] + counts['test']} "
            f"(train {counts['train']}, test {counts['test']}); bundles 50; fixtures 32 (tip, unchanged)",
            f"matter    : {facts['grouped_rows']} rows in {facts['threads']} threads; "
            f"{facts['rows'] - facts['grouped_rows']} unassigned; keys sha256-derived (stable)",
            f"methods   : {facts['annotation_methods']}",
            "sha256    :",
            *[f"  {k}  {v}" for k, v in sha_lines],
            "",
        ]),
        encoding="utf-8",
    )
    for k, v in sha_lines:
        print(f"  {k}  {v}")

    if args.publish:
        from mailroom_eda.hf_interface import get_hf_api, upload_folder

        upload_folder(get_hf_api(), stage_dir, REPO_ID, args.commit_message)
        print(f"published → https://huggingface.co/datasets/{REPO_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
