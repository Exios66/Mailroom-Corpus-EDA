"""P2 matter/grouping groundwork (plan §13–§16, §14A, HUB-022).

Implements the §14A-decided backfill methodology over the local corpus:

- ``source_native_thread`` — real reply-header chains (message_id →
  in_reply_to edges, per custodian). VERIFIED ABSENT in this corpus family:
  the CMU maildir itself carries no In-Reply-To/References headers (0/350
  raw files; 0/247,523 upstream dedup rows — HF audit 2026-09-02), so this
  construction yields ZERO matters today. Kept implemented because it is the
  only construction that is ground truth, and future feeds may carry it.
- ``heuristic_reconstructed`` — subject+custodian+time-window conversation
  reconstruction (normalized subject, degenerate Re:/Fwd:-only subjects
  excluded). Uses only real source fields but is NOT ground truth: every
  assignment is labeled ``heuristic_reconstructed`` and coverage reports
  count it separately from both other constructions (§14A never mixes
  silently).
- ``synthetic_constructed`` — scaffold only: the construction vocabulary and
  the bundle-derivation contract (§14 bundle families) are defined here;
  manufacturing actual bundle rows is a sanctioned-publish decision (§84),
  flagged ``matter_construction: synthetic_constructed`` when it happens.

Grouping derivations are pure functions over (filename → metadata) joined
rows; the GT config carries no metadata column (verified 31 columns), so the
caller supplies the default-config metadata map.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from .eval_contract import GROUP_ROLES, RELATIONSHIP_TYPES  # noqa: F401


def _stable_hash(text: str, digits: int) -> str:
    """Process-independent key material (str.hash() is PYTHONHASHSEED-salted
    — caught by the pre-publish determinism gate, 2026-09-02)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:digits]

#: metadata keys carrying Enron thread structure (verified v7 metadata union).
THREAD_ROOT_KEY = "message_id"
THREAD_REPLY_KEY = "in_reply_to"
THREAD_CUSTODIAN_KEY = "custodian"

ANGLE_RE = re.compile(r"<([^>]+)>")

#: Re:/Fwd:/Fw:/[...] prefixes stripped (repeatedly) when normalizing subjects.
SUBJECT_PREFIX_RE = re.compile(r"^\s*(re|fw|fwd|aw)\s*:", re.I)
BRACKET_PREFIX_RE = re.compile(r"^\s*\[[^\]]*\]")
DEGENERATE_SUBJECT_RE = re.compile(r"^(re|fwd?|forwarded?|fw)\s*[:.]?\s*$", re.I)


def _norm_message_id(value: Any) -> str:
    """Hub message-ids are angle-bracket strings; normalize for graph keys."""
    text = str(value or "").strip()
    inner = ANGLE_RE.match(text) if text.startswith("<") else None
    return (inner.group(1) if inner else text).strip()


def source_native_threads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build Enron source-native thread matters (§14A method 1).

    Rows are correspondence GT records; ``metadata_by_filename`` maps each
    filename to the default-config ``metadata`` dict. A thread = the weakly
    connected component of message-id ↔ in-reply-to edges INSIDE one
    custodian mailbox (ids are only unique per custodian in the maildir
    world). Rows without message ids (non-Enron correspondence) stay
    unassigned — they carry no fabricated matter.
    """
    by_custodian: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        md = row.get("metadata") or {}
        if isinstance(md, str):
            import json

            try:
                md = json.loads(md)
            except json.JSONDecodeError:
                md = {}
        row = {**row, "_md": md if isinstance(md, dict) else {}}
        by_custodian[str(row["_md"].get(THREAD_CUSTODIAN_KEY) or "")].append(row)

    out: list[dict[str, Any]] = []
    for custodian, crows in by_custodian.items():
        # message_id → row (normalized); only rows WITH ids participate
        id_to_row: dict[str, dict[str, Any]] = {}
        for row in by_custodian[custodian]:
            mid = _norm_message_id(row["_md"].get(THREAD_ROOT_KEY))
            if mid:
                id_to_row[mid] = row

        # union-find over reply edges (bounded: each row has ≤1 in_reply_to)
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.get(x, x) != x:
                x = parent[x]
            return x

        for row in by_custodian[custodian]:
            mid = _norm_message_id(row["_md"].get(THREAD_ROOT_KEY))
            if not mid:
                continue
            parent.setdefault(mid, mid)
            reply = _norm_message_id(row["_md"].get(THREAD_REPLY_KEY))
            if reply and reply in parent:
                a, b = find(mid), find(reply)
                if a != b:
                    parent[b] = a

        threads: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in by_custodian[custodian]:
            mid = _norm_message_id(row["_md"].get(THREAD_ROOT_KEY))
            if mid and mid in parent:
                threads[find(mid)].append(row)

        for root_id, members in threads.items():
            if len(members) < 2:
                continue  # singletons are standalone documents — no matter
            members.sort(key=lambda r: str(r["_md"].get("date") or ""))
            root_mid = _norm_message_id(members[0]["_md"].get(THREAD_ROOT_KEY))
            matter_id = f"MATTER-ENRON-{custodian}-{root_id[:24]}"
            for position, row in enumerate(members):
                out.append(
                    {
                        "filename": row["filename"],
                        "matter_id": matter_id,
                        "matter_construction": "source_native_thread",
                        "group_id": f"GROUP-THREAD-{root_id[:16]}",
                        "group_role": "correspondence",
                        "relationships": (
                            ["responds_to"] if position > 0 else []
                        ),
                        "thread_position": position,
                        "thread_size": len(members),
                    }
                )
    return out


def normalize_subject(subject: str) -> str:
    """Strip Re:/Fwd: prefixes and brackets; collapse case/whitespace.

    Returns '' for degenerate subjects (empty or prefix-only like 'Re:') —
    those carry no grouping signal and are never thread keys.
    """
    text = str(subject or "").strip()
    while True:
        stripped = SUBJECT_PREFIX_RE.sub("", text, count=1)
        stripped = BRACKET_PREFIX_RE.sub("", stripped, count=1)
        if stripped == text:
            break
        text = stripped
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return "" if DEGENERATE_SUBJECT_RE.match(text) else text


def _row_date(row: dict[str, Any]) -> str:
    md = row.get("_md") or row.get("metadata") or {}
    return str(md.get("date") or "")


def _row_custodian(row: dict[str, Any]) -> str:
    md = row.get("_md") or row.get("metadata") or {}
    return str(md.get("custodian") or "")


def _row_subject(row: dict[str, Any]) -> str:
    subject = str(row.get("subject") or "")
    if not subject:
        # correspondence doc_text begins with the RFC822-style header block
        # (compose_doc_text "Subject: ...\n\n<body>"); reuse the canonical
        # extractor from intent_backfill when available.
        try:
            from .intent_backfill import extract_subject

            subject = extract_subject(str(row.get("doc_text") or ""))
        except Exception:
            m = re.search(r"^subject:\s*(.*)$", str(row.get("doc_text") or ""), re.I | re.M)
            subject = m.group(1) if m else ""
    return subject


def heuristic_subject_threads(
    rows: list[dict[str, Any]],
    *,
    window_days: int = 30,
) -> list[dict[str, Any]]:
    """Subject-based conversation reconstruction (§14A method 2).

    Groups = (custodian, normalized subject) with ALL members inside a
    ``window_days`` span of the earliest message; ≥2 members required.
    Uses only real source fields (subject 346/350, custodian/date 350/350)
    but is NOT ground truth — every matter is labeled
    ``matter_construction: heuristic_reconstructed`` and coverage reports
    count it separately. Empty/degenerate subjects never group.
    """
    from datetime import datetime, timedelta

    def parse_date(text: str) -> datetime | None:
        try:
            return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        except ValueError:
            return None

    candidates: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        subject = normalize_subject(_row_subject(row))
        custodian = _row_custodian(row)
        if not subject or not custodian:
            continue
        if parse_date(_row_date(row)) is None:
            continue
        candidates[(custodian, subject)].append(row)

    out: list[dict[str, Any]] = []
    for (custodian, subject), members in candidates.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: _row_date(r))
        earliest = parse_date(_row_date(members[0]))
        latest = parse_date(_row_date(members[-1]))
        if earliest is None or latest is None:
            continue
        if (latest - earliest) > timedelta(days=window_days):
            continue  # same subject reused later is a topic repeat, not a thread
        matter_id = f"MATTER-SUBJ-{custodian}-{_stable_hash(subject, 12)}"
        for position, row in enumerate(members):
            out.append(
                {
                    "filename": row["filename"],
                    "matter_id": matter_id,
                    "matter_construction": "heuristic_reconstructed",
                    "group_id": f"GROUP-SUBJ-{_stable_hash(subject, 10)}",
                    "group_role": "correspondence",
                    "relationships": (["responds_to"] if position > 0 else []),
                    "thread_position": position,
                    "thread_size": len(members),
                    "thread_evidence": f"subject+custodian+{window_days}d-window",
                }
            )
    return out


def enrich_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach grouping to correspondence rows (by filename), §14A method order:

    1. ``source_native_thread`` — in_reply_to chains where they exist
       (structurally absent in this corpus family: 0 matters, verified);
    2. ``heuristic_reconstructed`` — subject threads for rows the header
       pass did not group;
    3. everything else stays UNASSIGNED (empty fields) — §14A: no
       fabricated matters, constructions never silently mixed.
    """
    groups = source_native_threads(rows)
    if groups:
        raise AssertionError(
            "source_native_thread groups found — in_reply_to verified absent "
            "in this corpus family; investigate before mixing constructions"
        )
    groups = heuristic_subject_threads(rows)
    by_filename = {g["filename"]: g for g in groups}
    out = []
    for row in rows:
        merged = dict(row)
        g = by_filename.get(str(row.get("filename") or ""))
        for key in (
            "matter_id", "matter_construction", "group_id", "group_role",
            "relationships", "thread_position", "thread_size", "thread_evidence",
        ):
            merged[key] = g[key] if g else ([] if key == "relationships" else "")
        out.append(merged)
    return out
