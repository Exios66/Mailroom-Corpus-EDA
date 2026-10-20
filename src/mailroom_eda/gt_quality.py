"""Strict ground-truth checks for mailroom-dataset Hub tag v9.1.

Golden CUAD clause maps (the Atticus draw) and golden MAUD clause maps are
left alone. Every other label is checked for structure, formatting, and a
small set of quality rules (closed vocabularies, thin templates, low-confidence
review flags).

Unfinished work — the spend queue — is only ``pending_annotation`` and
format/structure errors. Heuristic provenance and thin subjects are reported
so a later chunk can upgrade them; they are not unfinished labels.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable

from mailroom_eda.config import REPO_DATA_REVISION, REPO_ID, REPO_REVISION, REPO_TAG
from mailroom_eda.gt_presence import (
    ALL_GT_FIELDS,
    FIELD_KEYS_BY_CLASS,
    classify_field,
    context_window_band,
    is_populated,
)
from mailroom_eda.identity import source_corpus
from mailroom_eda.v8_build import (
    INTENT_CLAIM_FILING,
    INTENT_COVERAGE_DET,
    INTENT_DATA_RECORD,
)
from mailroom_eda.v9_build import CORRESPONDENCE_INTENTS, CORP_SUBCLASS_INTENT

HUB_TAG = REPO_TAG
HUB_REVISION = REPO_REVISION
HUB_DATA_REVISION = REPO_DATA_REVISION
HUB_REPO = REPO_ID

# Mirrors mailroom_sandbox.gt_labeler chunk caps. One labeling invocation
# submits one of these slices, not the whole queue.
QUEUE_CHUNK_DOCS = 40

INTENT_BY_CLASS: dict[str, frozenset[str]] = {
    "correspondence": frozenset(CORRESPONDENCE_INTENTS),
    "corporate_record": frozenset(CORP_SUBCLASS_INTENT.values()),
    "insurance_claim": frozenset({
        INTENT_CLAIM_FILING, INTENT_COVERAGE_DET, INTENT_DATA_RECORD,
    }),
    "contract": frozenset({"material_agreement"}),
}
INTENT_SOURCES = frozenset({
    "manual", "aeslc_join", "llm_zero_shot", "heuristic",
})
INTENT_STATUSES = frozenset({"manual", "auto_labeled", "flagged_review"})
COVERAGE_DETERMINATIONS = frozenset({"approved", "pending", "denied"})
CLAIM_TYPES = frozenset({"health", "auto", "property"})
SENTIMENT_LABELS = frozenset({"positive", "negative", "neutral"})
CONTEXT_BANDS = frozenset({"<=4k", "<=16k", "<=32k", ">32k"})
PLACEHOLDERS = frozenset({
    "todo", "tbd", "placeholder", "fixme", "lorem", "xxx", "lorem ipsum",
})
LINE_HAZARDS = ("\u2028", "\u2029", "\u0085")
THIN_SUBJECT_RE = re.compile(r"Correspondence concerning [A-Za-z0-9_]+[.]$")
PROVENANCE_FIELDS = ("intent_source", "intent_confidence", "intent_status")
CORRESPONDENCE_EXTRA = (
    "sentiment_score", "sentiment_evidence", "topic_evidence",
)

GOLDEN_CUAD_SOURCE = "theatticusproject/cuad"


@dataclass(frozen=True)
class Finding:
    filename: str
    doc_class: str
    field: str
    severity: str  # error | backfill | review | info
    code: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _clean(value: object) -> str:
    """Corpus absence convention: None, NaN, and the string 'nan' are empty."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value).strip()
    if text in ("nan", "None", "null"):
        return ""
    return str(value).strip() if not isinstance(value, str) else value.strip()


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _prepared(row: dict[str, Any]) -> dict[str, Any]:
    """Row shaped for ``classify_field`` / ``source_corpus``."""
    meta = _metadata(row)
    prepared = {
        "filename": _clean(row.get("filename")),
        "expected": _clean(row.get("expected")),
        "expected_subclass": _clean(row.get("expected_subclass")),
        "doc_text": "" if row.get("doc_text") is None else str(row.get("doc_text")),
        "metadata": meta,
        "split": _clean(row.get("split")),
    }
    for key in ALL_GT_FIELDS:
        prepared[key] = _clean(row.get(key))
    for key in (
        "label_evidence", "sentiment_score", "sentiment_evidence",
        "content_topic", "topic_evidence", "intent_source",
        "intent_confidence", "intent_status", "gt_presence",
        "token_estimate", "context_window_band", "clause_count",
        "maud_label_count",
    ):
        prepared[key] = _clean(row.get(key))
    return prepared


def is_golden_cuad(row: dict[str, Any]) -> bool:
    prepared = row if isinstance(row.get("metadata"), dict) else _prepared(row)
    return (
        prepared.get("expected") == "contract"
        and source_corpus(prepared) == GOLDEN_CUAD_SOURCE
    )


def is_golden_maud(row: dict[str, Any]) -> bool:
    return _clean(row.get("expected")) == "merger_agreement"


def scope_fields(row: dict[str, Any]) -> tuple[str, ...]:
    """Fields this row must satisfy. Golden CUAD and MAUD rows contribute none."""
    if is_golden_maud(row) or is_golden_cuad(row):
        return ()
    doc_class = _clean(row.get("expected"))
    if doc_class == "contract":
        # SEC EDGAR EX-10. Clause labels are unfinished; the other keys are
        # the non-golden labels already on the row.
        return (
            "cuad_clause_labels", "label_evidence", "intent",
            *PROVENANCE_FIELDS,
        )
    base = tuple(FIELD_KEYS_BY_CLASS.get(doc_class, ()))
    extra: tuple[str, ...] = PROVENANCE_FIELDS
    if doc_class == "correspondence":
        extra = extra + CORRESPONDENCE_EXTRA
    return base + extra


def _add(
    findings: list[Finding],
    row: dict[str, Any],
    field: str,
    severity: str,
    code: str,
    detail: str,
) -> None:
    findings.append(Finding(
        filename=_clean(row.get("filename")),
        doc_class=_clean(row.get("expected")),
        field=field,
        severity=severity,
        code=code,
        detail=detail,
    ))


def _line_and_placeholder(text: str) -> str | None:
    for hazard in LINE_HAZARDS:
        if hazard in text:
            return "line_boundary_hazard"
    if text.strip().lower() in PLACEHOLDERS:
        return "placeholder"
    return None


def _check_keywords(text: str) -> str | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return "keywords must be a JSON array"
    if not isinstance(parsed, list):
        return "keywords must be a JSON array"
    if not 3 <= len(parsed) <= 8:
        return f"keywords length {len(parsed)} outside 3..8"
    if any(not isinstance(item, str) or not item.strip() for item in parsed):
        return "keywords entries must be non-empty strings"
    if len(set(parsed)) != len(parsed):
        return "keywords contains duplicates"
    return None


def _check_string_list(text: str, field: str) -> str | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return f"{field} must be a JSON array"
    if not isinstance(parsed, list):
        return f"{field} must be a JSON array"
    if any(not isinstance(item, str) or not item.strip() for item in parsed):
        return f"{field} entries must be non-empty strings"
    return None


def _check_date(text: str) -> str | None:
    if text == "N/A":
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return "date must be YYYY-MM-DD or N/A"
    return None


def _check_amount(text: str) -> str | None:
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        number = float(cleaned)
    except ValueError:
        return "claimed_amount must be a number"
    if math.isnan(number) or number < 0:
        return "claimed_amount must be >= 0"
    return None


def _check_populated_field(row: dict[str, Any], field: str, text: str) -> str | None:
    mark = _line_and_placeholder(text)
    if mark == "line_boundary_hazard":
        return "contains a line-boundary character"
    if mark == "placeholder":
        return "placeholder value"
    doc_class = _clean(row.get("expected"))
    if field == "intent":
        vocab = INTENT_BY_CLASS.get(doc_class, frozenset())
        if vocab and text not in vocab:
            return f"intent {text!r} outside the closed vocabulary"
    elif field == "keywords":
        return _check_keywords(text)
    elif field in ("denial_reasons", "supporting_documents"):
        return _check_string_list(text, field)
    elif field in ("date_of_loss", "date_filed"):
        return _check_date(text)
    elif field == "claimed_amount":
        return _check_amount(text)
    elif field == "coverage_determination" and text not in COVERAGE_DETERMINATIONS:
        return f"coverage_determination {text!r} outside approved/pending/denied"
    elif field == "claim_type" and text not in CLAIM_TYPES:
        return f"claim_type {text!r} outside health/auto/property"
    elif field == "sentiment_label" and text not in SENTIMENT_LABELS:
        return f"sentiment_label {text!r} outside positive/negative/neutral"
    elif field == "sentiment_score":
        try:
            score = float(text)
        except ValueError:
            return "sentiment_score must be a number"
        if not -1.0 <= score <= 1.0:
            return "sentiment_score outside [-1, 1]"
    elif field == "intent_source" and text not in INTENT_SOURCES:
        return f"intent_source {text!r} outside the closed set"
    elif field == "intent_status" and text not in INTENT_STATUSES:
        return f"intent_status {text!r} outside the closed set"
    elif field == "intent_confidence":
        try:
            confidence = float(text)
        except ValueError:
            return "intent_confidence must be a number"
        if not 0.0 <= confidence <= 1.0:
            return "intent_confidence outside [0, 1]"
    elif field in ("subject_matter", "label_evidence", "damages_description",
                    "sentiment_evidence", "content_topic", "topic_evidence"):
        if len(text) < 2:
            return f"{field} is too short"
    return None


def audit_row(row: dict[str, Any]) -> list[Finding]:
    """Findings for one row. Golden CUAD and MAUD labels produce none."""
    prepared = _prepared(row)
    findings: list[Finding] = []
    doc_class = prepared["expected"]

    band = prepared.get("context_window_band") or ""
    if band not in CONTEXT_BANDS:
        _add(findings, prepared, "context_window_band", "error",
             "bad_context_band", f"band {band!r}")
    estimate = prepared.get("token_estimate") or ""
    if not estimate.isdigit():
        _add(findings, prepared, "token_estimate", "error",
             "bad_token_estimate", f"value {estimate!r}")
    else:
        expected_band = context_window_band(float(estimate))
        if band and band != expected_band:
            _add(findings, prepared, "context_window_band", "error",
                 "band_mismatch", f"{band} != {expected_band} for {estimate} tokens")

    presence_raw = prepared.get("gt_presence") or ""
    if presence_raw:
        try:
            presence = json.loads(presence_raw)
        except json.JSONDecodeError:
            presence = None
            _add(findings, prepared, "gt_presence", "error",
                 "bad_gt_presence", "gt_presence is not JSON")
        if isinstance(presence, dict):
            if set(presence) != set(ALL_GT_FIELDS):
                _add(findings, prepared, "gt_presence", "error",
                     "gt_presence_keys", "gt_presence keys are not the uniform field set")
            else:
                for key in ALL_GT_FIELDS:
                    live = classify_field(doc_class, key, prepared)
                    if presence.get(key) != live:
                        _add(
                            findings, prepared, key, "error", "gt_presence_drift",
                            f"published {presence.get(key)!r} != {live!r}",
                        )
                        break

    for field in scope_fields(prepared):
        text = prepared.get(field) or ""
        if field in FIELD_KEYS_BY_CLASS.get(doc_class, ()) or field == "cuad_clause_labels":
            kind = classify_field(doc_class, field, prepared)
            if kind == "pending_annotation":
                _add(findings, prepared, field, "backfill", "pending_annotation",
                     "catalogued unfinished label")
                continue
            if kind == "genuine_gap":
                _add(findings, prepared, field, "error", "genuine_gap",
                     "class-relevant field is empty and no absence rule covers it")
                continue
            if kind in ("schema_documented_absence", "not_applicable"):
                continue
        elif not is_populated(text):
            if field in PROVENANCE_FIELDS and is_populated(prepared.get("intent")):
                _add(findings, prepared, field, "error", "missing_provenance",
                     "intent is set but provenance is empty")
            elif field in ("sentiment_score", "sentiment_evidence") and doc_class == "correspondence":
                _add(findings, prepared, field, "error", "missing_sentiment",
                     "correspondence sentiment field is empty")
            continue

        if not is_populated(text):
            continue
        problem = _check_populated_field(prepared, field, text)
        if problem:
            _add(findings, prepared, field, "error", "format", problem)

    if (
        doc_class == "correspondence"
        and THIN_SUBJECT_RE.fullmatch(prepared.get("subject_matter") or "")
    ):
        _add(findings, prepared, "subject_matter", "review", "thin_subject",
             "subject is only the topic template")
    if prepared.get("intent_status") == "flagged_review" and not is_golden_cuad(prepared):
        _add(findings, prepared, "intent_status", "review", "flagged_review",
             f"confidence {prepared.get('intent_confidence') or ''}")
    if (
        prepared.get("intent_source") == "heuristic"
        and is_populated(prepared.get("intent"))
        and not is_golden_cuad(prepared)
        and not is_golden_maud(prepared)
    ):
        _add(findings, prepared, "intent_source", "info", "heuristic_provenance",
             "label is populated from a heuristic, not a model or a join")
    return findings


def load_snapshot(parquet_dir: Any = None) -> list[dict[str, Any]]:
    """Load the local v9.1 ground_truth snapshot joined to blind text.

    ``parquet_dir`` defaults to ``mailroom_eda.config.PARQUET_DIR``. Raises
    ``FileNotFoundError`` when the snapshot has not been fetched.
    """
    import pandas as pd

    from mailroom_eda.config import PARQUET_DIR

    root = PARQUET_DIR if parquet_dir is None else parquet_dir

    def _read(cfg: str) -> pd.DataFrame:
        frames = []
        for split in ("train", "test"):
            folder = root / cfg / split
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.parquet")):
                frames.append(pd.read_parquet(path))
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    ground = _read("ground_truth")
    if ground.empty or "gt_fields" not in ground.columns:
        raise FileNotFoundError(
            f"v9.1 ground_truth snapshot missing under {root}"
        )
    parsed = ground["gt_fields"].apply(
        lambda value: json.loads(value) if isinstance(value, str) and value.strip() else {}
    )
    expanded = pd.DataFrame(parsed.tolist(), index=ground.index)
    flat = pd.concat([ground.drop(columns=["gt_fields"]), expanded], axis=1)
    flat = flat.loc[:, ~flat.columns.duplicated(keep="last")]
    blind = _read("default")
    if not blind.empty:
        flat = flat.drop(columns=[c for c in ("doc_text", "metadata") if c in flat.columns])
        flat = flat.merge(
            blind[["filename", "doc_text", "metadata"]],
            on="filename",
            how="left",
        )
    records = flat.to_dict("records")
    if len(records) != 3302:
        raise RuntimeError(f"expected 3302 v9.1 rows, found {len(records)}")
    return records


def audit_rows(rows: Iterable[dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    for row in rows:
        findings.extend(audit_row(row))
    return findings


def backfill_targets(rows: Iterable[dict[str, Any]], findings: Iterable[Finding]) -> list[dict[str, Any]]:
    """One queue row per document that still has unfinished labels.

    Review-only and info-only rows stay out of this queue so a chunk does
    not spend GPU time upgrading labels that are already populated.
    """
    by_file: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        if finding.severity in ("backfill", "error"):
            by_file[finding.filename].append(finding)
    targets = []
    indexed = {_clean(row.get("filename")): _prepared(row) for row in rows}
    for filename in sorted(by_file):
        row = indexed.get(filename, {})
        fields = tuple(sorted({item.field for item in by_file[filename]}))
        meta = row.get("metadata") or {}
        targets.append({
            "id": filename,
            "filename": filename,
            "doc_class": row.get("expected") or by_file[filename][0].doc_class,
            "expected_subclass": row.get("expected_subclass") or "",
            "source_corpus": source_corpus(row) if row else "",
            "source_dataset": str(meta.get("source_dataset") or ""),
            "split": row.get("split") or "",
            "context_window_band": row.get("context_window_band") or "",
            "fields": list(fields),
            "codes": sorted({item.code for item in by_file[filename]}),
        })
    return targets


def chunk_plan(targets: list[dict[str, Any]], size: int = QUEUE_CHUNK_DOCS) -> list[dict[str, Any]]:
    """Slice the queue the way the sandbox labeler will submit it."""
    plan = []
    for index, start in enumerate(range(0, len(targets), size)):
        window = targets[start:start + size]
        plan.append({
            "index": index,
            "doc_count": len(window),
            "filenames": [item["filename"] for item in window],
            "bands": dict(Counter(item.get("context_window_band") or "" for item in window)),
        })
    return plan


def summarize(findings: Iterable[Finding], *, n_rows: int) -> dict[str, Any]:
    rows = list(findings)
    by_severity = Counter(item.severity for item in rows)
    by_code = Counter(item.code for item in rows)
    by_field = Counter((item.severity, item.doc_class, item.field, item.code) for item in rows)
    return {
        "dataset": HUB_REPO,
        "tag": HUB_TAG,
        "revision": HUB_REVISION,
        "data_revision": HUB_DATA_REVISION,
        "rows": n_rows,
        "findings": len(rows),
        "by_severity": dict(by_severity),
        "by_code": dict(by_code),
        "cells": [
            {
                "severity": severity,
                "doc_class": doc_class,
                "field": field,
                "code": code,
                "count": count,
            }
            for (severity, doc_class, field, code), count in sorted(by_field.items())
        ],
    }


def render_markdown(
    summary: dict[str, Any],
    targets: list[dict[str, Any]],
    plan: list[dict[str, Any]],
) -> str:
    severity = summary.get("by_severity") or {}
    lines = [
        "# v9.1 ground-truth backfill and quality",
        "",
        f"Dataset `{summary['dataset']}` tag `{summary['tag']}` "
        f"(`{summary['revision']}`), data commit `{summary['data_revision']}`. "
        f"{summary['rows']} rows.",
        "",
        "Golden CUAD clause maps and golden MAUD clause maps are not graded. "
        "The unfinished queue is pending annotation plus any structure or "
        "format error. Heuristic provenance and thin subjects are listed as "
        "later upgrades, not as this queue.",
        "",
        "## Counts",
        "",
        f"- unfinished backfill findings: {severity.get('backfill', 0)}",
        f"- structure/format errors: {severity.get('error', 0)}",
        f"- quality reviews (not in the spend queue): {severity.get('review', 0)}",
        f"- heuristic provenance notes: {severity.get('info', 0)}",
        f"- documents in the spend queue: {len(targets)}",
        f"- chunks of {QUEUE_CHUNK_DOCS}: {len(plan)}",
        "",
        "Each chunk is one labeling invocation on two L4s "
        "(`Qwen/Qwen3-14B-AWQ`, 16 requests in flight) with a $2 projected cap. "
        "Do not submit the queue as one job.",
        "",
        "## Cells",
        "",
        "| severity | class | field | code | count |",
        "|---|---|---|---|---|",
    ]
    for cell in summary.get("cells") or []:
        lines.append(
            f"| {cell['severity']} | `{cell['doc_class']}` | `{cell['field']}` | "
            f"`{cell['code']}` | {cell['count']} |"
        )
    lines += ["", "## Chunks", ""]
    if not plan:
        lines.append("The spend queue is empty.")
    for chunk in plan:
        lines.append(
            f"- chunk {chunk['index']}: {chunk['doc_count']} documents, "
            f"bands {chunk['bands']}"
        )
    lines.append("")
    return "\n".join(lines)
