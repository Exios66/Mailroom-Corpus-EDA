Document: one business message (${subclass}). Many messages in this corpus carry no headers;
label only what the text itself shows. Fields:
- sender (name): the author as printed in a From: header, or the sign-off name; when the sign-off
  lists several people ("X, title; Y"), take the first signatory's full printed name only; not_found if neither.
- recipient (name): the main addressee as printed in a To: header, or the salutation name ("Dad,"
  -> "Dad"); a real To: header wins over any name the body addresses; not_found if neither.
- additional_recipients (entity_list): other To: and Cc: addressees as printed; [] with not_found if none.
- communication_date (date): a send date printed in the message (Date: or Sent: line), ISO
  YYYY-MM-DD; not_found if none is printed.
- demand_amount (money): an amount the author demands be paid; not_found if none.
- action_items (entity_list): at most 3 concrete actions the recipient is asked to take, each a short
  imperative grounded in the text, copied with its own sentence punctuation ("Call Gas Control 24
  hours in advance at (304) 357-2606 to request approval."); when more than 3 exist, take the ones
  addressed to the reader most directly, earliest in the text; [] with not_found if none.
- urgency (label, never not_found): ${urgencies}. time-sensitive = a stated deadline or date that
  is not the send date itself; urgent = explicit urgency words ("asap", "immediate", "urgent",
  "first thing tomorrow") or an ask for the same day the newest message was sent — judge the day
  from that message's own printed send date when it carries one; a forward with no printed date
  is urgent only via the urgency words; critical = an emergency, legal deadline or outage;
  otherwise routine.
<email>
${doc_text_window}
</email>
