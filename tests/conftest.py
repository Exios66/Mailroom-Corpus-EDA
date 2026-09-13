"""Test bootstrap: put src/ on the path (virtual member, no build) and load
the committed synthetic fixture rows. The full-corpus contract test runs
against the local HF snapshot when data/parquet exists (gitignored — fetch
via ``python run_all.py --phases P0``) and skips otherwise."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_rows.jsonl"
SNAPSHOT_GT = ROOT / "data" / "parquet" / "ground_truth"

# Canonical five-class taxonomy (docs/dataset-cards/; HUB_CLASSES).
FIVE_CLASSES = {
    "contract",
    "merger_agreement",
    "corporate_record",
    "correspondence",
    "insurance_claim",
}

# The v9 ground-truth schema (mailroom-dataset v1, 3,302 rows): the GT
# parquet carries 36 top-level columns (identity / provenance / matter /
# eval-contract, plus prompt/expected/expected_subclass/split/_published)
# with all 29 label keys inside a nested ``gt_fields`` JSON column. The label
# set splits into per-class extraction GT and enrichment/purpose GT — the 27
# GT_SCALAR_KEYS below; the other two gt_fields keys (relationships,
# related_document_ids) are matter columns that also exist top-level.
# ``load_snapshot_rows()`` expands gt_fields to flat keys so consumers keep
# the v8-era flat schema. Extraction keys map onto the specialist
# ``field_types`` in llm-mailroom's config/taxonomy.yaml (§64); the two
# clause-list keys use GT-side names for the specialist's cuad_clauses /
# maud_clauses fields.
EXTRACTION_GT_BY_CLASS: dict[str, dict[str, str]] = {
    "contract": {"cuad_clause_labels": "cuad_clauses"},
    "merger_agreement": {"maud_clause_labels": "maud_clauses"},
    "insurance_claim": {k: k for k in (
        "claim_number", "policy_number", "insurer", "insured_party",
        "claim_type", "date_of_loss", "date_filed", "claimed_amount",
        "adjuster", "damages_description", "coverage_determination",
        "denial_reasons", "supporting_documents",
    )},
    "corporate_record": {},
    "correspondence": {},
}

# Enrichment / purpose-GT keys (not specialist extraction fields).
# intent/subject_matter/keywords double as specialist field_types on the three
# purpose-GT classes (corporate_record, correspondence, insurance_claim).
ENRICHMENT_KEYS = {
    "label_evidence", "content_topic", "topic_evidence",
    "sentiment_score", "sentiment_label", "sentiment_evidence",
    "intent", "subject_matter", "keywords",
    "intent_source", "intent_confidence", "intent_status",
}
PURPOSE_GT_CLASSES = {"corporate_record", "correspondence", "insurance_claim"}
PURPOSE_GT_KEYS = {"intent", "subject_matter", "keywords"}

# The 36 top-level columns of the v9 ground_truth parquet (verified on the
# pinned snapshot; also enforced by tests/test_contract.py). The 27 flat GT
# scalar keys (GT_SCALAR_KEYS) are disjoint from these — after expansion a
# snapshot row carries exactly these + the 27 GT keys + the harness-joined
# ``doc_text``.
GT_TOP_LEVEL_KEYS = {
    "filename", "prompt", "expected", "expected_subclass", "split",
    "gt_fields", "_published",
    # identity / provenance (§9–§11)
    "document_id", "source_corpus", "source_document_id", "source_filename",
    "source_revision", "content_sha256", "normalized_text_sha256",
    # eval-contract (§45/§58/§59)
    "expected_specialist", "expected_stage", "review_expected",
    "review_reason", "retry_expected", "expected_post_retry_state",
    "annotation_source", "annotation_method", "annotation_model",
    "annotation_prompt_version", "annotation_confidence",
    "annotation_reviewer", "annotation_timestamp",
    # matter / grouping (§13–§16)
    "matter_id", "matter_construction", "group_id", "group_role",
    "thread_position", "thread_size", "thread_evidence",
    "relationships", "related_document_ids",
}


def load_fixture_rows() -> list[dict]:
    rows = []
    with FIXTURE_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_snapshot_rows() -> list[dict]:
    """Ground-truth config rows from the local HF snapshot (both splits),
    with doc_text joined in from the default config by filename (the GT
    config carries no text — blind/label split).

    v9 schema: the label/annotation fields live inside the nested
    ``gt_fields`` JSON column (29-key union). We expand them to flat
    top-level GT keys via mailroom_eda.download._expand_gt_fields so
    consumers keep the flat v8-era schema; the two matter columns that also
    appear inside gt_fields (relationships, related_document_ids) keep their
    canonical top-level values. Missing class-specific GT keys become the
    corpus-wide absence convention (''), not NaN.
    """
    import pandas as pd

    from mailroom_eda.docclass_uploader import GT_SCALAR_KEYS
    from mailroom_eda.download import _expand_gt_fields

    frames = []
    for split in ("train", "test"):
        for f in sorted((SNAPSHOT_GT / split).glob("*.parquet")):
            frames.append(pd.read_parquet(f))
    if not frames:
        return []
    df = pd.concat(frames, ignore_index=True)
    if "gt_fields" in df.columns:
        df = _expand_gt_fields(df)
        # keep-first: canonical top-level matter columns beat the gt_fields
        # mirror (relationships differs on 14 rows — derived vs raw label)
        df = df.loc[:, ~df.columns.duplicated(keep="first")]
        # corpus-wide absence is '' — a sparse per-class gt_fields JSON
        # (12–29 keys) must not leave NaN in the flat GT columns
        df[list(GT_SCALAR_KEYS)] = df[list(GT_SCALAR_KEYS)].fillna("")
    blind_dir = SNAPSHOT_GT.parent / "default"
    blind = []
    for split in ("train", "test"):
        for f in sorted((blind_dir / split).glob("*.parquet")):
            blind.append(pd.read_parquet(f, columns=["filename", "doc_text"]))
    text_by_filename = dict(
        zip(pd.concat(blind, ignore_index=True)["filename"],
            pd.concat(blind, ignore_index=True)["doc_text"])
    )
    df["doc_text"] = df["filename"].map(text_by_filename).fillna("")
    return df.to_dict("records")


def snapshot_available() -> bool:
    return SNAPSHOT_GT.exists() and any(SNAPSHOT_GT.rglob("*.parquet"))


def _taxonomy_path() -> Path:
    """llm-mailroom's taxonomy.yaml, wherever the sibling checkout lives:
    the standalone sibling repo, or the Digital-Mailroom monorepo package
    (both carry the live config the §64 interface contract validates)."""
    candidates = (
        ROOT.parent / "llm-mailroom" / "src" / "config" / "taxonomy.yaml",
        ROOT.parent / "Digital-Mailroom" / "packages" / "llm-mailroom"
        / "src" / "config" / "taxonomy.yaml",
    )
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        "llm-mailroom taxonomy.yaml not found under "
        f"{ROOT.parent} (need the sibling checkout or the "
        "Digital-Mailroom monorepo)"
    )


def taxonomy_field_types() -> dict[str, set[str]]:
    """doc_classes key -> field_types keys, from llm-mailroom's taxonomy.yaml
    (read by path — the corpus package does not depend on the pipeline)."""
    import yaml

    cfg = yaml.safe_load(_taxonomy_path().read_text(encoding="utf-8"))
    return {
        str(dc["key"]): set((dc.get("field_types") or {}).keys())
        for dc in cfg.get("doc_classes", [])
        if dc.get("key")
    }


@pytest.fixture(scope="session")
def fixture_rows() -> list[dict]:
    return load_fixture_rows()


@pytest.fixture(scope="session")
def snapshot_rows() -> list[dict]:
    if not snapshot_available():
        pytest.skip("local HF snapshot absent (data/parquet) — fetch via run_all.py P0")
    return load_snapshot_rows()


def snapshot_metadata_map() -> dict[str, dict]:
    """filename -> default-config metadata dict (custodian/date/message_id…),
    for grouping derivations that the GT config cannot carry itself."""
    import pandas as pd

    frames = []
    for split in ("train", "test"):
        for f in sorted((SNAPSHOT_GT.parent / "default" / split).glob("*.parquet")):
            frames.append(pd.read_parquet(f, columns=["filename", "metadata"]))
    df = pd.concat(frames, ignore_index=True)
    return dict(zip(df["filename"], df["metadata"]))


@pytest.fixture(scope="session")
def snapshot_metadata() -> dict[str, dict]:
    if not snapshot_available():
        pytest.skip("local HF snapshot absent (data/parquet) — fetch via run_all.py P0")
    return snapshot_metadata_map()
