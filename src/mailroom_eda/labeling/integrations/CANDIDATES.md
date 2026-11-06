# Candidate document types

Document types that could join the core five (`contract`, `merger_agreement`,
`corporate_record`, `correspondence`, `insurance_claim`). Each entry gives the niche it
fills, the public sources with links and licences, and the overlap risk with the core
five. Add a class only after it passes the criteria in `GUIDE_new_doc_type.md` step 1.

**Licences.** Entries marked *verified* were checked against the source's own record on
2026-11-06. Everything else says *check*: read the dataset's licence file before planning
on it. Data under a non-commercial licence can calibrate labels but is not published as
rows unless the whole release accepts those terms.

## Summary

| # | class | niche the core five miss | main source | redistributable rows? | expected rows | overlap risk |
|---|---|---|---|---|---|---|
| 1 | `commercial_lease` | landlord and tenant intake | SEC EDGAR lease exhibits | yes (public filings) | ~300 | low: only one current contract reads like a lease |
| 2 | `regulatory_notice` | agency enforcement and warning letters | FDA warning letters; SEC comment letters | yes (US government works / public filings) | 300+ | medium: reads like `correspondence` (letter) |
| 3 | `litigation_filing` | served complaints, summonses and court orders | Multi-LexSum; CourtListener RECAP | yes, with attribution (check per document) | 300+ | medium: `correspondence` demand letters, `attorney_demand` |
| 4 | `nda` (or a `contract` subclass) | inbound confidentiality agreements | ContractNLI; Kleister-NDA | ContractNLI yes; Kleister check | 600 | high: an NDA is a contract; likely a subclass, not a class |
| 5 | `charity_or_annual_report` | financial-report filings | Kleister-Charity | check | 2,700 | medium: `corporate_record` |
| 6 | `invoice_receipt` | accounts-payable intake | CORD; SROIE | CORD yes; SROIE check | 1,000 | low, but out of scope for a legal mailroom |

**Recommended order:** commercial lease first (worked example ready in
`examples/commercial_lease/`); then regulatory notice and litigation filing, which are the
largest real gaps in legal intake; then NDA, as a `contract` subclass rather than a class.
Invoices and receipts belong in an accounts-payable mailroom, not a legal one; add them
only on request.

## 1. `commercial_lease`

- **Niche:** leases, amendments, estoppel certificates, SNDAs and landlord notices. Real
  legal intake with its own fields (premises, rent, term, renewal options, security
  deposit, assignment rights) that the contract specialist does not extract.
- **Sources:**
  - SEC EDGAR EX-10 exhibits whose description names a lease (public filings, the same
    path as the current EX-10 contracts).
  - ALeaseBERT, Leivaditi, Rossi and Kanoulas, *A Benchmark for Lease Contract Review*
    (2020): 179 commercial leases annotated for entities and red flags.
    Paper: https://arxiv.org/abs/2010.10386v1 (PDF https://arxiv.org/pdf/2010.10386v1.pdf).
    Data: https://dare.uva.nl/id/1e78cacc-e2b9-49f9-90cf-ccbec54a41b5
    (DOI 10.21942/uva.19732993.v1). **CC BY-NC 4.0, verified** (the paper is CC BY-NC-SA;
    that covers the paper, not the data). Calibration only.
- **Fields:** see `examples/commercial_lease/fields.yaml` (18 fields, 7 subclasses).
- **Background reading** on what lease abstraction extracts:
  https://ubiai.tools/streamlining-lease-abstraction-with-ai/,
  https://www.v7labs.com/agents/ai-real-estate-lease-abstraction-analysis-agent. These are
  commercial services; do not scrape their documents.

## 2. `regulatory_notice`

- **Niche:** warning letters, notices of violation, deficiency and comment letters from a
  regulator. They carry a deadline and a required response that the correspondence
  specialist does not model (agency, cited regulation, violations, response due date,
  corrective actions).
- **Sources:**
  - FDA warning letters: https://catalog.data.gov/dataset/warning-letters. US government
    works, so public domain in the US (*check*: some mirrors tag the catalog entry ODbL).
  - SEC staff comment letters and responses (EDGAR form types `UPLOAD` and `CORRESP`):
    public filings.
- **Subclasses (proposed):** warning_letter, notice_of_violation, comment_letter,
  deficiency_letter, consent_order, other.
- **Overlap:** a regulator's letter is a `correspondence` letter today. The class earns
  its place only if the specialist fields are distinct; check routing ambiguity with
  fixtures.

## 3. `litigation_filing`

- **Niche:** complaints, summonses, subpoenas and court orders served on the organisation.
  Fields: court, case number, parties and roles, filing date, causes of action, relief
  sought, response deadline.
- **Sources:**
  - Multi-LexSum: https://huggingface.co/datasets/allenai/multi_lexsum. Civil-rights
    case documents with expert summaries. ODC-By per the current dataset card (*check*
    against the card at the pinned revision; the underlying documents come from the Civil
    Rights Litigation Clearinghouse).
  - CourtListener RECAP archive (public court records from PACER).
- **Overlap:** demand letters and `attorney_demand` correspondence threaten litigation; a
  filing is the court document itself. Personal data: court records name private
  individuals, so they need the same review the Enron emails had.

## 4. `nda` (recommended as a `contract` subclass)

- **Niche:** inbound confidentiality agreements, the most common contract in a legal
  inbox, and absent from CUAD's families.
- **Sources:**
  - ContractNLI: https://stanfordnlp.github.io/contract-nli. 607 NDAs with expert
    hypothesis labels and evidence spans. **CC BY 4.0, verified.**
  - Kleister-NDA (Stanisławek et al., *Kleister: Key Information Extraction Datasets
    Involving Long Documents with Complex Layouts*): https://arxiv.org/pdf/2003.02356.pdf.
    EDGAR NDAs labeled with party, jurisdiction, effective date and term (*check* the
    licence).
- **Overlap:** high. An NDA is a contract, so it most likely belongs as a new contract
  subclass with its own `family_fields` (term, confidentiality period, permitted
  disclosures, mutual or one-way) rather than a sixth class. That is change kind B.

## 5. `charity_or_annual_report`

- **Niche:** annual reports and financial statements filed with a regulator.
- **Sources:** Kleister-Charity: https://github.com/applicaai/kleister-charity. UK Charity
  Commission reports labeled with charity name, number, report date, income and spending
  (*check* the licence; the source reports are public records).
- **Overlap:** medium with `corporate_record` (annual reports are corporate filings).
  Consider a `corporate_record` subclass first.

## 6. `invoice_receipt` (out of scope unless requested)

- **Niche:** accounts-payable intake: vendor, invoice number, dates, line items, totals.
- **Sources:** CORD: https://github.com/clovaai/cord (receipts, **CC BY 4.0, verified**);
  SROIE (scanned receipts, *check* the licence).
- **Overlap:** low, but the documents are images with OCR text and short, which the
  pipeline's text-first specialists are not built for.
