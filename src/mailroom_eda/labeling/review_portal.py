"""Loopback-only finished-packet collector and private, per-field human review ledger.

No gold labels or campaign approvals are written. A private HF checkpoint happens
only on an explicit POST, and only when the operator enabled --hf-checkpoint.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
import re
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from . import checkpoint, registry, review_page, scoring, verify
from .review_packet import DECISION_KEY

MAX_BODY = 10 * 1024 * 1024


PUBLIC_FALLBACK_DIR = Path("review_bundle") / "production-insurance"
PUBLIC_FALLBACK_CLASS = "insurance_claim"


class Conflict(ValueError):
    """A browser is attempting to save an obsolete packet."""


def packet_paths(labels_root: Path, out: Path) -> list[Path]:
    """Return finished ``*-review-data.json`` packets under labels_root, excluding portal output."""
    out = Path(out).resolve()
    return sorted(p for p in Path(labels_root).rglob("*-review-data.json")
                  if not p.resolve().is_relative_to(out))


def ensure_public_fallback(labels_root: Path, out: Path, *, revision: str | None = None,
                           per_subclass: int = 2, export=None) -> Path | None:
    """Export public insurance claims to review when no labeling packet exists yet.

    While the v10 claim_checklist campaign is still producing packets, the
    published dataset (pinned v9.3 unless ``revision`` says otherwise) is what the
    reviewer falls back to: up to ``per_subclass`` insurance documents per claim
    type, every registry field, published values as candidates. Return the
    written packet path, or None when finished packets already exist. Hub and
    validation errors propagate; nothing is uploaded."""
    if packet_paths(labels_root, out):
        return None
    if export is None:
        from .review_production import export_production as export
    return export(Path(labels_root) / PUBLIC_FALLBACK_DIR, revision=revision,
                  doc_class=PUBLIC_FALLBACK_CLASS, per_subclass=per_subclass)


def _json(value: Any) -> bytes:
    """Encode compact, sorted UTF-8 JSON; raise ValueError for nonfinite numbers."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _now() -> str:
    """Return the current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> Any:
    """Read and decode a UTF-8 JSON file; parsing and filesystem errors propagate."""
    return json.loads(path.read_text(encoding="utf-8"))


def _lines(path: Path) -> list[dict]:
    """Read nonblank JSONL entries, returning an empty list for a missing file.

    Malformed records and read errors propagate instead of being skipped."""
    if not path.exists():
        return []
    # A damaged audit ledger must be repaired explicitly, never skipped as if reviewed.
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def _append(path: Path, events: list[dict]) -> None:
    """Append events as JSONL and flush them to disk; filesystem errors propagate."""
    if events:
        with path.open("ab") as fh:
            fh.write(b"".join(_json(event) + b"\n" for event in events))
            fh.flush()
            os.fsync(fh.fileno())


def _stat(path: Path) -> tuple:
    """Return path, nanosecond modification time, size, and inode for cache invalidation."""
    stat = path.stat()
    return str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino


def _target_hash(record: Mapping[str, Any]) -> str:
    """Hash a review target excluding portal collection provenance."""
    return review_page.packet_id([{k: v for k, v in record.items() if k != "portal_provenance"}])


def _schema_blocker(record: Mapping[str, Any]) -> str:
    """Return a schema-blocking reason, or an empty string for an applicable pinned target."""
    if record.get("eligible_for_gold") is False or str(record.get("schema_alignment", "")).startswith("proposal"):
        return "proposal_only_not_pinned; human review is not schema approval or gold"
    if record["field"] == "v10_subclass":
        return "proposed v10 subclass is outside the pinned Dojo registry"
    if record["field"] == "subclass" and record["doc_class"] == "corporate_record":
        return ""
    try:
        if record["field"] not in registry.class_fields(record["doc_class"], record.get("subclass")):
            return "field not applicable in pinned Dojo schema"
    except KeyError:
        return "class/subclass outside pinned Dojo schema"
    return ""


def _confirmed_value(record: Mapping[str, Any], decision: Mapping[str, Any]) -> Any:
    """Return the candidate or correction after type, vocabulary, and grounding checks.

    Documented null absences are allowed only by the field policy. Raise
    ValueError for invalid values or catalog drift; missing registry keys and
    malformed correction JSON errors propagate."""
    field, cls = record["field"], record["doc_class"]
    value = (json.loads(decision["corrected_labels_json"]) if decision["decision"] == "CORRECT"
             else record.get("candidate_value", record.get("candidate_labels")))
    if isinstance(value, dict) and field in value:
        value = value[field]
    if field == "subclass" and cls == "corporate_record":
        if not isinstance(value, str) or value not in registry.dojo_snapshot()["corporate_subclass_schemas"]:
            raise ValueError("subclass is outside the pinned corporate vocabulary")
        return value
    spec = registry.class_fields(cls, record.get("subclass")).get(field)
    if spec is None:
        raise ValueError(f"field is not applicable in the pinned registry: {cls}.{field}")
    type_ = scoring.dojo_type(cls, field)
    if record.get("dojo_type", type_) != type_:
        raise ValueError("review target type differs from the pinned Dojo registry")
    if value is None:
        if spec["absence"] == "never_absent" or record.get("candidate_presence") not in (
                "schema_documented_absence", "not_applicable"):
            raise ValueError("null confirmation requires documented absence and an absence-permitting field")
        return None
    if type_.startswith("entity_list"):
        shape = isinstance(value, list) and bool(value) and all(isinstance(v, str) and v.strip() for v in value)
    elif type_ == "money":
        shape = (isinstance(value, (str, int, float)) and not isinstance(value, bool)
                 and verify.parse_money(value) is not None and math.isfinite(verify.parse_money(value)))
    else:
        shape = isinstance(value, str) and bool(value.strip())
    if not shape:
        raise ValueError(f"wrong or empty correction/candidate shape for {type_}")
    doc = verify.DocIndex.build(record["full_document_text"])
    if field == "claim_checklist":
        result = scoring.score_checklist(value, doc)
        if not result.passed:
            raise ValueError("checklist source checks failed: " + ", ".join(f.code for f in result.findings))
    elif not verify.grounded(value, type_, doc,
                             vocab=registry.vocabulary(spec["vocabulary"]) if spec.get("vocabulary") else None,
                             vocab_part=spec.get("vocabulary_part")):
        raise ValueError(f"candidate/correction is not grounded in source: {field}")
    return value


class ReviewPortal:
    """One operator, multiple tabs; file locks also serialize cooperating CLI writers."""

    def __init__(self, labels_root: Path, out: Path, *, campaign: str = "v10",
                 stage: Path | None = None, hf_checkpoint: bool = False):
        """Create local portal and receipt directories and initialize review state caches.

        ``out`` must be a strict subfolder of labels_root, and campaign must be a safe
        name; invalid values raise ValueError. ``hf_checkpoint`` enables explicit
        backup requests. Filesystem errors propagate."""
        self.labels_root, self.out = Path(labels_root).resolve(), Path(out).resolve()
        if not self.out.is_relative_to(self.labels_root) or self.out == self.labels_root:
            raise ValueError("portal output must be a subfolder of labels-root for private checkpoints")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", campaign):
            raise ValueError("invalid campaign name")
        self.campaign, self.stage, self.hf_checkpoint = campaign, stage, hf_checkpoint
        self.out.mkdir(parents=True, exist_ok=True)
        self.returns = self.out / "returns"
        self.returns.mkdir(exist_ok=True)
        self.journal = self.out / "confirmed-fields.jsonl"
        self.checkpoints = self.out / "checkpoints.jsonl"
        self._lock = threading.RLock()
        self._packet_key = self._tracker_key = None
        self._packet = None
        self._tracker = None
        self._stage_key = None
        self._stage_rows = None

    @contextmanager
    def _locked(self):
        """Yield while holding thread and file locks; reject symlinked state paths with ValueError."""
        with self._lock:
            for path in (self.returns, self.journal, self.checkpoints, self.out / ".portal.lock"):
                if path.is_symlink():
                    raise ValueError("portal state paths must not be symlinks")
            with (self.out / ".portal.lock").open("a") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)

    def _collect(self) -> dict:
        """Collect validated finished packets, choosing the lexically last path per target.

        Exclude portal output and cache by file metadata. Raise ValueError for unsafe
        paths, empty inventory, or inconsistent document identities; raise Conflict
        if a packet changes during its read. Packet and read errors propagate."""
        paths = packet_paths(self.labels_root, self.out)
        if any(p.is_symlink() or not p.resolve().is_relative_to(self.labels_root) for p in paths):
            raise ValueError("source review packets must be local regular files, not symlinks")
        fingerprint = tuple(_stat(p) for p in paths)
        if self._packet_key == fingerprint and self._packet is not None:
            return self._packet
        winners, origins = {}, {}
        # Restore changes mtimes; path order makes the same backup choose the same packet.
        for path in sorted(paths):
            before = _stat(path)
            packet = _read(path)
            targets = review_page.validate_packet(packet)
            if before != _stat(path):
                raise Conflict("source packet changed while it was being read; retry")
            origin = {"path": str(path.relative_to(self.labels_root)), "packet_id": packet["packet_id"],
                      "source_revision": packet.get("source_revision")}
            for key, record in targets.items():
                origins.setdefault(key, []).append(origin)
                winners[key] = copy.deepcopy(record)
        if not winners:
            raise ValueError("no finished *-review-data.json packets found")
        documents = {}
        for key, record in winners.items():
            identity = record["document_sha256"], record["doc_class"], record["split"]
            if key[0] in documents and documents[key[0]] != identity:
                raise ValueError(f"mixed source text/class/split across fields of document: {key[0]}")
            documents[key[0]] = identity
            record["portal_provenance"] = {"selected": origins[key][-1], "superseded": origins[key][:-1],
                                           "selection": "lexically last packet path; use ordered batch directory names"}
        records = [winners[key] for key in sorted(winners)]
        combined = {"schema": review_page.PACKET_SCHEMA, "packet_id": review_page.packet_id(records),
                    "decision_key": DECISION_KEY, "records": records}
        review_page.validate_packet(combined)
        self._packet, self._packet_key = combined, fingerprint
        return combined

    def _states(self, packet: dict) -> list[dict]:
        """Derive current field statuses from the latest matching events and verified backups."""
        latest = {}
        for event in _lines(self.journal):
            latest[event["document_id"], event["field"]] = event
        backed_up = {event for cp in _lines(self.checkpoints) if cp.get("verified") is True
                     for event in cp["event_ids"]}
        states = []
        for record in packet["records"]:
            key = record["document_id"], record["field"]
            event = latest.get(key)
            valid = event is not None and event["target_sha256"] == _target_hash(record)
            status = (("checkpointed" if event["event_id"] in backed_up else "human_confirmed") if event["human_confirmed"]
                      else "reviewed_needs_schema" if event.get("schema_blocker") and event["decision"] in (
                          "ACCEPT", "CORRECT") else "changes_requested") if valid else "awaiting_review"
            states.append({"document_id": key[0], "field": key[1], "status": status,
                           "decision": event["decision"] if valid else "NOT_REVIEWED",
                           "stale_confirmation": bool(event and not valid),
                           "blocker": event.get("schema_blocker", "") if valid else "",
                           "receipt_id": event["receipt_id"] if valid else None})
        return states

    def _state_revision(self) -> str:
        """Hash the current confirmation and checkpoint journals, treating missing files as empty."""
        return hashlib.sha256(b"\0".join(p.read_bytes() if p.exists() else b""
                                        for p in (self.journal, self.checkpoints))).hexdigest()

    def _campaign_tracker(self, packet: dict, states: list[dict]) -> dict:
        """Return campaign gate, approval, inventory, and loaded review counts.

        Unavailable selection inventories become unknown totals with a blocker;
        other registry, parsing, and filesystem errors propagate."""
        from . import operate, runner

        folder = registry.CAMPAIGNS_DIR / self.campaign
        campaign_path, approvals_path = folder / "campaign.yaml", folder / "approved.yaml"
        camp = registry.campaign(self.campaign)
        counts = Counter(str(c["id"]) for c in camp["chunks"])
        arms, entries = Counter(), []
        for entry in camp["chunks"]:
            cid = str(entry["id"])
            arms[cid] += 1
            arm = arms[cid] if counts[cid] > 1 else None
            entries.append((entry, arm, operate.chunk_dir(self.labels_root, entry, arm) / "gate.json"))
        entries += [(e, None, operate.step_gate_path(self.labels_root, e)) for e in camp.get("steps", [])]
        stage_path = Path(self.stage if self.stage is not None else runner.DEFAULT_STAGE) / "ground_truth_hardened.jsonl"
        inputs = [campaign_path, approvals_path, stage_path, *(path for _, _, path in entries)]
        fingerprint = tuple(_stat(p) if p.exists() else (str(p), None) for p in inputs)
        if self._tracker_key != fingerprint:
            if self._stage_key != (stage_sig := _stat(stage_path) if stage_path.exists() else None):
                self._stage_rows = runner.load_stage(stage_path.parent) if stage_sig else None
                self._stage_key = stage_sig
            import yaml

            approvals = (yaml.safe_load(approvals_path.read_text(encoding="utf-8")) or {}).get("approved", []) if approvals_path.exists() else []
            tasks = []
            for entry, arm, gate_path in entries:
                cid = str(entry["id"])
                approval = next((a for a in reversed(approvals) if str(a["chunk"]) == cid
                                 and a.get("arm") == arm and a.get("task") == entry["task"]), None)
                gate = _read(gate_path) if gate_path.exists() else None
                selected, why = None, ""
                if entry in camp["chunks"] and self._stage_rows is not None:
                    try:
                        # Same selection as operate.pick: expected_subclass filter (one value or a list).
                        rows = runner.select(self._stage_rows, entry["task"], entry["set"], entry.get("subclass"))
                        selected = {str(r.get("document_id") or r.get("id") or r["filename"])
                                    for r in runner.chunk(rows, entry.get("chunk"))}
                    except (ValueError, KeyError, OSError, SystemExit) as exc:
                        why = "inventory unavailable: " + type(exc).__name__
                dependencies = [str(n) for n in entry.get("needs", [])
                                if not any(str(a["chunk"]) == str(n) for a in approvals)]
                status = ("skipped" if entry.get("skip") else "approved" if approval else
                          "blocked" if dependencies else "gate_passed_unapproved" if gate and gate.get("passed") else
                          "gate_failed" if gate else "pending")
                tasks.append({"id": cid, "arm": arm, "task": entry["task"], "set": entry["set"],
                              "status": status, "documents_total": len(selected) if selected is not None else None,
                              "approved_by": approval.get("approved_by") if approval else None,
                              "blocker": entry.get("skip") or ("unapproved dependencies: " + ", ".join(dependencies) if dependencies else why),
                              "gate_passed": gate.get("passed") if gate else None, "_ids": selected})
            self._tracker = {"name": camp.get("name", self.campaign), "tasks": tasks}
            self._tracker_key = fingerprint
        tracker = copy.deepcopy(self._tracker)
        state_by = {(s["document_id"], s["field"]): s for s in states}
        for task in tracker["tasks"]:
            ids = task.pop("_ids")
            matching = [r for r in packet["records"] if ids is not None and r["document_id"] in ids and (
                r.get("task") == task["task"] or (task["task"] == "claim_checklist" and r["field"] == "claim_checklist")
                or (task["task"] == "corporate" and r["doc_class"] == "corporate_record"))]
            doc_fields = {}
            for record in matching:
                doc_fields.setdefault(record["document_id"], []).append(state_by[record["document_id"], record["field"]])
            reviewed = sum(all(s["status"] in ("human_confirmed", "checkpointed") for s in ss) for ss in doc_fields.values())
            task.update(reviewed=reviewed, ready=len(doc_fields),
                        remaining=max(0, task["documents_total"] - reviewed) if ids is not None else None,
                        loaded_target_fields=len(matching), inventory_known=ids is not None)
        revisions = sorted({r["portal_provenance"]["selected"]["source_revision"] for r in packet["records"]
                            if r["portal_provenance"]["selected"].get("source_revision")})
        tracker.update(source_revision=revisions[0] if len(revisions) == 1 else None,
                       source_revisions=revisions, updated_at=_now(),
                       loaded_target_fields=len(states), human_confirmed_fields=sum(s["status"] in (
                           "human_confirmed", "checkpointed") for s in states),
                       checkpointed_fields=sum(s["status"] == "checkpointed" for s in states),
                       reviewed_needs_schema_fields=sum(s["status"] == "reviewed_needs_schema" for s in states),
                       remaining_loaded_fields=sum(s["status"] not in ("human_confirmed", "checkpointed") for s in states),
                       campaign_complete=all(t["status"] in ("approved", "skipped") for t in tracker["tasks"]),
                        scope="loaded finished packets only; sample review is not campaign approval; unknown totals are null")
        origin = self.labels_root / "review-source.json"
        tracker["private_source"] = _read(origin) if origin.exists() else {
            "repo": checkpoint.STAGING_REPO, "revision": None,
            "status": "local packets; private HF restore not verified in this checkout"}
        tracker["campaign_file"] = str(campaign_path)
        tracker["approval_file"] = str(approvals_path)
        return tracker

    def _data(self) -> dict:
        """Assemble packet, current review state, saved receipts, revision queue, and tracker."""
        packet = self._collect()
        states = self._states(packet)
        docs = {}
        for state in states:
            docs.setdefault(state["document_id"], []).append(state)
        saved, history = [], []
        for state in states:
            if not state["receipt_id"]:
                continue
            path = self.returns / f"review-{state['receipt_id']}.json"
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != state["receipt_id"]:
                raise ValueError("stored review receipt hash mismatch")
            receipt = json.loads(raw)
            key = state["document_id"], state["field"]
            saved.extend(d for d in receipt["decisions"] if (d["document_id"], d["field"]) == key)
            history.extend(h for h in receipt["history"] if (h["document_id"], h["field"]) == key)
        records = {(r["document_id"], r["field"]): r for r in packet["records"]}
        decisions = {(d["document_id"], d["field"]): d for d in saved}
        revision_queue = []
        for state in states:
            key = state["document_id"], state["field"]
            record = records[key]
            if (record.get("source_dataset") != "Lucius-Morningstar/mailroom-dataset"
                    or state["status"] not in ("human_confirmed", "checkpointed")):
                continue
            decision = decisions[key]
            revision_queue.append({"document_id": key[0], "field": key[1],
                                   "source_dataset": record["source_dataset"],
                                   "source_revision": record.get("source_revision"),
                                   "document_sha256": record["document_sha256"], "split": record["split"],
                                   "status": state["status"], "receipt_id": state["receipt_id"],
                                   "value": json.loads(decision["corrected_labels_json"]) if decision["decision"] == "CORRECT"
                                   else record.get("candidate_value", record.get("candidate_labels")),
                                   "deployment_status": "pending v10 gates; production unchanged"})
        return {**copy.deepcopy(packet), "review_states": states, "state_revision": self._state_revision(),
                 "saved_decisions": saved, "saved_history": history,
                 "revision_queue": revision_queue,
                "documents": [{"document_id": doc, "loaded_target_fields": len(ss),
                               "loaded_targets_complete": all(s["status"] in ("human_confirmed", "checkpointed") for s in ss),
                               "scope": "all loaded applicable targets only; not whole-corpus completeness"}
                              for doc, ss in docs.items()],
                "tracker": self._campaign_tracker(packet, states), "checkpoint_enabled": self.hf_checkpoint}

    def data(self) -> dict:
        """Return the current packet and review state while holding the portal locks."""
        with self._locked():
            return self._data()

    def render(self) -> bytes:
        """Write the current standalone review page and return its HTML bytes under the portal locks."""
        with self._locked():
            path = review_page.render_page(self._data(), self.out / "review.html")
            return path.read_bytes()

    def save(self, returned: Mapping[str, Any]) -> dict:
        """Validate returned decisions, persist a receipt and field events, and return state.

        Return the receipt ID, changed-field count, and refreshed data. Repeated
        identical decisions add no events; NOT_REVIEWED preserves prior decisions.
        Raise Conflict for stale packets or new receipts based on stale state.
        Return-contract, schema, grounding, and filesystem errors propagate."""
        with self._locked():
            packet = self._collect()
            if returned.get("packet_id") != packet["packet_id"]:
                raise Conflict("packet changed; reload the latest source before saving")
            review_page.validate_return(packet, returned)
            targets = review_page.validate_packet(packet)
            raw = _json(returned)
            receipt_id = hashlib.sha256(raw).hexdigest()
            receipt = self.returns / f"review-{receipt_id}.json"
            if returned.get("state_revision") != self._state_revision() and not receipt.exists():
                raise Conflict("review state changed in another tab; reload before saving")
            events = _lines(self.journal)
            latest = {(e["document_id"], e["field"]): e for e in events}
            previous_ids = {e["event_id"] for e in events}
            changes, received_at = [], _now()
            for decision in returned["decisions"]:
                if decision["decision"] == "NOT_REVIEWED":
                    continue
                key = decision["document_id"], decision["field"]
                source = targets[key]
                target_hash = _target_hash(source)
                prior = latest.get(key)
                signature = hashlib.sha256(_json(decision)).hexdigest()
                event_id = hashlib.sha256(_json([receipt_id, *key])).hexdigest()
                if event_id in previous_ids or (prior and prior["decision_sha256"] == signature
                                                and prior["target_sha256"] == target_hash):
                    continue
                reviewed = decision["decision"] in ("ACCEPT", "CORRECT")
                schema_blocker = _schema_blocker(source) if reviewed else ""
                confirmed = reviewed and not schema_blocker
                value = (_confirmed_value(source, decision) if confirmed else
                         (json.loads(decision["corrected_labels_json"]) if decision["decision"] == "CORRECT"
                          else source.get("candidate_value", source.get("candidate_labels")) if reviewed else None))
                changes.append({"event_id": event_id, "receipt_id": receipt_id,
                                "receipt_sha256": receipt_id, "packet_id": packet["packet_id"],
                                "document_id": key[0], "field": key[1], "document_sha256": source["document_sha256"],
                                "target_sha256": target_hash, "split": source["split"],
                                "decision": decision["decision"], "value": value, "human_confirmed": confirmed,
                                "schema_blocker": schema_blocker,
                                "reviewer": decision["reviewer_initials"], "reviewer_notes": decision["reviewer_notes"],
                                "server_received_at": received_at, "decision_sha256": signature,
                                "provenance": source["portal_provenance"]})
            if receipt.is_symlink():
                raise ValueError("receipt must not be a symlink")
            try:
                with receipt.open("xb") as fh:
                    fh.write(raw)
                    fh.flush()
                    os.fsync(fh.fileno())
            except FileExistsError:
                if receipt.read_bytes() != raw:
                    raise ValueError("immutable receipt content mismatch") from None
            _append(self.journal, changes)
            return {"receipt_id": receipt_id, "changed_fields": len(changes), "data": self._data()}

    def checkpoint(self, packet_id: str, *, api: Any = None) -> dict:
        """Back up pending confirmations and return a checkpoint receipt or failure status.

        Raise PermissionError when backups are disabled and Conflict for an initially
        stale packet. No pending fields returns verified=False without an upload.
        Upload or verification failures, including SystemExit, are recorded and
        converted to verified=False; local confirmations remain pending. Errors before
        the backup attempt or while writing its journal can propagate."""
        with self._locked():
            if not self.hf_checkpoint:
                raise PermissionError("private checkpoints are not enabled")
            packet = self._collect()
            if packet_id != packet["packet_id"]:
                raise Conflict("packet changed; reload before checkpointing")
            states = self._states(packet)
            pending = {(s["document_id"], s["field"]) for s in states if s["status"] == "human_confirmed"}
            if not pending:
                return {"verified": False, "pending_fields": 0, "message": "no local confirmations pending"}
            latest = {(e["document_id"], e["field"]): e for e in _lines(self.journal)}
            event_ids = [latest[k]["event_id"] for k in sorted(pending)]
            state_revision = self._state_revision()
            try:
                api = api or checkpoint.hf_interface.get_hf_api()
                result = checkpoint.checkpoint(self.labels_root, registry.CAMPAIGNS_DIR / self.campaign / "approved.yaml",
                                               "explicit human review portal checkpoint", api=api)
                if result.get("verified") is not True or result.get("repo") != checkpoint.STAGING_REPO:
                    raise ValueError("checkpoint was not verified against the private staging repo")
                revision = result.get("revision") or api.repo_info(repo_id=result["repo"], repo_type="dataset").sha
                if not revision:
                    raise ValueError("verified checkpoint revision unavailable")
                if self._collect()["packet_id"] != packet_id or self._state_revision() != state_revision:
                    raise Conflict("source or review state changed during checkpoint; reload and retry")
            except (Exception, SystemExit) as exc:
                _append(self.checkpoints, [{"verified": False, "server_received_at": _now(),
                                           "error_type": type(exc).__name__, "event_ids": event_ids}])
                return {"verified": False, "pending_fields": len(event_ids),
                        "message": "private checkpoint failed; local confirmations remain pending"}
            receipt = {"verified": True, "revision": revision, "repo": result["repo"],
                       "files": result["files"], "event_ids": event_ids, "server_received_at": _now()}
            _append(self.checkpoints, [receipt])
            return receipt


def make_server(portal: ReviewPortal, port: int = 8765) -> ThreadingHTTPServer:
    """Return a server bound to 127.0.0.1; the caller starts its serving loop.

    ``port=0`` requests an available port. Fixed routes validate Host and
    same-origin JSON writes. Binding failures propagate as OSError."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            """Suppress HTTP access logging."""
            pass  # Source IDs, reviewer notes and tokens never enter access logs.

        def _reply(self, status, value, content_type="application/json; charset=utf-8"):
            """Send bytes or JSON with the supplied status and response security headers."""
            body = value if isinstance(value, bytes) else _json(value)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _security(self, post=False):
            """Raise PermissionError for non-loopback requests or invalid origins; writes require Origin."""
            port = self.server.server_address[1]
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in hosts or self.client_address[0] != "127.0.0.1":
                raise PermissionError("loopback Host required")
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in hosts}:
                raise PermissionError("cross-origin requests are forbidden")
            if post and not origin:
                raise PermissionError("same-origin Origin header required for writes")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise PermissionError("cross-site requests are forbidden")

        def _error(self, exc):
            """Send Conflict as HTTP 409, PermissionError as 403, and other handled errors as 400."""
            status = 409 if isinstance(exc, Conflict) else 403 if isinstance(exc, PermissionError) else 400
            self._reply(status, {"error": str(exc)})

        def _unexpected(self, exc):
            """Log an unhandled error server-side and answer HTTP 500 without its details."""
            self.log_error("unhandled %s: %s", type(exc).__name__, exc)
            self._reply(500, {"error": "internal error; see the portal log"})

        def do_GET(self):
            """Serve fixed review routes; convert handled validation and I/O errors to HTTP responses."""
            try:
                self._security()
                if self.path in ("/", "/review.html"):
                    self._reply(200, portal.render(), "text/html; charset=utf-8")
                elif self.path == "/review-data.json":
                    self._reply(200, portal.data())
                elif self.path == "/revision-queue.json":
                    data = portal.data()
                    self._reply(200, {"schema": "mailroom-v10-manual-revisions/v1",
                                      "packet_id": data["packet_id"], "state_revision": data["state_revision"],
                                      "revisions": data["revision_queue"]})
                else:
                    self._reply(404, {"error": "unknown route"})
            except (ValueError, KeyError, OSError, PermissionError) as exc:
                self._error(exc)
            except Exception as exc:  # noqa: BLE001 - always answer; never drop the connection
                self._unexpected(exc)

        def do_POST(self):
            """Validate JSON writes up to 10 MiB, then save reviews or explicitly checkpoint them."""
            try:
                self._security(post=True)
                if self.path not in ("/api/reviews", "/api/checkpoint"):
                    self._reply(404, {"error": "unknown API route"})
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                    self._reply(415, {"error": "Content-Type must be application/json"})
                    return
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("chunked request bodies are not supported")
                size = int(self.headers.get("Content-Length", "-1"))
                if not 0 < size <= MAX_BODY:
                    self._reply(413, {"error": "JSON body must be between 1 byte and 10 MiB"})
                    return
                body = self.rfile.read(size)
                if len(body) != size:
                    raise ValueError("incomplete request body")
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise ValueError("JSON body must be an object")
                if self.path == "/api/reviews":
                    self._reply(200, portal.save(value))
                elif set(value) != {"packet_id", "confirm"} or value["confirm"] is not True:
                    raise ValueError("checkpoint requires packet_id and explicit confirm=true")
                else:
                    result = portal.checkpoint(value["packet_id"])
                    self._reply(200 if result["verified"] else 503, result)
            except (ValueError, KeyError, OSError, PermissionError) as exc:
                self._error(exc)
            except Exception as exc:  # noqa: BLE001 - always answer; never drop the connection
                self._unexpected(exc)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None) -> int:
    """Optionally restore a pinned private checkpoint, then serve the portal until interrupted."""
    from .runner import DEFAULT_OUT, DEFAULT_STAGE

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels-root", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--campaign", default="v10")
    parser.add_argument("--stage", type=Path, default=DEFAULT_STAGE)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--hf-checkpoint", action="store_true", help="enable explicit private-checkpoint POSTs only")
    parser.add_argument("--restore-revision", help="restore private HF labels at this immutable commit before serving; existing conflicts fail closed")
    parser.add_argument("--no-public-fallback", action="store_true",
                        help="do not fall back to public insurance claims when no packets exist")
    parser.add_argument("--public-revision", help="public dataset revision for the fallback (default: pinned v9.3)")
    parser.add_argument("--public-per-subclass", type=int, default=2,
                        help="fallback documents per insurance claim type (default 2)")
    args = parser.parse_args(argv)
    if args.restore_revision:
        if not re.fullmatch(r"[0-9a-f]{40}", args.restore_revision):
            parser.error("--restore-revision requires a full immutable HF commit SHA")
        restored = checkpoint.restore(args.labels_root, revision=args.restore_revision)
        args.labels_root.mkdir(parents=True, exist_ok=True)
        (args.labels_root / "review-source.json").write_bytes(_json({
            "repo": checkpoint.STAGING_REPO, "revision": args.restore_revision,
            "status": "byte-verified private restore", "restored_files": len(restored)}))
    out = args.out or args.labels_root / "review_bundle" / "portal"
    if not args.no_public_fallback:
        fallback = ensure_public_fallback(args.labels_root, out, revision=args.public_revision,
                                          per_subclass=args.public_per_subclass)
        if fallback is not None:
            print(f"No labeling packets yet; reviewing public insurance claims from {fallback}")
    portal = ReviewPortal(args.labels_root, out,
                          campaign=args.campaign, stage=args.stage, hf_checkpoint=args.hf_checkpoint)
    server = make_server(portal, args.port)
    print(f"Private review portal: http://127.0.0.1:{server.server_port}/ (no automatic uploads)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
