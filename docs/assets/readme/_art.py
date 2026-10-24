"""Shared README artwork kit for the Mailroom constellation (design v2).

Every repository that carries this file renders the same visual language as the
Digital-Mailroom README: midnight gradients, system fonts, rounded panels, a
twinkling starfield and small "things travelling through a process" animations.
Each repo's `generate.py` supplies its own labels, scene and source-backed facts.

Rules the kit enforces:

* Python standard library only; output is deterministic (no timestamps, no
  randomness, no remote assets, no external fonts).
* Static content carries the meaning. Everything that merely moves (twinkling
  stars, travelling particles, envelopes, shine sweeps) lives in a `.motion`
  group that `prefers-reduced-motion: reduce` hides.
* Wide artwork ships with a vertical `*-mobile.svg` twin for phone viewports.
"""
from __future__ import annotations

import html
import math

FONT = "ui-sans-serif,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"

SKY, VIOLET, PINK, GREEN = '#38bdf8', '#a78bfa', '#f472b6', '#22c55e'
AMBER, LIME, YELLOW, ORANGE = '#f59e0b', '#a3e635', '#facc15', '#fb923c'
INK, MUTED, DIM, PANEL, EDGE = '#e2e8f0', '#94a3b8', '#64748b', '#0f172a', '#1f2a44'
PALETTE = (SKY, GREEN, LIME, VIOLET, ORANGE, PINK, YELLOW)


def esc(value) -> str:
    """HTML-escape any value for SVG text and attributes."""
    return html.escape(str(value), quote=True)


def num(value: float) -> str:
    """Format a coordinate compactly and deterministically."""
    text = f'{value:.1f}'
    return text[:-2] if text.endswith('.0') else text


def text(x, y, value, size=14, fill=INK, weight=400, anchor='start', italic=False,
         spacing=None, family=None, extra='') -> str:
    """Draw one escaped <text> node."""
    attrs = f'x="{num(x)}" y="{num(y)}" font-size="{size}" fill="{fill}"'
    if weight != 400:
        attrs += f' font-weight="{weight}"'
    if anchor != 'start':
        attrs += f' text-anchor="{anchor}"'
    if italic:
        attrs += ' font-style="italic"'
    if spacing is not None:
        attrs += f' letter-spacing="{spacing}"'
    if family:
        attrs += f' font-family="{family}"'
    return f'<text {attrs}{extra}>{esc(value)}</text>'


ENVELOPE = ('<g id="env"><rect x="-22" y="-30" width="44" height="30" rx="3" fill="#fef3c7" '
            'stroke="#f59e0b" stroke-width="2"/><polyline points="-22,-30 0,-13 22,-30" fill="none" '
            'stroke="#f59e0b" stroke-width="2" stroke-linejoin="round"/>'
            '<circle cx="0" cy="-13" r="3.2" fill="#ef4444"/></g>')


def svg_open(width, height, title, desc, mono=False, defs='') -> str:
    """Open an accessible SVG with the shared gradients, marker and motion rule."""
    family = MONO if mono else FONT
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
        f'height="{height}" role="img" aria-labelledby="t d" font-family="{family}">\n'
        f'  <title id="t">{esc(title)}</title>\n  <desc id="d">{esc(desc)}</desc>\n  <defs>\n'
        '    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0b1220"/>'
        '<stop offset=".55" stop-color="#151036"/><stop offset="1" stop-color="#22103a"/></linearGradient>\n'
        '    <linearGradient id="ttl" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#7dd3fc"/>'
        '<stop offset=".5" stop-color="#c4b5fd"/><stop offset="1" stop-color="#f9a8d4"/></linearGradient>\n'
        '    <radialGradient id="glow" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#a78bfa" '
        'stop-opacity=".9"/><stop offset="1" stop-color="#a78bfa" stop-opacity="0"/></radialGradient>\n'
        '    <filter id="soft" x="-50%" y="-50%" width="200%" height="200%">'
        '<feGaussianBlur stdDeviation="3"/></filter>\n'
        '    <marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M1 1 L9 5 L1 9" fill="none" stroke="#94a3b8" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></marker>\n'
        f'{defs}  </defs>\n'
        '  <style>@media (prefers-reduced-motion: reduce){.motion{display:none}}</style>\n'
        f'  <rect width="{width}" height="{height}" rx="24" fill="url(#bg)"/>\n'
        f'  <rect x=".5" y=".5" width="{width - 1}" height="{height - 1}" rx="24" fill="none" stroke="{EDGE}"/>\n')


def svg_close() -> str:
    """Close the document."""
    return '</svg>\n'


def stars(width, height, count, ymax, keep_out=None) -> str:
    """Deterministic starfield: faint static dots plus a `.motion` twinkle overlay."""
    base, twinkle, seed = [], [], 7
    for i in range(count):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        x = 20 + (seed >> 4) % (width - 40)
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        y = 16 + (seed >> 4) % max(1, ymax - 16)
        if keep_out and keep_out[0] <= x <= keep_out[2] and keep_out[1] <= y <= keep_out[3]:
            continue
        r = 1.0 + ((seed >> 9) % 8) / 10
        dur = 2.2 + ((seed >> 13) % 25) / 10
        base.append(f'<circle cx="{x}" cy="{y}" r="{num(r)}" opacity=".35"/>')
        twinkle.append(f'<circle cx="{x}" cy="{y}" r="{num(r)}"><animate attributeName="opacity" '
                       f'values="0;.9;0" dur="{num(dur)}s" begin="{num((i % 7) * 0.4)}s" repeatCount="indefinite"/></circle>')
    return ('  <g fill="#e0e7ff">' + ''.join(base) + '</g>\n'
            '  <g class="motion" fill="#e0e7ff">' + ''.join(twinkle) + '</g>\n')


def title_text(x, y, title, size) -> str:
    """Gradient title; a leading 'The ' is set light and italic."""
    if title.startswith('The '):
        inner = f'<tspan font-weight="300" font-style="italic">The </tspan>{esc(title[4:])}'
    else:
        inner = esc(title)
    return (f'<text x="{x}" y="{y}" text-anchor="middle" font-size="{size}" font-weight="800" '
            f'letter-spacing="-1.5" fill="url(#ttl)">{inner}</text>')


def arrow(x1, y1, x2, y2, color=DIM) -> str:
    """Static connector with an arrowhead."""
    return (f'<path d="M{num(x1)} {num(y1)} L{num(x2)} {num(y2)}" fill="none" stroke="{color}" '
            'stroke-width="2" marker-end="url(#arr)"/>')


def particle(path_id, dur, begin=0.0, color='#7dd3fc', r=5) -> str:
    """A glowing dot travelling along a path; hidden under reduced motion."""
    return (f'<circle class="motion" r="{r}" fill="{color}" filter="url(#soft)"><animateMotion dur="{num(dur)}s" '
            f'begin="{num(begin)}s" repeatCount="indefinite"><mpath href="#{path_id}"/></animateMotion></circle>')


def pulse_ring(x, y, w, h, color, dur=2.4, rx=14) -> str:
    """A breathing outline around a card (the "you are here" marker)."""
    return (f'<rect class="motion" x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" rx="{rx}" '
            f'fill="none" stroke="{color}" stroke-width="2"><animate attributeName="stroke-opacity" '
            f'values=".8;0;.8" dur="{num(dur)}s" repeatCount="indefinite"/><animate attributeName="stroke-width" '
            f'values="2;9;2" dur="{num(dur)}s" repeatCount="indefinite"/></rect>')


# --------------------------------------------------------------------------- hero


def conveyor(gates, y=318, branch=None) -> str:
    """The signature belt: envelopes ride through labelled gates.

    `gates` is [(label, color)]. `branch` is (gate_index, label) and adds a flagged
    envelope that drops off the belt into a labelled bin below that gate.
    """
    n = len(gates)
    span = 1120 / (n + 1)
    speed = 1120 / 6
    out = [f'<line x1="40" y1="{y}" x2="1160" y2="{y}" stroke="#334155" stroke-width="12" stroke-linecap="round"/>',
           f'<line x1="40" y1="{y}" x2="1160" y2="{y}" stroke="#64748b" stroke-width="2" stroke-dasharray="10 14"/>',
           f'<line class="motion" x1="40" y1="{y}" x2="1160" y2="{y}" stroke="#94a3b8" stroke-width="2" '
           'stroke-dasharray="10 14"><animate attributeName="stroke-dashoffset" from="24" to="0" dur="0.6s" '
           'repeatCount="indefinite"/></line>',
           '<g fill="#1e293b" stroke="#475569" stroke-width="2">'
           + ''.join(f'<circle cx="{60 + k * 180}" cy="{y + 16}" r="9"/>' for k in range(7)) + '</g>',
           f'<path id="belt" d="M40 {y - 6} H1160" fill="none"/>']
    out.append('<g font-size="12" font-weight="700" letter-spacing="1.5" text-anchor="middle">')
    centers = []
    for i, (label, color) in enumerate(gates):
        cx = 40 + span * (i + 1)
        centers.append(cx)
        begin = ((cx - 40) / speed) % 2
        out.append(f'<rect x="{num(cx - 50)}" y="{y - 74}" width="100" height="24" rx="6" fill="{color}"/>')
        out.append(f'<rect class="motion" x="{num(cx - 50)}" y="{y - 74}" width="100" height="24" rx="6" fill="#ffffff" '
                   f'opacity="0"><animate attributeName="opacity" values=".55;0;0" keyTimes="0;.35;1" dur="2s" '
                   f'begin="{num(begin)}s" repeatCount="indefinite"/></rect>')
        out.append(f'<text x="{num(cx)}" y="{y - 57}" fill="#0b1220">{esc(label)}</text>')
    out.append('</g>')
    out.append('<g class="motion">')
    for k in range(3):
        out.append(f'<use href="#env"><animateMotion dur="6s" begin="{2 * k}s" repeatCount="indefinite">'
                   '<mpath href="#belt"/></animateMotion><animate attributeName="opacity" values="0;1;1;0" '
                   f'keyTimes="0;.04;.94;1" dur="6s" begin="{2 * k}s" repeatCount="indefinite"/></use>')
    out.append('</g>')
    out.append('<g fill="#94a3b8" opacity=".85">' + ''.join(
        f'<rect x="{num(cx - 48)}" y="{y - 50}" width="6" height="44"/><rect x="{num(cx + 42)}" y="{y - 50}" '
        'width="6" height="44"/>' for cx in centers) + '</g>')
    if branch:
        idx, label = branch
        bx = centers[idx]
        out.append(f'<path d="M{num(bx - 38)} {y + 40} l8 38 h60 l8 -38" fill="#0f172a" stroke="#fb7185" '
                   'stroke-width="2" stroke-linejoin="round"/>')
        out.append(text(bx, y + 100, label, 12, '#fda4af', 700, 'middle', spacing=1.5))
        out.append(f'<path id="drop" d="M40 {y - 6} H{num(bx)} C{num(bx + 24)} {y - 6} {num(bx + 8)} {y + 22} '
                   f'{num(bx)} {y + 62}" fill="none"/>')
        out.append('<g class="motion"><g opacity="0"><use href="#env" style="filter:hue-rotate(-40deg)"/>'
                   '<animateMotion dur="4.5s" begin="1s" repeatCount="indefinite"><mpath href="#drop"/></animateMotion>'
                   '<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.05;.9;1" dur="4.5s" begin="1s" '
                   'repeatCount="indefinite"/></g></g>')
    return '\n'.join(out)


def hero(cfg, scene=None, mobile=False) -> str:
    """Render the animated banner (desktop) or its phone-sized twin.

    cfg keys: title, tagline, tagline_lines, kicker, eyebrow, caption, alt,
    mobile_cards [(label, detail)], mobile_note.
    """
    if mobile:
        width, height = 640, 560
        out = [svg_open(width, height, cfg['title'], cfg['alt'], defs='    ' + ENVELOPE + '\n'),
               stars(width, height, 26, 200, (60, 60, 580, 190))]
        out.append(text(320, 46, cfg['kicker'], 12, '#8b9bb4', 700, 'middle', spacing=2))
        out.append(title_text(320, 108, cfg['title'], cfg.get('mobile_title_size', 46)))
        for i, line in enumerate(cfg['tagline_lines']):
            out.append(text(320, 148 + i * 26, line, 18, '#cbd5e1', anchor='middle'))
        for i, (label, detail) in enumerate(cfg['mobile_cards']):
            x, y = 24 + (i % 2) * 300, 224 + (i // 2) * 100
            color = PALETTE[i % len(PALETTE)]
            out.append(f'<rect x="{x}" y="{y}" width="292" height="86" rx="14" fill="{PANEL}" stroke="{color}" '
                       'stroke-opacity=".7" stroke-width="1.5"/>')
            out.append(f'<rect x="{x + 16}" y="{y + 18}" width="5" height="50" rx="2" fill="{color}"/>')
            out.append(text(x + 34, y + 38, label, 19, INK, 700))
            out.append(text(x + 34, y + 62, detail, 14, MUTED))
        out.append('<path d="M40 468 H600" stroke="#334155" stroke-width="10" stroke-linecap="round"/>')
        out.append('<path d="M40 468 H600" stroke="#64748b" stroke-width="2" stroke-dasharray="10 14"/>')
        out.append('<path id="mb" d="M40 462 H600" fill="none"/>')
        out.append('<g class="motion">' + ''.join(
            f'<use href="#env" transform="scale(.7)" ><animateMotion dur="5s" begin="{k * 2.5}s" repeatCount="indefinite">'
            '<mpath href="#mb"/></animateMotion></use>' for k in range(2)) + '</g>')
        out.append(text(320, 516, cfg['mobile_note'], 14, '#8b9bb4', anchor='middle'))
        out.append(svg_close())
        return ''.join(out)
    width, height = 1200, 430
    out = [svg_open(width, height, cfg['title'], cfg['alt'], defs='    ' + ENVELOPE + '\n'),
           stars(width, height, 44, 215, (150, 60, 1050, 215))]
    out.append(text(600, 44, cfg['kicker'], 13, '#8b9bb4', 700, 'middle', spacing=3))
    out.append(f'<ellipse cx="600" cy="116" rx="420" ry="70" fill="url(#glow)" opacity=".18"/>')
    out.append(title_text(600, 126, cfg['title'], 70))
    out.append(text(600, 170, cfg['tagline'], 20, '#cbd5e1', anchor='middle'))
    out.append(text(600, 202, cfg['eyebrow'], 13, '#8b9bb4', 600, 'middle', spacing=3))
    out.append(scene())
    out.append(text(600, 414, cfg['caption'], 13, '#8b9bb4', anchor='middle'))
    out.append(svg_close())
    return ''.join(out)


# --------------------------------------------------------------------------- stats


def stats(items, title, mobile=False) -> str:
    """Render the "by the numbers" strip. `items` is [(value, label, color)]."""
    n = len(items)
    longest = max(len(v) for v, _, _ in items)
    desc = '; '.join(f'{v} {label}' for v, label, _ in items)
    if mobile:
        cols, width, pad = 2, 640, 16
        cw = (width - pad * 2) / cols
        rows = math.ceil(n / cols)
        height = pad * 2 + rows * 118 - 8
        cell_h = 110
    else:
        cols, width, pad = n, 1200, 20
        cw = (width - pad * 2) / cols
        rows, height, cell_h = 1, 170, 130
    size = max(24, min(46, int((cw - 40) / (longest * 0.62))))
    out = [svg_open(width, height, title, desc,
                    defs='    <linearGradient id="shine" x1="0" y1="0" x2="1" y2="0"><stop offset="0" '
                         'stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#fff" stop-opacity=".12"/>'
                         '<stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>\n')]
    for i, (value, label, color) in enumerate(items):
        col, row = i % cols, i // cols
        x = pad + col * cw
        if mobile and n % 2 and i == n - 1:
            x = (width - cw) / 2
        y = pad + row * 118 if mobile else 20
        cx = x + cw / 2
        out.append(f'  <clipPath id="c{i}"><rect x="{num(x + 8)}" y="{y}" width="{num(cw - 16)}" height="{cell_h}" rx="16"/></clipPath>')
        out.append(f'  <rect x="{num(x + 8)}" y="{y}" width="{num(cw - 16)}" height="{cell_h}" rx="16" fill="#111a2e" stroke="{EDGE}"/>')
        vy = y + (cell_h * 0.55 if not mobile else cell_h * 0.5)
        out.append('  ' + text(cx, vy, value, size, color, 800, 'middle'))
        out.append('  ' + text(cx, vy + (30 if not mobile else 26), label, 14 if not mobile else 15, MUTED, anchor='middle'))
        out.append(f'  <rect x="{num(cx - 22)}" y="{y + cell_h - 17}" width="44" height="4" rx="2" fill="{color}"/>')
        out.append(f'  <g class="motion" clip-path="url(#c{i})"><rect x="{num(x - 60)}" y="{y}" width="60" height="{cell_h}" '
                   f'fill="url(#shine)"><animate attributeName="x" values="{num(x - 60)};{num(x + cw)};{num(x + cw)}" '
                   f'keyTimes="0;.35;1" dur="5s" begin="{num(i * 0.5)}s" repeatCount="indefinite"/></rect></g>')
    out.append(svg_close())
    return '\n'.join(out)


# --------------------------------------------------------------------------- flow


def fit(value, width, size, floor=10.0) -> float:
    """Shrink `size` so `value` (pessimistically 0.58 em per glyph) fits `width`."""
    return max(floor, min(size, round(width / (len(value) * 0.58), 1)))


def uniform(rows, width, sizes=(17, 13, 12)):
    """Smallest fitting font size per text line across `rows`, so a row of cards stays even."""
    inner = width - 44
    out = []
    for col, base in enumerate(sizes):
        vals = [r[col] for r in rows if len(r) > col and r[col]]
        out.append(min((fit(v, inner, base) for v in vals), default=base))
    return tuple(out)


def _card(x, y, w, h, color, label, d1, d2=None, sizes=(17, 13, 12)) -> str:
    bits = [f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" rx="14" fill="{PANEL}" '
            f'stroke="{color}" stroke-opacity=".7" stroke-width="1.5"/>',
            f'<rect x="{num(x + 14)}" y="{num(y + 16)}" width="5" height="{num(h - 32)}" rx="2" fill="{color}"/>',
            text(x + 30, y + 34, label, sizes[0], INK, 700),
            text(x + 30, y + 56, d1, sizes[1], MUTED)]
    if d2:
        bits.append(text(x + 30, y + 74, d2, sizes[2], DIM))
    return ''.join(bits)


def flow(heading, stages, side=None, side_heading=None, mobile=False, desc='', side_color='#fb7185') -> str:
    """Process diagram: a chain of stage cards with a travelling particle.

    stages: [(label, detail1, detail2)]; side: same shape, drawn as a quieter
    "exceptions" row (desktop) or a trailing group (mobile).
    """
    n = len(stages)
    if not mobile:
        width, gap, top = 1200, 22, 74
        cw = (width - 80 - (n - 1) * gap) / n
        height = (top + 104 + 44 + 92 + 34) if side else (top + 104 + 40)
        out = [svg_open(width, height, heading, desc)]
        out.append(text(40, 42, heading.upper(), 13, '#8b9bb4', 700, spacing=3))
        mid = top + 52
        out.append(f'<path id="chain" d="M{num(40 + cw / 2)} {mid} H{num(40 + (n - 1) * (cw + gap) + cw / 2)}" fill="none"/>')
        sz = uniform(stages, cw, (16, 13, 12))
        for i, (label, d1, d2) in enumerate(stages):
            x = 40 + i * (cw + gap)
            color = PALETTE[i % len(PALETTE)]
            out.append(_card(x, top, cw, 104, color, label, d1, d2, sz))
            out.append(f'<rect class="motion" x="{num(x)}" y="{top}" width="{num(cw)}" height="104" rx="14" fill="none" '
                       f'stroke="{color}" stroke-width="3" opacity="0"><animate attributeName="opacity" values="1;0;0" '
                       f'keyTimes="0;.18;1" dur="{num(n * 0.9)}s" begin="{num(i * 0.9)}s" repeatCount="indefinite"/></rect>')
            if i < n - 1:
                out.append(arrow(x + cw + 2, mid, x + cw + gap - 2, mid))
        out.append(particle('chain', n * 0.9, 0))
        if side:
            sy = top + 104 + 44
            out.append(text(40, sy - 14, (side_heading or '').upper(), 13, '#8b9bb4', 700, spacing=3))
            m = len(side)
            sw = (width - 80 - (m - 1) * gap) / m
            ssz = uniform(side, sw, (15, 13, 12))
            for j, (label, d1, d2) in enumerate(side):
                x = 40 + j * (sw + gap)
                out.append(f'<g opacity=".92">{_card(x, sy, sw, 92, side_color, label, d1, d2, ssz)}</g>')
        out.append(svg_close())
        return '\n'.join(out)
    width, ch, gap, top = 640, 82, 26, 70
    m = len(side) if side else 0
    height = top + n * ch + (n - 1) * gap + (40 + 16 + m * 90 + 20 if side else 30)
    out = [svg_open(width, height, heading, desc)]
    out.append(text(24, 40, heading.upper(), 12, '#8b9bb4', 700, spacing=2))
    out.append(f'<path id="chain" d="M320 {top + ch / 2} V{top + (n - 1) * (ch + gap) + ch / 2}" fill="none"/>')
    for i, (label, d1, d2) in enumerate(stages):
        y = top + i * (ch + gap)
        color = PALETTE[i % len(PALETTE)]
        out.append(_card(24, y, 592, ch, color, label, d1, d2, (18, 14, 12)))
        if i < n - 1:
            out.append(arrow(320, y + ch + 3, 320, y + ch + gap - 3))
    out.append(particle('chain', n * 0.9, 0))
    if side:
        sy = top + n * ch + (n - 1) * gap + 40
        out.append(text(24, sy, (side_heading or '').upper(), 12, '#8b9bb4', 700, spacing=2))
        for j, (label, d1, d2) in enumerate(side):
            out.append(f'<g opacity=".92">{_card(24, sy + 16 + j * 90, 592, 82, side_color, label, d1, d2, (17, 14, 12))}</g>')
    out.append(svg_close())
    return '\n'.join(out)


# --------------------------------------------------------------------------- constellation

NODES = {
    'eda': ('mailroom-corpus-eda', 'EDA + centralized HF uploads', 'virtual member · no build', SKY, False),
    'hf': ('HF Hub', 'mailroom-dataset · pinned tag', 'external service', YELLOW, True),
    'ml': ('mailroom-ml', 'ModernBERT intake classifier', 'standalone contractor', PINK, True),
    'sandbox': ('local-mailroom-sandbox', 'offline/local + Modal/vLLM GPU', 'testing harness', VIOLET, False),
    'pipeline': ('llm-mailroom', '13-node LangGraph pipeline', 'FastAPI producer', GREEN, False),
    'dojo': ('llm-dojo-scoring', 'ALL scoring engine + metrics', 'error analysis & interpretation', AMBER, False),
    'langfuse': ('Langfuse', 'external · trace store', '', '#fb7185', True),
    'viz': ('The-Mailroom', 'pixel-art visualizer', 'shared by pipeline + sandbox', '#c084fc', False),
}
REPO_KEY = {'Mailroom-Corpus-EDA': 'eda', 'mailroom-ml': 'ml', 'local-mailroom-sandbox': 'sandbox',
            'llm-mailroom': 'pipeline', 'llm-dojo-scoring': 'dojo', 'The-Mailroom': 'viz'}

# (node key -> x, y, w, h) per layout, and (id, label, svg path, color, label x, label y) for edges.
LAYOUT_DESKTOP = {
    'size': (1200, 484), 'node': {
        'eda': (40, 70, 230, 84), 'hf': (330, 70, 240, 84), 'ml': (640, 70, 230, 84),
        'sandbox': (80, 230, 240, 84), 'pipeline': (420, 230, 240, 84),
        'viz': (950, 230, 210, 84), 'langfuse': (720, 230, 170, 84), 'dojo': (250, 376, 240, 84)},
    'edges': [
        ('e1', 'publishes', 'M270 112 H330', SKY, 276, 100),
        ('e2', 'pinned pull', 'M400 154 C380 190 250 190 210 230', SKY, 262, 192),
        ('e3', 'pinned pull', 'M500 154 C520 190 530 200 540 230', SKY, 530, 188),
        ('e4', 'trains on', 'M570 112 H640', PINK, 574, 100),
        ('e5', 'optional fast path', 'M745 154 C735 190 650 196 620 230', PINK, 700, 186),
        ('e6', 'executes', 'M320 272 H420', VIOLET, 340, 262),
        ('e7', 'scores with', 'M200 314 C215 355 260 372 300 376', AMBER, 128, 356),
        ('e8', 'scores with', 'M540 314 C530 355 480 372 440 376', AMBER, 520, 358),
        ('e9', 'traces', 'M660 284 H720', '#fb7185', 666, 276),
        ('e10', 'feeds', 'M890 272 H950', '#fb7185', 898, 262)],
    'caption': 'dashed outline = external service or standalone contractor', 'head': (40, 40), 'legend': (40, 468)}
LAYOUT_MOBILE = {
    'size': (640, 750), 'node': {
        'eda': (20, 56, 250, 80), 'hf': (330, 56, 260, 80), 'sandbox': (20, 196, 250, 80),
        'ml': (330, 196, 260, 80), 'pipeline': (330, 336, 260, 80), 'dojo': (20, 476, 250, 80),
        'viz': (20, 610, 250, 72), 'langfuse': (330, 610, 260, 72)},
    'edges': [
        ('e1', 'publishes', 'M270 96 H330', SKY, 270, 86),
        ('e2', 'pinned pull', 'M350 136 C340 172 170 150 145 196', SKY, 150, 168),
        ('e3', 'trains on', 'M460 136 V196', PINK, 470, 170),
        ('e4', 'optional fast path', 'M460 276 V336', PINK, 470, 312),
        ('e5', 'executes', 'M270 256 C300 256 300 376 330 376', VIOLET, 292, 330),
        ('e6', 'pinned pull', 'M590 90 C640 90 640 376 590 376', SKY, 0, 0),
        ('e7', 'scores with', 'M145 276 V476', AMBER, 155, 380),
        ('e8', 'scores with', 'M380 416 C380 480 330 516 270 516', AMBER, 290, 548),
        ('e9', 'traces', 'M540 416 V610', '#fb7185', 550, 520),
        ('e10', 'feeds', 'M330 646 H270', '#fb7185', 276, 636)],
    'caption': 'dashed = external service or contractor', 'head': (24, 36), 'legend': (24, 722)}


def constellation(current, mobile=False) -> str:
    """Mini data-flow map of the constellation with `current` marked "you are here"."""
    lay = LAYOUT_MOBILE if mobile else LAYOUT_DESKTOP
    width, height = lay['size']
    here = REPO_KEY[current]
    desc = (f'Data-flow map of the Mailroom constellation with {current} highlighted. '
            'mailroom-corpus-eda publishes mailroom-dataset to the Hugging Face Hub; the sandbox and the pipeline pull '
            'the pinned tag; the sandbox executes the pipeline; both score with llm-dojo-scoring; the pipeline sends '
            'traces to Langfuse, which feeds The-Mailroom visualizer; mailroom-ml trains on the dataset and offers an '
            'optional fast path to the pipeline.')
    out = [svg_open(width, height, f'{current} in the Mailroom constellation', desc)]
    hx, hy = lay['head']
    out.append(text(hx, hy, 'THE CONSTELLATION · HOW DATA MOVES', 12 if mobile else 13, '#8b9bb4', 700, spacing=2 if mobile else 3))
    out.append('<g fill="none" stroke-width="2.2" stroke-linecap="round">')
    for eid, _, path, color, _, _ in lay['edges']:
        dash = ' stroke-dasharray="6 6"' if color in ('#fb7185', PINK) else ''
        out.append(f'<path id="{eid}" d="{path}" stroke="{color}"{dash}/>')
    out.append('</g>')
    out.append(f'<g font-size="12" fill="{MUTED}" font-style="italic">' + ''.join(
        f'<text x="{lx}" y="{ly}">{esc(label)}</text>' for _, label, _, _, lx, ly in lay['edges'] if lx) + '</g>')
    if mobile:
        out.append(f'<text x="624" y="186" font-size="12" fill="{MUTED}" font-style="italic" transform="rotate(90 624 186)">pinned pull</text>')
    out.append('<g>' + ''.join(particle(eid, 1.3 + (k % 4) * 0.25, (k % 5) * 0.3, color) for k, (eid, _, _, color, _, _)
                               in enumerate(lay['edges'])) + '</g>')
    for key, (x, y, w, h) in lay['node'].items():
        name, line1, line2, color, external = NODES[key]
        dash = ' stroke-dasharray="7 5"' if external else ''
        is_here = key == here
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{PANEL if not is_here else "#14213d"}" '
                   f'stroke="{color}" stroke-width="{2.6 if is_here else 2}"{dash}/>')
        out.append(text(x + 16, y + 30, name, 15 if w < 220 else 16, INK, 700))
        out.append(text(x + 16, y + 52, line1, 12, MUTED))
        if line2:
            out.append(text(x + 16, y + 68, line2, 12, DIM))
        if is_here:
            out.append(pulse_ring(x, y, w, h, color))
            out.append(f'<rect x="{x + 10}" y="{y - 11}" width="88" height="22" rx="11" fill="{color}"/>')
            out.append(text(x + 54, y + 4, 'YOU ARE HERE', 10, '#0b1220', 800, 'middle', spacing=1))
    lx, ly = lay['legend']
    out.append(text(lx, ly, lay['caption'], 12, DIM))
    out.append(svg_close())
    return '\n'.join(out)


# --------------------------------------------------------------------------- terminal


def terminal(title, lines, desc) -> str:
    """Animated, self-typing terminal. `lines` is [("cmd"|"note", text)]."""
    cw, fs, top, step = 9.2, 15, 92, 30
    width = 900
    height = top + step * len(lines) + 30
    parts = [(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
              f'role="img" aria-labelledby="t d" font-family="{MONO}">\n  <title id="t">{esc(title)}</title>\n'
              f'  <desc id="d">{esc(desc)}</desc>\n  <defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
              '<stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#161b2e"/></linearGradient></defs>\n'
              '  <rect id="cyc" width="1" height="1" fill="none"><animate id="loop" attributeName="x" from="0" to="0" '
              'dur="__CYCLE__s" begin="0s;loop.end"/></rect>\n'
              f'  <rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="16" fill="url(#bg)" stroke="#30363d" stroke-width="2"/>\n'
              f'  <rect x="1" y="1" width="{width - 2}" height="44" rx="16" fill="#1c2230"/>\n'
              f'  <rect x="1" y="30" width="{width - 2}" height="15" fill="#1c2230"/>\n'
              '  <circle cx="30" cy="23" r="7" fill="#ff5f57"/><circle cx="54" cy="23" r="7" fill="#febc2e"/>'
              '<circle cx="78" cy="23" r="7" fill="#28c840"/>\n'
              f'  <text x="{width // 2}" y="28" text-anchor="middle" font-size="13" fill="#8b949e">{esc(title)}</text>')]
    t = 0.6
    for i, (kind, content) in enumerate(lines):
        y = top + i * step
        prefix = '$ ' if kind == 'cmd' else '  '
        w = len(prefix + content) * cw + 24
        dur = round(len(prefix + content) * 0.04, 2) if kind == 'cmd' else 0.35
        parts.append(f'  <clipPath id="c{i}"><rect x="30" y="{y - 20}" height="28" width="0">'
                     f'<set attributeName="width" to="0" begin="loop.begin"/>'
                     f'<animate attributeName="width" from="0" to="{num(w)}" dur="{dur}s" begin="loop.begin+{num(t)}s" '
                     'fill="freeze"/></rect></clipPath>')
        if kind == 'cmd':
            parts.append(f'  <text x="32" y="{y}" font-size="{fs}" clip-path="url(#c{i})"><tspan fill="#7ee787">$ </tspan>'
                         f'<tspan fill="#e6edf3">{esc(content)}</tspan></text>')
        else:
            parts.append(f'  <text x="32" y="{y}" font-size="{fs}" clip-path="url(#c{i})" fill="#8b949e" '
                         f'font-style="italic">{esc(prefix + content)}</text>')
        t += dur + (0.9 if kind == 'cmd' else 0.5)
    cycle = t + 3
    yy = top + len(lines) * step
    parts.append(f'  <rect class="motion" x="32" y="{yy - 16}" width="9" height="19" fill="#e6edf3"><animate attributeName="opacity" '
                 'values="1;0;1" dur="1s" repeatCount="indefinite"/></rect>\n</svg>\n')
    return '\n'.join(parts).replace('__CYCLE__', num(cycle))


# --------------------------------------------------------------------------- TUI dashboard

LEVELS = {'ok': ('OK', '#7ee787'), 'info': ('INFO', '#79c0ff'), 'warn': ('WARN', '#e3b341'), 'fail': ('FAIL', '#ff7b72')}


def tui(title, gauges, log, status, desc, left='GAUGES', right='LOG') -> str:
    """Mission-control dashboard in a terminal window.

    `gauges` is [(label, value, ratio or None, color)] (a ratio draws a bar, None a status dot);
    `log` is [("ok"|"info"|"warn"|"fail", text)]; `status` is a footer of key hints.
    Static content is the final state; the scan line, sheen and spinner live in `.motion`.
    """
    width, top, row = 900, 66, 34
    rows = max(len(gauges) * row, len(log) * 28)
    height = top + 40 + rows + 64
    lx, lw, rx, rw = 24, 420, 460, 416
    py, ph = top, 40 + rows
    out = [(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'role="img" aria-labelledby="t d" font-family="{MONO}">\n  <title id="t">{esc(title)}</title>\n'
            f'  <desc id="d">{esc(desc)}</desc>\n  <defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#161b2e"/></linearGradient>'
            '<linearGradient id="sheen" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/>'
            '<stop offset=".5" stop-color="#fff" stop-opacity=".35"/><stop offset="1" stop-color="#fff" stop-opacity="0"/>'
            '</linearGradient></defs>\n'
            '  <style>@media (prefers-reduced-motion: reduce){.motion{display:none}}</style>\n'
            f'  <rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="16" fill="url(#bg)" stroke="#30363d" stroke-width="2"/>\n'
            f'  <rect x="1" y="1" width="{width - 2}" height="44" rx="16" fill="#1c2230"/>'
            f'<rect x="1" y="30" width="{width - 2}" height="15" fill="#1c2230"/>\n'
            '  <circle cx="30" cy="23" r="7" fill="#ff5f57"/><circle cx="54" cy="23" r="7" fill="#febc2e"/>'
            '<circle cx="78" cy="23" r="7" fill="#28c840"/>\n'
            f'  <text x="{width // 2}" y="28" text-anchor="middle" font-size="13" fill="#8b949e">{esc(title)}</text>')]
    for x, w, name in ((lx, lw, left), (rx, rw, right)):
        out.append(f'  <rect x="{x}" y="{py}" width="{w}" height="{ph}" rx="8" fill="none" stroke="#3b4660" stroke-width="1.5"/>')
        out.append(f'  <rect x="{x + 14}" y="{py - 9}" width="{len(name) * 9 + 16}" height="18" fill="#12182a"/>')
        out.append(text(x + 22, py + 4, name, 12, '#8b9bb4', 700, spacing=1.5))
    barx, barw = lx + 150, 170
    for i, (label, value, ratio, color) in enumerate(gauges):
        y = py + 38 + i * row
        out.append(text(lx + 16, y, label, 13, MUTED))
        if ratio is None:
            out.append(f'<circle cx="{barx + 6}" cy="{y - 4}" r="5" fill="{color}"/>')
            out.append(text(barx + 20, y, value, 13, INK, 700))
        else:
            out.append(f'<rect x="{barx}" y="{y - 13}" width="{barw}" height="14" rx="4" fill="#1f2937"/>')
            fill_w = max(4.0, barw * ratio)
            out.append(f'<rect x="{barx}" y="{y - 13}" width="{num(fill_w)}" height="14" rx="4" fill="{color}"/>')
            out.append(f'<clipPath id="g{i}"><rect x="{barx}" y="{y - 13}" width="{num(fill_w)}" height="14" rx="4"/></clipPath>')
            out.append(f'<g class="motion" clip-path="url(#g{i})"><rect x="{barx - 50}" y="{y - 13}" width="50" height="14" '
                       f'fill="url(#sheen)"><animate attributeName="x" values="{barx - 50};{num(barx + fill_w)};{num(barx + fill_w)}" '
                       f'keyTimes="0;.45;1" dur="4s" begin="{num(i * 0.35)}s" repeatCount="indefinite"/></rect></g>')
            out.append(text(barx + barw + 12, y, value, 13, INK, 700))
    for i, (level, content) in enumerate(log):
        y = py + 38 + i * 28
        tag, color = LEVELS[level]
        out.append(f'<rect x="{rx + 14}" y="{y - 14}" width="50" height="19" rx="4" fill="{color}" fill-opacity=".16"/>')
        out.append(text(rx + 39, y, tag, 11, color, 700, 'middle'))
        out.append(text(rx + 74, y, content, 13, '#c9d1d9'))
    out.append(f'<rect class="motion" x="{rx + 6}" y="{py + 22}" width="{rw - 12}" height="24" rx="5" fill="#ffffff" opacity=".07">'
               f'<animate attributeName="y" values="{py + 22};{py + 22 + (len(log) - 1) * 28};{py + 22}" dur="{max(4, len(log) * 1.2)}s" '
               'repeatCount="indefinite"/></rect>')
    sy = py + ph + 14
    out.append(f'  <rect x="24" y="{sy}" width="{width - 48}" height="30" rx="6" fill="#1c2230"/>')
    out.append(text(40, sy + 20, status, 12.5, '#8b949e'))
    out.append(f'<g class="motion" transform="translate({width - 44} {sy + 15})"><circle r="7" fill="none" stroke="#30363d" stroke-width="2.5"/>'
               '<path d="M0 -7 A7 7 0 0 1 7 0" fill="none" stroke="#7ee787" stroke-width="2.5" stroke-linecap="round">'
               '<animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="1.1s" repeatCount="indefinite"/></path></g>')
    out.append('</svg>\n')
    return '\n'.join(out)


# --------------------------------------------------------------------------- scene helpers


def check_mark(x, y, color=GREEN) -> str:
    """A drawn tick (no glyph dependency)."""
    return (f'<path d="M{x} {y} l7 8 l14 -17" fill="none" stroke="{color}" stroke-width="3.5" '
            'stroke-linecap="round" stroke-linejoin="round"/>')


def cross_mark(x, y, color='#fb7185') -> str:
    """A drawn cross (no glyph dependency)."""
    return (f'<path d="M{x} {y - 8} l16 16 M{x + 16} {y - 8} l-16 16" fill="none" stroke="{color}" '
            'stroke-width="3.5" stroke-linecap="round"/>')


def chip(x, y, w, h, label, color, mono=True, size=13) -> str:
    """A small rounded label chip."""
    return (f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" rx="8" fill="{PANEL}" '
            f'stroke="{color}" stroke-opacity=".8"/>'
            + text(x + w / 2, y + h / 2 + size * 0.36, label, size, INK, 600, 'middle',
                   family=MONO if mono else None))
