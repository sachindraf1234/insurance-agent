#!/usr/bin/env python3
"""
SLBFE "Training & Experience Inquiry" bulk checker  (Python + Playwright)  -- v2

WHAT IT DOES
  For every applicant in name.txt it searches the portal with
      1. Current Passport   2. Current NIC   3. Previous Passport   4. Previous NIC
  (numbers that are "-" are skipped) and looks through ALL records returned for a
      "Training~ ... Pre Departure Training <date>"   record.

  * Pre Departure Training FOUND  -> screenshot saved as  screenshots/<No>_<NAME>.png,
                                     applicant marked TRAINED in progress.json and is
                                     SKIPPED on every later run.
  * NOT found                     -> applicant marked NOT_TRAINED, listed in
                                     not_trained.csv / not_trained.txt, printed as an
                                     ACTION NEEDED alert (optional e-mail alert).
  * At the end all screenshots are zipped into screenshots.zip.

SETUP (once)
    pip install playwright
    playwright install chromium

RUN
    python slbfe_training_check.py                  # normal run (skips already-TRAINED people)
    python slbfe_training_check.py --only 1-10,17   # only these row numbers
    python slbfe_training_check.py --recheck-all    # ignore progress.json, check everybody again
    python slbfe_training_check.py --zip-only        # just rebuild screenshots.zip
    python slbfe_training_check.py --notify-email    # also e-mail the not-trained list (see EMAIL below)

LOGIN  (exact entry URL, never altered: https://services.slbfe.lk/account/ext_account_main)
  That page opens automatically when no valid session exists and shows all 3 login
  methods (Public Users / Authorized Personnel Only / Development Officer). For
  automatic credential entry the script clicks the 'Authorized Personnel Only -> Access'
  link to reach the Sign In form (usrLoginName / usrPassWord -> chkuser.asp).
  Three ways to authenticate, in order:
    1. saved session in slbfe_session.json (created automatically after the first login)
    2. environment variables SLBFE_USER and SLBFE_PASS (fully automatic, works with --headless)
    3. type the username/password directly into the visible browser window;
       the script detects the successful login and continues by itself
  In --headless mode without env vars it prompts for the credentials in the terminal.

EMAIL (optional, only with --notify-email) - set these environment variables first:
    SMTP_HOST  SMTP_PORT(587)  SMTP_USER  SMTP_PASS  NOTIFY_TO  [NOTIFY_FROM]
"""

import argparse
import csv
import getpass
import json
import os
import re
import smtplib
import sys
import time
import zipfile
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

PORTAL_URL = "https://applications.slbfe.lk/"
INQUIRY_URL = "https://applications.slbfe.lk/logroot/agency/training/age_training/TraRegChk.asp"

# Exact SLBFE login entry URL - opens the page with all 3 login methods
# (Public Users / Authorized Personnel Only / Development Officer).
# Keep this URL exactly as provided - do not deep-link past it.
LOGIN_URL = "https://services.slbfe.lk/account/ext_account_main"
# "Authorized Personnel Only" card -> its "Access" link (relative href on the page)
AUTH_ACCESS = 'a[href$="ext_account_"]'
LOGIN_USER = 'input[name="usrLoginName"]'
LOGIN_PASS = 'input[name="usrPassWord"]'
LOGIN_SUBMIT = 'button[type="submit"]'

SCRIPT_DIR = Path(__file__).resolve().parent
SESSION_FILE = SCRIPT_DIR / "slbfe_session.json"
USER_ENV, PASS_ENV = "SLBFE_USER", "SLBFE_PASS"

# The training that MUST be present (case-insensitive; "Pre Departure", "Pre-departure", "predeparture" all match)
REQUIRED_TRAINING_RE = re.compile(r"pre[\s\-]*departure\s+training", re.I)

ID_ORDER = [
    ("Current Passport", "cur_pp"),
    ("Current NIC", "cur_nic"),
    ("Previous Passport", "prev_pp"),
    ("Previous NIC", "prev_nic"),
]

# Record line example:
#   PP N8074311 NIC 980260925V Training~ Romania Pre departure Training 14/10/2026
#   PP N8074311 NIC 980260925V Registration~ > 27/06/2023 | Qatar | Specialist Marketing | Source:...
RECORD_RE = re.compile(
    r"PP\s+(?P<pp>\S+)\s+NIC\s+(?P<nic>\S+)\s+(?P<kind>[A-Za-z ]+?)\s*[~\-:]\s*(?P<text>.*)$",
    re.S,
)
DATE_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")

DETAIL_COLS = ["time", "no", "name", "id_type", "id_searched", "status", "records", "note"]
SUMMARY_COLS = ["no", "name", "status", "found_by", "training", "training_date",
                "screenshot", "blacklisted", "ids_checked", "note", "last_checked"]


# =========================================================================== input
def parse_people(path):
    people = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 6 or not cells[0].isdigit():
            continue

        def clean(v):
            v = v.replace("*", "").replace("\\", "").strip().upper()
            return "" if v in ("", "-") else v

        people.append({
            "no": int(cells[0]),
            "name": cells[1].replace("*", "").strip(),
            "cur_pp": clean(cells[2]), "cur_nic": clean(cells[3]),
            "prev_pp": clean(cells[4]), "prev_nic": clean(cells[5]),
        })
    return people


def parse_only(spec):
    if not spec:
        return None
    wanted = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            wanted.update(range(int(a), int(b) + 1))
        elif part:
            wanted.add(int(part))
    return wanted


def site_accepts(identifier):
    """Mirror of the site's own chk(): >= 8 chars and characters 2-8 numeric."""
    if len(identifier) < 8:
        return False
    try:
        float(identifier[1:8])
        return True
    except ValueError:
        return False


def person_key(p):
    return p["cur_pp"] or p["cur_nic"] or f"row{p['no']}"


def safe_filename(text, limit=90):
    text = re.sub(r"[^A-Za-z0-9 _\-]", "", text).strip()
    return re.sub(r"\s+", "_", text)[:limit]


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


# =========================================================================== records
def parse_record(raw):
    raw = norm(raw)
    m = RECORD_RE.search(raw)
    if not m:
        return {"pp": "", "nic": "", "kind": "", "text": raw, "date": ""}
    text = m["text"].strip()
    tail = re.search(r"(\d{1,2}/\d{1,2}/\d{4})\s*$", text)
    first = DATE_RE.search(text)
    return {
        "pp": m["pp"].upper(), "nic": m["nic"].upper(),
        "kind": m["kind"].strip(), "text": text,
        "date": (tail or first).group(1) if (tail or first) else "",
    }


def find_required_training(records, known_ids):
    """Return (index, parsed_record, same_person) for the first Pre Departure Training record."""
    review = None
    for i, raw in enumerate(records):
        rec = parse_record(raw)
        is_training = rec["kind"].lower().startswith("training") or "training" in raw.lower()
        if is_training and REQUIRED_TRAINING_RE.search(raw):
            same = bool({rec["pp"], rec["nic"]} & known_ids)
            if same:
                return i, rec, True
            review = review or (i, rec, False)
    return review  # None, or a record whose PP/NIC are not this person's


# =========================================================================== browser
def lookup(page, identifier, alerts, timeout_ms):
    out = {"status": "ERROR", "records": [], "blacklisted": "", "note": ""}
    page.goto(INQUIRY_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    reader = page.locator('input[name="reader"]')
    if reader.count() == 0:
        out.update(status="SESSION_EXPIRED", note="Search form not found - probably logged out.")
        return out

    alerts.clear()
    reader.fill(identifier)  # site's chk() copies reader -> ppno on submit
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout_ms):
            page.click('input[name="Submit"]')
    except PWTimeout:
        out.update(status="NO_RESPONSE", note="; ".join(alerts) or "Timed out waiting for result page.")
        return out

    # a redirect to wrongway.asp means the ASP session died mid-search
    if "wrongway" in (page.url or "").lower():
        out.update(status="SESSION_EXPIRED",
                   note="Session expired - log in again in the browser window.")
        return out

    opts = [norm(t) for t in page.locator('select[name="idindex"] option').all_inner_texts() if t.strip()]
    if not opts:
        body = page.inner_text("body")
        if "session has expired" in body.lower():
            out.update(status="SESSION_EXPIRED",
                       note="Session expired - log in again in the browser window.")
            return out
        if "Training & Experience Details" in body:
            opts = [norm(ln) for ln in body.splitlines() if ln.strip().upper().startswith("PP ")]

    bl = page.locator('a[href*="wrongwayb.asp"]')
    if bl.count():
        out["blacklisted"] = norm(bl.first.inner_text() or "BLACKLISTED")

    if opts:
        out.update(status="FOUND", records=opts)
    elif "Training & Experience Details" in body:
        # we are on the results page but the server returned zero records
        out.update(status="NOT_FOUND",
                   note="; ".join(alerts) or "No records returned for this identifier")
    elif page.locator('input[name="reader"]').count():
        out.update(status="NOT_FOUND", note="; ".join(alerts))
    else:
        out.update(status="UNKNOWN_PAGE", note="Unrecognised result page (see debug_* files)")
    return out


def take_proof_screenshot(page, option_index, path):
    """Highlight the matching line in the listbox, then save a full-page screenshot."""
    try:
        page.evaluate(
            """(i) => { const s = document.querySelector('select[name="idindex"]');
                        if (s && s.options[i]) { const o = s.options[i];
                          o.style.backgroundColor = '#ffff00'; o.style.fontWeight = 'bold';
                          o.scrollIntoView({block: 'nearest'}); } }""",
            option_index,
        )
    except Exception:
        pass
    page.screenshot(path=str(path), full_page=True)


# =========================================================================== files
def load_json(path, default):
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            p.rename(p.with_suffix(f".corrupt{int(time.time())}.json"))
    return default


def save_json(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def append_detail(path, row):
    new = not Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=DETAIL_COLS)
        if new:
            w.writeheader()
        w.writerow(row)


def write_csv(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def make_zip(shot_dir, zip_path):
    files = sorted(Path(shot_dir).glob("*.png"))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.name)
    return len(files)


def send_email(subject, body):
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("NOTIFY_TO")
    if not host or not to:
        print("   (e-mail not sent: set SMTP_HOST and NOTIFY_TO environment variables)")
        return
    user, pwd = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASS", "")
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, to
    msg["From"] = os.environ.get("NOTIFY_FROM", user or "slbfe-checker@localhost")
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            if user:
                s.login(user, pwd)
            s.send_message(msg)
        print(f"   E-mail alert sent to {to}")
    except Exception as e:
        print(f"   E-mail failed: {e}")


# =========================================================================== login
def inquiry_form_present(page) -> bool:
    try:
        return page.locator('input[name="reader"]').count() > 0
    except Exception:
        return False


def _login_screen_visible(pg) -> bool:
    """True while the browser is still on an SLBFE login page:
    the 3-method page (has the Access link) or the agency Sign In form."""
    try:
        if "services.slbfe.lk/account" not in (pg.url or "").lower():
            return False
        return pg.locator(AUTH_ACCESS).count() > 0 or pg.locator(LOGIN_USER).count() > 0
    except Exception:
        return False


def _any_page_has_wrongway(context) -> bool:
    for pg in context.pages:
        try:
            if "wrongway" in (pg.url or "").lower():
                return True
        except Exception:
            pass
    return False


def _verify_session(page, context) -> bool:
    """Probe the target page. This works no matter which tab/window completed
    the login, because the ASP session cookie is shared browser-context-wide."""
    try:
        page.goto(INQUIRY_URL, wait_until="domcontentloaded", timeout=30000)
        if inquiry_form_present(page):
            context.storage_state(path=str(SESSION_FILE))
            return True
    except Exception:
        pass
    return False


def _submit_login_form(page, user: str, password: str):
    """Open the 3-method login page, click 'Authorized Personnel Only -> Access',
    fill the Sign In form (posts to applications.slbfe.lk/chkuser.asp), and
    verify the resulting session against the target page."""
    context = page.context
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(AUTH_ACCESS, timeout=30000)
    page.click(AUTH_ACCESS)
    page.wait_for_selector(LOGIN_USER, timeout=30000)
    page.fill(LOGIN_USER, user)
    page.fill(LOGIN_PASS, password)
    try:
        page.check("#checkRememberPassword")
    except Exception:
        pass
    page.click(LOGIN_SUBMIT)

    # wait until we leave the Sign In form or a wrongway rejection appears
    deadline = time.time() + 30
    while time.time() < deadline:
        if ("wrongway" in (page.url or "").lower()
                or "services.slbfe.lk/account" not in (page.url or "").lower()
                or _any_page_has_wrongway(context)):
            break
        page.wait_for_timeout(500)

    if "wrongway" in (page.url or "").lower() or _any_page_has_wrongway(context):
        try:
            msg = re.sub(r"\s+", " ", page.inner_text("body")).strip()
        except Exception:
            msg = "Wrong User Name or Password"
        return False, msg[:140]

    if _verify_session(page, context):
        return True, ""
    return False, "Login did not produce a valid portal session."


def _prompt_credentials():
    user = input("SLBFE username: ").strip()
    password = getpass.getpass("SLBFE password: ")
    return user, password


def _wait_manual_browser_login(page) -> None:
    """Headed login: the browser shows the 3-method SLBFE page and the user
    signs in manually. The script polls the shared browser session (works even
    if the portal opens in a new tab) and continues automatically."""
    context = page.context
    popups = []
    context.on("page", lambda p: popups.append(p))

    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    print("\n" + "=" * 70)
    print("LOGIN REQUIRED - the browser now shows the SLBFE page with 3 login methods.")
    print("1. Click 'Access' under 'Authorized Personnel Only' (for agencies).")
    print("2. Enter your username and password on the Sign In page.")
    print("(Credentials are typed into the website, not this terminal.)")
    print("The script continues automatically after a successful login.")
    print("=" * 70)

    deadline = time.time() + 300
    last_note = 0.0
    while time.time() < deadline:
        active = popups[-1] if popups and not popups[-1].is_closed() else page
        try:
            active.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass

        if "wrongway" in (active.url or "").lower() or _any_page_has_wrongway(context):
            print("[login] sign-in was rejected - returning to the 3-method login page...")
            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            time.sleep(1)
            continue

        if _login_screen_visible(active):
            if time.time() - last_note > 15:
                print("[login] waiting for you to complete the sign-in in the browser...")
                last_note = time.time()
            time.sleep(2)
            continue

        # left the login pages -> check whether the session cookie works
        print("[login] sign-in detected - verifying the portal session...")
        if _verify_session(page, context):
            for p in popups:   # close any helper tabs, keep the working page
                try:
                    if not p.is_closed():
                        p.close()
                except Exception:
                    pass
            print("[login] login OK - session saved. Starting automation...")
            return
        time.sleep(2)

    raise SystemExit("Login was not completed within 5 minutes. Re-run the script.")


def ensure_login(page, headless: bool) -> None:
    """1) reuse saved session, 2) SLBFE_USER/SLBFE_PASS env vars,
    3) in-browser login (headed) or terminal prompt (headless)."""
    page.goto(INQUIRY_URL, wait_until="domcontentloaded", timeout=30000)
    if inquiry_form_present(page):
        page.context.storage_state(path=str(SESSION_FILE))
        print("[login] saved session is valid")
        return

    user, password = os.getenv(USER_ENV), os.getenv(PASS_ENV)
    if user and password:
        ok, msg = _submit_login_form(page, user, password)
        if ok:
            print("[login] automatic login OK (environment credentials)")
            return
        print(f"[login] environment credentials rejected: {msg or 'unknown error'}")

    if not headless:
        _wait_manual_browser_login(page)
        return

    print("[login] no valid session and no SLBFE_USER/SLBFE_PASS environment variables.")
    for attempt in range(3):
        try:
            user, password = _prompt_credentials()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            break
        ok, msg = _submit_login_form(page, user, password)
        if ok:
            print("[login] login OK - session saved")
            return
        print(f"[login] rejected ({msg or 'unknown error'}); {2 - attempt} attempt(s) left.")
    raise SystemExit(
        "Login failed. Set SLBFE_USER and SLBFE_PASS environment variables, "
        "or run once without --headless to sign in inside the browser."
    )


# =========================================================================== main
def main():
    ap = argparse.ArgumentParser(description="SLBFE Pre Departure Training checker")
    ap.add_argument("--input", default=str(SCRIPT_DIR / "name.txt"))
    ap.add_argument("--outdir", default=str(SCRIPT_DIR))
    ap.add_argument("--only", help="Row numbers, e.g. 1-10,17")
    ap.add_argument("--recheck-all", action="store_true", help="Ignore progress.json and check everyone again")
    ap.add_argument("--zip-only", action="store_true", help="Only rebuild screenshots.zip and exit")
    ap.add_argument("--notify-email", action="store_true", help="E-mail the not-trained list at the end")
    ap.add_argument("--delay", type=float, default=1.0, help="Seconds between searches")
    ap.add_argument("--timeout", type=int, default=30, help="Page timeout (seconds)")
    ap.add_argument("--headless", action="store_true", help="Not recommended: you must log in manually")
    args = ap.parse_args()

    out = Path(args.outdir)
    shot_dir = out / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    progress_path = out / "progress.json"
    detail_path = out / "results_detail.csv"
    zip_path = out / "screenshots.zip"

    if args.zip_only:
        print(f"{make_zip(shot_dir, zip_path)} screenshots zipped -> {zip_path}")
        return

    people = parse_people(args.input)
    only = parse_only(args.only)
    if only:
        people = [p for p in people if p["no"] in only]
    if not people:
        sys.exit("No people found - check the input file / --only value.")

    progress = load_json(progress_path, {})
    todo = [p for p in people
            if args.recheck_all or progress.get(person_key(p), {}).get("status") != "TRAINED"]
    already = len(people) - len(todo)
    print(f"{len(people)} applicants loaded | {already} already TRAINED (skipped) | {len(todo)} to check")

    timeout_ms = args.timeout * 1000
    if todo:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=args.headless)
            ctx_kw = {"storage_state": str(SESSION_FILE)} if SESSION_FILE.exists() else {}
            context = browser.new_context(viewport={"width": 1280, "height": 800}, **ctx_kw)
            page = context.new_page()

            alerts = []
            page.on("dialog", lambda d: (alerts.append(d.message), d.accept()))

            # opens the SLBFE Sign In page automatically when no session exists
            ensure_login(page, args.headless)

            try:
                for n, p in enumerate(todo, 1):
                    print(f"\n[{n}/{len(todo)}] No.{p['no']}  {p['name']}")
                    known = {p[k] for _, k in ID_ORDER if p[k]}
                    entry = {
                        "no": p["no"], "name": p["name"], "status": "", "found_by": "", "training": "",
                        "training_date": "", "screenshot": "", "blacklisted": "", "ids_checked": "",
                        "note": "", "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    checked, any_records, any_error, review_note = [], [], False, ""
                    seen = set()

                    for label, key in ID_ORDER:
                        ident = p[key]
                        if not ident or ident in seen:
                            continue
                        seen.add(ident)
                        drow = {"time": datetime.now().strftime("%H:%M:%S"), "no": p["no"], "name": p["name"],
                                "id_type": label, "id_searched": ident, "status": "", "records": "", "note": ""}

                        if not site_accepts(ident):
                            drow.update(status="INVALID_FORMAT", note="Rejected by the site's own format check")
                            append_detail(detail_path, drow)
                            checked.append(f"{label}:INVALID_FORMAT")
                            print(f"   {label:<18} {ident:<14} -> INVALID_FORMAT")
                            continue

                        try:
                            res = lookup(page, ident, alerts, timeout_ms)
                            if res["status"] == "SESSION_EXPIRED":
                                print("   !! Session expired - automatic re-login required.")
                                ensure_login(page, args.headless)
                                res = lookup(page, ident, alerts, timeout_ms)
                        except Exception as e:
                            res = {"status": "ERROR", "records": [], "blacklisted": "", "note": str(e)[:200]}

                        if res["status"] == "UNKNOWN_PAGE":
                            stem = out / f"debug_{p['no']}_{ident}"
                            page.screenshot(path=str(stem) + ".png", full_page=True)
                            Path(str(stem) + ".html").write_text(page.content(), encoding="utf-8")

                        drow.update(status=res["status"], records=" || ".join(res["records"]), note=res["note"])
                        append_detail(detail_path, drow)
                        checked.append(f"{label}:{res['status']}")
                        if res["blacklisted"]:
                            entry["blacklisted"] = res["blacklisted"]
                            print(f"   !! BLACKLIST FLAG: {res['blacklisted']}")
                        if res["status"] in ("ERROR", "NO_RESPONSE", "SESSION_EXPIRED", "UNKNOWN_PAGE"):
                            any_error = True
                            entry["note"] = res["note"]

                        if res["status"] != "FOUND":
                            print(f"   {label:<18} {ident:<14} -> {res['status']} {res['note']}")
                            time.sleep(args.delay)
                            continue

                        any_records.extend(res["records"])
                        for r in res["records"]:
                            print(f"   {label:<18} {ident:<14} -> {r}")

                        hit = find_required_training(res["records"], known)
                        if hit and hit[2]:  # Pre Departure Training + same person
                            idx, rec, _ = hit
                            shot = shot_dir / f"{p['no']:03d}_{safe_filename(p['name'])}.png"
                            take_proof_screenshot(page, idx, shot)
                            entry.update(status="TRAINED", found_by=f"{label}={ident}",
                                         training=rec["text"], training_date=rec["date"],
                                         screenshot=str(shot.relative_to(out)))
                            print(f"   >>> PRE DEPARTURE TRAINING CONFIRMED ({rec['date']}) - screenshot saved: {shot.name}")
                            break
                        if hit and not hit[2]:
                            review_note = (f"Pre Departure Training record found under {label} {ident} but its "
                                           f"PP/NIC ({hit[1]['pp']}/{hit[1]['nic']}) are not this applicant's numbers")
                            print(f"   !! {review_note}")
                        time.sleep(args.delay)

                    entry["ids_checked"] = ", ".join(checked)
                    if entry["status"] != "TRAINED":
                        if review_note:
                            entry.update(status="REVIEW", note=review_note)
                        elif any_error:
                            entry["status"] = "INCOMPLETE"
                            entry["note"] = (entry["note"] or "Some searches failed") + " - re-run to retry"
                        else:
                            entry["status"] = "NOT_TRAINED"
                            if any_records:
                                kinds = sorted({parse_record(r)["kind"] or "?" for r in any_records})
                                entry["note"] = ("Records exist but NO Pre Departure Training. Found only: "
                                                 + ", ".join(kinds))
                            else:
                                entry["note"] = "No record found under any identifier"
                        print(f"   *** {entry['status']}: {entry['note']}")

                    progress[person_key(p)] = entry
                    save_json(progress_path, progress)  # saved after EVERY applicant
            except KeyboardInterrupt:
                print("\nInterrupted - progress has been saved. Run again to continue.")
            finally:
                browser.close()

    # ------------------------------------------------------------------ reports
    rows = [progress[person_key(p)] for p in people if person_key(p) in progress]
    for p in people:
        if person_key(p) not in progress:
            rows.append({"no": p["no"], "name": p["name"], "status": "NOT_CHECKED"})
    rows.sort(key=lambda r: int(r["no"]))
    write_csv(out / "results_summary.csv", SUMMARY_COLS, rows)

    pending = [r for r in rows if r["status"] != "TRAINED"]
    write_csv(out / "not_trained.csv", SUMMARY_COLS, pending)

    n_shots = make_zip(shot_dir, zip_path)

    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    lines = [f"SLBFE Pre Departure Training check - {datetime.now():%Y-%m-%d %H:%M}",
             f"Applicants without a confirmed Pre Departure Training: {len(pending)}", ""]
    for r in pending:
        lines.append(f"No.{r['no']:>3}  {r['name']}  [{r['status']}]  {r.get('note', '')}")
    alert_text = "\n".join(lines)
    (out / "not_trained.txt").write_text(alert_text, encoding="utf-8")

    print("\n================ SUMMARY ================")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"{v:>4}  {k}")
    print(f"\nScreenshots: {n_shots} files -> {zip_path}")
    print(f"Reports    : results_summary.csv | not_trained.csv | not_trained.txt | progress.json")
    if pending:
        print("\n" + "!" * 60)
        print(f"ACTION NEEDED - {len(pending)} applicant(s) have NO confirmed Pre Departure Training:")
        for r in pending:
            print(f"   No.{r['no']:>3}  {r['name']}  [{r['status']}]")
        print("!" * 60)
        if args.notify_email:
            send_email(f"[SLBFE] {len(pending)} applicants without Pre Departure Training", alert_text)


if __name__ == "__main__":
    main()
