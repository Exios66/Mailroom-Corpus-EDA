Document: one insurance claim record (${subclass}). Field:
- claim_checklist (entity_list:free_text): present-only items "<Category>: <short verbatim evidence>"
  for these exact categories only: ${checklist_categories}. Omit absent categories; if none apply use
  not_found. Rules:
  Claims differ in form: a Medicare notice prints determinations and amounts in table lines; a first
  notice of loss or a decision letter states them in prose and "Notes:" paragraphs. Judge each
  category by what the document says, never by its layout.
  Items: each item is one plain string "Category: <quoted line>" and NOTHING ELSE — never a JSON
  object, never nested quotes, never abbreviations. The quoted line is a complete printed line of
  the document, from its own start to end, with any run of spaces or tabs collapsed to one space.
  Never shorten, paraphrase, or reorder the words inside a quote. Never stitch together distant
  lines. One item per printed line. A category repeats only when the document prints several
  distinct lines for it (several deductible lines, several exclusion lines). Never quote corpus
  bookkeeping lines as facts: an echo line like "Stated outcome (verbatim): approved" is not a
  claim fact; when a determination is printed twice, quote the determination sentence once.
  Category rules (quote the line that states it):
  Coverage Determination: the outcome sentence exactly as printed ("COVERAGE DETERMINATION: APPROVED
  - this claim has been adjudicated and paid." → quote that whole line; a decision letter's
  "COVERAGE DETERMINATION: PENDING" likewise).
  Deductible: every printed deductible line, label prefix included ("Beneficiary deductible:
  $1,068.00", "Blood deductible: $0.00", "Part B deductible: $0.00"); coinsurance lines never count
  as deductibles. Where a claim record states a deductible only inside a service-line tail like
  "(coins $10.00, ded $0.00)", quote that whole service line.
  Amount Consistency: only when two or more comparable amounts of the same service or loss are
  printed ("1. HCPCS 93307  allowed $90.00  paid $100.00  (coins $0.00, ded $0.00)", "Claimed
  amount: $30,479.20" together with "Repair estimate: $36,301.22", "Total drug cost: $10.00"
  together with "Patient pay amount: $0.00"); a lone total never qualifies.
  Exclusions Cited: any line stating an item, peril, circumstance, or property use is excluded or
  not covered.
  Policy Limits: a stated money or duration maximum, quoted from its own sentence.
  Reservation Of Rights: any statement that rights are reserved or acceptance is provisional.
  Timely Notice: the printed notification/report date line (e.g. "Date of Report: 2026-02-25") or
  any statement about when the loss was notified.
  Proof Of Loss: the submission of a documented proof — attachments or filed documents used as
  proof ("...is supported by the tenancy agreement dated 2025-06-01", "I am submitting additional
  invoices as requested", the "Police Report Filed:" line of an FNOL).
  Subrogation: any statement of subrogation rights or recovery against the responsible third party.
  Independent Medical Exam: any requirement that the insured attend an examination.
  Evidence (field level): exactly one of your item lines — the one printed earliest in the document,
  repeated verbatim as one contiguous quote of at most 300 characters. Not a summary paragraph,
  never a stitch of distant lines.
<Omit a category that the document does not state; the document's silence is not_found, not an
inference.>
<document>
${doc_text_window}
</document>
