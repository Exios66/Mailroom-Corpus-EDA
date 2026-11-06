# Labeling settings optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tune every v10 labeling setting (window, reasoning, token budget, prompt, model pair, gate) to its task from measured evidence, so the bake-off picks the models and the later chunks run at the lowest cost that keeps gold-label quality.

**Architecture:** Changes stay inside `src/mailroom_eda/labeling/` and the `campaigns/v10/` config. Tasks 1–4 change what the models see and how cost is projected. They land **before** the paid bake-off, because they change prompt shas and caps. Task 5 adds the bake-off ranking that turns gate results into a model choice, and Task 6 adds the inspection report Claude uses to read outputs before every gate. Task 7 runs after the user authorizes and runs the bake-off. It re-plans chunks 2–9 from the measured winners.

**Tech Stack:** Python 3.12, pytest, PyYAML, OpenRouter chat completions (strict `json_schema`).

**Spec:** `docs/plans/v10-structure-and-label-completion.md` (Parts B, D, E, F). The user's rulings are recorded in `.superpowers/sdd/v10-structure-and-label-completion/progress.md`.

## Global Constraints

- Commits use the user's git identity only. No `Co-Authored-By`, Claude trailers or footers.
- No paid OpenRouter call without the user's authorization for that step. Report remaining credit (`labeling doctor`) after every paid step. Smoke-test (1–2 documents per arm) before any full arm.
- The OpenRouter key is read from the scratchpad key file per command. It is never written to the repo, and `sk-or-…` is redacted in output.
- Models: top-tier current models only. No DeepSeek snapshot older than `deepseek/deepseek-v4.1-flash` (2026-09-10). The second opinion is never a DeepSeek model.
- Chunk caps in `campaign.yaml` sum to **≤ $30.00** (`test_campaign_needs_order_and_output_dirs`).
- Labels land only in GT-tier configs. The blind `default` is `document_id`, `doc_text` and an empty `prompt`. The dataset represents incoming corporate mail ingress.
- `pytest` runs with `-p no:cacheprovider -o tmp_path_retention_policy=none` (the disk is small).
- `costs.md` and the plan's quoted prompts stay current: `labeling costs --check`, `scripts/labeling/sync_plan_prompts.py --check`.

## Review Focus

1. **A cached answer reused after the window changes.** The answer was made on cut-off text, so it must not be reused. The window goes into the prompt sha (Task 1, `test_cuad_window_is_part_of_the_prompt_sha`).
2. **A score gap that is just chance** (40 contracts, 96 emails) must not pick a model; selection uses paired bootstrap intervals (Task 5, `test_bakeoff_keeps_cheaper_deepseek_unless_costlier_is_significantly_better`). **A bake-off arm that stopped at its cap** (fewer documents than expected) must never win, even when its partial score is higher (Task 5, `test_bakeoff_ignores_incomplete_arms`).
3. **A second-opinion candidate that is another DeepSeek snapshot** has errors correlated with the primary, so it is not eligible (Task 5, `test_bakeoff_second_opinion_is_another_vendor`).
4. **A contract longer than the new window** (the 616k-character EX-10 exhibit) is still windowed rather than sent whole, and its description says so (Task 1, `test_cuad_window_sends_calibration_whole_and_caps_giants`).
5. **A low-reasoning arm whose real reasoning far exceeds the projection** must have a cap that fits the measured tokens (Task 2, `test_costs_use_measured_reasoning_tokens`).

---

### Task 1: CUAD sees the whole contract

**Files:**
- Modify: `src/mailroom_eda/labeling/tasks.py` (constants near `HEAD_CHARS`; `build_cuad`)
- Test: `tests/test_labeling.py`

**Interfaces:**
- Produces: `tasks.CUAD_HEAD_CHARS = 360_000` and `tasks.CUAD_TAIL_CHARS = 40_000` (about 100k tokens). `build_cuad` windows with them and hashes `{"categories": cats, "window": [CUAD_HEAD_CHARS, CUAD_TAIL_CHARS]}` into `prompt_sha256`.

- [ ] **Step 1: Write the failing tests**

```python
def test_cuad_window_sends_calibration_whole_and_caps_giants(rows):
    """Golden CUAD calibration contracts fit 32k tokens; all of them are sent whole.
    Only a document past 400k characters is windowed."""
    big = {**rows[0], "doc_text": "x" * 300_000}
    assert "the full document" in tasks.build_cuad(big).messages[1]["content"]
    giant = {**rows[0], "doc_text": "y" * 616_016}
    msg = tasks.build_cuad(giant).messages[1]["content"]
    assert "the full document" not in msg and len(msg) < 410_000

def test_cuad_window_is_part_of_the_prompt_sha(rows, monkeypatch):
    before = tasks.build_cuad(rows[0]).prompt_sha256
    monkeypatch.setattr(tasks, "CUAD_HEAD_CHARS", 48_000)
    assert tasks.build_cuad(rows[0]).prompt_sha256 != before
```

- [ ] **Step 2: Run them.** `.venv/bin/python -m pytest tests/test_labeling.py -k cuad_window -p no:cacheprovider -o tmp_path_retention_policy=none`. Expected: both FAIL.
- [ ] **Step 3: Implement.** Add the two constants. In `build_cuad`, call `window(text, CUAD_HEAD_CHARS, CUAD_TAIL_CHARS)` (read the module globals at call time so the monkeypatch works) and add `"window"` to the sha config.
- [ ] **Step 4: Run the labeling suite, then regenerate the derived files.** Run `labeling costs`. Chunk 1 arm projections rise by about $0.03–0.05; if a cap is now below 1.5× its projection, raise it and pay for it by lowering chunk 2 caps, so the total stays ≤ $30.00. Then run `sync_plan_prompts.py`. Expected: suite green, both `--check` commands report current.
- [ ] **Step 5: Commit.** `labeling: CUAD sees the whole contract (window 360k+40k chars, in the prompt sha)`

### Task 2: Costs use measured reasoning tokens

**Files:**
- Modify: `src/mailroom_eda/labeling/models.yaml` (per-model `reasoning_tokens`), `src/mailroom_eda/labeling/runner.py` (`campaign_costs`, `REASONING_OUTPUT_FACTOR`), `src/mailroom_eda/labeling/campaigns/v10/costs.md` (regenerated)
- Test: `tests/test_labeling.py`

**Interfaces:**
- Produces: `runner.reasoning_tokens(model: str, task: str, reasoning: str) -> int`. It returns 0 at `off`. At `low` it returns `models.yaml` `reasoning_tokens.low[task]`, falling back to `reasoning_tokens.low.default`. Values come from the 2026-11-06 journals, rounded up to the observed p75:
  - `deepseek-v4.1-flash`: `{intent: 900, cuad: 1500, default: 1200}`
  - `minimax-m3.1-flash`: `{intent: 250, cuad: 1000, default: 600}`
  - `glm-5.3-flashx-legal`: `{default: 300}`
  - every other model: `{default: 1200}`, conservative when unmeasured
- `campaign_costs` adds `reasoning_tokens(...) × calls` to the output tokens instead of multiplying by `REASONING_OUTPUT_FACTOR` (deleted).

- [ ] **Step 1: Write the failing test** `test_costs_use_measured_reasoning_tokens`. For DeepSeek on the intent calibration set (192 calls), the `low` row's output tokens minus the `off` row's equal `round(900 × 192 × RETRY_FACTOR)`, and `reasoning_tokens("deepseek/deepseek-v4.1-flash", "intent", "off") == 0`.
- [ ] **Step 2: Run it.** Expected: FAIL (`reasoning_tokens` not defined).
- [ ] **Step 3: Implement** `reasoning_tokens` and the `campaign_costs` change; add the `reasoning_tokens` blocks to `models.yaml`.
- [ ] **Step 4: Regenerate `costs.md`.** Re-check that every low arm's cap is ≥ 1.5× its new projection, rebalancing inside $30.00. Run the suite. Expected: green, `costs --check` current.
- [ ] **Step 5: Commit.** `labeling: project low-reasoning cost from measured reasoning tokens`

### Task 3: CUAD prompt and repair hints from the observed errors

**Files:**
- Modify: `src/mailroom_eda/labeling/prompts/cuad_system.md` (rule 5, Post-Termination Services), `src/mailroom_eda/labeling/runner.py` (`cuad_problems`), plan Part E via `sync_plan_prompts.py`
- Test: `tests/test_labeling.py`

**Interfaces:**
- Produces: `runner._probe_hint(problem: str) -> str`. A problem ending in `": failed semantic probe"` becomes `"<problem>: the span does not read as <name>. The category asks: <Dojo description> Replace it with the operative sentence that answers this YES, or drop <name> if the document has none."`. Any other problem is returned unchanged. `cuad_problems` maps every dropped or extra problem through it.

- [ ] **Step 1: Write the failing tests.**
  - `test_cuad_problems_restate_the_category_question_on_a_failed_semantic_probe`: monkeypatch `v9_4_revision.verify_cuad_labels` to return `({}, ["Audit Rights: failed semantic probe"], [])`; the single problem starts with that text and contains the Dojo description of Audit Rights.
  - `test_cuad_prompt_excludes_winding_up_from_post_termination_services`: `"winding up" in prompt("cuad_system").render({})`.
- [ ] **Step 2: Run them.** Expected: both FAIL.
- [ ] **Step 3: Implement.**
  - Append to the Post-Termination Services bullet: `Winding up or liquidating the venture or company itself (paying its debts, distributing its remaining funds or assets on dissolution) does not count.` (Five of seven smoke arms mislabeled exactly this clause; gold excludes it.)
  - Add `_probe_hint` as specified.
- [ ] **Step 4: Run the suite, then `sync_plan_prompts.py`.** Expected: green and current.
- [ ] **Step 5: Commit.** `labeling: CUAD excludes winding-up from post-termination services; repair restates the category question`

### Task 4: CUAD holdout set

**Files:**
- Modify: `src/mailroom_eda/v9_4_revision.py` (next to `cuad_calibration_sample`), `src/mailroom_eda/labeling/runner.py` (`select`), `src/mailroom_eda/labeling/campaigns/v10/campaign.yaml`
- Test: `tests/test_labeling.py`

**Interfaces:**
- Produces: `v94.cuad_holdout_sample(rows, n: int = 40) -> list[Mapping]`. It applies the same pool rule and stratified round-robin as `cuad_calibration_sample`, but excludes calibration filenames and uses seed `RANDOM_STATE + 1`, sorted by filename.
- `select(rows, "cuad", "holdout")` returns it, and the `cuad sets:` error lists `calibration, holdout, pending`.
- New chunk: `{id: "1h", needs: ["1"], task: cuad, set: holdout, models: [winner_of_1], reasoning: winner_of_1, cap_usd: <1.5× projection>, gate: {kind: cuad_f1, micro_f1: 0.75, precision: 0.80}, note: "the revised prompt must pass on contracts it was not tuned on"}`.
- Chunks `2a`–`2c` get `needs: ["1", "1h"]`.

- [ ] **Step 1: Write the failing test** `test_cuad_holdout_is_disjoint_from_calibration_and_stable`, on the real stage (skip if absent). The test checks:
  - 40 rows
  - no filename shared with `cuad_calibration_sample`
  - identical on a second call
  - every row passes the same golden or ≤32k pool rule
- [ ] **Step 2: Run it.** Expected: FAIL (`holdout` rejected).
- [ ] **Step 3: Implement** the sampler, the `select` branch and the campaign chunk; rebalance caps ≤ $30.00.
- [ ] **Step 4: Run the suite and `costs`.** Expected: green, current.
- [ ] **Step 5: Commit.** `labeling: CUAD holdout set gates the winning configuration before EX-10 labeling`

### Task 5: Bake-off ranking picks the primary and the second opinion

**Files:**
- Modify: `src/mailroom_eda/labeling/operate.py` (new `bakeoff_ranking`, `cmd_bakeoff`, CLI `bakeoff <chunk>`)
- Test: `tests/test_labeling.py`

**Interfaces:**
- Consumes: each arm's `gate.json`, as written by `gate_result`:
  - always: `passed`, `documents`, `expected_documents`, `spent_usd`, `models`, `reasoning`
  - chunk 1: `micro_f1`, `micro_precision`
  - chunk 3: `cohen_kappa`
- Produces:
  - `paired_bootstrap(a: list[dict], b: list[dict], metric: str, n: int = 2000, seed: int = 42) -> tuple[float, float, float]`. It returns (point, lo95, hi95) of metric(b) − metric(a), resampling the shared `document_id`s with replacement. Per-document records carry `tp`/`fp`/`fn` for `micro_f1` (pooled over the resample) and `gold`/`pred` for `cohen_kappa`.
  - `bakeoff_ranking(arms: list[dict], metric: str, margin: float = 0.05) -> dict`. Each arm is `{"gate": <gate.json>, "docs": [per-document records]}`; arms with the same model may have calibration and holdout docs pooled by the caller. It returns `{"primary": arm|None, "second": arm|None, "table": [...]}`.
- Rules (2026-11-06 user point: 40 contracts and 96 emails cannot resolve 0.02 gaps, so selection uses paired bootstrap intervals, never a raw score gap):
  - **Eligible:** complete (`documents == expected_documents`), no `served_outside_pin`, no `reasoning_not_honoured`, and floors passed.
  - **Primary:** among eligible `deepseek/` arms, the cheapest (lowest `spent_usd`) is the baseline. A costlier DeepSeek arm replaces it only if its bootstrap lo95 versus the baseline is > 0, i.e. significantly better.
  - **Second:** the eligible arm of another vendor (prefix before `/` ≠ `deepseek`) that is non-inferior to the primary (lo95 of candidate − primary ≥ −margin). Among those, the cheapest wins.
  - **Exclusion:** an arm whose hi95 versus the primary is < −margin is marked `excluded: clearly worse` and is not run on the holdout.
  - **Pooling:** for CUAD, the final pick pools each surviving arm's calibration and holdout documents (80 contracts).
  - **Table:** every arm with point, lo95, hi95, cost and eligibility reason.
  - `cmd_bakeoff` prints the table, writes `<out>/<chunk>.bakeoff.json` and exits 1 when `primary` or `second` is `None`.
- Chunk `1h` (Task 4) therefore has one arm per chunk-1 model, run only for arms the calibration bakeoff did not exclude.
- [ ] **Step 1: Write the failing tests.**
  - `test_paired_bootstrap_is_deterministic_and_brackets_the_point`: same inputs give the same triple, and lo95 ≤ point ≤ hi95.
  - `test_bakeoff_keeps_cheaper_deepseek_unless_costlier_is_significantly_better`: a 0.02 gain on 40 docs keeps the cheaper snapshot, while a gain whose lo95 > 0 switches.
  - `test_bakeoff_second_opinion_must_be_non_inferior_and_another_vendor`: a vendor 0.10 worse on every document is excluded; `deepseek-v4.1-flash-1017` is never `second`.
  - `test_bakeoff_ignores_incomplete_arms`: a 30/40-document arm with the top F1 is ineligible.
  - `test_bakeoff_has_no_second_when_none_is_non_inferior`: `second is None` and `cmd_bakeoff` returns 1.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run the suite.** Expected: green.
- [ ] **Step 5: Commit.** `labeling: bakeoff ranks arms and picks the primary and an equally capable second opinion`

### Task 6: Inspection report for Claude's own read of the outputs

**Files:**
- Modify: `src/mailroom_eda/labeling/operate.py` (new `cmd_inspect`, CLI `inspect --out <dir> [--n 8]`)
- Test: `tests/test_labeling.py`

**Interfaces:**
- Produces: `inspect_report(rows, calls, task: str, n: int = 8) -> str`. This is Markdown for n documents sampled with `random.Random(42)`, disagreements first. Each document shows:
  - its gold value(s), when the set is calibration or holdout
  - each model's answer, confidence and served provider
  - for every evidence quote or span, ±200 characters of `doc_text` around its verified location, or `NOT FOUND`

  `cmd_inspect` writes `<dir>/inspect.md` and prints its path.

- [ ] **Step 1: Write the failing test** `test_inspect_report_shows_gold_answers_and_quote_context`. It uses fixture rows plus two fake intent calls that disagree. The disagreeing document comes first, both answers appear, the quote context contains the quoted text, and an unlocatable quote prints `NOT FOUND`.
- [ ] **Step 2: Run it.** Expected: FAIL.
- [ ] **Step 3: Implement** using `verify.locate` for quote positions.
- [ ] **Step 4: Run the suite; update `campaigns/v10/runbook.md` gate step.** The runbook text reads: "Claude runs `labeling inspect` and reads `inspect.md` against the documents before any `gate --approve`." Expected: green.
- [ ] **Step 5: Commit.** `labeling: inspect report for reading model outputs before a gate`

### Task 8: Merger agreements: section-aware locator (runs before Task 7)

Measured 2026-11-06 on the 151 mergers:
- The median window is 15% of the agreement.
- The regex locator found Conversion of Shares in 23 of 151 agreements and Outside Date in 25.
- It often lands on the definitions article instead of the operative section.

The user chose: fix the locator for free, then A/B it against full text (about $1, DeepSeek only), and go full-text only if that is measurably better.

**Files:**
- Create: `src/mailroom_eda/labeling/sections.py`
- Modify: `src/mailroom_eda/labeling/tasks.py` (`merger_window`, `consideration_window`), `src/mailroom_eda/labeling/runner.py` (`run --full-text`)
- Test: `tests/test_labeling.py`

**Interfaces:**
- Produces:
  - `sections.headings(text) -> list[Heading]`, where `Heading = (start: int, number: str, title: str)`. It matches line-start numbered headings (`Section 3.01 Title.`, `2.7  Title.`, `§ 8.6 Title.`) and `ARTICLE <roman|digit>. TITLE`. Of the occurrences of one section number, it keeps the earliest whose text before the next heading is longer than 200 characters, so table-of-contents entries are skipped while later exhibits that reuse numbers are not picked.
  - `sections.section_text(text, heading, cap=15_000) -> str`: the heading through the next heading, capped.
  - `sections.definition(text, term) -> str | None`: the paragraph that defines `term` (`"<term>" means|shall mean|has the meaning`), up to 1,500 characters.
  - `sections.locate(text, needs) -> list[tuple[str, str]]`: (label, excerpt) pairs. Each need has a heading-title regex and the defined terms to include. Outside Date also matches its inline definition `(the "Outside Date")` / `"End Date"` and takes the section that contains it.
- `merger_window` returns: the first 24,000 characters, then `sections.locate` for the seven `MERGER_SECTIONS` needs plus definitions of `Termination Fee`, `Parent Termination Fee`, `Company Termination Fee`, `Outside Date`, `End Date`, `Effective Time` and `Merger Consideration`, then the last 8,000 characters. Total cap 120,000 characters.
- `consideration_window` uses the same locator for Conversion of Shares, Merger Consideration and Election, plus definitions of `Merger Consideration`, `Per Share Merger Consideration`, `Exchange Ratio`, `Cash Consideration` and `Stock Consideration`. Cap 40,000 characters.
- Both put their cap and locator version into the prompt sha config.
- `run --full-text` sends the whole document for the merger tasks and records `"window": "full"` in the sha config. It is used only for the A/B.

- [ ] **Step 1: Write the failing unit tests** on a synthetic agreement:
  - `test_headings_skip_table_of_contents_and_later_exhibits`: a TOC line lists `Section 8.6 Termination Fee 71`; the body has the real `Section 8.6 Termination Fee.` with 1,000 characters of text; an exhibit reuses `Section 8.6`. Only the body heading is returned.
  - `test_locate_takes_whole_operative_section_and_its_definition`: the excerpt for Termination Fee includes `$35,000,000` from `"Parent Termination Fee" means $35,000,000` in the definitions article.
  - `test_outside_date_found_by_inline_definition`: `... on or before June 30, 2027 (the "Outside Date")` inside Section 8.2 returns Section 8.2.
- [ ] **Step 2: Write the failing corpus test** `test_merger_locator_finds_every_needed_section` (skip without the stage). Across the 151 mergers it requires:
  - each of the seven needs located in ≥ 140
  - the excerpt carries its value signal (the regexes in the 2026-11-06 measurement) in ≥ 130 for Conversion, Termination Fee, Governing Law and Outside Date
  - every window ≤ 120,000 characters
- [ ] **Step 3: Run them.** Expected: all FAIL.
- [ ] **Step 4: Implement** `sections.py` and the two windows. Iterate on the heading-title regexes against the corpus test only. Every miss it prints gets read as text, not guessed.
- [ ] **Step 5: Run the suite; regenerate `costs.md`** (merger chunks rise); keep caps ≤ $30.00. Expected: green, current.
- [ ] **Step 6: Commit.** `labeling: section-aware merger locator (TOC-skipping, definitions included)`
- [ ] **Step 7: Paid A/B, after the user confirms the spend.** All arms are DeepSeek v4.1 Flash, `off`:
  - `merger_consideration` calibration (95 expert answers), located vs `--full-text`
  - `merger_entities` pilot (13 agreements), located vs `--full-text`

  Score consideration accuracy, run `inspect` on the pilot, read every field that differs against the agreement, ledger the findings and report credit.

  Rule: adopt full text for a task only if the paired bootstrap lo95 of (full − located) consideration accuracy over the 95 agreements is > 0, or if the pilot shows a fact present only in the full text in ≥ 2 of 13 agreements (read and confirmed against the agreement).

### Task 7: Re-plan chunks 2–9 from the measured winners (after the user-authorized bake-off)

**Precondition:** the user has authorized the bake-off, and chunks 1, 1h and 3 have run. For each arm, `gate` was scored, `inspect.md` was read against the documents (findings recorded in the ledger), and `labeling bakeoff 1` and `labeling bakeoff 3` were run. Report credit.

**Files:**
- Modify: `src/mailroom_eda/labeling/campaigns/v10/campaign.yaml`, `costs.md` (regenerated), plan Part D and Part F tables, `campaigns/v10/runbook.md`
- Test: `tests/test_labeling.py`

**Interfaces:**
- Consumes: `1.bakeoff.json` and `3.bakeoff.json` (`primary`, `second`: model and reasoning).
- Rules, written into `campaign.yaml`:
  - **Legal tasks** (`2a`–`2c`, `5a`–`5e`, `6c0`–`6c`, `9a`–`9e`): `models: [<primary of 1>, <legal second>]`. The legal second is `z-ai/glm-5.3-flashx-legal` if it is non-inferior to the primary on the pooled 80 contracts; otherwise it is `second` of chunk 1.
  - **General tasks** (`4`, `7a`–`7e`, `8a`–`8e`): `models: [<primary of 3>, <second of 3>]`.
  - **Reasoning:** each chunk takes its source bake-off winner's reasoning. GLM-legal and MiniMax are always `low`.
  - **Caps:** 1.5× the regenerated projection, total ≤ $30.00. If the total exceeds usable credit, the runbook states the top-up needed before the first chunk that does not fit.
  - Every new (model, reasoning) route gets a `ping --per-provider --real-doc` before its first chunk.

- [ ] **Step 1: Write the failing test** `test_two_model_chunks_pair_two_vendors`: every chunk with two models has two distinct vendors and no model older than `deepseek-v4.1-flash`.
- [ ] **Step 2: Run it.** Expected: FAIL while chunks 5, 6 and 9 still list GLM `off` or other stale pairs.
- [ ] **Step 3: Apply the rules** to `campaign.yaml`; regenerate `costs.md`; update Part D/F and the runbook.
- [ ] **Step 4: Run the suite, `costs --check`, `sync_plan_prompts.py --check`.** Expected: green, current.
- [ ] **Step 5: Commit.** `labeling: chunks 2–9 use the measured primary and second opinion`
