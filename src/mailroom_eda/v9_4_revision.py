"""v9.4 — exact-duplicate removal + label-completion QA revision (STAGING).

Plan: ``docs/plans/v9.4-dedup-label-completion.md``. Input: the v9.3 staged
payload (``data/v9_3/stage``, built by
``scripts/build/build_v9_3_corporate_presence.py``). Output:
``data/v9_4/stage``. Nothing here uploads; ``--publish`` raises.

What this revision changes, in order:

1. **Duplicate removal (row-set change, 3,302 -> 3,292).** Rows that
   share a ``content_sha256`` collapse to one deterministic survivor (see
   :func:`survivor_sort_key`). Then a test row whose normalized text equals a
   train row's is dropped (:func:`plan_near_dup_test_drops`): it would leak
   training text into test, and the md5 split rule forbids moving it.
   Survivors are byte-identical to their v9.3 lines. The removed rows, the
   survivor each one folded into, and any label conflict between the copies
   are recorded in ``dedup_removed.jsonl`` (shipped in the payload) and the
   audit report.
2. **Placeholder hygiene.** The literal strings ``"nan"`` / ``"None"`` /
   ``"null"`` inside ``gt_fields`` become ``""`` (the cast-safe absence
   convention). The 3 CMS outpatient ``"N/A"`` dates are a deliberate
   verbatim source marker (``v9_build``) and are left alone. Presence codes
   must not move (gate).
3. **CUAD clause labels for the SEC EDGAR EX-10 contracts (issue #30).**
   Labels come from an LLM journal produced on Modal by
   ``local-mailroom-sandbox`` (``scripts/gt_label_ex10.py``; that repository
   owns serving). This module never trusts the journal: every span is
   re-verified as a verbatim slice of this payload's ``doc_text`` at its
   recorded offset, every category must be one of the 41 CUAD names taken
   from the golden CUAD rows, light semantic probes drop spans whose text
   cannot be the named clause, and the row must clear the same quality floor
   the labeler used. A row that fails any of these stays
   ``pending_annotation``.
4. **Flagged correspondence intents.** An optional two-pass relabel journal
   (:func:`run_intent_pass`) is accepted per row only when both passes agree
   and either match the original label or are confident (>= 0.8). Anything
   else stays ``flagged_review``.
5. **Calibration against expert labels.** The same model is scored against
   golden CUAD contracts and the 96 manual intents
   (:func:`score_cuad_calibration`, :func:`score_intent_calibration`). The
   result does not block staging; it decides ``publish_ready`` in the
   conformance report. Without a calibration journal the status is
   ``not_run`` and the payload is not publish-ready.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .config import DATA_DIR, ROOT
from .dataset_export import assign_split, safe_jsonl_line
from .gt_presence import presence_law_problems, row_gt_presence
from .identity import normalized_text_sha256

PLAN_REF = "docs/plans/v9.4-dedup-label-completion.md"
LABELS_VERSION = "v9.4 dedup + label-completion"
REVISION_LABEL = "v9.4 dedup + label-completion revision"
REVISION_LINE = (
    f"{REVISION_LABEL} (exact content_sha256 duplicates removed; 'nan' "
    "placeholders scrubbed; verified LLM CUAD labels on SEC EDGAR EX-10; "
    "flagged intents re-checked) over v9.3"
)

BASE_STAGE_DIR = DATA_DIR / "v9_3" / "stage"
STAGE_DIR_DEFAULT = DATA_DIR / "v9_4" / "stage"
LLM_DIR = DATA_DIR / "v9_4" / "llm_raw"
QUEUE_DIR = DATA_DIR / "v9_4" / "queues"
AUDIT_DIR = ROOT / "docs" / "reports" / "audits"

#: The only model whose labels v9.4 will ingest: the sandbox GT labeler's
#: posture (local-mailroom-sandbox ``gt_labeler.POSTURE``, one H100). The
#: earlier ``Qwen/Qwen3-14B-AWQ`` journal is retired: 126 of its spans failed
#: re-verification, and small Qwen3 CUAD presence F1 measures 0.57-0.64 in the
#: sandbox benchmarks (SAND-032/SAND-042).
LABEL_MODEL = "Qwen/Qwen3-32B-FP8"
APPROVED_LABEL_MODELS = frozenset({LABEL_MODEL})

#: Default location of the sandbox EX-10 journal written by the approved model
#: (sibling checkout). The retired 14B journal lives at ``ex10-cuad-labels.jsonl``
#: and is deliberately not discovered.
SANDBOX_JOURNAL_NAME = "v9_4-ex10-cuad-labels-qwen3-32b.jsonl"
SANDBOX_JOURNAL_CANDIDATES = (
    ROOT.parent / "local-mailroom-sandbox" / "reports" / "gt-labeler" / SANDBOX_JOURNAL_NAME,
    ROOT.parent / "exios66" / "local-mailroom-sandbox" / "reports" / "gt-labeler" / SANDBOX_JOURNAL_NAME,
)

EXPECTED_BASE_ROWS = 3302
#: Tripwire, not a parameter: the v9.3 stage has 6 sha groups / 9 extra copies.
EXPECTED_REMOVED_ROWS = 9
#: Test rows dropped because their normalized text equals a train row's (plan
#: §5): a board resolution whose test copy differs from its two train siblings by
#: 2 characters in the signature block, with identical labels. Pinned, so a
#: changed base fails the build instead of silently dropping something else.
EXPECTED_NEAR_DUP_TEST_DROPS = frozenset({"0000950137-06-011083_n08078exv3w03.htm"})
EXACT_KIND = "exact"
NEAR_DUP_KIND = "near_duplicate_test_drop"

PLACEHOLDER_STRINGS = frozenset({"nan", "None", "null"})
COPY_MARKER_RE = re.compile(r"\(\d+\)(?=\.[A-Za-z0-9]+$)")
#: gt keys that legitimately differ between two filings of one exhibit
#: (the accession number is embedded in the keywords).
PROVENANCE_VARIANT_GT_KEYS = frozenset({"keywords"})
IDENTITY_KEYS = frozenset({
    "filename", "document_id", "source_document_id", "source_filename",
    "split", "metadata", "source_revision",
})

GOLDEN_CUAD_SOURCE = "theatticusproject/cuad"
EX10_SOURCE = "sec_edgar"
MAX_CUAD_SPAN_CHARS = 4000
MIN_PRESENT_CUAD_CATEGORIES = 4  # the labeler's floor (sandbox gt_labeler)

INTENT_ACCEPT_CONFIDENCE = 0.8
#: Calibration floors. The sandbox's small Qwen3 runs score 0.57-0.64 CUAD
#: presence F1, so 0.6 could not tell a weak labeler from a good one.
#: Precision carries its own floor: a wrong label is worse than a missing one.
CALIBRATION_THRESHOLDS = {
    "cuad": {"micro_f1": 0.75, "micro_precision": 0.80},
    "intent": {"cohen_kappa": 0.75},
}
CUAD_CALIBRATION_SAMPLE = 40
RANDOM_STATE = 42


# ---------------------------------------------------------------------------
# io helpers
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    """Hex sha256 of the file's raw bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_raw_lines(path: Path) -> list[str]:
    """Non-empty JSONL lines, exactly as stored (no re-serialization)."""
    return [ln for ln in path.read_text(encoding="utf-8").split("\n") if ln.strip()]


def load_jsonl(path: Path) -> list[dict]:
    """Parse every non-empty line of a JSONL file into a dict, in file order."""
    return [json.loads(ln) for ln in read_raw_lines(path)]


def _json_obj(value: Any) -> dict:
    """Coerce a dict or a JSON-object string to a dict.

    Return ``{}`` for anything else: empty strings, invalid JSON, or JSON that
    is not an object. Callers use it on ``gt_fields`` cells, which v9.x stores
    as JSON strings.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def presence_of(row: Mapping[str, Any]) -> dict[str, str]:
    """The row's ``gt_presence`` map (field -> presence code) as a fresh dict, ``{}`` if absent."""
    return dict(_json_obj((row.get("gt_fields") or {}).get("gt_presence")))


def presence_probe(row: Mapping[str, Any]) -> dict[str, Any]:
    """The flat row ``gt_presence.row_gt_presence`` classifies (v9.1 shape)."""
    probe = dict(row.get("gt_fields") or {})
    probe["expected"] = row.get("expected")
    probe["expected_subclass"] = row.get("expected_subclass")
    probe["doc_text"] = row.get("doc_text") or ""
    probe["metadata"] = row.get("metadata") or {}
    return probe


def recompute_presence(row: dict) -> dict[str, str]:
    """Recompute presence, store it as sorted JSON in the row, and return it."""
    old = presence_of(row)
    new = row_gt_presence(presence_probe(row))
    ordered = {k: new[k] for k in old if k in new}
    ordered.update({k: v for k, v in new.items() if k not in ordered})
    row["gt_fields"]["gt_presence"] = json.dumps(ordered, sort_keys=True)
    return ordered


# ---------------------------------------------------------------------------
# step 1 — exact-duplicate removal
# ---------------------------------------------------------------------------


#: Duplicate groups whose survivor is fixed by verified upstream evidence,
#: overriding :func:`survivor_sort_key`. Keyed by the full ``content_sha256``.
#:
#: MAUD v1 (Zenodo 7500064, ``maud_v1.zip``) ships ``data/contracts/contract_149.txt``
#: as a byte copy of ``contract_148.txt`` (Kimco Realty / Weingarten Realty, April
#: 15, 2021): the only identical pair among its 152 contracts. MAUD's own evidence
#: excerpts (``MAUD_{train,dev,test}.csv``) show which annotations belong to that
#: text. 57 of 71 ``contract_148`` excerpts occur in it (Type of Consideration
#: "Mixed Cash/Stock", correct for the deal), and 0 of 75 ``contract_149`` excerpts
#: do (an all-cash agreement whose text MAUD never shipped). The richness rule would
#: keep ``contract_149`` (more answers), whose labels do not describe the document.
#: ``scripts/audit/verify_maud_duplicate.py --check`` recomputes this evidence.
UPSTREAM_DEFECT_SURVIVORS: dict[str, dict[str, Any]] = {
    "7a84e0d4d21183ede27195bbaded6976e3ef3af05e72ad05aea0a73f4ad6a96e": {
        "survivor": "contract_148_merger_agreement.txt",
        "removed": ["contract_149_merger_agreement.txt"],
        "source": "MAUD v1, Zenodo record 7500064 (maud_v1.zip): data/contracts/contract_148.txt "
                  "and contract_149.txt are byte-identical",
        "note": ("MAUD v1 packaged contract_149.txt as a copy of contract_148.txt; "
                 "contract_149's annotations describe a different, all-cash agreement"),
        "evidence": {"contract_148": [57, 71], "contract_149": [0, 75]},
    },
}


@dataclass
class DedupGroup:
    """One removal record: the survivor kept and the rows removed.

    ``kind`` is ``"exact"`` (byte-identical copies sharing ``content_sha256``)
    or ``"near_duplicate_test_drop"`` (test rows whose normalized text equals
    the train survivor's; ``content_sha256`` is then the survivor's and
    ``normalized_text_sha256`` the shared normalized hash).
    ``survivor_basis`` is ``"rule"`` (:func:`survivor_sort_key`),
    ``"upstream_defect"`` (:data:`UPSTREAM_DEFECT_SURVIVORS`) or
    ``"cross_split_near_duplicate"``; ``survivor_note`` says why for the
    latter two.
    """

    content_sha256: str
    survivor: str
    removed: list[str]
    members: list[dict] = field(default_factory=list)
    conflict_fields: list[str] = field(default_factory=list)
    survivor_basis: str = "rule"
    survivor_note: str = ""
    kind: str = EXACT_KIND
    normalized_text_sha256: str = ""

    @property
    def label_conflict(self) -> bool:
        """True when the copies carry different labels for the same text."""
        return bool(self.conflict_fields)

    def as_dict(self) -> dict:
        """JSON-ready form for ``dedup_removed.jsonl`` and the audit report."""
        d = asdict(self)
        d["label_conflict"] = self.label_conflict
        return d


def annotation_richness(row: Mapping[str, Any]) -> int:
    """How much ground truth a row carries: CUAD spans + MAUD answers +
    populated presence cells. Used only to choose among identical texts."""
    gt = row.get("gt_fields") or {}
    cuad = _json_obj(gt.get("cuad_clause_labels"))
    n = sum(len(v) for v in cuad.values() if isinstance(v, list))
    try:
        n += int(str(gt.get("maud_label_count") or "0"))
    except ValueError:
        pass
    n += sum(1 for code in presence_of(row).values() if code == "populated")
    return n


def survivor_sort_key(row: Mapping[str, Any]) -> tuple:
    """Deterministic survivor order (first wins):

    1. the richest annotation (two expert annotations of one text can
       differ; keep the more complete one, never merge them);
    2. a filename without a copy marker such as ``(1)``;
    3. the ``train`` split (the md5 rule is not touched — this only picks
       which copy of a leaked cross-split group survives);
    4. the lexicographically smallest filename.
    """
    fn = str(row.get("filename") or "")
    return (
        -annotation_richness(row),
        bool(COPY_MARKER_RE.search(fn)),
        str(row.get("split")) != "train",
        fn,
    )


def label_conflict_fields(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Label fields whose values differ between copies of one text."""
    fields: set[str] = set()
    first = rows[0]
    for other in rows[1:]:
        for k in set(first) | set(other):
            if k in IDENTITY_KEYS or k == "gt_fields":
                continue
            if first.get(k) != other.get(k):
                fields.add(k)
        ga, gb = first.get("gt_fields") or {}, other.get("gt_fields") or {}
        for k in set(ga) | set(gb):
            if k in PROVENANCE_VARIANT_GT_KEYS or k == "gt_presence":
                continue
            if ga.get(k) != gb.get(k):
                fields.add(f"gt_fields.{k}")
    return sorted(fields)


def plan_dedup(
    rows: Sequence[Mapping[str, Any]],
    overrides: Mapping[str, Mapping[str, Any]] = UPSTREAM_DEFECT_SURVIVORS,
) -> list[DedupGroup]:
    """Plan duplicate groups by content hash, choosing one survivor per group.

    The survivor comes from :func:`survivor_sort_key` unless ``overrides``
    names one for the group's sha (verified upstream defects). Raise
    ``AssertionError`` if any row has an empty content hash, or if an
    override's survivor/removed names do not match the group's members.
    """
    by_sha: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        sha = str(r.get("content_sha256") or "")
        if not sha:
            raise AssertionError(f"{r.get('filename')}: empty content_sha256")
        by_sha[sha].append(r)
    groups = []
    for sha in sorted(by_sha):
        members = by_sha[sha]
        if len(members) < 2:
            continue
        ordered = sorted(members, key=survivor_sort_key)
        basis, note = "rule", ""
        if sha in overrides:
            ov = overrides[sha]
            names = {str(m["filename"]) for m in members}
            want = {ov["survivor"], *ov["removed"]}
            if names != want:
                raise AssertionError(f"override for {sha[:12]} names {sorted(want)}, group has {sorted(names)}")
            ordered = sorted(members, key=lambda m: m["filename"] != ov["survivor"])
            basis, note = "upstream_defect", str(ov["note"])
        groups.append(DedupGroup(
            content_sha256=sha,
            normalized_text_sha256=str(ordered[0].get("normalized_text_sha256") or ""),
            survivor=str(ordered[0]["filename"]),
            removed=[str(m["filename"]) for m in ordered[1:]],
            members=[
                {
                    "filename": m["filename"],
                    "document_id": m.get("document_id", ""),
                    "split": m.get("split", ""),
                    "expected": m.get("expected", ""),
                    "expected_subclass": m.get("expected_subclass", ""),
                    "source_corpus": m.get("source_corpus", ""),
                    "annotation_richness": annotation_richness(m),
                }
                for m in ordered
            ],
            conflict_fields=label_conflict_fields(ordered),
            survivor_basis=basis,
            survivor_note=note,
        ))
    return groups


_NEAR_DUP_TEXT_KEYS = frozenset({"content_sha256", "doc_text"})


def plan_near_dup_test_drops(
    rows: Sequence[Mapping[str, Any]],
    expected: frozenset[str] | None = EXPECTED_NEAR_DUP_TEST_DROPS,
) -> list[DedupGroup]:
    """Plan dropping test rows whose normalized text equals a train row's.

    Run on the exact-dedup survivors. For every ``normalized_text_sha256``
    group with members in both splits, the test members are removed and the
    best train member (:func:`survivor_sort_key`) is recorded as survivor.
    Groups within one split are left alone (report-only near-duplicates).
    Raise ``AssertionError`` when ``expected`` is given and the planned drops
    differ from it.
    """
    by_fn = {str(r["filename"]): r for r in rows}
    groups = []
    for g in near_duplicate_groups(rows):
        if not g["cross_split"]:
            continue
        members = [by_fn[m["filename"]] for m in g["members"]]
        train = sorted((m for m in members if m["split"] == "train"), key=survivor_sort_key)
        test = sorted(m["filename"] for m in members if m["split"] == "test")
        surv = train[0]
        groups.append(DedupGroup(
            content_sha256=str(surv["content_sha256"]),
            normalized_text_sha256=g["normalized_text_sha256"],
            survivor=str(surv["filename"]),
            removed=test,
            members=[
                {
                    "filename": m["filename"],
                    "document_id": m.get("document_id", ""),
                    "split": m.get("split", ""),
                    "expected": m.get("expected", ""),
                    "expected_subclass": m.get("expected_subclass", ""),
                    "source_corpus": m.get("source_corpus", ""),
                    "content_sha256": m.get("content_sha256", ""),
                }
                for m in [surv] + [by_fn[fn] for fn in test]
            ],
            # the texts differ by whitespace by construction; only labels count as a conflict
            conflict_fields=[f for f in label_conflict_fields([surv] + [by_fn[fn] for fn in test])
                             if f not in _NEAR_DUP_TEXT_KEYS],
            survivor_basis="cross_split_near_duplicate",
            survivor_note=("the test copy's text equals a train row's after normalization, so keeping "
                           "it would leak training text into test; the md5 split rule forbids moving it"),
            kind=NEAR_DUP_KIND,
        ))
    planned = {fn for g in groups for fn in g.removed}
    if expected is not None and planned != set(expected):
        raise AssertionError(f"near-duplicate test drops {sorted(planned)} != pinned {sorted(expected)}")
    return groups


_EVIDENCE_NORM = re.compile(r"[^a-z0-9]+")


def maud_evidence_alignment(text: str, excerpts: Iterable[str], *, min_chars: int = 40) -> tuple[int, int]:
    """Count how many distinct MAUD evidence excerpts occur in ``text``.

    Excerpts are split on MAUD's ``<omitted>`` marker and compared with
    punctuation, case and whitespace removed (MAUD normalizes quotes and
    spacing); fragments shorter than ``min_chars`` after normalization are
    skipped. Returns ``(found, total)``. A label set whose excerpts do not
    occur in the text was annotated on a different document.
    """
    hay = _EVIDENCE_NORM.sub("", text.lower())
    seen: set[str] = set()
    found = 0
    for ex in excerpts:
        for piece in str(ex or "").split("<omitted>"):
            frag = _EVIDENCE_NORM.sub("", piece.lower())
            if len(frag) < min_chars or frag in seen:
                continue
            seen.add(frag)
            found += frag[:200] in hay
    return found, len(seen)


def journal_aliases(groups: Iterable[DedupGroup]) -> dict[str, list[str]]:
    """survivor filename -> removed filenames whose journal record it may use.

    Only exact (byte-identical) copies qualify: their spans sit at the same
    offsets. A near-duplicate's text differs, so its record is never borrowed.
    """
    aliases: dict[str, list[str]] = defaultdict(list)
    for g in groups:
        if g.kind == EXACT_KIND:
            aliases[g.survivor].extend(g.removed)
    return dict(aliases)


def removed_index(groups: Iterable[DedupGroup]) -> dict[str, str]:
    """removed filename -> survivor filename."""
    return {fn: g.survivor for g in groups for fn in g.removed}


def dangling_references(
    kept_rows: Sequence[Mapping[str, Any]],
    removed_rows: Sequence[Mapping[str, Any]],
    sidecar_texts: Mapping[str, str],
) -> list[str]:
    """Find removed identifiers in selected row fields and sidecar text."""
    needles = {str(r["filename"]) for r in removed_rows}
    needles |= {str(r.get("document_id") or "") for r in removed_rows} - {""}
    hits = []
    for r in kept_rows:
        for col in ("related_document_ids", "relationships", "group_id", "matter_id"):
            val = r.get(col)
            vals = val if isinstance(val, list) else [val]
            for v in vals:
                if str(v) in needles:
                    hits.append(f"{r['filename']}.{col} -> {v}")
    for name, text in sidecar_texts.items():
        for n in sorted(needles):
            if n and n in text:
                hits.append(f"{name} mentions {n}")
    return hits


def verify_dedup(base_rows: Sequence[Mapping], kept_rows: Sequence[Mapping],
                 groups: Sequence[DedupGroup]) -> dict[str, Any]:
    """Validate the removal counts, surviving hashes, filenames, and splits.

    Expects exactly :data:`EXPECTED_REMOVED_ROWS` exact removals and the pinned
    :data:`EXPECTED_NEAR_DUP_TEST_DROPS`.

    Return row, class, and split counts. Raise ``AssertionError`` when a
    deduplication or split check fails.
    """
    removed = removed_index(groups)
    exact = removed_index(g for g in groups if g.kind == EXACT_KIND)
    near = removed_index(g for g in groups if g.kind == NEAR_DUP_KIND)
    if len(exact) != EXPECTED_REMOVED_ROWS:
        raise AssertionError(f"expected {EXPECTED_REMOVED_ROWS} exact removals, planned {len(exact)}")
    if set(near) != set(EXPECTED_NEAR_DUP_TEST_DROPS):
        raise AssertionError(f"near-duplicate test drops {sorted(near)} != pinned")
    if len(kept_rows) != len(base_rows) - len(removed):
        raise AssertionError("row count does not equal base minus removed")
    shas = Counter(r["content_sha256"] for r in kept_rows)
    dup = [s for s, n in shas.items() if n > 1]
    if dup:
        raise AssertionError(f"duplicate content_sha256 survive: {dup[:3]}")
    kept_names = {r["filename"] for r in kept_rows}
    if kept_names & set(removed):
        raise AssertionError("a removed filename survived")
    for r in kept_rows:
        if assign_split(r["filename"]) != r["split"]:
            raise AssertionError(f"{r['filename']}: split violates the md5 rule")
    base_by_class = Counter(r["expected"] for r in base_rows)
    kept_by_class = Counter(r["expected"] for r in kept_rows)
    deltas = {c: kept_by_class[c] - base_by_class[c] for c in sorted(base_by_class)}
    splits = dict(Counter(r["split"] for r in kept_rows))
    print(f"verify_dedup OK — {len(kept_rows):,} rows (removed {len(removed)}), "
          f"class deltas {deltas}, splits {splits}")
    return {"rows": len(kept_rows), "removed": len(removed), "class_deltas": deltas,
            "splits": splits, "composition": dict(sorted(kept_by_class.items()))}


def near_duplicate_groups(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Report-only: texts equal after normalization but not byte-equal."""
    by_norm: dict[str, list[Mapping]] = defaultdict(list)
    for r in rows:
        key = str(r.get("normalized_text_sha256") or "")
        if key:
            by_norm[key].append(r)
    out = []
    for key in sorted(by_norm):
        members = by_norm[key]
        if len({m["content_sha256"] for m in members}) > 1:
            out.append({
                "normalized_text_sha256": key,
                "cross_split": len({m["split"] for m in members}) > 1,
                "members": [
                    {"filename": m["filename"], "split": m["split"], "expected": m["expected"]}
                    for m in sorted(members, key=lambda m: m["filename"])
                ],
            })
    return out


# ---------------------------------------------------------------------------
# step 2 — placeholder hygiene
# ---------------------------------------------------------------------------


def scrub_placeholders(row: dict) -> list[str]:
    """Replace placeholder strings in ``gt_fields`` with ``""``; return the
    keys changed. Presence codes must be unchanged (they already treat these
    cells as absent); a move is a hard error."""
    gt = row.get("gt_fields") or {}
    changed = [
        k for k, v in gt.items()
        if k != "gt_presence" and isinstance(v, str) and v.strip() in PLACEHOLDER_STRINGS
    ]
    if not changed:
        return []
    before = presence_of(row)
    for k in changed:
        gt[k] = ""
    after = row_gt_presence(presence_probe(row))
    if after != before:
        diff = {k: (before.get(k), after.get(k)) for k in set(before) | set(after)
                if before.get(k) != after.get(k)}
        raise AssertionError(f"{row['filename']}: placeholder scrub moved presence {diff}")
    return changed


# ---------------------------------------------------------------------------
# step 3 — CUAD clause labels (verify + ingest an LLM journal)
# ---------------------------------------------------------------------------


def cuad_vocabulary(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """The 41 CUAD category names, taken from the golden rows themselves."""
    vocab: set[tuple[str, ...]] = set()
    for r in rows:
        if r.get("expected") == "contract" and r.get("source_corpus") == GOLDEN_CUAD_SOURCE:
            vocab.add(tuple(sorted(_json_obj(r["gt_fields"].get("cuad_clause_labels")))))
    if len(vocab) != 1:
        raise AssertionError(f"golden CUAD rows disagree on the category set ({len(vocab)} variants)")
    (names,) = vocab
    if len(names) != 41:
        raise AssertionError(f"expected 41 CUAD categories, found {len(names)}")
    return names


_DATEISH = re.compile(
    r"\d|\b(day|date|month|year|term|period|anniversary|effective|expir|terminat|until|"
    r"upon|closing|commence|execution|signing|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)",
    re.I,
)
#: Light, deliberately permissive semantic probes. A span that fails its
#: probe cannot be the named clause (e.g. a Governing Law span that never
#: mentions law); it is dropped, never rewritten.
SEMANTIC_PROBES: dict[str, re.Pattern] = {
    "Governing Law": re.compile(r"\blaws?\b|govern|constru|jurisdiction", re.I),
    "Agreement Date": _DATEISH,
    "Effective Date": _DATEISH,
    "Expiration Date": _DATEISH,
    "Renewal Term": _DATEISH,
    "Notice Period To Terminate Renewal": _DATEISH,
    "Warranty Duration": _DATEISH,
    "Audit Rights": re.compile(r"audit|inspect|\bbooks\b|records|examin", re.I),
    "Insurance": re.compile(r"insur", re.I),
    "Covenant Not To Sue": re.compile(r"\bsue\b|\bsuit|claim|challeng|contest", re.I),
    # CUAD: a license that extends to a party's affiliates; both words needed
    "Affiliate License-Licensee": re.compile(r"(?=.*affiliat)(?=.*licen)", re.I | re.S),
    "Affiliate License-Licensor": re.compile(r"(?=.*affiliat)(?=.*licen)", re.I | re.S),
    "Source Code Escrow": re.compile(r"escrow|source code", re.I),
}
#: Document Name must come from the opening of the filing.
DOCUMENT_NAME_MAX_START = 5000
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def is_heading_only(name: str, text: str) -> bool:
    """A span that is just the category's own name (a section heading such
    as ``Effective Date``) carries no clause."""
    return _NON_ALNUM.sub("", text.lower()) == _NON_ALNUM.sub("", name.lower())


def verify_cuad_labels(
    doc_text: str, labels: Mapping[str, Any], vocab: Sequence[str],
) -> tuple[dict[str, list[dict]], list[str], list[str]]:
    """Re-verify one journal label map against the payload's own text.

    Returns ``(clean_labels, dropped, problems)``. ``clean_labels`` has every
    key in ``vocab``; each kept span is verbatim at its character offset.
    ``dropped`` explains discarded spans; non-empty ``problems`` means the
    row must stay ``pending_annotation``.
    """
    allowed = set(vocab)
    clean: dict[str, list[dict]] = {name: [] for name in vocab}
    dropped: list[str] = []
    problems: list[str] = []
    unknown = sorted(set(labels) - allowed)
    if unknown:
        problems.append(f"unknown categories {unknown}")
    seen: set[tuple[str, int, str]] = set()
    for name in vocab:
        spans = labels.get(name) or []
        if not isinstance(spans, list):
            problems.append(f"{name}: spans are not a list")
            continue
        for span in spans:
            if not isinstance(span, dict):
                dropped.append(f"{name}: span is not an object")
                continue
            text, start = span.get("text"), span.get("start")
            if (not isinstance(text, str) or not isinstance(start, int) or isinstance(start, bool)
                    or len(text.strip()) < 2):
                dropped.append(f"{name}: malformed span")
                continue
            if start < 0 or start + len(text) > len(doc_text):
                dropped.append(f"{name}: offset {start} outside the document")
                continue
            if doc_text[start:start + len(text)] != text:
                dropped.append(f"{name}: not verbatim at offset {start}")
                continue
            if len(text) > MAX_CUAD_SPAN_CHARS:
                dropped.append(f"{name}: span longer than {MAX_CUAD_SPAN_CHARS} chars")
                continue
            if is_heading_only(name, text):
                dropped.append(f"{name}: heading only")
                continue
            probe = SEMANTIC_PROBES.get(name)
            if probe is not None and not probe.search(text):
                dropped.append(f"{name}: failed semantic probe")
                continue
            if name == "Document Name" and start > DOCUMENT_NAME_MAX_START:
                dropped.append(f"{name}: not in the opening of the filing")
                continue
            key = (name, start, text)
            if key in seen:
                continue
            seen.add(key)
            clean[name].append({"start": start, "text": text})
    for spans in clean.values():
        spans.sort(key=lambda s: s["start"])
    present = [n for n, s in clean.items() if s]
    if len(present) < MIN_PRESENT_CUAD_CATEGORIES:
        problems.append(f"{len(present)} present categories (floor {MIN_PRESENT_CUAD_CATEGORIES})")
    if not (clean.get("Document Name") or clean.get("Parties")):
        problems.append("neither Document Name nor Parties was recovered")
    return clean, dropped, problems


def fold_journal(records: Sequence[Mapping[str, Any]], key: str = "filename") -> dict[str, Mapping]:
    """One record per document: an accepted record sticks; otherwise the one
    with more spans (mirrors ``mailroom_sandbox.gt_labeler.fold_label_journal``)."""
    folded: dict[str, Mapping] = {}
    for rec in records:
        k = str(rec.get(key) or rec.get("id") or "")
        if not k:
            continue
        cur = folded.get(k)
        if cur is None:
            folded[k] = rec
            continue
        if rec.get("accepted") and not cur.get("accepted"):
            folded[k] = rec
        elif cur.get("accepted") and not rec.get("accepted"):
            continue
        elif rec.get("accepted") and cur.get("accepted"):
            folded[k] = rec
        elif int(rec.get("span_count") or 0) > int(cur.get("span_count") or 0):
            folded[k] = rec
    return folded


def cuad_label_evidence(labels: Mapping[str, list]) -> str:
    """The v9 builder's evidence line for a populated CUAD map."""
    names = sorted(n for n, s in labels.items() if s)
    return "CUAD-annotated clauses: " + ", ".join(names) if names else ""


def is_pending_cuad(row: Mapping[str, Any]) -> bool:
    """True when the row's presence map marks ``cuad_clause_labels`` as ``pending_annotation``."""
    return presence_of(row).get("cuad_clause_labels") == "pending_annotation"


def ingest_cuad_journal(
    rows: list[dict],
    journal: Sequence[Mapping[str, Any]],
    vocab: Sequence[str],
    *,
    sha_aliases: Mapping[str, Sequence[str]] | None = None,
    document_ids: Mapping[str, str] | None = None,
    approved_models: Iterable[str] = APPROVED_LABEL_MODELS,
) -> dict[str, Any]:
    """Fill pending ``cuad_clause_labels`` in place from a verified journal.

    Return counts and per-row verdicts; rejected or missing records leave the
    row pending. ``sha_aliases`` maps a survivor filename to the filenames of
    its removed byte-identical copies, whose journal record is equally valid
    for the survivor (same text, same offsets) and is used as a fallback.

    ``document_ids`` (filename -> published ``document_id`` for every base
    row, removed copies included) turns on the provenance check: a journal
    record must name the same ``document_id`` as the row it is filed under,
    or it is rejected as ``failed_verification``. Raise ``AssertionError``
    if an ingested label does not recompute as populated.

    Records from a model outside ``approved_models`` are discarded before
    folding, so a retired model's record can never win over (or stand in
    for) an approved one; their count is reported as ``unapproved_records``.
    """
    approved = set(approved_models)
    unapproved = sum(1 for r in journal if str(r.get("model") or "") not in approved)
    folded = fold_journal([r for r in journal if str(r.get("model") or "") in approved])
    aliases = sha_aliases or {}
    verdicts = []
    for row in rows:
        if not is_pending_cuad(row):
            continue
        fn = row["filename"]
        candidates = [fn, *aliases.get(fn, ())]
        chosen, verdict = None, None
        for cand in candidates:
            rec = folded.get(cand)
            if rec is None:
                continue
            if document_ids is not None:
                want = document_ids.get(cand, "")
                got = str(rec.get("document_id") or "")
                if not want or got != want:
                    verdict = {"filename": fn, "status": "failed_verification",
                               "journal_filename": cand,
                               "reasons": [f"journal document_id {got or '(none)'} != {want or '(unknown)'}"]}
                    continue
            if not rec.get("accepted"):
                verdict = verdict or {"filename": fn, "status": "rejected_by_labeler",
                                      "journal_filename": cand,
                                      "reasons": list(rec.get("problems") or [])}
                continue
            labels, dropped, problems = verify_cuad_labels(
                row["doc_text"], _json_obj(rec.get("cuad_clause_labels")), vocab)
            if problems:
                verdict = {"filename": fn, "status": "failed_verification",
                           "journal_filename": cand, "reasons": problems,
                           "dropped": dropped}
                continue
            chosen = (cand, rec, labels, dropped)
            break
        if chosen is None:
            verdicts.append(verdict or {"filename": fn, "status": "no_journal_record", "reasons": []})
            continue
        cand, rec, labels, dropped = chosen
        gt = row["gt_fields"]
        gt["cuad_clause_labels"] = json.dumps(labels, ensure_ascii=False, sort_keys=True)
        gt["label_evidence"] = cuad_label_evidence(labels)
        gt["clause_count"] = str(len(vocab))
        row["annotation_method"] = "llm_zero_shot"
        row["annotation_model"] = str(rec.get("model") or "")
        row["annotation_confidence"] = ""
        presence = recompute_presence(row)
        if presence.get("cuad_clause_labels") != "populated":
            raise AssertionError(f"{fn}: ingested labels did not classify as populated")
        verdicts.append({
            "filename": fn, "status": "ingested", "journal_filename": cand,
            "via_duplicate": cand != fn, "model": row["annotation_model"],
            "present_categories": sum(1 for s in labels.values() if s),
            "spans": sum(len(s) for s in labels.values()),
            "dropped": dropped,
        })
    counts = Counter(v["status"] for v in verdicts)
    print(f"ingest_cuad_journal — {dict(counts)}; {unapproved} records from unapproved models ignored")
    return {"counts": dict(counts), "verdicts": verdicts, "unapproved_records": unapproved}


# ---------------------------------------------------------------------------
# step 4 — flagged intents (two-pass relabel)
# ---------------------------------------------------------------------------


def intent_decision(original: str, pass_a: Mapping, pass_b: Mapping,
                    vocab: Sequence[str]) -> tuple[bool, str, str]:
    """Return acceptance, label, and reason for a two-pass intent decision.

    Both passes must agree on an in-vocabulary label, which must either
    confirm the original or have confidence of at least 0.8 in both passes.
    Invalid confidence values can raise ``ValueError`` or ``TypeError``.
    """
    a, b = str(pass_a.get("intent") or ""), str(pass_b.get("intent") or "")
    if a not in vocab or b not in vocab:
        return False, "", "out-of-vocabulary answer"
    if a != b:
        return False, "", f"passes disagree ({a} vs {b})"
    conf = min(float(pass_a.get("confidence") or 0), float(pass_b.get("confidence") or 0))
    if a == original:
        return True, a, "confirms original label"
    if conf >= INTENT_ACCEPT_CONFIDENCE:
        return True, a, f"confident relabel (min confidence {conf:.2f})"
    return False, "", f"changed label below confidence {INTENT_ACCEPT_CONFIDENCE} ({conf:.2f})"


def manual_intent_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The human-labeled correspondence intents (the intent calibration set)."""
    return [r for r in rows if r["expected"] == "correspondence"
            and r["gt_fields"].get("intent_status") == "manual"]


def flagged_intent_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Correspondence rows whose ``intent_status`` is ``flagged_review``.

    These are the only rows the intent pass may relabel (drift gate b allows
    intent fills nowhere else).
    """
    return [r for r in rows if r["expected"] == "correspondence"
            and r["gt_fields"].get("intent_status") == "flagged_review"]


def run_intent_pass(
    rows: Sequence[Mapping[str, Any]], out_path: Path, *, base_url: str, api_key: str,
    model: str,
) -> list[dict]:
    """Two passes per row against an OpenAI-compatible endpoint (the Modal
    vLLM app): pass A sees subject + body, pass B the body only. Checkpointed
    in ``out_path``: rows already there are not re-sent. Return existing and
    newly appended records. Labeling raises ``RuntimeError`` after retries;
    records written before a failure remain in the checkpoint."""
    from .intent_backfill import extract_subject, llm_label_one, strip_email_headers

    done = {r["filename"] for r in load_jsonl(out_path)} if out_path.exists() else set()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records = load_jsonl(out_path) if out_path.exists() else []
    with out_path.open("a", encoding="utf-8") as fh:
        for row in rows:
            if row["filename"] in done:
                continue
            text = row["doc_text"]
            a = llm_label_one(text, model, api_key, base_url, extract_subject(text))
            b = llm_label_one(strip_email_headers(text), model, api_key, base_url, "")
            rec = {"filename": row["filename"], "model": model, "pass_a": a, "pass_b": b,
                   "labeled_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            fh.write(safe_jsonl_line(rec) + "\n")
            fh.flush()
            records.append(rec)
    return records


def ingest_intent_journal(rows: list[dict], journal: Sequence[Mapping[str, Any]],
                          vocab: Sequence[str],
                          approved_models: Iterable[str] = APPROVED_LABEL_MODELS) -> dict[str, Any]:
    """Apply accepted two-pass decisions to flagged correspondence in place.

    Only records from ``approved_models`` are read. Return counts, per-row
    verdicts, and accepted model names. Missing or rejected records remain
    flagged. Invalid confidence values can raise ``ValueError`` or
    ``TypeError``; accepted records require both values.
    """
    approved = set(approved_models)
    by_fn = {str(r["filename"]): r for r in journal if str(r.get("model") or "") in approved}
    verdicts = []
    for row in rows:
        gt = row["gt_fields"]
        if row["expected"] != "correspondence" or gt.get("intent_status") != "flagged_review":
            continue
        rec = by_fn.get(row["filename"])
        if rec is None:
            verdicts.append({"filename": row["filename"], "status": "no_journal_record"})
            continue
        ok, intent, reason = intent_decision(str(gt.get("intent") or ""),
                                             rec.get("pass_a") or {}, rec.get("pass_b") or {}, vocab)
        if not ok:
            verdicts.append({"filename": row["filename"], "status": "kept_flagged", "reason": reason})
            continue
        # same reading as intent_decision: a missing confidence counts as 0
        conf = min(float((rec.get("pass_a") or {}).get("confidence") or 0),
                   float((rec.get("pass_b") or {}).get("confidence") or 0))
        gt["intent"] = intent
        gt["intent_status"] = "auto_labeled"
        gt["intent_source"] = "llm_zero_shot"
        gt["intent_confidence"] = f"{conf:g}"
        row["annotation_method"] = "llm_zero_shot"
        row["annotation_model"] = str(rec.get("model") or "")
        row["annotation_confidence"] = f"{conf:g}"
        recompute_presence(row)
        verdicts.append({"filename": row["filename"], "status": "accepted", "intent": intent,
                         "reason": reason, "model": row["annotation_model"]})
    counts = Counter(v["status"] for v in verdicts)
    print(f"ingest_intent_journal — {dict(counts)}")
    return {"counts": dict(counts), "verdicts": verdicts,
            "models": sorted({v["model"] for v in verdicts if v["status"] == "accepted"})}


# ---------------------------------------------------------------------------
# calibration against expert labels
# ---------------------------------------------------------------------------


def f1(tp: int, fp: int, fn: int) -> float:
    """Return F1 rounded to four decimals, or zero when there are no true positives."""
    if tp == 0:
        return 0.0
    p, r = tp / (tp + fp), tp / (tp + fn)
    return round(2 * p * r / (p + r), 4)


def _overlaps(a: Mapping, b: Mapping) -> bool:
    """True when two ``{start, text}`` spans share at least one character (half-open intervals)."""
    a0, a1 = int(a["start"]), int(a["start"]) + len(a["text"])
    b0, b1 = int(b["start"]), int(b["start"]) + len(b["text"])
    return a0 < b1 and b0 < a1


def score_cuad_calibration(gold_rows: Sequence[Mapping[str, Any]],
                           journal: Sequence[Mapping[str, Any]],
                           vocab: Sequence[str]) -> dict[str, Any]:
    """Category-presence P/R/F1 of the model against expert CUAD labels,
    plus how often a true-positive category's spans overlap a gold span.

    ``gold_rows`` is the fixed calibration sample; ``expected_documents`` is
    its size, so a journal that skipped documents scores as incomplete
    instead of passing on a convenient subset. Rejected records count as
    documents but contribute no predicted categories. Verification problems
    do not discard otherwise valid spans from accepted records."""
    folded = fold_journal(journal)
    per: dict[str, Counter] = {n: Counter() for n in vocab}
    overlap_hits = overlap_total = docs = 0
    models: set[str] = set()
    for row in gold_rows:
        rec = folded.get(row["filename"])
        if rec is None:
            continue
        docs += 1
        models.add(str(rec.get("model") or ""))
        gold = _json_obj(row["gt_fields"].get("cuad_clause_labels"))
        raw_pred = _json_obj(rec.get("cuad_clause_labels")) if rec.get("accepted") else {}
        pred, _, _ = verify_cuad_labels(row["doc_text"], raw_pred, vocab)
        for name in vocab:
            g, p = bool(gold.get(name)), bool(pred.get(name))
            per[name]["tp" if g and p else "fp" if p else "fn" if g else "tn"] += 1
            if g and p:
                overlap_total += 1
                if any(_overlaps(ps, gs) for ps in pred[name] for gs in gold[name]):
                    overlap_hits += 1
    tp = sum(c["tp"] for c in per.values())
    fp = sum(c["fp"] for c in per.values())
    fn = sum(c["fn"] for c in per.values())
    by_cat = {n: {**dict(c), "f1": f1(c["tp"], c["fp"], c["fn"])} for n, c in per.items()}
    return {
        "documents": docs,
        "expected_documents": len(gold_rows),
        "models": sorted(models),
        "micro_f1": f1(tp, fp, fn),
        "micro_precision": round(tp / (tp + fp), 4) if tp + fp else 0.0,
        "micro_recall": round(tp / (tp + fn), 4) if tp + fn else 0.0,
        "span_overlap_rate": round(overlap_hits / overlap_total, 4) if overlap_total else 0.0,
        "per_category": by_cat,
    }


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    """Return kappa rounded to four decimals; empty or unequal inputs return zero.

    Return one when expected agreement is one.
    """
    n = len(a)
    if n == 0 or n != len(b):
        return 0.0
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return round((po - pe) / (1 - pe), 4) if pe < 1 else 1.0


def score_intent_calibration(manual_rows: Sequence[Mapping[str, Any]],
                             journal: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Score pass A intents against manual labels for rows with journal records.

    Return accuracy, kappa, model names, and scored and expected row counts;
    missing records are excluded from the metrics but counted as expected.
    """
    by_fn = {str(r["filename"]): r for r in journal}
    gold, pred = [], []
    models: set[str] = set()
    for row in manual_rows:
        rec = by_fn.get(row["filename"])
        if rec is None:
            continue
        models.add(str(rec.get("model") or ""))
        gold.append(str(row["gt_fields"].get("intent") or ""))
        pred.append(str((rec.get("pass_a") or {}).get("intent") or ""))
    acc = round(sum(g == p for g, p in zip(gold, pred)) / len(gold), 4) if gold else 0.0
    return {"documents": len(gold), "expected_documents": len(manual_rows), "models": sorted(models),
            "accuracy": acc, "cohen_kappa": cohen_kappa(gold, pred)}


def calibration_status(cuad: Mapping | None, intent: Mapping | None,
                       *, need_intent: bool,
                       cuad_label_models: Iterable[str] | None = None,
                       intent_label_models: Iterable[str] | None = None) -> dict[str, Any]:
    """Ingest and publish gate. A result passes only when it covers its whole
    sample (``documents == expected_documents``), was produced by exactly the
    model(s) whose labels would be ingested, and clears every floor in
    ``CALIBRATION_THRESHOLDS``.

    Statuses: ``not_run``, ``incomplete``, ``model_mismatch``, ``fail``,
    ``pass`` (and ``not_needed`` for intents when no intent journal is used)."""
    def one(result, floors, label_models):
        """Grade one calibration result against its floors.

        Returns a status dict: ``not_run`` with no scored documents,
        ``incomplete`` when fewer than the expected documents were scored,
        ``model_mismatch`` when the scored model set differs from the label
        models, else ``pass``/``fail`` with each metric value.
        """
        if not result or not result.get("documents"):
            return {"status": "not_run"}
        docs, expected = int(result["documents"]), int(result.get("expected_documents") or 0)
        base = {"documents": docs, "expected_documents": expected, "thresholds": dict(floors)}
        if expected <= 0 or docs < expected:
            return {"status": "incomplete", **base}
        want = sorted(set(label_models or ()))
        got = sorted(set(result.get("models") or ()))
        if want and got != want:
            return {"status": "model_mismatch", "label_models": want, "calibration_models": got, **base}
        values = {m: result.get(m, 0.0) for m in floors}
        ok = all(values[m] >= t for m, t in floors.items())
        return {"status": "pass" if ok else "fail", **values, **base}

    out = {"cuad": one(cuad, CALIBRATION_THRESHOLDS["cuad"], cuad_label_models)}
    out["intent"] = (one(intent, CALIBRATION_THRESHOLDS["intent"], intent_label_models)
                     if need_intent else {"status": "not_needed"})
    out["publish_ready"] = all(v["status"] in ("pass", "not_needed") for v in out.values()
                               if isinstance(v, dict))
    return out


# ---------------------------------------------------------------------------
# queues for the Modal runbook
# ---------------------------------------------------------------------------


def _queue_row(row: Mapping[str, Any], purpose: str) -> dict:
    """One row of the sandbox labeling queue for ``row``.

    Contracts are queued for ``cuad_clause_labels`` under the EX-10 source;
    other rows (correspondence) are queued for ``intent``. ``purpose`` is copied
    through, and ``ex10_remaining`` rows carry the ``pending_annotation`` code.
    """
    gt = row.get("gt_fields") or {}
    return {
        "id": row["filename"], "filename": row["filename"], "purpose": purpose,
        "doc_class": row["expected"], "expected_subclass": row.get("expected_subclass", ""),
        "split": row["split"], "context_window_band": gt.get("context_window_band", ""),
        "fields": ["cuad_clause_labels"] if row["expected"] == "contract" else ["intent"],
        "codes": ["pending_annotation"] if purpose == "ex10_remaining" else [],
        "source_corpus": EX10_SOURCE if row["expected"] == "contract" else row.get("source_corpus", ""),
        "source_dataset": EX10_SOURCE if row["expected"] == "contract" else "",
    }


def cuad_calibration_sample(rows: Sequence[Mapping[str, Any]], n: int = CUAD_CALIBRATION_SAMPLE) -> list[Mapping]:
    """Stratified (by subclass) deterministic sample of golden CUAD rows that
    fit the 32k window — the same envelope the EX-10 pass ran in."""
    return _cuad_stratified(rows, n, RANDOM_STATE, frozenset())


def cuad_holdout_sample(rows: Sequence[Mapping[str, Any]], n: int = CUAD_CALIBRATION_SAMPLE) -> list[Mapping]:
    """A second stratified sample from the same golden pool, disjoint from calibration:
    a prompt revised on calibration failures must also pass on contracts it never saw."""
    cal = frozenset(r["filename"] for r in cuad_calibration_sample(rows))
    return _cuad_stratified(rows, n, RANDOM_STATE + 1, cal)


def _cuad_stratified(rows: Sequence[Mapping[str, Any]], n: int, seed: int,
                     exclude: frozenset) -> list[Mapping]:
    pool = [r for r in rows if r["expected"] == "contract" and r["source_corpus"] == GOLDEN_CUAD_SOURCE
            and (r["gt_fields"].get("context_window_band") or "") != ">32k" and r["filename"] not in exclude]
    by_sub: dict[str, list] = defaultdict(list)
    for r in sorted(pool, key=lambda r: r["filename"]):
        by_sub[r.get("expected_subclass") or ""].append(r)
    rng = random.Random(seed)
    for v in by_sub.values():
        rng.shuffle(v)
    picked: list[Mapping] = []
    while len(picked) < min(n, len(pool)):
        for sub in sorted(by_sub):
            if by_sub[sub] and len(picked) < n:
                picked.append(by_sub[sub].pop())
    return sorted(picked, key=lambda r: r["filename"])


def export_queues(rows: Sequence[Mapping[str, Any]], out_dir: Path = QUEUE_DIR) -> dict[str, int]:
    """Write the four labeling queues as JSONL and return counts by filename."""
    out_dir.mkdir(parents=True, exist_ok=True)
    queues = {
        "ex10_remaining.jsonl": [_queue_row(r, "ex10_remaining") for r in rows
                                 if r["expected"] == "contract" and is_pending_cuad(r)],
        "cuad_calibration.jsonl": [_queue_row(r, "cuad_calibration") for r in cuad_calibration_sample(rows)],
        "intents_flagged.jsonl": [_queue_row(r, "intent_flagged") for r in flagged_intent_rows(rows)],
        "intents_calibration.jsonl": [_queue_row(r, "intent_calibration")
                                      for r in manual_intent_rows(rows)],
    }
    for name, q in queues.items():
        (out_dir / name).write_text("".join(safe_jsonl_line(x) + "\n" for x in q), encoding="utf-8")
    return {k: len(v) for k, v in queues.items()}


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------


def write_review_pack(rows: Sequence[Mapping[str, Any]], verdicts: Sequence[Mapping],
                      md_path: Path, csv_path: Path, frac: float = 0.2) -> int:
    """Write Markdown and CSV spot-checks of a deterministic CUAD label sample.

    ``frac`` is the fraction of ingested documents to sample, rounded to the
    nearest integer with at least one when any exist. Return the sample size;
    ``random.sample`` raises ``ValueError`` if it exceeds the population.
    """
    by_fn = {r["filename"]: r for r in rows}
    ingested = sorted(v["filename"] for v in verdicts if v["status"] == "ingested")
    rng = random.Random(RANDOM_STATE)
    k = max(1, round(len(ingested) * frac)) if ingested else 0
    sample = sorted(rng.sample(ingested, k)) if k else []
    lines = [
        "# v9.4 CUAD label spot-check pack",
        "",
        f"A random {frac:.0%} sample ({k} of {len(ingested)}) of SEC EDGAR EX-10 contracts whose "
        "`cuad_clause_labels` were filled from the Modal LLM journal in v9.4. Every span below "
        "already passed the automatic checks (verbatim at its offset, closed CUAD vocabulary, "
        "semantic probe, quality floor). A reviewer should confirm the span is the named clause.",
        "",
        "Mark each line in the CSV `reviewer_verdict` column as `ok`, `wrong_category` or "
        "`not_a_clause`.",
        "",
    ]
    csv_rows = []
    for fn in sample:
        row = by_fn[fn]
        labels = _json_obj(row["gt_fields"]["cuad_clause_labels"])
        lines += [f"## `{fn}`", "", f"subclass: {row.get('expected_subclass')}, "
                  f"model: `{row.get('annotation_model')}`", "",
                  "| category | offset | span |", "|---|---:|---|"]
        for name in sorted(labels):
            for span in labels[name]:
                text = " ".join(span["text"].split())
                short = text if len(text) <= 160 else text[:157] + "..."
                lines.append(f"| {name} | {span['start']} | {short.replace('|', '/')} |")
                csv_rows.append({"filename": fn, "category": name, "start": span["start"],
                                 "text": text, "reviewer_verdict": ""})
        lines.append("")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "category", "start", "text", "reviewer_verdict"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(csv_rows)
    return k


def render_audit_md(report: Mapping[str, Any]) -> str:
    """Render the human-readable dedup + label-completion audit from a build report.

    Covers every dedup group with its survivor basis (``rule`` or
    ``upstream_defect``, with the defect note and how to re-derive it),
    near-duplicates, placeholder scrubs, CUAD/intent ingestion counts,
    calibration status and what remains pending. Deterministic for a given
    report.
    """
    d = report["dedup"]
    lines = [
        "# v9.4 dedup + label-completion audit",
        "",
        f"Input: v9.3 stage ({EXPECTED_BASE_ROWS:,} rows). Output: {d['stats']['rows']:,} rows. "
        f"Generated by `src/mailroom_eda/v9_4_revision.py` (plan `{PLAN_REF}`).",
        "",
        "## Duplicates removed",
        "",
        "Survivor rule: richest annotation, then no copy marker such as `(1)`, then the `train` "
        "split, then the smallest filename. A verified upstream defect overrides the rule "
        "(basis `upstream_defect`, notes below). A test row whose normalized text equals a train "
        "row's is dropped (basis `cross_split_near_duplicate`; the sha shown is the survivor's).",
        "",
        "| content_sha256 | survivor | removed | basis | label conflict |",
        "|---|---|---|---|---|",
    ]
    for g in d["groups"]:
        conflict = ", ".join(f"`{f}`" for f in g["conflict_fields"]) or "none"
        removed = "<br>".join(f"`{x}`" for x in g["removed"])
        lines.append(f"| `{g['content_sha256'][:12]}` | `{g['survivor']}` | {removed} | "
                     f"{g.get('survivor_basis', 'rule')} | {conflict} |")
    for g in d["groups"]:
        ov = UPSTREAM_DEFECT_SURVIVORS.get(g["content_sha256"])
        if ov:
            ev = "; ".join(f"`{k}` {a}/{b} MAUD evidence excerpts found in the text"
                           for k, (a, b) in ov["evidence"].items())
            lines += ["", f"**`{g['survivor']}` kept ({g['survivor_basis']}).** {ov['note']}. "
                      f"Source: {ov['source']}. Evidence: {ev}. Recompute with "
                      "`scripts/audit/verify_maud_duplicate.py --check`."]
    lines += ["", f"Class deltas: {d['stats']['class_deltas']}. Splits after: {d['stats']['splits']}.",
              "", "## Near duplicates (report only)", ""]
    for g in d["groups"]:
        if g.get("kind") == NEAR_DUP_KIND:
            lines.append(f"- dropped from test: {', '.join(f'`{x}`' for x in g['removed'])} "
                         f"(normalized text equals train `{g['survivor']}`)")
    if report["near_duplicates"]:
        for g in report["near_duplicates"]:
            lines.append("- " + ", ".join(f"`{m['filename']}` ({m['split']})" for m in g["members"])
                         + (" — **crosses train/test**" if g.get("cross_split") else ""))
    else:
        lines.append("None: no two texts are equal after normalization without being byte-equal.")
    p = report["placeholders"]
    lines += ["", "## Placeholder strings scrubbed", "",
              f"{p['rows']} rows, {p['cells']} cells set from `nan`/`None`/`null` to `\"\"`. "
              f"Presence codes unchanged. By source and key:", ""]
    for k, v in sorted(p["by_source_key"].items()):
        lines.append(f"- `{k}`: {v}")
    c = report["cuad"]
    lines += ["", "## CUAD clause labels on SEC EDGAR EX-10", "",
              f"Journal: `{report['cuad_journal'] or 'none'}` (sha256 `{c.get('journal_sha256', '')}`, "
              f"models {c.get('journal_models', [])}). Approved labeler: `{LABEL_MODEL}`; records from "
              f"any other model are ignored ({c.get('unapproved_records', 0)} here). Labels are ingested "
              f"only after that model passes calibration. Every record's `document_id` must match the "
              f"row it is filed under. Outcome: {c.get('held') or c['counts'] or 'no journal'}.", "",
              "| filename | status | detail |", "|---|---|---|"]
    for v in sorted(c["verdicts"], key=lambda v: (v["status"], v["filename"])):
        if v["status"] == "ingested":
            detail = f"{v['present_categories']} categories, {v['spans']} spans, {len(v['dropped'])} dropped"
            if v.get("via_duplicate"):
                detail += f" (label from duplicate `{v['journal_filename']}`)"
        else:
            detail = "; ".join(v.get("reasons") or []) or "-"
        lines.append(f"| `{v['filename']}` | {v['status']} | {detail} |")
    dropped = Counter(x.split(":", 1)[1].strip() for v in c["verdicts"] for x in v.get("dropped") or [])
    if dropped:
        lines += ["", "Dropped spans by reason: " + ", ".join(f"{k}: {n}" for k, n in dropped.most_common())]
    i = report["intent"]
    lines += ["", "## Flagged correspondence intents", "",
              f"Outcome: {i.get('held') or i['counts'] or 'no journal'}."]
    cal = report["calibration"]
    floors = CALIBRATION_THRESHOLDS
    lines += ["", "## Calibration against expert labels (gates ingest and publish)", "",
              f"Model under test: `{LABEL_MODEL}`. Floors: CUAD {floors['cuad']} on the fixed "
              f"{CUAD_CALIBRATION_SAMPLE}-contract golden sample; intent {floors['intent']} on the "
              "manual correspondence labels.", "",
              f"- CUAD (category presence vs golden CUAD): {cal['cuad']}",
              f"- Intent (Cohen's kappa vs the 96 manual labels): {cal['intent']}",
              f"- **publish_ready: {cal['publish_ready']}**", ""]
    if report.get("cuad_calibration_detail"):
        det = report["cuad_calibration_detail"]
        lines += [f"CUAD detail: {det['documents']} docs, P {det['micro_precision']}, "
                  f"R {det['micro_recall']}, span overlap {det['span_overlap_rate']}.", ""]
    s = report["remaining"]
    lines += ["## Remaining gaps", "",
              f"- `cuad_clause_labels` pending_annotation: {s['pending_cuad']}",
              f"- `intent_status` flagged_review: {s['flagged_intents']}", ""]
    return "\n".join(lines)


def render_readme_addendum(report: Mapping[str, Any]) -> str:
    """Render the dataset-card section for v9.4 from a build report.

    Lists each removed duplicate with its survivor, any upstream-defect
    survivor choice, ingestion counts and calibration status, so the card
    states exactly what changed relative to v9.3.
    """
    d = report["dedup"]
    rows = "\n".join(
        f"| `{g['survivor']}` | {', '.join(f'`{x}`' for x in g['removed'])} |" for g in d["groups"])
    c = report["cuad"]["counts"]
    cal = report["calibration"]
    return (
        "## v9.4 dedup + label-completion revision\n\n"
        f"Rows: {EXPECTED_BASE_ROWS:,} -> {d['stats']['rows']:,} "
        f"(train {d['stats']['splits'].get('train', 0):,} / test {d['stats']['splits'].get('test', 0):,}). "
        f"Composition: {d['stats']['composition']}.\n\n"
        "**Duplicates removed.** Rows sharing a `content_sha256` were collapsed to one "
        "survivor (richest annotation, then no `(1)` copy marker, then `train`, then smallest "
        "filename). One group had copies in both splits. Survivors are byte-identical to v9.3; "
        "`dedup_removed.jsonl` lists every removed copy and any label conflict between copies.\n\n"
        f"| survivor | removed |\n|---|---|\n{rows}\n\n"
        + "".join(
            f"**Upstream defect.** `{g['survivor']}` is kept over {', '.join(f'`{x}`' for x in g['removed'])}: "
            f"{g['survivor_note']}. The removed row's labels did not describe its text.\n\n"
            for g in d["groups"] if g.get("survivor_basis") == "upstream_defect") +
        "".join(
            f"**Cross-split near-duplicate.** {', '.join(f'`{x}`' for x in g['removed'])} (test) is "
            f"dropped: its text equals train `{g['survivor']}` after whitespace normalization, so it "
            "would leak training text into test.\n\n"
            for g in d["groups"] if g.get("kind") == NEAR_DUP_KIND) +
        f"**Placeholder hygiene.** {report['placeholders']['cells']} `gt_fields` cells on "
        f"{report['placeholders']['rows']} insurance rows held the string `nan`; they are now `\"\"`.\n\n"
        f"**CUAD clause labels on SEC EDGAR EX-10.** {c.get('ingested', 0)} contracts now carry "
        f"LLM-produced CUAD clause spans from `{LABEL_MODEL}` (`annotation_method = llm_zero_shot`), "
        "ingested only after that model passed calibration against golden CUAD. Every span is a "
        "verbatim slice of `doc_text` at its `start` offset, re-verified at build time. Rows "
        "without verified labels stay "
        f"`pending_annotation` ({report['remaining']['pending_cuad']}).\n\n"
        f"**Calibration.** CUAD: {cal['cuad'].get('status')}; intent: {cal['intent'].get('status')}. "
        f"Plan: `{PLAN_REF}`.\n\n"
    )


def render_manifest_v9_4(old_manifest: str, sha_updates: Mapping[str, str], built_utc: str,
                         stats: Mapping[str, Any]) -> str:
    """Rewrite the v9.3 manifest text for the v9.4 stage.

    Keeps the v9.3 header lines except ``built_utc``/``revision``/
    ``labels_version`` (re-emitted for v9.4), updates the row/split/composition
    lines from ``stats`` and replaces sha256 entries with ``sha_updates``.
    Raises ``StopIteration`` if ``old_manifest`` has no ``sha256`` section.
    """
    lines = old_manifest.splitlines()
    sha_idx = next(i for i, ln in enumerate(lines) if ln.strip().rstrip(":").strip() == "sha256")
    comp = stats["composition"]
    header = []
    for ln in lines[:sha_idx]:
        if ln.startswith(("built_utc", "revision   :", "labels_version")):
            continue
        if ln.startswith("rows       :"):
            ln = (f"rows       : {stats['rows']} (train {stats['splits'].get('train', 0)}, "
                  f"test {stats['splits'].get('test', 0)})")
        elif ln.startswith("composition:"):
            ln = "composition: " + ", ".join(f"{k} {v}" for k, v in sorted(comp.items()))
        elif ln.startswith("configs    :"):
            ln = re.sub(r"ground_truth \d+", f"ground_truth {stats['rows']}", ln)
        header.append(ln)
        if ln.startswith("split_rule"):
            header += [
                f"built_utc  : {built_utc}",
                f"revision   : {REVISION_LINE}",
                f"labels_version: {LABELS_VERSION} ({stats['removed']} exact duplicates removed; "
                "survivors byte-identical to v9.3 except the documented label fills)",
            ]
    return "\n".join(
        header + ["sha256     :", *[f"  {k}  {v}" for k, v in sorted(sha_updates.items())], ""])


# ---------------------------------------------------------------------------
# staging
# ---------------------------------------------------------------------------


def _fix_dataset_info(src: Path, dst: Path) -> None:
    """Copy dataset_info.json, removing the stale v9.2 ``_published`` feature."""
    info = json.loads(src.read_text(encoding="utf-8"))
    info.get("features", {}).pop("_published", None)
    dst.write_text(json.dumps(info, ensure_ascii=False) + "\n", encoding="utf-8")


def stage_digest(stage_dir: Path) -> str:
    """sha256 over ``path  sha256`` lines of every staged file except
    ``manifest.txt`` (whose ``built_utc`` changes per build), so the report
    binds to the staged bytes and stays byte-identical across rebuilds."""
    h = hashlib.sha256()
    for p in sorted(stage_dir.rglob("*")):
        if p.is_file() and p.name != "manifest.txt":
            h.update(f"{p.relative_to(stage_dir).as_posix()}  {sha256_file(p)}\n".encode())
    return h.hexdigest()


def refuse_overlapping_stage(base_dir: Path, stage_dir: Path) -> None:
    """``stage_payload`` deletes ``stage_dir``; refuse when that would touch
    the v9.3 input (same directory, or one inside the other)."""
    b, s = base_dir.resolve(), stage_dir.resolve()
    if s == b or b.is_relative_to(s) or s.is_relative_to(b):
        raise SystemExit(f"refusing to stage into {stage_dir}: it overlaps the base payload {base_dir}")


def stage_payload(base_dir: Path, stage_dir: Path, rows: list[dict], raw_lines: Mapping[str, str],
                  touched: set[str], groups: Sequence[DedupGroup], report: Mapping[str, Any]) -> dict:
    """Replace the stage with the revised payload and return parquet counts.

    ``raw_lines`` preserves untouched rows; filenames in ``touched`` are
    serialized anew. Raise ``SystemExit`` if the stage overlaps the base.
    """
    from .v9_1_revision import upsert_readme_addendum
    from .v9_2_revision import stage_default_and_ground_truth, verify_blind_label_free

    refuse_overlapping_stage(base_dir, stage_dir)
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    with (stage_dir / "ground_truth_hardened.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write((safe_jsonl_line(r) if r["filename"] in touched else raw_lines[r["filename"]]) + "\n")
    (stage_dir / "dedup_removed.jsonl").write_text(
        "".join(safe_jsonl_line(g.as_dict()) + "\n" for g in groups), encoding="utf-8")
    for name in ("bundles.jsonl", "streams.jsonl", "fixtures.jsonl"):
        shutil.copyfile(base_dir / name, stage_dir / name)
    for cfg in ("bundles", "streams", "fixtures"):
        shutil.copytree(base_dir / "parquet" / cfg, stage_dir / "parquet" / cfg)
    counts = stage_default_and_ground_truth(rows, stage_dir)
    for cfg in ("default", "ground_truth"):
        _fix_dataset_info(base_dir / "parquet" / cfg / "dataset_info.json",
                          stage_dir / "parquet" / cfg / "dataset_info.json")
    verify_blind_label_free(stage_dir)
    card = (base_dir / "README.md").read_text(encoding="utf-8")
    (stage_dir / "README.md").write_text(upsert_readme_addendum(card, render_readme_addendum(report)),
                                         encoding="utf-8")
    sha_updates = {str(p.relative_to(stage_dir)): sha256_file(p)
                   for p in sorted(stage_dir.rglob("*")) if p.is_file() and p.name != "manifest.txt"}
    built_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = render_manifest_v9_4((base_dir / "manifest.txt").read_text(encoding="utf-8"),
                                    sha_updates, built_utc, report["dedup"]["stats"])
    (stage_dir / "manifest.txt").write_text(manifest, encoding="utf-8")
    return {f"{k[0]}/{k[1]}": v for k, v in counts.items()}


# ---------------------------------------------------------------------------
# conformance gates over a staged payload
# ---------------------------------------------------------------------------


def _gate(ok: bool, message: str) -> None:
    """Gate check that survives ``python -O`` (unlike ``assert``)."""
    if not ok:
        raise AssertionError(message)


CUAD_FILL_GT_KEYS = frozenset({"cuad_clause_labels", "label_evidence", "clause_count", "gt_presence"})
INTENT_FILL_GT_KEYS = frozenset({"intent", "intent_status", "intent_source", "intent_confidence",
                                 "gt_presence"})
ANNOTATION_KEYS = frozenset({"annotation_method", "annotation_model", "annotation_confidence"})


def drift_problems(base: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    """Why ``new`` is not an allowed v9.4 revision of the v9.3 row ``base``.

    Allowed changes, and only on the rows they apply to:

    * placeholder scrub: a ``gt_fields`` value that was ``nan``/``None``/
      ``null`` in v9.3 may become ``""`` on any row;
    * CUAD fill: ``CUAD_FILL_GT_KEYS`` on SEC EDGAR EX-10 contracts whose
      v9.3 ``cuad_clause_labels`` was ``pending_annotation``;
    * intent fill: ``INTENT_FILL_GT_KEYS`` on correspondence rows that were
      ``flagged_review`` in v9.3;
    * ``annotation_*`` only on a row that received a CUAD or intent fill.
    """
    fn = str(new.get("filename"))
    out: list[str] = []
    top = {k for k in set(base) | set(new) if base.get(k) != new.get(k)}
    if top - ANNOTATION_KEYS - {"gt_fields"}:
        out.append(f"{fn}: unexpected top-level change {sorted(top - ANNOTATION_KEYS - {'gt_fields'})}")
    bg, ng = base.get("gt_fields") or {}, new.get("gt_fields") or {}
    gt = {k for k in set(bg) | set(ng) if bg.get(k) != ng.get(k)}
    scrubbed = {k for k in gt if isinstance(bg.get(k), str)
                and bg[k].strip() in PLACEHOLDER_STRINGS and ng.get(k) == ""}
    rest = gt - scrubbed
    cuad_ok = (new.get("expected") == "contract" and new.get("source_corpus") == EX10_SOURCE
               and presence_of(base).get("cuad_clause_labels") == "pending_annotation")
    intent_ok = (new.get("expected") == "correspondence"
                 and bg.get("intent_status") == "flagged_review")
    allowed: set[str] = set()
    if cuad_ok:
        allowed |= CUAD_FILL_GT_KEYS
    if intent_ok:
        allowed |= INTENT_FILL_GT_KEYS
    if rest - allowed:
        out.append(f"{fn}: unexpected gt_fields change {sorted(rest - allowed)}")
    filled = bool(rest & (CUAD_FILL_GT_KEYS | INTENT_FILL_GT_KEYS) - {"gt_presence"})
    if top & ANNOTATION_KEYS and not filled:
        out.append(f"{fn}: annotation_* changed without a label fill")
    return out


def presence_gate_problems(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Gate (d): the stored ``gt_presence`` must equal a fresh recompute (so a
    stale ``pending_annotation`` cannot hide a populated map from gate (e)),
    satisfy the key-set law, and contain no ``genuine_gap``."""
    probs: list[str] = []
    for r in rows:
        stored = presence_of(r)
        if row_gt_presence(presence_probe(r)) != stored:
            probs.append(f"{r['filename']}: stored gt_presence differs from a recompute")
        probs += [f"{r['filename']}: {p}" for p in presence_law_problems(
            stored, r["expected"], r.get("expected_subclass"))]
        if "genuine_gap" in stored.values():
            probs.append(f"{r['filename']}: genuine_gap")
    return probs


def verify_stage(stage_dir: Path = STAGE_DIR_DEFAULT, base_dir: Path = BASE_STAGE_DIR) -> dict[str, Any]:
    """Verify stage conformance against the base and return gates and readiness.

    Raise ``AssertionError`` on a failed conformance gate. Calibration is
    read from the adjacent report when present and otherwise remains pending;
    a present report must match the stage digest.
    """
    import pandas as pd

    gates: dict[str, str] = {}
    rows = load_jsonl(stage_dir / "ground_truth_hardened.jsonl")
    lines = read_raw_lines(stage_dir / "ground_truth_hardened.jsonl")
    base_lines = {json.loads(ln)["filename"]: ln for ln in read_raw_lines(base_dir / "ground_truth_hardened.jsonl")}
    removed = load_jsonl(stage_dir / "dedup_removed.jsonl")
    removed_names = {fn for g in removed for fn in g["removed"]}

    # (a) row set; evidence-backed survivors first, so a wrong copy is named as such
    for g in removed:
        ov = UPSTREAM_DEFECT_SURVIVORS.get(g["content_sha256"])
        if ov is not None:
            _gate(g["survivor"] == ov["survivor"] and sorted(g["removed"]) == sorted(ov["removed"]),
                  f"group {g['content_sha256'][:12]}: survivor {g['survivor']} != upstream-defect override {ov['survivor']}")
    shas = Counter(r["content_sha256"] for r in rows)
    _gate(all(n == 1 for n in shas.values()), "duplicate content_sha256 in stage")
    _gate(len(rows) == len(base_lines) - len(removed_names), "row count")
    _gate(not removed_names & {r["filename"] for r in rows}, "removed row present")
    _gate(all(assign_split(r["filename"]) == r["split"] for r in rows), "split rule")
    base_sha = {fn: json.loads(ln)["content_sha256"] for fn, ln in base_lines.items()}
    staged_sha = {r["filename"]: r["content_sha256"] for r in rows}
    _gate(set(staged_sha) <= set(base_sha), "staged row not in the v9.3 base")
    base_rec = {fn: json.loads(ln) for fn, ln in base_lines.items()}
    staged_by_fn = {r["filename"]: r for r in rows}
    n_exact = n_near = 0
    for g in removed:
        surv = g["survivor"]
        kind = g.get("kind", EXACT_KIND)
        _gate(kind in (EXACT_KIND, NEAR_DUP_KIND), f"group {surv}: unknown kind {kind!r}")
        _gate(surv in staged_sha, f"survivor {surv} missing from stage")
        _gate(staged_sha[surv] == g["content_sha256"], f"survivor {surv}: sha differs from its group")
        for fn in g["removed"]:
            _gate(fn in base_sha, f"removed {fn} not in the v9.3 base")
            if kind == EXACT_KIND:
                n_exact += 1
                _gate(base_sha[fn] == staged_sha[surv], f"removed {fn} is not a byte-identical copy of {surv}")
                continue
            n_near += 1
            _gate(fn in EXPECTED_NEAR_DUP_TEST_DROPS, f"near-duplicate drop {fn} is not pinned")
            _gate(base_rec[fn]["split"] == "test", f"near-duplicate drop {fn} is not a test row")
            _gate(staged_by_fn[surv]["split"] == "train", f"near-duplicate survivor {surv} is not in train")
            # recomputed from the text, not read from the stored hash column
            norm = normalized_text_sha256(base_rec[fn]["doc_text"])
            _gate(norm == g.get("normalized_text_sha256")
                  == normalized_text_sha256(staged_by_fn[surv]["doc_text"]),
                  f"near-duplicate drop {fn}: normalized text differs from {surv}")
    _gate({fn for g in removed if g.get("kind") == NEAR_DUP_KIND for fn in g["removed"]}
          == set(EXPECTED_NEAR_DUP_TEST_DROPS), "near-duplicate test drops != pinned set")
    _gate(set(base_sha) == set(staged_sha) | removed_names, "rows vanished without a dedup record")
    gates["a_row_set"] = (f"PASS {len(rows)} rows, 0 duplicate shas, md5 split rule holds, "
                          f"{n_exact} exact copies and {n_near} cross-split near-duplicate test rows removed")

    # (b) survivors unchanged except documented fills on the rows they apply to
    untouched = changed = 0
    for ln, r in zip(lines, rows):
        base_ln = base_lines[r["filename"]]
        if ln == base_ln:
            untouched += 1
            continue
        changed += 1
        base_row = json.loads(base_ln)
        probs = drift_problems(base_row, r)
        _gate(not probs, "; ".join(probs[:3]))
        if r["gt_fields"].get("intent") != base_row["gt_fields"].get("intent") or (
                r["gt_fields"].get("intent_status") != base_row["gt_fields"].get("intent_status")):
            _gate(r.get("annotation_model") in APPROVED_LABEL_MODELS,
                  f"{r['filename']}: intent relabeled by unapproved model {r.get('annotation_model')!r}")
    gates["b_zero_drift"] = f"PASS {untouched} rows byte-identical to v9.3, {changed} within the fill allowlist"

    # (c) placeholders
    bad = sum(1 for r in rows for k, v in r["gt_fields"].items()
              if isinstance(v, str) and v.strip() in PLACEHOLDER_STRINGS)
    _gate(bad == 0, f"{bad} placeholder cells")
    gates["c_placeholders"] = "PASS 0 nan/None/null cells in gt_fields"

    # (d) presence law
    probs = presence_gate_problems(rows)
    _gate(not probs, "; ".join(probs[:5]))
    gates["d_presence_law"] = ("PASS stored presence equals a recompute on every row; "
                               "presence law holds; no genuine_gap")

    # (e) every populated EX-10 CUAD map is verbatim
    vocab = cuad_vocabulary(rows)
    ex10_pop = 0
    for r in rows:
        if r["expected"] == "contract" and r["source_corpus"] == EX10_SOURCE and not is_pending_cuad(r):
            labels = _json_obj(r["gt_fields"]["cuad_clause_labels"])
            clean, dropped, problems = verify_cuad_labels(r["doc_text"], labels, vocab)
            _gate(not dropped and not problems and clean == labels, f"{r['filename']}: {dropped or problems}")
            _gate(r.get("annotation_model") in APPROVED_LABEL_MODELS,
                  f"{r['filename']}: labeled by unapproved model {r.get('annotation_model')!r}")
            ex10_pop += 1
    gates["e_cuad_verbatim"] = (f"PASS {ex10_pop} EX-10 CUAD maps re-verify verbatim at their offsets, "
                                f"all from {LABEL_MODEL}")

    # (f) parquet == jsonl, every column of both configs
    from .v9_2_revision import BLIND_COLUMNS, GT_ROW_SCHEMA_FIELDS, gt_parquet_record

    by_fn = {r["filename"]: r for r in rows}
    n_gt = n_blind = 0
    info = json.loads((stage_dir / "parquet" / "ground_truth" / "dataset_info.json").read_text())
    for split in ("train", "test"):
        want = {r["filename"] for r in rows if r["split"] == split}
        df = pd.read_parquet(stage_dir / "parquet" / "ground_truth" / split / f"{split}-00000-of-00001.parquet")
        _gate(list(df.columns) == list(GT_ROW_SCHEMA_FIELDS), f"ground_truth/{split}: columns differ from schema")
        _gate(set(info["features"]) == set(df.columns), "dataset_info features != parquet columns")
        seen: set[str] = set()
        for rec in df.to_dict("records"):
            fn = rec["filename"]
            _gate(fn in want and fn not in seen, f"ground_truth/{split}: unexpected or repeated row {fn}")
            seen.add(fn)
            got = {k: (list(v) if k in ("relationships", "related_document_ids") else v) for k, v in rec.items()}
            exp = gt_parquet_record(by_fn[fn])
            diff = sorted(k for k in exp if got.get(k) != exp[k])
            _gate(not diff, f"ground_truth/{split} {fn}: columns differ from the JSONL: {diff}")
        _gate(seen == want, f"ground_truth/{split}: row set differs from the JSONL")
        n_gt += len(seen)
        shards = sorted((stage_dir / "parquet" / "default" / split).glob("*.parquet"))
        _gate(bool(shards), f"default/{split}: no parquet shard")
        seen = set()
        for shard in shards:
            bf = pd.read_parquet(shard)
            _gate(set(bf.columns) == BLIND_COLUMNS, f"default/{split}: blind columns {sorted(bf.columns)}")
            for rec in bf[["filename", "doc_text"]].to_dict("records"):
                fn = rec["filename"]
                _gate(fn in want and fn not in seen, f"default/{split}: unexpected or repeated row {fn}")
                seen.add(fn)
                _gate(rec["doc_text"] == by_fn[fn]["doc_text"], f"default/{split} {fn}: doc_text differs")
        _gate(seen == want, f"default/{split}: row set differs from the JSONL")
        n_blind += len(seen)
    _gate(n_gt == n_blind == len(rows), "parquet row count")
    gates["f_parquet_parity"] = (f"PASS {n_gt} ground_truth rows equal the JSONL on every column; "
                                 f"{n_blind} blind rows match on row set and doc_text, 4 label-free columns")

    # (g) manifest sha table
    manifest = (stage_dir / "manifest.txt").read_text(encoding="utf-8")
    table = re.findall(r"^\s*(\S+)\s+([0-9a-f]{64})\s*$", manifest, flags=re.M)
    _gate(bool(table), "manifest sha table missing")
    for rel, sha in table:
        _gate((stage_dir / rel).is_file() and sha256_file(stage_dir / rel) == sha, f"manifest sha mismatch: {rel}")
    staged_files = {p.relative_to(stage_dir).as_posix() for p in stage_dir.rglob("*")
                    if p.is_file() and p.name != "manifest.txt"}
    listed = [rel for rel, _ in table]
    _gate(len(listed) == len(set(listed)), "manifest lists a file twice")
    _gate(set(listed) == staged_files,
          f"manifest file set differs from the stage: missing {sorted(staged_files - set(listed))[:3]}, "
          f"extra {sorted(set(listed) - staged_files)[:3]}")
    _gate(f"labels_version: {LABELS_VERSION}" in manifest, "labels_version line missing")
    gates["g_manifest"] = f"PASS {len(table)} manifest sha entries recompute and cover every staged file"

    report_path = stage_dir.parent / "v9_4_report.json"
    calibration = {"publish_ready": False, "cuad": {"status": "not_run"}, "intent": {"status": "not_run"}}
    unverified: list[str] = []
    if report_path.exists():
        rep = json.loads(report_path.read_text(encoding="utf-8"))
        _gate(rep.get("stage_digest") == stage_digest(stage_dir),
              f"{report_path.name} was not written for these staged bytes (stage digest differs)")
        calibration, unverified = rederive_calibration(rep, rows)
    ready = bool(calibration["publish_ready"]) and not unverified
    gates["h_calibration"] = (f"{'PASS' if ready else 'PENDING'} "
                              f"cuad={calibration['cuad'].get('status')} intent={calibration['intent'].get('status')}"
                              + (f" (unverified: {', '.join(unverified)})" if unverified else ""))
    return {"gates": gates, "publish_ready": ready}


def calibration_source(path: Path | None) -> dict[str, str] | None:
    """Where a calibration journal came from: a portable path and its sha256.

    The path is relative to the parent of this repository when the journal
    sits under it (sibling checkouts), so the record carries no machine layout.
    """
    if not path:
        return None
    p = Path(path).resolve()
    root = ROOT.parent.resolve()
    shown = p.relative_to(root).as_posix() if p.is_relative_to(root) else str(path)
    return {"path": shown, "sha256": sha256_file(p)}


def _locate_journal(shown: str) -> Path | None:
    """Resolve a :func:`calibration_source` path back to a file, if present."""
    for cand in (ROOT.parent / shown, Path(shown)):
        if cand.is_file():
            return cand
    return None


def rederive_calibration(rep: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> tuple[dict, list[str]]:
    """Gate (h): recompute calibration instead of trusting the report.

    The status is re-derived with :func:`calibration_status` from the scored
    details, and each detail is re-scored from its journal against the staged
    expert rows. Raise ``AssertionError`` when the report's status, a score or
    a journal's sha256 disagrees. A journal that is not on this machine leaves
    that calibration unverified (returned in the list); the caller then keeps
    publishing blocked.
    """
    cuad_det, intent_det = rep.get("cuad_calibration_detail"), rep.get("intent_calibration_detail")
    status = calibration_status(cuad_det, intent_det, need_intent=bool(rep.get("intent_journal_used")),
                                cuad_label_models=APPROVED_LABEL_MODELS,
                                intent_label_models=APPROVED_LABEL_MODELS)
    _gate(status == rep.get("calibration"), "report calibration status does not re-derive from its scored details")
    sources = rep.get("calibration_journals") or {}
    unverified: list[str] = []
    for kind, det in (("cuad", cuad_det), ("intent", intent_det)):
        if not det:
            continue
        src = sources.get(kind)
        _gate(bool(src), f"{kind} calibration detail has no recorded journal")
        path = _locate_journal(src["path"])
        if path is None:
            unverified.append(f"{kind} journal {src['path']} not found")
            continue
        _gate(sha256_file(path) == src["sha256"], f"{kind} calibration journal changed since staging: {src['path']}")
        recs = [r for r in load_jsonl(path) if str(r.get("model") or "") in APPROVED_LABEL_MODELS]
        if kind == "cuad":
            again = score_cuad_calibration(cuad_calibration_sample(rows), recs, cuad_vocabulary(rows))
        else:
            again = score_intent_calibration(manual_intent_rows(rows), recs)
        _gate(again == det, f"{kind} calibration does not re-score from its journal")
    return status, unverified


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def journal_display_path(path: Path) -> str:
    """Shorten paths under a ``reports`` directory for display in reports.

    Paths without that component are returned as supplied.
    """
    parts = path.resolve().parts
    if "reports" in parts:
        i = len(parts) - 1 - parts[::-1].index("reports")
        return "/".join(parts[i - 1:]) if i > 0 else "/".join(parts[i:])
    return str(path)


def resolve_sandbox_journal(explicit: str | None) -> Path | None:
    """Find an explicit journal or the first existing default, if any.

    Raise ``FileNotFoundError`` when an explicit path is not a file.
    """
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise FileNotFoundError(p)
        return p
    return next((p for p in SANDBOX_JOURNAL_CANDIDATES if p.is_file()), None)


def build_all(
    stage_dir: Path = STAGE_DIR_DEFAULT,
    *,
    base_dir: Path = BASE_STAGE_DIR,
    cuad_journal: str | None = None,
    use_cuad_journal: bool = True,
    cuad_calibration_journal: str | None = None,
    intent_journal: str | None = None,
    intent_calibration_journal: str | None = None,
    write_audit: bool = True,
    publish: bool = False,
) -> dict[str, Any]:
    """Build the v9.4 stage from the v9.3 base and return its audit report.

    Calibration journals are scored first; a label journal is ingested only
    when its model is ``LABEL_MODEL`` and that model's calibration passed,
    otherwise it is recorded as held. The stage,
    adjacent report and queues are written; ``write_audit`` also writes the
    repository audit files. ``use_cuad_journal=False`` skips CUAD ingestion.
    Raise ``SystemExit`` for publishing, a missing base, or overlapping stage;
    validation failures raise ``AssertionError`` and file errors propagate.
    """
    if publish:
        raise SystemExit(f"v9.4 is a STAGING revision; publishing is gated off (see {PLAN_REF})")
    base_path = base_dir / "ground_truth_hardened.jsonl"
    if not base_path.exists():
        raise SystemExit(f"{base_path} missing: run scripts/build/build_v9_3_corporate_presence.py first")
    raw = read_raw_lines(base_path)
    base_rows = [json.loads(ln) for ln in raw]
    if len(base_rows) != EXPECTED_BASE_ROWS:
        raise AssertionError(f"v9.3 base has {len(base_rows)} rows, expected {EXPECTED_BASE_ROWS}")
    raw_by_fn = {r["filename"]: ln for r, ln in zip(base_rows, raw)}
    vocab = cuad_vocabulary(base_rows)

    # 1. dedup
    groups = plan_dedup(base_rows)
    exact_removed = removed_index(groups)
    groups += plan_near_dup_test_drops([r for r in base_rows if r["filename"] not in exact_removed])
    removed = removed_index(groups)
    kept = [r for r in base_rows if r["filename"] not in removed]
    removed_rows = [r for r in base_rows if r["filename"] in removed]
    sidecars = {n: (base_dir / n).read_text(encoding="utf-8")
                for n in ("bundles.jsonl", "streams.jsonl", "fixtures.jsonl")}
    hits = dangling_references(kept, removed_rows, sidecars)
    if hits:
        raise AssertionError(f"removed rows are still referenced: {hits[:5]}")
    dedup_stats = verify_dedup(base_rows, kept, groups)
    near = near_duplicate_groups(kept)
    if any(g["cross_split"] for g in near):
        raise AssertionError("a near-duplicate group still crosses train/test")
    aliases = journal_aliases(groups)

    touched: set[str] = set()

    # 2. placeholders
    ph_rows, ph_cells, ph_by = 0, 0, Counter()
    for r in kept:
        changed = scrub_placeholders(r)
        if changed:
            touched.add(r["filename"])
            ph_rows += 1
            ph_cells += len(changed)
            src = (r.get("metadata") or {}).get("source_dataset") or r.get("source_corpus")
            for k in changed:
                ph_by[f"{src} · {k}"] += 1
    print(f"scrub_placeholders — {ph_rows} rows, {ph_cells} cells")

    # 3. calibration first: nothing is ingested from a model that has not
    #    passed against expert labels
    cuad_cal = intent_cal = None
    if cuad_calibration_journal:
        cuad_cal = score_cuad_calibration(
            cuad_calibration_sample(kept),
            [r for r in load_jsonl(Path(cuad_calibration_journal))
             if str(r.get("model") or "") in APPROVED_LABEL_MODELS], vocab)
    if intent_calibration_journal:
        intent_cal = score_intent_calibration(
            manual_intent_rows(kept),
            [r for r in load_jsonl(Path(intent_calibration_journal))
             if str(r.get("model") or "") in APPROVED_LABEL_MODELS])
    calibration = calibration_status(
        cuad_cal, intent_cal, need_intent=bool(intent_journal),
        cuad_label_models=APPROVED_LABEL_MODELS, intent_label_models=APPROVED_LABEL_MODELS)

    # 4. CUAD journal (approved model only, and only once calibration passes)
    cuad_path = resolve_sandbox_journal(cuad_journal) if use_cuad_journal else None
    cuad_report: dict[str, Any] = {"counts": {}, "verdicts": []}
    if cuad_path:
        journal_records = load_jsonl(cuad_path)
        cuad_report["journal_sha256"] = sha256_file(cuad_path)
        cuad_report["journal_models"] = sorted({str(r.get("model") or "") for r in journal_records})
        if calibration["cuad"]["status"] == "pass":
            before = {r["filename"]: r["gt_fields"].get("cuad_clause_labels") for r in kept}
            cuad_report.update(ingest_cuad_journal(
                kept, journal_records, vocab, sha_aliases=aliases,
                document_ids={r["filename"]: str(r.get("document_id") or "") for r in base_rows}))
            touched |= {r["filename"] for r in kept
                        if r["gt_fields"].get("cuad_clause_labels") != before[r["filename"]]}
        else:
            cuad_report["held"] = f"CUAD calibration {calibration['cuad']['status']}; nothing ingested"
            print(f"ingest_cuad_journal — held: {cuad_report['held']}")

    # 5. intents (same rule)
    intent_report: dict[str, Any] = {"counts": {}, "verdicts": []}
    from .intent_backfill import CANONICAL_INTENTS

    if intent_journal:
        if calibration["intent"]["status"] == "pass":
            before = {r["filename"]: r["gt_fields"].get("intent_status") for r in kept}
            intent_report = ingest_intent_journal(kept, load_jsonl(Path(intent_journal)), CANONICAL_INTENTS)
            touched |= {r["filename"] for r in kept
                        if r["gt_fields"].get("intent_status") != before[r["filename"]]}
        else:
            intent_report["held"] = f"intent calibration {calibration['intent']['status']}; nothing ingested"
            print(f"ingest_intent_journal — held: {intent_report['held']}")

    remaining = {
        "pending_cuad": sum(1 for r in kept if is_pending_cuad(r)),
        "flagged_intents": len(flagged_intent_rows(kept)),
    }
    report = {
        "labels_version": LABELS_VERSION,
        "dedup": {"stats": dedup_stats, "groups": [g.as_dict() for g in groups]},
        "near_duplicates": near,
        "placeholders": {"rows": ph_rows, "cells": ph_cells, "by_source_key": dict(ph_by)},
        "cuad_journal": journal_display_path(cuad_path) if cuad_path else "",
        "cuad": cuad_report,
        "intent": intent_report,
        "calibration": calibration,
        "cuad_calibration_detail": cuad_cal,
        "intent_calibration_detail": intent_cal,
        "calibration_journals": {
            k: v for k, v in (("cuad", calibration_source(cuad_calibration_journal and Path(cuad_calibration_journal))),
                              ("intent", calibration_source(intent_calibration_journal
                                                            and Path(intent_calibration_journal)))) if v},
        "intent_journal_used": bool(intent_journal),
        "remaining": remaining,
    }

    staged = stage_payload(base_dir, stage_dir, kept, raw_by_fn, touched, groups, report)
    report["staged_counts"] = staged
    report["stage_digest"] = stage_digest(stage_dir)
    (stage_dir.parent / "v9_4_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    queues = export_queues(kept, stage_dir.parent / "queues")
    report["queues"] = queues
    if write_audit:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        export_queues(kept, AUDIT_DIR / "v9_4_queues")
        (AUDIT_DIR / "v9_4_dedup_label_audit.md").write_text(render_audit_md(report), encoding="utf-8")
        slim = {k: v for k, v in report.items() if k != "cuad"}
        slim["cuad"] = {"counts": cuad_report["counts"],
                        "journal_sha256": cuad_report.get("journal_sha256", ""),
                        "journal_models": cuad_report.get("journal_models", []), "verdicts": [
            {k: v for k, v in x.items() if k != "dropped"} | {"dropped": len(x.get("dropped") or [])}
            for x in cuad_report["verdicts"]]}
        (AUDIT_DIR / "v9_4_dedup_label_audit.json").write_text(
            json.dumps(slim, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        write_review_pack(kept, cuad_report["verdicts"], AUDIT_DIR / "v9_4_label_review.md",
                          AUDIT_DIR / "v9_4_label_review.csv")
    print(f"staged v9.4 payload under {stage_dir}: {staged}; remaining {remaining}; "
          f"publish_ready={calibration['publish_ready']}")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for the v9.4 staging build; returns the process exit code.

    Refuses an ``--intent-model`` outside ``APPROVED_LABEL_MODELS``. With
    ``--run-intent-pass`` it first labels the flagged and manual-calibration
    intents against ``VLLM_BASE_URL`` (exits if that or ``VLLM_API_KEY`` is
    unset), then stages via :func:`build_all`. ``--publish`` is refused:
    v9.4 is staging-only.
    """
    ap = argparse.ArgumentParser(description="v9.4 dedup + label-completion staging build")
    ap.add_argument("--stage-dir", type=Path, default=STAGE_DIR_DEFAULT)
    ap.add_argument("--base-dir", type=Path, default=BASE_STAGE_DIR)
    ap.add_argument("--cuad-journal", default=None,
                    help="sandbox EX-10 journal (default: sibling local-mailroom-sandbox checkout)")
    ap.add_argument("--no-cuad-journal", action="store_true", help="skip CUAD ingestion")
    ap.add_argument("--cuad-calibration-journal", default=None)
    ap.add_argument("--intent-journal", default=None)
    ap.add_argument("--intent-calibration-journal", default=None)
    ap.add_argument("--run-intent-pass", action="store_true",
                    help="label the flagged + manual intents now (needs VLLM_BASE_URL/VLLM_API_KEY)")
    ap.add_argument("--intent-model", default=LABEL_MODEL,
                    help=f"must be an approved labeler ({', '.join(sorted(APPROVED_LABEL_MODELS))})")
    ap.add_argument("--no-audit", action="store_true")
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args(argv)

    if args.intent_model not in APPROVED_LABEL_MODELS:
        raise SystemExit(f"--intent-model {args.intent_model!r} is not an approved labeler "
                         f"({', '.join(sorted(APPROVED_LABEL_MODELS))})")
    if args.run_intent_pass:
        base_url = (os.environ.get("VLLM_BASE_URL") or "").rstrip("/")
        api_key = os.environ.get("VLLM_API_KEY") or ""
        if not base_url or not api_key:
            raise SystemExit("--run-intent-pass needs VLLM_BASE_URL and VLLM_API_KEY")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        rows = load_jsonl(args.base_dir / "ground_truth_hardened.jsonl")
        run_intent_pass(flagged_intent_rows(rows), LLM_DIR / "intents_flagged.jsonl", base_url=base_url,
                        api_key=api_key, model=args.intent_model)
        run_intent_pass(manual_intent_rows(rows), LLM_DIR / "intents_calibration.jsonl", base_url=base_url,
                        api_key=api_key, model=args.intent_model)
        args.intent_journal = args.intent_journal or str(LLM_DIR / "intents_flagged.jsonl")
        args.intent_calibration_journal = (args.intent_calibration_journal
                                           or str(LLM_DIR / "intents_calibration.jsonl"))

    build_all(
        args.stage_dir,
        base_dir=args.base_dir,
        cuad_journal=args.cuad_journal,
        use_cuad_journal=not args.no_cuad_journal,
        cuad_calibration_journal=args.cuad_calibration_journal,
        intent_journal=args.intent_journal,
        intent_calibration_journal=args.intent_calibration_journal,
        write_audit=not args.no_audit,
        publish=args.publish,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
