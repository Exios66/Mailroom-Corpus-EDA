"""Read approved campaign chunks into v10 labels (the build input for chunk 11).

For every chunk in ``approved.yaml`` this reads the chunk's output folder under the
labels root (``docs/reports/audits/v10_labels``, restored from the private staging repo
first) and returns ``{document_id: {field: Label}}``:

- field tasks: ``fields.csv`` rows whose ``final_decision`` is ``accept`` (a value with
  the chosen model's evidence quote) or ``absent`` (a documented absence);
- ``cuad``: accepted ``cuad-labels.jsonl`` records, as ``cuad_clauses`` spans;
- ``intent``: ``intent-labels.jsonl`` records whose two passes agree;
- ``intent`` relabel chunks (4a-4c): ``decisions.csv``. A train row takes the reviewer's
  decision, else the unanimous ``confirm``/``change`` answer; a test row takes only a decision
  a person initialled in ``reviewed_by`` (a model's test-split proposal is never written).
  ``keep`` leaves the existing label.
- ``gold-corrections.csv`` in the labels root: gold labels the user corrected (``decided_by``).

**Overrides.** Relabel decisions and gold corrections replace an existing (gold) value, so
they are ``override`` labels: their provenance starts ``override:`` and the conformance
gates accept exactly those changes.

**Configuration-bound calibration.** A value is used only when its configuration
(model, provider, reasoning, prompt sha256, temperature) matches an approved calibration,
holdout or pilot chunk of the same task. Otherwise ``ingest`` raises: an uncalibrated
configuration must never reach the dataset, however its own gate scored.

**Provenance** strings:

- ``llm:<configuration>`` (``+`` joins the two models of an agreed value);
- ``reviewer:<initials>+<source>`` for a reviewer verdict, where the source is the chosen
  model's ``llm:<configuration>``, ``value`` (typed by the reviewer), ``absent``, or
  ``interim:<gate_run>`` (the sandbox overlay, see ``labeling/interim.py``).

The build (``v10_build``) fills only cells that are ``pending_annotation``: gold labels
always win. The one exception is the merger ``subclass`` of the 56 MAUD mergers whose
source subclass is ``other``: chunk 6c1's adjudicated ``merger_consideration`` becomes
their v10 ``subclass`` (``source_subclass`` and the legacy ``expected_subclass`` stay
``other``), and the 95 mergers with an expert answer keep it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import export, verify
from .registry import campaign, fields_registry

#: chunk sets that calibrate a configuration for their task
CALIBRATING_SETS = frozenset({"calibration", "holdout", "pilot"})
#: reviewer verdicts that take a value, and where it comes from
VALUE_VERDICTS = {"a", "b", "value", "interim", "correct"}


@dataclass(frozen=True)
class Label:
    """One ingested label."""

    value: Any
    presence: str  # populated | schema_documented_absence
    evidence: tuple[str, ...] = ()
    provenance: str = ""
    chunk: str = ""
    override: bool = False  # replaces an existing gold value (relabel, gold correction)


@dataclass
class Ingested:
    """Labels per document and field, and what was read."""

    labels: dict[str, dict[str, Label]] = field(default_factory=dict)
    chunks: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def put(self, document_id: str, name: str, label: Label) -> None:
        """Add a label; two approved chunks labeling the same field must agree."""
        have = self.labels.setdefault(document_id, {}).get(name)
        if have is not None and have.value != label.value:
            raise SystemExit(f"{document_id}/{name}: chunk {have.chunk} says {have.value!r}, "
                             f"chunk {label.chunk} says {label.value!r}; adjudicate one of them")
        if have is None:
            self.labels[document_id][name] = label

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1


def model_provider(model: str, approval: Mapping[str, Any]) -> str:
    """Where ``model`` ran in an approved chunk: its ``providers`` entry (a mixed chunk), else
    the chunk's ``provider``."""
    return str((approval.get("providers") or {}).get(model) or approval.get("provider", "openrouter"))


def configuration(model: str, approval: Mapping[str, Any]) -> str:
    """The configuration string of one model in an approved chunk."""
    temps = approval.get("temperature") or {}
    shas = ",".join(s[:12] for s in approval.get("prompt_sha256") or [])
    return (f"{model}@{model_provider(model, approval)}/reasoning={approval.get('reasoning')}"
            f"/prompt={shas}/t={temps.get(model)}")


def _calibrated(model: str, approval: Mapping[str, Any], approvals: Sequence[Mapping[str, Any]]) -> bool:
    """An approved calibration/holdout/pilot chunk of the same task ran exactly this configuration."""
    shas = set(approval.get("prompt_sha256") or [])
    temp = (approval.get("temperature") or {}).get(model)
    for a in approvals:
        if a.get("task") != approval.get("task") or a.get("set") not in CALIBRATING_SETS:
            continue
        if model not in (a.get("models") or []):
            continue
        if (model_provider(model, a), str(a.get("reasoning"))) != \
                (model_provider(model, approval), str(approval.get("reasoning"))):
            continue
        if not shas <= set(a.get("prompt_sha256") or []):
            continue
        if (a.get("temperature") or {}).get(model) != temp:
            continue
        return True
    return False


def check_calibrated(approvals: Sequence[Mapping[str, Any]],
                     second_opinions: Sequence[Mapping[str, Any]] = ()) -> None:
    """Raise unless every model of every approved chunk has a calibrated configuration.

    ``second_opinions`` are calibration arms approved with ``gate --second-opinion``: they
    calibrate their model without becoming the chunk's winner."""
    pool = [*approvals, *second_opinions]
    bad = [f"chunk {a['chunk']}: {configuration(m, a)}" for a in approvals
           for m in a.get("models") or [] if not _calibrated(m, a, pool)]
    if bad:
        raise SystemExit("labels from an uncalibrated configuration (no approved calibration, holdout or "
                         "pilot of the same task ran it):\n  " + "\n  ".join(bad))


def decode(cell: Any, type_: str) -> Any:
    """A ``fields.csv`` cell back to its native value for ``type_``."""
    if cell is None or cell == "":
        return None
    if not isinstance(cell, str):
        return cell
    if type_.startswith("entity_list"):
        try:
            return json.loads(cell)
        except json.JSONDecodeError:
            raise SystemExit(f"list cell is not JSON: {cell[:80]!r}") from None
    if type_ == "money":
        return verify.parse_money(cell)
    return cell


def _field_specs() -> dict[str, dict[str, Any]]:
    """``{class: {field: spec}}`` from fields.yaml."""
    return {c: dict(spec["fields"]) for c, spec in fields_registry()["classes"].items()}


def _field_labels(out: Ingested, d: Path, approval: Mapping[str, Any], label: str) -> None:
    models = list(approval.get("models") or [])
    cfg = {m: f"llm:{configuration(m, approval)}" for m in models}
    who = approval.get("approved_by") or "unknown"
    specs = _field_specs()
    for f in export.read_csv(d / "fields.csv"):
        final = f.get("final_decision")
        if final not in ("accept", "absent"):
            out.skip(f"final_{final or 'none'}")
            continue
        spec = specs.get(f["doc_class"], {}).get(f["field"])
        if spec is None:
            out.skip("unregistered_field")
            continue
        t = f.get("type") or spec["type"]
        verdict = f.get("reviewer_verdict") or ""
        slot_models = {"a": f.get("model_a"), "b": f.get("model_b")}
        if final == "absent":
            prov = f"reviewer:{who}+absent" if verdict == "absent" else \
                "+".join(cfg.get(m, f"llm:{m}") for m in slot_models.values() if m)
            out.put(f["document_id"], f["field"], Label(None, "schema_documented_absence", (), prov, label))
            continue
        value = decode(f.get("final_value"), t)
        if verdict in ("a", "b"):
            src = cfg.get(slot_models[verdict] or "", f"llm:{slot_models[verdict]}")
            prov, ev = f"reviewer:{who}+{src}", (f.get(f"model_{verdict}_evidence") or "",)
        elif verdict == "value":
            prov, ev = f"reviewer:{who}+value", ()
        elif verdict == "interim":
            prov, ev = f"reviewer:{who}+{f.get('final_source') or 'interim:unknown'}", ()
        else:
            agreed = [m for m in slot_models.values() if m]
            prov = "+".join(cfg.get(m, f"llm:{m}") for m in agreed)
            if verdict == "correct":
                prov = f"reviewer:{who}+{prov}"
            ev = tuple(e for e in (f.get("model_a_evidence"), f.get("model_b_evidence")) if e)
        out.put(f["document_id"], f["field"], Label(value, "populated", tuple(e for e in ev if e), prov, label))


def _cuad_labels(out: Ingested, d: Path, approval: Mapping[str, Any], label: str) -> None:
    for rec in export.read_journal(d / "cuad-labels.jsonl"):
        if not rec.get("accepted"):
            out.skip("cuad_rejected")
            continue
        spans = [{"category": cat, "start": int(s["start"]), "end": int(s["start"]) + len(s["text"]),
                  "text": s["text"]}
                 for cat in sorted(rec.get("cuad_clause_labels") or {}) for s in rec["cuad_clause_labels"][cat]]
        prov = f"llm:{configuration(rec['model'], approval)}"
        out.put(rec["document_id"], "cuad_clauses", Label(spans, "populated", (), prov, label))


def _intent_labels(out: Ingested, d: Path, approval: Mapping[str, Any], label: str) -> None:
    for rec in export.read_journal(d / "intent-labels.jsonl"):
        a, b = (rec.get("pass_a") or {}), (rec.get("pass_b") or {})
        if not a.get("intent") or a.get("intent") != b.get("intent"):
            out.skip("intent_passes_disagree")
            continue
        prov = f"llm:{configuration(rec['model'], approval)}"
        ev = tuple(str(e) for e in (a.get("evidence"), b.get("evidence")) if e)
        out.put(rec["document_id"], "intent", Label(a["intent"], "populated", ev, prov, label))


def _relabel_labels(out: Ingested, d: Path, approval: Mapping[str, Any], label: str) -> None:
    """Changed intents from a relabel chunk's ``decisions.csv`` (see the module docstring)."""
    from .spend import INITIALS_RE  # noqa: PLC0415

    models = "".join(f"+llm:{configuration(m, approval)}" for m in approval.get("models") or [])
    for r in export.read_csv(d / "decisions.csv"):
        existing, rev = str(r.get("existing") or ""), str(r.get("reviewer_decision") or "").strip()
        who = str(r.get("reviewed_by") or "").strip()
        if rev:
            final = existing if rev == "keep" else rev
            if r["split"] == "test" and not INITIALS_RE.match(who):
                out.skip("relabel_test_without_initials")
                continue
            prov = f"override:reviewer:{who or 'unknown'}+relabel:{label}"
        elif r["split"] == "test":
            out.skip("relabel_test_proposal")
            continue
        elif r.get("decision") in ("confirm", "change") and r.get("new_intent"):
            final, prov = str(r["new_intent"]), f"override:relabel:{label}{models}"
        else:
            final, prov = existing, ""
        if final == existing:
            out.skip("relabel_unchanged")
            continue
        ev = (str(r["evidence"]),) if r.get("evidence") else ()
        out.put(r["document_id"], "intent", Label(final, "populated", ev, prov, label, override=True))


def _gold_corrections(out: Ingested, labels_root: Path) -> None:
    """User-accepted corrections of gold labels (``gold-corrections.csv``), as overrides."""
    from .operate import GOLD_CORRECTIONS  # noqa: PLC0415

    path = Path(labels_root) / GOLD_CORRECTIONS
    if not path.exists():
        return
    for c in export.read_csv(path):
        if not (c.get("decided_by") and c.get("gold_now")):
            raise SystemExit(f"{path}: every correction needs decided_by and gold_now ({c.get('filename')})")
        out.put(c["document_id"], c["field"], Label(c["gold_now"], "populated", (),
                                                    f"override:gold_correction:{c['decided_by']}",
                                                    "gold-corrections", override=True))
    out.chunks.append("gold-corrections")


def ingest(name: str, labels_root: Path, approvals: Sequence[Mapping[str, Any]]) -> Ingested:
    """Labels from every approved chunk of campaign ``name`` (offline; raises on uncalibrated configs)."""
    from . import operate  # noqa: PLC0415

    check_calibrated(approvals, operate.load_second_opinions(name))
    out = Ingested()
    step_ids = {str(s["id"]) for s in campaign(name).get("steps") or []}
    for a in approvals:
        cid = str(a["chunk"])
        if cid in step_ids:
            continue
        entry, arm = operate.chunk_entry(name, cid, a.get("arm"))
        if entry.get("set") in ("calibration", "holdout") and entry["task"] in ("cuad", "intent"):
            continue  # gold-set calibration: the rows already carry expert labels
        d = operate.chunk_dir(Path(labels_root), entry, arm)
        label = f"{cid}{f'.{arm}' if arm else ''}"
        if not (d / "calls.jsonl").exists():
            raise SystemExit(f"approved chunk {label}: {d} is missing; restore it (`labeling checkpoint --restore`)")
        if entry["task"] == "cuad":
            _cuad_labels(out, d, a, label)
        elif entry["task"] == "intent" and entry.get("set") == "relabel":
            _relabel_labels(out, d, a, label)
        elif entry["task"] == "intent":
            _intent_labels(out, d, a, label)
        else:
            _field_labels(out, d, a, label)
        out.chunks.append(label)
    _gold_corrections(out, labels_root)
    return out


def merger_subclass(row: Mapping[str, Any], labels: Mapping[str, Label]) -> tuple[str, str] | None:
    """(subclass, provenance) for a merger whose source subclass is ``other`` and has a 6c1 label."""
    if row.get("expected") != "merger_agreement" or str(row.get("source_subclass")) != "other":
        return None
    if (row.get("derived") or {}).get("merger_consideration"):
        return None  # an expert MAUD answer already decides it
    lab = labels.get("merger_consideration")
    if lab is None or lab.presence != "populated" or not lab.value:
        return None
    return str(lab.value), lab.provenance
