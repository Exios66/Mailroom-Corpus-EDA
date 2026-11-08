"""Durable local mirror of reviewed labels inside the labels-staging repository.

Writes a timestamped, self-contained copy of the review packets and the portal's
decision ledger, then refuses any backup whose reviewed documents duplicate a
document reviewed in an earlier backup (same content sha256). Agents read this
copy when the Hub is unreachable and use it to vet decisions. No network calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

MANIFEST = "local-backup.json"
PORTAL_STATE = ("confirmed-fields.jsonl", "checkpoints.jsonl")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def packet_files(labels_root: Path, out: Path | None = None) -> list[Path]:
    """Finished review packets under labels_root, excluding the portal's own output."""
    out = out.resolve() if out else None
    return sorted(p for p in Path(labels_root).rglob("*-review-data.json")
                  if not (out and p.resolve().is_relative_to(out)))


def reviewed_documents(packets: Sequence[Path], decisions: Sequence[dict]) -> list[dict]:
    """Documents with at least one decided field, carrying those field decisions."""
    latest: dict[tuple[str, str], dict] = {}
    for event in decisions:
        latest[(event["document_id"], event["field"])] = event
    documents: dict[str, dict] = {}
    for path in packets:
        packet = json.loads(path.read_text(encoding="utf-8"))
        for record in packet.get("records", []):
            event = latest.get((record["document_id"], record["field"]))
            if not event or event.get("decision") == "NOT_REVIEWED":
                continue
            doc = documents.setdefault(record["document_id"], {
                "document_id": record["document_id"],
                "document_sha256": record["document_sha256"],
                "doc_class": record.get("doc_class"),
                "packet": str(path),
                "fields": [],
            })
            if doc["document_sha256"] != record["document_sha256"]:
                raise ValueError(f"mixed source text for document: {record['document_id']}")
            doc["fields"].append({
                "field": record["field"], "decision": event["decision"],
                "reviewer": event.get("reviewer", ""),
                "server_received_at": event.get("server_received_at", ""),
                "receipt_id": event.get("receipt_id", ""),
            })
    return sorted(documents.values(), key=lambda d: d["document_id"])


def prior_reviewed(root: Path) -> dict[str, str]:
    """document_sha256 -> the earlier backup directory that reviewed it."""
    prior: dict[str, str] = {}
    if not root.exists():
        return prior
    for manifest in sorted(root.rglob(MANIFEST)):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for doc in data.get("reviewed_documents", []):
            prior.setdefault(doc["document_sha256"], str(manifest.parent))
    return prior


def backup(labels_root: Path, dest_root: Path, *, out: Path | None = None,
           allow_re_review: bool = False, now: datetime | None = None) -> dict:
    """Write one timestamped local backup and return its manifest.

    Raise ValueError when a reviewed document duplicates an already backed-up
    reviewed document (named in the error) unless ``allow_re_review`` is set.
    Filesystem errors propagate."""
    labels_root, dest_root = Path(labels_root).resolve(), Path(dest_root).resolve()
    packets = packet_files(labels_root, out)
    if not packets:
        raise ValueError(f"no finished review packets under {labels_root}")
    portal = Path(out).resolve() if out else None
    decisions = _lines((portal / PORTAL_STATE[0]) if portal else labels_root / "confirmed-fields.jsonl")
    reviewed = reviewed_documents(packets, decisions)

    prior = prior_reviewed(dest_root)
    duplicates = sorted({doc["document_id"] for doc in reviewed if doc["document_sha256"] in prior})
    if duplicates and not allow_re_review:
        raise ValueError(
            "already reviewed in an earlier backup: " + ", ".join(duplicates)
            + "; pass allow_re_review=True for an explicit re-review/correction")

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    target = dest_root / stamp
    while target.exists():
        target = target.with_name(target.name + "-1")
    target.mkdir(parents=True)

    files: dict[str, str] = {}
    for path in packets:
        rel = Path("packets") / path.relative_to(labels_root)
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target / rel)
        files[str(rel)] = _sha256(target / rel)
    portal_out = target / "portal"
    portal_out.mkdir()
    for name in PORTAL_STATE:
        source = (portal / name) if portal else None
        if source and source.exists():
            shutil.copy2(source, portal_out / name)
            files[f"portal/{name}"] = _sha256(portal_out / name)
    returns = portal / "returns" if portal else None
    if returns and returns.is_dir():
        (portal_out / "returns").mkdir()
        for path in sorted(returns.glob("*.json")):
            shutil.copy2(path, portal_out / "returns" / path.name)
            files[f"portal/returns/{path.name}"] = _sha256(portal_out / "returns" / path.name)

    manifest = {
        "schema": "mailroom-local-review-backup/v1",
        "created_at": stamp,
        "labels_root": str(labels_root),
        "packet_ids": sorted({json.loads(p.read_text(encoding="utf-8"))["packet_id"] for p in packets}),
        "documents": len(reviewed),
        "decisions": sum(len(doc["fields"]) for doc in reviewed),
        "re_review_allowed": bool(allow_re_review),
        "reviewed_documents": reviewed,
        "files": files,
    }
    (target / MANIFEST).write_text(_json(manifest) + "\n", encoding="utf-8")
    lines = [f"# Local review backup {stamp}", "",
             f"Source: `{labels_root}`", "",
             f"- documents reviewed: **{manifest['documents']}**",
             f"- field decisions: **{manifest['decisions']}**",
             f"- packets: {len(manifest['packet_ids'])}", ""]
    for doc in reviewed:
        lines.append(f"- `{doc['document_id']}` ({doc['doc_class']}): "
                     + ", ".join(f"{f['field']}={f['decision']}[{f['reviewer']}]" for f in doc["fields"]))
    (target / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {**manifest, "path": str(target)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels-root", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True,
                        help="labels-staging reviewed-labels folder; backups land in a timestamped subfolder")
    parser.add_argument("--out", type=Path, default=None, help="portal output dir (decision ledger)")
    parser.add_argument("--allow-re-review", action="store_true",
                        help="permit correcting a document already reviewed in an earlier backup")
    args = parser.parse_args(argv)
    try:
        result = backup(args.labels_root, args.dest, out=args.out, allow_re_review=args.allow_re_review)
    except (ValueError, KeyError, OSError) as err:
        parser.exit(1, f"local backup refused: {err}\n")
    print(_json({k: result[k] for k in ("path", "documents", "decisions")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
