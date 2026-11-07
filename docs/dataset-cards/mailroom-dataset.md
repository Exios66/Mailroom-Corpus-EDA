---
pretty_name: Mailroom Dataset
license: other
license_name: mixed-per-source
license_link: https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset#sources-and-licenses
language:
- en
task_categories:
- text-classification
- token-classification
task_ids:
- multi-class-classification
- named-entity-recognition
annotations_creators:
- expert-generated
- machine-generated
language_creators:
- found
multilinguality:
- monolingual
source_datasets:
- Lucius-Morningstar/enron-correspondence-dedup
- theatticusproject/cuad
- bdr-ai-org/insurance-motor-claims-decision-v1
- feihuangfh/INSURBIAS
- gratex/GNOTHEIA-synthetic-insurance-dataset
size_categories:
- 1K<n<10K
tags:
- legal
- contracts
- correspondence
- insurance
- corporate-records
- evaluation
- arxiv:2103.06268
- arxiv:2301.00876
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

# Mailroom Dataset

3,302 real and synthetic business documents across five classes (contracts, merger agreements, correspondence, insurance claims and corporate records) for **evaluating** document triage: classification, routing, field extraction, grouping and recovery. Documents arrive *blind*; ground truth is kept in a separate config so a model under test never sees a label.

**Current release: v9.3** (Hub tag [`v9.3`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset/tree/v9.3)). Pin a tag in evaluations; `main` moves.

| class | train | test | total | subclasses |
|---|---:|---:|---:|---:|
| `contract` | 540 | 60 | 600 | 26 |
| `corporate_record` | 403 | 47 | 450 | 10 |
| `correspondence` | 915 | 85 | 1,000 | 8 |
| `insurance_claim` | 986 | 114 | 1,100 | 6 |
| `merger_agreement` | 135 | 17 | 152 | 5 |
| **all** | **2,979** | **323** | **3,302** | **55** |

## Quick start

```python
from datasets import load_dataset

repo = "Lucius-Morningstar/mailroom-dataset"
docs = load_dataset(repo, "default", revision="v9.3")       # documents; keep filename for the join
gt   = load_dataset(repo, "ground_truth", revision="v9.3")  # labels, identity, eval contract

# what the model sees: text and prompt only (filename and metadata can reveal answers)
model_input = docs["test"].select_columns(["doc_text", "prompt"])

# pair documents with labels on `filename`; gt_fields is a JSON string
import json
row = gt["test"][0]
labels = json.loads(row["gt_fields"])
presence = json.loads(labels["gt_presence"])                # per-field presence code
```

## Configs

| config | rows | contents |
|---|---:|---|
| `default` | 3,302 | `filename`, `doc_text`, `prompt`, `metadata`. No label columns, but see the metadata caveat under [Bias and limitations](#bias-and-limitations). |
| `ground_truth` | 3,302 | class and subclass, `gt_fields` (JSON), identity and hashes, the evaluation contract, matter grouping and provenance |
| `bundles` | 50 | synthetic multi-document families built over real anchor documents (flagged `synthetic_constructed`) |
| `streams` | 62 | one interleaved ingress stream (`RUN-SIM-001`) over the bundle matters, with 12 unrelated distractors |
| `fixtures` | 32 | recovery and adversarial cases: calibration quartet, arbiter scenarios, failure stages |

<details><summary><code>ground_truth</code> columns (35)</summary>

| group | columns |
|---|---|
| labels | `expected`, `expected_subclass`, `gt_fields` |
| identity | `document_id`, `content_sha256`, `normalized_text_sha256`, `source_corpus`, `source_document_id`, `source_filename`, `source_revision` |
| evaluation contract | `expected_specialist`, `expected_stage`, `review_expected`, `review_reason`, `retry_expected`, `expected_post_retry_state` |
| provenance | `annotation_source`, `annotation_method`, `annotation_model`, `annotation_prompt_version`, `annotation_confidence`, `annotation_reviewer`, `annotation_timestamp` |
| matter and grouping | `matter_id`, `matter_construction`, `group_id`, `group_role`, `thread_position`, `thread_size`, `thread_evidence`, `relationships`, `related_document_ids` |
| other | `filename`, `prompt`, `split` |

`gt_fields` holds the class's label fields plus `gt_presence`, `token_estimate`, `context_window_band` (`<=4k`, `<=16k`, `<=32k`, `>32k`), `clause_count` and `maud_label_count`. CUAD and MAUD clause annotations are in `cuad_clause_labels` and `maud_clause_labels`.

On the 350 GNOTHEIA and BDR insurance rows, six fields hold the string `"nan"` as an absence placeholder, not a value: `content_topic`, `label_evidence`, `sentiment_evidence`, `sentiment_label`, `sentiment_score` and `topic_evidence` (2,100 cells). Treat them as absent, and use `gt_presence` where it has a code. The `"N/A"` dates on 3 CMS outpatient claims are the source's own marker; keep them.

</details>

### Presence codes

Every label-bearing field in `gt_presence` carries one code, so a blank value is never ambiguous:

| code | meaning | cells |
|---|---|---:|
| `populated` | a real expected value | 23,453 |
| `schema_documented_absence` | the field applies, but this document has none (e.g. `denial_reasons` on an approved claim) | 2,020 |
| `pending_annotation` | not labeled yet (the 91 SEC EDGAR EX-10 contracts' CUAD clauses) | 91 |
| `not_applicable` | the class never carries this field | 41,758 |

A field missing from `gt_presence` is **unlabeled**. Scorers should exclude it from both sides, so neither a filled nor an empty prediction counts. On corporate records, the per-subclass entity fields from `llm-dojo-scoring`'s corporate-record schema stay unlabeled until they are annotated.

## Splits

A row is in `test` when `md5(filename) % 10 == 0` (≈10%). The rule is stable across releases. The splits are an evaluation partition for reproducible sampling, not a training/validation design: this is not a training corpus.

## Sources and licenses

| class | source | rows | license |
|---|---|---:|---|
| contract | CUAD v1 ([`theatticusproject/cuad`](https://huggingface.co/datasets/theatticusproject/cuad)) | 509 | CC BY 4.0 |
| contract | SEC EDGAR EX-10 exhibits | 91 | public SEC filings; company-authored, not US-government works (see below) |
| merger_agreement | MAUD v1 (Zenodo 7500064) | 152 | CC BY 4.0 |
| corporate_record | SEC EDGAR S-1 / 8-K exhibits | 450 | public SEC filings; company-authored, not US-government works (see below) |
| correspondence | Enron, deduplicated ([`enron-correspondence-dedup`](https://huggingface.co/datasets/Lucius-Morningstar/enron-correspondence-dedup)) | 1,000 | research use; contains real names |
| insurance_claim | CMS DE-SynPUF (synthetic Medicare claims) | 600 | US public domain |
| insurance_claim | GNOTHEIA synthetic property FNOL | 200 | Apache-2.0 |
| insurance_claim | BDR motor-claim decisions | 150 | MIT |
| insurance_claim | INSURBIAS claim narratives | 150 | CC BY 4.0 |

The licensing is mixed, so the Hub metadata says `mixed-per-source`. CC BY 4.0 covers this compilation and its labels; each document keeps its source's terms, as listed above. SEC EDGAR filings are free to access and reuse under the SEC's [public-access terms](https://www.sec.gov/about/webmaster-frequently-asked-questions), but the companies wrote them, so they are not US-government works and this card does not claim they are in the public domain. The Enron emails are released for research use and contain real names. CC BY-NC sources were excluded.

## Annotation

Labels come from six regimes, recorded per row in `annotation_method`:

| `annotation_method` | rows | what it means |
|---|---:|---|
| `synthetic` | 1,100 | generated with the document (insurance sources) |
| `source_native` | 661 | the source corpus's own expert labels (CUAD, MAUD) |
| `heuristic` | 646 | deterministic rules over the document or its metadata |
| `llm_zero_shot` | 637 | `deepseek-chat` zero-shot (correspondence intent), with confidence and status |
| `verified_join` | 162 | exact sha256 body match to AESLC/Enron gold |
| `human_annotated` | 96 | a person's label |

Correspondence intent uses a closed 8-class vocabulary: `payment_demand`, `notice`, `analysis`, `request`, `update`, `meeting_invite`, `press_communication`, `other`. All eight occur in the test split.

## Personal information

The correspondence class is real Enron email: it contains the names and addresses of real people. Use it for evaluation only, and do not use it to profile individuals. The insurance claims are synthetic or anonymized; contracts, mergers and corporate records are public filings.

## Bias and limitations

- Contracts, mergers and corporate records are US public-company filings; other jurisdictions are rare.
- The insurance claims are synthetic and simpler than real claim files.
- 742 of the 1,000 correspondence intents are machine labels (637 `llm_zero_shot`) or heuristics (105); 258 are human or exact gold joins. `intent_source` and `intent_status` say which, and 25 rows are `flagged_review`.
- Corporate-record `entity_name` is the EDGAR filer name, not always the string printed in the document (about a quarter differ, mostly `Co.` vs `Company`).
- **Blind metadata is not fully blind.** Some source fields in `default`'s `metadata` give away the answer without reading the text: the subclass (`category`, `lob`, `claim_subtype`, `exhibit_description`), the class (which keys are non-empty, and source-style filenames such as `carrier:…`), insurance `claim_number` (`source_row_id`), and on the 152 merger agreements the per-category MAUD annotation counts (`maud_categories`). To score a model fairly, give it `doc_text` and `prompt` only. v10 removes metadata and filenames from the blind config.

## Versions

| tag | change |
|---|---|
| [`v9.3`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset/tree/v9.3) | Corporate records gain per-subclass presence keys; merger agreements route to `merger_agreement_specialist` (labels only; rows and text unchanged). Labels are versioned separately (`labels_version` in `manifest.txt`). |
| `v9.2.1` (branch) | Corporate-record label repair: `entity_name`, `record_type`, grounded `subject_matter` |
| [`v9.2`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset/tree/v9.2) | Six-regime annotation provenance; `related_document_ids` populated; build-internal column removed |
| [`v9.1`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset/tree/v9.1) | Label-derived counts moved out of the blind config; presence codes; context-window bands |
| v9 | 3,302 rows: correspondence, corporate records, EX-10 contracts and insurance narratives added to the v8 base |
| v8 | [`mailroom-corpus`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-corpus), 2,000 rows (frozen at `eafe1ab4`) |

Every release since v9 keeps the row set, `document_id`s and document text unchanged. Release notes: [`CHANGELOG.md`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset/blob/main/CHANGELOG.md). Byte checksums for every file are in [`manifest.txt`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset/blob/v9.3/manifest.txt).

## Citation

```bibtex
@misc{mailroom_dataset,
  title        = {Mailroom Dataset: An Evaluation and Ingress Simulation Corpus for Legal Document Triage},
  author       = {Morningstar, Lucius},
  year         = {2026},
  howpublished = {Hugging Face: \url{https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset}}
}
```

Please also cite the source corpora you use: CUAD ([arXiv:2103.06268](https://arxiv.org/abs/2103.06268)) and MAUD ([arXiv:2301.00876](https://arxiv.org/abs/2301.00876)).

Issues and pull requests: [Exios66/Mailroom-Corpus-EDA](https://github.com/Exios66/Mailroom-Corpus-EDA).
