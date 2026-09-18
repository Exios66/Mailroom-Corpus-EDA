#!/usr/bin/env python3
"""CLI: stage + publish the ModernBERT training set to HuggingFace.

Usage:
    .venv/bin/python modernbert/publish.py --stage-only
    .venv/bin/python modernbert/publish.py --publish          # create + upload + verify
    .venv/bin/python modernbert/publish.py --publish --repo-id Lucius-Morningstar/mailroom-modernbert-training

Stage-only by default; --publish creates the (public) dataset repo and
uploads the verified tree via the centralized mailroom_eda.hf_interface
helpers — never ad-hoc upload code (§44A).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# modernbert/ scripts sit outside scripts/, so the shared _bootstrap.py
# preamble (which walks up looking for _bootstrap.py) does not apply — anchor
# on the repo root (.git) instead and put src/ + modernbert/ on sys.path.
_b = Path(__file__).resolve()
while not (_b / ".git").is_dir():
    _b = _b.parent
ROOT = _b
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "modernbert"))

from mailroom_eda.config import DATA_DIR, REPO_ID, REPO_REVISION  # noqa: E402
from mailroom_eda.hf_interface import (  # noqa: E402
    create_dataset_repo,
    get_hf_api,
    sha256_file,
    upload_folder,
    verify_hub_sha256,
)

sys.path.insert(0, str(ROOT / "modernbert"))
from prep import (  # noqa: E402
    DOC_TYPES,
    MAX_TOKENS,
    MODEL_ID,
    SUBCLASS_BY_CLASS,
    WINDOW_OVERLAP_TOKENS,
    load_corpus_rows,
    stage,
    verify_stage,
)

DEFAULT_REPO_ID = "Lucius-Morningstar/mailroom-modernbert-training"
STAGE_DIR = DATA_DIR / "modernbert_training" / "stage"


def render_card(stats: dict, repo_id: str) -> str:
    counts = stats["counts"]
    docs = counts["documents"]
    wins = counts.get("windows", {})
    n_train = docs.get("train", 0)
    n_val = docs.get("validation", 0)
    n_test = docs.get("test", 0)
    return f"""---
license: cc-by-4.0
language:
- en
task_categories:
- text-classification
tags:
- legal
- mailroom
- modernbert
- hierarchical-classification
pretty_name: mailroom-modernbert-training
---

# mailroom-modernbert-training

Cleaned + prepared hierarchical-classification training set for the
**ModernBERT-base** ingest fast-path, derived from the pinned
[`Lucius-Morningstar/mailroom-dataset`](https://huggingface.co/datasets/{REPO_ID})
corpus (v1, canonically v9; tip `{REPO_REVISION[:8]}`).

| | |
|---|---|
| Documents | {n_train + n_val + n_test} (train {n_train} / validation {n_val} / test {n_test}) |
| Windows (8,192-token) | {sum(wins.values())} (train {wins.get('train', 0)} / validation {wins.get('validation', 0)}) |
| doc_type classes | {len(DOC_TYPES)} (+ `unknown`, inference-only) |
| Subclass heads | {len(SUBCLASS_BY_CLASS)} per-class heads |
| Model target | {MODEL_ID} (max_tokens={MAX_TOKENS}, overlap={WINDOW_OVERLAP_TOKENS}) |
| Source revision | `{REPO_REVISION}` (GT-closure) |

## Configs

- **`documents`** — one row per document: `filename`, `document_id`,
  `content_sha256`, `source_revision`, `title`, `doc_text`, `doc_type`,
  `subclass`, `corpus_split`, `split` (train/validation/test),
  `token_estimate`. The held-out **test split (323 rows) never touches
  training** — it is the eval surface.
- **`windows`** — one row per pre-computed 8,192-token ModernBERT window
  (`title + "\\n\\n" + window`, 512-token overlap) for **train + validation
  only** — the fine-tune surface. Test stays document-level.

## Labels

- `doc_type` — 5 classes: {", ".join(DOC_TYPES)}.
- `subclass` — canonical key per class, normalized through the same mapping
  the sandbox corpus uses (`llm_dojo_scoring.corpus.normalize_corpus_subclass`,
  DMR-066; vendored in `modernbert/prep.py`): `Service`/`service` unify,
  `Co_Branding` → `co_branding`, `Joint Venture _ Filing` → `joint_venture`.
  Per-class vocabularies in `vocabularies.json`; per-head id2label + class
  weights (inverse-frequency over the train split) in `labels.json`.

## Splits

- Corpus `train` (2,979) → **90/10 stratified** train/validation (stratified
  by `doc_type`, seed 42, deterministic `RandomState` shuffle — no sklearn
  dependency, byte-identical rebuilds).
- Corpus `test` (323) → held out entirely.

## Input construction

`title` follows the corpus's title-wins convention: **subject →
exhibit_description → filename**. Each window input is
`title + "\\n\\n" + window_text`, truncated to 8,192 tokens — the same
title-first doctrine the pipeline sorter uses.

## Provenance

Built by `modernbert/prep.py` + `modernbert/publish.py` in
[`Exios66/Mailroom-Corpus-EDA`](https://github.com/Exios66/Mailroom-Corpus-EDA)
(monorepo mirror: `packages/mailroom-corpus-eda`). `manifest.txt` carries the
build facts + sha256s; rebuilds are byte-identical (sorted rows, seeded
split, pinned tokenizer).
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--publish", action="store_true",
                    help="create the dataset repo + upload + verify")
    ap.add_argument("--repo-id", default=DEFAULT_REPO_ID)
    ap.add_argument("--stage-only", action="store_true",
                    help="build the staged tree under data/modernbert_training/stage")
    ap.add_argument("--no-windows", action="store_true",
                    help="skip the windows config (documents only)")
    args = ap.parse_args()

    stats = stage(STAGE_DIR, with_windows=not args.no_windows)
    print(f"staged: {json.dumps(stats['counts'], sort_keys=True)}")
    print(f"manifest sha256: {stats['manifest_sha256']}")

    check = verify_stage(STAGE_DIR)
    if not check["ok"]:
        print("VERIFY FAILED:")
        for p in check["problems"]:
            print(f"  - {p}")
        return 1
    print(f"verify ok: {check['rows']} rows, splits {check['splits']}")

    if not args.publish:
        print("stage-only (pass --publish to upload to HF)")
        return 0

    api = get_hf_api()
    create_dataset_repo(api, args.repo_id, private=False)
    print(f"repo ready: https://huggingface.co/datasets/{args.repo_id}")

    card = render_card(stats, args.repo_id)
    (STAGE_DIR / "README.md").write_text(card, encoding="utf-8")

    upload_folder(
        api,
        STAGE_DIR,
        args.repo_id,
        commit_message=(
            f"ModernBERT training set from {REPO_ID} @ {REPO_REVISION[:8]} "
            f"(documents {stats['counts']['documents']}, "
            f"windows {stats['counts'].get('windows', {})})"
        ),
    )
    print("uploaded; verifying sha256s...")
    results = []
    for rel in ("manifest.txt", "labels.json", "vocabularies.json", "README.md"):
        res = verify_hub_sha256(api, args.repo_id, rel, sha256_file(STAGE_DIR / rel))
        results.append(res)
        print(f"  {rel}: {res['status']} (local {res['local_sha256']} vs hub {res['hub_sha256']})")
    if not all(r["verified"] for r in results if r["status"] != "sha-not-exposed"):
        print("WARNING: some files could not be byte-verified against the Hub")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())