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

from . import derive, export, providers, scoring, spend, tasks, verify
from . import interim as interim_mod
from .registry import campaign, models_registry, vocabulary

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STAGE = ROOT / "data" / "v9_4" / "stage"
DEFAULT_OUT = ROOT / "docs" / "reports" / "audits" / "v10_labels"
#: the sibling sandbox checkout the interim cross-check reads (``labeling/interim.py``)
DEFAULT_SANDBOX = ROOT.parent / "local-mailroom-sandbox"
SETS = ("calibration", "holdout", "pilot", "remainder", "pending", "flagged", "relabel", "all")
PILOT_PER_STRATUM = {"contract_entities": 2, "merger_entities": 3, "merger_consideration": 3, "correspondence": 5,
                     "claim_checklist": 5, "corporate": 3}
#: Output-token budget per call, by task (also the worst case the spend gate reserves).
MAX_TOKENS = {"cuad": 8192, "intent": 400, "contract_entities": 3000, "merger_entities": 3500,
              "merger_consideration": 600,
              "correspondence": 1200, "claim_checklist": 5000, "corporate": 2500}
#: Typical output tokens per call, for estimates (measured from the field counts).
TYPICAL_OUTPUT = {"cuad": 2500, "intent": 120, "contract_entities": 1200, "merger_entities": 1500,
                  "merger_consideration": 200,
                  "correspondence": 450, "claim_checklist": 400, "corporate": 800}
RETRY_FACTOR = 1.25
#: Extra output tokens for a call with reasoning on: reasoning tokens count against
#: ``max_tokens``, and without this a short answer budget (intent: 400) is cut off.
# DeepSeek v4.1 Flash at low spent 336 to over 2,300 reasoning tokens on the same real email
# (2026-11-06 probes); a truncated answer is billed and discarded, unused room costs nothing
REASONING_ALLOWANCE = 6144


def max_tokens(task: str, reasoning: str = "off") -> int:
    """Output-token budget for a task (``fields.yaml`` tasks set ``max_tokens``), plus
    :data:`REASONING_ALLOWANCE` when reasoning is on."""
    base = MAX_TOKENS.get(task) or int(tasks.generic_tasks()[task].get("max_tokens", 3000))
    return base + (REASONING_ALLOWANCE if reasoning != "off" else 0)


def typical_output(task: str) -> int:
    """Typical output tokens per call, for estimates."""
    return TYPICAL_OUTPUT.get(task) or int(tasks.generic_tasks()[task].get("typical_output", 1000))


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
    per = PILOT_PER_STRATUM.get(task) or int(tasks.generic_tasks()[task].get("pilot_per_stratum", 3))
    by_sub: dict[str, list] = defaultdict(list)
    for r in sorted(rows, key=_sha_key):
        by_sub[str(r.get("expected_subclass") or "")].append(r)
    return [r for sub in sorted(by_sub) for r in by_sub[sub][:per]]


def select(rows: Sequence[Mapping[str, Any]], task: str, set_name: str,
           subclass: Any = "") -> list[Mapping[str, Any]]:
    """Rows for one task and set, sorted by filename.

    ``subclass`` restricts the class pool by expected_subclass, accepting one value
    or a list/tuple; false values disable the filter. Pilot and remainder sets
    sample within that pool. CUAD calibration/holdout, intent sets, and contract
    entity calibration select from the original rows and bypass this filter.
    Unsupported sets raise SystemExit; unknown tasks raise KeyError."""
    from .. import v9_4_revision as v94  # noqa: PLC0415

    cls = tasks.TASK_CLASSES[task]
    pool = [r for r in rows if r["expected"] == cls]
    if subclass:
        subs = {str(s) for s in (subclass if isinstance(subclass, (list, tuple)) else [subclass])}
        pool = [r for r in pool if str(r.get("expected_subclass") or "") in subs]
    if task == "cuad":
        if set_name == "calibration":
            out = v94.cuad_calibration_sample(rows)
        elif set_name == "holdout":
            out = v94.cuad_holdout_sample(rows)
        elif set_name == "pending":
            out = [r for r in pool if v94.is_pending_cuad(r)]
        else:
            raise SystemExit("cuad sets: calibration, holdout, pending")
    elif task == "merger_consideration":
        # calibration: the 95 mergers with the expert MAUD answer; pending: the 56 without.
        if set_name == "calibration":
            out = [r for r in pool if derive.maud_consideration_answer(r)]
        elif set_name == "pending":
            out = [r for r in pool if not derive.maud_consideration_answer(r)]
        elif set_name == "all":
            out = pool
        else:
            raise SystemExit("merger_consideration sets: calibration, pending, all")
    elif task == "intent":
        if set_name == "calibration":
            out = v94.manual_intent_rows(rows)
        elif set_name == "flagged":
            out = v94.flagged_intent_rows(rows)
        elif set_name == "relabel":  # every machine-made intent (flagged included), re-read
            out = [r for r in rows if r["expected"] == "correspondence"
                   and r["gt_fields"].get("intent_status") != "manual"]
        else:
            raise SystemExit("intent sets: calibration, flagged, relabel")
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


def estimate(rows: Sequence[Mapping[str, Any]], task: str, models: Sequence[str],
             set_name: str = "") -> dict[str, Any]:
    """Projected tokens and cost per model for these rows (no network)."""
    prompt_tokens = calls = 0
    for r in rows:
        for v in _variants(task):
            req = tasks.build(task, r, v, set_name=set_name)
            prompt_tokens += sum(spend.estimate_tokens(m["content"]) for m in req.messages)
            calls += 1
    out_tokens = calls * typical_output(task)
    p_tok, o_tok = int(prompt_tokens * RETRY_FACTOR), int(out_tokens * RETRY_FACTOR)
    costs, gpu = {}, {}
    for m in models:
        if spend.gpu_spec(m):  # Modal bills GPU time: a cold start, the calls' slots, the tail
            gpu[m] = gpu_projection_seconds(m, calls)
            costs[m] = round(gpu[m] * spend.gpu_spec(m)[0], 4)
            continue
        try:
            costs[m] = round(spend.token_cost(m, p_tok, o_tok), 4)
        except spend.SpendError:
            costs[m] = None
    return {"task": task, "documents": len(rows), "calls": calls, "prompt_tokens": p_tok,
            "output_tokens": o_tok, "cost_usd": costs, **({"gpu_seconds": gpu} if gpu else {})}


def gpu_projection_seconds(model: str, calls: int) -> float:
    """Projected billed GPU seconds for ``calls`` on a Modal model (``models.yaml``): a cold
    start, each call's slot at ``in_flight`` concurrency, and the scale-down tail."""
    spec = models_registry()["models"][model]
    return (float(spec["cold_boot_seconds"]) + float(spec["scaledown_seconds"])
            + calls * float(spec["seconds_per_call"]) / max(1, int(spec["in_flight"])))


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
    return problems + [_probe_hint(str(x)) for x in list(dropped) + list(more)]


SEMANTIC_PROBE = ": failed semantic probe"


def _probe_hint(problem: str) -> str:
    """Restate the category's CUAD question on a failed semantic probe, so the repair call
    knows what the span must establish instead of only that it was rejected."""
    if not problem.endswith(SEMANTIC_PROBE):
        return problem
    name = problem[: -len(SEMANTIC_PROBE)]
    desc = next((c["description"] for c in tasks.dojo_snapshot()["cuad_categories"] if c["name"] == name), "")
    if not desc:
        return problem
    return (f"{problem}: the span does not read as {name}. The category asks: {desc} Replace it with the "
            f"operative sentence that answers this YES, or drop {name} if the document has none.")


def chunk_name(task: str, set_name: str, spec: str | None, reasoning: str = "off", *,
               full_text: bool = False) -> str:
    """Directory name for an ad-hoc ``run`` (campaign chunks use ``operate.chunk_dir``)."""
    base = f"{task}-{set_name}"
    if spec:
        i, n = spec.split("/")
        base += f"-{i}of{n}"
    base = base if reasoning == "off" else f"{base}-reasoning-{reasoning}"
    return f"{base}-fulltext" if full_text else base


def _done(calls_path: Path, reasoning: str | None = None) -> set[tuple[str, str, str, str]]:
    """(document_id, model, variant, prompt_sha256) already answered without error (at ``reasoning``).

    The prompt sha is part of the key, so after a prompt or registry edit a re-run sends
    the new prompt instead of reusing answers to the old one.
    """
    return {(c["document_id"], c["model"], c.get("variant", ""), c.get("prompt_sha256", ""))
            for c in export.read_journal(calls_path)
            if not c.get("error") and (reasoning is None or c.get("reasoning") == reasoning)}


class _Stop(Exception):
    """The budget refused the next call."""


def import_reusable(calls_path: Path, roots: Sequence[Path], rows: Sequence[Mapping[str, Any]], *,
                    task: str, models: Sequence[str], reasoning: str, provider: str | Mapping[str, str],
                    set_name: str = "") -> int:
    """Copy answers this chunk would otherwise buy again from other journals under ``roots``.

    An answer is reused only when its configuration is identical: document, model,
    variant, prompt sha (the exact prompt bundle), reasoning, provider and temperature 0,
    and it has no error. Each copy keeps ``reused_from`` (the source journal), so the
    chunk's own journal stays self-contained. Returns the number of records copied.
    """
    prov = dict(provider) if isinstance(provider, Mapping) else {m: provider for m in models}
    done = _done(calls_path, reasoning)
    wanted: dict[tuple[str, str, str, str], str | None] = {}  # key -> rendered input sha
    for r in rows:
        for v in _variants(task):
            req = tasks.build(task, r, v, set_name=set_name)
            ins = tasks.input_sha256(req.messages)
            wanted |= {(req.document_id, m, v, req.prompt_sha256): ins for m in models}
        if task == "cuad":
            sha = tasks.build_cuad(r, repair_problems=["-"], prior_answer="").prompt_sha256
            wanted |= {(str(r["document_id"]), m, "repair", sha): None for m in models}
    for key in done:
        wanted.pop(key, None)
    if not wanted:
        return 0
    here = calls_path.resolve()
    sources = sorted({p.resolve() for root in roots for p in Path(root).rglob("calls.jsonl")} - {here})
    copied = 0
    for src in sources:
        firsts: set[tuple[str, str]] = set()  # (document, model) first answers copied from src
        for c in export.read_journal(src):
            key = (c.get("document_id"), c.get("model"), c.get("variant", ""), c.get("prompt_sha256", ""))
            if key not in wanted or not _reusable(c, reasoning, prov.get(str(key[1]), "")):
                continue
            if key[2] == "repair":
                # a repair follows from its first answer and the document; reuse it only with
                # the first answer it repaired
                if (key[0], key[1]) not in firsts:
                    continue
            elif c.get("input_sha256") != wanted[key]:
                continue  # answered on other text (or recorded before inputs were hashed)
            else:
                firsts.add((key[0], key[1]))
            paid = c.get("reused_cost_usd", c.get("cost_usd"))
            export.append_journal(calls_path, {**c, "reused_from": str(src), "cost_usd": 0, "reused_cost_usd": paid})
            del wanted[key]
            copied += 1
    return copied


def _reusable(c: Mapping[str, Any], reasoning: str, provider: str) -> bool:
    """Same configuration, no error, and served inside the model's current route pin."""
    model = str(c.get("model"))
    return (not c.get("error") and c.get("reasoning") == reasoning and c.get("provider") == provider
            and c.get("temperature", 0) == providers.temperature(model)
            and not providers.served_outside_route(providers.model_route(model), reasoning,
                                                   {c.get("served_provider")}))


def over_context(backend: providers.Backend, model: str, messages: Sequence[Mapping[str, str]],
                 answer_tokens: int, reasoning: str = "off", count_tokens=providers.count_tokens) -> str:
    """Why this prompt plus its answer budget cannot fit the model's window, or ``""``.

    Modal: the exact count and the served ``max_model_len`` from vLLM (an over-long request
    is refused there outright). Elsewhere: the planning estimate against ``models.yaml``
    ``context``; a model without one is not checked."""
    if backend.provider == "modal":
        n, window = count_tokens(backend, model, messages, reasoning=reasoning)
    else:
        window = int((models_registry()["models"].get(model) or {}).get("context") or 0)
        if not window:
            return ""
        n = sum(spend.estimate_tokens(m["content"]) for m in messages)
    if n + answer_tokens > window:
        return f"over_context: {n} prompt + {answer_tokens} answer tokens > the {window}-token window"
    return ""


def run_chunk(rows: Sequence[Mapping[str, Any]], *, task: str, models: Sequence[str],
              reasoning: str, budget: spend.Budget, out_dir: Path,
              backend: providers.Backend | None = None,
              backends: Mapping[str, providers.Backend] | None = None,
              vocab: Sequence[str] = (), chat=providers.chat, workers: int = 1,
              progress: Any = None, set_name: str = "",
              reuse_from: Sequence[Path] = (), count_tokens=providers.count_tokens) -> dict[str, Any]:
    """Send every (row, model, variant) not yet answered; append to ``calls.jsonl``.

    First, identical answers already paid for in other journals under ``reuse_from``
    are copied in (:func:`import_reusable`), so a document is never bought twice.

    CUAD answers with rejected spans get one repair call (variant ``repair``) that replays
    the first answer. ``workers`` calls run at once; the budget books every in-flight
    call's worst case, so together they never pass the cap. Stops cleanly at the cap or
    wall limit; everything finished so far is kept, so a re-run continues where it stopped.

    ``backends`` maps each model to its own backend (a mixed chunk: one model on Modal,
    the other on OpenRouter); ``backend`` serves every model otherwise. Before each call the
    prompt plus its answer budget is checked against the model's window (exactly, through
    vLLM's ``/tokenize``, on Modal; by estimate against ``models.yaml`` ``context``
    elsewhere): one that cannot fit is journaled as ``over_context`` and never sent.
    """
    import threading  # noqa: PLC0415
    from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: PLC0415

    if task == "cuad" and not vocab:
        raise ValueError("cuad runs need the CUAD vocabulary")
    bk = dict(backends) if backends else {m: backend for m in models}
    if any(bk.get(m) is None for m in models):
        raise ValueError("every model needs a backend")
    out_dir.mkdir(parents=True, exist_ok=True)
    calls_path = out_dir / "calls.jsonl"
    stats: Counter = Counter()
    if reuse_from:
        reused = import_reusable(calls_path, reuse_from, rows, task=task, models=models,
                                 reasoning=reasoning, provider={m: bk[m].provider for m in models},
                                 set_name=set_name)
        if reused:
            stats["reused"] = reused
    done = _done(calls_path, reasoning)
    lock = threading.Lock()
    stop = threading.Event()
    stop_reason: list[str] = []

    def journal(rec: Mapping[str, Any]) -> None:
        """Append one record to the journal under the lock."""
        with lock:
            export.append_journal(calls_path, rec)

    def one(model: str, req: tasks.Request, rec: dict) -> providers.Completion | None:
        """Send one call inside the budget; journal it (with any billed error) and return it."""
        rec = {**rec, "input_sha256": tasks.input_sha256(req.messages)}
        if stop.is_set():
            raise _Stop()
        try:
            over = over_context(bk[model], model, req.messages, max_tokens(task, reasoning), reasoning,
                                count_tokens)
        except providers.ProviderError as err:
            journal({**rec, "error": str(err)})
            with lock:
                stats["errors"] += 1
            return None
        if over:
            journal({**rec, "error": over})
            with lock:
                stats["over_context"] += 1
            return None
        ptoks = sum(spend.estimate_tokens(m["content"]) for m in req.messages)
        try:
            reserved = budget.reserve(model, ptoks, max_tokens(task, reasoning))
        except spend.SpendError as err:
            stop.set()
            stop_reason.append(str(err))
            raise _Stop() from err
        try:
            comp = chat(bk[model], model, req.messages, req.schema, reasoning=reasoning,
                        max_tokens=max_tokens(task, reasoning))
        except providers.ProviderError as err:
            billed = getattr(err, "usage", None)
            if billed:  # a truncated or unparseable answer is still billed: book it
                cost = budget.settle(model, billed["prompt_tokens"], billed["completion_tokens"],
                                     billed["cost_usd"], reserved)
                journal({**rec, "error": str(err), "prompt_tokens": billed["prompt_tokens"],
                         "completion_tokens": billed["completion_tokens"], "cost_usd": round(cost, 6)})
            else:
                budget.release(reserved)
                journal({**rec, "error": str(err)})
            with lock:
                stats["errors"] += 1
            return None
        cost = budget.settle(model, comp.prompt_tokens, comp.completion_tokens, comp.cost_usd, reserved)
        journal({**rec, "response": comp.parsed, "content": comp.content, "served_model": comp.served_model,
                 "served_provider": comp.served_provider, "used_schema": comp.used_schema,
                 "finish_reason": comp.finish_reason, "prompt_tokens": comp.prompt_tokens,
                 "completion_tokens": comp.completion_tokens, "reasoning_tokens": comp.reasoning_tokens,
                 "cached_tokens": comp.cached_tokens, "cost_usd": round(cost, 6)})
        return comp

    def repair(r: Mapping[str, Any], model: str, first: providers.Completion | Mapping[str, Any],
               rec: dict) -> None:
        """Send the CUAD repair call when the first answer has rejected spans."""
        parsed = first.parsed if isinstance(first, providers.Completion) else first["response"]
        content = first.content if isinstance(first, providers.Completion) else first.get("content", "")
        problems = cuad_problems(r, parsed, vocab)
        if not problems:
            return
        fix = tasks.build_cuad(r, repair_problems=problems[:40], prior_answer=content)
        if (rec["document_id"], model, "repair", fix.prompt_sha256) in done:
            return
        rec = {**rec, "variant": "repair", "prompt_sha256": fix.prompt_sha256,
               "repair_problems": problems[:40], "labeled_utc": _now()}
        if one(model, fix, rec) is not None:
            with lock:
                stats["repairs"] += 1

    def job(r: Mapping[str, Any], v: str, model: str, req: tasks.Request, first: Mapping[str, Any] | None) -> None:
        """One planned call: a first answer (and its repair), or only a missing repair."""
        rec = {"task": task, "document_id": req.document_id, "filename": r["filename"], "variant": v,
               "model": model, "provider": bk[model].provider, "reasoning": reasoning,
               "temperature": providers.temperature(model),
               "prompt_sha256": req.prompt_sha256, "labeled_utc": _now()}
        if first is not None:  # answered on an earlier run; only its CUAD repair may be missing
            repair(r, model, first, rec)
            return
        comp = one(model, req, rec)
        if comp is None:
            return
        with lock:
            stats["calls"] += 1
        if task == "cuad":
            repair(r, model, comp, rec)

    jobs = []
    answered = {}  # first answers already in the journal, for CUAD repairs still missing
    if task == "cuad":
        for c in export.read_journal(calls_path):
            if not c.get("error") and c.get("reasoning") == reasoning and not c.get("variant"):
                answered[(c["document_id"], c["model"], c.get("prompt_sha256", ""))] = c
    for r in rows:
        for v in _variants(task):
            req = tasks.build(task, r, v, set_name=set_name)
            for model in models:
                key = (req.document_id, model, v, req.prompt_sha256)
                if key not in done:
                    jobs.append((r, v, model, req, None))
                elif task == "cuad" and (req.document_id, model, req.prompt_sha256) in answered:
                    first = answered[(req.document_id, model, req.prompt_sha256)]
                    if cuad_problems(r, first.get("response") or {}, vocab) and not any(
                            d[:3] == (req.document_id, model, "repair") for d in done):
                        jobs.append((r, v, model, req, first))
                    else:
                        stats["skipped"] += 1
                else:
                    stats["skipped"] += 1
    finished = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(job, *j) for j in jobs]
        try:
            for fut in as_completed(futures):
                try:
                    fut.result()
                except _Stop:
                    pass
                finished += 1
                if progress and (finished % 25 == 0 or finished == len(jobs)):
                    progress(f"{finished}/{len(jobs)} jobs, ${budget.total_usd():.4f} spent, "
                             f"{stats['errors']} errors")
        except KeyboardInterrupt:
            stop.set()
            for f in futures:
                f.cancel()
            raise
    out = {**stats, "spent_usd": round(budget.total_usd(), 4)}
    if budget.gpu_first is not None:  # the Modal leg: billed GPU time, a cold start included
        out.update(gpu_seconds=round(budget.gpu_seconds(), 1), gpu_usd=round(budget.gpu_usd(), 4))
    if stop_reason:
        out.update(stopped=1, stop_reason=stop_reason[0])
    return out


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
                    models: Sequence[str], interim: Any = None) -> tuple[list[dict], list[dict]]:
    """Per-field decisions (``fields.csv`` rows) and per-document summaries.

    ``interim`` (an ``interim.Overlay``) adds the flag-only ``interim_*`` columns."""
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
            results = []
            labeling_problems = []
            for c in got:
                raw = ((c or {}).get("response") or {}).get("fields", {}).get(name) if c else None
                checked, quality = scoring.check_field(r["expected"], name, raw, idx) if c else (None, None)
                results.append(checked)
                if quality and not quality.passed:
                    labeling_problems.extend(f.code for f in quality.findings)
            a = results[0]
            b = results[1] if len(results) > 1 else None
            if a is None:
                a, b = b, None
            absence = spec["absence"]
            if absence == "absent_ok" and tasks.reads_in_part(r, task):
                absence = "evidence_only"  # the unread middle may hold it: no absence on silence
            decision, value, reason = verify.decide(a, b, t, absence)
            if labeling_problems:
                reason += "; labeling: " + ", ".join(sorted(set(labeling_problems)))
            counts[decision] += 1
            row = {"document_id": doc_id, "doc_class": r["expected"], "subclass": r.get("expected_subclass"),
                   "field": name, "type": t, "decision": decision, "reason": reason,
                   "agree": decision == "accept"}
            for slot, (m, res) in zip(("a", "b"), zip(models, results)):
                if res is not None:
                    row.update({f"model_{slot}": m, f"model_{slot}_value": res.value,
                                f"model_{slot}_status": res.status, f"model_{slot}_evidence": res.evidence})
            row["verbatim_ok"] = all(x.verbatim_ok for x in results if x is not None)
            row["accepted_value"] = value if decision == "accept" else None
            if interim is not None:
                interim_mod.annotate(row, interim, spec, idx)
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
            "document_tokens": spend.estimate_tokens(str(r.get("doc_text") or "")),
            "prompt_tokens": sum((c or {}).get("prompt_tokens") or 0 for c in got),
            "completion_tokens": sum((c or {}).get("completion_tokens") or 0 for c in got),
            "labeled_utc": first["labeled_utc"],
        })
    return field_rows, doc_rows


REVIEWER_COLUMNS = ("reviewer_verdict", "reviewer_value", "reviewer_note")
REVIEWER_VERDICTS = ("correct", "wrong", "a", "b", "value", "absent", "reject", "interim")


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


def _usage_by_document(calls: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """Billed tokens per document, over every call (repairs and failed calls included).

    Cost is deliberately left out: it stays in ``calls.jsonl`` / ``runs.jsonl`` and is
    never written to the CSVs.
    """
    out: dict[str, dict[str, int]] = {}
    for c in calls:
        u = out.setdefault(c["document_id"], {"prompt_tokens": 0, "completion_tokens": 0})
        u["prompt_tokens"] += c.get("prompt_tokens") or 0
        u["completion_tokens"] += c.get("completion_tokens") or 0
    return out


def _document_tokens(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Token estimate of each document's full text (the corpus ``token_estimate`` rule)."""
    return {str(r.get("document_id")): spend.estimate_tokens(str(r.get("doc_text") or "")) for r in rows}


def current_prompt_calls(rows: Sequence[Mapping[str, Any]], task: str, calls: Sequence[Mapping[str, Any]],
                         set_name: str = "") -> list[Mapping[str, Any]]:
    """The calls whose prompt sha is the one this task would send today.

    After a prompt or registry edit, answers to the old prompt stay in the journal (they
    were paid for) but must not be scored together with answers to the new one.
    """
    by_id = {str(r.get("document_id")): r for r in rows}
    want: dict[tuple[str, str], str] = {}
    out = []
    for c in calls:
        doc, v = str(c.get("document_id")), c.get("variant", "")
        if doc not in by_id:
            continue
        if (doc, v) not in want:
            r = by_id[doc]
            want[(doc, v)] = (tasks.build_cuad(r, repair_problems=["-"], prior_answer="").prompt_sha256
                              if v == "repair" else tasks.build(task, r, v, set_name=set_name).prompt_sha256)
        if c.get("prompt_sha256") == want[(doc, v)]:
            out.append(c)
    return out


def _run_set(out_dir: Path) -> str:
    """The ``set`` the chunk directory was last run with (``runs.jsonl``), or ``""``."""
    runs = [r for r in export.read_journal(out_dir / "runs.jsonl") if r.get("set")] \
        if (out_dir / "runs.jsonl").exists() else []
    return str(runs[-1]["set"]) if runs else ""


def review(rows: Sequence[Mapping[str, Any]], task: str, out_dir: Path,
           reasoning: str | None = None, interim: Any = None) -> dict[str, Any]:
    """Rebuild the derived journals and CSVs for one chunk directory (calls at ``reasoning``).

    With ``interim`` (an ``interim.Overlay``), field rows gain the flag-only ``interim_*``
    columns and an intent chunk writes ``interim-intent.csv``.

    Return task-specific record or decision counts. Existing reviewer columns are
    retained, but checklist confirmations may reopen when source checks fail.
    Checklist chunks also write labeling-scores.json. Catalog validation and
    filesystem errors propagate."""
    calls = [c for c in export.read_journal(out_dir / "calls.jsonl")
             if reasoning is None or c.get("reasoning") == reasoning]
    calls = current_prompt_calls(rows, task, calls, _run_set(out_dir))
    usage = _usage_by_document(calls)
    doc_tokens = _document_tokens(rows)
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
                 "labeled_utc": rec["labeled_utc"], "document_tokens": doc_tokens.get(rec["document_id"]),
                 **usage.get(rec["document_id"], {})} for rec in recs]
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
                 "labeled_utc": rec["labeled_utc"], "document_tokens": doc_tokens.get(rec["document_id"]),
                 **usage.get(rec["document_id"], {})} for rec in recs]
        export.write_csv(out_dir / "documents.csv", export.DOCUMENT_COLUMNS, docs)
        if interim is not None:
            side = interim_mod.intent_rows(interim, recs)
            export.write_csv(out_dir / "interim-intent.csv", interim_mod.INTENT_COLUMNS, side)
            return {"records": len(recs), "interim": {"rows": len(side), "by_status": dict(
                Counter(r["interim_status"] for r in side)), "agree": dict(Counter(r["interim_agree"] for r in side))}}
        return {"records": len(recs)}
    fields, docs = field_decisions(rows, calls, task, models, interim=interim)
    keep = {**_reviewer_columns(out_dir / "fields.csv"), **_reviewer_columns(out_dir / "adjudication.csv")}
    open_items: Counter = Counter()
    by_id = {tasks._ids(r)[0]: r for r in rows}
    for f in fields:
        f.update(keep.get((f["document_id"], f["field"]), {}))
        reopened = (f["field"] == "claim_checklist" and f.get("reviewer_verdict") == "correct"
                    and f["decision"] != "accept")
        if reopened:
            f["reviewer_verdict"] = ""
            f["reviewer_note"] = ("Prior 'correct' confirmation reopened by labeling checks; human review required. "
                                  + f.get("reviewer_note", ""))
        apply_verdict(f)
        if reopened:
            f["final_decision"], f["final_value"] = "adjudicate", None
        if f["field"] == "claim_checklist" and f["final_decision"] == "accept":
            value = f["final_value"]
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = None
            quality = scoring.score_checklist(value, verify.DocIndex.build(by_id[f["document_id"]]["doc_text"]))
            if not quality.passed:
                f["final_decision"], f["final_value"] = "adjudicate", None
                f["reason"] += "; final labeling: " + ", ".join(sorted({i.code for i in quality.findings}))
        open_items[f["document_id"]] += f["final_decision"] == "adjudicate"
    for d in docs:
        d["problems"] = open_items[d["document_id"]]
        d["status"] = "needs_review" if open_items[d["document_id"]] else "complete"
    export.write_csv(out_dir / "fields.csv", export.FIELD_COLUMNS, fields)
    export.write_csv(out_dir / "adjudication.csv", export.FIELD_COLUMNS,
                     [f for f in fields if f["decision"] == "adjudicate" or f["final_decision"] == "adjudicate"])
    export.write_csv(out_dir / "documents.csv", export.DOCUMENT_COLUMNS, docs)
    if task == "claim_checklist":
        audit = scoring.audit_fields(rows, export.read_csv(out_dir / "fields.csv"))
        (out_dir / "labeling-scores.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    out = {"documents": len(docs), "fields": len(fields),
           "decisions": dict(Counter(f["decision"] for f in fields)),
           "final_decisions": dict(Counter(f["final_decision"] for f in fields))}
    if interim is not None:
        out["interim"] = interim_mod.counts(fields)
    return out


def apply_verdict(f: dict[str, Any]) -> None:
    """Set ``final_decision`` and ``final_value`` from the model decision and any reviewer verdict.

    ``a``/``b`` take that model's value, ``value`` takes ``reviewer_value``, ``absent`` records
    a documented absence, ``reject`` and ``wrong`` leave the field unlabeled, ``correct``
    confirms an accepted value, and ``interim`` takes the sandbox overlay's value when it is
    grounded in the document (``interim_verified``), recording ``final_source``
    ``interim:<gate_run>``. With no verdict the model decision stands. Ingest reads only the
    ``final_*`` columns.
    """
    accepted = f.pop("accepted_value", None)
    verdict = f.get("reviewer_verdict") or ""
    where = f"{f['document_id']}/{f['field']}"
    decision, value = f["decision"], accepted
    if verdict in ("a", "b"):
        if not f.get(f"model_{verdict}"):
            raise SystemExit(f"{where}: reviewer_verdict {verdict!r}, but there is no model {verdict} answer")
        value = f.get(f"model_{verdict}_value")
        if value in (None, "", []):
            raise SystemExit(f"{where}: model {verdict} gave no value; use absent, value or reject")
        decision = "accept"
    elif verdict == "value":
        if not f.get("reviewer_value"):
            raise SystemExit(f"{where}: reviewer_verdict 'value' needs reviewer_value")
        decision, value = "accept", f["reviewer_value"]
    elif verdict == "interim":
        if not f.get("interim_status"):
            raise SystemExit(f"{where}: reviewer_verdict 'interim', but no overlay label is loaded for this "
                             "field (review with --sandbox pointing at local-mailroom-sandbox)")
        if f.get("interim_verified") not in (True, "true"):
            raise SystemExit(f"{where}: the overlay value is not grounded in this document; use value or reject")
        decision, value = "accept", f.get("interim_value")
        f["final_source"] = f"interim:{f.get('interim_gate_run') or 'unknown'}"
    elif verdict == "absent":
        decision, value = "absent", None
    elif verdict in ("reject", "wrong"):
        decision, value = "unlabeled", None
    elif verdict == "correct" and decision != "accept":
        raise SystemExit(f"{where}: 'correct' confirms an accepted value; this row is {decision!r}")
    f["final_decision"], f["final_value"] = decision, value


# ---------------------------------------------------------------- costs.md

#: Stand-ins used to price chunks whose model is decided by an earlier gate.
# priced as the costliest bake-off arm, so a projection never runs low
PLACEHOLDER_MODELS = {"winner_of_1": "z-ai/glm-5.3-flashx-legal", "winner_of_3": "deepseek/deepseek-v4.1-flash-1017"}
PLACEHOLDER_REASONING = {"winner_of_1": "low", "winner_of_3": "low"}
#: Reasoning tokens per call at ``low`` when models.yaml measures none for a model.
DEFAULT_LOW_REASONING_TOKENS = 1200


def reasoning_tokens(model: str, task: str, reasoning: str) -> int:
    """Reasoning tokens one call is projected to spend: 0 at off, else the model's measured
    ``reasoning_tokens.low`` entry for the task (or its ``default``)."""
    if reasoning == "off":
        return 0
    low = ((models_registry()["models"].get(model) or {}).get("reasoning_tokens") or {}).get(reasoning) or {}
    return int(low.get(task, low.get("default", DEFAULT_LOW_REASONING_TOKENS)))


def projected_output(model: str, task: str, reasoning: str, est: Mapping[str, Any]) -> int:
    """Output tokens for one model over a chunk: the answers plus the reasoning they cost."""
    return int(est["output_tokens"]) + round(reasoning_tokens(model, task, reasoning) * est["calls"] * RETRY_FACTOR)


def campaign_costs(rows: Sequence[Mapping[str, Any]], name: str = "v10") -> dict[str, Any]:
    """Per-chunk projections and single-model whole-campaign totals."""
    camp = campaign(name)
    out: list[dict[str, Any]] = []
    tokens: dict[str, list[int]] = {}
    for ch in camp["chunks"]:
        try:  # an approved bake-off winner is priced as itself, otherwise as the costliest arm
            from .operate import resolve, pick  # noqa: PLC0415
            models, reasoning = resolve(name, ch)
            picked = pick(rows, ch)
        except SystemExit:
            picked = chunk(select(rows, ch["task"], ch["set"]), ch.get("chunk"))
            models = [PLACEHOLDER_MODELS.get(m, m) for m in ch["models"]]
            reasoning = PLACEHOLDER_REASONING.get(str(ch["reasoning"]), str(ch["reasoning"]))
        est = estimate(picked, ch["task"], models, ch["set"])
        outputs = {m: projected_output(m, ch["task"], reasoning, est) for m in models}
        cost = 0.0 if ch.get("skip") else sum(spend.token_cost(m, est["prompt_tokens"], outputs[m]) for m in models)
        out.append({**ch, "documents": est["documents"], "calls": est["calls"] * len(models),
                    "prompt_tokens": est["prompt_tokens"] * len(models),
                    "output_tokens": sum(outputs.values()),
                    "projected_usd": round(cost, 4), "priced_models": models, "priced_reasoning": reasoning})
        key = f"{ch['task']}:{ch['set']}:{ch.get('chunk') or ''}"
        if ch["set"] != "calibration" or ch["task"] in ("cuad", "intent", "contract_entities", "merger_consideration"):
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
        "`low` adds each model's measured reasoning tokens per call (`reasoning_tokens` in models.yaml).",
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
    lines += ["", "An approved `winner_of_<id>` is priced as the approved arm; until then `winner_of_1` "
              "is priced as `glm-5.3-flashx-legal` at low and `winner_of_3` as `deepseek-v4.1-flash-1017` "
              "at low (the costliest arms).", ""]
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
            p.add_argument("--workers", type=int, default=4)
        if name == "review":
            p.add_argument("--reasoning", default="off", choices=providers.REASONING)
            p.add_argument("--no-restore", action="store_true")
            p.add_argument("--sandbox", type=Path, default=DEFAULT_SANDBOX,
                           help="local-mailroom-sandbox checkout for the interim cross-check (absent: skipped)")
        if name in ("run", "review"):
            p.add_argument("--no-checkpoint", action="store_true",
                           help="do not upload output to the private HF staging repo")
            p.add_argument("--full-text", action="store_true",
                           help="merger tasks: send the whole agreement instead of the located window "
                                "(the located-vs-full A/B only; output goes to a -fulltext directory)")
    from . import operate  # noqa: PLC0415

    operate.add_commands(sub)
    return ap


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns an exit code."""
    args = _parser().parse_args(argv)
    if getattr(args, "operate", None):
        return args.operate(args)
    full_text = getattr(args, "full_text", False)
    if full_text and args.task not in tasks.WINDOWED_MERGER_TASKS:
        raise SystemExit(f"--full-text applies to {sorted(tasks.WINDOWED_MERGER_TASKS)} only")
    before = set(tasks.FULL_TEXT_TASKS)
    if full_text:
        tasks.FULL_TEXT_TASKS.add(args.task)
    try:  # --full-text lasts one invocation: a later in-process run gets its window back
        return _run(args, full_text)
    finally:
        tasks.FULL_TEXT_TASKS.clear()
        tasks.FULL_TEXT_TASKS.update(before)


def _run(args: argparse.Namespace, full_text: bool) -> int:
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
    if args.cmd == "select":
        for r in picked:
            print(tasks._ids(r)[0], r["filename"], r.get("expected_subclass"), sep="\t")
        print(f"{len(picked)} rows", file=sys.stderr)
        return 0
    if args.cmd == "estimate":
        print(json.dumps(estimate(picked, args.task, args.models.split(","), args.set_name), indent=1))
        return 0
    if args.cmd == "review":
        from . import operate  # noqa: PLC0415

        operate.auto_restore(args)
        out_dir = args.out / chunk_name(args.task, args.set_name, args.chunk, args.reasoning, full_text=full_text)
        overlay = operate.load_interim(args, rows)
        print(json.dumps(review(rows, args.task, out_dir, reasoning=args.reasoning, interim=overlay), indent=1))
        return _checkpoint(args, f"review {out_dir.name}", 0)
    models = [args.model] + ([args.second_model] if args.second_model else [])
    out_dir = args.out / chunk_name(args.task, args.set_name, args.chunk, args.reasoning, full_text=full_text)
    rc = execute(rows, picked, task=args.task, set_name=args.set_name, chunk_spec=args.chunk, models=models,
                 provider=args.provider, reasoning=args.reasoning, initials=args.initials,
                 cap_usd=args.api_cost_cap_usd, max_wall_seconds=args.max_wall_seconds, out_dir=out_dir,
                 dry_run=args.dry_run, workers=args.workers, require_checkpoint=not args.no_checkpoint)
    return rc if args.dry_run else _checkpoint(args, f"run {out_dir.name}", rc)


def _checkpoint(args: argparse.Namespace, why: str, rc: int) -> int:
    """Checkpoint the labels root to the private HF staging repo (``operate.auto_checkpoint``)."""
    from . import operate  # noqa: PLC0415

    return operate.auto_checkpoint(argparse.Namespace(out=args.out, campaign="v10",
                                                      no_checkpoint=args.no_checkpoint), why, rc)


def provider_label(prov: Mapping[str, str]) -> str:
    """``openrouter`` / ``modal`` when every model shares it, else ``mixed``."""
    kinds = set(prov.values())
    return kinds.pop() if len(kinds) == 1 else "mixed"


def execute(rows: Sequence[Mapping[str, Any]], picked: Sequence[Mapping[str, Any]], *, task: str,
            set_name: str, chunk_spec: str | None, models: Sequence[str], provider: str | Mapping[str, str],
            reasoning: str,
            initials: str | None, cap_usd: float | None, max_wall_seconds: float | None, out_dir: Path,
            dry_run: bool = False, workers: int = 4, chunk_id: str | None = None,
            chat=providers.chat, key_status=providers.key_status,
            reuse_from: Sequence[Path] | None = None, require_checkpoint: bool = False,
            ack_requires: str | None = None) -> int:
    """Validate, project, gate the spend, run and review one chunk. Returns an exit code
    (0 done, 2 stopped at the cap or wall, 3 finished with failed calls to re-run).

    Identical answers under ``reuse_from`` (default: the labels root, ``out_dir``'s
    parent, which holds every chunk and smoke run) are reused instead of bought again.

    ``provider`` is one provider for every model, or a ``{model: provider}`` map for a mixed
    chunk (each model must be approved on its provider in ``models.yaml``)."""
    approved = models_registry()["models"]
    prov = dict(provider) if isinstance(provider, Mapping) else {m: provider for m in models}
    for m in models:
        if m not in approved or approved[m]["provider"] != prov.get(m):
            raise SystemExit(f"{m} is not an approved {prov.get(m)} model in models.yaml")
    if len(set(models)) != len(models):
        raise SystemExit("the two models must differ")
    # pilots and calibration sets are measured (by reading, or against expert gold), never
    # ingested as labels, so one model is enough there; everywhere else two must agree
    if task in tasks.TWO_MODEL_TASKS and len(models) != 2 and set_name not in ("pilot", "calibration"):
        raise SystemExit(f"{task} needs --second-model outside pilots and calibration (two-model agreement)")
    if task == "intent" and set_name == "relabel":
        if len(models) != 2:  # rewriting existing labels needs a second opinion that agrees
            raise SystemExit("the intent relabel needs --second-model (two-model agreement)")
    elif task not in tasks.TWO_MODEL_TASKS and len(models) != 1:
        raise SystemExit(f"{task} runs one model")
    proj = estimate(picked, task, models, set_name)
    print(json.dumps({"chunk": chunk_id, "out": str(out_dir), "reasoning": reasoning, **proj}, indent=1))
    if dry_run:
        return 0
    for m in models:  # a cap that cannot cover a cold start buys a boot and no labels
        floor = gpu_projection_seconds(m, 0) * spend.gpu_spec(m)[0] if spend.gpu_spec(m) else 0.0
        if cap_usd is not None and floor > cap_usd:
            raise SystemExit(f"cap ${cap_usd:.2f} cannot cover {m}'s Modal cold start and scale-down "
                             f"(${floor:.2f}); dry-run for the projection and pass --cap-usd above it")
    spend.require_run_flags(initials=initials, cap_usd=cap_usd, max_wall_seconds=max_wall_seconds)
    if require_checkpoint:
        from . import checkpoint  # noqa: PLC0415

        if not checkpoint.token_ready():  # paid answers need somewhere to survive a lost checkout
            raise SystemExit(f"no HF token: paid answers checkpoint to the private {checkpoint.STAGING_REPO}; "
                             "set HF_TOKEN, or pass --no-checkpoint")
    backends = {p: providers.Backend.from_env(p) for p in sorted(set(prov.values()))}
    if "openrouter" in backends:
        headroom = spend.check_key_headroom(key_status(backends["openrouter"]), cap_usd)
        print(f"key headroom after reserve: {'unlimited' if headroom == float('inf') else f'${headroom:.2f}'}",
              file=sys.stderr)
    budget = spend.Budget(cap_usd, max_wall_seconds)
    out_dir.mkdir(parents=True, exist_ok=True)
    export.append_journal(out_dir / "runs.jsonl", {
        "chunk": chunk_id, "task": task, "set": set_name, "slice": chunk_spec, "models": list(models),
        "provider": provider_label(prov), **({"providers": prov} if len(set(prov.values())) > 1 else {}),
        "reasoning": reasoning, "initials": initials, "api_cost_cap_usd": cap_usd,
        "max_wall_seconds": max_wall_seconds, "workers": workers, "documents": len(picked),
        "projection": proj, **({"ack_requires": ack_requires} if ack_requires else {}),
        "started_utc": _now()})
    result = run_chunk(picked, task=task, models=models, backends={m: backends[prov[m]] for m in models},
                       reasoning=reasoning,
                       budget=budget, out_dir=out_dir, vocab=cuad_vocab(rows) if task == "cuad" else (),
                       chat=chat, workers=workers, progress=lambda m: print(m, file=sys.stderr),
                       set_name=set_name,
                       reuse_from=[out_dir.parent] if reuse_from is None else reuse_from)
    result["review"] = review(rows, task, out_dir, reasoning=reasoning)
    export.append_journal(out_dir / "runs.jsonl", {"chunk": chunk_id, "finished_utc": _now(), "result": result})
    print(json.dumps(result, indent=1))
    if result.get("stopped"):
        print("stopped at the cap or wall limit; re-run the same command to continue", file=sys.stderr)
        return 2
    if result.get("errors"):
        print(f"{result['errors']} calls failed; re-run the same command to retry only those", file=sys.stderr)
        return 3
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
