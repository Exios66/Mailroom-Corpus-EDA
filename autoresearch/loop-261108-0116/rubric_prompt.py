#!/usr/bin/env python
"""Deterministic rubric score for the labeling prompt surface (mechanical working metric).

Higher is better: prints the weighted rubric coverage % over prompt bundles as built by
tasks.py (every extraction task sends ``extract_rules.md`` as system plus its own template),
one point per theme. The loop's Verify uses this number because it is fast, offline and
stable; independent acceptance of any kept change remains the paid gate (fixture re-record
or a calibration/trial run) — see loop-config.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[2] / "src/mailroom_eda/labeling/prompts"

#: task -> (weight, files in the bundle, themes); each theme is (regex, description).
BUNDLES: dict[str, tuple[int, list[str], list[tuple[str, str]]]] = {
    "contract_entities": (4, ["extract_rules.md", "contract_entities.md"], [
        (r'"value".*"status"', "schema key contract"),
        (r'[Nn]ever guess|never infer', "no-inference guard"),
        (r'max 300 chars', "evidence bound"),
        (r'entity_list:name', "entity_list array contract"),
        (r'\$\{family_definition\}', "subclass family context"),
        (r'\$\{family_field_hints\}', "family hints"),
        (r'never from the file name', "anti-provenance guard"),
        (r'party_roles', "subclass schema field"),
    ]),
    "merger_entities": (4, ["extract_rules.md", "merger_entities.md"], [
        (r'"status"', "status contract"),
        (r'as written|verbatim|copied', "verbatim guard"),
        (r'not_found when|not_found if|\[\] with not_found', "explicit absence"),
        (r'entity_list:name', "entity_list array contract"),
        (r'termination_fee', "subclass schema field"),
        (r'exchange_ratio', "subclass schema field"),
        (r'financial_advisors|legal_counsel', "subclass schema field"),
        (r'\$\{merger_intents\}', "label enum inject"),
    ]),
    "merger_consideration": (4, ["extract_rules.md", "merger_consideration.md"], [
        (r'One of: \$\{consideration_options\}', "enum from registry injects"),
        (r'evidence: the verbatim sentence', "verbatim sentence evidence"),
        (r'do\s*not\s*change the answer', "negative-inference guard"),
        (r'Say why in the evidence', "'other' requires a reason"),
        (r'mixed_cash_stock_election', "distinct election rule"),
        (r'in lieu of fractional shares', "fractional-share exclusion"),
    ]),
    # The 2026-11-08 "intent" bundle (sender-statement/imperative tie-break themes) was
    # DISCARDED: its paid kappa re-run scored 0.7356 vs the 0.8233 baseline (gate 0.75 failed);
    # the rubric only certifies themes that survived a live verification.
    "claim_checklist": (5, ["extract_rules.md", "claim_checklist.md"], [
        (r'\$\{checklist_categories\}', "registry vocabulary injects"),
        (r'"<Category>: <short verbatim evidence>"', "item shape contract"),
        (r'NEVER a JSON object|never nested quotes', "anti-nesting guard"),
        (r'[Nn]ever shorten|never stitch', "verbatim boundary guard"),
        (r'not_found', "explicit absence"),
        (r'run of spaces or tabs collapsed', "quote normalisation rule"),
        (r'bookkeeping lines', "corpus-echo exclusion"),
        (r'each item is one plain string', "single-line item rule"),
        (r'Category rules \(', "per-category operative rules"),
        (r'one line may serve two categories', "shared-line rule"),
        # themes calibrated from the 34 observed model disagreements (8a pilot fields.csv):
        (r'label prefix|prefix', "printed label prefix stays in the quote"),
        (r'typographic quote marks|enclosing quotes', "no enclosing quote marks unless printed"),
        # live-verified via adjudication.csv both-model probe (2026-11-08, it7):
        (r"Quote marks are copied, never added", "worked example bans adding quote pairs"),
        (r'different words|distinct lines', "distinct-line repeat rule"),
        (r'trailing parentheticals|trailing parenthetical', "service-line tail kept"),
        (r'first.{0,40}character to (?:its )?last|start.{0,20}to end', "whole-line span rule"),
    ]),
    "corporate": (3, ["extract_rules.md", "corporate.md"], [
        (r'NOT an\s*SEC|NOT an SEC', "separation from other id families"),
        (r'as printed', "verbatim guard"),
        (r'\$\{field_list\}', "registry field list injects"),
        (r'\$\{subclass_description\}', "subclass description inject"),
        (r'file, document or entity number', "filing-number disambiguation"),
    ]),
    "correspondence": (4, ["extract_rules.md", "correspondence.md"], [
        (r'\[\] with not_found', "empty-array absence"),
        (r'ISO YYYY-MM-DD', "date normalisation"),
        (r'at most 3', "action_items cap"),
        (r'never not_found', "label field must always answer"),
        (r'\$\{urgencies\}', "urgency enum inject"),
        (r'carry no headers', "header-absence guidance"),
        # live-verified themes (correspondence fixture re-record 2026-11-08, Ro1+TCM):
        (r"first signatory's full printed name", "multi-name sign-off keeps signatory #1 in full"),
        (r'newest message was sent', "same-day rule anchored to the newest printed send date"),
    ]),
    "intent": (3, ["intent_system.md", "intent_user.md"], [
        (r'closed vocabulary', "closed enum statement"),
        (r'Tie-breaks:', "tie-break rules"),
        (r'Output only \{"intent"', "json-only output contract"),
        (r'verbatim quote, max 200', "evidence bound"),
        (r'(?i)forwards? ?[:\-,]', "forward handling rule"),
        (r'use other only when', "'other' edge guard"),
    ]),
    "cuad": (5, ["cuad_system.md", "cuad_user.md", "cuad_repair.md"], [
        (r'character for character', "verbatim span copy"),
        (r'is never a span', "heading exclusion"),
        (r'When unsure, omit|costs less than a wrong one', "precision-over-recall guard"),
        (r'denies, disclaims or excludes', "negative-clause rule"),
        (r'\$\{category_list\}', "41-category list inject"),
        (r'Markdown marks', "formatting exception"),
        (r'\$\{family_definition\}', "subclass family context"),
        (r'Add no new categories', "repair no-invention guard"),
        (r'Document Name', "document-name metadata field"),
        # live-verified theme (contract fixture re-record 2026-11-08, it5):
        (r"bytes as printed|never smooth them away", "scrub artifacts stay in the quote"),
    ]),
}


def score(prompts_dir: Path = PROMPTS) -> tuple[float, float, int, list[str]]:
    """Measure weighted regex-theme coverage of the prompt files in ``prompts_dir``.

    Missing files contribute no text; decoding errors are ignored. Return the
    coverage percentage twice, the total possible theme weight, and missing
    themes prefixed by bundle name. Other file read errors propagate.
    """
    import re

    covered, total, missing = 0.0, 0, []
    for name, (weight, files, themes) in BUNDLES.items():
        text = " ".join((" ".join((prompts_dir / p).read_text(errors="ignore").split()))
                        for p in files if (prompts_dir / p).exists())
        hits = [desc for pattern, desc in themes if re.search(pattern, text)]
        covered += weight * len(hits)
        total += weight * len(themes)
        missing += [f"{name}: {d}" for d in (desc for _, desc in themes) if d not in hits]
    pct_out_of = 100.0 * covered / total
    return pct_out_of, pct_out_of, len(BUNDLES) * 0 + total, missing


if __name__ == "__main__":
    pct, _, _, missing = score()
    print(f"{pct:.0f}")
    for line in missing:
        print("MISSING", line, file=sys.stderr)
