"""mailroom_eda.labeling.staged: the sandbox's staged overlay becomes review candidates, never labels.

Network-free; rows come from the v9.4-shaped fixtures of test_v10_build."""
from __future__ import annotations

import csv
import hashlib
import json

import pytest
from test_v10_build import contract_row, insurance_row

from mailroom_eda.labeling import staged
from mailroom_eda.labeling.operate import GOLD_CORRECTION_COLUMNS


def _record(row, cls, **labels):
    return {"doc_id": row["document_id"], "doc_class": cls, "content_sha256": row["content_sha256"],
            "labels": labels}


def _lab(value, presence="populated", **kw):
    return {"value": value, "presence": presence, "source": "cuad_span", "agreement": "unanimous",
            "override": False, "evidence": [{"text": "governed by the laws of Delaware"}], **kw}


def test_pending_cells_become_fill_candidates_and_gold_wins_or_is_a_correction():
    contract, claim = contract_row(), insurance_row()
    records = [
        _record(contract, "contract", governing_law=_lab("Delaware"), parties=_lab(["Acme", "Beta"]),
                subclass=_lab("license"), cuad_clauses=_lab(["Governing Law"])),
        _record(claim, "insurance_claim", claim_number=_lab("123"),
                claimed_amount=_lab("45.0", override=True), date_of_loss=_lab(None, "documented_absence")),
    ]
    fills, corrections, counts = staged.candidates(records, [contract, claim], "9.3.1-rc.3")
    assert sorted((f["field"], f["value"]) for f in fills) == [("governing_law", "Delaware"),
                                                               ("parties", '["Acme", "Beta"]')]
    assert [(c["field"], c["gold_was"], c["gold_now"], c["decided_by"]) for c in corrections] == [
        ("claimed_amount", "30.0", "45.0", "")]
    assert set(corrections[0]) == set(GOLD_CORRECTION_COLUMNS)
    assert counts["agrees_with_v10"] == 2  # claim_number and the documented absence equal the gold
    assert counts["dropped_field"] == 2  # subclass and cuad_clauses are never taken from here


def test_records_for_other_text_or_class_are_dropped():
    contract = contract_row()
    rec = _record(contract, "contract", governing_law=_lab("Delaware"))
    other = dict(rec, content_sha256="0" * 64)
    wrong = dict(rec, doc_class="correspondence")
    _fills, _corr, counts = staged.candidates([other, wrong, dict(rec, doc_id="DOC-x")], [contract], "v")
    assert (counts["hash_mismatch"], counts["class_mismatch"], counts["no_document"]) == (1, 1, 1)


def _cut(tmp_path, records):
    d = tmp_path / "cut"
    (d / "overlay").mkdir(parents=True)
    body = "".join(json.dumps(r) + "\n" for r in records).encode()
    (d / "overlay" / "contract.jsonl").write_bytes(body)
    raw = json.dumps({"kind": staged.KIND, "version": "9.3.1-rc.3",
                      "files": {"overlay/contract.jsonl": {"sha256": hashlib.sha256(body).hexdigest()}}}).encode()
    (d / "MANIFEST.json").write_bytes(raw)
    return d, hashlib.sha256(raw).hexdigest()


def test_load_requires_the_pinned_bytes(tmp_path):
    d, sha = _cut(tmp_path, [_record(contract_row(), "contract", governing_law=_lab("Delaware"))])
    manifest, records = staged.load(d, manifest_sha256=sha)
    assert manifest["version"] == "9.3.1-rc.3" and len(records) == 1
    with pytest.raises(SystemExit, match="not the pinned"):
        staged.load(d, manifest_sha256="0" * 64)
    (d / "overlay" / "contract.jsonl").write_text("{}\n")
    with pytest.raises(SystemExit, match="not the sha256"):
        staged.load(d, manifest_sha256=sha)


def test_write_keeps_corrections_unsigned(tmp_path):
    contract, claim = contract_row(), insurance_row()
    records = [_record(claim, "insurance_claim", claimed_amount=_lab("45.0", override=True))]
    fills, corrections, _ = staged.candidates(records, [contract, claim], "9.3.1-rc.3")
    staged.write(tmp_path, fills, corrections)
    with (tmp_path / staged.CORRECTION_FILE).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["decided_by"] for r in rows] == [""]
    assert (tmp_path / staged.FILL_FILE).read_text().startswith("document_id,")
