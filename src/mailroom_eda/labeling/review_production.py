"""Manual-review packets from the public production dataset; never gold or deploy.

One record per registry field of a chosen production document. A published value
is a candidate only when it already has the registry's native shape. ACCEPT keeps
that exact value for the private v10 queue; CORRECT supplies the replacement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from .. import config, hf_interface
from .registry import class_fields, dojo_snapshot
from .review_page import PACKET_SCHEMA, packet_id, validate_packet
from .review_packet import DECISION_KEY, _json, _journal

PRODUCER = "production_manual_review/v1"
SOURCE_FILE = "ground_truth_hardened.jsonl"
CLAUSE_FIELDS = frozenset({"cuad_clauses", "maud_clauses"})
SCOPE = ("Production documents; existing published values are candidates. ACCEPT keeps this exact "
         "value for the private v10 queue; CORRECT supplies the replacement. The public dataset is "
         "never edited and nothing deploys automatically.")


def _sha(text: str) -> str:
    """Return the SHA-256 hex digest of UTF-8 text."""
    return hashlib.sha256(text.encode()).hexdigest()


def _native(value: Any, kind: str) -> Any | None:
    """Decode a published value only when it already matches the registry type."""
    if kind.startswith("entity_list"):
        items = value
        if isinstance(value, str):
            try:
                items = json.loads(value)
            except ValueError:
                return None
        if isinstance(items, list) and items and all(isinstance(v, str) and v.strip() for v in items):
            return items
        return None
    if kind == "money" and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    if isinstance(value, str) and value.strip():
        return value
    return None


def _evidence(value: Any, kind: str, text: str) -> list[dict[str, Any]]:
    """Return first exact source spans for all short string values, or an empty list.

    Money, missing matches, and strings over 240 characters yield no spans.
    Offsets use zero-based Unicode characters and exclusive ends."""
    items = value if isinstance(value, list) else [value]
    if kind == "money" or not all(isinstance(v, str) and v.strip() and len(v) <= 240 for v in items):
        return []
    spans = []
    for item in items:
        start = text.find(item)
        if start < 0:
            return []
        spans.append({"start": start, "end": start + len(item), "quote": item,
                      "offset_convention": "Python Unicode code points; zero-based, end exclusive"})
    return spans


def _checked(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy rows and decode gt_fields, preserving its original value separately.

    Raise ValueError for duplicate IDs, invalid splits, subclass or text values,
    malformed ground truth, or corporate subclasses outside the pinned schema.
    Missing required row keys propagate as KeyError."""
    seen = set()
    out = []
    for row in rows:
        row = dict(row)
        doc_id = row.get("document_id")
        if doc_id in seen:
            raise ValueError(f"duplicate production document rows: {doc_id}")
        seen.add(doc_id)
        if row.get("split") not in ("train", "test"):
            raise ValueError(f"{doc_id}: unknown production split")
        if not isinstance(row.get("expected_subclass"), str):
            raise ValueError(f"{doc_id}: production subclass must be text")
        if not isinstance(row.get("doc_text"), str) or not row["doc_text"]:
            raise ValueError(f"{doc_id}: production text must be non-empty text")
        gt = row.get("gt_fields")
        if isinstance(gt, str):
            try:
                parsed = json.loads(gt)
            except ValueError as exc:
                raise ValueError(f"{doc_id}: gt_fields is not JSON") from exc
        else:
            parsed = gt
        if not isinstance(parsed, dict):
            raise ValueError(f"{doc_id}: gt_fields must be an object")
        if row["expected"] == "corporate_record" and row["expected_subclass"] not in dojo_snapshot()["corporate_subclass_schemas"]:
            raise ValueError(f"unknown corporate subclass: {row['expected_subclass']}")
        row["_gt"], row["_raw_gt"] = parsed, gt
        out.append(row)
    return out


def build_packet(rows: list[dict[str, Any]], *, source_revision: str, source_sha256: str,
                 document_ids: Sequence[str] = (), per_class: int = 2,
                 doc_class: str | None = None, per_subclass: int | None = None) -> dict[str, Any]:
    """Build a deterministic packet of registry fields for selected production documents.

    ``document_ids`` selects explicit IDs; otherwise choose up to ``per_class``
    IDs per class in sorted order, or up to ``per_subclass`` IDs per
    (class, subclass) when it is given. ``doc_class`` restricts the candidates to
    one class. Records are sorted by document ID, independent of input order;
    source rows are not mutated. Source revision and sidecar hash are recorded as
    supplied. Raise ValueError for nonpositive counts, a class with no rows,
    unknown IDs, invalid rows, or an invalid resulting packet; registry KeyError
    propagates."""
    if per_class < 1 or (per_subclass is not None and per_subclass < 1):
        raise ValueError("per-class and per-subclass must be positive")
    checked = _checked(rows)
    if doc_class is not None:
        checked = [row for row in checked if row["expected"] == doc_class]
        if not checked:
            raise ValueError(f"no production rows for class: {doc_class}")
    by_id = {row["document_id"]: row for row in checked}
    if document_ids:
        chosen = list(dict.fromkeys(document_ids))
        unknown = [doc_id for doc_id in chosen if doc_id not in by_id]
        if unknown:
            raise ValueError(f"unknown document(s): {unknown}")
    else:
        grouped: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for row in checked:
            key = (row["expected"],) if per_subclass is None else (row["expected"], row["expected_subclass"])
            grouped[key].append(row["document_id"])
        limit = per_class if per_subclass is None else per_subclass
        chosen = [doc_id for key in sorted(grouped) for doc_id in sorted(grouped[key])[:limit]]
    records = []
    for doc_id in sorted(set(chosen)):
        row, gt, raw_gt = by_id[doc_id], by_id[doc_id]["_gt"], by_id[doc_id]["_raw_gt"]
        cls, subclass, text = row["expected"], row["expected_subclass"], row["doc_text"]
        presence = gt.get("gt_presence") if isinstance(gt.get("gt_presence"), dict) else {}
        for name, spec in class_fields(cls, subclass).items():
            kind = spec["type"]
            raw = gt.get(name)
            clause = name in CLAUSE_FIELDS
            value = None if clause else _native(raw, kind)
            mismatched = not clause and raw not in (None, "") and value is None
            evidence = [] if value is None else _evidence(value, kind, text)
            grounded = value is not None and (kind == "money" or bool(evidence))
            if value is not None:
                state = "populated"
            elif name in gt and raw is None:
                state = presence.get(name, "pending_annotation")
            else:
                state = "pending_annotation"
            records.append({
                "document_id": doc_id, "published_document_id": doc_id, "field": name,
                "filename": row.get("filename", ""), "doc_class": cls, "subclass": subclass,
                "split": row["split"], "source_dataset": config.REPO_ID,
                "source_revision": source_revision, "source_sha256": source_sha256,
                "document_sha256": _sha(text), "full_document_text": text,
                "kind": "production_manual", "candidate_labels": value, "original_gt_candidate": raw,
                "candidate_presence": state,
                "correction_type": "array" if kind.startswith("entity_list") else "number" if kind == "money" else "string",
                "dojo_type": kind, "nullable": spec["absence"] != "never_absent",
                "absence_policy": spec["absence"], "eligible_for_gold": not clause and not mismatched,
                "schema_status": "schema_pending" if clause or mismatched else "pinned_registry",
                "schema_alignment": "proposal_pending" if clause or mismatched else "aligned",
                "evidence": evidence,
                "candidate_status": "source_checks_passed" if grounded else "needs_source_review",
                "issues": [] if grounded else ["Existing value needs human source comparison or schema alignment"],
                "existing_gt_fields": raw_gt, "human_approval": "pending", "review_provenance": SCOPE,
            })
    packet = {"schema": PACKET_SCHEMA, "decision_key": DECISION_KEY, "scope": SCOPE, "records": records,
              "source": {"dataset": config.REPO_ID, "revision": source_revision, "sidecar_sha256": source_sha256}}
    packet["packet_id"] = packet_id(records)
    validate_packet(packet)
    return packet


def load_production(api: Any, revision: str | None) -> dict[str, Any]:
    """Download production rows at the resolved ref and return revision and byte hash.

    None uses config.REPO_REVISION. If Hub metadata omits its SHA, the requested
    ref is retained. Raise ValueError for a private source or invalid journal;
    Hub, JSON parsing, and filesystem errors propagate."""
    requested = revision or config.REPO_REVISION
    info = api.repo_info(repo_id=config.REPO_ID, repo_type="dataset", revision=requested)
    if getattr(info, "private", False):
        raise ValueError("review source must be the public production dataset")
    resolved = getattr(info, "sha", None) or requested
    with tempfile.TemporaryDirectory() as tmp:
        local = hf_interface.download_file(api, config.REPO_ID, SOURCE_FILE, Path(tmp) / SOURCE_FILE,
                                            revision=resolved)
        raw = Path(local).read_bytes()
        rows = _journal(Path(local))
    return {"rows": rows, "revision": resolved, "source_sha256": hashlib.sha256(raw).hexdigest()}


def export_production(root: Path, *, revision: str | None = None, per_class: int = 2,
                      doc_class: str | None = None, per_subclass: int | None = None) -> Path:
    """Write one private packet. Identical bytes are idempotent; different bytes are refused.

    Download at ``revision`` (the configured production revision when None),
    select documents as ``build_packet`` does, and return the packet path.
    Create root with mode 0700 and new packet files with mode 0600. Raise
    ValueError for a symlinked directory or conflicting packet; source validation,
    Hub, and filesystem errors propagate."""
    api = hf_interface.get_hf_api(token=None)
    source = load_production(api, revision)
    packet = build_packet(source["rows"], source_revision=source["revision"],
                          source_sha256=source["source_sha256"], per_class=per_class,
                          doc_class=doc_class, per_subclass=per_subclass)
    out = Path(root)
    if out.is_symlink():
        raise ValueError("production packet directory must not be a symlink")
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    path = out / "production-review-data.json"
    content = (_json(packet) + "\n").encode()
    if path.exists():
        if path.is_symlink() or path.read_bytes() != content:
            raise ValueError("refusing overwrite of an existing production packet")
        return path
    path.write_bytes(content)
    path.chmod(0o600)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    """Download production candidates, export a private packet, and print its path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--revision")
    parser.add_argument("--per-class", type=int, default=2)
    parser.add_argument("--doc-class", help="only this class, e.g. insurance_claim")
    parser.add_argument("--per-subclass", type=int, help="pick this many per (class, subclass) instead of per class")
    args = parser.parse_args(argv)
    print(export_production(args.out, revision=args.revision, per_class=args.per_class,
                            doc_class=args.doc_class, per_subclass=args.per_subclass))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
