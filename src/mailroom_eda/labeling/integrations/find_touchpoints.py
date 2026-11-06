"""List every line that names the current document classes, per repository.

    python src/mailroom_eda/labeling/integrations/find_touchpoints.py \
        --repo . --repo ../llm-mailroom --repo ../local-mailroom-sandbox \
        --repo ../local-mailroom-sandbox/vendor/llm-dojo-scoring \
        [--class commercial_lease:commercial_leases_specialist] [--markdown out.md]

The class list starts from the five core classes plus every class in this repository's
``labeling/fields.yaml`` (so a class added there is tracked too); ``--class KEY`` or
``--class KEY:SPECIALIST`` adds more. A file where three or more class keys appear together
is reported as a **class registry**: a list, tuple or map that must gain the new class.
Other mentions are reported as **class-specific code** to read (a branch that may need a
new case). Vendored copies, virtualenvs, caches, a root-level ``data/`` and report output
are skipped; nested folders named ``data`` (``schemas/data/``) are scanned. A ``--repo``
that is not a directory is an error (exit 1). Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

CORE = {"contract": "contracts_specialist", "merger_agreement": "merger_agreement_specialist",
        "corporate_record": "corporate_records_specialist", "correspondence": "correspondence_specialist",
        "insurance_claim": "insurance_claims_specialist"}
FIELDS_YAML = Path(__file__).resolve().parents[1] / "fields.yaml"
SUFFIXES = {".py", ".yaml", ".yml", ".json", ".toml", ".md", ".txt"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "reports", "dist", "build",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages"}
ROOT_SKIP = {"data"}  # runtime data at the checkout root only
WINDOW = 12  # lines: class keys this close together form one registry


def registry_classes(path: Path = FIELDS_YAML) -> dict[str, str]:
    """Class keys and specialists declared in ``fields.yaml`` (read without PyYAML: the
    two-space-indented keys under ``classes:`` and their ``specialist:`` lines)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    in_classes, current = False, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^classes:\s*$", line):
            in_classes = True
            continue
        if in_classes and re.match(r"^\S", line):
            break
        m = re.match(r"^  ([a-z][a-z0-9_]*):\s*$", line)
        if in_classes and m:
            current = m[1]
            out[current] = ""
            continue
        m = re.match(r"^    specialist:\s*([A-Za-z0-9_]+)", line)
        if in_classes and current and m:
            out[current] = m[1]
    return out


def class_table(extra: list[str] = ()) -> dict[str, str]:
    """Core classes, ``fields.yaml`` classes and ``--class KEY[:SPECIALIST]`` additions."""
    table = dict(CORE)
    for k, v in registry_classes().items():
        table[k] = v or table.get(k, "")
    for item in extra:
        key, _, spec = item.partition(":")
        table[key] = spec or table.get(key, "")
    return table


def patterns(classes: dict[str, str]) -> dict[str, re.Pattern]:
    """Whole-word class keys; "contract" alone is too common, so only quoted or keyed uses count."""
    return {c: re.compile(rf"""["'`]{c}["'`]|\b{c}\s*:|\bexpected\s*==\s*["']{c}""") for c in classes}


CLASSES = dict(CORE)
PATTERNS = patterns(CLASSES)


def scan_file(path: Path, classes: dict[str, str] | None = None) -> tuple[list[tuple[int, str]], bool]:
    """Matching lines and whether the file holds a class registry."""
    classes = CLASSES if classes is None else classes
    pats = PATTERNS if classes is CLASSES else patterns(classes)
    specialists = {s: c for c, s in classes.items() if s}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return [], False
    hits: list[tuple[int, str]] = []
    per_line: list[set[str]] = []
    for i, line in enumerate(lines, 1):
        found = {c for c, rx in pats.items() if rx.search(line)}
        found |= {c for s, c in specialists.items() if s in line}
        per_line.append(found)
        if found:
            hits.append((i, line.strip()[:160]))
    registry = any(len(set().union(*per_line[i:i + WINDOW])) >= 3 for i in range(len(per_line)))
    return hits, registry


def scan_repo(root: Path, classes: dict[str, str] | None = None) -> dict[str, list]:
    """Registries and other mentions in one checkout."""
    out: dict[str, list] = defaultdict(list)
    vendor = root / "vendor"
    for path in sorted(root.rglob("*")):
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]) or (len(rel.parts) > 1 and rel.parts[0] in ROOT_SKIP):
            continue
        if vendor in path.parents and root != vendor:
            continue
        hits, registry = scan_file(path, classes)
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
    ap.add_argument("--class", dest="classes", action="append", default=[], metavar="KEY[:SPECIALIST]",
                    help="an extra class key to track, optionally with its specialist (repeat)")
    args = ap.parse_args(argv)
    missing = [r for r in args.repo if not r.is_dir()]
    if missing:
        for r in missing:
            print(f"{r}: not a directory", file=sys.stderr)
        return 1
    classes = class_table(args.classes)
    results = {}
    for root in args.repo:
        results[root] = scan_repo(root.resolve(), classes)
    text = render(results, args.lines)
    if args.markdown:
        args.markdown.write_text(text, encoding="utf-8")
        print(args.markdown)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
