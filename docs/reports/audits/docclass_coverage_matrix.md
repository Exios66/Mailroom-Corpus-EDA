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

Coverage is reported over **eligible rows only** (populated / eligible). Unpopulated cells are classified `schema_documented_absence` — the v8_build/v9 conformance law says the field is empty on this row (e.g. `adjuster` on CMS/GNOTHEIA/INSURBIAS rows, `denial_reasons` on non-denied claims, `supporting_documents` on the 6 INSURBIAS bare-accident narratives that reference no supporting-document feature — issue #29) — or `genuine_gap` — the field should be populated but is not (e.g. `cuad_clause_labels` on the 91 EDGAR EX-10 contracts). Documented absences are tallied but never counted as gaps (issue #28).

### `contract` (600 rows, `contracts_specialist`)

| field | populated | eligible | documented-absent | genuine gap | coverage |
|---|---|---|---|---|---|
| `cuad_clause_labels` | 509 | 600 | 0 | 91 | 85% |

### `corporate_record` (450 rows, `corporate_records_specialist`)

| field | populated | eligible | documented-absent | genuine gap | coverage |
|---|---|---|---|---|---|
| `intent` | 450 | 450 | 0 | 0 | 100% |
| `keywords` | 450 | 450 | 0 | 0 | 100% |
| `subject_matter` | 450 | 450 | 0 | 0 | 100% |

### `correspondence` (1000 rows, `correspondence_specialist`)

| field | populated | eligible | documented-absent | genuine gap | coverage |
|---|---|---|---|---|---|
| `content_topic` | 1000 | 1000 | 0 | 0 | 100% |
| `intent` | 1000 | 1000 | 0 | 0 | 100% |
| `keywords` | 1000 | 1000 | 0 | 0 | 100% |
| `sentiment_label` | 1000 | 1000 | 0 | 0 | 100% |
| `subject_matter` | 1000 | 1000 | 0 | 0 | 100% |

### `insurance_claim` (1100 rows, `insurance_claims_specialist`)

| field | populated | eligible | documented-absent | genuine gap | coverage |
|---|---|---|---|---|---|
| `adjuster` | 150 | 150 | 950 | 0 | 100% |
| `claim_number` | 1100 | 1100 | 0 | 0 | 100% |
| `claim_type` | 1100 | 1100 | 0 | 0 | 100% |
| `claimed_amount` | 1100 | 1100 | 0 | 0 | 100% |
| `coverage_determination` | 1100 | 1100 | 0 | 0 | 100% |
| `damages_description` | 1100 | 1100 | 0 | 0 | 100% |
| `date_filed` | 1100 | 1100 | 0 | 0 | 100% |
| `date_of_loss` | 1100 | 1100 | 0 | 0 | 100% |
| `denial_reasons` | 36 | 36 | 1064 | 0 | 100% |
| `insured_party` | 1100 | 1100 | 0 | 0 | 100% |
| `insurer` | 1100 | 1100 | 0 | 0 | 100% |
| `intent` | 1100 | 1100 | 0 | 0 | 100% |
| `keywords` | 1100 | 1100 | 0 | 0 | 100% |
| `policy_number` | 1100 | 1100 | 0 | 0 | 100% |
| `subject_matter` | 1100 | 1100 | 0 | 0 | 100% |
| `supporting_documents` | 1094 | 1094 | 6 | 0 | 100% |

### `merger_agreement` (152 rows, `contracts_specialist`)

| field | populated | eligible | documented-absent | genuine gap | coverage |
|---|---|---|---|---|---|
| `maud_clause_labels` | 152 | 152 | 0 | 0 | 100% |

## Documented absences (§v8_build/v9 conformance law)

Rows classified `schema_documented_absence` are **not coverage gaps**: the conformance law documents the field empty on them. Rules (with code citations; full text in the JSON):

- **`insurance_claim.adjuster`** — 950 rows classified documented-absence; 0 populated rows match the absence predicate (0 = conformance-clean). v8_build.py ALLOWED_EMPTY = {'adjuster'} + module docstring ('' only where the schema documents absence, e.g. adjuster on property/CMS rows); v9_build.py ALLOWED_EMPTY['insurance_claim'] = {'adjuster'}; conform_rows: 'the only documented scalar allowance is insurance_claim.adjuster (source-absent on CMS / GNOTHEIA / INSURBIAS)' — only the BDR auto rows carry adjuster pseudonyms (v8_build.py _auto_row: _pseudo_adjuster(claim_id)).
- **`insurance_claim.denial_reasons`** — 1064 rows classified documented-absence; 0 populated rows match the absence predicate (0 = conformance-clean). v8_build.py _auto_row: reasons = _auto_denial_reasons(r) if determination == 'denied' else [] — denial reasons exist only on denied claims; non-denied rows ship '[]' (a complete no-items answer per v9_build.py LIST_GT_FIELDS: 'a valid JSON array is the COMPLETE answer — [] means no items (honest), never a missing value').
- **`insurance_claim.supporting_documents`** — 6 rows classified documented-absence; 0 populated rows match the absence predicate (0 = conformance-clean). v9_build.py complete_gt_fields + _insurbias_supporting_documents / _insurbias_supporting_doc_absent (issue #29): the INSURBIAS draw rows ship claim narratives only, so supporting_documents is derived deterministically from each narrative's referenced features — repair estimate on any vehicle-damage / repair assertion (the v8 BDR auto precedent, v8_build.py _auto_row 'supporting = ["repair estimate"]'), plus police report (authorities/police referenced), damage photos (image referenced), medical records (injury asserted, negation-aware), fire report (fire department called). Rows whose narrative references NO such feature (bare accident reports — 6/150 on the v9 draw) keep '[]', a documented absence per LIST_GT_FIELDS ('a valid JSON array is the COMPLETE answer — [] means no items (honest), never a missing value').

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
