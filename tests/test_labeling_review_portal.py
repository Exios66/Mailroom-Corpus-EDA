"""Offline synthetic packets, receipts, checkpoint mocks and loopback HTTP checks."""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from mailroom_eda.labeling import checkpoint, registry, review_page, runner
from mailroom_eda.labeling import review_portal as portal
from mailroom_eda.labeling.review_packet import DECISION_KEY

TIME = "2026-11-07T12:00:00+00:00"
TEXT = "Example LLC\nJurisdiction: Delaware\nDate of Report: 2026-02-25\n"


def record(field="entity_name", value="Example LLC", *, doc="D1", text=TEXT, **extra):
    return {"document_id": doc, "field": field, "filename": "synthetic.txt",
            "doc_class": "corporate_record", "subclass": "articles_of_incorporation", "split": "train",
            "candidate_labels": value, "candidate_status": "source_checks_passed",
            "document_sha256": hashlib.sha256(text.encode()).hexdigest(), "full_document_text": text,
            "evidence": [], **extra}


def write_packet(path, records, **extra):
    packet = {"schema": review_page.PACKET_SCHEMA, "records": records, "decision_key": DECISION_KEY,
              "packet_id": review_page.packet_id(records), **extra}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet), encoding="utf-8")
    return packet


@pytest.fixture
def env(tmp_path, monkeypatch):
    campaigns = tmp_path / "campaigns"
    folder = campaigns / "test"
    folder.mkdir(parents=True)
    campaign = {"name": "test", "chunks": [
        {"id": "9a", "task": "corporate", "set": "pilot"},
        {"id": "9b", "task": "corporate", "set": "remainder", "needs": ["9a"]}], "steps": []}
    # JSON is a YAML subset; tests never write actual campaign files.
    (folder / "campaign.yaml").write_text(json.dumps(campaign))
    monkeypatch.setattr(registry, "CAMPAIGNS_DIR", campaigns)
    monkeypatch.setattr(runner, "DEFAULT_STAGE", tmp_path / "missing-stage")
    root = tmp_path / "labels"
    out = root / "review_bundle" / "portal"
    source = root / "review_bundle" / "corporate" / "sample-review-data.json"
    write_packet(source, [record(), record("jurisdiction", "Delaware")])
    return SimpleNamespace(root=root, out=out, source=source, campaigns=folder,
                           app=portal.ReviewPortal(root, out, campaign="test"))


def returned(data, *decisions, at=TIME):
    targets = {(r["document_id"], r["field"]): r for r in data["records"]}
    rows = []
    for field, code, corrected in decisions:
        key = ("D1", field)
        rows.append({"document_id": key[0], "field": field,
                     "document_sha256": targets[key]["document_sha256"], "decision": code,
                     "corrected_labels_json": json.dumps(corrected) if code == "CORRECT" else "",
                     "reviewer_initials": "AB" if code != "NOT_REVIEWED" else "",
                     "reviewer_notes": "Synthetic test decision", "updated_at": at})
    return {"schema": review_page.RETURN_SCHEMA, "packet_id": data["packet_id"],
            "state_revision": data["state_revision"], "exported_at": at, "decisions": rows,
            "history": [{"document_id": d["document_id"], "field": d["field"],
                          "at": at, "before": {}, "after": copy.deepcopy(d)} for d in rows]}


def states(data):
    return {r["field"]: r["status"] for r in data["review_states"]}


def test_collector_finished_packets_only_cache_and_auto_refresh(env, monkeypatch):
    (env.root / "raw").mkdir()
    (env.root / "raw" / "fields.csv").write_text("torn,raw,not_ready")
    (env.root / "raw" / "calls.jsonl").write_text("not JSON")
    first = env.app.data()
    write_packet(env.out / "must-ignore-review-data.json", [record(doc="OWN-OUTPUT")])
    reads = []
    original = portal._read
    monkeypatch.setattr(portal, "_read", lambda path: (reads.append(path), original(path))[1])
    assert env.app.data()["packet_id"] == first["packet_id"]
    assert env.source not in reads
    write_packet(env.root / "future" / "batch-review-data.json", [record(doc="D2")])
    updated = env.app.data()
    assert len(updated["records"]) == 3 and updated["packet_id"] != first["packet_id"]
    assert updated["tracker"]["loaded_target_fields"] == 3


def test_saved_decision_and_history_survive_server_restart(env):
    submission = returned(env.app.data(), ("entity_name", "ACCEPT", None))
    env.app.save(submission)
    restored = portal.ReviewPortal(env.root, env.out, campaign="test").data()
    assert restored["saved_decisions"] == submission["decisions"]
    assert restored["saved_history"] == submission["history"]
    assert restored["tracker"]["remaining_loaded_fields"] == 1
    assert restored["tracker"]["campaign_complete"] is False
    assert all(t["status"] != "approved" for t in restored["tracker"]["tasks"])


def test_private_restore_origin_visible_in_tracker(env):
    origin = {"repo": checkpoint.STAGING_REPO, "revision": "a" * 40,
              "status": "byte-verified private restore"}
    (env.root / "review-source.json").write_text(json.dumps(origin))
    assert env.app.data()["tracker"]["private_source"] == origin


def test_production_confirmation_enters_source_bound_v10_queue(env):
    write_packet(env.source, [record(source_dataset="Lucius-Morningstar/mailroom-dataset",
                                    source_revision="a" * 40)])
    env.app.save(returned(env.app.data(), ("entity_name", "ACCEPT", None)))
    queue = env.app.data()["revision_queue"]
    assert len(queue) == 1
    assert queue[0]["source_revision"] == "a" * 40
    assert queue[0]["split"] == "train" and queue[0]["value"] == "Example LLC"
    assert queue[0]["status"] == "human_confirmed"
    assert queue[0]["deployment_status"] == "pending v10 gates; production unchanged"
    env.app.save(returned(env.app.data(), ("entity_name", "REJECT", None)))
    assert env.app.data()["revision_queue"] == []


def test_packet_selection_survives_restored_mtimes_with_explicit_provenance(env):
    newer = env.root / "z" / "second-review-data.json"
    value = record("entity_name", "Example LLC", review_note="new batch")
    write_packet(newer, [value], source_revision="private-test-revision")
    os.utime(env.source, ns=(100, 100))
    os.utime(newer, ns=(100, 100))
    data = env.app.data()
    found = next(r for r in data["records"] if r["field"] == "entity_name")
    assert found["review_note"] == "new batch"
    assert found["portal_provenance"]["selected"]["path"] == "z/second-review-data.json"
    assert len(found["portal_provenance"]["superseded"]) == 1
    assert data["tracker"]["source_revisions"] == ["private-test-revision"]
    os.utime(env.source, ns=(99999, 99999))
    assert env.app.data()["packet_id"] == data["packet_id"]


def test_mixed_source_versions_across_fields_fail_closed(env):
    write_packet(env.root / "z-new-review-data.json", [record("jurisdiction", "Delaware", text=TEXT + "changed")])
    os.utime(env.source, ns=(100, 100))
    with pytest.raises(ValueError, match="mixed source text"):
        env.app.data()


def test_corrupt_finished_packet_is_not_silently_used_as_ready(env):
    env.source.write_text("{torn")
    with pytest.raises(ValueError):
        env.app.data()


def test_one_of_two_fields_confirmation_is_durable_not_document_or_campaign_complete(env):
    request = returned(env.app.data(), ("entity_name", "ACCEPT", None))
    saved = env.app.save(request)
    assert saved["changed_fields"] == 1
    assert states(saved["data"]) == {"entity_name": "human_confirmed", "jurisdiction": "awaiting_review"}
    assert saved["data"]["documents"][0]["loaded_targets_complete"] is False
    assert saved["data"]["tracker"]["campaign_complete"] is False
    journal = portal._lines(env.app.journal)
    assert len(journal) == 1 and journal[0]["field"] == "entity_name" and journal[0]["value"] == "Example LLC"
    assert journal[0]["reviewer"] == "AB" and journal[0]["server_received_at"]
    assert journal[0]["receipt_sha256"] == saved["receipt_id"]
    restarted = portal.ReviewPortal(env.root, env.out, campaign="test")
    assert states(restarted.data()) == states(saved["data"])
    assert list(env.app.returns.glob("review-*.json"))


def test_receipt_idempotency_and_defaults_do_not_erase_prior_confirmation(env):
    request = returned(env.app.data(), ("entity_name", "ACCEPT", None))
    first = env.app.save(request)
    assert env.app.save(request)["changed_fields"] == 0
    assert len(portal._lines(env.app.journal)) == 1
    defaults = returned(first["data"], ("entity_name", "NOT_REVIEWED", None))
    result = env.app.save(defaults)
    assert result["changed_fields"] == 0
    assert states(result["data"])["entity_name"] == "human_confirmed"


@pytest.mark.parametrize("code", ["REJECT", "NEEDS_SOURCE"])
def test_explicit_rejection_supersedes_confirmation_without_losing_history(env, code):
    first = env.app.save(returned(env.app.data(), ("entity_name", "ACCEPT", None)))
    latest = env.app.save(returned(first["data"], ("entity_name", code, None)))
    assert states(latest["data"])["entity_name"] == "changes_requested"
    assert [e["human_confirmed"] for e in portal._lines(env.app.journal)] == [True, False]


def test_source_or_candidate_change_invalidates_confirmation_and_stale_tabs(env):
    initial = env.app.data()
    request = returned(initial, ("entity_name", "ACCEPT", None))
    env.app.save(request)
    with pytest.raises(portal.Conflict, match="another tab"):
        env.app.save(returned(initial, ("jurisdiction", "ACCEPT", None)))
    write_packet(env.source, [record(review_note="new candidate audit"), record("jurisdiction", "Delaware")])
    refreshed = env.app.data()
    assert states(refreshed)["entity_name"] == "awaiting_review"
    assert next(s for s in refreshed["review_states"] if s["field"] == "entity_name")["stale_confirmation"]
    with pytest.raises(portal.Conflict, match="packet changed"):
        env.app.save(request)


def test_only_validated_source_grounded_confirmations_are_written(env):
    write_packet(env.source, [record("entity_name", "INVENTED NAME")])
    with pytest.raises(ValueError, match="not grounded"):
        env.app.save(returned(env.app.data(), ("entity_name", "ACCEPT", None)))
    assert not env.app.journal.exists()
    assert not list(env.app.returns.glob("review-*.json"))


def test_checklist_correction_is_source_checked_not_just_json_shaped(env):
    text = "Date of Report: 2026-02-25\n"
    r = record("claim_checklist", ["Timely Notice: " + text.strip()], text=text,
               doc_class="insurance_claim", subclass="property")
    write_packet(env.source, [r])
    bad = returned(env.app.data(), ("claim_checklist", "CORRECT", ["Timely Notice: invented"]))
    with pytest.raises(ValueError, match="source checks failed"):
        env.app.save(bad)
    good = returned(env.app.data(), ("claim_checklist", "CORRECT", ["Timely Notice: " + text.strip()]))
    assert states(env.app.save(good)["data"])["claim_checklist"] == "human_confirmed"


def test_proposal_review_retains_receipt_but_never_confirms_gold_or_schema(env):
    proposal = record("v10_subclass", "governance_record", split="unassigned",
                      eligible_for_gold=False, schema_alignment="proposal_only_not_pinned")
    write_packet(env.source, [proposal])
    result = env.app.save(returned(env.app.data(), ("v10_subclass", "ACCEPT", None)))
    assert states(result["data"])["v10_subclass"] == "reviewed_needs_schema"
    assert result["data"]["tracker"]["reviewed_needs_schema_fields"] == 1
    assert result["data"]["tracker"]["human_confirmed_fields"] == 0
    event = portal._lines(env.app.journal)[0]
    assert event["value"] == "governance_record" and event["human_confirmed"] is False
    assert len(list(env.app.returns.glob("review-*.json"))) == 1


def test_documented_null_absence_allowed_but_never_absent_field_refused(env):
    write_packet(env.source, [record("jurisdiction", None, candidate_presence="schema_documented_absence")])
    assert states(env.app.save(returned(env.app.data(), ("jurisdiction", "ACCEPT", None)))["data"])["jurisdiction"] == "human_confirmed"
    write_packet(env.source, [record("record_type", None, candidate_presence="schema_documented_absence")])
    with pytest.raises(ValueError, match="absence-permitting"):
        env.app.save(returned(env.app.data(), ("record_type", "ACCEPT", None)))


def test_checkpoint_disabled_and_failed_verification_keep_local_confirmations(env, monkeypatch):
    result = env.app.save(returned(env.app.data(), ("entity_name", "ACCEPT", None)))
    with pytest.raises(PermissionError):
        env.app.checkpoint(result["data"]["packet_id"])
    env.app.hf_checkpoint = True
    monkeypatch.setattr(checkpoint, "checkpoint", lambda *a, **k: {"verified": False})
    failed = env.app.checkpoint(result["data"]["packet_id"], api=object())
    assert failed["verified"] is False and failed["pending_fields"] == 1
    assert states(env.app.data())["entity_name"] == "human_confirmed"


def test_verified_checkpoint_and_restore_into_clean_root_replay_per_field_state(env, tmp_path, monkeypatch):
    env.app.hf_checkpoint = True
    saved = env.app.save(returned(env.app.data(), ("entity_name", "ACCEPT", None)))
    staged = tmp_path / "mock-private-hub"
    snapshots = {}

    def fake_checkpoint(root, approvals, message, **kwargs):
        assert root == env.root and "explicit human" in message
        snapshots.update(checkpoint.stage(root, approvals, staged))
        return {"repo": checkpoint.STAGING_REPO, "files": len(snapshots), "verified": True,
                "revision": "fake-private-revision"}

    monkeypatch.setattr(checkpoint, "checkpoint", fake_checkpoint)
    result = env.app.checkpoint(saved["data"]["packet_id"], api=object())
    assert result["verified"] and result["revision"] == "fake-private-revision"
    assert states(env.app.data())["entity_name"] == "checkpointed"
    restarted = portal.ReviewPortal(env.root, env.out, campaign="test", hf_checkpoint=True)
    assert states(restarted.data())["entity_name"] == "checkpointed"
    # The verified receipt is appended AFTER upload, so a clean Hub-only restore
    # honestly recovers human_confirmed until that receipt is itself checkpointed.
    clean = tmp_path / "restored-labels"
    fake_api = SimpleNamespace(
        repo_info=lambda **kw: SimpleNamespace(private=True, sha="a" * 40),
        list_repo_tree=lambda **kw: [SimpleNamespace(path=p, blob_id=checkpoint.git_blob_sha(f.read_bytes()))
                                    for p, f in snapshots.items()])
    def download(api, repo, path, dest, *, revision):
        assert revision == "a" * 40
        return snapshots[path]
    monkeypatch.setattr(checkpoint.hf_interface, "download_file", download)
    checkpoint.restore(clean, api=fake_api)
    restored = portal.ReviewPortal(clean, clean / "review_bundle" / "portal", campaign="test")
    assert states(restored.data()) == {"entity_name": "human_confirmed", "jurisdiction": "awaiting_review"}
    assert len(list(restored.returns.glob("review-*.json"))) == 1
    assert portal._lines(restored.journal)[0]["value"] == "Example LLC"


def test_source_change_during_checkpoint_never_marks_stale_fields_checkpointed(env, monkeypatch):
    env.app.hf_checkpoint = True
    saved = env.app.save(returned(env.app.data(), ("entity_name", "ACCEPT", None)))

    def changed(*args, **kwargs):
        write_packet(env.source, [record(review_note="new source audit"), record("jurisdiction", "Delaware")])
        return {"verified": True, "repo": checkpoint.STAGING_REPO, "files": 1, "revision": "mock-revision"}

    monkeypatch.setattr(checkpoint, "checkpoint", changed)
    assert env.app.checkpoint(saved["data"]["packet_id"], api=object())["verified"] is False
    assert states(env.app.data())["entity_name"] == "awaiting_review"


def test_tracker_uses_real_inventory_gates_and_approvals_not_loaded_sample(env, tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    inventory = [{"document_id": f"C{i}", "filename": f"{i}.txt", "expected": "corporate_record",
                  "expected_subclass": "articles_of_incorporation", "doc_text": TEXT, "gt_fields": {}}
                 for i in range(10)]
    (stage / "ground_truth_hardened.jsonl").write_text("\n".join(json.dumps(r) for r in inventory))
    env.app.stage = stage
    write_packet(env.source, [record(doc="C0")])
    gate = env.root / "9a_corporate-pilot" / "gate.json"
    gate.parent.mkdir()
    gate.write_text(json.dumps({"passed": True}))
    tracker = env.app.data()["tracker"]
    first, second = tracker["tasks"]
    assert first["status"] == "gate_passed_unapproved" and second["status"] == "blocked"
    assert first["documents_total"] == 3 and second["documents_total"] == 7
    assert first["reviewed"] == 0 and tracker["loaded_target_fields"] == 1
    assert not tracker["campaign_complete"]
    (env.campaigns / "approved.yaml").write_text(json.dumps({"approved": [
        {"chunk": "9a", "task": "corporate", "arm": None, "approved_by": "TEST"}]}))
    next_tasks = env.app.data()["tracker"]["tasks"]
    assert next_tasks[0]["status"] == "approved" and next_tasks[0]["approved_by"] == "TEST"
    assert next_tasks[1]["status"] == "pending"


@contextmanager
def http_server(app):
    server = portal.make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(server, method, path, body=None, **headers):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    headers.setdefault("Origin", f"http://127.0.0.1:{server.server_port}")
    headers.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
    response = connection.getresponse()
    data = response.read()
    result = response.status, data, dict(response.getheaders())
    connection.close()
    return result


def test_http_routes_same_origin_security_conflicts_and_limits(env):
    with http_server(env.app) as server:
        assert server.server_address[0] == "127.0.0.1"
        status, body, headers = request(server, "GET", "/review-data.json")
        assert status == 200 and headers["Cache-Control"] == "no-store"
        data = json.loads(body)
        payload = returned(data, ("entity_name", "ACCEPT", None))
        assert request(server, "POST", "/api/reviews", payload, Origin="https://evil.invalid")[0] == 403
        assert request(server, "POST", "/api/reviews", payload, Host="evil.invalid")[0] == 403
        assert request(server, "POST", "/api/reviews", payload, **{"Content-Type": "text/plain"})[0] == 415
        assert request(server, "POST", "/api/unknown", payload)[0] == 404
        assert request(server, "GET", "/../confirmed-fields.jsonl")[0] == 404
        assert request(server, "POST", "/api/reviews", payload, **{"Content-Length": str(portal.MAX_BODY + 1)})[0] == 413
        assert request(server, "POST", "/api/reviews", payload)[0] == 200
        stale = returned(data, ("jurisdiction", "ACCEPT", None))
        assert request(server, "POST", "/api/reviews", stale)[0] == 409
        assert request(server, "POST", "/api/checkpoint", {"packet_id": data["packet_id"], "confirm": False})[0] == 400
        assert request(server, "POST", "/api/checkpoint", {"packet_id": data["packet_id"], "confirm": True})[0] == 403


def test_symlink_output_state_rejected_and_output_must_stay_private_root(env, tmp_path):
    with pytest.raises(ValueError, match="subfolder"):
        portal.ReviewPortal(env.root, tmp_path / "elsewhere", campaign="test")
    outside = tmp_path / "outside.jsonl"
    outside.write_text("")
    env.app.journal.symlink_to(outside)
    with pytest.raises(ValueError, match="symlinks"):
        env.app.data()
