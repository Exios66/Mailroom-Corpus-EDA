# v10 consumer follow-up patch plan

Status: **DRAFT.** Companion to `docs/plans/v10-structure-and-label-completion.md`.
It lists what llm-dojo-scoring, llm-mailroom and local-mailroom-sandbox must change to
take in the v10 layout, the new labels and the adapted contract and merger schemas.
Facts below were verified read-only against llm-dojo-scoring 0.27.2 (`1cd4aba7`, as
vendored in the sandbox), llm-mailroom `cb960a2` and the sandbox branch.

## Consumer facts

- **llm-dojo-scoring** (vendored 0.27.2 @ `1cd4aba7`):
  - `parse_gt_fields` decodes one JSON level. Absent codes are {not_applicable, schema_documented_absence, pending_annotation}. Keys that aren't in the suite map are dropped when `gt_presence` exists.
  - `name` scoring gives 1.0 credit whenever the prediction contains the gold tokens, so enum fields typed `name` get fuzzy credit (`demand` matches `attorney_demand`).
  - `governing_law` gold is **the governing-law sentence** (scored by containment). `cuad_family` must be a **canonical `CUAD_FAMILY_KEYS`** value.
  - `claim_checklist` lines are scored as free text. Span offsets are never read.
  - The contract headline can never reach "full", because the live map includes the foreign `merger_consideration` / `maud_clauses`.
  - Corporate `not_applicable` is treated inconsistently between `corporate_record_schema.py:380` and `:381`.
  - There is no HF loader, and the vendor snapshot has no tests.
- **llm-mailroom:**
  - Joins `default` and `ground_truth` on `filename`; v10's blind `default` has no `filename`, so the join moves to `document_id` (patch below). Counts rows where `content_sha256` doesn't equal sha256(`doc_text`).
  - Every `result_cache` key hashes `doc_text`.
  - `load_dataset` without a config name relies on `default` being the default config.
  - **Bug:** the viewer `/rows` fallbacks send no revision, so once v10 is on the Hub main branch, pinned v9.1 runs would silently read v10.
  - Other bugs: `sync_hf_ground_truth --push` writes every column as a string, and its pin rewrite only matches a 40-hex sha.
  - `coerce_gt_value` breaks on the numpy arrays that typed list columns produce.
  - Vocabularies: listed in Part E.
- **local-mailroom-sandbox:**
  - `FAMILY_CORPUS_SIZE=3302` and the class counts are hard checks. Shard names are hard-coded `-00000-of-00001`. `spec.config` is ignored while `ground_truth` exists.
  - `normalize_rows` raises on a hash mismatch.
  - The interim label overlay is keyed on `sha256(doc_text)[:16]`, so edited docs silently drop their corrections.
  - `gt_label_ex10` needs the `default.metadata` column.
  - There are 149 + 130 run configs pinned to v9 revisions. There is no pin registry; pins are bumped by hand.
  - `eval/extraction_scope.py` duplicates the Dojo's copy.
- **Nothing reads `expected_specialist`**, so the merger fix changes no score. It is still corrected, because the value is wrong.


## Patches

Order matters. Under the ownership rules, the Dojo comes first, then llm-mailroom, then the sandbox vendor refresh. **v10 is not published until G1–G3 are merged.** The v9 pins keep working throughout, because published tags don't move.

### G1. llm-dojo-scoring → 0.28.0

The repo is added with `add_repo` (push access) when this phase starts.

- **`schemas/cuad_schema.py`:** ContractExtraction v2 (A1). Drop the foreign fields from the live map and keep them parse-compat. Update `_check_invariants`.
- **`schemas/maud_schema.py`:** MergerAgreementExtraction v2 (A2), with the parity count updated. Add a `MERGER_INTENTS` vocabulary and its alias.
- **`scoring/suites.py`:**
  - `DEFAULT_FIELD_TYPES` contract and merger v2.
  - Retype the `label` fields (`cuad_family`, `merger_consideration`, merger `intent`, `urgency`, `communication_type`, `claim_type`, `coverage_determination`).
  - Update the `_HONEST_GAPS` text.
- **`schemas/corpus.py`:**
  - Update `CORPUS_EXTRACTION_FIELDS`.
  - Add the canonical keys to `CORPUS_SUBCLASS_SURFACES`.
  - Update the v10 pin docstring.
- **`scoring/extraction_scope.py`:** update `LIVE_SCHEMA_FIELDS`.
- **`metrics/scorecard_honesty.py`:** `_live_fields` excludes the parse-compat foreign fields.
- **`schemas/corporate_record_schema.py:380-393`:** consistent `not_applicable` handling.
- **New `schemas/v10_rows.py`:**
  - `gt_from_v10_row`: a typed per-class row becomes the legacy-shaped dict with `gt_presence`; `None` presence values are dropped.
  - Add `evidence`/`provenance` to `ANNOTATION_KEYS`.
  - Pivots for `cuad_clauses` and `maud_answers`.
  - `coverage_report(rows)`: per-field coverage.
- **`schemas/data/extraction_status.json`:** regenerate with `scripts/build_extraction_status.py`. Revise `_CUAD_DERIVED`/`_SUBTYPE_DERIVED`.
- **Tests:**
  - v2 parity;
  - label exact-match;
  - a v10 row through the adapter scores the same as the legacy row;
  - contract headline reaches "full";
  - corporate `not_applicable`;
  - a coverage-report fixture.

### G2. llm-mailroom (standalone repo)

- **Do this before any v10 publish:** the Hub dataset viewer's `/rows` API serves only the current main branch and takes no revision parameter, so it cannot serve a pinned run. For pinned runs, `run_hf_pilot.py` and `sync_hf_ground_truth.py` stop using `_viewer_rows` and load with `datasets.load_dataset(repo, config, revision=<pin>)` (or read that revision's parquet files with `huggingface_hub.hf_hub_download(..., revision=<pin>)`). `hf_corpus_loader` puts the revision into the cache key and **fails closed**: a pinned request that would have to fall back to the viewer raises instead of silently reading main. The viewer stays only for unpinned, interactive browsing.
- **Merger subclasses in v10:** the 56 MAUD mergers whose v9 subclass is `other` carry their labeled consideration (`all_cash`, `all_stock`, `mixed_cash_stock`, `mixed_cash_stock_election`, or `other` where a human confirmed it) in the v10 `subclass` column. `expected_subclass` in the legacy configs stays `other`. Stratified samplers that read `subclass` see the new distribution; pinned v9 runs are unaffected. Row count, split and `n_docs` are unchanged.
- **`schemas/documents.py` + `config/taxonomy.yaml`:** Contract and Merger v2 fields and types mirrored from the Dojo, and the `label` enums. New `llm/frozen_v2/{contracts,merger_agreement}_specialist.txt` prompts; `frozen_v1` stays frozen. Bump the prompt version.
- **`pipeline/hf_corpora.py`:** `FULL_CORPUS_SCHEMA="v10"`, the v10 pin, a per-class `configs` map, and `n_docs` 3,292.
- **`pipeline/hf_corpus_loader.py`:** `load_class_gt`, `load_routing`, long-table loaders joined on `document_id` with `filename` as the fallback, and numpy-to-list conversion.
- **Blind `default` is `document_id` + `doc_text` + empty `prompt`:** every join with `default` uses `document_id` (there is no `filename` fallback there, because the filename names the subclass or class on 1,346 rows). `gt_label_ex10` and any reader of source fields load the `source_metadata` config (`document_id`, `filename`, `metadata`). Pinned v9 runs are unaffected.
- **`langchain_agents/doc_inventories.coerce_gt_value`:** handle `np.ndarray` and pyarrow values.
- **`cuad_maud.flatten_*`:** accept long-table rows.
- **`scripts/run_hf_pilot.py`:**
  - read the per-class GT and `routing.subclass`;
  - add `CONTRACT_GT_KEYS`/`MERGER_GT_KEYS`;
  - name the config in `load_dataset`.
- **`scripts/sync_hf_ground_truth.py`:** keep typed columns, accept tag pins, and target the per-class configs.
- **`scripts/sync_dataset.py`:** read typed GT, and take the revision from the `hub_sha_tip` / `revision_requested` provenance keys.
- **Dojo pin:** bump to 0.28.0.
- **Fixtures:**
  - Regenerate `notebooks/fixtures/gmail_pilot_corpus_snapshot.json`.
  - Document that the result cache misses for the ~200 cleaned docs.
- **Tests:**
  - Update the pin and shape tests: `test_hf_corpora`, `test_gmail_pilot_lab`, `test_dataset_trace_metadata`, `test_sync_dataset_hf`, `test_dojo_v019_wiring`, `test_run_hf_pilot`.
  - Add a v10 loader shape test.
  - Add a test that specialist vocabularies match the taxonomy.
  - Add a test that raw and snake-case subclasses normalize the same way.

### G3. local-mailroom-sandbox (after G1 and G2 merge)

- **Vendor refresh:**
  - `python scripts/sync_vendor.py` (or `/vendor-sync`).
  - Bump `_VENDOR_PINS` and `requirements/dojo-release.txt` together.
  - `test_vendor_drift.py` and `test_dojo_source.py` must pass.
- **`corpus.py`:**
  - discover shards by glob;
  - make `spec.config` real: `gt_*` configs are mapped through the Dojo `gt_from_v10_row`;
  - join `routing` and the long tables;
  - carry `document_id`/`source_subclass` through;
  - replace `GT_FIELD_LISTS` with the Dojo types.
- **`job/spec.py`:**
  - a per-revision size and class-count table (v9: 3,302; v10: 3,292) so the v9 YAMLs still validate;
  - `FAMILY_HF_TAG="v10"` plus its shas;
  - a known-set validator for `DatasetSpec.config`.
- **`datasets.py` and `cli.py:575`:** accept the new configs, take sizes from the per-revision table, and key the cache on the resolved sha.
- **`job/benchmark_check.py`:** add the v10 revisions.
- **`eval/extraction_scope.py`:** delete it and import the Dojo copy.
- **`interim_labels`:** rebuild against the v10 text, and retire the `GAP_FIELDS` the Hub now covers.
- **`gt_label_ex10.py`:** make `metadata` optional and re-verify spans.
- **Hard-coded config names and revisions:** `export_sorter_handoff` derives the config name; update the dashboard prose revisions and the `data/cache/README.md`.
- **Run configs:** add v10 run YAMLs and leave the v9 YAMLs pinned.
- **Merger strata:** any sampler or report keyed on merger subclass reads the v10 `subclass` (the 56 `other` rows now carry their labeled consideration) and keeps `source_subclass` for v9 comparisons; the per-revision class counts are unchanged (151 mergers).
- **Tests:**
  - parametrize `test_sand032_configs`, `test_gt_labeler:54-64`, `test_modernbert_feeder`, `test_cli_wiring`;
  - add `test_corpus` fixtures for a typed config, a cleaned text with a matching hash, and raw vs canonical subclasses.

### G4. Publish (only on the user's go)

Status and guards: `v10-release-train.md`. The guarded script is `scripts/publish/publish_v10.py` (a dry run unless `--tag v10 --initials XX --confirm`).

1. Run the v10 gates.
2. Publish to the Hub as a new tag `v10`, keeping the older tags.
3. Update the EDA `config.py` pins.
4. Open follow-up pin-bump PRs in llm-mailroom and the sandbox.
5. Announce removal of the legacy `ground_truth` in v11.

