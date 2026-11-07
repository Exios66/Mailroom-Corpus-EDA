# Changelog

All notable changes to the [Mailroom Dataset](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset) are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Versioning

Each release is a Hub tag. Pin one with `load_dataset(..., revision="v9.3")`; `main` moves. Tags are not moved, with one exception: on 2026-11-06, its release day, `v9.3` was re-pointed to include the merger routing fix and the rewritten card (see [v9.3]).

From v9.3 on, version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html), applied to data:

| bump | when | example |
|---|---|---|
| **major** (`v10`) | rows added or removed, a config or column removed or renamed, split membership changed | duplicate removal, a new config layout |
| **minor** (`v9.3`) | new fields, presence codes or configs; existing rows, columns and values unchanged | corporate presence keys |
| **patch** (`v9.2.1`) | label values corrected; schema and rows unchanged | the corporate-record label repair |

Card and documentation edits are not releases. They are listed under the release they describe.

## [Unreleased]

### Planned for v10 (major)
- Typed per-class ground-truth configs (`gt_contract`, `gt_merger_agreement`, `gt_correspondence`, `gt_insurance_claim`, `gt_corporate_record`). Every field gets a presence code, evidence spans and a provenance string.
- Blind `default` config reduced to `document_id`, `doc_text` and `prompt`. Filenames and source metadata move to `source_metadata`; class and routing move to `routing`.
- New `cuad_clauses` and `maud_answers` configs.
- Labels completed by two calibrated models plus human review (the v10 labeling campaign).
- The legacy `ground_truth` config is deprecated, to be removed in v11.

### Staged as v9.4, to be folded into v10
- 10 duplicate documents removed (3,302 → 3,292 rows): 9 exact copies, and one near-duplicate that leaked from train into test.
- 2,100 `"nan"` placeholder cells on GNOTHEIA and BDR insurance rows set to empty.

## [v9.3] - 2026-11-06

Corporate-record presence layer and merger routing. Rows, `document_id`s and text are unchanged from v9.2. The corporate labels differ from the `v9.2` tag because v9.3 includes the v9.2.1 label repair; compared with v9.2.1 (branch `corporate-labels-v9.2.1`), every populated corporate value is unchanged.

### Added
- Corporate records carry per-subclass entity keys in `gt_presence`: 6 core fields plus each subclass's own extensions, 37 keys in all, mirrored from `llm-dojo-scoring`'s corporate-record schema.
- `labels_version` in `manifest.txt`, versioning labels separately from rows.

### Fixed
- Merger agreements route to `merger_agreement_specialist`, the class's own specialist in `llm-mailroom`, instead of `contracts_specialist`: `expected_specialist` on the 152 merger agreements and on the 8 bundle, 8 stream and 4 fixture rows built on them. No other value changed. Added on 2026-11-06 after v9.3 was first tagged; the tag now includes it.

### Changed
- A corporate entity field that is blank with no documented absence is *unlabeled*: its key is left out of `gt_presence`, never shipped as a code. Scorers exclude it from both sides.

### Documentation
- The dataset card was rewritten: quick start, config and column reference, presence codes, sources, annotation, limitations, versions.
- This changelog was added.
- The card states which blind `metadata` fields reveal the class, subclass or MAUD annotation counts (see the card's limitations); v10 removes them.

### Known issues
- On 411 SEC EDGAR corporate records, `keywords` holds only `[record type, entity name]`, below the 3–8 keywords the other classes carry. These rows need real keyword annotation.

## [v9.2.1] - 2026-10-17

Published as the Hub branch `corporate-labels-v9.2.1`, not as a tag. Its content is included in v9.3.

### Fixed
- `entity_name` populated on all 450 corporate records from the EDGAR filer name.
- `record_type` populated from each subclass.
- `subject_matter` rewritten as one grounded sentence.
- `keywords` stripped of exhibit, form, accession and CIK strings.
- 12 corporate records re-assigned to the subclass their text names.

## [v9.2] - 2026-10-06

### Changed
- `annotation_method` records six regimes (`verified_join`, `llm_zero_shot`, `human_annotated`, `synthetic`, `heuristic`, `source_native`) instead of two.
- `annotation_source` matches `source_corpus`: the 91 SEC EDGAR EX-10 contracts move from CUAD to `sec_edgar`.

### Added
- `related_document_ids` populated for the 23 rows in 9 subject threads. The copies inside `gt_fields` now mirror the top-level columns.

### Removed
- The build-internal `_published` column from `ground_truth`, which now has 35 columns.

## [v9.1] - 2026-09-26

### Added
- `gt_presence` in every row's `gt_fields`: one code per label-bearing field (`populated`, `schema_documented_absence`, `pending_annotation`, `not_applicable`).
- `token_estimate` and `context_window_band` (`<=4k`, `<=16k`, `<=32k`, `>32k`) in `gt_fields`.

### Removed
- `clause_count` and `maud_label_count` from the blind `default` config's `metadata`. They reveal which documents have hidden clause labels; they now live in `gt_fields` only.

## [v9] - 2026-09-13

First release of this repository, the successor of [`mailroom-corpus`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-corpus) v8. Earlier documents call it "dataset v1".

### Added
- 1,302 documents (2,000 → 3,302):
  - +650 correspondence, with intent from subject-line heuristics
  - +411 corporate records
  - +91 SEC EDGAR EX-10 contracts, without CUAD clause labels
  - +150 insurance documents: INSURBIAS narratives and workflow documents
- Five configs: `default` (blind), `ground_truth`, `bundles`, `streams`, `fixtures`.
- Evaluation-contract, identity and matter-grouping columns in `ground_truth`.

## [v8] - 2026-09-02

Released as [`Lucius-Morningstar/mailroom-corpus`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-corpus), frozen at revision `eafe1ab4`.

### Added
- 2,000 documents: 950 insurance claims, 509 CUAD contracts, 350 correspondence, 152 MAUD merger agreements, 39 corporate records.
- Property (GNOTHEIA) and auto (BDR) insurance lines.
- A canonical 8-class correspondence intent on all 350 emails.

[Unreleased]: https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset/tree/main
[v9.3]: https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset/tree/v9.3
[v9.2.1]: https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset/tree/corporate-labels-v9.2.1
[v9.2]: https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset/tree/v9.2
[v9.1]: https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset/tree/v9.1
[v9]: https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset/tree/46a4d3c240a36671cde0182fff4960f6b8b73aca
[v8]: https://huggingface.co/datasets/Lucius-Morningstar/mailroom-corpus/tree/eafe1ab4c0d330d8f9c7a5fb254155e75d290828
