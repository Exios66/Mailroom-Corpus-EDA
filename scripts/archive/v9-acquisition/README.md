# 🔍 v9-acquisition — one-time v9 sourcing (ARCHIVED)

**Status: PROVENANCE-ONLY.** These scripts sourced the v9 expansion rows
(issue #3; subissues #9/#12) that are already baked into the published
[`Lucius-Morningstar/mailroom-dataset`](https://huggingface.co/datasets/Lucius-Morningstar/mailroom-dataset).
They are the permanent record of the sourcing mechanics; they are not part
of the live build.

| Script | Purpose (one-time) |
|:---|:---|
| `edgar_ftsearch.py` | SEC EDGAR full-text-search enumerator (via the Jina Reader proxy) — exhibit-LEVEL hits for the governance/contract targets |
| `edgar_pull.py` | SEC EDGAR exhibit crawler — deterministic raw pool under `data/v9/edgar/` for `--kind corporate` and `--kind contract` |
| `draw_contract_v9.py` | Allocate the 91 EX-10 contract seats across subclasses (largest-remainder) → `data/v9/draws/contract.jsonl` |

## Notes

- Raw pools and draw sidecars live under `data/v9/` (gitignored scratch);
  the committed artifacts (`docs/dataset-cards/`, the published Hub tree)
  are canonical.
- The EDGAR crawls are external-network one-time runs; re-running them today
  would hit a different SEC/Jina state and is NOT expected to reproduce the
  published bytes.
- `edgar_ftsearch.py` uses `mailroom_eda.config.DATA_DIR` for its pool path;
  both crawlers require the Jina Reader proxy (SEC blocks direct IPs).

Run with:

```bash
.venv/bin/python scripts/archive/v9-acquisition/edgar_ftsearch.py --help
.venv/bin/python scripts/archive/v9-acquisition/edgar_pull.py --help
.venv/bin/python scripts/archive/v9-acquisition/draw_contract_v9.py --help
```