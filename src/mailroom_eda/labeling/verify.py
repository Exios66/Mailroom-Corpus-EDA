"""Evidence checks, per-type normalisation and two-model agreement.

A field value is accepted only when all of these hold:

1. **Evidence is verbatim.** The quoted evidence occurs in ``doc_text``, allowing only
   whitespace differences and dropped markdown marks (``*``, ``_``, ``#``).
2. **The value is grounded.** For names, ids, money, dates and name lists, the value's
   tokens occur in the document. Free text must share most of its tokens with the
   document. Labels must be in their vocabulary.
3. **Two models agree** after normalising for the field's Dojo scoring type, or one
   model's value is accepted after a human verdict in the review CSV.

``decide`` applies the field's absence policy and returns one of ``accept``,
``absent``, ``unlabeled`` or ``adjudicate``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

STATUSES = ("present", "documented_absent", "not_found")
_SKIP = "*_#"
_TOKEN = re.compile(r"[a-z0-9]+")
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], start=1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9


def fold(text: str) -> tuple[str, list[int]]:
    """Lower-case, collapse whitespace, drop markdown marks; return the folded text and
    the original index of every kept character."""
    chars: list[str] = []
    idx: list[int] = []
    pending: int | None = None
    for i, ch in enumerate(text):
        if ch in _SKIP:
            continue
        if ch.isspace():
            if pending is None and chars:
                pending = i
            continue
        if pending is not None:
            chars.append(" ")
            idx.append(pending)
            pending = None
        chars.append(ch.lower())
        idx.append(i)
    return "".join(chars), idx


def locate(doc: str, quote: str, folded: tuple[str, list[int]] | None = None) -> tuple[int, int] | None:
    """Character span of ``quote`` in ``doc``: exact first, then whitespace/markdown folded.

    Returns ``(start, end)`` offsets into the original ``doc``, or ``None``.
    """
    if not quote:
        return None
    pos = doc.find(quote)
    if pos >= 0:
        return pos, pos + len(quote)
    fdoc, idx = folded or fold(doc)
    fq, _ = fold(quote)
    if not fq:
        return None
    pos = fdoc.find(fq)
    if pos < 0:
        return None
    return idx[pos], idx[pos + len(fq) - 1] + 1


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class DocIndex:
    """Precomputed views of one document for repeated checks."""

    text: str
    folded: tuple[str, list[int]]
    tokens: frozenset[str]
    numbers: frozenset[str]

    @classmethod
    def build(cls, text: str) -> "DocIndex":
        """Index ``text``: folded form, token set, and digit strings with separators removed."""
        nums = frozenset(n.replace(",", "").split(".")[0] for n in _NUMBER.findall(text))
        return cls(text, fold(text), frozenset(tokens(text)), nums)


def tokens(text: Any) -> list[str]:
    """Lower-case alphanumeric tokens."""
    return _TOKEN.findall(str(text or "").lower())


def parse_date(value: Any) -> date | None:
    """Parse ISO ``YYYY-MM-DD`` or common written dates ("March 5, 2004", "5 March 2004")."""
    s = str(value or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    t = tokens(s.replace(",", " "))
    nums = [x for x in t if x.isdigit()]
    month = next((_MONTHS[x] for x in t if x in _MONTHS), None)
    year = next((int(x) for x in nums if len(x) == 4), None)
    day = next((int(x.rstrip("stndrh")) for x in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", s)), None)
    if month and year and day:
        try:
            return date(year, month, day)
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        try:
            return date(int(m[3]), int(m[1]), int(m[2]))
        except ValueError:
            return None
    return None


def parse_money(value: Any) -> float | None:
    """Parse "$1,250,000", "1.5 million", "USD 400" or a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(million|billion|thousand|m\b|bn\b|b\b|k\b)?", s)
    if not m:
        return None
    n = float(m[1])
    mult = {"million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9, "thousand": 1e3,
            "k": 1e3}.get((m[2] or "").strip(), 1.0)
    return n * mult


def normalize(value: Any, type_: str) -> Any:
    """Comparable form of a value for its Dojo scoring type."""
    if value is None or value == "" or value == []:
        return None
    if type_.startswith("entity_list"):
        items = value if isinstance(value, list) else [value]
        inner = type_.split(":", 1)[1] if ":" in type_ else "name"
        return frozenset(x for x in (normalize(i, inner) for i in items) if x)
    if type_ == "date":
        d = parse_date(value)
        return d.isoformat() if d else " ".join(tokens(value))
    if type_ == "money":
        return parse_money(value)
    if type_ == "label":
        return re.sub(r"[^a-z0-9]", "", str(value).lower())
    if type_ == "id":
        return re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    if type_ == "free_text":
        return frozenset(tokens(value))
    return " ".join(tokens(value))  # name


def _jaccard(a: frozenset, b: frozenset) -> float:
    """Set overlap."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def agree(a: Any, b: Any, type_: str) -> bool:
    """Do two values match for ``type_``? Lists need Jaccard >= 0.8 over normalised items;
    free text needs token Jaccard >= 0.6; names match exactly after normalisation or when
    one token set contains the other; other scalars must be equal."""
    na, nb = normalize(a, type_), normalize(b, type_)
    if na is None or nb is None:
        return na is None and nb is None
    if type_.startswith("entity_list"):
        return _jaccard(na, nb) >= 0.8
    if type_ == "free_text":
        return _jaccard(na, nb) >= 0.6
    if type_ == "name":
        if na == nb:
            return True
        ta, tb = set(na.split()), set(nb.split())
        return bool(ta and tb) and (ta <= tb or tb <= ta) and _jaccard(frozenset(ta), frozenset(tb)) >= 0.5
    if type_ == "money":
        return abs(na - nb) < 0.005
    return na == nb


def grounded(value: Any, type_: str, doc: DocIndex, *, vocab: Sequence[str] | None = None) -> bool:
    """Is ``value`` supported by the document (or by its vocabulary, for labels)?"""
    if value is None or value == "" or value == []:
        return False
    if type_ == "label":
        return vocab is None or normalize(value, "label") in {normalize(v, "label") for v in vocab}
    if type_.startswith("entity_list"):
        items = value if isinstance(value, list) else [value]
        inner = type_.split(":", 1)[1] if ":" in type_ else "name"
        return all(grounded(i, inner, doc) for i in items)
    if type_ == "date":
        d = parse_date(value)
        if d is None:
            return set(tokens(value)) <= doc.tokens
        months = {k for k, v in _MONTHS.items() if v == d.month}
        month_ok = bool(months & doc.tokens) or f"{d.month:02d}" in doc.tokens or str(d.month) in doc.tokens
        return str(d.year) in doc.tokens and month_ok
    if type_ == "money":
        digits = [n.replace(",", "").split(".")[0] for n in _NUMBER.findall(str(value))]
        if not digits:
            return False
        if re.search(r"million|billion|thousand", str(value), re.I):
            return all(d in doc.numbers or d in doc.tokens for d in digits)
        return all(d in doc.numbers for d in digits)
    toks = tokens(value)
    if not toks:
        return False
    hit = sum(t in doc.tokens for t in toks) / len(toks)
    return hit >= (0.6 if type_ == "free_text" else 1.0)


@dataclass(frozen=True)
class FieldResult:
    """One model's answer for one field, after checks."""

    value: Any
    status: str
    evidence: str
    verbatim_ok: bool
    grounded_ok: bool

    @property
    def usable(self) -> bool:
        """A present value that passed both checks."""
        return self.status == "present" and self.verbatim_ok and self.grounded_ok


def check_field(raw: Mapping[str, Any] | None, type_: str, doc: DocIndex, *,
                vocab: Sequence[str] | None = None) -> FieldResult:
    """Validate one model's ``{"value", "status", "evidence"}`` object against ``doc``."""
    raw = raw if isinstance(raw, Mapping) else {}
    status = raw.get("status") if raw.get("status") in STATUSES else "not_found"
    value = raw.get("value") if status == "present" else None
    evidence = str(raw.get("evidence") or "")
    if status == "not_found":
        verbatim = True
    else:
        verbatim = bool(evidence) and locate(doc.text, evidence, doc.folded) is not None
    ground = status != "present" or grounded(value, type_, doc, vocab=vocab)
    return FieldResult(value, status, evidence, verbatim, ground)


def decide(a: FieldResult, b: FieldResult | None, type_: str, absence: str) -> tuple[str, Any, str]:
    """Combine one or two model results under the field's absence policy.

    Returns ``(decision, value, reason)``, where decision is ``accept`` (use ``value``),
    ``absent`` (code ``schema_documented_absence``), ``unlabeled`` (leave the key absent)
    or ``adjudicate`` (send to the human review CSV).
    """
    if b is None:  # single-model task: only pilots, and the human verdict decides
        return ("adjudicate", a.value, "single model") if a.usable else ("unlabeled", None, "no usable value")
    if a.usable and b.usable:
        if agree(a.value, b.value, type_):
            return "accept", a.value, "both models agree"
        return "adjudicate", None, "models disagree"
    if a.status == b.status == "documented_absent" and a.verbatim_ok and b.verbatim_ok:
        return "absent", None, "both quote an absence statement"
    if a.status == b.status == "not_found":
        if absence == "absent_ok":
            return "absent", None, "both models find nothing (absent_ok)"
        if absence == "never_absent":
            return "adjudicate", None, "never_absent field with no value"
        return "unlabeled", None, "both models find nothing (evidence_only)"
    if a.usable or b.usable:
        return "adjudicate", None, "only one model has a usable value"
    return "adjudicate", None, "failed evidence checks"
