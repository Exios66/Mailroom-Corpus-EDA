# v10 campaign runbook

The operator's checklist for running the v10 label-completion campaign one chunk at a
time. The chunk list is `campaign.yaml`, the projections are `costs.md`, and the design is
in `docs/plans/v10-structure-and-label-completion.md`.

Nothing here runs by itself. You start each paid chunk with your own initials. The chunk's
USD cap and wall-clock limit come from `campaign.yaml`. A chunk will not start until every
chunk it `needs` has been approved.

All commands run from the repository root. `L` below stands for
`PYTHONPATH=src python -m mailroom_eda.labeling`; set it once:

```bash
L="env PYTHONPATH=src python -m mailroom_eda.labeling"
```

## One-time setup (about five minutes)

1. **Install and test** (offline):

   ```bash
   python -m venv .venv && .venv/bin/pip install -e ".[dev]"
   source .venv/bin/activate
   python -m pytest tests/test_labeling.py -q
   ```

2. **Build the stage.** This downloads the public v9.2 Hub tag and rebuilds
   `data/v9_4/stage/` (about 20 seconds), then checks its sha against `campaign.yaml`:

   ```bash
   $L stage
   ```

3. **Put the key in the environment only.** Never write it into a file in the repository.
   On the OpenRouter key page, give the key a credit limit a little above the campaign total
   (about $30). The runner keeps a $3 reserve on top of each chunk's cap.

   ```bash
   export OPENROUTER_API_KEY=...
   ```

4. **Check readiness.** `doctor` runs every check without a paid call: Python, the stage
   sha, the Dojo pin, the prompts in the plan, `costs.md`, live OpenRouter prices and
   provider routing, the key's remaining credit, and the approvals so far. It ends with the
   next chunk to run.

   ```bash
   $L doctor
   ```

   `$L prices` prints the full routing report: for every model, the pinned providers that
   currently serve it with every parameter we send (strict JSON schema, reasoning control,
   `max_tokens`, `temperature`, and `seed` where the route sends it), their prices, and
   any problem. If a price moved, update `models.yaml` (`checked:` too) and regenerate the
   projections with `$L costs`.

5. **Ping.** One tiny strict-schema request per model and reasoning level the campaign
   uses, capped at $0.05 in total (it usually costs well under $0.01):

   ```bash
   $L ping --initials XX
   ```

   A paid chunk refuses to start unless every (model, reasoning) route it uses has a passing
   ping from the last 24 hours. Add `--per-provider` to also ping each pinned provider alone
   and log its reasoning tokens, and `--real-doc` to send one real intent-calibration document
   instead of the tiny prompt (only the real-document probe shows whether a provider reasons:
   DeepSeek spent about 30 tokens on the tiny prompt and 336 to over 2,300 on a real email); a provider that reports 0 reasoning tokens at `low` belongs
   outside that level's `by_reasoning` pin in `models.yaml`. GLM-legal and MiniMax refuse
   reasoning off (HTTP 400); no chunk runs them at off. Add `--chunk <id>` (repeatable) to ping
   only the routes those chunks use, so a deferred route that is not ready yet does not block them.

   Every line must say `PASS ... schema=strict`. A `json_object fallback` means that route
   stopped supporting strict schemas; stop and run `$L prices`.

## Every chunk

`<id>` is the chunk's `id` in `campaign.yaml` (`1`, `2a`, `5c`, ...). Chunk 1 and chunk 3
have several arms (a bake-off); add `--arm N` to each command for those.

1. **Dry run.** Shows the rows, models, reasoning, projected tokens and USD, and the cap.
   No network call.

   ```bash
   $L chunk <id> [--arm N] --dry-run
   ```

2. **Run.** Four calls run in parallel by default (`--workers`). The run checks the key's
   remaining credit first, reserves the worst case of every call before sending it, and
   stops before anything could exceed the cap or the wall limit. Interrupt it at any time;
   running the same command again skips answered calls.

   ```bash
   $L chunk <id> [--arm N] --initials XX
   ```

   Exit codes: 0 done, 2 stopped at the cap or the wall (re-run to continue, or raise the
   cap in `campaign.yaml` if the projection was wrong), 3 some calls failed (re-run; they
   are retried).

3. **Review.** Output is in `docs/reports/audits/v10_labels/<id>[.<arm>]_<task>-<set>[-<i>of<n>]/`:

   | file | what to do with it |
   |---|---|
   | `documents.csv` | one row per document: status, counts, models, tokens (spend is in `calls.jsonl` and `run.json`, never in a published file) |
   | `fields.csv` | one row per (document, field): both models' values and evidence, the decision and why. On pilots, mark at least 30 accepted rows `correct` or `wrong` in `reviewer_verdict` |
   | `adjudication.csv` | only the rows a person must decide; fill `reviewer_verdict` (`a` or `b` to take that model's value, `value` with your own `reviewer_value`, `absent` when the document does not state it, `reject` to leave it unlabeled). `$L review` applies each verdict to `final_decision` and `final_value` in `fields.csv`; ingest reads only those two columns |
   | `sample.csv` | agreement chunks: 20 accepted rows to read before signing off |
   | `spans.csv` | CUAD only: every span, whether it was found verbatim, and why it was dropped |
   | `cuad-labels.jsonl`, `intent-labels.jsonl` | the records the v9.4 calibration and ingest code reads |
   | `calls.jsonl` | every raw answer with its model, served provider, reasoning, prompt sha, tokens and cost |
   | `runs.jsonl` | one line when a run starts (settings, projection, operator's initials) and one when it finishes (result and review summary); re-runs append |
   | `gate.json` | written by `$L gate`: the last gate result for this chunk arm |

   After editing a CSV, refresh the derived files (offline; reviewer entries are kept):

   ```bash
   $L review --task <task> --set <set> [--chunk i/n] --out docs/reports/audits/v10_labels/<dir> [--reasoning off]
   ```

4. **Gate.** Scores the chunk's gate offline and prints the result. For `agreement` chunks,
   read `sample.csv` first and add `--signed-off`. The gate scores and approves the models
   and provider the chunk actually ran (from `runs.jsonl`), so after a Modal run pass the same
   `--provider modal`; a different `--provider` is refused.

   ```bash
   $L gate <id> [--arm N] [--signed-off]
   ```

5. **Approve.** Records the passing configuration (models, served providers, reasoning,
   prompt sha, gate scores, your initials) in `campaigns/v10/approved.yaml`. Later chunks
   that use `winner_of_<id>` take their model and reasoning from it. For a bake-off, approve
   only the winning arm (`--replace` changes an earlier choice).

   ```bash
   $L gate <id> [--arm N] [--signed-off] --approve --initials XX
   ```

6. **Commit** the chunk directory and `approved.yaml` together, and add a line to the
   sign-off log at the end of this file.

`$L status` shows every chunk: calls answered, spend, gate and approval.

If a gate fails, stop: fix the prompt or the model choice and re-run that chunk's
calibration. A prompt change changes its sha, so earlier calibrations no longer cover it.

## Gates

| kind | passes when |
|---|---|
| `cuad_f1` | micro-F1 ≥ 0.75 and precision ≥ 0.80 against the expert CUAD spans (`v9_4_revision.score_cuad_calibration`). Bake-off arms are compared by paired bootstrap intervals (`docs/plans/2026-11-06-labeling-settings-optimization.md`), not by raw F1 gaps |
| `intent_kappa` | Cohen's κ ≥ 0.75 against the manual intent labels (`v9_4_revision.score_intent_calibration`) |
| `field_accuracy` | per-field accuracy ≥ 0.85 against values derived from the expert CUAD spans |
| `consideration_accuracy` | each model answers every one of the 95 mergers with an expert MAUD Type of Consideration answer, and each reaches ≥ 0.95 accuracy against it; `gate.json` has a confusion table per model |
| `human_verdicts` | at least 30 accepted `fields.csv` rows reviewed, and at least 90% of them marked correct |
| `agreement` | no unanswered calls, at most 30% of fields sent to adjudication, and a reviewer read the 20 rows in `sample.csv` (`--signed-off`) |
| `review` | a person read the CSVs and found no systematic error |

Calibration covers one configuration: (model, provider, reasoning, prompt sha, temperature).
The v10 build refuses labels whose configuration has no passing calibration or pilot.

## Order and dependencies

- Chunks 1–4 and 7–9 can run at any time.
- Chunks 5 and 6 (contract and merger entities) label the v2 schema fields; run them after
  llm-dojo-scoring 0.28.0 has merged, so the fields they fill are the ones the Dojo scores
  (`docs/plans/v10-consumer-patches.md`, G1). Their first chunks (5a, 6a) carry
  `requires:`; `$L chunk` refuses them until you confirm the release has landed with
  `--ack-requires`, and `$L doctor` lists them as waiting instead of offering them next.
- Chunks 6c0 and 6c1 label `merger_consideration` for the 56 MAUD mergers whose subclass
  is `other` (they have full MAUD clause labels but no expert Type of Consideration
  answer). 6c0 calibrates both models on the 95 mergers that do have the answer; 6c1
  labels the 56. Chunks 6a, 6b and 6c need 6c1. Neither chunk depends on the Dojo
  release: `merger_consideration` is a v1 field.
- Chunk 10 is human adjudication. Chunk 11 re-runs the v10 build and every gate.
- Any one-model chunk can run on Modal instead:
  `$L chunk <id> --provider modal --initials XX`, with `VLLM_BASE_URL` and `VLLM_API_KEY`
  set and the Modal app already deployed by a person (this package never deploys it).
  The campaign's OpenRouter model is mapped to the one approved Modal model
  (`Qwen/Qwen3-32B-FP8`); with several approved Modal models, name the mapping with
  `--model-map <campaign_model>=<modal_model>` (repeatable). A two-model chunk needs two
  different models, so it runs on Modal only once a second Modal model is approved in
  `models.yaml`; until then run it on OpenRouter. The Modal model is a different
  configuration, so calibrate it before it labels anything. Its `models.yaml` token price
  is a conservative equivalent of the GPU-hour cost (at least 1M prompt and 0.25M output
  tokens per GPU-hour), so the cap still bounds spend; `costs.md` therefore shows a
  ceiling for it, not an estimate.
- Spend: the chunk caps add up to $29.76 and the projection is about $20.36 (`costs.md`).

## Sign-off log

| chunk | date (UTC) | operator | models | reasoning | spent USD | gate result | notes |
|---|---|---|---|---|---:|---|---|
