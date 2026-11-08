"""Focused checks for the corporate review packet export."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from mailroom_eda.labeling import review_corporate
from mailroom_eda.labeling.review_packet import _csv


def test_batch_ids_uses_draft_order_and_only_resolved_labels() -> None:
    root = Path(__file__).resolve().parents[1]
    ids = review_corporate.batch_ids(root, "batch_A")

    assert len(ids) == 22
    assert len(set(ids)) == len(ids)
    assert all(isinstance(source_id, str) and source_id.startswith("s3://") for source_id in ids)


def test_oversized_document_text_becomes_a_spreadsheet_pointer() -> None:
    packet = {
        "records": [
            {
                "document_id": "REVIEW-ONLY-short",
                "document_link": "documents/REVIEW-ONLY-short.txt",
                "document_sha256": "a" * 64,
                "full_document_text": "short text",
                "human_decision": "NOT_REVIEWED",
            },
            {
                "document_id": "REVIEW-ONLY-large",
                "document_link": "documents/REVIEW-ONLY-large.txt",
                "document_sha256": "b" * 64,
                "full_document_text": "x" * 40_000,
                "human_decision": "NOT_REVIEWED",
            },
        ]
    }

    columns, rows = review_corporate._spreadsheet_rows(packet)

    assert "full_document_text_pointer" in columns
    assert rows[0]["full_document_text"] == "short text"
    assert rows[0]["full_document_text_pointer"] == ""
    assert rows[0]["full_document_text_chars"] == "10"
    assert rows[1]["full_document_text"] == (
        "[Full document text is stored in the linked document file; use "
        "documents/REVIEW-ONLY-large.txt or corporate-review-data.json.]"
    )
    assert rows[1]["full_document_text_pointer"] == "documents/REVIEW-ONLY-large.txt"
    assert rows[1]["full_document_text_chars"] == "40000"
    assert len(packet["records"][1]["full_document_text"]) == 40_000

    rendered = _csv(rows)
    parsed = list(csv.DictReader(io.StringIO(rendered)))
    assert parsed[1]["full_document_text_pointer"] == "documents/REVIEW-ONLY-large.txt"

    workbook = review_corporate._workbook(rows, {"NOT_REVIEWED": "No decision"})
    assert workbook[:2] == b"PK"
