# New document type checklist

Copy this list into the tracking issue for the new class and tick items off as each PR
lands. `<class>` is the new class key (for example `commercial_lease`) and `<specialist>`
its specialist (for example `commercial_leases_specialist`). Paths are relative to each
repository's root.

Run `find_touchpoints.py` against each checkout too: it finds anything this list misses
because the code moved.

## 0. Decision (no code)

- [ ] The class meets every criterion in `README.md` step 1, written up in the issue
- [ ] Sources chosen, with a licence for each; non-redistributable text marked calibration-only
- [ ] Field list (10 to 20 fields) with a Dojo scoring type for each
- [ ] Subclass list (5 to 10), each with at least 15 documents
- [ ] Intent vocabulary (closed, with `other`)
- [ ] Overlap check against the five existing classes, with results

## 1. llm-dojo-scoring (first, released as a minor version)

- [ ] `config.py`: `DOC_CLASS_KEYS`, `LIVE_DOC_CLASS_KEYS`
- [ ] `scoring/suites.py`: `SPECIALIST_DOC_TYPES`, `LIVE_DOC_TYPES`, `LIVE_SPECIALISTS`, `DEFAULT_FIELD_TYPES["<class>"]`
- [ ] `pipeline/mailroom.py`: live doc types, sorter label set, live specialists
- [ ] `schemas/corpus.py`: `CORPUS_DOC_TYPES`, subclass surfaces, `CORPUS_DIFFERENTIATORS`, `CORPUS_EXTRACTION_FIELDS`, the pin docstring
- [ ] `schemas/intents.py`: intent vocabulary
- [ ] `schemas/scored_enums.py`: closed label enums
- [ ] `schemas/extraction_schemas.py`: the class's document-type schema
- [ ] `schemas/sorter_guide.py`: class description and cues for the sorter
- [ ] `metrics/registry.py`, `metrics/profiles.py`: specialist metric surfaces
- [ ] `scoring/extraction_scope.py`: `LIVE_SCHEMA_FIELDS`
- [ ] `schemas/data/extraction_status.json`: regenerated
- [ ] Tests: schema parity, a fixture row scored end to end, sorter label set
- [ ] Version bump and release notes

## 2. Mailroom-Corpus-EDA: labeling

- [ ] `src/mailroom_eda/labeling/fields.yaml`: class block, vocabularies, `tasks:` entry
- [ ] `src/mailroom_eda/labeling/prompts/<task>.md`
- [ ] `python -m mailroom_eda.labeling check-class <folder>` is clean
- [ ] `scripts/labeling/snapshot_dojo.py`: add the class to its class tuple, run it against the new Dojo; `dojo_pin` updated
- [ ] `scripts/labeling/sync_plan_prompts.py` run (when the prompt goes in a plan)
- [ ] `campaigns/<name>/campaign.yaml` chunks for the class; `labeling costs` regenerated
- [ ] `tests/test_labeling.py` passes

## 3. Mailroom-Corpus-EDA: dataset

- [ ] Sourcing script for the new rows (stable `document_id`, `content_sha256`, metadata)
- [ ] `src/mailroom_eda/config.py`: `DOC_TYPES`, class colour
- [ ] `src/mailroom_eda/identity.py`: source corpus mapping and `document_id` rule
- [ ] `src/mailroom_eda/gt_presence.py`: applicable fields and allowed empties
- [ ] `src/mailroom_eda/gt_quality.py`: quality rules for the class
- [ ] `src/mailroom_eda/eval_contract.py`: class-to-specialist map, purpose classes
- [ ] `src/mailroom_eda/integrity.py`: class masks
- [ ] `src/mailroom_eda/visualizations.py`, `visualizations_interactive.py`: class panels
- [ ] `src/mailroom_eda/bundles.py`, `fixtures.py`: bundle family and routing fixtures (optional)
- [ ] `src/mailroom_eda/dataset_export.py`: class lists in the export
- [ ] `README.md`, `docs/README.md`, `docs/dataset-cards/`, `docs/assets/readme/generate.py`: composition tables and class colours
- [ ] The dataset build: a `gt_<class>` config, routing rows, card sections
- [ ] `tests/conftest.py`: the class set
- [ ] Conformance gates and the card's `--check` pass
- [ ] CHANGELOG entry

## 4. llm-mailroom

- [ ] `src/config/taxonomy.yaml`: class, specialist, confidence bands
- [ ] `src/schemas/documents.py`: extraction model and class map
- [ ] `src/agents/<specialist>.py`
- [ ] New frozen prompt version under `src/llm/` (old versions stay frozen)
- [ ] `src/langchain_agents/`: classifier, sorter, specialist agents, prompts, doc inventories
- [ ] `src/graph/build_graph.py`: routing edge
- [ ] `src/pipeline/hf_corpora.py`: corpus size, class counts, configs
- [ ] `src/observability/`: specialist suite, ground-truth loaders, honest gaps
- [ ] `src/scripts/run_hf_pilot.py`, `sync_hf_ground_truth.py`, `sync_dataset.py`: class keys
- [ ] Dojo pin bumped; tests updated

## 5. local-mailroom-sandbox

- [ ] `vendor/` refreshed with `scripts/sync_vendor.py` (never hand-edited)
- [ ] `_VENDOR_PINS` in `src/mailroom_sandbox/cli.py` and `requirements/dojo-release.txt` moved together
- [ ] `src/mailroom_sandbox/job/spec.py`: per-revision corpus size and class counts
- [ ] `src/mailroom_sandbox/corpus.py`: ground-truth field lists
- [ ] `src/mailroom_sandbox/gt_labeler.py`, `prompt_registry.py`, `eval/`
- [ ] `scripts/sync_specialist_prompts.py`, `scripts/estimate_spend.py`
- [ ] Run configs for the new revision; old configs left pinned
- [ ] `tests/test_vendor_drift.py` and `tests/test_dojo_source.py` pass

## 6. Publish

- [ ] Every gate passes on the final stage
- [ ] New dataset tag published; older tags untouched
- [ ] Pins bumped in this repository, llm-mailroom and the sandbox
- [ ] Card versioning table and CHANGELOG updated
