# `mailroom_eda.labeling`: the label-completion playbook

This package fills the labels the mailroom dataset is missing, using OpenRouter or a Modal
vLLM endpoint. Every label is backed by verbatim evidence and two-model agreement or a human
verdict, and the work is split into small chunks that a person starts and signs off one at
a time. Everything a later iteration needs to replay or improve the campaign is in here:
the field registry, the prompts, the model list with prices, the campaign plan and the code.

## The method

1. **Design.** `fields.yaml` lists every (class, field) the Dojo scores, plus the adapted
   contract and merger v2 fields. Each field has a Dojo scoring type, a source
   (`deterministic`, `gold` or `llm`) and an absence policy. Its Dojo types are pinned to a
   snapshot of llm-dojo-scoring (`dojo_snapshot/`) and checked by `tests/test_labeling.py`.
2. **Calibrate.** Each task first runs on rows that already have expert or manual labels
   (40 golden CUAD contracts, 96 manual intents) or on a small stratified pilot that a
   person reviews. A configuration (model, provider, reasoning, prompt sha, temperature)
   is used only once its calibration passes.
3. **Chunk.** The remaining rows are split into chunks with their own USD caps
   (`campaigns/v10/campaign.yaml`). Projections are generated into `costs.md`.
4. **Gate.** After each chunk, `review` writes CSVs for a person to read. The next chunk
   starts only after the gate in `runbook.md` passes and is signed off.
5. **Adjudicate.** Disagreements and failed evidence checks go to `adjudication.csv`;
   a reviewer's verdict decides them.
6. **Ingest.** The v10 build reads the journals, re-verifies every value against the
   document, and refuses anything from an uncalibrated configuration.

## Rules every label follows

- **Grounded in the text.** Scored models see only `doc_text`, so a value the text does
  not state is absent, even if row metadata knows it.
- **Verbatim evidence.** Each value comes with a quote that must be found in the document
  (whitespace and markdown marks aside), and the value's tokens must occur in it.
- **Two models.** Extraction tasks run two different model families. A value is accepted
  when both agree after normalising for its Dojo type; otherwise a person decides.
- **Absence is explicit.** `absent_ok` fields become `schema_documented_absence` when both
  models find nothing. `evidence_only` fields need a quoted absence statement.
  `never_absent` fields always need a value.
- **Reasoning.** Off for extraction (it is copying, and reasoning tokens are billed);
  `low` is tried for intent and kept only if calibration says so; never `high`.

## Files

| path | what it is |
|---|---|
| `fields.yaml` | the field registry and closed vocabularies |
| `models.yaml` | approved model configurations and prices, plus priced comparison models |
| `prompts/*.md` | every prompt, with `${name}` placeholders; a record stores the sha of the prompt bundle it used |
| `dojo_snapshot/dojo_schemas.json` | the pinned Dojo catalogs (`scripts/labeling/snapshot_dojo.py`) |
| `campaigns/v10/` | `campaign.yaml` (chunk order, caps, gates), `costs.md` (generated), `runbook.md` |
| `registry.py` | loads the registry, prompts and campaign |
| `tasks.py` | turns a row into messages and a strict JSON schema, one builder per task |
| `providers.py` | OpenRouter and Modal clients: per-model provider routing from `models.yaml`, retries, schema fallback, usage and cost |
| `spend.py` | the run gate: initials, cap, wall limit, key headroom |
| `verify.py` | evidence location, grounding, per-type normalisation, agreement, decisions |
| `export.py` | the JSONL journal and the review CSVs |
| `runner.py` | the low-level command line: `select`, `estimate`, `run`, `review`, `costs` |
| `operate.py` | the campaign commands: `stage`, `doctor`, `prices`, `ping`, `chunk`, `gate`, `status`, `check-class` |
| `derive.py` | deterministic labels (CUAD family, merger consideration, communication type, specialist) |
| `integrations/` | the cross-repository runbook for any integration change (a new document type or expanded labeling on the core five): release order, version lockstep table (`check_lockstep.py`), drift checks, both checklists, candidate document types with sources and licences, templates and a worked commercial-lease example |

## Quick reference

The campaign commands read everything (rows, models, reasoning, cap, wall limit) from
`campaigns/v10/campaign.yaml`; `campaigns/v10/runbook.md` is the step-by-step guide.

```bash
PYTHONPATH=src python -m mailroom_eda.labeling stage                 # build and check the v9.4 stage
PYTHONPATH=src python -m mailroom_eda.labeling doctor                # every readiness check, no paid call
PYTHONPATH=src python -m mailroom_eda.labeling ping --initials XX    # one tiny request per model (< $0.05)
PYTHONPATH=src python -m mailroom_eda.labeling chunk 1 --arm 1 --dry-run
PYTHONPATH=src python -m mailroom_eda.labeling chunk 1 --arm 1 --initials XX
PYTHONPATH=src python -m mailroom_eda.labeling gate 1 --arm 1 --approve --initials XX
PYTHONPATH=src python -m mailroom_eda.labeling status
```

The low-level `select`, `estimate`, `run` and `review` commands take every setting as a
flag; a paid `run` also needs `--initials`, `--api-cost-cap-usd` and `--max-wall-seconds`.

## Changing the playbook

- **A prompt.** Edit `prompts/<name>.md`, run `python scripts/labeling/sync_plan_prompts.py`
  to copy it into Part E of `docs/plans/v10-structure-and-label-completion.md` (a test
  compares them), and re-calibrate: the prompt sha changes, so earlier calibrations no
  longer cover it.
- **A model.** Add it to `models.yaml` with its provider, current prices and a `route:` (pinned
  providers, quantization, `max_price`, whether its endpoints accept `seed`), run `prices`, regenerate
  `costs.md`, and calibrate it before it labels anything.
- **The Dojo schema.** Refresh the snapshot with
  `python scripts/labeling/snapshot_dojo.py --dojo-src <llm-dojo-scoring>/src`, update
  `fields.yaml` and its `dojo_pin`, and run `tests/test_labeling.py`.
- **A new campaign.** Copy `campaigns/v10/` to `campaigns/<name>/` and pass
  `--campaign <name>` to `costs`.
- **A new document class or new labels.** Start at `integrations/README.md` (release order across the
  four repositories); for a class follow `integrations/GUIDE_new_doc_type.md` and check the proposal
  with `python -m mailroom_eda.labeling check-class <folder>`.
