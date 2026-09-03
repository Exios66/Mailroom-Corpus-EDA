"""P1/P3 evaluation fixture content (plan §68–§71, §72A, §31; HUB-022).

In-memory fixture builders — the fixture CONTENT layer on top of the P1
vocabularies in ``eval_contract`` (which stay the single source of truth for
every closed set and derivation). Publishing any of these rows is the §84
v0.2 release decision; nothing here writes files or touches the Hub.

Families:

- **§70 calibration quartet** — the four confidence × correctness cells
  (``CALIBRATION_QUARTET``), probed AT the live routing bands read from
  llm-mailroom's ``taxonomy.yaml`` (``confidence_bands()``), so fixtures
  target the policy the pipeline actually routes on, not the plan's
  illustrative numbers. Cell → fixture-kind mapping (reuses the closed
  §68 vocabulary; ``calibration_cell`` carries the quartet identity):

  ================  ==================  =====================================
  cell              fixture_kind        expected pipeline behavior
  ================  ==================  =====================================
  correct_high      high_confidence     archive — nothing to catch
  correct_low       low_confidence      review (low_confidence)
  wrong_high        conflicting         judge catch → review; the SILENT
                                        archive is the failure mode under
                                        test (conflicting_information)
  wrong_low         retry_review        retry → still low → human_review
  ================  ==================  =====================================

- **§72A review/arbiter scenarios** — the recovery path as ground truth:
  review corrections, retry-then-archive, arbiter stands / re-extract /
  escalate-to-human. Every scenario row carries its ``failure_stage``
  (§58 ``FAILURE_STAGES``) so P3 can score first-pass vs. recovered success
  (§88) against the stage where the failure was injected.

All builders are deterministic and pure; rows use the ``fixture:`` filename
namespace so they can never collide with snapshot filenames, and absent
fields are ``''`` per the corpus-wide convention.
"""
from __future__ import annotations

from typing import Any

from .eval_contract import (
    CALIBRATION_QUARTET,
    CONFIDENCE_CELL_FIXTURE_KIND,
    FAILURE_STAGES,
    FIXTURE_KINDS,
    REVIEW_REASONS,
    SPECIALIST_BY_CLASS,
    confidence_bands,
    enrich_row,
)

FIXTURE_NS = "fixture:"

#: §72A arbiter outcome vocabulary (closed) — the Arbiter resolves a
#: partial/failed judge result by standing the result, ordering
#: re-extraction, or escalating to human review.
ARBITER_OUTCOMES = ("stands", "re_extract", "escalate_human_review")


def _fixture_row(filename: str, doc_class: str, body: str) -> dict[str, Any]:
    return {
        "filename": f"{FIXTURE_NS}{filename}",
        "doc_text": body,
        "expected": doc_class,
        "expected_subclass": "",
        "synthetic": "true",
        "fixture_kind": "",
        "calibration_cell": "",
        "failure_stage": "",
        "arbiter_outcome": "",
        "review_reason_override": "",
        "probes_confidence": "",
    }


def _band_probe(band: dict[str, float], side: str) -> float:
    """A confidence value just INSIDE the band edge being probed.

    ``high`` probes the continue-threshold (≥ high → archive), ``low``
    probes the retry-threshold (< low → review). Probing just inside the
    edge catches off-by-epsilon routing bugs that a midpoint value hides.
    """
    edge = band["high"] if side == "high" else band["low"]
    step = 0.005 if side == "high" else -0.005
    return round(min(max(edge + step, 0.0), 1.0), 4)


def calibration_quartet(
    doc_class: str = "contract",
    bands: dict[str, dict[str, float]] | None = None,
) -> list[dict[str, Any]]:
    """§70: the four calibration cells for one class, at the live bands.

    Each row records the confidence value it probes (``probes_confidence``)
    and derives its review/retry expectations through ``eval_contract`` —
    the quartet must never hardcode expectations that drift from the
    pipeline's actual routing config.
    """
    if doc_class not in SPECIALIST_BY_CLASS:
        raise KeyError(f"unknown doc class: {doc_class}")
    bands = bands or confidence_bands()
    band = bands["by_class"].get(doc_class) or bands["global"]

    high, low = _band_probe(band, "high"), _band_probe(band, "low")
    bodies = {
        "correct_high": (
            "MASTER SERVICES AGREEMENT. This Agreement is entered into by and "
            "between the parties identified in the signature blocks below and "
            "governs the provision of services."
        ),
        "correct_low": (
            "Re: our conversation — the services paperwork is attached, I think "
            "this is the agreement version we discussed but please double-check "
            "the signature page."
        ),
        "wrong_high": (
            "AMENDMENT NO. 2 to the Master Services Agreement dated January 15. "
            "Section 4 is deleted and replaced with: 'Payment terms are Net 45.'"
        ),
        "wrong_low": (
            "Meeting notes — we talked about the deal paperwork again, still "
            "not sure which draft is current, will follow up."
        ),
    }
    rows = []
    for cell in CALIBRATION_QUARTET:
        row = _fixture_row(
            f"calibration-{doc_class}-{cell}", doc_class, bodies[cell]
        )
        row["fixture_kind"] = CONFIDENCE_CELL_FIXTURE_KIND[cell]
        row["calibration_cell"] = cell
        row["probes_confidence"] = high if cell in ("correct_high", "wrong_high") else low
        rows.append(row)
    return rows


def review_correction_scenario() -> dict[str, Any]:
    """§72A: human review CORRECTS an extraction, then it archives.

    Simulated failure: classification extracted wrong counterparties from a
    low-quality scan; review catches it (``unreadable_source``), corrects,
    and the pipeline archives the corrected result. Ground truth: the
    corrected values + the expectation that reviewability (not accuracy)
    is what saved the run.
    """
    row = _fixture_row(
        "review-correction-scan",
        "contract",
        "[SYNTHETIC FIXTURE] Scanned service agreement, pages 3–7 illegible. "
        "Between Bay State Logistics, LLC and Cardinal Freight Co. Term: 24 "
        "months from the Effective Date. Termination for convenience on 60 "
        "days' written notice.",
    )
    row.update(
        {
            "fixture_kind": "incomplete",
            "failure_stage": "extraction",
            "review_reason_override": "unreadable_source",
            "expected_correction": "reviewer re-reads scan; counterparties and term re-keyed",
            "expected_post_correction_state": "archived",
        }
    )
    return row


def arbiter_scenarios() -> list[dict[str, Any]]:
    """§72A: one scenario per closed arbiter outcome (stands/re-extract/
    escalate) — the recovery ladder as testable ground truth."""
    common = dict(failure_stage="extraction")

    def _scenario(name: str, doc_class: str, body: str, **extra: Any) -> dict[str, Any]:
        row = _fixture_row(f"arbiter-{name}", doc_class, body)
        row.update(common)
        row.update(extra)
        return row

    return [
        _scenario(
            "stands",
            "insurance_claim",
            "[SYNTHETIC FIXTURE] Claim 88-4417, policy PL-99312, carrier "
            "Meridian Mutual. Date of loss 2023-11-02, filed 2023-11-09. "
            "Claimed amount $18,240.00; adjuster R. Calloway.",
            fixture_kind="retry",
            arbiter_outcome="stands",
            arbiter_note="judge partial was wrong: all fields present, stands → archive",
        ),
        _scenario(
            "re-extract",
            "contract",
            "[SYNTHETIC FIXTURE] Amendment No. 4 to the Consulting Agreement "
            "between Helix Systems and Northwind Health; notice address moves "
            "to 400 Second Ave, Suite 210.",
            fixture_kind="retry",
            arbiter_outcome="re_extract",
            arbiter_note="first pass dropped the parties; re-extraction recovers them → archive",
        ),
        _scenario(
            "escalate",
            "correspondence",
            "[SYNTHETIC FIXTURE] FW: FW: FW: (no subject) — attached is what "
            "they sent, not sure if this is the signed one or the draft.",
            fixture_kind="retry_review",
            arbiter_outcome="escalate_human_review",
            arbiter_note="retry still below threshold and the document is genuinely ambiguous → human",
        ),
    ]


def failure_stage_matrix() -> list[dict[str, Any]]:
    """§58/§88: one minimal failure fixture per stage of FAILURE_STAGES —
    the P3 recovery suite's spine (first-pass vs. recovered success is
    scored per stage)."""
    per_stage = {
        "ingestion": ("correspondence", "unreadable_source",
                      "body is binary garbage — decode fails before classification"),
        "classification": ("contract", "ambiguous",
                           "looks like an amendment AND a letter of intent — one class must win"),
        "routing": ("insurance_claim", "ambiguous",
                    "well-formed claim routed to the wrong specialist's queue"),
        "extraction": ("contract", "incomplete_extraction",
                       "signature page missing — parties extract, term does not"),
        "validation": ("insurance_claim", "conflicting_information",
                       "claimed_amount differs between form and EOB — schema validates, values conflict"),
        "grouping": ("correspondence", "ambiguous",
                     "reply lands without its parent thread — group assignment must not guess"),
        "adjudication": ("contract", "conflicting_information",
                         "judge partial vs specialist confident — arbiter must resolve"),
        "archival": ("corporate_record", "incomplete_extraction",
                     "filing extract missing the exhibit index — archive staged, not committed"),
    }
    rows = []
    for stage, (doc_class, reason, note) in per_stage.items():
        assert stage in FAILURE_STAGES
        row = _fixture_row(
            f"failure-{stage}", doc_class,
            f"[SYNTHETIC FIXTURE] {doc_class} exercising a {stage}-stage "
            f"failure: {note}",
        )
        row["fixture_kind"] = "incomplete" if reason == "incomplete_extraction" else (
            "conflicting" if reason == "conflicting_information" else "ambiguous"
        )
        row["failure_stage"] = stage
        row["review_reason_override"] = reason
        row["failure_note"] = note
        rows.append(row)
    return rows


def _finalize(row: dict[str, Any]) -> dict[str, Any]:
    """Derive expectations through eval_contract (SSOT) + apply overrides."""
    enriched = enrich_row(row)
    override = row.get("review_reason_override") or ""
    if override:
        assert override in REVIEW_REASONS, override
        enriched["review_reason"] = override
    kind = enriched["fixture_kind"]
    assert kind in FIXTURE_KINDS, kind
    return enriched


def build_fixture_suite(
    classes: tuple[str, ...] = tuple(SPECIALIST_BY_CLASS),
) -> list[dict[str, Any]]:
    """The whole P1/P3 fixture content layer as one deterministic list:
    a calibration quartet per class + review correction + arbiter scenarios
    + the failure-stage matrix (§68–§72A). Publish decision: §84."""
    rows: list[dict[str, Any]] = []
    for doc_class in classes:
        rows.extend(calibration_quartet(doc_class))
    rows.append(review_correction_scenario())
    rows.extend(arbiter_scenarios())
    rows.extend(failure_stage_matrix())
    return [_finalize(r) for r in rows]
