"""§13–§16 P2 matter/grouping tests (HUB-022, plan §14A)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mailroom_eda import matter as mt


def _corr(filename, message_id, in_reply_to=None, custodian="mbx", date="2001-05-01"):
    return {
        "filename": filename,
        "expected": "correspondence",
        "metadata": {
            "message_id": message_id,
            "in_reply_to": in_reply_to,
            "custodian": custodian,
            "date": date,
        },
    }


def test_thread_of_three_becomes_one_matter():
    rows = [
        _corr("m1", "<a@x>", custodian="k", date="2001-05-01"),
        _corr("m2", "<b@x>", in_reply_to="<a@x>", custodian="k", date="2001-05-02"),
        _corr("m3", "<c@x>", in_reply_to="<b@x>", custodian="k", date="2001-05-03"),
    ]
    groups = mt.source_native_threads(rows)
    assert {g["filename"] for g in groups} == {"m1", "m2", "m3"}
    assert len({g["matter_id"] for g in groups}) == 1
    root = next(g for g in groups if g["thread_position"] == 0)
    assert root["group_role"] == "correspondence"
    assert root["matter_construction"] == "source_native_thread"
    assert root["thread_size"] == 3
    replies = [g for g in groups if g["thread_position"] > 0]
    assert all(g["relationships"] == ["responds_to"] for g in replies)


def test_singletons_stay_unassigned():
    rows = [_corr("solo", "<x@x>", custodian="k")]
    assert mt.source_native_threads(rows) == []
    out = mt.enrich_rows(rows)
    assert out[0]["matter_id"] == "" and out[0]["group_role"] == ""
    assert out[0]["relationships"] == []


def test_cross_custodian_ids_are_isolated():
    # identical message-id text in two mailboxes must NOT join (maildir ids
    # are per-custodian); k1 is a singleton, j1+j2 form the only thread
    rows = [
        _corr("k1", "<same@id>", custodian="k"),
        _corr("k2", "<same@id>", custodian="j"),
        _corr("j1", "<reply@id>", in_reply_to="<same@id>", custodian="j", date="2001-05-02"),
    ]
    groups = mt.source_native_threads(rows)
    files = {g["filename"] for g in groups}
    assert files == {"k2", "j1"}
    assert len({g["matter_id"] for g in groups}) == 1


def test_reply_to_unknown_root_stays_unlinked():
    rows = [_corr("orphan", "<o@x>", in_reply_to="<missing@x>", custodian="k")]
    assert mt.source_native_threads(rows) == []


def test_string_metadata_rows_are_parsed():
    rows = [
        {"filename": "m1", "expected": "correspondence",
         "metadata": '{"message_id": "<a@x>", "custodian": "k", "date": "2001"}'},
        {"filename": "m2", "expected": "correspondence",
         "metadata": '{"message_id": "<b@x>", "in_reply_to": "<a@x>", "custodian": "k"}'},
    ]
    groups = mt.source_native_threads(rows)
    assert len(groups) == 2
    assert len({g["matter_id"] for g in groups}) == 1


def test_vocabularies_align_with_contract():
    assert "correspondence" in mt.GROUP_ROLES
    assert "responds_to" in mt.RELATIONSHIP_TYPES


def test_normalize_subject_strips_prefixes_and_degenerates():
    assert mt.normalize_subject("Re: [dle-hou] Fwd: Q3 numbers") == "q3 numbers"
    assert mt.normalize_subject("FW:") == ""          # degenerate: prefix-only
    assert mt.normalize_subject("") == ""
    assert mt.normalize_subject("Meeting") == "meeting"


def _subj(filename, subject, custodian="k", date="2001-05-01"):
    return {
        "filename": filename,
        "expected": "correspondence",
        "subject": subject,
        "metadata": {"custodian": custodian, "date": date},
    }


def test_subject_threads_group_same_subject_in_window():
    rows = [
        _subj("s1", "Q3 numbers", date="2001-05-01"),
        _subj("s2", "Re: Q3 numbers", date="2001-05-03"),
        _subj("s3", "Fwd: Q3 numbers", date="2001-05-05"),
    ]
    groups = mt.heuristic_subject_threads(rows)
    assert {g["filename"] for g in groups} == {"s1", "s2", "s3"}
    assert all(g["matter_construction"] == "heuristic_reconstructed" for g in groups)
    assert all(g["thread_evidence"].startswith("subject+custodian") for g in groups)
    assert len({g["matter_id"] for g in groups}) == 1
    assert next(g for g in groups if g["thread_position"] == 0)["filename"] == "s1"


def test_subject_threads_exclude_degenerate_and_out_of_window():
    rows = [
        _subj("d1", "Re:", date="2001-05-01"),            # degenerate — never groups
        _subj("o1", "Lunch?", date="2001-05-01"),
        _subj("o2", "Lunch?", date="2001-06-15"),          # same text, 45d later
    ]
    assert mt.heuristic_subject_threads(rows) == []


def test_subject_threads_never_cross_custodians():
    rows = [
        _subj("a1", "Contract", custodian="k", date="2001-05-01"),
        _subj("a2", "Contract", custodian="j", date="2001-05-02"),
    ]
    assert mt.heuristic_subject_threads(rows) == []


def test_enrich_rows_constructions_never_mixed():
    # header threads verified absent (0 matters); subject threads fill in
    rows = [
        _subj("m1", "Q3 numbers", date="2001-05-01"),
        _subj("m2", "Re: Q3 numbers", date="2001-05-02"),
        _subj("m3", "Lunch?", date="2001-05-01"),
    ]
    out = mt.enrich_rows(rows)
    constructions = {r["matter_construction"] for r in out}
    assert constructions <= {"heuristic_reconstructed", ""}
    assigned = [r for r in out if r["matter_construction"]]
    assert len(assigned) == 2 and len({r["matter_id"] for r in assigned}) == 1
    unassigned = [r for r in out if not r["matter_construction"]]
    assert unassigned[0]["filename"] == "m3" and unassigned[0]["matter_id"] == ""


def test_enrich_rows_refuses_header_threads_if_they_ever_appear():
    # the §14A never-mix-silently guard: in_reply_to chains must never
    # silently co-exist with subject threads
    rows = [
        _corr("h1", "<a@x>", in_reply_to=None, custodian="k", date="2001-05-01"),
        _corr("h2", "<b@x>", in_reply_to="<a@x>", custodian="k", date="2001-05-02"),
    ]
    try:
        mt.enrich_rows(rows)
        raised = False
    except AssertionError:
        raised = True
    assert raised


def test_live_snapshot_header_threads_are_structurally_absent(
    snapshot_rows, snapshot_metadata
):
    """§84B claim+check over the pinned corpus (HUB-022, HF audit 2026-09-02):
    the CMU maildir carries no In-Reply-To/References headers, so
    source-native header threads are structurally 0 — and the never-mix
    guard must hold over the real corpus."""
    corr = [r for r in snapshot_rows if r.get("expected") == "correspondence"]
    assert len(corr) == 350
    for row in corr:
        md = snapshot_metadata.get(row["filename"]) or {}
        assert str(md.get("in_reply_to") or "").strip() == ""  # the audited fact
    assert mt.source_native_threads(
        [{**r, "metadata": snapshot_metadata.get(r["filename"])} for r in corr]
    ) == []


def test_live_snapshot_subject_threads_reconstructed_and_separate(
    snapshot_rows, snapshot_metadata
):
    """§84B claim+check: subject threads exist, are labeled
    heuristic_reconstructed, and are counted separately — the honest §14A
    grouping numbers for the audit record."""
    corr = [r for r in snapshot_rows if r.get("expected") == "correspondence"]
    rows = [{**r, "metadata": snapshot_metadata.get(r["filename"])} for r in corr]
    with_subject = sum(1 for r in rows if mt.normalize_subject(mt._row_subject(r)))
    # verified floor (HF audit 2026-09-02): 346/350 dedup subjects non-empty,
    # ~80 of those degenerate Re:/FW:-only → ~266 meaningful — doc_text
    # extraction matches (266/350 live). The rest are genuinely subjectless.
    assert with_subject >= 260, f"subject extraction degraded: {with_subject}/350"
    out = mt.enrich_rows(rows)
    constructions = {r["matter_construction"] for r in out}
    assert constructions <= {"heuristic_reconstructed", ""}
    assigned = [r for r in out if r["matter_construction"]]
    matters = {r["matter_id"] for r in assigned}
    multi = [m for m in matters if sum(1 for r in assigned if r["matter_id"] == m) >= 2]
    print(
        f"\n§14A live audit: {with_subject}/350 subjects extracted; "
        f"{len(assigned)} rows in {len(matters)} heuristic threads "
        f"({len(multi)} multi-member); {len(out) - len(assigned)} unassigned"
    )
    assert all(
        r["thread_evidence"].startswith("subject+custodian") for r in assigned
    )
    assert all(r["group_role"] == "correspondence" for r in assigned)


def test_subject_thread_ids_stable_across_processes(snapshot_rows, snapshot_metadata):
    """Regression (pre-publish determinism gate, 2026-09-02): thread keys
    must not derive from str.hash() (PYTHONHASHSEED-salted). The ids for the
    same rows computed in a FRESH interpreter must equal this one's."""
    import subprocess

    corr = [r for r in snapshot_rows if r.get("expected") == "correspondence"]
    rows = [{**r, "metadata": snapshot_metadata.get(r["filename"])} for r in corr]
    here = sorted(
        (r["matter_id"], r["group_id"]) for r in mt.enrich_rows(rows)
        if r["matter_construction"]
    )
    assert here, "no subject threads in snapshot"
    tests_dir = str(Path(__file__).resolve().parent)
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, {tests_dir!r})\n"
        "from conftest import load_snapshot_rows, snapshot_metadata_map\n"
        "from mailroom_eda import matter as m\n"
        "rows = [r for r in load_snapshot_rows() if r.get('expected') == 'correspondence']\n"
        "mds = snapshot_metadata_map()\n"
        "rows = [{**r, 'metadata': mds.get(r['filename'])} for r in rows]\n"
        "print(json.dumps(sorted((r['matter_id'], r['group_id']) "
        "for r in m.enrich_rows(rows) if r['matter_construction'])))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=600,
        cwd=tests_dir,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    there = [tuple(pair) for pair in json.loads(result.stdout.strip().splitlines()[-1])]
    assert here == there, "thread ids re-keyed across processes (salted hash?)"
