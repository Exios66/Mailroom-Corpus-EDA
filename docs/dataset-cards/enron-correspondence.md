# Enron Correspondence — docclass-merged source card

> `correspondence` · 350 rows (21.2% of the corpus) · 8 strata · train 310 / test 40
> · license **research use (inherited from the CMU Enron Email Dataset)** ·
> one of the five source corpora of
> [`Lucius-Morningstar/docclass-merged`](https://huggingface.co/datasets/Lucius-Morningstar/docclass-merged)

## Identity

| Field | Value |
|---|---|
| doc_type | `correspondence` |
| Rows | 350 (21.2% of 1,650): v4 drew 110, v6 appended 240 |
| Splits | train 310 / test 40 (`md5(filename) % 10 == 0 → test`; each email keeps its split across this dataset and the dedup corpus) |
| Strata | 8 `expected_subclass` values: demand 51, notice 50, email 50, letter 49, meeting_request 49, press_release 49, memo 49, attorney_demand 3 |
| Content topics | 11 (general_business 165, energy_market 40, marketing_clients 32, hr_personnel 36, legal_contracts 21, regulatory 20, scheduling 19, finance_earnings 5, announcements 4, it_systems 4, travel_logistics 4) |
| Provenance keys | `metadata.source = cmu_enron_maildir`, `metadata.source_dataset = Lucius-Morningstar/enron-correspondence-dedup`, `metadata.license = "Enron corpus — released for research use"` |
| Entered at | v4 (+110) and v6 (+240); v7 hydrated `intent` on all rows |
| License | Research use only; contains real PII of Enron employees |

## Full context

The correspondence block is drawn from the **CMU Enron Email Dataset**
(Klimt & Yang, 2004) — the public release of ~500k messages produced during
the FERC investigation of Enron Corporation — specifically from the family's
deduplicated and enriched derivative
[`Lucius-Morningstar/enron-correspondence-dedup`](https://huggingface.co/datasets/Lucius-Morningstar/enron-correspondence-dedup)
(247K rows after the family dedup rule; labelers from
[Enron-Evaluation-Environment](https://github.com/Exios66/Enron-Evaluation-Environment)).

Sampling was **deterministic at every step** and is recorded per row in
`metadata.sample_method`: the v4 draw took a stratified sha256(filename)
draw of 110 from 222,572 dedup rows; the v6 rebalance appended a stratified
draw of 240 from 247,413 (quota 30) after excluding every existing
filename. The shared Enron labelers (subclass / content-topic / sentiment)
were re-run on every v6 row as a verification pass and reproduce the Hub
ground truth exactly; the KANBAN-103 phrase-lexicon GT overrides are
honored. Per-email provenance (custodian, folder, date, sender,
message-id, in-reply-to, attachment flag) rides along in `metadata`.

At v7 (issue #5) every row was additionally hydrated with a canonical
**8-class `intent`** label plus provenance columns — see Intent hydration
below. This makes correspondence the only multi-task subset of the corpus:
doc subclass, content topic, intent, and sentiment all supervise the same
rows.

## Source material

| Layer | Where |
|---|---|
| Original download | CMU Enron Email Dataset — <https://www.cs.cmu.edu/~enron/> (Bryan Klimt & Yiming Yang, Carnegie Mellon University, 2004) |
| Family derivative | [`Lucius-Morningstar/enron-correspondence-dedup`](https://huggingface.co/datasets/Lucius-Morningstar/enron-correspondence-dedup) — sha256-verified parquet shards the draw was made from |
| Provenance-join mirrors (v7) | [`snoop2head/enron_aeslc_emails`](https://huggingface.co/datasets/snoop2head/enron_aeslc_emails) (535k mails) and [`Yale-LILY/aeslc`](https://huggingface.co/datasets/Yale-LILY/aeslc) — used **only** for sha256 exact-body provenance joins and recovered subject lines; they carry no intent annotations |
| Labelers | [Enron-Evaluation-Environment](https://github.com/Exios66/Enron-Evaluation-Environment) — `correspondence_subclasses.py`, `content_topics.py`, `sentiment_scorer.py`, `dedupe.py` |
| Form in docclass-merged | maildir text in `doc_text` (the maildir path is the filename); no separate original file — the mail text is the original |

## Attribution

> Klimt, Bryan, and Yiming Yang. "The Enron Corpus: A New Dataset for Email
> Classification Research." *ECML 2004*, pp. 217–226.

```bibtex
@inproceedings{klimt2004enron,
  title     = {The Enron Corpus: A New Dataset for Email Classification Research},
  author    = {Klimt, Bryan and Yang, Yiming},
  booktitle = {European Conference on Machine Learning (ECML 2004)},
  pages     = {217--226},
  year      = {2004}
}
```

For the v7 AESLC provenance join, also cite:

> Zhang, Rui, and Joel Tetreault. "This Email Could Save Your Life:
> Introducing the Task of Email Subject Line Generation." *ACL 2019*,
> pp. 446–456. <https://arxiv.org/abs/1906.03497>

```bibtex
@inproceedings{zhang-tetreault-2019-email,
  title     = "This Email Could Save Your Life: Introducing the Task of Email Subject Line Generation",
  author    = "Zhang, Rui and Tetreault, Joel",
  booktitle = "Proceedings of the 57th Annual Meeting of the Association for Computational Linguistics",
  year      = "2019",
  pages     = "446--456",
}
```

**License note (binding):** the CMU Enron Email Dataset is released for
**research use** and contains real personally identifying information of
Enron employees. These terms are inherited by every correspondence row —
flagged per-row in `metadata.license` and `metadata.source_dataset`. No
redistribution of raw PII outside research contexts; no production or
consumer use of the correspondence subset.

## Purpose in docclass-merged

1. **doc_type supervision** — 350 gold `correspondence` labels across 8
   mail subtypes, including the hard minority `attorney_demand` (3 rows, an
   honest gap: the dedup corpus carries no more beyond the v4 sample).
2. **Multi-task head targets** — the only subset supervised on four axes at
   once: `expected_subclass` (8), `content_topic` (11), `intent` (8-class
   canonical vocabulary), and `sentiment_score`/`sentiment_label`
   (neutral 178 / positive 97 / negative 75) with per-label evidence
   strings (`label_evidence`, `topic_evidence`, `sentiment_evidence`) on
   the `ground_truth` config.
3. **Agentic triage realism** — short, noisy, conversational text balancing
   the formal legal blocks; the direct supervision surface for
   [LLM Mailroom](https://github.com/Exios66/llm-mailroom) email triage.
4. **Intent taxonomy anchor** — the canonical 8-class intent vocabulary
   (`payment_demand`, `notice`, `analysis`, `request`, `update`,
   `meeting_invite`, `press_communication`, `other`) defined for this block
   is the family-wide `INTENT_LABELS` reference (see
   `src/mailroom_eda/intent_backfill.py`).

## Intent hydration (v7, issue #5)

All 350 rows carry a non-null canonical intent — **100.0% coverage** — with
three provenance columns on the `ground_truth` config
(`intent_source` / `intent_confidence` / `intent_status`).
`intent_source` records the **hydration path**; the three values are
disjoint and sum to 350:

| `intent_source` | Rows | Mechanism |
|---|---:|---|
| `manual` | 96 | purpose-GT labeling push (llm-mailroom, 2026-08-30) |
| `aeslc_join` | 162 | join-assisted hydration: a sha256 exact normalized-body match against the Enron/AESLC mirrors routes the row through the assisted pass — the join supplies row provenance + the recovered `subject_line` used as constrained context |
| `llm_zero_shot` | 92 | constrained zero-shot pass without a join hit, OpenRouter `deepseek/deepseek-chat`, temperature 0.1, closed 8-class vocabulary |

The mirrors carry **no** intent annotations (verified 2026-08-31): every
label is assigned under the closed vocabulary during the labeling pass, so
`aeslc_join` marks the path a row's label came through — not a mirror-side
label origin. Statuses (`intent_status`): `manual` 96, `auto_labeled` 253,
`flagged_review` 1 (confidence < 0.85 threshold → manual review queue).
The `other` class is the explicit fallback (22 rows), never null.

Intent distribution (v7 EDA): notice 74, request 73, meeting_invite 57,
press_communication 51, update 51, other 22, analysis 12, payment_demand
10. Every canonical class appears in the 10% test split (test sources:
aeslc_join 26, llm_zero_shot 14). The checkpointed backfill is reproducible
via `scripts/backfill_intent.py` (never hand-edit
`data/backfill/intent_labels.jsonl`).

## Subset statistics (v7 EDA)

- Text length (chars): mean 2,470 · p50 1,230 · p95 6,101 · max 104,836 —
  the short-text pole of the corpus (bodies < 80 chars were excluded from
  sampling; empty bodies never win a slot).
- Sentiment: neutral 178 / positive 97 / negative 75.
- Topic × intent crosstab:
  [`reports/tables/correspondence_topic_intent.csv`](../../reports/tables/correspondence_topic_intent.csv)
  (e.g. payment_demand concentrates in marketing_clients; meeting_invite in
  general_business and scheduling).

## Caveats & limitations

- **Weak heuristic labels**: subclass / content-topic / sentiment labels are
  deterministic lexicon and marker-taxonomy functions, human spot-checked
  upstream — single-topic assignment for multi-topic emails, head-window
  scanning, no sarcasm detection. Treat them as routing priors, not gold.
  The v7 `intent` labels inherit this: 254/350 came from a zero-shot LLM
  pass (confidence-thresholded at 0.85), 1 row remains flagged for review.
- **Research-use / PII**: real employee names, addresses and content;
  research contexts only (see Attribution).
- **Subclass skew**: `attorney_demand` has only 3 rows and zero test rows;
  the remaining 7 subtypes are balanced (~49–51 each).
- **Time-locked**: all mail predates the 2004 release; language and
  formatting are early-2000s corporate email.

## Cross-references

- Sibling cards: [CMS DE-SynPUF insurance claims](cms-desynpuf-insurance-claims.md)
  (the other corpus appended post-v3), [CUAD contracts](cuad-contracts.md)
- EDA figures: `20`–`22` (`reports/figures/`) — correspondence topic,
  intent, sentiment
- Backfill module: `src/mailroom_eda/intent_backfill.py`; CLI:
  `scripts/backfill_intent.py --check`
- Full corpus: <https://huggingface.co/datasets/Lucius-Morningstar/enron-correspondence-dedup>
