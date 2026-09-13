# CUAD clause annotation review — 91 EDGAR EX-10 contracts (issue #30)

**Status: DATED EXCEPTION (2026-09-13).** No annotations produced — the populate path is blocked and the rows are classified as a documented exception per issue #30's fallback (mirrored in `scripts/audit/coverage_matrix.py` ABSENCE_RULES for `contract.cuad_clause_labels`).

## Blocker

The populate path (CUAD clause annotation of the 91 EX-10 texts via the corpus-eda LLM clause pass lineage — constrained zero-shot pass, temperature 0.1, JSON response) could not be executed on the execution date: the only LLM credential available (OPENROUTER_API_KEY in the eval-environment) returns HTTP 401 'API key expired'; no VLLM_BASE_URL / Modal endpoint is configured; no local ollama model is cached. With no pass output there is nothing to human-gate, so per issue #30's fallback the rows are recorded as a DATED EXCEPTION in the coverage matrix absence classification.

## Follow-up

Re-run the clause-classification pass over exactly these 91 rows when a working provider credential is available (OPENROUTER refresh or VLLM_BASE_URL), then flip the matrix absence rule and the test pin. Scope is catalogued per row below.

## Scope

| subclass | rows |
|---|---|
| Affiliate_Agreements | 1 |
| Consulting Agreements | 27 |
| IP | 18 |
| License_Agreements | 10 |
| Service | 6 |
| Supply | 29 |

| # | filename | subclass | exhibit_type | filing_date | status |
|---|---|---|---|---|---|
| 1 | `0001193125-13-395732_d559406dex104.htm` | Affiliate_Agreements | EX-10.4 | 2013-10-10 | exception_not_annotated |
| 2 | `0000721748-15-000033_ncmf121214s1aex10_19.htm` | Consulting Agreements | EX-10.19 | 2015-01-16 | exception_not_annotated |
| 3 | `0000721748-15-000083_ncmf02121510_20.htm` | Consulting Agreements | EX-10.20 | 2015-02-13 | exception_not_annotated |
| 4 | `0000819050-20-000127_a20200608s1aexhibit1014.htm` | Consulting Agreements | EX-10.14 | 2020-06-08 | exception_not_annotated |
| 5 | `0000950123-05-012539_y06540a5exv10w3.htm` | Consulting Agreements | EX-10.3 | 2005-10-25 | exception_not_annotated |
| 6 | `0000950123-05-012539_y06540a5exv10w5.htm` | Consulting Agreements | EX-10.5 | 2005-10-25 | exception_not_annotated |
| 7 | `0000950123-09-046709_y01981a3exv10w15.htm` | Consulting Agreements | EX-10.15 | 2009-09-29 | exception_not_annotated |
| 8 | `0000950123-10-028441_a54842a1exv10w11w1.htm` | Consulting Agreements | EX-10.11.1 | 2010-03-26 | exception_not_annotated |
| 9 | `0001047469-06-012992_a2173503zex-10_30.htm` | Consulting Agreements | EX-10.30 | 2006-10-23 | exception_not_annotated |
| 10 | `0001047469-06-012992_a2173503zex-10_32.htm` | Consulting Agreements | EX-10.32 | 2006-10-23 | exception_not_annotated |
| 11 | `0001047469-08-008242_a2186911zex-10_13.htm` | Consulting Agreements | EX-10.13 | 2008-07-21 | exception_not_annotated |
| 12 | `0001144204-09-019873_v145506_ex10-7.htm` | Consulting Agreements | EX-10.7 | 2009-04-09 | exception_not_annotated |
| 13 | `0001193125-04-040915_dex1041.htm` | Consulting Agreements | EX-10.41 | 2004-03-15 | exception_not_annotated |
| 14 | `0001193125-04-202829_dex10291.htm` | Consulting Agreements | EX-10.29.1 | 2004-11-24 | exception_not_annotated |
| 15 | `0001193125-07-205683_dex1039.htm` | Consulting Agreements | EX-10.39 | 2007-09-24 | exception_not_annotated |
| 16 | `0001193125-14-115023_d659829dex106.htm` | Consulting Agreements | EX-10.6 | 2014-03-26 | exception_not_annotated |
| 17 | `0001213900-23-083790_ea187099ex10-23_perfect.htm` | Consulting Agreements | EX-10.23 | 2023-11-06 | exception_not_annotated |
| 18 | `0001213900-23-083790_ea187099ex10-24_perfect.htm` | Consulting Agreements | EX-10.24 | 2023-11-06 | exception_not_annotated |
| 19 | `0001213900-23-083790_ea187099ex10-28_perfect.htm` | Consulting Agreements | EX-10.28 | 2023-11-06 | exception_not_annotated |
| 20 | `0001213900-24-021327_ea020144601ex10-18_retic.htm` | Consulting Agreements | EX-10.18 | 2024-03-11 | exception_not_annotated |
| 21 | `0001213900-24-021327_ea020144601ex10-20_retic.htm` | Consulting Agreements | EX-10.20 | 2024-03-11 | exception_not_annotated |
| 22 | `0001213900-24-021327_ea020144601ex10-21_retic.htm` | Consulting Agreements | EX-10.21 | 2024-03-11 | exception_not_annotated |
| 23 | `0001393905-18-000242_tngp_ex101.htm` | Consulting Agreements | EX-10.1 | 2018-08-13 | exception_not_annotated |
| 24 | `0001493152-17-012423_ex10-25.htm` | Consulting Agreements | EX-10.25 | 2017-11-06 | exception_not_annotated |
| 25 | `0001493152-22-019482_ex10-10.htm` | Consulting Agreements | EX-10.10 | 2022-07-15 | exception_not_annotated |
| 26 | `0001493152-23-003674_ex10-32.htm` | Consulting Agreements | EX-10.32 | 2023-02-06 | exception_not_annotated |
| 27 | `0001628280-23-006327_exhibit1015-sx1a2.htm` | Consulting Agreements | EX-10.15 | 2023-03-03 | exception_not_annotated |
| 28 | `0001683168-18-001629_iiot_s1a3-ex1019.htm` | Consulting Agreements | EX-10.19 | 2018-06-08 | exception_not_annotated |
| 29 | `0001047469-05-021628_a2161868zex-10_9.htm` | IP | EX-10.9 | 2005-08-16 | exception_not_annotated |
| 30 | `0001047469-15-005538_a2225135zex-10_19.htm` | IP | EX-10.19 | 2015-06-18 | exception_not_annotated |
| 31 | `0001193125-12-121805_d306143dex1030.htm` | IP | EX-10.30 | 2012-03-19 | exception_not_annotated |
| 32 | `0001193125-18-272192_d494258dex1011.htm` | IP | EX-10.11 | 2018-09-13 | exception_not_annotated |
| 33 | `0001193125-19-189938_d727772dex1013.htm` | IP | EX-10.13 | 2019-07-08 | exception_not_annotated |
| 34 | `0001193125-20-156385_d875118dex109.htm` | IP | EX-10.9 | 2020-06-01 | exception_not_annotated |
| 35 | `0001548123-19-000138_ex10-2.htm` | IP | EX-10 | 2019-07-25 | exception_not_annotated |
| 36 | `0001564590-21-028164_ck7045120534-ex108_276.htm` | IP | EX-10.8 | 2021-05-17 | exception_not_annotated |
| 37 | `0001582718-14-000075_ex-10_1.htm` | IP | EX-10.1 | 2014-05-13 | exception_not_annotated |
| 38 | `0001582718-14-000092_ex-10_1.htm` | IP | EX-10.1 | 2014-06-05 | exception_not_annotated |
| 39 | `0001627469-15-000036_ex-10_1.htm` | IP | EX-10.1 | 2015-07-20 | exception_not_annotated |
| 40 | `0001628280-18-007944_exhibit1013s-1a.htm` | IP | EX-10.13 | 2018-06-18 | exception_not_annotated |
| 41 | `0001628280-18-007944_exhibit1014s-1a.htm` | IP | EX-10.14 | 2018-06-18 | exception_not_annotated |
| 42 | `0001628280-26-004091_exhibit1010-sx1a.htm` | IP | EX-10.10 | 2026-01-29 | exception_not_annotated |
| 43 | `0001628280-26-042574_exhibit1010-sx1a1.htm` | IP | EX-10.10 | 2026-06-11 | exception_not_annotated |
| 44 | `0001654954-23-003821_lafa_ex1013.htm` | IP | EX-10.13 | 2023-03-29 | exception_not_annotated |
| 45 | `0001675426-16-000031_ex-10_1.htm` | IP | EX-10.1 | 2016-08-05 | exception_not_annotated |
| 46 | `0001675426-16-000038_ex-10_1.htm` | IP | EX-10.1 | 2016-08-12 | exception_not_annotated |
| 47 | `0000936392-05-000402_a12564a2exv10w17.txt` | License_Agreements | EX-10.17 | 2005-11-23 | exception_not_annotated |
| 48 | `0000950123-07-005608_l25337a5exv10w4.htm` | License_Agreements | EX-10.4 | 2007-04-18 | exception_not_annotated |
| 49 | `0000950135-04-002790_b49788a4exv10w5.txt` | License_Agreements | EX-10.5 | 2004-05-25 | exception_not_annotated |
| 50 | `0001193125-04-113293_dex101.htm` | License_Agreements | EX-10.1 | 2004-07-02 | exception_not_annotated |
| 51 | `0001193125-07-065680_dex1044.htm` | License_Agreements | EX-10.44 | 2007-03-27 | exception_not_annotated |
| 52 | `0001193125-10-076663_dex104a.htm` | License_Agreements | EX-10.4A | 2010-04-05 | exception_not_annotated |
| 53 | `0001193125-15-263882_d917371dex1014.htm` | License_Agreements | EX-10.14 | 2015-07-27 | exception_not_annotated |
| 54 | `0001193125-19-248330_d617454dex1073.htm` | License_Agreements | EX-10.7.3 | 2019-09-18 | exception_not_annotated |
| 55 | `0001213900-12-003228_fs12012a2ex10iii_caldera.htm` | License_Agreements | EX-10.3 | 2012-06-08 | exception_not_annotated |
| 56 | `0001493152-24-017321_ex10-19.htm` | License_Agreements | EX-10.19 | 2024-05-01 | exception_not_annotated |
| 57 | `0000950134-07-005012_v25599a5exv10w55.txt` | Service | EX-10.55 | 2007-03-07 | exception_not_annotated |
| 58 | `0001047469-06-006057_a2169810zex-10_10.htm` | Service | EX-10.10 | 2006-05-02 | exception_not_annotated |
| 59 | `0001144204-14-011078_v367545_ex10-51.htm` | Service | EX-10.5.1 | 2014-02-24 | exception_not_annotated |
| 60 | `0001193125-14-230547_d693356dex1030.htm` | Service | EX-10.30 | 2014-06-09 | exception_not_annotated |
| 61 | `0001376799-08-000016_exhibit1019.htm` | Service | EX-10 | 2008-02-04 | exception_not_annotated |
| 62 | `0001567619-13-000087_s000086x3_ex10-3.htm` | Service | EX-10.3 | 2013-10-28 | exception_not_annotated |
| 63 | `0000891020-07-000003_v25599a1exv10w31.txt` | Supply | EX-10.31 | 2007-01-08 | exception_not_annotated |
| 64 | `0000936392-03-001431_a92189a4exv10w38.txt` | Supply | EX-10.38 | 2003-10-24 | exception_not_annotated |
| 65 | `0000950123-05-003443_y68255a1exv10w22.txt` | Supply | EX-10.22 | 2005-03-22 | exception_not_annotated |
| 66 | `0000950123-11-069430_a59248a2exv10w32.htm` | Supply | EX-10.32 | 2011-07-28 | exception_not_annotated |
| 67 | `0001047469-02-008341_a2095799zex-10_12c.txt` | Supply | EX-10.12(C) | 2002-12-26 | exception_not_annotated |
| 68 | `0001047469-06-008719_a2171318zex-10_43.htm` | Supply | EX-10.43 | 2006-06-22 | exception_not_annotated |
| 69 | `0001104659-26-049674_tm2518736d11_ex10-4.htm` | Supply | EX-10.4 | 2026-04-28 | exception_not_annotated |
| 70 | `0001144204-13-006917_v333161_ex10-32.htm` | Supply | EX-10.32 | 2013-02-08 | exception_not_annotated |
| 71 | `0001193125-05-021087_dex1060.htm` | Supply | EX-10.60 | 2005-02-08 | exception_not_annotated |
| 72 | `0001193125-10-068933_dex1029.htm` | Supply | EX-10.29 | 2010-03-29 | exception_not_annotated |
| 73 | `0001193125-10-124434_dex1022.htm` | Supply | EX-10.22 | 2010-05-20 | exception_not_annotated |
| 74 | `0001193125-12-006758_d231008dex1019.htm` | Supply | EX-10.19 | 2012-01-09 | exception_not_annotated |
| 75 | `0001193125-14-131966_d406707dex1045.htm` | Supply | EX-10.45 | 2014-04-04 | exception_not_annotated |
| 76 | `0001193125-14-242205_d623882dex1023.htm` | Supply | EX-10.23 | 2014-06-19 | exception_not_annotated |
| 77 | `0001193125-15-404697_d73715dex1014b.htm` | Supply | EX-10.14(B) | 2015-12-16 | exception_not_annotated |
| 78 | `0001213900-19-008324_fs12019a3ex10-22_bricktown.htm` | Supply | EX-10.22 | 2019-05-13 | exception_not_annotated |
| 79 | `0001213900-25-051717_ea024476901ex10-37_natures.htm` | Supply | EX-10.37 | 2025-06-06 | exception_not_annotated |
| 80 | `0001213900-25-051717_ea024476901ex10-38_natures.htm` | Supply | EX-10.38 | 2025-06-06 | exception_not_annotated |
| 81 | `0001368365-24-000034_ex1029letteragreementdated.htm` | Supply | EX-10.29 | 2024-02-14 | exception_not_annotated |
| 82 | `0001437749-22-002169_ex_331042.htm` | Supply | EX-10.36 | 2022-02-02 | exception_not_annotated |
| 83 | `0001493152-15-005653_ex10-23.htm` | Supply | EX-10.23 | 2015-11-17 | exception_not_annotated |
| 84 | `0001493152-16-006927_ex10-23.htm` | Supply | EX-10.23 | 2016-01-25 | exception_not_annotated |
| 85 | `0001493152-17-012423_ex10-22.htm` | Supply | EX-10.22 | 2017-11-06 | exception_not_annotated |
| 86 | `0001564590-21-046613_sdmi-ex1025_706.htm` | Supply | EX-10.25 | 2021-08-31 | exception_not_annotated |
| 87 | `0001564590-21-046613_sdmi-ex1027_705.htm` | Supply | EX-10.27 | 2021-08-31 | exception_not_annotated |
| 88 | `0001571049-13-001230_t1300651_ex10-8.htm` | Supply | EX-10.8 | 2013-12-06 | exception_not_annotated |
| 89 | `0001575872-22-000712_cm106_ex10-69.htm` | Supply | EX-10.69 | 2022-08-03 | exception_not_annotated |
| 90 | `0001640334-26-000472_gbux_ex106.htm` | Supply | EX-10.6 | 2026-03-18 | exception_not_annotated |
| 91 | `0001683168-21-002146_intorio_ex1003.htm` | Supply | EX-10.3 | 2021-05-21 | exception_not_annotated |
