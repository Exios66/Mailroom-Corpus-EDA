"""v10 conformance gates 1-13 over a built stage (``data/v10/stage``).

Plan: ``docs/plans/v10-structure-and-label-completion.md`` Part C3. CLI:
``scripts/audit/verify_v10_conformance.py --stage data/v10/stage [--gates 1-8,12,13]``.

Every gate returns ``(passed, detail)``; nothing here uses ``assert``, so the gates give
the same answer under ``python -O``. Chunk 0 runs gates 1-8, 12 and 13 (no labels yet);
chunk 11 runs all 13. Gate 11 passes only when the coverage residue is empty or every
residual ``class.field`` count is accepted in ``campaign.yaml`` ``accepted_residue``.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import v10_build as v10

GATE_NAMES = {
    1: "row set equals v9.4",
    2: "cleanup allowlist and content hashes",
    3: "spans and evidence re-verify",
    4: "fields.yaml matches the pinned Dojo",
    5: "class schemas match fields.yaml",
    6: "legacy ground_truth round-trips to v9.4",
    7: "blind default config is label-free",
    8: "manifest is complete",
    9: "calibration re-derived per configuration",
    10: "merger consideration on every merger",
    11: "field coverage",
    12: "card is rendered from the data",
    13: "every config loads offline",
}
CHUNK0_GATES = (1, 2, 3, 4, 5, 6, 7, 8, 12, 13)
DOC_ID_RE = re.compile(r"^DOC-[0-9a-f]{16}$")
COVERED = ("populated", "schema_documented_absence")


def parse_gates(spec: str | None) -> list[int]:
    """``"1-8,12,13"`` -> ``[1, ..., 8, 12, 13]``; ``None`` means all 13."""
    if not spec:
        return list(GATE_NAMES)
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        out += list(range(int(a), int(b or a) + 1))
    bad = [g for g in out if g not in GATE_NAMES]
    if bad:
        raise SystemExit(f"unknown gates: {bad}")
    return sorted(set(out))


class Stage:
    """Lazy access to a built v10 stage and its v9.4 input."""

    def __init__(self, stage: Path, source: Path, *, labels_root: Path | None = None, campaign: str = "v10"):
        self.root, self.source, self.labels_root, self.campaign = Path(stage), Path(source), labels_root, campaign
        self._tables: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._source_rows: list[dict[str, Any]] | None = None
        self.manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))

    def config(self, name: str) -> dict[str, list[dict[str, Any]]]:
        """``{split: rows}`` of one config."""
        import pyarrow.parquet as pq

        if name not in self._tables:
            out = {}
            for split in v10.SPLITS:
                f = self.root / "parquet" / name / split / f"{split}-00000-of-00001.parquet"
                if f.exists():
                    out[split] = pq.read_table(f).to_pylist()
            self._tables[name] = out
        return self._tables[name]

    def rows(self, name: str) -> list[dict[str, Any]]:
        """All rows of a config, train then test."""
        c = self.config(name)
        return [r for s in v10.SPLITS for r in c.get(s, [])]

    def source_rows(self) -> list[dict[str, Any]]:
        """The v9.4 stage rows."""
        from .labeling import runner

        if self._source_rows is None:
            self._source_rows = runner.load_stage(self.source)
        return self._source_rows

    def class_rows(self) -> dict[str, list[dict[str, Any]]]:
        return {c: self.rows(f"gt_{c}") for c in v10.CLASSES}


def _fail(items: Sequence[str], ok_msg: str, limit: int = 8) -> tuple[bool, str]:
    if items:
        return False, f"{len(items)} problem(s): " + "; ".join(items[:limit])
    return True, ok_msg


def gate_1(st: Stage) -> tuple[bool, str]:
    src = {r["document_id"]: r["split"] for r in st.source_rows()}
    bad = []
    for name in ("default", "routing", "source_metadata", "ground_truth"):
        got = {r["document_id"]: s for s, rows in st.config(name).items() for r in rows}
        if got != src:
            bad.append(f"{name}: {len(set(got) ^ set(src))} ids differ, "
                       f"{sum(1 for k in got if k in src and got[k] != src[k])} splits differ")
    cls = {r["document_id"]: r["expected"] for r in st.source_rows()}
    for c, rows in st.class_rows().items():
        want = {k for k, v in cls.items() if v == c}
        if {r["document_id"] for r in rows} != want:
            bad.append(f"gt_{c}: row set differs from the v9.4 {c} rows")
    return _fail(bad, f"{len(src)} rows, splits {dict(Counter(src.values()))}, every config")


def gate_2(st: Stage) -> tuple[bool, str]:
    old = {r["document_id"]: r for r in st.source_rows()}
    new = {r["document_id"]: r for r in st.rows("default")}
    bad, changed = [], 0
    text_hash = {}
    for k, r in new.items():
        o = str(old[k].get("doc_text") or "") if k in old else None
        if o is None:
            continue
        want = v10.clean_text(o)[0] if v10.needs_cleanup(o) else o
        if r["doc_text"] != want:
            bad.append(f"{k}: doc_text differs from the allowlisted cleanup")
        changed += r["doc_text"] != o
        text_hash[k] = v10.sha256_text(r["doc_text"])
    for name in ("ground_truth", "routing", *(f"gt_{c}" for c in v10.CLASSES), *v10.SIDECARS):
        for r in st.rows(name):
            if "doc_text" in r and "content_sha256" in r and r["content_sha256"] != v10.sha256_text(r["doc_text"]):
                bad.append(f"{name}/{r.get('document_id') or r.get('filename')}: content_sha256 != sha256(doc_text)")
            elif "doc_text" not in r and r.get("content_sha256") and r.get("document_id") in text_hash \
                    and r["content_sha256"] != text_hash[r["document_id"]]:
                bad.append(f"{name}/{r['document_id']}: content_sha256 does not match its default doc_text")
            if r.get("source_content_sha256") and r.get("document_id") in old and \
                    r["source_content_sha256"] != old[r["document_id"]].get("content_sha256"):
                bad.append(f"{name}/{r['document_id']}: source_content_sha256 is not the v9.4 hash")
    return _fail(bad, f"{changed} rows cleaned, every hash matches its text in every config")


def gate_3(st: Stage) -> tuple[bool, str]:
    text = {r["document_id"]: r["doc_text"] for r in st.rows("default")}
    bad, n = [], 0
    for r in st.rows("cuad_clauses"):
        n += 1
        if text.get(r["document_id"], "")[r["start"]:r["end"]] != r["text"]:
            bad.append(f"cuad_clauses {r['document_id']}@{r['start']}")
    for c, rows in st.class_rows().items():
        for r in rows:
            t = text.get(r["document_id"], "")
            for sp in r.get("cuad_clauses") or []:
                n += 1
                if t[sp["start"]:sp["end"]] != sp["text"]:
                    bad.append(f"gt_{c} {r['document_id']} cuad@{sp['start']}")
            for name, spans in (r.get("evidence") or {}).items():
                for sp in spans or []:
                    n += 1
                    if t[sp["start"]:sp["end"]] != sp["text"]:
                        bad.append(f"gt_{c} {r['document_id']} {name} evidence@{sp['start']}")
    return _fail(bad, f"{n} spans and evidence quotes slice their text")


def gate_4(st: Stage) -> tuple[bool, str]:
    from .labeling.registry import dojo_snapshot, fields_registry, validate_registry

    reg, snap = fields_registry(), dojo_snapshot()
    bad = list(validate_registry(reg))
    pin = reg["dojo_pin"]
    if (snap["dojo_version"], snap["dojo_commit"]) != (pin["version"], pin["commit"]):
        bad.append(f"snapshot {snap['dojo_version']} differs from dojo_pin {pin['version']}")
    for cls, types in snap["default_field_types"].items():
        for name, spec in reg["classes"].get(cls, {}).get("fields", {}).items():
            if spec.get("dojo") == "v2" or spec.get("subclass_entity"):
                continue
            if types.get(name) != spec["type"]:
                bad.append(f"{cls}.{name}: {spec['type']} vs Dojo {types.get(name)}")
    return _fail(bad, f"fields.yaml matches llm-dojo-scoring {pin['version']}")


def gate_5(st: Stage) -> tuple[bool, str]:
    import pyarrow.parquet as pq

    bad = []
    for c in v10.CLASSES:
        want = v10.class_schema(c)
        for split in v10.SPLITS:
            f = st.root / "parquet" / f"gt_{c}" / split / f"{split}-00000-of-00001.parquet"
            if not f.exists():
                continue
            got = pq.read_schema(f).remove_metadata()
            if not got.equals(want):
                bad.append(f"gt_{c}/{split}: schema differs from fields.yaml")
        for r in st.rows(f"gt_{c}"):
            if set(r["presence"]) != set(v10.class_fields(c)):
                bad.append(f"gt_{c} {r['document_id']}: presence keys differ")
                break
    return _fail(bad, "every gt_<class> schema matches fields.yaml")


def gate_6(st: Stage) -> tuple[bool, str]:
    from .v9_2_revision import gt_parquet_record

    old = {r["document_id"]: r for r in st.source_rows()}
    allowed_top = {"doc_text", "content_sha256", "normalized_text_sha256", "expected_specialist", "gt_fields"}
    bad, added = [], Counter()
    for r in st.rows("ground_truth"):
        o = old.get(r["document_id"])
        if o is None:
            continue
        orec = gt_parquet_record(o)
        for k in set(orec) | set(r):
            if k not in allowed_top and orec.get(k) != r.get(k):
                bad.append(f"{r['document_id']}: {k} changed")
        og, ng = json.loads(orec["gt_fields"]), json.loads(r["gt_fields"])
        op, np_ = (json.loads(og.get("gt_presence") or "{}"), json.loads(ng.get("gt_presence") or "{}"))
        for k, v in og.items():
            if k == "gt_presence":
                continue
            if k not in ng:
                bad.append(f"{r['document_id']}: gt_fields.{k} dropped")
            elif k == "cuad_clause_labels":
                if len(json.loads(v or "{}") if isinstance(v, str) else v or {}) != \
                        len(json.loads(ng[k] or "{}") if isinstance(ng[k], str) else ng[k] or {}):
                    bad.append(f"{r['document_id']}: cuad_clause_labels categories changed")
            elif v not in (None, "", "[]") and ng[k] != v:
                bad.append(f"{r['document_id']}: gt_fields.{k} changed")
        for k, code in op.items():
            if code in COVERED and np_.get(k) != code:
                bad.append(f"{r['document_id']}: presence of {k} changed from {code}")
        for k in set(ng) - set(og):
            added[k] += 1
    return _fail(bad, f"v9.4 semantics kept; added keys {dict(added) or 'none'}")


def gate_7(st: Stage) -> tuple[bool, str]:
    import pyarrow.parquet as pq

    bad = []
    for split in v10.SPLITS:
        f = st.root / "parquet" / "default" / split / f"{split}-00000-of-00001.parquet"
        if f.exists() and tuple(pq.read_schema(f).names) != v10.DEFAULT_COLUMNS:
            bad.append(f"default/{split}: columns {pq.read_schema(f).names}")
    for r in st.rows("default"):
        if not DOC_ID_RE.match(str(r["document_id"])):
            bad.append(f"document_id {r['document_id']!r} is not opaque")
        if r["prompt"]:
            bad.append(f"{r['document_id']}: prompt is not empty")
    if st.manifest.get("configs", [None])[0] != "default":
        bad.append("default is not the first config")
    return _fail(bad, f"default has only {', '.join(v10.DEFAULT_COLUMNS)} and is the first config")


def gate_8(st: Stage) -> tuple[bool, str]:
    bad = []
    listed = st.manifest.get("files") or {}
    on_disk = {p.relative_to(st.root).as_posix() for p in st.root.rglob("*") if p.is_file()} - {"manifest.json"}
    for path in sorted(on_disk - set(listed)):
        bad.append(f"{path}: not in manifest.json")
    for path, sha in sorted(listed.items()):
        p = st.root / path
        if not p.exists():
            bad.append(f"{path}: listed but missing")
        elif v10.sha256_file(p) != sha:
            bad.append(f"{path}: sha256 differs from manifest.json")
    for name in st.manifest.get("configs") or []:
        info_path = st.root / "parquet" / name / "dataset_info.json"
        if not info_path.exists():
            bad.append(f"{name}: no dataset_info.json")
            continue
        info = json.loads(info_path.read_text(encoding="utf-8"))
        got = {s: len(v) for s, v in st.config(name).items()}
        want = {s: v["num_examples"] for s, v in (info.get("splits") or {}).items()}
        if got != want or got != st.manifest["rows"][name]:
            bad.append(f"{name}: num_examples {want} vs parquet {got}")
    return _fail(bad, f"{len(listed)} files hashed; dataset_info matches every config")


def _llm_provenances(st: Stage) -> Counter:
    out: Counter = Counter()
    for c, rows in st.class_rows().items():
        for r in rows:
            for v in (r.get("provenance") or {}).values():
                for part in str(v or "").split("+"):
                    if part.startswith("llm:"):
                        out[part[4:]] += 1
    return out


def gate_9(st: Stage) -> tuple[bool, str]:
    from .labeling import ingest, operate

    approvals = operate.load_approvals(st.campaign)
    used = _llm_provenances(st)
    if not used:
        return True, "no llm labels in this build"
    bad = []
    try:
        ingest.check_calibrated(approvals)
    except SystemExit as e:
        bad.append(str(e))
    known = {ingest.configuration(m, a) for a in approvals for m in a.get("models") or []}
    for cfg in sorted(set(used) - known):
        bad.append(f"provenance llm:{cfg} names no approved configuration")
    if st.labels_root is not None:
        for a in approvals:
            if str(a["chunk"]) in {str(s["id"]) for s in operate.campaign(st.campaign).get("steps") or []}:
                continue
            entry, arm = operate.chunk_entry(st.campaign, str(a["chunk"]), a.get("arm"))
            g = operate.chunk_dir(Path(st.labels_root), entry, arm) / "gate.json"
            res = json.loads(g.read_text(encoding="utf-8")) if g.exists() else None
            if not res or not res.get("passed"):
                bad.append(f"chunk {a['chunk']}: gate.json missing or not passed")
            elif sorted(res.get("prompt_sha256") or []) != sorted(a.get("prompt_sha256") or []):
                bad.append(f"chunk {a['chunk']}: gate.json prompt shas differ from approved.yaml")
    return _fail(bad, f"{sum(used.values())} llm values from {len(used)} calibrated configurations")


def gate_10(st: Stage) -> tuple[bool, str]:
    bad = []
    rows = st.rows("gt_merger_agreement")
    for r in rows:
        v = r.get("merger_consideration")
        prov = (r.get("provenance") or {}).get("merger_consideration") or ""
        ev = (r.get("evidence") or {}).get("merger_consideration") or []
        if not v:
            bad.append(f"{r['document_id']}: no merger_consideration")
        elif not (prov in ("deterministic",) or prov.startswith("gold:") or ev or "reviewer:" in prov):
            bad.append(f"{r['document_id']}: merger_consideration has neither an expert source nor evidence")
        if v and r["subclass"] != v:
            bad.append(f"{r['document_id']}: subclass {r['subclass']!r} != merger_consideration {v!r}")
    return _fail(bad, f"{len(rows)} mergers, each with a sourced merger_consideration equal to its subclass")


def coverage_residue(st: Stage) -> Counter:
    """``{class.field: rows}`` of applicable fields that are neither populated nor documented absent."""
    out: Counter = Counter()
    for c, rows in st.class_rows().items():
        for r in rows:
            for name, code in (r.get("presence") or {}).items():
                if code not in COVERED and code != "not_applicable":
                    out[f"{c}.{name}"] += 1
    return out


def gate_11(st: Stage) -> tuple[bool, str]:
    from .labeling.registry import campaign

    residue = coverage_residue(st)
    accepted = {str(k): int(v) for k, v in (campaign(st.campaign).get("accepted_residue") or {}).items()}
    bad = [f"{k}: {n} rows" + (f" (accepted {accepted[k]})" if k in accepted else "")
           for k, n in sorted(residue.items()) if n > accepted.get(k, 0)]
    total = sum(residue.values())
    return _fail(bad, f"residue {total} rows" + (" (all accepted)" if total else ""), limit=20)


def gate_12(st: Stage) -> tuple[bool, str]:
    from . import card_v10

    return card_v10.check(st.root)


def gate_13(st: Stage) -> tuple[bool, str]:
    import os
    import tempfile

    import datasets

    bad = []
    old = os.environ.get("HF_DATASETS_OFFLINE")
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    try:
        for name in st.manifest.get("configs") or []:
            files = {s: str(st.root / "parquet" / name / s / f"{s}-00000-of-00001.parquet")
                     for s in st.manifest["rows"][name]}
            with tempfile.TemporaryDirectory() as cache:
                try:
                    ds = datasets.load_dataset("parquet", data_files=files, cache_dir=cache)
                    got = {s: len(ds[s]) for s in ds}
                    if got != st.manifest["rows"][name]:
                        bad.append(f"{name}: loaded {got}")
                except Exception as e:  # noqa: BLE001 - report every config that fails to load
                    bad.append(f"{name}: {type(e).__name__}: {str(e.__cause__ or e)[:160]}")
    finally:
        if old is None:
            os.environ.pop("HF_DATASETS_OFFLINE", None)
        else:
            os.environ["HF_DATASETS_OFFLINE"] = old
    return _fail(bad, f"{len(st.manifest.get('configs') or [])} configs load offline with datasets")


GATES: dict[int, Callable[[Stage], tuple[bool, str]]] = {
    1: gate_1, 2: gate_2, 3: gate_3, 4: gate_4, 5: gate_5, 6: gate_6, 7: gate_7, 8: gate_8,
    9: gate_9, 10: gate_10, 11: gate_11, 12: gate_12, 13: gate_13,
}


def run(stage: Path, source: Path, gates: Sequence[int] | None = None, *, labels_root: Path | None = None,
        campaign: str = "v10") -> dict[str, Any]:
    """Run the gates; returns ``{"passed": bool, "gates": {n: {...}}}``."""
    st = Stage(stage, source, labels_root=labels_root, campaign=campaign)
    out: dict[int, dict[str, Any]] = {}
    for g in gates or list(GATES):
        try:
            ok, detail = GATES[g](st)
        except Exception as e:  # noqa: BLE001 - a crashing gate is a failing gate
            ok, detail = False, f"{type(e).__name__}: {e}"
        out[g] = {"name": GATE_NAMES[g], "passed": bool(ok), "detail": detail}
    return {"stage": str(stage), "passed": all(v["passed"] for v in out.values()), "gates": out}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: JSON report, exit 1 on any failed gate."""
    import argparse

    from .labeling import runner

    ap = argparse.ArgumentParser(description="v10 conformance gates (plan Part C3)")
    ap.add_argument("--stage", type=Path, default=Path("data/v10/stage"))
    ap.add_argument("--source", type=Path, default=runner.DEFAULT_STAGE, help="the v9.4 stage it was built from")
    ap.add_argument("--gates", help="e.g. 1-8,12,13 (chunk 0); default all 13")
    ap.add_argument("--labels-root", type=Path, default=None, help="labels root, for gate 9's gate.json check")
    ap.add_argument("--campaign", default="v10")
    args = ap.parse_args(argv)
    res = run(args.stage, args.source, parse_gates(args.gates), labels_root=args.labels_root,
              campaign=args.campaign)
    print(json.dumps(res, indent=1))
    return 0 if res["passed"] else 1


def summary_lines(res: Mapping[str, Any]) -> list[str]:
    """``PASS  3 spans and evidence re-verify  ...`` lines."""
    return [f"{'PASS' if v['passed'] else 'FAIL'}  {g:>2} {v['name']}: {v['detail']}" for g, v in res["gates"].items()]
