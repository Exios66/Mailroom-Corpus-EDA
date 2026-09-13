<div align="center">

# 🧪 Corpus EDA Tests

**Test suites for the mailroom-corpus-eda package.**

</div>

---

## Running Tests

Standalone checkout:

```bash
cd Mailroom-Corpus-EDA
.venv/bin/python -m pytest tests/
```

Inside the Digital-Mailroom monorepo:

```bash
cd Digital-Mailroom
uv run pytest packages/mailroom-corpus-eda/tests/
```

## Structure

| Path | Contents |
|:---|:---|
| `conftest.py` | Path bootstrap + committed synthetic fixture rows + local-HF-snapshot loader (skips the full-corpus tests when `data/parquet` is absent) |
| [`fixtures/`](fixtures/) | Committed synthetic sample rows (`sample_rows.jsonl`) |

## Suites

| Test file | Covers |
|:---|:---|
| `test_identity.py` | P0 document identity + content hashes |
| `test_eval_contract.py` | Evaluation-contract derivations + closed vocabularies |
| `test_matter.py` | P2 grouping derivations (§14A) |
| `test_bundles.py` | §14 synthetic bundle families |
| `test_fixtures.py` | §68–§72A fixture content |
| `test_contract.py` | v9 ground-truth schema contract (36 top-level cols + 27-key `gt_fields`) against the local snapshot |
| `test_v8_build.py` | Frozen v8 builder logic (HUB-028) — network-free |

The full-corpus suite (`test_contract.py`, and the `snapshot_rows`/`snapshot_metadata`
fixtures) runs against the local HF snapshot in `data/parquet/` (gitignored —
fetch via `python run_all.py --phases P0`). Without it those tests skip.

## Related Files

- `src/` — Source code
- `scripts/` — Utility scripts
- `reports/` — Generated reports