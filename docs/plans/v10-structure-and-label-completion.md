# v10 plan: structure, cleanup and label completion

Status: **DRAFT, not executed.** Nothing is published. Companion documents:
`docs/plans/v10-consumer-patches.md` (consumer follow-ups) and the labeling playbook
`src/mailroom_eda/labeling/` (prompts, model registry, campaign runbook).

## Context

The v9.4 stage (merged in Exios66/Mailroom-Corpus-EDA#41) is free of duplicates, but it still can't support classification **and** entity extraction scoring on every document:

- **Labels are missing.** These fields are 0% labeled:
  - contract: `document_name`, `parties`, `effective_date`, `term_length`, `governing_law`, `contract_value`, `renewal_terms`, `cuad_family`
  - merger: all 9 non-MAUD fields
  - correspondence: `sender`, `recipient`, `additional_recipients`, `communication_type`, `communication_date`, `demand_amount`, `action_items`, `urgency`
  - insurance: `claim_checklist`
  - corporate: `effective_date`, `signatories`, `jurisdiction`, `filing_number`, plus 31 subclass keys (2,933 fields)

  On top of that, 89 EX-10 contracts have no CUAD labels and 25 intents are flagged for review.
- **The layout is hard for scorers and LLMs to read.**
  - All labels sit in one `gt_fields` JSON string, and some values inside it are JSON-encoded a second time.
  - Each row carries 20–36 keys, most of them `not_applicable`.
  - Every value is a string.
  - Some columns are empty, constant or duplicated.
  - Build-internal metadata is mixed in with the data.
- **The card is stale and reads like a build log.** It still says 3,302 rows. It has no quick start, field table, example rows, annotation process, PII section or limitations, and it declares a single license that conflicts with the per-source licenses.

The user also wants these entity-schema changes:
- Correspondence, insurance and corporate keep their current Dojo schemas.
- **Contracts and mergers get their own adapted entity schemas**, separate from the CUAD clause checklist and the MAUD questions.

### Alignment with the live Hub (checked 2026-11-06)

- The Hub `main` is still the v9.2 data (`670e8bc6`, tags `v9.1` and `v9.2`, plus the
  `corporate-labels-v9.2.1` branch). v9.3 and v9.4 exist only as local stages, rebuilt from
  that tag by `labeling stage`. v10 builds from the v9.4 stage, not from anything newer
  on the Hub.
- A label-status export of the "core" subset (`mailroom_core.csv`, 2,379 rows, dated
  2026-11-03) agrees with v9.2 on every shared row: split, subtype, `annotation_method`,
  `intent`, text length and context band. It is the v9.4 row set minus the 800 template
  insurance rows (600 CMS DE-SynPUF and 200 GNOTHEIA), the 89 EX-10 contracts, and the 25
  flagged intents. It predates the near-duplicate test drop, so it still has
  `0000950137-06-011083_n08078exv3w03.htm` (445 corporate records, where v9.4 has 444).
- Scope (user decision, 2026-11-06): v10 labels **all 1,100** insurance rows, not only
  the 300 in the core subset. `communication_date` stays **text-only**: the export's
  `sent_date` comes from `metadata.date`, and that remains a cross-check, not gold.

### Decisions (asked and answered)

1. The labeling playbook is an **in-repo package** in the EDA repo.
2. **v10 adds new per-class configs and keeps the legacy `ground_truth` config**, regenerated and marked deprecated.
3. The coverage target is **the fields the Dojo scores**, plus the adapted contract and merger schemas.
4. Models return schema-checked JSON, and the runner writes the CSVs.

### Consumer facts that constrain the plan (verified read-only)

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

## Part A: entity schemas (Dojo is the owner; the dataset and llm-mailroom mirror it)

There is one source of truth: the Dojo `EntitySchema`/`CuadField` catalogs. The EDA `labeling/fields.yaml` pins the Dojo commit and has a parity test against it. llm-mailroom `schemas/documents.py` and `taxonomy.yaml` mirror it.

### A1. `ContractExtraction` v2 (dojo `cuad_schema.CONTRACT_EXTRACTION_FIELDS`)

Kept from today:
- `document_name`, `parties`, `effective_date`, `term_length`, `governing_law` (sentence), `contract_value`, `renewal_terms`
- `cuad_family`, **retyped to `label`** (closed: the 25 keys + `other`)
- `cuad_clauses`, the CUAD checklist, unchanged

**Removed from the contract live map:** the parse-compat `merger_consideration` and `maud_clauses`. They are still accepted when parsing, but not scored, which fixes the contract headline that can never be "full".

**New entity fields:**

| field | type | rule |
|---|---|---|
| `party_roles` | entity_list:free_text | `"<party>: <role>"`, with role one of licensor/licensee/supplier/customer/distributor/reseller/franchisor/franchisee/service_provider/client/sponsor/agent/principal/partner/lender/borrower/other |
| `signatories` | entity_list:name | persons who sign, as printed |
| `agreement_date` | date | the "dated as of" or signature date, ISO |
| `expiration_date` | date | end of the initial term when stated as a date; absent_ok |
| `termination_notice_period` | free_text | notice needed to terminate or to stop a renewal; absent_ok |
| `payment_terms` | free_text | how and when consideration is paid; absent_ok |
| `territory` | free_text | the stated territory; absent_ok (promoted from the family hints) |
| `dispute_forum` | name | courts or arbitral seat for disputes, kept separate from governing law; absent_ok |

The family-specific `FamilyField` hints (royalty_rate, commission_rate, …) stay as unscored prompt aids.

### A2. `MergerAgreementExtraction` v2 (dojo `maud_schema`)

Kept from today:
- `document_name`, `parties`, `effective_date` (null when only an Effective Time is defined), `effective_time`, `governing_law` (sentence), `subject_matter`, `keywords`
- `maud_clauses`, the MAUD checklist, unchanged
- `merger_consideration`, **retyped to `label`** (`all_cash|all_stock|mixed_cash_stock|mixed_cash_stock_election|other`)
- `intent`, **retyped to `label`** (closed: `effect_merger|amend_merger|other`, with `plan_of_merger` aliased to `effect_merger`)

**New entity fields:**

| field | type | rule |
|---|---|---|
| `acquirer` | name | the Parent, as printed |
| `target` | name | the Company, as printed |
| `merger_sub` | entity_list:name | the merger subsidiary or subsidiaries; absent_ok |
| `signatories` | entity_list:name | persons signing for each party |
| `per_share_cash_amount` | money | absent_ok (promoted from the hint) |
| `exchange_ratio` | free_text | absent_ok (promoted from the hint) |
| `target_termination_fee` | money | absent_ok |
| `parent_termination_fee` | money | reverse termination fee; absent_ok |
| `outside_date` | date | End/Outside Date; ISO, or as printed if relative |
| `financial_advisors` | entity_list:name | from the brokers and fairness sections; absent_ok |
| `legal_counsel` | entity_list:name | from the notice "with a copy to" lines; absent_ok |

### A3. Other classes and shared fixes

- Correspondence, insurance and corporate keep their schemas.
- Retype these from `name` to `label`, with closed lists: `urgency` (routine|time-sensitive|urgent|critical; **never absent**, defaults to routine), `communication_type` (8 tokens + other), insurance `claim_type`, and `coverage_determination`.
- Corporate: fix the `not_applicable` inconsistency.
- `claim_checklist` keeps the 10 exact category names.

## Part B: playbook package (`src/mailroom_eda/labeling/`, package data, replayable)

```
labeling/README.md         the strategy playbook: design → calibrate → chunk → gate → adjudicate → ingest
labeling/fields.yaml       class → field → dojo type, source (deterministic|gold|llm), absence_policy
                           (absent_ok | evidence_only | never_absent), normaliser, evidence_required,
                           prompt hint; pinned dojo commit + parity test
labeling/models.yaml       approved configs (id, provider, prices, checked date, tasks)
labeling/prompts/*.md      every prompt (Part E), sha256 recorded per record
labeling/campaigns/v10/    campaign.yaml (chunk order, caps, gates), costs.md (generated), runbook.md
labeling/{providers,spend,runner,verify,export}.py
```

- **Providers:**
  - `modal` uses `VLLM_*`; `openrouter` uses `OPENROUTER_API_KEY`.
  - Requests use `temperature 0`, strict json_schema, `provider{require_parameters:true, only:[pinned providers]}` (fallbacks only within the pinned list), `seed 42` where the pinned endpoints accept it, `reasoning{enabled:false}` or `{effort:"low"}`, and `usage{include:true}`.
  - Retries on 429/5xx with backoff. Falls back to `json_object` when a schema request is rejected.
  - Records the served model, the provider, usage and cost.
- **Spend:**
  - Required: `--initials` (a human's), `--api-cost-cap-usd`, `--max-wall-seconds`.
  - Checks key headroom against `/api/v1/key` with a $3 reserve.
  - A running cap stops the next call that could exceed it. `--dry-run` projects the cost.
- **Outputs per chunk** (`docs/reports/audits/v10_labels/<chunk>/`):
  - a JSONL journal;
  - `documents.csv`: one row per `document_id`, with status, problems, configuration, tokens and cost;
  - `fields.csv`: one row per (document_id, field), with each model's value, status and evidence, `agree`, `verbatim_ok`, `decision`, and an empty `reviewer_verdict` column;
  - `spans.csv`: CUAD only;
  - `adjudication.csv`.
- **Calibration binds a configuration tuple** (model, provider, reasoning, prompt_sha, temperature). Ingest refuses anything uncalibrated. This also fixes the v9.4 `model_mismatch` trap.

## Part C: v10 build (`src/mailroom_eda/v10_build.py`, input is the v9.4 stage)

### C1. Cleanup (minimal byte churn, because consumer caches hash `doc_text`)

- **`doc_text`:** only the rows that need it (about 200: `\r` 149, control characters 46, NBSP 9) get CRLF/CR changed to LF, C0 control characters other than `\n`/`\t` removed, and NBSP changed to a space.
  - Every span and offset is remapped and re-verified (gate: zero spans lost).
  - `content_sha256` is recomputed **in every config, the legacy one included**. The old value is kept as `source_content_sha256`.
  - Also remap the journals that already exist in the sandbox (CUAD EX-10) and the interim overlay.
- **Types:** native numbers, bools, ISO dates and lists. The 6 `N/A` dates become null with a presence code.
- **Subclasses:** the legacy `expected_subclass` raw values **stay unchanged**, so llm-mailroom stratification and sandbox strata keys still work. The new configs carry `subclass` (the Dojo canonical key) plus `source_subclass` (the raw value).
- **The 56 merger rows with subclass `other`** are kept. They are MAUD rows with full expert clause labels (15–17 MAUD categories each, like the other 95) and lack only the expert Type of Consideration answer. Chunks `6c0`/`6c1` (`merger_consideration` task, Part E) label that answer, so in the new configs:
  - each of the 56 gets `subclass` = its adjudicated `merger_consideration` label, with `provenance.subclass = "llm:<configuration>"`; `source_subclass` and the legacy `expected_subclass` stay `other`;
  - a row a human adjudicates as genuinely `other` keeps `other`, with its evidence quote;
  - `merger_consideration` is `populated` on all 151 mergers (95 from the expert answer, 56 from the task), and the 95 keep the expert value whatever the models answered in calibration.
- **Splits are unchanged.** The md5 rule is untouched and no row is added or removed: 3,292 rows (train 2,971 / test 321) and 151 mergers. Only the merger subclass distribution in the new configs changes (today all_cash 57, all_stock 24, mixed_cash_stock 13, mixed_cash_stock_election 1, other 56; the 56 split 48 train / 8 test); the card's composition and per-subclass tables are rendered from the data, so they follow.
- **New configs drop:** the empty and constant columns, `source_filename`, the nested mirrors, and build-internal metadata keys.
- **`default` is blind:** `document_id`, `doc_text` and the empty `prompt`, nothing else. It stays the **first config**, because llm-mailroom calls `load_dataset` without a config name.
  - **No `metadata`:** a leak scan on the v9.4 stage found metadata values that give away the class, the subclass and gold values (`source_row_id` equals `claim_number`).
  - **No `filename`:** the source filename names the subclass or class on 1,346 of 3,292 rows, test split included: `carrier:…`, `inpatient:…`, `auto:CLM-000803.txt` (subclass plus the gold claim number; 96 of 114 test insurance rows), `contract_#_merger_agreement.txt` (all 17 test mergers), CUAD names such as `…_Co-Branding Agreement_ Agency Agreement.pdf` (46 of 60 test contracts). `document_id` is an opaque hash (`DOC-` plus 16 hex digits, checked on every staged row) and is the join key.
  - `doc_text` carries no build-injected header; its first lines are the documents' own titles.
- **`source_metadata`** (non-blind, like the GT configs): `document_id`, `filename` and `metadata` minus the build-internal keys. `gt_label_ex10` and anything else that needs the source fields reads it.
- **Labels land only in the GT-tier configs** (`ground_truth`, `gt_<class>`, `routing`, `cuad_clauses`, `maud_answers`), never in `default`. The labeling journals under `docs/reports/audits/v10_labels/` are build inputs, not dataset configs.
- **`prompt`** is dropped from the new configs. It stays as an empty column in the legacy and default configs for one release.
- **Fixes:**
  - `expected_specialist` comes from the taxonomy (`merger_agreement_specialist`).
  - The dedup wording is corrected: 9 exact copies in 6 groups, plus 1 near-duplicate dropped from test.

### C2. Layout

| config | rows | contents |
|---|---|---|
| `default` (blind) | 3,292 | as above |
| `routing` | 3,292 | `document_id, filename, doc_class, subclass, source_subclass, expected_specialist, intent, label_status` |
| `gt_contract` / `gt_merger_agreement` / `gt_correspondence` / `gt_insurance_claim` / `gt_corporate_record` | 597 / 151 / 1,000 / 1,100 / 444 | identity, one typed column per schema field, `presence` struct, `evidence` struct (list of start/end/text), `provenance` struct |
| `cuad_clauses` / `maud_answers` | long | one span per row / one answer per row |
| `bundles` / `streams` / `fixtures` | as today | retyped |
| `ground_truth` (deprecated) | 3,292 | v9.4 shape; `gt_fields` now **includes every new label**, so the legacy Dojo path scores whatever its schema knows |

`dataset_info.json` carries splits and `num_examples`. Shards keep the `-00000-of-00001` naming. The sandbox hard-codes those names; a patch replaces that with a glob.

### C3. Gates (`scripts/audit/verify_v10_conformance.py`)

1. The row set equals v9.4.
2. The cleanup diff stays inside its allowlist, and every hash matches its `doc_text` in every config.
3. Spans and evidence re-verify.
4. `fields.yaml` matches the pinned Dojo.
5. Each class schema matches `fields.yaml`.
6. The legacy `ground_truth` round-trips to v9.4 semantics plus the listed additions.
7. The blind config is label-free.
8. The manifest is complete.
9. Calibration is re-derived per configuration.
10. **Merger consideration:** every merger has a non-null `merger_consideration` with an expert source or an evidence quote, and `subclass` equals it on every merger row.
11. **Coverage:** every applicable field on every row is `populated` or `schema_documented_absence`, otherwise the residue is listed. `publish_ready` requires the residue to be 0 or accepted by the user.
12. Every number on the card is rendered from the data (`--check`).
13. Every config loads offline with `datasets` from local parquet.

### C4. Dataset card (`src/mailroom_eda/card_v10.py`, rendered from data and `fields.yaml`)

**YAML:**
- `pretty_name`
- `license: other` plus `license_name`/`license_link`
- `task_categories` (text-classification, token-classification, question-answering) and `task_ids`
- `annotations_creators`, `language_creators`, `multilinguality`, `source_datasets`, `size_categories`, `tags`
- `configs`, with `default` first
- `dataset_info`

**Body:**
1. A pitch and a composition table.
2. Quick start (load `default`, `routing` and one `gt_*` config; join on `document_id`; a scoring snippet).
3. Supported tasks.
4. Structure: configs, a per-class field table (type, description, coverage %), and one example per class.
5. Splits and the md5 split rule.
6. Curation rationale.
7. Sources and per-source licenses.
8. Annotation process: methods, models, calibration floors, human-reviewed share.
9. PII: the Enron corpus contains personal data; the CMS data is synthetic.
10. Bias and limitations.
11. A versioning table linking to the CHANGELOG.
12. Citation and contributions.

Layout uses clean tables, collapsible long lists, and no internal ticket ids in the prose. `docs/dataset-cards/mailroom-dataset.md` becomes the generated copy.

## Part D: model choice, reasoning, costs

**Models** (live OpenRouter prices on 2026-11-06, $ per million tokens, input / output, at the priciest pinned endpoint):
- **Legal primary:** `z-ai/glm-5.3-flashx-legal` (0.37 / 1.25).
- **General primary and legal second opinion:** `deepseek/deepseek-v4.1-flash` (0.15 / 0.65).
- **Non-legal second opinion:** `minimax/minimax-m3.1-flash` (0.15 / 0.60).
- **Fallback:** Modal `Qwen/Qwen3-32B-FP8`.
- **Excluded:** `qwen/qwen3-32b` on OpenRouter (2025 model, about 40k context served), Kimi K3.1, and Gemini 3.8 Flash (5–20× the price).
- Calibration picks the model; reputation does not.

**Provider routing** (the `route:` block per model in `models.yaml`; checked live with `labeling prices`):
- Every request sends `provider.require_parameters: true`, so OpenRouter only routes to endpoints that accept every parameter we send (strict `json_schema`, `reasoning`, `max_tokens`, `temperature`).
- Each model's providers are pinned with `only`, so the served model and price are known: GLM-legal on `z-ai`; DeepSeek on `deepinfra`, `atlas-cloud`, `coreweave` and `inference-net` at fp8 with a `max_price` ceiling (cheaper fp4 endpoints are excluded); MiniMax on `parasail`, `together`, `fireworks` and `sambanova`. Fallbacks are allowed only within the pinned list.
- A `by_reasoning` overlay narrows the pin per reasoning level, because providers do not honour `low` alike (2026-11-06 per-provider pings, `labeling ping --per-provider`): DeepSeek `low` runs on `atlas-cloud`, `coreweave` and `inference-net` (DeepInfra spent 0 reasoning tokens on a real CUAD document; only `ping --per-provider --real-doc` shows this, since every provider reasons on the tiny ping). At `low` DeepSeek's reasoning length is erratic on any provider (336 to over 2,300 tokens on the same email), so the output allowance for reasoning is 6,144 tokens: a truncated answer is billed and discarded, unused room is free; MiniMax `low` runs on `fireworks` and `sambanova` (Parasail and Together reported 0 reasoning tokens). A gate fails when any call was served outside its pin, or when most of a provider's answers at `low` spent 0 reasoning tokens.
- `seed: 42` is sent only where every pinned endpoint accepts it (DeepSeek). The z-ai GLM endpoint does not list `seed`, and `require_parameters` would reject the request, so GLM runs at temperature 0 without a seed.
- `models.yaml` prices are the priciest pinned endpoint, so projections and caps are upper bounds. Every journal record keeps the served provider.

**Reasoning:**
- **Off** for span and entity extraction where the model allows it. It is a copying task, and reasoning tokens are billed as output.
- **Re-plan (2026-11-06):** GLM-legal and MiniMax now refuse reasoning off (HTTP 400 "Reasoning is mandatory for this endpoint"). Chunks 7 and 8 (DeepSeek + MiniMax) therefore run at `low` for both models, so both answer under one configuration; projected $15.66 (was $14.72), every chunk inside its cap. Chunk 1 arm 1 (GLM off) is skipped. Chunks 5, 6 and 9 still list GLM at off and must be re-planned to `low` before they run.
- The CUAD bake-off tests GLM with `low`; it is adopted only if micro-F1 improves by at least 0.02.
- **Low** is tried for intent (chunk 3 runs off and low and keeps the better kappa). Urgency is labeled inside the correspondence extraction call, and `communication_type` is deterministic (the subclass).
- **Never `high`.**

**Token budget** (one model, every task once, windowed prompts measured by `labeling costs`): about **18.4M prompt / 3.4M output tokens**, including a 1.25x allowance for retries and CUAD repair calls. Merger windows add the regex-located Termination Fee, Outside Date, Brokers and Notices sections, and the `merger_consideration` window adds the Conversion of Shares, Merger Consideration and Election sections.

**Single-model campaign cost** (from `src/mailroom_eda/labeling/campaigns/v10/costs.md`, prices checked 2026-11-06):

| model | cost |
|---|---|
| gpt-6-luna | $3.55 |
| qwen3.8-flash | $4.37 |
| glm-5.3-flash | $4.47 |
| minimax-m3.1-flash | $4.81 |
| deepseek-v4.1-flash | $4.98 |
| deepseek-v4.1-flash-1017 | $9.62 |
| glm-5.3-flashx-legal | $11.08 |
| gemini-3.5-flash-lite | $14.05 |
| Modal Qwen3-32B | about $8–12 of H100 time (`costs.md` shows $126.67: the conservative token-equivalent price in `models.yaml` that keeps the cap safe, not an estimate) |

**Recommended mix:**
- GLM-legal + DeepSeek for contracts, mergers and corporate records.
- DeepSeek + MiniMax for correspondence and insurance.
- **About $14.68 projected (two models on every extraction chunk, all three CUAD bake-off arms, both intent arms); the per-chunk caps add up to $28.90.** The chunk-by-chunk table is in `costs.md`.
- Prices are re-checked before every chunk, and `costs.md` is regenerated (`labeling costs`; `--check` fails when it is stale).

## Part E: prompts

The prompts live in `src/mailroom_eda/labeling/prompts/` and are copied here verbatim. `${name}` placeholders are filled by `labeling/tasks.py`; rendering fails on a missing or unexpected value. Every call runs at temperature 0 with a strict JSON schema, and every journal record stores the sha256 of the prompt bundle it used, so a calibration only covers the exact text it was run with. The extraction tasks send `extract_rules.md` as the system prompt and the task prompt as the user turn.

### `cuad_system.md`

CUAD clause spans, system prompt (one model, calibrated on the 40 golden CUAD contracts).

```text
You are a contract analyst annotating one SEC EDGAR EX-10 exhibit in the style of the Atticus
Project CUAD dataset, so the result can sit beside expert CUAD annotations.
Task: find every CUAD category that is PRESENT and copy the short operative span(s) that establish it.
Output one JSON object, nothing else: {"clauses": [{"category": "<exact name>", "text": "<verbatim span>"}]}
1. category is exactly one of the 41 names in the user message.
2. text is copied character for character (same words, order, punctuation, capitalisation). Never
   paraphrase, summarise, fix typos or join text from two places. Markdown marks (* _ #) may be dropped.
3. Copy the operative sentence or clause, not a whole article and not a heading. A heading alone
   ("Governing Law.", "ARTICLE 3 CONFIDENTIALITY AND NON-COMPETITION") is never a span.
4. Presence test. Each category in the user message is a question. Add the category only when the span
   itself answers that question YES. Matching a keyword is not enough.
   - A clause that denies, disclaims or excludes the thing makes the category ABSENT: "not exclusive",
     "non-exclusive", "no third-party beneficiaries", "shall not be considered a renewal", "neither
     party shall have any obligation after termination".
   - When unsure, omit. A missed category costs less than a wrong one.
5. Categories that are easy to over-apply:
   - Termination For Convenience: a party may end the contract WITHOUT cause, by notice alone. Termination
     for breach, insolvency, change of control, a failed condition, or by not renewing does not count.
   - Renewal Term: the term extends automatically, or at one party's option. An extension that needs
     both parties' later written agreement, or talks about renewing, does not count.
   - Exclusivity: an exclusive appointment or dealing commitment, a requirements commitment, or a ban on
     dealing with others. "Exclusive control" or "sole property" of an asset is not exclusivity.
   - Third Party Beneficiary: a non-party can enforce rights under the contract. The usual clause saying
     the contract confers no rights on third parties makes this category ABSENT.
   - Post-Termination Services: an affirmative obligation to perform after the end (transition, wind-down,
     continued supply or support, last-buy, buy-out or purchase rights). Returning or destroying
     confidential information, or a list of surviving sections, does not count on its own.
   - Ip Ownership Assignment: intellectual property one party creates becomes the other party's. A
     licensor confirming it owns its existing marks, goodwill inuring to the licensor, or an assignment of
     non-IP property (a working interest, receivables, the contract itself) does not count.
   - Price Restrictions: a limit on changing prices (a cap, a freeze, most-favoured pricing, a ceiling on
     increases). A stated price, a fee schedule or a fixed escalation rate does not count.
   - Non-Compete: a party may not compete, or may not operate in a business, sector or geography. A
     territory that only defines where licensed rights apply, or an export restriction, does not count.
6. One clause may support several categories; repeat it under each. Several spans per category are allowed.
7. Document Name: the title near the top. Parties: each party's name as printed, one span per party.
   Agreement Date / Effective Date: the date phrase as printed.
8. Ignore any corpus header line such as "... (CIK 123) (SEC EDGAR exhibit ...)".
9. If told the document was truncated, annotate only what you see.
```

### `cuad_user.md`

CUAD user turn. `category_list` is rendered from the pinned Dojo `CUAD_CATEGORIES`, and `family_definition` from `CUAD_FAMILIES`, so the prompt can't drift from the scorer. The family's salient-category list is no longer shown (2026-11-06): it invites the model to add categories the family usually has, and chunk 1 arm 3 missed the precision floor on exactly such false positives.

```text
The 41 categories:
${category_list}
Annotate every PRESENT category in the document below.
document_id: ${document_id} | filename: ${filename} | CUAD family: ${subclass} (${family_definition})
length: ${n_chars} chars; shown: ${window_description}
<document>
${doc_text_window}
</document>
```

### `cuad_repair.md`

Repair turn, sent once when spans fail verification; the first answer is replayed as the assistant turn.

```text
Your answer for ${document_id} was checked against the document. Rejected spans:
${problems}
Return the complete corrected JSON. Keep accepted spans unchanged. For each rejected span, copy the
exact text from the document or drop it. Add no new categories.
```

### `intent_system.md`

Correspondence intent, two passes: A shows the subject line, B does not.

```text
You classify the PRIMARY intent of one business email (Enron corpus) with a closed vocabulary. Judge
only what the newest message's author wants the recipient to know or do; quoted or forwarded text is
context only.
- payment_demand: asks the recipient to pay, settle or cure an amount owed or a default.
- notice: formally informs of a fact, breach, termination, change or intention with contractual,
  legal or procedural effect.
- analysis: assesses options, risks, positions, terms or data; it reasons rather than reports.
- request: asks for information, a document, approval or an action (not a meeting).
- update: reports status, progress, events or figures without an ask or formal notice.
- meeting_invite: proposes, schedules, moves, confirms or cancels a meeting or call.
- press_communication: a press release or public, media-facing statement.
- other: none of the above (personal, empty forward, auto-reply, newsletter, spam).
Tie-breaks: an explicit ask beats an update; a meeting ask is meeting_invite; a formal notice beats an
update; use other only when no label fits, never for uncertainty.
Output only {"intent": "<label>", "confidence": <0-1>, "evidence": "<verbatim quote, max 200 chars, from the newest message>"}
```

### `intent_user.md`

```text
document_id: ${document_id}
${subject_line}
<email>
${doc_text_window}
</email>
```

### `extract_rules.md`

Shared system prompt for every extraction task.

```text
You extract fields from ONE document for a ground-truth dataset. For every listed field return
{"value": <typed value or null>, "status": "present"|"documented_absent"|"not_found",
 "evidence": "<verbatim quote, max 300 chars>"}.
- present: the document states it; evidence is copied character for character and contains the value
  (or the date it was parsed from).
- documented_absent: the document itself says the field does not exist or apply; evidence is that
  statement; value null.
- not_found: not stated; value null, evidence "". Never guess, never infer from the file name or the
  corpus header "(CIK ...) (SEC EDGAR exhibit ...)", never use outside knowledge.
Formats. name: as printed, no honorifics. entity_list:name: JSON array in document order, no duplicates.
date: ISO YYYY-MM-DD when the day is printed, else as printed. money: as printed with currency
("$250,000,000"); $0 is a stated amount; never compute. id: exactly as printed. free_text: max 300 chars,
grounded. label: exactly one value from the list given.
Return {"fields": {...}} with exactly the listed fields and nothing else.
```

### `contract_entities.md`

ContractExtraction v2. `cuad_family` is not asked; it is set from the subclass. `expert_span_hints` lists the expert CUAD spans for Document Name, Parties, dates, Governing Law and Renewal Term when the row has them.

```text
Document: a commercial contract, CUAD family ${subclass} (${family_definition}). Fields:
- document_name (name): the contract's name as given (e.g. "Web Hosting Agreement"); never from the file name.
- parties (entity_list:name): each distinct contracting party, full legal name plus the parenthetical
  alias when printed ("Acme Corp. (\"Acme\")").
- party_roles (entity_list:free_text): "<party>: <role>" for each party, role one of ${party_roles}.
- signatories (entity_list:name): the people who sign, as printed in the signature blocks.
- agreement_date (date): the "dated as of" or signature date.
- effective_date (date): when the agreement takes effect; a defined Effective Date wins over the
  signature date.
- expiration_date (date): the stated end date of the initial term; not_found if only a duration is stated.
- term_length (free_text): the duration of the agreement itself, not a sub-period.
- renewal_terms (free_text): renewal, extension or evergreen language.
- termination_notice_period (free_text): notice required to terminate or to stop renewal.
- governing_law (name): copy ONLY the sentence naming the jurisdiction whose laws govern; no forum,
  venue or attorneys' fees.
- dispute_forum (name): the courts or arbitral seat for disputes, as printed.
- contract_value (money): stated monetary consideration; never computed.
- payment_terms (free_text): how and when the consideration is paid.
- territory (free_text): the stated territory.
Family hints (help you find the fields above; not output): ${family_field_hints}
${expert_span_hints}
<document>
${doc_text_window}
</document>
```

### `merger_entities.md`

MergerAgreementExtraction v2. `merger_consideration` is not asked here: it is its own task (`merger_consideration.md`, below).

```text
Document: a merger agreement (MAUD). Shown: ${window_description}. Fields:
- document_name (name): the agreement title as stated ("AGREEMENT AND PLAN OF MERGER").
- parties (entity_list:name): Parent, Merger Sub, the Company and any other contracting entity, as written.
- acquirer (name): the Parent / acquiring company. target (name): the Company being acquired.
- merger_sub (entity_list:name): the merger subsidiary or subsidiaries.
- signatories (entity_list:name): the people who sign for each party.
- effective_date (date): a calendar date only; not_found when only an Effective Time is defined.
- effective_time (free_text): how the Effective Time is defined, as written.
- governing_law (name): ONLY the governing-law sentence; no forum or venue.
- per_share_cash_amount (money): cash paid per target share. exchange_ratio (free_text): stock per
  target share, as written.
- target_termination_fee (money): the fee the Company pays on termination.
  parent_termination_fee (money): any reverse termination fee Parent pays.
- outside_date (date): the End Date / Outside Date (ISO, or as printed if relative).
- financial_advisors (entity_list:name): financial advisors or brokers named for any party.
- legal_counsel (entity_list:name): law firms named in the notice "with a copy to" lines.
- intent (label): ${merger_intents} (an amendment to an existing merger agreement is amend_merger).
- subject_matter (free_text): one grounded sentence on who acquires whom and how.
- keywords (entity_list:name): up to 8 phrases copied from the text.
<document>
${doc_text_window}
</document>
```

### `merger_consideration.md`

The merger Type of Consideration label. `merger_consideration` comes from the subclass where the expert MAUD "Type of Consideration" answer confirms it (95 rows). The 56 MAUD rows with subclass `other` have full MAUD clause labels but no consideration answer, so this task labels them. It runs on a window of the opening 6,000 characters plus the located Conversion of Shares, Merger Consideration and Election sections (under 20,000 characters on every merger in the stage). Chunk `6c0` asks it on the 95 rows with the expert answer and gates both models at 0.95 accuracy (`consideration_accuracy`, with a confusion table); only then does `6c1` label the 56, under the two-model agreement gate, with disagreements adjudicated. The 95 rows keep the expert value whatever the models answer.

```text
Document: a merger agreement (MAUD). Shown: ${window_description}. One field:
- merger_consideration (label): how the target's shares are paid for at the Effective Time,
  read from the Conversion of Shares / Effect on Capital Stock / Merger Consideration
  sections. One of: ${consideration_options}.
  - all_cash: each share converts into the right to receive cash only.
  - all_stock: each share converts into shares of the acquirer (or its parent) only, with
    cash only in lieu of fractional shares.
  - mixed_cash_stock: each share receives a fixed combination of cash and stock.
  - mixed_cash_stock_election: holders elect cash or stock (or a mix), usually subject to
    proration; any election between cash and stock is this, not mixed_cash_stock.
  - other: none of these (for example units, contingent value rights only, or no
    conversion of target shares at all). Say why in the evidence.
  Cash in lieu of fractional shares, dissenting shares and cancelled treasury shares do
  not change the answer. evidence: the verbatim sentence that converts each share.
<document>
${doc_text_window}
</document>
```

### `correspondence.md`

Correspondence entities. Most messages in the corpus have no headers (671 of 1,000), so sender, recipients and date are labeled only from what the text prints; `metadata.sender_addr` and `metadata.date` are cross-checks, not labels. `communication_type` is the subclass, so it is not asked.

```text
Document: one business message (${subclass}). Many messages in this corpus carry no headers;
label only what the text itself shows. Fields:
- sender (name): the author as printed in a From: header, or the sign-off name; not_found if neither.
- recipient (name): the main addressee as printed in a To: header, or the salutation name ("Dad,"
  -> "Dad"); not_found if neither.
- additional_recipients (entity_list): other To: and Cc: addressees as printed; [] with not_found if none.
- communication_date (date): a send date printed in the message (Date: or Sent: line), ISO
  YYYY-MM-DD; not_found if none is printed.
- demand_amount (money): an amount the author demands be paid; not_found if none.
- action_items (entity_list): at most 3 concrete actions the recipient is asked to take, each a short
  imperative grounded in the text; [] with not_found if none.
- urgency (label, never not_found): ${urgencies}. time-sensitive = a stated deadline or date; urgent =
  explicit urgency words or a same-day ask; critical = an emergency, legal deadline or outage;
  otherwise routine.
<email>
${doc_text_window}
</email>
```

### `claim_checklist.md`

Insurance claim checklist; every other insurance field is already gold.

```text
Document: one insurance claim record (${subclass}). Field:
- claim_checklist (entity_list:free_text): present-only items "<Category>: <short verbatim evidence>"
  for these exact categories only: ${checklist_categories}. Omit absent categories; if none apply use
  not_found. For tabular CMS rows, quote the column value as evidence.
<document>
${doc_text_window}
</document>
```

### `corporate.md`

Corporate records. `field_list` is rendered from the pinned Dojo `SUBCLASS_SCHEMAS` (description, where to look, type) for the row's subclass, plus the core fields.

```text
Document: a corporate record filed as an SEC EDGAR exhibit, subclass ${subclass}: ${subclass_description}.
filing_number = the number the state filing office assigned (file, document or entity number), NOT an
SEC accession number, CIK or Commission file number. jurisdiction = the state or country as printed.
Fields:
${field_list}
<document>
${doc_text_window}
</document>
```

## Part F: chunk sequence (the user launches each chunk; sign-off is required before the next)

The machine-readable sequence is `src/mailroom_eda/labeling/campaigns/v10/campaign.yaml`; row counts come from `labeling select`, and the projections from `costs.md`.

**Model re-plan (2026-11-06).** Only top-tier current models label: no older DeepSeek snapshots and no weaker models. Chunks 1 and 3 are bake-offs on the golden sets. The primary DeepSeek is the better of `deepseek-v4.1-flash` (2026-09-10) and the newer `deepseek-v4.1-flash-1017` (2026-10-17, about 2x the price). The second opinion for the two-model chunks is the candidate that scores at least as well as the primary on gold: GPT-6 Luna Pro, Claude Haiku 5.5 or MiniMax M3.1 Flash. GLM-5.3 FlashX Legal is the candidate for the contract and merger tasks. Chunks 5–9 are re-planned from the measured scores before any of them runs. Claude reads a sample of each arm's answers against the documents before a gate is approved. Claude Haiku 5.5 and GPT-6 Luna Pro endpoints take no `temperature`, so their routes omit it and the calibration records `temperature: null`.

| # | chunk | config | rows | cap | gate to proceed |
|---|---|---|---|---|---|
| 0 | build: cleanup, layout, specialist fix, deterministic fields, card | none | all | — | C3 gates 1–8, 12 and 13 pass. Deterministic fields: `cuad_family`, `merger_consideration`, `communication_type` |
| 1 | CUAD model bake-off | DeepSeek v4.1 Flash low / off, DeepSeek v4.1 Flash 1017 off, GLM-5.3 FlashX Legal low, GPT-6 Luna Pro, Claude Haiku 5.5, MiniMax M3.1 Flash low | 40 × 7 arms | $0.35 / $0.30 / $0.55 / $0.63 / $0.25 / $0.25 / $0.35 | micro-F1 ≥ 0.75 and P ≥ 0.80, else STOP |
| 2a–c | CUAD on EX-10 | winner of #1 | 89 (30/30/29) | $0.55 / $0.55 / $0.45 | spans re-verify; the user reviews `spans.csv` |
| 3 | intent model bake-off | DeepSeek v4.1 Flash off / low, DeepSeek v4.1 Flash 1017 off, GPT-6 Luna Pro, Claude Haiku 5.5, MiniMax M3.1 Flash low | 96 × 2 passes × 6 arms | $0.10 / $0.30 / $0.20 / $0.10 / $0.10 / $0.15 | κ ≥ 0.75 |
| 4 | flagged intents | winner of #3 | 25 × 2 passes | $0.17 | two-pass agreement; the user reviews the CSV |
| 5a | contract entity calibration | GLM-legal + DeepSeek | 40 | $0.75 | per-field accuracy ≥ 0.85 against gold derived from the expert CUAD spans (Parties, dates, Governing Law, Renewal Term) |
| 5b | contract entity pilot (2 per CUAD family) | as 5a | 52 | $1.00 | human verdicts ≥ 0.90 |
| 5c–e | contract entities | as 5a | 545 (182/182/181) | $3.00 each | agreement, verbatim checks, adjudication CSV |
| 6c0 | merger consideration calibration | GLM-legal + DeepSeek | 95 | $0.60 | each model ≥ 0.95 accuracy against the expert MAUD answer (`consideration_accuracy`, confusion table) |
| 6c1 | merger consideration for the 56 `other` rows | as 6c0 | 56 | $0.40 | agreement; disagreements adjudicated |
| 6a | merger pilot (3 per subclass) | GLM-legal + DeepSeek | 13 | $0.40 | needs 6c1; the user fills `reviewer_verdict`; agreed-field precision ≥ 0.90 |
| 6b–c | merger remainder | as 6a | 138 (69/69) | $1.50 each | agreement and verbatim checks |
| 7a | correspondence pilot (5 per subclass) | DeepSeek + MiniMax, reasoning low | 38 | $0.25 | human verdicts ≥ 0.90 |
| 7b–e | correspondence | as 7a | 962 | $0.50 each | agreement; enum checks |
| 8a | claim_checklist pilot (5 per subclass) | DeepSeek + MiniMax, reasoning low | 30 | $0.25 | human verdicts ≥ 0.90 |
| 8b–e | claim_checklist | as 8a | 1,070 | $0.50, $0.50, $0.50, $0.75 | agreement; evidence checks |
| 9a | corporate pilot (3 per subclass) | GLM-legal + DeepSeek | 29 | $0.50 | human verdicts ≥ 0.90 |
| 9b–e | corporate | as 9a | 415 | $1.00, $1.00, $1.00, $1.25 | agreement; verbatim checks |
| 10 | adjudication | human, via `adjudication.csv` | residue | $0 | every item resolved |
| 11 | re-stage, all gates, render the card | none | — | — | gates 1–13 pass |

Each chunk is run with `labeling chunk <id> [--arm N] --initials XX` and approved with `labeling gate <id> [--arm N] [--signed-off] --approve --initials XX` (`--arm` for bake-off chunks, `--signed-off` for agreement gates after reading `sample.csv`; see `campaigns/v10/runbook.md`). `needs:` in `campaign.yaml` enforces the order, and a chunk with `requires:` (5a, 6a) also needs `--ack-requires` once the external release has landed.

Stop rules: a failed gate or a reached cap stops the sequence. Nothing is ingested without its calibration or pilot. One-model chunks can run on Modal with `--provider modal` (the campaign model maps to the approved `Qwen/Qwen3-32B-FP8`, or as named by `--model-map`); two-model chunks need a second approved Modal model, so they stay on OpenRouter until one exists. The Modal model is a separate configuration and is calibrated before it labels anything.

The sandbox's interim overlay corrections (CUAD parties and dates) are imported as candidates in 5a/5b and verified like any other label, so they aren't lost to the hash change.

## Order of work

1. Sandbox Modal-fallback PR for the Qwen3-32B-FP8 labeler (`local-mailroom-sandbox`, branch `claude/focused-keller-7g1ce1`).
2. EDA branch `feat/v10-structure-labeling` from main:
   - the labeling package with tests;
   - the v10 build and gates;
   - the card renderer;
   - docs: `docs/plans/v10-structure-and-label-completion.md` (Parts A–F), `docs/plans/v10-consumer-patches.md` (the consumer patch plan), `labeling/campaigns/v10/runbook.md`, CHANGELOG, and AGENTS pointers.

   Push and open a PR.
3. G1 Dojo PR. Its schema must land before chunks 5 and 6 run, because those label the contract and merger v2 fields. Chunks 1–4 and 7–9 can run before it.
4. G2 llm-mailroom PR, then the G3 sandbox PR.
5. The user runs chunks 0 → 11.
6. Publish (G4) only on the user's go.

Each PR waits for review. No PR is merged and no paid call is launched without the operator's explicit go.

## Verification

- **EDA:**
  - Full pytest, offline with mocked HTTP. It covers:
    - provider request shapes, retries, the cap and key headroom;
    - CSV round-trip;
    - configuration-bound calibration;
    - agreement and verbatim checks;
    - `fields.yaml` parity with the Dojo pin;
    - span remap with zero spans lost;
    - hash equals sha256 in every config;
    - legacy round-trip;
    - tamper tests for every gate;
    - card `--check`.
  - Also:
    - ruff;
    - `labeling costs --check`, which confirms `costs.md` matches the campaign, `models.yaml` and the stage;
    - a mocked `--dry-run` per task;
    - the v10 build twice, byte-compared;
    - conformance normally and under `-O`;
    - an offline `datasets` load of every config.
  - Simulate the consumers against the local v10 parquet:
    - the sandbox `normalize_rows` on the legacy config (no hash mismatches);
    - llm-mailroom `hf_corpus_loader` merge (integrity count 0);
    - Dojo `parse_gt_fields` + `score_document` on legacy rows with the new labels.
- **G1–G3:** each repo's own test suite, plus the new tests listed. Sandbox vendor drift and Dojo source checks.
- **Sandbox PR:** head `2656524`, base `main`, and the body carries no tool attribution footer.
