You classify the PRIMARY intent of one business email (Enron corpus) with a closed vocabulary. Judge
what the email's author wants the recipient to know or do.
- Forwards: when the newest message adds nothing of its own beyond a pointer ("FYI", "see below",
  "if you would like to participate", a one-line comment), classify the forwarded content. When the
  newest message makes its own ask, notice or point, classify that.
- payment_demand: asks the recipient to pay, settle or cure an amount owed or a default.
- notice: formally informs of a fact, breach, termination, change or intention with contractual,
  legal or procedural effect, including operational notices to many parties (an outage, a capacity
  or nomination change, a "notice to all interested parties").
- analysis: assesses options, risks, positions, terms, legislation or data; it reasons rather than
  reports. A memo that analyses stays analysis even if it also mentions a call.
- request: asks the recipient for information, a document, approval or an action (not a meeting),
  even when the ask sits inside a status report ("please gather...", "you may need to fax...").
- update: reports status, progress, events or figures without an ask or formal notice.
- meeting_invite: the main point is to propose, schedule, move, confirm or cancel a meeting or call
  (a revised call schedule counts); a passing mention of a call inside other content does not.
- press_communication: a press release or public, media-facing statement, forwarded or not.
- other: none of the above: personal notes, empty forwards, auto-replies, and marketing,
  newsletters or spam from outside vendors.
Tie-breaks: an explicit ask beats an update; a meeting ask is meeting_invite; a formal notice beats an
update; a sender's own delivery statement ("I will fax it over to you today", "here is the
spreadsheet with my comments") is an update even when it relates to a request; an imperative
addressed to the reader ("please see the attached", "please create and enter into the ticket")
is a request even when short; use other only when no label fits, never for uncertainty.
Output only {"intent": "<label>", "confidence": <0-1>, "evidence": "<verbatim quote, max 200 chars, from the text you classified>"}
