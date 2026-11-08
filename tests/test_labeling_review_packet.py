"""Synthetic, offline review packet coverage; no private source fixtures or calls."""
from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from dataclasses import asdict
from pathlib import Path

import pytest
from openpyxl import load_workbook

from mailroom_eda.labeling import export, review_packet as packet, scoring, tasks, verify

NOTICE = "Date of Report: 2026-02-25"
DEDUCTIBLE = "Part B deductible:\t $500.00"


def fixture(root, *, text=None, doc_id="DOC-TEST", split="train", state="accept", items=None):
    text = text if text is not None else '=SUM(1,2)\r\n' + NOTICE + '\r\n  ' + DEDUCTIBLE + '  \r\n'
    row = {"document_id": doc_id, "filename": "fixture.txt", "expected": "insurance_claim",
           "expected_subclass": "property", "doc_text": text, "split": split, "gt_fields": {}}
    raw_items = ["Timely Notice: " + NOTICE]
    value = items if items is not None else raw_items + ["Deductible: Part B deductible: $500.00"]
    req = tasks.build("claim_checklist", row)
    raw = {"value": raw_items, "status": "present", "evidence": NOTICE}
    call = {"document_id": doc_id, "filename": row["filename"], "task": "claim_checklist",
            "model": packet.MODEL, "provider": "openrouter", "served_provider": "synthetic",
            "reasoning": "low", "prompt_sha256": req.prompt_sha256,
            "input_sha256": tasks.input_sha256(req.messages),
            "doc_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "response": {"fields": {"claim_checklist": raw}}, "labeled_utc": "2026-11-07T00:00:00Z"}
    export.append_journal(root / "calls.jsonl", call)
    field = {"document_id": doc_id, "doc_class": "insurance_claim", "subclass": "property",
             "field": "claim_checklist", "type": scoring.dojo_type("insurance_claim", "claim_checklist"),
             "model_a": packet.MODEL, "model_a_value": raw_items, "model_a_status": "present",
             "model_a_evidence": NOTICE, "final_decision": state, "final_value": value,
             "reviewer_verdict": "value" if state == "accept" else "reject",
             "reviewer_value": value, "reviewer_note": "AI source review, not human approval",
             "reason": "Human test decision required" if split == "test" else "AI reconciliation"}
    return row, field, call


def write_fields(root, fields):
    export.write_csv(root / "fields.csv", export.FIELD_COLUMNS, fields)


def sidecar(root, pairs):
    candidates = []
    for row, field in pairs:
        items = field["final_value"]
        score = scoring.score_checklist(items, verify.DocIndex.build(row["doc_text"]))
        candidates.append({"document_id": row["document_id"], "split": row["split"],
                           "subclass": row["expected_subclass"],
                           "doc_sha256": hashlib.sha256(row["doc_text"].encode()).hexdigest(),
                           "items": items, "evidence": min(score.evidence, key=lambda e: e.start).quote[:300],
                           "located": [{k: v for k, v in asdict(e).items() if k != "item"} for e in score.evidence],
                           "notes": ["AI source review"], "reviewer_type": "AI", "human_approved": False,
                           "hold": field["final_decision"] != "accept", "hold_reason": field["reason"]})
    (root / "review-candidates.json").write_text(json.dumps(candidates), encoding="utf-8")


def decode(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        for key in json.loads(row["escaped_columns"]):
            assert row[key].startswith("'")
            row[key] = row[key][1:]
    return rows


def test_five_files_real_multiline_roundtrip_provenance_and_determinism(tmp_path):
    chunk, dest = tmp_path / "8a_claim_checklist-pilot", tmp_path / "out"
    row, field, call = fixture(chunk)
    row["gt_fields"] = {"policy_number": "LEGACY-ONLY", "gt_presence": {"policy_number": "populated"}}
    write_fields(chunk, [field])
    result = packet.export_packet([row], chunk, dest)
    assert result["accepted"] == result["source_checks_passed"] == 1
    assert {p.name for p in dest.iterdir()} == {
        "8a-review.csv", "8a-review-google-sheets.csv", "8a-review.md", "8a-review.xlsx", "8a-review-data.json"}
    for path in dest.glob("*.csv"):
        r = decode(path)[0]
        assert r["full_document_text"] == row["doc_text"]
        assert r["document_sha256"] == hashlib.sha256(r["full_document_text"].encode()).hexdigest()
        assert r["input_sha256"] == call["input_sha256"]
        assert json.loads(r["raw_glm_labels_json"]) != json.loads(r["reviewed_candidate_labels_json"])
        assert r["candidate_attribution"] == "AI source-review correction; not raw GLM"
        assert r["human_decision"] == "NOT_REVIEWED"
        assert all(not r[k] for k in ("corrected_labels_json", "reviewer_initials", "reviewer_notes"))
        assert json.loads(r["decision_key_json"]) == packet.DECISION_KEY
        assert r["decision_contract_version"] == packet.DECISION_VERSION
        assert r["human_approval"] == "pending" and r["candidate_status"] == "source_checks_passed"
        assert json.loads(r["existing_gt_fields_json"]) == row["gt_fields"]
        assert json.loads(r["dojo_field_types_json"])["claim_checklist"] == field["type"]
        assert "LEGACY-ONLY" not in r["raw_glm_field_json"]
        for e in json.loads(r["source_evidence_json"]):
            assert row["doc_text"][e["start"]:e["end"]] == e["quote"]
        assert [e["line"] for e in json.loads(r["source_evidence_json"])] == [2, 3]
        assert len(path.read_bytes().split(b"\n")) > 3  # physical, quoted source newlines
    sheets = dest / "8a-review-google-sheets.csv"
    assert sheets.read_bytes().startswith(b"\xef\xbb\xbf")
    assert decode(sheets)[0]["reviewed_candidate_labels"] == "\n".join(field["final_value"])
    md = (dest / "8a-review.md").read_bytes().decode()
    assert row["doc_text"] in md
    assert "disable conversion to numbers, dates and formulas" in md
    assert "[Document 1](#doc-1)" in md and "<details>" in md
    assert "legacy context, NOT GLM output" in md and "LEGACY-ONLY" in md
    assert all(code in md and meaning in md for code, meaning in packet.DECISION_KEY.items())
    data = json.loads((dest / "8a-review-data.json").read_text())
    assert data["schema"] == "mailroom-review-packet/v1"
    assert data["decision_key"] == packet.DECISION_KEY
    assert data["records"][0]["candidate_labels"] == field["final_value"]
    assert data["records"][0]["raw_glm_labels"] == field["model_a_value"]
    assert data["records"][0]["existing_gt_fields"] == row["gt_fields"]
    assert data["records"][0]["full_document_text"] == row["doc_text"]
    canonical = json.dumps(data["records"], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert data["packet_id"] == result["packet_id"] == hashlib.sha256(canonical.encode()).hexdigest()
    before = {p.name: p.read_bytes() for p in dest.iterdir()}
    packet.export_packet([row], chunk, dest)
    assert before == {p.name: p.read_bytes() for p in dest.iterdir()}


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "'", "\t=", " \t@", "\r\n+", "\x00=", "\u200b="])
def test_all_string_cells_formula_protection_is_explicit_and_reversible(prefix):
    text = prefix + "synthetic source\nsecond line\\literal"
    rendered = packet._csv([{"full_document_text": text, "filename": text, "safe": "literal\\n"}])
    row = next(csv.DictReader(rendered.splitlines(keepends=True)))
    assert json.loads(row["escaped_columns"]) == ["full_document_text", "filename"]
    assert row["full_document_text"][1:] == row["filename"][1:] == text
    assert row["safe"] == "literal\\n"


def test_sidecar_includes_held_test_and_blocked_example_without_promoting_them(tmp_path):
    chunk, dest = tmp_path / "8a_pilot", tmp_path / "out"
    a, fa, _ = fixture(chunk)
    b, fb, _ = fixture(chunk, doc_id="TEST", split="test", state="unlabeled")
    text = "CLAIM RECORD EXTRACT (system)\n" + NOTICE
    c, fc, _ = fixture(chunk, doc_id="BLOCKED", split="test", state="unlabeled", text=text,
                       items=["Timely Notice: " + NOTICE])
    write_fields(chunk, [fa, fb, fc])
    sidecar(chunk, [(a, fa), (b, fb), (c, fc)])
    result = packet.export_packet([a, b, c], chunk, dest)
    assert result["documents"] == 3 and result["accepted"] == 1
    assert result["source_checks_passed"] == 2 and result["needs_source_review"] == 1
    assert result["held"] == 2 and result["excluded"] == 0
    records = {r["document_id"]: r for r in decode(dest / "8a-review.csv")}
    assert records["TEST"]["split"] == "test"
    assert records["TEST"]["final_decision"] == "unlabeled"
    assert records["TEST"]["candidate_status"] == "source_checks_passed"
    assert records["TEST"]["held_reason"] == "Human test decision required"
    assert records["BLOCKED"]["candidate_status"] == "needs_source_review"
    assert "corpus_bookkeeping" in records["BLOCKED"]["source_check_issues_json"]
    assert all(r["human_approval"] == "pending" for r in records.values())
    md = (dest / "8a-review.md").read_bytes().decode()
    assert "Blocked / source-policy examples — NOT source-check passed" in md
    assert all(r["doc_text"] in md for r in (a, b, c))


@pytest.mark.parametrize("mutation,match", [
    ("prompt", "current prompt"), ("input", "input_sha256 mismatch"),
    ("source", "doc_text_sha256 mismatch"), ("slot", "latest current GLM"),
    ("bad-final", "failed source checks"), ("corrupt-journal", "Expecting"),
    ("sidecar-sha", "sidecar source hash"), ("sidecar-offset", "sidecar evidence"),
    ("sidecar-items", "differs from AI sidecar"), ("sidecar-approved", "AI hold metadata"),
])
def test_corrupt_or_stale_accepted_data_refused_before_any_write(tmp_path, mutation, match):
    chunk, dest = tmp_path / "8a_pilot", tmp_path / "out"
    row, field, call = fixture(chunk)
    if mutation == "prompt":
        call["prompt_sha256"] = "old"
    elif mutation == "input":
        call["input_sha256"] = "wrong"
    elif mutation == "source":
        call["doc_text_sha256"] = "wrong"
    elif mutation == "slot":
        field["model_a_value"] = ["tampered"]
    elif mutation == "bad-final":
        field["final_value"] = ["Timely Notice: invented text"]
    if mutation.startswith("sidecar-"):
        sidecar(chunk, [(row, field)])
        path = chunk / "review-candidates.json"
        side = json.loads(path.read_text())
        if mutation == "sidecar-sha":
            side[0]["doc_sha256"] = "wrong"
        elif mutation == "sidecar-offset":
            side[0]["located"][0]["start"] += 1
        elif mutation == "sidecar-items":
            side[0]["items"] = ["Timely Notice: " + NOTICE]
        else:
            side[0]["human_approved"] = True
        path.write_text(json.dumps(side))
    (chunk / "calls.jsonl").write_text(json.dumps(call) + "\n" + ("torn" if mutation == "corrupt-journal" else ""))
    write_fields(chunk, [field])
    dest.mkdir()
    previous = {name: b"previous packet" for name in (
        "8a-review.csv", "8a-review-google-sheets.csv", "8a-review.md", "8a-review.xlsx", "8a-review-data.json")}
    for name, content in previous.items():
        (dest / name).write_bytes(content)
    with pytest.raises(ValueError, match=match):
        packet.export_packet([row], chunk, dest)
    assert previous == {p.name: p.read_bytes() for p in dest.iterdir()}


def test_latest_success_current_prompt_model_and_low_only(tmp_path):
    chunk, dest = tmp_path / "8a_pilot", tmp_path / "out"
    row, field, call = fixture(chunk)
    for changes in ({"prompt_sha256": "old"}, {"reasoning": "off"},
                    {"model": "another/model"}, {"error": "failed"}):
        export.append_journal(chunk / "calls.jsonl", {**call, **changes, "input_sha256": "wrong"})
    write_fields(chunk, [field])
    packet.export_packet([row], chunk, dest)
    # A later successful current-prompt response must not hide a stale input hash.
    export.append_journal(chunk / "calls.jsonl", {**call, "input_sha256": "wrong"})
    with pytest.raises(ValueError, match="input_sha256 mismatch"):
        packet.export_packet([row], chunk, dest)


def test_unchanged_raw_candidate_and_legacy_input_hash_are_labeled_honestly(tmp_path):
    chunk, dest = tmp_path / "8a_pilot", tmp_path / "out"
    row, field, call = fixture(chunk, items=["Timely Notice: " + NOTICE])
    row["source_url"] = "https://example.invalid/synthetic-only"
    del call["input_sha256"]
    (chunk / "calls.jsonl").write_text(json.dumps(call) + "\n")
    write_fields(chunk, [field])
    packet.export_packet([row], chunk, dest)
    record = decode(dest / "8a-review.csv")[0]
    assert record["candidate_attribution"] == "unchanged raw GLM candidate"
    assert record["human_approval"] == "pending"
    assert record["input_binding"] == "legacy_journal_hash_unavailable"
    assert record["input_sha256"] == "" and record["expected_input_sha256"]
    assert record["source_url"] == row["source_url"]


def test_google_limit_refuses_without_truncation_or_output(tmp_path):
    chunk, dest = tmp_path / "8a_pilot", tmp_path / "out"
    row, field, _ = fixture(chunk, text=NOTICE + "\n" + DEDUCTIBLE + "\n" + "x" * 50_000)
    write_fields(chunk, [field])
    with pytest.raises(ValueError, match="50000-character cell limit.*not truncated"):
        packet.export_packet([row], chunk, dest)
    assert not dest.exists()
    with pytest.raises(ValueError, match="50000-character cell limit"):
        packet._csv([{"astral": "😀" * 25_001}])


def test_workbook_dropdown_shared_key_text_cells_styles_and_full_source(tmp_path):
    chunk, dest = tmp_path / "8a_pilot", tmp_path / "out"
    row, field, _ = fixture(chunk)
    write_fields(chunk, [field])
    packet.export_packet([row], chunk, dest)
    path = dest / "8a-review.xlsx"
    wb = load_workbook(path)
    assert wb.sheetnames == ["Checklist review", "Decision key"]
    ws = wb["Checklist review"]
    columns = {cell.value: cell.column for cell in ws[1]}
    assert ws.cell(2, columns["full_document_text"]).value == row["doc_text"]
    assert ws.cell(2, columns["full_document_text"]).data_type == "s"
    assert ws.cell(2, columns["human_decision"]).value == "NOT_REVIEWED"
    assert ws.cell(2, columns["reviewer_initials"]).value is None
    assert ws.cell(2, columns["corrected_labels_json"]).value is None
    assert ws.cell(2, columns["human_decision"]).fill.fgColor.rgb == "00FFF2CC"
    assert ws["A1"].font.color.rgb == "00FFFFFF" and ws["A1"].fill.fgColor.rgb == "00245A81"
    assert ws["A1"].font.name == "Arial" and ws["A2"].alignment.wrap_text
    assert ws["A2"].alignment.vertical == "top" and ws.freeze_panes == "D2"
    assert ws.auto_filter.ref == ws.dimensions
    validation = list(ws.data_validations.dataValidation)
    assert len(validation) == 1
    dropdown = validation[0]
    assert dropdown.type == "list" and dropdown.showDropDown is False
    assert dropdown.showErrorMessage is True and dropdown.errorStyle == "stop"
    assert dropdown.formula1 == '"NOT_REVIEWED,ACCEPT,CORRECT,REJECT,NEEDS_SOURCE"'
    assert ws.cell(2, columns["human_decision"]).coordinate in dropdown
    keys = {r[0].value: r[1].value for r in wb["Decision key"].iter_rows(min_row=2)}
    assert all(keys[code] == meaning for code, meaning in packet.DECISION_KEY.items())
    assert all(cell.data_type != "f" for sheet in wb for cells in sheet for cell in cells)
    with zipfile.ZipFile(path) as archive:
        assert all(info.date_time == (2000, 1, 1, 0, 0, 0) for info in archive.infolist())


def test_workbook_32767_limit_fails_before_clobbering_any_packet(tmp_path):
    chunk, dest = tmp_path / "8a_pilot", tmp_path / "out"
    row, field, _ = fixture(chunk, text=NOTICE + "\n" + DEDUCTIBLE + "\n" + "x" * 32_767)
    write_fields(chunk, [field])
    dest.mkdir()
    (dest / "8a-review.csv").write_bytes(b"prior")
    with pytest.raises(ValueError, match="Excel 32767-character cell limit.*not truncated"):
        packet.export_packet([row], chunk, dest)
    assert list(dest.iterdir()) == [dest / "8a-review.csv"]
    assert (dest / "8a-review.csv").read_bytes() == b"prior"


def test_workbook_rejects_unrepresentable_control_characters_instead_of_rewriting_source(tmp_path):
    chunk, dest = tmp_path / "8a_pilot", tmp_path / "out"
    row, field, _ = fixture(chunk, text=NOTICE + "\n" + DEDUCTIBLE + "\n\x00untouched")
    write_fields(chunk, [field])
    with pytest.raises(ValueError, match="illegal XML control characters"):
        packet.export_packet([row], chunk, dest)
    assert not dest.exists()


def test_fallback_excludes_held_and_refuses_empty_accepted_set(tmp_path):
    chunk, dest = tmp_path / "8a_pilot", tmp_path / "out"
    row, field, _ = fixture(chunk)
    held, fh, _ = fixture(chunk, doc_id="HELD", state="unlabeled")
    fh["final_value"] = "corrupt old value not a candidate"
    write_fields(chunk, [field, fh])
    result = packet.export_packet([row, held], chunk, dest)
    assert result["excluded"] == 1 and len(decode(dest / "8a-review.csv")) == 1
    assert "HELD" in (dest / "8a-review.md").read_text()
    write_fields(chunk, [fh])
    with pytest.raises(ValueError, match="empty accepted set"):
        packet.export_packet([row, held], chunk, dest)


def test_markdown_safe_fence_html_heading_and_full_source(tmp_path):
    chunk, dest = tmp_path / "8a_pilot", tmp_path / "out"
    text = NOTICE + "\n" + DEDUCTIBLE + "\n``````\n</details><script>not markup</script>\n`````\n"
    row, field, _ = fixture(chunk, text=text, doc_id="<script>DOC</script>")
    write_fields(chunk, [field])
    packet.export_packet([row], chunk, dest)
    md = (dest / "8a-review.md").read_text()
    assert "<h3>&lt;script&gt;DOC&lt;/script&gt;</h3>" in md
    assert "```````text\n" + text + "\n```````" in md


def test_cli_reads_local_stage_only(tmp_path, capsys):
    chunk, dest, stage = tmp_path / "8a_pilot", tmp_path / "out", tmp_path / "stage"
    row, field, _ = fixture(chunk)
    write_fields(chunk, [field])
    stage.mkdir()
    (stage / "ground_truth_hardened.jsonl").write_text(json.dumps(row) + "\n")
    assert packet.main(["--stage", str(stage), "--chunk-dir", str(chunk), "--out", str(dest)]) == 0
    assert json.loads(capsys.readouterr().out)["human_approval"] == "pending"
