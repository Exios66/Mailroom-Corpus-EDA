# MAUD Merger Agreements — mailroom-corpus source card

> `merger_agreement` · 152 rows (9.2% of the corpus) · 5 strata · train 135 / test 17
> · license **CC BY 4.0** · one of the five source corpora of
> [`Lucius-Morningstar/mailroom-corpus`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-corpus)

## Identity

| Field | Value |
|---|---|
| doc_type | `merger_agreement` |
| Rows | 152 (9.2% of 1,650) |
| Splits | train 135 / test 17 (`md5(filename) % 10 == 0 → test`) |
| Strata | 5 `expected_subclass` values by consideration type: all_cash 57, other 57, all_stock 24, mixed_cash_stock 13, mixed_cash_stock_election 1 |
| Provenance keys | `metadata.source = maud_v1`, `metadata.source_dataset = data/maud/contracts.jsonl` |
| Entered at | v1–v3 (legacy rows) — founding corpus |
| License | CC BY 4.0 (The Atticus Project, Inc.; Wang et al. 2023) |

## Full context

**MAUD v1** (Merger Agreement Understanding Dataset) is an expert-annotated
corpus of 152 public-company merger agreements from The Atticus Project,
carrying **47,000+ expert labels** across 22 classification tasks organized
into four categories — Conditions to Closing, Covenants, No-Shop / FTR, and
Definitions. The annotation schema operationalizes the ABA 2021 Public
Target Deal Points Study, making MAUD the reference benchmark for
merger-agreement legal reasoning.

In mailroom-corpus the corpus serves as the **long-document stress class**.
Agreement texts were streamed from the Zenodo v1 corpus export; the
consideration-type annotation informs `expected_subclass`, and the upstream
label bookkeeping rides in `metadata.maud_label_count` (individual answer
labels per agreement, 70–244) and `metadata.maud_categories`. The full MAUD
gold labels joined per contract id as `maud_clause_labels` on the
`ground_truth` config (category / answer / valid_classes / label_idx per
task). Every row's `metadata.original_file` points at its upstream
`contract_N.txt` text (MAUD ships no PDFs).

## Source material

| Layer | Where |
|---|---|
| Original download | <https://www.atticusprojectai.org/maud/> |
| Corpus export | Zenodo record [7500064](https://zenodo.org/records/7500064) (CC BY 4.0) |
| Family mirror | [`Lucius-Morningstar/mailroom-maud-contracts`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-maud-contracts) |
| Form in mailroom-corpus | full agreement text in `doc_text`; upstream `contract_N.txt` path in `metadata.original_file` |

## Attribution

The MAUD portion is released **CC BY 4.0** by The Atticus Project, Inc. —
attribution is required when redistributing or building on these rows:

> Wang, Steven H., Antoine Scardigli, Leonard Tang, Wei Chen, Dimitry
> Levkin, Anya Chen, Spencer Ball, Thomas Woodside, et al. "MAUD: An
> Expert-Annotated Legal NLP Dataset for Merger Agreement Understanding."
> *EMNLP 2023*. <https://arxiv.org/abs/2301.00876>

```bibtex
@inproceedings{wang2023maud,
  title     = {{MAUD}: An Expert-Annotated Legal {NLP} Dataset for Merger Agreement Understanding},
  author    = {Wang, Steven H. and Scardigli, Antoine and Tang, Leonard and Chen, Wei and Levkin, Dimitry and Chen, Anya and Ball, Spencer and Woodside, Thomas and others},
  booktitle = {Proceedings of the 2023 Conference on Empirical Methods in Natural Language Processing (EMNLP)},
  year      = {2023}
}
```

Zenodo DOI: `10.5281/zenodo.7500064`.

## Purpose in mailroom-corpus

1. **doc_type supervision** — 152 gold `merger_agreement` labels; the
   smallest large-document class, deliberately kept to exercise long-context
   behavior.
2. **Second-level classification** — consideration-type `expected_subclass`
   (all-cash / all-stock / mixed / election / other structures).
3. **Multi-task legal-reasoning GT** — `maud_clause_labels` carries MAUD's
   gold labels across its 22 tasks (category, answer, valid_classes,
   label_idx), joined 152/152 via contract id with metadata consistency
   asserted (`maud_label_count == sum(maud_categories) == upstream count`
   on all 152 rows). Scoring substrate for extraction/eval — never in the
   blind config.
4. **Context-window stress test** — merger agreements are by far the
   longest documents in the corpus and the reason the token-budget coverage
   table has a 131k column.

## Subset statistics (v7 EDA)

- Text length (chars): mean 356,194 · p50 338,188 · p95 497,718 · max
  1,008,540 — roughly **89k tokens mean / ~252k tokens max** at the
  chars/4 heuristic: **exceeds common 32k/65k contexts**; requires 131k+
  context or chunking.
- Task coverage: 3 of the 22 tasks are annotated on every agreement;
  coverage ranges 11–152 documents; mean 16.4 tasks answered per agreement
  (range 11–20) — see
  [`reports/tables/maud_task_stats.csv`](../../reports/tables/maud_task_stats.csv).
- Task-category mix: Deal Protection and Related Provisions dominates
  (no-shop, FTR triggers, fiduciary exceptions); Conditions to Closing
  carries the 100%-coverage Accuracy-of-Target-R&W task.
- Largest stratum is tied: all_cash and other at 57 rows each; the
  mixed_cash_stock_election stratum has a single row (the corpus minimum).

## Caveats & limitations

- 152 agreements is a small class — per-stratum test shares deviate from
  the nominal 10% (17 test rows, 11.2%), and the single
  mixed_cash_stock_election row sits in train with zero test representation.
- Public-target deals only (per the ABA Public Target Deal Points Study
  scope); private-target structures are out of distribution.
- Mean length ~89k tokens means most consumer model contexts cannot ingest
  these rows whole — plan for 131k+ context, retrieval, or chunked scoring;
  see `ml-readiness` recommendations in
  [`reports/SUMMARY_REPORT.md`](../../reports/SUMMARY_REPORT.md).
- MAUD labels are expert but sparse at task level (11–152 docs per task);
  tasks below ~25% coverage are weak supervision targets.

## Cross-references

- Sibling cards: [CUAD contracts](cuad-contracts.md),
  [S-1 corporate records](s1-corporate-records.md)
- EDA figures: `13`–`15` (`reports/figures/`) — MAUD task frequency, answer
  distributions, category coverage; `04`–`07` for token-budget fit
- Upstream: <https://www.atticusprojectai.org/maud/> ·
  [Zenodo 7500064](https://zenodo.org/records/7500064)
