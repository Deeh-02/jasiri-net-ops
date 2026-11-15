"""Finance: expenses and the expense category list (Phase 7). Tables come from
migration 0037. The Finance domain is one router (routers/finance.py) over
several db/finance_*.py files split by concern, the same shape as Inventory.

Nothing here is ever deleted: a wrong expense is voided with a reason, and
every change is written to finance_audit_log.
"""
from psycopg2 import errors
from psycopg2.extras import Json

from db.connection import db_cursor, utc_iso


class FinanceNotSetUp(Exception):
    """The finance tables don't exist yet: migration 0037 hasn't been run on
    this database."""


def list_categories(include_retired=False):
    """The expense categories in the order the Performance statement shows
    them, each with the group it rolls up into."""
    try:
        with db_cursor() as (conn, cur):
            cur.execute(
                """
                SELECT id, name, grp, is_active FROM expense_categories
                WHERE is_active OR %s
                ORDER BY sort_order, name
                """,
                (include_retired,),
            )
            rows = cur.fetchall()
    except errors.UndefinedTable:
        raise FinanceNotSetUp()
    return [{"id": r[0], "name": r[1], "group": r[2], "is_active": r[3]} for r in rows]


# ---- Reading what the review screen needs ----

def existing_references(references):
    """Which of these codes are already saved (voided ones included: a voided
    expense is un-voided, not pasted again)."""
    wanted = [r for r in references if r]
    if not wanted:
        return set()
    with db_cursor() as (conn, cur):
        cur.execute("SELECT reference FROM expenses WHERE reference = ANY(%s)", (wanted,))
        return {r[0] for r in cur.fetchall()}


def load_rules():
    """Every supplier rule, keyed by payee_key."""
    with db_cursor() as (conn, cur):
        cur.execute("SELECT payee_key, category_id, kind FROM supplier_rules")
        return {r[0]: {"category_id": r[1], "kind": r[2]} for r in cur.fetchall()}


# ---- Writing: every change also goes to finance_audit_log ----

def _jsonable(row):
    """A row as plain JSON for the audit log (dates and money as text)."""
    return {k: (str(v) if v is not None and not isinstance(v, (str, int, bool)) else v) for k, v in row.items()}


def _audit(cur, user_id, entity, entity_id, action, before=None, after=None, reason=None):
    cur.execute(
        """
        INSERT INTO finance_audit_log (user_id, entity, entity_id, action, before, after, reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (user_id, entity, str(entity_id), action,
         Json(_jsonable(before)) if before else None, Json(_jsonable(after)) if after else None, reason),
    )


_INSERT_COLUMNS = ("reference", "spent_on", "amount", "payee", "category_id", "kind", "segment",
                   "description", "method", "source_message", "ai_suggested")


def save_expenses(rows, rules, user_id):
    """Inserts the rows and writes the rules, all in one transaction. A code
    that was saved by someone else since the review was read is skipped, not
    an error (the unique index decides). Returns how many were saved and
    which codes were skipped."""
    saved, skipped = 0, []
    with db_cursor() as (conn, cur):
        for row in rows:
            cur.execute(
                f"""
                INSERT INTO expenses ({", ".join(_INSERT_COLUMNS)}, created_by)
                VALUES ({", ".join(["%s"] * len(_INSERT_COLUMNS))}, %s)
                ON CONFLICT (reference) DO NOTHING
                RETURNING id
                """,
                tuple(row[c] for c in _INSERT_COLUMNS) + (user_id,),
            )
            inserted = cur.fetchone()
            if inserted is None:
                skipped.append(row["reference"])
                continue
            saved += 1
            _audit(cur, user_id, "expense", inserted[0], "create", after=row)
        for payee_key, rule in rules.items():
            cur.execute("SELECT category_id, kind FROM supplier_rules WHERE payee_key = %s", (payee_key,))
            previous = cur.fetchone()
            cur.execute(
                """
                INSERT INTO supplier_rules (payee_key, category_id, kind, created_by, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (payee_key) DO UPDATE
                    SET category_id = EXCLUDED.category_id, kind = EXCLUDED.kind, updated_at = now()
                """,
                (payee_key, rule["category_id"], rule["kind"], user_id),
            )
            before = {"category_id": previous[0], "kind": previous[1]} if previous else None
            if before != rule:
                _audit(cur, user_id, "supplier_rule", payee_key, "update" if previous else "create",
                       before=before, after=rule)
        conn.commit()
    return {"saved": saved, "skipped_references": skipped}


_EXPENSE_SELECT = """
    SELECT e.id, e.reference, e.spent_on, e.amount, e.payee, e.category_id, c.name, c.grp,
           e.kind, e.segment, e.description, e.method, e.source_message, e.ai_suggested,
           e.created_at, u.name, e.voided_at, e.void_reason
    FROM expenses e
    JOIN expense_categories c ON c.id = e.category_id
    LEFT JOIN users u ON u.id = e.created_by
"""


def _expense_dict(r):
    return {
        "id": r[0], "reference": r[1], "spent_on": r[2].isoformat(), "amount": str(r[3]),
        "payee": r[4], "category_id": r[5], "category": r[6], "group": r[7],
        "kind": r[8], "segment": r[9], "description": r[10], "method": r[11],
        "source_message": r[12], "ai_suggested": r[13], "created_at": utc_iso(r[14]),
        "created_by": r[15], "voided_at": utc_iso(r[16]), "void_reason": r[17],
    }


def list_expenses(first_day, last_day, include_voided=False):
    """Expenses spent between the two dates (both included), newest first."""
    try:
        with db_cursor() as (conn, cur):
            cur.execute(
                _EXPENSE_SELECT + """
                WHERE e.spent_on BETWEEN %s AND %s AND (e.voided_at IS NULL OR %s)
                ORDER BY e.spent_on DESC, e.id DESC
                """,
                (first_day, last_day, include_voided),
            )
            return [_expense_dict(r) for r in cur.fetchall()]
    except errors.UndefinedTable:
        raise FinanceNotSetUp()


def get_expense(expense_id):
    with db_cursor() as (conn, cur):
        cur.execute(_EXPENSE_SELECT + " WHERE e.id = %s", (expense_id,))
        row = cur.fetchone()
    return _expense_dict(row) if row else None


# What an edit may change. Amount, date and code never change: they were read
# from the message. A wrong one is voided and entered again.
EDITABLE_FIELDS = ("category_id", "kind", "segment", "description")


def update_expense(expense_id, changes, user_id):
    """Changes the classification of one expense and logs before/after.
    Returns False if it doesn't exist or is voided."""
    fields = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS}
    with db_cursor() as (conn, cur):
        cur.execute(
            f"SELECT {', '.join(EDITABLE_FIELDS)} FROM expenses WHERE id = %s AND voided_at IS NULL FOR UPDATE",
            (expense_id,),
        )
        current = cur.fetchone()
        if current is None:
            return False
        before = dict(zip(EDITABLE_FIELDS, current))
        if all(before[k] == v for k, v in fields.items()):
            return True
        assignments = ", ".join(f"{k} = %s" for k in fields)
        cur.execute(f"UPDATE expenses SET {assignments}, updated_at = now() WHERE id = %s",
                    tuple(fields.values()) + (expense_id,))
        _audit(cur, user_id, "expense", expense_id, "update",
               before={k: before[k] for k in fields}, after=fields)
        conn.commit()
    return True


def void_expense(expense_id, reason, user_id):
    """Voids one expense (it stays in the table, out of every total).
    Returns False if it doesn't exist or is already voided."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            UPDATE expenses SET voided_at = now(), voided_by = %s, void_reason = %s, updated_at = now()
            WHERE id = %s AND voided_at IS NULL
            RETURNING amount, reference
            """,
            (user_id, reason, expense_id),
        )
        row = cur.fetchone()
        if row is None:
            return False
        _audit(cur, user_id, "expense", expense_id, "void",
               before={"amount": row[0], "reference": row[1]}, reason=reason)
        conn.commit()
    return True
