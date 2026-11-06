Document: one business message (${subclass}). Many messages in this corpus carry no headers;
label only what the text itself shows. Fields:
- sender (name): the author as printed in a From: header, or the sign-off name; not_found if neither.
- recipient (name): the main addressee as printed in a To: header, or the salutation name ("Dad,"
  -> "Dad"); not_found if neither.
- additional_recipients (entity_list): other To: and Cc: addressees as printed; [] with not_found if none.
- communication_date (date): a send date printed in the message (Date: or Sent: line), ISO
  YYYY-MM-DD; not_found if none is printed.
- demand_amount (money): an amount the author demands be paid; not_found if none.
- action_items (entity_list): at most 3 concrete actions the recipient is asked to take, each a short
  imperative grounded in the text; [] with not_found if none.
- urgency (label, never not_found): ${urgencies}. time-sensitive = a stated deadline or date; urgent =
  explicit urgency words or a same-day ask; critical = an emergency, legal deadline or outage;
  otherwise routine.
<email>
${doc_text_window}
</email>
