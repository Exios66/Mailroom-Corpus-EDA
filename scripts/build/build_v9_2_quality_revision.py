#!/usr/bin/env python3
"""CLI: build (and optionally publish) the v9.2 relations & provenance revision.

Code-only revision over the live v9.1 data commit (plan:
``docs/plans/v9.2-relations-provenance-revision.md``). No new document
sourcing, no Modal/GPU/LLM credentials. Downloads the LIVE (unpinned)
Lucius-Morningstar/mailroom-dataset tip, recomputes the seven
``annotation_*`` columns from the nested ``gt_fields`` provenance (C1),
populates ``related_document_ids`` + syncs the nested matter mirrors (C3),
drops the build-internal ``_published`` column (C2), and re-stages
``parquet/default`` (4 cols) + ``parquet/ground_truth`` (35 cols) + the
hardened sidecar + card + manifest. Stage-only by default; ``--publish``
uploads the verified tree.

Workstream S (bundles/streams/fixtures overlay) is deferred to v9.3 (D8) and
is not available here.

Usage:
    .venv/bin/python scripts/build/build_v9_2_quality_revision.py               # stage under data/v9_2/stage
    .venv/bin/python scripts/build/build_v9_2_quality_revision.py --publish     # stage + publish
"""
from __future__ import annotations

from pathlib import Path

import sys
_b = Path(__file__).resolve()
while not (_b / "_bootstrap.py").is_file():
    _b = _b.parent
sys.path.insert(0, str(_b))
from _bootstrap import ROOT  # noqa: E402

from mailroom_eda.v9_2_revision import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
