"""PPPoE income rules: reads the billing system's recharge export and decides
which recharges count as income. Pure (no database, no clock), tested in
tests/test_pppoe_recharges.py; db/finance_pppoe.py does the saving and the
queries.

The export (FreeISPRadius "recharges", owner's chosen source 2026-11-16)
has a title row above the headers, like the customer export, so the header
row is found rather than assumed. Columns used: User, Name, Plan, Base Plan,
Quantity, Amount ("KSh1500.00"), Status, Recharge Date (dd/mm/yyyy HH:MM:SS,
Nairobi time), Created By ("NCBAServer" when the bank paybill recharged the
account by itself, otherwise the staff member who did it).

A recharge is income unless a rule leaves it out (kept, never deleted, so
the call can be seen and reversed):
  - test:   a test or staff account (TEST_ACCOUNTS)
  - free:   KSh 0
  - moved:  entered by staff on MOVE_DAY, the day PPPoE moved here from the
            old billing system: the customer's month already paid there
  - repeat: the same account, plan and amount again within REPEAT_MINUTES of
            one that counts: a double click, not a second payment

The move (owner, 2026-11-16: "they had been moved"): on 20 Jun 2026 staff
entered every PPPoE customer here (JS02 at 16:34, then 18 accounts within
seconds at 17:46). Every one of them with a payment in the old system's
export still had a paid month running there that day, and 7 of the others
never recharged again: these were existing subscriptions copied across, not
new money. The old system's payments are counted from its own transactions
export (db/billing_history_logic.py), so these entries are left out. An
earlier reading counted them (2026-11-16, before the old system's history
was in).

Plans: "Bronze" was renamed "Standard" (owner, 2026-11-16), so it is shown
and grouped as Standard. "Jasiri" (KSh 1) and "Test" (KSh 0) are staff plans.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from db.customer_import import ImportFileError, _cell, _read_rows

REQUIRED_HEADERS = ("user", "plan", "amount", "recharge date", "created by")
HEADER_SEARCH_ROWS = 10
MAX_ROWS = 50000
EAT_OFFSET = timedelta(hours=3)

TEST_ACCOUNTS = {"test", "jasiri"}
TEST_PLANS = {"test", "jasiri"}
REPEAT_MINUTES = 10
BANK_CREATOR = "ncbaserver"
MOVE_DAY = date(2026, 6, 20)
PLAN_RENAMED = {"Bronze": "Standard"}

LEFT_OUT = {
    "test": "Test or staff account",
    "free": "KSh 0",
    "moved": "Moved from the old billing system (paid there)",
    "repeat": "Same recharge again within minutes",
}


def plan_name(plan):
    """The plan as it is called now."""
    return PLAN_RENAMED.get(plan, plan)


def plan_sql(column):
    """plan_name() as SQL, for grouping and filtering in queries."""
    whens = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in PLAN_RENAMED.items())
    return f"CASE {column} {whens} ELSE {column} END"


def _amount(text):
    """'KSh1500.00' / 'KSh 1,500' / '1500' -> Decimal, None if unreadable."""
    cleaned = text.lower().replace("ksh", "").replace("kes", "").replace(",", "").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value >= 0 else None


def _when(text):
    """Nairobi wall-clock text -> naive UTC, None if unreadable."""
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt) - EAT_OFFSET
        except ValueError:
            continue
    return None


def parse_recharge_file(filename, content):
    """{'rows': [...], 'unreadable': [line numbers]} with rows in time order.
    Raises ImportFileError for a file that isn't the recharge export."""
    rows = _read_rows(filename, content, "the recharge export")
    if len(rows) > MAX_ROWS:
        raise ImportFileError(f"That file has more than {MAX_ROWS} rows. Is it the right one?")
    header_idx, index = None, {}
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        names = {_cell(c).lower(): j for j, c in enumerate(row) if _cell(c)}
        if all(h in names for h in REQUIRED_HEADERS):
            header_idx, index = i, names
            break
    if header_idx is None:
        if any(_cell(c).lower() == "phone" for r in rows[:HEADER_SEARCH_ROWS] for c in r):
            raise ImportFileError("That looks like the customer export. Upload it on the Customers page; "
                                  "this needs the recharge export (it has Amount and Recharge Date columns).")
        raise ImportFileError("Couldn't find the header row. Expected columns: User, Plan, Amount, "
                              "Recharge Date, Created By.")

    def get(row, header):
        j = index.get(header)
        return _cell(row[j]) if j is not None and j < len(row) else ""

    parsed, unreadable = [], []
    for n, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        username = get(row, "user")
        if not username:
            continue
        amount, at = _amount(get(row, "amount")), _when(get(row, "recharge date"))
        if amount is None or at is None or not get(row, "plan"):
            unreadable.append(n)
            continue
        parsed.append({
            "username": username,
            "customer_name": get(row, "name") or None,
            "plan": get(row, "plan"),
            "base_plan": get(row, "base plan") or None,
            "amount": amount,
            "recharged_at": at,
            "billing_status": get(row, "status") or None,
            "quantity": get(row, "quantity") or None,
            "created_by": get(row, "created by") or None,
        })
    parsed.sort(key=lambda r: (r["recharged_at"], r["username"]))
    return {"rows": parsed, "unreadable": unreadable}


def classify(rows):
    """Sets row['left_out'] (None when it counts) on rows in time order."""
    last_counted = {}
    for row in rows:
        key = (row["username"].lower(), plan_name(row["plan"]), row["amount"])
        if row["username"].lower() in TEST_ACCOUNTS or row["plan"].lower() in TEST_PLANS:
            row["left_out"] = "test"
        elif row["amount"] == 0:
            row["left_out"] = "free"
        elif (row["recharged_at"] + EAT_OFFSET).date() == MOVE_DAY and not paid_by_bank(row["created_by"]):
            row["left_out"] = "moved"
        elif key in last_counted and row["recharged_at"] - last_counted[key] <= timedelta(minutes=REPEAT_MINUTES):
            row["left_out"] = "repeat"
        else:
            row["left_out"] = None
            last_counted[key] = row["recharged_at"]
    return rows


def paid_by_bank(created_by):
    return (created_by or "").strip().lower() == BANK_CREATOR


def how_paid(created_by):
    """How the recharge was made, for the transactions table."""
    if paid_by_bank(created_by):
        return "NCBA paybill (automatic)"
    return f"Recharged by {created_by}" if created_by else "Recharged by staff"


def not_used_yet(row):
    """A recharge the account hasn't started using: 'Inactive' with its one
    period still unused ('1/1'). Money in, service still to come."""
    return (row.get("billing_status") or "").lower() == "inactive" and (row.get("quantity") or "").startswith("1/")


def preview(rows, saved_keys):
    """What saving would do: which rows are new (not already saved, matched
    on recharge_key), what they add per month, and what is left out and
    why. Rows must already be classified."""
    new = [r for r in rows if recharge_key(r) not in saved_keys]
    counted = [r for r in new if r["left_out"] is None]
    months = {}
    for r in counted:
        month = (r["recharged_at"] + EAT_OFFSET).strftime("%Y-%m")
        entry = months.setdefault(month, {"month": month, "recharges": 0, "total": Decimal("0")})
        entry["recharges"] += 1
        entry["total"] += r["amount"]
    left_out = []
    for reason, label in LEFT_OUT.items():
        hit = [r for r in new if r["left_out"] == reason]
        if hit:
            left_out.append({"reason": reason, "label": label, "recharges": len(hit),
                             "total": sum((r["amount"] for r in hit), Decimal("0")),
                             "examples": [_example(r) for r in hit[:6]]})
    return {
        "in_file": len(rows),
        "already_saved": len(rows) - len(new),
        "new": len(new),
        "counted": len(counted),
        "counted_total": sum((r["amount"] for r in counted), Decimal("0")),
        "by_bank": sum(1 for r in counted if paid_by_bank(r["created_by"])),
        "months": sorted(months.values(), key=lambda m: m["month"]),
        "left_out": left_out,
        "first": rows[0]["recharged_at"] if rows else None,
        "latest": rows[-1]["recharged_at"] if rows else None,
    }


def _example(row):
    at = row["recharged_at"] + EAT_OFFSET
    return f"{row['username']} {row['customer_name'] or ''}, {row['plan']}, KSh {row['amount']:,.0f}, {at:%-d %b %Y %H:%M}".replace("  ", " ")


def recharge_key(row):
    return (row["username"], row["recharged_at"], row["plan"], Decimal(row["amount"]))


def attention(not_used, held, last_import_eat, latest_recharge_eat, now_eat, stale_days=2):
    """PPPoE items for Needs attention: an upload that is getting old, held
    repeats waiting for a look, and paid recharges not used yet."""
    items = []
    if last_import_eat and now_eat - last_import_eat >= timedelta(days=stale_days):
        days = (now_eat - last_import_eat).days
        items.append({"level": "warn", "title": f"PPPoE recharges were last uploaded {days} days ago",
                      "detail": f"Recorded up to {latest_recharge_eat:%-d %b %H:%M}. Recharges since then aren't counted: "
                                "upload a fresh recharge export on the PPPoE view."})
    count, total = held
    if count:
        items.append({"level": "info", "title": f"{count} PPPoE recharge{'s' if count != 1 else ''} left out as repeats",
                      "detail": f"KSh {total:,.0f}: the same account, plan and amount again within {REPEAT_MINUTES} minutes, "
                                "so taken as a double entry. Tell the developer if any was a real second payment."})
    count, total = not_used
    if count:
        items.append({"level": "info", "title": f"{count} paid PPPoE recharge{'s' if count != 1 else ''} not used yet",
                      "detail": f"KSh {total:,.0f} counted as income; the account hasn't started that period yet "
                                "(Inactive on the billing system)."})
    return items
