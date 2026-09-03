# CUAD Contracts — mailroom-corpus source card

> `contract` · 509 rows (30.8% of the corpus) · 26 strata · train 455 / test 54
> · license **CC BY 4.0** · one of the five source corpora of
> [`Lucius-Morningstar/mailroom-corpus`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-corpus)

## Identity

| Field | Value |
|---|---|
| doc_type | `contract` |
| Rows | 509 (30.8% of 1,650) |
| Splits | train 455 / test 54 (`md5(filename) % 10 == 0 → test`) |
| Strata | 26 `expected_subclass` values (CUAD's 28-group contract taxonomy; 26 groups appear in the merged surface) |
| Largest strata | Maintenance 34, License_Agreements 33, Distributor 32, Strategic Alliance 32, Sponsorship 31 |
| Smallest strata | Non_Compete_Non_Solicit 3, Joint Venture 9, Affiliate_Agreements 10 |
| Provenance keys | `metadata.source = cuad_v1`, `metadata.source_dataset = mailroom-cuad-contracts-full` |
| Entered at | v1–v3 (legacy rows) — the founding corpus of mailroom-corpus |
| License | CC BY 4.0 (The Atticus Project, Inc.) |

## Full context

**CUAD v1** (Contract Understanding Atticus Dataset) is an expert-annotated
corpus of 510 US commercial contracts curated by The Atticus Project. Each
contract is annotated for 41 clause types — covering parties, dates,
governing law, liability caps, license grants, non-competes and more — with
over 25,000 answer spans total, to support legal contract review by NLP
models. It is the standard benchmark for contract-understanding tasks and
was published at NeurIPS 2021 (Datasets & Benchmarks).

In mailroom-corpus the corpus is the **contract backbone**: every row keeps
CUAD's own commercial-contract grouping as the second-level gold label
(`expected_subclass` — e.g. `Co_Branding`, `Distributor`, `License_Agreements`),
plus the full official clause annotation set on the `ground_truth` config as
`cuad_clause_labels`. The upstream corpus was created with dozens of legal
experts from The Atticus Project and carries over 13,000 annotations across
41 clause types (per the official paper); the 509-row merged subset alone
contributes 13,753 verified answer spans to the `ground_truth` config. The
509 rows are a byte-verified export of the
Braintrust CUAD mirror curated as
[`mailroom-cuad-contracts-full`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-cuad-contracts-full),
so the docclass surface inherits a sha256-checkable chain back to the
original CUAD distribution. Rows also carry clause counts and
applicable-categories metadata, and every row's `metadata.original_file`
points at its upstream CUAD PDF path (source PDFs ride along under
`files/contract/...` in the Hub tree when staged).

## Source material

| Layer | Where |
|---|---|
| Original download | <https://www.atticusprojectai.org/cuad> |
| Mirror repo | <https://github.com/TheAtticusProject/cuad> |
| HF mirror of the original | [`theatticusproject/cuad`](https://huggingface.co/datasets/theatticusproject/cuad) (CC BY 4.0) |
| Family ingestion mirror | [`Lucius-Morningstar/mailroom-cuad-contracts-full`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-cuad-contracts-full) (byte-verified export used for the merge) |
| Form in mailroom-corpus | full contract text in `doc_text`; PDF basename in `filename`; upstream PDF path in `metadata.original_file` / `metadata.pdf_path` |

## Attribution

The CUAD portion is released **CC BY 4.0** by The Atticus Project, Inc. —
attribution is required when redistributing or building on these rows:

> Hendrycks, Dan, Collin Burns, Anya Chen, and Spencer Ball. "CUAD: An
> Expert-Annotated NLP Dataset for Legal Contract Review." *NeurIPS Datasets
> and Benchmarks Track*, 2021. <https://arxiv.org/abs/2103.06268>

```bibtex
@inproceedings{hendrycks2021cuad,
  title     = {{CUAD}: An Expert-Annotated {NLP} Dataset for Legal Contract Review},
  author    = {Hendrycks, Dan and Burns, Collin and Chen, Anya and Ball, Spencer},
  booktitle = {NeurIPS Datasets and Benchmarks Track},
  year      = {2021}
}
```

## Purpose in mailroom-corpus

1. **doc_type supervision** — 509 gold `contract` labels (30.8% of the
   corpus), the second-largest class after insurance_claim.
2. **Second-level classification** — `expected_subclass` over 26 commercial
   contract groups gives a fine-grained contract-type head.
3. **Clause-level extraction GT** — `cuad_clause_labels` on the
   `ground_truth` config carries the official CUAD annotation set as compact
   JSON (clause name → `[{text, start}]`): **13,753/13,753 answer spans
   verified at exact char offsets** against the stored `doc_text` (100%
   match, audited in the P1 integrity pass). This is the scoring substrate
   for entity-extraction evaluation — it never appears in the blind config.
4. **Realistic mid-length legal text** — contracts anchor the corpus's
   mid-length tail (median ~33k chars ≈ 8k tokens; 95th percentile ~161k
   chars ≈ 40k tokens), between short correspondence/claims and merger
   agreements.

## Subset statistics (v7 EDA)

- Text length (chars): mean 52,176 · p50 32,861 · p95 161,402 · max 338,211.
- Most-annotated clauses: Document Name (509 contracts, 100%), Parties
  (508, mean 5.0 spans), Agreement Date (469), Governing Law (436),
  Expiration Date (412), Effective Date (389) — see
  [`reports/tables/cuad_clause_stats.csv`](../../reports/tables/cuad_clause_stats.csv).
- 14 of the 41 clause types appear in fewer than 15% of contracts (long-tail
  annotation density).
- Every contract carries annotations (509/509 joined, zero orphans).

## Caveats & limitations

- US commercial contracts, English only; the 28-group taxonomy is CUAD's
  own — 26 groups appear here, so `expected_subclass` is not exhaustive of
  the upstream taxonomy.
- 6 contract subclasses have 12 or fewer rows, and 8 of the 26 (Endorsement,
  Outsourcing, Promotion, Reseller, Consulting Agreements,
  Agency Agreements, Joint Venture _ Filing, Non_Compete_Non_Solicit) drew
  zero test rows under the family split rule — see
  [`reports/tables/strata_counts.csv`](../../reports/tables/strata_counts.csv).
- CUAD's annotations were produced by trained annotators with expert review,
  but clause presence is inherently subjective at the margins; treat
  `cuad_clause_labels` as gold for *span-matching against CUAD's own
  convention*, not as universal legal truth.

## Cross-references

- Sibling cards: [MAUD merger agreements](maud-merger-agreements.md),
  [S-1 corporate records](s1-corporate-records.md)
- EDA figures: `08`–`12` (`reports/figures/`) — CUAD clause presence, span
  density and co-occurrence
- Upstream dataset card:
  <https://huggingface.co/datasets/theatticusproject/cuad>
