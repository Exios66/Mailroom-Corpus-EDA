# Integration runbook: changing what the mailroom dataset labels

Every change to what the dataset contains or labels touches four repositories. They have
to move together, on matching versions, or a consumer scores against a schema it does not
know. This runbook is the one walkthrough for all such changes.

| repository | owns | its pins of the others |
|---|---|---|
| **llm-dojo-scoring** (the Dojo) | the entity schemas, scoring types, subclass surfaces, sorter labels | none |
| **Mailroom-Corpus-EDA** (this one) | the dataset rows, labels, splits, configs and card | `labeling/fields.yaml` `dojo_pin`, `config.REPO_TAG` |
| **llm-mailroom** | taxonomy, specialist agents and prompts, Hub loaders | `pyproject.toml` Dojo ref, `pipeline/hf_corpora.py` revision and `n_docs` |
| **local-mailroom-sandbox** | the offline/Modal harness; vendors the Dojo and llm-mailroom | `_VENDOR_PINS`, `requirements/dojo-release.txt`, `job/spec.py` tag, size and class counts |

## The two kinds of change

**A. A new document type.** A sixth (or later) class with its own specialist: a new Dojo
schema, a new sorter label, new rows, a new `gt_<class>` config. Detail:
`GUIDE_new_doc_type.md`; tick list: `CHECKLIST_new_doc_type.md`; what to add next:
`CANDIDATES.md`.

**B. Expanded labeling on the core five.** New fields or vocabularies, a retype (for
example `urgency` from `name` to `label`), a relabel of existing rows (for example the
v10 `merger_consideration` task for the 56 MAUD mergers whose subclass was `other`), or a
row removal and re-split. Tick list: `CHECKLIST_expanded_labeling.md`.

Both kinds ship on the same release train.

## The release train

Each step merges before the next one starts. Steps 1, 3 and 4 are PRs in other
repositories; this repository never edits their files.

1. **llm-dojo-scoring first.** Add or change the schema, the scoring type per field
   (`label`, `name`, `date`, `money`, `id`, `free_text`, `entity_list:*`), the subclass
   surfaces and, for kind A, the sorter label and `LIVE_SPECIALISTS`. Release a minor
   version (a new field or class) or a patch (a description change). Record the version
   and commit in the lockstep table below.
2. **Mailroom-Corpus-EDA.**
   1. Refresh `labeling/dojo_snapshot/dojo_schemas.json` against the new Dojo with
      `scripts/labeling/snapshot_dojo.py` and move `dojo_pin` in `fields.yaml`.
      `tests/test_labeling.py::test_snapshot_pins_match_fields_yaml` and
      `test_fields_match_pinned_dojo_types` fail until both agree.
   2. `labeling/fields.yaml`, prompts and campaign chunks; `labeling check-class` (kind A)
      and `labeling costs`.
   3. Run the campaign chunks (a human, with initials and caps; see
      `campaigns/v10/runbook.md`).
   4. The dataset build, the conformance gates and the card. The md5 split rule never
      changes; if rows are added or removed, the card's split counts are re-rendered from
      the data.
   5. Publish a **new** dataset tag (old tags never move) and move `config.REPO_TAG`.
3. **llm-mailroom.** Bump the Dojo dependency to the step-1 release; mirror the schema
   in `src/schemas/documents.py`; for kind A add the taxonomy entry, the specialist agent
   and a new frozen prompt version (`src/llm/frozen_vN/`, earlier versions stay frozen),
   and the routing edge; move `FULL_CORPUS_REVISION`, `n_docs` and the class counts in
   `src/pipeline/hf_corpora.py` to the step-2 tag.
4. **local-mailroom-sandbox.** Refresh `vendor/` with `scripts/sync_vendor.py` (or
   `/vendor-sync`), never by hand, moving `_VENDOR_PINS` and
   `requirements/dojo-release.txt` together; add the new revision to the per-revision
   size and class-count table in `job/spec.py` (`FAMILY_HF_TAG`, `FAMILY_CORPUS_SIZE`,
   `FAMILY_CLASS_COUNTS`) and to `corpus.py`'s field lists; add run configs pinned to the
   new tag and leave the old configs pinned to theirs.
5. **Close the train.** `check_lockstep.py` exits 0 (below), each repository's drift
   tests pass, and the CHANGELOG of every repository names the same Dojo version and
   dataset tag.

Pinned runs keep working throughout: they name an old tag and an old Dojo, and neither
moves.

## Version lockstep table

Copy this into the tracking issue and fill one column per release. Nothing is published as
"current" until every cell in the column is filled and merged.

| | Dojo version / commit | dataset tag | llm-mailroom Dojo ref / corpus revision / `n_docs` | sandbox `_VENDOR_PINS` / `FAMILY_HF_TAG` / `FAMILY_CORPUS_SIZE` |
|---|---|---|---|---|
| release being cut | | | | |
| merged PR | | | | |

`check_lockstep.py` reads all of these from the four checkouts and prints the table:

```bash
python src/mailroom_eda/labeling/integrations/check_lockstep.py \
    --eda . --mailroom ../llm-mailroom --sandbox ../local-mailroom-sandbox \
    [--dojo ../llm-dojo-scoring] [--rows 3292] [--markdown lockstep.md]
```

It exits 1 when any row disagrees. As of 2026-11-06 it reports two lags that predate v10:
the dataset tag (this repository `v9.2`; llm-mailroom and the sandbox `v9.1`) and
llm-mailroom's standalone Dojo ref (`v0.22.0`; the sandbox and this repository pin
`0.27.2`; inside the monorepo llm-mailroom resolves the Dojo as a workspace member, so the
ref matters only for standalone installs). The v10 release train closes both.

## Drift checks: what fails when a repository lags

| repository | check | catches |
|---|---|---|
| Mailroom-Corpus-EDA | `tests/test_labeling.py` (`test_snapshot_pins_match_fields_yaml`, `test_fields_match_pinned_dojo_types`, `test_snapshot_is_current_against_vendored_dojo`) | `fields.yaml` types or the snapshot behind the Dojo |
| Mailroom-Corpus-EDA | `labeling doctor`, `labeling costs --check`, `scripts/labeling/sync_plan_prompts.py --check` | a stale campaign, cost table or plan prompt |
| Mailroom-Corpus-EDA | `scripts/audit/verify_v10_conformance.py` (to be written with the v10 build; plan Part C3) | the build disagreeing with `fields.yaml`, the Dojo or the card |
| llm-mailroom | `src/tests/test_hf_corpora.py` | `n_docs`, class counts or configs behind the dataset tag |
| local-mailroom-sandbox | `tests/test_vendor_drift.py` | a hand-edited or partially refreshed `vendor/` |
| local-mailroom-sandbox | `tests/test_dojo_source.py` | `requirements/dojo-release.txt` and `_VENDOR_PINS` apart |
| local-mailroom-sandbox | `tests/test_job_spec.py`, `tests/test_corpus.py` | the per-revision size and class-count table |
| all four | `check_lockstep.py` | any pin that did not move with the others |

## Finding every file to change

`find_touchpoints.py` lists every file in each checkout that names the document classes.
A file where three or more appear together is a class registry that must gain a new class;
other mentions are code to read.

```bash
python src/mailroom_eda/labeling/integrations/find_touchpoints.py \
    --repo . --repo ../llm-mailroom --repo ../local-mailroom-sandbox \
    --repo ../local-mailroom-sandbox/vendor/llm-dojo-scoring \
    --class commercial_lease:commercial_leases_specialist --markdown touchpoints.md
```

It also tracks every class already declared in `labeling/fields.yaml`, and exits 1 if a
`--repo` is not a directory.

## Files here

| file | what it is |
|---|---|
| `README.md` | this runbook |
| `GUIDE_new_doc_type.md` | kind A in detail, step by step, with the decision criteria |
| `CHECKLIST_new_doc_type.md` | kind A tick list, per repository |
| `CHECKLIST_expanded_labeling.md` | kind B tick list, per repository |
| `CANDIDATES.md` | document types that could be added, with source datasets, licences and the niche each fills |
| `check_lockstep.py` | the version lockstep table, from the four checkouts |
| `find_touchpoints.py` | every file that names the document classes |
| `templates/` | a `fields.yaml` class block, a task prompt and campaign chunks to copy |
| `examples/commercial_lease/` | a complete, validated kind-A proposal (`labeling check-class` passes) |
