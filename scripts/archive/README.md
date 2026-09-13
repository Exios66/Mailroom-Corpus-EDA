# 🗃️ Archived Scripts

**Status: FROZEN / PROVENANCE-ONLY.** Everything under this tree is preserved
for lineage and reproducibility — none of it is part of the live workflow.

| Bucket | Contents | Status |
|:---|:---|:---|
| [`v8/`](v8/) | Tooling for the frozen `Lucius-Morningstar/mailroom-corpus` (v8 baseline, 2,000 rows, pinned at `eafe1ab4` — never destroyed) | Frozen; the live successor builder is `scripts/build/build_v9.py` |
| [`v9-acquisition/`](v9-acquisition/) | One-time sourcing for the v9 expansion (issue #3 / #9 / #12): SEC EDGAR crawlers + the contract draw | One-time; the draws they produced are already baked into the published `mailroom-dataset` |

## Running archived scripts

Every script here uses the same bootstrap as the live scripts, so it runs
from the repo root regardless of bucket depth:

```bash
.venv/bin/python scripts/archive/v8/reconcile_gt_v8.py --help
.venv/bin/python scripts/archive/v9-acquisition/edgar_pull.py --help
```

## Why archive instead of delete

- The frozen-v8 tooling documents exactly how the `mailroom-corpus` baseline
  was built, hardened, reconciled, and published (HUB-022 / HUB-028 /
  HUB-032) — that release must stay reproducible without re-derivation.
- The v9-acquisition scripts document how the +91 contract / +411 corporate
  / +150 insurance rows entered the corpus. The raw pools and draws live
  under `data/v9/` (gitignored), so the scripts are the only permanent
  record of the sourcing mechanics.

Archive policy: treat these as read-only reference. Fix path/bootstrap
breakage if a rebuild is ever attempted, but do not "improve" behavior — the
published bytes are canonical and must never drift.