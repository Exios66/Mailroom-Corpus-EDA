#!/usr/bin/env python3
"""Re-derive the MAUD v1 contract_148/149 upstream defect from the Zenodo release.

Downloads ``maud_v1.zip`` (Zenodo record 7500064) into memory and checks that:

1. the shared text's sha256 matches the ``UPSTREAM_DEFECT_SURVIVORS`` key;
2. ``contract_148.txt`` and ``contract_149.txt`` are the only byte-identical
   pair among MAUD's contracts;
3. MAUD's own evidence excerpts (train/dev/test CSVs) align with the shared
   text for contract_148 and not for contract_149, with the pinned counts.

Network-only and outside the build, so builds stay offline and deterministic.
``--zip`` reads a local copy instead of downloading. ``--check`` exits 1 on any
drift from the pinned override.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import sys
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

_b = Path(__file__).resolve()
while not (_b / "_bootstrap.py").is_file():
    _b = _b.parent
sys.path.insert(0, str(_b))
from _bootstrap import ROOT  # noqa: E402,F401

from mailroom_eda.v9_4_revision import UPSTREAM_DEFECT_SURVIVORS, maud_evidence_alignment  # noqa: E402

ZENODO_URL = "https://zenodo.org/records/7500064/files/maud_v1.zip?download=1"
CONTRACTS_PREFIX = "data/contracts/"
SPLITS = ("train", "dev", "test")


def load_zip(path: Path | None) -> zipfile.ZipFile:
    """Open ``path``, or download the Zenodo release into memory when it is None."""
    if path is not None:
        return zipfile.ZipFile(path)
    with urllib.request.urlopen(ZENODO_URL, timeout=300) as resp:  # noqa: S310 - fixed https URL
        return zipfile.ZipFile(io.BytesIO(resp.read()))


def identical_pairs(z: zipfile.ZipFile) -> tuple[int, list[list[str]]]:
    """Return (contract count, groups of byte-identical contract files)."""
    by_sha: dict[str, list[str]] = defaultdict(list)
    for name in sorted(z.namelist()):
        if name.startswith(CONTRACTS_PREFIX) and name.endswith(".txt"):
            by_sha[hashlib.sha256(z.read(name)).hexdigest()].append(name)
    return sum(map(len, by_sha.values())), [v for v in by_sha.values() if len(v) > 1]


def evidence_by_contract(z: zipfile.ZipFile) -> dict[str, list[str]]:
    """Map MAUD ``contract_name`` -> every evidence excerpt across all splits."""
    out: dict[str, list[str]] = defaultdict(list)
    for split in SPLITS:
        with z.open(f"data/MAUD_{split}.csv") as fh:
            for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")):
                out[row["contract_name"]].append(row["text"])
    return out


def main() -> int:
    """Print the re-derived evidence; with ``--check`` return 1 on drift."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zip", type=Path, default=None, help="local maud_v1.zip instead of downloading")
    ap.add_argument("--check", action="store_true", help="exit 1 if anything differs from the pinned override")
    args = ap.parse_args()

    z = load_zip(args.zip)
    problems: list[str] = []
    total, pairs = identical_pairs(z)
    print(f"contracts            {total}")
    print(f"identical groups     {pairs}")
    expected_pair = [f"{CONTRACTS_PREFIX}contract_148.txt", f"{CONTRACTS_PREFIX}contract_149.txt"]
    if pairs != [expected_pair]:
        problems.append(f"expected exactly one identical pair {expected_pair}, got {pairs}")

    text_bytes = z.read(expected_pair[0])
    sha = hashlib.sha256(text_bytes).hexdigest()
    print(f"shared sha256        {sha}")
    override = UPSTREAM_DEFECT_SURVIVORS.get(sha)
    if override is None:
        problems.append(f"sha {sha} is not a key of UPSTREAM_DEFECT_SURVIVORS")
        pinned: dict[str, list[int]] = {}
    else:
        pinned = override["evidence"]

    text = text_bytes.decode("utf-8")
    evidence = evidence_by_contract(z)
    for contract in ("contract_148", "contract_149"):
        found, n = maud_evidence_alignment(text, evidence.get(contract, []))
        print(f"{contract:20s} {found}/{n} excerpts found in the shared text")
        if pinned and [found, n] != list(pinned.get(contract, [])):
            problems.append(f"{contract}: {found}/{n} != pinned {pinned.get(contract)}")

    for p in problems:
        print(f"DRIFT: {p}", file=sys.stderr)
    print("result               " + ("DRIFT" if problems else "MATCHES PINNED OVERRIDE"))
    return 1 if (args.check and problems) else 0


if __name__ == "__main__":
    raise SystemExit(main())
