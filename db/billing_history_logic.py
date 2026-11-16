"""Old billing history: rules for the billing system's "transactions" export
(the system that sells the hotspot, and ran PPPoE until PPPoE moved to the
new billing system on 20 Jun 2026). Pure (no database, no clock), tested in
tests/test_billing_history.py; db/billing_history.py saves and queries,
tools/import_billing_history.py runs the import.

The export (owner's file transactions_2026-05-01_to_2026-11-16.csv) has one
row per transaction: #, Invoice, Username, Plan, Type (Hotspot / PPPOE /
Balance), Router, Price, Method, Recharged ("01 May 2026 00:08", Nairobi
time, to the minute) and Expiration.

How the old system booked PPPoE money (owner's explanation, 2026-11-16, and
the rows themselves): a paybill payment lands as a Balance row, which is the
cash. The plan bought with it is a PPPOE row paid "from balance" (method
"Customer - Balance", or "Mpesa Paybill Manual - Mpesa" in the same second).
Counting both would count the money twice, so the Balance row counts and the
PPPOE row is left out. A staff recharge counts, as it does in the new
system, unless it applies a paybill payment already counted.

A row counts unless a rule leaves it out. Left-out rows are kept, never
deleted, so every call can be seen and reversed:
  correction  the 12 May "hp support users" balance top-ups and the 22 May
              Manual Balance Edit: an accounting correction that cancels out
  free        KSh 0 or 1 (support, staff and test accounts), or an Admin Bypass
  repeat      the same payment twice: an M-Pesa code already used, or a
              PPPoE plan for a period already paid (same account, plan, expiry)
  balance     PPPoE paid from the account's balance, or a staff recharge
              applying a paybill payment from the 2 days before: the money
              counted when it came in
  moved       PPPoE paid on or after the move that the new billing system
              also recorded (same account, within MOVED_MATCH days): counted
              there, from the recharge export
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from db.customer_import import ImportFileError, _cell, _read_rows

REQUIRED_HEADERS = ("invoice", "username", "plan", "type", "price", "method", "recharged", "expiration")
HEADER_SEARCH_ROWS = 10
MAX_ROWS = 1_000_000
EAT_OFFSET = timedelta(hours=3)

MOVE_DAY = date(2026, 6, 20)              # PPPoE's first day on the new billing system
MOVED_MATCH = (timedelta(days=1), timedelta(days=7))   # new-system recharge from 1 day before to 7 after
PAYBILL_APPLIED = timedelta(days=2)       # a staff recharge this soon after a paybill payment applies it
CORRECTION_PLANS = {"manual balance edit", "hp support users"}

LEFT_OUT = {
    "correction": "Balance correction (cancels out)",
    "free": "KSh 0 or 1: support, staff or test",
    "repeat": "The same payment recorded twice",
    "balance": "Paid from balance: counted when the money came in",
    "moved": "After PPPoE moved: the new billing system has it",
}
SEGMENT_OF = {"hotspot": "hotspot", "pppoe": "pppoe", "balance": "pppoe"}


def _amount(text):
    try:
        return Decimal(text.replace(",", "").strip())
    except InvalidOperation:
        return None


def _when(text):
    """Nairobi wall-clock text -> naive UTC, None if unreadable."""
    for fmt in ("%d %b %Y %H:%M:%S", "%d %b %Y %H:%M"):
        try:
            return datetime.strptime(text.strip(), fmt) - EAT_OFFSET
        except ValueError:
            continue
    return None


def account_of(username, segment):
    """Who paid. Hotspot: the username as the router reports it (phone and
    device tag). PPPoE: the account name with the old system's "unknown "
    prefix (a paybill payment it couldn't link) and stray punctuation taken
    off, so 'unknown JS,19' and 'unknown JSO5' are JS19 and JS05."""
    if segment == "hotspot":
        return username
    name = re.sub(r"(?i)^unknown\s+", "", username).strip()
    js = re.fullmatch(r"(?i)js[\s,.\-]*([0-9oO]+)", name)
    return "JS" + js.group(1).upper().replace("O", "0") if js else name


def parse_export(filename, content):
    """{'rows': [...], 'unreadable': [line numbers]} with rows oldest first.
    Raises ImportFileError for a file that isn't the transactions export."""
    rows = _read_rows(filename, content, "the transactions export")
    if len(rows) > MAX_ROWS:
        raise ImportFileError(f"That file has more than {MAX_ROWS:,} rows. Is it the right one?")
    header_idx, index = None, {}
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        names = {_cell(c).lower(): j for j, c in enumerate(row) if _cell(c)}
        if all(h in names for h in REQUIRED_HEADERS):
            header_idx, index = i, names
            break
    if header_idx is None:
        raise ImportFileError("Couldn't find the header row. Expected columns: Invoice, Username, Plan, Type, "
                              "Price, Method, Recharged, Expiration.")

    def get(row, header):
        j = index.get(header)
        return _cell(row[j]) if j is not None and j < len(row) else ""

    parsed, unreadable = [], []
    for n, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if not any(_cell(c) for c in row):
            continue
        kind = get(row, "type").lower()
        amount, paid_at, expires_at = _amount(get(row, "price")), _when(get(row, "recharged")), _when(get(row, "expiration"))
        if kind not in SEGMENT_OF or amount is None or paid_at is None or expires_at is None \
                or not get(row, "username") or not get(row, "invoice"):
            unreadable.append(n)
            continue
        segment = SEGMENT_OF[kind]
        parsed.append({
            "row_no": int(get(row, "#")) if get(row, "#").isdigit() else None,
            "invoice": get(row, "invoice"),
            "username": get(row, "username"),
            "account": account_of(get(row, "username"), segment),
            "plan": get(row, "plan") or "unknown",
            "kind": kind,
            "segment": segment,
            "router": get(row, "router") or None,
            "amount": amount,
            "method": get(row, "method") or None,
            "paid_at": paid_at,
            "expires_at": expires_at,
        })
    # The export is newest first; within a minute its own row number keeps
    # the order the system wrote them (a higher # is older).
    parsed.sort(key=lambda r: (r["paid_at"], -(r["row_no"] or 0)))
    return {"rows": parsed, "unreadable": unreadable}


def _staff_made(method):
    return (method or "").split(" - ")[0] in ("Recharge", "Activation")


def _from_balance(method):
    m = (method or "").lower()
    return m.startswith("customer - balance") or m.startswith("mpesa paybill manual - ")


def _paybill(row):
    return row["kind"] == "balance" and (row["method"] or "").lower() == "mpesa paybill manual"


def classify(rows, new_system=()):
    """Sets row['left_out'] (None when it counts) on rows oldest first.
    new_system: (username, recharged_at naive UTC) of the new billing
    system's recharges, for the 'moved' rule."""
    moved_after = {}
    for username, at in new_system:
        moved_after.setdefault(username.upper(), []).append(at)
    invoices, periods, paybill = set(), set(), {}
    move_at = datetime.combine(MOVE_DAY, datetime.min.time()) - EAT_OFFSET
    for row in rows:
        acct = row["account"].upper()
        reason = None
        if row["amount"] < 0 or (row["kind"] == "balance" and row["plan"].lower() in CORRECTION_PLANS):
            reason = "correction"
        elif row["amount"] <= 1 or (row["method"] or "").lower() == "admin bypass":
            reason = "free"
        elif row["segment"] == "hotspot":
            reason = "repeat" if row["invoice"] in invoices else None
            invoices.add(row["invoice"])
        elif row["kind"] == "pppoe" and (acct, row["plan"], row["expires_at"]) in periods:
            reason = "repeat"
        elif row["kind"] == "pppoe" and _from_balance(row["method"]):
            reason = "balance"
        elif row["kind"] == "pppoe" and _staff_made(row["method"]) and any(
                timedelta(0) <= row["paid_at"] - at <= PAYBILL_APPLIED for at in paybill.get(acct, ())):
            reason = "balance"
        elif row["paid_at"] >= move_at and any(
                -MOVED_MATCH[0] <= at - row["paid_at"] <= MOVED_MATCH[1] for at in moved_after.get(acct, ())):
            reason = "moved"
        if row["kind"] == "pppoe" and reason in (None, "balance"):
            periods.add((acct, row["plan"], row["expires_at"]))
        if reason is None and _paybill(row):
            paybill.setdefault(acct, []).append(row["paid_at"])
        row["left_out"] = reason
    return rows


def how_paid(method):
    """How the transaction was paid, for the transactions table."""
    m = method or ""
    if m.startswith("BankStkPush"):
        return "M-Pesa (STK push)"
    if m.startswith("TV Binding"):
        return "M-Pesa (TV binding)"
    if m == "Vouchers":
        return "Voucher"
    if m.lower().startswith("mpesa paybill manual"):
        return "M-Pesa paybill"
    staff = re.fullmatch(r"(Recharge|Activation) - (.+)", m)
    if staff:
        return f"{'Recharged' if staff.group(1) == 'Recharge' else 'Activated'} by {staff.group(2)}"
    return m or "Unknown"


def row_key(row):
    """One transaction, however often the export is loaded."""
    return (row["invoice"], row["username"], row["plan"], row["paid_at"], row["expires_at"])


def _month(row):
    return (row["paid_at"] + EAT_OFFSET).strftime("%Y-%m")


def _example(row):
    at = row["paid_at"] + EAT_OFFSET
    return f"{row['username']}, {row['plan']}, KSh {row['amount']:,.0f}, {how_paid(row['method'])}, {at:%-d %b %Y %H:%M}"


def preview(rows, saved_keys):
    """What saving would do: new rows, what they add per month and segment,
    and what is left out and why. Rows must already be classified."""
    new = [r for r in rows if row_key(r) not in saved_keys]
    months = {}
    for r in new:
        if r["left_out"] is None:
            entry = months.setdefault((_month(r), r["segment"]),
                                      {"month": _month(r), "segment": r["segment"], "count": 0, "total": Decimal("0")})
            entry["count"] += 1
            entry["total"] += r["amount"]
    left_out = []
    for reason, label in LEFT_OUT.items():
        for segment in ("hotspot", "pppoe"):
            hit = [r for r in new if r["left_out"] == reason and r["segment"] == segment]
            if hit:
                left_out.append({"reason": reason, "label": label, "segment": segment, "count": len(hit),
                                 "total": sum((r["amount"] for r in hit), Decimal("0")),
                                 "examples": [_example(r) for r in hit[:8]]})
    counted = [r for r in new if r["left_out"] is None]
    return {
        "in_file": len(rows), "already_saved": len(rows) - len(new), "new": len(new),
        "counted": len(counted), "counted_total": sum((r["amount"] for r in counted), Decimal("0")),
        "months": sorted(months.values(), key=lambda m: (m["month"], m["segment"])),
        "left_out": left_out,
        "first": rows[0]["paid_at"] if rows else None,
        "latest": rows[-1]["paid_at"] if rows else None,
    }


def daily_raw_totals(rows):
    """{(EAT day, kind): (count, total)} over every row, left out or not:
    the same sums as the summary file 2_daily_revenue.csv, so the import can
    be checked against it."""
    out = {}
    for r in rows:
        key = ((r["paid_at"] + EAT_OFFSET).date(), r["kind"])
        c, t = out.get(key, (0, Decimal("0")))
        out[key] = (c + 1, t + r["amount"])
    return out


def router_from(router_start, history_last_day):
    """The first day hotspot income comes from the router rather than this
    history. The router's first day is partial (it started mid-day), so
    when the history covers that day the router takes over the day after."""
    if router_start is None:
        return None
    if history_last_day is not None and history_last_day >= router_start:
        return router_start + timedelta(days=1)
    return router_start
