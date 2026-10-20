# v9.1 ground-truth backfill and quality

Dataset `Lucius-Morningstar/mailroom-dataset` tag `v9.1` (`bc9eab280044befb51e19dda3071d290a8677f42`), data commit `ed7576b676343e0b402ec5412cded301e629bdee`. 3302 rows.

Golden CUAD clause maps and golden MAUD clause maps are not graded. The unfinished queue is pending annotation plus any structure or format error. Heuristic provenance and thin subjects are listed as later upgrades, not as this queue.

## Counts

- unfinished backfill findings: 91
- structure/format errors: 0
- quality reviews (not in the spend queue): 302
- heuristic provenance notes: 796
- documents in the spend queue: 91
- chunks of 40: 3

Each chunk is one labeling invocation on two L4s (`Qwen/Qwen3-14B-AWQ`, 16 requests in flight) with a $2 projected cap. Do not submit the queue as one job.

## Cells

| severity | class | field | code | count |
|---|---|---|---|---|
| backfill | `contract` | `cuad_clause_labels` | `pending_annotation` | 91 |
| info | `contract` | `intent_source` | `heuristic_provenance` | 91 |
| info | `corporate_record` | `intent_source` | `heuristic_provenance` | 450 |
| info | `correspondence` | `intent_source` | `heuristic_provenance` | 105 |
| info | `insurance_claim` | `intent_source` | `heuristic_provenance` | 150 |
| review | `correspondence` | `intent_status` | `flagged_review` | 25 |
| review | `correspondence` | `subject_matter` | `thin_subject` | 277 |

## Chunks

- chunk 0: 40 documents, bands {'<=4k': 13, '<=16k': 21, '<=32k': 6}
- chunk 1: 40 documents, bands {'<=16k': 15, '>32k': 1, '<=4k': 23, '<=32k': 1}
- chunk 2: 11 documents, bands {'<=4k': 8, '<=16k': 3}
