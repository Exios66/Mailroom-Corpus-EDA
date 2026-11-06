"""Copy every labeling prompt into Part E of the v10 plan, verbatim.

    python scripts/labeling/sync_plan_prompts.py          # rewrite the plan's prompt blocks
    python scripts/labeling/sync_plan_prompts.py --check  # exit 1 when a block is stale

Each prompt has a ``### `<name>.md` `` heading in the plan followed (after optional prose)
by a ```` ```text ```` block; only the block's contents are replaced.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "plans" / "v10-structure-and-label-completion.md"
PROMPTS = ROOT / "src" / "mailroom_eda" / "labeling" / "prompts"


def synced(plan: str) -> str:
    """The plan text with every prompt block replaced by the prompt file."""
    for path in sorted(PROMPTS.glob("*.md")):
        head = f"### `{path.name}`"
        if head not in plan:
            raise SystemExit(f"plan has no section {head}")
        pre, post = plan.split(head, 1)
        body = path.read_text(encoding="utf-8").rstrip("\n")
        post, n = re.subn(r"```text\n.*?\n```", lambda _: f"```text\n{body}\n```", post, count=1, flags=re.S)
        if not n:
            raise SystemExit(f"{head} has no ```text block")
        plan = pre + head + post
    return plan


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    old = PLAN.read_text(encoding="utf-8")
    new = synced(old)
    if args.check:
        print("plan prompts are current" if new == old else "plan prompts are stale")
        return 0 if new == old else 1
    PLAN.write_text(new, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
