# Sorter Landing, Vendor Split and v10 Labeling Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the condensed production sorter across llm-mailroom, the sandbox and Dojo as separate, reviewable PRs, so the Dojo 0.28 scoring change gets its own PR. Then align the v10 labeling plan with v9.3 and the private staging repo, and put a corporate-keywords task up for approval, gated on a person's verdicts.

**Architecture:** There are four PRs, merged in order.
- llm-mailroom #179 holds the production sorter switch.
- A new sandbox PR vendors llm-mailroom `main@6de82bc` and Dojo `v0.28.0`. This is the scoring change, and it carries its own golden diffs.
- Sandbox #258 is then cut down to the sorter alone. It merges `main` in and vendors the #179 merge commit.
- Dojo #126 re-vendors the production prompts from that commit.

Labeling work in Mailroom-Corpus-EDA covers wording, a read-only staging inspection, and one proposal. No paid call runs in this plan.

**Tech Stack:** Python 3.12, pytest, `gh`, `huggingface_hub`, the sandbox's `sandbox fetch-deps`.

**Spec:** none on disk. This plan argues from the session record:
- issue Exios66/local-mailroom-sandbox#259;
- the user's directives, copied into Global Constraints;
- the user's correction of 2026-11-07: "The 39 corporate records proposed as the keywords reference set are themselves labeled 'heuristic', so they may not be trustworthy gold."

Rulings made without a spec are provisional.

## Global Constraints

- **Git identity and attribution:** commit as the user's git identity (`Jack J Burleson // Lucius Morningstar`). No `Co-Authored-By` trailer and no "Generated with Claude Code" line, in any commit or PR body. This overrides the harness reminder.
- **Merge rule:** a PR merges only after it is flipped to ready for review and every requested bot review is addressed. If CodeRabbit is rate-limited, a scoped Sonnet 5.5 subagent review stands in for it.
- **Shared checkout:** `~/Downloads/local-mailroom-sandbox` is shared with another agent and the user's Git GUI.
  - `git fetch` before every commit and push.
  - Never force-push.
  - Never rebase a pushed branch; merge `origin/main` into it instead.
- **Secrets:** `OPENROUTER_API_KEY` and `HF_TOKEN` are loaded per command from `$SP/openrouter.key` and `$SP/hf.key`. They are never written into a repo, and are redacted (`sk-or-…`, `hf_…`) in any output.
  - `$SP` = `/private/tmp/claude-501/-Users-luciusjmorningstar-Downloads-Mailroom-Corpus-EDA/eb2e50c1-7520-4754-98ef-e5056b450692/scratchpad`.
- **Paid calls:** no paid call runs in this plan. Any future one needs a cost estimate first and the user's approval. Cost goes in logs (`reports/run-ledger.md`), never in a published dataset.
- **Labels:** labels never enter git (`docs/reports/audits/v10_labels/` is gitignored) and never enter the blind `default` config. Every paid labeling step checkpoints to the private `Lucius-Morningstar/mailroom-dataset-v10-staging`. Completed labeling work there must never be overwritten or lost.
- **Labeling rules:** never apply a labeling rule the user has not approved. Propose the rule instead.
- **Out of scope:** do not work in Digital-Mailroom (monorepo).
- **Disk:** about 4 GB is free.
  - Run pytest with `--basetemp=$SP/pt-<x>` and `rm -rf` it afterwards.
  - After a hook fails with `No space left on device`, `rm -rf /private/tmp/pytest-of-luciusjmorningstar`.
- **Evidence figure:** the corrected comparison figure is **class fixed − broken: 108 vs 100** (v1 vs V15). "96 vs 80" is wrong everywhere it appears.

## Review Focus

1. **A wholesale golden regeneration hides an unintended score change.** Every regenerated scoring golden must trace to a Dojo 0.28 change listed in `$SP/departures.json` or to the schema-v2 field set. Owner: Task 2, Step 5.
2. **A vendored prompt changed under an existing version id** (`contracts_specialist_v33`: file `6a5047ed`, vendor `aff71b8a`). Re-syncing it blindly would rewrite a historical pin. A person expects version ids to be immutable. Owner: Task 2, Step 6.
3. **History gets rewritten in the shared checkout.** A rebase or force-push of #258 would drop the other agent's commits. Owner: Task 3, Step 1, which merges and asserts the old head is an ancestor.
4. **Labels leak while the private staging repo is inspected.** Downloads go to `$SP`, never into a tracked path, and nothing is printed except counts and keys. Owner: Task 6, Step 3.
5. **Heuristic data is treated as gold.** All 450 corporate records are `annotation_method = heuristic`, so none may serve as the reference set, gate set or few-shot examples for keywords. Owner: Task 7, Step 1, an assertion in the proposal's evidence script.

---

### Task 1: Land llm-mailroom #179 (production sorter switch)

**Files:** none changed unless a review finding needs a fix. Repo `Exios66/llm-mailroom`, branch `claude/sorter-condensed-production`, head `6a76220`.

**Interfaces:**
- Produces: `SORTER_MERGE_SHA`, the merge commit of #179 on `main`. Tasks 3 and 4 vendor it.

- [ ] **Step 1: Confirm every bot review is addressed.**
  Run: `gh pr view 179 -R Exios66/llm-mailroom --json isDraft,reviewDecision,statusCheckRollup` and `gh api repos/Exios66/llm-mailroom/pulls/179/comments --jq '.[]|{user:.user.login,body:.body[0:120],in_reply_to_id}'`.
  Expected:
  - `isDraft: false`;
  - CodeRabbit and GitGuardian are `SUCCESS`;
  - every CodeRabbit or CodeAnt thread has a reply or a fix commit.
  Fix any unaddressed finding on the branch: run a test before and after the fix, then fetch, commit and push.
- [ ] **Step 2: Merge.** Run `gh pr merge 179 -R Exios66/llm-mailroom --merge`. Record `SORTER_MERGE_SHA=$(gh pr view 179 -R Exios66/llm-mailroom --json mergeCommit -q .mergeCommit.oid)` in the ledger.

### Task 2: Sandbox PR, vendor llm-mailroom main `6de82bc` and Dojo `v0.28.0` (scoring change only)

**Files** (repo `~/Downloads/local-mailroom-sandbox`, branch `claude/vendor-llm-mailroom-main-dojo-0.28.0`, already holding the uncommitted vendor refresh and pin docs):
- Modify: `tests/test_eval.py:318,333,335`: `"0.27.2"` → `"0.28.0"`.
- Modify: `src/mailroom_sandbox/eval/extraction_scope.py`: change `LIVE_SCHEMA_FIELDS["contract"]` and `["merger_agreement"]`.
- Regenerate: `tests/fixtures/scoring_golden/` outputs file.
- Modify: `README.md:11-12`. The badges become `llm--mailroom_0.9.2_%40_6de82bc` and `llm--dojo--scoring_0.28.0_%40_cab9768`.
- Modify: `CHANGELOG.md`, a new Unreleased entry.
- Possibly modify: `config/prompts/contracts_specialist_v33.txt`, but only per the ruling in Step 6.

**Interfaces:**
- Consumes: the vendor pins in `src/mailroom_sandbox/cli.py` `_VENDOR_PINS` (`6de82bc3…`, `cab97680…`).
- Produces: `main` with Dojo 0.28.0 vendored. Task 3 merges it into #258.

- [ ] **Step 1: Baseline.** Run `.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=$SP/pt-vd -n auto tests > $SP/vd-suite.log 2>&1; tail -1 $SP/vd-suite.log`.
  Expected: `5 failed, 2716 passed, 16 skipped`. The 5 failures:
  - `test_dojo_pin_matches_current_vendored_snapshot`;
  - `test_seam_keys_track_the_vendored_engine_and_the_merger_agent`;
  - `test_scoring_outputs_match_goldens`;
  - `test_check_passes_on_repo_pins`;
  - `test_root_readme_names_the_vendored_versions_and_commits`.
- [ ] **Step 2: Pin the Dojo version in tests.** Edit `tests/test_eval.py:318,333,335`.
  Run: `pytest tests/test_eval.py::test_dojo_pin_matches_current_vendored_snapshot`. Expected: PASS.
- [ ] **Step 3: Update the README badges.**
  Run: `pytest tests/test_vendor.py::test_root_readme_names_the_vendored_versions_and_commits`. Expected: PASS.
- [ ] **Step 4: Move the seam to Dojo's schema-v2 fields.**
  - Set `LIVE_SCHEMA_FIELDS["contract"]` to `set(suite_for_doc_type("contract").field_types)`, written out as a literal tuple. Dojo v2 ContractExtraction has 17 fields. It adds `signatories` and `party_roles`, among others. It drops `merger_consideration` and `maud_clauses`.
  - Set `["merger_agreement"]` to the vendored `MERGER_AGREEMENT_SCHEMA` properties minus `reasoning` and `confidence`.
  - Keep the test's guard against the CUAD leftovers (`term_length`, `cuad_family`, `cuad_clauses`, `contract_value`, `renewal_terms`).
  Run: `pytest tests/test_extraction_scope.py`. Expected: all PASS.
- [ ] **Step 5: Regenerate the scoring goldens and account for every change.**
  - Run `SCORING_GOLDEN_WRITE=1 pytest tests/test_scoring_outputs_golden.py`. Expected: SKIP `regenerated … cases`.
  - Then run `git diff --stat tests/fixtures/scoring_golden`.
  - Write a scratch script, `$SP/golden_audit.py`, that lists each changed golden key with its doc type and old and new score.
  - Every changed key must belong to a case whose Dojo score changed in `$SP/departures.json` (contract 15, correspondence 2, insurance 2, merger 2), or to a field the schema-v2 seam added or dropped.
  Expected: 33 changed outputs, all accounted for. A key that is not accounted for is a stop. Ledger it and report it to the user before going on.
  Re-run: `pytest tests/test_scoring_outputs_golden.py`. Expected: PASS.
- [ ] **Step 6: Contract prompt drift (`contracts_specialist_v33`).**
  - Diff the vendored text against the local file: `PYTHONPATH=vendor/llm-mailroom/src:vendor/llm-dojo-scoring/src .venv/bin/python -c "from langchain_agents.prompts import CONTRACTS_SPECIALIST_PROMPT_V33 as p; print(p)" | diff - config/prompts/contracts_specialist_v33.txt`.
  - Find the upstream commit that changed it: `git -C ../llm-mailroom log --oneline 9e8f30b..6de82bc -- src/llm/frozen_v1 src/langchain_agents/prompts.py`.
  - **Ruling rule:** if v33's text changed under the same version id, that breaks version immutability upstream. Do **not** sync it.
    - Stop and ask the user whether to accept it, or to keep `9e8f30b`'s v33 and file an llm-mailroom issue.
    - Only on acceptance run `.venv/bin/python scripts/sync_specialist_prompts.py`.
  Then run `pytest tests/test_sync_specialist_prompts.py`. Expected: PASS.
- [ ] **Step 7: Re-score the saved runs that the scoring change moves (free; no model calls).**
  - Rescore `mock-pipeline-luna-qwen`, `pipeline-modal-smoke-20261030`, and `pipeline-modal-smoke-20261030b` under 0.27.2 (`main`) and under this branch, using the sandbox's offline scoring command for saved runs.
  - Record the per-doc-type aggregate before and after for the PR body.
  - If no offline rescore command exists, ledger that and use the golden audit from Step 5 as the evidence.
- [ ] **Step 8: Full suite.** Run the Step 1 command. Expected: `0 failed`. Then `rm -rf $SP/pt-vd`.
- [ ] **Step 9: Write the CHANGELOG entry.** Under Unreleased, add one entry with these points:
  - llm-mailroom main `6de82bc`: #173, #177, #178.
  - Dojo v0.28.0 `cab9768`: ContractExtraction v2 (17 fields) and MergerAgreementExtraction v2 (21 fields), and corporate `not_applicable` meaning "outside the row's schema".
  - Scoring departures: 21 of 65 frozen Dojo goldens.
  - The sorter prompt is unchanged in this PR.
- [ ] **Step 10: Commit, push and open the PR.** Run `git fetch && git status`, then `git add` the vendor trees and every file above. Commit message: "Vendor llm-mailroom main 6de82bc and llm-dojo-scoring v0.28.0 (schema-v2 contract and merger scoring)". Push with `-u`, and open the PR as a draft.
  The PR body must include:
  - the departures table (doc type × changed of frozen);
  - the Step 5 audit summary;
  - the Step 6 ruling;
  - the Step 7 before and after numbers;
  - the line "No sorter change; #258 carries it."
- [ ] **Step 11: Reviews, then merge.**
  - Flip the PR to ready.
  - Wait for CodeRabbit. If it is rate-limited, dispatch a Sonnet 5.5 stand-in review scoped to this PR's diff (not the vendor trees' contents), with Review Focus items 1 and 2.
  - Address every finding.
  - Merge with `gh pr merge --merge`.

### Task 3: Shrink #258 to the sorter alone and land it

**Files** (branch `claude/sorter-condensed-v2`, head `d75333d`, written by another agent):
- Modify: `CHANGELOG.md` and `config/prompts/sorter_condensed_lineage.yaml` (the v1 entry): "96 vs 80" → "108 vs 100".
- Modify: `README.md`, the line claiming "V15 won" (about line 356 on that branch). It should say condensed v1 won: 108 vs 100 class fixed − broken, 263/228 vs 255/219 right after.
- Vendor: llm-mailroom at `SORTER_MERGE_SHA` from Task 1. Update `_VENDOR_PINS` and `vendor/llm-mailroom/VENDOR.md`. Run `sandbox fetch-deps`.

**Interfaces:**
- Consumes: `SORTER_MERGE_SHA` (Task 1) and `main` with Dojo 0.28.0 (Task 2).

- [ ] **Step 1: Merge `origin/main` into the branch (no rebase).**
  - Run `git fetch && git checkout claude/sorter-condensed-v2 && git merge origin/claude/sorter-condensed-v2 && git merge origin/main`.
  - Resolve vendor conflicts by taking `main`'s side.
  - Expected: `git merge-base --is-ancestor d75333d HEAD` exits 0.
- [ ] **Step 2: Re-vendor llm-mailroom at `SORTER_MERGE_SHA`.** Set the `_VENDOR_PINS` llm-mailroom entry to `("llm-mailroom", "main", …, SORTER_MERGE_SHA)` and its comment to "main at #179". Run `sandbox fetch-deps`.
  Expected: `git diff --stat vendor/llm-dojo-scoring` is empty, so the Dojo stays at 0.28.0, and the llm-mailroom diff touches only the sorter files from #179.
- [ ] **Step 3: Correct the figures and the README claim.**
  Run: `git grep -n "96 vs 80\|V15 won"`. Expected after the edits: no output.
  The `d75333d` commit message cannot be changed without a force-push, so leave it and say so in the PR body.
- [ ] **Step 4: Full suite** (`--basetemp=$SP/pt-258`). Expected: `0 failed`. Fix any sorter-pin test that the new vendor commit moves, with a test before and after each fix.
- [ ] **Step 5: Commit and push** (fetch first). Message: "Sorter adoption follows the merged llm-mailroom #179; correct the evidence figure to 108 vs 100".
- [ ] **Step 6: Update the PR body and reply to the threads.**
  - The PR body's evidence table should match issue #259.
  - Reply to the open CodeAnt threads:
    - B1 supplements: v1's text is frozen, and changes go in a new lineage version.
    - The urgency nit: it belongs in upstream llm-mailroom prompts.
- [ ] **Step 7: Reviews, then merge.** Same as Task 2, Step 11. Scope a stand-in review to the sorter configs, prompts and lineage, the vendor pin, and the README and CHANGELOG.

### Task 4: Dojo #126, re-vendor the production prompts from #179

**Files** (repo `Exios66/llm-dojo-scoring`, PR #126's branch): the vendored mailroom production-prompt files that #126 touches. List them with `gh pr diff 126 -R Exios66/llm-dojo-scoring --name-only`.

**Interfaces:**
- Consumes: `SORTER_MERGE_SHA`.

- [ ] **Step 1:** Check out #126 in a temporary clone at `$SP/dojo-126`, not in a shared checkout. Merge `origin/main` into it.
- [ ] **Step 2:** Re-vendor the prompt files from `llm-mailroom@SORTER_MERGE_SHA` with the repo's own vendor or sync script. Its name is in the PR's description or in `scripts/`. A hand copy is not allowed.
  Expected: the sorter production text is `sorter_condensed_v1`, and the repo's prompt-parity tests pass.
- [ ] **Step 3:** Fetch, commit and push. Update the PR body with the new source commit. Then reviews and merge, per the merge rule. Afterwards `rm -rf $SP/dojo-126`.

### Task 5: Vet the remaining open PRs (sandbox #256, llm-mailroom #175 and #160, Dojo #128)

**Files:** none in this task; each PR's own fixes are made on its own branch.

- [ ] **Step 1:** For each PR, record these in the ledger:
  - mergeable state;
  - checks;
  - unaddressed bot threads;
  - whether its diff conflicts with Tasks 2–4. For example, a pin or a vendor tree that has gone stale.
- [ ] **Step 2:** Act on each:
  - **#256 (CLI diagnostics, CLEAN):** merge `main` in. Run the full suite, then a stand-in review if CodeRabbit has not reviewed the current head. Merge it per the merge rule.
  - **#175 (docs, BLOCKED):** name the block (a required check or review) and fix it if it is ours to fix. Otherwise report it.
  - **#160 (draft checkpoint, DIRTY):** do not merge. Report what it holds and what it would conflict with, for the user to decide.
  - **Dojo #128 (docs):** merge per the merge rule if its claims match the post-Task-4 state. Otherwise fix them first.

### Task 6: v10 labeling alignment with v9.3 and the private staging repo

**Files** (repo `~/Downloads/Mailroom-Corpus-EDA`, a new branch `claude/v10-campaign-v9-3-wording` from `main@86090a2`):
- Modify: `src/mailroom_eda/labeling/campaigns/v10/campaign.yaml:23` and `runbook.md:28`.
  - The stage *is* rebuilt from the public v9.2 tag, so "v9.2" stays.
  - The text must add that the stage then applies `v9_3_revision` and `v9_4_revision`. That makes the stage carry the v9.3 labels: stage sha256 `eccb0acf…`.
- Create nothing in git from staging. Reconciliation output goes to `$SP/v10-staging-reconcile.md`.

- [ ] **Step 1: Fix the wording.**
  Run: `.venv/bin/python -m pytest -q --basetemp=$SP/pt-lab tests -k "labeling or campaign"`. Expected: PASS.
  Run: `PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling doctor`. Expected: PASS, apart from the shell's missing keys.
- [ ] **Step 2:** Fetch, commit, push and open a PR. Message: "v10 campaign: the stage applies the v9.3 and v9.4 revisions on top of the v9.2 tag". Then reviews and merge, per the merge rule.
- [ ] **Step 3: Inspect `Lucius-Morningstar/mailroom-dataset-v10-staging` read-only.**
  - Run `HF_TOKEN="$(cat $SP/hf.key)"`, then `HfApi().list_repo_files(…, repo_type="dataset")` and `list_repo_commits`.
  - Download only manifests or checkpoint indexes into `$SP/v10-staging/`. Print only counts, chunk ids and field keys, never label values.
  - Expected: checkpoints exist for the approved chunks 3 and 4a–c (intent).
  - Record each chunk's rows, model, sha and stage sha (expected `eccb0acf…`) in `$SP/v10-staging-reconcile.md`.
  - A chunk whose stage sha is not `eccb0acf…` is flagged, not re-run.
- [ ] **Step 4:** Compare the reconciliation with `campaigns/v10/approved.yaml` and `costs.md`.
  - An approved chunk with no checkpoint is a finding.
  - A checkpoint with no approval is also a finding.
  - Report both. Nothing is re-run or deleted.

### Task 7: Corporate keywords task, a proposal with a person's verdicts as the gate (nothing runs)

**Files:** none in git. The proposal goes to the user in chat and, once they approve, into a new `campaigns/v10` chunk in a later PR. The evidence script is `$SP/kw/evidence.py`.

**Facts the proposal states:**
- 450 corporate records.
- 411 have keywords = `[record type, entity]` only.
- All 450 are `annotation_method = heuristic`, the 39 richer ones included.
- `fields.yaml` marks `keywords` `source: gold`, so no v10 task covers it today.
- `fields.yaml` types `keywords` as `entity_list:name` for every class. Merger keywords come from the LLM `merger_entities` task. So the scorer expects a list of names, not topic terms.
- Of the 39 richer heuristic lists, 36 are topic terms (`quorum`, `indemnification`, `Board of Directors`), which conflicts with that type. One holds a form type taken from metadata (`8-K/A`). None cover `charter_amendment` (80 rows), `officer_certificate`, `indenture`, `subsidiary_list` or `powers_of_attorney`.
- Text is long-tailed: median 9,181 chars, p90 80,594, and 133 rows over 32k chars, about 4.26M tokens in all. 42 are "FORM OF" drafts with blank placeholders (`up to ________ shares`, `$______`).
- The GeoVax pair (`0001437749-20-019373` and `-020141`, both in test) has the same body under different file headers. There are 3 normalized-text duplicate groups (10 rows).
- 210 records are EX-3 charters, amendments or designations, 73 are EX-4, 36 are EX-21 subsidiary tables (names plus jurisdictions only) and 20 are EX-24. 111 have no exhibit header in the text.

- [ ] **Step 0: Subclass check (free).** Script `$SP/kw/subclass_check.py` writes `docs/reports/audits/v10_labels/corporate_subclass_check.csv`, a gitignored file. The user gives verdicts on boundary rules and rows. Keyword rules are drawn only from verified strata, and only from the train split.
- [ ] **Step 1: Evidence script.** It loads `data/parquet/ground_truth` (v9.3).
  - It asserts that `annotation_method == "heuristic"` for every corporate record.
  - It asserts that 411 have keywords of length ≤ 2 equal to `[record_type, entity_name]`.
  - It prints the subclass distribution of the 450.
  Expected: both assertions hold. If they do not, the proposal's numbers change before it is shown.
- [ ] **Step 2: Draft the proposal.** It has five parts.
  - **(a) The rule text to approve:**
    - **what a keyword is.** Option A keeps the `entity_list:name` type: names stated in the text, such as the issuer, the security series ("Series A Convertible Preferred Stock"), the governing statute ("Section 151 DGCL"), and named agents, trustees, underwriters and counsel. Option B is topic terms ("conversion price", "beneficial ownership limitation"), which needs a scorer type change in Dojo first.
    - the cap per record, for example 8, and for EX-21 tables, whether subsidiary names count;
    - for "FORM OF" drafts, blanks yield nothing, and no value is ever filled in;
    - whether the `[record type, entity]` floor is kept or replaced;
    - metadata (form type, filer) never becomes a keyword; keywords come from the text alone.
  - **(b) Gold, first and free:** the user labels about 20 records, at least 2 per subclass (9 subclasses plus `other`), chosen by a seeded draw (`RANDOM_STATE = 42`) before any model runs. Only one row per near-duplicate group is drawn. No heuristic record serves as the reference set, gate set or few-shot examples.
  - **(c) Paid pilot, needs approval:**
    - the approved labeler pair on those 20 plus 20 more;
    - the user gives verdicts on each keyword: accept or reject;
    - estimate: re-run the campaign's estimator on the 40 drawn rows' real token counts. The p90 is about 20k tokens, so a per-row average understates the cost. The earlier $0.02 counted only extra output tokens inside the 9a–9e calls, and a standalone pilot pays for the input too.
  - **(d) Gate:** the full run happens only if per-keyword precision against the user's verdicts is at least a threshold the user sets. The proposed threshold is 0.9.
  - **(e) The full run:** fold it into chunks 9a–9e. Estimate: 444 rows, about **$0.24** extra; caps rise to about **$3.95**. It checkpoints to the private staging repo.
- [ ] **Step 3:** Present the proposal for approval. Nothing runs until the user approves (a), (b) and (c).

### Task 7b: Condensed specialists into sandbox production, then upstream (user decision, 2026-11-07)

**Files:**
- sandbox `config/components.yaml:60-64`. Corporate, correspondence and insurance move to `corporate_records_specialist_condensed_v1`, `correspondence_specialist_condensed_v2` and `insurance_claims_specialist_condensed_v3`. Contracts and merger stay frozen.
- the lineage comment in `config/prompts/specialist_condensed_lineage.yaml`, which still says the frozen stems are "the production reference".
- then an llm-mailroom PR that serves the same three prompts, shaped like #179: production pins, a guard against stale managed prompts, freeze tests.

**Constraint:** the condensed specialists never write labels. A prompt that writes the answer key would be graded on its own answers.

- [ ] **Step 1:** List every test and config that pins the `*_simplified` defaults: `git grep -n "_simplified" config tests src`. The SAND-027-3 eval-environment default changes with this, so name every run that would change baseline in the PR body.
- [ ] **Step 2:** Switch the three pins. Run the full suite (`--basetemp=$SP/pt-spec`). Expected: `0 failed`, with every pin test updated on purpose.
- [ ] **Step 3:** Branch, fetch, commit, PR, reviews, merge. Then do the llm-mailroom PR the same way, and re-vendor it in the sandbox as in Task 3, Step 2.
- [ ] **Step 4:** The corporate condensed v1 prompt uses the v7 record_type map, which conflicts with the Dojo 0.28 subclass schemas. Write that in the PR body as a known limitation, to fix in a `condensed_v2` lineage mutation after the subclass check (Task 7, Step 0) settles the strata.

### Task 8: Hand off

- [ ] **Step 1:** Comment on Exios66/local-mailroom-sandbox#259. List:
  - the merged PRs and their SHAs;
  - `SORTER_MERGE_SHA`;
  - the Task 2 Step 6 ruling;
  - the Task 6 reconciliation findings (counts only);
  - the keywords proposal's status.
  Then check off open items 1 and 2.

## Not in this plan (waiting on the user)

- Checks of 5 documents by a person: `corporate_record/other` ×2 and `correspondence/attorney_demand` ×3.
- Notice-label consistency.
- The `Either` stub and the sponsorship/service boundary.
- An optional test-split confirmation run of v1 vs V15, cost estimate first.
- The run ID or path for "results came back on the full test".
