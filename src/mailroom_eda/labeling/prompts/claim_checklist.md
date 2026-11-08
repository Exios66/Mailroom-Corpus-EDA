Document: one insurance claim record (${subclass}). Field:
- claim_checklist (entity_list:free_text): present-only items "<Category>: <short verbatim evidence>"
  for these exact categories only: ${checklist_categories}. Omit absent categories; if none apply use
  not_found. Rules:
  Claims differ in form: a Medicare notice prints determinations and amounts in table lines; an FNOL
  or decision letter states them in prose. Judge each category by what the document says, never by
  its layout.
  Items: each item is one plain string "Category: <quoted line>" and NOTHING ELSE — never a JSON
  object, never nested quotes, never abbreviations. The quoted line is a complete printed line of
  the document, from its own start to end, with any run of spaces or tabs collapsed to one space;
  its printed label prefix ("COVERAGE DETERMINATION:", "Deductible:", the row's "1. HCPCS 93307"),
  trailing parenthetical, and typographic quote marks stay in the quote. Never shorten,
  paraphrase, or reorder the words inside a quote. Never stitch together distant lines; the same
  printed line is one item even where similar lines repeat.
  One item per category per printed line: one line may serve two categories (a service
  line can carry both a deductible and comparable amounts). A category repeats only when the
  document prints several distinct lines for it (several deductible lines, several exclusion
  lines). Never quote corpus bookkeeping lines as facts: an echo line like "Stated outcome (verbatim): approved" is not a
  claim fact; when a determination is printed twice, quote the determination sentence once.
  Category rules (quote the line that states it):
  Coverage Determination: the outcome sentence exactly as printed ("COVERAGE DETERMINATION: APPROVED
  - this claim has been adjudicated and paid." → quote that whole line; a decision letter's
  "COVERAGE DETERMINATION: PENDING" likewise).
  Deductible: every printed deductible line, label prefix included ("Beneficiary deductible:
  $1,068.00", "Blood deductible: $0.00"); coinsurance lines never count
  as deductibles. Where a claim record states a deductible only inside a service-line tail like
  "(coins $10.00, ded $0.00)", quote that whole service line. A deductible printed as N/A, not
  available or not applicable states no deductible; omit that line.
  Amount Consistency: only when two or more comparable amounts of the same service or loss are
  printed ("1. HCPCS 93307  allowed $90.00  paid $100.00  (coins $0.00, ded $0.00)",
  "Total drug cost: $10.00" together with "Patient pay amount: $0.00"); a lone total never
  qualifies.
  Exclusions Cited: any line stating an item, peril, circumstance, or property use is excluded or
  not covered.
  Policy Limits: a stated money or duration maximum, quoted from its own sentence.
  Reservation Of Rights: any statement that rights are reserved or acceptance is provisional.
  Timely Notice: the printed notification/report date line (e.g. "Date of Report: 2026-02-25") or
  any statement about when the loss was notified.
  Proof Of Loss: the submission of a documented proof — attachments or filed documents used as
  proof ("...is supported by the tenancy agreement dated 2025-06-01", "I am submitting additional
  invoices as requested", an FNOL's "Police Report Filed: Yes" line). A police or fire brigade
  report line that says No or not filed is not proof; omit it.
  Subrogation: any statement of subrogation rights or recovery against the responsible third party.
  Independent Medical Exam: any requirement that the insured attend an examination.
  Evidence (field level): the quoted line of exactly one of your items, without its "Category: "
  prefix — the one printed earliest in the document, repeated verbatim as one contiguous quote of
  at most 300 characters. Not a summary paragraph, never a stitch of distant lines.
<Omit a category that the document does not state; the document's silence is not_found, not an
inference.>
<document>
${doc_text_window}
</document>
