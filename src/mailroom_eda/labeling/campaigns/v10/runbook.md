# v10 campaign runbook

The operator's checklist for running the v10 label-completion campaign one chunk at a
time. The chunk list is `campaign.yaml`, the projections are `costs.md`, and the design is
in `docs/plans/v10-structure-and-label-completion.md`.

Nothing here runs by itself. Every paid chunk is started by a person, with their own
initials, a USD cap and a wall-clock limit, and only after the previous chunk's gate passed.

## Before the first chunk

1. Build the v9.4 stage if `data/v9_4/stage/ground_truth_hardened.jsonl` is missing
   (`python run_all.py` phases through v9.4; `data/` is gitignored).
2. Check the prices on <https://openrouter.ai/models> against `models.yaml`. If any changed,
   edit `models.yaml`, update `checked`, and regenerate the projections:

   ```bash
   PYTHONPATH=src python -m mailroom_eda.labeling costs
   ```

3. Put the key in the environment only. Never write it to a file in the repository.

   ```bash
   export OPENROUTER_API_KEY=...        # or, for Modal: VLLM_BASE_URL and VLLM_API_KEY
   ```

4. Run the offline tests: `python -m pytest tests/test_labeling.py -q`.

## Every chunk

Replace `<...>` with the chunk's row in `campaign.yaml`. `XX` is your initials.

1. **Look at the rows.**

   ```bash
   PYTHONPATH=src python -m mailroom_eda.labeling select --task <task> --set <set> --chunk <i/n>
   ```

2. **Dry run.** This prints the projected tokens and USD and makes no network call.

   ```bash
   PYTHONPATH=src python -m mailroom_eda.labeling run --task <task> --set <set> --chunk <i/n> \
     --provider openrouter --model <model_a> --second-model <model_b> --reasoning off --dry-run
   ```

3. **Run.** The runner checks the key's remaining credit (keeping a $3 reserve), refuses a
   call that could exceed the cap, and stops at the wall limit. A stopped or interrupted
   chunk can be re-run with the same command: answered calls are skipped. Exit code 2 means
   it stopped at the cap or the wall.

   ```bash
   PYTHONPATH=src python -m mailroom_eda.labeling run --task <task> --set <set> --chunk <i/n> \
     --provider openrouter --model <model_a> --second-model <model_b> --reasoning off \
     --initials XX --api-cost-cap-usd <cap_usd> --max-wall-seconds 5400
   ```

4. **Review.** Output is in `docs/reports/audits/v10_labels/<task>-<set>-<i>of<n>/`:

   | file | what to do with it |
   |---|---|
   | `documents.csv` | one row per document: status, counts, models, tokens, cost |
   | `fields.csv` | one row per (document, field): both models' values and evidence, the decision and why. On pilots, mark each reviewed row `correct` or `wrong` in `reviewer_verdict` |
   | `adjudication.csv` | only the rows a person must decide; fill `reviewer_verdict` (`a` or `b` to take that model's value, `value` with your own `reviewer_value`, `absent` when the document does not state it, `reject` to leave it unlabeled) |
   | `spans.csv` | CUAD only: every span, whether it was found verbatim, and why it was dropped |
   | `cuad-labels.jsonl`, `intent-labels.jsonl` | the records the v9.4 calibration and ingest code reads |
   | `calls.jsonl` | every raw answer with its model, provider, reasoning, prompt sha, tokens and cost |
   | `run.json` | the exact command settings, the projection and the operator's initials |

   `review` needs no network and can be re-run at any time; it keeps every `reviewer_*`
   entry already in `fields.csv` and `adjudication.csv`:

   ```bash
   PYTHONPATH=src python -m mailroom_eda.labeling review --task <task> --set <set> --chunk <i/n>
   ```

5. **Gate.** Check the chunk's gate (below). If it fails, stop the sequence: fix the prompt
   or the model choice, and re-run calibration. A prompt change changes its sha, so earlier
   calibrations no longer cover it.

6. **Sign off.** Add a line to the log at the end of this file, then commit the chunk directory.

## Gates

| kind | passes when |
|---|---|
| `cuad_f1` | micro-F1 ≥ 0.75 and precision ≥ 0.80 against the expert CUAD spans (`v9_4_revision.score_cuad_calibration`). For the GLM `low` arm, adopt it only if its micro-F1 beats the `off` arm by 0.02 or more |
| `intent_kappa` | Cohen's κ ≥ 0.75 against the manual intent labels (`v9_4_revision.score_intent_calibration`) |
| `field_accuracy` | per-field accuracy ≥ 0.85 against values derived from the expert CUAD spans |
| `human_verdicts` | at least 90% of the reviewed `fields.csv` rows marked correct |
| `agreement` | every disagreement and every failed evidence check is in `adjudication.csv`; a reviewer reads a sample of 20 accepted rows |
| `review` | a person read the CSVs and found no systematic error |

Calibration covers one configuration: (model, provider, reasoning, prompt sha, temperature).
The v10 build refuses labels whose configuration has no passing calibration or pilot.

## Order and dependencies

- Chunks 1–4 and 7–9 can run at any time.
- Chunks 5 and 6 (contract and merger entities) label the v2 schema fields; run them after
  llm-dojo-scoring 0.28.0 has merged, so the fields they fill are the ones the Dojo scores
  (`docs/plans/v10-consumer-patches.md`, G1).
- Chunk 10 is human adjudication. Chunk 11 re-runs the v10 build and every gate.
- Any chunk can run on Modal instead: `--provider modal --model Qwen/Qwen3-32B-FP8`, with
  `VLLM_BASE_URL` pointing at the deployed app. Calibrate that configuration first.

## Sign-off log

| chunk | date (UTC) | operator | models | reasoning | spent USD | gate result | notes |
|---|---|---|---|---|---:|---|---|
