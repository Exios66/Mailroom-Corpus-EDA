# modernbert/ — ModernBERT ingest fast-path: training set + fine-tune

The deployable ML piece of the ingest node: a **fine-tuned ModernBERT-base
hierarchical classifier** (doc_type + per-class subclass heads) trained on a
cleaned + prepared training set derived from the pinned
`Lucius-Morningstar/mailroom-dataset` corpus (v1/v9, tip `46a4d3c2`).

```
corpus (mailroom-dataset @ 46a4d3c2)
   │  prep.py        — labels (canonical), title, splits, 8,192-token windows
   ▼
mailroom-modernbert-training (HF, public)     ← publish.py
   │  train.py       — hierarchical heads, class-weighted loss, temp scaling
   ▼
mailroom-modernbert-classifier (HF, model)    ← modal_app.py (L4 GPU)
```

## Files

| File | Role |
|---|---|
| `prep.py` | Corpus → prepared training set (labels, splits, windows, staging). Canonical subclass vocab vendored from `llm-dojo-scoring` (DMR-066) — `Service`/`service`, `Co_Branding`/`co_branding`, `Joint Venture _ Filing` → `joint_venture`. |
| `publish.py` | Stage + publish the training set to HF via the centralized `mailroom_eda.hf_interface` helpers (§44A — never ad-hoc upload code). |
| `train.py` | Fine-tune: shared ModernBERT backbone + 6 heads, class-weighted CE, AdamW bf16 (CUDA), LR 2e-5, 6% warmup, early stop on val loss, per-head temperature scaling, ECE/macro-F1, held-out test gate. |
| `modal_app.py` | Dedicated Modal app (`modernbert-train`, L4 GPU) — bundles `src/` + `modernbert/`, pulls the published dataset, runs `train.py`, persists to the `modernbert-checkpoints` Volume + pushes to HF. |
| `tests/test_prep.py` | Determinism, split integrity, leakage, vocab conformance, window-budget invariants (full-corpus contract test). |

## The published dataset

`Lucius-Morningstar/mailroom-modernbert-training` (public, CC-BY-4.0):

- **`documents`** — 3,302 rows (train 2,680 / validation 299 / test 323):
  `filename`, `document_id`, `content_sha256`, `source_revision`, `title`,
  `doc_text`, `doc_type`, `subclass`, `corpus_split`, `split`,
  `token_estimate`.
- **`windows`** — 5,068 pre-computed 8,192-token windows (train 4,573 /
  validation 495): `text` = `title + "\n\n" + window` (512-token overlap,
  BPE-round-trip clamped so every window re-tokenizes ≤ 8,192 WITH specials).
  Test stays document-level — **held out entirely, never touches training**.
- Sidecars: `labels.json` (per-head id2label + inverse-frequency class
  weights over the train split), `vocabularies.json`, `manifest.txt`
  (byte-identical across rebuilds — no timestamp).

Splits: corpus train → 90/10 stratified by doc_type (seed 42, deterministic
`RandomState` — no sklearn dependency); corpus test (323) held out.

## Training

```bash
# local (CPU smoke)
.venv/bin/python modernbert/train.py --data data/modernbert_training/stage \
    --epochs 1 --max-length 512 --limit 96 --eval-test

# full run on Modal (L4)
HF_TOKEN=$(cat ~/.config/opencode/secrets/hf-token) \
    modal run modernbert/modal_app.py --epochs 5 \
    --push-to-hub Lucius-Morningstar/mailroom-modernbert-classifier
```

The classifier's output rides the pipeline's existing `intake_prep.triage`
slot; the LLM sorter stays the authority for the hard tail (messy docs,
confidence < 0.88, subclass tails with < 5 rows). No new graph nodes, no new
thresholds — the 0.88/0.97 confidence bands are reused verbatim.

## Verification gates

- `pytest modernbert/tests/test_prep.py` — prep invariants (9 tests incl.
  the full-corpus contract).
- `publish.py --publish` — byte-verifies every LFS parquet against the Hub.
- `train.py --eval-test` — held-out test doc_type/subclass accuracy (P0
  gate: doc_type ≥ 0.95, subclass ≥ 0.75 before deployment).