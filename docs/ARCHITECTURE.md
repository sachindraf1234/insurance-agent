# Architecture

```
Documents (Passport / Police / Application)
   │  app/ocr/engine.py  (PyMuPDF → Paddle/Rapid/Tesseract → Ollama VLM)
   ▼
Field extraction (app/ocr/extractors.py + mrz.py check-digits)
   ├─ address_parser.py → NO./STREET  └─ city_matcher.py → dropdown CITY (fuzzy + review)
   ├─ phone.py → local 10-digit LK mobile (leading 0 retained)    └─ dates_engine.py → NEW / EXTEND(+14d) / +6mo
   ▼
Flask Review UI (app/routes.py, SQLite app/models.py) — human approves every row
   ▼
excel_gen.py — exact Allianz/LOLC columns + dropdown validations (_LISTS sheet)
   ▼
Send: smtp_imap.py (SMTP)  OR  playwright_agent/webmail_agent.py (webmail session)
   ▼
Inbox watcher (scheduler.py + inbox_service.py): IMAP UNSEEN →
   reply_parser.py: policy nos → POLICY_RECEIVED 🔔 | embassy date → EXTEND draft 🔔
MCP: mcp_server/server.py wraps the same pipeline for AI assistants (no send tool).
State machine: EXTRACTED→NEEDS_REVIEW→APPROVED→EXCEL_GENERATED→EMAIL_SENT→AWAITING_REPLY
  →POLICY_RECEIVED→(APPOINTMENT_RECEIVED)→EXTEND_DRAFT→…→EXTENDED_POLICY_RECEIVED→DONE
Config: config.yaml (insurers, subjects, defaults) + .env (secrets). Formats: app/constants.py.
```
Data file: instance/insurance.db (SQLite) · Outputs: output/ · Uploads: instance/uploads/


### Departure and arrival dates
For NEW, departure equals the issue date. For EXTEND, departure equals the embassy interview date plus 14 days. For both workflows, arrival is six calendar months after departure; if that day number is unavailable in the target month, it clamps to that month’s last valid day (for example, 31 August → 28/29 February). Excel export writes these computed values in the insurer-specific date format.
