"""Reads a FreeISPRadius customer export (.xlsx or .csv) into plain dicts.
Pure parsing — no database — so it can be checked against a real file on its
own. db/customers.py does the saving.

The export has a title row above the real headers (row 1 "JASIRI NET
LIMITED", headers on row 2), so the header row is FOUND (first of the top
rows containing both "Username" and "Phone") rather than assumed.

Columns used: Username, Id, Name, Phone, Plan, Expiry, Account (Active /
Expired / Inactive), Status (Enable / Disable). Everything else is ignored.
"""
import csv
import io
from datetime import datetime, timedelta

import openpyxl

from db.alert_channels import _normalize_kenyan_phone as normalize_phone

REQUIRED_HEADERS = ("username", "phone", "account", "status")
HEADER_SEARCH_ROWS = 10
MAX_ROWS = 20000

# The export's dates are Nairobi wall-clock text; every timestamp column in
# this app holds UTC.
EAT_OFFSET = timedelta(hours=3)


class ImportFileError(ValueError):
    """The file isn't something we can read — the message is shown to the user."""


def _cell(v):
    return str(v).strip() if v is not None else ""


def _read_rows(filename, content):
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        try:
            ws = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True).active
            return [list(r) for r in ws.iter_rows(values_only=True)]
        except Exception as e:
            raise ImportFileError(f"Couldn't open that as an Excel file: {e}")
    if name.endswith(".csv"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        return list(csv.reader(io.StringIO(text)))
    raise ImportFileError("Upload the customer export as .xlsx or .csv")


def _parse_expiry(text):
    """'31/10/2026 16:35:07' (dd/mm/yyyy, Nairobi time) -> naive UTC datetime,
    or None when blank (an 'Inactive' account that never activated) or
    unreadable."""
    if not text:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt) - EAT_OFFSET
        except ValueError:
            continue
    return None


def parse_customer_file(filename, content):
    """Returns {"customers": [...], "bad_phone": [username, ...],
    "duplicates": [username, ...]}. Raises ImportFileError for an unreadable
    file or one without the expected columns.

    A row is dropped (counted in "duplicates") when its username already
    appeared earlier in the same file — the first one wins. A row with no
    usable phone is still returned, with phone None, and its username is
    listed in "bad_phone" so the user can fix it at the source."""
    rows = _read_rows(filename, content)
    if len(rows) > MAX_ROWS:
        raise ImportFileError(f"That file has more than {MAX_ROWS} rows — is it the right one?")

    header_idx, index = None, {}
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        names = {_cell(c).lower(): j for j, c in enumerate(row) if _cell(c)}
        if all(h in names for h in REQUIRED_HEADERS):
            header_idx, index = i, names
            break
    if header_idx is None:
        found = [_cell(c) for r in rows[:HEADER_SEARCH_ROWS] for c in r if _cell(c)][:15]
        raise ImportFileError(
            "Couldn't find the header row. Expected columns: Username, Phone, Account, Status. "
            f"Saw: {', '.join(found) or 'nothing'}"
        )

    def get(row, header):
        j = index.get(header)
        return _cell(row[j]) if j is not None and j < len(row) else ""

    customers, bad_phone, duplicates, seen = [], [], [], set()
    for row in rows[header_idx + 1:]:
        username = get(row, "username")
        if not username:
            continue
        if username in seen:
            duplicates.append(username)
            continue
        seen.add(username)
        phone_raw = get(row, "phone")
        phone = normalize_phone(phone_raw)
        if phone is None:
            bad_phone.append(username)
        customers.append({
            "username": username,
            "external_id": get(row, "id") or None,
            "name": get(row, "name") or None,
            "phone": phone,
            "phone_raw": phone_raw,
            "plan": get(row, "plan") or None,
            "expiry": _parse_expiry(get(row, "expiry")),
            "account_status": get(row, "account").title() or "Unknown",
            "enabled": get(row, "status").lower() != "disable",
        })
    return {"customers": customers, "bad_phone": bad_phone, "duplicates": duplicates}
