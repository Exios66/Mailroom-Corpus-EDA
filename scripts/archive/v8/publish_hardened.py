#!/usr/bin/env python3
"""CLI (ARCHIVED — frozen v8 baseline): stage/publish the §84 hardened release.

**Status: FROZEN.** This is the v8-era release tool for the frozen
``Lucius-Morningstar/mailroom-corpus`` baseline (HUB-022). It is preserved
for lineage/repro only — the live successor builder is
``scripts/build/build_v9.py`` (mailroom-dataset, v9). It is pinned to
``V8_REPO_ID`` explicitly: ``mailroom_eda.config.REPO_ID`` now points at the
v9 ``mailroom-dataset`` repo and must NEVER be used here.

The §84 release-chain implementations (identity → eval_contract → matter,
bundles/streams/fixtures builders, staging + verification) now live in
``mailroom_eda.hardened``; this CLI is a thin driver over them.

Delivers, in ONE repo revision (plan §26 — conceptual targets, not forced
releases): v0.2-mailroom-hardened (identity + evaluation contract on
ground_truth), v0.3-matter-aware (heuristic §14A grouping + bundles/streams
configs), v0.4-recovery-suite (fixtures config).

Laws enforced here:
- the blind `default` config is NOT staged — labels never ride blind;
- the original ground_truth columns are value-identical to the published
  snapshot (asserted per-row before staging);
- every new scalar column is string-typed with '' for absence; list columns
  are list<string>;
- JSONL sidecars go through safe_jsonl_line (KANBAN-088 line-boundary law).

Dry run (default) stages everything under --stage-dir and verifies it;
--publish uploads the staged folder via the centralized hf_interface and
prints the sha256 table for post-upload verification.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import importlib.util
_b = Path(__file__).resolve()
while not (_b / "_bootstrap.py").is_file():
    _b = _b.parent
sys.path.insert(0, str(_b))
from _bootstrap import ROOT  # noqa: E402

from mailroom_eda.config import V8_REPO_ID  # noqa: E402
from mailroom_eda.dataset_export import safe_jsonl_line  # noqa: E402
from mailroom_eda.docclass_uploader import upsert_section  # noqa: E402
from mailroom_eda.hardened import (  # noqa: E402
    build_bundle_rows,
    build_fixture_rows,
    build_stream_rows,
    enrich_gt_rows,
    load_snapshot_rows,
    stage_configs,
    verify_stage,
)
from mailroom_eda.release_sections import fetch_live_card  # noqa: E402

CARD_ANCHOR = "## Original files (KANBAN-105 addendum, 2026-08-30)"
CARD_HEADING = "## Mailroom evaluation hardening (v0.2/v0.3/v0.4, 2026-09-02)"


def snapshot_available() -> bool:
    gt = ROOT / "data" / "parquet" / "ground_truth"
    return gt.exists() and any(gt.rglob("*.parquet"))


CARD_BODY = """## Mailroom evaluation hardening (v0.2/v0.3/v0.4, 2026-09-02)

One revision delivering three §84 markers (plan §26: conceptual targets, not
forced releases). Blind `default` config is UNCHANGED (4 columns, no labels).

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
  (`verified_join` 162 / `llm_zero_shot` 92 / `human_annotated` 96 /
  `synthetic` 950 (DE-SynPUF 600 + GNOTHEIA 200 + BDR 150) /
  `source_native` 700) /`annotation_model`/
  `annotation_prompt_version`/`annotation_confidence`/`annotation_reviewer`/
  `annotation_timestamp`. Built on the v8 base (2,000 rows, HUB-028): the
  v8 LOB rows carry their own dataset as `source_corpus` /
  `annotation_source` (GNOTHEIA / BDR) and their pinned upstream
  `source_revision`; published v7 `document_id`s are unchanged (0 drift).

**v0.3-matter-aware** — §14A methodology (verified 2026-09-02 against the
raw CMU maildir: In-Reply-To/References are structurally ABSENT — 0/350 raw
files, 0/247,523 upstream dedup rows — so header-thread ground truth cannot
exist in this corpus family):
* `ground_truth` gains `matter_id`, `matter_construction`, `group_id`,
  `group_role`, `relationships` (list), `related_document_ids` (list),
  `thread_position`, `thread_size`, `thread_evidence`. Populated ONLY via
  `heuristic_reconstructed` (normalized subject + custodian + 30-day window,
  degenerate subjects excluded): **19 rows in 7 threads; 1,981 rows
  unassigned** (all threads are correspondence; the v8 insurance LOB rows
  carry no header-thread evidence) — honest baseline, counted separately,
  never merged into a
  "matters" total. `source_native_thread` stays implemented (guarded) for
  future feeds that carry real reply headers.
* NEW `bundles` config (§14 synthetic families, flagged
  `synthetic_constructed`): five family templates over REAL anchor rows
  (§14's legal/insurance worked examples span ≥2 classes) —
  legal_contract_family, insurance_claim_family, merger_family,
  corporate_record_family, correspondence_thread_family; seed 42, 2 anchors
  per family, one `template_variant` duplicate per instance (§87).
  Manufactured siblings carry `synthetic: true`, an explicit scaffold
  doc_text header, and `MATTER-SYN-*` filenames (no snapshot collisions);
  they claim NO source provenance (`source_corpus` = '') and
  `annotation_method` = `synthetic`. Roles/relationships come ONLY from the
  closed GROUP_ROLES/RELATIONSHIP_TYPES vocabularies.
* NEW `streams` config (§27–§29/§48 STREAM eval tier): the bundle members
  interleaved into ONE reproducible ingress stream — `RUN-SIM-001`,
  round-robin across the bundle matters (A1 B1 A2 C1 B2 ... — never
  matter-contiguous, §28), with `distractor` rows injected every 4
  positions (real corpus rows from outside every matter, `matter_id`/`group_id`
  empty, §29). Every row carries `simulation_run_id` +
  `sequence_position` (strictly increasing, §27 reproduces the exact
  incoming sequence).

**v0.4-recovery-suite** — NEW `fixtures` config (§68–§72A fixture content;
`fixture:` filename namespace, so these are evaluation scenarios, not
corpus rows):
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

Splits: `md5(filename) % 10 == 0 → test` applied uniformly (existing rows
keep their published split; new synthetic rows derive theirs the same way).
Scaffold modules live in the monorepo
(`packages/mailroom-corpus-eda/src/mailroom_eda/`); methodology in
`docs/DOCCLASS_CONTRACT.md` §9/§9A/§9B/§9C.

"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-dir", type=Path, default=Path("/tmp/opencode/v02_v03_v04_stage"))
    parser.add_argument("--publish", action="store_true",
                        help="upload the staged folder (centralized hf_interface)")
    parser.add_argument("--commit-message", default=(
        "§84 hardened release: v0.2-mailroom-hardened + v0.3-matter-aware "
        "+ v0.4-recovery-suite (HUB-022)"))
    args = parser.parse_args()

    if not snapshot_available():
        print("local HF snapshot absent (data/parquet) — fetch via run_all.py P0 first")
        return 1

    rows = enrich_gt_rows(load_snapshot_rows())
    bundle_rows, bundle_manifest = build_bundle_rows(rows)
    stream_rows, stream_manifest = build_stream_rows(bundle_rows, rows)
    fixture_rows = build_fixture_rows()

    stage_dir = args.stage_dir
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    counts = stage_configs(rows, bundle_rows, fixture_rows, stream_rows, stage_dir)
    verify_stage(rows, bundle_rows, fixture_rows, stream_rows, stage_dir, counts)

    for name, subset in (
        ("ground_truth_hardened.jsonl", rows),
        ("bundles.jsonl", bundle_rows),
        ("streams.jsonl", stream_rows),
        ("fixtures.jsonl", fixture_rows),
    ):
        with (stage_dir / name).open("w", encoding="utf-8") as fh:
            for row in subset:
                fh.write(safe_jsonl_line(row) + "\n")

    card = fetch_live_card(V8_REPO_ID)
    if card is not None:
        assert card.count(CARD_ANCHOR) == 1, "card anchor not unique — abort"
        (stage_dir / "README.md").write_text(
            upsert_section(card, CARD_HEADING, CARD_BODY, CARD_ANCHOR), encoding="utf-8")

    sha_table = {
        str(p.relative_to(stage_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(stage_dir.rglob("*")) if p.is_file()
    }
    (stage_dir / "manifest_hardened.txt").write_text(
        "\n".join([
            "mailroom-corpus hardened-release manifest (v0.2/v0.3/v0.4)",
            f"built_utc : {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"rows      : ground_truth {counts[('ground_truth', 'train')] + counts[('ground_truth', 'test')]}"
            f" (train {counts[('ground_truth', 'train')]}, test {counts[('ground_truth', 'test')]})"
            f"; bundles {counts[('bundles', 'train')] + counts[('bundles', 'test')]}"
            f"; streams {counts[('streams', 'train')] + counts[('streams', 'test')]}"
            f" ({stream_manifest['distractors']} distractors)"
            f"; fixtures {counts[('fixtures', 'train')] + counts[('fixtures', 'test')]}",
            f"matter    : {dict(Counter(r.get('matter_construction') or 'unassigned' for r in rows))}",
            f"bundles   : seed 42, families {bundle_manifest['families']}",
            f"streams   : {stream_manifest['run_id']} (members {stream_manifest['members']},"
            f" matters {stream_manifest['matters']}, distractors {stream_manifest['distractors']},"
            f" interleave round-robin)",
            f"fixtures  : {dict(Counter(r['fixture_kind'] for r in fixture_rows))}",
            "sha256    :",
            *[f"  {k}  {v}" for k, v in sha_table.items()],
            "",
        ]),
        encoding="utf-8",
    )
    # the manifest must hash AFTER the sha table is written — append its own
    # entry so post-upload verification covers the manifest too
    manifest_sha = hashlib.sha256(
        (stage_dir / "manifest_hardened.txt").read_bytes()).hexdigest()
    with (stage_dir / "manifest_hardened.txt").open("a", encoding="utf-8") as fh:
        fh.write(f"  manifest_hardened.txt  {manifest_sha} (self, excludes this line)\n")

    print(f"staged {len(sha_table)} files under {stage_dir}")
    for k, v in sha_table.items():
        print(f"  {k}  {v}")

    if args.publish:
        from mailroom_eda.hf_interface import get_hf_api, upload_folder

        upload_folder(get_hf_api(), stage_dir, V8_REPO_ID, args.commit_message)
        print(f"published → https://huggingface.co/datasets/{V8_REPO_ID}")
        print("verify each sha256 above against the Hub LFS objects "
              "(mailroom_eda.hf_interface.verify_hub_sha256)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())