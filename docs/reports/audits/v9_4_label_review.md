# v9.4 CUAD label spot-check pack

A random 20% sample (15 of 76) of SEC EDGAR EX-10 contracts whose `cuad_clause_labels` were filled from the Modal LLM journal in v9.4. Every span below already passed the automatic checks (verbatim at its offset, closed CUAD vocabulary, semantic probe, quality floor). A reviewer should confirm the span is the named clause.

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
| Audit Rights | 5827 | Clearwire will be responsible for the payment of all applicable sales, use, retailers occupation, excise, property and other assessments in the nature of tax... |
| Covenant Not To Sue | 19563 | The parties agree that where the context of any provision indicates an intent that it will survive the term of this Services Agreement then it will survive. |
| Document Name | 223 | WIRELESS BROADBAND SYSTEM SERVICES AGREEMENT |
| Effective Date | 21514 | Effective Date |
| Governing Law | 15483 | The laws of the State of New York, USA, will govern the validity, performance and all matters relating to the effect of this Services Agreement and any amend... |
| Insurance | 16976 | Clearwire and Motorola agree to comply with all applicable export laws and regulations of the United States of America or such other country of origin. |
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

## `0001193125-05-021087_dex1060.htm`

subclass: Supply, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 254 | as of February , 2005 |
| Document Name | 24 | AMENDMENT NO. 1 TO STOCK PURCHASE AGREEMENT |
| Governing Law | 9540 | The corporate law of Delaware shall govern all issues concerning the relative rights of the Company and its stockholders. All other questions concerning the ... |
| Parties | 290 | (i) Syniverse Holdings, Inc., a Delaware corporation (the “Company”), (ii) Syniverse Holdings, LLC, a Delaware limited liability company (“Holdings LLC”), an... |

## `0001193125-07-065680_dex1044.htm`

subclass: License_Agreements, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Affiliate License-Licensee | 2780 | Licensee will **[ * ]** to assist Licensor to protect and enforce the Licensed Mark |
| Affiliate License-Licensor | 1842 | Licensor, at its own cost, shall be responsible for maintaining and renewing trademark registrations for the Licensed Mark |
| Agreement Date | 112 | dated as of January 31, 2007 |
| Audit Rights | 5329 | Licensee shall provide Licensor with representative specimens of the packaging, labeling, advertising, and promotional material showing Licensee’s use of the... |
| Covenant Not To Sue | 4852 | Licensor shall not take (or cause any other person to take) any action which will conflict with, contravene or otherwise limit or restrict the rights of the ... |
| Document Name | 25 | TRADEMARK LICENSE AGREEMENT |
| Insurance | 8139 | Licensor shall cooperate to the extent reasonably required by Licensee to stop such infringement or act, and, if so requested by Licensee, shall join with Li... |
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
| Insurance | 83134 | Each Party shall comply with all applicable statutes, laws, regulations, enactments, directives and ordinances and all injunctions, decisions, directives, ju... |
| License Grant | 13646 | Codexis hereby grants to Shell, under all of Codexis’ rights and interest in Program Patent Rights and Program Licensed Technology, an exclusive, worldwide, ... |

## `0001193125-12-006758_d231008dex1019.htm`

subclass: Supply, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Affiliate License-Licensor | 7748 | Supplier grants to Customer the right to purchase at least 10,000 tons of Product per calendar month (as ordered by Customer pursuant to Section 2.6). |
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
| Affiliate License-Licensee | 18545 | Employer may assign this Employment Agreement without the consent of Executive. |
| Affiliate License-Licensor | 18545 | Employer may assign this Employment Agreement without the consent of Executive. |
| Agreement Date | 161 | as of March 5, 2014 |
| Anti-Assignment | 18485 | This Employment Agreement may not be assigned by Executive. |
| Audit Rights | 8154 | Executive will promptly disclose to Employer or its designee during Executive’s employment with Employer and for one year thereafter all Employer Inventions ... |
| Change Of Control | 35026 | a “change of control” is intended generally to refer to the happening of any of the following: |
| Covenant Not To Sue | 20098 | Executive will not, without Employer’s prior written consent, directly or indirectly: (i)copy or use any Confidential Information for any purpose not within ... |
| Document Name | 66 | EMPLOYMENT AGREEMENT |
| Governing Law | 26982 | This Employment Agreement and any dispute arising from the relationship between the Parties will be governed by and construed under and according to the laws... |
| Insurance | 5629 | Employer will reimburse Executive promptly for reasonable, necessary, customary and usual business expenses, including travel, entertainment, parking, busine... |
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
| Affiliate License-Licensee | 863 | Limson Trading, Inc., 1300 Gezon Parkway SW, Wyoming, Michigan 49509-9300 ( |
| Affiliate License-Licensor | 993 | The trū Shrimp Company, a Delaware corporation, on behalf of itself and its affiliates |
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

## `0001627469-15-000036_ex-10_1.htm`

subclass: IP, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Agreement Date | 1160 | Date: January 26, 2015 |
| Document Name | 1 | EX-10.1 6 ex-10_1.htm MATERIAL AGREEMENT FOR FUNDS |
| Minimum Commitment | 197 | I, Thomas DeNunzio, agree and state my intention to pay for the offering costs and any subsequent fees to the offering or relating to the offering of Exquisi... |
| Parties | 140 | between Thomas DeNunzio and Exquisite Acquisition, Inc. |
| Price Restrictions | 462 | It is expected that the offering costs and any subsequent fees to the offering or relating to the offering will not exceed this amount. |

## `0001683168-18-001629_iiot_s1a3-ex1019.htm`

subclass: Consulting Agreements, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Affiliate License-Licensor | 761 | IIOT-OXYS, Inc. |
| Agreement Date | 174 | October 1, 2017 |
| Document Name | 33 | FIRST AMENDMENT TO CONSULTING AGREEMENT |
| Effective Date | 174 | October 1, 2017 |

## `0001683168-21-002146_intorio_ex1003.htm`

subclass: Supply, model: `Qwen/Qwen3-14B-AWQ`

| category | offset | span |
|---|---:|---|
| Audit Rights | 2250 | Seller agrees to cooperate with Buyer and take any and all actions necessary to transfer and perfect the ownership of the Website registration and hosting fr... |
| Document Name | 30 | WEBSITE ASSET PURCHASE AGREEMENT |
| Effective Date | 175 | effective on February 27, 2021 (the “Effective Date”) |
| Minimum Commitment | 1148 | the Buyer agrees to pay Nine Thousand Dollars (US $9,000.00) to Seller within 360 days of the Effective Date |
| Post-Termination Services | 1932 | The Seller will provide technical consulting in the area of improving/maintaining the website for the next 60 days |
| Source Code Escrow | 2250 | Seller agrees to cooperate with Buyer and take any and all actions necessary to transfer and perfect the ownership of the Website registration and hosting fr... |

