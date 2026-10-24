#!/usr/bin/env python3
"""Generate the README artwork for Mailroom-Corpus-EDA (constellation design v2).

    python3 docs/assets/readme/generate.py          # rewrite the SVGs beside this file
    python3 docs/assets/readme/generate.py --check  # read-only drift gate (exit 1 on drift)

`_art.py` is the shared kit (identical in every constellation repo); this file
owns only this repository's labels, scene and facts. Facts come from the README
and `reports/`: the v9.2 pin (3,302 documents, five classes, 55 strata), the
class counts, 13,753 CUAD spans, P0-P6, and the 30 PNG + 18 interactive figures.
Standard library only, deterministic output.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _art as art  # noqa: E402  (shared kit lives beside this file)

HERO = {
    'title': 'Mailroom Corpus EDA',
    'kicker': 'THE MAILROOM CONSTELLATION · THE CORPUS',
    'tagline': 'Know your corpus before you trust your model — 3,302 legal documents, profiled seven ways.',
    'eyebrow': 'v9.2 PIN · FIVE CLASSES · 55 STRATA · PHASES P0–P6 · CAST-SAFE HUB UPLOADS',
    'caption': 'pinned to mailroom-dataset v9.2 · static PNG and interactive Plotly reports · nothing publishes unless you say so',
    'alt': 'Animated banner: a bar chart of the five document classes in the v9.2 corpus (insurance claims 1,100, '
           'correspondence 1,000, contracts 600, corporate records 450, merger agreements 152) beside the seven '
           'analysis phases P0 to P6.',
    'tagline_lines': ['Know your corpus before you', 'trust your model.'],
    'mobile_cards': [('Fetch', 'pinned v9.2 snapshot'), ('Audit', 'integrity + composition'),
                     ('Report', '30 PNG + 18 interactive'), ('Publish', 'cast-safe HF helpers')],
    'mobile_note': '3,302 documents · five classes · 55 strata',
}
CLASSES = [('insurance_claim', 1100, art.PINK), ('correspondence', 1000, art.GREEN), ('contract', 600, art.SKY),
           ('corporate_record', 450, art.YELLOW), ('merger_agreement', 152, art.VIOLET)]
PHASES = [('P0', 'fetch'), ('P1', 'audit'), ('P2', 'strata'), ('P3', 'figures'), ('P4', 'plotly'), ('P5', 'stage'),
          ('P6', 'intent')]
STATS = [('3,302', 'documents · v9.2', art.SKY), ('5', 'document classes', art.VIOLET), ('55', 'strata', art.LIME),
         ('13,753', 'CUAD spans', art.YELLOW), ('P0–P6', 'analysis phases', art.PINK),
         ('48', 'figures', art.GREEN)]
STAGES = [('P0 Fetch', 'pinned corpus', 'manifest check'), ('P1 Audit', 'integrity', 'provenance'),
          ('P2 Strata', 'composition', 'imbalance'), ('P3 Figures', 'static PNGs', 'EDA tables'),
          ('P4 Plotly', 'interactive', 'HTML figures'), ('P5 Stage', 'cast-safe JSONL', '+ parquet'),
          ('P6 Intent', 'correspondence', 'coverage audit')]
SIDE = [('Summary report', 'SUMMARY_REPORT.md', 'narrative findings'), ('30 PNGs', 'reports/figures/', 'static figures'),
        ('18 HTML', 'figures_interactive/', 'Plotly, per-render UUIDs'), ('Tables', 'reports/tables/', 'CSV + JSON'),
        ('Dataset cards', 'docs/dataset-cards/', 'sources + licenses')]
TERMINAL = [('cmd', 'git clone https://github.com/Exios66/Mailroom-Corpus-EDA.git'),
            ('cmd', 'cd Mailroom-Corpus-EDA && python3.13 -m venv .venv'),
            ('cmd', '.venv/bin/python -m pip install -r requirements.txt'),
            ('cmd', '.venv/bin/python run_all.py --help'),
            ('note', '# read-only command discovery'),
            ('cmd', '.venv/bin/python run_all.py --phases P0'),
            ('note', '# download the pinned public corpus · no HF_TOKEN needed'),
            ('cmd', '.venv/bin/python run_all.py'),
            ('note', '# full analysis: local reports and staged exports, never a publish')]

TUI_GAUGES = [(name, f'{count:,} · {count / 3302:.0%}', count / 1100, color) for name, count, color in CLASSES]
TUI_LOG = [('ok', 'P0 fetched the pinned v9.2 corpus'), ('ok', '3,302 documents · five classes'), ('info', '55 strata profiled'),
           ('info', '13,753 CUAD spans counted'), ('ok', '30 PNG + 18 interactive figures'),
           ('warn', 'publishing is opt-in: nothing uploads')]
TUI_STATUS = 'run_all.py --phases P0 · no HF_TOKEN needed · bar length is relative to the largest class'


def scene() -> str:
    """Class-count bars on the left; the seven phases lighting up in turn on the right."""
    out = [art.text(90, 246, 'DOCUMENTS PER CLASS', 11, '#8b9bb4', 700, spacing=2)]
    longest = max(c for _, c, _ in CLASSES)
    defs = []
    for i, (name, count, color) in enumerate(CLASSES):
        y = 258 + i * 27
        w = 300 * count / longest
        out.append(art.text(236, y + 15, name, 13, art.MUTED, anchor='end', family=art.MONO))
        out.append(f'<rect x="248" y="{y}" width="{art.num(w)}" height="20" rx="6" fill="{color}" opacity=".9"/>')
        out.append(art.text(248 + w + 10, y + 15, f'{count:,}', 13, art.INK, 700))
        defs.append(f'<clipPath id="bar{i}"><rect x="248" y="{y}" width="{art.num(w)}" height="20" rx="6"/></clipPath>')
        out.append(f'<g class="motion" clip-path="url(#bar{i})"><rect x="188" y="{y}" width="60" height="20" fill="#ffffff" '
                   f'opacity=".35"><animate attributeName="x" values="188;{art.num(248 + w)};{art.num(248 + w)}" '
                   f'keyTimes="0;.4;1" dur="5s" begin="{art.num(i * 0.4)}s" repeatCount="indefinite"/></rect></g>')
    out.insert(0, '<defs>' + ''.join(defs) + '</defs>')
    out.append(art.text(700, 246, 'SEVEN PHASES, ONE COMMAND', 11, '#8b9bb4', 700, spacing=2))
    for i, (pid, label) in enumerate(PHASES):
        col, row = i % 4, i // 4
        x, y = 700 + col * 112, 260 + row * 72
        color = art.PALETTE[i % len(art.PALETTE)]
        out.append(f'<rect x="{x}" y="{y}" width="102" height="60" rx="12" fill="{art.PANEL}" stroke="{color}" '
                   'stroke-opacity=".75" stroke-width="1.5"/>')
        out.append(art.text(x + 51, y + 28, pid, 20, color, 800, 'middle'))
        out.append(art.text(x + 51, y + 47, label, 12, art.MUTED, anchor='middle'))
        out.append(f'<rect class="motion" x="{x}" y="{y}" width="102" height="60" rx="12" fill="{color}" opacity="0">'
                   f'<animate attributeName="opacity" values=".35;0;0" keyTimes="0;.2;1" dur="6.3s" '
                   f'begin="{art.num(i * 0.9)}s" repeatCount="indefinite"/></rect>')
    return '\n'.join(out)


def build() -> dict[str, str]:
    """Return every generated asset keyed by file name."""
    flow_desc = ('Seven analysis phases, P0 to P6, from fetching the pinned corpus to the correspondence intent '
                 'audit, and the reports they leave under reports/.')
    return {
        'hero.svg': art.hero(HERO, scene),
        'hero-mobile.svg': art.hero(HERO, mobile=True),
        'stats.svg': art.stats(STATS, 'Mailroom-Corpus-EDA by the numbers'),
        'stats-mobile.svg': art.stats(STATS, 'Mailroom-Corpus-EDA by the numbers', mobile=True),
        'flow.svg': art.flow('Seven phases, one command', STAGES, SIDE, 'What lands in reports/ and docs/',
                             desc=flow_desc, side_color=art.SKY),
        'flow-mobile.svg': art.flow('Seven phases, one command', STAGES, SIDE, 'What lands in reports/ and docs/',
                                    mobile=True, desc=flow_desc, side_color=art.SKY),
        'constellation.svg': art.constellation('Mailroom-Corpus-EDA'),
        'constellation-mobile.svg': art.constellation('Mailroom-Corpus-EDA', mobile=True),
        'tui.svg': art.tui('~/Mailroom-Corpus-EDA — run_all.py', TUI_GAUGES, TUI_LOG, TUI_STATUS,
                           'Dashboard: the five document classes with their counts and share of the 3,302-document v9.2 corpus, and a log of what the analysis produced.', left='CORPUS BY CLASS', right='RUN LOG'),
        'terminal.svg': art.terminal('~/Mailroom-Corpus-EDA — zsh', TERMINAL,
                                     'Animated terminal: clone, create the Python 3.13 venv, install the pinned '
                                     'requirements, list the commands, fetch the corpus and run the full analysis.'),
    }


def main() -> None:
    """Write the assets, or compare them byte-for-byte with --check."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true', help='fail on drift without writing')
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    drift = []
    for name, content in build().items():
        path = base / name
        if args.check:
            if not path.exists() or path.read_text(encoding='utf-8') != content:
                drift.append(name)
        else:
            path.write_text(content, encoding='utf-8')
    if drift:
        parser.exit(1, 'README asset drift: ' + ', '.join(drift) + '\n')
    print('README assets match' if args.check else 'README assets written')


if __name__ == '__main__':
    main()
