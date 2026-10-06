# Changelog

Notable changes to `Exios66/Mailroom-Corpus-EDA` (mirrored monorepo-side as
`packages/mailroom-corpus-eda`). Behavior-changing releases carry an entry
here in the same commit; docs-only changes declare themselves as such.
Timestamps are UTC. Earlier revisions (v9.1, v9) are documented in
`AGENTS.md` §HF facts and the per-revision plan docs under `docs/plans/`.

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
