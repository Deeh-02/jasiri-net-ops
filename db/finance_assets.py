"""Finance > Assets (Phase 7F) and the typed month targets (7E). Tables from
migration 0038. Inventory stays the equipment register: its items, costs,
statuses, locations and movement log are read through db/inventory_items.py
and db/inventory_transactions.py, never queried or changed here. Finance only
keeps the money facts beside them. Every change is written to
finance_audit_log. No book value or depreciation (owner's call, 2026-11-16;
see db/finance_assets_logic.py).
"""
from decimal import Decimal

from psycopg2 import errors

from db import finance_assets_logic as logic
from db import inventory_items, inventory_transactions
from db.connection import db_cursor
from db.finance_expenses import FinanceNotSetUp, _audit

ASSET_TRACKING_TYPE = "asset_serialized"
# The money facts Finance keeps per item. asset_values also has life_months
# and salvage columns, no longer read or written (no depreciation).
VALUE_FIELDS = ("purchased_on", "warranty_until", "segment", "expense_id")


def _finance_rows(with_capital_expenses=False):
    """({item_id: value row}, [unlinked capital expenses] or None) over one
    connection."""
    try:
        with db_cursor() as (conn, cur):
            cur.execute(f"SELECT item_id, {', '.join(VALUE_FIELDS)} FROM asset_values")
            values = {r[0]: dict(zip(VALUE_FIELDS, r[1:])) for r in cur.fetchall()}
            capital = _unlinked_capital_expenses(cur) if with_capital_expenses else None
    except errors.UndefinedTable:
        raise FinanceNotSetUp()
    return values, capital


def _inventory_assets():
    return [i for i in inventory_items.get_all_items() if i["tracking_type"] == ASSET_TRACKING_TYPE]


def report(today):
    """Every serialised Inventory item with its Finance facts, totals per
    category, what needs attention, and the unlinked capital expenses (for
    the item dialog's "Paid by" picker)."""
    values, capital = _finance_rows(with_capital_expenses=True)
    described = [logic.describe(i, values.get(i["id"]), today) for i in _inventory_assets()]
    categories = {}
    for a in described:
        c = categories.setdefault(a["category_id"], {"id": a["category_id"], "name": a["category"], "items": 0,
                                                     "dated": 0, "cost": Decimal("0")})
        c["items"] += 1
        c["dated"] += 1 if a["purchased_on"] else 0
        c["cost"] += a["cost"] or Decimal("0")
    return {
        "assets": described,
        "summary": logic.summary(described),
        "alerts": logic.alerts(described),
        "categories": sorted(categories.values(), key=lambda c: c["name"]),
        "capital_expenses": capital,
    }


def _unlinked_capital_expenses(cur):
    """Capital expenses (equipment bought to keep) not yet linked to an
    asset, newest first, for the asset dialog's "Paid by" picker."""
    cur.execute(
        """
        SELECT e.id, e.spent_on, e.amount, e.payee, e.description, e.reference
        FROM expenses e
        WHERE e.kind = 'capital' AND e.voided_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM asset_values v WHERE v.expense_id = e.id)
        ORDER BY e.spent_on DESC, e.id DESC LIMIT 100
        """
    )
    return [{"id": r[0], "spent_on": r[1], "amount": r[2], "payee": r[3], "description": r[4], "reference": r[5]}
            for r in cur.fetchall()]


def item_history(item_id, limit=20):
    """The item's movements, from Inventory's own log."""
    return inventory_transactions.get_transaction_log(item_id=item_id, limit=limit)


def is_asset(item_id):
    item = inventory_items.get_item_by_id(item_id)
    return bool(item and item["tracking_type"] == ASSET_TRACKING_TYPE)


def set_values(item_ids, changes, user_id):
    """Writes the given money facts for each item (only the keys passed),
    creating its row if needed, and logs before/after per item."""
    fields = {k: v for k, v in changes.items() if k in VALUE_FIELDS}
    if not fields or not item_ids:
        return 0
    columns = ", ".join(fields)
    with db_cursor() as (conn, cur):
        for item_id in item_ids:
            cur.execute(f"SELECT {columns} FROM asset_values WHERE item_id = %s FOR UPDATE", (item_id,))
            current = cur.fetchone()
            before = dict(zip(fields, current)) if current else None
            if before == fields:
                continue
            cur.execute(
                f"""
                INSERT INTO asset_values (item_id, {columns}, updated_by, updated_at)
                VALUES (%s, {', '.join(['%s'] * len(fields))}, %s, now())
                ON CONFLICT (item_id) DO UPDATE SET {', '.join(f'{k} = EXCLUDED.{k}' for k in fields)},
                    updated_by = EXCLUDED.updated_by, updated_at = now()
                """,
                (item_id, *fields.values(), user_id),
            )
            _audit(cur, user_id, "asset", item_id, "update" if before else "create", before=before, after=fields)
        conn.commit()
    return len(item_ids)


def category_items_without_date(category_id):
    """Ids of the category's live assets with no purchase date yet: what
    "set a date for the whole category" fills in (dates already set stay)."""
    values, _ = _finance_rows()
    return [i["id"] for i in _inventory_assets()
            if i["category_id"] == category_id and not (values.get(i["id"]) or {}).get("purchased_on")]


def is_unlinked_capital_expense(expense_id, item_id):
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT 1 FROM expenses e WHERE e.id = %s AND e.kind = 'capital' AND e.voided_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM asset_values v WHERE v.expense_id = e.id AND v.item_id <> %s)
            """,
            (expense_id, item_id),
        )
        return cur.fetchone() is not None


# ---- Month targets (7E) ----

def get_target(month_first_day):
    try:
        with db_cursor() as (conn, cur):
            cur.execute("SELECT amount FROM finance_month_targets WHERE month = %s", (month_first_day,))
            row = cur.fetchone()
    except errors.UndefinedTable:
        return None
    return row[0] if row else None


def set_target(month_first_day, amount, user_id):
    """amount None clears the month's target (the bar falls back to
    break-even). Logged either way."""
    with db_cursor() as (conn, cur):
        cur.execute("SELECT amount FROM finance_month_targets WHERE month = %s FOR UPDATE", (month_first_day,))
        current = cur.fetchone()
        if amount is None:
            cur.execute("DELETE FROM finance_month_targets WHERE month = %s", (month_first_day,))
        else:
            cur.execute(
                """
                INSERT INTO finance_month_targets (month, amount, set_by, set_at) VALUES (%s, %s, %s, now())
                ON CONFLICT (month) DO UPDATE SET amount = EXCLUDED.amount, set_by = EXCLUDED.set_by, set_at = now()
                """,
                (month_first_day, amount, user_id),
            )
        _audit(cur, user_id, "month_target", month_first_day.isoformat(), "clear" if amount is None else "set",
               before={"amount": current[0]} if current else None, after={"amount": amount} if amount is not None else None)
        conn.commit()
