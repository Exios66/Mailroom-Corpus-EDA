# v10 release train: status

What has to ship, and in what order, before `scripts/publish/publish_v10.py --confirm`
is allowed to run. `v10-consumer-patches.md` is the full spec (G1–G4); this file tracks
status, shows the evidence each item is still open, and says what it blocks.
Checked 2026-11-06 against the local checkouts of llm-mailroom, local-mailroom-sandbox
(`main` at `55ea9d7`) and this repository.

**Order:** G1 (llm-dojo-scoring) → G2 (llm-mailroom) → G3 (sandbox vendor refresh) →
EDA chunks 5a/6a onward → chunk 11 → G4 publish. Published tags never move, so v9 pins
keep working the whole way.

## Release guards (enforced by `publish_v10.py`)

| guard | check | today |
|---|---|---|
| conformance | `scripts/audit/verify_v10_conformance.py --stage data/v10/stage` (gates 1–13) | chunk-0 stage: 1–8, 12, 13 pass; 10 (56 mergers) and 11 (pending fields) fail until labeling is done |
| lockstep | `check_lockstep.py --rows 3292`, with this repo's `REPO_TAG` taken as the tag being cut | drifts, see the table below |
| clean tree | `git status --porcelain` is empty | — |
| tag | `--tag v10` (or `v10.N`) not already on the Hub; existing tags are never moved | checked only with `--confirm` |
| human | `--initials XX --confirm` | — |

## Lockstep table

| check | where | now | release being cut |
|---|---|---|---|
| Dojo release version | EDA `fields.yaml` `dojo_pin` | 0.28.0 | 0.28.0 |
| | sandbox `_VENDOR_PINS` | v0.27.2 (or an untagged commit, sandbox PR 249) | v0.28.0 |
| | sandbox vendored `__version__` | 0.27.2 | 0.28.0 |
| | llm-mailroom `pyproject.toml` | v0.22.0 | v0.28.0 |
| Dojo commit | EDA / sandbox pin / `dojo-release.txt` | EDA `cab97680…` (the v0.28.0 tag); sandbox `1cd4aba7…` until G3 | `cab97680…` |
| Dataset tag | EDA `config.REPO_TAG` | v9.2 | v10 (rewritten by the publish) |
| | llm-mailroom `FULL_CORPUS_REVISION` | v9.1 | v10 |
| | sandbox `FAMILY_HF_TAG` | v9.1 | v10 |
| Corpus size | llm-mailroom `n_docs` / sandbox `FAMILY_CORPUS_SIZE` | 3302 / 3302 | 3292 / 3292 |

`check_lockstep.py` accepts a `_VENDOR_PINS` commit pin: the version comes from the
vendored `__version__`, and the sha joins the commit row (full shas only).

## G1: llm-dojo-scoring 0.28.0

Status: **tagged** `v0.28.0` at `cab976807854129d1cfea4897dbff5b6e337a62b` (2026-11-06). The
EDA side is done: `fields.yaml` pins it, `dojo_snapshot/dojo_schemas.json` is regenerated from
the tag, and every field matches the released types (no `dojo: v2` / `retype` left), so
chunks 5a and 6a are unblocked. The lockstep rows stay red until G2 and G3 move their pins.

- [ ] Contract v2 and merger v2 extraction schemas (`schemas/cuad_schema.py`, `schemas/maud_schema.py`), `MERGER_INTENTS`.
- [ ] `label` retypes in `scoring/suites.py` (`cuad_family`, `merger_consideration`, merger `intent`, `urgency`, `communication_type`, `claim_type`, `coverage_determination`).
- [ ] `schemas/corpus.py` field lists and subclass surfaces; `extraction_scope.LIVE_SCHEMA_FIELDS`; `scorecard_honesty._live_fields`.
- [ ] Corporate `not_applicable` handling (`schemas/corporate_record_schema.py:380-393`).
- [ ] New `schemas/v10_rows.py` (`gt_from_v10_row`, long-table pivots, `coverage_report`).
- [ ] Regenerate `schemas/data/extraction_status.json`; tag `v0.28.0`.

## G2: llm-mailroom

Status: **not started.** Blocks the publish: the first two items are hard blockers.

- [ ] **Viewer `/rows` fails closed for pinned runs.** `src/pipeline/hf_corpus_loader.py` still falls back to `/rows` (lines 22, 192–215), which serves only `main`; a pinned v10 run must never silently read another revision.
- [ ] **`document_id` join.** `src/pipeline/hf_corpus_loader.py:61` has `JOIN_KEY = "filename"`; the v10 `default` config has only `document_id`, `doc_text` and `prompt`, so this join breaks. (A simulated join on `document_id` with the legacy `ground_truth` config passes: 3,292 rows.)
- [ ] v10 pin in `src/pipeline/hf_corpora.py` (`FULL_CORPUS_REVISION = "v9.1"` at line 48 today), per-class `configs`, `n_docs` 3292.
- [ ] `coerce_gt_value` handles numpy / pyarrow values (`src/langchain_agents/doc_inventories.py`).
- [ ] `scripts/sync_hf_ground_truth.py` pin regex accepts tags (it matches a 40-hex sha only, line 572).
- [ ] Contract/merger v2 schemas and `label` enums in `schemas/documents.py` and `config/taxonomy.yaml`; new `llm/frozen_v2/` prompts.
- [ ] Dojo pin 0.28.0; regenerate the Gmail pilot fixture; the test updates listed in the spec.

## G3: local-mailroom-sandbox (after G1 and G2)

Status: **not started.** Blocks the lockstep guard (dataset tag and corpus size).

- [ ] Vendor refresh through `scripts/sync_vendor.py` / `/vendor-sync` (never by hand), moving `_VENDOR_PINS` and `requirements/dojo-release.txt` together. **Hold sandbox PR 249** (Dojo pinned by an untagged commit) until G1 is tagged.
- [ ] Per-revision size and class-count table in `src/mailroom_sandbox/job/spec.py` (`FAMILY_CORPUS_SIZE = 3302` at line 31 is a single constant, asserted at line 43), so v9 YAMLs still validate.
- [ ] Shard discovery by glob and real `spec.config` support in `src/mailroom_sandbox/corpus.py`.
- [ ] `normalize_rows` on v10 (works with a `document_id` join in simulation).
- [ ] Interim overlay rebuilt against the v10 `doc_text` (about 200 cleaned documents change hash; today's overlay matches through `source_content_sha256`).
- [ ] `metadata` optional in `scripts/gt_label_ex10.py` (read `source_metadata`, never `default`).
- [ ] v10 run YAMLs, v9 YAMLs left pinned.
- [ ] Delete `src/mailroom_sandbox/eval/extraction_scope.py` and import the Dojo copy.

## This repository

- [x] Sweep operations, chunk 10 adjudication, interim cross-check (branch `claude/v10-sweep-ops`).
- [x] Build driver, ingest, conformance gates 1–13, card (branch `claude/v10-build-ingest`).
- [x] Guarded publish and lockstep commit pins (branch `claude/v10-publish`).
- [ ] PR 43 Tasks 5–7 (bake-off ranking, `labeling inspect`, chunk 2–9 re-plan).
- [ ] Labeling chunks 1–9e, then chunk 10 (no open adjudication items) and chunk 11 (all 13 gates).

## G4: publish (on the user's go only)

1. `python scripts/publish/publish_v10.py --mailroom ../llm-mailroom --sandbox ../local-mailroom-sandbox` (a dry run; every guard must PASS).
2. The same with `--tag v10 --initials XX --confirm`, run by the human whose initials those are. It uploads exactly the manifest's files, replaces each v10 config folder, creates the tag at that commit, and rewrites `config.REPO_TAG`.
3. Commit the `REPO_TAG` bump here, then open the pin-bump PRs it prints for llm-mailroom and the sandbox.
4. Announce the removal of the legacy `ground_truth` config in v11.
