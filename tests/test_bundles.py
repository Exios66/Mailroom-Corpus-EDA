"""§14 synthetic bundle-family generator — P2 tests (HUB-022)."""
from __future__ import annotations

import json

import pytest

import mailroom_eda.bundles as bd
from mailroom_eda import eval_contract as ec
from conftest import FIVE_CLASSES, load_fixture_rows


@pytest.fixture(scope="module")
def anchors():
    return load_fixture_rows()


def test_family_templates_respect_closed_vocabularies():
    for family, template in bd.BUNDLE_FAMILIES.items():
        assert template["anchor_class"] in FIVE_CLASSES
        for spec in template["members"]:
            assert spec["role"] in ec.GROUP_ROLES, (family, spec)
            assert spec["relationship"] in ec.RELATIONSHIP_TYPES, (family, spec)
            assert spec["doc_class"] in FIVE_CLASSES


def test_section14_worked_examples_span_multiple_classes():
    # §14's two worked examples (legal + insurance) must exercise multi-CLASS
    # routing; single-class families (corporate_record, correspondence_thread)
    # are legitimate extensions and not required to.
    for family in ("legal_contract_family", "insurance_claim_family"):
        template = bd.BUNDLE_FAMILIES[family]
        classes = {template["anchor_class"]} | {
            m["doc_class"] for m in template["members"]
        }
        assert len(classes) >= 2, family


def test_determinism_same_seed_identical_rows(anchors):
    rows_a, manifest_a = bd.synthetic_bundles(anchors, seed=42)
    rows_b, manifest_b = bd.synthetic_bundles(anchors, seed=42)
    assert json.dumps(rows_a, sort_keys=True) == json.dumps(rows_b, sort_keys=True)
    assert manifest_a == manifest_b


def test_all_members_flagged_synthetic_constructed(anchors):
    rows, manifest = bd.synthetic_bundles(anchors, seed=42)
    assert rows and manifest["matter_construction"] == "synthetic_constructed"
    for row in rows:
        assert row["matter_construction"] == "synthetic_constructed"
    assert manifest["members_total"] == len(rows)


def test_anchor_keeps_identity_manufactured_members_flagged(anchors):
    rows, _ = bd.synthetic_bundles(anchors, seed=42, anchors_per_family=2)
    anchors_by_fn = {r["filename"]: r for r in anchors}
    seen_anchors = set()
    for row in rows:
        fn = row["filename"]
        if fn in anchors_by_fn:
            seen_anchors.add(fn)
            assert row["group_role"] == "primary"
            assert row["doc_text"] == anchors_by_fn[fn]["doc_text"]
            assert "synthetic" not in row or row["synthetic"] != "true"
        else:
            assert fn.startswith("matter-syn-") and fn.endswith(".txt")
            assert row["synthetic"] == "true"
            assert bd.SYNTHETIC_FLAG_HEADER in row["doc_text"]
    assert seen_anchors, "no real anchor rows retained"


def test_bundle_ids_consistent_within_instance(anchors):
    rows, _ = bd.synthetic_bundles(anchors, seed=42)
    by_matter: dict[str, set] = {}
    for row in rows:
        by_matter.setdefault(row["matter_id"], set()).add(row["group_id"])
    for matter_id, groups in by_matter.items():
        assert len(groups) == 1, (matter_id, groups)


def test_relationships_and_routing(anchors):
    rows, _ = bd.synthetic_bundles(anchors, seed=42)
    for row in rows:
        if row["filename"] in {r["filename"] for r in anchors}:
            continue
        assert len(row["relationships"]) == 1
        assert row["relationships"][0] in ec.RELATIONSHIP_TYPES
        assert row["related_document_ids"] == [row["bundle_anchor_filename"]]
        expected_sp = ec.SPECIALIST_BY_CLASS[row["expected"]]
        assert bd.bundle_specialist(row) == expected_sp


def test_duplicates_axis(anchors):
    rows, manifest = bd.synthetic_bundles(anchors, seed=42, with_duplicates=True)
    dups = [r for r in rows if r.get("duplicate_type")]
    assert dups
    for dup in dups:
        assert dup["duplicate_type"] in ec.DUPLICATE_TYPES
        assert dup["relationships"] == ["duplicate_of"]
        assert "duplicate_of" in ec.RELATIONSHIP_TYPES
    assert manifest["manufactured_total"] > 0


def test_unknown_family_rejected(anchors):
    with pytest.raises(KeyError):
        bd.synthetic_bundles(anchors, families=("not_a_family",))


def test_manifest_counts_add_up(anchors):
    rows, manifest = bd.synthetic_bundles(anchors, seed=42, with_duplicates=True)
    assert manifest["members_total"] == sum(
        f["members"] for f in manifest["families"].values()
    )
    assert manifest["manufactured_total"] == sum(
        f["manufactured"] for f in manifest["families"].values()
    )


def test_missing_anchor_class_skips_honestly():
    rows, manifest = bd.synthetic_bundles(
        [{"filename": "x", "expected": "correspondence"}],
        seed=42, families=("legal_contract_family",),
    )
    assert rows == [] and manifest["families"] == {}


def test_streams_interleave_matters_round_robin(anchors):
    """§28: A1 B1 A2 C1 ... — members of different matters never arrive
    matter-contiguous (A A B B); sequence_position strictly increases."""
    bundle_rows, _ = bd.synthetic_bundles(anchors, seed=42, with_duplicates=True)
    stream, manifest = bd.build_streams(bundle_rows, run_id="RUN-SIM-001")
    members = [r for r in stream if r["stream_role"] == "member"]
    assert len(stream) == len(members) + manifest["distractors"]
    positions = [r["sequence_position"] for r in stream]
    assert positions == sorted(set(positions))
    # round-robin: consecutive members alternate matters (A1 B1 A2 B2 ...)
    # — a matter-contiguous block (A A B B) would break the §28 interleave
    first = [str(r["matter_id"]) for r in members[:6]]
    assert len(set(first)) >= 2
    assert first[0] != first[1], f"matters contiguous in {first}"


def test_stream_distractors_carry_no_matter(anchors):
    """§29: distractor rows belong to NO matter in the stream and keep their
    real identity (no synthetic_constructed flag from the bundle)."""
    bundle_rows, _ = bd.synthetic_bundles(anchors, seed=42, with_duplicates=True)
    stream, manifest = bd.build_streams(bundle_rows, distractor_every=2)
    distractors = [r for r in stream if r["stream_role"] == bd.DISTRACTOR_ROLE]
    assert distractors, "expected distractors on this cadence"
    for d in distractors:
        assert d["matter_id"] == "" and d["group_id"] == ""
        assert d["stream_role"] == "distractor"
    # the stream is reproducible (§27): same seed/args → same sequence
    stream_b, manifest_b = bd.build_streams(bundle_rows, distractor_every=2)
    assert json.dumps(stream, sort_keys=True) == json.dumps(stream_b, sort_keys=True)
    assert manifest == manifest_b


def test_stream_requires_bundle_rows():
    with pytest.raises(ValueError):
        bd.build_streams([])
