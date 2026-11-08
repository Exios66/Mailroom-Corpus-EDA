"""Gold smoke fixtures: live labeling answers, recorded once, replayed in every test run.

``tests/fixtures/live_smoke/<doc_type>/`` holds one small, real labeling run per document type
(contract, merger_agreement, corporate_record, insurance_claim, correspondence): the public
documents exactly as downloaded (full text), every model call the run made (prompt hashes,
answers, tokens, cost), and the review-portal packet that run produced. Only documents that
still need labels are ever recorded (the launcher's trial rule): CUAD and MAUD gold is
refused, so nothing already expert-labeled is billed.

* ``record`` is the only paid command. It builds each type's plan exactly as the review
  portal's trial launcher does (same selection, models, reasoning, ``max_tokens``), runs the
  launcher's preflight for it, and aborts before spending if any item fails. ``--plan-only``
  prints what would run and spends nothing (it downloads the public file). A real run needs the
  total cap typed again on stdin.
* ``check`` (offline) is the staleness check: it recomputes what the CURRENT code would send
  (plan, prompt hashes, input hashes, response schema) and compares it with the manifest. A
  mismatch means the recorded answers no longer match the code; it says what changed and the
  command that re-records (``--replace``).
* ``replay`` (offline) pushes the recorded answers back through the real ``runner.execute`` with
  a fake chat keyed by the request actually sent, then diffs the review packet against
  ``expected-review-data.json``. A request the fixture has no answer for is a stale-fixture
  error, never a network call.
* ``verify-hub`` (network, opt-in) re-downloads the public file at the manifest revision and
  confirms each stored document's text hash.

Nothing here writes outside the fixture root and a temporary directory.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence, TextIO

from .. import config
from . import export, launch, providers, runner, spend, tasks

SCHEMA_VERSION = 1
FIXTURE_ROOT = runner.ROOT / "tests" / "fixtures" / "live_smoke"
DOC_TYPES = tuple(launch.TRIAL_TYPES)
CAMPAIGN = "v10"
MODULE = "mailroom_eda.labeling.smoke_fixtures"
RECORD_CMD = f"PYTHONPATH=src .venv/bin/python -m {MODULE}"
DEFAULT_DOCUMENTS = launch.TRIAL_DEFAULT_DOCUMENTS
DEFAULT_CAP_USD = 1.00
#: How the total cap is split between types (by labeling task); they sum to 1.00. A cap that
#: differs, or a subset of types, scales these proportionally.
CAP_WEIGHTS = {"cuad": 0.40, "corporate": 0.20, "merger_consideration": 0.15, "claim_checklist": 0.15,
               "correspondence": 0.10}
FILES = ("manifest.json", "documents.jsonl", "calls.jsonl", "expected-review-data.json", "README.md")
SOURCE_FILE = "ground_truth_hardened.jsonl"
#: The only journal fields a fixture keeps: deterministic, and none can hold a credential or a path.
CALL_KEYS = ("task", "document_id", "filename", "variant", "model", "provider", "reasoning", "temperature",
             "prompt_sha256", "input_sha256", "response", "content", "served_model", "served_provider",
             "used_schema", "finish_reason", "prompt_tokens", "completion_tokens", "reasoning_tokens",
             "cached_tokens", "cost_usd", "error", "reasoning_runaway", "repair_problems")
#: ``calls.jsonl`` fields that hold the model's answer (and so may quote a document): patterns only.
TEXT_KEYS = frozenset({"response", "content", "repair_problems"})
KEY_ITEMS = ("key_present", "key_valid", "key_credit")
REPLAY_ENV = {"OPENROUTER_API_KEY": "replay-fixture-not-a-key"}


class FixtureError(RuntimeError):
    """A fixture is missing a file, unreadable or internally inconsistent."""


class StaleFixture(FixtureError):
    """The code now sends a request the fixture holds no recorded answer for."""


def batch_name(doc_type: str) -> str:
    """The fixed chunk directory name of a recording, so the packet's ``batch`` never varies."""
    return f"smoke-{doc_type}"


def _now() -> str:
    return launch._now()


def _usd(value: float | None) -> str:
    """``0.4`` as ``0.40``; ``0.0375`` as ``0.0375``; ``None`` as ``?``."""
    if value is None:
        return "?"
    whole, _, frac = f"{value:.4f}".rstrip("0").partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def sha256_text(text: str) -> str:
    """SHA-256 hex digest of UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def schema_sha256(schema: Any) -> str:
    """Hash of a task's JSON response schema (canonical JSON)."""
    return sha256_text(json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def combined_schema_sha256(shas: Sequence[str]) -> str:
    """One hash for a fixture's response schemas: the schema's own when every document shares it."""
    unique = sorted(set(shas))
    return unique[0] if len(unique) == 1 else sha256_text(",".join(unique))


def per_type_caps(types: Sequence[str], cap_usd: float) -> dict[str, float]:
    """Split ``cap_usd`` over ``types`` by :data:`CAP_WEIGHTS` (proportionally for any subset)."""
    weights = {t: CAP_WEIGHTS[launch.TRIAL_TYPES[t][0]] for t in types}
    total = sum(weights.values())
    return {t: round(cap_usd * w / total, 6) for t, w in weights.items()}


def default_share(doc_type: str) -> float:
    """The type's share of the default $1.00 total (what re-recording it alone should cap at)."""
    return CAP_WEIGHTS[launch.TRIAL_TYPES[doc_type][0]] * DEFAULT_CAP_USD


# ------------------------------------------------------------------ JSON files

def _line(value: Any, sort_keys: bool = False) -> str:
    """One JSONL line. U+2028, U+2029 and U+0085 are escaped so no reader can split a record."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=sort_keys)
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029").replace("\x85", "\\u0085")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FixtureError(f"{path.name} is missing from {path.parent}")
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError as exc:
            raise FixtureError(f"{path.name} line {n} is not JSON: {exc}") from exc
    return out


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise FixtureError(f"{path.name} is missing from {path.parent}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise FixtureError(f"{path.name} is not JSON: {exc}") from exc


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ normalization

_VOLATILE_KEY = re.compile(r"(_utc|_at|_ts)$")
_DURATION_NAMES = frozenset({"duration", "elapsed", "duration_seconds", "elapsed_seconds"})
_TMP_PATH = re.compile(r"(?:/tmp|/var/folders|/private/var)/[\w./+-]+")
_VERBATIM = frozenset({"full_document_text", "existing_gt_fields", "quote"})


def normalize(value: Any, scrub: Sequence[str] = (), _key: str = "") -> Any:
    """``value`` with volatile content replaced by stable placeholders, for comparing two runs.

    A scalar under a timestamp key (ending ``_utc``/``_at``/``_ts``, or ``timestamp``) becomes
    ``<timestamp>``, under a duration key ``<duration>``; each directory in ``scrub`` and any temp
    path inside a string becomes ``<tmp>``. Document text, quotes and the dataset's own gold
    fields are never touched. Values, statuses, issues, evidence offsets and ``model_outputs`` stay."""
    if _key in _VERBATIM:
        return value
    if isinstance(value, dict):
        return {k: normalize(v, scrub, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, scrub, _key) for v in value]
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        if _key in _DURATION_NAMES:
            return "<duration>"
        if _VOLATILE_KEY.search(_key) or _key == "timestamp":
            return "<timestamp>"
    if isinstance(value, str):
        for root in sorted((s for s in scrub if s), key=len, reverse=True):
            value = value.replace(root, "<tmp>")
        return _TMP_PATH.sub("<tmp>", value)
    return value


def normalize_packet(packet: Mapping[str, Any], scrub: Sequence[str] = ()) -> dict[str, Any]:
    """A packet with volatile content normalized and ``packet_id`` recomputed over its records,
    so a normalized packet still validates (``review_page.validate_packet``)."""
    from .review_page import packet_id  # noqa: PLC0415

    out = normalize(dict(packet), scrub)
    if out != packet:
        out["packet_id"] = packet_id(out["records"])
    return out


def _flat(value: Any, path: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k in sorted(value):
            out.update(_flat(value[k], f"{path}.{k}" if path else str(k)))
        return out or {path: {}}
    if isinstance(value, list):
        out = {}
        for i, v in enumerate(value):
            out.update(_flat(v, f"{path}[{i}]"))
        return out or {path: []}
    return {path: value}


def _short(value: Any, limit: int = 140) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else f"{text[:limit - 1]}… ({len(text)} chars)"


def diff_json(expected: Any, actual: Any, limit: int = 40) -> list[str]:
    """Readable differences between two JSON values: one line per differing leaf path."""
    a, b = _flat(expected), _flat(actual)
    lines = []
    for path in sorted(set(a) | set(b)):
        if path not in b:
            lines.append(f"  {path}: expected {_short(a[path])}, missing from the replay")
        elif path not in a:
            lines.append(f"  {path}: not in the fixture, replay produced {_short(b[path])}")
        elif a[path] != b[path]:
            lines.append(f"  {path}: expected {_short(a[path])}, replay produced {_short(b[path])}")
    if len(lines) > limit:
        lines = lines[:limit] + [f"  … and {len(lines) - limit} more differences"]
    return lines


# ------------------------------------------------------------------ credential scan

_CRED_PATTERNS = (
    ("an OpenRouter key (sk-or-)", re.compile(r"(?<![A-Za-z])sk-or-")),
    ("a bearer token", re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("an Authorization header", re.compile(r"""["']?Authorization["']?\s*[:=]\s*\S""")),
)
_CRED_LITERALS = ("Authorization", "Bearer", "sk-or-")
_PATH_LITERALS = ("/home/", "/Users/", "/tmp/", "C:\\", "/var/folders")


def _scan_value(value: Any, key: str, where: str, out: list[str], *, text: bool, literal: bool) -> None:
    """Walk one JSON value. ``text`` is true under a document-text or model-output key."""
    if isinstance(value, dict):
        for k, v in value.items():
            _scan_value(v, str(k), where, out, text=text or str(k) in TEXT_KEYS, literal=literal)
        return
    if isinstance(value, list):
        for v in value:
            _scan_value(v, key, where, out, text=text, literal=literal)
        return
    if not isinstance(value, str):
        return
    for name, pattern in _CRED_PATTERNS:
        if pattern.search(value):
            out.append(f"{where}: {key!r} holds {name}")
    if literal and not text:  # metadata, never document text: the bare words are not allowed at all
        for lit in _CRED_LITERALS + _PATH_LITERALS:
            if lit in value:
                out.append(f"{where}: {key!r} contains {lit!r}")


def credential_problems(path: Path, secrets: Sequence[str] = ()) -> list[str]:
    """Credentials or local paths in one fixture file (empty when clean).

    Credential patterns (an ``sk-or-`` key, a bearer token, an Authorization header) are refused
    in every file. In the files the recorder writes about itself (``manifest.json``, ``README.md``
    and the metadata fields of ``calls.jsonl``: everything but the model's ``response``/``content``
    and its repair notes) the bare words ``Authorization``, ``Bearer`` and ``sk-or-`` and any local
    path are refused too. The documents, their quotes and the model's answers may legitimately
    contain such words (a contract has an "Authorization" clause), so only the patterns apply there.
    Any actual secret value passed in ``secrets`` is refused anywhere."""
    text = path.read_text(encoding="utf-8")
    out: list[str] = []
    for secret in secrets:
        if len(secret) >= launch.MIN_SECRET_CHARS and secret in text:
            out.append(f"{path.name}: contains a secret value from the environment")
    literal = path.name in ("calls.jsonl", "manifest.json", "README.md")
    if path.suffix == ".jsonl":
        for n, line in enumerate(text.split("\n"), 1):
            if line.strip():
                _scan_value(json.loads(line), "", f"{path.name} line {n}", out, text=False, literal=literal)
    elif path.suffix == ".json":
        _scan_value(json.loads(text), "", path.name, out, text=False, literal=literal)
    else:
        for name, pattern in _CRED_PATTERNS:
            if pattern.search(text):
                out.append(f"{path.name}: holds {name}")
        if literal:
            out += [f"{path.name}: contains {lit!r}" for lit in _CRED_LITERALS + _PATH_LITERALS if lit in text]
    return out


def fixture_credential_problems(directory: Path, secrets: Sequence[str] = ()) -> list[str]:
    """:func:`credential_problems` over every file of one fixture directory."""
    out: list[str] = []
    for name in FILES:
        if (directory / name).is_file():
            out += credential_problems(directory / name, secrets)
    return out


# ------------------------------------------------------------------ loading a fixture

@dataclass
class Fixture:
    """A fixture directory, read."""

    path: Path
    manifest: dict[str, Any]
    raw_rows: list[dict[str, Any]]
    calls: list[dict[str, Any]]
    state: dict[str, Any] | None = None

    @property
    def doc_type(self) -> str:
        return str(self.manifest["doc_type"])

    def rows(self) -> list[dict[str, Any]]:
        """The stored documents as a trial reads them: ``gt_fields`` decoded, id namespaced."""
        return [launch.trial_row(launch._decode_gt(dict(r))) for r in self.raw_rows]


def fixture_dirs(root: Path, types: Sequence[str] | None = None) -> list[Path]:
    """Existing fixture directories under ``root``, in document-type order."""
    return [root / t for t in (types or DOC_TYPES) if (root / t).is_dir()]


def load_fixture(path: Path) -> Fixture:
    """Read a fixture directory; :class:`FixtureError` when a file is missing or malformed."""
    path = Path(path)
    missing = [n for n in FILES if not (path / n).is_file()]
    if missing:
        raise FixtureError(f"fixture {path.name} is missing {', '.join(missing)}")
    manifest = _read_json(path / "manifest.json")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise FixtureError(f"fixture {path.name}: manifest schema_version {manifest.get('schema_version')!r}, "
                           f"this code reads {SCHEMA_VERSION}")
    return Fixture(path, manifest, _read_jsonl(path / "documents.jsonl"), _read_jsonl(path / "calls.jsonl"))


# ------------------------------------------------------------------ the plan (launcher's, exactly)

def download_public(revision: str | None = None) -> dict[str, Any]:
    """The public production rows at ``revision`` (default: the pinned one) with the file's sha256."""
    from .. import hf_interface  # noqa: PLC0415
    from .review_production import load_production  # noqa: PLC0415

    return load_production(hf_interface.get_hf_api(token=None), revision)


def _plain_launcher(work: Path | None = None, **seams: Any) -> launch.Launcher:
    """A launcher over a stand-in portal: all it needs is the campaign name and a labels root."""
    work = work or Path(tempfile.gettempdir())
    portal = SimpleNamespace(campaign=CAMPAIGN, labels_root=work / "labels", stage=None, out=work / "portal")
    return launch.Launcher(portal, enabled=True, **seams)


@dataclass
class TypePlan:
    """What one document type would run: the launcher's plan plus its preflight."""

    doc_type: str
    task: str
    set_name: str
    cap_usd: float
    plan: launch.Plan
    items: list[dict[str, str]]
    raw: list[dict[str, Any]]
    source: dict[str, Any]

    @property
    def models(self) -> list[str]:
        return list(self.plan.models)

    @property
    def levels(self) -> dict[str, str]:
        return runner.reasoning_levels(self.plan.reasoning, self.plan.models) if self.plan.models else {}

    @property
    def max_tokens(self) -> dict[str, int]:
        return {m: runner.max_tokens(self.task, lvl, m) for m, lvl in self.levels.items()}

    @property
    def fails(self) -> list[dict[str, str]]:
        return [i for i in self.items if i["status"] == launch.FAIL]

    def item(self, item_id: str) -> dict[str, str] | None:
        return next((i for i in self.items if i["id"] == item_id), None)

    @property
    def gold(self) -> str:
        """The gold check: the preflight's ``gold_free`` verdict, and each selected document again."""
        found = [f"{tasks._ids(r)[0]}: {launch.gold_reason(r)}" for r in self.plan.picked
                 if launch.gold_reason(r)]
        if found:
            return "FAIL: " + "; ".join(found)
        gold = self.item("gold_free")
        return f"{gold['status']}: {gold['detail']}" if gold else "not checked (no selection)"

    @property
    def documents(self) -> list[dict[str, Any]]:
        out = []
        for r in self.plan.picked:
            text = str(r["doc_text"])
            out.append({"document_id": r["document_id"], "published_document_id": r["published_document_id"],
                        "subclass": str(r.get("expected_subclass") or ""), "split": str(r.get("split") or ""),
                        "text_sha256": sha256_text(text), "chars": len(text)})
        return out

    @property
    def projection(self) -> dict[str, Any]:
        return self.plan.projection or {"documents": len(self.plan.picked), "calls": None, "cost_usd": None}


def build_plans(types: Sequence[str], *, initials: str, cap_usd: float, documents: int, launcher: launch.Launcher,
                downloaded: Mapping[str, Any], keys: bool = True) -> list[TypePlan]:
    """Run the launcher's preflight for each type's public trial and collect its plan.

    The launcher's trial cap ceiling does not apply: the recorder splits its own total cap per
    type (:data:`CAP_WEIGHTS`), so a failed ``cap_ceiling`` item is dropped. With ``keys`` False
    (``--plan-only``) the OpenRouter key items are dropped as well: nothing is sent. ``downloaded``
    is filled by the launcher's hub seam (:func:`_capturing_hub`) with the rows as downloaded."""
    caps = per_type_caps(types, cap_usd)
    plans = []
    for t in types:
        body = {"target": f"trial:{t}", "initials": initials, "cap_usd": caps[t], "documents": documents}
        result, plan = launcher._preflight(launch.parse_request(body, start=False))
        items = [i for i in result["items"] if not (i["id"] == "cap_ceiling" and i["status"] == launch.FAIL)]
        if not keys:
            items = [i for i in items if i["id"] not in KEY_ITEMS]
        published = {str(r.get("document_id") or r.get("id") or r["filename"]): r
                     for r in downloaded.get("rows", [])}
        raw = [published[str(p["published_document_id"])] for p in plan.picked
               if str(p["published_document_id"]) in published]
        plans.append(TypePlan(t, plan.task or launch.TRIAL_TYPES[t][0], plan.set_name or launch.TRIAL_TYPES[t][1],
                              caps[t], plan, items, raw, plan.source or {"revision": downloaded.get("revision", "")}))
    return plans


def render_plan(plans: Sequence[TypePlan], cap_usd: float) -> str:
    """The text a human approves before the paid run."""
    lines = []
    calls = cost = 0
    priced = True
    for tp in plans:
        proj = tp.projection
        lines.append(f"== {tp.doc_type} ({tp.task} / {tp.set_name}) ==")
        if not tp.plan.picked:
            lines.append("  no documents selected")
        for d in tp.documents:
            lines.append(f"  document {d['document_id']}  published as {d['published_document_id']}  "
                         f"subclass {d['subclass'] or '-'}  {d['chars']:,} chars")
            lines.append(f"    text sha256 {d['text_sha256']}")
        lines.append("  models: " + ("; ".join(f"{m} reasoning {tp.levels[m]} max_tokens {tp.max_tokens[m]}"
                                               for m in tp.models) or "none resolved"))
        lines.append(f"  projected: {proj['calls'] if proj['calls'] is not None else '?'} calls, "
                     f"~${_usd(proj['cost_usd'])}; cap ${_usd(tp.cap_usd)} "
                     f"(the cap must be at least {runner.CAP_HEADROOM_FACTOR}x the projection)")
        lines.append(f"  gold check: {tp.gold}")
        counts = {s: sum(i["status"] == s for i in tp.items)
                  for s in (launch.PASS, launch.WARN, launch.FAIL, launch.SKIP)}
        lines.append("  preflight: " + ", ".join(f"{n} {s}" for s, n in counts.items()))
        for i in tp.items:
            if i["status"] == launch.WARN:
                lines.append(f"    warn: {i['title']}: {i['detail']}")
        if proj["calls"] is not None:
            calls += proj["calls"]
        if proj["cost_usd"] is None:
            priced = False
        else:
            cost += proj["cost_usd"]
    lines.append(f"total: {len(plans)} type(s), {calls} calls, projected ~${_usd(cost) if priced else '?'}, "
                 f"caps sum ${_usd(sum(tp.cap_usd for tp in plans))} of the ${_usd(cap_usd)} total cap")
    return "\n".join(lines)


def render_failures(plans: Sequence[TypePlan]) -> str:
    """Every failing preflight item of every type, each with its fix."""
    lines = []
    for tp in plans:
        for i in tp.fails:
            lines.append(f"FAIL [{tp.doc_type}] {i['title']}: {i['detail']}\n     fix: {i['fix']}")
    return "\n".join(lines)


# ------------------------------------------------------------------ recording

def _git_state() -> tuple[str, bool]:
    """``(commit, dirty)`` of the checkout the recorder runs from; ``("unknown", False)`` outside git."""
    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", *args], cwd=runner.ROOT, capture_output=True, text=True, timeout=10,
                                  check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    commit = (git("rev-parse", "HEAD") or "").strip()
    return (commit, bool((git("status", "--porcelain") or "").strip())) if commit else ("unknown", False)


def prompt_entries(rows: Sequence[Mapping[str, Any]], task: str, set_name: str,
                   models: Sequence[str]) -> list[dict[str, str]]:
    """prompt, input and schema hashes for every (document, model, variant), as the CURRENT code builds them."""
    out = []
    for r in rows:
        for v in runner._variants(task):
            req = tasks.build(task, r, v, set_name=set_name)
            for m in models:
                out.append({"document_id": req.document_id, "published_document_id": str(r["published_document_id"]),
                            "model": m, "variant": v, "prompt_sha256": req.prompt_sha256,
                            "input_sha256": tasks.input_sha256(req.messages),
                            "schema_sha256": schema_sha256(req.schema)})
    return out


def _kept_calls(calls: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{k: c[k] for k in CALL_KEYS if k in c} for c in calls]


def _call_counts(calls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    spent: dict[str, float] = {}
    for c in calls:
        if c.get("cost_usd") is not None:
            spent[c["model"]] = round(spent.get(c["model"], 0.0) + float(c["cost_usd"]), 6)
    return {"answered": sum(1 for c in calls if not c.get("error")),
            "repairs": sum(1 for c in calls if c.get("variant") == "repair" and not c.get("error")),
            "errors": sum(1 for c in calls if c.get("error")),
            "reasoning_runaways": sum(1 for c in calls if c.get("reasoning_runaway")),
            "spent_by_model": spent, "spent_usd": round(sum(spent.values()), 6)}


def readme_text(manifest: Mapping[str, Any]) -> str:
    """The one-paragraph README of a fixture."""
    docs = ", ".join(d["published_document_id"] for d in manifest["documents"])
    c = manifest["calls"]
    dirty = " (uncommitted changes)" if manifest.get("recorder_tree_dirty") else ""
    return (
        f"# {manifest['doc_type']} gold smoke fixture\n\n"
        f"A real labeling run on {len(manifest['documents'])} public document(s) of type `{manifest['doc_type']}` "
        f"({docs}; dataset `{manifest['dataset']['repo']}` at `{manifest['dataset']['revision'][:12]}`), task "
        f"`{manifest['task']}` / set `{manifest['set']}`, with {', '.join(manifest['models'])}: the documents as "
        f"downloaded (`documents.jsonl`), every model call (`calls.jsonl`) and the review packet the run produced "
        f"(`expected-review-data.json`). Recorded {manifest['recorded_utc']} by {manifest['initials']} at commit "
        f"`{manifest['recorder_commit'][:12]}`{dirty} for ${manifest['spent_usd']:.4f} ({c['answered']} answered, "
        f"{c['errors']} failed calls). Tests replay it offline; it goes stale when a prompt, model, reasoning level, "
        f"the dataset revision or a document changes: `{RECORD_CMD} check` says what changed, and "
        f"`{RECORD_CMD} record --types {manifest['doc_type']} --initials XX --cap-usd "
        f"{_usd(default_share(manifest['doc_type']))} --replace` re-records it (paid; see "
        f"`docs/labeler-review-viewer.md`, \"Gold smoke fixtures\").\n")


@dataclass
class Recorded:
    """The outcome of one type's paid run, before it is written."""

    tp: TypePlan
    calls: list[dict[str, Any]]
    packet: dict[str, Any]
    exit_code: int
    work: Path


def run_type(tp: TypePlan, *, initials: str, chat: Callable, key_status: Callable, work: Path) -> Recorded:
    """The paid run of one type: ``runner.execute`` as the launcher's trial calls it (one worker, so
    the journal order, and with it which model is A and which is B, is the same every time)."""
    plan = tp.plan
    out_dir = work / tp.doc_type / batch_name(tp.doc_type)
    out_dir.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = runner.execute(
                plan.rows, plan.picked, task=plan.task, set_name=plan.set_name, chunk_spec=None, models=plan.models,
                provider="openrouter", reasoning=plan.reasoning, initials=initials, cap_usd=tp.cap_usd,
                max_wall_seconds=plan.max_wall, out_dir=out_dir, dry_run=False, workers=1,
                chunk_id=f"trial-{plan.task}", chat=chat, key_status=key_status, require_checkpoint=False,
                env=plan.env, compare=True, reuse_from=[])
            packet_path = out_dir / f"{out_dir.name}-review-data.json"
            if not packet_path.exists():
                note = runner.review(plan.rows, plan.task, out_dir, reasoning=plan.reasoning)
                if not packet_path.exists():
                    raise FixtureError(f"no review packet: {note.get('review_packet_error', 'not written')}")
    except SystemExit as exc:
        raise FixtureError(f"{tp.doc_type}: the run refused to start: {exc.code or exc}") from exc
    finally:
        log = buf.getvalue()
        if log.strip():
            (work / tp.doc_type / "run.log").write_text(log, encoding="utf-8")
    return Recorded(tp, export.read_journal(out_dir / "calls.jsonl"),
                    json.loads(packet_path.read_text(encoding="utf-8")), rc, work / tp.doc_type)


def build_manifest(rec: Recorded, *, initials: str, recorded_utc: str, commit: tuple[str, bool],
                   total_cap: float) -> dict[str, Any]:
    """The manifest of a recording."""
    tp = rec.tp
    prompts = prompt_entries(tp.plan.picked, tp.task, tp.set_name, tp.models)
    counts = _call_counts(rec.calls)
    return {
        "schema_version": SCHEMA_VERSION, "doc_type": tp.doc_type, "task": tp.task, "set": tp.set_name,
        "dataset": {"repo": config.REPO_ID, "revision": tp.source.get("revision", config.REPO_REVISION),
                    "tag": config.REPO_TAG, "file": SOURCE_FILE, "file_sha256": tp.source.get("sha256", "")},
        "models": tp.models, "reasoning": tp.levels, "max_tokens": tp.max_tokens,
        "prompts": prompts, "response_schema_sha256": combined_schema_sha256([p["schema_sha256"] for p in prompts]),
        "documents": tp.documents, "recorded_utc": recorded_utc, "initials": initials,
        "recorder_commit": commit[0], "recorder_tree_dirty": commit[1],
        "spent_usd": counts["spent_usd"], "spent_by_model": counts["spent_by_model"],
        "calls": {k: counts[k] for k in ("answered", "repairs", "errors", "reasoning_runaways")},
        "exit_code": rec.exit_code,
        "cap_usd": {"this_type": tp.cap_usd, "total_requested": total_cap}}


def write_fixture(root: Path, rec: Recorded, manifest: Mapping[str, Any], *, replace: bool,
                  secrets: Sequence[str] = ()) -> Path:
    """Write one fixture directory under ``root`` atomically (a sibling directory, then a rename).

    :class:`FixtureError` if it exists and ``replace`` is false, or if a file would carry a credential
    or a local path; nothing is left behind then."""
    doc_type = rec.tp.doc_type
    final = Path(root) / doc_type
    if final.exists() and not replace:
        raise FixtureError(f"{final} already exists; pass --replace to record it again")
    root.mkdir(parents=True, exist_ok=True)
    staging, backup = root / f".{doc_type}.writing", root / f".{doc_type}.replaced"
    for p in (staging, backup):
        shutil.rmtree(p, ignore_errors=True)
    staging.mkdir()
    try:
        _write_json(staging / "manifest.json", manifest)
        (staging / "documents.jsonl").write_text("".join(_line(r) + "\n" for r in rec.tp.raw), encoding="utf-8")
        (staging / "calls.jsonl").write_text(
            "".join(_line(c, sort_keys=True) + "\n" for c in _kept_calls(rec.calls)), encoding="utf-8")
        _write_json(staging / "expected-review-data.json", normalize_packet(rec.packet, [str(rec.work)]))
        (staging / "README.md").write_text(readme_text(manifest), encoding="utf-8")
        problems = fixture_credential_problems(staging, secrets)
        if problems:
            raise FixtureError("refusing to write a fixture that holds credentials or local paths:\n  "
                               + "\n  ".join(problems))
        if final.exists():
            os.replace(final, backup)
        os.replace(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and not final.exists():
            os.replace(backup, final)
        raise
    shutil.rmtree(backup, ignore_errors=True)
    return final


def record(types: Sequence[str], *, initials: str, cap_usd: float, documents: int = DEFAULT_DOCUMENTS,
           plan_only: bool = False, replace: bool = False, root: Path = FIXTURE_ROOT,
           hub: Callable[[str | None], Mapping[str, Any]] | None = None, chat: Callable | None = None,
           key_status: Callable | None = None, env: Mapping[str, str] | None = None,
           stdin: TextIO | None = None, out: TextIO | None = None) -> int:
    """Plan, preflight, confirm, run and write fixtures. Exit code: 0 recorded (or planned), 1 refused,
    2 a type stopped at its cap or the wall limit (nothing written for it), 3 recorded with failed calls."""
    out = out or sys.stdout
    say = lambda text="": print(text, file=out)  # noqa: E731
    hub = hub or download_public
    stdin = stdin or sys.stdin
    root = Path(root)
    if not types or any(t not in DOC_TYPES for t in types):
        say(f"refused: --types must be some of {', '.join(DOC_TYPES)}")
        return 1
    if not (cap_usd > 0 and cap_usd < float("inf")):
        say("refused: --cap-usd must be a positive number")
        return 1
    existing = [t for t in types if (root / t).exists()]
    if existing and not replace and not plan_only:
        say(f"refused: fixture(s) already recorded: {', '.join(existing)}. Pass --replace to record them again "
            "(the old fixture is kept until the new one is written). Nothing was spent.")
        return 1
    env = dict(os.environ if env is None else env)
    if plan_only:  # nothing is sent: a stand-in key keeps the preflight's key items from failing for it
        env.setdefault("OPENROUTER_API_KEY", "plan-only")
        key_status = lambda backend: {"limit_remaining": None}  # noqa: E731
    with tempfile.TemporaryDirectory(prefix="smoke-plan-") as plan_tmp:
        work = Path(plan_tmp)
        (work / "labels").mkdir()
        ping = runner.DEFAULT_OUT / "ping.jsonl"
        if ping.is_file():  # read-only: the preflight warns when the route was not pinged lately
            shutil.copyfile(ping, work / "labels" / "ping.jsonl")
        downloaded: dict[str, Any] = {}
        launcher = _plain_launcher(work, chat=chat, key_status=key_status, env=env,
                                   hub=_capturing_hub(hub, downloaded))
        try:
            plans = build_plans(types, initials=initials, cap_usd=cap_usd, documents=documents,
                                launcher=launcher, downloaded=downloaded, keys=not plan_only)
        except ValueError as exc:
            say(f"refused: {exc}")
            return 1
    say(f"Gold smoke fixtures: {len(plans)} type(s), dataset {config.REPO_ID} @ "
        f"{str((plans[0].source or {}).get('revision', ''))[:12]}")
    say(render_plan(plans, cap_usd))
    if any(tp.fails for tp in plans):
        say("")
        say(render_failures(plans))
        say("\nrefused: the preflight failed. Nothing was sent and nothing was spent.")
        return 1
    if plan_only:
        say("\nplan only: nothing was sent and nothing was spent.")
        return 0
    say(f"\nThis sends paid requests, up to ${_usd(sum(tp.cap_usd for tp in plans))} in total.")
    say(f"Type the total cap again ({_usd(cap_usd)}) to confirm: ")
    typed = (stdin.readline() or "").strip()
    try:
        confirmed = Decimal(typed) == Decimal(repr(float(cap_usd)))
    except InvalidOperation:
        confirmed = False
    if not confirmed:
        say(f"refused: you typed {typed[:20]!r}, the total cap is {_usd(cap_usd)}. Nothing was spent.")
        return 1
    return _run_all(plans, initials=initials, cap_usd=cap_usd, replace=replace, root=root,
                    chat=launcher.chat, key_status=launcher.key_status, env=env, say=say)


def _capturing_hub(hub: Callable[[str | None], Mapping[str, Any]],
                   downloaded: dict[str, Any]) -> Callable[[], Mapping[str, Any]]:
    """The launcher's hub seam: download once, and keep the rows exactly as downloaded in ``downloaded``
    (the launcher itself decodes ``gt_fields`` on copies)."""
    def seam() -> Mapping[str, Any]:
        got = dict(hub(None))
        downloaded.update(rows=[dict(r) for r in got["rows"]], revision=got["revision"],
                          source_sha256=got["source_sha256"])
        return got
    return seam


def _run_all(plans: Sequence[TypePlan], *, initials: str, cap_usd: float, replace: bool,
             root: Path, chat: Callable, key_status: Callable, env: Mapping[str, str],
             say: Callable[[str], None]) -> int:
    commit = _git_state()
    secrets = [env.get(k, "") for k in ("OPENROUTER_API_KEY", "VLLM_API_KEY", "HF_TOKEN")]
    worst = 0
    for tp in plans:
        work = Path(tempfile.mkdtemp(prefix=f"smoke-{tp.doc_type}-"))
        keep = True
        try:
            say(f"\nrecording {tp.doc_type} ...")
            try:
                rec = run_type(tp, initials=initials, chat=chat, key_status=key_status, work=work)
            except Exception as exc:  # noqa: BLE001 - report it and go on; the work directory is kept
                say(f"FAILED {tp.doc_type}: {type(exc).__name__}: {exc}\n  work directory (kept): {work}")
                worst = max(worst, 1)
                continue
            calls = _call_counts(rec.calls)
            say(f"  exit {rec.exit_code}; {calls['answered']} answered, {calls['errors']} failed, "
                f"{calls['reasoning_runaways']} reasoning runaway(s), spent ${calls['spent_usd']:.4f} "
                f"of the ${_usd(tp.cap_usd)} cap")
            if rec.exit_code == 2:
                say(f"  NOT WRITTEN: the run stopped at its cap or wall limit, so the recording is incomplete. "
                    f"The paid answers are in {rec.work} (kept).")
                worst = max(worst, 2)
                continue
            manifest = build_manifest(rec, initials=initials, recorded_utc=_now(), commit=commit, total_cap=cap_usd)
            try:
                path = write_fixture(root, rec, manifest, replace=replace, secrets=secrets)
            except FixtureError as exc:
                say(f"NOT WRITTEN {tp.doc_type}: {exc}\n  the paid answers are in {rec.work} (kept).")
                worst = max(worst, 1)
                continue
            say(f"  wrote {path}")
            problems = verify_written(path)
            if problems:
                say(f"  WARNING: the new fixture does not pass its own checks:\n" + "\n".join(problems))
                worst = max(worst, 1)
            if rec.exit_code == 3:
                say(f"  WARNING: {calls['errors']} call(s) failed and are part of this recording; "
                    "review it before committing, or record again.")
                worst = max(worst, 3)
            keep = False
        finally:
            if not keep:
                shutil.rmtree(work, ignore_errors=True)
    return worst


def verify_written(path: Path) -> list[str]:
    """Lines describing why a freshly written fixture fails ``check`` or ``replay`` (empty when it passes)."""
    out: list[str] = []
    try:
        mismatches, _ = check_fixture(path)
        out += [f"  check: {m.field} was {_short(m.recorded)}, now {_short(m.current)}" for m in mismatches]
        diff = replay_fixture(path)
        out += [f"  replay: {line.strip()}" for line in diff]
    except FixtureError as exc:
        out.append(f"  {exc}")
    return out


# ------------------------------------------------------------------ check (staleness)

@dataclass
class Mismatch:
    """One way the current code differs from a recording."""

    field: str
    recorded: Any
    current: Any


def current_state(fx: Fixture) -> dict[str, Any]:
    """What the CURRENT code would run for this fixture's document type: the trial plan's task, set,
    models, per-model reasoning and ``max_tokens``, and the cost to re-record it."""
    t = fx.doc_type
    if t not in launch.TRIAL_TYPES:
        raise FixtureError(f"fixture {fx.path.name}: unknown document type {t!r}")
    task, set_name = launch.TRIAL_TYPES[t]
    launcher = _plain_launcher()
    entry = launcher._trial_entry(t)
    models, reasoning, resolved, _ = launcher._trial_config(t, entry)
    levels = runner.reasoning_levels(reasoning, models) if resolved and models else {}
    return {"task": task, "set": set_name, "models": models, "reasoning": levels,
            "max_tokens": {m: runner.max_tokens(task, lvl, m) for m, lvl in levels.items()}}


def projected_cost(fx: Fixture, state: Mapping[str, Any]) -> float | None:
    """The projected cost of re-recording ``fx`` with the current plan (documents from the fixture)."""
    try:
        picked = fx.rows()
        est = runner.estimate(picked, state["task"], state["models"], state["set"])
        cost = runner.projected_cost(est, state["models"], state["task"], state["reasoning"])
        return None if cost is None else round(cost, 4)
    except Exception:  # noqa: BLE001 - a projection is a hint, never a reason to hide the staleness
        return None


def check_fixture(path: Path) -> tuple[list[Mismatch], Fixture]:
    """Compare a fixture with what the current code would send. Offline."""
    fx = load_fixture(path)
    m = fx.manifest
    out: list[Mismatch] = []

    def compare(name: str, recorded: Any, current: Any) -> None:
        if recorded != current:
            out.append(Mismatch(name, recorded, current))

    ds = m.get("dataset") or {}
    compare("dataset revision", ds.get("revision"), config.REPO_REVISION)
    compare("dataset repo", ds.get("repo"), config.REPO_ID)
    compare("dataset tag", ds.get("tag"), config.REPO_TAG)
    state = current_state(fx)
    for key in ("task", "set", "models", "reasoning", "max_tokens"):
        compare(key, m.get(key), state[key])
    # documents: the stored text against the manifest, and the gold rule on what is stored
    stored = {str(r.get("document_id") or r.get("id") or r["filename"]): r for r in fx.raw_rows}
    listed = {d["published_document_id"]: d for d in m["documents"]}
    compare("documents", sorted(listed), sorted(stored))
    rows = {}
    for d in m["documents"]:
        raw = stored.get(d["published_document_id"])
        if raw is None:
            continue
        compare(f"text sha256 of {d['published_document_id']}", d["text_sha256"],
                sha256_text(str(raw.get("doc_text") or "")))
        row = launch.trial_row(launch._decode_gt(dict(raw)))
        rows[d["published_document_id"]] = row
        reason = launch.gold_reason(row)
        if reason:
            out.append(Mismatch(f"gold check of {d['published_document_id']}", "not CUAD or MAUD gold", reason))
    # prompts: rebuilt from the stored documents with today's templates, registry and schemas
    calls = {(c.get("document_id"), c.get("model"), c.get("variant") or "", c.get("prompt_sha256")): c
             for c in fx.calls}
    schemas = []
    for p in m["prompts"]:
        row = rows.get(p["published_document_id"])
        if row is None:
            continue
        label = f"({p['published_document_id']}, {p['model']}, {p['variant'] or '-'})"
        try:
            req = tasks.build(m["task"], row, p["variant"], set_name=m["set"])
        except Exception as exc:  # noqa: BLE001
            out.append(Mismatch(f"prompt for {label}", p["prompt_sha256"],
                                f"cannot be built: {type(exc).__name__}: {exc}"))
            continue
        schemas.append(schema_sha256(req.schema))
        compare(f"prompt_sha256 {label}", p["prompt_sha256"], req.prompt_sha256)
        compare(f"input_sha256 {label}", p["input_sha256"], tasks.input_sha256(req.messages))
        compare(f"response schema sha256 {label}", p["schema_sha256"], schema_sha256(req.schema))
        recorded_call = calls.get((p["document_id"], p["model"], p["variant"], p["prompt_sha256"]))
        if recorded_call is None:
            out.append(Mismatch(f"recorded answer for {label}", "an answer in calls.jsonl", "none"))
        elif recorded_call.get("input_sha256") != p["input_sha256"]:
            out.append(Mismatch(f"input_sha256 of the recorded call {label}", p["input_sha256"],
                                recorded_call.get("input_sha256")))
    if schemas:
        compare("response schema sha256", m["response_schema_sha256"], combined_schema_sha256(schemas))
    fx.state = state
    return out, fx


def stale_message(fx: Fixture, mismatch: Mismatch) -> str:
    """The loud message for one mismatch."""
    state = fx.state or current_state(fx)
    cost = projected_cost(fx, state)
    return (f"Gold fixture {fx.doc_type} is stale: {mismatch.field} was {_short(mismatch.recorded)}, "
            f"now {_short(mismatch.current)}. The recorded model answers no longer match what the code "
            f"would send. Re-record with: {RECORD_CMD} record --types {fx.doc_type} --initials XX "
            f"--cap-usd {_usd(default_share(fx.doc_type))} --replace (projected ~${_usd(cost)})")


def check(types: Sequence[str] | None = None, *, root: Path = FIXTURE_ROOT, out: TextIO | None = None) -> int:
    """Check each recorded fixture; exit 1 on any stale or unreadable fixture."""
    out = out or sys.stdout
    say = lambda text="": print(text, file=out)  # noqa: E731
    wanted = list(types or DOC_TYPES)
    found = fixture_dirs(Path(root), wanted)
    if types and len(found) != len(wanted):
        missing = [t for t in wanted if not (Path(root) / t).is_dir()]
        say(f"no fixture recorded for: {', '.join(missing)}")
        return 1
    if not found:
        say("no live smoke fixtures recorded yet; run smoke_fixtures record")
        return 0
    bad = 0
    for path in found:
        try:
            mismatches, fx = check_fixture(path)
        except FixtureError as exc:
            say(f"Gold fixture {path.name} is unreadable: {exc}")
            bad += 1
            continue
        if not mismatches:
            say(f"ok: {fx.doc_type} is current ({len(fx.manifest['prompts'])} prompt hashes, "
                f"{len(fx.manifest['documents'])} document hashes)")
            continue
        bad += 1
        say(stale_message(fx, mismatches[0]))
        for extra in mismatches[1:6]:
            say(f"  also stale: {extra.field} was {_short(extra.recorded)}, now {_short(extra.current)}")
        if len(mismatches) > 6:
            say(f"  … and {len(mismatches) - 6} more stale fields")
    return 1 if bad else 0


# ------------------------------------------------------------------ replay

class RecordedChat:
    """A chat that answers from a fixture's journal and never touches the network.

    The request a worker sends is identified by its model and the hash of the exact messages
    (``input_sha256``, which covers the prompt templates and the document text); that finds the
    recorded call, which carries its (document, variant, prompt_sha256). A request the fixture holds
    no answer for raises :class:`StaleFixture`."""

    def __init__(self, fx: Fixture):
        self.fx, self.sent = fx, []
        self.by_input = {(c["model"], c["input_sha256"]): c for c in fx.calls
                         if c.get("input_sha256") and ("response" in c or c.get("error"))}
        self.max_tokens = fx.manifest["max_tokens"]

    def stale(self, why: str) -> StaleFixture:
        t = self.fx.doc_type
        return StaleFixture(f"Gold fixture {t} is stale: {why}. The recorded model answers no longer match what "
                            f"the code would send. Re-record with: {RECORD_CMD} record --types {t} --initials XX "
                            f"--cap-usd {_usd(default_share(t))} --replace")

    def __call__(self, backend: Any, model: str, messages: Sequence[Mapping[str, str]], schema: Any, *,
                 reasoning: str = "off", max_tokens: int = 4096) -> providers.Completion:
        key = (model, tasks.input_sha256(messages))
        call = self.by_input.get(key)
        if call is None:
            raise self.stale(f"the code sent {model} a request (input_sha256 {key[1][:12]}) "
                             "that has no recorded answer")
        if call.get("reasoning") != reasoning:
            raise self.stale(f"reasoning for {model} was {call.get('reasoning')!r}, now {reasoning!r}")
        if self.max_tokens.get(model) != max_tokens:
            raise self.stale(f"max_tokens for {model} was {self.max_tokens.get(model)}, now {max_tokens}")
        self.sent.append((call.get("document_id"), model, call.get("variant") or "", call.get("prompt_sha256")))
        if "response" in call and call.get("response") is not None:
            return providers.Completion(
                call.get("content", ""), call["response"], call.get("served_model", model),
                call.get("served_provider", ""), int(call.get("prompt_tokens") or 0),
                int(call.get("completion_tokens") or 0), int(call.get("reasoning_tokens") or 0),
                call.get("cost_usd"), str(call.get("finish_reason") or ""), bool(call.get("used_schema")),
                int(call.get("cached_tokens") or 0))
        usage = None
        if call.get("prompt_tokens") is not None:  # a failed call the provider billed
            usage = {k: call[k] for k in ("prompt_tokens", "completion_tokens", "cost_usd", "finish_reason",
                                          "reasoning_tokens") if call.get(k) is not None}
        raise providers.ProviderError(str(call.get("error")), usage)


def replay_fixture(path: Path) -> list[str]:
    """Replay a fixture's answers through the real runner and review, then diff the normalized packet
    against ``expected-review-data.json``. Returns the difference lines (empty when identical).
    Raises :class:`StaleFixture` when the code asks for an answer the fixture does not hold."""
    fx = load_fixture(path)
    m = fx.manifest
    chat = RecordedChat(fx)
    rows = fx.rows()
    with tempfile.TemporaryDirectory(prefix="smoke-replay-") as tmp:
        out_dir = Path(tmp) / batch_name(fx.doc_type)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = runner.execute(
                    rows, rows, task=m["task"], set_name=m["set"], chunk_spec=None, models=m["models"],
                    provider="openrouter", reasoning=runner.reasoning_spec(m["reasoning"]), initials="REPLAY",
                    cap_usd=1000.0, max_wall_seconds=3600.0, out_dir=out_dir, dry_run=False, workers=1,
                    chunk_id=f"trial-{m['task']}", chat=chat, key_status=lambda backend: {"limit_remaining": None},
                    require_checkpoint=False, env=REPLAY_ENV, compare=True, reuse_from=[])
        except SystemExit as exc:
            raise FixtureError(f"replay of {fx.doc_type} could not start: {exc.code or exc}") from exc
        packet_path = out_dir / f"{out_dir.name}-review-data.json"
        if not packet_path.exists():
            note = runner.review(rows, m["task"], out_dir, reasoning=runner.reasoning_spec(m["reasoning"]))
            raise FixtureError(f"replay of {fx.doc_type} wrote no review packet: "
                               f"{note.get('review_packet_error', 'unknown reason')}")
        got = normalize_packet(json.loads(packet_path.read_text(encoding="utf-8")), [tmp])
    expected = normalize_packet(_read_json(fx.path / "expected-review-data.json"))
    lines = diff_json(expected, got)
    if rc != m.get("exit_code", rc):
        lines.append(f"  exit code: recorded {m['exit_code']}, replay {rc}")
    return lines


def replay(types: Sequence[str] | None = None, *, root: Path = FIXTURE_ROOT, out: TextIO | None = None) -> int:
    """Replay each recorded fixture; exit 1 on a stale fixture or a packet that differs."""
    out = out or sys.stdout
    say = lambda text="": print(text, file=out)  # noqa: E731
    wanted = list(types or DOC_TYPES)
    found = fixture_dirs(Path(root), wanted)
    if types and len(found) != len(wanted):
        say(f"no fixture recorded for: {', '.join(t for t in wanted if not (Path(root) / t).is_dir())}")
        return 1
    if not found:
        say("no live smoke fixtures recorded yet; run smoke_fixtures record")
        return 0
    bad = 0
    for path in found:
        try:
            diff = replay_fixture(path)
        except FixtureError as exc:
            say(str(exc))
            bad += 1
            continue
        if diff:
            bad += 1
            say(f"REPLAY MISMATCH {path.name}: the review packet differs from expected-review-data.json:")
            say("\n".join(diff))
        else:
            say(f"ok: {path.name} replays to the recorded review packet")
    return 1 if bad else 0


# ------------------------------------------------------------------ verify-hub (network, opt-in)

def verify_hub(types: Sequence[str] | None = None, *, root: Path = FIXTURE_ROOT,
               hub: Callable[[str | None], Mapping[str, Any]] | None = None) -> list[str]:
    """Problems found re-downloading the public file at each manifest's revision (empty when it matches)."""
    hub = hub or download_public
    problems: list[str] = []
    cache: dict[str, Mapping[str, Any]] = {}
    for path in fixture_dirs(Path(root), types):
        fx = load_fixture(path)
        ds = fx.manifest["dataset"]
        if ds["revision"] not in cache:
            cache[ds["revision"]] = hub(ds["revision"])
        got = cache[ds["revision"]]
        if str(got["source_sha256"]) != ds["file_sha256"]:
            problems.append(f"{fx.doc_type}: {SOURCE_FILE} at {ds['revision'][:12]} has sha256 "
                            f"{str(got['source_sha256'])[:12]}, the manifest says {ds['file_sha256'][:12]}")
        by_id = {str(r.get("document_id") or r.get("id") or r["filename"]): r for r in got["rows"]}
        for d in fx.manifest["documents"]:
            row = by_id.get(d["published_document_id"])
            if row is None:
                problems.append(f"{fx.doc_type}: {d['published_document_id']} is not in the public file "
                                f"at {ds['revision'][:12]}")
            elif sha256_text(str(row.get("doc_text") or "")) != d["text_sha256"]:
                problems.append(f"{fx.doc_type}: the text of {d['published_document_id']} differs from the "
                                f"public file at {ds['revision'][:12]}")
    return problems


# ------------------------------------------------------------------ command line

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"python -m {MODULE}", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    for name, helptext in (("record", "record fixtures: the ONLY paid command (--plan-only spends nothing)"),
                           ("check", "offline: is each fixture still what the current code would send?"),
                           ("replay", "offline: replay recorded answers through the runner and diff the packet"),
                           ("verify-hub", "network: re-download the public file and confirm each document's hash")):
        cmd = sub.add_parser(name, help=helptext)
        cmd.add_argument("--types", nargs="+", choices=DOC_TYPES, default=None,
                         help="document types (default: all five)")
        cmd.add_argument("--root", type=Path, default=FIXTURE_ROOT, help=argparse.SUPPRESS)
        if name == "record":
            cmd.add_argument("--initials", required=True, help="your initials, 2-6 capital letters")
            cmd.add_argument("--cap-usd", type=float, default=DEFAULT_CAP_USD,
                             help="total cap, split per type (cuad 0.40, corporate 0.20, merger_consideration "
                                  "0.15, claim_checklist 0.15, correspondence 0.10 of $1.00; default 1.00)")
            cmd.add_argument("--documents", type=int, default=DEFAULT_DOCUMENTS,
                             help=f"documents per type (default {DEFAULT_DOCUMENTS}, "
                                  f"at most {launch.TRIAL_MAX_DOCUMENTS})")
            cmd.add_argument("--plan-only", action="store_true",
                             help="print what would run and what it would cost; send nothing")
            cmd.add_argument("--replace", action="store_true", help="overwrite an existing fixture")
    return p


def main(argv: Sequence[str] | None = None, **seams: Any) -> int:
    """Command line entry; ``seams`` (``hub``, ``chat``, ``key_status``, ``env``, ``stdin``, ``out``) are for tests."""
    args = _parser().parse_args(argv)
    out = seams.get("out") or sys.stdout
    types = args.types or list(DOC_TYPES)
    if args.command == "record":
        return record(types, initials=args.initials, cap_usd=args.cap_usd, documents=args.documents,
                      plan_only=args.plan_only, replace=args.replace, root=args.root, **seams)
    if args.command == "check":
        return check(args.types, root=args.root, out=out)
    if args.command == "replay":
        return replay(args.types, root=args.root, out=out)
    try:
        problems = verify_hub(args.types, root=args.root, hub=seams.get("hub"))
    except FixtureError as exc:
        print(f"unreadable fixture: {exc}", file=out)
        return 1
    for line in problems:
        print(f"MISMATCH {line}", file=out)
    if not problems:
        print("ok: every stored document matches the public file", file=out)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
