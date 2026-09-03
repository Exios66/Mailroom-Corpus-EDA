"""P1 evaluation-contract derivations (plan §86, HUB-022).

Pure, deterministic derivations of the Mailroom evaluation-hardening ground
truth for every ``mailroom-corpus`` row, per the §84A dependency chain
(document_id → annotation_provenance → confidence-band/calibration fixtures;
failure/review/retry expectations → P3 fixtures). Every derivation is a pure
function of the row (+ the pipeline's taxonomy.yaml for specialist routing);
absence is ``''`` (the corpus-wide convention — verified: zero true NULLs).

Fields (§45/§58/§59/§31/§43):

- ``expected_specialist`` (§59) — from the canonical class × specialist
  mapping read out of llm-mailroom's ``taxonomy.yaml`` (the same registry the
  parity gate validates); ``merger_agreement`` stays a distinct class that
  routes to ``contracts_specialist`` (§6/§81).
- ``expected_stage`` (§57–58) — pipeline terminal expectation. The live
  pipeline's terminal stages are ``archived`` / ``review`` / ``failed``
  (``pipeline.bins.TERMINAL_MANIFEST_STAGES``); every canonical GT row is a
  well-formed five-class document, so the expected terminal stage is
  ``archived`` unless a fixture row overrides it (review/retry fixtures set
  ``review_expected=True`` and may expect ``review``).
- ``review_expected`` / ``review_reason`` (§31) — False/``''`` for canonical
  rows; the reason vocabulary is closed (§73).
- ``retry_expected`` / ``expected_post_retry_state`` (§31/§74) — same
  pattern; post-retry states come from the terminal stages.
- ``annotation_provenance`` (§43) — per-row: source, method, model,
  prompt_version, confidence, reviewer, timestamp. Derived from the row's
  EXISTING provenance columns (``intent_source`` → §20 regime; class/subclass
  labels are source_native by construction — the v7 builder derives them
  from the source corpora, not from an annotator).

Deriving these does NOT publish anything: like ``identity.py``, this is the
v0.2-mailroom-hardened groundwork (§84) verified by the §63 contract tests
without pushing a new revision.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# vocabularies (closed sets — a §63 test asserts every derived value is in)
# ---------------------------------------------------------------------------

#: Live Mailroom specialists (llm-mailroom taxonomy.yaml ``specialist:``).
SPECIALISTS = (
    "contracts_specialist",
    "corporate_records_specialist",
    "correspondence_specialist",
    "insurance_claims_specialist",
)

#: Canonical class → specialist (§59). Read from taxonomy.yaml when available;
#: this constant is the verified fallback and the docclass-arm truth.
SPECIALIST_BY_CLASS: dict[str, str] = {
    "contract": "contracts_specialist",
    "merger_agreement": "contracts_specialist",  # distinct class, shared specialist (§6)
    "corporate_record": "corporate_records_specialist",
    "correspondence": "correspondence_specialist",
    "insurance_claim": "insurance_claims_specialist",
}

#: Pipeline terminal stages (llm-mailroom ``pipeline.bins.TERMINAL_MANIFEST_STAGES``).
TERMINAL_STAGES = ("archived", "review", "failed")

#: §73 review-reason vocabulary (closed).
REVIEW_REASONS = (
    "ambiguous",
    "low_confidence",
    "incomplete_extraction",
    "conflicting_information",
    "unreadable_source",
    "guardrail_violation",
    "taxonomy_unknown",
)

#: §74 post-retry outcomes (closed; terminal stages + human handoff).
POST_RETRY_STATES = ("archived", "review", "human_review", "failed")

#: §20 ground-truth provenance regimes.
ANNOTATION_METHODS = (
    "source_native",
    "verified_join",
    "human_annotated",
    "human_adjudicated",
    "llm_assisted",
    "llm_zero_shot",
    "heuristic",
    "synthetic",
)

#: intent_source (v7 builder vocabulary, verified 1,650 rows: aeslc_join 162,
#: manual 96, llm_zero_shot 92 on correspondence; '' elsewhere) → §43 method.
INTENT_SOURCE_METHOD = {
    "aeslc_join": "verified_join",
    "manual": "human_annotated",
    "llm_zero_shot": "llm_zero_shot",
}

#: The LLM that produced llm_zero_shot labels (v7 intent hydration, issue #5).
LLM_ZERO_SHOT_MODEL = "deepseek-chat"

PATH_TAXONOMY = (
    Path(__file__).resolve().parents[2]
    / "llm-mailroom" / "src" / "config" / "taxonomy.yaml"
)


def specialist_registry() -> dict[str, str]:
    """Class → specialist from llm-mailroom's taxonomy.yaml (live registry).

    Falls back to the verified constant when the sibling pipeline package is
    absent (corpus-eda does not depend on the pipeline at runtime).
    """
    try:
        import yaml

        tax_path = (
            Path(__file__).resolve().parents[3]
            / "llm-mailroom" / "src" / "config" / "taxonomy.yaml"
        )
        cfg = yaml.safe_load(tax_path.read_text(encoding="utf-8"))
        registry = {
            str(dc["key"]): str(dc.get("specialist") or "")
            for dc in cfg.get("doc_classes", [])
            if dc.get("key") and dc.get("status") != "retired"
        }
        if all(registry.get(k) for k in SPECIALIST_BY_CLASS):
            return {k: registry[k] for k in SPECIALIST_BY_CLASS}
    except Exception:
        pass
    return dict(SPECIALIST_BY_CLASS)


def expected_specialist(row: dict[str, Any]) -> str:
    """§59: the specialist this class routes to (registry-derived)."""
    registry = specialist_registry()
    doc_class = str(row.get("expected") or "")
    if doc_class not in registry:
        return ""
    return registry[doc_class]


def expected_stage(row: dict[str, Any], *, review_route: bool = False) -> str:
    """§57–58: the pipeline terminal expectation.

    Canonical rows archive; a review-expected fixture's first terminal state
    is the review bin (the arbiter/retry path may then carry it onward).
    """
    return "review" if review_route else "archived"


def review_expected(row: dict[str, Any]) -> tuple[bool, str]:
    """§31/§73: (review_expected, review_reason).

    Canonical five-class rows are well-formed by construction (the §63
    contract tests validate class/subclass/fields), so no canonical row
    expects review; the vocabulary exists for the fixture families (§68
    OOD/unknown → ``taxonomy_unknown``; §70 calibration → ``low_confidence``;
    §31 incomplete/conflicting sources) that land with the v0.2 publication.
    """
    ood = str(row.get("fixture_kind") or "")
    if ood == "ood_unknown":
        return True, "taxonomy_unknown"
    if ood == "low_confidence":
        return True, "low_confidence"
    if ood == "incomplete":
        return True, "incomplete_extraction"
    if ood == "conflicting":
        return True, "conflicting_information"
    return False, ""


def retry_expected(row: dict[str, Any]) -> tuple[bool, str]:
    """§31/§74: (retry_expected, expected_post_retry_state)."""
    ood = str(row.get("fixture_kind") or "")
    if ood == "retry":
        return True, "archived"
    if ood == "retry_review":
        return True, "human_review"
    return False, ""


def annotation_provenance(row: dict[str, Any]) -> dict[str, str]:
    """§43: source/method/model/prompt_version/confidence/reviewer/timestamp.

    Derived from the row's existing provenance columns (verified vocabulary:
    intent_source ∈ {aeslc_join, manual, llm_zero_shot, ''}; absence is ''
    corpus-wide):
    - correspondence intent labels → method from INTENT_SOURCE_METHOD,
      model deepseek-chat for llm_zero_shot rows, confidence = intent_confidence;
    - class/subclass labels → method=source_native (CUAD folder / MAUD
      consideration / EDGAR exhibit / Enron native / CMS DE-SynPUF synthetic
      tables — derived from the source corpora by the v7 builder, §20);
    - model/prompt_version/reviewer/timestamp stay '' where the v7 builder
      did not track them (cast-safe, same rule as identity.source_revision).
    """
    doc_class = str(row.get("expected") or "")
    source = SOURCE_BY_CLASS.get(doc_class, "")
    md = row.get("metadata") or {}
    if doc_class == "insurance_claim":
        source_dataset = md.get("source_dataset")
        if source_dataset and "cms-de-synpuf" not in str(source_dataset):
            # v8 LOB expansion (HUB-028): GNOTHEIA / BDR rows annotate from
            # their own dataset, not the CMS fused source.
            source = str(source_dataset)
    method, model, prompt_version = "source_native", "", ""
    confidence = ""
    reviewer, timestamp = "", ""
    intent_source = str(row.get("intent_source") or "")
    if intent_source in INTENT_SOURCE_METHOD:
        method = INTENT_SOURCE_METHOD[intent_source]
        if method == "llm_zero_shot":
            model = LLM_ZERO_SHOT_MODEL
        confidence = str(row.get("intent_confidence") or "")
        reviewer = "human" if intent_source == "manual" else ""
    # timestamp: the v7 builder does not track per-row annotation timestamps —
    # cast-safe '' (same rule as identity.source_revision, §43 groundwork).
    if doc_class == "insurance_claim":
        # class/subclass GT for CMS DE-SynPUF + the v8 synthetic expansion
        # (GNOTHEIA / BDR) derives from the synthetic source corpus itself
        # (§4A/§39), so the primary label regime stays ``synthetic``; the
        # intent annotation regime is carried on intent_source separately.
        method = "synthetic"
    return {
        "source": source,
        "method": method,
        "model": model,
        "prompt_version": prompt_version,
        "confidence": confidence,
        "reviewer": reviewer,
        "timestamp": timestamp,
    }


#: §8/§10 source inventory — the five fused sources (mirrors identity.py's
#: SOURCE_CORPUS_BY_CLASS; kept here as the evaluation-contract view).
SOURCE_BY_CLASS = {
    "contract": "theatticusproject/cuad",
    "merger_agreement": "maud",
    "corporate_record": "sec_edgar",
    "correspondence": "Lucius-Morningstar/enron-correspondence-dedup",
    "insurance_claim": "cms_desynpuf",
}


#: §68 fixture kinds — deliberately-added documents that should NOT map to a
#: live class (the sorter must know when not to guess, §67). Retired-class
#: documents (court_opinion / compliance_filing / due_diligence) are §60
#: historical families: as fixtures they are expected to produce
#: ``unknown``/review, never a live-class label.
FIXTURE_KINDS = (
    "ood_unknown",        # §68: out-of-taxonomy document → unknown + review
    "ood_retired_class",  # a former class's document family (§60) → unknown
    "ambiguous",          # §30: two classes plausibly fit → review
    "low_confidence",     # §69/§70: correct-but-low-confidence calibration row
    "high_confidence",    # §70: correct-and-high-confidence calibration row
    "incomplete",         # §31: missing_information → review
    "conflicting",        # §30: conflicting_information → review
    "retry",              # §31/§74: low-after-retry → archive on attempt N
    "retry_review",       # §31: still-below-threshold after retry → human review
)

#: §70 calibration quartet — the four confidence × correctness combinations
#: every calibration fixture family must cover (correct+high, correct+low,
#: wrong+high, wrong+low) so Mailroom can test whether its routing policy is
#: sensible rather than merely accurate.
CALIBRATION_QUARTET = (
    "correct_high", "correct_low", "wrong_high", "wrong_low",
)

#: Calibration cell → §68 fixture kind (closed mapping; fixtures.py builds
#: rows with the cell identity in ``calibration_cell`` and the kind here so
#: review/retry derivations stay single-sourced in eval_contract):
#: correct_high archives; correct_low reviews (low_confidence); wrong_high is
#: the silent-archive failure mode under test (judge catch → conflicting);
#: wrong_low retries then escalates (retry_review → human_review).
CONFIDENCE_CELL_FIXTURE_KIND = {
    "correct_high": "high_confidence",
    "correct_low": "low_confidence",
    "wrong_high": "conflicting",
    "wrong_low": "retry_review",
}


def confidence_bands() -> dict[str, dict[str, float]]:
    """§69: the routing bands read from the LIVE taxonomy.yaml (the plan's
    illustrative 0.95/0.70 numbers are not the config truth). Returns per-class
    ``high`` (≥ continue) and ``low`` (< retry) plus the global fallback and
    the judge band, so fixtures target the bands the pipeline actually routes
    on. Falls back to the committed defaults when the sibling package is
    absent."""
    bands = {
        "global": {"high": 0.97, "low": 0.88, "judge_band_high": 0.95},
        "by_class": {k: {"high": v["high"], "low": v["low"], "judge_band_high": v["judge_band_high"]}
                     for k, v in {
                         "contract": {"high": 0.98, "low": 0.90, "judge_band_high": 0.97},
                         "merger_agreement": {"high": 0.98, "low": 0.90, "judge_band_high": 0.97},
                         "insurance_claim": {"high": 0.98, "low": 0.90, "judge_band_high": 0.97},
                         "corporate_record": {"high": 0.96, "low": 0.86, "judge_band_high": 0.94},
                         "correspondence": {"high": 0.95, "low": 0.85, "judge_band_high": 0.92},
                     }.items()},
    }
    try:
        import yaml

        tax_path = (
            Path(__file__).resolve().parents[3]
            / "llm-mailroom" / "src" / "config" / "taxonomy.yaml"
        )
        cfg = yaml.safe_load(tax_path.read_text(encoding="utf-8"))
        conf = cfg.get("confidence") or {}
        bands["global"] = {
            "high": float(conf.get("high", bands["global"]["high"])),
            "low": float(conf.get("low", bands["global"]["low"])),
            "judge_band_high": float(conf.get("judge_band_high", bands["global"]["judge_band_high"])),
        }
        for doc_class, override in (conf.get("by_class") or {}).items():
            if doc_class in bands["by_class"] and override:
                bands["by_class"][doc_class] = {
                    "high": float(override.get("high", bands["by_class"][doc_class]["high"])),
                    "low": float(override.get("low", bands["by_class"][doc_class]["low"])),
                    "judge_band_high": float(
                        override.get("judge_band_high", bands["by_class"][doc_class]["judge_band_high"])
                    ),
                }
    except Exception:
        pass
    return bands


#: §14A matter/group backfill methodology — the P2 prerequisite decision
#: (documented in DOCCLASS_CONTRACT.md; never mixed silently).
#: Verified 2026-09-02 (HF audit @ pin bb57c5ad): In-Reply-To/References are
#: structurally ABSENT from the CMU maildir itself (0/350 raw files,
#: 0/247,523 upstream dedup rows), so true header-thread reconstruction is
#: impossible from published data; ``heuristic_reconstructed`` covers the
#: subject+custodian+time-window derivation (subject populated 346/350).
MATTER_CONSTRUCTION = (
    "source_native_thread",     # real reply-header chains (unavailable here)
    "heuristic_reconstructed",  # subject-based reconstruction, flagged, separate count
    "synthetic_constructed",    # manufactured bundles, flagged, never silent
)
GROUP_ROLES = (
    "primary", "attachment", "exhibit", "amendment", "supporting",
    "correspondence", "duplicate", "related", "unknown",
)
RELATIONSHIP_TYPES = (
    "attachment_of", "exhibit_of", "amendment_of", "supplement_to",
    "responds_to", "references", "duplicate_of", "supersedes", "related_to",
)
DUPLICATE_TYPES = (
    "exact_duplicate", "normalized_duplicate", "near_duplicate",
    "template_variant", "legitimate_repeat",
)

#: §58 stage-of-failure vocabulary (P3 recovery fixtures).
FAILURE_STAGES = (
    "ingestion", "classification", "routing", "extraction",
    "validation", "grouping", "adjudication", "archival",
)

#: Canonical per-class ground-truth FIELD sets (GT-side names, verified by
#: the §63 contract tests against the published schema; the two clause-list
#: keys use the GT-side names, not the specialist's field_type names).
#: Purpose-GT classes carry intent/subject_matter/keywords on top (§25).
EXPECTED_EXTRACTION_FIELDS: dict[str, tuple[str, ...]] = {
    "contract": ("cuad_clause_labels",),
    "merger_agreement": ("maud_clause_labels",),
    "insurance_claim": (
        "claim_number", "policy_number", "insurer", "insured_party",
        "claim_type", "date_of_loss", "date_filed", "claimed_amount",
        "adjuster", "damages_description", "coverage_determination",
        "denial_reasons", "supporting_documents",
    ),
    "corporate_record": (),
    "correspondence": (),
}
PURPOSE_GT_CLASSES = ("corporate_record", "correspondence", "insurance_claim")
PURPOSE_GT_KEYS = ("intent", "subject_matter", "keywords")


def expected_gt_fields(doc_class: str) -> tuple[str, ...]:
    """The full expected GT field set for a class (extraction + purpose)."""
    fields = list(EXPECTED_EXTRACTION_FIELDS.get(doc_class, ()))
    if doc_class in PURPOSE_GT_CLASSES:
        fields.extend(PURPOSE_GT_KEYS)
    return tuple(fields)


def enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    """P1 evaluation-contract fields on a row copy (identity.py pattern)."""
    out = dict(row)
    review, review_reason = review_expected(row)
    retry, post_retry = retry_expected(row)
    out["expected_specialist"] = expected_specialist(row)
    out["expected_stage"] = expected_stage(row, review_route=review)
    out["review_expected"] = str(bool(review)).lower()
    out["review_reason"] = review_reason
    out["retry_expected"] = str(bool(retry)).lower()
    out["expected_post_retry_state"] = post_retry_state(row) if retry else ""
    provenance = annotation_provenance(row)
    out["annotation_source"] = provenance["source"]
    out["annotation_method"] = provenance["method"]
    out["annotation_model"] = provenance["model"]
    out["annotation_prompt_version"] = provenance["prompt_version"]
    out["annotation_confidence"] = provenance["confidence"]
    out["annotation_reviewer"] = provenance["reviewer"]
    out["annotation_timestamp"] = provenance["timestamp"]
    return out


def post_retry_state(row: dict[str, Any]) -> str:
    """§31: expected_post_retry_state for retry fixtures (canonical: archived)."""
    ood = str(row.get("fixture_kind") or "")
    if ood == "retry_review":
        return "human_review"
    return "archived"


def enrich_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich every row (input order preserved; derivations are row-local)."""
    return [enrich_row(r) for r in rows]
