"""v9 builder: standalone ``mailroom-dataset`` v1 — canonical successor of
``mailroom-corpus`` v8 (issue #3; subissues #10/#14/#16).

The v9 build merges the frozen v8 corpus (2,000 rows, byte-stable identity)
with the four expansion draws (corporate_record +411, correspondence +650,
contract +91, insurance_claim +150) into a NEW standalone HF dataset:

    Lucius-Morningstar/mailroom-dataset   (v1 — canonically "v9" of the
                                           mailroom corpus family)

Design laws enforced here:

- **LLM-safe formatting** (§48–49): the ``default`` (blind) config carries
  EXACTLY 4 columns — filename, doc_text, prompt, metadata — never a label
  column. Ground truth lives ONLY in the ``ground_truth`` sidecar (joined on
  filename). The corpus is an evaluation/ingress corpus, not an ML training
  set; the md5 90/10 split is an evaluation partition for reproducibility,
  not training semantics.
- **Zero drift**: the 2,000 v8 rows keep their published ``document_id``,
  ``source_corpus``, GT values, and split. Re-derivation (identity →
  eval_contract → matter, the §84A chain) is asserted equal to the published
  values before anything is staged.
- **Ground-truth conformance**: every row passes the per-class 27-key sweep
  (no None; '' only where schema-documented), the verbatim contract where
  the class defines one, and test-split nullification (zero empty
  class-relevant keys on test rows).
- **Determinism**: RANDOM_STATE = 42 convention, sorted output, byte-identical
  rebuilds from the same sources.
- **Publishing** rides the centralized helpers (hf_interface) — never
  ad-hoc upload code (§44A).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mailroom_eda import identity  # noqa: E402
from mailroom_eda import eval_contract as ec  # noqa: E402
from mailroom_eda.config import DATA_DIR, V8_REPO_ID  # noqa: E402
from mailroom_eda.dataset_export import (  # noqa: E402
    assign_split,
    normalize_metadata_rows,
    safe_jsonl_line,
    stage_parquet,
)
from mailroom_eda.hardened import (  # noqa: E402
    CONTRACT_FIELDS,
    IDENTITY_FIELDS,
    MATTER_LISTS,
    MATTER_SCALARS,
    build_bundle_rows,
    build_fixture_rows,
    build_stream_rows,
    enrich_gt_rows,
    stage_configs,
)

V9_REPO_ID = "Lucius-Morningstar/mailroom-dataset"
V9_VERSION = "v1"  # standalone lineage of mailroom-dataset (canonically v9 of the corpus family)
V9_BUILD_DIR = DATA_DIR / "v9"
DRAW_DIR = V9_BUILD_DIR / "draws"
STAGE_DIR_DEFAULT = V9_BUILD_DIR / "stage"
V8_FILENAMES_PATH = DATA_DIR / "v8_filenames.txt"

#: Per-class expected subclass vocabulary (from the published v8 corpus +
#: the §38 corporate expansion + INSURBIAS categories).
EXPECTED_SUBCLASS_BY_CLASS: dict[str, tuple[str, ...]] = {
    "contract": (
        "Affiliate_Agreements", "Agency Agreements", "Co_Branding", "Collaboration",
        "Consulting Agreements", "Development", "Distributor", "Endorsement",
        "Franchise", "Hosting", "IP", "Joint Venture", "Joint Venture _ Filing",
        "License_Agreements", "Maintenance", "Manufacturing", "Marketing",
        "Non_Compete_Non_Solicit", "Outsourcing", "Promotion", "Reseller", "Service",
        "Sponsorship", "Strategic Alliance", "Supply", "Transportation",
    ),
    "merger_agreement": ("all_cash", "all_stock", "mixed_cash_stock",
                         "mixed_cash_stock_election", "other"),
    "corporate_record": (
        "articles_of_incorporation", "bylaws", "charter_amendment", "board_resolution",
        "officer_certificate", "powers_of_attorney", "subsidiary_list",
        "rights_instrument", "indenture", "other",
    ),
    "correspondence": (
        "email", "notice", "memo", "letter", "press_release",
        "demand", "meeting_request", "attorney_demand",
    ),
    "insurance_claim": ("auto", "carrier", "inpatient", "outpatient", "pde",
                        "property", "residential", "health", "commercial"),
}

#: Class-relevant GT keys (subset of the 27 stage_parquet scalar keys).
#: informational keys (label_evidence/content_topic/sentiment_*) are NOT
#: mandatory — the purpose-GT keys and the clause axes are.
CLASS_GT_KEYS: dict[str, tuple[str, ...]] = {
    "contract": ("label_evidence", "cuad_clause_labels"),
    "merger_agreement": ("label_evidence", "maud_clause_labels"),
    "corporate_record": ("intent", "subject_matter", "keywords",
                         "intent_source", "intent_confidence", "intent_status"),
    "correspondence": ("intent", "subject_matter", "keywords",
                       "intent_source", "intent_confidence", "intent_status"),
    "insurance_claim": (
        "claim_number", "policy_number", "insurer", "insured_party", "claim_type",
        "date_of_loss", "date_filed", "claimed_amount", "adjuster",
        "damages_description", "coverage_determination", "denial_reasons",
        "supporting_documents", "intent", "subject_matter", "keywords",
        "intent_source", "intent_confidence", "intent_status",
    ),
}

#: Clause-axis pairs: TEST rows must carry at least ONE member non-empty.
CLAUSE_AXES: dict[str, tuple[str, str]] = {
    "contract": ("label_evidence", "cuad_clause_labels"),
    "merger_agreement": ("label_evidence", "maud_clause_labels"),
}

ALLOWED_EMPTY: dict[str, set[str]] = {
    "insurance_claim": {"adjuster"},
    "contract": set(),  # clause labels may be absent on EDGAR rows (partial coverage)
    "correspondence": set(),
    "corporate_record": set(),
    "merger_agreement": set(),
}

#: List-typed GT fields: a valid JSON array is the COMPLETE answer — "[]" means
#: "no items" (honest), never a missing value. Dict-typed clause labels use
#: "{}" for "no annotations". Scalar fields must be non-empty.
LIST_GT_FIELDS = frozenset({
    "denial_reasons", "supporting_documents", "relationships",
    "related_document_ids",
})
DICT_GT_FIELDS = frozenset({"cuad_clause_labels", "maud_clause_labels"})

CORRESPONDENCE_INTENTS = frozenset({
    "payment_demand", "notice", "analysis", "request", "update",
    "meeting_invite", "press_communication", "other",
})

#: Corporate-record subclass → closed llm-mailroom intent vocabulary
#: (doc_inventories.INTENT_LABELS.corporate_record, verified 2026-09-12).
CORP_SUBCLASS_INTENT: dict[str, str] = {
    "articles_of_incorporation": "entity_formation",
    "bylaws": "governance_rules",
    "charter_amendment": "entity_formation",
    "board_resolution": "corporate_action_approval",
    "officer_certificate": "governance_rules",
    "powers_of_attorney": "authority_delegation",
    "subsidiary_list": "other",
    "rights_instrument": "investor_rights",
    "indenture": "investor_rights",
    "other": "other",
}


def backfill_purpose_gt(rows: list[dict]) -> dict:
    """v9 purpose-GT completion on legacy rows (v8 documented gaps).

    - corporate_record: intent was 0% on all 39 v8 rows — complete via the
      subclass → closed-vocabulary template (deterministic, zero LLM).
    - correspondence: subject_matter/keywords were 96/350 (27%) — complete
      from the published content_topic/topic_evidence + metadata
      (subject/custodian/date) + doc_text head.

    Only EMPTY fields are written (never overwrites an existing value);
    provenance is ``heuristic`` (the §20/§43 regime, now mapped in
    eval_contract.INTENT_SOURCE_METHOD). Returns completion stats.
    """
    stats = {"corporate_intent": 0, "correspondence_purpose": 0}
    for r in rows:
        cls = r["expected"]
        gt = r.get("gt_fields") or {}
        md = r.get("metadata") or {}
        if cls == "corporate_record":
            if not str(gt.get("intent") or "").strip():
                subclass = str(r.get("expected_subclass") or "other")
                gt["intent"] = CORP_SUBCLASS_INTENT.get(subclass, "other")
                gt["intent_source"] = "heuristic"
                gt["intent_confidence"] = "0.9"
                gt["intent_status"] = "auto_labeled"
                stats["corporate_intent"] += 1
            if not str(gt.get("subject_matter") or "").strip():
                filer = md.get("filer") or "the registrant"
                desc = md.get("exhibit_description") or str(r.get("expected_subclass") or "")
                gt["subject_matter"] = (
                    f"{str(r.get('expected_subclass') or 'corporate record')} of {filer} "
                    f"(SEC EDGAR exhibit{f': {desc}' if desc else ''})."
                )
            if not str(gt.get("keywords") or "").strip():
                kws = [str(r.get("expected_subclass") or "corporate record")]
                for key in ("filer", "exhibit_type", "form"):
                    if md.get(key):
                        kws.append(str(md[key]))
                gt["keywords"] = json.dumps(list(dict.fromkeys(kws))[:8], ensure_ascii=False)
        elif cls == "correspondence":
            if not str(gt.get("subject_matter") or "").strip():
                topic = str(gt.get("content_topic") or "").strip() or "general business"
                subject = str(md.get("subject") or "").strip()
                if subject:
                    gt["subject_matter"] = f"Correspondence concerning {topic}: {subject}"
                else:
                    gt["subject_matter"] = f"Correspondence concerning {topic}."
            if not str(gt.get("keywords") or "").strip():
                kws = [str(gt.get("content_topic") or "correspondence"),
                       str(r.get("expected_subclass") or "email")]
                for key in ("custodian", "folder", "date"):
                    if md.get(key):
                        kws.append(str(md[key]))
                gt["keywords"] = json.dumps(list(dict.fromkeys(kws))[:8], ensure_ascii=False)
            if gt.get("intent"):
                stats["correspondence_purpose"] += 1
    return stats


def _json_obj(v: object) -> object:
    """Parse a GT value that may be a JSON string, a dict, a list, or empty."""
    if isinstance(v, (dict, list)):
        return v
    s = str(v or "").strip()
    if not s or s in ("[]", "{}"):
        return {}
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {}


#: INSURBIAS supporting-document completion (issue #29, epic #27).
#:
#: The v9 insurance_claim draw (``feihuangfh/INSURBIAS``, CC-BY-4.0) ships
#: claim narratives only — no document inventory. ``supporting_documents`` is
#: therefore derived deterministically from each narrative's referenced
#: features, mirroring the v8 BDR auto precedent (``v8_build.py _auto_row``:
#: ``supporting = ["repair estimate"]`` unconditionally, ``+ ["police
#: report"]`` when a police report is on file). Document-type vocabulary is
#: the v8/v9 lowercase doc-type names; the list is sorted + deduped.
INSURBIAS_SOURCE = "feihuangfh/INSURBIAS"

#: Narrative features that ground a ``repair estimate`` (the auto-claim
#: standard supporting document): explicit vehicle damage / repair need.
_INSURBIAS_DAMAGE_RE = re.compile(
    r"\bdamag\w*\b|\bdent(s|ed)?\b|\bscratch(es|ed)?\b|\brepair(s|ed)?\b|\brepair costs?\b"
    r"|\bbroke down\b|\bbreakdown\b|\bflat tire\b|\bstalled\b|\bflood(ed|ing)?\b"
    r"|\bsubmerg(ed)?\b|\bbumper\b|\bwindshield\b|\bengine mount\b|\bhail(stones?)?\b"
    r"|\bcaught fire\b|\btire\b"
)


def _insurbias_injury(narrative: str) -> bool:
    """True when the narrative asserts claimant injury (negation-aware)."""
    low = narrative.lower()
    if re.search(r"\b(no one|nobody) was (seriously )?injured\b", low):
        return False
    if re.search(r"\bwasn'?t injured\b|\bweren'?t injured\b", low):
        return False
    if re.search(r"\bnot (seriously )?injured\b|\bunharmed\b|\bno (serious )?injuries\b", low):
        return False
    return bool(re.search(r"\binjur(y|ed)\b|\bpain\b|\bmedical\b|\bhospital\b|\bdoctor\b|\btreatment\b", low))


def _insurbias_supporting_documents(doc_text: str) -> list[str]:
    """Deterministic supporting-document derivation for INSURBIAS auto rows.

    Grounded in the narrative's referenced features (issue #29): repair
    estimate where the narrative asserts vehicle damage / a repair need;
    police report where the authorities/police are referenced; damage photos
    where the narrative references an image; medical records where an injury
    is asserted (negation-aware); fire report where the fire department was
    called. Returns a sorted, deduped list of lowercase doc-type names.
    """
    narrative = doc_text.split("CLAIM NARRATIVE (source text)")[-1]
    low = narrative.lower()
    docs: list[str] = []
    if _INSURBIAS_DAMAGE_RE.search(low):
        docs.append("repair estimate")
    if re.search(r"\bauthorities\b|\bpolice\b", low):
        docs.append("police report")
    if re.search(r"\bimage\b", low):
        docs.append("damage photos")
    if _insurbias_injury(narrative):
        docs.append("medical records")
    if re.search(r"\btow(ed|ing| truck)?\b", low):
        docs.append("towing invoice")
    if re.search(r"\bwitness(es)?\b", low):
        docs.append("witness statement")
    if re.search(r"\bfire department\b", low):
        docs.append("fire report")
    return sorted(set(docs))


def _insurbias_supporting_doc_absent(doc_text: str) -> bool:
    """Documented-absence predicate (issue #29): True when the INSURBIAS
    narrative references NO supporting-document feature.

    A narrow class of bare accident reports (6/150 on the v9 draw): the
    narrative asserts a collision/accident event but no vehicle damage, no
    repair need, no police/authorities, no image, no injury, no towing, no
    witness, no fire department — there is no feature to ground any
    supporting document on. Such rows keep ``"[]"``, a complete no-items
    answer per LIST_GT_FIELDS ("a valid JSON array is the COMPLETE answer —
    '[]' means no items (honest), never a missing value"). The predicate is
    mirrored by scripts/coverage_matrix.py ABSENCE_RULES so the matrix
    reports these cells as ``schema_documented_absence``, never as gaps.
    """
    return not _insurbias_supporting_documents(doc_text)


def complete_gt_fields(rows: list[dict]) -> dict:
    """v9 GT-completeness pass (issue #3 / #14 follow-up).

    Fills every expected non-empty class-relevant field so no ground-truth
    entry is missing, per class/subclass expected presence:

    - contract / merger_agreement: ``label_evidence`` derived deterministically
      from the CUAD / MAUD clause annotations (clause names with evidence);
      list fields normalized to a valid JSON array ("[]" = no items).
    - insurance_claim: ``denial_reasons`` / ``supporting_documents`` become
      "[]" where the source has no items; the 3 source-N/A outpatient ``:2``
      notices (service dates literally "N/A" in the source) get the verbatim
      marker "N/A" for date_of_loss / date_filed.
    - list fields everywhere are JSON-normalized ("[]" for absent).

    Only empty fields are written (never overwrites an existing value);
    deterministic and zero-LLM. Returns completion stats.
    """
    stats: Counter = Counter()
    for r in rows:
        gt = r.setdefault("gt_fields", {})
        cls = r["expected"]
        if cls in ("contract", "merger_agreement"):
            if not str(gt.get("label_evidence") or "").strip():
                if cls == "contract":
                    cuad = _json_obj(gt.get("cuad_clause_labels"))
                    names = sorted(
                        str(k) for k, v in cuad.items()
                        if isinstance(v, list) and v)
                    if names:
                        gt["label_evidence"] = (
                            "CUAD-annotated clauses: " + ", ".join(names))
                        stats["contract_label_evidence_cuad"] += 1
                    else:
                        gt["label_evidence"] = (
                            "SEC EDGAR EX-10 material agreement exhibit "
                            "(no CUAD clause annotation available).")
                        stats["contract_label_evidence_edgar"] += 1
                else:
                    maud = _json_obj(gt.get("maud_clause_labels"))
                    names = sorted(
                        str(k) for k, v in maud.items()
                        if isinstance(v, dict)
                        and str(v.get("answer") or "").strip())
                    if names:
                        gt["label_evidence"] = (
                            "MAUD-annotated clauses: " + ", ".join(names))
                        stats["merger_label_evidence"] += 1
            if not str(gt.get("cuad_clause_labels") or "").strip():
                gt["cuad_clause_labels"] = "{}"
            if not str(gt.get("maud_clause_labels") or "").strip():
                gt["maud_clause_labels"] = "{}"
        elif cls == "insurance_claim":
            # issue #29 (epic #27): the INSURBIAS draw rows ship claim
            # narratives only — derive supporting_documents from each
            # narrative's referenced features (see _insurbias_supporting_
            # documents above; mirrors the v8 BDR auto rule). Rows whose
            # narrative grounds NO supporting-document feature (bare accident
            # reports — _insurbias_supporting_doc_absent, 6/150 on the v9
            # draw) keep "[]": a documented absence, complete per
            # LIST_GT_FIELDS.
            if str((r.get("metadata") or {}).get("source_dataset") or "") == INSURBIAS_SOURCE:
                if not str(gt.get("supporting_documents") or "").strip():
                    docs = _insurbias_supporting_documents(str(r.get("doc_text") or ""))
                    if docs:
                        gt["supporting_documents"] = json.dumps(docs, ensure_ascii=False)
                        stats["insurance_insurbias_supporting_docs"] += 1
            for k in ("denial_reasons", "supporting_documents"):
                if not str(gt.get(k) or "").strip():
                    gt[k] = "[]"
                    stats[f"insurance_{k}"] += 1
            if (not str(gt.get("date_of_loss") or "").strip()
                    and not str(gt.get("date_filed") or "").strip()):
                # source-N/A: the 3 train-only outpatient `:2` MSNs print
                # "Service start date: N/A" — verbatim marker, not fabricated.
                gt["date_of_loss"] = "N/A"
                gt["date_filed"] = "N/A"
                stats["insurance_date_source_na"] += 1
        for k in LIST_GT_FIELDS:
            if gt.get(k) is None or not str(gt[k]).strip():
                gt[k] = "[]"
        for k in DICT_GT_FIELDS:
            cur = gt.get(k)
            s = "" if cur is None else str(cur).strip()
            if not s:
                gt[k] = "{}"
                continue
            try:
                parsed = json.loads(s)
            except json.JSONDecodeError:
                parsed = None
            if not isinstance(parsed, dict):
                # e.g. EDGAR draws wrote "[]" — dict fields require an object
                gt[k] = "{}"
                stats[f"{k}_normalized"] += 1
    return dict(stats)


def load_v8_rows() -> list[dict]:
    """Reconstruct canonical rows from the published v8 parquet snapshot."""
    import pandas as pd

    frames = [
        pd.read_parquet(f)
        for split in ("train", "test")
        for f in sorted((DATA_DIR / "parquet" / "ground_truth" / split).glob("*.parquet"))
    ]
    blind = [
        pd.read_parquet(f)
        for split in ("train", "test")
        for f in sorted((DATA_DIR / "parquet" / "default" / split).glob("*.parquet"))
    ]
    b = pd.concat(blind, ignore_index=True)
    text_by_fn = dict(zip(b["filename"], b["doc_text"]))
    md_by_fn = dict(zip(b["filename"], b["metadata"]))
    gt_keys = [
        "label_evidence", "content_topic", "topic_evidence", "sentiment_score",
        "sentiment_label", "sentiment_evidence", "claim_number", "policy_number",
        "insurer", "insured_party", "claim_type", "date_of_loss", "date_filed",
        "claimed_amount", "adjuster", "damages_description", "coverage_determination",
        "denial_reasons", "supporting_documents", "cuad_clause_labels",
        "maud_clause_labels", "intent", "subject_matter", "keywords",
        "intent_source", "intent_confidence", "intent_status",
    ]
    rows = []
    for r in pd.concat(frames, ignore_index=True).to_dict("records"):
        fn = str(r["filename"])
        rows.append({
            "filename": fn,
            "doc_text": str(text_by_fn.get(fn, "")),
            "prompt": "",
            "expected": str(r["expected"]),
            "expected_subclass": str(r["expected_subclass"]),
            "split": str(r["split"]),
            "metadata": md_by_fn.get(fn) or {},
            "gt_fields": {k: ("" if r.get(k) is None else str(r.get(k, "")))
                          for k in gt_keys},
        })
    return rows


def load_draw_rows() -> list[dict]:
    """Load + schema-validate the expansion draw sidecars."""
    if not DRAW_DIR.exists():
        return []
    rows: list[dict] = []
    errors: list[str] = []
    sidecar_names = {f"{cls}.jsonl" for cls in EXPECTED_SUBCLASS_BY_CLASS}
    for sidecar in sorted(DRAW_DIR.glob("*.jsonl")):
        if sidecar.name not in sidecar_names:  # ignore scratch (checkpoints etc.)
            continue
        n = 0
        for line in sidecar.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{sidecar.name}: bad JSON line: {exc}")
                continue
            n += 1
            err = _validate_draw_row(r)
            if err:
                errors.append(f"{sidecar.name} row {n}: {err}")
                continue
            rows.append(r)
        print(f"  draw {sidecar.name}: {n} rows")
    if errors:
        raise SystemExit("\n".join(["draw sidecar validation failed:", *errors[:40]]))
    return rows


def _validate_draw_row(r: dict) -> str:
    if not isinstance(r, dict):
        return "not an object"
    for key in ("filename", "doc_text", "expected", "expected_subclass"):
        if not isinstance(r.get(key), str) or not r[key]:
            return f"missing/empty {key!r}"
    expected = r["expected"]
    if expected not in EXPECTED_SUBCLASS_BY_CLASS:
        return f"unknown expected {expected!r}"
    if r["expected_subclass"] not in EXPECTED_SUBCLASS_BY_CLASS[expected]:
        return f"subclass {r['expected_subclass']!r} not in {expected} vocabulary"
    if not isinstance(r.get("metadata"), dict):
        return "metadata must be a dict"
    if not isinstance(r.get("gt_fields"), dict):
        return "gt_fields must be a dict"
    # no label leakage into metadata: metadata keys must never look like labels
    for key in r["metadata"]:
        kl = key.lower()
        if any(tok in kl for tok in ("expected", "gt_", "intent", "label", "clause")):
            if key not in {"label_evidence"}:
                return f"metadata key {key!r} looks like a label — move to gt_fields"
    return ""


def conform_rows(rows: list[dict]) -> dict:
    """Per-class 27-key conformance sweep.

    Post-completion standard: every expected class-relevant key is non-empty.
    List-typed fields are satisfied by a valid JSON array ("[]" = no items is
    a complete answer). The only documented scalar allowance is
    insurance_claim.adjuster (source-absent on CMS / GNOTHEIA / INSURBIAS).
    """
    errors: list[str] = []
    counts = Counter(r["expected"] for r in rows)
    empty_allowed = 0
    for r in rows:
        cls = r["expected"]
        gt = r.get("gt_fields") or {}
        is_test = r.get("split") == "test"
        for key in CLASS_GT_KEYS[cls]:
            v = gt.get(key)
            if v is None:
                errors.append(f"{r['filename']}: {key} is None")
                continue
            s = str(v).strip()
            if key in LIST_GT_FIELDS or key in DICT_GT_FIELDS:
                # a valid JSON array/object (including "[]"/"{}") is complete
                if not s:
                    errors.append(f"{r['filename']}: {key} empty (expected JSON)")
                    continue
                try:
                    parsed = json.loads(s)
                except json.JSONDecodeError:
                    errors.append(f"{r['filename']}: {key} not valid JSON ({s[:40]})")
                    continue
                want = list if key in LIST_GT_FIELDS else dict
                if not isinstance(parsed, want):
                    errors.append(f"{r['filename']}: {key} not a JSON "
                                  f"{'array' if key in LIST_GT_FIELDS else 'object'}")
                continue
            if s == "":
                if key in ALLOWED_EMPTY[cls]:
                    empty_allowed += 1
                    continue
                errors.append(f"{r['filename']}: {key} empty (expected non-empty)")
        # closed vocabularies
        intent = str(gt.get("intent") or "")
        if cls == "correspondence" and intent and intent not in CORRESPONDENCE_INTENTS:
            errors.append(f"{r['filename']}: intent {intent!r} not in 8-class vocabulary")
    return {"errors": errors, "counts": dict(counts), "empty_allowed": empty_allowed}


def verify_v8_drift(v8_rows: list[dict], enriched: list[dict]) -> None:
    """The 2,000 v8 rows must re-derive to their published identity values."""
    by_fn = {r["filename"]: r for r in enriched}
    mismatches: list[str] = []
    for v8 in v8_rows:
        e = by_fn.get(v8["filename"])
        if e is None:
            mismatches.append(f"{v8['filename']}: missing after merge")
            continue
        published = v8.get("_published", {})
        for key in ("document_id", "source_corpus", "source_document_id",
                    "source_filename", "source_revision"):
            if str(e.get(key, "")) != str(published.get(key, "")):
                mismatches.append(
                    f"{v8['filename']}: {key} drifted {published.get(key)!r} -> {e.get(key)!r}")
    if mismatches:
        raise SystemExit("v8 identity drift:\n" + "\n".join(mismatches[:20]))


def _published_v8_values() -> dict[str, dict[str, str]]:
    import pandas as pd

    frames = [
        pd.read_parquet(f)
        for split in ("train", "test")
        for f in sorted((DATA_DIR / "parquet" / "ground_truth" / split).glob("*.parquet"))
    ]
    out: dict[str, dict[str, str]] = {}
    keys = ("document_id", "source_corpus", "source_document_id",
            "source_filename", "source_revision")
    for r in pd.concat(frames, ignore_index=True).to_dict("records"):
        fn = str(r["filename"])
        out[fn] = {k: ("" if r.get(k) is None else str(r.get(k, ""))) for k in keys}
    return out


def render_card_v1(
    rows: list[dict],
    counts: dict[tuple[str, str], int],
    bundle_manifest: dict,
    stream_manifest: dict,
    draw_counts: dict[str, int],
) -> str:
    """v1 dataset card for the standalone mailroom-dataset (lineage pointers)."""
    #: Frozen v8 baseline composition (verified against the published v8 GT).
    V8_COMP = {
        "insurance_claim": 950,
        "contract": 509,
        "correspondence": 350,
        "merger_agreement": 152,
        "corporate_record": 39,
    }
    total = len(rows)
    comp = Counter(r["expected"] for r in rows)
    share = {c: f"{n / total:.1%}" for c, n in comp.items()}
    delta = {c: n - V8_COMP.get(c, 0) for c, n in comp.items()}
    rows_md = "\n".join(
        f"| `{c}` | {n:,} | {share[c]} | {d:+,} |"
        for c, n in sorted(comp.items()) for d in [delta[c]])
    draw_md = "\n".join(
        f"| {c} | +{n:,} |" for c, n in sorted(draw_counts.items()))
    gt_n = counts[("ground_truth", "train")] + counts[("ground_truth", "test")]
    bd_n = counts[("bundles", "train")] + counts[("bundles", "test")]
    st_n = counts[("streams", "train")] + counts[("streams", "test")]
    fx_n = counts[("fixtures", "train")] + counts[("fixtures", "test")]
    return f"""---
license: cc-by-4.0
task_categories:
- text-classification
language:
- en
tags:
- legal
- contracts
- correspondence
- insurance
- corporate-records
- evaluation
pretty_name: "Mailroom Dataset v1 (canonical successor of mailroom-corpus v8)"
size_categories:
- 1K<n<10K
configs:
- config_name: default
  data_files:
  - split: train
    path: parquet/default/train/*
  - split: test
    path: parquet/default/test/*
- config_name: ground_truth
  data_files:
  - split: train
    path: parquet/ground_truth/train/*
  - split: test
    path: parquet/ground_truth/test/*
- config_name: bundles
  data_files:
  - split: train
    path: parquet/bundles/train/*
  - split: test
    path: parquet/bundles/test/*
- config_name: streams
  data_files:
  - split: train
    path: parquet/streams/train/*
  - split: test
    path: parquet/streams/test/*
- config_name: fixtures
  data_files:
  - split: train
    path: parquet/fixtures/train/*
  - split: test
    path: parquet/fixtures/test/*
---

# Mailroom Dataset v1

> **Lineage**: this is the **standalone successor** of
> [`Lucius-Morningstar/mailroom-corpus`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-corpus)
> (frozen v8 baseline, 2,000 rows — never destroyed, plan §4; hardened
> release pinned at revision `eafe1ab4c0d330d8f9c7a5fb254155e75d290828`).
> It is canonically referred to as **v9** of the mailroom corpus family;
> within this repository's own lineage it is versioned **v1**
> (dataset_version, plan §62). The original corpus remains available and
> pinned; evaluation traces that target the successor should record this
> repo + the parent revision above.

## What this is (and is not)

`mailroom-dataset` is an **evaluation/ingress simulation corpus** for
LLM-Mailroom: it simulates documents arriving at a mailroom pipeline so that
classification, routing, extraction, grouping, adjudication, and recovery can
be measured end-to-end. It is **not** a model-training dataset, and its
component corpora are deliberately heterogeneous (plan §1, §78).

**Splits**: the md5(filename) 90/10 split is an **evaluation partition for
reproducible sampling** — NOT ML train/test semantics. The corpus is not
organized around training; the primary unit is the evaluation scenario
(plan §48–49).

**Label separation (LLM-safe formatting)**: the `default` (blind) config
carries exactly 4 columns — `filename`, `doc_text`, `prompt`, `metadata` —
and **never** contains a label column. All ground truth lives in the
`ground_truth` config, joined on `filename`. An LLM processing the blind
config cannot see labels, intent, expected classes, or clause annotations.

> **v8-inherited metadata aggregates**: 509 v8 contract rows carry a
> `clause_count` and 152 v8 merger rows a `maud_label_count` integer inside
> their `metadata` blob (inherited verbatim from `mailroom-corpus` v8 to
> preserve zero identity drift). These are label-*derived aggregate counts*,
> not labels, and are not present in any v9 expansion row. Consumers that
> require strict label-free metadata may treat them as weak indirect signals;
> they cannot be stripped without violating the zero-drift mandate.

> **GT completeness**: every expected ground-truth field is populated for all
> 3,302 rows — `label_evidence` is derived from the CUAD/MAUD clause
> annotations on the 509 contract / 152 merger rows; list-typed fields
> (`denial_reasons`, `supporting_documents`, `relationships`,
> `related_document_ids`) carry a valid JSON array (`[]` = no items); clause
> labels are JSON objects (`{{}}` = no annotations on the 91 SEC EDGAR EX-10
> contracts). The single documented allowance is `adjuster`: the CMS
> DE-SynPUF, GNOTHEIA property, and INSURBIAS subclasses have no adjuster in
> their sources, so those 950 rows leave `adjuster` empty (BDR auto rows
> carry their pseudonyms). Three outpatient `:2` notices with service dates
> literally "N/A" in the source carry the verbatim `N/A` marker. The 150
> INSURBIAS auto rows (issue #29) ship claim narratives only, so their
> `supporting_documents` are derived deterministically from each narrative's
> referenced features — repair estimate on any vehicle-damage / repair
> assertion (the v8 BDR auto precedent), plus police report / damage photos
> / medical records / fire report on explicit feature matches; the 6 rows
> whose narrative references no supporting-document feature carry `[]` as a
> documented absence (see `v9_build._insurbias_supporting_doc_absent`).

## Composition (v1 = v8 + expansions)

| Document class | Rows | Share | Δ vs v8 |
|---|---:|---:|---:|
{rows_md}

Expansion draws (deterministic, sha256-within-stratum):

{draw_md}

## Configs

| Config | Rows | Contents |
|---|---:|---|
| `default` (blind) | {total:,} | filename, doc_text, prompt, metadata — **zero labels by construction** |
| `ground_truth` | {gt_n:,} | labels (27-key schema), identity/hashes, evaluation contract, matter/group |
| `bundles` | {bd_n:,} | §14 synthetic bundle families over real anchors (flagged `synthetic_constructed`) |
| `streams` | {st_n:,} | §27–29 interleaved ingress stream (`{stream_manifest.get('run_id', 'RUN-SIM-001')}`) with distractors |
| `fixtures` | {fx_n:,} | §68–§72A recovery/adversarial fixtures (calibration quartet, arbiter, failure stages) |

## Sources & licensing

| Class | Source | License |
|---|---|---|
| contract | CUAD v1 (509) + SEC EDGAR EX-10 (91) | CC BY 4.0 / US public domain |
| merger_agreement | MAUD v1 (Zenodo 7500064) | CC BY 4.0 |
| corporate_record | SEC EDGAR S-1/8-K exhibits | US public domain |
| correspondence | Enron deduplicated corpus (247,523-row pool) | research-use (`other`); real named-individual PII — conservative handling |
| insurance_claim | CMS DE-SynPUF + GNOTHEIA + BDR + INSURBIAS narratives | Apache-2.0 / MIT / CC BY 4.0 |

Usage:

```python
from datasets import load_dataset
blind = load_dataset("Lucius-Morningstar/mailroom-dataset", "default")
gt = load_dataset("Lucius-Morningstar/mailroom-dataset", "ground_truth")
# join on "filename" to pair documents with their labels
```
"""


def build_manifest_v9(rows: list[dict], counts: dict, draw_counts: dict,
                      sha_table: dict[str, str], stage_dir: Path) -> None:
    comp = dict(Counter(r["expected"] for r in rows))
    split_c = dict(Counter(r["split"] for r in rows))
    (stage_dir / "manifest.txt").write_text(
        "\n".join([
            f"name       : {V9_REPO_ID}",
            f"version    : {V9_VERSION} (canonically v9 of the mailroom corpus family)",
            f"parent     : {V8_REPO_ID} (frozen v8 baseline, 2,000 rows)",
            f"rows       : {len(rows)} (train {split_c.get('train', 0)}, test {split_c.get('test', 0)})",
            f"composition: " + ", ".join(f"{c} {n}" for c, n in sorted(comp.items())),
            f"draws      : " + ", ".join(f"{c} +{n}" for c, n in sorted(draw_counts.items())),
            f"configs    : ground_truth {counts[('ground_truth', 'train')] + counts[('ground_truth', 'test')]}"
            f"; bundles {counts[('bundles', 'train')] + counts[('bundles', 'test')]}"
            f"; streams {counts[('streams', 'train')] + counts[('streams', 'test')]}"
            f"; fixtures {counts[('fixtures', 'train')] + counts[('fixtures', 'test')]}",
            f"split_rule : md5(filename) % 10 == 0 -> test (evaluation partition, NOT ML semantics)",
            f"built_utc  : {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "sha256     :",
            *[f"  {k}  {v}" for k, v in sorted(sha_table.items())],
            "",
        ]),
        encoding="utf-8",
    )


def verify_blind_label_free(stage_dir: Path) -> None:
    import pandas as pd

    for split in ("train", "test"):
        for f in sorted((stage_dir / "parquet" / "default" / split).glob("*.parquet")):
            df = pd.read_parquet(f, columns=None)
            assert set(df.columns) == {"filename", "doc_text", "prompt", "metadata"}, \
                f"blind config leaked columns: {sorted(df.columns)}"
    print("verify_blind_label_free OK — blind config is exactly 4 label-free columns")


def verify_v9_stage(rows: list[dict], stage_dir: Path, counts: dict) -> None:
    import pandas as pd

    gt = pd.concat([
        pd.read_parquet(f)
        for split in ("train", "test")
        for f in sorted((stage_dir / "parquet" / "ground_truth" / split).glob("*.parquet"))
    ], ignore_index=True)
    assert len(gt) == len(rows), f"GT {len(gt)} != rows {len(rows)}"
    assert gt["document_id"].nunique() == len(rows), "document_id not unique"
    assert set(gt["expected"]) <= set(EXPECTED_SUBCLASS_BY_CLASS)
    for col in IDENTITY_FIELDS + CONTRACT_FIELDS + MATTER_SCALARS:
        assert gt[col].isna().sum() == 0, f"{col} has NaN"
    # no label keys in default metadata (label-free by construction)
    verify_blind_label_free(stage_dir)
    print(f"verify_v9_stage OK — {len(gt)} GT rows, {gt['document_id'].nunique()} unique ids")


def build_all(
    stage_dir: Path,
    *,
    publish: bool = False,
    commit_message: str = "",
) -> dict:
    """Full v9 build: merge → conformance → stage → verify → (publish)."""
    v8_rows = load_v8_rows()
    published = _published_v8_values()
    for r in v8_rows:
        r["_published"] = published.get(r["filename"], {})

    draw_rows = load_draw_rows()
    draw_counts = Counter(r["expected"] for r in draw_rows)
    print(f"v8 rows {len(v8_rows)} + draws {len(draw_rows)} "
          f"({dict(draw_counts)}) = {len(v8_rows) + len(draw_rows)}")

    # filename collisions (draws vs v8) are fatal
    v8_fns = {r["filename"] for r in v8_rows}
    dup = [r["filename"] for r in draw_rows if r["filename"] in v8_fns]
    if dup:
        raise SystemExit(f"draw/v8 filename collision: {dup[:5]}")

    rows = v8_rows + draw_rows
    for r in rows:
        r.setdefault("split", assign_split(str(r["filename"])))
        if "gt_fields" not in r:
            r["gt_fields"] = {}
    normalize_metadata_rows(rows)

    # v9 purpose-GT completion on legacy v8 gaps (intent 0% / purpose 27%)
    # — empty fields only, heuristic provenance, deterministic.
    backfill_stats = backfill_purpose_gt(rows)
    print(f"purpose-GT completion: {backfill_stats}")

    # v9 GT-completeness pass — no expected ground-truth entry left empty
    # (label_evidence from clause annotations; "[]" for no-item lists; the 3
    # source-N/A outpatient `:2` dates get the verbatim "N/A" marker).
    complete_stats = complete_gt_fields(rows)
    print(f"GT completeness: {complete_stats}")

    conformance = conform_rows(rows)
    if conformance["errors"]:
        raise SystemExit("conformance failures:\n" + "\n".join(conformance["errors"][:40]))
    print(f"conformance OK — {dict(conformance['counts'])}; "
          f"{conformance['empty_allowed']} documented empty (adjuster)")

    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    # 1) blind + 31-col GT (stage_parquet) — blind staged for the NEW repo
    counts = stage_parquet(rows, stage_dir)
    # 2) §84 hardening chain (identity → eval contract → matter) on ALL rows
    enriched = enrich_gt_rows(rows)
    verify_v8_drift(v8_rows, enriched)
    print("v8 identity drift check OK — 0 drift")
    # 3) hardened GT + bundles/streams/fixtures
    bundle_rows, bundle_manifest = build_bundle_rows(enriched)
    stream_rows, stream_manifest = build_stream_rows(bundle_rows, enriched)
    fixture_rows = build_fixture_rows()
    counts = stage_configs(enriched, bundle_rows, fixture_rows, stream_rows, stage_dir)
    verify_v9_stage(enriched, stage_dir, counts)

    for name, subset in (
        ("ground_truth_hardened.jsonl", enriched),
        ("bundles.jsonl", bundle_rows),
        ("streams.jsonl", stream_rows),
        ("fixtures.jsonl", fixture_rows),
    ):
        with (stage_dir / name).open("w", encoding="utf-8") as fh:
            for row in subset:
                clean = {k: v for k, v in row.items() if not k.startswith("_")}
                fh.write(safe_jsonl_line(clean) + "\n")

    (stage_dir / "README.md").write_text(
        render_card_v1(enriched, counts, bundle_manifest, stream_manifest, draw_counts),
        encoding="utf-8")

    sha_table = {
        str(p.relative_to(stage_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(stage_dir.rglob("*")) if p.is_file()
    }
    build_manifest_v9(enriched, counts, draw_counts, sha_table, stage_dir)

    print(f"staged {len(sha_table)} files under {stage_dir}")
    for k, v in sorted(sha_table.items()):
        print(f"  {k}  {v}")

    if publish:
        from mailroom_eda.hf_interface import create_dataset_repo, get_hf_api, upload_folder

        api = get_hf_api()
        create_dataset_repo(api, V9_REPO_ID)
        upload_folder(api, stage_dir, V9_REPO_ID,
                      commit_message or f"{V9_VERSION} build: mailroom corpus expansion "
                                        f"— {len(enriched)} rows")
        return {"status": "published", "repo": f"https://huggingface.co/datasets/{V9_REPO_ID}"}
    return {"status": "staged", "stage_dir": str(stage_dir), "counts": counts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-dir", type=Path, default=STAGE_DIR_DEFAULT)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--commit-message", default="")
    args = parser.parse_args()
    result = build_all(args.stage_dir, publish=args.publish,
                       commit_message=args.commit_message)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())