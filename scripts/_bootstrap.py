"""Shared bootstrap for the scripts/ CLIs.

Every script in this tree (including the nested purpose/ buckets and the
archive/ sub-buckets) starts with the same preamble that locates THIS file by
walking up from ``__file__``, puts ``scripts/`` on ``sys.path``, and imports
this module — which resolves the repo root (the first ancestor carrying
``.git``) and adds ``<root>/src`` to ``sys.path`` so ``import mailroom_eda``
works regardless of how deep the script sits.

    import importlib.util, pathlib, sys
    _b = pathlib.Path(__file__).resolve()
    while not (_b / "_bootstrap.py").is_file():
        _b = _b.parent
    sys.path.insert(0, str(_b))
    from _bootstrap import ROOT  # noqa: E402

Never derive repo paths from ``__file__.parents[n]`` in a script — the bucket
depth varies (``scripts/`` vs ``scripts/build/`` vs ``scripts/archive/v8/``).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))