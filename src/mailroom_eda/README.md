<div align="center">

# 🐍 Mailroom EDA Package

**Main Python package for the mailroom-corpus-eda package.**

</div>

---

## Modules

| Module | Purpose |
|:---|:---|
| `config.py` | Paths, doc types, colors, token budgets, matplotlib setup, split rule |
| `download.py` | Corpus acquisition (HF snapshot) + manifest parsing |
| `integrity.py` | P1 structural integrity & provenance audit |
| `composition.py` | P2 strata / imbalance / provenance / metadata coverage |
| `identity.py` | P0 document identity (document_id, content hashes, source provenance) |
| `eval_contract.py` | P1 evaluation-contract derivations + closed vocabularies |
| `matter.py` | P2 grouping derivations (§14A header threads, reconstruction, never-mix guard) |
| `bundles.py` | P2 §14 synthetic bundle-family generator |
| `fixtures.py` | §68–§72A fixture content |
| `token_budget.py` | Token estimation & budget coverage |
| `release_sections.py` | §84 hardened-release column registries + card helpers |
| `hardened.py` | §84 release-chain builders (shared by the v9 builder + archived v8 CLIs) |
| `hf_interface.py` | Hub client (upload, sha256 verify, repo mgmt) |
| `dataset_export.py` | Cast-safe JSONL (KANBAN-088), parquet staging, manifests |
| `docclass_uploader.py` | Docclass publish, surgical card render, blind-label strip, leak guard |
| `intent_backfill.py` | Correspondence intent hydration (issue #5) |
| `visualizations.py` | P3 static PNG figures (30) + EDA tables |
| `visualizations_interactive.py` | P4 Plotly HTML figures (18) |
| `v8_build.py` | Frozen v8 builder logic (HUB-028; used by the archived v8 CLI + tests) |
| `v9_build.py` | v9 `mailroom-dataset` builder (imports the hardened chain) |

## Usage

```python
import mailroom_eda
from mailroom_eda.config import REPO_ID
from mailroom_eda.download import download_corpus, load_ground_truth
from mailroom_eda.hf_interface import get_hf_api, upload_folder
```

Entrypoints: the P0–P6 pipeline runs via `run_all.py` at the repo root; the
individual CLIs live under `scripts/` (see `scripts/README.md`).

## Related Files

- `../tests/` — Test suites
- `../scripts/` — Utility scripts
- `../reports/` — Generated reports