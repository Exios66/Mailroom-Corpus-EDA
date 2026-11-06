"""Jev intent QA: TypeSafe ``typesafe/jev-1.14`` checks intent labels; it never writes one.

Jev is a decisions model on OpenRouter's Decisions API (``POST /api/alpha/decisions``;
``chat/completions`` returns 400). One ``choice`` question per email returns a probability for
each of the eight intents. The existing label is never sent, so it cannot anchor the answer.

* ``calibrate`` scores the current prompt against the corrected manual gold (Cohen's kappa).
* ``audit`` re-reads relabeled train emails and flags those whose agreed label Jev gives a
  probability below ``flag_below``. It is refused until the current prompt sha has a
  calibration at or above ``kappa_floor``: an uncalibrated judge would only buy noise.

Spend is bounded four ways: every run needs initials, a cap and a wall limit, and the cap
must fit the key's headroom (``spend``); the worst-case estimate must fit the run cap before
any call; all Jev journals together may not pass ``jev.total_cap_usd`` in ``campaign.yaml``;
and every answer is journaled by (filename, prompt sha) and never bought twice. Test-split
rows are never audited (their labels are a person's decision).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import spend
from .registry import campaign

PROMPT_PATH = Path(__file__).parent / "prompts" / "jev_intent.json"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
BYTES_PER_TOKEN = 3.0  # conservative: real ratio on the gold emails is about 3.5
SAFETY = 1.5           # worst-case multiplier on the estimate


def settings(name: str = "v10") -> dict[str, Any]:
    """The campaign's ``jev`` block."""
    cfg = dict(campaign(name).get("jev") or {})
    for k in ("model", "price_per_m_input_usd", "total_cap_usd", "kappa_floor", "flag_below"):
        if k not in cfg:
            raise SystemExit(f"campaign.yaml jev.{k} is missing")
    return cfg


def load_prompt(path: Path = PROMPT_PATH) -> tuple[dict[str, Any], str]:
    """The prompt and its sha (instructions and criteria only)."""
    p = json.loads(path.read_text(encoding="utf-8"))
    core = {"instructions": p["instructions"], "criteria": p["criteria"]}
    return p, hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()


def build_body(row: Mapping[str, Any], prompt: Mapping[str, Any], model: str) -> dict[str, Any]:
    """The Decisions request for one email: full text, no label, no data collection."""
    return {"model": model, "state": {"email": str(row["doc_text"])},
            "provider": {"data_collection": "deny"},
            "questions": {"intent": {"type": "choice", "instructions": prompt["instructions"],
                                     "criteria": prompt["criteria"]}}}


def worst_cost(body: Mapping[str, Any], price_per_m: float) -> float:
    """Worst-case input cost of one request (output is free)."""
    return len(json.dumps(body)) / BYTES_PER_TOKEN * price_per_m / 1e6 * SAFETY


def journal_path(out: Path, mode: str) -> Path:
    return out / f"jev_intent-{mode}" / "journal.jsonl"


def read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def total_spent(out: Path) -> float:
    """Billed cost of every Jev answer on disk."""
    return sum(float(j.get("cost_usd") or 0) for p in out.glob("jev_intent-*/journal.jsonl")
               for j in read_journal(p))


def post_decision(body: Mapping[str, Any], api_key: str, *, timeout: float = 90.0) -> dict[str, Any] | None:
    """One Decisions call with three tries on transient errors; None when all fail."""
    req = urllib.request.Request(DECISIONS_URL, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {api_key}",
                                          "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as err:
            if err.code < 500 and err.code != 429:  # a malformed request: stop, never retry-buy
                raise SystemExit(f"Decisions API HTTP {err.code}: {err.read()[:400]!r}") from err
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(5 * (attempt + 1))
    return None


def kappa(pairs: Sequence[tuple[str, str]]) -> float:
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in ca) / n / n
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def ask(rows: Sequence[Mapping[str, Any]], out: Path, mode: str, *, cap_usd: float, max_wall_seconds: float,
        cfg: Mapping[str, Any], prompt: Mapping[str, Any], sha: str,
        post: Callable[[Mapping[str, Any]], dict[str, Any] | None]) -> dict[str, Any]:
    """Answer every row not yet journaled for ``sha``, within both caps. Returns run stats."""
    jpath = journal_path(out, mode)
    done = {(j["filename"], j["prompt_sha"]) for j in read_journal(jpath)}
    todo = [r for r in rows if (r["filename"], sha) not in done]
    price = float(cfg["price_per_m_input_usd"])
    bodies = [build_body(r, prompt, cfg["model"]) for r in todo]
    est = sum(worst_cost(b, price) for b in bodies)
    left = float(cfg["total_cap_usd"]) - total_spent(out)
    print(f"jev {mode}: {len(rows)} rows, {len(todo)} to ask, worst case ${est:.4f}; "
          f"run cap ${cap_usd:.2f}, campaign Jev budget left ${left:.4f}", file=sys.stderr)
    if est > cap_usd:
        raise spend.SpendError(f"worst case ${est:.4f} exceeds the run cap ${cap_usd:.2f}; nothing sent")
    if est > left:
        raise spend.SpendError(f"worst case ${est:.4f} exceeds the Jev budget left ${left:.4f} "
                               "(campaign.yaml jev.total_cap_usd); nothing sent")
    jpath.parent.mkdir(parents=True, exist_ok=True)
    started, spent, failed = time.monotonic(), 0.0, 0
    with jpath.open("a", encoding="utf-8") as jf:
        for row, body in zip(todo, bodies):
            if time.monotonic() - started > max_wall_seconds:
                raise spend.SpendError(f"wall-clock limit {max_wall_seconds:.0f}s reached")
            if spent + worst_cost(body, price) > min(cap_usd, left):
                raise spend.SpendError(f"next call could pass the cap (spent ${spent:.4f})")
            res = post(body)
            answer = (res or {}).get("answers", {}).get("intent")
            if not answer:
                failed += 1
                continue
            cost = float((res.get("usage") or {}).get("cost") or 0)
            spent += cost
            jf.write(json.dumps({"filename": row["filename"], "document_id": row.get("document_id"),
                                 "split": row.get("split"), "prompt_sha": sha,
                                 "prompt_version": prompt.get("version"), "answer": answer,
                                 "cost_usd": cost, "served": res.get("model"), "gen_id": res.get("id"),
                                 "bytes": len(str(row["doc_text"]).encode()),
                                 "long_context": len(str(row["doc_text"]).encode()) > 32_000}) + "\n")
            jf.flush()
    return {"asked": len(todo) - failed, "failed": failed, "spent_usd": round(spent, 6)}


def score_calibration(rows: Sequence[Mapping[str, Any]], journal: Sequence[Mapping[str, Any]], sha: str
                      ) -> dict[str, Any]:
    """Kappa, accuracy and the misses of ``sha`` against the rows' (corrected) gold intents."""
    ans = {j["filename"]: j["answer"] for j in journal if j["prompt_sha"] == sha}
    pairs, misses = [], []
    for r in rows:
        a = ans.get(r["filename"])
        if not a or a.get("type") != "choice":
            continue
        gold, got = r["gt_fields"]["intent"], a["choice"]
        pairs.append((gold, got))
        if got != gold:
            misses.append({"filename": r["filename"], "gold": gold, "jev": got,
                           "confidence": a.get("confidence"), "p_gold": a["probabilities"].get(gold)})
    if not pairs:
        return {"prompt_sha": sha, "n": 0}
    return {"prompt_sha": sha, "n": len(pairs), "of": len(rows),
            "accuracy": round(sum(a == b for a, b in pairs) / len(pairs), 4),
            "kappa": round(kappa(pairs), 4), "misses": misses}


def calibrated(out: Path, sha: str, floor: float) -> dict[str, Any] | None:
    """The calibration report for ``sha`` if it covers every gold row and reaches ``floor``."""
    p = out / "jev_intent-calibrate" / f"calibration-{sha[:12]}.json"
    if not p.exists():
        return None
    rep = json.loads(p.read_text(encoding="utf-8"))
    return rep if rep.get("n") and rep["n"] == rep.get("of") and rep["kappa"] >= floor else None


def agreed_labels(out: Path) -> dict[str, str]:
    """Final train intent per filename from the relabel ``decisions.csv`` files."""
    final: dict[str, str] = {}
    for p in sorted(out.glob("4*_intent-relabel-*/decisions.csv")):
        with p.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["split"] != "train":
                    continue
                rd = r["reviewer_decision"]
                final[r["filename"]] = (r["existing"] if rd == "keep" else rd) if rd else \
                    (r["new_intent"] if r["decision"] in ("confirm", "change") else r["existing"])
    return final


def flags(journal: Sequence[Mapping[str, Any]], agreed: Mapping[str, str], sha: str, below: float
          ) -> list[dict[str, Any]]:
    """Audit rows where Jev's probability on the agreed label is below ``below``."""
    out = []
    for j in journal:
        if j["prompt_sha"] != sha or j["filename"] not in agreed:
            continue
        lab = agreed[j["filename"]]
        p = float(j["answer"].get("probabilities", {}).get(lab, 0))
        if p < below:
            out.append({"filename": j["filename"], "agreed": lab, "p_agreed": p,
                        "jev": j["answer"].get("choice"), "long_context": j.get("long_context")})
    return sorted(out, key=lambda f: f["p_agreed"])


def cmd_jev(args: argparse.Namespace, *, post: Callable[..., Any] | None = None, key: str | None = None) -> int:
    """``labeling jev calibrate|audit``."""
    from .. import v9_4_revision as v94  # noqa: PLC0415
    from . import operate, providers, runner  # noqa: PLC0415

    cfg = settings(args.campaign)
    prompt, sha = load_prompt(Path(args.prompt) if args.prompt else PROMPT_PATH)
    out = Path(args.out)
    rows = runner.load_stage(Path(args.stage))
    if args.mode == "calibrate":
        rows = v94.manual_intent_rows(rows)
        rows, _ = operate.corrected_gold(rows, operate.gold_corrections(out))
    else:
        if not calibrated(out, sha, float(cfg["kappa_floor"])):
            raise SystemExit(f"jev audit refused: prompt {sha[:12]} has no full calibration at "
                             f"kappa >= {cfg['kappa_floor']} (run `labeling jev calibrate` first)")
        agreed = agreed_labels(out)
        rows = [r for r in rows if r["filename"] in agreed and r.get("split") == "train"]
    spend.require_run_flags(initials=args.initials, cap_usd=args.api_cost_cap_usd,
                            max_wall_seconds=args.max_wall_seconds)
    if post is None:
        backend = providers.Backend.from_env("openrouter")
        spend.check_key_headroom(providers.key_status(backend), args.api_cost_cap_usd)
        key = backend.api_key
        post = lambda b: post_decision(b, key)  # noqa: E731
    try:
        stats = ask(rows, out, args.mode, cap_usd=args.api_cost_cap_usd, max_wall_seconds=args.max_wall_seconds,
                    cfg=cfg, prompt=prompt, sha=sha, post=post)
    except spend.SpendError as err:
        if getattr(args, "no_checkpoint", False) is False and journal_path(out, args.mode).exists():
            operate.auto_checkpoint(args, f"jev {args.mode} (stopped)")
        raise SystemExit(f"jev {args.mode} stopped: {err}") from err
    journal = read_journal(journal_path(out, args.mode))
    if args.mode == "calibrate":
        rep = {**score_calibration(rows, journal, sha), "prompt_version": prompt.get("version"),
               "kappa_floor": cfg["kappa_floor"], "operator": args.initials, **stats}
        (out / "jev_intent-calibrate" / f"calibration-{sha[:12]}.json").write_text(
            json.dumps(rep, indent=1), encoding="utf-8")
        print(json.dumps({k: rep.get(k) for k in ("prompt_sha", "n", "of", "accuracy", "kappa", "spent_usd")}))
    else:
        fl = flags(journal, agreed_labels(out), sha, float(cfg["flag_below"]))
        p = out / "jev_intent-audit" / f"flags-{sha[:12]}.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["filename", "agreed", "p_agreed", "jev", "long_context",
                                               "reviewer_decision", "reviewed_by", "reviewer_note"],
                               quoting=csv.QUOTE_ALL)
            w.writeheader()
            w.writerows(fl)
        print(json.dumps({"flagged": len(fl), "of": len(rows), "file": str(p), **stats}))
    if getattr(args, "no_checkpoint", False):
        return 0
    return operate.auto_checkpoint(args, f"jev {args.mode}")


def add_command(sub: Any, common: Callable[[argparse.ArgumentParser], None]) -> None:
    p = sub.add_parser("jev", help="Jev 1.14 intent QA: calibrate on the gold, or audit relabeled train rows")
    common(p)
    p.add_argument("mode", choices=("calibrate", "audit"))
    p.add_argument("--initials")
    p.add_argument("--api-cost-cap-usd", type=float)
    p.add_argument("--max-wall-seconds", type=float, default=1800)
    p.add_argument("--prompt", help="a prompt file other than prompts/jev_intent.json (refinement drafts)")
    p.add_argument("--no-checkpoint", action="store_true")
    p.set_defaults(operate=cmd_jev)
