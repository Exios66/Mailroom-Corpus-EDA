---
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
- arxiv:2103.06268
- arxiv:2301.00876
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
> labels are JSON objects (`{}` = no annotations on the 91 SEC EDGAR EX-10
> contracts). The single documented allowance is `adjuster`: the CMS
> DE-SynPUF, GNOTHEIA property, and INSURBIAS subclasses have no adjuster in
> their sources, so those 950 rows leave `adjuster` empty (BDR auto rows
> carry their pseudonyms). Three outpatient `:2` notices with service dates
> literally "N/A" in the source carry the verbatim `N/A` marker.

## Composition (v1 = v8 + expansions)

| Document class | Rows | Share | Δ vs v8 |
|---|---:|---:|---:|
| `contract` | 600 | 18.2% | +91 |
| `corporate_record` | 450 | 13.6% | +411 |
| `correspondence` | 1,000 | 30.3% | +650 |
| `insurance_claim` | 1,100 | 33.3% | +150 |
| `merger_agreement` | 152 | 4.6% | +0 |

Expansion draws (deterministic, sha256-within-stratum):

| contract | +91 |
| corporate_record | +411 |
| correspondence | +650 |
| insurance_claim | +150 |

## Configs

| Config | Rows | Contents |
|---|---:|---|
| `default` (blind) | 3,302 | filename, doc_text, prompt, metadata — **zero labels by construction** |
| `ground_truth` | 3,302 | labels (27-key schema), identity/hashes, evaluation contract, matter/group |
| `bundles` | 50 | §14 synthetic bundle families over real anchors (flagged `synthetic_constructed`) |
| `streams` | 62 | §27–29 interleaved ingress stream (`RUN-SIM-001`) with distractors |
| `fixtures` | 32 | §68–§72A recovery/adversarial fixtures (calibration quartet, arbiter, failure stages) |

## Sources & licensing

| Class | Source | License |
|---|---|---|
| contract | CUAD v1 (509) + SEC EDGAR EX-10 (91) | CC BY 4.0 / US public domain |
| merger_agreement | MAUD v1 (Zenodo 7500064) | CC BY 4.0 |
| corporate_record | SEC EDGAR S-1/8-K exhibits | US public domain |
| correspondence | Enron deduplicated corpus (247,523-row pool) | research-use (`other`); real named-individual PII — conservative handling |
| insurance_claim | CMS DE-SynPUF + GNOTHEIA + BDR + INSURBIAS narratives | Apache-2.0 / MIT / CC BY 4.0 |

## Family & related repositories

The [**Mailroom Corpus Family**](https://huggingface.co/collections/Lucius-Morningstar/mailroom-corpus-family-6aa715cce29d415b0db92473)
collection catalogs this corpus, its frozen lineage parent, and the upstream
source corpora:

| Repo | Role |
|:---|:---|
| [`mailroom-dataset`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset) | this corpus — v1 (canonically v9), 3,302 rows |
| [`mailroom-corpus`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-corpus) | frozen v8 lineage parent (2,000 rows, `eafe1ab4`) |
| [`enron-correspondence-dedup`](https://huggingface.co/datasets/Lucius-Morningstar/enron-correspondence-dedup) | correspondence source (247,523-row dedup pool) |
| [`mailroom-cuad-contracts-full`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-cuad-contracts-full) | CUAD contract source (byte-verified mirror) |
| [`cms-desynpuf-insurance-claims`](https://huggingface.co/datasets/Lucius-Morningstar/cms-desynpuf-insurance-claims) | CMS DE-SynPUF insurance source (rendered EOBs) |

The EDA pipeline and centralized upload helpers live in the
[`Exios66/Mailroom-Corpus-EDA`](https://github.com/Exios66/Mailroom-Corpus-EDA)
repository (per-source dataset cards under `docs/dataset-cards/`; `run_all.py`
reproduces every number on this card).

Usage:

```python
from datasets import load_dataset
blind = load_dataset("Lucius-Morningstar/mailroom-dataset", "default")
gt = load_dataset("Lucius-Morningstar/mailroom-dataset", "ground_truth")
# join on "filename" to pair documents with their labels
```

## Citation

If you use `mailroom-dataset` in published work, please cite the corpus and
its primary component corpora:

```bibtex
@misc{mailroom_dataset,
  title        = {Mailroom Dataset v1: An Evaluation and Ingress Simulation Corpus for Legal Document Triage},
  author       = {Morningstar, Lucius},
  year         = {2026},
  howpublished = {Hugging Face: \url{https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset}},
  note         = {Canonically v9 of the mailroom corpus family; lineage parent mailroom-corpus v8 (2,000 rows, frozen).}
}
@inproceedings{wang2021cuad,
  title     = {CUAD: An Expert-Annotated NLP Dataset for Legal Contract Review},
  author    = {Hendrycks, Dan and Burns, Collin and Chen, Anya and Ball, Spencer},
  booktitle = {NeurIPS Datasets and Benchmarks},
  year      = {2021}
}
@inproceedings{holzenberger2023maud,
  title     = {MAUD: An Expert-Annotated Merger Agreement Understanding Dataset},
  author    = {Holzenberger, Nils and Blair-Stanek, Andrew and Van Durme, Benjamin},
  booktitle = {Findings of ACL},
  year      = {2023}
}
@inproceedings{klimt2004enron,
  title     = {The Enron Corpus: A New Dataset for Email Classification Research},
  author    = {Klimt, Bryan and Yang, Yiming},
  booktitle = {ECML},
  year      = {2004}
}
```