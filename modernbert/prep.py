"""ModernBERT training-set preparation (mailroom-dataset v9 -> fine-tune set).

Builds the cleaned + prepared hierarchical-classification training set for
the ModernBERT-base ingest fast-path (see ``modernbert/README.md``):

- **Source**: ``Lucius-Morningstar/mailroom-dataset`` @ ``46a4d3c2…``
  (``ground_truth`` config, both splits) — the exact pinned corpus the
  pipeline's eval harness consumes (sha-verified snapshot).
- **Labels**: ``expected`` -> ``doc_type`` (5 classes); ``expected_subclass``
  -> canonical subclass key per class, normalized through the SAME mapping
  the sandbox corpus uses (``llm_dojo_scoring.corpus.normalize_corpus_subclass``,
  DMR-066) so ``Service``/``service`` and ``Co_Branding``/``co_branding``
  unify. The canonical tables are vendored here (cited below) so this
  standalone repo never imports the dojo package.
- **Inputs**: ``title`` (subject -> exhibit_description -> filename, the
  corpus's own title-wins signals) + full ``doc_text``; the ``windows``
  config carries the pre-computed 8,192-token ModernBERT windows
  (``title + "\n\n" + window``, 512-token overlap) for train+validation.
- **Splits**: corpus ``train`` (2,979) -> 90/10 stratified train/validation
  (stratified by ``doc_type``, seeded 42, deterministic RandomState); corpus
  ``test`` (323) is held out ENTIRELY — it never touches training.
- **Determinism**: sorted rows, seeded split, byte-identical rebuilds.

Output layout (staged under ``data/modernbert_training/stage``):

    parquet/documents/{train,validation,test}/*.parquet   one row per document
    parquet/windows/{train,validation}/*.parquet          one row per window
    labels.json        id2label per head + class weights (train split)
    vocabularies.json  canonical subclass vocab per doc_type
    manifest.txt       build facts + sha256s
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mailroom_eda.config import DATA_DIR, REPO_ID, REPO_REVISION, RANDOM_STATE
from mailroom_eda.dataset_export import safe_jsonl_line
from mailroom_eda.download import load_default, load_ground_truth

# ---------------------------------------------------------------------------
# Canonical vocabularies — vendored from llm-dojo-scoring (DMR-066):
#   llm_dojo_scoring/config.py      CONTRACT_SUBTYPES / SUBTYPE_ALIASES /
#                                   MAUD_CONSIDERATION_TYPES
#   llm_dojo_scoring/corpus.py      DOC_TYPE_SUBCLASSES /
#                                   normalize_corpus_subclass
#   llm_dojo_scoring/equivalences.py normalize_subtype / normalize_doc_subclass
# The corpus GT surfaces (CORPUS_SUBCLASS_SURFACES) all resolve through these
# tables; tests pin every surface value to its canonical key.
# ---------------------------------------------------------------------------

MODEL_ID = "answerdotai/ModernBERT-base"
MAX_TOKENS = 8192
WINDOW_OVERLAP_TOKENS = 512
VAL_FRACTION = 0.1

DOC_TYPES: tuple[str, ...] = (
    "contract", "merger_agreement", "corporate_record", "correspondence",
    "insurance_claim",
)

# 25 CUAD families (canonical snake_case keys) — dojo CONTRACT_SUBTYPE_KEYS.
CONTRACT_SUBTYPE_KEYS: tuple[str, ...] = (
    "affiliate", "agency", "co_branding", "collaboration", "consulting",
    "development", "distributor", "endorsement", "franchise", "hosting",
    "ip", "joint_venture", "license", "maintenance", "manufacturing",
    "marketing", "non_compete_no_solicit", "outsourcing", "promotion",
    "reseller", "service", "sponsorship", "strategic_alliance", "supply",
    "transportation",
)

# CUAD folder-name aliases -> canonical key — dojo SUBTYPE_ALIASES.
SUBTYPE_ALIASES: dict[str, str] = {
    "affiliate_agreements": "affiliate",
    "affiliate_agreement": "affiliate",
    "agency_agreements": "agency",
    "co_branding": "co_branding",
    "collaboration": "collaboration",
    "consulting_agreements": "consulting",
    "development": "development",
    "distributor": "distributor",
    "endorsement": "endorsement",
    "endorsement_agreement": "endorsement",
    "franchise": "franchise",
    "hosting": "hosting",
    "ip": "ip",
    "joint_venture": "joint_venture",
    "joint_venture_filing": "joint_venture",
    "license_agreements": "license",
    "maintenance": "maintenance",
    "manufacturing": "manufacturing",
    "marketing": "marketing",
    "non_compete_non_solicit": "non_compete_no_solicit",
    "outsourcing": "outsourcing",
    "promotion": "promotion",
    "reseller": "reseller",
    "service": "service",
    "sponsorship": "sponsorship",
    "strategic_alliance": "strategic_alliance",
    "supply": "supply",
    "transportation": "transportation",
}

# MAUD Type-of-Consideration keys — dojo MAUD_CONSIDERATION_TYPES.
MAUD_CONSIDERATION_TYPES: tuple[str, ...] = (
    "all_cash", "all_stock", "mixed_cash_stock", "mixed_cash_stock_election",
    "other",
)

# Canonical subclass keys per doc_type — dojo DOC_TYPE_SUBCLASSES (corpus
# classes only; the corpus ships a subset of the full enum).
SUBCLASS_BY_CLASS: dict[str, tuple[str, ...]] = {
    "contract": CONTRACT_SUBTYPE_KEYS + ("other",),
    "merger_agreement": MAUD_CONSIDERATION_TYPES,
    "corporate_record": (
        "bylaws", "articles_of_incorporation", "certificate_of_formation",
        "charter_amendment", "powers_of_attorney", "subsidiary_list",
        "rights_instrument", "indenture", "board_resolution",
        "officer_certificate", "other",
    ),
    "correspondence": (
        "email", "memo", "letter", "notice", "demand", "attorney_demand",
        "press_release", "meeting_request", "other",
    ),
    "insurance_claim": ("carrier", "inpatient", "outpatient", "pde",
                        "property", "auto"),
}

_ALIAS_KEY_RE = re.compile(r"[^a-z0-9]")

# Canonical label -> human label (contract families) for the card + id2label.
CONTRACT_SUBTYPE_LABELS: dict[str, str] = {
    "affiliate": "Affiliate Agreement",
    "agency": "Agency Agreement",
    "co_branding": "Co-Branding Agreement",
    "collaboration": "Collaboration / Cooperation Agreement",
    "consulting": "Consulting Agreement",
    "development": "Development Agreement",
    "distributor": "Distributor Agreement",
    "endorsement": "Endorsement Agreement",
    "franchise": "Franchise Agreement",
    "hosting": "Hosting Agreement",
    "ip": "IP Agreement",
    "joint_venture": "Joint Venture Agreement",
    "license": "License Agreement",
    "maintenance": "Maintenance Agreement",
    "manufacturing": "Manufacturing Agreement",
    "marketing": "Marketing Agreement",
    "non_compete_no_solicit": "Non-Compete / No-Solicit / Non-Disparagement Agreement",
    "outsourcing": "Outsourcing Agreement",
    "promotion": "Promotion Agreement",
    "reseller": "Reseller Agreement",
    "service": "Service Agreement",
    "sponsorship": "Sponsorship Agreement",
    "strategic_alliance": "Strategic Alliance Agreement",
    "supply": "Supply Agreement",
    "transportation": "Transportation Agreement",
}


def normalize_subclass(doc_type: str, value: Any) -> str:
    """Canonical subclass key for ``value`` under ``doc_type``.

    Replicates ``llm_dojo_scoring.corpus.normalize_corpus_subclass``:
    contract surfaces resolve through the CUAD alias table (case/separator
    folded), every other class through case-folded exact match against its
    canonical enum; unresolvable values become ``other``.
    """
    if value is None:
        return "other"
    raw = str(value).strip()
    if not raw:
        return "other"
    if doc_type == "contract":
        key = _ALIAS_KEY_RE.sub("", raw.lower())
        if not key:
            return "other"
        if key in CONTRACT_SUBTYPE_KEYS:
            return key
        aliases = {_ALIAS_KEY_RE.sub("", k): v for k, v in SUBTYPE_ALIASES.items()}
        if key in aliases:
            return aliases[key]
        for subtype in CONTRACT_SUBTYPE_KEYS:
            norm_label = _ALIAS_KEY_RE.sub("", CONTRACT_SUBTYPE_LABELS[subtype].lower())
            if key == norm_label or key.startswith(norm_label[:8]):
                return subtype
        return "other"
    allowed = SUBCLASS_BY_CLASS.get(doc_type, ())
    if raw in allowed:
        return raw
    key = _ALIAS_KEY_RE.sub("", raw.lower())
    for candidate in allowed:
        if key == _ALIAS_KEY_RE.sub("", candidate.lower()):
            return candidate
    return "other"


def build_title(row: dict) -> str:
    """Title-wins signal: subject -> exhibit_description -> filename.

    Mirrors the corpus's own title conventions (the sorter prompt's
    title-wins doctrine): correspondence carries real subject lines, EDGAR
    exhibits carry exhibit descriptions, synthetic renders carry the
    subclass in the filename.
    """
    for key in ("subject", "exhibit_description"):
        v = str((row.get("metadata") or {}).get(key) or "").strip()
        if v:
            return v
    return str(row.get("filename") or "")


def load_corpus_rows() -> list[dict]:
    """GT rows + doc_text + title metadata, joined by filename (sorted)."""
    gt = load_ground_truth()
    # gt_fields expansion can mirror top-level matter columns (relationships,
    # related_document_ids) — keep the canonical top-level values.
    gt = gt.loc[:, ~gt.columns.duplicated(keep="first")]
    blind = load_default()
    text_by_fn = dict(zip(blind["filename"], blind["doc_text"]))
    md_by_fn = dict(zip(blind["filename"], blind["metadata"]))
    rows = []
    for r in gt.sort_values("filename").to_dict("records"):
        fn = str(r["filename"])
        r["doc_text"] = str(text_by_fn.get(fn, ""))
        r["metadata"] = md_by_fn.get(fn) or {}
        rows.append(r)
    return rows


def stratified_split(df: pd.DataFrame, stratify_col: str = "doc_type",
                     val_fraction: float = VAL_FRACTION,
                     seed: int = RANDOM_STATE) -> pd.DataFrame:
    """Deterministic 90/10 stratified split (by class, seeded RandomState).

    Per class: rows sorted by filename, shuffled with ``np.random.RandomState
    (seed)`` (the legacy RandomState algorithm is frozen across numpy
    versions — no sklearn dependency, byte-stable rebuilds), first
    ``round(n * val_fraction)`` rows -> validation. Returns a copy with the
    ``split`` column set to train/validation.
    """
    rng = np.random.RandomState(seed)
    out = []
    for _, grp in df.sort_values("filename").groupby(stratify_col, sort=True):
        idx = grp.index.tolist()
        rng.shuffle(idx)
        n_val = int(round(len(idx) * val_fraction))
        val = set(idx[:n_val])
        grp = grp.copy()
        grp["split"] = np.where(grp.index.isin(val), "validation", "train")
        out.append(grp)
    return pd.concat(out).sort_values("filename").reset_index(drop=True)


def estimate_tokens(text: str, chars_per_token: float = 4.0) -> int:
    """Chars/4 heuristic token estimate (tiktoken-o200k-accurate at scale)."""
    return max(1, int(round(len(text) / chars_per_token)))


def _tokenizer():
    """ModernBERT tokenizer (cached); None when transformers is absent."""
    if _tokenizer.cache is None:
        try:
            from transformers import AutoTokenizer
            tok = AutoTokenizer.from_pretrained(MODEL_ID)
            # full-doc tokenization for windowing is intentionally longer than
            # the model context — silence the per-call warning (we chunk).
            tok.model_max_length = 1 << 30
            _tokenizer.cache = tok
        except Exception:  # noqa: BLE001 — train deps not installed
            _tokenizer.cache = False
    return _tokenizer.cache or None


_tokenizer.cache = None  # type: ignore[attr-defined]


def window_document(title: str, doc_text: str, max_tokens: int = MAX_TOKENS,
                    overlap: int = WINDOW_OVERLAP_TOKENS) -> list[str]:
    """Token-level windows: ``title + "\\n\\n" + window`` per chunk.

    The full input (title + body) is tokenized once; chunks of
    ``max_tokens`` with ``overlap``-token overlap are decoded back to text.
    BPE decode->re-encode is not idempotent, so each window's text is
    re-tokenized and clamped to ``max_tokens`` — every published window is
    guaranteed to fit the model's context. Deterministic for a pinned
    tokenizer. A doc at or under budget yields a single window (title +
    head).
    """
    tok = _tokenizer()
    if tok is None:
        raise RuntimeError("transformers not installed — windowing needs the "
                           "ModernBERT tokenizer (pip install -r requirements-train.txt)")
    full = f"{title}\n\n{doc_text}" if title else doc_text
    ids = tok(full, add_special_tokens=False)["input_ids"]
    if not ids:
        return [full]
    if len(ids) <= max_tokens:
        return [full]
    # headroom for the title + the tokenizer's default specials (<s></s>):
    # every published window re-tokenizes to <= max_tokens WITH specials.
    n_title = len(tok(title, add_special_tokens=False)["input_ids"]) if title else 0
    body_budget = max_tokens - 2 - n_title
    step = max_tokens - overlap
    windows = []
    for start in range(0, len(ids), step):
        chunk = ids[start:start + max_tokens]
        body = tok.decode(chunk, skip_special_tokens=True)
        # BPE decode->re-encode is not idempotent: clamp the BODY (never the
        # title — the raw title string is re-attached verbatim) so the
        # decorated window fits the model context with specials.
        re_ids = tok(body, add_special_tokens=False)["input_ids"]
        if len(re_ids) > body_budget:
            body = tok.decode(re_ids[:body_budget], skip_special_tokens=True)
        decorated = f"{title}\n\n{body}" if title else body
        # BPE is not compositional across the "\n\n" boundary (the separator
        # can add 1-2 tokens): verify the DECORATED string and trim the body
        # tail until it fits — deterministic, terminates (body shrinks).
        while len(tok(decorated)["input_ids"]) > max_tokens - 2:
            body_ids = tok(body, add_special_tokens=False)["input_ids"]
            body = tok.decode(body_ids[:-8], skip_special_tokens=True)
            decorated = f"{title}\n\n{body}" if title else body
        windows.append(decorated)
        if start + max_tokens >= len(ids):
            break
    return windows


def build_documents(rows: list[dict]) -> pd.DataFrame:
    """One row per document: labels (canonical), title, text, split, stats."""
    recs = []
    for r in rows:
        doc_type = str(r["expected"])
        recs.append({
            "filename": str(r["filename"]),
            "document_id": str(r.get("document_id") or ""),
            "content_sha256": str(r.get("content_sha256") or ""),
            "source_revision": str(r.get("source_revision") or REPO_REVISION),
            "title": build_title(r),
            "doc_text": str(r["doc_text"]),
            "doc_type": doc_type,
            "subclass": normalize_subclass(doc_type, r.get("expected_subclass")),
            "corpus_split": str(r.get("split") or ""),
            "token_estimate": estimate_tokens(str(r["doc_text"])),
        })
    df = pd.DataFrame(recs).sort_values("filename").reset_index(drop=True)
    # corpus test rows are held out entirely; train rows get the 90/10 split.
    train = stratified_split(df[df["corpus_split"] == "train"].copy())
    test = df[df["corpus_split"] == "test"].copy()
    test["split"] = "test"
    return pd.concat([train, test]).sort_values("filename").reset_index(drop=True)


def build_windows(docs: pd.DataFrame, max_tokens: int = MAX_TOKENS,
                  overlap: int = WINDOW_OVERLAP_TOKENS) -> pd.DataFrame:
    """One row per 8,192-token window (train+validation only — the fine-tune
    surface; the held-out test split stays document-level)."""
    recs = []
    for r in docs[docs["split"] != "test"].to_dict("records"):
        windows = window_document(r["title"], r["doc_text"], max_tokens, overlap)
        for i, text in enumerate(windows):
            recs.append({
                "filename": r["filename"],
                "window_index": i,
                "n_windows": len(windows),
                "text": text,
                "doc_type": r["doc_type"],
                "subclass": r["subclass"],
                "split": r["split"],
                "window_tokens": estimate_tokens(text),
            })
    return pd.DataFrame(recs).sort_values(["filename", "window_index"]).reset_index(drop=True)


def class_weights(df: pd.DataFrame, label_col: str) -> dict[str, float]:
    """Inverse-frequency class weights over the TRAIN split (val/test excluded)."""
    counts = Counter(df[df["split"] == "train"][label_col])
    total = sum(counts.values())
    weights = {k: total / (len(counts) * v) for k, v in counts.items()}
    return dict(sorted(weights.items()))


def label_maps(docs: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Per-head id2label/label2id/weights (doc_type + one head per class)."""
    heads: dict[str, dict[str, Any]] = {}
    doc_labels = list(DOC_TYPES) + ["unknown"]  # unknown = inference-only
    heads["doc_type"] = {
        "labels": doc_labels,
        "id2label": {i: k for i, k in enumerate(doc_labels)},
        "label2id": {k: i for i, k in enumerate(doc_labels)},
        "weights": class_weights(docs, "doc_type"),
        "note": "unknown is inference-time only (zero training rows)",
    }
    for cls in DOC_TYPES:
        labels = list(SUBCLASS_BY_CLASS[cls])
        heads[cls] = {
            "labels": labels,
            "id2label": {i: k for i, k in enumerate(labels)},
            "label2id": {k: i for i, k in enumerate(labels)},
            "weights": class_weights(docs[docs["doc_type"] == cls], "subclass"),
            "note": "fires only when doc_type predicts this class",
        }
    return heads


def stage(stage_dir: Path, rows: list[dict] | None = None,
          with_windows: bool = True) -> dict:
    """Build + write the staged training set (parquet + sidecars + manifest).

    Returns a stats dict (row counts, sha256s) for the publish CLI.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    if rows is None:
        rows = load_corpus_rows()
    docs = build_documents(rows)
    maps = label_maps(docs)

    def _write(df: pd.DataFrame, cfg: str, split: str) -> int:
        d = stage_dir / "parquet" / cfg / split
        d.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False),
                       d / f"{split}-00000-of-00001.parquet")
        return len(df)

    counts: dict[str, dict[str, int]] = {}
    for split in ("train", "validation", "test"):
        counts.setdefault("documents", {})[split] = _write(
            docs[docs["split"] == split], "documents", split)
    if with_windows:
        wins = build_windows(docs)
        for split in ("train", "validation"):
            counts.setdefault("windows", {})[split] = _write(
                wins[wins["split"] == split], "windows", split)

    sidecars = {
        "labels.json": json.dumps(maps, sort_keys=True, indent=2),
        "vocabularies.json": json.dumps(
            {k: list(v) for k, v in SUBCLASS_BY_CLASS.items()},
            sort_keys=True, indent=2),
    }
    for name, content in sidecars.items():
        (stage_dir / name).write_text(content + "\n", encoding="utf-8")

    manifest = build_manifest(docs, counts, maps)
    (stage_dir / "manifest.txt").write_text(manifest, encoding="utf-8")
    return {"counts": counts, "manifest_sha256": _sha256(stage_dir / "manifest.txt")}


def build_manifest(docs: pd.DataFrame, counts: dict, maps: dict) -> str:
    types = Counter(docs["doc_type"])
    strata = Counter(zip(docs["doc_type"], docs["subclass"]))
    n_windows = sum(counts.get("windows", {}).values())
    # NOTE: no built_utc timestamp — the manifest must be byte-identical
    # across rebuilds (determinism law); the publish commit carries the time.
    return f"""mailroom-modernbert-training manifest
==================================================
source_repo      : {REPO_ID} @ {REPO_REVISION}
source_config    : ground_truth (labels) + default (doc_text), joined on filename
model            : {MODEL_ID} (max_tokens={MAX_TOKENS}, overlap={WINDOW_OVERLAP_TOKENS})
rows_total       : {len(docs)} ({dict(sorted(types.items()))})
rows_by_config   : documents {dict(counts.get("documents", {}))}; windows {dict(counts.get("windows", {}))} ({n_windows} total)
strata           : {len(strata)} (doc_type x canonical subclass)
split_rule       : corpus train -> 90/10 stratified train/validation (by doc_type,
                    seed {RANDOM_STATE}, RandomState shuffle); corpus test (323)
                    held out entirely — never touches training
subclass_norm    : llm-dojo-scoring normalize_corpus_subclass (DMR-066),
                    vendored in modernbert/prep.py (Service/service,
                    Co_Branding/co_branding, Joint Venture _ Filing -> joint_venture)
title_rule       : subject -> exhibit_description -> filename (title-wins)
heads            : doc_type (5 + unknown) + per-class subclass heads
                    ({", ".join(f"{k}: {len(v['labels'])}" for k, v in maps.items())})
builder          : mailroom_eda.modernbert.prep @ Mailroom-Corpus-EDA
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_stage(stage_dir: Path) -> dict:
    """Integrity checks over a staged tree: counts, splits, vocab, leakage."""
    import pyarrow.parquet as pq

    problems: list[str] = []
    for cfg in ("documents", "windows"):
        for split in ("train", "validation", "test"):
            files = sorted((stage_dir / "parquet" / cfg / split).glob("*.parquet"))
            if cfg == "windows" and split == "test":
                if files:
                    problems.append("windows/test must not exist (test held out)")
                continue
            if not files:
                problems.append(f"missing {cfg}/{split}")
                continue
            df = pd.read_parquet(files[0])
            if (df["split"] != split).any():
                problems.append(f"{cfg}/{split}: split column mismatch")
            if cfg == "documents":
                bad = df[~df["doc_type"].isin(DOC_TYPES)]
                if len(bad):
                    problems.append(f"documents/{split}: {len(bad)} bad doc_type")
                for cls, grp in df.groupby("doc_type"):
                    allowed = set(SUBCLASS_BY_CLASS[cls])
                    bad_sub = grp[~grp["subclass"].isin(allowed)]
                    if len(bad_sub):
                        problems.append(f"documents/{split}: {cls} bad subclass: "
                                        f"{sorted(bad_sub['subclass'].unique())}")
    # leakage: no filename appears in more than one split
    docs = pd.concat([
        pd.read_parquet(f)
        for split in ("train", "validation", "test")
        for f in sorted((stage_dir / "parquet" / "documents" / split).glob("*.parquet"))
    ])
    dup = docs["filename"].duplicated()
    if dup.any():
        problems.append(f"{int(dup.sum())} duplicate filenames across splits")
    return {"ok": not problems, "problems": problems,
            "rows": int(len(docs)),
            "splits": docs["split"].value_counts().to_dict()}