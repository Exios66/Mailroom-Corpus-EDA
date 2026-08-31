"""Correspondence intent backfill for docclass-merged v7 (KANBAN issue #5).

Implements the four-phase plan from
https://github.com/Exios66/mailroom-dev/issues/5:

Phase 1 — Taxonomy cross-walk: the canonical Enron intent vocabulary is
    vendored from llm-mailroom's ``INTENT_LABELS["correspondence"]`` (8
    classes). A cross-walk maps external email-intent taxonomies (aeslc-style
    subject heuristics, tasksource/email-intent style labels) onto that
    canonical set; anything non-conforming falls to ``other`` instead of null.

Phase 2 — Exact-match join: the 251 unlabeled Enron correspondence rows are
    hydrated by sha256(normalized_header_stripped_body) against the full Enron
    mail corpus (``snoop2head/enron_aeslc_emails``, 535k mails) and AESLC
    (``Yale-LILY/aeslc``). A body match sets provenance ``aeslc_join`` and, for
    Yale-LILY, also recovers the AESLC ``subject_line``.

Phase 3 — Zero-shot LLM pass for residuals: rows without an exact body match
    (or without any match) are labeled by a constrained LLM call (OpenRouter)
    forced to choose from the canonical vocabulary, with confidence
    thresholding: confidence >= CONFIDENCE_AUTO (0.85) -> intent_status
    ``auto_labeled``; below -> ``flagged_review`` (intent still populated —
    the vocabulary's ``other`` is the explicit fallback, never null).

Phase 4 — Provenance columns: every correspondence row gains ``intent``,
    ``intent_source`` (manual | aeslc_join | llm_zero_shot),
    ``intent_confidence`` (0..1), ``intent_status`` (manual | auto_labeled |
    flagged_review). Downstream (dataset_export / docclass_uploader) carry the
    three new provenance keys into the ground_truth config.

Determinism: the whole backfill is keyed on ``md5(filename)``-stable ordering;
    the join index and any LLM sidecar are cached under data/backfill/ so a
    re-run is byte-identical without spending new LLM calls.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import pandas as pd

from .config import DATA_DIR, RANDOM_STATE

BACKFILL_DIR = DATA_DIR / "backfill"
INDEX_CACHE = BACKFILL_DIR / "enron_body_sha256.parquet"
LABEL_SIDECAR = BACKFILL_DIR / "intent_labels.jsonl"

# Phase 1: canonical vocabulary (vendored from llm-mailroom INTENT_LABELS).
CANONICAL_INTENTS: tuple[str, ...] = (
    "payment_demand",
    "notice",
    "analysis",
    "request",
    "update",
    "meeting_invite",
    "press_communication",
    "other",
)

INTENT_DESCRIPTIONS: dict[str, str] = {
    "payment_demand": "demand for payment or cure",
    "notice": "formal notice of a fact, breach, or intent",
    "analysis": "internal memo analyzing options/remedies",
    "request": "request for information or action",
    "update": "informational status update",
    "meeting_invite": "meeting/calendar request",
    "press_communication": "press release / public statement",
    "other": "residual",
}

# External taxonomies -> canonical (Phase 1 cross-walk). Sources:
# - AESLC-style subject-line heuristics (Zhang & Tetreault 2019)
# - tasksource/email-intent style labels (request, inform_status, ...)
CROSS_WALK: dict[str, str] = {
    "request": "request",
    "query": "request",
    "request / query": "request",
    "inform_status": "update",
    "status_update": "update",
    "status update": "update",
    "deliverable": "update",
    "deliverable / status update": "update",
    "propose_meeting": "meeting_invite",
    "propose / meeting": "meeting_invite",
    "meeting": "meeting_invite",
    "dispute_complaint": "notice",
    "dispute / complaint": "notice",
    "complaint": "notice",
    "notice": "notice",
    "press_communication": "press_communication",
    "press_release": "press_communication",
    "press release": "press_communication",
    "analysis": "analysis",
    "payment_demand": "payment_demand",
    "demand": "payment_demand",
    "invoice": "payment_demand",
    "other": "other",
}


def crosswalk_external(label: str) -> str:
    """Phase 1: map an external intent label onto the canonical vocabulary."""
    key = normalize_text(label)
    if key in CANONICAL_INTENTS:
        return key
    if key in CROSS_WALK:
        return CROSS_WALK[key]
    first = key.split(" / ")[0].strip()
    return CROSS_WALK.get(first, "other")

CONFIDENCE_AUTO = 0.85
HEADER_RE = re.compile(
    r"^(subject|from|to|date|cc|bcc|sent|importance|mime|content|received|"
    r"x-|reply-to|message-id|in-reply-to|references|body):",
    re.I,
)
SUBJECT_RE = re.compile(r"^subject:\s*(.*)$", re.I | re.M)


def normalize_text(text: str) -> str:
    """Issue #5 Phase 2 normalization: collapse whitespace, lowercase."""
    return " ".join(str(text).strip().lower().split())


def strip_email_headers(text: str) -> str:
    """Remove RFC-style header lines and attachment fences from an email."""
    lines = []
    for ln in str(text).splitlines():
        s = ln.strip()
        if HEADER_RE.match(s):
            continue
        if s.startswith("----") or s.startswith("***"):
            continue
        lines.append(s)
    return "\n".join(lines)


def body_sha256(text: str) -> str:
    """sha256 of the normalized header-stripped body (Phase 2 join key)."""
    return hashlib.sha256(normalize_text(strip_email_headers(text)).encode("utf-8")).hexdigest()


def extract_subject(text: str) -> str:
    """Pull the Subject: line out of a raw email, if present."""
    m = SUBJECT_RE.search(str(text))
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Phase 2: Enron body-hash index + exact-match join
# ---------------------------------------------------------------------------

def build_enron_index(force: bool = False) -> pd.DataFrame:
    """Build (or load cached) sha256(body) -> subject_line index over Enron.

    Sources:
    - ``snoop2head/enron_aeslc_emails`` (535k raw mails, full headers) — the
      broad Enron dump; bodies hashed header-stripped.
    - ``Yale-LILY/aeslc`` (18k, email_body + subject_line) — the canonical
      AESLC subset; bodies hashed as-is (already body-only).
    """
    INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if INDEX_CACHE.exists() and not force:
        df = pd.read_parquet(INDEX_CACHE)
        if "body_sha256" in df.columns and "subject_line" in df.columns:
            return df

    from huggingface_hub import hf_hub_download

    frames = []
    for shard in (
        "data/train-00000-of-00003-e84eb6663f20c00e.parquet",
        "data/train-00001-of-00003-1b1300ac770ec5ec.parquet",
        "data/train-00002-of-00003-d94747ba7d786cc1.parquet",
    ):
        path = hf_hub_download("snoop2head/enron_aeslc_emails", shard, repo_type="dataset")
        raw = pd.read_parquet(path)
        bodies = raw["text"].fillna("")
        frame = pd.DataFrame({
            "body_sha256": [body_sha256(t) for t in bodies],
            "subject_line": [extract_subject(t) for t in bodies],
        })
        frames.append(frame)
        print(f"  enron shard {shard}: {len(raw)} mails hashed")

    try:
        from datasets import load_dataset
        aeslc = load_dataset("Yale-LILY/aeslc", split="train").to_pandas()
        frames.append(pd.DataFrame({
            "body_sha256": [body_sha256(t) for t in aeslc["email_body"].fillna("")],
            "subject_line": aeslc["subject_line"].fillna(""),
        }))
        print(f"  AESLC: {len(aeslc)} emails hashed")
    except Exception as exc:
        print(f"  AESLC load skipped: {exc}")

    index = pd.concat(frames, ignore_index=True)
    index = index.drop_duplicates(subset="body_sha256", keep="first")
    index.to_parquet(INDEX_CACHE, index=False)
    print(f"Enron body index: {len(index)} unique hashes -> {INDEX_CACHE}")
    return index


def join_enron(
    rows: list[dict],
    index: pd.DataFrame,
    doc_text_map: dict[str, str] | None = None,
) -> dict[str, dict]:
    """Phase 2: sha256 exact-body join; returns filename -> join metadata.

    ``rows`` are ground_truth rows (no doc_text); pass ``doc_text_map``
    filename -> blind ``doc_text`` so bodies are hashed from the blind config.
    Matched rows get ``aeslc_joined: true`` and, when the match came from the
    AESLC subset (subject_line present), the recovered subject line.
    """
    if index is None or index.empty:
        return {}
    lookup = dict(zip(index["body_sha256"], index["subject_line"]))
    hits: dict[str, dict] = {}
    for r in rows:
        text = (doc_text_map or {}).get(r["filename"], "")
        key = body_sha256(text)
        if key in lookup:
            hits[r["filename"]] = {
                "aeslc_joined": True,
                "subject_line": lookup.get(key) or "",
            }
    return hits


# ---------------------------------------------------------------------------
# Phase 3: constrained LLM labeler (OpenRouter)
# ---------------------------------------------------------------------------

LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(CANONICAL_INTENTS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["intent", "confidence"],
}


def labeler_prompt(text: str, subject_line: str = "") -> str:
    labels = ", ".join(CANONICAL_INTENTS)
    desc = "; ".join(f"{k} = {v}" for k, v in INTENT_DESCRIPTIONS.items())
    window = re.sub(r"\s+", " ", str(text or ""))[:4000]
    subject = f"Subject: {subject_line}\n" if subject_line else ""
    return (
        "You are labeling an Enron business email with a controlled intent "
        "taxonomy. Choose exactly ONE intent strictly grounded in the email "
        "body below — no inferences beyond what the text states.\n\n"
        "Closed vocabulary (choose only from these):\n"
        f"{labels}\n\n"
        f"Definitions: {desc}\n\n"
        "Email:\n"
        f"{subject}{window}\n\n"
        "Return ONLY a json object: {\"intent\": \"<one of the labels>\", "
        "\"confidence\": <0..1 float — how confidently the text supports this "
        "label>}. Never invent labels outside the closed vocabulary."
    )


def llm_label_one(text: str, model: str, api_key: str, base_url: str, subject_line: str = "") -> dict:
    """One constrained zero-shot label call. Returns {intent, confidence}."""
    import httpx

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You label Enron emails with a closed intent vocabulary. Output strict JSON only."},
            {"role": "user", "content": labeler_prompt(text, subject_line)},
        ],
        "temperature": 0.1,
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
    }
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            resp = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=180,
            )
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("retry-after", 0) or 0)
                wait = max(retry_after, 20 * (attempt + 1))
                time.sleep(wait)
                last_err = RuntimeError(f"429 rate limit (retry-after {retry_after}s)")
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content") or ""
            if not content.strip():
                raise ValueError("empty completion (reasoning-only response)")
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
            parsed = json.loads(content)
            intent = crosswalk_external(parsed.get("intent", ""))
            confidence = float(parsed.get("confidence", 0.0))
            confidence = min(max(confidence, 0.0), 1.0)
            return {"intent": intent, "confidence": round(confidence, 4)}
        except Exception as exc:
            last_err = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"LLM labeling failed after 5 attempts: {last_err}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def load_existing_labels() -> dict[str, dict]:
    """Sidecar labels keyed by filename (checkpointing / resume)."""
    out: dict[str, dict] = {}
    if not LABEL_SIDECAR.exists():
        return out
    for line in LABEL_SIDECAR.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["filename"]] = row
    return out


def backfill_correspondence(
    gt: pd.DataFrame,
    blind: pd.DataFrame,
    *,
    model: str = "deepseek/deepseek-chat",
    api_key: str = "",
    base_url: str = "https://openrouter.ai/api/v1",
    max_llm_rows: int = 0,
    force_index: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """Backfill intent for every correspondence row lacking a non-empty intent.

    - Phase 1 vocabulary + cross-walk baked in via CANONICAL_INTENTS.
    - Phase 2 join over the Enron body index (provenance aeslc_join).
    - Phase 3 LLM pass for rows without existing intent (provenance
      llm_zero_shot, confidence thresholding, ``other`` fallback).
    - Rows that already carry a non-empty intent keep it (manual).

    Returns (enriched ground_truth frame, stats dict).
    """
    from .download import load_ground_truth, load_jsonl

    corr_mask = gt["expected"] == "correspondence"
    corr = gt.loc[corr_mask].copy()
    existing = corr["intent"].fillna("").str.strip()
    need = corr[existing.eq("")].copy()
    print(f"correspondence: {len(corr)} rows; {len(corr) - len(need)} already labeled; "
          f"{len(need)} need intent")

    stats = {
        "rows_total": int(len(corr)),
        "manual_preserved": int(len(corr) - len(need)),
        "aeslc_joined": 0,
        "llm_zero_shot": 0,
        "flagged_review": 0,
        "other_fallback": 0,
    }

    # Phase 2: build index + join
    index = build_enron_index(force=force_index)
    doc_text_map = {
        fn: text
        for fn, text in zip(blind["filename"], blind["doc_text"])
        if fn in set(need["filename"])
    }
    join_hits = join_enron(need.to_dict("records"), index, doc_text_map)
    for fn in join_hits:
        stats["aeslc_joined"] += 1

    # Phase 3: LLM pass (checkpointed; skips rows already in the sidecar).
    # Note: the AESLC/Enron mirrors carry NO intent annotations (verified
    # 2026-08-31) — the exact-body join provides provenance + subject context;
    # intent itself is assigned under the closed vocabulary by the constrained
    # LLM pass for every previously-unlabeled row.
    existing_labels = load_existing_labels()
    llm_rows = list(existing_labels.values())
    n_llm = 0
    n_done = len(existing_labels)

    def _checkpoint() -> None:
        LABEL_SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        tmp = LABEL_SIDECAR.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for entry in llm_rows:
                fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        tmp.replace(LABEL_SIDECAR)

    for _, row in need.iterrows():
        fn = row["filename"]
        if fn in existing_labels:
            continue
        if max_llm_rows and n_llm >= max_llm_rows:
            break
        if not api_key:
            raise SystemExit(
                "LLM pass requires OPENROUTER_API_KEY (set it in the environment "
                "or ~/.hermes/.env). Rows not in the sidecar cannot be labeled."
            )
        text = doc_text_map.get(fn, "")
        subject = (join_hits.get(fn) or {}).get("subject_line", "")
        label = llm_label_one(text, model, api_key, base_url, subject)
        entry = {
            "filename": fn,
            "intent": label["intent"],
            "intent_source": "llm_zero_shot",
            "intent_confidence": label["confidence"],
            "intent_status": "auto_labeled" if label["confidence"] >= CONFIDENCE_AUTO else "flagged_review",
        }
        if fn in join_hits:
            entry["aeslc_joined"] = True
            entry["aeslc_subject_line"] = subject
        llm_rows.append(entry)
        existing_labels[fn] = entry
        n_llm += 1
        n_done += 1
        stats["llm_zero_shot"] += 1
        if entry["intent_status"] == "flagged_review":
            stats["flagged_review"] += 1
        if entry["intent"] == "other":
            stats["other_fallback"] += 1
        if n_done % 10 == 0:
            _checkpoint()
            print(f"  checkpoint: {n_done}/{len(need)} labeled ({n_llm} new this run)",
                  flush=True)

    _checkpoint()

    # Merge back onto the full ground_truth frame
    merged: dict[str, dict] = {}
    for entry in llm_rows:
        merged[entry["filename"]] = entry

    def _row_values(fn: str, src: pd.Series) -> dict:
        entry = merged.get(fn)
        if entry is None:
            # preserve pre-existing labels as-is
            has_intent = str(src.get("intent", "") or "").strip()
            return {
                "intent": src.get("intent", ""),
                "intent_source": "manual" if has_intent else src.get("intent_source", ""),
                "intent_confidence": 1.0 if has_intent else src.get("intent_confidence", ""),
                "intent_status": "manual" if has_intent else src.get("intent_status", ""),
            }
        return {
            "intent": entry["intent"],
            "intent_source": entry["intent_source"],
            "intent_confidence": entry["intent_confidence"],
            "intent_status": entry["intent_status"],
        }

    gt = gt.copy()
    # Only correspondence rows are re-labeled by the backfill; every other
    # doc_type keeps its existing intent / provenance columns untouched.
    corr_lookup = {fn: idx for fn, idx in zip(corr["filename"], corr.index)}
    vals = {fn: _row_values(fn, gt.loc[idx]) for fn, idx in corr_lookup.items()}
    for k in ("intent", "intent_source", "intent_confidence", "intent_status"):
        if k in gt.columns:
            gt = gt.drop(columns=k)
        gt[f"__{k}"] = gt["filename"].map(vals).map(
            lambda v: v.get(k, "") if isinstance(v, dict) else ""
        )
    gt["intent"] = gt["__intent"].fillna("")
    gt["intent_source"] = gt["__intent_source"].fillna("")
    gt["intent_confidence"] = gt["__intent_confidence"].fillna("")
    gt["intent_status"] = gt["__intent_status"].fillna("")
    gt = gt.drop(columns=["__intent", "__intent_source", "__intent_confidence", "__intent_status"])

    stats["coverage_pct"] = round(
        float(100 * gt.loc[corr_mask, "intent"].fillna("").str.strip().ne("").sum() / corr_mask.sum()), 2
    )
    # Merge totals (sidecar-resumed rows count as labeled too) so the manifest
    # reflects the final corpus state, not just this run's new calls.
    stats["llm_zero_shot_total"] = int(
        (gt.loc[corr_mask, "intent_source"] == "llm_zero_shot").sum()
    )
    stats["manual_total"] = int(
        (gt.loc[corr_mask, "intent_source"] == "manual").sum()
    )
    stats["flagged_review"] = int(
        (gt.loc[corr_mask, "intent_status"] == "flagged_review").sum()
    )
    stats["other_fallback"] = int(
        (gt.loc[corr_mask, "intent"] == "other").sum()
    )
    return gt, stats


def validate_intent_coverage(gt: pd.DataFrame, strict: bool = True) -> dict:
    """Phase 4 assertion: 100% non-null intent coverage for correspondence.

    Also verifies every intent is inside the canonical vocabulary and that the
    provenance columns ride the rows. ``strict=False`` downgrades the coverage
    assertion to a report (for bounded probe runs).
    """
    corr = gt[gt["expected"] == "correspondence"]
    covered = corr["intent"].fillna("").str.strip().ne("")
    report = {
        "correspondence_rows": int(len(corr)),
        "intent_covered": int(covered.sum()),
        "coverage_pct": round(float(100 * covered.sum() / len(corr)), 2) if len(corr) else 100.0,
        "unknown_intents": sorted(set(corr.loc[covered, "intent"]) - set(CANONICAL_INTENTS)),
        "null_intent_rows": int((~covered).sum()),
        "flagged_review": int((corr["intent_status"] == "flagged_review").sum()),
        "sources": corr.loc[covered, "intent_source"].value_counts().to_dict(),
    }
    if strict:
        assert report["null_intent_rows"] == 0, f"null intent on {report['null_intent_rows']} correspondence rows"
    assert not report["unknown_intents"], f"intents outside canonical vocabulary: {report['unknown_intents']}"
    for col in ("intent_source", "intent_confidence", "intent_status"):
        assert col in corr.columns, f"missing provenance column: {col}"
    return report


def test_split_intent_coverage(gt: pd.DataFrame) -> dict:
    """Phase 5: every canonical intent class present in the 10% test split."""
    corr = gt[gt["expected"] == "correspondence"]
    test = corr[corr["split"] == "test"]
    test_intents = set(test["intent"].fillna("").str.strip())
    report = {
        "test_rows": int(len(test)),
        "test_intents": sorted(i for i in test_intents if i),
        "missing_from_test": sorted(set(CANONICAL_INTENTS) - set(i for i in test_intents if i)),
    }
    return report


GT_PUBLISH_KEYS = [
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


def build_v7_rows(
    gt: pd.DataFrame,
    blind: pd.DataFrame,
    path: Path,
) -> list[dict]:
    """Assemble the v7 publish rows (blind text + enriched GT fields).

    Mirrors run_all P5 row shape; ``gt_fields`` carries the GT scalar keys so
    ``stage_parquet`` can rebuild the ground_truth configs with the new
    provenance columns. Rows are sorted by filename for byte-determinism.
    """
    blind_map = {
        fn: (text, prompt, md)
        for fn, text, prompt, md in zip(
            blind["filename"], blind["doc_text"],
            blind.get("prompt", ""), blind["metadata"],
        )
    }
    rows = []
    for _, r in gt.sort_values("filename").iterrows():
        fn = r["filename"]
        text, prompt, md = blind_map.get(fn, ("", "", {}))
        gf = {k: ("" if pd.isna(r.get(k)) else r.get(k)) for k in GT_PUBLISH_KEYS}
        rows.append({
            "filename": fn,
            "doc_text": text,
            "prompt": prompt,
            "expected": r["expected"],
            "expected_subclass": r["expected_subclass"],
            "split": r["split"],
            "metadata": dict(md) if isinstance(md, dict) else {},
            "gt_fields": gf,
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    print(f"v7 rows -> {path} ({len(rows)} rows)")
    return rows
    return report