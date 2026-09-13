# 🧊 v8 — frozen `mailroom-corpus` tooling (ARCHIVED)

**Status: FROZEN.** These scripts target the frozen v8 baseline
[`Lucius-Morningstar/mailroom-corpus`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-corpus)
(2,000 rows, pinned at `eafe1ab4` — never destroyed). They are preserved for
lineage/repro only. The live successor corpus is
`Lucius-Morningstar/mailroom-dataset` (v9), published by
`scripts/build/build_v9.py`.

> ⚠️ These scripts are explicitly pinned to `V8_REPO_ID`
> (`Lucius-Morningstar/mailroom-corpus`). `mailroom_eda.config.REPO_ID` now
> points at the v9 repo and must NEVER be used by this tree.

| Script | Purpose (historical) | Notes |
|:---|:---|:---|
| `build_v8.py` | Build + stage/publish the v8 release (insurance LOB expansion, HUB-028) | Reads `data/v8_*` fixtures |
| `publish_hardened.py` | §84 hardened release on the v8 base (HUB-022): v0.2-hardened / v0.3-matter-aware / v0.4-recovery-suite | Thin CLI over `mailroom_eda.hardened` |
| `reconcile_gt_v8.py` | HUB-032: rebuild `ground_truth` onto HUB-028's GT commit after the interleaved-publish collision | Was broken (`V8_V8_REPO_ID`) — fixed on archive |
| `publish_docclass.py` | Pre-v9 docclass publish (v7 lineage) to `mailroom-corpus` | Requires legacy `data/v7_rows.jsonl` |
| `export_docclass.py` | Export a v6 docclass JSONL to cast-safe JSONL + parquet staging | Targets the deleted `docclass-merged` lineage |

## Reproducibility notes

- The §84 release chain (identity → eval_contract → matter builders, bundles
  / streams / fixtures staging + verification) is implemented once in
  `src/mailroom_eda/hardened.py` and shared with the v9 builder — the v8 CLI
  is a thin driver.
- Column registries (`IDENTITY_FIELDS`, `CONTRACT_FIELDS`, `MATTER_SCALARS`,
  `MATTER_LISTS`, card-section helpers) live in
  `src/mailroom_eda/release_sections.py` — never re-declared.
- The live card section owned by the §84 release is `release_sections.py`
  `CARD_HEADING` (reconciled title, HUB-032); `publish_hardened.py` here
  carries the original pre-reconciliation heading for the archived path.

Run with:

```bash
.venv/bin/python scripts/archive/v8/build_v8.py --help
.venv/bin/python scripts/archive/v8/publish_hardened.py --help
.venv/bin/python scripts/archive/v8/reconcile_gt_v8.py --help
.venv/bin/python scripts/archive/v8/publish_docclass.py --help
.venv/bin/python scripts/archive/v8/export_docclass.py --help
```