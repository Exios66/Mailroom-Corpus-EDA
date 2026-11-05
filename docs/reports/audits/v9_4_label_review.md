# v9.4 CUAD label spot-check pack

A random 20% sample (15 of 75) of SEC EDGAR EX-10 contracts whose `cuad_clause_labels` were filled from the Modal LLM journal in v9.4. Every span below already passed the automatic checks (verbatim at its offset, closed CUAD vocabulary, semantic probe, quality floor). A reviewer should confirm the span is the named clause.

Mark each line in the CSV `reviewer_verdict` column as `ok`, `wrong_category` or `not_a_clause`.

## `0000891020-07-000003_v25599a1exv10w31.txt`

subclass: Supply, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 298 | the 16th day of March, 2005 |
| Document Name | 84 | SIDE AGREEMENT |
| Governing Law | 48037 | the laws of the State of Delaware |
| Parties | 339 | Clearwire Corporation, a Delaware corporation (the "Company"), Eagle River Holdings, LLC, a Washington limited liability company ("ERH"), and Bell Canada, a ... |

## `0000936392-03-001431_a92189a4exv10w38.txt`

subclass: Supply, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 349 | July 31, 2000 |
| Document Name | 222 | ASSIGNMENT OF SUPPLY AGREEMENT |
| Governing Law | 3458 | This Agreement shall be governed by and construed in accordance with the laws of the State of California. |
| Parties | 378 | 3DLM, Inc., a California corporation formerly known as CancerVax, Inc., which is changing its name to OncoVac, Inc. ("OncoVac"), and CancerVax Corporation, a... |

## `0000950134-07-005012_v25599a5exv10w55.txt`

subclass: Service, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 25352 | Executed as of this 29th day of August, 2006 |
| Document Name | 223 | WIRELESS BROADBAND SYSTEM SERVICES AGREEMENT |
| Governing Law | 15483 | The laws of the State of New York, USA, will govern the validity, performance and all matters relating to the effect of this Services Agreement and any amend... |
| Parties | 966 | between Motorola, Inc., a Delaware corporation, ("Motorola", which term will also mean, where the context requires, Motorola subsidiaries or subcontractors i... |
| Termination For Convenience | 10840 | Either party may terminate this Services Agreement without liability by notice pursuant to Section 18 if the other makes a general assignment for the benefit... |

## `0001047469-02-008341_a2095799zex-10_12c.txt`

subclass: Supply, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 936 | dated as of July 25, 2002 |
| Document Name | 761 | AMENDMENT NUMBER 2 TO AMENDED AND RESTATED PURCHASE AGREEMENT GCT-025/98 |
| Effective Date | 12130 | to be effective as of the date first written above |
| Parties | 1082 | Embraer - Empresa Brasileira de Aeronautica S.A. ("Embraer") and Republic Airways Holdings, Inc. ("Buyer") |

## `0001047469-06-006057_a2169810zex-10_10.htm`

subclass: Service, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 294 | the _____ day of _________, 2006 |
| Document Name | 184 | TRANSITION SERVICES AGREEMENT |
| Effective Date | 294 | the _____ day of _________, 2006 |
| Governing Law | 22301 | the laws of New York |
| Notice Period To Terminate Renewal | 9944 | the effective date of termination, which date shall not be less than 30 days after receipt of such notice |
| Parties | 108 | by and between Walter Industries, Inc. and Mueller Water Products, Inc. |
| Termination For Convenience | 9579 | a Recipient may terminate at any time during the Term any individual service provided under this Services Agreement on a service-by-service basis |

## `0001047469-06-012992_a2173503zex-10_32.htm`

subclass: Consulting Agreements, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 53 | [ ], 2006 |
| Document Name | 3 | Exhibit 10.32 |
| Effective Date | 1299 | the closing date of the Company’s initial Public Offering |
| Governing Law | 3049 | the laws of the State of New York applicable to agreements made and to be performed within that state. |
| Parties | 65 | Merrill Lynch Global Partners,Inc. c/o Merrill Lynch Global Private Equity 4 World Financial Center, 23 rd Floor New York, NY 10080 Tel: (212) 449-1119 Atten... |
| Parties | 3400 | HERTZ GLOBAL HOLDINGS,INC. By: Name: Title: |
| Parties | 3446 | THE HERTZ CORPORATION By: Name: Title: |
| Parties | 3548 | MERRILL LYNCH GLOBAL PARTNERS,INC. By: Name: Title: |

## `0001193125-07-065680_dex1044.htm`

subclass: License_Agreements, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 112 | dated as of January 31, 2007 |
| Document Name | 25 | TRADEMARK LICENSE AGREEMENT |
| Ip Ownership Assignment | 2318 | Licensee acknowledges that Licensor, or its affiliate, is the exclusive owner of all right title and interest in the Licensed Mark and any registration there... |
| License Grant | 1450 | Licensor hereby grants to Licensee, and Licensee hereby accepts, an exclusive license to use the Licensed Mark in the Territory (as defined below) in connect... |

## `0001193125-10-076663_dex104a.htm`

subclass: License_Agreements, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 588 | as of the Execution Date and effective as of November 1, 2006 |
| Document Name | 24 | AMENDED AND RESTATED LICENSE AGREEMENT |
| Effective Date | 633 | November 1, 2006 |
| Governing Law | 75579 | This Amended and Restated License Agreement will be governed by and interpreted in accordance with the laws of the State of New York |
| License Grant | 13646 | Codexis hereby grants to Shell, under all of Codexis’ rights and interest in Program Patent Rights and Program Licensed Technology, an exclusive, worldwide, ... |

## `0001193125-12-006758_d231008dex1019.htm`

subclass: Supply, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 394 | effective October 5, 2011 (the “Effective Date”) |
| Anti-Assignment | 34683 | Neither party may assign its rights under this Agreement to, or have its obligations assumed by, any Person without the prior written consent of the other party |
| Audit Rights | 12208 | Customer has the right, at its own expense, to inspect and test the Product during production or storage at Supplier’s Facility. |
| Change Of Control | 36171 | in the event of any sale of a controlling interest in the equity interests in either party to a third party (a “Change of Control”), the selling party shall ... |
| Document Name | 316 | SUPPLY AGREEMENT |
| Governing Law | 33688 | this Agreement shall be governed by, subject to, and construed according to the internal laws, and not the laws relating to conflicts of law, of the State of... |
| Minimum Commitment | 7899 | Supplier shall produce at least 10,000 tons of Product for Customer. |
| Rofr/Rofo/Rofn | 37784 | Customer shall provide a recurring advance payment to Supplier for each 10,000 tons of Product to be delivered by Supplier to Customer within a ***** mile di... |
| Termination For Convenience | 22209 | Either party shall have the right to terminate this Agreement for any other breach of this Agreement that, if capable of being cured, is not cured within thi... |

## `0001193125-14-115023_d659829dex106.htm`

subclass: Consulting Agreements, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 161 | as of March 5, 2014 |
| Anti-Assignment | 18485 | This Employment Agreement may not be assigned by Executive. |
| Change Of Control | 35026 | a “change of control” is intended generally to refer to the happening of any of the following: |
| Document Name | 66 | EMPLOYMENT AGREEMENT |
| Governing Law | 26982 | This Employment Agreement and any dispute arising from the relationship between the Parties will be governed by and construed under and according to the laws... |
| Ip Ownership Assignment | 8821 | Executive hereby assigns to Employer all right, title and interest to all Employer Inventions, which will be the sole and exclusive property of Employer |
| License Grant | 10247 | Executive will promptly execute, acknowledge and deliver to Employer all additional instruments or documents that Employer determines at any time to be neces... |
| No-Solicit Of Customers | 23213 | During Executive’s employment with Employer and for a period of two (2)years from and after the termination of Executive’s employment with Employer for any r... |
| No-Solicit Of Employees | 22717 | During Executive’s employment with Employer and for a period of two (2)years from and after the termination of Executive’s employment with Employer for any r... |
| Non-Compete | 21598 | During Executive’s employment with Employer and for a period of one (1) year from and after the termination of Executive’s employment with Employer for any r... |
| Non-Disparagement | 20098 | Executive will not, without Employer’s prior written consent, directly or indirectly: (i)copy or use any Confidential Information for any purpose not within ... |
| Non-Transferable License | 10247 | Executive will promptly execute, acknowledge and deliver to Employer all additional instruments or documents that Employer determines at any time to be neces... |
| Volume Restriction | 20098 | Executive will not, without Employer’s prior written consent, directly or indirectly: (i)copy or use any Confidential Information for any purpose not within ... |

## `0001437749-22-002169_ex_331042.htm`

subclass: Supply, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 416 | December 3, 2020 |
| Anti-Assignment | 8442 | Neither this LOI nor any rights or obligations hereunder may be assigned, delegated, or conveyed by either Party without the prior written consent of the oth... |
| Document Name | 642 | Letter of Intent for a Definitive Supply Agreement for Purchase of Shrimp from The trū® Shrimp Company |
| Exclusivity | 4705 | Seller agrees that until such time as this letter agreement has terminated in accordance with the provisions of Section 6 (such period, the "Exclusivity Peri... |
| Governing Law | 4474 | This LOI shall be governed by and construed in accordance with the laws of the State of Delaware. |
| No-Solicit Of Customers | 4705 | Seller agrees that until such time as this letter agreement has terminated in accordance with the provisions of Section 6 (such period, the "Exclusivity Peri... |
| Non-Compete | 4705 | Seller agrees that until such time as this letter agreement has terminated in accordance with the provisions of Section 6 (such period, the "Exclusivity Peri... |
| Non-Transferable License | 8442 | Neither this LOI nor any rights or obligations hereunder may be assigned, delegated, or conveyed by either Party without the prior written consent of the oth... |
| Parties | 863 | Limson Trading, Inc., 1300 Gezon Parkway SW, Wyoming, Michigan 49509-9300 ("Buyer") to purchase the Goods (as defined below) from The trū Shrimp Company, a D... |
| Third Party Beneficiary | 7862 | Nothing herein is intended or shall be construed to confer upon any person or entity other than the Parties and their successors or assigns, any rights or re... |

## `0001575872-22-000712_cm106_ex10-69.htm`

subclass: Supply, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 149 | July 12, 2022 |
| Document Name | 66 | EXTENSION AGREEMENT |
| Effective Date | 183 | July 6, 2022 |
| Governing Law | 10636 | the law of the State of Nevada and the law of the United States applicable to transactions within said State |

## `0001627469-15-000036_ex-10_1.htm`

subclass: IP, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 1160 | Date: January 26, 2015 |
| Document Name | 1 | EX-10.1 6 ex-10_1.htm MATERIAL AGREEMENT FOR FUNDS |
| Minimum Commitment | 197 | I, Thomas DeNunzio, agree and state my intention to pay for the offering costs and any subsequent fees to the offering or relating to the offering of Exquisi... |
| Parties | 140 | between Thomas DeNunzio and Exquisite Acquisition, Inc. |
| Price Restrictions | 462 | It is expected that the offering costs and any subsequent fees to the offering or relating to the offering will not exceed this amount. |

## `0001654954-23-003821_lafa_ex1013.htm`

subclass: IP, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 382 | December 16, 2022 |
| Anti-Assignment | 9265 | No Party shall assign this Agreement or any part hereof without the prior written consent of the other Party |
| Document Name | 77 | 3-D SEISMIC ACQUISTION AND DEVELOPMENT OPTION AGREEMENT |
| Effective Date | 382 | December 16, 2022 |
| Expiration Date | 1833 | four (4) months from the Effective Date |
| Governing Law | 9601 | This Agreement shall be governed and construed in accordance with and pursuant to the laws of the State of Texas |
| Non-Compete | 8921 | Optionee shall not, directly or indirectly, acquire any new oil and gas leases, top lease, or enter into any other transactions to acquire any leasehold inte... |

## `0001683168-21-002146_intorio_ex1003.htm`

subclass: Supply, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Document Name | 30 | WEBSITE ASSET PURCHASE AGREEMENT |
| Effective Date | 175 | effective on February 27, 2021 (the “Effective Date”) |
| Minimum Commitment | 1148 | the Buyer agrees to pay Nine Thousand Dollars (US $9,000.00) to Seller within 360 days of the Effective Date |
| Post-Termination Services | 1932 | The Seller will provide technical consulting in the area of improving/maintaining the website for the next 60 days |

