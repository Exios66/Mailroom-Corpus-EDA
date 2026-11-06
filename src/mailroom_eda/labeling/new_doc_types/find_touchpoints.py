"""List every line that names the current document classes, per repository.

    python src/mailroom_eda/labeling/new_doc_types/find_touchpoints.py \
        --repo . --repo ../llm-mailroom --repo ../local-mailroom-sandbox \
        --repo ../local-mailroom-sandbox/vendor/llm-dojo-scoring [--markdown out.md]

A file where three or more of the five class keys appear together is reported as a
**class registry**: a list, tuple or map that must gain the new class. Other mentions are
reported as **class-specific code** to read (a branch that may need a new case). Vendored
copies, virtualenvs, caches, data and report output are skipped. Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

CLASSES = ("contract", "merger_agreement", "corporate_record", "correspondence", "insurance_claim")
SPECIALISTS = ("contracts_specialist", "merger_agreement_specialist", "corporate_records_specialist",
               "correspondence_specialist", "insurance_claims_specialist")
SUFFIXES = {".py", ".yaml", ".yml", ".json", ".toml", ".md", ".txt"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "data", "reports", "dist", "build",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages"}
#: Whole-word class keys; "contract" alone is too common, so only quoted or keyed uses count.
PATTERNS = {c: re.compile(rf"""["'`]{c}["'`]|\b{c}\s*:|\bexpected\s*==\s*["']{c}""") for c in CLASSES}
WINDOW = 12  # lines: class keys this close together form one registry


def scan_file(path: Path) -> tuple[list[tuple[int, str]], bool]:
    """Matching lines and whether the file holds a class registry."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return [], False
    hits: list[tuple[int, str]] = []
    per_line: list[set[str]] = []
    for i, line in enumerate(lines, 1):
        found = {c for c, rx in PATTERNS.items() if rx.search(line)}
        found |= {CLASSES[SPECIALISTS.index(s)] for s in SPECIALISTS if s in line}
        per_line.append(found)
        if found:
            hits.append((i, line.strip()[:160]))
    registry = any(len(set().union(*per_line[i:i + WINDOW])) >= 3 for i in range(len(per_line)))
    return hits, registry


def scan_repo(root: Path) -> dict[str, list]:
    """Registries and other mentions in one checkout."""
    out: dict[str, list] = defaultdict(list)
    vendor = root / "vendor"
    for path in sorted(root.rglob("*")):
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if vendor in path.parents and root != vendor:
            continue
        hits, registry = scan_file(path)
        if hits:
            out["registries" if registry else "mentions"].append((str(rel), hits))
    return out


def render(results: dict[Path, dict[str, list]], show_lines: int) -> str:
    """Markdown report."""
    lines = ["# Document-class touchpoints", ""]
    for root, res in results.items():
        lines += [f"## `{root}`", "",
                  f"{len(res['registries'])} files hold class registries; "
                  f"{len(res['mentions'])} more mention a class.", "", "### Class registries (must gain the new class)", ""]
        for rel, hits in res["registries"]:
            lines.append(f"- `{rel}` ({len(hits)} lines)")
            lines += [f"  - L{n}: `{t}`" for n, t in hits[:show_lines]]
        lines += ["", "### Class-specific code (read; may need a new branch)", ""]
        lines += [f"- `{rel}` ({len(hits)} lines, first L{hits[0][0]})" for rel, hits in res["mentions"]]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", action="append", type=Path, required=True, help="a checkout to scan (repeat)")
    ap.add_argument("--markdown", type=Path, help="write the report here instead of stdout")
    ap.add_argument("--lines", type=int, default=3, help="lines to show per registry file")
    args = ap.parse_args(argv)
    results = {}
    for root in args.repo:
        if not root.is_dir():
            print(f"skipping {root}: not a directory", file=sys.stderr)
            continue
        results[root] = scan_repo(root.resolve())
    text = render(results, args.lines)
    if args.markdown:
        args.markdown.write_text(text, encoding="utf-8")
        print(args.markdown)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
