"""Print the version lockstep table for the four Mailroom repositories and flag drift.

    python src/mailroom_eda/labeling/integrations/check_lockstep.py \
        --eda . --mailroom ../llm-mailroom --sandbox ../local-mailroom-sandbox \
        [--dojo ../llm-dojo-scoring] [--markdown out.md]

Reads each pin from the checkout's own source (no imports, no network):

* **Dojo release**: this repository's ``labeling/fields.yaml`` ``dojo_pin``; the sandbox's
  ``_VENDOR_PINS`` (``src/mailroom_sandbox/cli.py``), ``requirements/dojo-release.txt`` and
  vendored ``llm_dojo_scoring.__version__``; llm-mailroom's ``pyproject.toml`` dependency; and,
  with ``--dojo``, the Dojo checkout's own version.
* **Dataset tag**: this repository's ``config.REPO_TAG``, llm-mailroom's
  ``FULL_CORPUS_REVISION`` and the sandbox's ``FAMILY_HF_TAG``.
* **Corpus size**: llm-mailroom's full-corpus ``n_docs`` and the sandbox's
  ``FAMILY_CORPUS_SIZE`` (compared with ``--rows`` when given).

Exit 0 when every row agrees, 1 when any row drifts or a pin cannot be read. The
lockstep rule (``README.md``): nothing is published until this exits 0 against the
release being cut. Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _read(path: Path) -> str:
    """File text, or '' when it is missing (the missing path is named on stderr)."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as err:
        print(f"missing: {path} ({err.strerror or err})", file=sys.stderr)
        return ""


def _find(pattern: str, text: str, group: int = 1) -> str | None:
    """First match of ``pattern`` (multiline), or None."""
    m = re.search(pattern, text, re.M)
    return m.group(group) if m else None


def eda_pins(root: Path) -> dict[str, str | None]:
    """Pins declared in this repository."""
    fields = _read(root / "src/mailroom_eda/labeling/fields.yaml")
    config = _read(root / "src/mailroom_eda/config.py")
    return {
        "dojo_version": _find(r'^dojo_pin:.*version:\s*"?([\w.\-]+)"?', fields),
        "dojo_commit": _find(r'^dojo_pin:.*commit:\s*"?([0-9a-f]{7,40})"?', fields),
        "dataset_tag": _find(r'^REPO_TAG\s*=\s*"([^"]+)"', config),
    }


def mailroom_pins(root: Path) -> dict[str, str | None]:
    """Pins declared in llm-mailroom."""
    py = _read(root / "pyproject.toml")
    corpora = _read(root / "src/pipeline/hf_corpora.py")
    block = corpora.split('"docclass-merged": {', 1)[1] if '"docclass-merged": {' in corpora else ""
    return {
        "dojo_ref": _find(r'llm-dojo-scoring\s*@\s*git\+[^@"]+@([\w.\-]+)', py),
        "dataset_tag": _find(r'^FULL_CORPUS_REVISION\s*=\s*"([^"]+)"', corpora),
        "n_docs": _find(r'"n_docs":\s*(\d+)', block),
    }


def sandbox_pins(root: Path) -> dict[str, str | None]:
    """Pins declared in local-mailroom-sandbox."""
    cli = _read(root / "src/mailroom_sandbox/cli.py")
    dojo = re.search(r'\("llm-dojo-scoring",\s*"([^"]+)",\s*"[^"]+",\s*"([0-9a-f]{40})"\)', cli)
    spec = _read(root / "src/mailroom_sandbox/job/spec.py")
    return {
        "dojo_version": dojo[1].lstrip("v") if dojo else None,
        "dojo_commit": dojo[2] if dojo else None,
        "release_commit": _find(r"llm-dojo-scoring\.git@([0-9a-f]{40})",
                                _read(root / "requirements/dojo-release.txt")),
        "vendored_version": _find(r'^__version__\s*=\s*"([^"]+)"',
                                  _read(root / "vendor/llm-dojo-scoring/src/llm_dojo_scoring/__init__.py")),
        "dataset_tag": _find(r'^FAMILY_HF_TAG\s*=\s*"([^"]+)"', spec),
        "corpus_size": _find(r"^FAMILY_CORPUS_SIZE\s*=\s*(\d+)", spec),
    }


def table(eda: Path, mailroom: Path, sandbox: Path, dojo: Path | None = None,
          rows: int | None = None) -> list[tuple[str, dict[str, str | None], bool]]:
    """``(check, {where: value}, agrees)`` rows of the lockstep table."""
    e, m, s = eda_pins(eda), mailroom_pins(mailroom), sandbox_pins(sandbox)
    versions = {"EDA fields.yaml dojo_pin": e["dojo_version"], "sandbox _VENDOR_PINS": s["dojo_version"],
                "sandbox vendored __version__": s["vendored_version"],
                "llm-mailroom pyproject": (m["dojo_ref"] or "").lstrip("v") or None}
    if dojo is not None:
        versions["llm-dojo-scoring pyproject"] = _find(r'^version\s*=\s*"([^"]+)"', _read(dojo / "pyproject.toml"))
    commits = {"EDA fields.yaml dojo_pin": e["dojo_commit"], "sandbox _VENDOR_PINS": s["dojo_commit"],
               "sandbox requirements/dojo-release.txt": s["release_commit"]}
    tags = {"EDA config.REPO_TAG": e["dataset_tag"], "llm-mailroom FULL_CORPUS_REVISION": m["dataset_tag"],
            "sandbox FAMILY_HF_TAG": s["dataset_tag"]}
    sizes = {"llm-mailroom n_docs": m["n_docs"], "sandbox FAMILY_CORPUS_SIZE": s["corpus_size"]}
    if rows is not None:
        sizes["expected (--rows)"] = str(rows)

    def same(values: dict[str, str | None]) -> bool:
        """Every value was read and they are all equal."""
        return None not in values.values() and len(set(values.values())) == 1

    return [("Dojo release version", versions, same(versions)),
            ("Dojo commit", commits, same(commits)),
            ("Dataset tag", tags, same(tags)),
            ("Corpus size", sizes, same(sizes))]


def render(result: list[tuple[str, dict[str, str | None], bool]]) -> str:
    """Markdown table."""
    lines = ["| check | where | value | status |", "|---|---|---|---|"]
    for check, values, ok in result:
        for i, (where, value) in enumerate(values.items()):
            status = ("ok" if ok else "**DRIFT**") if i == 0 else ""
            lines.append(f"| {check if i == 0 else ''} | {where} | `{value or 'unreadable'}` | {status} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--eda", type=Path, default=Path("."), help="Mailroom-Corpus-EDA checkout")
    ap.add_argument("--mailroom", type=Path, required=True, help="llm-mailroom checkout")
    ap.add_argument("--sandbox", type=Path, required=True, help="local-mailroom-sandbox checkout")
    ap.add_argument("--dojo", type=Path, help="llm-dojo-scoring checkout (optional)")
    ap.add_argument("--rows", type=int, help="the corpus size of the release being cut")
    ap.add_argument("--markdown", type=Path, help="also write the table here")
    args = ap.parse_args(argv)
    missing = [p for p in (args.eda, args.mailroom, args.sandbox, args.dojo) if p is not None and not p.is_dir()]
    if missing:
        for p in missing:
            print(f"{p}: not a directory", file=sys.stderr)
        return 1
    result = table(args.eda, args.mailroom, args.sandbox, args.dojo, args.rows)
    text = render(result)
    print(text)
    if args.markdown:
        args.markdown.write_text(text + "\n", encoding="utf-8")
    drift = [c for c, _, ok in result if not ok]
    if drift:
        print(f"drift: {', '.join(drift)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
