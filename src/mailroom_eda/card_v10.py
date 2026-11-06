"""Render the v10 dataset card from the built stage (plan Part C4).

Every number on the card comes from the parquet in ``data/v10/stage``, its
``manifest.json``, ``labeling/fields.yaml`` and the campaign's ``approved.yaml``; none is
hand-written. ``write`` puts the card at ``<stage>/README.md`` (the Hub card) and adds its
hash to ``manifest.json``; ``check`` re-renders and compares (gate 12). At publish time
the same text becomes ``docs/dataset-cards/mailroom-dataset.md``.

CLI: ``python -m mailroom_eda.card_v10 --stage data/v10/stage [--check]``.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import v10_build as v10

REPO = "Lucius-Morningstar/mailroom-dataset"
CHANGELOG = f"https://huggingface.co/datasets/{REPO}/blob/main/CHANGELOG.md"
#: per-source licenses, unchanged from the v9 card
SOURCE_LICENSES = {
    "theatticusproject/cuad": ("contract", "CUAD v1", "CC BY 4.0"),
    "sec_edgar": ("contract, corporate_record", "SEC EDGAR exhibits", "US public domain"),
    "maud": ("merger_agreement", "MAUD v1 (Zenodo 7500064)", "CC BY 4.0"),
    "Lucius-Morningstar/enron-correspondence-dedup": ("correspondence", "Enron deduplicated corpus",
                                                      "research use (other); real named-individual PII"),
    "cms_desynpuf": ("insurance_claim", "CMS DE-SynPUF", "US public domain (synthetic)"),
    "gratex/GNOTHEIA-synthetic-insurance-dataset": ("insurance_claim", "GNOTHEIA", "Apache-2.0"),
    "bdr-ai-org/insurance-motor-claims-decision-v1": ("insurance_claim", "BDR motor claims", "MIT"),
    "feihuangfh/INSURBIAS": ("insurance_claim", "INSURBIAS", "CC BY 4.0"),
}
PROVENANCE_KINDS = ("gold", "deterministic", "llm", "reviewer")


def _kind(prov: str) -> str:
    """Who produced a value. An override names its labeler: a two-model relabel is ``llm``;
    a reviewer decision or an accepted gold correction is ``reviewer``."""
    p = str(prov or "")
    if p.startswith("override:"):
        return "llm" if p.startswith("override:relabel:") else "reviewer"
    if p.startswith("reviewer:"):
        return "reviewer"
    for k in ("gold", "deterministic", "llm"):
        if p.startswith(k):
            return k
    return "none"


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}%" if d else "n/a"


def stats(stage: Path) -> dict[str, Any]:
    """Every number the card prints, from the built stage."""
    from .v10_conformance import Stage, coverage_residue

    st = Stage(stage, stage)
    m = st.manifest
    routing = st.rows("routing")
    meta = {r["document_id"]: r for r in st.rows("ground_truth")}
    out: dict[str, Any] = {"rows": m["rows"], "configs": m["configs"], "inputs": m.get("inputs") or {}}
    out["total"] = len(routing)
    out["splits"] = {s: len(rows) for s, rows in st.config("routing").items()}
    out["classes"] = {c: {s: sum(1 for r in rows if r["doc_class"] == c)
                          for s, rows in st.config("routing").items()} for c in v10.CLASSES}
    out["subclasses"] = {c: dict(sorted(Counter(r["subclass"] for r in routing if r["doc_class"] == c).items()))
                         for c in v10.CLASSES}
    out["sources"] = dict(sorted(Counter(str(meta[r["document_id"]].get("annotation_source") or "")
                                         for r in routing).items()))
    fields, prov = {}, Counter()
    for c, rows in st.class_rows().items():
        per = {}
        for name, spec in v10.class_fields(c).items():
            applicable = [r for r in rows if r["presence"][name] != "not_applicable"]
            covered = sum(1 for r in applicable if r["presence"][name] in ("populated", "schema_documented_absence"))
            per[name] = {"type": spec["type"], "applicable": len(applicable), "covered": covered,
                         "description": str(spec.get("description") or spec.get("note") or "").strip().split("\n")[0]}
            for r in applicable:
                if r["presence"][name] == "populated":
                    prov[_kind(r["provenance"][name])] += 1
        fields[c] = per
    out["fields"] = fields
    out["provenance"] = {k: prov.get(k, 0) for k in PROVENANCE_KINDS}
    out["residue"] = sum(coverage_residue(st).values())
    out["examples"] = {c: rows[0]["document_id"] for c, rows in st.class_rows().items() if rows}
    return out


def _approvals(campaign: str = "v10") -> list[dict[str, Any]]:
    from .labeling import operate

    return operate.load_approvals(campaign)


def _yaml(s: Mapping[str, Any]) -> str:
    lines = ["---", "pretty_name: \"Mailroom Dataset v10\"", "license: other",
             "license_name: mixed-per-source",
             f"license_link: https://huggingface.co/datasets/{REPO}#sources-and-licenses",
             "language:", "- en", "task_categories:", "- text-classification", "- token-classification",
             "- question-answering", "task_ids:", "- multi-class-classification", "- named-entity-recognition",
             "- extractive-qa", "annotations_creators:", "- expert-generated", "- machine-generated",
             "language_creators:", "- found", "multilinguality:", "- monolingual", "source_datasets:",
             *(f"- {k}" for k in sorted(SOURCE_LICENSES)), "size_categories:", "- 1K<n<10K", "tags:",
             "- legal", "- contracts", "- correspondence", "- insurance", "- corporate-records", "- evaluation",
             "configs:"]
    for name in s["configs"]:
        lines.append(f"- config_name: {name}")
        lines.append("  data_files:")
        for split in s["rows"][name]:
            lines += [f"  - split: {split}", f"    path: parquet/{name}/{split}/*"]
    lines.append("dataset_info:")
    for name in s["configs"]:
        lines += [f"- config_name: {name}", "  splits:"]
        for split, n in s["rows"][name].items():
            lines += [f"  - name: {split}", f"    num_examples: {n}"]
    lines.append("---")
    return "\n".join(lines)


def render(stage: Path, campaign: str = "v10") -> str:
    """The card text for a built stage."""
    from .labeling.registry import campaign as load_campaign
    from .labeling.registry import fields_registry

    s = stats(Path(stage))
    approvals = _approvals(campaign)
    total = s["total"]
    tr, te = s["splits"].get("train", 0), s["splits"].get("test", 0)
    body = [_yaml(s), "", "# Mailroom Dataset v10", "",
            f"{total:,} business documents in five classes (contracts, merger agreements, correspondence, "
            "insurance claims and corporate records) for evaluating document triage, classification and "
            "field extraction. Each document carries typed ground-truth fields with a presence code, "
            "evidence spans and a provenance string per field.", "",
            "| class | train | test | total |", "|---|---:|---:|---:|"]
    for c in v10.CLASSES:
        n = s["classes"][c]
        body.append(f"| {c} | {n.get('train', 0):,} | {n.get('test', 0):,} | {sum(n.values()):,} |")
    body += [f"| **all** | {tr:,} | {te:,} | {total:,} |", "",
             "## Quick start", "", "```python", "from datasets import load_dataset",
             f'docs = load_dataset("{REPO}")                 # blind: document_id, doc_text',
             f'routing = load_dataset("{REPO}", "routing")   # class, subclass, specialist',
             f'contracts = load_dataset("{REPO}", "gt_contract")',
             "# join every config on document_id", "```", "",
             "Score extraction with `llm-dojo-scoring` (`parse_gt_fields` + `score_document`) against the "
             "deprecated `ground_truth` config, or read the typed `gt_<class>` columns directly.", "",
             "## Supported tasks", "",
             "- document classification (5 classes) and subclass routing (`routing`);",
             "- typed field extraction per class (`gt_<class>`), with evidence spans;",
             "- CUAD clause spans (`cuad_clauses`) and MAUD answers (`maud_answers`);",
             "- multi-document bundles, streams and fixtures for ingress simulation.", "",
             "## Structure", "", "| config | train | test |", "|---|---:|---:|"]
    for name in s["configs"]:
        r = s["rows"][name]
        body.append(f"| `{name}` | {r.get('train', 0):,} | {r.get('test', 0):,} |")
    body += ["", "`default` is blind: it carries only `document_id`, `doc_text` and an empty `prompt`. "
             "Filenames and source metadata are in `source_metadata`.", ""]
    for c in v10.CLASSES:
        body += [f"<details><summary><code>gt_{c}</code> fields</summary>", "",
                 "| field | type | coverage | description |", "|---|---|---:|---|"]
        for name, f in s["fields"][c].items():
            body.append(f"| `{name}` | {f['type']} | {_pct(f['covered'], f['applicable'])} "
                        f"({f['covered']:,}/{f['applicable']:,}) | {f['description']} |")
        subs = ", ".join(f"{k} {v:,}" for k, v in s["subclasses"][c].items())
        body += ["", f"Subclasses: {subs}. Example row: `{s['examples'].get(c, '')}`.", "", "</details>", ""]
    body += ["Coverage counts rows where a field applies and is `populated` or `schema_documented_absence`; "
             f"{s['residue']:,} applicable cells are still `pending_annotation`.", "",
             "## Splits", "",
             f"{tr:,} train and {te:,} test rows. A row is in test when `md5(filename) % 10 == 0`; the rule "
             "is unchanged from v9, so no row changed split.", "",
             "## Curation rationale", "",
             "The corpus mirrors a legal mailroom: mixed document types arrive unlabeled and must be "
             "routed and read. v10 types every field, removes stray control characters from about 200 "
             "texts (hashes recomputed; the old hash is `source_content_sha256`), and completes labels "
             "the earlier releases left empty.", "",
             "## Sources and licenses", "", "| source | classes | rows | license |", "|---|---|---:|---|"]
    for src, n in s["sources"].items():
        cls, name, lic = SOURCE_LICENSES.get(src, ("", src, "see source"))
        body.append(f"| {name} (`{src}`) | {cls} | {n:,} | {lic} |")
    prov = s["provenance"]
    populated = sum(prov.values())
    floors = sorted({json.dumps(c["gate"], sort_keys=True) for c in load_campaign(campaign)["chunks"]
                     if c["gate"]["kind"] in ("cuad_f1", "intent_kappa", "field_accuracy",
                                              "consideration_accuracy", "human_verdicts")})
    models = sorted({m for a in approvals for m in a.get("models") or []})
    body += ["", "## Annotation process", "",
             "| source of a populated value | values | share |", "|---|---:|---:|"]
    for k in PROVENANCE_KINDS:
        body.append(f"| {k} | {prov[k]:,} | {_pct(prov[k], populated)} |")
    body += ["", "Gold values come from the source corpora's experts or native fields; deterministic values "
             "are computed from the row; `llm` values come from two-model agreement with verbatim evidence; "
             "`reviewer` values are a person's verdict on a disagreement. A model configuration labels "
             "data only after it passes a calibration gate on gold data:", ""]
    body += [f"- `{f}`" for f in floors]
    body += ["", f"Models that labeled approved chunks: {', '.join(f'`{m}`' for m in models) or 'none yet'}. "
             f"Field registry pinned to llm-dojo-scoring {fields_registry()['dojo_pin']['version']}.", "",
             "## Personal information", "",
             "The correspondence class is drawn from the Enron corpus and contains real names and email "
             "addresses of individuals. The CMS insurance claims are synthetic (DE-SynPUF); the other "
             "insurance sources are synthetic or public.", "",
             "## Bias and limitations", "",
             "- Contracts and mergers are US public-company documents; other jurisdictions are rare.",
             "- Insurance claims are synthetic and simpler than real claim files.",
             "- Machine-labeled fields can be wrong where both models agree on the same mistake; their "
             "provenance says so, and the calibration floors bound the error measured on gold data.", "",
             "## Versions", "", "| version | change |", "|---|---|",
             "| v10 | typed per-class configs, blind `default`, evidence and provenance, label completion |",
             "| v9.4 | duplicate removal and label completion |",
             "| v9 (v1) | successor of mailroom-corpus v8 |", "",
             f"Release notes: [`CHANGELOG.md`]({CHANGELOG}).", "",
             "## Citation", "", "```bibtex", "@misc{mailroom_dataset,",
             "  title        = {Mailroom Dataset: An Evaluation and Ingress Simulation Corpus for Legal Document Triage},",
             "  author       = {Morningstar, Lucius},", "  year         = {2026},",
             f"  howpublished = {{Hugging Face: \\url{{https://huggingface.co/datasets/{REPO}}}}}",
             "}", "```", "",
             "Contributions: issues and pull requests at "
             "[Exios66/Mailroom-Corpus-EDA](https://github.com/Exios66/Mailroom-Corpus-EDA).", ""]
    return "\n".join(body)


def write(stage: Path, campaign: str = "v10") -> Path:
    """Write ``<stage>/README.md`` and record its hash in ``manifest.json``."""
    stage = Path(stage)
    path = stage / "README.md"
    path.write_text(render(stage, campaign), encoding="utf-8")
    mpath = stage / "manifest.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    m["files"]["README.md"] = v10.sha256_file(path)
    mpath.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


def check(stage: Path, campaign: str = "v10") -> tuple[bool, str]:
    """Gate 12: ``<stage>/README.md`` equals a fresh render."""
    path = Path(stage) / "README.md"
    if not path.exists():
        return False, "no README.md; run `python -m mailroom_eda.card_v10 --stage <stage>`"
    want = render(Path(stage), campaign)
    if path.read_text(encoding="utf-8") != want:
        return False, "README.md differs from a fresh render (re-run card_v10)"
    return True, f"card matches a fresh render ({len(want.splitlines())} lines)"


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="render the v10 dataset card from the built stage")
    ap.add_argument("--stage", type=Path, default=Path("data/v10/stage"))
    ap.add_argument("--campaign", default="v10")
    ap.add_argument("--check", action="store_true", help="exit 1 when README.md is not a fresh render")
    args = ap.parse_args(argv)
    if args.check:
        ok, msg = check(args.stage, args.campaign)
        print(msg)
        return 0 if ok else 1
    print(write(args.stage, args.campaign))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
