"""Section-aware excerpts of long agreements (merger agreements average about 340k characters).

The labeling window used to take the first heading-like regex hit for each field; on the 151
v9.4 mergers it found the Conversion of Shares section in 23 and the Outside Date in 25, and
often landed on the definitions article (2026-11-06). This module finds the numbered headings,
skips table-of-contents entries and later exhibit copies, and returns each needed section
whole, plus the definitions of the terms the fields depend on (a fee amount may be stated only
in the definitions article).
"""
from __future__ import annotations

import re
from typing import NamedTuple, Sequence

#: Bump when the locator changes: it is hashed into the merger prompt shas.
VERSION = 5
#: Minimum text between a heading and the next one for it to be a real section: a TOC entry
#: is followed by the next entry within a line or two.
MIN_SECTION_CHARS = 80
SECTION_CAP = 11_000
DEFINITION_CAP = 1_500

#: A heading at a line start (the number may stand alone) ...
_LINE_SECTION = re.compile(
    r"(?m)^[^\S\n]*(?:(?:Section|SECTION|Sec\.|§)\s*)?(\d{1,2}\.\d{1,3}[A-Za-z]?)\.?[^\S\n]+"
    r"([A-Z][^\n]{0,150}?)(?:\.(?=\s)|$|[^\S\n]{2,})")
#: ... or anywhere, in text whose line breaks were lost: then it needs the word Section and a
#: title that ends in a period, as body headings do (cross-references and TOC entries do not).
_INLINE_SECTION = re.compile(r"(?:Section|SECTION|§)\s*(\d{1,2}\.\d{1,3}[A-Za-z]?)\.?\s+([A-Z][^.\n]{0,120}?)\.(?=\s)")
#: ... or a bare number after a sentence end or a wide gap ("... security. 9.9 Governing Law.
#: This Agreement ..."): the title must be title case and end in a period.
_TITLE_WORD = r"(?:[A-Z][\w'’&/-]*|and|or|of|the|to|for|in|on|by|with|a|an|no|not|upon)"
_BARE_INLINE = re.compile(rf"(?:[.;:)][^\S\n]+|[^\S\n]{{3,}})(\d{{1,2}}\.\d{{1,3}}[A-Za-z]?)\.?[^\S\n]+"
                          rf"([A-Z][\w'’&/-]*(?:[,;]?[^\S\n]+{_TITLE_WORD}){{0,12}})\.(?=\s)")
_ARTICLE = re.compile(r"ARTICLE\s+([IVXLC]+|\d{1,2})\b\.?[^\S\n]*([^\n]{0,150})")
#: A table-of-contents entry: the title is followed by a page number.
_PAGE_NUMBER = re.compile(r"[^\S\n]*\d{1,3}(?:\s|$)")
_QUOTE = "[\"“”']"


class Heading(NamedTuple):
    start: int
    number: str
    title: str


class Need(NamedTuple):
    """One thing a field needs: heading titles to try in priority order, the defined terms
    to include, and optionally an inline-definition pattern whose enclosing section counts."""
    label: str
    titles: tuple[str, ...]
    terms: tuple[str, ...] = ()
    inline: str | None = None
    #: the clause around the inline match is the excerpt (no enclosing section): for
    #: lettered representations ("(u) Brokers or Finders.") inside a long numbered section
    clause_only: bool = False


def _all_headings(text: str) -> list[Heading]:
    found: dict[int, Heading] = {}
    for rx in (_LINE_SECTION, _INLINE_SECTION, _BARE_INLINE):
        for m in rx.finditer(text):
            title = re.split(r"\s+(?:Section|SECTION)\s", m.group(2))[0].strip()
            if _PAGE_NUMBER.match(text, m.end()) or re.search(r"\s\d{1,3}$", title):
                continue  # a table-of-contents entry
            start = m.start(1) if rx is _BARE_INLINE else m.start()
            if not any(abs(start - k) < 12 for k in found):  # one heading, matched twice
                found[start] = Heading(start, m.group(1), title)
    for m in _ARTICLE.finditer(text):
        found.setdefault(m.start(), Heading(m.start(), f"ARTICLE {m.group(1)}", m.group(2).strip()))
    return sorted(found.values())


def headings(text: str) -> list[Heading]:
    """The operative headings: per section number, the earliest occurrence followed by more
    than ``MIN_SECTION_CHARS`` of text before the next heading."""
    every = _all_headings(text)
    ends = [h.start for h in every[1:]] + [len(text)]
    kept: dict[str, Heading] = {}
    for h, end in zip(every, ends):
        if end - h.start > MIN_SECTION_CHARS and h.number not in kept:
            kept[h.number] = h
    return sorted(kept.values())


def section_text(text: str, heading: Heading, cap: int = SECTION_CAP) -> str:
    """From the heading to the next heading (of any level), at most ``cap`` characters."""
    nxt = next((h.start for h in _all_headings(text) if h.start > heading.start), len(text))
    return text[heading.start:min(nxt, heading.start + cap)]


def definition(text: str, term: str) -> str | None:
    """The paragraph that defines ``term``, at most ``DEFINITION_CAP`` characters."""
    m = re.search(rf"{_QUOTE}{re.escape(term)}{_QUOTE}\s*(?:means|shall\s+mean|has\s+the\s+meaning|"
                  rf"shall\s+have\s+the\s+meaning|shall\s+be|shall\s+equal)", text, re.I)
    if m is None:
        return None
    # the defining line, but never more than a little before the term: flattened text can
    # run a whole article on one "line", and the value follows the term
    start = max(text.rfind("\n", 0, m.start()) + 1, m.start() - 200)
    end = text.find("\n", m.end())
    end = len(text) if end < 0 else end
    return text[start:min(end, start + DEFINITION_CAP)]


INLINE_BEFORE, INLINE_AFTER = 500, 300
CLAUSE_BEFORE, CLAUSE_AFTER = 300, 1_500


def _inline(text: str, need: Need) -> re.Match[str] | None:
    """The first match of the need's inline pattern outside the recitals (a WHEREAS clause
    restates the deal, it does not operate it)."""
    if not need.inline:
        return None
    for m in re.finditer(need.inline, text, re.I):
        if "WHEREAS" not in text[max(0, m.start() - 600):m.start()]:
            return m
    return None


def _find(text: str, heads: Sequence[Heading], need: Need) -> Heading | None:
    sections = [h for h in heads if not h.number.startswith("ARTICLE")]
    m = _inline(text, need)
    if m:  # the operative inline definition beats any heading title
        hit = max((h for h in sections if h.start <= m.start()), default=None, key=lambda h: h.start)
        if hit:
            return hit
    for pat in need.titles:
        hit = next((h for h in sections if re.search(pat, h.title, re.I)), None)
        if hit:
            return hit
    return None


def locate(text: str, needs: Sequence[Need]) -> list[tuple[str, str]]:
    """(label, excerpt) for each need found: its section whole (capped) and its definitions."""
    heads = headings(text)
    out = []
    for need in needs:
        m = _inline(text, need)
        if need.clause_only and m:
            parts = ["[clause] " + text[max(0, m.start() - CLAUSE_BEFORE):m.end() + CLAUSE_AFTER]]
            out.append((need.label, "\n".join(parts)))
            continue
        h = _find(text, heads, need)
        parts = [section_text(text, h)] if h else []
        if m and not (h and h.start <= m.start() and m.end() <= h.start + len(parts[0])):
            # the defining clause itself, when the section excerpt does not reach it
            parts.append("[clause] " + text[max(0, m.start() - INLINE_BEFORE):m.end() + INLINE_AFTER])
        parts += [f"[definition] {d}" for d in (definition(text, t) for t in need.terms) if d]
        if parts:
            out.append((need.label, "\n".join(parts)))
    return out


#: where the fee is actually set: "a fee equal to $11,000,000 (the “Termination Fee”)" or
#: "a termination fee of $30,000,000"
_FEE_INLINE = (rf"\$\s?[\d,.]+[^()]{{0,40}}\(\s*(?:the\s+)?{_QUOTE}?\s*(?:company\s+|parent\s+|buyer\s+)?"
               rf"termination\s+(?:fee|payment)\s*{_QUOTE}?\s*\)"
               r"|termination\s+fee\s+(?:of|equal\s+to|in\s+the\s+amount\s+of)\s+\$")
#: a broker representation, wherever it is headed: "(u) Brokers or Finders. Neither the Company
#: ... has employed any broker", "no broker, finder or investment banker is entitled"
_BROKER_INLINE = (r"(?:has|have)\s+(?:not\s+)?employed\s+any\s+(?:investment\s+banker,?\s+)?broker"
                  r"|\bno\s+(?:investment\s+banker,?\s+)?broker\b|brokerage\s+(?:fees?|commissions?)")
#: the operative conversion clause: "each share ... issued and outstanding immediately prior to
#: the Effective Time ... shall be [cancelled and] converted into the right to receive ..."
_CONVERSION_INLINE = (r"issued\s+and\s+outstanding\s+(?:as\s+of\s+)?immediately\s+prior\s+to\s+the\s+[^.;]{0,40}?"
                      r"effective\s+time[^;]{0,500}?(?:shall|will)\s+(?:[a-z,]+\s+){0,6}?converted")
_DATE_TERM = rf"{_QUOTE}\s*(?:outside|end|termination|drop[- ]dead)\s+date\s*{_QUOTE}"  # quoted: a definition, not a cross-reference
MERGER_NEEDS: tuple[Need, ...] = (
    Need("Effective Time", (r"effective\s+time", r"closing"), ("Effective Time",),
         inline=rf"\(\s*(?:such\s+[^()]{{0,80}}?\s)?(?:the\s+)?{_QUOTE}?\s*effective\s+time\s*{_QUOTE}?\s*\)"),
    Need("Governing Law", (r"governing\s+law", r"applicable\s+law", r"choice\s+of\s+law")),
    Need("Conversion of Shares",
         (r"effects?\s+(of\s+the\s+mergers?\s+)?on\s+(the\s+)?(capital\s+stock|securities|shares|equity)",
          r"conversion\s+of\s+(capital\s+stock|shares|securities|company)", r"merger\s+consideration",
          r"effect\s+on\s+capital"),
         ("Merger Consideration", "Per Share Merger Consideration", "Exchange Ratio"),
         inline=_CONVERSION_INLINE),
    Need("Termination Fee",
         (r"termination\s+fees?", r"fees\s+following\s+termination", r"termination\s+payments?",
          r"fees\s+and\s+expenses", r"expenses", r"effect\s+of\s+termination"),
         ("Termination Fee", "Company Termination Fee", "Parent Termination Fee"),
         inline=_FEE_INLINE),
    Need("Outside Date", (r"^termination\b(?!\s+fee)",), ("Outside Date", "End Date", "Termination Date"),
         inline=rf"\(\s*(?:[^()]{{0,120}}?\s)?(?:the\s+)?{_DATE_TERM}\s*\)"),
    Need("Brokers", (r"brokers?", r"finders?", r"financial\s+advisors?", r"advisors?[’']?\s+fees"),
         inline=_BROKER_INLINE, clause_only=True),
    Need("Notices", (r"notices?",)),
)
CONSIDERATION_NEEDS: tuple[Need, ...] = (
    MERGER_NEEDS[2],
    Need("Merger Consideration", (r"merger\s+consideration", r"consideration"),
         ("Cash Consideration", "Stock Consideration", "Per Share Cash Consideration")),
    Need("Election", (r"election", r"proration", r"allocation")),
)
