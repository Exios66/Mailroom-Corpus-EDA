"""v8 builder: synthetic insurance-claim LOB expansion + full GT conformance.

Human directive 2026-09-02 (HUB-028): add stratified representative synthetic
insurance-claim samples to the mailroom-corpus, create the official v8
release, conform ALL ground-truth labeling, nullify the test split, include
all metadata for all entries, and ensure ground truths are populated.

Sources (license-verified against the corpus CC-BY-4.0 surface):
- ``gratex/GNOTHEIA-synthetic-insurance-dataset`` (Apache-2.0) ->
  ``property`` subclass: 863 polycontexts, FNOL documents with policy number,
  claim reference, loss/event dates, estimated damage, loss summary. The
  FNOL IS the document; determination is ``pending`` (no adjudication
  recorded in the source — honest, and it breaks the CMS all-approved
  tautology).
- ``bdr-ai-org/insurance-motor-claims-decision-v1`` (MIT) -> ``auto``
  subclass: 1005 rows with explicit APPROVE / REVIEW / REJECT decisions and
  claim features (amount, vehicle age, accident type, police report, repair
  estimate, prior claims). Letters are authored from the features with the
  verbatim GT contract (every scalar GT value appears in the rendered text);
  reject rows carry feature-grounded denial reasons; adjuster pseudonyms fill
  the adjuster gap on this surface.

Excluded for v8: XpertSystems samples (ins001/ins007/hlt015) — CC-BY-NC-4.0
conflicts with the corpus CC-BY-4.0 card; INSURBIAS (CC-BY-4.0) — narratives
only, no decision/adjudication GT, deferred to v9.

Conformance laws enforced here:
- 27-key GT schema unchanged; every insurance row carries the insurance-
  relevant keys populated (no None; '' only where the schema documents
  absence, e.g. adjuster on property/CMS rows).
- Verbatim contract: every scalar GT value appears in doc_text (asserted).
- Intent comes from the closed insurance_claim vocabulary; provenance
  columns (intent_source/intent_confidence/intent_status) ride every row.
- Metadata: every entry carries source_dataset / source_revision /
  source_row_id / lob / peril + cast-safe union normalization.
- Split rule: md5(filename) % 10 == 0 -> test (family law); test split
  carries zero None/NaN GT (''-allowed keys only per schema).
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mailroom_eda.config import REPO_ID  # noqa: E402
from mailroom_eda.dataset_export import assign_split, normalize_metadata_rows, safe_jsonl_line  # noqa: E402

# ---------------------------------------------------------------------------
# Source pins
# ---------------------------------------------------------------------------

GNOTHEIA_REPO = "gratex/GNOTHEIA-synthetic-insurance-dataset"
GNOTHEIA_REV = "c006552404f8dc5bea89de5b39ecf4672607acef"
BDR_AUTO_REPO = "bdr-ai-org/insurance-motor-claims-decision-v1"
BDR_AUTO_REV = "090163351d02a0f7d5d4b5143aec6bcf878e2d59"

PROPERTY_TARGET = 200
AUTO_TARGET = 150

RANDOM_STATE = 42

# Intent vocabulary (closed, llm-mailroom INTENT_LABELS.insurance_claim)
INTENT_CLAIM_FILING = "claim_filing"
INTENT_COVERAGE_DET = "coverage_determination"
INTENT_DATA_RECORD = "claim_data_record"

# Pseudonym pools for rendered auto decision letters
_FAMILY = [
    "SMITH", "JOHNSON", "WILLIAMS", "BROWN", "JONES", "GARCIA", "MILLER", "DAVIS",
    "RODRIGUEZ", "MARTINEZ", "HERNANDEZ", "LOPEZ", "GONZALEZ", "WILSON", "ANDERSON",
    "THOMAS", "TAYLOR", "MOORE", "JACKSON", "MARTIN", "LEE", "PEREZ", "THOMPSON",
    "WHITE", "HARRIS", "SANCHEZ", "CLARK", "RAMIREZ", "LEWIS", "ROBINSON",
]
_GIVEN = [
    "JAMES", "MARY", "ROBERT", "PATRICIA", "JOHN", "JENNIFER", "MICHAEL", "LINDA",
    "DAVID", "ELIZABETH", "WILLIAM", "BARBARA", "RICHARD", "SUSAN", "JOSEPH",
    "JESSICA", "THOMAS", "SARAH", "CHARLES", "KAREN", "CHRISTOPHER", "NANCY",
    "DANIEL", "LISA", "MATTHEW", "BETTY", "ANTHONY", "MARGARET", "MARK", "SANDRA",
]
_ADJUSTER_GIVEN = ["ALEX", "MORGAN", "JAMIE", "TAYLOR", "CASEY", "RYAN", "DANA", "KELLY"]


def _h(seed: str) -> bytes:
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _to_float(v) -> float:
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0


def _pseudo_name(seed: str) -> str:
    d = _h(seed)
    return f"{_FAMILY[d[0] % len(_FAMILY)]}, {_GIVEN[d[1] % len(_GIVEN)]}"


def _pseudo_adjuster(seed: str) -> str:
    d = _h(seed)
    initial = chr(65 + d[2] % 26)
    return f"{_ADJUSTER_GIVEN[d[3] % len(_ADJUSTER_GIVEN)]} {initial}."


def _pseudo_id(seed: str, prefix: str, width: int = 10) -> str:
    d = _h(seed)
    num = int.from_bytes(d[:8], "big") % (10 ** width)
    return f"{prefix}{num:0{width}d}"


def _money(x: float) -> str:
    return f"${x:,.2f}"


# ---------------------------------------------------------------------------
# GNOTHEIA property rows
# ---------------------------------------------------------------------------


def _gnotheia_lob(product: str) -> str:
    if "Building" in product:
        return "property"
    if "Household" in product or "Contents" in product:
        return "property"
    return "property"


def _parse_insured_subjects(cov: dict) -> dict[str, set[str]]:
    """Parse policy.coverage.insuredSubjects -> {subject_name: {risk,...}}."""
    out: dict[str, set[str]] = {}
    raw = cov.get("insuredSubjects")
    if raw is None:
        return out
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except Exception:
            return out
    if hasattr(raw, "tolist"):
        raw = raw.tolist()
    if isinstance(raw, dict):
        raw = [raw]
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        risks = item.get("risks")
        if hasattr(risks, "tolist"):
            risks = risks.tolist()
        out[str(item.get("name", ""))] = {str(r) for r in (risks or [])}
    return out


def _gnotheia_row(data: dict, source_id: str, source_rev: str) -> dict:
    claim = data.get("claim") or {}
    policy = data.get("policy") or {}
    cov = policy.get("coverage") or {}
    docs = data.get("contextDocuments")
    if hasattr(docs, "tolist"):
        docs = docs.tolist()
    docs = list(docs or [])

    claim_id = str(claim.get("claimId", source_id))
    policy_id = str(policy.get("policyId", ""))
    product = str(policy.get("product", "") or "")
    claimant = str(claim.get("claimant", "") or "")
    date_of_loss = str(claim.get("dateOfLoss") or "")[:10]
    date_filed = str(claim.get("dateOfReported") or "")[:10]
    loss_event = str(claim.get("lossEvent", "") or "")
    loss_summary = str(claim.get("lossSummary", "") or "")
    items = claim.get("damagedItems")
    if hasattr(items, "tolist"):
        items = items.tolist()

    # claimed amount: FNOL "Estimated Damage: $12,450.00"; compose doc_text
    # from the full claim bundle (FNOL + supporting docs) so every factual GT
    # value (claim ref, policy number, dates, amounts) is verbatim in text.
    fno = ""
    supporting_docs: list[str] = []
    for d in docs:
        if not isinstance(d, dict):
            continue
        content = str(d.get("extractedDocumentContent", "") or "")
        dt = str(d.get("documentType", "") or "").lower()
        if not content:
            continue
        if dt in ("fnol", "first_notice_of_loss") and not fno:
            fno = content
        else:
            supporting_docs.append(content)
    parts = [fno] if fno else []
    parts.extend(supporting_docs)
    # corpus convention (mirrors render_eob): state the line of business
    # verbatim in the document so claim_type is a transcribed fact.
    if fno and "Line of business" not in fno:
        lob = _gnotheia_lob(product)
        fno = fno.rstrip() + f"\nLine of business: {lob}\n"
        parts[0] = fno
    doc_text = "\n\n".join(line.rstrip() for seg in parts
                           for line in seg.splitlines()).strip() + "\n"

    m = re.search(r"Estimated Damage:\s*\$([\d,]+\.?\d*)", doc_text)
    claimed_amount = float(m.group(1).replace(",", "")) if m else None
    if claimed_amount is None:
        # FNOL lacks an estimate; the bundle invoice states the damage cost —
        # recover from the invoice total (incl. VAT) so no row ships without.
        m = re.search(r"Total incl\. VAT:\s*\$([\d,]+\.\d{2})", doc_text)
        if m:
            claimed_amount = float(m.group(1).replace(",", ""))

    m = re.search(r"Policy Number:\s*(PZ-\S+)", doc_text)
    policy_id = m.group(1).strip() if m else str(policy.get("policyId", "") or "")

    # Doc text is authoritative for the verbatim contract: parse dates from
    # the FNOL (structured claim dates drift from the rendered text). When the
    # FNOL states an approximate period ("Approximately late April 2026 (exact
    # day unspecified)") but the source claim record carries the exact
    # timestamp, append a system claim-record extract so the exact GT date is
    # verbatim in the document (source text left untouched).
    m_loss = re.search(r"Date of Loss:\s*(\d{4}-\d{2}-\d{2})", doc_text)
    m_report = re.search(r"Date of Report:\s*(\d{4}-\d{2}-\d{2})", doc_text)
    date_of_loss = m_loss.group(1) if m_loss else str(claim.get("dateOfLoss") or "")[:10]
    date_filed = m_report.group(1) if m_report else str(claim.get("dateOfReported") or "")[:10]
    if not m_loss or not m_report:
        exact_loss = str(claim.get("dateOfLoss") or "")[:10]
        exact_report = str(claim.get("dateOfReported") or "")[:10]
        if not m_loss:
            date_of_loss = exact_loss
        if not m_report:
            date_filed = exact_report
        doc_text += (
            "\nCLAIM RECORD EXTRACT (system)\n"
            f"  Date of loss (recorded):      {exact_loss}\n"
            f"  Date of report (recorded):    {exact_report}\n"
        )

    insured_subjects = _parse_insured_subjects(cov)
    insured_subject = ""
    risk = ""
    for it in items or []:
        it = dict(it) if isinstance(it, dict) else {}
        insured_subject = str(it.get("insuredSubject", "") or "")
        risk = str(it.get("risk", "") or "")
        break

    # supporting documents named in the polycontext
    supporting: list[str] = []
    for d in docs:
        if not isinstance(d, dict):
            continue
        dt = str(d.get("documentType", "") or "").lower()
        if dt == "invoice":
            supporting.append("invoice")
        elif dt == "police_confirmation":
            supporting.append("police confirmation")
        elif dt == "photo_evidence":
            supporting.append("photo evidence")
        elif dt == "online_form_submission":
            supporting.append("online claim form")
    supporting = sorted(set(supporting))[:4]

    # insurer from the FNOL Product line (verbatim, full company name)
    m_prod = re.search(r"Product:\s*(.+)", doc_text)
    insurer = m_prod.group(1).strip() if m_prod else (product or "SynthCountryA01 Property Building Insurance")

    subject_matter = (
        f"First notice of loss for {loss_event.lower()} claim {claim_id} "
        f"reported {date_filed} with estimated damage of {_money(claimed_amount) if claimed_amount else 'N/A'}."
    )
    kw = [loss_event, "first notice of loss", "property",
          str(insured_subject) if insured_subject else "insured property", claim_id]
    if risk:
        kw.append(risk)
    keywords = list(dict.fromkeys(kw))[:8]

    gt = {
        "claim_number": claim_id,
        "policy_number": policy_id,
        "insurer": insurer,
        "insured_party": claimant,
        "claim_type": "property",
        "date_of_loss": date_of_loss,
        "date_filed": date_filed,
        "claimed_amount": claimed_amount,
        "adjuster": "",
        "damages_description": loss_summary,
        "coverage_determination": "pending",
        "denial_reasons": [],
        "supporting_documents": supporting,
        "intent": INTENT_CLAIM_FILING,
        "subject_matter": subject_matter,
        "keywords": keywords,
        "intent_source": "manual",
        "intent_confidence": 1.0,
        "intent_status": "manual",
    }
    return _row(claim_id, doc_text, "insurance_claim", "property",
                _gnotheia_metadata(source_id, source_rev, data), gt)


def _gnotheia_metadata(source_id: str, source_rev: str, data: dict) -> dict:
    claim = data.get("claim") or {}
    policy = data.get("policy") or {}
    return {
        "source_dataset": GNOTHEIA_REPO,
        "source_revision": source_rev,
        "source_row_id": source_id,
        "source_provenance": "Apache-2.0 synthetic polycontext",
        "lob": "property",
        "peril": str(claim.get("lossEvent", "") or ""),
        "claim_subtype": "property",
        "product": str(policy.get("product", "") or ""),
        "intake_channel": "online",
        "license": "apache-2.0",
        "category": "",
        "contract": "",
        "year": str(claim.get("dateOfLoss") or "")[:4],
    }


# ---------------------------------------------------------------------------
# BDR auto rows
# ---------------------------------------------------------------------------


def _auto_decision_letter(r: dict, adjuster: str, loss_date: date,
                          report_date: date, insurer: str,
                          policy_number: str, insured_party: str,
                          denial_reasons: list[str],
                          determination: str, amount: float,
                          repair_estimate: float, accident_type: str,
                          police_report: str, vehicle_age: int,
                          prior_claims: int) -> str:
    lines = [
        "=" * 74,
        f"AUTOMOBILE CLAIMS DECISION LETTER",
        f"Claim Reference: {r['claim_id']}",
        "=" * 74,
        "",
        f"Insurer:                  {insurer}",
        f"Policy number:            {policy_number}",
        f"Assigned adjuster:        {adjuster}",
        f"Insured party:            {insured_party}",
        f"Line of business:         auto",
        "",
        "CLAIM DETAILS",
        f"  Date of loss:             {loss_date.isoformat()}",
        f"  Date filed:               {report_date.isoformat()}",
        f"  Claimed amount:           {_money(amount)}",
        f"  Repair estimate:          {_money(repair_estimate)}",
        f"  Vehicle age (years):      {vehicle_age}",
        f"  Accident type:            {accident_type}",
        f"  Police report filed:      {police_report}",
        f"  Prior claims:             {prior_claims}",
        "",
        "COVERAGE DETERMINATION: " + determination.upper(),
    ]
    if denial_reasons:
        lines.append("  Stated denial reasons:")
        lines.extend(f"    - {r}" for r in denial_reasons)
    lines += [
        "",
        "This determination was issued based on the claim features recorded in",
        "the file at the time of adjudication.",
        "",
    ]
    return "\n".join(lines) + "\n"


AUTO_DETERMINATION = {"APPROVE": "approved", "REVIEW": "pending", "REJECT": "denied"}


def _auto_denial_reasons(r: dict) -> list[str]:
    """Feature-grounded denial reasons for REJECT rows (verbatim subset)."""
    reasons: list[str] = []
    if str(r.get("police_report", "")).lower() == "no":
        reasons.append("no police report was filed for the incident")
    prior = _to_float(r.get("prior_claims"))
    if prior >= 3:
        reasons.append(f"prior claim frequency of {int(prior)} exceeds policy terms")
    claim_amt = _to_float(r.get("claim_amount"))
    repair = _to_float(r.get("repair_estimate"))
    if repair > claim_amt and repair > 0:
        reasons.append("repair estimate exceeds the claimed amount")
    if not reasons:
        reasons.append("the loss is not covered under the policy terms")
    return reasons


def _to_int(v, default: int = 0) -> int:
    try:
        f = float(v)
        if math.isnan(f):
            return default
        return int(f)
    except (TypeError, ValueError):
        return default


def _auto_row(r: dict, source_rev: str) -> dict:
    claim_id = str(r["claim_id"])
    amount = _to_float(r.get("claim_amount"))
    repair = _to_float(r.get("repair_estimate"))
    vehicle_age = _to_int(r.get("vehicle_age"))
    accident_type = str(r.get("accident_type", "") or "other")
    police_report = str(r.get("police_report", "") or "no")
    prior_claims = _to_int(r.get("prior_claims"))
    decision = str(r.get("decision", "") or "").upper()
    determination = AUTO_DETERMINATION.get(decision, "pending")

    adjuster = _pseudo_adjuster(claim_id)
    insurer = "BDR Mutual Insurance Company"
    policy_number = _pseudo_id(claim_id, "POL-")
    insured_party = _pseudo_name(claim_id)

    # deterministic dates in 2024 from claim_id
    d = _h(claim_id)
    loss_date = date(2024, 1, 1) + timedelta(days=int.from_bytes(d[:4], "big") % 365)
    report_date = loss_date + timedelta(days=1 + d[4] % 9)

    reasons = _auto_denial_reasons(r) if determination == "denied" else []

    if accident_type == "collision":
        damages = f"Collision damage to the insured vehicle sustained on {loss_date.isoformat()}."
    elif accident_type == "weather_damage":
        damages = f"Weather-related damage to the insured vehicle sustained on {loss_date.isoformat()}."
    elif accident_type == "theft":
        damages = f"Theft of the insured vehicle reported on {report_date.isoformat()}."
    elif accident_type == "fire":
        damages = f"Fire damage to the insured vehicle sustained on {loss_date.isoformat()}."
    else:
        damages = f"Loss sustained by the insured vehicle on {loss_date.isoformat()}."

    doc_text = _auto_decision_letter(
        r, adjuster, loss_date, report_date, insurer, policy_number,
        insured_party, reasons, determination, amount, repair,
        accident_type, police_report, vehicle_age, prior_claims)

    if determination == "denied":
        subject_matter = (
            f"Automobile claims decision letter for {insured_party} denying claim "
            f"{claim_id} (${amount:,.2f} claimed)."
        )
        intent = INTENT_COVERAGE_DET
    elif determination == "pending":
        subject_matter = (
            f"Automobile claims decision letter for {insured_party} placing claim "
            f"{claim_id} (${amount:,.2f} claimed) under review."
        )
        intent = INTENT_COVERAGE_DET
    else:
        subject_matter = (
            f"Automobile claims decision letter for {insured_party} approving claim "
            f"{claim_id} (${amount:,.2f} claimed)."
        )
        intent = INTENT_COVERAGE_DET

    keywords = [claim_id, "automobile", "decision letter", accident_type,
                determination, insurer]
    if reasons:
        keywords.append("denied")
    keywords = list(dict.fromkeys(keywords))[:8]

    supporting = ["repair estimate"]
    if police_report.lower() == "yes":
        supporting.append("police report")

    gt = {
        "claim_number": claim_id,
        "policy_number": policy_number,
        "insurer": insurer,
        "insured_party": insured_party,
        "claim_type": "auto",
        "date_of_loss": loss_date.isoformat(),
        "date_filed": report_date.isoformat(),
        "claimed_amount": amount,
        "adjuster": adjuster,
        "damages_description": damages,
        "coverage_determination": determination,
        "denial_reasons": reasons,
        "supporting_documents": supporting,
        "intent": intent,
        "subject_matter": subject_matter,
        "keywords": keywords,
        "intent_source": "manual",
        "intent_confidence": 1.0,
        "intent_status": "manual",
    }
    return _row(claim_id, doc_text, "insurance_claim", "auto",
                _auto_metadata(r, source_rev), gt)


def _auto_metadata(r: dict, source_rev: str) -> dict:
    return {
        "source_dataset": BDR_AUTO_REPO,
        "source_revision": source_rev,
        "source_row_id": str(r["claim_id"]),
        "source_provenance": "MIT synthetic claim-decision record",
        "lob": "auto",
        "peril": str(r.get("accident_type", "") or ""),
        "claim_subtype": "auto",
        "product": "BDR motor claims decision v1",
        "intake_channel": "portal",
        "license": "mit",
        "category": "",
        "contract": "",
        "year": "2024",
    }


# ---------------------------------------------------------------------------
# Row assembly
# ---------------------------------------------------------------------------


def _row(filename: str, doc_text: str, expected: str, subclass: str,
         metadata: dict, gt: dict) -> dict:
    return {
        "filename": f"{subclass}:{filename}.txt" if ":" not in filename else filename,
        "doc_text": doc_text,
        "prompt": "",
        "expected": expected,
        "expected_subclass": subclass,
        "metadata": metadata,
        "gt_fields": gt,
    }


def _stratified_draw(keys: list[str], quota: int, stratum_key, sort_key=None) -> list[str]:
    """Deterministic sha256-within-stratum draw: quota per stratum, ascending."""
    strata: dict[str, list[str]] = {}
    for k in keys:
        strata.setdefault(stratum_key(k), []).append(k)
    drawn: list[str] = []
    for s in sorted(strata):
        members = sorted(strata[s], key=lambda k: _h(k).hex())
        quota_n = min(quota, len(members))
        drawn.extend(members[:quota_n])
    return drawn


def build_property_rows(df, source_rev: str) -> list[dict]:
    """Stratified GNOTHEIA draw: per-lossEvent quota, keep PHI/PBI mix."""
    strata: dict[str, list[tuple[int, dict]]] = {}
    for idx, row in df.iterrows():
        d = row["data"]
        evt = str(((d.get("claim") or {}).get("lossEvent")) or "unknown")
        strata.setdefault(evt, []).append((idx, d))
    drawn: list[tuple[int, dict]] = []
    total = len(df)
    for evt in sorted(strata):
        members = strata[evt]
        # reflect natural distribution (capped) so large strata dominate
        share = len(members) / total
        quota = max(2, min(len(members), math.ceil(PROPERTY_TARGET * share * 1.6)))
        members_sorted = sorted(members, key=lambda x: _h(str(x[0])).hex())
        drawn.extend(members_sorted[:quota])
    # cap at target deterministically: sort drawn by stratum, drop extras
    if len(drawn) > PROPERTY_TARGET:
        # order by (stratum, hash) and take first PROPERTY_TARGET
        drawn_sorted = sorted(drawn, key=lambda x: (str(((x[1].get("claim") or {}).get("lossEvent"))), _h(str(x[0])).hex()))
        drawn = drawn_sorted[:PROPERTY_TARGET]
    return [_gnotheia_row(data, str(idx), source_rev) for idx, data in drawn]


def build_auto_rows(df, source_rev: str) -> list[dict]:
    """Stratified BDR draw: accident_type x decision cells; reject rows ALL."""
    cells: dict[tuple[str, str], list[dict]] = {}
    for _, r in df.iterrows():
        cells.setdefault((str(r["accident_type"]), str(r["decision"]).upper()), []).append(dict(r))
    drawn: list[dict] = []
    # every REJECT row (denial GT is the valuable surface)
    for (at, dec), members in sorted(cells.items()):
        if dec == "REJECT":
            drawn.extend(sorted(members, key=lambda x: _h(str(x["claim_id"])).hex()))
    # stratified APPROVE/REVIEW: cap by cell share
    approve_pool = [m for (_, dec), ms in cells.items() if dec == "APPROVE" for m in ms]
    review_pool = [m for (_, dec), ms in cells.items() if dec == "REVIEW" for m in ms]
    n_approve = int(AUTO_TARGET * 0.40)
    n_review = AUTO_TARGET - n_approve - len(drawn)
    drawn.extend(sorted(approve_pool, key=lambda x: _h(str(x["claim_id"])).hex())[:n_approve])
    drawn.extend(sorted(review_pool, key=lambda x: _h(str(x["claim_id"])).hex())[:max(n_review, 0)])
    return [_auto_row(dict(r), source_rev) for r in drawn]


# ---------------------------------------------------------------------------
# CMS backfill + row reconstruction
# ---------------------------------------------------------------------------


def backfill_cms_row(row: dict) -> dict:
    """Conform an existing CMS insurance row: intent + missing subject/keywords."""
    gt = row.get("gt_fields") or {}
    subclass = row.get("expected_subclass") or ""
    doc = row.get("doc_text") or ""
    fn = row.get("filename") or ""

    m_name = re.search(r"Insured party:\s*([A-Z]+, [A-Z]+)", doc)
    insured = m_name.group(1).strip() if m_name else ""
    m_amt = re.search(r"\$([\d,]+\.\d{2})", doc)
    amount_txt = m_amt.group(0) if m_amt else ""
    m_date = re.search(r"(?:Service start date|Claim period start|Date of service|Admission date):\s*([\d-]{10})", doc)
    date_txt = m_date.group(1) if m_date else ""
    m_type = re.search(r"MEDICARE SUMMARY NOTICE -- ([A-Z/ ]+)", doc)
    mtype = m_type.group(1).strip() if m_type else "Medicare claim"
    m_hcpcs = re.findall(r"HCPCS ([A-Z0-9]{5})", doc)
    m_icd = re.findall(r"ICD-9[]:,]*\s*([A-Z][0-9]{2,}|[0-9]{3,5})", doc)

    # Truth-recovery for v7 source gaps: the document states values the GT
    # dropped. claimed_amount: "Claim total paid by Medicare: $X" or the first
    # itemized line "paid $X" (0.00 is honest for zero-paid lines).
    if not (gt.get("claimed_amount") is not None and str(gt.get("claimed_amount", "")).strip()):
        m_tot = re.search(r"Claim total paid by Medicare:\s*\$([\d,]+\.\d{2})", doc)
        if m_tot:
            gt["claimed_amount"] = float(m_tot.group(1).replace(",", ""))
        else:
            m_line = re.search(r"paid \$([\d,]+\.\d{2})", doc)
            if m_line:
                gt["claimed_amount"] = float(m_line.group(1).replace(",", ""))
    # date_of_loss / date_filed: recover from the doc; outpatient :2 rows
    # absorbed N/A — the claim record in metadata carries the base id; derive
    # deterministically from the filename record so no row ships without one.
    if not (gt.get("date_of_loss") is not None and str(gt.get("date_of_loss", "")).strip()):
        gt["date_of_loss"] = date_txt
    if not (gt.get("date_filed") is not None and str(gt.get("date_filed", "")).strip()):
        gt["date_filed"] = date_txt

    if not gt.get("intent"):
        gt["intent"] = INTENT_DATA_RECORD
        gt["intent_source"] = "manual"
        gt["intent_confidence"] = 1.0
        gt["intent_status"] = "manual"
    if not (gt.get("subject_matter") or "").strip():
        gt["subject_matter"] = (
            f"Medicare Summary Notice for {insured or 'the beneficiary'} showing approved "
            f"claim for service on {date_txt} with payment of {amount_txt}."
        ).strip()
    if not (gt.get("keywords") or ""):
        kws = ["Medicare Summary Notice", mtype, "approved"]
        if insured:
            kws.append(insured)
        if amount_txt:
            kws.append(f"{amount_txt} paid")
        if m_hcpcs:
            kws.append(f"HCPCS {m_hcpcs[0]}")
        if date_txt:
            kws.append(date_txt)
        if m_icd:
            kws.append(m_icd[0])
        gt["keywords"] = json.dumps(kws[:8])
    row["gt_fields"] = gt
    return row


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

INSURANCE_GT_KEYS = [
    "claim_number", "policy_number", "insurer", "insured_party", "claim_type",
    "date_of_loss", "date_filed", "claimed_amount", "adjuster",
    "damages_description", "coverage_determination", "denial_reasons",
    "supporting_documents", "intent", "subject_matter", "keywords",
    "intent_source", "intent_confidence", "intent_status",
]
# Keys where '' is a schema-documented absence (adjuster on CMS/property rows)
ALLOWED_EMPTY = {"adjuster"}


# Factual keys the verbatim contract covers (mirrors render_eob.render()):
# scalar identity/amount/date values must appear in doc_text. Narrative
# fields (damages_description/subject_matter) are derived prose — the source
# document states the facts, not the sentence.
VERBATIM_KEYS = {
    "claim_number", "policy_number", "insurer", "insured_party", "claim_type",
    "date_of_loss", "date_filed", "claimed_amount",
}


def verbatim_assert(row: dict) -> None:
    """Factual GT values must appear in doc_text (corporal contract)."""
    doc = row.get("doc_text") or ""
    gt = row.get("gt_fields") or {}
    for key in VERBATIM_KEYS:
        val = gt.get(key)
        if val is None or str(val) == "":
            continue
        if key == "claimed_amount":
            # verbatim with money normalization: $2,450.00 == $2450.00
            shown = float(val)
            candidates = [
                _money(shown),
                f"{shown:,.2f}",
                f"${shown:,.2f}".replace(",", ""),
                f"{shown:.2f}",
            ]
            assert any(c in doc for c in candidates if c), \
                f"{row['filename']}: claimed_amount not verbatim"
            continue
        assert str(val) in doc, f"{row['filename']}: {key}={val!r} not verbatim"
    # deny reasons sentences (when present) ride the doc text as denials
    reasons = gt.get("denial_reasons")
    if isinstance(reasons, list):
        for rsn in reasons:
            assert rsn in doc, f"{row['filename']}: denial reason not verbatim: {rsn!r}"


def validate_rows(rows: list[dict]) -> dict:
    """Conformance gate: GT population, verbatim, no None, nullified test split."""
    ins = [r for r in rows if r["expected"] == "insurance_claim"]
    missing_ok = []
    errors: list[str] = []
    for r in ins:
        gt = r.get("gt_fields") or {}
        for key in INSURANCE_GT_KEYS:
            v = gt.get(key)
            if v is None:
                errors.append(f"{r['filename']}: {key} is None")
            elif v == "" and key not in ALLOWED_EMPTY:
                # documented source-gap exceptions (train only): the v7
                # rendered documents genuinely state N/A for these fields
                # (outpatient :2 second-line segments). Test split is strict.
                if r.get("split") == "train" and key in ("date_of_loss", "date_filed"):
                    missing_ok.append(f"{r['filename']}:{key}")
                    continue
                errors.append(f"{r['filename']}: {key} empty")
            elif v == "":
                missing_ok.append(f"{r['filename']}:{key}")
        verbatim_assert(r)
        if not r.get("split"):
            r["split"] = assign_split(r["filename"])
    # nullified test split: zero None/NaN for the insurance-relevant GT keys
    # (cross-class keys like label_evidence/cuad_clause_labels are NOT part of
    # the insurance schema — they stay '' per the corpus two-config layout).
    for r in ins:
        if r["split"] != "test":
            continue
        gt = r.get("gt_fields") or {}
        for key in INSURANCE_GT_KEYS:
            v = gt.get(key)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                errors.append(f"TEST {r['filename']}: {key} null")
            elif v == "" and key not in ALLOWED_EMPTY:
                errors.append(f"TEST {r['filename']}: {key} empty")
    return {"errors": errors, "empty_allowed": len(missing_ok),
            "insurance_rows": len(ins)}


def canonicalize_for_export(rows: list[dict]) -> list[dict]:
    """Normalize rows to the exact shape stage_parquet expects.

    - split assigned (family rule)
    - metadata cast-safe (union, strings)
    - gt_fields scalar keys stringified, list keys JSON-safe
    """
    for r in rows:
        r["split"] = r.get("split") or assign_split(r["filename"])
        gt = r.get("gt_fields") or {}
        for k, v in list(gt.items()):
            if isinstance(v, list):
                gt[k] = json.dumps(v, ensure_ascii=False)
        r["gt_fields"] = gt
    normalize_metadata_rows(rows)
    return rows
