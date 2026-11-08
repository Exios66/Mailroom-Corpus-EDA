# Changelog

Notable changes to `Exios66/Mailroom-Corpus-EDA` (mirrored monorepo-side as
`packages/mailroom-corpus-eda`). Behavior-changing releases carry an entry
here in the same commit; docs-only changes declare themselves as such.
Timestamps are UTC. Earlier revisions (v9.1, v9) are documented in
`AGENTS.md` §HF facts and the per-revision plan docs under `docs/plans/`.

## Unreleased

- Gold smoke fixtures: `tests/fixtures/live_smoke/<doc_type>/` holds one real labeling run per document type
  (contract, merger_agreement, corporate_record, insurance_claim, correspondence) as committed test data: the
  public documents as downloaded (full text), every model call (`calls.jsonl`), the review packet
  (`expected-review-data.json`) and a manifest of the dataset revision, plan and prompt / input / schema hashes.
  New `mailroom_eda.labeling.smoke_fixtures` (`python -m mailroom_eda.labeling.smoke_fixtures`):
  `record` builds each type's plan as the trial launcher does, runs its preflight, aborts before spending on any
  failing item, splits `--cap-usd` per type (cuad 0.40, corporate 0.20, merger_consideration 0.15,
  claim_checklist 0.15, correspondence 0.10 of $1.00), and needs the total cap typed again (`--plan-only` prints
  the plan and spends nothing); only documents that need labels are recorded, never CUAD or MAUD gold; `check`
  (offline) fails with a "Gold fixture <type> is stale" message and the exact re-record command when a prompt,
  model, reasoning level, `max_tokens`, response schema, the dataset revision or a document's text changed;
  `replay` (offline) feeds the recorded answers through the real `runner.execute`, review and export and diffs
  the normalized packet; `verify-hub` re-checks each stored document against the Hub (test gated by
  `MAILROOM_LIVE_HUB=1`). `tests/test_live_smoke_fixtures.py` runs `check`, `replay`, the portal and the
  no-gold and no-credentials checks over every recorded fixture (skipped until they are recorded) and
  exercises the recorder end to end with a fake hub and chat. Docs: `docs/labeler-review-viewer.md` "Gold
  smoke fixtures".

- Reasoning is per model, and its token allowance is measured. `reasoning` in `runner`, `operate` and
  `launch` is a level (`off`/`low`, unchanged) or `{model: level}` (`runner.reasoning_levels` normalizes
  and raises `ValueError` for an unknown model or level); `campaign.yaml` entries may give
  `reasoning: {model: level}`, and `labeling run` takes `--second-reasoning` for `--second-model`
  (default: `--reasoning`; `review` takes it too, to find a mixed pair's directory). Every per-call
  decision uses the model's own level: window check, budget reservation, `max_tokens`, the request, the
  journaled `reasoning`, resume and reuse, the `review()` filter, projections, the `runs.jsonl` start
  record (the mapping, only when the levels differ), the packet's `reasoning`, approved-configuration
  matching in `ingest`, and the ping. A mixed pair's directory is `...-reasoning-low+off`; single levels
  keep their names. `runner.reasoning_allowance(model, task, level)` replaces the flat 6,144 added to every
  reasoning call: 0 at off, else `clamp(4 x measured p75, 1,024, 6,144)` (no measurement: 6,144), so the spend
  gate reserves what a call can plausibly use (`max_tokens(task, level, model)`; the old two-argument form
  still returns the ceiling). A call cut off at the token limit with at least 80% of its output spent
  reasoning is journaled as a failed, billed call with `reasoning_runaway: true` ("reasoning ran away: N of
  M output tokens were reasoning; answer truncated"), retried by the next run, and shown on the review
  record as an issue and in `model_outputs`. Launch trials: contracts run GLM-5.3 FlashX Legal at `low` beside
  DeepSeek v4.1 Flash at `off` (not waiting for chunk 1's winner; both answer, as proposals), and any DeepSeek
  in a trial pair runs at `off`; trial document ids are namespaced `public-v9.3:<id>` with
  `published_document_id` kept, so a trial packet and a stage packet of the same document load together in the
  portal. `test_reasoning_calls_get_a_reasoning_allowance_on_top_of_the_answer_budget` now asserts DeepSeek's
  measured intent budget (400 answer + 3,600 allowance = 4,000) instead of the flat ceiling; the launch tests for the contract trial no
  longer expect it to wait for the chunk-1 winner.
- Review portal can launch labeling batches from the page, off unless started with `--enable-launch`
  (`labeling/launch.py`; `GET /api/launch/options`, `POST /api/launch/preflight`, `POST /api/launch/start`,
  `GET /api/launch/status`). Targets are campaign arms (run through `operate.cmd_chunk`, labeled Labeling or
  Qualification) and public trials of 1 to 5 documents per type, capped at $0.25, drawn only from documents
  that need labels and refused if any is CUAD or MAUD gold. A loud preflight checklist (key and credit, models,
  stage sha, ping, HF checkpoint, cap headroom, approvals, ...) with a fix for every failure gates a
  server-rechecked start that needs the cap typed again; one run at a time, bounded log, receipts in
  `launches.jsonl`. A pasted API key stays in memory and reaches the run through `execute(env=...)`, never
  `os.environ`, disk or a response. The page also shows a Model answers table from `model_outputs`.
  `runner.model_problem`, `runner.projected_cost` and `runner.CAP_HEADROOM_FACTOR` are new shared helpers;
  `cmd_chunk` takes `chat`, `key_status` and `env`.
- Fix: `operate.cmd_chunk` reused `why` for the requires, checkpoint and Modal checks, so an
  `--ack-requires` run journaled the checkpoint's message as the reason it overrode the requirement.
- Labeling batches reach the review portal in one format. `labeling review` (so every `run` and `chunk`)
  now writes `<chunk_dir>/<chunk_dir.name>-review-data.json` through the new `review_export.py`
  (also `labeling export-review DIR` and `python -m mailroom_eda.labeling.review_export`): one
  record per (document, field) for the field tasks, `cuad_clauses` per contract and `intent` per
  email, each with `task`, `chunk`, `models`, `reasoning`, `prompt_sha256`, registry policy, each
  model's own answer in `model_outputs`, and located `{start, end, quote}` evidence. The claim
  checklist bundle (`review_packet`) takes its model and reasoning from `runs.jsonl` instead of
  requiring GLM at `low`, and its records follow the same contract (`correction_type: array`,
  sentence issues). The portal tracker now sees `task` on exported records and reads a production
  packet's nested `source.revision`.
- Review portal: Save now works. The page never set `updated_at` on an edited decision, so the
  server rejected every Accept/Correct/Reject with "reviewer initials and a timestamp are
  required". The browser smoke test now checks the timestamp.
- Review portal keeps unsaved drafts across a reload. On load the page restored the browser draft
  before any targets existed, then wrote server state over it, so unsaved notes and decisions were
  lost. The browser smoke test's draft-resume step now passes.
- Review portal shows values for people: the published ground truth is a table (nested JSON
  parsed, lists as bullets, presence in words, the reviewed field highlighted, empty fields folded
  into one line, dataset bookkeeping collapsed); internal codes move under "Technical details".
- Review portal defaults to public insurance claims when no labeling packet exists yet: two
  claims per claim type from the pinned v9.3 dataset, every registry field
  (`--public-per-subclass`, `--public-revision`, `--no-public-fallback`). `review_production`
  gains `--doc-class` and `--per-subclass`.
- Post-publication release verification (`mailroom_eda.release_verify`,
  `scripts/publish/verify_release.py`): downloads what the Hub serves at a tag and checks it
  against invariants every release must meet and a frozen per-tag spec
  (`src/mailroom_eda/release_specs/`, aggregates and sha256s only). Specs frozen for `v9.2` and
  `v9.3`; both pass live. A new release is added with `--freeze`, which refuses a release
  that fails an invariant and prints the diff against the previous release.
- `config.py` pins the Hub tag `v9.3` (`ccd040f6`, data `7fd99b40`), so P0 downloads and every
  report and build read v9.3. The full-corpus tests now assert the v9.3 schema (corporate
  `entity_name`/`record_type`) and pin its two known label gaps: the 91 EX-10 CUAD clauses and
  the 411 corporate records whose `keywords` are only `[record type, entity]`.
- v9.3 amendment (`mailroom_eda.v9_3_merger_routing`): the 152 merger agreements, and the 8 bundle,
  8 stream and 4 fixture rows built on them, route to `merger_agreement_specialist` instead of
  `contracts_specialist`. Only that value changes; the build checks every other parquet cell and
  JSONL byte, checks that every class routes to its own `taxonomy.yaml` specialist, and recomputes
  the manifest. Published to the Hub and the `v9.3` tag re-pointed to it.
- Dataset card rewritten and published to the Hub (v9.3, 2026-11-06), with a dataset `CHANGELOG.md`
  (Keep a Changelog, SemVer for data) on the Hub; source in
  `docs/dataset-cards/mailroom-dataset-CHANGELOG.md`. The v10 card generator's `license_name`
  now passes the Hub metadata check, override labels count as `llm` / `reviewer`, field notes
  fill the description column, and the card links the Hub changelog.
  The card also states which blind `default` metadata fields reveal the class, subclass or
  MAUD annotation counts (a v9.x limitation that v10's blind config removes).
- v10 labeling playbook (`src/mailroom_eda/labeling/`). It is offline until a person runs a
  chunk; no dataset is regenerated or published.
  - Campaign commands read every setting from `campaigns/v10/campaign.yaml`:
    `stage`, `doctor`, `prices`, `ping`, `chunk <id>`, `gate <id> --approve`, `status`.
    `needs:` enforces chunk order, and approvals (with served providers and the prompt
    sha) are recorded in `campaigns/v10/approved.yaml`.
  - OpenRouter routing is pinned per model in `models.yaml` (`route:`):
    `require_parameters`, pinned providers, fp8 and `max_price` for DeepSeek, and `seed`
    only where the endpoints accept it (the z-ai GLM endpoint does not). Prices were
    re-checked on 2026-11-06 (DeepSeek 0.15/0.65, MiniMax 0.15/0.60). The campaign projects
    $14.68 against $28.90 of chunk caps.
  - Chunks run four calls in parallel. The budget reserves each call's worst case, so
    concurrent calls cannot exceed the cap together.
  - `integrations/` (was `new_doc_types/`, which now only points here): one runbook for
    every cross-repository change, either a new document type or expanded labeling on the
    core five. It sets the release order (llm-dojo-scoring, this repository, llm-mailroom,
    then the sandbox vendor refresh), a version lockstep table that `check_lockstep.py`
    reads from the four checkouts (it exits 1 on drift, and today reports dataset tag v9.2
    here against v9.1 in llm-mailroom and the sandbox, and llm-mailroom's standalone Dojo
    ref v0.22.0 against 0.27.2), the drift tests per repository, a checklist for each
    kind of change, and `CANDIDATES.md` (lease, regulatory notice, litigation filing,
    NDA, charity report, invoice, with source datasets, licences and the niche each
    fills). The walkthrough, `find_touchpoints.py` (now with `--class`, the
    `fields.yaml` classes, nested `data/` folders, and exit 1 on a missing repo),
    templates and the validated `commercial_lease` example moved with it. ALeaseBERT's
    data licence is corrected to CC BY-NC 4.0.
  - Merger consideration labeling. The 56 MAUD merger agreements whose subclass is
    `other` stay in the dataset: they have full MAUD clause labels and lack only the
    expert Type of Consideration answer. A new `merger_consideration` task labels it from
    a window of the conversion clauses; chunk `6c0` calibrates both models on the 95
    mergers with the expert answer (new gate `consideration_accuracy`, ≥ 0.95 per model,
    with a confusion table) and `6c1` labels the 56 before the merger entity chunks run.
    `merger_entities` no longer asks for it. Row count and splits are unchanged (3,292
    rows, train 2,971 / test 321, 151 mergers); only the v10 merger subclass distribution
    changes.
  - PR review fixes: the journal reader no longer splits records on U+2028/U+2029/U+0085;
    CSV cells that start with a formula character are prefixed; billed truncated or
    malformed answers are settled and journaled, and unreadable bodies become provider
    errors; NaN or infinite caps are refused; `requires:` chunks need `--ack-requires`;
    the Modal fallback maps models (`--model-map`) and has a token-equivalent price;
    the agreement gate needs every (document, model) pair answered; `review` applies
    reviewer verdicts to `final_decision`/`final_value`; resume keys include the prompt
    sha, which now covers field specs and vocabularies; failed CUAD repairs are retried;
    evidence grounding is stricter (full dates, exact money with decimals and sign, the
    value in its own evidence quote, `never_absent` absences adjudicated); the contract
    calibration set no longer shows the expert spans it is scored against; corporate rows
    with no subclass get the `other` fields.
- DMR-091 independent source audit: join cross-config metadata, text, provenance,
  split and MAUD reports by unique filename, rejecting ambiguous identities.
  Verify staged parquet class/subclass routing against JSONL, and retain native
  reply edges when a parent occurs later in input. No datasets regenerated or
  published; existing presence/null predicates and corpus pins are unchanged.

## v9.4 — dedup + label-completion (STAGING, 2026-11-04)

STAGING revision over the v9.3 stage (plan `docs/plans/v9.4-dedup-label-completion.md`;
module `src/mailroom_eda/v9_4_revision.py`). Nothing is published. Gates:
`scripts/audit/verify_v9_4_conformance.py`, where (a)–(g) pass and (h) calibration is
PENDING, so the payload is not publish-ready yet.

- **Duplicates removed, 3,302 → 3,292 rows** (train 2,971 / test 321).
  - There were 6 `content_sha256` groups: CUAD 1, MAUD 1, EX-10 1 group of 3, and
    corporate 3 groups, one of which leaked across train and test.
  - Rule: richest annotation, then no `(1)` copy marker, then `train`, then smallest
    filename. Conflicting expert annotations (CUAD ADUROBIOTECH, MAUD
    contract_148/149) are recorded in `dedup_removed.jsonl`, never merged.
  - MAUD `contract_148`/`contract_149` is an upstream defect: MAUD v1 (Zenodo
    7500064) ships `contract_149.txt` as a byte copy of `contract_148.txt`. MAUD's
    own evidence excerpts match `contract_148` (57/71) and not `contract_149` (0/75),
    so `contract_148` is kept by an evidence-backed override
    (`UPSTREAM_DEFECT_SURVIVORS`, checked by gate (a)) instead of the richness rule,
    which had kept the mislabeled copy. `scripts/audit/verify_maud_duplicate.py
    --check` re-derives the evidence.
- **Placeholder hygiene**: 2,100 `"nan"` cells on 350 GNOTHEIA/BDR insurance rows are
  set to `""`. Presence codes are unchanged (gate).
- **CUAD labels on SEC EDGAR EX-10 (issue #30)**: none ingested yet; all 89 rows stay
  `pending_annotation`. The labeler moves to `Qwen/Qwen3-32B-FP8` on one H100, and
  `LABEL_MODEL` makes it the only model whose journal records are read. The first
  `Qwen/Qwen3-14B-AWQ` pass is retired: 126 of its spans failed re-verification and
  the spot check found category mix-ups. Labels are ingested only after the model
  passes calibration. Each span must also re-verify verbatim at an in-bounds offset,
  use the closed 41-category vocabulary, pass the semantic probes on 12 categories
  and the heading-only check, and meet the quality floor.
- **Flagged intents**: a two-pass agreement rule and the Modal runbook are in place;
  the 25 rows stay `flagged_review` until the pass runs.
- **Calibration gates ingest, not just publishing**: CUAD presence micro-F1 ≥ 0.75
  and micro-precision ≥ 0.80 on the fixed 40-contract golden sample; intent κ ≥ 0.75
  on all 96 manual labels. The draft's 0.6 floors were raised because small Qwen3
  models score 0.57–0.64. A calibration journal must cover its whole sample (else
  `incomplete`) and come from `LABEL_MODEL` (else `model_mismatch`). Until it
  passes, the matching label journal is held.
- `dataset_info.json` for ground_truth no longer declares the removed `_published` column.
- Gate (h) re-derives calibration from the scored details and re-scores each journal
  (sha256-checked) instead of trusting the report. Gate (f) compares every ground_truth
  column and the blind `default` config (row set, `doc_text`, 4 label-free columns).
  Gate (g) requires the manifest to list every staged file. The blind-config check no
  longer uses `assert`.
- Gates are explicit checks that still run under `python -O`. Gate (b) allows each fill only on
  the rows it applies to. Each journal record must name the row's `document_id`, and the
  journal's sha256 and model are recorded. Gate (a) proves each removed row is a
  byte-identical copy of a staged survivor; gate (d) recomputes presence instead of
  trusting the stored map; the report is bound to a digest of the staged files. Staging
  refuses a target that overlaps the v9.3 input. Two rebuilds produce byte-identical output.
- **Cross-split near-duplicate dropped from test**: EX-3 board resolution
  `0000950137-06-011083_n08078exv3w03.htm` matches two train rows after whitespace
  normalization (2 characters differ, labels identical). It is removed as a
  `near_duplicate_test_drop` record (pinned; gate (a) recomputes the normalized hash
  and checks test → train). It never lends a CUAD journal record. The remaining 3
  same-split near-duplicate groups are reported only.
- Audit outputs: `docs/reports/audits/v9_4_dedup_label_audit.*`, a
  `v9_4_label_review.*` spot-check pack, and `v9_4_queues/`.
- Operator runbook for the GPU steps (calibrate first, then label, then re-stage):
  `docs/runbooks/v9.4-modal-handoff.md`.

## v9.3 — corporate presence-layer labels (STAGING, 2026-10-17)

STAGING release on branch `feat/v9.3-staging-corporate-presence` (draft plan:
`docs/plans/v9.3-corporate-presence-revision.md`; module:
`src/mailroom_eda/v9_3_revision.py`). **Label-only** over the published
`corporate-labels-v9.2.1` content — the same 3,302 rows (train 2,979 /
test 323, corporate 450), every identity/content column and every populated
label value byte-identical. Nothing here pushes tags or the HF Hub; the
staged payload waits for the promotion run. Formal gates:
`scripts/audit/verify_v9_3_conformance.py` (a–g all PASS plus the registry
parity guard, skippable via `--skip-registry`; pytest 264 passed, 18 skipped —
the skips are the full-corpus tests that wait on the `data/` snapshot).

- **gt_presence key-set law unfrozen (the label-presence layer).** The
  uniform-law assertion `set(presence) == set(ALL_GT_FIELDS)` is relaxed to
  the v9.3 **superset** law: every row carries the 20-key `BASE_GT_FIELDS`
  base, keys ⊆ the 57-key `ALL_GT_FIELDS` registry ceiling, only
  `corporate_record` rows may extend with entity keys, unknown keys and
  non-vocabulary codes are rejected. `FIELD_KEYS_BY_CLASS["corporate_record"]`
  grows from the 3-key trio to trio + **37** entity keys (6 core + 31
  per-subclass extension) enumerated from
  `llm_dojo_scoring.corporate_record_schema.SUBCLASS_SCHEMAS` (sha-pinned
  mirror in `corporate_presence.py`; facet-parity-checked against the live
  dojo module).
- **The dojo unstated-blank regime is now expressible by the producer.**
  Blank entity fields with no absence statement are UNLABELED and ship as
  *absent keys* in `gt_presence` — never as a code. The published presence
  vocabulary is documented verbatim from the dojo docs
  (`llm-dojo-scoring/docs/CORPORATE_RECORD_SCHEMA.md`) and on the dataset
  card's v9.3 section by the builder:

  > The four published codes: `populated` — a real expected value for this
  > document; `not_applicable` — the class never carries this field;
  > `schema_documented_absence` — the field exists in the class schema but
  > this document legitimately has none; `pending_annotation` — the label
  > backfill has not run yet. Plus a fifth, scorer-only unstated-blank ⇒
  > unlabeled regime: a key-entity field the dataset leaves blank *without*
  > documenting absence is **unlabeled**, not "expected empty", and is
  > excluded from both sides of the comparison (reported as
  > `unlabeled_fields` on the score payload), so neither a filled nor an
  > empty prediction is scored.
- **EDA-side relabel equivalents.** `corporate_presence.relabel_row` is a
  faithful, deterministic port of `corporate_record_relabel` — verified
  450/450 **byte-identical** to the published v9.2.1 corporate rows from the
  v9.2 base (canonical + raw-line), including the 12 subclass conflict
  re-assignments; guarded by hard-STOP shape checks (non-corporate
  pass-through, frozen pre-existing gt values, entity additions only).
- **`labels_version` adopted.** The manifest now versions labels separately
  from the dataset (`version` line unchanged — v1, canonically v9 family):
  `labels_version: v9.3 corporate gt_presence presence-layer …`, per the
  FAIR guidance that a label wave that doesn't change rows should not ride
  the dataset version. Manifest `revision:` line names the presence-layer
  change; README/manifest parquet-config rows untouched (no new configs).
- **Precedence honesty.** No new label facts: the entity presence on the
  450 corporate rows is exactly the v9.2.1 relabel's derivations
  (`entity_name`/`record_type` pins, not-applicable core marks).

## v9.2 — 2026-10-06

Code-only revision of the live v9.1 tip of the
`Lucius-Morningstar/mailroom-dataset` corpus (plan:
`docs/plans/v9.2-relations-provenance-revision.md`; module:
`src/mailroom_eda/v9_2_revision.py`). No new document sourcing; zero
row-set/identity/content drift — the same 3,302 rows (train 2,979 / test 323)
as v9.1 (`bc9eab28…` / data `ed7576b6…`). Published as Hub tag **`v9.2`**
(docs/card commit `670e8bc6…`, data commit `d49f60a4…`).

- **C1 — annotation provenance SSOT.** Published `annotation_method` now
  carries the six §20 regimes (`verified_join` 162 · `llm_zero_shot` 637 ·
  `human_annotated` 96 · `synthetic` 1,100 · `heuristic` 646 ·
  `source_native` 661) instead of only `source_native` + `synthetic`;
  `annotation_source` aligns to the published `source_corpus` (SEC EDGAR
  EX-10 contracts move to `sec_edgar`; CUAD 600→509, EDGAR 450→541).
- **C2 — `_published` removal.** Dropped the undocumented build-internal
  `_published` migration column; the `ground_truth` parquet is now **35**
  top-level columns (was 36). The nested `gt_fields` union is **34** keys
  (32 scalar + 2 matter lists). Staging rejects any `_`-prefixed key.
- **C3 — relations completion.** `related_document_ids` is populated on the
  23 heuristic matter rows (9 subject threads, all-pairs `document_id`
  targets); the nested `gt_fields.relationships` / `related_document_ids`
  mirrors are canonical copies of the top-level columns.
- **C4 — `source_revision` override gate** extended to SEC EDGAR EX-10
  contracts, aligning with `source_corpus` (zero live effect today).
- **C5 — GT registry single-sourced.** `release_sections.GT_SCALAR_KEYS`
  (32) / `LEGACY_V7_GT_KEYS` (27) replace three stale inline 27-key lists.
- **P1 audit correction (reporting).** `integrity.audit_maud_labels` read the
  MAUD annotation count from the **blind** `metadata` blob; the v9.1 B1 fix
  moved `maud_label_count` to GT-only `gt_fields`, so the regenerated report
  silently shipped a false `metadata_count_consistency_ok: 0/152`. The audit
  now reads the GT-side count, restoring a truthful `152/152`
  (`gt_fields.maud_label_count == sum(metadata.maud_categories)`); regression
  pinned in `tests/test_integrity.py`. No dataset/byte change — reports only.
- **Deferral.** Workstream S (bundles/streams/fixtures column overlay) is
  deferred to v9.3 (decision D8).
- **Docs.** `AGENTS.md` re-pinned to v9.2; `docs/dataset-cards/mailroom-dataset.md`
  corrected (GT-only clause counts; 35-column / 34-key schema);
  `config.py` pins `REPO_TAG="v9.2"`, `REPO_REVISION=670e8bc6…`,
  `REPO_DATA_REVISION=d49f60a4…`.
