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

from mailroom_eda.labeling import providers, registry, runner, tasks  # noqa: E402

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
    ap.add_argument("--task", default="claim_checklist",
                    help="label task; corporate grades scalar values, list tasks grade items")
    ap.add_argument("--doc-tsv", required=False,
                    help="adjudication-style CSV with a document_id column (list tasks)")
    ap.add_argument("--state", default=str(runner.DEFAULT_STAGE / "ground_truth_hardened.jsonl"))
    ap.add_argument("--corporate-labels", required=False,
                    help="10.1-style corporate-labels.jsonl: texts come from the recorded gold docs")
    ap.add_argument("--cap-usd", type=float, default=0.15)
    ap.add_argument("--provider", default="openrouter")
    args = ap.parse_args()

    doc_text: dict[str, str] = {}
    doc_ids: list[str] = []
    rows: dict[str, dict] = {}
    report: dict = {"task": args.task, "models": list(MODELS), "answers": []}
    if args.corporate_labels:
        if args.task != "corporate":
            print("--corporate-labels requires --task corporate"); return 1
        import hashlib
        labels_path = Path(args.corporate_labels)
        docs_dir = REPO / "data/v10/kl3m/gold/docs"
        for line in open(labels_path, encoding="utf-8"):
            rec = json.loads(line)
            doc_id = rec["doc_file"].removeprefix("docs/")
            text = (docs_dir / doc_id).read_text(encoding="utf-8")
            digest = hashlib.sha256(text.encode()).hexdigest()
            if rec.get("text_sha256") and digest != rec["text_sha256"]:
                report.setdefault("sha_mismatches", []).append(doc_id)
            doc_text[doc_id] = text
            sub = rec.get("v10_subclass") or "other"
            if sub not in registry.dojo_snapshot()["corporate_subclass_schemas"]:
                # the draw names pre-v10 aliases; map through record_form into the pinned registry
                sub = {"securities_rights": "rights_instrument",
                       "original_charter": "certificate_of_formation" if rec.get("record_form") == "certificate_of_formation"
                                           else "articles_of_incorporation",
                       "governance_record": {"officer_certificate": "officer_certificate",
                                             "board_resolution": "board_resolution"}.get(rec.get("record_form"), "other"),
                      }.get(sub, "other")
            rows[doc_id] = {"document_id": doc_id, "doc_text": text, "expected": "corporate_record",
                            "expected_subclass": sub, "filename": rec["doc_file"]}
            doc_ids.append(doc_id)
    else:
        if not args.doc_tsv:
            print("one of --doc-tsv or --corporate-labels is required"); return 1
        with open(args.state, encoding="utf-8") as fh:
            for line in fh:
                d = json.loads(line)
                doc_text[d["document_id"]] = d.get("doc_text") or d.get("text") or ""
        import csv
        with open(args.doc_tsv, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["document_id"] not in doc_ids:
                    doc_ids.append(r["document_id"])
        rows = {d["document_id"]: d for d in (json.loads(l) for l in open(args.state, encoding="utf-8"))}

    try:
        backend = providers.Backend.from_env(args.provider)
    except providers.ProviderError as exc:
        print(f"provider setup failed: {exc}", file=sys.stderr)
        return 1
    # max_tokens per model, from the recorded insurance-claim fixture (the probe grades
    # the same builder the chunks use; a smaller ceiling would truncate long lists).
    fixture_manifest = REPO / "tests/fixtures/live_smoke/insurance_claim/manifest.json"
    per_model = json.loads(fixture_manifest.read_text()).get("max_tokens") if fixture_manifest.exists() else {}
    spent = 0.0
    report["documents"] = len(doc_ids)
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
                spent += float((exc.usage or {}).get("cost_usd") or 0.0)  # billed even when it failed
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
            if args.task == "corporate":
                # corporate answers a {field: scalar|list} dict: grade every value printed-run
                bad = []
                size = 0
                for field, value in (parsed or {}).items():
                    vals = value if isinstance(value, list) else [value]
                    for v in vals:
                        size += 1
                        if isinstance(v, str) and v and not printed_run_ok(doc, v, None):
                            bad.append(f"{field}: {v}")
                entry = {"document_id": doc_id, "model": model, "items": size,
                         "violations": bad, "parsed": parsed}
            else:
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
