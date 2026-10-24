#!/usr/bin/env python3
"""Generate the repository's Mailroom constellation README artwork.

Design system: constellation-v1 (Digital-Mailroom midnight/cyan/violet/pink).
Run `python3 docs/assets/readme/generate.py` to rebuild adjacent SVGs, or
append `--check` for a read-only drift check. Python standard library only.
Facts are repository-local CONFIG values; see the README's source links.
No timestamp, external font, network access or image-generation dependency.
"""
from __future__ import annotations

import argparse
from html import escape
from pathlib import Path

CONFIG = {'kind': 'eda',
 'title': 'Mailroom Corpus EDA',
 'tagline': 'Inspect the corpus. Trace the evidence. Stage a reproducible export.',
 'tagline_lines': ['Inspect the corpus. Trace the evidence.',
                   'Stage a reproducible export.'],
 'eyebrow': 'PINNED CORPUS · EXPLORATORY ANALYSIS · CENTRALIZED HUB HELPERS',
 'description': 'The pinned corpus is downloaded, audited for integrity and '
                'composition, visualized in static and interactive reports, then '
                'staged for export and audited for intent coverage. Publishing to the '
                'Hub is an explicit separate action.',
 'stages': [('Download', 'P0 · pinned corpus'),
            ('Audit', 'P1–P2 · integrity + mix'),
            ('Visualize', 'P3–P4 · PNG + HTML'),
            ('Stage + coverage', 'P5–P6 · export + intents')],
 'mobile_stages': [('Download', 'P0 · pinned corpus'),
                   ('Audit', 'P1–P2 · integrity'),
                   ('Visualize', 'P3–P4 · reports'),
                   ('Stage + audit', 'P5–P6 · coverage')],
 'mobile_note': ['Reports + reproducible staging', 'Hub publish is an explicit action'],
 'stats': [('3,302', 'documents at v9.2', 'revision-pinned corpus'),
           ('5', 'document classes', '55 observed strata'),
           ('P0–P6', 'analysis phases', 'integrity → intent audit'),
           ('PNG + HTML', 'report formats', 'static + interactive')]}
COLORS = ("#38bdf8", "#c084fc", "#f472b6", "#22c55e")
FONT = "ui-sans-serif,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def text(x, y, value, size=20, color="#e2e8f0", weight=400, anchor="start"):
    """Draw escaped text using system fonts and explicit SVG coordinates."""
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'font-weight="{weight}" text-anchor="{anchor}">{escape(str(value))}</text>')


def frame(width, height, title, description):
    """Open an accessible, self-contained SVG with shared family tokens."""
    return [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" aria-labelledby="title desc" font-family="{FONT}">',
            f'<title id="title">{escape(title)}</title><desc id="desc">{escape(description)}</desc>',
            '<defs><linearGradient id="bg" x2="1" y2="1">'
            '<stop stop-color="#0b1220"/><stop offset=".55" stop-color="#151036"/>'
            '<stop offset="1" stop-color="#22103a"/></linearGradient>'
            '<linearGradient id="heading"><stop stop-color="#7dd3fc"/>'
            '<stop offset=".5" stop-color="#c4b5fd"/><stop offset="1" stop-color="#f9a8d4"/>'
            '</linearGradient><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
            '<path d="M0 0 L10 5 L0 10" fill="none" stroke="#94a3b8" stroke-width="1.5"/>'
            '</marker></defs>',
            '<style>.cue{stroke-dasharray:6 22;animation:travel 4.5s linear 1}'
            '@keyframes travel{from{stroke-dashoffset:112}to{stroke-dashoffset:0}}'
            '@media(prefers-reduced-motion:reduce){.cue{animation:none}}</style>',
            f'<rect width="{width}" height="{height}" rx="24" fill="url(#bg)"/>',
            f'<rect x=".5" y=".5" width="{width-1}" height="{height-1}" rx="24" fill="none" stroke="#334155"/>']


def line(x1, y1, x2, y2, cue=False):
    """Draw a permanently visible connector with an optional finite flow cue."""
    base = (f'<path d="M{x1} {y1} L{x2} {y2}" fill="none" stroke="#64748b" '
            'stroke-width="2" marker-end="url(#arrow)"/>')
    if cue:
        base += (f'<path class="cue" d="M{x1} {y1} L{x2} {y2}" '
                 'fill="none" stroke="#7dd3fc" stroke-width="2"/>')
    return base


def card(x, y, width, label, detail, color, height=86):
    """Draw one labeled stage without relying on motion to reveal content."""
    return (f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="14" '
            f'fill="#111a2e" stroke="{color}" stroke-opacity=".65"/>'
            f'<rect x="{x+16}" y="{y+18}" width="5" height="{height-36}" rx="2" fill="{color}"/>'
            + text(x+34, y+36, label, 21, "#e2e8f0", 700)
            + text(x+34, y+63, detail, 16, "#cbd5e1"))


def branch_band(label, left, right):
    """Draw a visible two-outcome band for routing and quality branches."""
    return (text(600, 462, label, 16, "#94a3b8", anchor="middle")
            + text(326, 425, left, 19, "#7dd3fc", 600, "middle")
            + text(874, 425, right, 19, "#facc15", 600, "middle")
            + '<path d="M1023 346 V366 H600 V393 H326 M600 393 H874" fill="none" stroke="#64748b" stroke-width="1.5"/>')


def desktop_diagram(kind):
    """Render a repository-specific topology, rather than an identical conveyor."""
    stages = CONFIG["stages"]
    bits = []
    if kind == "dojo":
        bits += [card(48, 246, 270, "Ground truth", "expected fields + labels", COLORS[0], 80),
                 card(48, 344, 270, "Prediction", "observed fields + labels", COLORS[1], 80),
                 '<path d="M318 286 H370 V335 H420 M318 384 H370 V335" fill="none" stroke="#64748b" stroke-width="2"/>',
                 card(420, 292, 330, "Field-type matcher", "normalize · align · compare", COLORS[2]),
                 line(750, 335, 825, 335, True),
                 card(825, 246, 327, "Metrics + intervals", "task suites · uncertainty", COLORS[3], 80),
                 card(825, 344, 327, "Diagnostics + reports", "explain errors · export", COLORS[0], 80)]
    else:
        for i, (label, detail) in enumerate(stages):
            x = 48+i*282
            bits.append(card(x, 260, 258, label, detail, COLORS[i]))
            if i < 3:
                bits.append(line(x+258, 303, x+279, 303, True))
        if kind == "ml":
            bits.append(branch_band("calibrated gate", "CPU fast path when accepted", "LLM fallback when uncertain"))
        elif kind == "pipeline":
            bits += ['<path d="M741 346 V404 H1020 V346" fill="none" stroke="#facc15" stroke-width="1.5" stroke-dasharray="5 5"/>',
                     text(600, 391, "When required: Judge → Arbiter → human review", 18, "#facc15", 600, "middle"),
                     text(600, 435, "Boss handles matter conflicts · procedural compilation · hash-chained audit", 17, "#cbd5e1", anchor="middle")]
        else:
            bits += [text(600, 395, "P0 → P1 → P2 → P3 / P4 → P5 → P6", 23, "#7dd3fc", 600, "middle"),
                     text(600, 435, "Reports + staged exports · Hub publication is an explicit operator action", 17, "#cbd5e1", anchor="middle")]
    return "\n".join(bits)


def hero(mobile=False):
    """Render the full desktop hero or a readable two-column phone variant."""
    width, height = (640, 680) if mobile else (1200, 480)
    bits = frame(width, height, CONFIG["title"], CONFIG["description"])
    center = width//2
    bits += [text(center, 54, "THE MAILROOM CONSTELLATION", 14, "#94a3b8", 700, "middle"),
             text(center, 116 if mobile else 132, CONFIG["title"], 42 if mobile else 64, "url(#heading)", 800, "middle")]
    if mobile:
        for i, label in enumerate(CONFIG["tagline_lines"]):
            bits.append(text(center, 157+i*29, label, 22, "#cbd5e1", anchor="middle"))
        for i, (label, detail) in enumerate(CONFIG["mobile_stages"]):
            x, y = 24+(i%2)*306, 248+(i//2)*146
            bits.append(card(x, y, 286, label, detail, COLORS[i], 110))
        bits += ([line(167, 358, 167, 388), '<path d="M473 358 V378 H167" fill="none" stroke="#64748b" stroke-width="2"/>'] if CONFIG["kind"] == "dojo" else [line(310,303,326,303), '<path d="M473 363 V378 H167 V388" fill="none" stroke="#64748b" stroke-width="2" marker-end="url(#arrow)"/>'])
        bits += [line(310,449,326,449),
                 text(center, 575, CONFIG["mobile_note"][0], 22, "#7dd3fc", 600, "middle"),
                 text(center, 610, CONFIG["mobile_note"][1], 20, "#cbd5e1", anchor="middle")]
    else:
        bits += [text(center, 181, CONFIG["tagline"], 23, "#cbd5e1", anchor="middle"),
                 text(center, 220, CONFIG["eyebrow"], 14, "#94a3b8", 600, "middle"),
                 desktop_diagram(CONFIG["kind"])]
    bits.append('</svg>\n')
    return "\n".join(bits)


def stats():
    """Render static capability cards; repeat the facts as prose in the README."""
    bits = frame(1200, 176, CONFIG["title"]+" at a glance", "; ".join(" — ".join(s) for s in CONFIG["stats"]))
    for i, (value, label, detail) in enumerate(CONFIG["stats"]):
        x = 24+i*294
        bits += [f'<rect x="{x}" y="24" width="270" height="128" rx="18" fill="#111a2e" stroke="#334155"/>',
                 text(x+22, 76, value, 34, COLORS[i], 800),
                 text(x+22, 107, label, 19, "#e2e8f0", 600),
                 text(x+22, 132, detail, 15, "#94a3b8")]
    bits.append('</svg>\n')
    return "\n".join(bits)


def main():
    """Generate deterministic adjacent assets, or check their bytes read-only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on asset drift without writing")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    assets = {"hero.svg": hero(), "hero-mobile.svg": hero(True), "stats.svg": stats()}
    drift = []
    for name, content in assets.items():
        path = base/name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                drift.append(name)
        else:
            path.write_text(content, encoding="utf-8")
    if drift:
        parser.exit(1, "README asset drift: " + ", ".join(drift) + "\n")
    print("README assets match" if args.check else "Generated three README SVG assets")


if __name__ == "__main__":
    main()
