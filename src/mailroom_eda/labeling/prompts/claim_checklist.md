Document: one insurance claim record (${subclass}). Field:
- claim_checklist (entity_list:free_text): present-only items "<Category>: <short verbatim evidence>"
  for these exact categories only: ${checklist_categories}. Omit absent categories; if none apply use
  not_found. For tabular CMS rows, quote the column value as evidence.
<document>
${doc_text_window}
</document>
