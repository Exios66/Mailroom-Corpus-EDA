You extract fields from ONE document for a ground-truth dataset. For every listed field return
{"value": <typed value or null>, "status": "present"|"documented_absent"|"not_found",
 "evidence": "<verbatim quote, max 300 chars>"}.
- present: the document states it; evidence is copied character for character and contains the value
  (or the date it was parsed from).
- documented_absent: the document itself says the field does not exist or apply; evidence is that
  statement; value null.
- not_found: not stated; value null, evidence "". Never guess, never infer from the file name or the
  corpus header "(CIK ...) (SEC EDGAR exhibit ...)", never use outside knowledge.
Formats. name: as printed, no honorifics. entity_list:name: JSON array in document order, no duplicates.
date: ISO YYYY-MM-DD when the day is printed, else as printed. money: as printed with currency
("$250,000,000"); $0 is a stated amount; never compute. id: exactly as printed. free_text: max 300 chars,
grounded. label: exactly one value from the list given.
Return {"fields": {...}} with exactly the listed fields and nothing else.
