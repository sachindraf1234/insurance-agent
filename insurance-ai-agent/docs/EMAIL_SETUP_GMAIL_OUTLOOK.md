# Email setup (send + reply notifications)

## Recommended: SMTP/IMAP (mode: "smtp" in config.yaml)
### Yahoo Mail (configured for this installation)
- The sending mailbox is the account in local `.env` (`MAIL_ADDRESS`); store its Yahoo App Password in `MAIL_PASSWORD` only. This is separate from the insurer recipients below.
- SMTP: `smtp.mail.yahoo.com:587` with STARTTLS; IMAP: `imap.mail.yahoo.com:993` over SSL.
- Allianz outbound: To `chamikafernando456@gmail.com`; CC `chamikaf@allianz.lk` only.
- LOLC outbound: To `suthsil73@gmail.com` only; no CC.
- `warunic@allianz.lk` is receive-only: it remains on the inbound reply allowlist but is explicitly blocked as an outbound recipient.
- Inbox checking is allowlisted to unread messages from the four authorized From addresses. Other senders are not processed; exact sender headers are validated before full-message fetch.
- Never put `.env` into a zip, Git repository, or share it. Because the app password was pasted into chat, revoke it and create a fresh Yahoo app password after setup, then replace `MAIL_PASSWORD` in `.env`.

### Gmail
1. Google Account → Security → turn ON **2-Step Verification**.
2. Security → **App passwords** → create one for "Insurance Agent" (16 characters).
3. In `.env`: `MAIL_ADDRESS=you@gmail.com`, `MAIL_PASSWORD=<that app password>` (NOT your normal password).
4. config.yaml already has smtp.gmail.com:587 / imap.gmail.com:993.
### Outlook / Microsoft 365
- smtp_host: `smtp.office365.com` (587), imap_host: `outlook.office365.com` (993).
- If your admin disabled basic auth, use Playwright webmail mode below.
### Company/cPanel mail
Use the hosts your provider gave you (usually `mail.yourdomain.lk`). Same .env fields.

## Alternative: Playwright webmail (mode: "playwright")
Sends through Gmail/Outlook in a real browser — no app password needed.
```bash
python playwright_agent/webmail_agent.py --login gmail   # log in ONCE (2FA ok)
# set config.yaml -> email.mode: "playwright", email.webmail: "gmail" | "outlook"
```
Session is saved in `instance/browser_profile/` (keep private — it is a logged-in session).
Receiving/notifications still use IMAP (read-only). If IMAP is also unavailable, tell the
developer — an inbox-scraping Playwright watcher can be enabled per webmail UI.

## How notifications work
Watcher polls UNSEEN inbox mail (`poll_seconds`) → insurer reply? parse policy nos by passport →
applicant status = POLICY_RECEIVED + 🔔 dashboard/Telegram. Embassy wording + date? →
EXTEND draft auto-created + 🔔. Run daily with `python run.py --watch`, or click 📥 anytime.

## Telegram (optional, free)
Create a bot with @BotFather → put token + your chat id in `.env` → get phone push alerts.

## Message templates and EXTEND grouping
The current NEW and EXTEND wording/signatures are built into the application. NEW includes applicant names. EXTEND email text displays the **departure date** (not the arrival date); the insurer Excel still contains the separate departure and arrival fields. In **Batches**, applicants are grouped by insurer, type, mode, and the relevant date. Applicants with another date belong in a separate group. If you want custom email body text, provide the exact NEW and EXTEND wording for each insurer and it can be installed before enabling automatic sending.

In the Email queue, approved applicants appear automatically. Filter by insurer, NEW/EXTEND, and Single/Batch; Singles list each applicant's name, passport number, and city, while Batch rows list each member. With **Auto-send OFF** (safe default), prepare the Excel, preview the prepared job, then click **Send now**. With it **ON**, Singles send immediately after final-check approval; Batch groups wait until all same-date applicants are approved, then prepare and send. Turning the setting on does not send an email immediately; it affects later approvals/preparation. Prepared drafts already in the queue still require clicking **Send now**. Send success/failure is logged and shown as a notification.

## Current insurer email setup
- Outbound To/CC recipients are configured as listed above for both NEW and EXTEND email jobs; the inbox allowlist uses the same four addresses.
- Every email includes its generated insurer Excel. If you tick **Attach this uploaded passport copy** in that applicant's Final Check, the saved selection makes that applicant's own passport file attach automatically on both manual and automatic sends (including Batch groups). Leave it unticked to omit that copy. The Email Queue's optional file selector is for extra copies only.
