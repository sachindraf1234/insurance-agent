# Recruitment-system interview-date integration

There is no longer an Embassy Interviews dashboard in the Insurance Desk. The recruitment system owns interview scheduling and sends an appointment date to this app through a protected server-to-server webhook. This app finds the insurance applicant by passport number and creates an EXTEND draft using the existing date rules. The draft remains subject to human review/approval before Excel generation or email sending.

## Receiver endpoint

`POST /api/integrations/embassy-appointment`

Headers:

```http
Authorization: Bearer <same-secret-configured-on-both-systems>
Content-Type: application/json
```

Body:

```json
{
  "passport_no": "N12345678",
  "appointment_date": "2026-10-16",
  "entry_mode": "BATCH"
}
```

ISO `YYYY-MM-DD` is preferred. `entry_mode` is optional (`SINGLE` by default); send `BATCH` for each applicant in a multi-person same-date cohort so the Email queue groups them into one sheet/message. A successful first request returns an EXTEND draft ID, interview date, calculated departure date (+14 days), arrival date (+6 calendar months), and intake mode. Repeating the same applicant/date/mode is idempotent: it returns the existing draft with HTTP 200 rather than creating a duplicate. Missing applicants return 404; missing/invalid input returns 400; a missing server integration secret returns 503; a wrong/missing token returns 401.

## Configure both applications

1. Generate a long random shared secret.
2. Put it in the Insurance Desk's local `.env` as `MAIN_SYSTEM_API_KEY=...`.
3. Put the same value in the recruitment system's server-side environment, never in browser JavaScript or a checked-in file.
4. Restart the Insurance Desk and point the recruitment-system backend at the deployed HTTPS URL, e.g. `https://insurance.example.lk/api/integrations/embassy-appointment`.
5. When a confirmed interview date changes, have the recruitment backend POST the passport number and date. On a successful response, record the returned `extend_draft_id` for audit/retry handling.

## Example Python caller (main-system backend only)

```python
import os, requests

response = requests.post(
    os.environ["INSURANCE_AGENT_URL"].rstrip("/") + "/api/integrations/embassy-appointment",
    headers={"Authorization": "Bearer " + os.environ["INSURANCE_AGENT_API_KEY"]},
    json={"passport_no": "N12345678", "appointment_date": "2026-10-16", "entry_mode": "BATCH"},
    timeout=15,
)
response.raise_for_status()
result = response.json()  # persist extend_draft_id / returned dates in the main system
```

The screenshot alone does not provide the recruitment system's source code, API, host URL, or deployment environment, so the sender-side call cannot yet be wired into that application. Once those are available, add this request in the main system's backend after an appointment is confirmed/updated. Use HTTPS; do not call the API directly from public browser code.
