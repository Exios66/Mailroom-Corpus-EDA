# Adding a document type to the mailroom dataset

The mailroom dataset has five document classes: `contract`, `merger_agreement`,
`corporate_record`, `correspondence` and `insurance_claim`. This folder explains how to add
a sixth (or tenth) class end to end, so the new documents can be classified, routed to a
specialist, fully labeled and scored. It covers this repository and everything downstream.

The worked example is a **commercial lease** class (`examples/commercial_lease/`): leases
are a common piece of real mailroom intake (landlord notices, renewals, amendments), there
are none in the current corpus, and SEC EDGAR has thousands of public lease exhibits.

| file | what it is |
|---|---|
| `README.md` | this walkthrough |
| `CHECKLIST.md` | every change in every repository, as a tick list for the PRs |
| `find_touchpoints.py` | lists every line in each repository that names the current classes, so nothing is missed |
| `templates/` | a `fields.yaml` class block, a task prompt and campaign chunks to copy |
| `examples/commercial_lease/` | a complete, validated proposal: schema, prompt, chunks, sourcing notes |

## The order matters

The repositories depend on each other, and a class only works when every layer knows it.
Make the changes in this order and do not publish the dataset until every PR has merged:

1. **Decide and source** the class (below). No code yet.
2. **llm-dojo-scoring** owns the entity schema and the scoring types. It goes first, and
   everything else mirrors it.
3. **Mailroom-Corpus-EDA** (this repository): the labeling registry, the prompt, the
   campaign, then the dataset build, integrity checks and card.
4. **llm-mailroom**: taxonomy, specialist agent, prompts, loaders.
5. **local-mailroom-sandbox**: vendor refresh of both of the above, corpus size and class
   tables, run configs.
6. **Publish** a new dataset tag, then bump the pins.

Run `python src/mailroom_eda/labeling/new_doc_types/find_touchpoints.py --repo <path> ...`
on all four checkouts before you start; it prints the files that name the current five
classes, which are the files you will edit.

## Step 1: decide whether the class belongs

A new class has to earn its place. Check all of these before writing code:

- **It is real mailroom intake.** Someone would plausibly mail, email or upload it to an
  organisation's legal or operations inbox.
- **It is distinct.** A classifier must be able to separate it from the five existing
  classes using the text alone. Look for overlap first: grep the stage for the class's
  telltale phrases (`landlord`, `premises`, `sublease` for leases). Leases are a good
  example: there is no CUAD lease family, and only one current document reads like one.
- **It has a closed specialist job.** Write down the entity fields a specialist would
  extract (10 to 20 is typical) and the subclasses (5 to 10). If you cannot, the class is
  too vague.
- **There is enough public text.** Aim for at least 150 documents, with every subclass
  having at least 15, so the pilot and the test split are meaningful.
- **The licence allows redistribution.** The dataset card lists a licence per source. Text
  under a non-commercial or share-alike licence can be used to calibrate labels but should
  not be published as rows unless the whole release accepts those terms.
- **Personal data is acceptable.** Public filings are fine. Anything with private
  individuals' details needs the same review the Enron emails had.

## Step 2: source the documents

Reuse the existing sourcing paths where possible. They already produce stable
`document_id`s, `content_sha256` and provenance metadata.

- **SEC EDGAR exhibits** are how the corpus already gets EX-10 contracts and corporate
  records (`source_corpus: sec_edgar`). Leases are filed as EX-10 exhibits whose description
  names a lease. Select by exhibit description and by the first page's title, and keep the
  accession, CIK and exhibit URL in `metadata`.
- **A labeled public dataset** is valuable as calibration gold even when its licence keeps
  it out of the published rows. For leases, the ALeaseBERT benchmark (Leivaditi, Rossi and
  Kanoulas, *A Benchmark for Lease Contract Review*, arXiv 2010.10386) has 179 leases
  annotated with entities and red flags, under CC BY-NC-SA 4.0. Map its entity types onto
  your fields and use it the way chunk 1 uses the golden CUAD contracts.
- **Synthetic documents** are a last resort and must be marked `synthetic` (like the CMS
  claims renders), with the generator recorded.

Every row needs `filename`, `doc_text`, `expected` (the class key), `expected_subclass`,
`split` from the md5 rule (`md5(filename) % 10 == 0` means test), `source_corpus`,
`source_document_id`, `metadata`, and the identity fields from `identity.py`. Text cleanup
follows the v10 rules (LF line endings, no C0 control characters, no NBSP).

## Step 3: llm-dojo-scoring (the schema owner)

The Dojo decides which fields exist and how each is scored. Add the class there first,
release a minor version, and only then mirror it elsewhere. `CHECKLIST.md` lists each
constant; the main ones are:

- `config.py`: `DOC_CLASS_KEYS`, `LIVE_DOC_CLASS_KEYS`.
- `scoring/suites.py`: `SPECIALIST_DOC_TYPES`, `LIVE_DOC_TYPES`, `LIVE_SPECIALISTS`, and the
  class's `DEFAULT_FIELD_TYPES` map (one scoring type per field).
- `pipeline/mailroom.py`: the live class, specialist and sorter label tuples.
- `schemas/corpus.py`: `CORPUS_DOC_TYPES`, the subclass surface tables,
  `CORPUS_DIFFERENTIATORS`, `CORPUS_EXTRACTION_FIELDS`.
- `schemas/intents.py` and `schemas/scored_enums.py`: the intent vocabulary and any closed
  label enums.
- `schemas/extraction_schemas.py`, `metrics/registry.py`, `schemas/sorter_guide.py`.

Pick field types deliberately: `label` for a closed list (exact match), `name` for
names (fuzzy), `date`, `money`, `id` (exact after normalising), `free_text` (token overlap)
and `entity_list:*` for lists. A closed list typed `name` gets fuzzy credit, which is how
`urgency` and `communication_type` ended up retyped in v10.

## Step 4: this repository, labeling first

Everything for labeling is configuration. No Python changes are needed for a new
extraction task.

1. **`fields.yaml`.** Add the class block (copy `templates/class_block.yaml`): the
   specialist key, and per field its Dojo `type`, `source` (`llm` for new labels), `task`,
   `absence` policy, a one-line `description` and a `where` hint. Add any closed
   vocabularies under `vocabularies:`. Declare the extraction task under `tasks:`:

   ```yaml
   tasks:
     lease_entities: {class: commercial_lease, prompt: lease_entities, two_model: true,
                      pilot_per_stratum: 3, max_tokens: 3000, typical_output: 1200}
   ```

   The generic builder renders `${field_list}` from the descriptions, so the prompt and the
   schema cannot drift apart.
2. **`prompts/<task>.md`.** Copy `templates/task_prompt.md`. It may use only
   `${subclass}`, `${field_list}`, `${window_description}` and `${doc_text_window}`. The
   shared `extract_rules.md` is the system prompt and already covers evidence, statuses
   and value formats.
3. **Check it offline.**

   ```bash
   PYTHONPATH=src python -m mailroom_eda.labeling check-class src/mailroom_eda/labeling/new_doc_types/examples/commercial_lease
   ```

   This merges the folder's `fields.yaml` and prompts into the registry and reports every
   problem: unknown types, a label without a vocabulary, a task that targets the wrong
   class, a prompt with placeholders the builder cannot fill.
4. **Dojo snapshot.** After the Dojo release, refresh
   `dojo_snapshot/dojo_schemas.json` with `scripts/labeling/snapshot_dojo.py` and update
   `dojo_pin`. `tests/test_labeling.py` then checks your types against the Dojo's.
5. **Campaign.** Add the class's chunks (copy `templates/campaign_chunks.yaml`): a pilot
   with human verdicts, then remainder slices under the two-model agreement gate. If a
   labeled public dataset exists, add a calibration chunk against it first. Regenerate
   `costs.md` with `labeling costs`.
6. **Run it** like any other chunk: `labeling chunk <id> --dry-run`, then with your
   initials, then `labeling gate <id> --approve`.

## Step 5: this repository, dataset build and checks

- `config.py`: `DOC_TYPES` and the class colour.
- `identity.py`: the source corpus for the class and how `document_id` is derived.
- `gt_presence.py` and `gt_quality.py`: the class's applicable fields and allowed empties.
- `eval_contract.py`: the class-to-specialist map.
- `integrity.py`, `visualizations*.py`: class masks and per-class panels.
- The v10 build writes one typed config per class (`gt_<class>`); add the new one to the
  card's config list, field table and composition table.
- `tests/conftest.py`: the class set the tests expect (`FIVE_CLASSES`).
- `bundles.py` and `fixtures.py`: optional new bundle families (for example a lease plus
  its amendment) and routing fixtures (lease versus contract ambiguity).

## Step 6: llm-mailroom

- `src/config/taxonomy.yaml`: the class entry, its specialist, and its confidence bands.
- `src/schemas/documents.py`: a Pydantic extraction model mirroring the Dojo fields, added
  to the class-to-model map.
- `src/agents/<name>_specialist.py` and a new frozen prompt version under `src/llm/`.
- `src/langchain_agents/` (`classifier.py`, `sorter_agent.py`, `specialist_agents.py`,
  `prompts.py`, `doc_inventories.py`) and `src/graph/build_graph.py` for routing.
- `src/pipeline/hf_corpora.py`: corpus size, class counts and config names.
- `src/observability/`: the specialist suite and ground-truth loaders.
- `src/scripts/run_hf_pilot.py`: the class's ground-truth keys.

## Step 7: local-mailroom-sandbox

- Refresh `vendor/` (never hand-edit it): `python scripts/sync_vendor.py`, bump
  `_VENDOR_PINS` and `requirements/dojo-release.txt` together.
- `src/mailroom_sandbox/job/spec.py`: the per-revision corpus size and class counts.
- `src/mailroom_sandbox/corpus.py`: `GT_FIELD_LISTS` (or the Dojo types once it reads them).
- `gt_labeler.py`, `prompt_registry.py`, `eval/`: specialist prompts and scoring scope.
- New run configs pinned to the new dataset revision; old configs stay pinned.

## Step 8: publish

Run every conformance gate, publish a new dataset tag (never move an old one), update the
pins in this repository's `config.py`, llm-mailroom and the sandbox, and note the new class
in the CHANGELOG and the dataset card's versioning table.
