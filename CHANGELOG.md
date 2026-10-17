# Changelog

Notable changes to `Exios66/Mailroom-Corpus-EDA` (mirrored monorepo-side as
`packages/mailroom-corpus-eda`). Behavior-changing releases carry an entry
here in the same commit; docs-only changes declare themselves as such.
Timestamps are UTC. Earlier revisions (v9.1, v9) are documented in
`AGENTS.md` §HF facts and the per-revision plan docs under `docs/plans/`.

## v9.3 — corporate presence-layer labels (STAGING, 2026-10-17)

STAGING release on branch `feat/v9.3-staging-corporate-presence` (draft plan:
`docs/plans/v9.3-corporate-presence-revision.md`; module:
`src/mailroom_eda/v9_3_revision.py`). **Label-only** over the published
`corporate-labels-v9.2.1` content — the same 3,302 rows (train 2,979 /
test 323, corporate 450), every identity/content column and every populated
label value byte-identical. Nothing here pushes tags or the HF Hub; the
staged payload waits for the promotion run. Formal gates:
`scripts/audit/verify_v9_3_conformance.py` (a–f all PASS; pytest 250 passed,
15 skipped).

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
