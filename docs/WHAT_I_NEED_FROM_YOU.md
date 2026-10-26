# What I need from you to go live

## Completed
- Executive Dashboard is summary-only. Insurance navigation is grouped as NEW → Single/Batch and EXTEND → Single/Batch.
- Allianz NEW and EXTEND columns and row defaults are checked against the supplied PDFs. Defaults repeat on every applicant row: Territorial Area `Worldwide E`; Total Premium `15500`; Agent `DDP JAYASURIYA`; ACS Agent Code `0016938`; I Code `0017183`; Individual Contract `Overseas Emergency Accident and Sickness Medical Expenses-50 000  Deductible 100`. STATUS is `NEW` for new cover and `EXTEND` for extensions.
- LOLC columns mapped (NEW 12 columns, EXTEND 13 columns).
- Date engines, city matcher, address splitter, and phone normaliser are in place.
- Free OCR tested on the supplied police reports.

## Still needed before full production verification
1. **Mailbox setup:** Allianz outbound To → `chamikafernando456@gmail.com`, CC → `chamikaf@allianz.lk` only; `warunic@allianz.lk` is inbound-only and blocked from sending. LOLC outbound → `suthsil73@gmail.com` only. The sender is the Yahoo account configured in local `.env`. Inbox checks are restricted to unread messages from the four authorized addresses. Add a fresh Yahoo app password to local `.env`; send only after explicit approval for a specific email. No real email has been sent; rotate the previously shared app password before use.
2. **Passport/application samples:** one passport photo page and one applicant application sample would help tune passport/telephone extraction on real scans.
3. **LOLC date separator:** the system follows DD/MM/YYYY with `/`; the supplied samples print DD.MM.YYYY. Change `date_sep` in `config.yaml` if LOLC requires dots. The sample's 2026 arrival year appears inconsistent with the +6-month rule; the system calculates the correct date from the applicant's departure date.
4. **Main recruitment-system sending side:** the protected receiver is ready at `POST /api/integrations/embassy-appointment` (see `docs/MAIN_SYSTEM_INTEGRATION.md`). Provide the main-system project/repository (or backend framework and HTTPS URL) to wire and verify its sender. Passport number is currently the matching key.
5. **Insurer reply sample:** one real reply (Excel/PDF) is needed to verify policy-number parsing against the actual reply format.
6. **Salutation convention:** the system suggests Ms. for women; staff choose Miss/Mrs./Ms. per applicant. Confirm if this default suggestion should change.
