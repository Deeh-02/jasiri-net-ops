"""Finance > Assets (Phase 7F) and the typed month targets (7E). Tables from
migration 0038. Inventory stays the equipment register: its items, costs,
statuses, locations and movement log are read through db/inventory_items.py
and db/inventory_transactions.py, never queried or changed here. Finance only
keeps the money facts beside them. Every change is written to
finance_audit_log.
"""
from decimal import Decimal

from psycopg2 import errors

from db import finance_assets_logic as logic
from db import inventory_items, inventory_transactions
from db.connection import db_cursor
from db.finance_expenses import FinanceNotSetUp, _audit

ASSET_TRACKING_TYPE = "asset_serialized"
VALUE_FIELDS = ("purchased_on", "life_months", "salvage", "warranty_until", "segment", "expense_id")


def _finance_rows(with_capital_expenses=False):
    """({item_id: value row}, {category_id: life row}, [unlinked capital
    expenses] or None) over one connection."""
    try:
        with db_cursor() as (conn, cur):
            cur.execute(f"SELECT item_id, {', '.join(VALUE_FIELDS)} FROM asset_values")
            values = {r[0]: dict(zip(VALUE_FIELDS, r[1:])) for r in cur.fetchall()}
            cur.execute("SELECT category_id, life_months, salvage_pct FROM asset_category_lives")
            lives = {r[0]: {"life_months": r[1], "salvage_pct": r[2]} for r in cur.fetchall()}
            capital = _unlinked_capital_expenses(cur) if with_capital_expenses else None
    except errors.UndefinedTable:
        raise FinanceNotSetUp()
    return values, lives, capital


def _inventory_assets():
    return [i for i in inventory_items.get_all_items() if i["tracking_type"] == ASSET_TRACKING_TYPE]


def assets(today, with_capital_expenses=False):
    """Every serialised Inventory item with its Finance figures, the
    category defaults, and (if asked) the unlinked capital expenses."""
    values, lives, capital = _finance_rows(with_capital_expenses)
    items = _inventory_assets()
    described = [logic.describe(i, values.get(i["id"]), lives.get(i["category_id"]), today) for i in items]
    return described, lives, capital


def report(today):
    described, lives, capital = assets(today, with_capital_expenses=True)
    categories = {}
    for a in described:
        c = categories.setdefault(a["category_id"], {"id": a["category_id"], "name": a["category"], "items": 0,
                                                     "dated": 0, "cost": Decimal("0"), "life": lives.get(a["category_id"])})
        c["items"] += 1
        c["dated"] += 0 if "purchase date" in a["terms"]["missing"] else 1
        c["cost"] += a["terms"]["cost"] or Decimal("0")
    return {
        "assets": described,
        "summary": logic.summary(described, today),
        "alerts": logic.alerts(described),
        "categories": sorted(categories.values(), key=lambda c: c["name"]),
        "capital_expenses": capital,
    }


def months_depreciation(month_first_days, today):
    """For Performance: {month: depreciation result} for each month asked,
    loading the assets once. A month's result is None when no asset can be
    depreciated yet (no purchase dates), so the statement says "no data",
    not 0."""
    try:
        described, _, _ = assets(today)
    except FinanceNotSetUp:
        return {m: None for m in month_first_days}
    results = {}
    for month in month_first_days:
        result = logic.month_depreciation(described, month)
        results[month] = None if result["counted"] == 0 else result
    return results


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


def set_category_life(category_id, life_months, salvage_pct, user_id):
    with db_cursor() as (conn, cur):
        cur.execute("SELECT life_months, salvage_pct FROM asset_category_lives WHERE category_id = %s FOR UPDATE",
                    (category_id,))
        current = cur.fetchone()
        cur.execute(
            """
            INSERT INTO asset_category_lives (category_id, life_months, salvage_pct, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (category_id) DO UPDATE SET life_months = EXCLUDED.life_months,
                salvage_pct = EXCLUDED.salvage_pct, updated_by = EXCLUDED.updated_by, updated_at = now()
            """,
            (category_id, life_months, salvage_pct, user_id),
        )
        after = {"life_months": life_months, "salvage_pct": salvage_pct}
        before = {"life_months": current[0], "salvage_pct": current[1]} if current else None
        if before != after:
            _audit(cur, user_id, "asset_category_life", category_id, "update" if current else "create",
                   before=before, after=after)
        conn.commit()


def category_items_without_date(category_id):
    """Ids of the category's live assets with no purchase date yet: what
    "set a date for the whole category" fills in (dates already set stay)."""
    values, _, _ = _finance_rows()
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
