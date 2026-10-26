# 🛡 Insurance AI Agent — Travel Insurance Email Automation (Allianz + LOLC)

Upload passport + police report → free offline OCR extracts details → you review →
system builds the insurer's exact Excel → emails it → **watches your inbox and notifies
you when the insurer replies with policy numbers**. Embassy interview dates are managed in
your main recruitment system and synced here through a protected API to calculate EXTEND dates.

**100% free & open-source stack. No paid APIs. Documents never leave your computer.**

## Daily workflow
The **Executive Dashboard** is a summary view of workload and recent applicants. Open **Insurance** in the sidebar to choose NEW or EXTEND, then Single or Batch. Choose Allianz/LOLC; the matching sheet columns preview automatically. Single mode has separate passport, police, and optional application/phone inputs. Batch mode uses one mixed multi-file picker: add files one by one or select several together; local OCR sorts each file into passport, police, application/phone, or (for EXTEND) policy categories and processes them in one job. Review the detected category labels and every applicant before approval. After extraction, **Clear uploaded source documents** removes that batch's source folder while preserving linked passport copies needed for optional email attachments. For EXTEND, policy copies are optional; local OCR extracts their numbers for Review and the insurer's EXTEND sheet.

   📄 **Police-report rule — applies to EVERY upload** (single, bulk, re-upload; NEW & EXTEND):
   the report is OCR'd → address extracted → split into NO. / STREET → the **last city is matched
   to the CITY dropdown list and filled automatically**. The police report stays primary; a
   matching application form can corroborate/correct OCR-damaged components only when its
   passport number matches the passport in the same upload. If the first OCR pass can't match a
   city, other OCR engines/variants are tried automatically; if still unsure, Review shows
   one-click city candidates. You can also re-upload a report — or paste the address — on any
   applicant's Review page to re-run this step.
2. **Review** → red fields = OCR unsure. Fix, pick Salutation/Gender/City from dropdowns, Approve. If a passport copy is available, you may select its per-applicant attachment checkbox before approval.
3. **Email** → approved applicants appear here automatically. Auto-send is off by default. If you enable it, Singles send after approval; Batch groups send after all applications for that date are approved. With auto-send off, prepare/preview and send manually; optional passport-copy files can be added there too.
4. **Replies** → click *📥 Check inbox* (or run with `--watch`). Policy numbers are matched to passports and notifications appear in the dashboard.
5. **Interview date sync** → the main recruitment system calls the protected webhook. It creates an EXTEND draft (departure = interview +14 days; arrival = +6 calendar months) for Review.

## Rules built in (from your samples)
| Rule | Implementation |
|---|---|
| FIRST NAME / LAST NAME | FIRST NAME = passport **Surname**, LAST NAME = **Given names** (as in your sheets) |
| Address | Match CITY first; move an explicit `NO` number—or the first house-number token anywhere before CITY—to **NO.**; put every other pre-city address detail into **STREET**. A passport-matched application may fill an OCR-missed police-report number/street. |
| NEW dates | Departure = Issue date · Arrival = +6 months (same day, month-end clamped) |
| EXTEND dates | Departure = Embassy appointment **+14 days** · Arrival = +6 months |
| Date formats | **Allianz MM/DD/YYYY** · **LOLC DD/MM/YYYY** (separator switchable in `config.yaml`) |
| Allianz row defaults | Repeated on every applicant row for NEW and EXTEND: Territorial Area `Worldwide E`; Total Premium `15500`; Agent `DDP JAYASURIYA`; ACS Agent Code `0016938`; I Code `0017183`; Individual Contract `Overseas Emergency Accident and Sickness Medical Expenses-50 000  Deductible 100`. Status is `NEW` or `EXTEND` to match the supplied form. |
| Salutation/Gender | Dropdowns only: Capt., Dr., His Eminence, M/S, Master, Miss, Mr., Mrs., Ms., Pastor, Prof., Rev., Rev. Father, Ven. · Male/Female |
| Telephone | From application OCR or manual; saved in local 10-digit form with the leading `0` (e.g. `0771234567`) for Allianz CONTACT NO. `+94`/`0094` inputs are converted to the same local form. |
| Passport accuracy | MRZ check digits validate passport number/date; printed name is cross-checked and disagreements are flagged for human review |

## Install (Windows / Linux / Mac)
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
# RapidOCR + ONNX Runtime CPU (free/local) are installed together; optional OCR backends are in requirements-ocr.txt
playwright install chromium                # only needed for webmail/portal automation
copy .env.example .env                     # then put your mailbox + App Password in .env
# edit config.yaml -> insurer email addresses
python run.py --watch                      # open http://127.0.0.1:5000
```
Guides: `docs/EMAIL_SETUP_GMAIL_OUTLOOK.md` · `docs/OCR_GUIDE.md` · `docs/MCP_GUIDE.md` ·
`docs/ARCHITECTURE.md` · `docs/MAIN_SYSTEM_INTEGRATION.md` · **`docs/WHAT_I_NEED_FROM_YOU.md` ← start here**

## AI-assistant (MCP) use
`python mcp_server/server.py` exposes tools (`extract_documents`, `match_city`,
`calculate_dates`, `generate_insurer_excel`, `record_embassy_appointment`, `check_inbox_now`, …)
to Claude Desktop / Cursor. Email sending is not exposed as an AI/MCP tool; a human reviews every row and must either explicitly enable auto-send or click Send manually.

## Safety
Nothing is emailed without human approval of every row. Configurable auto-send only dispatches after approval and is off by default. OCR confidence is stored per field.
Back up `instance/insurance.db` + `.env`. The insurers' sample files contain personal data —
keep this folder on your office PC, not in public cloud/GitHub.
