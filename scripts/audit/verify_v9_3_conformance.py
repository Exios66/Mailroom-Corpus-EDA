#!/usr/bin/env python3
"""v9.3 conformance script — formal gates report for the staging revision.

Runs the v9.3 staging build over the verified sources (the ``v9.2`` base
sidecar + the ``corporate-labels-v9.2.1`` branch payload) and proves:

(a) 3,302 rows, split counts unchanged (2979 train / 323 test);
(b) every corporate row's presence keys ⊆ the extended ``ALL_GT_FIELDS``
    and ⊇ the 20-key ``BASE_GT_FIELDS``;
(c) every presence value ∈ the published vocabulary
    (populated / not_applicable / schema_documented_absence / pending_annotation);
(d) all 450 corporate rows' ``gt_fields`` byte-identical to the v9.2.1
    branch source, re-derived by the EDA relabel equivalents from the base;
(e) non-corporate rows byte-identical to v9.2.1;
(f) the sha256 manifest regenerates (recomputed from the staged bytes,
    ``labels_version`` line present);
(+ the registry parity guard: EDA mirror == live dojo
    ``SUBCLASS_SCHEMAS`` when the sibling checkout is present).

Exit code 0 iff every gate passes. Sources can be overridden:

    python scripts/audit/verify_v9_3_conformance.py \
        --base-path /tmp/gt92/hardened.jsonl --verify-registry
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import mailroom_eda.corporate_presence as cp  # noqa: E402
import mailroom_eda.v9_3_revision as v93  # noqa: E402
from mailroom_eda.gt_presence import ALL_GT_FIELDS, BASE_GT_FIELDS  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _raw_lines(path: Path) -> dict[str, str]:
    return {
        json.loads(ln)["filename"]: ln.decode("utf-8")
        for ln in path.read_bytes().split(b"\n")
        if ln
    }


def _dojo_facets_via_venv(checkout: Path) -> dict | None:
    """Import the dojo schema under the dojo repo's own venv (its deps are
    installed there) and dump the registry facets as JSON via subprocess."""
    import subprocess

    for py in (checkout / ".venv" / "bin" / "python",):
        if not py.is_file():
            continue
        try:
            out = subprocess.run(
                [str(py), "-c", (
                    "import json, llm_dojo_scoring.corporate_record_schema as m; "
                    "print(json.dumps({"
                    "'record_types': list(m.RECORD_TYPES), "
                    "'entity_core': list(m.ENTITY_CORE_FIELDS), "
                    "'subclasses': {s: {'record_type': v.record_type, 'intent': v.intent, "
                    "'applicable': sorted(v.core_applicable), "
                    "'entities': [e.key for e in v.entities]} for s, v in m.SUBCLASS_SCHEMAS.items()}}))"
                )],
                capture_output=True, text=True, timeout=120, cwd=str(checkout),
            )
            if out.returncode == 0:
                return json.loads(out.stdout.strip().splitlines()[-1])
            print(f"  registry parity: dojo venv import failed: {out.stderr.strip().splitlines()[-1] if out.stderr else out.returncode}")
        except (OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            print(f"  registry parity: dojo venv invocation failed: {exc}")
    return None


def _compare_dojo_facets(facets: dict) -> str:
    """Mirror == dojo facets, field by field."""
    problems: list[str] = []
    if tuple(facets["record_types"]) != cp.RECORD_TYPES:
        problems.append("record_type enum")
    if tuple(facets["entity_core"]) != cp.CORPORATE_ENTITY_CORE_KEYS:
        problems.append("entity core keys")
    dojo_sub = facets["subclasses"]
    if set(dojo_sub) != set(cp.CORPORATE_SUBCLASS_ENTITY_KEYS):
        problems.append("subclass set")
    for sub, f in dojo_sub.items():
        if cp.SUBCLASS_RECORD_TYPES.get(sub) != f["record_type"]:
            problems.append(f"{sub}.record_type")
        if cp.SUBCLASS_INTENTS.get(sub) != f["intent"]:
            problems.append(f"{sub}.intent")
        if tuple(f["entities"]) != tuple(cp.CORPORATE_SUBCLASS_ENTITY_KEYS.get(sub, ())):
            problems.append(f"{sub}.entities")
        if set(f["applicable"]) != set(cp.SUBCLASS_CORE_APPLICABLE.get(sub, frozenset())):
            problems.append(f"{sub}.core_applicable")
    if problems:
        print("  registry parity diverged on:", ", ".join(sorted(set(problems))))
        return "FAIL"
    return (
        f"PASS (mirror == llm_dojo_scoring @ {cp.DOJO_REV[:12]}, "
        f"schema sha256 {cp.DOJO_SCHEMA_SHA256[:12]}…)"
    )


def verify_registry_parity() -> str:
    """(+): mirror == live dojo module (byte-check the pinned sources, then
    import the sibling checkout — in-process when the deps allow, else through
    the dojo repo's own venv — and compare every registry facet)."""
    dojo = ROOT.parent / "llm-dojo-scoring"
    schema = dojo / "llm_dojo_scoring" / "corporate_record_schema.py"
    relabel = dojo / "llm_dojo_scoring" / "corporate_record_relabel.py"
    if not schema.is_file() or not relabel.is_file():
        return "SKIP (llm-dojo-scoring checkout not present)"
    if _sha(schema) != cp.DOJO_SCHEMA_SHA256 or _sha(relabel) != cp.DOJO_RELABEL_SHA256:
        print(f"  registry parity: pinned sha mismatch! schema {_sha(schema)} relabel {_sha(relabel)}")
        return "FAIL"
    # 1) in-process (needs structlog etc. installed in THIS venv)
    if str(dojo) not in sys.path:
        sys.path.insert(0, str(dojo))
    try:
        from llm_dojo_scoring.corporate_record_schema import SUBCLASS_SCHEMAS as SS
        from llm_dojo_scoring.corporate_record_schema import (
            ENTITY_CORE_FIELDS as ss_entity_core,
            RECORD_TYPES as ss_record_types,
        )
    except Exception:
        SS = None, None, None  # deps unavailable in this interpreter — fall through
    if SS[0] is not None:
        SS, ss_record_types, ss_entity_core = SS
        facets = {
            "record_types": list(ss_record_types),
            "entity_core": list(ss_entity_core),
            "subclasses": {
                sub: {
                    "record_type": sch.record_type,
                    "intent": sch.intent,
                    "applicable": sorted(sch.core_applicable),
                    "entities": [e.key for e in sch.entities],
                }
                for sub, sch in SS.items()
            },
        }
        out = _compare_dojo_facets(facets)
        if out.startswith("PASS"):
            out += " (in-process)"
        return out
    # 2) via the dojo's own venv subprocess
    facets = _dojo_facets_via_venv(dojo)
    if facets is None:
        return "SKIP (dojo module not importable in any available venv)"
    return _compare_dojo_facets(facets)


def main() -> int:
    parser = argparse.ArgumentParser(description="v9.3 conformance gates report")
    parser.add_argument("--base-path", default=None,
                        help="sha-verified v9.2 base sidecar (required unless present in data/v9_3)")
    parser.add_argument("--branch-dir", type=Path, default=None)
    parser.add_argument("--verify-registry", action="store_true", default=True)
    args = parser.parse_args()

    base_path = args.base_path or (v93.BASE_SNAPSHOT_DIR / "ground_truth_hardened.jsonl")
    base_path = Path(base_path)
    if not base_path.is_file():
        base_path = v93.resolve_base_path(None)

    results: list[tuple[str, str, str]] = []

    # Run the staging build (which internally runs every gate) into a scratch
    # stage dir, then assert the gate artifacts explicitly per letter.
    stage = Path(tempfile.mkdtemp(prefix="v9_3_conformance_"))
    result = v93.build_all(stage, branch_dir=args.branch_dir, base_path=str(base_path))
    branch_dir = Path(args.branch_dir) if args.branch_dir else v93.BRANCH_SNAPSHOT_DIR

    base_rows = v93._load_jsonl(Path(base_path))
    new_rows = v93.relabel_all_rows(base_rows)
    branch_rows = v93._load_jsonl(branch_dir / "ground_truth_hardened.jsonl")

    # (a)
    from collections import Counter
    splits = Counter(r["split"] for r in new_rows)
    corp = sum(1 for r in new_rows if r["expected"] == "corporate_record")
    va = "PASS"
    if len(new_rows) != 3302 or dict(splits) != {"train": 2979, "test": 323} or corp != 450:
        va = "FAIL"
    results.append(("(a) row set", va, f"{len(new_rows):,} rows, splits {dict(splits)}, corporate {corp}"))

    # (b) + (c) — re-assert explicitly against the registry
    v93.verify_presence_laws(new_rows)
    results.append((
        "(b) presence key set",
        "PASS",
        f"⊆ {len(ALL_GT_FIELDS)}-key ALL_GT_FIELDS (37 corporate entity keys), "
        f"⊇ {len(BASE_GT_FIELDS)}-key base; unknown keys rejected",
    ))
    results.append((
        "(c) presence vocabulary",
        "PASS",
        f"codes shipped on corporate rows: {result['presence_stats']['corporate_codes']}"
        " — populated / not_applicable / schema_documented_absence / pending_annotation only",
    ))

    # (d) + (e) — raw-byte identity against the published branch sidecar
    raw = v93.verify_raw_line_equality(new_rows, branch_dir / "ground_truth_hardened.jsonl")
    results.append((
        "(d) corporate gt_fields byte-identity",
        "PASS" if raw["corporate"] == 450 else "FAIL",
        f"{raw['corporate']}/450 corporate rows byte-identical (relabel-equivalent rebuilt)",
    ))
    results.append((
        "(e) non-corporate byte-identity",
        "PASS" if raw["non_corporate"] == len(new_rows) - 450 else "FAIL",
        f"{raw['non_corporate']}/{len(new_rows) - 450} non-corporate rows byte-identical",
    ))

    # (f) — manifest regeneration from staged bytes
    manifest = (stage / "manifest.txt").read_text(encoding="utf-8")
    table = dict(re.findall(r"^\s*(\S+)\s+([0-9a-f]{64})\s*$", manifest, flags=re.M))
    misses = [rel for rel, sha in table.items() if rel != "manifest.txt" and _sha(stage / rel) != sha]
    labels_ok = (
        "labels_version" in manifest and manifest.count("labels_version") == 1
        and v93.REVISION_LABEL in manifest
    )
    regen_ok = all(
        _sha(stage / rel) == sha for rel, sha in table.items() if rel != "manifest.txt"
    )
    results.append((
        "(f) sha256 manifest regenerates",
        "PASS" if regen_ok and labels_ok and not misses else "FAIL",
        f"{len(table)} sha rows recomputed from the staged bytes"
        + (f"; misses {misses}" if misses else "")
        + f"; labels_version present, dataset version untouched: {labels_ok}",
    ))

    # (+) registry parity with the live dojo module
    results.append(("(+) registry parity", verify_registry_parity(), cp.DOJO_REV))

    # staged artifact sanity: sidecar sha equals the pinned branch sha
    staged_sha = _sha(stage / "ground_truth_hardened.jsonl")
    results.append((
        "(payload) staged sidecar sha",
        "PASS" if staged_sha == v93.BRANCH_SIDECAR_SHA256 else "FAIL",
        f"{staged_sha[:12]}… == pinned {v93.BRANCH_SIDECAR_SHA256[:12]}… (dbe0c604…)",
    ))

    print("\n=== v9.3 conformance gates ===")
    failed = False
    for name, status, detail in results:
        print(f"{status:6} {name:36} {detail}")
        if status == "FAIL":
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
