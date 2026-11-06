# Example: a `commercial_lease` class

A complete, validated proposal for a sixth document class. Nothing here is live: the class is
not in `labeling/fields.yaml`, the dataset or any consumer. It shows exactly what each file
looks like, and `check-class` proves it is consistent.

```bash
PYTHONPATH=src python -m mailroom_eda.labeling check-class src/mailroom_eda/labeling/new_doc_types/examples/commercial_lease
```

| file | becomes |
|---|---|
| `fields.yaml` | three blocks merged into `labeling/fields.yaml` (vocabularies, the class, the task) |
| `prompts/lease_entities.md` | `labeling/prompts/lease_entities.md` |
| `campaign_chunks.yaml` | chunks appended to the campaign that labels the new rows |

## Why leases

- **Mailroom realism.** Leases, amendments, renewal notices, estoppel certificates and
  subordination agreements (SNDAs) are routine legal-inbox intake for any company that rents
  space. The current five classes have none.
- **No overlap.** No CUAD family covers leases, and a scan of the v9.4 stage for lease
  vocabulary (landlord, tenant, premises, sublease) found a single contract, an Agency
  Agreement, that reads like one.
- **A distinct specialist job.** The fields (landlord, tenant, premises, rent, term,
  deposit, options) do not exist on any current schema, so a contracts specialist would miss
  them.

## Where the documents come from

1. **SEC EDGAR EX-10 exhibits (publishable).** Companies file material leases as EX-10
   exhibits, the same path the corpus already uses for EX-10 contracts. Select exhibits whose
   description or first-page title contains `Lease`, `Sublease`, `Lease Amendment`,
   `Amendment to Lease`, `Ground Lease`, `Estoppel` or `Subordination, Non-Disturbance`. Keep
   the accession number, CIK, filing date and exhibit URL in `metadata`, and use the exhibit
   description to propose the subclass (a human confirms it in the pilot). SEC filings are
   public records, so they can be published like the existing EX-10 rows. Aim for about 300
   exhibits spread over the seven subclasses, with at least 15 per subclass.
2. **ALeaseBERT benchmark (calibration only).** Leivaditi, Rossi and Kanoulas,
   *A Benchmark for Lease Contract Review* (2020) annotated 179 English commercial leases for
   entities (parties, dates, property, rent, term and more) and red-flag clauses. It is
   released under CC BY-NC-SA 4.0: non-commercial and share-alike. Use it to measure label
   accuracy, never as published rows, unless the whole release takes the same licence.
   Confirm where the data is currently hosted before planning on it; the paper links the
   original repository.

Do not scrape commercial lease-abstraction services: their documents are not licensed for
redistribution.

## Subclasses

`office`, `retail`, `industrial_warehouse`, `ground_lease`, `sublease`, `lease_amendment`,
`other` (estoppel certificates, SNDAs, guaranties, terminations). The first four describe the
property; `sublease` and `lease_amendment` describe the instrument, and win when both apply.

## Notes for the specialist schema in llm-dojo-scoring

- `landlord`, `tenant` and `governing_law` are typed `name`, like the contract fields, so the
  Dojo's containment scoring treats them the same way.
- `base_rent` is `money` as printed with its period. The scorer compares amounts, so
  "$25,000.00 per month" and "$300,000 per year" are different values on purpose.
- `lease_type` is `evidence_only`: many leases never name their type, and it should be
  labeled only when the text states it or the expense clauses make it unambiguous.
- `intent` is `never_absent`: every lease instrument does one of the listed things.
- Bundles: a lease with its later amendment is a natural new bundle family in `bundles.py`.
- Routing fixtures: a lease against a services contract that mentions premises, and a lease
  amendment against a contract amendment, are the useful ambiguity cases for `fixtures.py`.
