#!/usr/bin/env .venv/bin/python
"""Live output-quality probe for one label prompt's mined documents.

Grades every model answer against the exact source bytes the prompt promises:
each list item's printed run must appear byte-for-byte (whitespace collapsed)
in the document text, and quote marks may only be added when the source prints
them. Parallel evidence to the mechanical smoke fixture (record/replay), which
proves plumbing, not quality. Spends real money: set OPENROUTER_API_KEY.

Usage:
  PYTHONPATH=src .venv/bin/python autoresearch/loop-*/quality_probe.py \
      --task claim_checklist --doc-tsv adjudication.csv --cap-usd 0.15

--doc-tsv: an adjudication-style CSV with a document_id column; the probe runs
every row's document (deduped) through both models of the disagreement pair.
Exit 0 when every graded answer is clean, 1 when any violation remains.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from mailroom_eda.labeling import providers, runner, tasks  # noqa: E402

WHITESPACE = re.compile(r"\s+")

# The disagreement pair the 8a pilot / adjudication.csv routed claim_checklist through.
MODELS = ("minimax/minimax-m3.1-flash", "deepseek/deepseek-v4.1-flash")


def printed_run_ok(doc_text: str, item: str, labels):
    """True when the item equals a printed run: a label prefix (if any) this exact byte
    string occurs in the document, and the model added no quote marks the source lacks."""
    if item in doc_text:
        return True  # copied whole, printed bytes
    collapsed = WHITESPACE.sub(" ", item)
    if collapsed in WHITESPACE.sub(" ", doc_text):
        return True
    # Quote-rule: a wrapped value "X" inside the item must be printed wrapped in the doc.
    for quoted in re.findall(r"\"([^\"]+)\"", item):
        inner = quoted.strip()
        if not inner:
            continue
        candidate = item.replace(f'"{inner}"', inner)  # unquoted variant
        if candidate not in doc_text and WHITESPACE.sub(" ", candidate) not in WHITESPACE.sub(" ", doc_text):
            return False  # quotes wrap text the source prints unwrapped
    # prefix-aware second look: the quoted line span (after "Category: ") printed?
    value = re.split(r"^?\s*[A-Za-z][^\n:~]{2,40}:\s", item)[-1] if len(item) <= 400 else item
    value = value.strip()
    if value and (value in doc_text or WHITESPACE.sub(" ", value) in WHITESPACE.sub(" ", doc_text)):
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="claim_checklist")
    ap.add_argument("--doc-tsv", required=True)
    ap.add_argument("--state", default=str(runner.DEFAULT_STAGE / "ground_truth_hardened.jsonl"))
    ap.add_argument("--cap-usd", type=float, default=0.15)
    ap.add_argument("--provider", default="openrouter")
    args = ap.parse_args()

    doc_text: dict[str, str] = {}
    with open(args.state, encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            doc_text[d["document_id"]] = d.get("doc_text") or d.get("text") or ""
    import csv
    doc_ids = []
    with open(args.doc_tsv, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["document_id"] not in doc_ids:
                doc_ids.append(r["document_id"])
    rows = {d["document_id"]: d for d in (json.loads(l) for l in open(args.state, encoding="utf-8"))}

    backend = providers.Backend.from_env(args.provider)
    # max_tokens per model, from the recorded insurance-claim fixture (the probe grades
    # the same builder the chunks use; a smaller ceiling would truncate long lists).
    fixture_manifest = REPO / "tests/fixtures/live_smoke/insurance_claim/manifest.json"
    per_model = json.loads(fixture_manifest.read_text()).get("max_tokens") if fixture_manifest.exists() else {}
    spent = 0.0
    report = {"task": args.task, "documents": len(doc_ids), "models": list(MODELS), "answers": []}
    clean_calls = bad_calls = 0
    for doc_id in doc_ids:
        row = rows.get(doc_id)
        if row is None:
            report["answers"].append({"document_id": doc_id, "error": "absent from the stage"})
            bad_calls += 1
            continue
        request = tasks.build(args.task, row)
        for model in MODELS:
            if spent >= args.cap_usd:
                report["answers"].append({"document_id": doc_id, "model": model,
                                          "error": "wall: spend cap reached"})
                bad_calls += 1
                continue
            try:
                completion = providers.chat(backend, model, request.messages, request.schema,
                                            reasoning="low",
                                            max_tokens=int(per_model.get(model, 8192)))
            except providers.ProviderError as exc:  # e.g. output truncated (finish_reason=length)
                report["answers"].append({"document_id": doc_id, "model": model,
                                          "error": "provider: " + str(exc)[:160]})
                bad_calls += 1
                continue
            spent += float(completion.cost_usd or 0.0)
            try:
                parsed = json.loads(completion.content)
            except json.JSONDecodeError:
                report["answers"].append({"document_id": doc_id, "model": model,
                                          "error": "unparseable", "text": completion.content[:200]})
                bad_calls += 1
                continue
            items = parsed.get("items") if isinstance(parsed, dict) else parsed
            doc = doc_text[doc_id]
            bad = [i for i in items or [] if isinstance(i, str) and not printed_run_ok(doc, i, labels=None)]
            entry = {"document_id": doc_id, "model": model, "items": len(items or []),
                     "violations": bad}
            report["answers"].append(entry)
            if bad:
                bad_calls += 1
            else:
                clean_calls += 1
    report["spent_usd"] = round(spent, 4)
    report["clean_calls"] = clean_calls
    report["bad_calls"] = bad_calls
    out = Path(__file__).parent / "quality_probe_report.json"
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"spent_usd": report["spent_usd"], "clean": clean_calls, "bad": bad_calls,
                      "report": str(out)}))
    return 1 if bad_calls else 0


if __name__ == "__main__":
    raise SystemExit(main())
