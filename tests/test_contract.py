"""§63/§64 dataset + Mailroom contract tests for mailroom-corpus.

§63 (dataset contract): every row has a unique deterministic document_id;
document_type valid; subtype belongs to type; expected_fields schema-valid;
split valid and rule-consistent; provenance present; content hashes valid.

§64 (Mailroom contract): the corpus's per-class extraction GT keys map onto
the specialist ``field_types`` in llm-mailroom's config/taxonomy.yaml, and
enrichment/purpose-GT keys stay inside the documented enrichment set — the
dataset validates against the actual Mailroom interfaces.

Runs against the committed synthetic fixture always, and against the full
local HF snapshot when data/parquet exists (skipped otherwise — data never
commits).
"""
from __future__ import annotations

from conftest import (
    ENRICHMENT_KEYS,
    EXTRACTION_GT_BY_CLASS,
    FIVE_CLASSES,
    PURPOSE_GT_CLASSES,
    PURPOSE_GT_KEYS,
    taxonomy_field_types,
)

from mailroom_eda.dataset_export import assign_split
from mailroom_eda.docclass_uploader import GT_SCALAR_KEYS
from mailroom_eda.identity import enrich_rows

GT_KEY_SET = set(GT_SCALAR_KEYS)


def _check_row_contract(rows: list[dict]) -> None:
    """The §63 row-level contract, shared by fixture and snapshot runs."""
    enriched = enrich_rows(rows)
    ids = [r["document_id"] for r in enriched]
    assert len(ids) == len(set(ids)), "document_id must be unique (§9, §63)"
    # content hashes must be real, not a hollow constant (the ground_truth
    # config carries no doc_text — it must be joined in from default first)
    assert len({r["content_sha256"] for r in enriched}) > 1, (
        "content_sha256 constant across rows — doc_text missing?"
    )

    subclass_to_type: dict[str, str] = {}
    for row in enriched:
        # document_type valid (§66: the canonical five; anything else is
        # retired or unknown, never "extended taxonomy")
        assert row["expected"] in FIVE_CLASSES, f"invalid class: {row['expected']}"
        # subtype present and belongs to exactly one document_type (§7, §63).
        # Exception: "other" is the documented catch-all fallback (never null)
        # and may appear under more than one type.
        subclass = str(row.get("expected_subclass") or "")
        assert subclass, f"{row['filename']}: empty expected_subclass"
        if subclass != "other":
            owner = subclass_to_type.setdefault(subclass, row["expected"])
            assert owner == row["expected"], (
                f"subclass {subclass!r} spans two types: {owner} + {row['expected']}"
            )
        # split valid + rule-consistent (family split: md5(filename) % 10)
        assert row["split"] in ("train", "test")
        assert row["split"] == assign_split(row["filename"])
        # provenance present (§10) + content hashes valid (§11)
        assert row["document_id"].startswith("DOC-")
        assert row["source_corpus"]
        assert row["source_filename"] == row["filename"]
        assert len(row["content_sha256"]) == 64
        assert len(row["normalized_text_sha256"]) == 64
        # expected_fields schema-valid: GT keys stay inside the 27-key schema
        gt = row.get("gt_fields") or {}
        unknown = set(gt) - GT_KEY_SET
        assert not unknown, f"{row['filename']}: GT keys outside the 27-key schema: {unknown}"


def _check_mailroom_contract(rows: list[dict], field_types: dict[str, set[str]]) -> None:
    """The §64 interface contract against llm-mailroom's taxonomy.yaml."""
    for row in rows:
        cls = row["expected"]
        assert cls in field_types, f"{cls}: no doc_classes entry in taxonomy.yaml"
        gt = row.get("gt_fields") or {}
        present = {k for k, v in gt.items() if v not in (None, "", [])}
        for key in present:
            if key in ENRICHMENT_KEYS:
                # purpose GT only rides the three purpose classes (§20/§21)
                if key in PURPOSE_GT_KEYS:
                    assert cls in PURPOSE_GT_CLASSES, (
                        f"{row['filename']}: purpose-GT key {key} on {cls}"
                    )
                    assert key in field_types[cls], (
                        f"{row['filename']}: {key} not in {cls} field_types"
                    )
                continue
            mapping = EXTRACTION_GT_BY_CLASS[cls]
            assert key in mapping, (
                f"{row['filename']}: extraction GT key {key!r} not registered "
                f"for {cls} (not in ENRICHMENT_KEYS either)"
            )
            target = mapping[key]
            assert target in field_types[cls], (
                f"{row['filename']}: {key} -> {target} missing from "
                f"taxonomy.yaml field_types for {cls}"
            )


def test_row_contract_fixture(fixture_rows):
    _check_row_contract(fixture_rows)


def test_mailroom_contract_fixture(fixture_rows):
    _check_mailroom_contract(fixture_rows, taxonomy_field_types())


def test_row_contract_snapshot(snapshot_rows):
    """Full-corpus §63 run against the pinned local snapshot (v8, 2,000 rows)."""
    assert len(snapshot_rows) == 2000
    _check_row_contract(snapshot_rows)


def test_mailroom_contract_snapshot(snapshot_rows):
    _check_mailroom_contract(snapshot_rows, taxonomy_field_types())


def test_snapshot_gt_schema_is_27_key(snapshot_rows):
    """The published ground_truth config stays inside the 27-key GT schema
    (plus the four identity columns filename/expected/expected_subclass/split)."""
    # doc_text is joined in from the default config by the test harness;
    # it is not a ground_truth config column.
    cols = set(snapshot_rows[0].keys()) - {"doc_text"}
    assert cols == GT_KEY_SET | {"filename", "expected", "expected_subclass", "split"}
