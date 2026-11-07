# v10 corporate_record subclass hierarchy (proposal, not approved)

Status: **direction approved 2026-11-07**: rules A–E, own subclasses for restated charters and preferred designations, consolidation of weak subclasses with the fine label kept, and about 110 EDGAR records. Rule F (subscription agreement) and the consolidated taxonomy below still need sign-off. Nothing has been relabeled. The counts are
aggregates of the text-grounded mapping in the gitignored
`docs/reports/audits/v10_labels/corporate_subclass_mapping.csv`, which covers the 450 v9.3
corporate records only (SEC EDGAR EX-3, EX-4, EX-21, EX-24 and EX-32). The 91 SEC EDGAR EX-10
contracts and the 509 CUAD contracts are out of scope.

## Why

The published v9.3 corporate subclasses are heuristic. 142 of 450 contradict the document's own
title and statute. Two leaves are almost empty in the actual text: `officer_certificate` has 0
real members and `board_resolution` has 4. The real records cluster into four families.
Routing and scoring at the family level gives every record a home with enough examples. The
leaves stay as the fine label.

## Hierarchy: corporate_record → family → subclass (Dojo leaf) → form (descriptive only)

| Family | Subclass (Dojo 0.28 leaf) | Form | Rows (test) |
|---|---|---|---|
| **charter_documents** (161) | articles_of_incorporation | original charter | 32 (2) |
| | | Secretary of State certified-copy bundle | 2 (0) |
| | charter_amendment | restated charter | 87 (8) |
| | | other amendment | 25 (4) |
| | | reverse split / share combination | 13 (4) |
| | | certificate of correction | 2 (0) |
| | certificate_of_formation | LLC formation | **0** |
| **governance_rules_and_actions** (49) | bylaws | full bylaws | 36 (1) |
| | | bylaw amendment | 9 (0) |
| | board_resolution | resolution / written consent / minutes | **4 (0)** |
| | officer_certificate | officer or secretary certificate | **0** |
| **securities_instruments** (159) | rights_instrument | preferred-stock designation (and its amendments) | 53 (7) |
| | | registration rights | 37 (4) |
| | | warrant / purchase option | 11 (0) |
| | | rights plan, stock certificate | 3 (0) |
| | indenture | base indenture | 26 (2) |
| | | supplemental indenture | 29 (2) |
| **authority_and_structure** (79) | subsidiary_list | Exhibit 21 | 53 (10) |
| | powers_of_attorney | SEC signing authority | 26 (3) |
| out of class (2) | — | subscription agreement; unresolved filing acknowledgement | 2 |

Imbalance: families range 49–161 (3.3×). Leaves range 0–87.

## Coverage targets (proposed)

- Every family has at least 100 rows. Every leaf has at least 30, with at least 3 in the test split.
- No existing record is dropped to reach balance. Balance comes from sourcing new records and
  from stratified sampling weights.

## Gaps that need additional records (SEC EDGAR, public domain, no model cost)

| Leaf | Have | Target | Where they are filed |
|---|---|---|---|
| officer_certificate | 0 | 30 | secretary's / officer's / incumbency certificates (EX-3, EX-4, EX-99 closing sets) |
| board_resolution | 4 | 30 | board resolutions and written consents (EX-3, EX-99, 8-K exhibits) |
| certificate_of_formation | 0 | 30 | certificates of formation of LLC co-registrants and guarantors (S-4 / S-1 EX-3) |
| warrant (form) | 11 | 30 | EX-4 warrant agreements and certificates |
| bylaw amendment (form) | 9 | — | the bylaws leaf already meets 30; this form needs test-split rows only |

That is about 110 new records, which takes governance_rules_and_actions to about 113.

## Decisions for the user

1. Approve the four families and the leaf mapping rules A–F from the subclass analysis.
2. Decide whether families become a scored level, so a right family with a wrong leaf earns partial credit, or stay descriptive only.
3. Decide whether to split `charter_amendment` (restated 87 vs amendment 40) or `rights_instrument` (designation 53 vs registration rights 37 vs warrant 11) into their own leaves.
4. Approve sourcing about 110 EDGAR records. That is a corpus expansion: a new release with frozen-spec verification, not a label edit.

## Ownership (lockstep, see `labeling/integrations/`)

`Exios66/llm-dojo-scoring` owns the corporate schema. The families and any new leaf are authored
there first. Then they are mirrored to this repo's registry, llm-mailroom `taxonomy.yaml`, and the
sandbox sorter catalog. The condensed sandbox prompts never write labels.

## Root cause (from `scripts/archive/v9-acquisition/edgar_ftsearch.py` `CORPORATE_TARGETS`)

v9 assigned each subclass from the search bucket a record came from, not from the record:
- `officer_certificate` was sourced by searching `"certificate of designation"`;
- `board_resolution` was sourced by searching `"unanimous consent of the board"` (which hits charter amendments);
- `other` was sourced by searching `"charter"`.

New sourcing labels each record from its own text (the mapping script), never from its query.

## Consolidated taxonomy (approved direction; final sign-off pending)

Weak subclasses fold into the strong ones. Each record also keeps its fine label in a new
ground-truth-only key, `gt_fields.record_form`, from a closed vocabulary. It never goes in the
blind `default` config.

| v10 subclass | Folds in (record_form values) | Now | + EDGAR | After |
|---|---|---|---|---|
| original_charter | articles_of_incorporation, certificate_of_formation, certified_copy_bundle | 34 | +30 LLC certificates of formation | 64 |
| restated_charter | restated_charter, form_of_restated_charter | 87 | — | 87 |
| charter_amendment | amendment, reverse_split, name_change, certificate_of_correction | 40 | — | 40 |
| governance_record | bylaws, bylaw_amendment, board_resolution, written_consent, minutes, officer_certificate, secretary_certificate | 49 | +25 resolutions/consents, +25 officer/secretary certificates | 99 |
| preferred_designation | designation, designation_amendment | 53 | — | 53 |
| securities_rights | registration_rights, warrant, warrant_indenture, rights_plan, stock_certificate | 51 | +19 warrant agreements | 70 |
| indenture | base_indenture, supplemental_indenture | 55 | — | 55 |
| subsidiary_list | subsidiary_list | 53 | — | 53 |
| powers_of_attorney | sec_signing_poa | 26 | +10 | 36 |
| other | residual (1 unresolved filing acknowledgement) | 1 | — | 1 |
| *(removed)* | subscription agreement filed as EX-4.1 (rule F, user decision 2026-11-07: not a governance record, leaves the corpus in v10) | 1 | — | 0 |

That gives 9 subclasses in a 36–99 range, down from 11 published leaves in a 2–80 range (true
membership 0–87), and 109 new records. Families stay as the routing umbrella:
- charter_documents = original_charter, restated_charter, charter_amendment;
- governance = governance_record;
- securities_instruments = preferred_designation, securities_rights, indenture;
- authority_and_structure = subsidiary_list, powers_of_attorney.

## Knock-on effects

- **Dojo** (owner of the corporate schema): new leaves, merged entity schemas keyed by `record_form`, and the `record_form` vocabulary. This goes first.
- **Sorter:** the adopted condensed sorter guard "officer_certificate, never rights_instrument" (llm-mailroom #179, `SORTER_CONDENSED_GUARDS`) encodes the v9 mislabel. #179 still lands, because it matches the published v9.3 labels. The v10 taxonomy needs a new sorter lineage version, and the guard set changes with it.
- **Bylaws are bylaws (user directive, 2026-11-07):** #179's production sorter is now `sorter_condensed_v5`, which is v1 with "a bylaw amendment is bylaws, never board_resolution" (rule C). The v1 bytes stay as history. The opposite rule still lives in Dojo `sorter_guide` (the `board_resolution` meaning), sandbox `sorter_condensed_v1`–`v4`, reviewer_decisions v1–v4 and the frozen V15 S2. Dojo is fixed first, then the sandbox lineage gets a v5 entry. Scoring against v9.3 counts the 9 bylaw amendments labelled `board_resolution` as wrong until v10 relabels them.
- **Corporate condensed specialist:** its v7 record_type map is replaced in a `condensed_v2` mutation.
- **Release:** a v10 corpus expansion with new rows, frozen-spec verification, and the split rule md5(filename) % 10 == 0 → test.
- **Sourcing compliance:** v9 fetched SEC through the Jina Reader proxy with a browser User-Agent, because SEC blocked direct requests. v10 sourcing uses direct sec.gov requests with a declared User-Agent (name and contact) at 10 requests/s or fewer, per SEC fair-access policy. That needs a contact email: on 2026-11-07, a declared User-Agent with only a GitHub contact got 403 from both efts.sec.gov and sec.gov/Archives. The user asked to reuse the v9 Jina proxy with a browser User-Agent. That was declined, because it evades SEC's access control.

## Source for the new records (user decisions, 2026-11-07)

- **Source:** `alea-institute/kl3m-data-edgar-10-k` v1.2 (CC-BY-4.0). It is rebuilt from SEC's own files and holds every `EX-*` exhibit of 10-K-family filings, one row per document with `document_type`, `accession`, `form` and `text_sha256`. Its text is stored as `kl3m-004-128k-cased` token ids and is decoded locally. No sec.gov fetch is needed. Attribution goes on the dataset card.
- **Filing type:** the new rows come from 10-K filings, while the existing corporate records are mostly S-1s. So for the new rows, `metadata.form` hints at the subclass. The user accepted this as realistic for mail ingress, where a mailroom sees the filing type. `form` stays in blind metadata. Cost: on the added rows, a model can partly score by reading the form rather than the document.
- Candidates are scanned by `$SP/kl3m/scan.py` into the gitignored `data/v10/kl3m/candidates.jsonl`. Each one is labelled from its own title, never from the query that found it.

## Gold additions: the new records skip the paid queue (user directive, 2026-11-07)

The ~110 new records are labelled fully, once, as gold examples of corporate-record quality,
entity extraction above all. They go into the private `mailroom-dataset-v10-staging`, not
into a campaign chunk.

- **Schema:** every core field plus every entity field of the record's Dojo 0.28 leaf schema. The leaf is the `record_form`: officer_certificate, board_resolution, certificate_of_formation, rights_instrument for warrants, or powers_of_attorney. This matches the staging `gt_corporate_record` columns (for example `certifying_officer`, `officer_title`, `certified_matter`, `addressee`, `acting_body`, `action_approved`, `resolutions`, `organizers`, `registered_agent`, `instrument_kind`, `holder`, `rights_agent`, `securities_covered`, `exercise_price`, `principal`, `attorneys_in_fact`, `authority_scope`, `expiration_date`), plus `presence`, `evidence` and `provenance`. The leaf schemas fold into the v10 subclasses unchanged, so these labels carry forward when Dojo adds the new leaves.
- **Evidence:** every populated value has a verbatim quote and character offsets into `doc_text`, checked mechanically, so a value must appear in the text. Every absent field has a presence code (`schema_documented_absence` or `not_applicable`), never a blank.
- **Who labels:** Claude drafts each record from the document, a script checks every value against its evidence span, and the user reviews every record before it is staged. Provenance: `annotation_method = human_annotated`, `annotation_model = claude-opus-5-5` (the drafter), `annotation_reviewer = human`, with the review date. *(Pending user approval.)*
- **Keywords:** pending the keyword rule (Option A, names).
- **Staging path:** `labels/10.1_corporate-gold-additions/`, with `documents.csv` (ids, source, sha256), `corporate-labels.jsonl`, `review.csv` (the user's verdict per record) and `provenance.json`. It is checkpointed to the private staging repo, then ingested into `build/v10-stage` as new rows by a new revision module with tests. Split by md5(filename) % 10.
- **Source pin:** `alea-institute/kl3m-data-edgar-10-k` @ `eccf36c61e4c94f7950ef5797b8b7ad7532ad2cb` (CC-BY-4.0). Row `source_corpus` = that repo, with `source_revision`, the KL3M `identifier` and `text_sha256`.
- **Release train (kind B, `labeling/integrations/CHECKLIST_expanded_labeling.md`):** Dojo adds the v10 leaves and `record_form` first. The EDA dataset build follows, then llm-mailroom, then the sandbox.
