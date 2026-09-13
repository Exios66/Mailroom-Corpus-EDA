#!/usr/bin/env python3
"""Build the v9 contract draw sidecar (data/v9/draws/contract.jsonl).

Reads the SEC EDGAR pool (data/v9/edgar/edgar_rows.jsonl, kind=="contract"),
excludes v8 filenames, allocates 91 seats across subclasses via largest-
remainder (floor 1 per stratum), and writes the sidecar + manifest.

Usage:
    .venv/bin/python scripts/draw_contract_v9.py [--quota 91] [--min-pool 200]
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import importlib.util
import sys
_b = Path(__file__).resolve()
while not (_b / "_bootstrap.py").is_file():
    _b = _b.parent
sys.path.insert(0, str(_b))
from _bootstrap import ROOT  # noqa: E402

from mailroom_eda.config import DATA_DIR  # noqa: E402

EDGAR_DIR = DATA_DIR / "v9" / "edgar"
ROWS_PATH = EDGAR_DIR / "edgar_rows.jsonl"
V8_FN_PATH = DATA_DIR / "v8_filenames.txt"
DRAWS_DIR = DATA_DIR / "v9" / "draws"
OUT_PATH = DRAWS_DIR / "contract.jsonl"
MANIFEST_PATH = DRAWS_DIR / "contract_manifest.json"

QUOTA = 91
MIN_POOL = 200


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _split(filename: str) -> str:
    return "test" if int(_md5(filename), 16) % 10 == 0 else "train"


def _load_v8_filenames() -> set[str]:
    if not V8_FN_PATH.exists():
        return set()
    return {line.strip() for line in V8_FN_PATH.read_text().splitlines() if line.strip()}


def _load_pool() -> list[dict]:
    """Load contract rows from the EDGAR pool."""
    if not ROWS_PATH.exists():
        return []
    rows = []
    for line in ROWS_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("kind") == "contract":
            rows.append(r)
    return rows


def _extract_keywords(text: str, subclass: str, n: int = 8) -> list[str]:
    """Heuristic keyword extraction: subclass + top terms from text."""
    terms = [subclass.replace("_", " ")]
    # Extract capitalized phrases and quoted terms
    caps = re.findall(r'"([^"]{3,40})"', text[:3000])
    for c in caps[:3]:
        terms.append(c.strip())
    # Common legal/agreement terms
    legal_terms = re.findall(
        r'\b(agreement|exhibit|section|party|parties|obligations|term|termination|'
        r'confidential|compensation|liability|indemnif|warrant|represent|covenant|'
        r'consideration|effective date|governing law|jurisdiction|arbitration|'
        r'assign|amend|breach|remedy|force majeure|intellectual property|license|'
        r'royalt|deliverable|milestone|service|supply|purchase|consulting|employment|'
        r'non-disclosure|non-compete)\b',
        text[:5000], re.I
    )
    seen = set()
    for t in legal_terms:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            terms.append(tl)
        if len(terms) >= n:
            break
    return terms[:n]


def _build_label_evidence(row: dict) -> str:
    """Build label_evidence for test-split clause labels."""
    desc = (row.get("exhibit_description") or "").strip()
    if desc:
        return f"SEC EDGAR {row.get('exhibit_type', 'EX-10')} exhibit: {desc}"
    subclass = row.get("subclass", "other")
    return f"SEC EDGAR EX-10 material agreement exhibit ({subclass})"


def _largest_remainder_alloc(strata_counts: dict[str, int], quota: int) -> dict[str, int]:
    """Largest-remainder allocation with floor 1 per stratum.
    
    Each stratum gets at least 1 seat (if it has any rows).
    Remaining seats distributed by largest fractional remainder.
    """
    strata = {k: v for k, v in strata_counts.items() if v > 0}
    n_strata = len(strata)
    if n_strata == 0:
        return {}
    
    # Floor 1 per stratum
    if quota < n_strata:
        # Can't give 1 to each — give to the largest strata
        sorted_strata = sorted(strata.items(), key=lambda x: -x[1])
        alloc = {}
        for k, v in sorted_strata[:quota]:
            alloc[k] = 1
        return alloc
    
    total = sum(strata.values())
    # Exact shares
    exact = {k: (v / total) * quota for k, v in strata.items()}
    # Floor
    floors = {k: max(1, int(math.floor(ex))) for k, ex in exact.items()}
    # Remaining seats
    assigned = sum(floors.values())
    remaining = quota - assigned
    
    if remaining < 0:
        # Over-allocated due to floor-1; trim from smallest strata
        sorted_by_exact = sorted(floors.items(), key=lambda x: exact[x[0]] - 1)
        for k, _ in sorted_by_exact:
            if remaining >= 0:
                break
            if floors[k] > 1:
                floors[k] -= 1
                remaining += 1
        return floors
    
    if remaining == 0:
        return floors
    
    # Distribute remaining by largest fractional remainder
    remainders = {k: exact[k] - floors[k] for k in strata}
    sorted_by_rem = sorted(remainders.items(), key=lambda x: -x[1])
    for k, _ in sorted_by_rem[:remaining]:
        floors[k] += 1
    
    return floors


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quota", type=int, default=QUOTA)
    parser.add_argument("--min-pool", type=int, default=MIN_POOL)
    parser.add_argument("--force", action="store_true",
                        help="proceed even if pool < min-pool")
    args = parser.parse_args()
    
    quota = args.quota
    
    # Load pool
    pool = _load_pool()
    pool_size = len(pool)
    print(f"Pool: {pool_size} contract rows")
    
    if pool_size < args.min_pool and not args.force:
        print(f"ERROR: pool has {pool_size} rows, need >= {args.min_pool}. "
              f"Use --force to proceed anyway or wait for the crawl.")
        return 1
    
    # Exclude v8 filenames
    v8_fns = _load_v8_filenames()
    # Build filename for each pool row: <accession>_<document_name>
    eligible = []
    for r in pool:
        fn = f"{r['accession']}_{r['document_name']}"
        if fn in v8_fns:
            continue
        if not r.get("doc_text", "").strip():
            continue
        eligible.append(r)
    
    print(f"Eligible (after v8 exclusion + non-empty text): {len(eligible)}")
    
    if len(eligible) < quota:
        print(f"WARNING: only {len(eligible)} eligible rows for quota {quota}. "
              f"Proceeding with available rows.")
        quota = len(eligible)
    
    # Group by subclass
    by_subclass: dict[str, list[dict]] = defaultdict(list)
    for r in eligible:
        by_subclass[r["subclass"]].append(r)
    
    # Sort each stratum by sha256(accession + document_name)
    for subclass in by_subclass:
        by_subclass[subclass].sort(
            key=lambda r: hashlib.sha256(
                (r["accession"] + r["document_name"]).encode()
            ).hexdigest()
        )
    
    strata_counts = {k: len(v) for k, v in by_subclass.items()}
    print(f"Strata: {dict(sorted(strata_counts.items()))}")
    
    # Allocate seats
    alloc = _largest_remainder_alloc(strata_counts, quota)
    actual_total = sum(alloc.values())
    print(f"Allocation (total={actual_total}): {dict(sorted(alloc.items()))}")
    
    # Build draw rows
    draw_rows = []
    used_ciks = set()
    for subclass, seats in sorted(alloc.items()):
        candidates = by_subclass[subclass][:seats]
        for r in candidates:
            fn = f"{r['accession']}_{r['document_name']}"
            split = _split(fn)
            subclass_label = r["subclass"]
            
            # gt_fields (6 keys for contract: intent, subject_matter, keywords,
            # intent_source, intent_confidence, intent_status)
            # Plus test-split clause labels: label_evidence, cuad_clause_labels
            keywords = _extract_keywords(r.get("doc_text", ""), subclass_label)
            
            gt_fields = {
                "intent": "material_agreement",
                "subject_matter": f"{subclass_label} agreement (SEC EDGAR EX-10 exhibit).",
                "keywords": json.dumps(keywords),
                "intent_source": "heuristic",
                "intent_confidence": "0.9",
                "intent_status": "auto_labeled",
            }
            
            # Test-split clause labels
            label_evidence = _build_label_evidence(r)
            gt_fields["label_evidence"] = label_evidence
            gt_fields["cuad_clause_labels"] = "[]"
            
            # metadata (all str, no label-like keys)
            metadata = {
                "source_dataset": "sec_edgar",
                "source_revision": "",
                "source_row_id": f"{r['accession']}_{r['document_name']}",
                "license": "us-public-domain",
                "cik": str(r.get("cik", "")),
                "filer": str(r.get("filer", "")),
                "form": str(r.get("form", "")),
                "accession": str(r.get("accession", "")),
                "filing_date": str(r.get("filing_date", "")),
                "exhibit_type": str(r.get("exhibit_type", "")),
                "exhibit_description": str(r.get("exhibit_description", "")),
                "exhibit_url": str(r.get("exhibit_url", "")),
                "original_file": str(r.get("raw_file", "")),
            }
            
            row = {
                "filename": fn,
                "doc_text": r["doc_text"],
                "prompt": "",
                "expected": "contract",
                "expected_subclass": subclass_label,
                "split": split,
                "metadata": metadata,
                "gt_fields": gt_fields,
            }
            draw_rows.append(row)
            used_ciks.add(r.get("cik", ""))
    
    # Sort deterministically by filename
    draw_rows.sort(key=lambda r: r["filename"])
    
    # Verify
    filenames = [r["filename"] for r in draw_rows]
    assert len(filenames) == len(set(filenames)), "Duplicate filenames!"
    assert len(set(filenames) & v8_fns) == 0, "Collision with v8!"
    
    # Verify test-split rows have clause labels
    test_rows = [r for r in draw_rows if r["split"] == "test"]
    for r in test_rows:
        le = r["gt_fields"].get("label_evidence", "")
        assert le, f"Test row {r['filename']} missing label_evidence"
        ccl = r["gt_fields"].get("cuad_clause_labels", "")
        assert ccl == "[]", f"Test row {r['filename']} has non-empty cuad_clause_labels"
    
    # Verify all gt_fields keys present
    required_gt = {"intent", "subject_matter", "keywords", "intent_source",
                   "intent_confidence", "intent_status", "label_evidence",
                   "cuad_clause_labels"}
    for r in draw_rows:
        missing = required_gt - set(r["gt_fields"].keys())
        assert not missing, f"Row {r['filename']} missing gt_fields: {missing}"
        for k, v in r["gt_fields"].items():
            assert v is not None, f"Row {r['filename']} has None gt_field: {k}"
    
    # Verify all metadata values are str
    for r in draw_rows:
        for k, v in r["metadata"].items():
            assert isinstance(v, str), f"Row {r['filename']} metadata.{k} is not str: {type(v)}"
    
    # Write sidecar
    DRAWS_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        for r in draw_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    
    # Subclass distribution in draw
    draw_strata = Counter(r["expected_subclass"] for r in draw_rows)
    train_count = sum(1 for r in draw_rows if r["split"] == "train")
    test_count = sum(1 for r in draw_rows if r["split"] == "test")
    
    # Write manifest
    manifest = {
        "class": "contract",
        "quota": args.quota,
        "actual": len(draw_rows),
        "strata": dict(sorted(draw_strata.items())),
        "cik_count": len(used_ciks),
        "pool_rows": pool_size,
        "eligible_rows": len(eligible),
        "train_rows": train_count,
        "test_rows": test_count,
        "note": (
            f"{len(draw_rows)}-row contract draw from SEC EDGAR EX-10 exhibits "
            f"({pool_size}-row pool; {len(eligible)} eligible after excluding "
            f"{len(v8_fns)} v8 filenames). Allocation: largest-remainder with "
            f"floor 1 per stratum across {len(draw_strata)} subclasses. "
            f"Split: md5(filename)%10==0 -> test ({test_count} test, {train_count} train). "
            f"Intent: heuristic (material_agreement, confidence 0.9). "
            f"Test-split clause labels: label_evidence from exhibit description, "
            f"cuad_clause_labels=[] (SEC exhibits, not CUAD)."
        ),
    }
    with MANIFEST_PATH.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    
    print(f"\n=== CONTRACT DRAW COMPLETE ===")
    print(f"Sidecar: {OUT_PATH}")
    print(f"Manifest: {MANIFEST_PATH}")
    print(f"Rows: {len(draw_rows)} (quota was {args.quota})")
    print(f"Train: {train_count}, Test: {test_count}")
    print(f"Subclass distribution: {dict(sorted(draw_strata.items()))}")
    print(f"Unique CIKs: {len(used_ciks)}")
    print(f"Pool size: {pool_size}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
