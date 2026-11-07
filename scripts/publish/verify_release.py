#!/usr/bin/env python3
"""CLI: verify a published mailroom-dataset release on the Hub, after publication.

Downloads every file the Hub serves at the release tag and checks it against
the release invariants and the frozen spec in
``src/mailroom_eda/release_specs/<tag>.json`` (see ``mailroom_eda.release_verify``).

Usage:
    python scripts/publish/verify_release.py                 # the pinned tag (config.REPO_TAG)
    python scripts/publish/verify_release.py --tag v9.3
    python scripts/publish/verify_release.py --all           # every frozen release
    python scripts/publish/verify_release.py --tag v9.4 --freeze   # after publishing v9.4

``--freeze`` refuses to write a spec when the tag already has one (unless
``--refreeze``), when any invariant or the Hub card check fails (only a
``manifest_coverage`` finding, a served file the manifest does not hash, of an
immutable release may be recorded, with ``--accept-findings REASON``), when
routing differs from today's registry, or when the release adds a blind
metadata key (unless ``--allow-new-blind-keys``).
It prints what changed since the previous release so the diff can be
reviewed before the spec is committed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_b = Path(__file__).resolve()
while not (_b / "_bootstrap.py").is_file():
    _b = _b.parent
sys.path.insert(0, str(_b))
from _bootstrap import ROOT
from huggingface_hub import HfApi

from mailroom_eda import release_verify as rv
from mailroom_eda.config import REPO_ID, REPO_TAG


def freeze(api: HfApi, args: argparse.Namespace) -> int:
    spec, lines = rv.freeze_release(api, args.tag, accept_reason=args.accept_findings,
                                    refreeze=args.refreeze, allow_new_blind_keys=args.allow_new_blind_keys)
    for line in lines:
        print(line)
    if spec is None:
        return 1
    path = rv.write_spec(spec)
    print(f"froze {args.tag} @ {spec['revision'][:8]} -> {path.relative_to(ROOT)}; "
          "review the diff, then commit it")
    return 0


def verify(api: HfApi, tag: str) -> int:
    try:
        spec = rv.load_spec(tag)
        findings = rv.verify_release(api, tag)
    except Exception as exc:  # noqa: BLE001 - one broken release must not stop --all
        print(f"FAIL {tag}: {exc!r}")
        return 1
    if findings:
        print(f"FAIL {tag} @ {spec['revision'][:8]}: {len(findings)} findings")
        for f in findings:
            print(f"  {f}")
        return 1
    print(f"PASS {tag} @ {spec['revision'][:8]}: invariants, Hub card validation, "
          f"{len(set(spec) - set(rv.SPEC_META))} spec facts, {len(spec.get('files', {}))} file sha256s")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", default=REPO_TAG, help=f"release tag (default: {REPO_TAG})")
    ap.add_argument("--all", action="store_true", help="verify every frozen release")
    ap.add_argument("--freeze", action="store_true",
                    help="write the spec for --tag from the Hub (after publishing it)")
    ap.add_argument("--accept-findings", metavar="REASON", default=None,
                    help="with --freeze: record the current invariant failures as reviewed "
                         "exceptions of an already-published release (only a served file the manifest does not hash)")
    ap.add_argument("--refreeze", action="store_true",
                    help="with --freeze: replace an existing spec (prints the diff against it)")
    ap.add_argument("--allow-new-blind-keys", action="store_true",
                    help="with --freeze: allow blind metadata keys the previous release did not have")
    args = ap.parse_args()
    if not args.freeze and (args.accept_findings or args.refreeze or args.allow_new_blind_keys):
        ap.error("--accept-findings, --refreeze and --allow-new-blind-keys need --freeze")
    api = HfApi()
    print(f"repo {REPO_ID}")
    if args.freeze:
        return freeze(api, args)
    tags = rv.frozen_tags() if args.all else [args.tag]
    return max([verify(api, t) for t in tags])


if __name__ == "__main__":
    raise SystemExit(main())
