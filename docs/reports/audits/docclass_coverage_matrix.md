# docclass coverage matrix (plan §40–§41)

Generated from the local pinned snapshot — 3302 rows, 5 classes, 55 class × subclass strata.

## Class view (§40)

| class | rows | strata | source | specialist |
|---|---|---|---|---|
| `contract` | 600 | 26 | `theatticusproject/cuad` | `contracts_specialist` |
| `corporate_record` | 450 | 10 | `sec_edgar` | `corporate_records_specialist` |
| `correspondence` | 1000 | 8 | `Lucius-Morningstar/enron-correspondence-dedup` | `correspondence_specialist` |
| `insurance_claim` | 1100 | 6 | `cms_desynpuf (+ GNOTHEIA, BDR, INSURBIAS)` | `insurance_claims_specialist` |
| `merger_agreement` | 152 | 5 | `maud` | `contracts_specialist` |

## Field coverage per specialist (§41)

Coverage is reported over **eligible rows only** (populated / eligible, eligible = populated + genuine_gap + pending_annotation). Unpopulated cells are classified `schema_documented_absence` — the v8_build/v9 conformance law says the field is empty on this row (e.g. `adjuster` on CMS/GNOTHEIA/INSURBIAS rows, `denial_reasons` on non-denied claims, `supporting_documents` on the 6 INSURBIAS bare-accident narratives that reference no supporting-document feature — issue #29), excluded from eligible (absent by design, never a coverage target) — or `pending_annotation` — a genuine, catalogued gap blocked on a dependency (`cuad_clause_labels` on the 91 SEC EDGAR EX-10 contracts — issue #30: the LLM clause pass could not run on 2026-09-13, no working provider credential; see the pending_rules entry), counted AGAINST coverage_pct — or `genuine_gap` — the field should be populated and no rule explains the absence (mailroom-issues#196 Phase B2: after issues #28/#29 the corpus reports zero undiagnosed genuine gaps; the EX-10 clause hole is now honestly `pending_annotation`, not silently folded into `schema_documented_absence`). Documented absences are tallied but never counted as gaps (issue #28).

### `contract` (600 rows, `contracts_specialist`)

| field | populated | eligible | documented-absent | pending-annotation | genuine gap | coverage |
|---|---|---|---|---|---|---|
| `cuad_clause_labels` | 509 | 600 | 0 | 91 | 0 | 85% |

### `corporate_record` (450 rows, `corporate_records_specialist`)

| field | populated | eligible | documented-absent | pending-annotation | genuine gap | coverage |
|---|---|---|---|---|---|---|
| `intent` | 450 | 450 | 0 | 0 | 0 | 100% |
| `keywords` | 450 | 450 | 0 | 0 | 0 | 100% |
| `subject_matter` | 450 | 450 | 0 | 0 | 0 | 100% |

### `correspondence` (1000 rows, `correspondence_specialist`)

| field | populated | eligible | documented-absent | pending-annotation | genuine gap | coverage |
|---|---|---|---|---|---|---|
| `content_topic` | 1000 | 1000 | 0 | 0 | 0 | 100% |
| `intent` | 1000 | 1000 | 0 | 0 | 0 | 100% |
| `keywords` | 1000 | 1000 | 0 | 0 | 0 | 100% |
| `sentiment_label` | 1000 | 1000 | 0 | 0 | 0 | 100% |
| `subject_matter` | 1000 | 1000 | 0 | 0 | 0 | 100% |

### `insurance_claim` (1100 rows, `insurance_claims_specialist`)

| field | populated | eligible | documented-absent | pending-annotation | genuine gap | coverage |
|---|---|---|---|---|---|---|
| `adjuster` | 150 | 150 | 950 | 0 | 0 | 100% |
| `claim_number` | 1100 | 1100 | 0 | 0 | 0 | 100% |
| `claim_type` | 1100 | 1100 | 0 | 0 | 0 | 100% |
| `claimed_amount` | 1100 | 1100 | 0 | 0 | 0 | 100% |
| `coverage_determination` | 1100 | 1100 | 0 | 0 | 0 | 100% |
| `damages_description` | 1100 | 1100 | 0 | 0 | 0 | 100% |
| `date_filed` | 1100 | 1100 | 0 | 0 | 0 | 100% |
| `date_of_loss` | 1100 | 1100 | 0 | 0 | 0 | 100% |
| `denial_reasons` | 36 | 36 | 1064 | 0 | 0 | 100% |
| `insured_party` | 1100 | 1100 | 0 | 0 | 0 | 100% |
| `insurer` | 1100 | 1100 | 0 | 0 | 0 | 100% |
| `intent` | 1100 | 1100 | 0 | 0 | 0 | 100% |
| `keywords` | 1100 | 1100 | 0 | 0 | 0 | 100% |
| `policy_number` | 1100 | 1100 | 0 | 0 | 0 | 100% |
| `subject_matter` | 1100 | 1100 | 0 | 0 | 0 | 100% |
| `supporting_documents` | 1094 | 1094 | 6 | 0 | 0 | 100% |

### `merger_agreement` (152 rows, `contracts_specialist`)

| field | populated | eligible | documented-absent | pending-annotation | genuine gap | coverage |
|---|---|---|---|---|---|---|
| `maud_clause_labels` | 152 | 152 | 0 | 0 | 0 | 100% |

## Documented absences (§v8_build/v9 conformance law)

Rows classified `schema_documented_absence` are **not coverage gaps**: the conformance law documents the field empty on them by design — they never close. Rules (with code citations; full text in the JSON):

- **`insurance_claim.adjuster`** — 950 rows classified documented-absence; 0 populated rows match the absence predicate (0 = conformance-clean). v8_build.py ALLOWED_EMPTY = {'adjuster'}; v9_build.py ALLOWED_EMPTY['insurance_claim'] = {'adjuster'} — source-absent on every insurance subclass except the BDR auto draw, which carries adjuster pseudonyms.
- **`insurance_claim.denial_reasons`** — 1064 rows classified documented-absence; 0 populated rows match the absence predicate (0 = conformance-clean). v8_build.py _auto_row: denial reasons exist only on denied claims; non-denied rows ship '[]' (a complete no-items answer per v9_build.LIST_GT_FIELDS).
- **`insurance_claim.supporting_documents`** — 6 rows classified documented-absence; 0 populated rows match the absence predicate (0 = conformance-clean). v9_build.py complete_gt_fields + _insurbias_supporting_documents / _insurbias_supporting_doc_absent (issue #29): INSURBIAS rows ship claim narratives only; supporting_documents is derived from referenced features. '[]' is a documented absence only when the narrative grounds no such feature (bare accident report, 6/150 on the v9 draw).

## Pending annotations (catalogued gaps blocked on a dependency)

Rows classified `pending_annotation` ARE coverage gaps — they count against `coverage_pct` above — but the gap is diagnosed and catalogued (never a silent `{}`), and closes as soon as the named dependency clears (mailroom-issues#196 Phase B2):

- **`contract.cuad_clause_labels`** — 91 rows classified pending-annotation; 0 populated rows match the pending predicate (0 = conformance-clean). issue #30 (dated 2026-09-13): the 91 SEC EDGAR EX-10 contract rows ship no CUAD clause annotation — the clause-classification pass could not run (no working OPENROUTER_API_KEY / VLLM_BASE_URL / Modal endpoint at build time). '{}' is a complete no-annotations answer per v9_build.DICT_GT_FIELDS, but the gap is a catalogued pending annotation, not a schema-documented absence: it closes as soon as a working clause-pass credential re-runs the DMR-052 provider seam over exactly these 91 filenames (docs/reports/audits/cuad_ex10_annotation_review.md).

## Scenario columns (§40)

tested/regression/challenge/multi-document are §40 template columns at zero: the sandbox/pilot fixtures (P1) and the matter/grouping (P2) + recovery (P3) families fill them at their phases — the corpus does not overstate coverage (§14A/§53).

## Strata (§40 rows × subclass)

| class | subclass | rows |
|---|---|---|
| `contract` | `Affiliate_Agreements` | 11 |
| `contract` | `Agency Agreements` | 13 |
| `contract` | `Co_Branding` | 22 |
| `contract` | `Collaboration` | 26 |
| `contract` | `Consulting Agreements` | 38 |
| `contract` | `Development` | 28 |
| `contract` | `Distributor` | 32 |
| `contract` | `Endorsement` | 24 |
| `contract` | `Franchise` | 15 |
| `contract` | `Hosting` | 20 |
| `contract` | `IP` | 35 |
| `contract` | `Joint Venture` | 9 |
| `contract` | `Joint Venture _ Filing` | 14 |
| `contract` | `License_Agreements` | 43 |
| `contract` | `Maintenance` | 34 |
| `contract` | `Manufacturing` | 17 |
| `contract` | `Marketing` | 17 |
| `contract` | `Non_Compete_Non_Solicit` | 3 |
| `contract` | `Outsourcing` | 18 |
| `contract` | `Promotion` | 12 |
| `contract` | `Reseller` | 12 |
| `contract` | `Service` | 34 |
| `contract` | `Sponsorship` | 31 |
| `contract` | `Strategic Alliance` | 32 |
| `contract` | `Supply` | 47 |
| `contract` | `Transportation` | 13 |
| `corporate_record` | `articles_of_incorporation` | 62 |
| `corporate_record` | `board_resolution` | 32 |
| `corporate_record` | `bylaws` | 28 |
| `corporate_record` | `charter_amendment` | 80 |
| `corporate_record` | `indenture` | 57 |
| `corporate_record` | `officer_certificate` | 61 |
| `corporate_record` | `other` | 3 |
| `corporate_record` | `powers_of_attorney` | 28 |
| `corporate_record` | `rights_instrument` | 46 |
| `corporate_record` | `subsidiary_list` | 53 |
| `correspondence` | `attorney_demand` | 3 |
| `correspondence` | `demand` | 66 |
| `correspondence` | `email` | 557 |
| `correspondence` | `letter` | 79 |
| `correspondence` | `meeting_request` | 53 |
| `correspondence` | `memo` | 83 |
| `correspondence` | `notice` | 81 |
| `correspondence` | `press_release` | 78 |
| `insurance_claim` | `auto` | 300 |
| `insurance_claim` | `carrier` | 150 |
| `insurance_claim` | `inpatient` | 150 |
| `insurance_claim` | `outpatient` | 150 |
| `insurance_claim` | `pde` | 150 |
| `insurance_claim` | `property` | 200 |
| `merger_agreement` | `all_cash` | 57 |
| `merger_agreement` | `all_stock` | 24 |
| `merger_agreement` | `mixed_cash_stock` | 13 |
| `merger_agreement` | `mixed_cash_stock_election` | 1 |
| `merger_agreement` | `other` | 57 |
