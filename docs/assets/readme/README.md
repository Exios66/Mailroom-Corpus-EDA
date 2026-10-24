# README artwork

Constellation design **v1** follows the Digital-Mailroom README: midnight
backgrounds, cyan/violet/pink accents, system fonts, rounded panels and
repository-specific process diagrams. The root README repeats key facts in
text; artwork does not carry the only explanation of routing or quality.

```bash
python3 docs/assets/readme/generate.py
python3 docs/assets/readme/generate.py --check
```

The generator uses only Python's standard library and writes the three SVGs
beside it. `CONFIG` owns this repository's labels and source-backed facts.
Output is deterministic, with no timestamps, remote assets or build tooling.
`hero-mobile.svg` uses larger type in a two-column layout. `hero.svg` has a
single finite flow cue; all content is visible without animation and reduced
motion disables the cue. `stats.svg` is static. Review both viewport variants
after changing labels. `--check` compares bytes without writing.
