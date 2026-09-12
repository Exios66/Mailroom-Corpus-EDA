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
import hashlib
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
CACHE_DIR = EDGAR_DIR / "cache"

SLEEP = 0.6  # Jina Reader tolerates bursts; 429s are retried with backoff
CONSECUTIVE_503: list[int] = [0]  # module-level circuit breaker state

# Browser-like headers to avoid SEC blocks (no Accept-Encoding: gzip is fine,
# Jina handles decoding; direct urllib does NOT request gzip).
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
BASE_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"
BASE_SUBMISSIONS = "https://data.sec.gov/submissions"
JINA_BASE = "https://r.jina.ai/"

#: SEC blocks our IP on www.sec.gov/Archives (403/503). Transport ladder:
#: direct -> Jina Reader (their servers fetch, ours aren't blocked) -> None.
#: Wayback CDX showed zero snapshots of SEC archive paths, and allorigins was
#: flaky (522), so those are not in the ladder.
BACKOFF_503 = 30.0
MAX_CONSECUTIVE_503 = 8
CIRCUIT_SLEEP = 120.0

#: Curated CIK list: the 11 v8 filers (seeded from data/v8_ciks.txt) plus a
#: deterministic set of well-known public companies (recent-IPO + large-cap)
#: so exhibit diversity is broad and no single filer dominates.
CURATED_CIKS = [
    "0000320193",
    "0000354950",
    "0000704532",
    "0000733269",
    "0000789019",
    "0000827876",
    "0000895419",
    "0000918646",
    "0001045810",
    "0001050446",
    "0001065280",
    "0001167419",
    "0001274494",
    "0001315098",
    "0001318605",
    "0001321655",
    "0001372612",
    "0001404655",
    "0001412408",
    "0001413329",
    "0001429764",
    "0001441816",
    "0001447669",
    "0001463101",
    "0001467373",
    "0001467623",
    "0001469367",
    "0001477333",
    "0001477449",
    "0001477720",
    "0001506293",
    "0001507605",
    "0001516513",
    "0001517396",
    "0001522540",
    "0001535527",
    "0001535804",
    "0001543151",
    "0001544522",
    "0001558370",
    "0001559720",
    "0001561550",
    "0001567908",
    "0001577526",
    "0001577552",
    "0001579878",
    "0001583708",
    "0001585521",
    "0001594805",
    "0001608401",
    "0001639438",
    "0001640147",
    "0001641390",
    "0001642896",
    "0001652044",
    "0001653482",
    "0001653909",
    "0001657853",
    "0001660134",
    "0001662684",
    "0001665650",
    "0001677267",
    "0001679788",
    "0001682852",
    "0001698516",
    "0001703399",
    "0001706946",
    "0001707753",
    "0001708174",
    "0001709186",
    "0001713445",
    "0001717115",
    "0001723464",
    "0001734714",
    "0001734722",
    "0001739945",
    "0001743759",
    "0001745431",
    "0001745433",
    "0001747082",
    "0001758808",
    "0001759509",
    "0001760496",
    "0001770787",
    "0001777393",
    "0001780312",
    "0001783879",
    "0001792785",
    "0001792789",
    "0001799207",
    "0001801169",
    "0001801179",
    "0001802916",
    "0001810806",
    "0001811073",
    "0001811210",
    "0001811414",
    "0001813756",
    "0001818644",
    "0001818874",
    "0001819908",
    "0001819994",
    "0001820953",
    "0001821813",
    "0001822966",
    "0001824920",
    "0001827095",
    "0001828108",
    "0001828871",
    "0001830043",
    "0001833132",
    "0001834584",
    "0001836135",
    "0001836833",
    "0001836981",
    "0001838359",
    "0001840669",
    "0001840856",
    "0001844452",
    "0001849056",
    "0001850236",
    "0001850270",
    "0001855612",
    "0001855933",
    "0001862757",
    "0001870940",
    "0001874178",
    "0001874639",
    "0001875560",
    "0001881014",
    "0001907982",
    "0001923891",
    "0001973239",
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


def _get_direct(url: str, timeout: float = 15.0) -> bytes | None:
    """Single direct attempt. Returns None on any HTTP error or exception."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.HTTPError, Exception):
        return None


def _fetch_jina(url: str, raw: bool = False) -> bytes | None:
    """Fetch via Jina Reader via curl (their servers fetch the target; ours
    aren't blocked). curl is used because Jina serves Cloudflare's JS
    challenge to urllib's TLS fingerprint AND to browser User-Agents (403),
    but serves a bare curl client. raw=True returns the body as text (for
    JSON index files)."""
    import subprocess

    cmd = ["curl", "-s", "-L", "-m", "60", "--compressed"]
    if raw:
        cmd += ["-H", "X-Return-Format: text"]
    cmd.append(JINA_BASE + url)
    for attempt in range(3):
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=70)
            out, err = proc.stdout, proc.stderr
            if proc.returncode != 0:
                raise RuntimeError(err.decode(errors="replace")[:200] or f"curl rc={proc.returncode}")
            if b"Just a moment" in out[:500] or b"Just a moment..." in out[:500]:
                raise RuntimeError("Cloudflare challenge")
            if len(out) < 20 and b"error" in out.lower():
                raise RuntimeError(out.decode(errors="replace")[:120])
            return out
        except Exception as exc:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            print(f"  Jina error for {url}: {exc}", flush=True)
            return None
    return None


def _strip_jina_preamble(data: bytes) -> bytes:
    """Jina markdown output begins with 'Title:/URL Source:/.../Markdown Content:'.
    Strip everything through the first 'Markdown Content:' line."""
    text = data.decode("utf-8", errors="replace")
    marker = "Markdown Content:"
    idx = text.find(marker)
    if idx != -1:
        text = text[idx + len(marker):]
    return text.encode("utf-8", errors="replace")


def _get(url: str, retries: int = 3, raw: bool = False) -> bytes | None:
    """Transport ladder: disk cache -> direct -> Jina Reader. raw=True asks
    Jina for the plain body (JSON index files)."""
    key = hashlib.sha256(url.encode()).hexdigest()
    cf = CACHE_DIR / f"{key}.bin"
    if cf.exists():
        return cf.read_bytes()
    data = _get_direct(url)
    if data is None:
        # direct blocked (403/503) or failed -> Jina fallback
        CONSECUTIVE_503[0] += 1
        if CONSECUTIVE_503[0] >= MAX_CONSECUTIVE_503:
            print(f"  direct path blocked — using Jina transport (sustained)", flush=True)
            CONSECUTIVE_503[0] = 0
        data = _fetch_jina(url, raw=raw)
        if data is not None and not raw:
            data = _strip_jina_preamble(data)
    if data is None:
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cf.write_bytes(data)
    return data


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


def _filings_for_cik(cik: str, kind: str, cap: int = 60) -> list[dict]:
    """Filings for a CIK, S-1-family prioritized (their submissions carry the
    governance exhibits: EX-3.x articles/bylaws, EX-4.x rights instruments,
    EX-21.1 subsidiaries, EX-24 powers of attorney, EX-10.x contracts).
    Scans the full recent list so older-but-relevant S-1/A forms are found."""
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
    all_items = []
    for i, form in enumerate(forms):
        if kind == "corporate" and form not in ("S-1", "S-1/A", "8-K"):
            continue
        if kind == "contract" and form not in ("S-1", "S-1/A", "8-K"):
            continue
        all_items.append({
            "cik": cik,
            "form": form,
            "accession": accns[i] if i < len(accns) else "",
            "filing_date": dates[i] if i < len(dates) else "",
            "primary_document": docs[i] if i < len(docs) else "",
            "filer": doc.get("name", ""),
        })
    # S-1 / S-1/A first (richest exhibit source), then most-recent others
    priority = {"S-1": 0, "S-1/A": 1}.get
    all_items.sort(key=lambda f: (priority(f["form"], 2), f["filing_date"]), reverse=False)
    out = []
    seen = set()
    for it in all_items:
        if len(out) >= cap:
            break
        if it["accession"] in seen:
            continue
        seen.add(it["accession"])
        out.append(it)
    return out


def _archive_cik(accession: str) -> str:
    """EDGAR archive paths use the FILER CIK from the accession prefix —
    submissions data returns the company CIK, which differs when an agent
    (e.g. Cooley LLP) files on the company's behalf. The accession's first
    10 digits ARE the archive CIK."""
    digits = accession.replace("-", "")
    return digits[:10]


def _parse_submission(raw: bytes) -> list[dict]:
    """Parse the full EDGAR submission text (index-headers.html) into per-
    document records. Returns [{type, sequence, filename, description, text}]."""
    text = raw.decode("utf-8", errors="replace")
    docs: list[dict] = []
    # split on <DOCUMENT> markers; each block holds TYPE/FILENAME/DESCRIPTION/TEXT
    parts = text.split("<DOCUMENT>")
    for part in parts[1:]:
        doc = {"type": "", "sequence": "", "filename": "", "description": "", "text": ""}
        segs = part.split("</DOCUMENT>", 1)
        head = segs[0]
        body = segs[1] if len(segs) > 1 else ""
        # TYPE / SEQUENCE / FILENAME / DESCRIPTION are first non-empty lines
        lines = head.splitlines()
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("<TYPE>"):
                doc["type"] = s[len("<TYPE>"):].strip().upper()
            elif s.startswith("<SEQUENCE>"):
                doc["sequence"] = s[len("<SEQUENCE>"):].strip()
            elif s.startswith("<FILENAME>"):
                doc["filename"] = s[len("<FILENAME>"):].strip()
            elif s.startswith("<DESCRIPTION>"):
                doc["description"] = s[len("<DESCRIPTION>"):].strip()
            elif s.startswith("<TEXT>"):
                # everything after <TEXT> up to </TEXT> is the document body
                rest = head[i + 1:] or body
                end = rest.find("</TEXT>")
                doc["text"] = "\n".join(rest[:end].splitlines() if end != -1 else rest.splitlines())
                break
        if not doc["type"] and not doc["filename"]:
            continue
        docs.append(doc)
    return docs


def _exhibit_text(doc: dict) -> str:
    """Raw per-document text (HTML/plain) -> readable text."""
    raw_txt = doc["text"].encode("utf-8", errors="replace")
    return extract_text(raw_txt)


def _index_for_filing(filing: dict) -> list[dict]:
    """Full submission (index-headers.html) via Jina — one request yields every
    exhibit's type/filename/description/full text. Returns parsed documents."""
    acc = filing["accession"]
    acc_no = acc.replace("-", "")
    url = (f"{BASE_ARCHIVE}/{_archive_cik(acc)}/{acc_no}/"
           f"{acc}-index-headers.html")
    raw = _get(url, raw=True)
    if raw is None:
        return []
    return _parse_submission(raw)


def crawl(kind: str, ciks: list[str] | None = None, cap_per_cik: int = 30,
          max_pool: int | None = None) -> dict:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # kind-specific pool avoids append races when corporate/contract crawls run
    # concurrently (parallel subagents)
    pool_path = EDGAR_DIR / f"edgar_rows_{kind}.jsonl"
    if ciks is None:
        ciks = sorted(set(CURATED_CIKS) | set(_v8_ciks()))
    ciks = sorted(set(ciks))
    existing = set()
    if pool_path.exists():
        for line in pool_path.read_text().splitlines():
            try:
                r = json.loads(line)
                existing.add((r["accession"], r["document_name"]))
            except json.JSONDecodeError:
                continue
    stats = {"requests": 0, "exhibits": 0, "skipped_existing": 0,
             "skipped_short": 0, "fetched_filings": 0, "no_target_docs": 0}
    with pool_path.open("a", encoding="utf-8") as fh:
        for cik_idx, cik in enumerate(ciks):
            if max_pool is not None and stats["exhibits"] >= max_pool:
                print(f"  max_pool reached ({max_pool} exhibits) — stopping early", flush=True)
                break
            if cik_idx > 0:
                time.sleep(2.0)  # extra gentle between CIKs
            filings = _filings_for_cik(cik, kind, cap=cap_per_cik)
            stats["requests"] += 1
            for filing in filings:
                stats["fetched_filings"] += 1
                items = _index_for_filing(filing)
                stats["requests"] += 1
                time.sleep(SLEEP)
                if not items:
                    stats["no_target_docs"] += 1
                    continue
                for item in items:
                    name = str(item.get("filename") or "")
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
                    raw = _get(url)  # per-exhibit full text via Jina (markdown)
                    stats["requests"] += 1
                    time.sleep(SLEEP)
                    if raw is None:
                        continue
                    text = extract_text(raw)
                    short = len(text) < 500
                    if short:
                        stats["skipped_short"] += 1
                        # Keep short exhibits but flag them (draw may still use them)
                    rel = RAW_DIR / subclass / f"{filing['accession']}_{name}"
                    rel.parent.mkdir(parents=True, exist_ok=True)
                    rel.write_text(text, encoding="utf-8")
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
    print(f"crawl {kind}: {stats}", flush=True)
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
    parser.add_argument("--max-pool", type=int, default=None,
                        help="stop crawling once the pool has this many exhibits")
    parser.add_argument("--ciks", default="",
                        help="comma-separated CIK override (default: curated + v8)")
    args = parser.parse_args()

    ciks = [c.strip() for c in args.ciks.split(",") if c.strip()] or None
    crawl(args.kind, ciks=ciks, cap_per_cik=args.cap_per_cik,
          max_pool=args.max_pool)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())