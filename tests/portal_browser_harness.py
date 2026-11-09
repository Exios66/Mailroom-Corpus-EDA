"""Serve the real review portal for the browser end-to-end test.

The browser test must not talk to a hand-written mock: it drives the shipped
``review_portal`` HTTP handler, the shipped HTML page, the real receipt/journal
code, the real campaign tracker, and the real launcher preflight. Only the paid
edges are seamed:

* the private HF checkpoint API (in-memory, byte-verified like the Hub);
* the launcher's OpenRouter chat / key-status / public-dataset calls.

Packets are built from the committed live smoke fixtures (real exporter output)
plus a few labelled clones that add the production, schema-pending and unsafe
link cases. Usage::

    PYTHONPATH=src python tests/portal_browser_harness.py --root /tmp/e2e

It prints one JSON line with ``portal``, ``control`` and ``root`` URLs, then
serves until the control route ``/control/shutdown`` is called.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mailroom_eda import config  # noqa: E402
from mailroom_eda.labeling import checkpoint, export, operate, registry, review_page  # noqa: E402
from mailroom_eda.labeling import review_portal, runner  # noqa: E402
from mailroom_eda.labeling.review_packet import DECISION_KEY  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "live_smoke"
GLM = "z-ai/glm-5.3-flashx-legal"
DEEPSEEK = "deepseek/deepseek-v4.1-flash"
EMAIL = "Dear Ms. Ahamer,\n\nPlease find the demand attached.\n\nRegards,\nKevin G Moore\n"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name / "expected-review-data.json").read_text(encoding="utf-8"))


def write_packet(path: Path, records: list[dict]) -> dict:
    packet = {"schema": review_page.PACKET_SCHEMA, "records": records,
              "decision_key": DECISION_KEY, "packet_id": review_page.packet_id(records)}
    review_page.validate_packet(packet)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
    return packet


def tagged(records: list[dict], *, batch: str, task: str, set_name: str, slice_: str = "") -> list[dict]:
    """Copy fixture records into one review batch, keeping all exporter metadata."""
    out = []
    for record in records:
        row = json.loads(json.dumps(record))
        row.update(batch=batch, chunk="", task=task, set=set_name)
        if slice_:
            row["slice"] = slice_
        out.append(row)
    return out


def production_record() -> dict:
    """A published-dataset record: no batch header, real revision, revision-queue eligible."""
    source = next(r for r in fixture("corporate_record")["records"]
                  if r["field"] == "jurisdiction" and r["candidate_status"] == "source_checks_passed")
    row = json.loads(json.dumps(source))
    row.update(document_id="public-v9.3:PROD-0001", published_document_id="PROD-0001",
               batch="", chunk="", task="corporate", set="remainder",
               source_dataset=config.REPO_ID, source_revision=config.REPO_DATA_REVISION,
               kind="production_manual", review_provenance="Published v9.3 field, re-checked by hand")
    return row


def schema_pending_record() -> dict:
    """A target outside the pinned registry: ACCEPT must save as needs-schema, never gold."""
    source = next(r for r in fixture("corporate_record")["records"]
                  if r["field"] == "registered_agent" and r["candidate_status"] == "source_checks_passed")
    row = json.loads(json.dumps(source))
    row.update(document_id="public-v9.3:PROPOSAL-0001", batch="8a", chunk="", task="corporate",
               set="pilot", field="proposed_new_field", candidate_labels="CT Corporation System",
               eligible_for_gold=False, schema_alignment="proposal_only",
               schema_status="proposal_only", kind="labeling_batch")
    return row


def unsafe_link_record() -> dict:
    """A record whose source URL must never become a clickable javascript: link."""
    data = fixture("correspondence")
    source = next(r for r in data["records"] if r["field"] == "sender")
    text = source["full_document_text"]
    row = json.loads(json.dumps(source))
    row.update(document_id="public-v9.3:UNSAFE-0001", published_document_id="UNSAFE-0001",
               batch="7a", chunk="", task="correspondence", set="pilot",
               source_url="javascript:window.injected=1")
    assert hashlib.sha256(text.encode()).hexdigest() == row["document_sha256"]
    return row


def picker_record() -> dict:
    """A three-item list target with every name grounded in the source text."""
    text = ("This charter amendment is signed by Alice Smith, Bob Jones and Carol White.\n"
            "Each signatory approved the amendment on 2026-03-04.\n")
    start = text.index("Alice Smith")
    evidence = [{"start": start, "end": text.index("Carol White") + len("Carol White"),
                 "quote": text[start:text.index("Carol White") + len("Carol White")]}]
    items = ["Alice Smith", "Bob Jones", "Carol White"]
    return {
        "document_id": "public-v9.3:PICKER-0001", "published_document_id": "PICKER-0001",
        "field": "signatories", "filename": "picker_charter.txt", "doc_class": "corporate_record",
        "subclass": "charter_amendment", "split": "train", "candidate_labels": items,
        "candidate_status": "source_checks_passed", "candidate_presence": "populated",
        "absence_policy": "absent_ok", "correction_type": "array", "dojo_type": "entity_list:name",
        "document_sha256": hashlib.sha256(text.encode()).hexdigest(), "full_document_text": text,
        "evidence": evidence, "existing_gt_fields": {}, "issues": [], "models": [GLM, DEEPSEEK],
        "model_outputs": [
            {"model": GLM, "value": items, "status": "present", "evidence": evidence[0]["quote"],
             "evidence_spans": evidence},
            {"model": DEEPSEEK, "value": items, "status": "present", "evidence": evidence[0]["quote"],
             "evidence_spans": evidence},
        ],
        "batch": "8a", "chunk": "", "task": "corporate", "set": "pilot", "slice": "batch 1/2",
        "kind": "labeling_batch", "schema_status": "pinned_registry", "eligible_for_gold": True,
        "review_provenance": "Synthetic picker target built from the shipped fixture shape",
    }


def packet_sets() -> dict[str, list[dict]]:
    corporate = tagged(fixture("corporate_record")["records"], batch="8a", task="corporate",
                       set_name="pilot", slice_="batch 1/2")
    correspondence = tagged(fixture("correspondence")["records"], batch="7a", task="correspondence",
                            set_name="pilot", slice_="batch 1/1")
    insurance = tagged(fixture("insurance_claim")["records"], batch="8b", task="claim_checklist",
                       set_name="remainder", slice_="batch 1/1")
    return {
        "batch-0001/browser-corporate-review-data.json": corporate + [
            production_record(), schema_pending_record(), picker_record()],
        "batch-0002/browser-correspondence-review-data.json": correspondence + [unsafe_link_record()],
        "batch-0003/browser-insurance-review-data.json": insurance,
    }


def stage_rows() -> list[dict]:
    """A small stage whose ids match the loaded packets, so tracker counts are meaningful."""
    rows = []
    for record in fixture("corporate_record")["records"]:
        if record["document_id"] not in {r["document_id"] for r in rows}:
            rows.append({"document_id": record["document_id"], "filename": record["filename"],
                         "expected": "corporate_record", "expected_subclass": record["subclass"],
                         "doc_text": record["full_document_text"], "split": record["split"],
                         "gt_fields": {}, "metadata": {}})
    for record in fixture("correspondence")["records"]:
        if record["document_id"] not in {r["document_id"] for r in rows}:
            rows.append({"document_id": record["document_id"], "filename": record["filename"],
                         "expected": "correspondence", "expected_subclass": record["subclass"],
                         "doc_text": record["full_document_text"], "split": record["split"],
                         "gt_fields": {}, "metadata": {}})
    for record in fixture("insurance_claim")["records"]:
        rows.append({"document_id": record["document_id"], "filename": record["filename"],
                     "expected": "insurance_claim", "expected_subclass": record["subclass"],
                     "doc_text": record["full_document_text"], "split": record["split"],
                     "gt_fields": {}, "metadata": {}})
    # Extra pool rows so pilot/remainder splits both have inventory (the tracker shows real totals).
    for cls, sub, n in (("corporate_record", "charter_amendment", 4),
                        ("correspondence", "press_release", 4),
                        ("insurance_claim", "outpatient", 6)):
        for i in range(1, n + 1):
            rows.append({"document_id": f"pool-{cls[:4]}-{sub[:4]}-{i}", "filename": f"{cls}_{sub}_{i}.txt",
                         "expected": cls, "expected_subclass": sub,
                         "doc_text": "Pool document that still needs labels.\n", "split": "train",
                         "gt_fields": {}, "metadata": {}})
    return rows


def public_rows() -> list[dict]:
    """The fake public dataset a trial downloads (journals carry model answers only)."""
    rows = []
    for n in range(1, 4):
        rows.append({"document_id": f"cont-{n:02d}", "filename": f"contract_{n}.txt", "expected": "contract",
                     "expected_subclass": "Consulting Agreements", "doc_text": "Services agreement\n" * 30,
                     "split": "train", "source_corpus": "sec_edgar",
                     "gt_fields": json.dumps({"gt_presence": json.dumps({"cuad_clause_labels": "pending_annotation"})}),
                     "metadata": {}})
    for n in range(1, 3):
        rows.append({"document_id": f"merg-{n:02d}", "filename": f"merger_{n}.txt",
                     "expected": "merger_agreement", "expected_subclass": "other",
                     "doc_text": "Agreement and plan of merger\n" * 30, "split": "train",
                     "gt_fields": json.dumps({}), "metadata": {}})
    for n in range(1, 5):
        rows.append({"document_id": f"mail-{n:02d}", "filename": f"mail_{n}.txt",
                     "expected": "correspondence", "expected_subclass": "attorney_demand",
                     "doc_text": EMAIL + f"Ref {n}\n", "split": "train",
                     "gt_fields": json.dumps({"intent": "request"}), "metadata": {}})
    for n in range(1, 5):
        rows.append({"document_id": f"claim-{n:02d}", "filename": f"claim_{n}.txt",
                     "expected": "insurance_claim", "expected_subclass": "outpatient",
                     "doc_text": "Claim determination\n", "split": "train",
                     "gt_fields": json.dumps({}), "metadata": {}})
    for n in range(1, 4):
        rows.append({"document_id": f"corp-{n:02d}", "filename": f"corp_{n}.txt",
                     "expected": "corporate_record", "expected_subclass": "bylaws",
                     "doc_text": "Acme Corp bylaws\n", "split": "train",
                     "gt_fields": json.dumps({"entity_name": "Acme Corp."}), "metadata": {}})
    return rows


def answer(schema: dict) -> dict:
    """The smallest valid labeling answer for any schema the runner sends."""
    properties = schema.get("properties", {})
    if "clauses" in properties:
        return {"clauses": []}
    fields = properties.get("fields", {}).get("properties", {})
    return {"fields": {name: {"value": None, "status": "not_found", "evidence": ""} for name in fields}}


class FakeChat:
    """The launcher's paid edges: answers, key status, public rows. Records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.gate: threading.Event | None = None

    def chat(self, backend, model, messages, schema, *, reasoning, max_tokens):
        from mailroom_eda.labeling import providers  # noqa: PLC0415

        self.calls.append((model, reasoning, max_tokens, backend.api_key))
        if self.gate is not None:
            self.gate.wait(30)
        body = json.dumps(answer(schema))
        return providers.Completion(body, answer(schema), model, "CoreWeave", 100, 50, 0, 0.0001, "stop", True)

    def key_status(self, backend):
        return {"limit_remaining": 50.0, "label": "sk-or-v1-browser...key"}

    def hub(self):
        return {"rows": public_rows(), "revision": "ab" * 20, "source_sha256": "cd" * 32}


class FakeHubApi:
    """Enough of ``HfApi`` for the real checkpoint: an in-memory private dataset repo."""

    revision = "a" * 40

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.fail = False
        self.uploads = 0

    def create_repo(self, repo_id, repo_type, private, exist_ok):
        assert private is True

    def repo_info(self, repo_id, repo_type, revision=None):
        return SimpleNamespace(private=True, sha=self.revision)

    def upload_folder(self, folder_path, repo_id, repo_type, path_in_repo, commit_message, allow_patterns):
        self.uploads += 1
        if self.fail:
            raise OSError("synthetic Hub outage")
        for path in Path(folder_path).rglob("*"):
            if path.is_file():
                self.files[path.relative_to(folder_path).as_posix()] = path.read_bytes()
        return SimpleNamespace(oid=self.revision)

    def list_repo_tree(self, repo_id, repo_type, recursive, revision=None):
        return [SimpleNamespace(path=name, lfs=None, blob_id=checkpoint.git_blob_sha(data))
                for name, data in self.files.items()]


def campaign_def(stage_sha: str) -> dict:
    return {
        "name": "browser", "max_wall_seconds": 600, "stage_sha256": stage_sha,
        "chunks": [
            {"id": "8a", "task": "corporate", "set": "pilot", "models": [GLM], "reasoning": "off",
             "cap_usd": 0.50, "chunk": "1/1"},
            {"id": "7a", "task": "correspondence", "set": "pilot", "models": [DEEPSEEK],
             "reasoning": "off", "cap_usd": 0.20},
            {"id": "9b", "needs": ["8a"], "task": "corporate", "set": "remainder", "models": [DEEPSEEK],
             "reasoning": "off", "cap_usd": 0.30},
            {"id": "1", "task": "cuad", "set": "calibration", "models": [GLM], "reasoning": "low",
             "cap_usd": 0.30},
            {"id": "2a", "needs": ["1"], "task": "cuad", "set": "pending", "models": ["winner_of_1"],
             "reasoning": "winner_of_1", "cap_usd": 0.40},
            {"id": "2b", "task": "cuad", "set": "pending", "models": [GLM, DEEPSEEK],
             "reasoning": "off", "cap_usd": 0.40},
            {"id": "6c1", "task": "merger_consideration", "set": "pending", "models": [GLM, DEEPSEEK],
             "reasoning": "off", "cap_usd": 0.25},
            {"id": "7b", "task": "correspondence", "set": "remainder", "models": [DEEPSEEK],
             "reasoning": "off", "cap_usd": 0.30},
            {"id": "7s", "task": "correspondence", "set": "pilot", "models": [DEEPSEEK],
             "reasoning": "off", "cap_usd": 0.0, "skip": "superseded"},
            {"id": "8b", "task": "claim_checklist", "set": "remainder", "models": [GLM],
             "reasoning": "off", "cap_usd": 0.30},
            {"id": "9c", "task": "corporate", "set": "calibration", "models": [GLM],
             "reasoning": "off", "cap_usd": 0.30},
        ],
        "steps": [],
    }


class Portal(review_portal.ReviewPortal):
    """The shipped portal with only the paid HF checkpoint seam replaced."""

    def __init__(self, *args, fake_api: FakeHubApi, **kwargs):
        self.fake_api = fake_api
        super().__init__(*args, **kwargs)

    def checkpoint(self, packet_id: str, *, api=None):
        return super().checkpoint(packet_id, api=api or self.fake_api)


def build(root: Path) -> tuple[Portal, Path, dict]:
    labels = root / "labels"
    out = labels / "review_bundle" / "portal"
    labels.mkdir(parents=True, exist_ok=True)
    for relative, records in packet_sets().items():
        write_packet(labels / relative, records)

    campaigns = root / "campaigns" / "browser"
    campaigns.mkdir(parents=True)
    stage = root / "stage"
    stage.mkdir()
    rows = stage_rows()
    stage_file = stage / "ground_truth_hardened.jsonl"
    stage_file.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    stage_sha = hashlib.sha256(stage_file.read_bytes()).hexdigest()
    (campaigns / "campaign.yaml").write_text(json.dumps(campaign_def(stage_sha)), encoding="utf-8")
    registry.CAMPAIGNS_DIR = root / "campaigns"
    operate.CAMPAIGNS_DIR = root / "campaigns"
    runner.DEFAULT_STAGE = stage

    fake = FakeChat()
    portal = Portal(
        labels, out, campaign="browser", stage=stage, hf_checkpoint=True, launch_enabled=True,
        launch_options={"chat": fake.chat, "key_status": fake.key_status, "hub": fake.hub,
                        "env": {}},
        local_backup=root / "mirror",
        fake_api=FakeHubApi(),
    )
    return portal, labels, {"fake": fake, "stage": stage, "campaigns": root / "campaigns"}


class Control:
    """Test-only control plane (never part of the portal): inspect state, add packets, break Hub."""

    def __init__(self, portal: Portal, labels: Path, seams: dict):
        self.portal, self.labels, self.seams = portal, labels, seams
        self.fake_api: FakeHubApi = portal.fake_api

    def snapshot(self) -> dict:
        out = self.portal.out
        def lines(path):
            return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] \
                if path.exists() else []
        return {
            "root": str(self.labels), "out": str(out),
            "journal": lines(self.portal.journal),
            "checkpoints": lines(self.portal.checkpoints),
            "receipts": sorted(p.name for p in (out / "returns").glob("review-*.json")),
            "launches": lines(out / "launches.jsonl"),
            "hub_files": sorted(self.fake_api.files),
            "hub_uploads": self.fake_api.uploads,
            "mirror": str(self.portal.local_backup) if self.portal.local_backup else None,
            "mirror_files": sorted(str(p.relative_to(self.portal.local_backup))
                                   for p in self.portal.local_backup.rglob("*")
                                   if p.is_file()) if self.portal.local_backup else [],
        }

    def handle(self, action: str, body: dict) -> dict:
        if action == "snapshot":
            return self.snapshot()
        if action == "add-packet":
            record = json.loads(json.dumps(fixture("corporate_record")["records"][2]))
            record.update(document_id=body.get("document_id", "public-v9.3:LATE-0001"),
                          batch="9a", chunk="", task="corporate", set="pilot")
            records = [record]
            write_packet(self.labels / body.get("path", "batch-0009/late-review-data.json"), records)
            return {"written": body.get("path", "batch-0009/late-review-data.json"),
                    "target": [record["document_id"], record["field"]]}
        if action == "checkpoint-fail":
            self.fake_api.fail = bool(body.get("fail", True))
            return {"fail": self.fake_api.fail}
        if action == "touch-state":
            # A cooperating writer saves one target through the real portal: the page's
            # held state_revision is now stale, exactly like a second reviewer tab.
            data = self.portal.data()
            target = data["records"][0]
            at = datetime.now(timezone.utc).isoformat()
            decision = {"document_id": target["document_id"], "field": target["field"],
                        "document_sha256": target["document_sha256"], "decision": "NEEDS_SOURCE",
                        "corrected_labels_json": "", "reviewer_initials": "ZZ",
                        "reviewer_notes": "other tab", "updated_at": at}
            body = {"schema": review_page.RETURN_SCHEMA, "packet_id": data["packet_id"],
                    "state_revision": data["state_revision"], "exported_at": at,
                    "decisions": [decision],
                    "history": [{"at": at, "document_id": decision["document_id"],
                                 "field": decision["field"], "before": {}, "after": decision}]}
            result = self.portal.save(body)
            return {"receipt_id": result["receipt_id"], "target": [target["document_id"], target["field"]]}
        if action == "approve-chunk":
            folder = self.seams["campaigns"] / "browser"
            path = folder / "approved.yaml"
            data = json.loads(path.read_text()) if path.exists() else {"approved": []}
            data["approved"].append(body)
            path.write_text(json.dumps(data, indent=1))
            return {"approved": body}
        if action == "gate":
            entry = next(e for e in campaign_def("x")["chunks"] if e["id"] == body["chunk"])
            gate = operate.chunk_dir(self.labels, entry, None) / "gate.json"
            gate.parent.mkdir(parents=True, exist_ok=True)
            gate.write_text(json.dumps({"passed": bool(body.get("passed", True)), "task": entry["task"]}))
            return {"gate": str(gate)}
        if action == "write-ping":
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            for model, reasoning in ((GLM, "low"), (GLM, "off"), (DEEPSEEK, "off")):
                export.append_journal(self.labels / "ping.jsonl", {
                    "model": model, "reasoning": reasoning, "utc": now,
                    "response": {"ok": True, "word": "mailroom"}, "used_schema": True})
            return {"pings": str(self.labels / "ping.jsonl")}
        if action == "receive":
            # Validate the page's exported return against the packet the portal actually
            # serves (the combined multi-packet view), exactly as a review agent would.
            packet = self.portal._collect()
            review_page.validate_return(packet, body["payload"])
            return {"valid": True, "packet_id": packet["packet_id"],
                    "decisions": len(body["payload"]["decisions"])}
        raise ValueError(f"unknown control action: {action}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="labels root to create (default: a temp dir)")
    parser.add_argument("--keep", action="store_true", help="reuse --root instead of deleting it")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    root = (args.root or Path(ROOT / ".browser-portal")).resolve()
    if root.exists() and not args.keep:
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    portal, labels, seams = build(root)
    control = Control(portal, labels, seams)
    server = review_portal.make_server(portal, args.port)

    class ControlHandler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/snapshot":
                self._json(200, control.snapshot())
            elif self.path == "/health":
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "unknown"})

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size) or b"{}") if size else {}
            if self.path == "/shutdown":
                self._json(200, {"ok": True})
                threading.Thread(target=server.shutdown, daemon=True).start()
                return
            try:
                self._json(200, control.handle(self.path.lstrip("/"), body))
            except Exception as exc:  # noqa: BLE001 - the driver must see why a control call failed
                self._json(400, {"error": f"{type(exc).__name__}: {exc}"})

    control_server = ThreadingHTTPServer(("127.0.0.1", 0), ControlHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    control_thread = threading.Thread(target=control_server.serve_forever, daemon=True)
    thread.start()
    control_thread.start()
    print(json.dumps({
        "portal": f"http://127.0.0.1:{server.server_port}",
        "control": f"http://127.0.0.1:{control_server.server_port}",
        "root": str(labels), "out": str(portal.out),
        "phase": "ready",
    }), flush=True)
    try:
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        control_server.shutdown()
        control_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
