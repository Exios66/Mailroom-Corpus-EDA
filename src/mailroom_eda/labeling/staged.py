"""Review candidates from the sandbox's staged production label overlay (never labels).

``local-mailroom-sandbox`` merges its label layers (Hub v9.3, interim, near-gold, reviewed,
CUAD-derived) into one master and stages it in the mailroom-issues repository as a
``production-overlay`` pre-release (``mailroom_cache/v9.3.0/pre-release/v9.3.1-rc.N/``).
Production scoring applies it now; v10 takes from it only what a person accepts, through
the gates v10 already has. This module turns the overlay into two candidate files:

- ``staged-fill-candidates.csv``: a staged value for a v10 cell that is still
  ``pending_annotation``. Nothing here enters ``v10_build``: route a candidate through a
  calibrated chunk or a reviewer verdict like any other proposed value.
- ``staged-gold-correction-candidates.csv``: a staged value that differs from a gold
  (``populated`` or ``schema_documented_absence``) cell, in ``gold-corrections.csv`` columns
  with ``decided_by`` blank. ``ingest`` refuses a correction without initials, so a candidate
  reaches the dataset only when a person reads it, initials it and moves the row into
  ``gold-corrections.csv`` themselves.

Cells where the staged value equals the v10 cell, fields outside ``fields.yaml``, ``subclass``
(v10 derives it) and ``cuad_clauses`` / ``maud_clauses`` (the sandbox keeps category names,
v10 keeps spans and answers) are counted and dropped.

Integrity: the cut's ``MANIFEST.json`` must have the sha256 given on the command line (the pin
the sandbox records in ``config/staged_overlays.yaml``), and every overlay file must match its
manifest hash. A record is used only for the stage row whose ``content_sha256`` or
``source_content_sha256`` equals the record's and whose class matches.

    python -m mailroom_eda.labeling.staged --cut <mailroom-issues>/mailroom_cache/v9.3.0/pre-release/v9.3.1-rc.3 \\
        --manifest-sha256 <pin> [--stage data/v9_4/stage] [--out docs/reports/audits/v10_labels/staged]

Offline; no spend. The output stays in the gitignored labels root by default.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

KIND = "production-overlay"
FILL_FILE = "staged-fill-candidates.csv"
CORRECTION_FILE = "staged-gold-correction-candidates.csv"
FILL_COLUMNS = ("document_id", "filename", "field", "value", "presence", "evidence", "source", "agreement",
                "staged_version")
#: overlay fields v10 does not take from here (see the module docstring)
DROPPED_FIELDS = frozenset({"subclass", "cuad_clauses", "maud_clauses"})
GOLD = ("populated", "schema_documented_absence")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(cut: Path | str, *, manifest_sha256: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``(manifest, overlay records)`` of a pinned cut; ``SystemExit`` on any byte difference."""
    d = Path(cut)
    mpath = d / "MANIFEST.json"
    if not mpath.exists():
        raise SystemExit(f"{mpath}: not found")
    raw = mpath.read_bytes()
    if _sha256(raw) != manifest_sha256:
        raise SystemExit(f"{mpath}: sha256 {_sha256(raw)[:12]} is not the pinned {manifest_sha256[:12]}")
    manifest = json.loads(raw)
    if manifest.get("kind") != KIND:
        raise SystemExit(f"{mpath}: kind {manifest.get('kind')!r}, not {KIND!r}")
    records = []
    files = {k: v for k, v in (manifest.get("files") or {}).items() if k.startswith("overlay/")}
    if not files:
        raise SystemExit(f"{mpath}: lists no overlay/ files")
    for rel, entry in sorted(files.items()):
        data = (d / rel).read_bytes() if (d / rel).exists() else b""
        if _sha256(data) != entry.get("sha256"):
            raise SystemExit(f"{d / rel}: missing or not the sha256 MANIFEST.json pins")
        records += [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    return manifest, records


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def candidates(records: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], version: str
               ) -> tuple[list[dict[str, str]], list[dict[str, str]], Counter]:
    """``(fills, corrections, counts)`` for the stage ``rows`` (``runner.load_stage``)."""
    from .. import v10_build

    by_id = {str(r.get("document_id")): r for r in rows}
    fills: list[dict[str, str]] = []
    corrections: list[dict[str, str]] = []
    counts: Counter = Counter()
    for rec in records:
        row = by_id.get(str(rec.get("doc_id")))
        if row is None:
            counts["no_document"] += 1
            continue
        if rec.get("content_sha256") not in (row.get("content_sha256"), row.get("source_content_sha256")):
            counts["hash_mismatch"] += 1
            continue
        cls = str(rec.get("doc_class"))
        if row.get("expected") != cls:
            counts["class_mismatch"] += 1
            continue
        derived = v10_build.derive_row(row)
        fields = v10_build.class_fields(cls)
        applicable = v10_build.applicable_fields(cls, derived["subclass"])
        for name, lab in sorted((rec.get("labels") or {}).items()):
            spec = fields.get(name)
            if name in DROPPED_FIELDS or spec is None:
                counts["dropped_field"] += 1
                continue
            current, presence, _prov = v10_build.field_cell(derived, name, spec, applicable)
            if presence == "not_applicable":
                counts["not_applicable"] += 1
                continue
            absent = lab.get("presence") == "documented_absence"
            try:
                staged = None if absent else v10_build.typed(lab.get("value"), spec["type"])
            except ValueError:
                counts["untyped_value"] += 1
                continue
            if staged is None and not absent:
                counts["empty_value"] += 1
                continue
            staged_presence = "schema_documented_absence" if absent else "populated"
            if presence == "pending_annotation":
                counts["fill_candidates"] += 1
                fills.append({"document_id": row["document_id"], "filename": row["filename"], "field": name,
                              "value": _cell_text(staged), "presence": staged_presence,
                              "evidence": json.dumps(lab.get("evidence") or [], ensure_ascii=False),
                              "source": str(lab.get("source") or ""), "agreement": str(lab.get("agreement") or ""),
                              "staged_version": version})
            elif presence in GOLD and (staged, staged_presence) != (current, presence):
                counts["correction_candidates"] += 1
                gt = v10_build._gt(row)
                key = v10_build.LEGACY_KEY.get(name, name)
                corrections.append({
                    "document_id": row["document_id"], "filename": row["filename"], "field": name,
                    "gold_was": _cell_text(gt.get(key)), "gold_now": _cell_text(staged),
                    "reason": (f"staged overlay v{version} ({lab.get('source')}, {lab.get('agreement')}; "
                               f"sandbox override={bool(lab.get('override'))}); evidence "
                               + json.dumps([e.get("text") for e in lab.get("evidence") or []], ensure_ascii=False)),
                    "decided_by": "", "decided_utc": ""})
            else:
                counts["agrees_with_v10"] += 1
    return fills, corrections, counts


def write(out: Path, fills: Sequence[Mapping[str, str]], corrections: Sequence[Mapping[str, str]]) -> None:
    from .operate import GOLD_CORRECTION_COLUMNS

    out.mkdir(parents=True, exist_ok=True)
    for name, columns, rows in ((FILL_FILE, FILL_COLUMNS, fills),
                                (CORRECTION_FILE, GOLD_CORRECTION_COLUMNS, corrections)):
        with (out / name).open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(columns), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    from . import runner

    ap = argparse.ArgumentParser(prog="python -m mailroom_eda.labeling.staged", description=__doc__.split("\n")[0])
    ap.add_argument("--cut", type=Path, required=True, help="a production-overlay pre-release folder")
    ap.add_argument("--manifest-sha256", required=True, help="the cut's pinned MANIFEST.json sha256")
    ap.add_argument("--stage", type=Path, default=runner.DEFAULT_STAGE)
    ap.add_argument("--out", type=Path, default=runner.DEFAULT_OUT / "staged")
    args = ap.parse_args(argv)
    manifest, records = load(args.cut, manifest_sha256=args.manifest_sha256)
    version = str(manifest.get("version") or "")
    fills, corrections, counts = candidates(records, runner.load_stage(args.stage), version)
    out = args.out / f"v{version}"
    write(out, fills, corrections)
    print(json.dumps({"out": str(out), "version": version, **dict(sorted(counts.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
