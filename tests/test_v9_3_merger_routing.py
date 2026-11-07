"""v9.3 merger routing amendment: one value changes, nothing else does."""
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mailroom_eda import v9_3_merger_routing as m


def _line(**kw):
    row = {"filename": "f.txt", "expected": "merger_agreement",
           "expected_specialist": "contracts_specialist", "doc_text": "a   b"}
    row.update(kw)
    return json.dumps(row, ensure_ascii=False)


def test_merger_line_is_rerouted_in_place():
    old = _line()
    new, changed = m.reroute_jsonl_line(old)
    assert changed
    assert new == old.replace('"contracts_specialist"', '"merger_agreement_specialist"')
    assert json.loads(new)["expected_specialist"] == "merger_agreement_specialist"


@pytest.mark.parametrize("row", [
    {"expected": "contract"},
    {"expected": "merger_agreement", "expected_specialist": "merger_agreement_specialist"},
    {"expected": "correspondence", "expected_specialist": "correspondence_specialist"},
])
def test_other_lines_are_kept_byte_for_byte(row):
    old = _line(**row)
    assert m.reroute_jsonl_line(old) == (old, False)


def test_reroute_jsonl_keeps_trailing_newline(tmp_path):
    src, dst = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    src.write_text(_line() + "\n" + _line(expected="contract") + "\n", encoding="utf-8")
    assert m.reroute_jsonl(src, dst) == 1
    out = dst.read_text(encoding="utf-8")
    assert out.endswith("\n") and out.count("\n") == 2
    assert out.split("\n")[1] == _line(expected="contract")


def test_reroute_parquet_changes_only_merger_specialist(tmp_path):
    schema = pa.schema([("filename", pa.string()), ("expected", pa.string()),
                        ("expected_specialist", pa.string()), ("gt_fields", pa.string())])
    t = pa.Table.from_pylist([
        {"filename": "a", "expected": "merger_agreement", "expected_specialist": "contracts_specialist", "gt_fields": "{}"},
        {"filename": "b", "expected": "contract", "expected_specialist": "contracts_specialist", "gt_fields": "{\"x\": 1}"},
        {"filename": "c", "expected": "insurance_claim", "expected_specialist": "insurance_claims_specialist", "gt_fields": None},
    ], schema=schema)
    src, dst = tmp_path / "a.parquet", tmp_path / "b.parquet"
    pq.write_table(t, src)
    assert m.reroute_parquet(src, dst) == 1
    back = pq.read_table(dst)
    assert back.schema == schema
    assert back.column("expected_specialist").to_pylist() == [
        "merger_agreement_specialist", "contracts_specialist", "insurance_claims_specialist"]
    for col in ("filename", "expected", "gt_fields"):
        assert back.column(col).equals(t.column(col))


def test_registry_routes_every_class_to_its_own_specialist():
    m.check_registry()
    assert m.SPECIALIST_BY_CLASS == {
        "contract": "contracts_specialist",
        "merger_agreement": "merger_agreement_specialist",
        "corporate_record": "corporate_records_specialist",
        "correspondence": "correspondence_specialist",
        "insurance_claim": "insurance_claims_specialist",
    }


def test_verify_routing_rejects_any_misrouted_class(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "EXPECTED_CHANGES", {"x.jsonl": 0})
    (tmp_path / "x.jsonl").write_text(
        _line(expected="correspondence", expected_specialist="contracts_specialist") + "\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="correspondence"):
        m.verify_routing(tmp_path)


def test_manifest_adds_one_amendment_line_and_recomputes_shas(tmp_path):
    (tmp_path / "a.txt").write_text("new", encoding="utf-8")
    old = ("name : x\nlabels_version: v9.3 corporate\nsha256     :\n"
           "  a.txt  " + "0" * 64 + "\n")
    out = m.render_manifest(old, tmp_path)
    assert out.count("amendment  :") == 1
    assert out.index("labels_version") < out.index("amendment  :")
    assert m.render_manifest(out, tmp_path).count("amendment  :") == 1
    (tmp_path / "manifest.txt").write_text(out, encoding="utf-8")
    assert m.verify_manifest(tmp_path) == 1


def test_manifest_refuses_a_changed_file_set(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "extra.txt").write_text("y", encoding="utf-8")
    old = "labels_version: v\nsha256     :\n  a.txt  " + "0" * 64 + "\n"
    with pytest.raises(AssertionError, match="file set"):
        m.render_manifest(old, tmp_path)
