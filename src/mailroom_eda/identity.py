"""Stable document identity, source provenance, and content hashes.

Groundwork for the mailroom-corpus hardening plan (§9–§11), shipping in the
``v0.2-mailroom-hardened`` release (§84). Every corpus row derives:

- ``document_id`` — stable identity: unique, deterministic, stable across
  rebuilds, independent of row ordering, train/test split, and Hub config.
  Derived from source identity (source_corpus + source filename), NEVER from
  a row index and never from content (two rows may share content — duplicates
  are Mailroom scenarios (§12), but each row is a distinct incoming artifact).
- ``source_corpus`` / ``source_document_id`` / ``source_filename`` /
  ``source_revision`` — provenance (§10): "where did this incoming document
  originate?" Source is an evaluation dimension, not taxonomy (§8).
- ``content_sha256`` — sha256 of the canonical doc_text bytes (utf-8).
- ``normalized_text_sha256`` — sha256 of the normalized text (see
  ``normalize_text``); supports duplicate detection (§12) and rebuild
  verification.

All derivations are pure functions of the row. Wiring into the published
parquet configs is the v0.2 release decision (§84); this module plus the
contract tests (``tests/test_contract.py``) establish and verify the fields
against the pinned corpus snapshot (§44) without pushing a new revision.
"""
from __future__ import annotations

import hashlib
import unicodedata
from typing import Any

# Release marker that ships these fields on the published configs (§84).
SCHEMA_ADDITION = "v0.2-mailroom-hardened"

# The five fused sources, keyed by the canonical five-class taxonomy
# (docs/v7-taxonomy.md; HUB_CLASSES in llm-mailroom's pipeline/hf_corpora.py).
SOURCE_CORPUS_BY_CLASS: dict[str, str] = {
    "contract": "theatticusproject/cuad",
    "merger_agreement": "maud",  # Zenodo 7500064
    "corporate_record": "sec_edgar",
    "correspondence": "Lucius-Morningstar/enron-correspondence-dedup",
    "insurance_claim": "cms_desynpuf",  # rendered via Exios66/claims-data-eda
}


def normalize_text(text: str) -> str:
    """Canonical text form for ``normalized_text_sha256``.

    NFC unicode normalization, CRLF/CR line endings folded to LF, runs of
    whitespace collapsed to a single space, ends stripped. Byte-level
    differences that Mailroom ingestion would not preserve do not produce
    distinct normalized hashes.
    """
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(text.split())


def content_sha256(text: str) -> str:
    """sha256 of the canonical doc_text bytes (utf-8) — the canonical-bytes rule."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def normalized_text_sha256(text: str) -> str:
    """sha256 of ``normalize_text(text)`` encoded utf-8."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def source_corpus(row: dict[str, Any]) -> str:
    """Originating corpus for a row (§8, §10).

    The class map stays authoritative for the five fused sources (published
    document_ids are derived from it — never churn them). The v8 LOB
    expansion (HUB-028) added insurance rows from GNOTHEIA / BDR, which do
    not collapse into the class-level CMS mapping; those rows carry their
    exact HF dataset id in ``metadata.source_dataset`` and override the map
    (the CMS rows keep ``cms_desynpuf`` so their published IDs are stable).
    """
    md = row.get("metadata") or {}
    doc_class = str(row.get("expected") or "")
    if doc_class == "insurance_claim":
        source_dataset = md.get("source_dataset")
        if source_dataset and "cms-de-synpuf" not in str(source_dataset):
            return str(source_dataset)
    return SOURCE_CORPUS_BY_CLASS.get(doc_class, "unknown")


def source_document_id(row: dict[str, Any]) -> str:
    """Source-native identifier where one exists (§10).

    insurance_claim rows carry the claims-data-eda render ``record_id`` in
    metadata; every other source's stable native id is its filename (CUAD
    file name, MAUD contract_N.txt, EDGAR exhibit name, Enron maildir path).
    """
    md = row.get("metadata") or {}
    record_id = md.get("record_id")
    if record_id:
        return str(record_id)
    return str(row.get("filename") or "")


def document_id(row: dict[str, Any]) -> str:
    """Stable document identity (§9): ``DOC-`` + first 16 hex of sha256 over
    ``source_corpus`` + source filename.

    Deterministic and independent of row order, split, and Hub config.
    Uniqueness rests on the corpus's filename-uniqueness invariant (the v6
    append draws excluded existing filenames; asserted by the contract tests).
    """
    key = f"{source_corpus(row)}\n{row.get('filename') or ''}"
    return "DOC-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the row with identity/provenance/hash fields added."""
    out = dict(row)
    out["document_id"] = document_id(row)
    out["source_corpus"] = source_corpus(row)
    out["source_document_id"] = source_document_id(row)
    out["source_filename"] = str(row.get("filename") or "")
    # Per-row source revision: the v7 builder did not track it (cast-safe ''
    # per §10 groundwork). The v8 LOB rows carry the pinned upstream dataset
    # revision in metadata.source_revision — preserve it where the source
    # override applies (same gate as source_corpus, so published v7 rows
    # keep the '' convention and their stable identities).
    out["source_revision"] = str(row.get("source_revision") or "")
    if not out["source_revision"]:
        md = row.get("metadata") or {}
        if (str(row.get("expected") or "") == "insurance_claim"
                and "cms-de-synpuf" not in str(md.get("source_dataset") or "")):
            out["source_revision"] = str(md.get("source_revision") or "")
    out["content_sha256"] = content_sha256(str(row.get("doc_text") or ""))
    out["normalized_text_sha256"] = normalized_text_sha256(str(row.get("doc_text") or ""))
    return out


def enrich_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich every row (input order preserved; identity is order-independent)."""
    return [enrich_row(r) for r in rows]
