# CMS DE-SynPUF Insurance Claims — mailroom-corpus source card

> `insurance_claim` · 600 rows (36.4% of the corpus) · 4 strata · train 536 / test 64
> · license **CMS public-use (fully synthetic)** · one of the five source
> corpora of
> [`Lucius-Morningstar/mailroom-corpus`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-corpus)

## Identity

| Field | Value |
|---|---|
| doc_type | `insurance_claim` |
| Rows | 600 (30% of v8's 2,000) — the CMS parent: v5 added 400, v6 appended 200; v8 adds sibling LOBs (property 200, auto 150) + full GT backfill |
| Splits | train 536 / test 64 (`md5(filename) % 10 == 0 → test`) |
| Strata | 4 `expected_subclass` values, exactly balanced: carrier 150, inpatient 150, outpatient 150, pde 150 |
| Provenance keys | `metadata.source_dataset = cms-de-synpuf-2008-2010-sample1` (`metadata.source` is empty — the synthetic render *is* the original) |
| Entered at | v5 (+400, KANBAN-084) and v6 (+200 re-render, KANBAN-105) |
| v8 conformance | HUB-028: intent/subject/keywords backfilled on all 600; claimed_amount recovered from doc text on 10 gap rows; 3 train-only outpatient `:2` date gaps documented source-N/A |
| License | CMS public-use terms; fully synthetic — no real PHI |

## Full context

The insurance-claims block is a **rendered evaluation corpus** derived from
the **CMS 2008–2010 Data Entrepreneurs' Synthetic Public Use File
(DE-SynPUF), Sample 1** — CMS's fully synthetic Medicare fee-for-service
dataset designed for data entrepreneurs to develop applications without
exposing beneficiary PHI. One row = one claim rendered as a plain-text
Medicare Summary Notice / pharmacy statement (EOB-style document), with a
ground-truth extraction contract aligned verbatim to the
[llm-mailroom](https://github.com/Exios66/llm-mailroom)
`InsuranceClaimExtraction` schema: every scalar GT value appears
**verbatim** in `doc_text` (machine-auditable; see `spot_check.csv` in the
producing repo [Exios66/claims-data-eda](https://github.com/Exios66/claims-data-eda)).

Provenance of the raw material: the 2010 Beneficiary Summary, Carrier
Claims (A/B) and Prescription Drug Events Sample-1 archives were no longer
hosted by CMS and were recovered from Internet Archive Wayback Machine
captures, with sha256 manifests recorded in `claims-data-eda`
(`data/raw/MANIFEST.json`). The v5 build rendered 400 EOBs (subtypes
inpatient / outpatient / carrier / pde); the v6 boost re-rendered +200 from
the same Sample 1 with the verbatim GT contract asserted at render time and
every existing `record_id` excluded — yielding the current balanced
150-per-subtype block. A stable `metadata.record_id` join key survives
across revisions.

## Source material

| Layer | Where |
|---|---|
| Original data | CMS DE-SynPUF 2008–2010, Sample 1 — <https://www.cms.gov/data-research/statistics-trends-and-reports/medicare-claims-synthetic-public-use-files> (Sample-1 archives recovered via Wayback Machine; sha256 manifests in the producing repo) |
| Rendering pipeline | [Exios66/claims-data-eda](https://github.com/Exios66/claims-data-eda) — EOB renderer + verbatim GT contract |
| Family mirror (v5 state) | [`Lucius-Morningstar/cms-desynpuf-insurance-claims`](https://huggingface.co/datasets/Lucius-Morningstar/cms-desynpuf-insurance-claims) |
| Form in mailroom-corpus | rendered EOB text in `doc_text`; `metadata.record_id` join key; GT fields on the `ground_truth` config |

## Attribution

- Source: **Centers for Medicare & Medicaid Services (CMS)**, 2008–2010
  Data Entrepreneurs' Synthetic Public Use File (DE-SynPUF). CMS grants
  public access under its public-use terms; the data are **fully
  synthetic** ("very limited inferential research utility" per CMS) and
  contain no real beneficiary information.
- No formal academic publication accompanies the DE-SynPUF; cite CMS as the
  upstream source and the mailroom-corpus dataset for the rendered
  documents.

## Purpose in mailroom-corpus

1. **doc_type supervision** — 600 gold `insurance_claim` labels: the
   largest class (36.4%), balancing the formal legal blocks with a
   transactional document type.
2. **Second-level classification** — four claim-claim pathway subtypes:
   `carrier` (physician/supplier A/B claims), `inpatient`, `outpatient`,
   `pde` (prescription drug events), exactly 150 each.
3. **Structured extraction GT** — the 13-field InsuranceClaimExtraction
   contract (`claim_number`, `policy_number`, `insurer`, `insured_party`,
   `claim_type`, `date_of_loss`, `date_filed`, `claimed_amount`,
   `adjuster`, `damages_description`, `coverage_determination`,
   `denial_reasons`, `supporting_documents`) rides the `ground_truth`
   config — the supervision surface for claim-extraction and
   coverage-classification heads.
4. **Deterministic length regime** — a tight, homogeneous short-text block
   (see stats below) useful for isolating format compliance from length
   effects in evaluation.
5. **Split-rule invariant testing** — the claims source keyed its own
   placement on `md5(record_id)`; mailroom-corpus re-keys every row on the
   family `md5(filename)` rule, so claims rows are an intentional
   reconciliation case (65/400 v5 rows changed placement vs the source
   repo; the `split` column here is authoritative).

## Subset statistics (v7 EDA)

- Text length (chars): mean 1,199 · p50 1,190 · p95 1,487 · max 1,562 —
  a uniform short-text block (σ ≈ 186 chars), the tightest of the five
  doc_types.
- GT field coverage (non-blank, v7 parquet): 9 of 13 fields 100% populated
  (`claim_number`, `policy_number`, `insurer`, `insured_party`,
  `claim_type`, `damages_description`, `coverage_determination`,
  `denial_reasons`, `supporting_documents`); `date_of_loss`/`date_filed`
  99.5% (597/600); `claimed_amount` 98.3% non-blank (571/600 numeric > 0).
- Loss→filing delay: median 0 days (425/597 same-day; max 35 days) — see
  `18_claim_dates_timeline.png`.
- Amounts and per-field detail:
  [`reports/tables/claim_amount_stats.csv`](../../reports/tables/claim_amount_stats.csv),
  [`reports/tables/claim_field_coverage.csv`](../../reports/tables/claim_field_coverage.csv)
  (note: that table reports *non-null* rates, so its `adjuster = 1.0`
  reflects empty strings, not populated values — see caveats).

## Caveats & limitations

- **PAID-only corpus**: `coverage_determination` is `"approved"` on 600/600
  rows and `denial_reasons` is the empty list on every row — SynPUF
  contains only adjudicated-paid FFS claims, so **no denial ground truth
  exists here**. "Coverage-classification supervision" in the EDA summary
  means the fields are fully populated and verbatim-grounded, not that the
  label space is balanced.
- **`adjuster` is empty on all 600 rows** in the v7 surface (no adjusters
  exist in SynPUF). Two prior statements conflict with this and should be
  read as historical: the source-repo card's "always null" (correct in
  substance) and the EDA summary's "adjuster 59%" (stale v6-rev2-era
  artifact). The `claim_field_coverage.csv` 1.0 for `adjuster` is a
  non-null metric artifact, not populated data.
- `insured_party` is a deterministic pseudonym derived from `DESYNPUF_ID` —
  safe by construction, but pseudonym-reversal against the public SynPUF
  file is trivial and pointless; treat as opaque.
- **Single line of business** (health/Medicare FFS), 2008–2010 era, US
  Medicare coding conventions (HCPCS, NDC, provider NPIs) — out of
  distribution for other LOBs and jurisdictions.
- CMS's own caveat applies in full: synthetic data with "very limited
  inferential research utility" — an **evaluation substrate, not
  epidemiology**.

## Cross-references

- Sibling cards: [Enron correspondence](enron-correspondence.md) (the other
  post-v3 append), [CUAD contracts](cuad-contracts.md)
- EDA figures: `16`–`19` (`reports/figures/`) — claim amounts, coverage,
  dates timeline, subtype fill
- Rendering repo: <https://github.com/Exios66/claims-data-eda> ·
  Family mirror: <https://huggingface.co/datasets/Lucius-Morningstar/cms-desynpuf-insurance-claims>
