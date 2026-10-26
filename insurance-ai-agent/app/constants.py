"""Single source of truth for dropdowns and insurer Excel formats.
Everything here was transcribed from the user's sample PDFs / screenshots.
If an insurer changes a format, edit ONLY this file (+ config.yaml defaults).
"""

# --- SALUTATION dropdown (from SALUTATION.jpg + SALUTATION2.jpg, combined) ---
SALUTATIONS = [
    "Capt.", "Dr.", "His Eminence", "M/S", "Master", "Miss", "Mr.",
    "Mrs.", "Ms.", "Pastor", "Prof.", "Rev.", "Rev. Father", "Ven.",
]

GENDERS = ["Male", "Female"]

# Default salutation suggestion (human must confirm marital-status ones)
def suggest_salutation(gender: str) -> str:
    return "Mr." if (gender or "").lower().startswith("m") else "Ms."

# --- Column orders EXACTLY as in the sample files (note: insurers spell it DEPATURE) ---
ALLIANZ_COLUMNS = [
    "E-TRAVEL POLICY NO", "ACS POLICY NO", "ISSUE DATE", "STATUS",
    "CUSTOMER PIN", "SALUTATION", "GENDER", "FIRST NAME", "LAST NAME",
    "PASSPORT NO", "BIRTHDAY", "CONTACT NO", "TERRITORIAL AREA",
    "DEPATURE DATE", "ARRIVAL DATE", "PRODUCT", "TOTAL PREMIUM",
    "AGENT NAME", "NO.", "STREET", "CITY", "ACS AGENT CODE", "I CODE",
    "INDIVIDUAL CONTRACT",
]

LOLC_NEW_COLUMNS = [
    "ISSUE DATE", "SALUTATION", "GENDER", "FIRST NAME", "LAST NAME",
    "PASSPORT NO", "BIRTHDAY", "DEPATURE DATE", "ARRIVAL DATE",
    "NO.", "STREET", "CITY",
]

LOLC_EXTEND_COLUMNS = [
    "INSURANCE POLICY NO", "ISSUE DATE", "SALUTATION", "GENDER",
    "FIRST NAME", "LAST NAME", "PASSPORT NO", "BIRTHDAY",
    "DEPATURE DATE", "ARRIVAL DATE", "NO.", "STREET", "CITY",
]

COMPANIES = {
    "ALLIANZ": {
        "display": "Allianz Insurance",
        "date_order": "MDY",          # Legacy metadata; sheet dates are formatted per column in excel_gen.py.
        "date_sep": "/",
        "columns_new": ALLIANZ_COLUMNS,
        "columns_extend": ALLIANZ_COLUMNS,   # same sheet; STATUS differs (config)
        # Defaults verified against the supplied Allianz NEW and EXTEND forms.
        "defaults": {
            "TERRITORIAL AREA": "Worldwide E",
            "PRODUCT": "Single Trip Gold Plan - USD 50,000 Coverage (per person)",
            "TOTAL PREMIUM": 15500,
            "AGENT NAME": "DDP JAYASURIYA",
            "ACS AGENT CODE": "0016938",         # text! keep leading zeros
            "I CODE": "0017183",
            "INDIVIDUAL CONTRACT": "Overseas Emergency Accident and Sickness Medical Expenses-50 000  Deductible 100",
            "STATUS_NEW": "NEW",
            "STATUS_EXTEND": "EXTEND",
        },
    },
    "LOLC": {
        "display": "LOLC Insurance",
        "date_order": "DMY",          # Legacy metadata; supplied LOLC sheets use DD.MM.YYYY.
        "date_sep": ".",
        "columns_new": LOLC_NEW_COLUMNS,
        "columns_extend": LOLC_EXTEND_COLUMNS,
        "defaults": {},
    },
}

# Applicant workflow states
STATUSES = [
    "EXTRACTED", "NEEDS_REVIEW", "APPROVED", "EXCEL_GENERATED",
    "EMAIL_SENT", "AWAITING_REPLY", "POLICY_RECEIVED",
    "AWAITING_EMBASSY", "APPOINTMENT_RECEIVED", "EXTEND_DRAFT",
    "EXTEND_SENT", "EXTENDED_POLICY_RECEIVED", "DONE", "ERROR",
]
