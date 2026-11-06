Document: a merger agreement (MAUD). Shown: ${window_description}. Fields:
- document_name (name): the agreement title as stated ("AGREEMENT AND PLAN OF MERGER").
- parties (entity_list:name): Parent, Merger Sub, the Company and any other contracting entity, as written.
- acquirer (name): the Parent / acquiring company. target (name): the Company being acquired.
- merger_sub (entity_list:name): the merger subsidiary or subsidiaries.
- signatories (entity_list:name): the people who sign for each party.
- effective_date (date): a calendar date only; not_found when only an Effective Time is defined.
- effective_time (free_text): how the Effective Time is defined, as written.
- governing_law (name): ONLY the governing-law sentence; no forum or venue.
- per_share_cash_amount (money): cash paid per target share. exchange_ratio (free_text): stock per
  target share, as written.
- target_termination_fee (money): the fee the Company pays on termination.
  parent_termination_fee (money): any reverse termination fee Parent pays.
- outside_date (date): the End Date / Outside Date (ISO, or as printed if relative).
- financial_advisors (entity_list:name): financial advisors or brokers named for any party.
- legal_counsel (entity_list:name): law firms named in the notice "with a copy to" lines.
- intent (label): ${merger_intents} (an amendment to an existing merger agreement is amend_merger).
- subject_matter (free_text): one grounded sentence on who acquires whom and how.
- keywords (entity_list:name): up to 8 phrases copied from the text.
${consideration_line}
<document>
${doc_text_window}
</document>
