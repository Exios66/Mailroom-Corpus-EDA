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

``--freeze`` runs the invariants first and refuses to write a spec for a
release that fails them; it prints what changed since the previous release
so the diff can be reviewed before the spec is committed.
"""
from __future__ import annotations

import argparse
import shutil
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


def freeze(api: HfApi, tag: str, accept_reason: str | None) -> int:
    commit = rv.resolve_tag(api, tag)
    snap = rv.fetch(commit)
    try:
        card = rv.check_card_on_hub(snap)
        findings = rv.check_invariants(snap)
        if card or (findings and not accept_reason):
            print(f"{tag} @ {commit[:8]}: {len(card + findings)} invariant failures; spec NOT written")
            for f in card + findings:
                print(f"  {f}")
            if findings and not card:
                print("a published release that cannot be fixed in place may record them with "
                      "--accept-findings REASON, after review")
            return 1
        spec = rv.make_spec(tag, commit, rv.collect_facts(snap),
                            accepted=findings, reason=accept_reason or "")
    finally:
        shutil.rmtree(snap, ignore_errors=True)
    for f in findings:
        print(f"  accepted: {f}")
    prev_tag = rv.previous_tag(tag)
    if prev_tag:
        prev = rv.load_spec(prev_tag)
        print(f"changes since {prev['tag']}:")
        for line in rv.diff_specs(prev, spec):
            print(f"  {line}")
    path = rv.write_spec(spec)
    print(f"froze {tag} @ {commit[:8]} -> {path.relative_to(ROOT)}; review the diff, then commit it")
    return 0


def verify(api: HfApi, tag: str) -> int:
    findings = rv.verify_release(api, tag)
    spec = rv.load_spec(tag)
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
                         "exceptions of an already-published release")
    args = ap.parse_args()
    api = HfApi()
    print(f"repo {REPO_ID}")
    if args.freeze:
        return freeze(api, args.tag, args.accept_findings)
    tags = rv.frozen_tags() if args.all else [args.tag]
    return max([verify(api, t) for t in tags])


if __name__ == "__main__":
    raise SystemExit(main())
