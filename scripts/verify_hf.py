#!/usr/bin/env python3
"""CLI: verify a local docclass export against the Hub (byte-identity check).

Usage:
    python scripts/verify_hf.py --jsonl data/hf_export/mailroom-cuad-contracts-full.jsonl --repo Lucius-Morningstar/mailroom-cuad-contracts-full
    python scripts/verify_hf.py --repo Lucius-Morningstar/docclass-merged            # list files
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mailroom_eda.hf_interface import (  # noqa: E402
    get_hf_api,
    list_repo_files,
    sha256_file,
    verify_hub_sha256,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True,
                        help="HF dataset repo id (e.g. Lucius-Morningstar/docclass-merged)")
    parser.add_argument("--jsonl", type=Path, default=None,
                        help="local jsonl to byte-verify against the Hub copy")
    parser.add_argument("--in-repo", default=None,
                        help="filename in the repo to compare (default: basename of --jsonl)")
    args = parser.parse_args()

    api = get_hf_api()
    me = api.whoami()
    print(f"HF account: {me['name']}")

    if args.jsonl is None:
        files = list_repo_files(api, args.repo)
        print(f"{len(files)} files in {args.repo}:")
        for f in files:
            print(f"  {f}")
        return 0

    local_sha = sha256_file(args.jsonl)
    in_repo = args.in_repo or args.jsonl.name
    print(f"local  sha256 {local_sha[:12]} ({args.jsonl})")
    result = verify_hub_sha256(api, args.repo, in_repo, local_sha)
    print(json.dumps(result, indent=2))
    return 0 if result["verified"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())