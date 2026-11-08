"""Render an offline human-review page and receive its decision log, never gold.

The packet binds every proposal to a document hash. Returned files are validated
and saved beside the page under ``returns/``; no labels, splits, approvals, or Hub
files are changed. Browser timestamps and initials are user claims, not signatures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .spend import INITIALS_RE
from .registry import dojo_snapshot

PACKET_SCHEMA = "mailroom-review-packet/v1"
RETURN_SCHEMA = "mailroom-human-review/v1"
DECISIONS = frozenset({"NOT_REVIEWED", "ACCEPT", "CORRECT", "REJECT", "NEEDS_SOURCE"})


def packet_id(records: Sequence[Mapping[str, Any]]) -> str:
    """Hash the exact ordered review payload, including its source text and labels."""
    data = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def validate_packet(packet: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    """Validate packet integrity and return targets keyed by (document_id, field).

    Raise ValueError for invalid schema, payload or source hashes, duplicate
    targets, decision keys, or source highlights. Evidence offsets are zero-based
    Unicode character positions with exclusive ends; whitespace is normalized
    when comparing quotes. Missing required record keys propagate as KeyError."""
    if packet.get("schema") != PACKET_SCHEMA or not isinstance(packet.get("records"), list):
        raise ValueError("unknown review packet schema")
    records = packet["records"]
    if not records or packet.get("packet_id") != packet_id(records):
        raise ValueError("empty or changed review packet")
    if set(packet.get("decision_key") or {}) != DECISIONS:
        raise ValueError("review packet decision key differs from the return contract")
    by_key = {}
    for r in records:
        key = r["document_id"], r["field"]
        if key in by_key:
            raise ValueError(f"duplicate review target: {key}")
        text = r["full_document_text"]
        if not isinstance(text, str) or hashlib.sha256(text.encode()).hexdigest() != r["document_sha256"]:
            raise ValueError(f"source hash mismatch: {key}")
        for evidence in r.get("evidence", []):
            start, end = evidence.get("start"), evidence.get("end")
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                    or " ".join(text[start:end].split()) != " ".join(str(evidence.get("quote", "")).split())):
                raise ValueError(f"invalid source highlight: {key}")
        by_key[key] = r
    return by_key


def _compact_payload(packet: Mapping[str, Any]) -> dict:
    """Embed each source text once per content sha, not once per field record.

    The page rehydrates ``full_document_text`` client-side; the packet contract and
    /review-data.json keep their per-record text untouched."""
    texts: dict[str, str] = {}
    records = []
    for record in packet["records"]:
        texts.setdefault(record["document_sha256"], record["full_document_text"])
        records.append({k: v for k, v in record.items() if k != "full_document_text"})
    return {**packet, "records": records, "document_texts": texts}


def render_page(packet: Mapping[str, Any], out: Path) -> Path:
    """Embed the full packet safely in a standalone page with no external resources.

    Write ``out`` and a returns/README.md beside it, creating parent directories;
    return the page path. Packet validation errors propagate. Raise ValueError
    for an invalid template placeholder count; filesystem errors propagate."""
    validate_packet(packet)
    template = Path(__file__).with_suffix(".html").read_text(encoding="utf-8")
    marker = "__REVIEW_PACKET_JSON__"
    if template.count(marker) != 1:
        raise ValueError("review page must contain exactly one packet placeholder")
    payload = json.dumps(_compact_payload(packet), ensure_ascii=False)
    for char, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                          ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        payload = payload.replace(char, escaped)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(template.replace(marker, payload), encoding="utf-8")
    returns = out.parent / "returns"
    returns.mkdir(exist_ok=True)
    (returns / "README.md").write_text(
        "# Returned human reviews (private)\n\n"
        "Save the page's downloaded JSON here, or pass it to `review_page receive`.\n"
        "Do not commit review data to the public repository. Receipts are not gold labels.\n"
        "The agent must recheck document hashes and validate corrections before proposing\n"
        "ground-truth edits in each document's existing train/test split. No staging is automatic.\n",
        encoding="utf-8",
    )
    return out


def _time(value: Any) -> bool:
    """Return whether a string parses as an ISO timestamp with a timezone."""
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def validate_return(packet: Mapping[str, Any], returned: Mapping[str, Any]) -> None:
    """Validate human decisions without treating them as automatic ingest authority.

    Partial target sets are allowed. Reviewed decisions require initials, zoned
    timestamps, and a matching latest history entry. ACCEPT requires a candidate
    that passed source checks; CORRECT validates JSON shape and subclass vocabulary,
    not general source grounding. Raise ValueError for contract violations;
    packet errors and missing required packet keys propagate."""
    targets = validate_packet(packet)
    if returned.get("schema") != RETURN_SCHEMA or returned.get("packet_id") != packet["packet_id"]:
        raise ValueError("decision file belongs to another packet or schema")
    if not _time(returned.get("exported_at")):
        raise ValueError("exported_at must be a timestamp with a timezone")
    decisions, history = returned.get("decisions"), returned.get("history")
    if not isinstance(decisions, list) or not decisions or not isinstance(history, list):
        raise ValueError("decisions and history arrays are required")
    seen = set()
    allowed = {"document_id", "field", "document_sha256", "decision", "corrected_labels_json",
               "reviewer_initials", "reviewer_notes", "updated_at",
               "verified_models", "verified_outputs", "manual_source_verification"}
    for d in decisions:
        if not isinstance(d, dict) or set(d) - allowed:
            raise ValueError("unknown decision fields")
        key = d.get("document_id"), d.get("field")
        if key not in targets or key in seen:
            raise ValueError(f"unknown or repeated review target: {key}")
        seen.add(key)
        source = targets[key]
        if d.get("document_sha256") != source["document_sha256"]:
            raise ValueError(f"returned source hash mismatch: {key}")
        decision = d.get("decision")
        if decision not in DECISIONS:
            raise ValueError(f"unknown decision: {decision!r}")
        for name in ("corrected_labels_json", "reviewer_initials", "reviewer_notes"):
            if not isinstance(d.get(name), str):
                raise ValueError(f"{name} must be text")
        verified_models = d.get("verified_models", [])
        if not isinstance(verified_models, list) or not all(isinstance(m, str) for m in verified_models):
            raise ValueError(f"verified_models must be a list of model names: {key}")
        outputs = d.get("verified_outputs", [])
        if not isinstance(outputs, list):
            raise ValueError(f"verified_outputs must be a list: {key}")
        known = {(str(o.get("model")), o.get("pass")) for o in source.get("model_outputs") or []
                 if isinstance(o, dict) and o.get("model")}
        seen_outputs = set()
        for output in outputs:
            if (not isinstance(output, dict) or set(output) - {"model", "pass"}
                    or not isinstance(output.get("model"), str)
                    or not (output.get("pass") is None or type(output.get("pass")) is int)):
                raise ValueError(f"verified_outputs entries need a model name and an optional pass: {key}")
            pair = (output["model"], output.get("pass"))
            if pair in seen_outputs:
                raise ValueError(f"repeated verified output: {key}")
            seen_outputs.add(pair)
            if known and pair not in known:
                raise ValueError(f"verified_outputs names a run this target did not make: {key}")
        if verified_models and known and not set(verified_models) <= {m for m, _ in known}:
            raise ValueError(f"verified_models names a model this target did not run: {key}")
        if not isinstance(d.get("manual_source_verification", False), bool):
            raise ValueError(f"manual_source_verification must be boolean: {key}")
        if decision == "NOT_REVIEWED":
            continue
        if not INITIALS_RE.fullmatch(d["reviewer_initials"]) or not _time(d.get("updated_at")):
            raise ValueError(f"reviewer initials and a timestamp are required: {key}")
        if decision == "ACCEPT" and source["candidate_status"] != "source_checks_passed" \
                and d.get("manual_source_verification") is not True:
            raise ValueError(f"blocked candidate cannot be accepted without manual source verification: {key}")
        if decision == "CORRECT":
            try:
                corrected = json.loads(d["corrected_labels_json"])
            except ValueError as exc:
                raise ValueError(f"correction must be valid JSON: {key}") from exc
            native = source.get("candidate_labels")
            kind = source.get("correction_type") or (
                "list" if source["field"] == "claim_checklist" or isinstance(native, list) else
                "string" if isinstance(native, str) or source["field"] in ("subclass", "v10_subclass") else
                "number" if type(native) in (int, float) else "object")
            expected = {"list": list, "array": list, "string": str, "number": (int, float), "object": dict}.get(kind)
            absent = corrected is None and source.get("candidate_presence") == "schema_documented_absence" and source.get("absence_policy") != "never_absent"
            if not absent and (expected is None or not isinstance(corrected, expected)
                               or isinstance(corrected, bool)
                               or isinstance(corrected, float) and not math.isfinite(corrected)
                               or expected is list and not all(isinstance(x, str) for x in corrected)):
                raise ValueError(f"wrong correction shape: {key}")
            if source["field"] == "subclass" and corrected not in dojo_snapshot()["corporate_subclass_schemas"]:
                raise ValueError(f"subclass correction is outside the pinned Dojo vocabulary: {key}")
        elif d["corrected_labels_json"].strip():
            raise ValueError(f"choose CORRECT when supplying changed labels: {key}")
        matching = [h for h in history if isinstance(h, dict) and h.get("document_id") == key[0]
                    and h.get("field") == key[1] and isinstance(h.get("after"), dict)
                    and _time(h.get("at"))]
        if not matching or matching[-1]["after"] != d:
            raise ValueError(f"reviewed decision has no matching change log: {key}")


def receive(packet_path: Path, input_path: Path) -> Path:
    """Store a validated receipt under returns/ beside the packet and return its path.

    Name receipts by SHA-256 of the original input bytes; identical receipts are
    reused. Raise ValueError for invalid returns, a symlinked returns folder, or
    conflicting existing bytes. JSON parsing and filesystem errors propagate."""
    packet = json.loads(Path(packet_path).read_text(encoding="utf-8"))
    raw = Path(input_path).read_bytes()
    returned = json.loads(raw)
    validate_return(packet, returned)
    folder = Path(packet_path).parent / "returns"
    if folder.is_symlink():
        raise ValueError("returns folder must not be a symlink")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / ("review-" + hashlib.sha256(raw).hexdigest() + ".json")
    try:
        with dest.open("xb") as f:
            f.write(raw)
    except FileExistsError:
        if dest.read_bytes() != raw:
            raise ValueError("existing receipt differs; refusing to overwrite") from None
    return dest


def main(argv: Sequence[str] | None = None) -> int:
    """Render a local page or receive an exported log; never publish or apply labels."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render")
    render.add_argument("--packet", required=True, type=Path)
    render.add_argument("--out", required=True, type=Path)
    returned = commands.add_parser("receive")
    returned.add_argument("--packet", required=True, type=Path)
    returned.add_argument("--input", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "render":
        result = render_page(json.loads(args.packet.read_text(encoding="utf-8")), args.out)
    else:
        result = receive(args.packet, args.input)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
