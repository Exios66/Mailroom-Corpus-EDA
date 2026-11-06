# Expanded labeling checklist (change kind B)

For changes to the labels of the five existing classes: a new field, a new or changed
vocabulary, a retype, a relabel of existing rows, or a row removal. Copy the sections that
apply into the tracking issue. Release order and the lockstep table are in `README.md`.

Worked example in v10: the 56 MAUD merger agreements whose subclass was `other` had full
MAUD clause labels but no expert Type of Consideration answer. Instead of dropping them,
the `merger_consideration` task labels that answer (chunk `6c0` calibrates on the 95 rows
with the expert answer at ≥ 0.95 accuracy per model; `6c1` labels the 56), so every merger
reaches the same label quality and the row count and splits stay unchanged.

## 0. Decision (no code)

- [ ] What changes, per class and field, and why (a scoring gap, an unlabeled slice, a wrong type)
- [ ] Is a Dojo change needed? A new field, a new scoring type or a new closed vocabulary is; a relabel of an existing field is not
- [ ] Is there gold to calibrate against (expert spans, an expert answer, manual labels)? If so, a calibration chunk with an accuracy gate comes first
- [ ] Does the row set change? If rows are removed, list them with the reason; prefer labeling over dropping
- [ ] Projected spend from `labeling costs` and a cap per chunk

## 1. llm-dojo-scoring (only when the schema changes)

- [ ] `scoring/suites.py` `DEFAULT_FIELD_TYPES[<class>]`: the field and its scoring type (a closed list is `label`, never `name`)
- [ ] `schemas/extraction_schemas.py` and `schemas/scored_enums.py`: the field and any closed enum
- [ ] `schemas/corpus.py` `CORPUS_EXTRACTION_FIELDS` and, for subclass changes, the subclass surfaces
- [ ] `scoring/extraction_scope.py` `LIVE_SCHEMA_FIELDS`; `schemas/data/extraction_status.json` regenerated
- [ ] Tests: schema parity and a fixture row scored with the new field
- [ ] Version bump (minor for a new field or type) and release notes

## 2. Mailroom-Corpus-EDA: labeling

- [ ] `scripts/labeling/snapshot_dojo.py` against the new Dojo; `fields.yaml` `dojo_pin` moved
- [ ] `src/mailroom_eda/labeling/fields.yaml`: field spec (`type`, `retype`, `source`, `task`, `absence`, `vocabulary`), vocabularies
- [ ] A new task when the field needs its own window or calibration: a builder in `tasks.py` (`BUILDERS`, `TASK_CLASSES`, `TWO_MODEL_TASKS`), a prompt in `prompts/`, `runner.select` sets, `MAX_TOKENS` and `TYPICAL_OUTPUT`; otherwise a `fields.yaml` `tasks:` entry is enough
- [ ] A gate kind in `operate.gate_result` if the calibration gold is new (as `consideration_accuracy` was)
- [ ] `campaigns/<name>/campaign.yaml`: calibration, then label chunks; later chunks that depend on the new label get it in `needs:`
- [ ] `labeling costs` regenerated; `scripts/labeling/sync_plan_prompts.py` run
- [ ] `tests/test_labeling.py`: the builder, the selection counts, the gate (pass and fail), the chunk order

## 3. Mailroom-Corpus-EDA: dataset

- [ ] The v10 build reads the approved labels; `provenance` records `llm:<configuration>` for model labels and the expert source otherwise (`labeling/ingest.py`, applied by `v10_build.build --labels`; an uncalibrated configuration is refused)
- [ ] `gt_presence.py` / `gt_quality.py`: the field's applicability and allowed empties
- [ ] Subclass relabels: the new `subclass` follows the label, `source_subclass` and the legacy `expected_subclass` keep the raw value
- [ ] Row removals only: the removal list, the new size, and the re-rendered split counts (the md5 rule is unchanged, so remaining rows keep their split)
- [ ] A conformance check for the change (for example: every merger has a non-null `merger_consideration` with evidence or an expert source): a gate in `v10_conformance.py`, with a tamper test in `tests/test_v10_pipeline.py`
- [ ] The card: field table, coverage, composition and per-subclass tables are re-rendered from the data (`python -m mailroom_eda.card_v10 --check` passes)
- [ ] CHANGELOG entry with the counts before and after

## 4. llm-mailroom

- [ ] `src/schemas/documents.py`: the field in the class's extraction model
- [ ] Specialist prompt: a new frozen version under `src/llm/frozen_vN/` if the specialist now extracts the field
- [ ] `src/pipeline/hf_corpora.py`: the new revision; `n_docs` and class counts if the row set changed
- [ ] Samplers or reports keyed on a relabeled subclass read the new `subclass`
- [ ] Dojo pin bumped; `src/tests/test_hf_corpora.py` passes
- [ ] The result cache: keys include the dataset revision, so cached results for the old revision are not reused

## 5. local-mailroom-sandbox

- [ ] `vendor/` refreshed with `scripts/sync_vendor.py`; `_VENDOR_PINS` and `requirements/dojo-release.txt` moved together
- [ ] `src/mailroom_sandbox/job/spec.py`: a new per-revision row in the size and class-count table (`FAMILY_CORPUS_SIZE`, `FAMILY_CLASS_COUNTS`) only if the row set changed; old revisions keep theirs
- [ ] `src/mailroom_sandbox/corpus.py`: the field in the ground-truth field lists
- [ ] Run configs for the new revision; old configs left pinned
- [ ] `tests/test_vendor_drift.py`, `tests/test_dojo_source.py`, `tests/test_job_spec.py` pass

## 6. Publish

- [ ] Every gate passes on the final stage
- [ ] New dataset tag; older tags untouched
- [ ] `check_lockstep.py --rows <size>` exits 0
- [ ] Card versioning table and every repository's CHANGELOG name the same Dojo version and tag
