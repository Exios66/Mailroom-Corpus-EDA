# S-1 Corporate Records — mailroom-dataset source card

> `corporate_record` · 450 rows (13.6% of the corpus) · 10 strata · train 403 / test 47
> · license **public domain (US government works)** · one of the five source
> corpora of
> [`Lucius-Morningstar/mailroom-dataset`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset)

## Identity

| Field | Value |
|---|---|
| doc_type | `corporate_record` |
| Rows | 450 (13.6% of 3,302): 39 S-1 legacy + 411 EDGAR expansion |
| Splits | train 403 / test 47 (`md5(filename) % 10 == 0 → test`) |
| Strata | 10 `expected_subclass` values: charter_amendment 80, articles_of_incorporation 62, officer_certificate 61, indenture 57, subsidiary_list 53, rights_instrument 46, board_resolution 32, bylaws 28, powers_of_attorney 28, other 3 |
| Provenance keys | `metadata.source = edgar_s1`, `metadata.source_dataset = data/s1_corporate_records/corporate-records.jsonl` |
| Entered at | v1–v3 (legacy rows) — founding corpus |
| License | US public domain (SEC EDGAR filings are US government works) |

## Full context

The corporate-record block comprises exhibits extracted from **SEC EDGAR
S-1 registration statements** — the filings companies submit before an IPO.
Exhibit documents (articles of incorporation, rights instruments, bylaws,
powers of attorney) were pulled live from EDGAR with their filer metadata
retained: 450 rows across **333 unique CIKs**, each carrying
`metadata.exhibit_type`, `metadata.exhibit_description`,
`metadata.exhibit_url`, `metadata.filer`, `metadata.accession` and
`metadata.filing_date` — a full audit trail back to the EDGAR source
document. Every row's `metadata.original_file` points at the upstream .htm
exhibit original.

In mailroom-dataset this is the **governance-document block**: it
deliberately introduces a heavily imbalanced class so that classification
systems are evaluated under realistic mailroom conditions — high-stakes
document types that must not be confused with the dominant contracts and
correspondence. (The v8 corpus expanded the block from 39 to 450 rows with
the EDGAR exhibit expansion; merger_agreement at 152 rows is now the
smallest class.)

## Source material

| Layer | Where |
|---|---|
| Original download | SEC EDGAR — <https://www.sec.gov/edgar.shtml> (public filings; exhibit URLs preserved per row in `metadata.exhibit_url`) |
| Family mirror | [`Lucius-Morningstar/mailroom-s1-corporate-records`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-s1-corporate-records) |
| Form in mailroom-dataset | exhibit text in `doc_text`; upstream .htm exhibit path in `metadata.original_file`; filer/accession metadata retained |

## Attribution

- Source: **US Securities and Exchange Commission, EDGAR** public filings.
  Works of the US federal government are in the public domain; the SEC
  requests that EDGAR be cited as the source and that filings not be
  presented as official SEC records. Re-verify filings against EDGAR before
  any commercial redistribution.
- No formal academic publication accompanies this block; cite the
  mailroom-dataset and reference EDGAR as the upstream source.

## Purpose in mailroom-dataset

1. **doc_type supervision under imbalance** — 450 gold
   `corporate_record` labels (13.6%): the corpus's governance-document
   imbalance stress test (max/min type ratio 7.24×, with
   `corporate_record/other` the 3-row minimum).
2. **Second-level classification** — ten governance-document subclasses
   (charter_amendment, articles_of_incorporation, officer_certificate,
   indenture, subsidiary_list, rights_instrument, board_resolution, bylaws,
   powers_of_attorney, other) spanning the corporate-record taxonomy the
   llm-mailroom triage target distinguishes.
3. **Filing provenance modeling** — filer/accession/exhibit metadata makes
   this the only block with a live pointer back to the authoritative
   regulatory source per row, useful for provenance-aware evaluation.
4. **Realistic mailroom mix** — governance records round out the document
   variety an agentic mailroom triage system encounters (contracts, merger
   agreements, correspondence, claims, and now corporate filings).

## Subset statistics (v7 EDA)

- Text length (chars): mean 44,899 · p50 35,823 · p95 91,211 · max 93,296 —
  mid-length documents, all comfortably within 32k-token contexts.
- Strata skew: articles_of_incorporation (20) and rights_instrument (14)
  hold 87% of the rows; bylaws and powers_of_attorney have 2 rows each and
  `other` exactly 1.
- Split exposure is minimal: a single test row (from
  articles_of_incorporation); bylaws, powers_of_attorney, rights_instrument
  and other drew zero test rows — see
  [`reports/tables/strata_counts.csv`](../../reports/tables/strata_counts.csv)
  and the minority-strata report
  [`reports/tables/minority_strata_report.csv`](../../reports/tables/minority_strata_report.csv).

## Caveats & limitations

- **n = 39.** Any per-class metric on this block is high-variance; treat it
  as a routing-prior and imbalance stress test, not a statistically robust
  class. The EDA's ML-readiness notes suggest considering a rollup (e.g.
  bylaws / powers_of_attorney → corporate_record `other`) or a per-stratum
  test floor in a future revision.
- One filer (one S-1) can contribute multiple exhibits — the 11 CIKs mean
  filer-level leakage between train and test is possible under the
  filename-hash split.
- EDGAR exhibits are .htm-derived text; rendering artifacts (tables, entity
  escapes) may survive in `doc_text`.
- Public-domain status attaches to the US government works; verify the
  terms of any downstream redistribution channel that mixes these rows with
  the research-use Enron block.

## Cross-references

- Sibling cards: [CUAD contracts](cuad-contracts.md),
  [MAUD merger agreements](maud-merger-agreements.md)
- EDA figures: `23`–`25` (`reports/figures/`) — treemap, strata ratios,
  minority strata; `26`–`28` for temporal/provenance views
- Upstream: <https://www.sec.gov/edgar.shtml>
