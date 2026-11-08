# labeling prompt surface — learned (autoresearch:learn)

`generated_by: autoresearch` — written at loop start; validated field-for-field against
`src/mailroom_eda/labeling/fields.yaml` and `tasks.py` (see learn-results.tsv).

## How prompts are assembled

`tasks.py` builds every request from templates in `src/mailroom_eda/labeling/prompts/`:

| task | system | user | notes |
|---|---|---|---|
| intent | `intent_system.md` | `intent_user.md` (subject line, email) | closed 8-class vocabulary; `+variant` letter in sha |
| contract_entities | `extract_rules.md` | `contract_entities.md` | family context + field list from registry |
| merger_entities / merger_consideration / corporate / correspondence / claim_checklist | `extract_rules.md` | `<task>.md` | same shared contract |
| cuad | `cuad_system.md` | `cuad_user.md` (+ `cuad_repair.md` on repair passes) | 41 categories, strict schema |
| jev | `jev_intent.json` | — | JSON structured prompt |

Every task's `prompt_sha256` is `prompt_bundle_sha([system, user])` plus the field specs and
vocabulary lists (`_task_config`), so a re-run lands on exactly the prompt it answered.
Changing ANY text in any template invalidates the gold smoke fixtures (`smoke_fixtures
check` fails) — re-record the affected type in the same PR (~$0.01/type).

## The per-template contract (validated)

- **extract_rules.md** — the shared schema for every extraction task: `{"fields": {"field":
  {"value", "status": present|documented_absent|not_found, "evidence"}}}`; verbatim-copy
  evidence bound; names, dates, money, ids, free_text typed rules; "never guess, never infer".
- **contract_entities.md / merger_entities.md** — list-of-fields per registry; explicit
  absence strings (`[]) with not_found`), anti-provenance guard ("never from the file name").
- **claim_checklist.md** — the densest template: per-category operative rules (quote whole
  printed lines, no paraphrase, one item per category per line, corpus-echo exclusions).
- **cuad_system.md** — CUAD-style presence tests with negation/disclaimer rules and
  precision-over-recall ("a missed category costs less than a wrong one").
- **corporate.md / correspondence.md / merger_consideration.md** — subclass descriptions and
  enum options injected from `fields.yaml` vocabularies; label fields never answer not_found.

## Where quality is measured

- Paul pilot evidence: `docs/reports/audits/v10_labels/**/fields.csv` — the A/B value pairs,
  agree flag and adjudication reason per (document, field).
- Gold gates: `operate.py` gate kinds (`intent_kappa`, `cuad_f1`, ...) with the floors in
  `campaigns/v10/campaign.yaml`; chunk 3 = 0.8233 kappa (intents), approved 2026-11-06.
- Smoke fixtures: `check` (staleness), `replay` (offline determinism), `verify-hub`.

## Known staleness economics

Directorate artifacts budgeted in the same $1.00 recording line the fixtures use; a full
five-type re-record projects ~$0.0432 with today's measured allowances (see
`campaigns/v10/costs.md`), and individual types scale by their `--cap-usd` share.
