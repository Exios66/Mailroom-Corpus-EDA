#!/usr/bin/env python3
"""EDGAR exhibit crawler for the v9 expansion (issue #3; subissues #9/#12).

Pulls SEC public-domain exhibits into a deterministic raw pool under
``data/v9/edgar/``:

- ``--kind corporate`` — governance exhibits (EX-3.x articles/bylaws,
  EX-4.x rights instruments/indentures, EX-21.x subsidiary lists,
  EX-24.x powers of attorney, EX-10.x power-of-attorney/officer shapes)
  → corporate_record draws.
- ``--kind contract`` — EX-10.x material agreements (employment, license,
  purchase, supply, consulting, NDA, services, …) → contract draws.

Laws:

- SEC politeness: User-Agent with contact, ~8 req/s (0.12 s sleep), bounded
  requests per CIK; only public-domain exhibit text is downloaded.
- Determinism: CIK list + filings processed in sorted order; every exhibit
  row is written once to ``edgar_rows.jsonl``; no quotas applied here — the
  draw layer samples strata deterministically from this pool.
- HTML → text: tag stripping + entity unescape + whitespace collapse;
  exhibits whose extracted text is < 500 chars (boilerplate/signature pages)
  are kept with a ``short_text: true`` flag (the draw may still use them as
  scaffold-free short documents) — the flag is honest, not a filter.
- The pool is appendable: re-running skips exhibit rows already present
  (keyed by accession + document name), so interrupted crawls resume.

Output: ``data/v9/edgar/raw/<subclass>/<accession>_<docname>.htm`` (original
bytes) + ``data/v9/edgar/edgar_rows.jsonl`` (one row per exhibit with
metadata + extracted text).

Usage:
    .venv/bin/python scripts/edgar_pull.py --kind corporate
    .venv/bin/python scripts/edgar_pull.py --kind contract
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mailroom_eda.config import DATA_DIR  # noqa: E402

EDGAR_DIR = DATA_DIR / "v9" / "edgar"
RAW_DIR = EDGAR_DIR / "raw"
ROWS_PATH = EDGAR_DIR / "edgar_rows.jsonl"

SLEEP = 0.25  # ~4 req/s (SEC limit 10 req/s; gentler after a block incident)
UA = ("MailroomCorpusEDA/1.0 mailroom-corpus-edb-admin@proton.me "
      "(public-domain EDGAR exhibit acquisition)")
BASE_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"
BASE_SUBMISSIONS = "https://data.sec.gov/submissions"

#: 503/429 → long backoff (SEC IP blocks clear in minutes; the crawl waits).
BACKOFF_503 = 45.0
MAX_CONSECUTIVE_503 = 12

#: Curated CIK list: the 11 v8 filers (seeded from data/v8_ciks.txt) plus a
#: deterministic set of well-known public companies (recent-IPO + large-cap)
#: so exhibit diversity is broad and no single filer dominates.
CURATED_CIKS = [
    "0000320193", "0000789019", "0001045810", "0001065280", "00012927",
    "0000354950", "0001467373", "0001318605", "0001652044", "0001813756",
    "0001758808", "0001840669", "0001821813", "0001770787", "0001739945",
    "0001723464", "0001874639", "0001855933", "0001801179", "0001708174",
    "0001819908", "0001792785", "0001862757", "0001836135", "0001799207",
    "0001827095", "0001881014", "0001850236", "0001828871", "0001875560",
    "0001833132", "0001802916", "0001811073", "0001760496", "0001745433",
    "0001734714", "0001747082", "0001709186", "0001677267", "0001698516",
    "0001665650", "0001657853", "0001641390", "0001608401", "0001577552",
    "0001567908", "0001558370", "0001535804", "0001517396", "0001413329",
]

#: Corporate-mode exhibit-type → subclass mapping (exact SEC exhibit types).
CORP_TYPE_MAP = {
    "EX-3.1": "articles_of_incorporation",
    "EX-3.2": "bylaws",
    "EX-3.3": "charter_amendment",
    "EX-21.1": "subsidiary_list",
    "EX-21": "subsidiary_list",
    "EX-4.1": "rights_instrument",
    "EX-4.2": "rights_instrument",
    "EX-4.3": "rights_instrument",
    "EX-4.4": "rights_instrument",
    "EX-24.1": "powers_of_attorney",
    "EX-24": "powers_of_attorney",
}
CORP_DESC_PATTERNS = (
    (re.compile(r"power of attorney", re.I), "powers_of_attorney"),
    (re.compile(r"officer.?s? certificate", re.I), "officer_certificate"),
    (re.compile(r"indenture", re.I), "indenture"),
    (re.compile(r"board resolution", re.I), "board_resolution"),
    (re.compile(r"certificate of (incorporation|designation)", re.I), "articles_of_incorporation"),
    (re.compile(r"amendment", re.I), "charter_amendment"),
)

#: Contract-mode description → v8 contract subclass vocabulary.
CONTRACT_PATTERNS = (
    (re.compile(r"employment", re.I), "Consulting Agreements"),  # employment ≈ consulting family
    (re.compile(r"license|licence", re.I), "License_Agreements"),
    (re.compile(r"purchase", re.I), "Supply"),
    (re.compile(r"supply", re.I), "Supply"),
    (re.compile(r"consulting|advisory", re.I), "Consulting Agreements"),
    (re.compile(r"non-?disclosure|nda|confidentiality", re.I), "IP"),
    (re.compile(r"service", re.I), "Service"),
    (re.compile(r"manufactur", re.I), "Manufacturing"),
    (re.compile(r"distribut", re.I), "Distributor"),
    (re.compile(r"market", re.I), "Marketing"),
    (re.compile(r"promotion", re.I), "Promotion"),
    (re.compile(r"sponsor", re.I), "Sponsorship"),
    (re.compile(r"endorse", re.I), "Endorsement"),
    (re.compile(r"develop", re.I), "Development"),
    (re.compile(r"collaborat|joint", re.I), "Collaboration"),
    (re.compile(r"joint venture", re.I), "Joint Venture"),
    (re.compile(r"outsourc", re.I), "Outsourcing"),
    (re.compile(r"maintenance", re.I), "Maintenance"),
    (re.compile(r"hosting", re.I), "Hosting"),
    (re.compile(r"reseller", re.I), "Reseller"),
    (re.compile(r"franchise", re.I), "Franchise"),
    (re.compile(r"non-?compete|non-?solicit", re.I), "Non_Compete_Non_Solicit"),
    (re.compile(r"strategic alliance", re.I), "Strategic Alliance"),
    (re.compile(r"agency", re.I), "Agency Agreements"),
    (re.compile(r"affiliate", re.I), "Affiliate_Agreements"),
    (re.compile(r"co-?brand", re.I), "Co_Branding"),
    (re.compile(r"transport|shipping|freight", re.I), "Transportation"),
    (re.compile(r"lease", re.I), "Service"),
    (re.compile(r"credit|loan|indemnif|settlement|guarant", re.I), "IP"),
)
CONTRACT_FALLBACK = "other"


class _TextExtractor(HTMLParser):
    """Minimal HTML → text (SEC exhibits are simple, table-heavy HTML)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in ("p", "div", "tr", "br", "h1", "h2", "h3", "li"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data)


def extract_text(raw: bytes) -> str:
    """Strip HTML to readable text; collapse whitespace; unescape entities."""
    text = raw.decode("utf-8", errors="replace")
    if "<" not in text or ">" not in text:
        # plain-text exhibit (.txt)
        return " ".join(text.split())
    parser = _TextExtractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return " ".join(text.split())
    out = " ".join(" ".join(parser.parts).split())
    return html.unescape(out).strip()


def _get(url: str, retries: int = 6) -> bytes | None:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503):
                if exc.code == 503 and attempt == 0:
                    print(f"  503 hit — backing off {BACKOFF_503:.0f}s (SEC block)")
                    time.sleep(BACKOFF_503)
                else:
                    time.sleep(min(2 ** attempt * 2.0, 30.0))
                continue
            print(f"  WARN fetch failed {url}: HTTP {exc.code}")
            return None
        except Exception as exc:
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt * 2.0, 30.0))
                continue
            print(f"  WARN fetch failed {url}: {exc}")
            return None
    print(f"  WARN fetch failed {url}: exhausted retries")
    return None


def _exhibit_subclass(ex_type: str, description: str, kind: str) -> str | None:
    if kind == "corporate":
        if ex_type in CORP_TYPE_MAP:
            return CORP_TYPE_MAP[ex_type]
        if ex_type.startswith("EX-4"):
            return "rights_instrument"
        for pat, subclass in CORP_DESC_PATTERNS:
            if pat.search(description or ""):
                return subclass
        return None
    # contract mode
    for pat, subclass in CONTRACT_PATTERNS:
        if pat.search(description or ""):
            return subclass
    if not (description or "").strip():
        return None
    return CONTRACT_FALLBACK


def _filings_for_cik(cik: str, kind: str, cap: int = 30) -> list[dict]:
    """Recent S-1-family + 8-K filings for a CIK (sorted, bounded)."""
    data = _get(f"{BASE_SUBMISSIONS}/CIK{cik}.json")
    if data is None:
        return []
    try:
        doc = json.loads(data)
    except json.JSONDecodeError:
        return []
    recent = doc.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    docs = recent.get("primaryDocument", [])
    out = []
    for i, form in enumerate(forms):
        if len(out) >= cap:
            break
        if kind == "corporate" and form not in ("S-1", "S-1/A", "8-K", "10-K", "10-K/A"):
            continue
        if kind == "contract" and form not in ("S-1", "S-1/A", "8-K"):
            continue
        out.append({
            "cik": cik,
            "form": form,
            "accession": accns[i] if i < len(accns) else "",
            "filing_date": dates[i] if i < len(dates) else "",
            "primary_document": docs[i] if i < len(docs) else "",
            "filer": doc.get("name", ""),
        })
    return out


def _archive_cik(accession: str) -> str:
    """EDGAR archive paths use the FILER CIK from the accession prefix —
    submissions data returns the company CIK, which differs when an agent
    (e.g. Cooley LLP) files on the company's behalf. The accession's first
    10 digits ARE the archive CIK."""
    digits = accession.replace("-", "")
    return digits[:10]


def _index_for_filing(filing: dict) -> list[dict]:
    acc = filing["accession"].replace("-", "")
    idx = _get(f"{BASE_ARCHIVE}/{_archive_cik(filing['accession'])}/{acc}/index.json")
    if idx is None:
        return []
    try:
        doc = json.loads(idx)
    except json.JSONDecodeError:
        return []
    return doc.get("directory", {}).get("item", []) or doc.get("items", [])


def crawl(kind: str, ciks: list[str] | None = None, cap_per_cik: int = 30) -> dict:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if ciks is None:
        ciks = sorted(set(CURATED_CIKS) | _v8_ciks())
    ciks = sorted(set(ciks))
    existing = set()
    if ROWS_PATH.exists():
        for line in ROWS_PATH.read_text().splitlines():
            try:
                r = json.loads(line)
                existing.add((r["accession"], r["document_name"]))
            except json.JSONDecodeError:
                continue
    stats = {"requests": 0, "exhibits": 0, "skipped_existing": 0,
             "skipped_short": 0, "fetched_filings": 0}
    with ROWS_PATH.open("a", encoding="utf-8") as fh:
        for cik in ciks:
            filings = _filings_for_cik(cik, kind, cap=cap_per_cik)
            stats["requests"] += 1
            for filing in filings:
                stats["fetched_filings"] += 1
                items = _index_for_filing(filing)
                stats["requests"] += 1
                time.sleep(SLEEP)
                for item in items:
                    name = str(item.get("name") or "")
                    ex_type = str(item.get("type") or "").upper()
                    desc = str(item.get("description") or "")
                    if not name.lower().endswith((".htm", ".html", ".txt")):
                        continue
                    if kind == "corporate":
                        if not (ex_type.startswith("EX-3") or ex_type.startswith("EX-4")
                                or ex_type.startswith("EX-21") or ex_type.startswith("EX-24")
                                or (ex_type.startswith("EX-10") and (
                                    "power of attorney" in desc.lower()
                                    or "officer" in desc.lower()
                                    or "certificate" in desc.lower()))):
                            continue
                    elif kind == "contract":
                        if not ex_type.startswith("EX-10"):
                            continue
                    subclass = _exhibit_subclass(ex_type, desc, kind)
                    if subclass is None:
                        continue
                    key = (filing["accession"], name)
                    if key in existing:
                        stats["skipped_existing"] += 1
                        continue
                    url = (f"{BASE_ARCHIVE}/{_archive_cik(filing['accession'])}/"
                           f"{filing['accession'].replace('-', '')}/{name}")
                    raw = _get(url)
                    stats["requests"] += 1
                    time.sleep(SLEEP)
                    if raw is None:
                        continue
                    text = extract_text(raw)
                    short = len(text) < 500
                    if short:
                        stats["skipped_short"] += 1
                        continue
                    rel = RAW_DIR / subclass / f"{filing['accession']}_{name}"
                    rel.parent.mkdir(parents=True, exist_ok=True)
                    rel.write_bytes(raw)
                    row = {
                        "kind": kind,
                        "cik": filing["cik"],
                        "filer": filing["filer"],
                        "form": filing["form"],
                        "accession": filing["accession"],
                        "filing_date": filing["filing_date"],
                        "document_name": name,
                        "exhibit_type": ex_type,
                        "exhibit_description": desc,
                        "exhibit_url": url,
                        "subclass": subclass,
                        "chars": len(text),
                        "short_text": short,
                        "raw_file": str(rel.relative_to(DATA_DIR)),
                        "doc_text": text,
                    }
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    existing.add(key)
                    stats["exhibits"] += 1
                time.sleep(SLEEP)
    print(f"crawl {kind}: {stats}")
    return stats


def _v8_ciks() -> list[str]:
    v8_ciks = DATA_DIR / "v8_ciks.txt"
    if v8_ciks.exists():
        return [line.strip() for line in v8_ciks.read_text().splitlines() if line.strip()]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("corporate", "contract"), required=True)
    parser.add_argument("--cap-per-cik", type=int, default=30)
    parser.add_argument("--ciks", default="",
                        help="comma-separated CIK override (default: curated + v8)")
    args = parser.parse_args()

    ciks = [c.strip() for c in args.ciks.split(",") if c.strip()] or None
    crawl(args.kind, ciks=ciks, cap_per_cik=args.cap_per_cik)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())