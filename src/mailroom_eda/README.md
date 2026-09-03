<div align="center">

# 🐍 Mailroom EDA Package

**Main Python package for the mailroom-corpus-eda package.**

</div>

---

## Modules

| Module | Purpose |
|:---|:---|
| `identity.py` | Canonical class definitions |
| `hf_interface.py` | HuggingFace Hub interface |
| `dataset_export.py` | Dataset export utilities |
| `docclass_uploader.py` | Docclass upload helpers |
| `intent_backfill.py` | Intent backfill utilities |

## Usage

```python
from mailroom_eda import run_eda
from mailroom_eda.identity import get_canonical_classes
from mailroom_eda.hf_interface import upload_dataset
```

## Related Files

- `../tests/` — Test suites
- `../scripts/` — Utility scripts
- `../reports/` — Generated reports
