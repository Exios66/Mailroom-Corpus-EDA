"""Launch labeling batches from the review portal, behind a loud preflight checklist.

Off unless the portal starts with ``--enable-launch``; otherwise every ``/api/launch/*``
route answers 403 with the flag to set. Two kinds of target:

* **campaign arm**: one chunk arm of ``campaign.yaml``, run through ``operate.cmd_chunk``
  exactly as ``labeling chunk <id>`` does (approvals, ping, HF checkpoint, stage sha, ...).
* **public trial**: 1-5 documents of one type from the public dataset (``config.REPO_ID`` at
  ``config.REPO_REVISION``), labeled with the task and models of that type's labeling campaign
  entry, capped at ``TRIAL_CAP_CEILING`` dollars. A trial's reasoning is per model
  (``TRIAL_PAIRS``, ``trial_reasoning``): DeepSeek runs at ``off`` because at ``low`` it spirals
  (see ``TRIAL_DEEPSEEK_NOTE``). Trial documents get namespaced ids (``trial_row``) so a trial
  packet never collides with a stage packet of the same document in the review portal. Trial documents are chosen
  by ``runner.select`` on the set that holds only documents that need labels (``pending`` or
  ``remainder``), then refused again if any is CUAD or MAUD gold: nothing here ever bills a
  document that already has expert labels. Campaign *qualification* arms (calibration and
  holdout sets) re-label gold on purpose to score models; they are labeled as such.

``preflight`` returns one item per check (``pass`` / ``fail`` / ``warn`` / ``skip``). A failed
item says what is wrong, quotes the underlying error and gives the exact command, variable or
file to change. ``start`` re-runs the preflight on the server (the client is never trusted),
refuses with every failing item unless all pass and the typed confirmation equals the cap, then
runs ONE background job. An API key the caller provides lives in server memory for that call
only: it reaches the run through an environment mapping handed to ``Backend.from_env`` and is
redacted from every log line, response, receipt and error text; ``os.environ`` is never changed.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import config
from . import checkpoint, derive, export, operate, providers, registry, runner, spend, tasks

TRIAL_CAP_CEILING = 0.25
TRIAL_MAX_WALL_SECONDS = 900.0
TRIAL_DEFAULT_DOCUMENTS = 2
TRIAL_MAX_DOCUMENTS = 5
LOG_LINES = 500
LOG_TAIL = 200
LINE_CHARS = 600
MIN_SECRET_CHARS = 6
#: sets whose documents are re-labeled to score models, not because they need labels
QUALIFICATION_SETS = frozenset({"calibration", "holdout"})
KIND_LABELS = {"qualification": "Qualification — re-labels gold documents to score models",
               "labeling": "Labeling — documents that need labels"}
#: document type -> (task, set) of its labeling campaign entry; the set holds only documents
#: that need labels (contracts: the EX-10 contracts without CUAD labels; mergers: consideration
#: MAUD does not answer; the rest: what the campaign still has to label)
TRIAL_TYPES = {"contract": ("cuad", "pending"),
               "merger_agreement": ("merger_consideration", "pending"),
               "correspondence": ("correspondence", "remainder"),
               "insurance_claim": ("claim_checklist", "remainder"),
               "corporate_record": ("corporate", "remainder")}
GLM = "z-ai/glm-5.3-flashx-legal"
DEEPSEEK = "deepseek/deepseek-v4.1-flash"
#: Why a trial runs DeepSeek at ``off`` (shown as the target's description and in the preflight).
TRIAL_DEEPSEEK_NOTE = (
    "DeepSeek v4.1 Flash runs at reasoning off in trials: at low it spirals (336 to over 2,300 reasoning "
    "tokens on the same email, 2026-11-06; 96% of its output was reasoning in the claim_checklist pilot, "
    "truncating even at an 11k budget), and the campaign's own CUAD arms 3 and holdout 3 run it at off. "
    "A model at low gets max_tokens plus 4 x its measured p75 reasoning tokens (floor 1,024, ceiling "
    "6,144); a call cut off with 80% or more of its output spent reasoning is journaled as a failed "
    "call (reasoning_runaway) and shown on the review record.")
#: Trial types whose models and reasoning are fixed here rather than read from the campaign entry.
#: Contracts: the campaign's labeling arms (2a-2c) say ``winner_of_1``, which has no approval yet, so a
#: trial would be blocked for the whole bake-off; the owner chose GLM-5.3 Flash Legal and DeepSeek
#: for contracts, with the reasoning each is known to handle.
TRIAL_PAIRS = {"contract": {
    "models": [GLM, DEEPSEEK], "reasoning": {GLM: "low", DEEPSEEK: "off"},
    "note": ("Contract trials label with GLM-5.3 FlashX Legal at reasoning low beside DeepSeek v4.1 Flash at "
             "off (the owner's pick). The campaign's cuad labeling arms (2a-2c) say winner_of_1, which has no "
             "approval yet, so this trial does not wait for it and its labels are proposals for review only. "
             + TRIAL_DEEPSEEK_NOTE)}}
EXIT_MEANING = {2: "stopped at the cap or wall limit; launch the same target again to continue "
                   "(answered calls are not bought twice)",
                3: "finished with failed calls; launch the same target again to retry only those",
                4: "the run finished but the HF checkpoint failed; the output is on disk, retry "
                   "`python -m mailroom_eda.labeling checkpoint`"}
RUN_CMD = "PYTHONPATH=src .venv/bin/python -m mailroom_eda.labeling"
STAGE_BUILD = ("`.venv/bin/python scripts/build/build_v9_3_corporate_presence.py` then "
               "`.venv/bin/python scripts/build/build_v9_4_dedup_labels.py --no-cuad-journal` "
               f"(or in one step `{RUN_CMD} stage`)")
PASS, FAIL, WARN, SKIP = "pass", "fail", "warn", "skip"


class LaunchDisabled(PermissionError):
    """Launch endpoints were called while the portal runs without ``--enable-launch``."""


class LaunchConflict(RuntimeError):
    """A start was refused: ``items`` are the preflight items (failing ones included)."""

    def __init__(self, message: str, items: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.items = items or []


def _now() -> str:
    """UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def item(id: str, title: str, status: str, detail: str = "", fix: str = "") -> dict[str, str]:
    """One checklist entry. A failure always carries a fix: that is the point of the list."""
    if status == FAIL and not fix:
        raise ValueError(f"preflight item {id} failed without a fix")
    return {"id": id, "title": title, "status": status, "detail": detail, "fix": fix}


def redact(text: Any, secrets: set[str]) -> str:
    """``text`` with every secret replaced, so a key never reaches a response, log or receipt."""
    out = str(text)
    for secret in sorted(secrets, key=len, reverse=True):
        if len(secret) >= MIN_SECRET_CHARS:
            out = out.replace(secret, "[redacted]")
    return out


def _decode_gt(row: dict[str, Any]) -> dict[str, Any]:
    """Public rows carry ``gt_fields`` as JSON text; ``runner.load_stage`` decodes it, so do the same."""
    import json  # noqa: PLC0415

    if isinstance(row.get("gt_fields"), str):
        row["gt_fields"] = json.loads(row["gt_fields"])
    return row


def gold_reason(row: Mapping[str, Any]) -> str:
    """Why this row is CUAD or MAUD gold (a trial must never bill it), or ``""``."""
    cls = row.get("expected")
    if cls == "contract":
        if row.get("source_corpus") != "sec_edgar":
            return f"source_corpus is {row.get('source_corpus')!r}: a CUAD-gold contract"
        labels = derive._obj((row.get("gt_fields") or {}).get("cuad_clause_labels"))
        if labels:
            return "it already has CUAD clause labels"
    if cls == "merger_agreement" and derive.maud_consideration_answer(row):
        return "MAUD already answers its consideration"
    return ""


def trial_prefix() -> str:
    """``public-v9.3:``: the namespace of trial document ids (the tag of ``config.REPO_REVISION``)."""
    return f"public-{config.REPO_TAG}:"


def trial_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """A public row as a trial reads it: ``document_id`` namespaced (``public-v9.3:<id>``) so its
    packet cannot collide with a stage packet of the same document (the portal refuses one
    document id with two source texts); ``published_document_id`` keeps the dataset's own id."""
    published = str(row.get("document_id") or row.get("id") or row["filename"])
    return {**row, "document_id": trial_prefix() + published, "published_document_id": published}


#: Models whose providers refuse reasoning off with HTTP 400 (runbook.md, "ping" step).
NEEDS_REASONING = ("z-ai/glm-", "minimax/")


def trial_reasoning(models: list[str], reasoning: "str | Mapping[str, str]") -> "str | dict[str, str]":
    """A trial's reasoning for ``models``: the campaign's level for each model, except DeepSeek,
    which always runs at ``off`` (:data:`TRIAL_DEEPSEEK_NOTE`), and GLM-legal and MiniMax, which
    refuse ``off`` (HTTP 400) and so run at ``low`` even where a deferred campaign entry still
    lists them at off (chunks 5, 6 and 9). One level when the models share it."""
    levels = runner.reasoning_levels(reasoning, models)
    return runner.reasoning_spec({m: "off" if m.startswith("deepseek/")
                                  else "low" if m.startswith(NEEDS_REASONING) else lvl
                                  for m, lvl in levels.items()})


def pick_trial(rows: list[Mapping[str, Any]], count: int, per_subclass: bool = False) -> list[Mapping[str, Any]]:
    """Deterministic trial documents: ascending ``document_id``; with ``per_subclass`` one per
    subclass in turn (subclasses sorted) until ``count`` are chosen."""
    key = lambda r: str(r.get("document_id") or r.get("id") or r["filename"])  # noqa: E731
    ordered = sorted(rows, key=key)
    if not per_subclass:
        return ordered[:count]
    groups: dict[str, list] = {}
    for r in ordered:
        groups.setdefault(str(r.get("expected_subclass") or ""), []).append(r)
    chosen: list = []
    while len(chosen) < count and any(groups.values()):
        for sub in sorted(groups):
            if groups[sub] and len(chosen) < count:
                chosen.append(groups[sub].pop(0))
    return sorted(chosen, key=key)


@dataclass
class Request:
    """A validated launch request body."""

    target: str = ""
    initials: str | None = None
    cap_usd: float | None = None
    cap_text: str = ""
    api_key: str = field(default="", repr=False)
    ack_requires: bool = False
    documents: int = TRIAL_DEFAULT_DOCUMENTS
    per_subclass: bool = False
    confirm: str | None = None
    cap_problem: str = ""


def parse_request(body: Mapping[str, Any], *, start: bool) -> Request:
    """Validate the shape of a preflight/start body. Values that merely fail a rule (a bad cap,
    bad initials) are left for the preflight to report as items; a malformed body raises ValueError."""
    allowed = {"target", "initials", "cap_usd", "api_key", "ack_requires", "documents", "per_subclass"}
    if start:
        allowed |= {"confirm"}
    extra = sorted(set(body) - allowed)
    if extra:
        raise ValueError(f"unexpected field(s) {extra}; allowed: {sorted(allowed)}")
    req = Request()
    if not isinstance(body.get("target"), str) or not body["target"]:
        raise ValueError("target is required (an id from GET /api/launch/options)")
    req.target = body["target"]
    initials = body.get("initials")
    req.initials = initials if isinstance(initials, str) else None
    cap = body.get("cap_usd")
    req.cap_text = "" if cap is None else str(cap)
    if cap is None or cap == "":
        req.cap_problem = "cap_usd is missing"
    elif isinstance(cap, bool) or not isinstance(cap, (int, float, str)):
        req.cap_problem = f"cap_usd must be a number, got {type(cap).__name__}"
    else:
        try:
            req.cap_usd = float(cap)
        except ValueError:
            req.cap_problem = f"cap_usd {cap!r} is not a number"
    key = body.get("api_key", "")
    if key is None:
        key = ""
    if not isinstance(key, str) or len(key) > 512:
        raise ValueError("api_key must be a string of at most 512 characters")
    key = key.strip()
    if key and (not key.isascii() or any(ord(c) < 33 or ord(c) == 127 for c in key)):
        raise ValueError("api_key contains whitespace, control or non-ASCII characters")
    req.api_key = key
    if not isinstance(body.get("ack_requires", False), bool) or not isinstance(body.get("per_subclass", False), bool):
        raise ValueError("ack_requires and per_subclass must be true or false")
    req.ack_requires, req.per_subclass = bool(body.get("ack_requires", False)), bool(body.get("per_subclass", False))
    docs = body.get("documents", TRIAL_DEFAULT_DOCUMENTS)
    if isinstance(docs, bool) or not isinstance(docs, int):
        raise ValueError("documents must be an integer")
    req.documents = docs
    if start:
        confirm = body.get("confirm")
        if confirm is not None and not isinstance(confirm, str):
            raise ValueError("confirm must be the cap typed again, as a string")
        req.confirm = confirm
    return req


@dataclass
class Plan:
    """What a passing preflight resolved, and what ``start`` runs."""

    kind: str = ""
    target_id: str = ""
    entry: dict[str, Any] | None = None
    arm: int | None = None
    task: str = ""
    set_name: str = ""
    models: list[str] = field(default_factory=list)
    reasoning: "str | dict[str, str]" = "off"
    rows: list[Any] = field(default_factory=list)
    picked: list[Any] = field(default_factory=list)
    max_wall: float = 0.0
    source: dict[str, Any] = field(default_factory=dict)
    projection: dict[str, Any] | None = None
    env: dict[str, str] = field(default_factory=dict)
    secrets: set[str] = field(default_factory=set)


class _Tee:
    """Replaces ``sys.stdout``/``sys.stderr`` for the length of a run: complete lines go to the log."""

    encoding = "utf-8"

    def __init__(self, emit: Callable[[str], None]):
        self._emit, self._buf, self._lock = emit, "", threading.Lock()

    def write(self, text: str) -> int:
        """Buffer ``text`` and emit each finished line."""
        with self._lock:
            self._buf += text
            *lines, self._buf = self._buf.split("\n")
        for line in lines:
            self._emit(line)
        return len(text)

    def flush(self) -> None:
        """Nothing to flush: partial lines wait for their newline."""

    def isatty(self) -> bool:
        """Never a terminal."""
        return False

    def finish(self) -> None:
        """Emit a trailing partial line."""
        with self._lock:
            rest, self._buf = self._buf, ""
        if rest:
            self._emit(rest)


class Launcher:
    """The portal's launch backend. All network and Hub access goes through injectable seams:

    * ``chat`` / ``key_status``: the OpenRouter chat call and its ``GET /key`` (defaults:
      ``providers.chat`` / ``providers.key_status``);
    * ``hub``: ``() -> {"rows", "revision", "source_sha256"}`` for the public dataset (default:
      ``review_production.load_production`` on the pinned revision);
    * ``env``: the environment API keys are read from (default ``os.environ``, read at call time).
    """

    def __init__(self, portal: Any, *, enabled: bool = False, chat: Callable | None = None,
                 key_status: Callable | None = None, hub: Callable[[], Mapping[str, Any]] | None = None,
                 env: Mapping[str, str] | None = None):
        self.portal, self.enabled = portal, enabled
        self._chat, self._key_status, self._hub, self._env = chat, key_status, hub, env
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"state": "idle", "target": None, "started_utc": None,
                                       "finished_utc": None, "result": None, "error": None}
        self._log: deque[str] = deque(maxlen=LOG_LINES)
        self._thread: threading.Thread | None = None
        self._hub_cache: dict[str, Any] | None = None
        self._stage_cache: tuple[Any, list[dict[str, Any]], str] | None = None

    # ------------------------------------------------------------------ seams
    @property
    def chat(self) -> Callable:
        """The chat call a run sends requests through."""
        return self._chat or providers.chat

    @property
    def key_status(self) -> Callable:
        """The OpenRouter ``GET /key`` call."""
        return self._key_status or providers.key_status

    def environment(self) -> Mapping[str, str]:
        """The environment keys are read from."""
        return os.environ if self._env is None else self._env

    def _secrets(self, req_key: str = "") -> set[str]:
        """Every secret value that must never be echoed: the provided key and the environment's."""
        env = self.environment()
        return {s for s in (req_key, env.get("OPENROUTER_API_KEY", ""), env.get("VLLM_API_KEY", ""),
                            env.get("HF_TOKEN", "")) if s}

    # ------------------------------------------------------------------ guards
    def require_enabled(self) -> None:
        """Raise ``LaunchDisabled`` (HTTP 403) with the flag that turns launching on."""
        if not self.enabled:
            raise LaunchDisabled("launching labeling batches is disabled; restart the portal with "
                                 "--enable-launch (python -m mailroom_eda.labeling.review_portal --enable-launch)")

    # ------------------------------------------------------------------ targets
    @property
    def _campaign_name(self) -> str:
        return self.portal.campaign

    def _arms(self) -> list[tuple[str, dict[str, Any], int | None]]:
        """``(target id, entry, arm)`` for every chunk arm in the campaign, in campaign order."""
        camp = registry.campaign(self._campaign_name)
        counts, seen, out = {}, {}, []
        for entry in camp["chunks"]:
            counts[str(entry["id"])] = counts.get(str(entry["id"]), 0) + 1
        for entry in camp["chunks"]:
            cid = str(entry["id"])
            seen[cid] = seen.get(cid, 0) + 1
            arm = seen[cid] if counts[cid] > 1 else None
            out.append((f"campaign:{cid}{f'.{arm}' if arm else ''}", entry, arm))
        return out

    def _trial_entry(self, doc_class: str) -> dict[str, Any] | None:
        """The first non-skipped labeling campaign entry of the trial type's task and set."""
        task, set_name = TRIAL_TYPES[doc_class]
        return next((e for e in registry.campaign(self._campaign_name)["chunks"]
                     if e["task"] == task and e["set"] == set_name and not e.get("skip")), None)

    def _winner_gap(self, entry: Mapping[str, Any]) -> str:
        """The unapproved ``winner_of_<id>`` references of an entry, or ``""``."""
        refs = operate.winner_refs(entry)
        approved = {str(a["chunk"]) for a in operate.load_approvals(self._campaign_name)}
        return ", ".join(sorted(r.removeprefix("winner_of_") for r in refs if r.removeprefix("winner_of_") not in approved))

    def _resolved(self, entry: Mapping[str, Any]) -> tuple[list[str], "str | dict[str, str]", bool]:
        """``(models, reasoning, resolved)``: the approved winner replaces ``winner_of_<id>``."""
        try:
            models, reasoning = operate.resolve(self._campaign_name, entry)
            return models, reasoning, True
        except SystemExit:
            return [str(m) for m in entry["models"]], entry["reasoning"], False

    def _trial_config(self, doc_class: str, entry: Mapping[str, Any] | None
                      ) -> tuple[list[str], "str | dict[str, str]", bool, str]:
        """``(models, reasoning, resolved, note)`` of a trial: ``TRIAL_PAIRS`` where the type has one,
        else the campaign entry's models with every DeepSeek at off (:func:`trial_reasoning`)."""
        if doc_class in TRIAL_PAIRS:
            pair = TRIAL_PAIRS[doc_class]
            return list(pair["models"]), runner.reasoning_spec(dict(pair["reasoning"])), True, pair["note"]
        if entry is None:
            return [], "", False, ""
        models, reasoning, resolved = self._resolved(entry)
        if not resolved:
            return models, reasoning, False, ""
        return (models, trial_reasoning(models, reasoning), True,
                TRIAL_DEEPSEEK_NOTE if any(m.startswith("deepseek/") for m in models) else "")

    def options(self) -> dict[str, Any]:
        """Every target the launcher offers, with the kind label, models, reasoning and cap."""
        self.require_enabled()
        approvals = operate.load_approvals(self._campaign_name)
        approved_ids = {str(a["chunk"]) for a in approvals}
        targets = []
        for tid, entry, arm in self._arms():
            models, reasoning, resolved = self._resolved(entry)
            kind = "qualification" if entry["set"] in QUALIFICATION_SETS else "labeling"
            missing = [str(n) for n in entry.get("needs") or [] if str(n) not in approved_ids]
            approved = any(str(a["chunk"]) == str(entry["id"]) and a.get("arm") == arm
                           and a.get("task") == entry["task"] for a in approvals)
            blocker = (f"skipped: {entry['skip']}" if entry.get("skip") else
                       f"needs approved chunk(s): {', '.join(missing)}" if missing else "")
            targets.append({
                "id": tid, "group": "Campaign", "chunk": str(entry["id"]), "arm": arm,
                "title": f"{entry['id']}{f'.{arm}' if arm else ''} · {entry['task']} / {entry['set']}"
                         + (f" {entry['chunk']}" if entry.get("chunk") else ""),
                "task": entry["task"], "set": entry["set"], "models": models, "reasoning": reasoning,
                "models_resolved": resolved, "cap_usd": float(entry["cap_usd"]), "needs": [str(n) for n in entry.get("needs") or []],
                "requires": entry.get("requires"), "kind": kind, "kind_label": KIND_LABELS[kind],
                "status": "blocked" if blocker else "approved" if approved else "ready", "blocker": blocker})
        for cls, (task, set_name) in TRIAL_TYPES.items():
            entry = self._trial_entry(cls)
            models, reasoning, resolved, note = self._trial_config(cls, entry)
            targets.append({
                "id": f"trial:{cls}", "group": "Public trial", "doc_class": cls, "description": note,
                "title": f"{cls} · {task} / {set_name} (public dataset)", "task": task, "set": set_name,
                "chunk": str(entry["id"]) if entry else None, "arm": None,
                "models": models, "reasoning": reasoning, "models_resolved": resolved,
                "cap_usd": TRIAL_CAP_CEILING, "needs": [], "requires": None, "kind": "labeling",
                "kind_label": KIND_LABELS["labeling"], "status": "ready" if entry and resolved else "blocked",
                "blocker": "" if entry and resolved else (
                    f"no labeling entry for {task}/{set_name} in campaign.yaml" if not entry else
                    f"waiting for the approved winner of chunk {self._winner_gap(entry)}")})
        return {"enabled": True, "campaign": self._campaign_name, "kind_labels": KIND_LABELS, "targets": targets,
                "trial": {"default_documents": TRIAL_DEFAULT_DOCUMENTS, "max_documents": TRIAL_MAX_DOCUMENTS,
                          "cap_ceiling_usd": TRIAL_CAP_CEILING,
                          "source": f"{_repo()} @ {_revision()[:12]}"}}

    # ------------------------------------------------------------------ sources
    @property
    def stage_dir(self) -> Path:
        """The stage directory campaign arms read."""
        return Path(self.portal.stage if self.portal.stage is not None else runner.DEFAULT_STAGE)

    def _stage(self) -> tuple[list[dict[str, Any]], str]:
        """Stage rows and the file's sha256, cached by file identity. Raises OSError if absent."""
        import hashlib  # noqa: PLC0415

        path = self.stage_dir / "ground_truth_hardened.jsonl"
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if self._stage_cache and self._stage_cache[0] == key:
            return self._stage_cache[1], self._stage_cache[2]
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = runner.load_stage(self.stage_dir)
        self._stage_cache = (key, rows, sha)
        return rows, sha

    def _public(self) -> dict[str, Any]:
        """The public dataset rows (downloaded once per process), with revision and file sha256."""
        if self._hub_cache is None:
            if self._hub is not None:
                got = dict(self._hub())
            else:
                from .. import hf_interface  # noqa: PLC0415
                from .review_production import load_production  # noqa: PLC0415

                got = load_production(hf_interface.get_hf_api(token=None), None)
            got["rows"] = [_decode_gt(dict(r)) for r in got["rows"]]
            self._hub_cache = got
        return self._hub_cache

    # ------------------------------------------------------------------ preflight
    def preflight(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """The checklist for ``body`` (items, projection, ``can_launch``); starts nothing."""
        self.require_enabled()
        result, _ = self._preflight(parse_request(body, start=False))
        return result

    def _preflight(self, req: Request) -> tuple[dict[str, Any], Plan]:
        plan = Plan(target_id=req.target)
        items: list[dict[str, str]] = []
        add = items.append
        env = dict(self.environment())
        if req.api_key:
            env["OPENROUTER_API_KEY"] = req.api_key
        plan.env, plan.secrets = env, self._secrets(req.api_key)
        try:
            self._checks(req, plan, items)
        except Exception as exc:  # noqa: BLE001 - a crashed check must be a loud failed item, not a 500
            add(item("preflight_error", f"A preflight check crashed: {type(exc).__name__}", FAIL,
                     redact(f"{type(exc).__name__}: {exc}", plan.secrets),
                     "this is a bug or a corrupt input; fix the error quoted above, or run the same steps on the "
                     f"command line: `{RUN_CMD} chunk <id> --dry-run`"))
        for it in items:
            for k in ("title", "detail", "fix"):
                it[k] = redact(it[k], plan.secrets)
        return {"items": items, "projection": plan.projection,
                "can_launch": bool(items) and all(i["status"] != FAIL for i in items),
                "target": req.target}, plan

    def _checks(self, req: Request, plan: Plan, items: list[dict[str, str]]) -> None:  # noqa: C901
        add = items.append
        add(item("launch_enabled", "Launching is enabled", PASS, "the portal was started with --enable-launch"))
        with self._lock:
            running = self._state["state"] == "running"
        add(item("idle", "Nothing else is running", FAIL if running else PASS,
                 "a labeling run is already in progress" if running else "no run in progress",
                 "wait for the current run to finish (watch Status in the Launch panel or GET /api/launch/status); "
                 "only one run at a time" if running else ""))
        arms = {tid: (entry, arm) for tid, entry, arm in self._arms()}
        if req.target in arms:
            plan.kind = "campaign"
            plan.entry, plan.arm = arms[req.target]
        elif req.target.startswith("trial:") and req.target[6:] in TRIAL_TYPES:
            plan.kind = "trial"
        else:
            valid = list(arms) + [f"trial:{c}" for c in TRIAL_TYPES]
            add(item("target", "Unknown launch target", FAIL, f"{req.target!r} is not a target",
                     "pick one of: " + ", ".join(valid) + " (GET /api/launch/options lists them)"))
            return
        trial = plan.kind == "trial"
        if trial:
            plan.entry = self._trial_entry(req.target[6:])
            if plan.entry is None:
                task, set_name = TRIAL_TYPES[req.target[6:]]
                add(item("target", "No labeling entry for this document type", FAIL,
                         f"campaign {self._campaign_name} has no non-skipped {task}/{set_name} chunk",
                         "restore the chunk in src/mailroom_eda/labeling/campaigns/"
                         f"{self._campaign_name}/campaign.yaml, or launch a campaign arm instead"))
                return
        entry = plan.entry
        plan.task, plan.set_name = entry["task"], entry["set"]
        kind = "qualification" if plan.set_name in QUALIFICATION_SETS else "labeling"
        add(item("target", f"Target: {req.target}", PASS,
                 f"{plan.task} / {plan.set_name}" + (" on the public dataset" if trial else f", chunk {entry['id']}")))
        if kind == "qualification":
            add(item("kind", KIND_LABELS[kind], WARN,
                     f"the {plan.set_name} set is expert-labeled gold: this arm bills to score models against it, "
                     "not to label anything", "launch it only to qualify models; to label documents that need labels "
                     "pick a Labeling arm or a Public trial"))
        else:
            add(item("kind", KIND_LABELS[kind], PASS, f"the {plan.set_name} set holds documents that need labels"))

        # --- run flags (spend.require_run_flags)
        ok_initials = bool(req.initials and spend.INITIALS_RE.match(req.initials))
        add(item("initials", "Operator initials", PASS if ok_initials else FAIL,
                 req.initials if ok_initials else f"{req.initials!r} is not 2-6 capital letters",
                 "" if ok_initials else "enter your own initials, 2-6 capital letters, e.g. JB (spend.INITIALS_RE)"))
        cap = req.cap_usd
        cap_ok = cap is not None and math.isfinite(cap) and cap > 0
        add(item("cap_value", "Spend cap is a positive number", PASS if cap_ok else FAIL,
                 f"${cap:.4f}" if cap_ok else (req.cap_problem or f"cap_usd {req.cap_text!r} is not positive and finite"),
                 "" if cap_ok else "enter the most you allow this run to spend in USD, e.g. 0.25 "
                 "(the run stops at the cap)"))
        cap = cap if cap_ok else None
        plan.max_wall = (float(entry.get("max_wall_seconds") or registry.campaign(self._campaign_name)["max_wall_seconds"])
                         if not trial else TRIAL_MAX_WALL_SECONDS)
        wall_ok = math.isfinite(plan.max_wall) and plan.max_wall > 0
        add(item("wall_limit", "Wall-clock limit", PASS if wall_ok else FAIL, f"{plan.max_wall:.0f} s",
                 "" if wall_ok else "set max_wall_seconds in campaign.yaml to a positive number"))

        # --- campaign gates (cmd_chunk)
        if not trial:
            self._campaign_gates(req, plan, items)
        # --- models (resolve + execute's approval and count rules)
        self._models(req, plan, items)
        # --- source rows and selection
        if trial:
            self._trial_rows(req, plan, items)
        else:
            self._stage_rows(plan, items)
        # --- stale winner (cmd_chunk, and a trial that uses a winner)
        uses_winner = bool(operate.winner_refs(entry)) and not (trial and req.target[6:] in TRIAL_PAIRS)
        if uses_winner and plan.picked and plan.models:
            stale = operate.stale_winner(self._campaign_name, entry, plan.picked)
            add(item("stale_winner", "Approved winner was scored on the prompt this run sends",
                     FAIL if stale else PASS, stale or "the approval's prompt sha matches",
                     "re-run the winner's chunk on the current prompt, gate it and re-approve with --replace: "
                     f"`{RUN_CMD} gate <id> --arm N --approve --replace --initials XX`" if stale else ""))
        else:
            add(item("stale_winner", "Approved winner was scored on the prompt this run sends", SKIP,
                     "models or documents are not resolved" if uses_winner else "this arm names no winner_of_<id> model"))
        # --- projection and cap rules
        self._projection(plan)
        self._cap_rules(cap, plan, trial, entry, items)
        # --- key
        self._key_checks(req, plan, cap, items)
        # --- routes, checkpoint, output
        self._route_checks(plan, trial, items)
        root = Path(self.portal.labels_root)
        writable = os.access(root, os.W_OK)
        add(item("output_dir", "Output folder is writable", PASS if writable else FAIL,
                 str(root / "trials") if trial else str(operate.chunk_dir(root, entry, plan.arm)),
                 "" if writable else f"make {root} writable by this user, or start the portal with "
                 "--labels-root pointing at a folder you own"))

    def _campaign_gates(self, req: Request, plan: Plan, items: list[dict[str, str]]) -> None:
        add, entry = items.append, plan.entry
        add(item("arm_runnable", "Arm is not skipped", FAIL if entry.get("skip") else PASS,
                 f"skipped: {entry['skip']}" if entry.get("skip") else "",
                 "pick another arm; a skipped arm keeps its number but never runs (campaign.yaml skip:)"
                 if entry.get("skip") else ""))
        approvals = operate.load_approvals(self._campaign_name)
        done = {str(a["chunk"]) for a in approvals}
        missing = [str(n) for n in entry.get("needs") or [] if str(n) not in done]
        add(item("needs_approved", "Prerequisite chunks are approved", FAIL if missing else PASS,
                 f"not approved yet: {', '.join(missing)}" if missing else
                 (f"approved: {', '.join(str(n) for n in entry['needs'])}" if entry.get("needs") else "no prerequisites"),
                 "read each chunk's report, then approve it: " + "; ".join(
                     f"`{RUN_CMD} gate {n} --arm N --approve --initials XX`" for n in missing) if missing else ""))
        if any(str(a["chunk"]) == str(entry["id"]) and a.get("arm") == plan.arm and a.get("task") == entry["task"]
               for a in approvals):
            add(item("already_approved", "This arm is already approved", WARN,
                     "running it again buys the same labels twice (identical answers are reused, changed prompts are not)",
                     "launch the next chunk instead unless you mean to re-run this one"))
        met, why = operate.requires_met(entry.get("requires"))
        if not entry.get("requires"):
            add(item("requires", "External requirement", SKIP, "this arm has no requires:"))
        elif met:
            add(item("requires", "External requirement met", PASS, why))
        elif req.ack_requires:
            add(item("requires", "External requirement overridden", WARN, f"{why}; you acknowledged it"))
        else:
            add(item("requires", "External requirement not met", FAIL, f"requires {entry['requires']}: {why}",
                     "bump the Dojo pin once the release has landed (`python src/scripts/bump_dojo_scoring.py`), or "
                     "tick \"I acknowledge the external requirement\" if the release has landed"))

    def _models(self, req: Request, plan: Plan, items: list[dict[str, str]]) -> None:
        add, entry = items.append, plan.entry
        trial = plan.kind == "trial"
        try:
            if trial:
                models, reasoning, resolved, _ = self._trial_config(req.target[6:], entry)
                if not resolved:
                    operate.resolve(self._campaign_name, entry)  # raises SystemExit naming the missing approval
                plan.models, plan.reasoning = models, reasoning
            else:
                plan.models, plan.reasoning = operate.resolve(self._campaign_name, entry)
        except SystemExit as exc:
            gap = self._winner_gap(entry)
            add(item("models", "Models are resolved", FAIL, str(exc),
                     f"approve the winning arm of chunk {gap or '<id>'} first: read its report, then "
                     f"`{RUN_CMD} gate {gap or '<id>'} --arm N --approve --initials XX`"))
            return
        prov = {m: "openrouter" for m in plan.models}
        problem = runner.model_problem(plan.task, plan.set_name, plan.models, prov, compare=trial)
        if problem:
            add(item("models", "Models are approved for this task", FAIL, problem,
                     "this launcher runs OpenRouter models; use the command line for Modal or mixed arms: "
                     f"`{RUN_CMD} chunk <id> --provider modal|mixed --model-map ...`, or fix models.yaml"))
            plan.models = []
            return
        add(item("models", "Models are approved for this task", PASS,
                 f"{', '.join(plan.models)} at reasoning {runner.reasoning_text(plan.reasoning)}"))
        levels = runner.reasoning_levels(plan.reasoning, plan.models)
        add(item("reasoning", "Reasoning level and token allowance per model", PASS,
                 "; ".join(f"{m.split('/')[-1]}: {lvl}, max_tokens {runner.max_tokens(plan.task, lvl, m)}"
                           + (f" ({runner.max_tokens(plan.task, 'off')} answer + "
                              f"{runner.reasoning_allowance(m, plan.task, lvl)} reasoning)" if lvl != "off" else "")
                           for m, lvl in levels.items())
                 + ("; " + TRIAL_DEEPSEEK_NOTE if trial and any(m.startswith("deepseek/") for m in plan.models)
                    else "")))

    def _stage_rows(self, plan: Plan, items: list[dict[str, str]]) -> None:
        add = items.append
        path = self.stage_dir / "ground_truth_hardened.jsonl"
        want = registry.campaign(self._campaign_name).get("stage_sha256")
        try:
            rows, sha = self._stage()
        except OSError as exc:
            add(item("stage", "Stage data is missing", FAIL, f"{path}: {type(exc).__name__}: {exc}",
                     f"build it: {STAGE_BUILD}. data/ is git-ignored and is built from the public Hub tag"))
            add(item("selection", "Documents selected", SKIP, "no stage"))
            return
        if want and sha != want:
            add(item("stage", "Stage data does not match the campaign pin", FAIL,
                     f"{path} has sha256 {sha[:12]}; campaign.yaml stage_sha256 is {want[:12]}, so selections and "
                     "costs differ", f"rebuild it: {STAGE_BUILD}. If the pin is what changed, update stage_sha256 in "
                     f"src/mailroom_eda/labeling/campaigns/{self._campaign_name}/campaign.yaml (owner decision)"))
            add(item("selection", "Documents selected", SKIP, "stage does not match the pin"))
            return
        add(item("stage", "Stage data present and pinned", PASS, f"{path} sha256 {sha[:12]}"))
        plan.rows = rows
        try:
            plan.picked = operate.pick(rows, plan.entry)
        except (SystemExit, KeyError, ValueError) as exc:
            add(item("selection", "Documents could not be selected", FAIL, f"{type(exc).__name__}: {exc}",
                     "check task, set, chunk and subclass in campaign.yaml against "
                     f"`{RUN_CMD} select --task {plan.task} --set {plan.set_name}`"))
            return
        add(item("selection", "Documents selected", PASS if plan.picked else FAIL,
                 f"{len(plan.picked)} documents" if plan.picked else "the selection is empty",
                 "" if plan.picked else "nothing to label here; pick another arm"))

    def _trial_rows(self, req: Request, plan: Plan, items: list[dict[str, str]]) -> None:
        add = items.append
        cls = req.target[6:]
        add(item("stage", "Stage data", SKIP, "trials read the public dataset, not data/v9_4/stage"))
        try:
            hub = self._public()
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            add(item("public_source", "Public dataset could not be downloaded", FAIL,
                     f"{type(exc).__name__}: {exc}",
                     f"check network access to huggingface.co (no token is needed for the public dataset {_repo()}); "
                     "if you are behind a proxy set HTTPS_PROXY, then run the preflight again"))
            add(item("selection", "Documents selected", SKIP, "no public rows"))
            return
        add(item("public_source", "Public dataset downloaded", PASS,
                 f"{_repo()} @ {str(hub['revision'])[:12]}, ground_truth_hardened.jsonl sha256 "
                 f"{str(hub['source_sha256'])[:12]}, {len(hub['rows'])} rows"))
        plan.rows = [trial_row(r) for r in hub["rows"]]  # ids namespaced: public-<tag>:<id>
        plan.source = {"dataset": _repo(), "revision": hub["revision"], "file": "ground_truth_hardened.jsonl",
                       "sha256": hub["source_sha256"]}
        docs_ok = 1 <= req.documents <= TRIAL_MAX_DOCUMENTS
        add(item("documents", "Trial size", PASS if docs_ok else FAIL, f"{req.documents} document(s)",
                 "" if docs_ok else f"choose 1 to {TRIAL_MAX_DOCUMENTS} documents (default {TRIAL_DEFAULT_DOCUMENTS})"))
        if not docs_ok:
            return
        try:
            pool = list(runner.select(plan.rows, plan.task, plan.set_name))
        except (SystemExit, KeyError, ValueError) as exc:
            add(item("selection", "Documents could not be selected", FAIL, f"{type(exc).__name__}: {exc}",
                     f"run `{RUN_CMD} select --task {plan.task} --set {plan.set_name}` and compare"))
            return
        # Nothing here may bill expert gold. The set holds only documents that need labels (contracts: the
        # EX-10 contracts without CUAD labels; mergers: consideration MAUD does not answer), so any gold in it,
        # picked or not, means the selection is wrong: refuse the whole trial.
        gold = [(tasks._ids(r)[0], gold_reason(r)) for r in pool if gold_reason(r)]
        plan.picked = pick_trial(pool, req.documents, req.per_subclass)
        add(item("selection", "Documents selected", PASS if plan.picked else FAIL,
                 f"{len(plan.picked)} of {len(pool)} documents that need labels: "
                 + ", ".join(tasks._ids(r)[0] for r in plan.picked) if plan.picked else
                 f"no {cls} document in the public dataset needs {plan.task} labels",
                 "" if plan.picked else "pick another document type"))
        if gold:
            add(item("gold_free", "The selection contains expert gold", FAIL,
                     "; ".join(f"{d}: {why}" for d, why in gold[:5]) + (f"; and {len(gold) - 5} more" if len(gold) > 5 else ""),
                     "trials never bill CUAD or MAUD gold documents, so nothing was started. This is a selection "
                     f"bug or a changed dataset: run `{RUN_CMD} select --task {plan.task} --set {plan.set_name}` "
                     "and report the document ids above"))
        elif plan.picked:
            add(item("gold_free", "No selected document is CUAD or MAUD gold", PASS,
                     f"checked all {len(pool)} candidates: every one needs labels"))

    def _projection(self, plan: Plan) -> None:
        if not (plan.models and plan.picked):
            return
        est = runner.estimate(plan.picked, plan.task, plan.models, plan.set_name)
        cost = runner.projected_cost(est, plan.models, plan.task, plan.reasoning)
        plan.projection = {"documents": len(plan.picked), "calls": est["calls"] * len(plan.models),
                           "cost_usd": None if cost is None else round(cost, 4)}

    def _cap_rules(self, cap: float | None, plan: Plan, trial: bool, entry: Mapping[str, Any],
                   items: list[dict[str, str]]) -> None:
        add = items.append
        proj = (plan.projection or {}).get("cost_usd")
        if plan.projection is None or proj is None or cap is None:
            why = ("no models or documents to price" if plan.projection is None else
                   "a model has no price in models.yaml" if proj is None else "no valid cap")
            add(item("cap_projection", "Cap covers the projected cost", SKIP, why))
        else:
            need = math.ceil(runner.CAP_HEADROOM_FACTOR * proj * 100) / 100
            ok = cap >= runner.CAP_HEADROOM_FACTOR * proj - 1e-9
            add(item("cap_projection", "Cap covers the projected cost with headroom", PASS if ok else FAIL,
                     f"projected ${proj:.4f}; cap ${cap:.2f} {'>=' if ok else '<'} {runner.CAP_HEADROOM_FACTOR}x "
                     f"projection (${runner.CAP_HEADROOM_FACTOR * proj:.4f}): the cap check reserves each call's "
                     "worst case, so a lower cap stops the run before its last calls",
                     "" if ok else f"raise the cap to at least ${need:.2f}" + (
                         f" (the trial ceiling is ${TRIAL_CAP_CEILING:.2f}: select fewer documents)"
                         if trial and need > TRIAL_CAP_CEILING else "")))
        ceiling = TRIAL_CAP_CEILING if trial else float(entry["cap_usd"])
        label = "trial ceiling" if trial else "campaign.yaml cap_usd"
        if cap is None:
            add(item("cap_ceiling", "Cap is within the ceiling", SKIP, "no valid cap"))
        else:
            ok = cap <= ceiling + 1e-9
            add(item("cap_ceiling", "Cap is within the ceiling", PASS if ok else FAIL,
                     f"cap ${cap:.2f} {'<=' if ok else '>'} {label} ${ceiling:.2f}",
                     "" if ok else f"lower the cap to at most ${ceiling:.2f}" + (
                         "" if trial else " (the arm's cap_usd in campaign.yaml; raising it is an owner decision)")))

    def _key_checks(self, req: Request, plan: Plan, cap: float | None, items: list[dict[str, str]]) -> None:
        add = items.append
        if not plan.models:
            for i, t in (("key_present", "OpenRouter API key"), ("key_valid", "OpenRouter accepts the key"),
                         ("key_credit", "Key has credit for the cap")):
                add(item(i, t, SKIP, "no resolved models"))
            return
        source = ("provided in this request (kept in server memory for this call only)" if req.api_key else
                  "OPENROUTER_API_KEY in the portal's environment" if self.environment().get("OPENROUTER_API_KEY") else "")
        add(item("key_present", "OpenRouter API key", PASS if source else FAIL, source or "no key",
                 "" if source else "export OPENROUTER_API_KEY=... in the shell that starts the portal and restart it, "
                 "or paste a key in the API key field (never stored, cleared after each call). Create one at "
                 "https://openrouter.ai/keys"))
        if not source:
            add(item("key_valid", "OpenRouter accepts the key", SKIP, "no key"))
            add(item("key_credit", "Key has credit for the cap", SKIP, "no key"))
            return
        try:
            backend = providers.Backend.from_env("openrouter", plan.env)
            data = self.key_status(backend)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - network, TLS, proxy and HTTP errors alike
            add(item("key_valid", "OpenRouter did not accept the key", FAIL, f"{type(exc).__name__}: {exc}",
                     "HTTP 401/403: the key is wrong, revoked or disabled; create a new one at "
                     "https://openrouter.ai/keys. A network or TLS error: check your connection, HTTPS_PROXY and "
                     "OPENROUTER_BASE_URL (default https://openrouter.ai/api/v1)"))
            add(item("key_credit", "Key has credit for the cap", SKIP, "the key could not be read"))
            return
        remaining = data.get("limit_remaining")
        add(item("key_valid", "OpenRouter accepts the key", PASS,
                 "no credit limit on this key (the cap alone bounds the run)" if remaining is None else
                 f"${float(remaining):.2f} credit remaining"))
        if cap is None:
            add(item("key_credit", "Key has credit for the cap", SKIP, "no valid cap"))
            return
        try:
            headroom = spend.check_key_headroom(data, cap)
        except spend.SpendError as exc:
            usable = max(0.0, float(remaining) - float(registry.models_registry().get("key_reserve_usd", 3.0)))
            add(item("key_credit", "Key does not have credit for the cap", FAIL, str(exc),
                     f"lower the cap to at most ${usable:.2f}, or add credit at https://openrouter.ai/settings/credits"))
            return
        add(item("key_credit", "Key has credit for the cap", PASS,
                 "unlimited key" if headroom == math.inf else f"headroom ${headroom:.2f} after the reserve"))

    def _route_checks(self, plan: Plan, trial: bool, items: list[dict[str, str]]) -> None:
        add = items.append
        root = Path(self.portal.labels_root)
        if plan.models:
            log = root / "ping.jsonl"
            probs = operate.ping_problems(export.read_journal(log) if log.exists() else [],
                                          list(runner.reasoning_levels(plan.reasoning, plan.models).items()))
            fix = (f"send the exact request this run sends once (under $0.01 per model): `{RUN_CMD} ping --initials XX`"
                   " (needs OPENROUTER_API_KEY)")
            if probs and trial:
                add(item("ping", "Route not pinged in the last 24 h", WARN,
                         "; ".join(probs) + "; a trial does not require it, but a broken route wastes the cap", fix))
            elif probs:
                add(item("ping", "Route not verified", FAIL, "; ".join(probs), fix))
            else:
                add(item("ping", "Route verified by a recent ping", PASS, f"{len(plan.models)} route(s), "
                         f"{operate.PING_MAX_AGE_HOURS} h freshness, strict schema"))
        else:
            add(item("ping", "Route verified by a recent ping", SKIP, "no resolved models"))
        if trial:
            add(item("hf_checkpoint", "HF checkpoint", SKIP,
                     "trials are not checkpointed (their output stays under trials/ and is not uploaded)"))
            return
        try:
            ok, why = checkpoint.write_access()
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            ok, why = False, f"{type(exc).__name__}: {exc}"
        add(item("hf_checkpoint", "HF checkpoint is writable" if ok else "HF checkpoint is not writable",
                 PASS if ok else FAIL, why,
                 "" if ok else "export HF_TOKEN=<a write token for the staging repo's namespace> in the portal's shell "
                 f"and restart it (https://huggingface.co/settings/tokens); paid answers upload to the private "
                 f"{checkpoint.STAGING_REPO} so a lost checkout cannot lose them"))

    # ------------------------------------------------------------------ start
    def start(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Re-run the preflight on the server, then start ONE background run. Raises ``LaunchConflict``
        (HTTP 409) with the failing items when anything fails, the typed confirmation differs from
        the cap, or a run is already in progress."""
        self.require_enabled()
        req = parse_request(body, start=True)
        with self._lock:
            if self._state["state"] == "running":
                raise LaunchConflict("already running: a labeling run is in progress", [item(
                    "idle", "Nothing else is running", FAIL, "a labeling run is already in progress",
                    "wait for it to finish (GET /api/launch/status); only one run at a time")])
        result, plan = self._preflight(req)
        items = result["items"]
        try:
            typed = Decimal((req.confirm or "").strip())
            matches = req.cap_usd is not None and typed == Decimal(repr(req.cap_usd))  # "0.250" confirms 0.25
        except InvalidOperation:
            matches = False
        items.append(item("confirm", "Typed confirmation matches the cap", PASS if matches else FAIL,
                          "confirmed" if matches else f"you typed {(req.confirm or '')[:20]!r}, the cap is {req.cap_text!r}",
                          "" if matches else "type the cap again exactly as entered, e.g. 0.25"))
        failing = [i for i in items if i["status"] == FAIL]
        if failing:
            raise LaunchConflict(f"preflight failed: {len(failing)} check(s) failed, nothing was started: "
                                 + "; ".join(i["title"] for i in failing), items)
        with self._lock:
            if self._state["state"] == "running":
                raise LaunchConflict("already running: a labeling run is in progress")
            self._log.clear()
            self._state.update(state="running", target=self._describe(req, plan), started_utc=_now(),
                               finished_utc=None, result=None, error=None)
            self._thread = threading.Thread(target=self._worker, args=(req, plan), daemon=True,
                                            name="labeling-launch")
            self._thread.start()
        return {"started": True, "projection": plan.projection, "status": self.status()}

    def _describe(self, req: Request, plan: Plan) -> dict[str, Any]:
        return {"id": req.target, "kind": plan.kind, "task": plan.task, "set": plan.set_name,
                "models": plan.models, "reasoning": plan.reasoning, "cap_usd": req.cap_usd,
                "initials": req.initials, "documents": len(plan.picked)}

    def join(self, timeout: float | None = None) -> None:
        """Wait for the background run (tests, shutdown)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def status(self) -> dict[str, Any]:
        """The run state, its log tail and result. ``GET /api/launch/status``."""
        self.require_enabled()
        with self._lock:
            return {**self._state, "log_tail": list(self._log)[-LOG_TAIL:]}

    def _emit(self, line: str, secrets: set[str]) -> None:
        with self._lock:
            self._log.append(redact(line, secrets)[:LINE_CHARS])

    # ------------------------------------------------------------------ worker
    def _worker(self, req: Request, plan: Plan) -> None:
        secrets = plan.secrets
        tee = _Tee(lambda line: self._emit(line, secrets))
        saved = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = tee
        rc: int | None = None
        error = None
        out_dir = None
        try:
            self._receipt(req, plan, "started", secrets)
            if plan.kind == "campaign":
                out_dir = operate.chunk_dir(Path(self.portal.labels_root), plan.entry, plan.arm)
                rc = operate.cmd_chunk(self._namespace(req, plan), chat=self.chat, key_status=self.key_status,
                                       env=plan.env)
            else:
                out_dir = self._trial_dir(plan)
                rc = self._run_trial(req, plan, out_dir)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - the thread must always record an outcome
            msg = exc.code if isinstance(exc, SystemExit) and isinstance(exc.code, str) else \
                f"{type(exc).__name__}: {exc}"
            error = redact(msg, secrets)
        finally:
            tee.finish()
            sys.stdout, sys.stderr = saved
        if error is None and rc != 0:
            error = f"exit code {rc}: {EXIT_MEANING.get(rc, 'the run did not complete')}"
        result = self._result(out_dir, rc)
        with self._lock:
            self._state.update(state="succeeded" if error is None else "failed", finished_utc=_now(),
                               result=result, error=error)
        self._receipt(req, plan, "succeeded" if error is None else "failed", secrets, result=result, error=error)

    def _namespace(self, req: Request, plan: Plan) -> argparse.Namespace:
        """The arguments ``labeling chunk <id> [--arm N]`` would parse to."""
        return argparse.Namespace(
            campaign=self._campaign_name, id=str(plan.entry["id"]), arm=plan.arm, initials=req.initials,
            provider="openrouter", cap_usd=req.cap_usd, workers=4, model_map=[],
            ack_requires=req.ack_requires, dry_run=False, no_checkpoint=False, stage=self.stage_dir,
            out=Path(self.portal.labels_root), no_restore=True)

    def _trial_dir(self, plan: Plan) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return Path(self.portal.labels_root) / "trials" / f"{stamp}_{plan.task}-public"

    def _run_trial(self, req: Request, plan: Plan, out_dir: Path) -> int:
        import json  # noqa: PLC0415

        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "trial.json").write_text(json.dumps({
            "target": req.target, "source": plan.source, "task": plan.task, "set": plan.set_name,
            "models": plan.models, "reasoning": plan.reasoning, "initials": req.initials, "cap_usd": req.cap_usd,
            "documents": [tasks._ids(r)[0] for r in plan.picked],
            "published_document_ids": [r["published_document_id"] for r in plan.picked], "started_utc": _now(),
            "note": "public trial: only documents that need labels; no campaign approval, no HF checkpoint"},
            indent=1, sort_keys=True), encoding="utf-8")
        rc = runner.execute(
            plan.rows, plan.picked, task=plan.task, set_name=plan.set_name, chunk_spec=None, models=plan.models,
            provider="openrouter", reasoning=plan.reasoning, initials=req.initials, cap_usd=req.cap_usd,
            max_wall_seconds=plan.max_wall, out_dir=out_dir, dry_run=False, workers=4,
            chunk_id=f"trial-{plan.task}", chat=self.chat, key_status=self.key_status,
            require_checkpoint=False, env=plan.env, compare=True)
        if not (out_dir / f"{out_dir.name}-review-data.json").exists():
            runner.review(plan.rows, plan.task, out_dir, reasoning=plan.reasoning)
        return rc

    def _result(self, out_dir: Path | None, rc: int | None) -> dict[str, Any]:
        """Spend and packet of a finished run, read back from its own journals."""
        spent, packet = None, None
        if out_dir is not None:
            for rec in reversed(export.read_journal(out_dir / "runs.jsonl")):
                if isinstance(rec.get("result"), dict):
                    spent = rec["result"].get("spent_usd")
                    break
            path = out_dir / f"{out_dir.name}-review-data.json"
            if path.exists():
                root = Path(self.portal.labels_root).resolve()
                packet = str(path.resolve().relative_to(root)) if path.resolve().is_relative_to(root) else str(path)
        return {"spent_usd": spent, "packet": packet, "exit_code": rc,
                "out_dir": None if out_dir is None else str(out_dir)}

    def _receipt(self, req: Request, plan: Plan, outcome: str, secrets: set[str], *,
                 result: Mapping[str, Any] | None = None, error: str | None = None) -> None:
        """Append to ``<portal out>/launches.jsonl``. Never the key; text is redacted again."""
        record = {"utc": _now(), "outcome": outcome, "target": req.target, "kind": plan.kind,
                  "task": plan.task, "set": plan.set_name, "models": plan.models, "reasoning": plan.reasoning,
                  "initials": req.initials, "cap_usd": req.cap_usd, "projection": plan.projection,
                  "spent_usd": (result or {}).get("spent_usd"), "packet": (result or {}).get("packet"),
                  "exit_code": (result or {}).get("exit_code"),
                  **({"source": plan.source} if plan.source else {}),
                  **({"error": redact(error, secrets)[:LINE_CHARS]} if error else {})}
        try:
            export.append_journal(Path(self.portal.out) / "launches.jsonl", record)
        except OSError as exc:
            self._emit(f"could not write the launch receipt: {exc}", secrets)


def _repo() -> str:
    from .. import config  # noqa: PLC0415

    return config.REPO_ID


def _revision() -> str:
    from .. import config  # noqa: PLC0415

    return config.REPO_REVISION
