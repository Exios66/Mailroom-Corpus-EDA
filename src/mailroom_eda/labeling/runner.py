"""Command line for labeling chunks: select, estimate, run, review.

    python -m mailroom_eda.labeling select   --task T --set S [--chunk i/n]
    python -m mailroom_eda.labeling estimate --task T --set S [--chunk i/n] [--models a,b]
    python -m mailroom_eda.labeling run      --task T --set S --chunk i/n --provider P \
        --model A [--second-model B] [--reasoning off|low] --initials XX \
        --api-cost-cap-usd N --max-wall-seconds N --out DIR [--dry-run]
    python -m mailroom_eda.labeling review   --task T --set S --out DIR
    python -m mailroom_eda.labeling costs    [--check]

``--stage`` defaults to ``data/v9_4/stage`` (the v10 build reads the same stage).
``run`` never starts without a human's initials, a USD cap and a wall limit, and
``--dry-run`` makes no network call. Output for chunk ``<task>-<set>-<i>of<n>`` goes to
``--out`` (default ``docs/reports/audits/v10_labels``):

* ``calls.jsonl``: every request's parsed response, configuration and cost.
* ``cuad-labels.jsonl`` / ``intent-labels.jsonl``: records in the shape the v9.4
  ingest and calibration code reads, plus configuration fields.
* ``documents.csv``, ``fields.csv`` and ``adjudication.csv`` (extraction tasks; the
  last holds only the rows a human must decide), ``spans.csv`` (CUAD): written by
  ``review``, which needs no network and can be re-run at any time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import export, providers, spend, tasks, verify
from .registry import campaign, models_registry, vocabulary

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STAGE = ROOT / "data" / "v9_4" / "stage"
DEFAULT_OUT = ROOT / "docs" / "reports" / "audits" / "v10_labels"
SETS = ("calibration", "pilot", "remainder", "pending", "flagged", "all")
PILOT_PER_STRATUM = {"contract_entities": 2, "merger_entities": 3, "correspondence": 5,
                     "claim_checklist": 5, "corporate": 3}
#: Output-token budget per call, by task (also the worst case the spend gate reserves).
MAX_TOKENS = {"cuad": 6000, "intent": 400, "contract_entities": 3000, "merger_entities": 3500,
              "correspondence": 1200, "claim_checklist": 1500, "corporate": 2500}
#: Typical output tokens per call, for estimates (measured from the field counts).
TYPICAL_OUTPUT = {"cuad": 2500, "intent": 120, "contract_entities": 1200, "merger_entities": 1500,
                  "correspondence": 450, "claim_checklist": 500, "corporate": 800}
RETRY_FACTOR = 1.25


def _now() -> str:
    """UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_stage(stage: Path) -> list[dict[str, Any]]:
    """Stage rows with ``gt_fields`` as dicts."""
    rows = []
    with (stage / "ground_truth_hardened.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                if isinstance(r.get("gt_fields"), str):
                    r["gt_fields"] = json.loads(r["gt_fields"])
                rows.append(r)
    return rows


def _sha_key(row: Mapping[str, Any]) -> str:
    """Stable order key."""
    return hashlib.sha256(str(row["filename"]).encode()).hexdigest()


def pilot_rows(rows: Sequence[Mapping[str, Any]], task: str) -> list[Mapping[str, Any]]:
    """Deterministic pilot: the first ``PILOT_PER_STRATUM[task]`` rows per subclass by sha."""
    per = PILOT_PER_STRATUM[task]
    by_sub: dict[str, list] = defaultdict(list)
    for r in sorted(rows, key=_sha_key):
        by_sub[str(r.get("expected_subclass") or "")].append(r)
    return [r for sub in sorted(by_sub) for r in by_sub[sub][:per]]


def select(rows: Sequence[Mapping[str, Any]], task: str, set_name: str) -> list[Mapping[str, Any]]:
    """Rows for one task and set, sorted by filename."""
    from .. import v9_4_revision as v94  # noqa: PLC0415

    cls = tasks.TASK_CLASSES[task]
    pool = [r for r in rows if r["expected"] == cls]
    if task == "cuad":
        if set_name == "calibration":
            out = v94.cuad_calibration_sample(rows)
        elif set_name == "pending":
            out = [r for r in pool if v94.is_pending_cuad(r)]
        else:
            raise SystemExit("cuad sets: calibration, pending")
    elif task == "intent":
        if set_name == "calibration":
            out = v94.manual_intent_rows(rows)
        elif set_name == "flagged":
            out = v94.flagged_intent_rows(rows)
        else:
            raise SystemExit("intent sets: calibration, flagged")
    else:
        pool = [r for r in pool if tasks._task_fields(r, task)]
        if set_name == "pilot":
            out = pilot_rows(pool, task)
        elif set_name == "remainder":
            pilot = {r["filename"] for r in pilot_rows(pool, task)}
            out = [r for r in pool if r["filename"] not in pilot]
        elif set_name == "calibration" and task == "contract_entities":
            out = [r for r in v94.cuad_calibration_sample(rows) if tasks._task_fields(r, task)]
        elif set_name == "all":
            out = pool
        else:
            raise SystemExit(f"{task} sets: pilot, remainder, all" +
                             (", calibration" if task == "contract_entities" else ""))
    return sorted(out, key=lambda r: str(r["filename"]))


def chunk(rows: Sequence[Any], spec: str | None) -> list[Any]:
    """Slice ``i/n`` (1-based) of ``rows``; ``None`` keeps everything."""
    if not spec:
        return list(rows)
    i, n = (int(x) for x in spec.split("/"))
    if not 1 <= i <= n:
        raise SystemExit("--chunk must be i/n with 1 <= i <= n")
    size = -(-len(rows) // n)
    return list(rows[(i - 1) * size: i * size])


def _variants(task: str) -> tuple[str, ...]:
    """Intent runs two passes per document."""
    return ("a", "b") if task == "intent" else ("",)


def estimate(rows: Sequence[Mapping[str, Any]], task: str, models: Sequence[str]) -> dict[str, Any]:
    """Projected tokens and cost per model for these rows (no network)."""
    prompt_tokens = calls = 0
    for r in rows:
        for v in _variants(task):
            req = tasks.build(task, r, v)
            prompt_tokens += sum(spend.estimate_tokens(m["content"]) for m in req.messages)
            calls += 1
    out_tokens = calls * TYPICAL_OUTPUT[task]
    p_tok, o_tok = int(prompt_tokens * RETRY_FACTOR), int(out_tokens * RETRY_FACTOR)
    costs = {}
    for m in models:
        try:
            costs[m] = round(spend.token_cost(m, p_tok, o_tok), 4)
        except spend.SpendError:
            costs[m] = None
    return {"task": task, "documents": len(rows), "calls": calls, "prompt_tokens": p_tok,
            "output_tokens": o_tok, "cost_usd": costs}


def cuad_vocab(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """CUAD category names: the golden rows' vocabulary, else the Dojo catalog."""
    from .. import v9_4_revision as v94  # noqa: PLC0415

    golden = [r for r in rows if r.get("source_corpus") == v94.GOLDEN_CUAD_SOURCE]
    return list(v94.cuad_vocabulary(golden)) if golden else [
        c["name"] for c in tasks.dojo_snapshot()["cuad_categories"]]


def cuad_problems(row: Mapping[str, Any], response: Any, vocab: Sequence[str]) -> list[str]:
    """Rejected spans in one CUAD answer (empty when every span verifies)."""
    from .. import v9_4_revision as v94  # noqa: PLC0415

    doc = str(row["doc_text"])
    folded = verify.fold(doc)
    labels: dict[str, list[dict]] = defaultdict(list)
    problems = []
    for cl in (response or {}).get("clauses") or []:
        cat, text = str(cl.get("category")), str(cl.get("text") or "")
        loc = verify.locate(doc, text, folded)
        if loc is None:
            problems.append(f"{cat}: not verbatim: {text[:160]!r}")
        else:
            labels[cat].append({"start": loc[0], "text": doc[loc[0]:loc[1]]})
    _, dropped, more = v94.verify_cuad_labels(doc, dict(labels), vocab)
    return problems + [str(x) for x in list(dropped) + list(more)]


def chunk_name(task: str, set_name: str, spec: str | None) -> str:
    """Directory name for a chunk."""
    if not spec:
        return f"{task}-{set_name}"
    i, n = spec.split("/")
    return f"{task}-{set_name}-{i}of{n}"


def _done(calls_path: Path) -> set[tuple[str, str, str]]:
    """(document_id, model, variant) triples already answered without error."""
    return {(c["document_id"], c["model"], c.get("variant", ""))
            for c in export.read_journal(calls_path) if not c.get("error")}


def run_chunk(rows: Sequence[Mapping[str, Any]], *, task: str, models: Sequence[str],
              backend: providers.Backend, reasoning: str, budget: spend.Budget, out_dir: Path,
              vocab: Sequence[str] = (), chat=providers.chat) -> dict[str, Any]:
    """Send every (row, model, variant) not yet answered; append to ``calls.jsonl``.

    CUAD answers with rejected spans get one repair call (variant ``repair``) that replays
    the first answer. Stops cleanly at the cap or wall limit (``SpendError``); everything
    finished so far is kept, so a re-run continues where it stopped.
    """
    if task == "cuad" and not vocab:
        raise ValueError("cuad runs need the CUAD vocabulary")
    calls_path = out_dir / "calls.jsonl"
    done = _done(calls_path)
    stats: Counter = Counter()
    for r in rows:
        for v in _variants(task):
            for model in models:
                req = tasks.build(task, r, v)
                if (req.document_id, model, v) in done:
                    stats["skipped"] += 1
                    continue
                ptoks = sum(spend.estimate_tokens(m["content"]) for m in req.messages)
                try:
                    budget.reserve(model, ptoks, MAX_TOKENS[task])
                except spend.SpendError as err:
                    stats["stopped"] = 1
                    return {**stats, "spent_usd": round(budget.spent_usd, 4), "stop_reason": str(err)}
                rec = {"task": task, "document_id": req.document_id, "filename": r["filename"],
                       "variant": v, "model": model, "provider": backend.provider,
                       "reasoning": reasoning, "temperature": 0, "prompt_sha256": req.prompt_sha256,
                       "labeled_utc": _now()}
                try:
                    comp = chat(backend, model, req.messages, req.schema, reasoning=reasoning,
                                max_tokens=MAX_TOKENS[task])
                except providers.ProviderError as err:
                    export.append_journal(calls_path, {**rec, "error": str(err)})
                    stats["errors"] += 1
                    continue
                _book(calls_path, rec, comp, budget, model)
                stats["calls"] += 1
                if task != "cuad":
                    continue
                problems = cuad_problems(r, comp.parsed, vocab)
                if not problems or (req.document_id, model, "repair") in done:
                    continue
                fix = tasks.build_cuad(r, repair_problems=problems[:40], prior_answer=comp.content)
                ptoks = sum(spend.estimate_tokens(m["content"]) for m in fix.messages)
                try:
                    budget.reserve(model, ptoks, MAX_TOKENS[task])
                except spend.SpendError as err:
                    stats["stopped"] = 1
                    return {**stats, "spent_usd": round(budget.spent_usd, 4), "stop_reason": str(err)}
                rec = {**rec, "variant": "repair", "prompt_sha256": fix.prompt_sha256,
                       "repair_problems": problems[:40], "labeled_utc": _now()}
                try:
                    comp = chat(backend, model, fix.messages, fix.schema, reasoning=reasoning,
                                max_tokens=MAX_TOKENS[task])
                except providers.ProviderError as err:
                    export.append_journal(calls_path, {**rec, "error": str(err)})
                    stats["errors"] += 1
                    continue
                _book(calls_path, rec, comp, budget, model)
                stats["repairs"] += 1
    return {**stats, "spent_usd": round(budget.spent_usd, 4)}


def _book(calls_path: Path, rec: Mapping[str, Any], comp: providers.Completion,
          budget: spend.Budget, model: str) -> None:
    """Settle one completion against the budget and journal it."""
    cost = budget.settle(model, comp.prompt_tokens, comp.completion_tokens, comp.cost_usd)
    export.append_journal(calls_path, {
        **rec, "response": comp.parsed, "content": comp.content, "served_model": comp.served_model,
        "served_provider": comp.served_provider, "used_schema": comp.used_schema,
        "finish_reason": comp.finish_reason, "prompt_tokens": comp.prompt_tokens,
        "completion_tokens": comp.completion_tokens, "reasoning_tokens": comp.reasoning_tokens,
        "cost_usd": round(cost, 6)})


# ---------------------------------------------------------------- review (offline)

def _latest(calls: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    """Last successful call per (document_id, model, variant)."""
    out = {}
    for c in calls:
        if not c.get("error"):
            out[(c["document_id"], c["model"], c.get("variant", ""))] = c
    return out


def cuad_records(rows: Sequence[Mapping[str, Any]], calls: Sequence[Mapping[str, Any]],
                 vocab: Sequence[str]) -> tuple[list[dict], list[dict]]:
    """v9.4-shaped CUAD journal records and ``spans.csv`` rows from raw calls."""
    from .. import v9_4_revision as v94  # noqa: PLC0415

    best: dict[tuple[str, str], Mapping[str, Any]] = {}
    for (doc_id, model, variant), c in _latest(calls).items():
        if variant == "repair" or (doc_id, model) not in best:
            best[(doc_id, model)] = c
    by_id = {tasks._ids(r)[0]: r for r in rows}
    records, span_rows = [], []
    for (doc_id, model), c in sorted(best.items()):
        row = by_id.get(doc_id)
        if row is None:
            continue
        doc = str(row["doc_text"])
        idx = verify.DocIndex.build(doc)
        labels: dict[str, list[dict]] = defaultdict(list)
        for cl in (c.get("response") or {}).get("clauses") or []:
            cat, text = cl.get("category"), str(cl.get("text") or "")
            loc = verify.locate(doc, text, idx.folded)
            entry = {"document_id": doc_id, "category": cat, "text": text, "verbatim_ok": loc is not None,
                     "heading_only": v94.is_heading_only(str(cat), text)}
            if loc is not None:
                labels[cat].append({"start": loc[0], "text": doc[loc[0]:loc[1]]})
                entry.update(start=loc[0], end=loc[1])
            span_rows.append(entry)
        clean, dropped, problems = v94.verify_cuad_labels(doc, dict(labels), vocab)
        kept = {(n, s["start"]) for n, spans in clean.items() for s in spans}
        for e in span_rows:
            if e["document_id"] == doc_id and "kept" not in e:
                e["kept"] = (e["category"], e.get("start")) in kept
                if not e["kept"]:
                    e["drop_reason"] = "not verbatim" if not e["verbatim_ok"] else "failed verification"
        n_spans = sum(len(v) for v in clean.values())
        records.append({
            "id": row.get("id") or doc_id, "document_id": row.get("document_id") or doc_id,
            "filename": row["filename"], "model": model, "provider": c["provider"],
            "served_model": c.get("served_model"), "reasoning": c["reasoning"],
            "prompt_sha256": c["prompt_sha256"], "accepted": not problems and n_spans > 0,
            "problems": problems, "dropped": dropped, "span_count": n_spans,
            "cuad_clause_labels": clean, "repaired": c.get("variant") == "repair",
            "labeled_utc": c["labeled_utc"],
        })
    return records, span_rows


def intent_records(calls: Sequence[Mapping[str, Any]]) -> list[dict]:
    """v9.4-shaped two-pass intent records (one per document and model)."""
    latest = _latest(calls)
    pairs: dict[tuple[str, str], dict] = {}
    for (doc_id, model, variant), c in latest.items():
        rec = pairs.setdefault((doc_id, model), {
            "document_id": doc_id, "filename": c["filename"], "model": model, "provider": c["provider"],
            "reasoning": c["reasoning"], "prompt_sha256": c["prompt_sha256"],
            "labeled_utc": c["labeled_utc"]})
        resp = c.get("response") or {}
        rec[f"pass_{variant}"] = {"intent": resp.get("intent"), "confidence": resp.get("confidence"),
                                  "evidence": resp.get("evidence")}
    return [r for _, r in sorted(pairs.items()) if "pass_a" in r and "pass_b" in r]


def field_decisions(rows: Sequence[Mapping[str, Any]], calls: Sequence[Mapping[str, Any]], task: str,
                    models: Sequence[str]) -> tuple[list[dict], list[dict]]:
    """Per-field decisions (``fields.csv`` rows) and per-document summaries."""
    latest = _latest(calls)
    field_rows, doc_rows = [], []
    for r in rows:
        doc_id, filename = tasks._ids(r)
        got = [latest.get((doc_id, m, "")) for m in models]
        if not any(got):
            continue
        idx = verify.DocIndex.build(str(r["doc_text"]))
        fields = tasks._task_fields(r, task)
        counts: Counter = Counter()
        for name, spec in fields.items():
            t = spec.get("retype") or spec["type"]
            vocab = vocabulary(spec["vocabulary"]) if spec.get("vocabulary") else None
            results = []
            for c in got:
                raw = ((c or {}).get("response") or {}).get("fields", {}).get(name) if c else None
                results.append(verify.check_field(raw, t, idx, vocab=vocab) if c else None)
            a = results[0]
            b = results[1] if len(results) > 1 else None
            if a is None:
                a, b = b, None
            decision, value, reason = verify.decide(a, b, t, spec["absence"])
            counts[decision] += 1
            row = {"document_id": doc_id, "doc_class": r["expected"], "subclass": r.get("expected_subclass"),
                   "field": name, "type": t, "decision": decision, "reason": reason,
                   "agree": decision == "accept"}
            for slot, (m, res) in zip(("a", "b"), zip(models, results)):
                if res is not None:
                    row.update({f"model_{slot}": m, f"model_{slot}_value": res.value,
                                f"model_{slot}_status": res.status, f"model_{slot}_evidence": res.evidence})
            row["verbatim_ok"] = all(x.verbatim_ok for x in results if x is not None)
            if decision == "accept":
                row["model_a_value"] = value if row.get("model_a_value") is None else row["model_a_value"]
            field_rows.append(row)
        first = next(c for c in got if c)
        doc_rows.append({
            "document_id": doc_id, "filename": filename, "doc_class": r["expected"],
            "subclass": r.get("expected_subclass"), "split": r.get("split"), "task": task,
            "status": "needs_review" if counts["adjudicate"] else "complete",
            "fields_present": counts["accept"], "fields_absent": counts["absent"],
            "fields_not_found": counts["unlabeled"], "problems": counts["adjudicate"],
            "model": ",".join(m for m, c in zip(models, got) if c), "provider": first["provider"],
            "served_model": first.get("served_model"), "reasoning": first["reasoning"],
            "prompt_sha256": first["prompt_sha256"],
            "prompt_tokens": sum((c or {}).get("prompt_tokens") or 0 for c in got),
            "completion_tokens": sum((c or {}).get("completion_tokens") or 0 for c in got),
            "cost_usd": round(sum((c or {}).get("cost_usd") or 0 for c in got), 6),
            "labeled_utc": first["labeled_utc"],
        })
    return field_rows, doc_rows


REVIEWER_COLUMNS = ("reviewer_verdict", "reviewer_value", "reviewer_note")
REVIEWER_VERDICTS = ("correct", "wrong", "a", "b", "value", "absent", "reject")


def _reviewer_columns(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    """Reviewer entries already in a CSV, so re-running ``review`` never loses them."""
    if not path.exists():
        return {}
    out = {}
    for r in export.read_csv(path):
        vals = {k: r.get(k, "") for k in REVIEWER_COLUMNS if r.get(k)}
        if vals:
            if vals.get("reviewer_verdict") and vals["reviewer_verdict"] not in REVIEWER_VERDICTS:
                raise SystemExit(f"{path}: {r['document_id']}/{r['field']}: reviewer_verdict must be one of "
                                 f"{REVIEWER_VERDICTS}")
            out[(r["document_id"], r["field"])] = vals
    return out


def review(rows: Sequence[Mapping[str, Any]], task: str, out_dir: Path) -> dict[str, Any]:
    """Rebuild the derived journals and CSVs for one chunk directory."""
    calls = export.read_journal(out_dir / "calls.jsonl")
    models = sorted({c["model"] for c in calls if not c.get("error")},
                    key=lambda m: [c["model"] for c in calls].index(m))
    if task == "cuad":
        vocab = cuad_vocab(rows)
        recs, spans = cuad_records(rows, calls, vocab)
        path = out_dir / "cuad-labels.jsonl"
        path.unlink(missing_ok=True)
        for rec in recs:
            export.append_journal(path, rec)
        export.write_csv(out_dir / "spans.csv", export.SPAN_COLUMNS,
                         [{k: v for k, v in s.items() if k in export.SPAN_COLUMNS} for s in spans])
        docs = [{"document_id": rec["document_id"], "filename": rec["filename"], "task": task,
                 "status": "accepted" if rec["accepted"] else "rejected",
                 "problems": "; ".join(rec["problems"] + rec["dropped"]),
                 "fields_present": sum(1 for v in rec["cuad_clause_labels"].values() if v),
                 "model": rec["model"], "provider": rec["provider"], "served_model": rec["served_model"],
                 "reasoning": rec["reasoning"], "prompt_sha256": rec["prompt_sha256"],
                 "labeled_utc": rec["labeled_utc"]} for rec in recs]
        export.write_csv(out_dir / "documents.csv", export.DOCUMENT_COLUMNS, docs)
        return {"records": len(recs), "spans": len(spans)}
    if task == "intent":
        recs = intent_records(calls)
        path = out_dir / "intent-labels.jsonl"
        path.unlink(missing_ok=True)
        for rec in recs:
            export.append_journal(path, rec)
        docs = [{"document_id": rec["document_id"], "filename": rec["filename"], "task": task,
                 "status": "agree" if rec["pass_a"]["intent"] == rec["pass_b"]["intent"] else "disagree",
                 "problems": "", "model": rec["model"], "provider": rec["provider"],
                 "reasoning": rec["reasoning"], "prompt_sha256": rec["prompt_sha256"],
                 "labeled_utc": rec["labeled_utc"]} for rec in recs]
        export.write_csv(out_dir / "documents.csv", export.DOCUMENT_COLUMNS, docs)
        return {"records": len(recs)}
    fields, docs = field_decisions(rows, calls, task, models)
    keep = {**_reviewer_columns(out_dir / "fields.csv"), **_reviewer_columns(out_dir / "adjudication.csv")}
    for f in fields:
        f.update(keep.get((f["document_id"], f["field"]), {}))
    export.write_csv(out_dir / "fields.csv", export.FIELD_COLUMNS, fields)
    export.write_csv(out_dir / "adjudication.csv", export.FIELD_COLUMNS,
                     [f for f in fields if f["decision"] == "adjudicate"])
    export.write_csv(out_dir / "documents.csv", export.DOCUMENT_COLUMNS, docs)
    return {"documents": len(docs), "fields": len(fields),
            "decisions": dict(Counter(f["decision"] for f in fields))}


# ---------------------------------------------------------------- costs.md

#: Stand-ins used to price chunks whose model is decided by an earlier gate.
PLACEHOLDER_MODELS = {"winner_of_1": "z-ai/glm-5.3-flashx-legal"}
PLACEHOLDER_REASONING = {"winner_of_1": "off", "winner_of_3": "low"}
REASONING_OUTPUT_FACTOR = {"off": 1.0, "low": 1.6}


def campaign_costs(rows: Sequence[Mapping[str, Any]], name: str = "v10") -> dict[str, Any]:
    """Per-chunk projections and single-model whole-campaign totals."""
    camp = campaign(name)
    out: list[dict[str, Any]] = []
    tokens: dict[str, list[int]] = {}
    for ch in camp["chunks"]:
        picked = chunk(select(rows, ch["task"], ch["set"]), ch.get("chunk"))
        models = [PLACEHOLDER_MODELS.get(m, m) for m in ch["models"]]
        reasoning = PLACEHOLDER_REASONING.get(str(ch["reasoning"]), str(ch["reasoning"]))
        est = estimate(picked, ch["task"], models)
        factor = REASONING_OUTPUT_FACTOR[reasoning]
        cost = sum(spend.token_cost(m, est["prompt_tokens"], int(est["output_tokens"] * factor))
                   for m in models)
        out.append({**ch, "documents": est["documents"], "calls": est["calls"] * len(models),
                    "prompt_tokens": est["prompt_tokens"] * len(models),
                    "output_tokens": int(est["output_tokens"] * factor) * len(models),
                    "projected_usd": round(cost, 4), "priced_models": models, "priced_reasoning": reasoning})
        key = f"{ch['task']}:{ch['set']}:{ch.get('chunk') or ''}"
        if ch["set"] != "calibration" or ch["task"] in ("cuad", "intent", "contract_entities"):
            tokens.setdefault(key, [est["prompt_tokens"], est["output_tokens"]])
    p_tok = sum(v[0] for v in tokens.values())
    o_tok = sum(v[1] for v in tokens.values())
    reg = models_registry()
    single = {m: round(spend.token_cost(m, p_tok, o_tok), 2)
              for m in list(reg["models"]) + list(reg.get("comparison", {}))
              if "prompt" in (reg["models"].get(m) or reg["comparison"].get(m) or {})}
    return {"chunks": out, "campaign_prompt_tokens": p_tok, "campaign_output_tokens": o_tok,
            "single_model_usd": dict(sorted(single.items(), key=lambda kv: kv[1])),
            "recommended_usd": round(sum(c["projected_usd"] for c in out), 2),
            "caps_usd": round(sum(c["cap_usd"] for c in out), 2), "checked": reg["checked"]}


def render_costs(c: Mapping[str, Any]) -> str:
    """Markdown for ``campaigns/v10/costs.md``."""
    lines = [
        "# v10 campaign cost projection",
        "",
        "Generated by `python -m mailroom_eda.labeling costs`; do not edit by hand.",
        f"Prices: `models.yaml` (checked {c['checked']}). Tokens: prompt characters / 4 over the",
        f"exact windowed prompts, typical output per call, x{RETRY_FACTOR} for retries and repairs;",
        "`low` reasoning bills about 1.6x the output tokens.",
        "",
        "## Whole campaign on one model",
        "",
        f"{c['campaign_prompt_tokens'] / 1e6:.1f}M prompt and {c['campaign_output_tokens'] / 1e6:.1f}M "
        "output tokens: every task once, each extraction task on one model.",
        "",
        "| model | USD |",
        "|---|---:|",
    ]
    lines += [f"| `{m}` | {v:.2f} |" for m, v in c["single_model_usd"].items()]
    lines += [
        "",
        "## Recommended plan, chunk by chunk",
        "",
        f"Projected total **${c['recommended_usd']:.2f}**; the chunk caps add up to **${c['caps_usd']:.2f}**.",
        "Two-model chunks are priced for both models.",
        "",
        "| chunk | task | set | slice | docs | calls | models | reasoning | projected USD | cap USD |",
        "|---|---|---|---|---:|---:|---|---|---:|---:|",
    ]
    for ch in c["chunks"]:
        models = " + ".join(f"`{m.split('/')[-1]}`" for m in ch["models"])
        lines.append(f"| {ch['id']} | {ch['task']} | {ch['set']} | {ch.get('chunk') or 'all'} | "
                     f"{ch['documents']} | {ch['calls']} | {models} | {ch['reasoning']} | "
                     f"{ch['projected_usd']:.2f} | {ch['cap_usd']:.2f} |")
    lines += ["", "`winner_of_1` is priced as `glm-5.3-flashx-legal` with reasoning off; "
              "`winner_of_3` as low reasoning.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    """Argument parser."""
    ap = argparse.ArgumentParser(prog="python -m mailroom_eda.labeling", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("costs", help="regenerate campaigns/<name>/costs.md")
    c.add_argument("--campaign", default="v10")
    c.add_argument("--stage", type=Path, default=DEFAULT_STAGE)
    c.add_argument("--check", action="store_true", help="exit 1 when costs.md is stale")
    for name in ("select", "estimate", "run", "review"):
        p = sub.add_parser(name)
        p.add_argument("--task", required=True, choices=sorted(tasks.TASK_CLASSES))
        p.add_argument("--set", dest="set_name", required=True, choices=SETS)
        p.add_argument("--chunk")
        p.add_argument("--stage", type=Path, default=DEFAULT_STAGE)
        p.add_argument("--out", type=Path, default=DEFAULT_OUT)
        if name == "estimate":
            p.add_argument("--models", default=",".join(models_registry()["models"]))
        if name == "run":
            p.add_argument("--provider", required=True, choices=providers.PROVIDERS)
            p.add_argument("--model", required=True)
            p.add_argument("--second-model")
            p.add_argument("--reasoning", default="off", choices=providers.REASONING)
            p.add_argument("--initials")
            p.add_argument("--api-cost-cap-usd", type=float)
            p.add_argument("--max-wall-seconds", type=float)
            p.add_argument("--dry-run", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns an exit code."""
    args = _parser().parse_args(argv)
    rows = load_stage(args.stage)
    if args.cmd == "costs":
        from .registry import CAMPAIGNS_DIR  # noqa: PLC0415

        path = CAMPAIGNS_DIR / args.campaign / "costs.md"
        text = render_costs(campaign_costs(rows, args.campaign))
        if args.check:
            ok = path.exists() and path.read_text(encoding="utf-8") == text
            print("costs.md is current" if ok else "costs.md is stale: run `labeling costs`")
            return 0 if ok else 1
        path.write_text(text, encoding="utf-8")
        print(path)
        return 0
    picked = chunk(select(rows, args.task, args.set_name), args.chunk)
    out_dir = args.out / chunk_name(args.task, args.set_name, args.chunk)
    if args.cmd == "select":
        for r in picked:
            print(tasks._ids(r)[0], r["filename"], r.get("expected_subclass"), sep="\t")
        print(f"{len(picked)} rows", file=sys.stderr)
        return 0
    if args.cmd == "estimate":
        print(json.dumps(estimate(picked, args.task, args.models.split(",")), indent=1))
        return 0
    if args.cmd == "review":
        print(json.dumps(review(rows, args.task, out_dir), indent=1))
        return 0
    models = [args.model] + ([args.second_model] if args.second_model else [])
    approved = models_registry()["models"]
    for m in models:
        if m not in approved or approved[m]["provider"] != args.provider:
            raise SystemExit(f"{m} is not an approved {args.provider} model in models.yaml")
    if args.task in tasks.TWO_MODEL_TASKS and len(models) != 2 and args.set_name != "pilot":
        raise SystemExit(f"{args.task} needs --second-model outside pilots (two-model agreement)")
    proj = estimate(picked, args.task, models)
    print(json.dumps(proj, indent=1))
    if args.dry_run:
        return 0
    spend.require_run_flags(initials=args.initials, cap_usd=args.api_cost_cap_usd,
                            max_wall_seconds=args.max_wall_seconds)
    backend = providers.Backend.from_env(args.provider)
    if args.provider == "openrouter":
        spend.check_key_headroom(providers.key_status(backend), args.api_cost_cap_usd)
    budget = spend.Budget(args.api_cost_cap_usd, args.max_wall_seconds)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run.json").write_text(json.dumps({
        "task": args.task, "set": args.set_name, "chunk": args.chunk, "models": models,
        "provider": args.provider, "reasoning": args.reasoning, "initials": args.initials,
        "api_cost_cap_usd": args.api_cost_cap_usd, "max_wall_seconds": args.max_wall_seconds,
        "documents": len(picked), "projection": proj, "started_utc": _now()}, indent=1) + "\n")
    result = run_chunk(picked, task=args.task, models=models, backend=backend, reasoning=args.reasoning,
                       budget=budget, out_dir=out_dir,
                       vocab=cuad_vocab(rows) if args.task == "cuad" else ())
    result["review"] = review(rows, args.task, out_dir)
    print(json.dumps(result, indent=1))
    return 2 if result.get("stopped") else 0


if __name__ == "__main__":
    raise SystemExit(main())
