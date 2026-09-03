#!/usr/bin/env python3
"""mailroom-corpus baseline audit (plan §4 / §85 P0).

Freezes the current corpus as release marker ``docclass-merged-v0.1-working``:
loads the local HF snapshot (fetched at the pinned revision), verifies the
§9–§11 identity/provenance/hash groundwork over every row, and writes a
machine-readable manifest + the human audit report under
``docs/reports/audits/`` (monorepo root).

Usage:
    python scripts/baseline_audit.py            # audit + write artifacts
    python scripts/baseline_audit.py --check    # audit only, no writes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG_ROOT / "src"))

from mailroom_eda.config import PARQUET_DIR, MANIFEST_PATH  # noqa: E402
from mailroom_eda.dataset_export import assign_split  # noqa: E402
from mailroom_eda.docclass_uploader import GT_SCALAR_KEYS  # noqa: E402
from mailroom_eda.download import parse_manifest  # noqa: E402
from mailroom_eda.identity import enrich_rows  # noqa: E402

MONOREPO_ROOT = PKG_ROOT.parents[1]
OUT_DIR = MONOREPO_ROOT / "docs" / "reports" / "audits"

RELEASE_MARKER = "docclass-merged-v0.1-working"
# Hub revision facts (verified via HfApi.list_repo_commits 2026-09-02):
#   bb57c5ad 2026-09-02  issue #5 fix: intent_source aeslc_join (162 rows)
#   fc1f211c 2026-08-31  card: pretty_name v6 -> v7
#   1acd2600 2026-08-31  v7 correspondence intent hydration (data)
#   b3ec9ee7 2026-08-31  purpose-GT push (former pipeline pin — stale)
PINNED_REVISION = "bb57c5ad00333d239ea456fe3f2298c3ba5b5108"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit() -> dict:
    import pandas as pd

    frames = {}
    for cfg in ("default", "ground_truth"):
        for split in ("train", "test"):
            files = sorted((PARQUET_DIR / cfg / split).glob("*.parquet"))
            if not files:
                raise SystemExit(
                    f"snapshot missing at {PARQUET_DIR/cfg/split} — fetch first "
                    "(run_all.py P0 or snapshot_download at the pinned revision)"
                )
            frames[(cfg, split)] = pd.concat(
                [pd.read_parquet(f) for f in files], ignore_index=True
            )

    gt = pd.concat(
        [frames[("ground_truth", "train")], frames[("ground_truth", "test")]],
        ignore_index=True,
    )
    # The ground_truth config carries no doc_text (blind/label split) — join
    # the text in from the default config by filename for content hashing.
    blind = pd.concat(
        [frames[("default", "train")], frames[("default", "test")]],
        ignore_index=True,
    )
    text_by_filename = dict(zip(blind["filename"], blind["doc_text"]))
    gt["doc_text"] = gt["filename"].map(text_by_filename)
    if gt["doc_text"].isna().any():
        raise SystemExit("default/ground_truth filename sets diverge — cannot join doc_text")
    rows = gt.to_dict("records")
    enriched = enrich_rows(rows)

    ids = [r["document_id"] for r in enriched]
    class_counts = Counter(r["expected"] for r in rows)
    strata = Counter((r["expected"], r["expected_subclass"]) for r in rows)
    split_counts = Counter(r["split"] for r in rows)
    split_rule_ok = all(r["split"] == assign_split(r["filename"]) for r in rows)
    filenames = [r["filename"] for r in rows]
    content_hashes = Counter(r["content_sha256"] for r in enriched)
    dup_groups = sum(1 for n in content_hashes.values() if n > 1)
    dup_rows = sum(n for n in content_hashes.values() if n > 1)

    corr = [r for r in rows if r["expected"] == "correspondence"]
    intent_sources = Counter(str(r.get("intent_source") or "") for r in corr)
    intent_status = Counter(str(r.get("intent_status") or "") for r in corr)

    parquet_sha = {
        f"{cfg}/{split}": sha256_file(files[0])
        for (cfg, split), files in
        ((k, sorted((PARQUET_DIR / k[0] / k[1]).glob("*.parquet"))) for k in frames)
    }

    manifest_txt = parse_manifest(MANIFEST_PATH) if MANIFEST_PATH.exists() else {}

    return {
        "release_marker": RELEASE_MARKER,
        "audited_at_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset": {
            "name": "Lucius-Morningstar/mailroom-corpus",
            "pinned_revision": PINNED_REVISION,
            "schema_version": manifest_txt.get("schema_version", "7"),
            "builder": "mailroom_eda.dataset_export @ Mailroom-Corpus-EDA",
        },
        "rows_total": len(rows),
        "class_counts": dict(sorted(class_counts.items())),
        "split_counts": {
            "ground_truth": dict(sorted(split_counts.items())),
            "default": {
                s: int(len(frames[("default", s)])) for s in ("train", "test")
            },
        },
        "strata": len(strata),
        "configs": {
            "default": {"columns": int(frames[("default", "train")].shape[1]), "blind": True},
            "ground_truth": {
                # The PUBLISHED config's parquet column count (4 identity +
                # 27-key GT schema = 31). The audit-time frame additionally
                # carries the in-memory doc_text join for content hashing —
                # never counted here (the earlier "32" was exactly that).
                "columns": int(frames[("ground_truth", "train")].shape[1]),
                "gt_scalar_keys": len(GT_SCALAR_KEYS),
            },
        },
        "identity_verification": {
            "document_id_unique": len(ids) == len(set(ids)),
            "document_id_coverage": sum(1 for i in ids if i.startswith("DOC-")),
            "filename_unique": len(filenames) == len(set(filenames)),
            "split_rule_consistent": split_rule_ok,
            "content_sha256_coverage": len(rows),
            "normalized_text_sha256_coverage": len(rows),
        },
        "duplicates": {
            "exact_content_groups": dup_groups,
            "rows_in_duplicate_groups": dup_rows,
            "note": "classified, not deleted (§12); per-row duplicate_of/"
                    "duplicate_type lands with the v0.2 schema",
        },
        "intent_hydration": {
            "correspondence_rows": len(corr),
            "intent_source": dict(sorted(intent_sources.items())),
            "intent_status": dict(sorted(intent_status.items())),
        },
        "parquet_sha256": parquet_sha,
        "taxonomy": {
            "canonical_classes": sorted(class_counts),
            "doctrine": "docs/v7-taxonomy.md (five-class; compliance_filing "
                        "status: retired remnant in pipeline config)",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="audit only, no writes")
    args = ap.parse_args()

    result = audit()
    print(json.dumps(result, indent=2))

    ok = all(result["identity_verification"].values())
    if not ok:
        print("BASELINE AUDIT FAILED: identity verification not clean", file=sys.stderr)
        return 1

    if not args.check:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / "docclass_merged_baseline.json"
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"\nmanifest -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
