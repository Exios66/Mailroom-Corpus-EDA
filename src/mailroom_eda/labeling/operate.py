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
from typing import Any, Callable, Collection, Mapping, Sequence

import yaml

from . import checkpoint, derive, export, interim, providers, runner, spend, tasks, verify
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
    camp = campaign(name)
    entries = [c for c in camp["chunks"] if str(c["id"]) == str(chunk_id)] \
        or [c for c in camp.get("steps") or [] if str(c["id"]) == str(chunk_id)]
    if not entries:
        ids = sorted({str(c["id"]) for c in camp["chunks"] + (camp.get("steps") or [])},
                     key=lambda x: (len(x), x))
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


def load_interim(args: argparse.Namespace, rows: Sequence[Mapping[str, Any]]) -> "interim.Overlay | None":
    """The sandbox overlay for ``rows`` when ``args.sandbox`` holds one (else ``None``, one line)."""
    sandbox = getattr(args, "sandbox", None)
    pinned = (campaign(getattr(args, "campaign", "v10")).get("interim_overlay") or {}).get("manifest_sha256")
    overlay = interim.load(sandbox, rows, manifest_sha256=pinned)
    if overlay is None and sandbox is not None:
        print(f"interim cross-check skipped: no {interim.INTERIM_DIR} under {sandbox}", file=sys.stderr)
    return overlay


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


def load_second_opinions(name: str) -> list[dict[str, Any]]:
    """Calibration arms approved as a second opinion (they calibrate a model; they never pick
    the ``winner_of_<id>`` model later chunks use)."""
    path = approvals_path(name)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("second_opinions") or [])


def _write_approvals(name: str, approvals: Sequence[Mapping[str, Any]],
                     second: Sequence[Mapping[str, Any]]) -> Path:
    path = approvals_path(name)
    body: dict[str, Any] = {"approved": list(approvals)}
    if second:
        body["second_opinions"] = list(second)
    path.write_text("# Written by `labeling gate --approve`. Commit this file; the chunk's label output\n"
                    "# stays out of git (private HF staging repo only).\n"
                    + yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return path


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


def mixed_models(models: Sequence[str], model_map: Sequence[str]) -> tuple[list[str], dict[str, str]]:
    """``--provider mixed``: each ``campaign_model=replacement`` swaps one model of the chunk for
    another approved model; every model then runs on its own ``models.yaml`` provider (for
    example Qwen on Modal beside GLM on OpenRouter). Returns the models and ``{model: provider}``."""
    reg = models_registry()["models"]
    out = list(models)
    for item in model_map:
        old, _, new = item.partition("=")
        if old not in out:
            raise SystemExit(f"--model-map {item!r}: {old!r} is not a model of this chunk ({out})")
        if new not in reg:
            raise SystemExit(f"--model-map {item!r}: {new!r} is not an approved model in models.yaml")
        out[out.index(old)] = new
    if len(set(out)) != len(out):
        raise SystemExit(f"this chunk uses two models, but the map leaves {out}")
    prov = {m: str(reg[m]["provider"]) for m in out}
    if len(set(prov.values())) < 2:
        raise SystemExit(f"--provider mixed, but every model runs on one provider ({prov}); "
                         "run it with that --provider instead")
    return out, prov


def chunk_models(args: argparse.Namespace, models: Sequence[str]) -> tuple[list[str], str | dict[str, str]]:
    """The models a chunk sends and where: one provider, or ``{model: provider}`` when mixed."""
    if args.provider == "mixed":
        return mixed_models(models, args.model_map)
    if args.provider != "openrouter":
        return provider_models(models, args.provider, args.model_map), args.provider
    return list(models), args.provider


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
    entries = campaign(name)["chunks"]
    # A chunk every entry of which is skipped (e.g. superseded) is never offered as next.
    skipped = {str(c["id"]) for c in entries
               if all(e.get("skip") for e in entries if str(e["id"]) == str(c["id"]))}
    for c in entries:
        cid = str(c["id"])
        if cid in done or cid in skipped or not all(str(n) in done for n in c.get("needs") or []):
            continue
        if c.get("requires") and not requires_met(c["requires"])[0]:
            if cid not in held:
                held.append(cid)
            continue
        if not lines:
            lines.append(f"next chunk: {cid} ({c['task']} / {c['set']}); `labeling chunk {cid} --dry-run` shows it")
    if held:
        lines.append(f"waiting on an external release (requires:): {', '.join(held)}")
    for st in campaign(name).get("steps") or []:
        sid = str(st["id"])
        if not lines and sid not in done and all(str(n) in done for n in st.get("needs") or []):
            hint = {"adjudication": " (`labeling adjudicate` lists what is open)",
                    "build": " (build first: `python -m mailroom_eda.v10_build`)"}.get(st["task"], "")
            lines.append(f"next step: {sid} ({st['task']}); `labeling gate {sid}` scores it{hint}")
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


def stale_winner(name: str, entry: Mapping[str, Any], picked: Sequence[Mapping[str, Any]]) -> str:
    """Why a ``winner_of_<id>`` chunk may not run, or ``""``: the approval it uses must have
    been scored on the prompt this chunk would send (a prompt edit needs a new calibration)."""
    refs = {str(m) for m in [*entry["models"], entry["reasoning"]] if str(m).startswith("winner_of_")}
    if not refs or not picked:
        return ""
    variant = "a" if entry["task"] == "intent" else ""
    want = tasks.build(entry["task"], picked[0], variant, set_name=entry["set"]).prompt_sha256
    approvals = load_approvals(name)
    for ref in sorted(refs):
        cid = ref.removeprefix("winner_of_")
        hits = [a for a in approvals if str(a["chunk"]) == cid]
        if hits and want not in (hits[-1].get("prompt_sha256") or []):
            return (f"chunk {entry['id']} would send prompt {want[:12]}, but chunk {cid} was approved on "
                    f"{[s[:12] for s in hits[-1].get('prompt_sha256') or []]}: re-run chunk {cid} on the "
                    f"current prompt, gate it and re-approve with --replace")
    return ""


def cmd_chunk(args: argparse.Namespace) -> int:
    """Run one campaign chunk arm with the campaign's models, reasoning, cap and wall limit."""
    entry, arm = chunk_entry(args.campaign, args.id, args.arm)
    if entry.get("skip"):
        raise SystemExit(f"chunk {entry['id']} arm {arm} is skipped: {entry['skip']}")
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
    models, provider = chunk_models(args, models)
    prov = provider if isinstance(provider, dict) else {m: provider for m in models}
    routed = [m for m in models if prov[m] == "openrouter"]
    if routed and not args.dry_run:
        log = args.out / "ping.jsonl"
        probs = ping_problems(export.read_journal(log) if log.exists() else [],
                              [(m, reasoning) for m in routed])
        if probs:
            raise SystemExit("route not verified; run `labeling ping --initials XX` first:\n  "
                             + "\n  ".join(probs))
    if not args.dry_run and not getattr(args, "no_checkpoint", False):
        ok, why = checkpoint.write_access()
        if not ok:
            raise SystemExit(f"checkpoint not writable ({why}); paid answers must survive a lost checkout. "
                             "Fix the token, or pass --no-checkpoint")
    if "modal" in prov.values() and not args.dry_run:
        ok, why = modal_check()
        if not ok:
            raise SystemExit(f"Modal endpoint not ready: {why}")
    rows = runner.load_stage(args.stage)
    picked = runner.chunk(runner.select(rows, entry["task"], entry["set"]), entry.get("chunk"))
    stale = stale_winner(args.campaign, entry, picked)
    if stale:
        raise SystemExit(stale)
    camp = campaign(args.campaign)
    rc = runner.execute(
        rows, picked, task=entry["task"], set_name=entry["set"], chunk_spec=entry.get("chunk"), models=models,
        provider=provider, reasoning=reasoning, initials=args.initials,
        cap_usd=float(args.cap_usd or entry["cap_usd"]),
        max_wall_seconds=float(entry.get("max_wall_seconds") or camp["max_wall_seconds"]),
        out_dir=chunk_dir(args.out, entry, arm), dry_run=args.dry_run, workers=args.workers,
        chunk_id=f"{entry['id']}{f'.{arm}' if arm else ''}",
        require_checkpoint=not getattr(args, "no_checkpoint", False),
        **({"ack_requires": why} if not met and args.ack_requires and not args.dry_run else {}))
    return rc if args.dry_run else auto_checkpoint(args, f"chunk {entry['id']}{f'.{arm}' if arm else ''} run", rc)


# ---------------------------------------------------------------- sweep

def sweep_plan(name: str, approvals: Sequence[Mapping[str, Any]], through: str,
               start: str | None = None, tasks: Collection[str] | None = None
               ) -> tuple[list[tuple[dict[str, Any], int | None]], str]:
    """The chunk arms a sweep runs, in ``campaign.yaml`` order, and why it stops there.

    From ``start`` (default: the first chunk) through ``through``: approved chunks are
    skipped, as are arms marked ``skip:``; the first chunk whose ``needs`` are not all approved, or whose ``requires``
    is not met, ends the plan. Every gate needs a human (a bake-off pick, verdicts, or a
    ``--signed-off`` read), so the plan runs a pilot or bake-off and then ends at the
    first chunk that depends on it. Nothing is ever approved here.

    ``tasks`` keeps only those tasks' chunks; a chunk that cannot run then blocks only the
    later chunks of its own task, so one document type waiting on review does not stop another.
    """
    entries = campaign(name)["chunks"]
    ids = [str(c["id"]) for c in entries]
    if through not in ids:
        raise SystemExit(f"--through {through}: no such chunk in campaign {name}")
    if start is not None and start not in ids:
        raise SystemExit(f"--from {start}: no such chunk in campaign {name}")
    first = ids.index(start) if start else 0
    last = len(ids) - 1 - ids[::-1].index(through)
    if first > last:
        raise SystemExit(f"--from {start} comes after --through {through} in campaign.yaml")
    if tasks and not any(c["task"] in tasks for c in entries):
        raise SystemExit(f"--task {', '.join(sorted(tasks))}: no chunk of task in campaign {name}")
    done = {str(a["chunk"]) for a in approvals}
    blocked: dict[str, str] = {}
    counts = Counter(ids)
    plan: list[tuple[dict[str, Any], int | None]] = []
    seen: Counter = Counter()
    for i, c in enumerate(entries):
        cid = ids[i]
        seen[cid] += 1
        if i < first or i > last or cid in done or c.get("skip"):
            continue  # a ``skip:`` arm keeps its number but never runs (``cmd_chunk`` refuses it)
        if tasks and (c["task"] not in tasks or c["task"] in blocked):
            continue
        missing = [str(n) for n in c.get("needs") or [] if str(n) not in done]
        met, why = requires_met(c.get("requires"))
        stop = (f"stopped at {cid}: needs chunk {', '.join(missing)} approved first" if missing
                else None if met else f"stopped at {cid}: {why}")
        if stop and not tasks:
            return plan, stop
        if stop:
            blocked[c["task"]] = stop
            continue
        plan.append((c, seen[cid] if counts[cid] > 1 else None))
    return plan, "; ".join(blocked.values()) or f"reached {through}"


def _sweep_projection(args: argparse.Namespace, plan: Sequence[tuple[Mapping[str, Any], int | None]],
                      rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One projection line per planned arm (no network)."""
    out = []
    for entry, arm in plan:
        models, reasoning = resolve(args.campaign, entry)
        models, _ = chunk_models(args, models)
        picked = runner.chunk(runner.select(rows, entry["task"], entry["set"]), entry.get("chunk"))
        proj = runner.estimate(picked, entry["task"], models, entry["set"])
        out.append({"chunk": f"{entry['id']}{f'.{arm}' if arm else ''}", "task": entry["task"],
                    "set": entry["set"], "documents": proj["documents"], "calls": proj["calls"],
                    "projected_usd": round(sum(proj["cost_usd"].values()), 4),
                    "cap_usd": float(entry["cap_usd"]), "reasoning": reasoning, "models": models})
    return out


def cmd_sweep(args: argparse.Namespace, *, confirm: Callable[[str], str] = input,
              run_chunk: Callable[[argparse.Namespace], int] | None = None) -> int:
    """Run every runnable chunk through ``--through`` after one combined dry run and one ``yes``.

    Each arm goes through ``cmd_chunk`` (its own cap, wall limit, ping and checkpoint
    checks). The sweep stops at the first chunk that needs an unapproved one (so after a
    pilot or bake-off, whose gate a human decides), an unmet ``requires``, or the first
    non-zero exit (2 cap/wall, 3 failed calls, 4
    checkpoint). It never approves: ``labeling gate --approve`` stays a human step.
    """
    run_chunk = run_chunk or cmd_chunk
    auto_restore(args)
    approvals = load_approvals(args.campaign)
    plan, why = sweep_plan(args.campaign, approvals, args.through, args.start,
                           set(getattr(args, "task", None) or ()) or None)
    if not plan:
        print(f"nothing to run: {why}")
        return 0
    lines = _sweep_projection(args, plan, runner.load_stage(args.stage))
    width = max(len(ln["chunk"]) for ln in lines)
    for ln in lines:
        print(f"{ln['chunk']:<{width}}  {ln['task']:<20} {ln['set']:<12} docs={ln['documents']:<5} "
              f"calls={ln['calls']:<5} projected=${ln['projected_usd']:.2f} cap=${ln['cap_usd']:.2f} "
              f"reasoning={ln['reasoning']}")
    proj = sum(ln["projected_usd"] for ln in lines)
    caps = sum(ln["cap_usd"] for ln in lines)
    print(f"{len(lines)} arms: projected ${proj:.2f}, caps ${caps:.2f}; {why}")
    if args.provider in ("openrouter", "mixed") and os.environ.get("OPENROUTER_API_KEY"):
        rem = providers.key_status(providers.Backend.from_env("openrouter")).get("limit_remaining")
        reserve = models_registry().get("key_reserve_usd", 3.0)
        usable = None if rem is None else float(rem) - reserve
        print("funding: no credit limit" if usable is None else f"funding: ${usable:.2f} usable after reserve")
        if usable is not None and usable < caps:
            # run what the credit likely covers (projected cost), in order; each chunk is capped
            # at the live credit left, checkpoints, and resumes from its journal after a top-up
            keep, total = 0, 0.0
            while keep < len(lines) and total + lines[keep]["projected_usd"] <= usable + 1e-9:
                total += lines[keep]["projected_usd"]
                keep += 1
            if not keep:
                raise SystemExit(f"the key's usable credit (${usable:.2f}) covers no chunk: "
                                 f"{lines[0]['chunk']} projects ${lines[0]['projected_usd']:.2f}")
            print(f"deferred until more credit: {', '.join(ln['chunk'] for ln in lines[keep:])}")
            plan, lines, caps = plan[:keep], lines[:keep], sum(ln["cap_usd"] for ln in lines[:keep])
            print(f"running {keep} arms: projected ${total:.2f} of ${usable:.2f} usable; "
                  f"each is capped at the credit left when it starts")
    elif args.provider in ("openrouter", "mixed"):
        print("funding: not checked (OPENROUTER_API_KEY is not set)")
    if args.dry_run:
        return 0
    if not args.initials or not spend.INITIALS_RE.match(args.initials):
        raise SystemExit("a sweep needs --initials (a human's 2-6 capital letters)")
    if confirm(f"run these {len(lines)} arms (caps ${caps:.2f})? type yes: ").strip() != "yes":
        print("not confirmed; nothing ran")
        return 1
    for entry, arm in plan:
        label = f"{entry['id']}{f'.{arm}' if arm else ''}"
        print(f"=== sweep: chunk {label}", file=sys.stderr)
        cap = None
        if args.provider in ("openrouter", "mixed") and os.environ.get("OPENROUTER_API_KEY"):
            rem = providers.key_status(providers.Backend.from_env("openrouter")).get("limit_remaining")
            left = None if rem is None else float(rem) - models_registry().get("key_reserve_usd", 3.0)
            if left is not None and left < 0.01:
                print(f"sweep stopped before chunk {label}: no credit left above the reserve")
                return 0
            if left is not None and left < float(entry["cap_usd"]):
                cap = round(left, 2)  # never past the reserve; a stop here resumes after a top-up
        ns = argparse.Namespace(**{**vars(args), "id": str(entry["id"]), "arm": arm, "dry_run": False,
                                   "ack_requires": False, "cap_usd": cap, "no_restore": True})
        rc = run_chunk(ns)
        if rc:
            print(f"sweep stopped: chunk {label} exited {rc}")
            return rc
    print(f"sweep done: {why}. Score and approve each chunk with `labeling gate <id> --approve`.")
    return 0


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


#: Expert gold the user overruled after reading the email (``<out>/gold-corrections.csv``). It
#: lives beside the labels (gitignored, private HF backup) because it is ground truth; scoring
#: applies it, and the ingest writes it to the GT config with its provenance, never to default.
GOLD_CORRECTIONS = "gold-corrections.csv"
GOLD_CORRECTION_COLUMNS = ("document_id", "filename", "field", "gold_was", "gold_now", "reason",
                           "decided_by", "decided_utc")


def gold_corrections(out: Path) -> dict[tuple[str, str], dict[str, str]]:
    """``{(filename, field): correction}`` from ``<out>/gold-corrections.csv`` (empty if none)."""
    path = out / GOLD_CORRECTIONS
    if not path.exists():
        return {}
    found: dict[tuple[str, str], dict[str, str]] = {}
    for c in export.read_csv(path):
        if not (c.get("decided_by") and c.get("gold_now")):
            raise SystemExit(f"{path}: every correction needs decided_by and gold_now ({c.get('filename')})")
        found[(c["filename"], c["field"])] = c
    return found


def corrected_gold(rows: Sequence[Mapping[str, Any]],
                   corrections: Mapping[tuple[str, str], Mapping[str, str]]) -> tuple[list[dict[str, Any]], int]:
    """Rows with corrected ``gt_fields`` values, and how many changed. A correction whose
    ``gold_was`` no longer matches the row is refused: the gold moved under it."""
    out, n = [], 0
    for row in rows:
        fixes = {f: c for (fn, f), c in corrections.items() if fn == row["filename"]}
        if not fixes:
            out.append(dict(row))
            continue
        gt = dict(row["gt_fields"])
        for f, c in fixes.items():
            if str(gt.get(f) or "") != c["gold_was"]:
                raise SystemExit(f"gold correction for {row['filename']} {f}: expected "
                                 f"{c['gold_was']!r}, the row has {gt.get(f)!r}")
            gt[f] = c["gold_now"]
            n += 1
        out.append({**row, "gt_fields": gt})
    return out, n


#: The intent a correspondence subtype (``expected_subclass``) points to. The subtypes came from
#: older automatic labelers, so a conflict is a reason to read the email, not proof either side is
#: right: it goes to adjudication, never written automatically. ``demand`` and ``attorney_demand``
#: are left out (user, 2026-11-06): a demand is often for an action, not a payment. Subtypes not
#: listed (email, memo, letter, demand) admit any intent.
SUBTYPE_INTENT = {"press_release": "press_communication", "meeting_request": "meeting_invite",
                  "notice": "notice"}
RELABEL_COLUMNS = ("document_id", "filename", "split", "subtype", "subtype_intent", "existing",
                   "existing_source", "primary_a", "primary_b", "second_a", "second_b", "decision",
                   "new_intent", "reason", "evidence", "reviewer_decision", "reviewed_by", "reviewer_note")


def relabel_decision(existing: str, subtype: str | None, answers: Sequence[str]) -> tuple[str, str, str]:
    """(decision, new intent, reason) for one email from its four answers (two models, two
    passes). Only a unanimous answer that fits the subtype is written; a subtype conflict is
    read by a person (who may find the subtype wrong); ``keep`` leaves the existing label when
    the answers split but one of them is it."""
    implied = SUBTYPE_INTENT.get(subtype or "")
    if len(answers) == 4 and len(set(answers)) == 1:
        label = answers[0]
        if implied and label != implied:
            return "adjudicate", "", f"all four say {label}; subtype {subtype} implies {implied}"
        return ("confirm" if label == existing else "change"), label, "unanimous"
    if implied and existing != implied:
        return "adjudicate", "", f"answers split; existing {existing} contradicts subtype {subtype}"
    if existing not in answers:
        return "adjudicate", "", "answers split and none keeps the existing label"
    return "keep", existing, "answers split; existing label among them"


def gate_intent_relabel(picked: Sequence[Mapping[str, Any]], out_dir: Path, models: Sequence[str],
                        signed_off: bool) -> dict[str, Any]:
    """Decide every email of a relabel chunk into ``decisions.csv`` (reviewer columns kept
    across re-gates). Passes when both models answered both passes on every email, every
    adjudicated email has a reviewer decision (an intent or ``keep``), and it is signed off."""
    from ..intent_backfill import CANONICAL_INTENTS  # noqa: PLC0415

    recs = {(r["document_id"], r["model"]): r for r in export.read_journal(out_dir / "intent-labels.jsonl")}
    path = out_dir / "decisions.csv"
    kept = {r["document_id"]: r for r in export.read_csv(path)} if path.exists() else {}
    rows, missing = [], []
    for row in picked:
        doc_id, fn = tasks._ids(row)
        got = [recs.get((doc_id, m)) for m in models]
        if len(models) != 2 or not all(got):
            missing.append(doc_id)
            continue
        answers = [str(g[p]["intent"] or "") for g in got for p in ("pass_a", "pass_b")]
        gt = row["gt_fields"]
        existing = str(gt.get("intent") or "")
        decision, new, reason = relabel_decision(existing, row.get("expected_subclass"), answers)
        if row.get("split") == "test" and decision == "change":
            # the test split scores models later: labels written by these two models would
            # favor models that read email like them, so a person decides every test change
            decision, new, reason = "adjudicate", "", f"test split: models propose {new}; a person decides"
        prev = kept.get(doc_id, {})
        rows.append({"document_id": doc_id, "filename": fn, "split": row.get("split", ""),
                     "subtype": row.get("expected_subclass") or "",
                     "subtype_intent": SUBTYPE_INTENT.get(row.get("expected_subclass") or "", ""),
                     "existing": existing, "existing_source": str(gt.get("intent_source") or ""),
                     "primary_a": answers[0], "primary_b": answers[1], "second_a": answers[2],
                     "second_b": answers[3], "decision": decision, "new_intent": new, "reason": reason,
                     "evidence": str(got[0]["pass_a"].get("evidence") or ""),
                     "reviewer_decision": prev.get("reviewer_decision", ""),
                     "reviewed_by": prev.get("reviewed_by", ""), "reviewer_note": prev.get("reviewer_note", "")})
    export.write_csv(path, RELABEL_COLUMNS, rows)
    allowed = set(CANONICAL_INTENTS) | {"keep"}
    open_items = [r["document_id"] for r in rows
                  if r["decision"] == "adjudicate" and r["reviewer_decision"] not in allowed]
    bad = [r["document_id"] for r in rows if r["reviewer_decision"] and r["reviewer_decision"] not in allowed]
    # a test-split adjudication is decided by a person (initials), never by a model
    unsigned_test = [r["document_id"] for r in rows if r["split"] == "test" and r["decision"] == "adjudicate"
                     and not spend.INITIALS_RE.match(r["reviewed_by"] or "")]
    mix = Counter(r["decision"] for r in rows)
    ok = not missing and not open_items and not bad and not unsigned_test and signed_off
    return {"kind": "intent_relabel", "documents": len(rows), "expected_documents": len(picked),
            "decisions": dict(mix), "unanswered_documents": len(missing), "open_adjudications": len(open_items),
            "invalid_reviewer_decisions": bad, "test_adjudications_without_initials": len(unsigned_test),
            "decisions_csv": str(path), "signed_off": signed_off,
            "passed": ok, "how": "decide each adjudicate row in decisions.csv (an intent or keep; test-split "
                                 "rows need a person's initials in reviewed_by), read the changes, "
                                 "then re-run with --signed-off"}


def gate_result(name: str, entry: Mapping[str, Any], arm: int | None, out: Path, stage: Path,
                signed_off: bool = False, sandbox: Path | None = None) -> dict[str, Any]:
    """Score one chunk arm's gate from its output directory (offline)."""
    from .. import v9_4_revision as v94  # noqa: PLC0415

    if entry["gate"]["kind"] == "resolved":
        return gate_resolved(name, entry, out, stage, sandbox=sandbox)
    if entry["gate"]["kind"] == "conformance":
        return gate_conformance(name, entry, out, stage)
    out_dir = chunk_dir(out, entry, arm)
    if not (out_dir / "calls.jsonl").exists():
        raise SystemExit(f"{out_dir} has no calls.jsonl; run `labeling chunk {entry['id']}` first")
    rows = runner.load_stage(stage)
    picked = runner.chunk(runner.select(rows, entry["task"], entry["set"]), entry.get("chunk"))
    models, reasoning = resolve(name, entry)
    provider, providers_of = "openrouter", None
    started = [r for r in export.read_journal(out_dir / "runs.jsonl") if r.get("started_utc") and r.get("models")]
    if started:  # score the models the chunk actually ran (e.g. the mapped Modal model)
        models, provider = list(started[-1]["models"]), str(started[-1].get("provider") or provider)
        providers_of = started[-1].get("providers") or None
    overlay = load_interim(argparse.Namespace(sandbox=sandbox, campaign=name), rows)
    reviewed = runner.review(rows, entry["task"], out_dir, reasoning=reasoning, interim=overlay)
    gate = dict(entry["gate"])
    kind = gate.pop("kind")
    if kind == "cuad_f1":
        res = v94.score_cuad_calibration(picked, export.read_journal(out_dir / "cuad-labels.jsonl"),
                                         runner.cuad_vocab(rows))
        ok = (res["documents"] == res["expected_documents"] and res["micro_f1"] >= gate["micro_f1"]
              and res["micro_precision"] >= gate["precision"])
        res = {"kind": kind, **{k: v for k, v in res.items() if k != "per_category"}, "floors": gate, "passed": ok}
        res["per_document"] = cuad_per_document(picked, export.read_journal(out_dir / "cuad-labels.jsonl"),
                                                runner.cuad_vocab(rows))
    elif kind == "intent_kappa":
        picked, fixed = corrected_gold(picked, gold_corrections(out))
        res = v94.score_intent_calibration(picked, export.read_journal(out_dir / "intent-labels.jsonl"))
        ok = res["documents"] == res["expected_documents"] and res["cohen_kappa"] >= gate["kappa"]
        res = {"kind": kind, **res, "gold_corrections": fixed, "floors": gate, "passed": ok}
        res["per_document"] = intent_per_document(picked, export.read_journal(out_dir / "intent-labels.jsonl"))
    elif kind == "intent_relabel":
        res = gate_intent_relabel(picked, out_dir, models, signed_off)
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
    # what was scored: answers to today's prompt. Old-prompt answers stay in the journal (and
    # in spent_usd) but must not vouch for this prompt in an approval, nor price the arm.
    current = runner.current_prompt_calls(rows, entry["task"], calls, entry["set"])
    res.update(chunk=entry["id"], arm=arm, task=entry["task"], models=models, provider=provider,
               **({"providers": providers_of} if providers_of else {}), reasoning=reasoning,
               prompt_sha256=sorted({c["prompt_sha256"] for c in current}),
               served_providers=sorted({c.get("served_provider") or "" for c in current} - {""}),
               spent_usd=round(sum(c.get("cost_usd") or 0 for c in every), 4),
               answer_cost_usd=round(sum(c.get("reused_cost_usd") or c.get("cost_usd") or 0 for c in current), 4),
               scored_utc=_now())
    if overlay is not None:  # informational: never part of passed
        res["interim"] = {**reviewed.get("interim", {}), "manifest_sha256": overlay.manifest_sha256}
    outside = pin_violations(current, reasoning)
    if outside:
        res.update(passed=False, served_outside_pin=outside)
    unreasoned = unreasoned_providers(current, reasoning)
    if unreasoned:
        res.update(passed=False, reasoning_not_honoured=unreasoned)
    (out_dir / "gate.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    return res


def cuad_per_document(gold_rows: Sequence[Mapping[str, Any]], journal: Sequence[Mapping[str, Any]],
                      vocab: Sequence[str]) -> list[dict[str, Any]]:
    """Per-document category tp/fp/fn, scored as ``v9_4_revision.score_cuad_calibration`` does."""
    from .. import v9_4_revision as v94  # noqa: PLC0415

    folded = v94.fold_journal(journal)
    out = []
    for row in gold_rows:
        rec = folded.get(row["filename"])
        if rec is None:
            continue
        gold = v94._json_obj(row["gt_fields"].get("cuad_clause_labels"))
        raw = v94._json_obj(rec.get("cuad_clause_labels")) if rec.get("accepted") else {}
        pred, _, _ = v94.verify_cuad_labels(row["doc_text"], raw, vocab)
        c = Counter("tp" if gold.get(n) and pred.get(n) else "fp" if pred.get(n) else "fn" if gold.get(n) else "tn"
                    for n in vocab)
        out.append({"document_id": tasks._ids(row)[0], "tp": c["tp"], "fp": c["fp"], "fn": c["fn"]})
    return out


def intent_per_document(manual_rows: Sequence[Mapping[str, Any]],
                        journal: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per-document gold and pass-A intent, as ``score_intent_calibration`` pairs them."""
    by_fn = {str(r["filename"]): r for r in journal}
    return [{"document_id": tasks._ids(row)[0], "gold": str(row["gt_fields"].get("intent") or ""),
             "pred": str((by_fn[row["filename"]].get("pass_a") or {}).get("intent") or "")}
            for row in manual_rows if row["filename"] in by_fn]


# ---------------------------------------------------------------- bake-off

#: 40 contracts and 96 emails cannot resolve 0.02 gaps (user, 2026-11-06): arms are compared by
#: paired bootstrap intervals over shared documents, never by a raw score gap.
BOOTSTRAP_N, BOOTSTRAP_SEED, NON_INFERIORITY_MARGIN = 2000, 42, 0.05


def _metric(docs: Sequence[Mapping[str, Any]], metric: str) -> float:
    from .. import v9_4_revision as v94  # noqa: PLC0415

    if metric == "micro_f1":
        tp, fp, fn = (sum(d[k] for d in docs) for k in ("tp", "fp", "fn"))
        return 2 * tp / (2 * tp + fp + fn) if tp else 0.0
    if metric == "cohen_kappa":
        return v94.cohen_kappa([d["gold"] for d in docs], [d["pred"] for d in docs])
    raise ValueError(metric)


def paired_bootstrap(a: Sequence[Mapping[str, Any]], b: Sequence[Mapping[str, Any]], metric: str,
                     n: int = BOOTSTRAP_N, seed: int = BOOTSTRAP_SEED) -> tuple[float, float, float]:
    """(point, lo95, hi95) of metric(b) - metric(a), resampling the shared documents."""
    import random  # noqa: PLC0415

    da, db = {d["document_id"]: d for d in a}, {d["document_id"]: d for d in b}
    ids = sorted(set(da) & set(db))
    if not ids:
        return 0.0, 0.0, 0.0
    point = _metric([db[i] for i in ids], metric) - _metric([da[i] for i in ids], metric)
    rng = random.Random(seed)
    diffs = []
    for _ in range(n):
        pick = [rng.choice(ids) for _ in ids]
        diffs.append(_metric([db[i] for i in pick], metric) - _metric([da[i] for i in pick], metric))
    diffs.sort()
    return round(point, 4), round(diffs[int(0.025 * n)], 4), round(diffs[int(0.975 * n) - 1], 4)


def _arm_model(arm: Mapping[str, Any]) -> str:
    return str(arm["gate"]["models"][0])


def _arm_cost(arm: Mapping[str, Any]) -> float:
    """What the arm's answers cost (reused answers count at their original price)."""
    g = arm["gate"]
    return float(g.get("answer_cost_usd") or g.get("spent_usd") or 0.0)


def _ineligible(arm: Mapping[str, Any]) -> str:
    g = arm["gate"]
    if g.get("skipped"):
        return f"skipped: {g['skipped']}"
    if g.get("documents") != g.get("expected_documents"):
        return f"incomplete ({g.get('documents')}/{g.get('expected_documents')})"
    if g.get("served_outside_pin"):
        return f"served outside pin: {g['served_outside_pin']}"
    if g.get("reasoning_not_honoured"):
        return f"reasoning not honoured: {g['reasoning_not_honoured']}"
    if not g.get("passed"):
        return "floor not passed"
    return ""


def bakeoff_ranking(arms: Sequence[Mapping[str, Any]], metric: str,
                    margin: float = NON_INFERIORITY_MARGIN) -> dict[str, Any]:
    """Pick the primary DeepSeek arm and an equally capable second opinion of another vendor.

    Cheapest first, upgraded only on evidence: the primary is the cheapest eligible DeepSeek
    arm, replaced by a costlier one only when its lo95 against the current pick is > 0. The
    second is the cheapest eligible arm of another vendor whose lo95 against the primary is
    >= -margin. An arm whose hi95 against the primary is < -margin is clearly worse.
    """
    eligible = [a for a in arms if not _ineligible(a)]
    primary = None
    for a in sorted((a for a in eligible if _arm_model(a).startswith("deepseek/")), key=_arm_cost):
        if primary is None or paired_bootstrap(primary["docs"], a["docs"], metric)[1] > 0:
            primary = a
    table, second = [], None
    for a in sorted(arms, key=_arm_cost):
        why = _ineligible(a)
        point, lo, hi = paired_bootstrap(primary["docs"], a["docs"], metric) if primary else (0.0, 0.0, 0.0)
        row = {"model": _arm_model(a), "reasoning": a["gate"].get("reasoning"), "arm": a["gate"].get("arm"),
               "score": round(_metric(a["docs"], metric), 4), "vs_primary": point, "lo95": lo, "hi95": hi,
               "cost_usd": round(_arm_cost(a), 4), "eligible": why or "yes",
               "excluded": "clearly worse" if primary and a is not primary and hi < -margin else "",
               "primary": a is primary, "second": False}
        if (primary and not why and second is None and not _arm_model(a).startswith("deepseek/")
                and lo >= -margin):
            second, row["second"] = a, True
        table.append(row)
    return {"metric": metric, "margin": margin, "primary": primary, "second": second, "table": table}


def bakeoff_arms(name: str, chunk_id: str, out: Path) -> tuple[list[dict[str, Any]], str]:
    """Each arm of a bake-off chunk as ``{"gate", "docs"}``; chunk 1 pools the matching
    chunk-1h holdout arm (same model and reasoning) when it was run."""
    entries = [c for c in campaign(name)["chunks"] if str(c["id"]) == str(chunk_id)]
    if not entries:
        raise SystemExit(f"no chunk {chunk_id} in campaign {name}")
    metric = {"cuad": "micro_f1", "intent": "cohen_kappa"}.get(entries[0]["task"])
    if metric is None:
        raise SystemExit(f"chunk {chunk_id} ({entries[0]['task']}) is not a bake-off")
    holdout = {(tuple(c["models"]), str(c["reasoning"])): (c, i)
               for i, c in enumerate((c for c in campaign(name)["chunks"] if str(c["id"]) == f"{chunk_id}h"), 1)}
    arms = []
    for i, entry in enumerate(entries, 1):
        path = chunk_dir(out, entry, i) / "gate.json"
        if entry.get("skip"):
            arms.append({"gate": {"models": entry["models"], "reasoning": entry["reasoning"], "arm": i,
                                  "skipped": entry["skip"], "passed": False}, "docs": []})
            continue
        if not path.exists():
            arms.append({"gate": {"models": entry["models"], "reasoning": entry["reasoning"], "arm": i,
                                  "documents": 0, "expected_documents": None, "passed": False}, "docs": []})
            continue
        gate = json.loads(path.read_text(encoding="utf-8"))
        arm = {"gate": gate, "docs": list(gate.get("per_document") or [])}
        h = holdout.get((tuple(entry["models"]), str(entry["reasoning"])))
        hpath = chunk_dir(out, h[0], h[1]) / "gate.json" if h else None
        if hpath and hpath.exists():
            hg = json.loads(hpath.read_text(encoding="utf-8"))
            arm = {"gate": {**gate, "documents": gate["documents"] + hg["documents"],
                            "expected_documents": gate["expected_documents"] + hg["expected_documents"],
                            "passed": gate["passed"] and hg["passed"],
                            "served_outside_pin": (gate.get("served_outside_pin") or []) + (hg.get("served_outside_pin") or []),
                            "reasoning_not_honoured": {**(gate.get("reasoning_not_honoured") or {}),
                                                       **(hg.get("reasoning_not_honoured") or {})},
                            "answer_cost_usd": _arm_cost({"gate": gate}) + _arm_cost({"gate": hg}),
                            "pooled_holdout": True},
                   "docs": arm["docs"] + list(hg.get("per_document") or [])}
        arms.append(arm)
    return arms, metric


def cmd_bakeoff(args: argparse.Namespace) -> int:
    """Rank a bake-off chunk's arms; write ``<out>/<chunk>.bakeoff.json``."""
    arms, metric = bakeoff_arms(args.campaign, args.id, args.out)
    rank = bakeoff_ranking(arms, metric, getattr(args, "margin", NON_INFERIORITY_MARGIN))
    print(f"{'model':40} {'reasoning':9} {'score':>6} {'vs prim':>8} {'lo95':>7} {'hi95':>7} {'cost':>7}  status")
    for r in rank["table"]:
        tag = "PRIMARY" if r["primary"] else "SECOND" if r["second"] else r["excluded"] or (
            "" if r["eligible"] == "yes" else r["eligible"])
        print(f"{r['model']:40} {str(r['reasoning']):9} {r['score']:6.3f} {r['vs_primary']:+8.3f} "
              f"{r['lo95']:+7.3f} {r['hi95']:+7.3f} {r['cost_usd']:7.3f}  {tag}")
    pick = lambda a: a and {"model": _arm_model(a), "reasoning": a["gate"].get("reasoning"),  # noqa: E731
                            "arm": a["gate"].get("arm")}
    saved = {"chunk": str(args.id), "metric": metric, "margin": rank["margin"], "primary": pick(rank["primary"]),
             "second": pick(rank["second"]), "table": rank["table"], "ranked_utc": _now()}
    path = Path(args.out) / f"{args.id}.bakeoff.json"
    path.write_text(json.dumps(saved, indent=1) + "\n", encoding="utf-8")
    print(path)
    if rank["primary"] is None or rank["second"] is None:
        print("no " + ("primary" if rank["primary"] is None else "second opinion")
              + " qualifies: STOP and re-plan", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------- inspect

INSPECT_CONTEXT = 200


def _quote_context(text: str, quote: str) -> str:
    """The quote's verified location with INSPECT_CONTEXT characters each side, or NOT FOUND."""
    if not quote:
        return "(no quote)"
    span = verify.locate(text, quote)
    if span is None:
        return f"NOT FOUND: `{quote[:160]}`"
    a, b = span
    ctx = text[max(0, a - INSPECT_CONTEXT):a] + "**" + text[a:b] + "**" + text[b:b + INSPECT_CONTEXT]
    return "> " + " ".join(ctx.split())


def _span_text(span: Any) -> str:
    """A CUAD span's text: gold spans may be bare strings, model spans are ``{"text": ...}``."""
    return str(span.get("text") or "") if isinstance(span, Mapping) else str(span or "")


def _inspect_items(rows: Mapping[str, Mapping[str, Any]], records: Sequence[Mapping[str, Any]],
                   task: str) -> list[tuple[bool, str, list[str]]]:
    """(disagrees, document_id, markdown lines) per labeled document."""
    from .. import v9_4_revision as v94  # noqa: PLC0415

    items = []
    if task == "intent":
        for r in records:
            row = rows.get(str(r.get("document_id")))
            if row is None:
                continue
            gold = (row.get("gt_fields") or {}).get("intent") or ""
            passes = [(k, r.get(k) or {}) for k in ("pass_a", "pass_b") if r.get(k)]
            answers = {p.get("intent") for _, p in passes} | ({gold} if gold else set())
            lines = [f"- gold: `{gold}`" if gold else "- gold: (none)"]
            for k, p in passes:
                lines += [f"- {k}: `{p.get('intent')}` (confidence {p.get('confidence')})",
                          "  " + _quote_context(row["doc_text"], str(p.get("evidence") or ""))]
            items.append((len(answers) > 1, str(r["document_id"]), lines))
    elif task == "cuad":
        for r in records:
            row = rows.get(str(r.get("document_id")))
            if row is None:
                continue
            gold = v94._json_obj((row.get("gt_fields") or {}).get("cuad_clause_labels")) or {}
            pred = v94._json_obj(r.get("cuad_clause_labels")) or {}
            g, p = {k for k, v in gold.items() if v}, {k for k, v in pred.items() if v}
            lines = [f"- accepted: {r.get('accepted')}; gold categories {len(g)}, predicted {len(p)}"]
            for cat in sorted(p - g):
                spans = pred[cat]
                lines += [f"- **false positive** {cat}", "  " + _quote_context(row["doc_text"], _span_text(spans[0]))]
            for cat in sorted(g - p):
                lines += [f"- **missed** {cat}", "  gold: " + _quote_context(row["doc_text"], _span_text(gold[cat][0]))]
            for cat in sorted(g & p)[:3]:
                lines += [f"- agreed {cat}", "  " + _quote_context(row["doc_text"], _span_text(pred[cat][0]))]
            items.append((bool(p ^ g), str(r["document_id"]), lines))
    else:  # extraction tasks: fields.csv rows
        by_doc: dict[str, list[Mapping[str, Any]]] = {}
        for f in records:
            by_doc.setdefault(str(f["document_id"]), []).append(f)
        for doc, fields in by_doc.items():
            row = rows.get(doc)
            if row is None:
                continue
            lines, split = [], False
            for f in fields:
                va = (f.get("model_a_status"), f.get("model_a_value"))
                vb = (f.get("model_b_status"), f.get("model_b_value")) if f.get("model_b") else va
                split |= va != vb
                lines.append(f"- {f['field']}: {f.get('model_a')} `{va[1] or va[0]}`"
                             + (f" vs {f.get('model_b')} `{vb[1] or vb[0]}`" if f.get("model_b") else "")
                             + f" → {f.get('decision') or f.get('final_decision') or ''}")
                for slot in ("a", "b"):
                    if f.get(f"model_{slot}_evidence"):
                        lines.append("  " + _quote_context(row["doc_text"], str(f[f"model_{slot}_evidence"])))
            items.append((split, doc, lines))
    return items


def inspect_report(rows: Sequence[Mapping[str, Any]], records: Sequence[Mapping[str, Any]], task: str,
                   n: int = 8, calls: Sequence[Mapping[str, Any]] = ()) -> str:
    """Markdown for ``n`` documents (disagreements first, then a seeded sample) to read
    against the documents before any ``gate --approve``."""
    import random  # noqa: PLC0415

    by_id = {tasks._ids(r)[0]: r for r in rows}
    items = _inspect_items(by_id, records, task)
    rng = random.Random(42)
    rng.shuffle(items)
    items.sort(key=lambda it: not it[0])  # stable: disagreements first, each group shuffled
    served: dict[str, set[str]] = {}
    for c in calls:
        served.setdefault(str(c.get("document_id")), set()).add(str(c.get("served_provider") or "?"))
    out = [f"# inspect: {task}, {min(n, len(items))} of {len(items)} documents "
           f"({sum(it[0] for it in items)} disagree)", ""]
    for split, doc, lines in items[:n]:
        row = by_id[doc]
        out += [f"## {doc} {'(DISAGREES)' if split else ''}".rstrip(),
                f"`{row.get('filename')}` · split {row.get('split')} · served by "
                f"{', '.join(sorted(served.get(doc, set()))) or '?'}", "", *lines, ""]
    return "\n".join(out)


def cmd_inspect(args: argparse.Namespace) -> int:
    """Write ``<dir>/inspect.md`` for a chunk or run directory."""
    d = Path(args.dir)
    for task, name in (("intent", "intent-labels.jsonl"), ("cuad", "cuad-labels.jsonl")):
        if (d / name).exists():
            records = export.read_journal(d / name)
            break
    else:
        if not (d / "fields.csv").exists():
            raise SystemExit(f"{d} has no labels to inspect (run `labeling review` first)")
        task, records = "fields", export.read_csv(d / "fields.csv")
    calls = export.read_journal(d / "calls.jsonl") if (d / "calls.jsonl").exists() else []
    rows = runner.load_stage(args.stage)
    if task == "intent":  # the gold the gate scores against (corrections live in the labels root)
        rows, _ = corrected_gold(rows, gold_corrections(d.parent))
    md = inspect_report(rows, records, task, args.n, calls)
    (d / "inspect.md").write_text(md, encoding="utf-8")
    print(d / "inspect.md")
    return 0
# ---------------------------------------------------------------- adjudication (step 10)

ADJUDICATION_DIR = "adjudication"
OPEN_COLUMNS = ("chunk", *export.FIELD_COLUMNS)
#: chunk tasks whose output is cuad-labels/intent-labels, not fields.csv
NON_FIELD_TASKS = {"cuad", "intent"}


def _merge_open_verdicts(chunk_out: Path, label: str, open_rows: Mapping[tuple[str, str, str], Mapping[str, str]]
                         ) -> int:
    """Copy reviewer columns filled in ``adjudication/open.csv`` into the chunk's adjudication.csv."""
    path = chunk_out / "adjudication.csv"
    if not path.exists():
        return 0
    rows = export.read_csv(path)
    changed = 0
    for r in rows:
        o = open_rows.get((label, r["document_id"], r["field"]))
        if o and any(o.get(c) for c in runner.REVIEWER_COLUMNS) \
                and any(o.get(c, "") != r.get(c, "") for c in runner.REVIEWER_COLUMNS):
            r.update({c: o.get(c, "") for c in runner.REVIEWER_COLUMNS})
            changed += 1
    if changed:
        export.write_csv(path, export.FIELD_COLUMNS, rows)
    return changed


def adjudication_items(name: str, out: Path, stage: Path, *, only: str | None = None,
                       rows: Sequence[Mapping[str, Any]] | None = None,
                       sandbox: Path | None = None) -> tuple[list[dict[str, Any]], int]:
    """Every field of an approved chunk still at ``final_decision == adjudicate``.

    Verdicts already typed into ``adjudication/open.csv`` are first copied into each
    chunk's ``adjudication.csv``; then ``runner.review`` rebuilds the chunk (it keeps the
    reviewer columns), so a verdict entered in either place counts. Returns the open rows
    and how many verdicts were copied.
    """
    rows = runner.load_stage(stage) if rows is None else rows
    open_path = Path(out) / ADJUDICATION_DIR / "open.csv"
    typed = {(r["chunk"], r["document_id"], r["field"]): r
             for r in (export.read_csv(open_path) if open_path.exists() else [])}
    items: list[dict[str, Any]] = []
    copied = 0
    overlay = load_interim(argparse.Namespace(sandbox=sandbox, campaign=name), rows) if sandbox else None
    for a in load_approvals(name):
        cid = str(a["chunk"])
        if only and cid != str(only):
            continue
        entry, arm = chunk_entry(name, cid, a.get("arm"))
        if "models" not in entry or entry["task"] in NON_FIELD_TASKS:
            continue
        label = f"{cid}{f'.{arm}' if arm else ''}"
        d = chunk_dir(Path(out), entry, arm)
        if not (d / "calls.jsonl").exists():
            raise SystemExit(f"approved chunk {label}: {d} has no calls.jsonl; restore it first "
                             "(`labeling checkpoint --restore`)")
        copied += _merge_open_verdicts(d, label, typed)
        runner.review(rows, entry["task"], d, reasoning=a.get("reasoning"), interim=overlay)
        items += [{"chunk": label, **f} for f in export.read_csv(d / "fields.csv")
                  if f.get("final_decision") == "adjudicate"]
    return items, copied


def gate_resolved(name: str, entry: Mapping[str, Any], out: Path, stage: Path,
                  sandbox: Path | None = None) -> dict[str, Any]:
    """Step gate: every ``needs`` chunk is approved and none has an open adjudication item."""
    done = {str(a["chunk"]) for a in load_approvals(name)}
    unapproved = [str(n) for n in entry.get("needs") or [] if str(n) not in done]
    items, _ = adjudication_items(name, out, stage, sandbox=sandbox)
    per_chunk = dict(Counter(i["chunk"] for i in items))
    res = {"kind": "resolved", "open_items": len(items), "per_chunk": per_chunk,
           "per_field": dict(Counter(f"{i['chunk']}/{i['field']}" for i in items)),
           "unapproved_needs": unapproved, "passed": not items and not unapproved,
           "how": "fill reviewer_verdict in adjudication/open.csv, then `labeling adjudicate` and re-gate",
           "chunk": entry["id"], "arm": None, "task": entry["task"], "models": [], "provider": "none",
           "reasoning": "none", "prompt_sha256": [], "served_providers": [], "spent_usd": 0.0,
           "scored_utc": _now()}
    path = step_gate_path(out, entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    return res


#: gate kinds of campaign steps (no model calls, so no provider check)
STEP_GATES = ("resolved", "conformance")


def step_gate_path(out: Path, entry: Mapping[str, Any]) -> Path:
    """Where a step's gate.json lives under the labels root."""
    sub = ADJUDICATION_DIR if entry["task"] == "adjudication" else f"build-{entry['id']}"
    return Path(out) / sub / "gate.json"


def gate_conformance(name: str, entry: Mapping[str, Any], out: Path, stage: Path) -> dict[str, Any]:
    """Step gate for chunks 0 and 11: the listed v10 conformance gates over the built stage."""
    from .. import v10_conformance  # noqa: PLC0415

    built = ROOT / str(entry.get("stage") or "data/v10/stage")
    if not (built / "manifest.json").exists():
        raise SystemExit(f"{built} has no manifest.json; run `python -m mailroom_eda.v10_build --out {built}"
                         f"{' --labels' if str(entry['id']) != '0' else ''}` first")
    done = {str(a["chunk"]) for a in load_approvals(name)}
    unapproved = [str(n) for n in entry.get("needs") or [] if str(n) not in done]
    rep = v10_conformance.run(built, stage, list(entry["gate"].get("gates") or v10_conformance.GATE_NAMES),
                              labels_root=Path(out), campaign=name)
    manifest = json.loads((built / "manifest.json").read_text(encoding="utf-8"))
    res = {"kind": "conformance", "gates": rep["gates"], "unapproved_needs": unapproved,
           "passed": rep["passed"] and not unapproved, "stage": str(built),
           "manifest_sha256": hashlib.sha256((built / "manifest.json").read_bytes()).hexdigest(),
           "labels": (manifest.get("inputs") or {}).get("labels"),
           "chunk": entry["id"], "arm": None, "task": entry["task"], "models": [], "provider": "none",
           "reasoning": "none", "prompt_sha256": [], "served_providers": [], "spent_usd": 0.0,
           "scored_utc": _now()}
    path = step_gate_path(out, entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    return res


def cmd_adjudicate(args: argparse.Namespace) -> int:
    """Rebuild every approved field chunk and list its open items in ``adjudication/open.csv``."""
    auto_restore(args)
    items, copied = adjudication_items(args.campaign, args.out, args.stage, only=args.chunk,
                                       sandbox=getattr(args, "sandbox", None))
    path = Path(args.out) / ADJUDICATION_DIR / "open.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    if args.chunk and path.exists():
        # One chunk was rebuilt: keep every other chunk's rows (and their typed verdicts).
        keep = [r for r in export.read_csv(path) if str(r["chunk"]).split(".")[0] != str(args.chunk)]
        items = keep + items
    export.write_csv(path, OPEN_COLUMNS, items)
    if copied:
        print(f"copied {copied} verdicts from open.csv into their chunks")
    per = Counter((i["chunk"], i["field"]) for i in items)
    for (chunk, field), n in sorted(per.items()):
        print(f"{chunk:<6} {field:<32} {n:>5} open")
    print(f"{len(items)} open items -> {path}")
    return auto_checkpoint(args, f"adjudicate ({len(items)} open)")


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


def bakeoff_guard(name: str, entry: Mapping[str, Any], res: Mapping[str, Any], out: Path,
                  override: bool = False) -> None:
    """Approving a bake-off arm picks the model later chunks use (``winner_of_<id>``): it must
    be the arm ``labeling bakeoff`` chose. A holdout chunk (``1h``) follows its chunk's pick."""
    cid = str(entry["id"])
    base = cid[:-1] if cid.endswith("h") else cid
    if sum(str(c["id"]) == cid for c in campaign(name)["chunks"]) < 2 or override:
        return
    path = out / f"{base}.bakeoff.json"
    if not path.exists():
        raise SystemExit(f"chunk {cid} is a bake-off: run `labeling bakeoff {base}` before approving an arm")
    primary = json.loads(path.read_text(encoding="utf-8")).get("primary") or {}
    if (primary.get("model"), str(primary.get("reasoning"))) != (res["models"][0], str(res["reasoning"])):
        raise SystemExit(f"the bake-off picked {primary.get('model')} at {primary.get('reasoning')}; this arm is "
                         f"{res['models'][0]} at {res['reasoning']} (pass --override-bakeoff to approve it anyway)")


def cmd_gate(args: argparse.Namespace) -> int:
    """Score a chunk's gate; with --approve, record a pass in approved.yaml."""
    entry, arm = chunk_entry(args.campaign, args.id, args.arm)
    auto_restore(args)
    res = gate_result(args.campaign, entry, arm, args.out, args.stage, signed_off=args.signed_off,
                      sandbox=getattr(args, "sandbox", None))
    if res["kind"] not in STEP_GATES and res["provider"] != args.provider:
        raise SystemExit(f"--provider {args.provider}, but chunk {entry['id']} ran on {res['provider']} "
                         f"(runs.jsonl); the gate scores the models the chunk ran")
    print(json.dumps(res, indent=1))
    if not args.approve:
        return 0 if res["passed"] else 1
    if not res["passed"]:
        raise SystemExit("gate did not pass; nothing approved")
    if not args.initials or not spend.INITIALS_RE.match(args.initials):
        raise SystemExit("--approve needs --initials (a human's 2-6 capital letters)")
    second = getattr(args, "second_opinion", False)
    if not second:
        bakeoff_guard(args.campaign, entry, res, Path(args.out), getattr(args, "override_bakeoff", False))
    approvals = load_approvals(args.campaign)
    opinions = load_second_opinions(args.campaign)
    if second:
        if entry.get("set") not in ("calibration", "holdout"):
            raise SystemExit("--second-opinion records a calibration or holdout arm only")
        if any((str(a["chunk"]), a.get("arm")) == (str(entry["id"]), arm) for a in [*approvals, *opinions]):
            raise SystemExit(f"chunk {entry['id']} arm {arm} is already approved")
    same = [a for a in approvals if str(a["chunk"]) == str(entry["id"])] if not second else []
    if same and not args.replace:
        raise SystemExit(f"chunk {entry['id']} already has an approved arm; pass --replace to change it")
    approvals = [a for a in approvals if str(a["chunk"]) != str(entry["id"])] if args.replace and not second \
        else approvals
    (opinions if second else approvals).append({
        "chunk": str(entry["id"]), "arm": arm, "task": entry["task"], "set": entry["set"],
        "slice": entry.get("chunk"), "models": res["models"], "provider": res["provider"],
        **({"providers": res["providers"]} if res.get("providers") else {}),
        "reasoning": res["reasoning"],
        "temperature": {m: providers.temperature(m) for m in res["models"]}, "prompt_sha256": res["prompt_sha256"],
        "served_providers": res["served_providers"],
        "gate": {k: v for k, v in res.items() if k in ("kind", "micro_f1", "micro_precision", "cohen_kappa",
                                                       "precision", "adjudicate_share", "passed")},
        "approved_by": args.initials, "approved_utc": _now()})
    path = _write_approvals(args.campaign, approvals, opinions)
    print(f"approved chunk {entry['id']}{f' arm {arm}' if arm else ''}"
          f"{' as a second opinion' if second else ''} -> {path}")
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
    for st in camp.get("steps") or []:
        sid = str(st["id"])
        gate_path = step_gate_path(args.out, st)
        gate = json.loads(gate_path.read_text()) if gate_path.exists() else None
        failed = ([g for g, v in (gate or {}).get("gates", {}).items() if not v["passed"]]
                  if st["task"] == "build" else (gate or {}).get("open_items", "?"))
        state = ("gate passed" if gate and gate["passed"] else
                 f"gate failed ({'gates ' + ','.join(map(str, failed)) if st['task'] == 'build' else f'{failed} open'})"
                 if gate else "not scored")
        if sid in approved:
            state += f"; approved by {approved[sid]['approved_by']}"
        print(f"{sid:<6} {st['task']:<18} {st.get('set', ''):<12} {'':<5} {'':>9} {'':>8} {'':>6}  {state}")
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
    if host.endswith(".modal.run") and urllib.parse.urlparse(backend.base_url).scheme != "https":
        return False, "VLLM_BASE_URL must be https:// for a *.modal.run host (the API key is sent)"
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

    from . import jev  # noqa: PLC0415

    jev.add_command(sub, common)
    p = sub.add_parser("chunk", help="run one campaign chunk as campaign.yaml defines it")
    common(p)
    p.add_argument("id")
    p.add_argument("--arm", type=int)
    p.add_argument("--initials")
    p.add_argument("--provider", default="openrouter", choices=(*providers.PROVIDERS, "mixed"),
                   help="mixed: --model-map swaps a campaign model for one on another provider")
    p.add_argument("--cap-usd", type=float, help="override the campaign cap (raise it for Modal: GPU time and the cold start count)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--model-map", action="append", default=[], metavar="CAMPAIGN_MODEL=PROVIDER_MODEL",
                   help="with --provider modal: which approved Modal model replaces a campaign model; "
                        "with --provider mixed: which campaign model the other provider's model replaces")
    p.add_argument("--ack-requires", action="store_true",
                   help="the external release named by the chunk's requires: has landed")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-checkpoint", action="store_true",
                   help="do not upload the answers to the private HF staging repo (not recommended)")
    p.set_defaults(operate=cmd_chunk)

    p = sub.add_parser("sweep", help="run every runnable chunk through --through after one dry run and one yes")
    common(p)
    p.add_argument("--through", required=True, help="the last chunk id to run (campaign.yaml order)")
    p.add_argument("--from", dest="start", help="start at this chunk id instead of the first")
    p.add_argument("--task", action="append", default=[],
                   help="only this task's chunks (repeatable), e.g. --task correspondence --task corporate")
    p.add_argument("--initials")
    p.add_argument("--provider", default="openrouter", choices=(*providers.PROVIDERS, "mixed"),
                   help="mixed: --model-map swaps a campaign model for one on another provider")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--model-map", action="append", default=[], metavar="CAMPAIGN_MODEL=PROVIDER_MODEL")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-checkpoint", action="store_true")
    p.set_defaults(operate=cmd_sweep)

    p = sub.add_parser("gate", help="score a chunk's gate; --approve records a pass")
    common(p)
    p.add_argument("id")
    p.add_argument("--arm", type=int)
    p.add_argument("--approve", action="store_true")
    p.add_argument("--replace", action="store_true")
    p.add_argument("--signed-off", action="store_true", help="you read the chunk's CSVs / sample.csv")
    p.add_argument("--override-bakeoff", action="store_true",
                   help="approve a bake-off arm other than the one `labeling bakeoff` picked")
    p.add_argument("--second-opinion", action="store_true",
                   help="record a calibration arm as a calibrated second-opinion model; it does not "
                        "change the chunk's winner")
    p.add_argument("--sandbox", type=Path, default=runner.DEFAULT_SANDBOX,
                   help="local-mailroom-sandbox checkout for the interim cross-check (absent: skipped)")
    p.add_argument("--initials")
    p.add_argument("--provider", default="openrouter", choices=(*providers.PROVIDERS, "mixed"),
                   help="mixed: --model-map swaps a campaign model for one on another provider")
    p.add_argument("--no-checkpoint", action="store_true")
    p.set_defaults(operate=cmd_gate)

    p = sub.add_parser("bakeoff", help="rank a bake-off chunk's arms by paired bootstrap (offline)")
    common(p)
    p.add_argument("id")
    p.add_argument("--margin", type=float, default=NON_INFERIORITY_MARGIN)
    p.set_defaults(operate=cmd_bakeoff)

    p = sub.add_parser("inspect", help="write inspect.md: answers, gold and quote context to read before a gate")
    common(p)
    p.add_argument("dir", type=Path, help="a chunk or run output directory")
    p.add_argument("--n", type=int, default=8)
    p.set_defaults(operate=cmd_inspect)

    p = sub.add_parser("adjudicate", help="list every approved chunk's open adjudication items (offline)")
    common(p)
    p.add_argument("--chunk", help="only this approved chunk id")
    p.add_argument("--sandbox", type=Path, default=runner.DEFAULT_SANDBOX)
    p.add_argument("--no-checkpoint", action="store_true")
    p.set_defaults(operate=cmd_adjudicate)

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

