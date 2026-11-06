"""Operator commands for running a campaign chunk by chunk.

    labeling stage                      build data/v9_4/stage if it is missing, then check its sha
    labeling doctor                     every readiness check, offline and live (no paid call)
    labeling prices                     live OpenRouter prices and pinned-provider parameters
    labeling ping   --initials XX       one tiny paid request per approved model (< $0.01)
    labeling status                     every chunk: progress, spend, gate, approval
    labeling chunk  <id> [--arm N] --initials XX [--dry-run]
                                        run a campaign chunk exactly as campaign.yaml defines it
    labeling gate   <id> [--arm N] [--approve --initials XX] [--signed-off]
                                        score the chunk's gate; --approve records a pass

A chunk's output directory is ``<out>/<id>[.<arm>]_<task>-<set>[-<i>of<n>]``. Approvals go
to ``campaigns/<name>/approved.yaml``; a chunk whose model is ``winner_of_<id>`` reads its
model and reasoning from the approved arm of that chunk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from . import checkpoint, derive, export, providers, runner, spend, tasks, verify
from .registry import CAMPAIGNS_DIR, campaign, dojo_snapshot, fields_registry, models_registry

ROOT = runner.ROOT
PUBLIC_MODELS_URL = "https://openrouter.ai/api/v1/models"
#: Parameters every pinned provider must support (``seed`` only when the route sends it).
REQUIRED_PARAMETERS = ("max_tokens", "temperature", "response_format", "structured_outputs", "reasoning")
REVIEW_MIN = 30
AGREEMENT_MAX_ADJUDICATE = 0.30
SAMPLE_SIZE = 20


def _now() -> str:
    """UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- campaign chunks

def chunk_entries(name: str, chunk_id: str) -> list[dict[str, Any]]:
    """All arms of one chunk id, in campaign order."""
    entries = [c for c in campaign(name)["chunks"] if str(c["id"]) == str(chunk_id)]
    if not entries:
        ids = sorted({str(c["id"]) for c in campaign(name)["chunks"]}, key=lambda x: (len(x), x))
        raise SystemExit(f"no chunk {chunk_id!r} in campaign {name}; chunks: {', '.join(ids)}")
    return entries


def chunk_entry(name: str, chunk_id: str, arm: int | None) -> tuple[dict[str, Any], int | None]:
    """One arm of a chunk (``arm`` is 1-based; required when the chunk has several)."""
    entries = chunk_entries(name, chunk_id)
    if len(entries) == 1:
        if arm not in (None, 1):
            raise SystemExit(f"chunk {chunk_id} has one arm")
        return entries[0], None
    if arm is None or not 1 <= arm <= len(entries):
        arms = "; ".join(f"--arm {i}: {e['models'][0]} reasoning {e['reasoning']}"
                         for i, e in enumerate(entries, 1))
        raise SystemExit(f"chunk {chunk_id} has {len(entries)} arms; pick one ({arms})")
    return entries[arm - 1], arm


def chunk_dir(out: Path, entry: Mapping[str, Any], arm: int | None) -> Path:
    """Output directory for a campaign chunk arm."""
    name = f"{entry['id']}{f'.{arm}' if arm else ''}_{entry['task']}-{entry['set']}"
    if entry.get("chunk"):
        i, n = str(entry["chunk"]).split("/")
        name += f"-{i}of{n}"
    return out / name


def approvals_path(name: str) -> Path:
    """``campaigns/<name>/approved.yaml``."""
    return CAMPAIGNS_DIR / name / "approved.yaml"


def auto_checkpoint(args: argparse.Namespace, why: str, rc: int = 0, *, api: Any = None) -> int:
    """Checkpoint the labels root after ``why``; a failed upload turns a clean exit into 4."""
    if getattr(args, "no_checkpoint", False):
        print(f"checkpoint skipped (--no-checkpoint) after {why}", file=sys.stderr)
        return rc
    try:
        res = checkpoint.checkpoint(Path(args.out), approvals_path(getattr(args, "campaign", "v10")),
                                    f"checkpoint: {why}", api=api)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - the run's output is on disk either way
        print(f"CHECKPOINT FAILED after {why}: {exc}\n  output is on disk; retry with "
              "`labeling checkpoint`", file=sys.stderr)
        return rc or 4
    print(f"checkpointed {res['files']} files to {res['repo']} (verified)", file=sys.stderr)
    return rc


def auto_restore(args: argparse.Namespace, *, api: Any = None) -> list[Path]:
    """Pull staged files missing from ``args.out`` before a command reads it (never overwrites).

    Runs only when the command was parsed with ``--no-restore`` available and not given
    (a hand-built ``Namespace`` without the attribute skips it). No token, or a staging
    repo that does not exist yet, prints one line and carries on.
    """
    if getattr(args, "no_restore", True):
        return []
    if api is None and not checkpoint.token_ready():
        print("restore skipped: no HF token", file=sys.stderr)
        return []
    try:
        wrote = checkpoint.restore(Path(args.out), api=api)
    except Exception as exc:  # noqa: BLE001 - restore is a convenience; the command still runs
        print(f"restore skipped: {type(exc).__name__}: {str(exc)[:160]}", file=sys.stderr)
        return []
    if wrote:
        print(f"restored {len(wrote)} missing files from {checkpoint.STAGING_REPO}", file=sys.stderr)
    return wrote


def cmd_checkpoint(args: argparse.Namespace) -> int:
    """Upload the labels root to the private staging repo, or --restore missing files from it."""
    if args.restore:
        wrote = checkpoint.restore(Path(args.out))
        print(f"restored {len(wrote)} files into {args.out}")
        return 0
    return auto_checkpoint(argparse.Namespace(out=args.out, campaign=args.campaign), "manual checkpoint")


def funding_status(remaining: float | None, reserve: float, chunks: Sequence[Mapping[str, Any]], *,
                   approved: set[str]) -> tuple[bool, str]:
    """Can the key's credit pay for the next chunk, and for the rest of the campaign?

    Usable credit is ``remaining`` minus ``reserve``. Fails only when it does not cover
    the next unapproved chunk's caps (every arm of it); a shortfall on the remaining caps
    is reported in the message. ``remaining=None`` means the key has no credit limit.
    """
    todo = [c for c in chunks if str(c["id"]) not in approved]
    caps = sum(float(c.get("cap_usd") or 0) for c in todo)
    if remaining is None:
        return True, f"no credit limit; ${caps:.2f} in remaining caps"
    usable = float(remaining) - reserve
    msg = f"${usable:.2f} usable, ${caps:.2f} in remaining caps"
    if usable < caps:
        msg += f" (short by ${caps - usable:.2f}: add credit before the later chunks)"
    if not todo:
        return True, msg
    nxt = str(todo[0]["id"])
    need = sum(float(c.get("cap_usd") or 0) for c in todo if str(c["id"]) == nxt)
    if usable < need:
        return False, f"next chunk {nxt} needs ${need:.2f}; {msg}"
    return True, msg


def load_approvals(name: str) -> list[dict[str, Any]]:
    """Approved chunk configurations (empty before the first sign-off)."""
    path = approvals_path(name)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("approved") or [])


def resolve(name: str, entry: Mapping[str, Any]) -> tuple[list[str], str]:
    """Models and reasoning for a chunk, replacing ``winner_of_<id>`` with the approved arm."""
    approvals = load_approvals(name)

    def winner(ref: str) -> dict[str, Any]:
        """The approved entry of the chunk a ``winner_of_<id>`` placeholder names."""
        cid = ref.removeprefix("winner_of_")
        hits = [a for a in approvals if str(a["chunk"]) == cid]
        if not hits:
            raise SystemExit(f"chunk {entry['id']} uses the winner of chunk {cid}, which has no approved arm "
                             f"yet: run `labeling gate {cid} --arm N --approve --initials XX` first")
        return hits[-1]

    models = [winner(m)["models"][0] if str(m).startswith("winner_of_") else m for m in entry["models"]]
    reasoning = str(entry["reasoning"])
    if reasoning.startswith("winner_of_"):
        reasoning = winner(reasoning)["reasoning"]
    return models, reasoning


def provider_models(models: Sequence[str], provider: str, model_map: Sequence[str] = ()) -> list[str]:
    """The models a chunk uses on a non-default provider.

    ``model_map`` entries are ``campaign_model=provider_model``; a campaign model without
    an entry maps to the provider's only approved model. Two campaign models that map to
    the same provider model are refused: a two-model chunk needs two different models.
    """
    approved = [m for m, s in models_registry()["models"].items() if s["provider"] == provider]
    if not approved:
        raise SystemExit(f"models.yaml has no approved {provider} model")
    mapping = {}
    for item in model_map:
        src, _, dst = item.partition("=")
        if not dst or dst not in approved:
            raise SystemExit(f"--model-map {item!r}: use campaign_model=<one of {approved}>")
        mapping[src] = dst
    out = []
    for m in models:
        if m in mapping:
            out.append(mapping[m])
        elif len(approved) == 1:
            out.append(approved[0])
        else:
            raise SystemExit(f"--model-map is needed for {m}: {provider} has several approved models {approved}")
    if len(set(out)) != len(out):
        raise SystemExit(f"this chunk uses two models, but they both map to {out[0]} on {provider}. Approve a "
                         f"second {provider} model in models.yaml, or run this chunk on openrouter.")
    return out


REQUIRES_RE = re.compile(r"^\s*llm-dojo-scoring\s+(\d+(?:\.\d+)*)\b")


def _version(text: str) -> tuple[int, ...]:
    """``"0.28.0"`` -> ``(0, 28, 0)``."""
    return tuple(int(x) for x in text.split("."))


def requires_met(requires: str | None) -> tuple[bool, str]:
    """Whether a chunk's external ``requires:`` has landed.

    ``"llm-dojo-scoring X.Y.Z ..."`` is met once ``fields.yaml`` ``dojo_pin.version`` is at
    least X.Y.Z (the Dojo snapshot test ties that pin to the vendored scorer). Anything
    else cannot be checked here and stays unmet until ``--ack-requires``.
    """
    if not requires:
        return True, "no external requirement"
    m = REQUIRES_RE.match(str(requires))
    if not m:
        return False, f"{requires!r} is not machine-checkable"
    have = str(fields_registry()["dojo_pin"]["version"])
    ok = _version(have) >= _version(m[1])
    return ok, f"dojo_pin is llm-dojo-scoring {have}; {requires!r} {'met' if ok else 'not met'}"


def next_chunks(name: str, approvals: Sequence[Mapping[str, Any]]) -> list[str]:
    """Lines naming the next runnable chunk and any chunk held by an external ``requires``."""
    done = {str(a["chunk"]) for a in approvals}
    lines, held = [], []
    for c in campaign(name)["chunks"]:
        cid = str(c["id"])
        if cid in done or not all(str(n) in done for n in c.get("needs") or []):
            continue
        if c.get("requires") and not requires_met(c["requires"])[0]:
            if cid not in held:
                held.append(cid)
            continue
        if not lines:
            lines.append(f"next chunk: {cid} ({c['task']} / {c['set']}); `labeling chunk {cid} --dry-run` shows it")
    if held:
        lines.append(f"waiting on an external release (requires:): {', '.join(held)}")
    return lines


PING_MAX_AGE_HOURS = 24


def ping_problems(log: Sequence[Mapping[str, Any]], routes: Sequence[tuple[str, str]], *,
                  now: datetime | None = None, max_age_hours: float = PING_MAX_AGE_HOURS) -> list[str]:
    """Why each (model, reasoning) route may not be used yet: no ping, a failed latest ping, or a stale one.

    ``doctor`` checks catalog metadata only; the ping is the one check that sends the
    exact parameters a chunk sends, so a route that changed (for example reasoning that
    can no longer be turned off) is caught before a chunk spends on it.
    """
    now = now or datetime.now(timezone.utc)
    out = []
    for model, reasoning in routes:
        # per-provider and real-document probes answer other questions (which provider
        # reasons); only the plain route ping decides whether the route works
        mine = sorted((r for r in log if r.get("model") == model and str(r.get("reasoning")) == reasoning
                       and not r.get("pinned") and not r.get("probe")),
                      key=lambda r: str(r.get("utc") or ""))
        name = f"{model} reasoning={reasoning}"
        if not mine:
            out.append(f"{name}: never pinged")
            continue
        last = mine[-1]
        if last.get("error") or last.get("response") != {"ok": True, "word": "mailroom"} \
                or last.get("used_schema") is not True:  # a json_object fallback is not the strict schema
            out.append(f"{name}: latest ping failed: {str(last.get('error') or last.get('response'))[:160]}")
            continue
        age = (now - datetime.fromisoformat(str(last["utc"]).replace("Z", "+00:00"))).total_seconds() / 3600
        if age > max_age_hours:
            out.append(f"{name}: latest passing ping is older than {max_age_hours:g}h ({age:.0f}h)")
    return out


def cmd_chunk(args: argparse.Namespace) -> int:
    """Run one campaign chunk arm with the campaign's models, reasoning, cap and wall limit."""
    entry, arm = chunk_entry(args.campaign, args.id, args.arm)
    auto_restore(args)
    models, reasoning = resolve(args.campaign, entry)
    for need in entry.get("needs") or []:
        if not any(str(a["chunk"]) == str(need) for a in load_approvals(args.campaign)):
            raise SystemExit(f"chunk {entry['id']} starts after chunk {need} is approved")
    met, why = requires_met(entry.get("requires"))
    if not met and not args.dry_run:
        if not args.ack_requires:
            raise SystemExit(f"chunk {entry['id']} requires {entry['requires']} ({why}); bump the Dojo pin "
                             f"once it has landed, or re-run with --ack-requires")
        print(f"WARNING: --ack-requires overrides: {why}", file=sys.stderr)
    if args.provider == "openrouter" and not args.dry_run:
        log = args.out / "ping.jsonl"
        probs = ping_problems(export.read_journal(log) if log.exists() else [],
                              [(m, reasoning) for m in models])
        if probs:
            raise SystemExit("route not verified; run `labeling ping --initials XX` first:\n  "
                             + "\n  ".join(probs))
    if not args.dry_run and not getattr(args, "no_checkpoint", False):
        ok, why = checkpoint.write_access()
        if not ok:
            raise SystemExit(f"checkpoint not writable ({why}); paid answers must survive a lost checkout. "
                             "Fix the token, or pass --no-checkpoint")
    if args.provider != "openrouter":
        models = provider_models(models, args.provider, args.model_map)
    rows = runner.load_stage(args.stage)
    picked = runner.chunk(runner.select(rows, entry["task"], entry["set"]), entry.get("chunk"))
    camp = campaign(args.campaign)
    rc = runner.execute(
        rows, picked, task=entry["task"], set_name=entry["set"], chunk_spec=entry.get("chunk"), models=models,
        provider=args.provider, reasoning=reasoning, initials=args.initials,
        cap_usd=float(args.cap_usd or entry["cap_usd"]),
        max_wall_seconds=float(entry.get("max_wall_seconds") or camp["max_wall_seconds"]),
        out_dir=chunk_dir(args.out, entry, arm), dry_run=args.dry_run, workers=args.workers,
        chunk_id=f"{entry['id']}{f'.{arm}' if arm else ''}",
        require_checkpoint=not getattr(args, "no_checkpoint", False),
        **({"ack_requires": why} if not met and args.ack_requires and not args.dry_run else {}))
    return rc if args.dry_run else auto_checkpoint(args, f"chunk {entry['id']}{f'.{arm}' if arm else ''} run", rc)


# ---------------------------------------------------------------- gates

def _gold_tokens(spans: Sequence[Any]) -> set[str]:
    """Token union of gold CUAD spans."""
    out: set[str] = set()
    for s in spans:
        out |= set(verify.tokens(s.get("text") if isinstance(s, dict) else s))
    return out


#: Contract entity fields with an expert CUAD category that states them.
CUAD_GOLD_FIELDS = {
    "document_name": "Document Name", "parties": "Parties", "agreement_date": "Agreement Date",
    "effective_date": "Effective Date", "expiration_date": "Expiration Date",
    "renewal_terms": "Renewal Term", "termination_notice_period": "Notice Period To Terminate Renewal",
    "governing_law": "Governing Law",
}


def matches_cuad_gold(value: Any, type_: str, spans: Sequence[Any]) -> bool:
    """Does an accepted value agree with the expert CUAD spans for its category?

    Dates must equal a date parsed from a gold span (when any parses); everything else
    needs at least 80% of its tokens inside the gold spans' tokens.
    """
    if type_ == "date":
        gold_dates = {verify.parse_date(s.get("text") if isinstance(s, dict) else s) for s in spans} - {None}
        if gold_dates:
            return verify.parse_date(value) in gold_dates
    items = value if isinstance(value, list) else [value]
    gold = _gold_tokens(spans)
    toks = [t for i in items for t in verify.tokens(i)]
    return bool(toks) and sum(t in gold for t in toks) / len(toks) >= 0.8


def gate_field_accuracy(rows: Sequence[Mapping[str, Any]], out_dir: Path, floor: float) -> dict[str, Any]:
    """Per-field accuracy of accepted contract values against expert CUAD spans."""
    by_id = {tasks._ids(r)[0]: r for r in rows}
    per: dict[str, Counter] = {f: Counter() for f in CUAD_GOLD_FIELDS}
    for f in export.read_csv(out_dir / "fields.csv"):
        if f["field"] not in CUAD_GOLD_FIELDS:
            continue
        row = by_id.get(f["document_id"])
        labels = tasks._json_obj(tasks.gt_fields(row).get("cuad_clause_labels")) if row else {}
        spans = (labels or {}).get(CUAD_GOLD_FIELDS[f["field"]]) or []
        if not spans:
            continue
        c = per[f["field"]]
        c["gold"] += 1
        if f["final_decision"] != "accept":
            c["not_accepted"] += 1
            continue
        raw = f["final_value"]
        try:  # lists are written as JSON; a plain string may also start with "["
            value = json.loads(raw) if raw[:1] == "[" else raw
        except ValueError:
            value = raw
        c["correct" if matches_cuad_gold(value, f["type"], spans) else "wrong"] += 1
    fields = {}
    for name, c in per.items():
        scored = c["correct"] + c["wrong"]
        fields[name] = {**dict(c), "accuracy": round(c["correct"] / scored, 4) if scored else None}
    scored = {k: v for k, v in fields.items() if (v.get("correct", 0) + v.get("wrong", 0)) >= 5}
    ok = bool(scored) and all(v["accuracy"] >= floor for v in scored.values())
    return {"kind": "field_accuracy", "floor": floor, "fields": fields, "passed": ok,
            "note": "fields with fewer than 5 scored values are reported but not gated"}


def gate_consideration_accuracy(rows: Sequence[Mapping[str, Any]], out_dir: Path, floor: float,
                                models: Sequence[str]) -> dict[str, Any]:
    """Each model's ``merger_consideration`` answers against the expert MAUD answer.

    Every selected row must be answered by every model; a missing, ``not_found`` or wrong
    answer counts against the model. Passes when every model reaches ``floor``. The
    result carries a confusion table per model (gold -> answered -> count) and the
    accuracy of the values the two models agreed on.
    """
    gold = {tasks._ids(r)[0]: derive.maud_consideration_answer(r) for r in rows}
    gold = {k: v for k, v in gold.items() if v}
    path = out_dir / "fields.csv"
    found = [f for f in export.read_csv(path) if f["field"] == "merger_consideration"] if path.exists() else []
    answers: dict[str, dict[str, str]] = {m: {} for m in models}
    agreed = Counter()
    for f in found:
        if f["document_id"] not in gold:
            continue
        for slot in ("a", "b"):
            m = f.get(f"model_{slot}")
            if m in answers:
                val = f.get(f"model_{slot}_value") if f.get(f"model_{slot}_status") == "present" else ""
                answers[m][f["document_id"]] = verify.normalize(val, "label") or "(none)"
        if f.get("final_decision") == "accept":
            ok = verify.normalize(f.get("final_value"), "label") == verify.normalize(gold[f["document_id"]], "label")
            agreed["correct" if ok else "wrong"] += 1
    per_model = {}
    for m in models:
        got = answers[m]
        confusion: dict[str, Counter] = {}
        correct = 0
        for doc, g in gold.items():
            a = got.get(doc, "(missing)")
            confusion.setdefault(g, Counter())[a] += 1
            correct += a == verify.normalize(g, "label")
        acc = round(correct / len(gold), 4) if gold else None
        per_model[m] = {"answered": len(got), "correct": correct, "accuracy": acc,
                        "confusion": {g: dict(c) for g, c in sorted(confusion.items())}}
    scored = agreed["correct"] + agreed["wrong"]
    ok = bool(gold) and all(v["accuracy"] is not None and v["accuracy"] >= floor
                            and v["answered"] == len(gold) for v in per_model.values())
    return {"kind": "consideration_accuracy", "floor": floor, "gold_rows": len(gold), "models": per_model,
            "agreed": {**dict(agreed), "accuracy": round(agreed["correct"] / scored, 4) if scored else None},
            "passed": ok}


def gate_human_verdicts(out_dir: Path, floor: float) -> dict[str, Any]:
    """Precision of accepted values among the rows a reviewer marked correct or wrong."""
    rows = export.read_csv(out_dir / "fields.csv")
    accepted = [r for r in rows if r["decision"] == "accept"]
    marked = [r for r in accepted if r.get("reviewer_verdict") in ("correct", "wrong")]
    need = min(REVIEW_MIN, len(accepted))
    correct = sum(r["reviewer_verdict"] == "correct" for r in marked)
    precision = round(correct / len(marked), 4) if marked else None
    ok = len(marked) >= need and precision is not None and precision >= floor
    return {"kind": "human_verdicts", "floor": floor, "accepted_rows": len(accepted), "reviewed": len(marked),
            "reviews_needed": need, "precision": precision, "passed": ok,
            "how": "in fields.csv, set reviewer_verdict to correct or wrong on accepted rows"}


def gate_agreement(out_dir: Path, signed_off: bool,
                   expected: set[tuple[str, str]] | None = None) -> dict[str, Any]:
    """Decision mix, unanswered calls, and a fixed sample of accepted rows for a human read.

    ``expected`` is every (document_id, model) pair the chunk should have answered; a pair
    with no successful call (failed, or never sent because the run stopped) blocks the gate.
    """
    rows = export.read_csv(out_dir / "fields.csv")
    calls = export.read_journal(out_dir / "calls.jsonl")
    ok_keys = {(c["document_id"], c["model"]) for c in calls if not c.get("error")}
    failed = {(c["document_id"], c["model"]) for c in calls if c.get("error")} - ok_keys
    if expected is not None:
        failed |= expected - ok_keys
    mix = Counter(r["decision"] for r in rows)
    adj = mix["adjudicate"] / len(rows) if rows else 0.0
    accepted = [r for r in rows if r["decision"] == "accept"]
    sample = random.Random(42).sample(accepted, min(SAMPLE_SIZE, len(accepted)))
    export.write_csv(out_dir / "sample.csv", export.FIELD_COLUMNS, sample)
    ok = bool(rows) and not failed and adj <= AGREEMENT_MAX_ADJUDICATE and signed_off
    return {"kind": "agreement", "decisions": dict(mix), "adjudicate_share": round(adj, 4),
            "max_adjudicate_share": AGREEMENT_MAX_ADJUDICATE, "unanswered_calls": len(failed),
            "sample_csv": str(out_dir / "sample.csv"), "signed_off": signed_off, "passed": ok,
            "how": "read sample.csv, then re-run with --signed-off"}


def gate_result(name: str, entry: Mapping[str, Any], arm: int | None, out: Path, stage: Path,
                signed_off: bool = False) -> dict[str, Any]:
    """Score one chunk arm's gate from its output directory (offline)."""
    from .. import v9_4_revision as v94  # noqa: PLC0415

    out_dir = chunk_dir(out, entry, arm)
    if not (out_dir / "calls.jsonl").exists():
        raise SystemExit(f"{out_dir} has no calls.jsonl; run `labeling chunk {entry['id']}` first")
    rows = runner.load_stage(stage)
    picked = runner.chunk(runner.select(rows, entry["task"], entry["set"]), entry.get("chunk"))
    models, reasoning = resolve(name, entry)
    provider = "openrouter"
    started = [r for r in export.read_journal(out_dir / "runs.jsonl") if r.get("started_utc") and r.get("models")]
    if started:  # score the models the chunk actually ran (e.g. the mapped Modal model)
        models, provider = list(started[-1]["models"]), str(started[-1].get("provider") or provider)
    runner.review(rows, entry["task"], out_dir, reasoning=reasoning)
    gate = dict(entry["gate"])
    kind = gate.pop("kind")
    if kind == "cuad_f1":
        res = v94.score_cuad_calibration(picked, export.read_journal(out_dir / "cuad-labels.jsonl"),
                                         runner.cuad_vocab(rows))
        ok = (res["documents"] == res["expected_documents"] and res["micro_f1"] >= gate["micro_f1"]
              and res["micro_precision"] >= gate["precision"])
        res = {"kind": kind, **{k: v for k, v in res.items() if k != "per_category"}, "floors": gate, "passed": ok}
    elif kind == "intent_kappa":
        res = v94.score_intent_calibration(picked, export.read_journal(out_dir / "intent-labels.jsonl"))
        ok = res["documents"] == res["expected_documents"] and res["cohen_kappa"] >= gate["kappa"]
        res = {"kind": kind, **res, "floors": gate, "passed": ok}
    elif kind == "field_accuracy":
        res = gate_field_accuracy(rows, out_dir, gate["accuracy"])
    elif kind == "human_verdicts":
        res = gate_human_verdicts(out_dir, gate["precision"])
    elif kind == "agreement":
        res = gate_agreement(out_dir, signed_off, {(tasks._ids(r)[0], m) for r in picked for m in models})
    elif kind == "consideration_accuracy":
        res = gate_consideration_accuracy(picked, out_dir, gate["accuracy"], models)
    elif kind == "review":
        docs = export.read_csv(out_dir / "documents.csv") if (out_dir / "documents.csv").exists() else []
        res = {"kind": kind, "documents": len(docs), "expected_documents": len(picked),
               "signed_off": signed_off, "passed": signed_off and len(docs) == len(picked),
               "how": "read documents.csv / spans.csv, then re-run with --signed-off"}
    else:
        raise SystemExit(f"unknown gate kind {kind!r}")
    every = [c for c in export.read_journal(out_dir / "calls.jsonl") if c.get("reasoning") == reasoning]
    calls = [c for c in every if not c.get("error")]
    res.update(chunk=entry["id"], arm=arm, task=entry["task"], models=models, provider=provider, reasoning=reasoning,
               prompt_sha256=sorted({c["prompt_sha256"] for c in calls}),
               served_providers=sorted({c.get("served_provider") or "" for c in calls} - {""}),
               spent_usd=round(sum(c.get("cost_usd") or 0 for c in every), 4), scored_utc=_now())
    current = runner.current_prompt_calls(rows, entry["task"], calls, entry["set"])
    outside = pin_violations(current, reasoning)
    if outside:
        res.update(passed=False, served_outside_pin=outside)
    unreasoned = unreasoned_providers(current, reasoning)
    if unreasoned:
        res.update(passed=False, reasoning_not_honoured=unreasoned)
    (out_dir / "gate.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    return res


def pin_violations(calls: Sequence[Mapping[str, Any]], reasoning: str) -> list[str]:
    """``"<model> via <provider>"`` for each answer served outside its model's route pin."""
    out: set[str] = set()
    for c in calls:
        for p in providers.served_outside_route(providers.model_route(c["model"]), reasoning,
                                                {c.get("served_provider")}):
            out.add(f"{c['model']} via {p}")
    return sorted(out)


UNREASONED_MAX_SHARE = 0.5
#: and at least this many zero-token answers: one or two easy documents are not a pattern
UNREASONED_MIN_ZEROS = 3


def unreasoned_providers(calls: Sequence[Mapping[str, Any]], reasoning: str) -> dict[str, str]:
    """``{"<model> via <provider>": "<zero>/<n>"}`` where most answers at a reasoning level
    other than off spent 0 reasoning tokens: that provider is not honouring the level.

    A tiny ping cannot show this; a model may skip reasoning on one easy prompt, so only a
    majority of zero-token answers on real documents flags the provider.
    """
    if reasoning == "off":
        return {}
    seen: dict[str, list[int]] = {}
    for c in calls:
        key = f"{c['model']} via {c.get('served_provider') or '?'}"
        tally = seen.setdefault(key, [0, 0])
        tally[0] += not c.get("reasoning_tokens")
        tally[1] += 1
    return {k: f"{z}/{n}" for k, (z, n) in sorted(seen.items()) if z > UNREASONED_MAX_SHARE * n and z >= UNREASONED_MIN_ZEROS}


def cmd_gate(args: argparse.Namespace) -> int:
    """Score a chunk's gate; with --approve, record a pass in approved.yaml."""
    entry, arm = chunk_entry(args.campaign, args.id, args.arm)
    auto_restore(args)
    res = gate_result(args.campaign, entry, arm, args.out, args.stage, signed_off=args.signed_off)
    if res["provider"] != args.provider:
        raise SystemExit(f"--provider {args.provider}, but chunk {entry['id']} ran on {res['provider']} "
                         f"(runs.jsonl); the gate scores the models the chunk ran")
    print(json.dumps(res, indent=1))
    if not args.approve:
        return 0 if res["passed"] else 1
    if not res["passed"]:
        raise SystemExit("gate did not pass; nothing approved")
    if not args.initials or not spend.INITIALS_RE.match(args.initials):
        raise SystemExit("--approve needs --initials (a human's 2-6 capital letters)")
    approvals = load_approvals(args.campaign)
    same = [a for a in approvals if str(a["chunk"]) == str(entry["id"])]
    if same and not args.replace:
        raise SystemExit(f"chunk {entry['id']} already has an approved arm; pass --replace to change it")
    approvals = [a for a in approvals if str(a["chunk"]) != str(entry["id"])] if args.replace else approvals
    approvals.append({
        "chunk": str(entry["id"]), "arm": arm, "task": entry["task"], "set": entry["set"],
        "slice": entry.get("chunk"), "models": res["models"], "provider": res["provider"],
        "reasoning": res["reasoning"],
        "temperature": {m: providers.temperature(m) for m in res["models"]}, "prompt_sha256": res["prompt_sha256"],
        "served_providers": res["served_providers"],
        "gate": {k: v for k, v in res.items() if k in ("kind", "micro_f1", "micro_precision", "cohen_kappa",
                                                       "precision", "adjudicate_share", "passed")},
        "approved_by": args.initials, "approved_utc": _now()})
    path = approvals_path(args.campaign)
    path.write_text("# Written by `labeling gate --approve`. Commit this file; the chunk's label output\n"
                    "# stays out of git (private HF staging repo only).\n"
                    + yaml.safe_dump({"approved": approvals}, sort_keys=False), encoding="utf-8")
    print(f"approved chunk {entry['id']}{f' arm {arm}' if arm else ''} -> {path}")
    return auto_checkpoint(args, f"chunk {entry['id']} approved by {args.initials}")


# ---------------------------------------------------------------- status

def cmd_status(args: argparse.Namespace) -> int:
    """One line per chunk arm: calls answered, spend, gate, approval."""
    auto_restore(args)
    camp = campaign(args.campaign)
    approved = {str(a["chunk"]): a for a in load_approvals(args.campaign)}
    seen: Counter = Counter()
    counts = Counter(str(c["id"]) for c in camp["chunks"])
    print(f"{'chunk':<6} {'task':<18} {'set':<12} {'slice':<5} {'answered':>9} {'spent':>8} {'cap':>6}  gate / approval")
    for entry in camp["chunks"]:
        cid = str(entry["id"])
        seen[cid] += 1
        arm = seen[cid] if counts[cid] > 1 else None
        d = chunk_dir(args.out, entry, arm)
        calls = export.read_journal(d / "calls.jsonl")
        ok = sum(1 for c in calls if not c.get("error"))
        spent = sum(c.get("cost_usd") or 0 for c in calls)
        gate = json.loads((d / "gate.json").read_text()) if (d / "gate.json").exists() else None
        state = "not run" if not calls else ("gate passed" if gate and gate["passed"] else
                                             "gate failed" if gate else "ran, gate not scored")
        appr = approved.get(cid)
        if appr and (arm is None or appr.get("arm") == arm):
            state += f"; approved by {appr['approved_by']}"
        label = f"{cid}{f'.{arm}' if arm else ''}"
        print(f"{label:<6} {entry['task']:<18} {entry['set']:<12} {entry.get('chunk') or 'all':<5} "
              f"{ok:>9} {spent:>8.4f} {entry['cap_usd']:>6.2f}  {state}")
    return 0


# ---------------------------------------------------------------- live checks (no key needed)

def _get_json(url: str, timeout: float = 30.0, headers: Mapping[str, str] | None = None) -> Any:
    """GET a JSON document."""
    req = urllib.request.Request(url, headers=dict(headers or {}))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def price_report(fetch: Callable[[str], Any] = _get_json) -> dict[str, Any]:
    """Compare models.yaml with the live OpenRouter catalog and each model's endpoints."""
    reg = models_registry()
    catalog = {m["id"]: m for m in fetch(PUBLIC_MODELS_URL)["data"]}
    problems: list[str] = []
    out: dict[str, Any] = {}
    for model, spec in reg["models"].items():
        if spec["provider"] != "openrouter":
            continue
        live = catalog.get(model)
        if live is None:
            problems.append(f"{model}: not in the OpenRouter catalog")
            continue
        route = spec.get("route") or {}
        eps = fetch(f"{PUBLIC_MODELS_URL}/{model}/endpoints")["data"]["endpoints"]
        need = set(REQUIRED_PARAMETERS) | ({"seed"} if route.get("seed") else set())
        if route.get("temperature", True) is False:
            need.discard("temperature")
        cap = route.get("max_price") or {}
        usable = []
        for ep in eps:
            slug = str(ep.get("tag") or "").split("/")[0]
            if route.get("only") and slug not in route["only"]:
                continue
            if route.get("quantizations") and ep.get("quantization") not in route["quantizations"]:
                continue
            p_in = float(ep["pricing"]["prompt"]) * 1e6
            p_out = float(ep["pricing"]["completion"]) * 1e6
            if cap and (p_in > cap["prompt"] + 1e-9 or p_out > cap["completion"] + 1e-9):
                continue
            missing = sorted(need - set(ep.get("supported_parameters") or []))
            usable.append({"tag": ep.get("tag"), "quantization": ep.get("quantization"), "prompt": round(p_in, 4),
                           "completion": round(p_out, 4), "context": ep.get("context_length"),
                           "missing_parameters": missing})
        serving = [u for u in usable if not u["missing_parameters"]]
        if not serving:
            problems.append(f"{model}: no pinned endpoint supports {sorted(need)} within max_price")
        if serving:
            worst_in = max(u["prompt"] for u in serving)
            worst_out = max(u["completion"] for u in serving)
            if worst_in > spec["prompt"] + 1e-9 or worst_out > spec["completion"] + 1e-9:
                problems.append(f"{model}: models.yaml price {spec['prompt']}/{spec['completion']} is below the "
                                f"priciest pinned endpoint {worst_in}/{worst_out}")
        out[model] = {"list_price": [round(float(live["pricing"]["prompt"]) * 1e6, 4),
                                     round(float(live["pricing"]["completion"]) * 1e6, 4)],
                      "yaml_price": [spec["prompt"], spec["completion"]], "context": live.get("context_length"),
                      "serving_endpoints": serving,
                      "skipped_endpoints": [u for u in usable if u["missing_parameters"]]}
    for model, spec in (reg.get("comparison") or {}).items():
        live = catalog.get(model)
        if live is None:
            problems.append(f"comparison {model}: not in the OpenRouter catalog")
    return {"checked": reg["checked"], "models": out, "problems": problems}


def cmd_prices(args: argparse.Namespace) -> int:
    """Print the live price and routing report; exit 1 on a problem."""
    rep = price_report()
    print(json.dumps(rep, indent=1))
    return 1 if rep["problems"] else 0


# ---------------------------------------------------------------- stage

def cmd_stage(args: argparse.Namespace) -> int:
    """Build the v9.4 stage from the public Hub tag when it is missing, then check its sha."""
    target = args.stage / "ground_truth_hardened.jsonl"
    if not target.exists() and args.stage.resolve() != runner.DEFAULT_STAGE.resolve():
        raise SystemExit(f"{target} is missing; `labeling stage` builds only the default stage "
                         f"({runner.DEFAULT_STAGE}). Build that, then copy it or pass --stage to it.")
    if not target.exists():
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        for module, extra in (("mailroom_eda.v9_3_revision", []),
                              ("mailroom_eda.v9_4_revision", ["--no-cuad-journal", "--no-audit"])):
            print(f"building: python -m {module} {' '.join(extra)}", file=sys.stderr)
            subprocess.run([sys.executable, "-m", module, *extra], cwd=ROOT, env=env, check=True)
    return 0 if _stage_check(args.stage, args.campaign)[0] else 1


def _stage_check(stage: Path, name: str) -> tuple[bool, str]:
    """Does the stage exist and match the campaign's pinned sha?"""
    target = stage / "ground_truth_hardened.jsonl"
    if not target.exists():
        return False, f"missing {target}: run `labeling stage`"
    h = hashlib.sha256(target.read_bytes()).hexdigest()
    want = campaign(name).get("stage_sha256")
    if want and h != want:
        return False, f"stage sha {h[:12]} != campaign stage_sha256 {want[:12]} (selections and costs differ)"
    print(f"stage OK: {target} sha256 {h[:12]}", file=sys.stderr)
    return True, f"sha256 {h[:12]}"


# ---------------------------------------------------------------- doctor and ping

def cmd_doctor(args: argparse.Namespace) -> int:
    """Run every readiness check and print PASS/FAIL lines. Makes no paid call."""
    results: list[tuple[str, bool, str]] = []
    auto_restore(args)

    def check(name: str, fn: Callable[[], tuple[bool, str]]) -> None:
        """Run one doctor check and record its result."""
        try:
            ok, msg = fn()
        except Exception as err:  # noqa: BLE001 - report every failure, keep checking
            ok, msg = False, f"{type(err).__name__}: {err}"
        results.append((name, ok, msg))

    check("python >= 3.11", lambda: (sys.version_info >= (3, 11), sys.version.split()[0]))
    check("stage", lambda: _stage_check(args.stage, args.campaign))
    pin = fields_registry()["dojo_pin"]
    snap = dojo_snapshot()
    check("dojo snapshot pin", lambda: ((snap["dojo_version"], snap["dojo_commit"]) == (pin["version"], pin["commit"]),
                                        f"llm-dojo-scoring {pin['version']}"))

    def prompts_synced() -> tuple[bool, str]:
        """The plan quotes every prompt verbatim."""
        proc = subprocess.run([sys.executable, str(ROOT / "scripts/labeling/sync_plan_prompts.py"), "--check"],
                              capture_output=True, text=True)
        return proc.returncode == 0, proc.stdout.strip()

    check("plan prompts", prompts_synced)

    def costs_current() -> tuple[bool, str]:
        """``costs.md`` matches the campaign and the stage."""
        rows = runner.load_stage(args.stage)
        text = runner.render_costs(runner.campaign_costs(rows, args.campaign))
        path = CAMPAIGNS_DIR / args.campaign / "costs.md"
        return path.read_text(encoding="utf-8") == text, "costs.md matches models.yaml and the stage"

    check("costs.md", costs_current)

    def prices() -> tuple[bool, str]:
        """The live OpenRouter catalog still serves every route at the listed prices."""
        rep = price_report()
        n = {m: len(v["serving_endpoints"]) for m, v in rep["models"].items()}
        return not rep["problems"], "; ".join(rep["problems"]) or f"serving endpoints per model: {n}"

    check("live prices and routing", prices)

    def key() -> tuple[bool, str]:
        """An OpenRouter key is set (its value is never printed)."""
        if not os.environ.get("OPENROUTER_API_KEY"):
            return False, "OPENROUTER_API_KEY is not set in this shell"
        data = providers.key_status(providers.Backend.from_env("openrouter"))
        rem = data.get("limit_remaining")
        reserve = models_registry().get("key_reserve_usd", 3.0)
        funding.append(None if rem is None else float(rem))
        if rem is None:
            return True, f"key OK, no credit limit; usage so far ${float(data.get('usage') or 0):.2f}"
        return float(rem) > reserve, f"key OK, ${float(rem):.2f} remaining (reserve ${reserve:.2f})"

    funding: list[float | None] = []
    check("OpenRouter key", key)
    check("HF checkpoint write access", checkpoint.write_access)
    if os.environ.get("VLLM_BASE_URL") or getattr(args, "modal", False):
        check("Modal vLLM endpoint", modal_check)
    approvals = load_approvals(args.campaign)
    if funding:
        check("funding", lambda: funding_status(
            funding[0], models_registry().get("key_reserve_usd", 3.0), campaign(args.campaign)["chunks"],
            approved={str(a["chunk"]) for a in approvals}))
    check("approvals", lambda: (True, f"{len(approvals)} chunks approved"))
    width = max(len(n) for n, _, _ in results)
    for name, ok, msg in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {msg}")
    for line in next_chunks(args.campaign, approvals):
        print(line)
    return 0 if all(ok for _, ok, _ in results) else 1


def modal_models() -> list[str]:
    """The models.yaml entries served by the sandbox's Modal vLLM app."""
    return sorted(m for m, s in models_registry()["models"].items() if s.get("provider") == "modal")


def modal_check(env: Mapping[str, str] | None = None,
                fetch: Callable[..., Any] = _get_json) -> tuple[bool, str]:
    """``VLLM_BASE_URL``/``VLLM_API_KEY`` are set and a free ``GET /v1/models`` lists the Modal model.

    ``VLLM_API_KEY`` is the value the sandbox deploys as ``MODAL_VLLM_API_TOKEN``; the key
    is sent but never printed. Generates nothing, so it costs only the endpoint's idle time.
    """
    env = os.environ if env is None else env
    missing = [n for n in ("VLLM_BASE_URL", "VLLM_API_KEY") if not env.get(n)]
    if missing:
        return False, (f"{' and '.join(missing)} not set (VLLM_API_KEY is the value deployed as "
                       "MODAL_VLLM_API_TOKEN)")
    backend = providers.Backend.from_env("modal", env)
    host = urllib.parse.urlparse(backend.base_url).hostname or ""
    if not host.endswith(".modal.run") and host not in ("localhost", "127.0.0.1"):
        return False, f"VLLM_BASE_URL host {host!r} is not a *.modal.run app URL"
    data = fetch(f"{backend.base_url}/models", headers={"Authorization": f"Bearer {backend.api_key}"})
    served = sorted(str(m.get("id")) for m in (data or {}).get("data") or [])
    want = modal_models()
    lacking = [m for m in want if m not in served]
    if lacking:
        return False, f"endpoint serves {served or 'nothing'}; models.yaml expects {lacking}"
    return True, f"{backend.base_url} serves {', '.join(want)}"


PING_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ok", "word"],
               "properties": {"ok": {"type": "boolean"}, "word": {"type": "string"}}}


def cmd_ping(args: argparse.Namespace, chat=providers.chat, key_status=providers.key_status) -> int:
    """One strict-schema request per approved OpenRouter model and reasoning level in use."""
    spend.require_run_flags(initials=args.initials, cap_usd=args.cap_usd, max_wall_seconds=300)
    backend = providers.Backend.from_env("openrouter")
    spend.check_key_headroom(key_status(backend), args.cap_usd)
    budget = spend.Budget(args.cap_usd, 300)
    picked = set(getattr(args, "chunks", None) or [])
    used = {(m, str(c["reasoning"])) for c in campaign(args.campaign)["chunks"] for m in c["models"]
            if not picked or str(c["id"]) in picked
            if not str(m).startswith("winner_of_") and not str(c["reasoning"]).startswith("winner_of_")}
    msgs = [{"role": "system", "content": "Reply with JSON only."},
            {"role": "user", "content": 'Return {"ok": true, "word": "mailroom"}.'}]
    schema: Mapping[str, Any] = PING_SCHEMA
    probe = ""

    def answered(parsed: Any) -> bool:
        return parsed == {"ok": True, "word": "mailroom"}

    if getattr(args, "real_doc", False):
        # one real correspondence document under the intent prompt: a tiny prompt cannot show
        # whether a provider actually reasons at a level on real text
        rows = runner.select(runner.load_stage(args.stage), "intent", "calibration")
        row = min(rows, key=lambda r: str(r.get("document_id")))
        req = tasks.build_intent(row, "a")
        msgs, schema, probe = req.messages, req.schema, f"real_doc:{req.document_id}"
        enum = set(schema["properties"]["intent"]["enum"])

        def answered(parsed: Any) -> bool:  # noqa: F811
            return isinstance(parsed, dict) and parsed.get("intent") in enum
    failures = 0
    log = args.out / "ping.jsonl"
    per_provider = bool(getattr(args, "per_provider", False))
    for model, reasoning in sorted(used):
        if models_registry()["models"].get(model, {}).get("provider") != "openrouter":
            continue
        base = providers.effective_route(providers.model_route(model), reasoning)
        # the route as a whole, then (with --per-provider) each pinned provider alone
        targets: list[str | None] = [None] + (list(base.get("only") or []) if per_provider else [])
        for pinned in targets:
            reserved = budget.reserve(model, spend.estimate_tokens(json.dumps(msgs)) if probe else 50,
                                      300 + runner.REASONING_ALLOWANCE)
            rec = {"model": model, "reasoning": reasoning, "initials": args.initials, "utc": _now(),
                   **({"pinned": pinned} if pinned else {}), **({"probe": probe} if probe else {})}
            kw = {"route": {**base, "only": [pinned]}} if pinned else {}
            name = f"{model} reasoning={reasoning}" + (f" pinned={pinned}" if pinned else "")
            try:
                comp = chat(backend, model, msgs, schema, reasoning=reasoning,
                            max_tokens=300 + (runner.REASONING_ALLOWANCE if reasoning != "off" else 0), **kw)
            except providers.ProviderError as err:
                budget.release(reserved)
                failures += pinned is None
                export.append_journal(log, {**rec, "error": str(err)})
                print(f"FAIL  {name}: {err}")
                continue
            cost = budget.settle(model, comp.prompt_tokens, comp.completion_tokens, comp.cost_usd, reserved)
            good = answered(comp.parsed) and comp.used_schema
            failures += (not good) and pinned is None
            export.append_journal(log, {**rec, "served_provider": comp.served_provider,
                                        "served_model": comp.served_model, "used_schema": comp.used_schema,
                                        "response": comp.parsed, "reasoning_tokens": comp.reasoning_tokens,
                                        "cost_usd": cost})
            print(f"{'PASS' if good else 'FAIL'}  {name} via {comp.served_provider or '?'} "
                  f"schema={'strict' if comp.used_schema else 'json_object fallback'} "
                  f"reasoning_tokens={comp.reasoning_tokens} ${cost:.6f}")
    print(f"spent ${budget.spent_usd:.6f}")
    rc = 1 if failures else 0
    # a hand-built Namespace without the flag (tests, internal callers) does not stage
    if not log.exists() or getattr(args, "no_checkpoint", True):
        return rc
    # the ping log gates every paid chunk on any machine, so it is staged like an answer
    return auto_checkpoint(args, f"ping by {args.initials}", rc, api=getattr(args, "checkpoint_api", None))


# ---------------------------------------------------------------- argparse

def check_class(folder: Path) -> tuple[list[str], dict[str, str]]:
    """Validate a proposed class folder against the live registry, offline.

    The folder's ``fields.yaml`` (``vocabularies``, ``classes``, ``tasks``) is merged into a
    copy of ``labeling/fields.yaml`` and its ``prompts/`` beside the live prompts; then
    ``validate_registry`` runs and each new task renders one request for a placeholder row.
    Returns (problems, {task: rendered user message}).
    """
    import copy  # noqa: PLC0415
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    from unittest import mock  # noqa: PLC0415

    from . import registry  # noqa: PLC0415

    folder = Path(folder)
    spec_path = folder / "fields.yaml"
    if not spec_path.exists():
        return [f"{spec_path}: missing"], {}
    extra = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    merged = copy.deepcopy(fields_registry())
    problems: list[str] = []
    for section in ("vocabularies", "classes", "tasks"):
        for key, value in (extra.get(section) or {}).items():
            if key in (merged.get(section) or {}):
                problems.append(f"{section}.{key} already exists in labeling/fields.yaml")
            merged.setdefault(section, {})[key] = value
    for t in extra.get("tasks") or {}:
        if t in tasks.BUILDERS:
            problems.append(f"task {t}: shadows a built-in task")
    samples: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for src in registry.PROMPTS_DIR.glob("*.md"):
            shutil.copy(src, tmp_dir / src.name)
        for src in sorted((folder / "prompts").glob("*.md")):
            if (tmp_dir / src.name).exists():
                problems.append(f"prompts/{src.name}: would overwrite a live prompt")
            shutil.copy(src, tmp_dir / src.name)
        problems += registry.validate_registry(merged, prompts_dir=tmp_dir)
        if problems:
            return problems, samples
        registry.prompt.cache_clear()
        try:
            with mock.patch.object(registry, "fields_registry", lambda: merged), \
                    mock.patch.object(registry, "PROMPTS_DIR", tmp_dir):
                for t, spec in (extra.get("tasks") or {}).items():
                    cls = spec["class"]
                    subs = (merged["classes"][cls].get("subclasses") or ["unknown"])
                    row = {"filename": f"check-class-{cls}.txt", "expected": cls,
                           "expected_subclass": subs[0], "gt_fields": "{}",
                           "doc_text": "PLACEHOLDER DOCUMENT TEXT"}
                    try:
                        req = tasks.build_generic(row, t)
                    except Exception as e:  # noqa: BLE001 - report every failure as a problem
                        problems.append(f"task {t}: building a request failed: {e!r}")
                        continue
                    props = set(req.schema["properties"]["fields"]["properties"])
                    want = {k for k, f in merged["classes"][cls]["fields"].items() if f.get("task") == t}
                    if props != want:
                        problems.append(f"task {t}: schema fields {sorted(props)} differ from fields.yaml {sorted(want)}")
                    samples[t] = req.messages[-1]["content"]
        finally:
            registry.prompt.cache_clear()
    return problems, samples


def cmd_check_class(args: argparse.Namespace) -> int:
    """``check-class``: print problems (exit 1) or the rendered sample request (exit 0)."""
    problems, samples = check_class(args.folder)
    for p in problems:
        print(f"PROBLEM  {p}")
    if problems:
        print(f"{len(problems)} problem(s); fix them before merging the class.")
        return 1
    for t, text in samples.items():
        print(f"--- task {t}: sample user message for a placeholder row ---")
        print(text[:4000])
    print(f"OK: {args.folder} is consistent with labeling/fields.yaml and the prompt rules.")
    return 0


def add_commands(sub: Any) -> None:
    """Register the operator commands on the runner's subparsers."""
    def common(p: argparse.ArgumentParser) -> None:
        """Options every subcommand takes."""
        p.add_argument("--campaign", default="v10")
        p.add_argument("--stage", type=Path, default=runner.DEFAULT_STAGE)
        p.add_argument("--out", type=Path, default=runner.DEFAULT_OUT)
        p.add_argument("--no-restore", action="store_true",
                       help=f"do not first pull staged files missing locally from {checkpoint.STAGING_REPO}")

    p = sub.add_parser("chunk", help="run one campaign chunk as campaign.yaml defines it")
    common(p)
    p.add_argument("id")
    p.add_argument("--arm", type=int)
    p.add_argument("--initials")
    p.add_argument("--provider", default="openrouter", choices=providers.PROVIDERS)
    p.add_argument("--cap-usd", type=float, help="override the campaign cap (lower only is sensible)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--model-map", action="append", default=[], metavar="CAMPAIGN_MODEL=PROVIDER_MODEL",
                   help="with --provider modal: which approved Modal model replaces a campaign model")
    p.add_argument("--ack-requires", action="store_true",
                   help="the external release named by the chunk's requires: has landed")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-checkpoint", action="store_true",
                   help="do not upload the answers to the private HF staging repo (not recommended)")
    p.set_defaults(operate=cmd_chunk)

    p = sub.add_parser("gate", help="score a chunk's gate; --approve records a pass")
    common(p)
    p.add_argument("id")
    p.add_argument("--arm", type=int)
    p.add_argument("--approve", action="store_true")
    p.add_argument("--replace", action="store_true")
    p.add_argument("--signed-off", action="store_true", help="you read the chunk's CSVs / sample.csv")
    p.add_argument("--initials")
    p.add_argument("--provider", default="openrouter", choices=providers.PROVIDERS)
    p.add_argument("--no-checkpoint", action="store_true")
    p.set_defaults(operate=cmd_gate)

    p = sub.add_parser("checkpoint", help=f"upload label output to the private {checkpoint.STAGING_REPO}")
    common(p)
    p.add_argument("--restore", action="store_true", help="download staged files missing locally")
    p.set_defaults(operate=cmd_checkpoint)

    p = sub.add_parser("status", help="progress, spend, gate and approval for every chunk")
    common(p)
    p.set_defaults(operate=cmd_status)

    p = sub.add_parser("prices", help="live OpenRouter prices and pinned-provider parameters (no key)")
    common(p)
    p.set_defaults(operate=cmd_prices)

    p = sub.add_parser("stage", help="build data/v9_4/stage if missing, then check its sha")
    common(p)
    p.set_defaults(operate=cmd_stage)

    p = sub.add_parser("doctor", help="every readiness check (no paid call)")
    common(p)
    p.add_argument("--modal", action="store_true",
                   help="also check the Modal vLLM endpoint (on by default when VLLM_BASE_URL is set)")
    p.set_defaults(operate=cmd_doctor)

    p = sub.add_parser("check-class", help="validate a proposed new document class folder (offline)")
    p.add_argument("folder", type=Path, help="a folder with fields.yaml and prompts/ (see new_doc_types/)")
    p.set_defaults(operate=cmd_check_class)

    p = sub.add_parser("ping", help="one tiny paid request per approved model (about $0.001)")
    common(p)
    p.add_argument("--initials", required=True)
    p.add_argument("--cap-usd", type=float, default=0.05)
    p.add_argument("--per-provider", action="store_true",
                   help="also ping each pinned provider alone and log its reasoning tokens")
    p.add_argument("--real-doc", action="store_true",
                   help="ping with one real intent-calibration document instead of the tiny prompt")
    p.add_argument("--chunk", action="append", dest="chunks", default=None,
                   help="ping only the routes these chunks use (repeatable; default: every chunk)")
    p.add_argument("--no-checkpoint", action="store_true",
                   help="do not stage ping.jsonl to the private HF repo (other machines will not see it)")
    p.set_defaults(operate=cmd_ping)

