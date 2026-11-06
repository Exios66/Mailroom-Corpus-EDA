"""Regenerate the llm-dojo-scoring schema snapshot used by the labeling playbook.

The EDA repo does not depend on llm-dojo-scoring. The playbook renders prompts and
checks field types against a JSON snapshot of the Dojo catalogs instead, and this
script refreshes that snapshot from an importable Dojo checkout:

    python scripts/labeling/snapshot_dojo.py --dojo-src ../local-mailroom-sandbox/vendor/llm-dojo-scoring/src
    python scripts/labeling/snapshot_dojo.py --dojo-src ... --check   # exit 1 on drift

``--check`` is what ``tests/test_labeling.py`` runs when a Dojo source is present.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "src" / "mailroom_eda" / "labeling" / "dojo_snapshot" / "dojo_schemas.json"


def build_snapshot(dojo_src: Path) -> dict:
    """Read the Dojo catalogs from ``dojo_src`` and return the snapshot dict."""
    sys.path.insert(0, str(dojo_src))
    from llm_dojo_scoring import __version__ as version  # noqa: PLC0415
    from llm_dojo_scoring.config import MAUD_CONSIDERATION_TYPES  # noqa: PLC0415
    from llm_dojo_scoring.schemas.corporate_record_schema import SUBCLASS_SCHEMAS  # noqa: PLC0415
    from llm_dojo_scoring.schemas.cuad_schema import CUAD_CATEGORIES, CUAD_FAMILIES  # noqa: PLC0415
    from llm_dojo_scoring.schemas.maud_schema import (  # noqa: PLC0415
        MAUD_TYPE_OF_CONSIDERATION_TO_TOKEN,
    )
    from llm_dojo_scoring.scoring.suites import DEFAULT_FIELD_TYPES  # noqa: PLC0415

    vendor_md = dojo_src.parent / "VENDOR.md"
    commit = ""
    if vendor_md.exists():
        for line in vendor_md.read_text(encoding="utf-8").splitlines():
            if "Exact source commit" in line:
                commit = line.split("`")[1]
    return {
        "dojo_version": version,
        "dojo_commit": commit,
        "default_field_types": {
            k: DEFAULT_FIELD_TYPES[k]
            for k in ("contract", "merger_agreement", "correspondence", "insurance_claim",
                      "corporate_record")
        },
        "cuad_categories": [{"name": c.name, "description": c.description} for c in CUAD_CATEGORIES],
        "cuad_families": [
            {"key": f.key, "label": f.label, "surfaces": list(f.surfaces), "definition": f.definition,
             "salient": list(f.salient),
             "family_fields": [dataclasses.asdict(x) for x in f.family_fields]}
            for f in CUAD_FAMILIES
        ],
        "maud_consideration_types": list(MAUD_CONSIDERATION_TYPES),
        "maud_type_of_consideration_to_token": dict(MAUD_TYPE_OF_CONSIDERATION_TO_TOKEN),
        "corporate_subclass_schemas": {
            k: {"label": v.label, "record_type": v.record_type, "intent": v.intent,
                "summary": v.summary, "core_applicable": list(v.core_applicable),
                "entities": [dataclasses.asdict(e) for e in v.entities]}
            for k, v in SUBCLASS_SCHEMAS.items()
        },
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dojo-src", type=Path, required=True,
                    help="directory containing the llm_dojo_scoring package")
    ap.add_argument("--check", action="store_true", help="compare instead of writing")
    args = ap.parse_args(argv)
    text = json.dumps(build_snapshot(args.dojo_src.resolve()), indent=1, sort_keys=True,
                      ensure_ascii=False) + "\n"
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"DRIFT: {OUT.relative_to(ROOT)} differs from the Dojo at {args.dojo_src}")
            return 1
        print("ok dojo snapshot")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
