"""Cross-check v10 labels against the sandbox's interim overlay (flag only).

``local-mailroom-sandbox`` keeps unpublished enhancements to the v9.2 ground truth in
``data/labels/interim/``: gate-supported extractions (``gap_fill``), CUAD-checked fixes
(``correction``), values computed from the row (``derived``) and Hub labels a gated
extraction contradicts (``disputed``). The PR 43 plan imports its contract parties and
dates corrections as candidates in chunks 5a/5b, verified like any other label.

This module reads the overlay at run time (no copy is committed: v10 label output stays
out of the public repo) and puts it beside the models' answers in ``fields.csv``:

- ``interim_value`` / ``interim_status``: the overlay's value and status;
- ``interim_verified``: the value is non-empty and grounded in this document's text
  (``verify.grounded`` with the field's type and vocabulary);
- ``interim_agree``: which model values agree with it (``a+b``, ``a``, ``b`` or ``none``).

It never changes ``decision`` or adds work to adjudication. A reviewer may pick the
overlay with ``reviewer_verdict: interim`` when ``interim_verified`` is true; the row's
``final_source`` then records ``interim:<gate_run>``.

Integrity: every overlay file must match ``MANIFEST.json``, and the manifest must match
``campaign.yaml`` ``interim_overlay.manifest_sha256`` when that is set. A record is used
only when its ``doc_id`` is a stage ``document_id`` and its ``doc_sha256`` (a 16-hex
prefix) prefixes that row's ``content_sha256`` or v10 ``source_content_sha256``.
``derived`` labels (computed from the subclass, no model involved) and fields outside
``fields.yaml`` are dropped.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import verify
from .registry import fields_registry, vocabulary

#: where the overlay lives inside a sandbox checkout
INTERIM_DIR = Path("data/labels/interim")
#: statuses carried into fields.csv (``derived`` is dropped)
USED_STATUSES = ("gap_fill", "correction", "disputed")


@dataclass
class Overlay:
    """The usable overlay labels, keyed by ``(document_id, field)``."""

    manifest_sha256: str
    labels: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    skipped: Counter = field(default_factory=Counter)

    def get(self, document_id: str, name: str) -> dict[str, Any] | None:
        """The overlay label for one field, or ``None``."""
        return self.labels.get((document_id, name))

    def summary(self) -> dict[str, Any]:
        """Counts for gate.json and status lines."""
        return {"manifest_sha256": self.manifest_sha256, "labels": len(self.labels),
                "by_status": dict(Counter(v["status"] for v in self.labels.values())),
                "skipped": dict(self.skipped)}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(sandbox: Path | str | None, rows: Sequence[Mapping[str, Any]], *,
         manifest_sha256: str | None = None) -> Overlay | None:
    """The overlay labels for ``rows``, or ``None`` when ``sandbox`` has no overlay.

    Raises ``SystemExit`` when the manifest differs from ``manifest_sha256`` or a file
    differs from its manifest hash: a cross-check against an unknown overlay is worse
    than none.
    """
    if sandbox is None:
        return None
    d = Path(sandbox) / INTERIM_DIR
    mpath = d / "MANIFEST.json"
    if not mpath.exists():
        return None
    sha = _sha256(mpath)
    if manifest_sha256 and sha != manifest_sha256:
        raise SystemExit(f"{mpath}: sha256 {sha[:12]} differs from campaign.yaml interim_overlay "
                         f"{manifest_sha256[:12]}; pin the new overlay after reading what changed")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    for name, want in sorted((manifest.get("files") or {}).items()):
        got = _sha256(d / name) if (d / name).exists() else "missing"
        if got != want:
            raise SystemExit(f"{d / name}: sha256 {got[:12]} differs from MANIFEST.json {want[:12]}")
    by_id = {str(r.get("document_id")): r for r in rows}
    classes = fields_registry()["classes"]
    out = Overlay(manifest_sha256=sha)
    for name in sorted(manifest.get("files") or {}):
        for line in (d / name).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            row = by_id.get(str(rec.get("doc_id")))
            if row is None:
                out.skipped["no_document"] += 1
                continue
            prefix = str(rec.get("doc_sha256") or "")
            if not prefix or not any(str(row.get(k) or "").startswith(prefix)
                                     for k in ("content_sha256", "source_content_sha256")):
                out.skipped["hash_mismatch"] += 1
                continue
            cls = str(rec.get("doc_class"))
            if row.get("expected") != cls:
                out.skipped["class_mismatch"] += 1
                continue
            spec = (classes.get(cls) or {}).get("fields") or {}
            gate_run = str((rec.get("source") or {}).get("gate_run") or "")
            for fname, lab in sorted((rec.get("labels") or {}).items()):
                status = lab.get("status")
                if status not in USED_STATUSES:
                    out.skipped[status or "no_status"] += 1
                    continue
                if fname not in spec:
                    out.skipped["unregistered_field"] += 1
                    continue
                out.labels[(str(row["document_id"]), fname)] = {
                    "value": lab.get("value"), "status": status, "basis": lab.get("basis"),
                    "hub_value": lab.get("hub_value"), "hub_source": lab.get("hub_source"),
                    "gate_run": gate_run}
    return out


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def annotate(row: dict[str, Any], overlay: Overlay | None, spec: Mapping[str, Any], idx: verify.DocIndex) -> None:
    """Add the ``interim_*`` columns to one ``fields.csv`` row (no-op without a label)."""
    lab = overlay.get(str(row["document_id"]), str(row["field"])) if overlay is not None else None
    if lab is None:
        return
    t = row["type"]
    vocab = vocabulary(spec["vocabulary"]) if spec.get("vocabulary") else None
    value = lab["value"]
    ok = not _empty(value) and verify.grounded(value, t, idx, vocab=vocab, vocab_part=spec.get("vocabulary_part"))
    agree = [s for s in ("a", "b") if row.get(f"model_{s}") and not _empty(row.get(f"model_{s}_value"))
             and verify.agree(value, row.get(f"model_{s}_value"), t)]
    row.update(interim_value=value, interim_status=lab["status"], interim_verified=bool(ok),
               interim_agree="+".join(agree) or "none", interim_gate_run=lab["gate_run"])


def counts(fields: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Informational agreement counts for gate.json and status."""
    seen = [f for f in fields if f.get("interim_status")]
    return {"rows": len(seen),
            "by_status": dict(Counter(str(f["interim_status"]) for f in seen)),
            "verified": sum(1 for f in seen if f.get("interim_verified") in (True, "true")),
            "agree": dict(Counter(str(f.get("interim_agree")) for f in seen))}


def intent_rows(overlay: Overlay | None, records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``interim-intent.csv`` rows: the overlay's intent next to each document's two passes."""
    if overlay is None:
        return []
    out = []
    for rec in records:
        lab = overlay.get(str(rec["document_id"]), "intent")
        if lab is None:
            continue
        a = (rec.get("pass_a") or {}).get("intent")
        b = (rec.get("pass_b") or {}).get("intent")
        out.append({"document_id": rec["document_id"], "pass_a": a, "pass_b": b,
                    "interim_value": lab["value"], "interim_status": lab["status"],
                    "hub_value": lab.get("hub_value"), "hub_source": lab.get("hub_source"),
                    "interim_agree": "+".join(s for s, v in (("a", a), ("b", b)) if v == lab["value"]) or "none"})
    return out


INTENT_COLUMNS = ("document_id", "pass_a", "pass_b", "interim_value", "interim_status", "hub_value",
                  "hub_source", "interim_agree")
