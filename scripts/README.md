<div align="center">

# ⚙️ Corpus EDA Scripts

**Utility scripts for the mailroom-corpus-eda package.**

</div>

---

## Scripts

| Script | Purpose |
|:---|:---|
| `run_all.py` (repo root) | Full EDA pipeline P0–P6 |
| `generate_index.py` | Regenerate `reports/index.html` dashboard (data-driven) |
| `generate_summary_md.py` | Regenerate `reports/SUMMARY_REPORT.md` (data-driven) |
| `baseline_audit.py` | §4 / §85 P0 baseline audit |
| `coverage_matrix.py` | §40–§41 coverage matrix |
| `build_v9.py` | Build (and optionally publish) the v9 mailroom-dataset |

## Usage

```bash
cd Mailroom-Corpus-EDA
.venv/bin/python run_all.py                       # full pipeline P0-P6
.venv/bin/python run_all.py --phases P3 P4        # figures only
.venv/bin/python scripts/generate_summary_md.py   # SUMMARY_REPORT.md
.venv/bin/python scripts/generate_index.py        # reports/index.html
```

> Note: `generate_figures.py` / `gen_interactive_charts.py` (documented in
> earlier revisions) no longer exist; P3/P4 entrypoints are the `__main__`
> blocks of `src/mailroom_eda/visualizations.py` and
> `src/mailroom_eda/visualizations_interactive.py`, invoked via `run_all.py`.

## Related Files

- `reports/` — Generated reports and figures
- `src/` — Source code
- `tests/` — Test suites