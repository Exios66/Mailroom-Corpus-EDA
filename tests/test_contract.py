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
    GT_TOP_LEVEL_KEYS,
    PURPOSE_GT_CLASSES,
    PURPOSE_GT_KEYS,
    taxonomy_field_types,
)

from mailroom_eda.dataset_export import assign_split
from mailroom_eda.docclass_uploader import GT_SCALAR_KEYS
from mailroom_eda.identity import enrich_rows

GT_KEY_SET = set(GT_SCALAR_KEYS)


def _gt_value_present(v) -> bool:
    """v9 complete-GT convention: '' is absent and the JSON-encoded no-item
    markers ``'[]'`` / ``'{}'`` carry no GT signal (same convention
    ``_parse_labels`` uses in integrity.py / visualizations.py). Native
    empty lists/dicts are absent too (v8-era flat schema)."""
    if v is None:
        return False
    if isinstance(v, str):
        v = v.strip()
        if not v or v in ("[]", "{}"):
            return False
    return v not in ("", [], {})


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
        # expected_fields schema-valid: GT keys stay inside the v9 27-key
        # GT scalar schema (fixture rows carry gt_fields as a dict; snapshot
        # rows carry it as the harness-expanded flat keys)
        gt = row.get("gt_fields") or {}
        if isinstance(gt, str):
            gt = {k: row[k] for k in GT_KEY_SET if k in row}
        unknown = set(gt) - GT_KEY_SET
        assert not unknown, (
            f"{row['filename']}: GT keys outside the 27-key schema: {unknown}"
        )


def _check_mailroom_contract(rows: list[dict], field_types: dict[str, set[str]]) -> None:
    """The §64 interface contract against llm-mailroom's taxonomy.yaml."""
    for row in rows:
        cls = row["expected"]
        assert cls in field_types, f"{cls}: no doc_classes entry in taxonomy.yaml"
        gt = row.get("gt_fields") or {}
        if isinstance(gt, str):
            # v9 snapshot rows: gt_fields JSON expanded to flat keys by the
            # harness; only non-empty values are "present"
            gt = {k: row.get(k) for k in GT_KEY_SET}
        present = {k for k, v in gt.items() if _gt_value_present(v)}
        for key in present:
            if key in ENRICHMENT_KEYS:
                # purpose GT rides the three purpose classes (§20/§21), which
                # expose intent/subject_matter/keywords as specialist
                # field_types. The v9 EX-10 contract expansion (issue #3) is
                # the documented exception: 91 EDGAR contracts carry
                # heuristic intent (material_agreement) that the contracts
                # specialist does NOT consume (no intent field_type) — those
                # rows are enrichment-only, validated by
                # test_mailroom_contract_snapshot's count pin below.
                if key in PURPOSE_GT_KEYS:
                    if cls in PURPOSE_GT_CLASSES:
                        assert key in field_types[cls], (
                            f"{row['filename']}: {key} not in {cls} field_types"
                        )
                    else:
                        assert cls == "contract" and (
                            str(row.get("intent_source") or "") == "heuristic"
                        ), (
                            f"{row['filename']}: purpose-GT key {key} on {cls} "
                            "outside the v9 heuristic-contract exception"
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
    """Full-corpus §63 run against the pinned local snapshot (v9, 3,302 rows)."""
    assert len(snapshot_rows) == 3302
    _check_row_contract(snapshot_rows)


def test_mailroom_contract_snapshot(snapshot_rows):
    _check_mailroom_contract(snapshot_rows, taxonomy_field_types())
    # v9 truth (issue #3): exactly the 91 EDGAR EX-10 contracts carry
    # heuristic purpose intent — value material_agreement, provenance
    # heuristic — and no other contract row does.
    heuristic_contract = [
        r for r in snapshot_rows
        if r["expected"] == "contract" and _gt_value_present(r.get("intent"))
    ]
    assert len(heuristic_contract) == 91
    assert {r["intent_source"] for r in heuristic_contract} == {"heuristic"}
    assert {r["intent"] for r in heuristic_contract} == {"material_agreement"}


def test_snapshot_gt_schema_is_v9(snapshot_rows):
    """The published ground_truth config carries the v9 schema: 36 top-level
    columns (identity / provenance / matter / eval-contract, plus
    prompt/expected/expected_subclass/split/_published and the nested
    ``gt_fields`` JSON) with the label keys expanded to flat GT keys by the
    test harness — i.e. the 36-column set + the 27-key GT scalar set.
    The nested gt_fields union across the corpus is the full 29-key label
    set (27 scalar keys + the two matter columns that also exist
    top-level)."""
    import json

    # doc_text is joined in from the default config by the test harness;
    # it is not a ground_truth config column.
    cols = set(snapshot_rows[0].keys()) - {"doc_text"}
    assert len(cols) == 36 + len(GT_KEY_SET)
    assert cols == GT_TOP_LEVEL_KEYS | set(GT_KEY_SET)
    # every row carries the identical flat schema (no sparse rows)
    assert {frozenset(set(r.keys()) - {"doc_text"}) for r in snapshot_rows} == {frozenset(cols)}
    # nested gt_fields: the union across the corpus is the full 29-key set
    union: set[str] = set()
    for row in snapshot_rows:
        gf = row["gt_fields"]
        union |= set(json.loads(gf) if isinstance(gf, str) else gf)
    assert union == set(GT_KEY_SET) | {"relationships", "related_document_ids"}


def test_edgar_cuad_exception_documented(snapshot_rows):
    """#30 (epic #27): the 91 SEC EDGAR EX-10 contract rows carry no CUAD
    clause annotation — a DATED documented exception (2026-09-13) recorded in
    scripts/audit/coverage_matrix.py ABSENCE_RULES (the corpus-eda LLM clause
    pass could not run on the execution date for want of a working provider
    credential; follow-up catalogued in
    docs/reports/audits/cuad_ex10_annotation_review.md). This pin flips when
    the follow-up annotation lands — the 509 CUAD rows must keep the full
    41-type label set either way."""
    edgar = [
        r for r in snapshot_rows
        if r["expected"] == "contract" and r.get("source_corpus") == "sec_edgar"
    ]
    assert len(edgar) == 91
    assert all(not _gt_value_present(r.get("cuad_clause_labels")) for r in edgar)
    # the 509 CUAD-v1 rows keep the full label set (never loosened)
    cuad = [
        r for r in snapshot_rows
        if r["expected"] == "contract" and r.get("source_corpus") != "sec_edgar"
    ]
    assert len(cuad) == 509
    assert all(_gt_value_present(r.get("cuad_clause_labels")) for r in cuad)
