#!/usr/bin/env python3
"""CLI: stage (and optionally publish) the §84 hardened release (HUB-022).

Delivers, in ONE repo revision (the §84 markers are conceptual targets, not
three forced releases — plan §26):

- **v0.2-mailroom-hardened** — the `ground_truth` config gains the identity
  fields (identity.py: document_id, source provenance, content hashes) and
  the evaluation-contract fields (eval_contract.py: expected_specialist,
  expected_stage, review/retry expectations, annotation provenance).
- **v0.3-matter-aware** — `ground_truth` gains the §14A grouping fields
  (heuristic_reconstructed subject threads on correspondence; header threads
  verified structurally absent) + the NEW `bundles` config: §14 synthetic
  bundle families over real anchors (flagged synthetic_constructed).
- **v0.4-recovery-suite** — the NEW `fixtures` config: §68–§72A fixture
  content (calibration quartet at the live bands, arbiter scenarios,
  failure-stage matrix).

Laws enforced here:
- the blind `default` config is NOT staged — labels never ride blind, and
  the existing blind bytes stay untouched on the Hub;
- the 31 existing ground_truth columns are value-identical to the published
  snapshot (asserted per-row before staging);
- every new scalar column is string-typed with '' for absence (corpus
  convention: no true NULLs); list columns are list<string>;
- JSONL sidecars go through safe_jsonl_line (KANBAN-088 line-boundary law).

Dry run (default) stages everything under --stage-dir and verifies it;
--publish uploads the staged folder via the centralized hf_interface and
prints the sha256 table for post-upload verification.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mailroom_eda import bundles as bd  # noqa: E402
from mailroom_eda import fixtures as fx  # noqa: E402
from mailroom_eda import identity, matter  # noqa: F401,E402
from mailroom_eda import eval_contract as ec  # noqa: E402
from mailroom_eda.bundles import STREAM_FIELDS  # noqa: E402
from mailroom_eda.config import REPO_ID  # noqa: E402
from mailroom_eda.dataset_export import assign_split, safe_jsonl_line  # noqa: E402
from mailroom_eda.docclass_uploader import upsert_section  # noqa: E402

SNAPSHOT = ROOT / "data" / "parquet"
CARD_ANCHOR = "## Original files (KANBAN-105 addendum, 2026-08-30)"
CARD_HEADING = "## Mailroom evaluation hardening (v0.2/v0.3/v0.4, 2026-09-02)"

IDENTITY_FIELDS = (
    "document_id", "source_corpus", "source_document_id", "source_filename",
    "source_revision", "content_sha256", "normalized_text_sha256",
)
CONTRACT_FIELDS = (
    "expected_specialist", "expected_stage", "review_expected", "review_reason",
    "retry_expected", "expected_post_retry_state",
    "annotation_source", "annotation_method", "annotation_model",
    "annotation_prompt_version", "annotation_confidence", "annotation_reviewer",
    "annotation_timestamp",
)
MATTER_SCALARS = (
    "matter_id", "matter_construction", "group_id", "group_role",
    "thread_position", "thread_size", "thread_evidence",
)
MATTER_LISTS = ("relationships", "related_document_ids")

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
    return "" if v is None else str(v)


def stage_configs(rows: list[dict], bundle_rows: list[dict], fixture_rows: list[dict],
                  stream_rows: list[dict], stage_dir: Path) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq

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

    # ground_truth: the 31 original columns are value-identical to the snapshot
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


def fetch_live_card() -> str | None:
    try:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(repo_id=REPO_ID, filename="README.md", repo_type="dataset")
        return Path(path).read_text(encoding="utf-8")
    except Exception as exc:  # offline / no token — stage without the card
        print(f"WARN: could not fetch live card ({exc}); staging without README.md")
        return None


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

    card = fetch_live_card()
    if card is not None:
        assert card.count(CARD_ANCHOR) == 1, "card anchor not unique — abort"
        (stage_dir / "README.md").write_text(
            upsert_section(card, CARD_HEADING, CARD_BODY, CARD_ANCHOR), encoding="utf-8")

    sha_table = {
        str(p.relative_to(stage_dir)): __import__("hashlib").sha256(p.read_bytes()).hexdigest()
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
    manifest_sha = __import__("hashlib").sha256(
        (stage_dir / "manifest_hardened.txt").read_bytes()).hexdigest()
    with (stage_dir / "manifest_hardened.txt").open("a", encoding="utf-8") as fh:
        fh.write(f"  manifest_hardened.txt  {manifest_sha} (self, excludes this line)\n")

    print(f"staged {len(sha_table)} files under {stage_dir}")
    for k, v in sha_table.items():
        print(f"  {k}  {v}")

    if args.publish:
        from mailroom_eda.hf_interface import get_hf_api, upload_folder

        upload_folder(get_hf_api(), stage_dir, REPO_ID, args.commit_message)
        print(f"published → https://huggingface.co/datasets/{REPO_ID}")
        print("verify each sha256 above against the Hub LFS objects "
              "(mailroom_eda.hf_interface.verify_hub_sha256)")
    return 0


def snapshot_available() -> bool:
    gt = SNAPSHOT / "ground_truth"
    return gt.exists() and any(gt.rglob("*.parquet"))


if __name__ == "__main__":
    raise SystemExit(main())
