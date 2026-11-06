Document: a commercial contract, CUAD family ${subclass} (${family_definition}). Fields:
- document_name (name): the contract's name as given (e.g. "Web Hosting Agreement"); never from the file name.
- parties (entity_list:name): each distinct contracting party, full legal name plus the parenthetical
  alias when printed ("Acme Corp. (\"Acme\")").
- party_roles (entity_list:free_text): "<party>: <role>" for each party, role one of ${party_roles}.
- signatories (entity_list:name): the people who sign, as printed in the signature blocks.
- agreement_date (date): the "dated as of" or signature date.
- effective_date (date): when the agreement takes effect; a defined Effective Date wins over the
  signature date.
- expiration_date (date): the stated end date of the initial term; not_found if only a duration is stated.
- term_length (free_text): the duration of the agreement itself, not a sub-period.
- renewal_terms (free_text): renewal, extension or evergreen language.
- termination_notice_period (free_text): notice required to terminate or to stop renewal.
- governing_law (name): copy ONLY the sentence naming the jurisdiction whose laws govern; no forum,
  venue or attorneys' fees.
- dispute_forum (name): the courts or arbitral seat for disputes, as printed.
- contract_value (money): stated monetary consideration; never computed.
- payment_terms (free_text): how and when the consideration is paid.
- territory (free_text): the stated territory.
Family hints (help you find the fields above; not output): ${family_field_hints}
${expert_span_hints}
<document>
${doc_text_window}
</document>
