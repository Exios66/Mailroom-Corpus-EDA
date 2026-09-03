"""HUB-028 v8 builder tests: synthetic LOB expansion + GT conformance.

Covers (network-free; source fixtures reconstructed from the committed
builder logic):
- property rows: GT keys populated, verbatim contract, intent vocabulary,
  metadata provenance.
- auto rows: determination mapping (APPROVE/REVIEW/REJECT), denial reasons
  grounded in features, adjuster pseudonym, intent vocabulary.
- CMS backfill: intent/subject/keywords conformed, claimed_amount recovered
  from doc text where v7 shipped empty.
- validation gate: test-split nullification, no None, allowed-empty set.
"""
from __future__ import annotations

from mailroom_eda import v8_build as vb


def _min_gnotheia_row() -> dict:
    data = {
        "claim": {
            "claimId": "261163075",
            "claimant": "SynthGivenA73 SynthFamilyB7238",
            "dateOfLoss": "2026-03-01",
            "dateOfReported": "2026-03-03",
            "lossEvent": "Direct lightning strike",
            "lossSummary": "Lightning strike ignites fire in a roof structure.",
            "damagedItems": [{"insuredSubject": "Flat", "risk": "Direct lightning strike"}],
        },
        "policy": {
            "policyId": "PZ-PBI-3409831299",
            "product": "SynthCountryA01 Property Building Insurance",
            "coverage": {"insuredSubjects": "[{'name': 'Flat', 'risks': ['Direct lightning strike']}]"},
        },
        "contextDocuments": [
            {
                "documentType": "first_notice_of_loss",
                "extractedDocumentContent": (
                    "FIRST NOTICE OF LOSS\n\nClaim Reference: 261163075\n"
                    "Policy Number: PZ-PBI-3409831299\n"
                    "Product: SynthCountryA01 Property Building Insurance\n"
                    "Insured: SynthGivenA73 SynthFamilyB7238\n"
                    "Date of Loss: 2026-03-01\nDate of Report: 2026-03-03\n"
                    "Line of business: property\n"
                    "Estimated Damage: $12,500.00\n\nLoss Event Description: hi.\n"
                ),
            },
        ],
    }
    return vb._gnotheia_row(data, "1", vb.GNOTHEIA_REV)


def test_property_row_gt_conformed():
    row = _min_gnotheia_row()
    gt = row["gt_fields"]
    assert row["expected"] == "insurance_claim"
    assert row["expected_subclass"] == "property"
    assert gt["claim_number"] == "261163075"
    assert gt["policy_number"] == "PZ-PBI-3409831299"
    assert gt["claim_type"] == "property"
    assert gt["coverage_determination"] == "pending"
    assert gt["claimed_amount"] == 12500.0
    assert gt["intent"] == "claim_filing"
    assert gt["intent_source"] == "manual"
    # every scalar value verbatim
    vb.verbatim_assert(row)
    # metadata provenance
    assert row["metadata"]["source_dataset"] == vb.GNOTHEIA_REPO
    assert row["metadata"]["lob"] == "property"


def test_auto_row_denied_with_reasons():
    r = {
        "claim_id": "CLM-000963", "claim_amount": 15634.21,
        "vehicle_age": 13, "accident_type": "collision",
        "police_report": "no", "repair_estimate": 13050.65,
        "prior_claims": 5, "decision": "REJECT",
    }
    row = vb._auto_row(r, vb.BDR_AUTO_REV)
    gt = row["gt_fields"]
    assert gt["coverage_determination"] == "denied"
    assert len(gt["denial_reasons"]) >= 1
    assert "no police report" in gt["denial_reasons"][0]
    assert gt["adjuster"] and gt["adjuster"] != ""
    assert gt["claim_type"] == "auto"
    assert gt["intent"] == "coverage_determination"
    vb.verbatim_assert(row)
    for reason in gt["denial_reasons"]:
        assert reason in row["doc_text"]


def test_auto_row_approved_empty_reasons():
    r = {
        "claim_id": "CLM-000100", "claim_amount": 5000.0,
        "vehicle_age": 3, "accident_type": "weather_damage",
        "police_report": "yes", "repair_estimate": 4800.0,
        "prior_claims": 0, "decision": "APPROVE",
    }
    row = vb._auto_row(r, vb.BDR_AUTO_REV)
    gt = row["gt_fields"]
    assert gt["coverage_determination"] == "approved"
    assert gt["denial_reasons"] == []
    vb.verbatim_assert(row)


def test_backfill_cms_row_conforms():
    row = {
        "filename": "carrier:887013387879564.txt",
        "expected": "insurance_claim",
        "expected_subclass": "carrier",
        "doc_text": (
            "MEDICARE SUMMARY NOTICE -- PHYSICIAN/SUPPLIER CLAIM (Part B)\n"
            "Notice ID: 887013387879564\n\nInsurer:                  CMS Medicare\n"
            "Line of business:         health\n\nBENEFICIARY / INSURED\n"
            "  Insured party:            SANCHEZ, DAVID\n"
            "  Policy number:            B81C39C31DF18BEB\n\nSERVICE DETAILS\n"
            "  Service start date:       2009-07-20\n  Service end date:         2009-07-20\n\n"
            "ITEMIZED SERVICE LINES (1)\n   1. HCPCS 90816   allowed $60.00   paid $30.00   (coins $10.00, ded $0.00)\n"
            "ADJUDICATION SUMMARY\n  Claim total paid by Medicare: $30.00\n"
            "COVERAGE DETERMINATION: APPROVED - this claim has been adjudicated and paid.\n"
        ),
        "gt_fields": {
            "claim_number": "887013387879564",
            "policy_number": "B81C39C31DF18BEB",
            "insurer": "CMS Medicare",
            "insured_party": "SANCHEZ, DAVID",
            "claim_type": "health",
            "date_of_loss": "2009-07-20",
            "date_filed": "2009-07-20",
            "claimed_amount": 30.0,
            "adjuster": "",
            "damages_description": "x",
            "coverage_determination": "approved",
            "denial_reasons": "[]",
            "supporting_documents": "[]",
            "intent": "",
            "subject_matter": "",
            "keywords": "",
            "intent_source": "",
            "intent_confidence": "",
            "intent_status": "",
        },
    }
    vb.backfill_cms_row(row)
    gt = row["gt_fields"]
    assert gt["intent"] == "claim_data_record"
    assert gt["intent_source"] == "manual"
    assert gt["subject_matter"]
    assert gt["keywords"]


def test_backfill_recovers_claimed_amount_from_doc():
    row = {
        "filename": "carrier:887263386589340.txt",
        "expected": "insurance_claim",
        "expected_subclass": "carrier",
        "doc_text": (
            "MEDICARE SUMMARY NOTICE -- PHYSICIAN/SUPPLIER CLAIM (Part B)\n"
            "Notice ID: 887263386589340\n\nInsurer:                  CMS Medicare\n"
            "Line of business:         health\n\nBENEFICIARY / INSURED\n"
            "  Insured party:            BROWN, DAVID\n"
            "  Policy number:            AA1B2C3D4E5F6000\n\nSERVICE DETAILS\n"
            "  Service start date:       2009-07-20\n  Service end date:         2009-07-20\n\n"
            "ITEMIZED SERVICE LINES (1)\n   1. HCPCS 99308   allowed $40.00   paid $0.00   (coins $0.00, ded $0.00)\n"
            "ADJUDICATION SUMMARY\n  Claim total paid by Medicare: N/A\n"
            "COVERAGE DETERMINATION: APPROVED - this claim has been adjudicated and paid.\n"
        ),
        "gt_fields": {
            "claim_number": "887263386589340",
            "policy_number": "AA1B2C3D4E5F6000",
            "insurer": "CMS Medicare",
            "insured_party": "BROWN, DAVID",
            "claim_type": "health",
            "date_of_loss": "2009-07-20",
            "date_filed": "2009-07-20",
            "claimed_amount": "",
            "adjuster": "",
            "damages_description": "x",
            "coverage_determination": "approved",
            "denial_reasons": "[]",
            "supporting_documents": "[]",
            "intent": "",
            "subject_matter": "",
            "keywords": "",
            "intent_source": "",
            "intent_confidence": "",
            "intent_status": "",
        },
    }
    vb.backfill_cms_row(row)
    assert row["gt_fields"]["claimed_amount"] == 0.0


def test_validation_gate_nullifies_test_split():
    rows = [_min_gnotheia_row(), vb._auto_row({
        "claim_id": "CLM-000963", "claim_amount": 15634.21,
        "vehicle_age": 13, "accident_type": "collision",
        "police_report": "no", "repair_estimate": 13050.65,
        "prior_claims": 5, "decision": "REJECT",
    }, vb.BDR_AUTO_REV)]
    for r in rows:
        r["split"] = vb.assign_split(r["filename"])
    rep = vb.validate_rows(rows)
    # only 2 rows; at least one lands train — no test-split row should error
    assert rep["errors"] == [], rep["errors"][:3]


def test_stratification_keeps_denials():
    import pandas as pd
    # synthetic mini-frame: all decisions present
    df = pd.DataFrame([
        {"claim_id": f"CLM-{i:06d}", "claim_amount": 1000.0 + i, "vehicle_age": 5,
         "accident_type": at, "police_report": "no", "repair_estimate": 900.0,
         "prior_claims": 4, "decision": dec}
        for i, (at, dec) in enumerate([
            ("collision", "REJECT"), ("collision", "APPROVE"),
            ("theft", "REJECT"), ("theft", "REVIEW"),
            ("fire", "APPROVE"), ("weather_damage", "REVIEW"),
        ])
    ])
    rows = vb.build_auto_rows(df, vb.BDR_AUTO_REV)
    seen = {r["gt_fields"]["coverage_determination"] for r in rows}
    assert "denied" in seen
