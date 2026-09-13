#!/usr/bin/env python3
"""EDGAR full-text-search enumerator for the v9 expansion (issue #3; #9/#12).

Uses the SEC EDGAR full-text search API (efts.sec.gov) VIA the Jina Reader
proxy (our IP is blocked by SEC directly). This gives exhibit-LEVEL results:
each hit carries file_type (EX-3.2), file_description (BYLAWS), adsh
(accession), ciks[], form, file_date and the exact document filename — so we
target the exact governance exhibits without per-CIK crawling. Old documents
are fine (human directive: corporate records need not be recent).

Flow:
1. For each (subclass, query_term, file_type_prefix, forms) target, query
   efts via Jina, paginate (start=0,100,200,...), keep hits whose
   ``_source.file_type`` matches the prefix; dedupe by (adsh, filename);
   stop when a subclass has enough candidates.
2. Fetch each exhibit's full text via Jina with a disk cache
   (https://www.sec.gov/Archives/edgar/data/{cik_unpadded}/{adsh_no_dash}/{filename}).
3. Write rows to ``edgar_rows_corporate.jsonl`` / ``edgar_rows_contract.jsonl``
   (deterministic: sorted by (adsh, filename); sha256 dedupe already applied).

Rate limiting: efts via Jina is per-IP rate limited (retryAfter ~1-2s), so a
~3.5s base sleep between queries is used, with retry-after backoff on 429.

Usage:
    .venv/bin/python scripts/edgar_ftsearch.py --kind corporate --max-pool 950
    .venv/bin/python scripts/edgar_ftsearch.py --kind contract  --max-pool 220
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from mailroom_eda.config import DATA_DIR  # noqa: E402

EDGAR_DIR = DATA_DIR / "v9" / "edgar"
CACHE_DIR = EDGAR_DIR / "cache"
JINA_BASE = "https://r.jina.ai/"
EFTS = "https://efts.sec.gov/LATEST/search-index"
BASE_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"

#: Per-subclass: (query term, file_type prefix, forms filter, pool kind).
#: Distinctive phrases so the exhibit itself is what matches.
CORPORATE_TARGETS = [
    # (subclass, query, file_type_prefix, forms)
    ("articles_of_incorporation", '"articles of incorporation"', "EX-3.1", "S-1,S-1/A,8-K,10-K"),
    ("bylaws",                    '"bylaws"',                     "EX-3.2", "S-1,S-1/A,8-K,10-K"),
    ("charter_amendment",         '"certificate of amendment"',   "EX-3",   "8-K,S-1,S-1/A,10-K"),
    ("board_resolution",          '"unanimous consent of the board"', "EX-3", "8-K,S-1"),
    ("officer_certificate",       '"certificate of designation"', "EX-3",   "8-K,S-1,S-1/A,10-K"),
    ("powers_of_attorney",        '"powers of attorney"',         "EX-24",  "S-1,S-1/A,8-K,10-K"),
    ("subsidiary_list",           '"subsidiaries of the registrant"', "EX-21", "S-1,S-1/A,10-K,8-K"),
    ("rights_instrument",         '"registration rights agreement"', "EX-4", "S-1,S-1/A,10-K,8-K"),
    ("indenture",                 '"indenture"',                  "EX-4",   "S-1,S-1/A,8-K,10-K"),
    ("other",                     '"charter"',                    "EX-3",   "8-K,S-1,S-1/A"),
]

CONTRACT_TARGETS = [
    ("employment",   '"employment agreement"',      "EX-10", "S-1,S-1/A,8-K,10-K"),
    ("license",      '"license agreement"',         "EX-10", "S-1,S-1/A,8-K"),
    ("purchase",     '"purchase agreement"',        "EX-10", "S-1,S-1/A,8-K,10-K"),
    ("supply",       '"supply agreement"',          "EX-10", "S-1,S-1/A,8-K"),
    ("consulting",   '"consulting agreement"',      "EX-10", "S-1,S-1/A,8-K"),
    ("nda",          '"confidentiality agreement"', "EX-10", "S-1,S-1/A,8-K"),
    ("services",     '"services agreement"',        "EX-10", "S-1,S-1/A,8-K"),
    ("other",        '"material agreement"',        "EX-10", "S-1,S-1/A,8-K,10-K"),
]

TARGETS = {"corporate": CORPORATE_TARGETS, "contract": CONTRACT_TARGETS}

#: subclass -> pool kind (contract subclass names differ but both go to contract)
POOL_BY_KIND = {"corporate": "corporate", "contract": "contract"}


def _jina(url: str, raw: bool = False) -> bytes | None:
    """Fetch a URL via Jina Reader with curl (their servers fetch; ours aren't
    blocked). curl is used because Jina serves Cloudflare's JS challenge to
    urllib's TLS fingerprint and to browser UAs, but accepts a bare curl."""
    cmd = ["curl", "-s", "-L", "-m", "60", "--compressed"]
    if raw:
        cmd += ["-H", "X-Return-Format: text"]
    cmd.append(JINA_BASE + url)
    for attempt in range(3):
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=70)
            out = proc.stdout
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.decode(errors="replace")[:150])
            if b"Just a moment" in out[:500]:
                raise RuntimeError("Cloudflare challenge")
            return out
        except Exception as exc:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            print(f"  Jina error {url[:80]}: {exc}", flush=True)
            return None
    return None


def _cached(url: str, raw: bool = False) -> bytes | None:
    key = hashlib.sha256(url.encode()).hexdigest()
    cf = CACHE_DIR / f"{key}.bin"
    if cf.exists():
        return cf.read_bytes()
    data = _jina(url, raw=raw)
    if data is not None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cf.write_bytes(data)
    return data


def _strip_preamble(data: bytes) -> bytes:
    text = data.decode("utf-8", errors="replace")
    marker = "Markdown Content:"
    idx = text.find(marker)
    if idx != -1:
        text = text[idx + len(marker):]
    return text.encode("utf-8", errors="replace")


def _efts_query(term: str, forms: str, start: int) -> list[dict]:
    q = term.replace('"', "%22").replace(" ", "%20")
    url = (f"{EFTS}?q={q}&forms={forms.replace(',', '%2C')}"
           f"&start={start}&output=json")
    data = _cached(url)
    if data is None:
        return []
    raw = _strip_preamble(data)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if "hits" not in doc:
        # rate-limit payload: {"retryAfter":N,...} -> return sentinel
        ra = doc.get("retryAfter", 3) if isinstance(doc, dict) else 3
        return [{"_rate_limit": ra}]
    return doc.get("hits", {}).get("hits", [])


def _exhibit_url(ciks: list[str], adsh: str, filename: str) -> str:
    cik = (ciks[0] if ciks else "").lstrip("0")
    accn = adsh.replace("-", "")
    return f"{BASE_ARCHIVE}/{cik}/{accn}/{filename}"


def crawl(kind: str, max_pool: int) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pool_path = EDGAR_DIR / f"edgar_rows_{kind}.jsonl"
    existing = set()
    if pool_path.exists():
        for line in pool_path.read_text().splitlines():
            try:
                r = json.loads(line)
                existing.add((r["accession"], r["document_name"]))
            except json.JSONDecodeError:
                continue
    seen_candidates = set()
    candidates: dict[str, dict] = {}   # key (adsh, filename) -> candidate
    stats = {"queries": 0, "candidates": 0, "exhibits": 0,
             "skipped_existing": 0, "fetches_failed": 0}
    with pool_path.open("a", encoding="utf-8") as fh:
        for subclass, term, ft_prefix, forms in TARGETS[kind]:
            if stats["exhibits"] >= max_pool:
                break
            for start in range(0, 600, 100):
                hits = _efts_query(term, forms, start)
                stats["queries"] += 1
                if hits and "_rate_limit" in hits[0]:
                    ra = hits[0]["_rate_limit"]
                    print(f"  efts 429 — sleeping {ra + 2}s", flush=True)
                    time.sleep(ra + 2)
                    hits = _efts_query(term, forms, start)
                    stats["queries"] += 1
                if not hits:
                    break
                got = 0
                for h in hits:
                    src = h.get("_source", {})
                    ft = str(src.get("file_type") or "")
                    if not ft.upper().startswith(ft_prefix):
                        continue
                    filename = h["_id"].split(":", 1)[-1] if ":" in h["_id"] else h["_id"]
                    adsh = src.get("adsh") or ""
                    if not filename or not adsh:
                        continue
                    key = (adsh, filename)
                    if key in seen_candidates:
                        continue
                    seen_candidates.add(key)
                    candidates[key] = {
                        "subclass": subclass,
                        "file_type": ft,
                        "description": src.get("file_description") or "",
                        "adsh": adsh,
                        "ciks": src.get("ciks") or [],
                        "form": src.get("form") or "",
                        "file_date": src.get("file_date") or "",
                        "display": src.get("display_names") or "",
                        "filename": filename,
                    }
                    got += 1
                if got == 0 or len(hits) < 100:
                    break
                time.sleep(3.5)
            time.sleep(3.5)
        stats["candidates"] = len(candidates)
        # Fetch each candidate exhibit (sorted for determinism), write pool rows
        for key in sorted(candidates):
            if stats["exhibits"] >= max_pool:
                break
            c = candidates[key]
            adsh, filename = key
            if key in existing:
                stats["skipped_existing"] += 1
                continue
            url = _exhibit_url(c["ciks"], adsh, filename)
            data = _cached(url)
            time.sleep(0.6)
            if data is None:
                stats["fetches_failed"] += 1
                continue
            text = _strip_preamble(data).decode("utf-8", errors="replace")
            if len(text) < 300:
                stats["fetches_failed"] += 1
                continue
            row = {
                "kind": kind,
                "cik": c["ciks"][0] if c["ciks"] else "",
                "filer": c["display"],
                "form": c["form"],
                "accession": adsh,
                "filing_date": c["file_date"],
                "document_name": filename,
                "exhibit_type": c["file_type"],
                "exhibit_description": c["description"],
                "exhibit_url": url,
                "subclass": c["subclass"],
                "chars": len(text),
                "short_text": len(text) < 500,
                "raw_file": "",
                "doc_text": text,
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            existing.add(key)
            stats["exhibits"] += 1
    print(f"crawl {kind}: {stats}", flush=True)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("corporate", "contract"), required=True)
    parser.add_argument("--max-pool", type=int, default=950)
    args = parser.parse_args()
    crawl(args.kind, args.max_pool)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
