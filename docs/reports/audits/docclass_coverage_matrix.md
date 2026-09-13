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

### `contract` (600 rows, `contracts_specialist`)

| field | populated | coverage |
|---|---|---|
| `cuad_clause_labels` | 509 | 85% |

### `corporate_record` (450 rows, `corporate_records_specialist`)

| field | populated | coverage |
|---|---|---|
| `intent` | 450 | 100% |
| `keywords` | 450 | 100% |
| `subject_matter` | 450 | 100% |

### `correspondence` (1000 rows, `correspondence_specialist`)

| field | populated | coverage |
|---|---|---|
| `content_topic` | 1000 | 100% |
| `intent` | 1000 | 100% |
| `keywords` | 1000 | 100% |
| `sentiment_label` | 1000 | 100% |
| `subject_matter` | 1000 | 100% |

### `insurance_claim` (1100 rows, `insurance_claims_specialist`)

| field | populated | coverage |
|---|---|---|
| `adjuster` | 150 | 14% |
| `claim_number` | 1100 | 100% |
| `claim_type` | 1100 | 100% |
| `claimed_amount` | 1100 | 100% |
| `coverage_determination` | 1100 | 100% |
| `damages_description` | 1100 | 100% |
| `date_filed` | 1100 | 100% |
| `date_of_loss` | 1100 | 100% |
| `denial_reasons` | 36 | 3% |
| `insured_party` | 1100 | 100% |
| `insurer` | 1100 | 100% |
| `intent` | 1100 | 100% |
| `keywords` | 1100 | 100% |
| `policy_number` | 1100 | 100% |
| `subject_matter` | 1100 | 100% |
| `supporting_documents` | 950 | 86% |

### `merger_agreement` (152 rows, `contracts_specialist`)

| field | populated | coverage |
|---|---|---|
| `maud_clause_labels` | 152 | 100% |

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
