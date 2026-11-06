"""Journal and CSV output for labeling chunks.

The journal is append-only JSONL, flushed and fsync'd per record, so an interrupted
chunk resumes without re-sending finished documents. The CSVs are for people: one
row per document, one row per (document, field), and for CUAD one row per span. They
use ``QUOTE_ALL`` and escape newlines as ``\\n``, so they open cleanly in a
spreadsheet. ``read_csv`` reverses the escaping.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

DOCUMENT_COLUMNS = (
    "document_id", "filename", "doc_class", "subclass", "split", "task", "status", "problems",
    "fields_present", "fields_absent", "fields_not_found", "model", "provider", "served_model",
    "reasoning", "prompt_sha256", "prompt_tokens", "completion_tokens", "cost_usd", "labeled_utc",
)
FIELD_COLUMNS = (
    "document_id", "doc_class", "subclass", "field", "type", "model_a", "model_a_value",
    "model_a_status", "model_a_evidence", "model_b", "model_b_value", "model_b_status",
    "model_b_evidence", "agree", "verbatim_ok", "decision", "reason", "reviewer_verdict",
    "reviewer_value", "reviewer_note",
)
SPAN_COLUMNS = (
    "document_id", "category", "start", "end", "text", "verbatim_ok", "probe_ok",
    "heading_only", "kept", "drop_reason",
)


def append_journal(path: Path, record: Mapping[str, Any]) -> None:
    """Append one record and fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_journal(path: Path) -> list[dict[str, Any]]:
    """All records; a torn final line from a crash is ignored."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _cell(value: Any) -> str:
    """Render one CSV cell: lists and dicts as JSON, newlines escaped."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    return str(value).replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def _uncell(value: str) -> str:
    """Reverse ``_cell``'s escaping."""
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "r": "\r", "\\": "\\"}.get(nxt, "\\" + nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> int:
    """Write rows (missing keys become empty cells). Returns the row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL, lineterminator="\n")
        w.writerow(columns)
        for row in rows:
            unknown = set(row) - set(columns)
            if unknown:
                raise ValueError(f"{path.name}: unknown columns {sorted(unknown)}")
            w.writerow([_cell(row.get(c)) for c in columns])
            n += 1
    return n


def read_csv(path: Path) -> list[dict[str, str]]:
    """Rows as dicts of unescaped strings (for reading back reviewer verdicts)."""
    with path.open(encoding="utf-8", newline="") as fh:
        return [{k: _uncell(v) for k, v in row.items()} for row in csv.DictReader(fh)]
