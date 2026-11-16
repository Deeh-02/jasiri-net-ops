"""Old billing history (Phase 7): the billing system's transactions export,
saved in billing_history (migrations/0040_billing_history.sql). The rules
(what counts, what is left out and why) are in db/billing_history_logic.py;
tools/import_billing_history.py loads the file.

Finance reads it two ways:
  - hotspot: db/finance_hotspot.py asks the functions below for the days
    before the router started recording sales, and the router after.
  - PPPoE: db/finance_pppoe.py adds the old system's counted PPPoE rows to
    the new system's recharges (HISTORY_PPPOE_SQL).

Only rows with left_out IS NULL are income. Every function here works
without the tables (it reports no history), so Finance runs the same
before the migration is applied.
"""
from datetime import timedelta
from decimal import Decimal

from psycopg2.extras import execute_values

from db import billing_history_logic as logic
from db.connection import db_cursor, utc_iso

_EAT = "(b.paid_at + interval '3 hours')"
_HOTSPOT = "b.segment = 'hotspot' AND b.left_out IS NULL"

# The old system's counted PPPoE rows, in the columns finance_pppoe's income
# union uses: id, at, account, name, plan, amount, how, source. A paybill
# payment the old system couldn't link to an account has plan "unknown".
NO_PLAN = "Plan not recorded"
HISTORY_PPPOE_SQL = f"""
    SELECT 'b' || b.id, b.paid_at, b.account, NULL::text,
           CASE WHEN b.plan = 'unknown' THEN '{NO_PLAN}' ELSE b.plan END, b.amount, b.method, 'old'
    FROM billing_history b WHERE b.segment = 'pppoe' AND b.left_out IS NULL"""


def _range(first_day, last_day):
    return first_day, last_day + timedelta(days=1)


def available(cur):
    """True once migrations/0040 has been run on this database."""
    cur.execute("SELECT to_regclass('public.billing_history') IS NOT NULL")
    return cur.fetchone()[0]


def coverage(cur):
    """(first EAT day, last EAT day) the saved export covers, or (None, None).
    The last day is the export's final day, which may be partial."""
    if not available(cur):
        return None, None
    cur.execute("SELECT min(first_at), max(latest_at) FROM billing_history_imports")
    first, latest = cur.fetchone()
    eat = timedelta(hours=3)
    return ((first + eat).date() if first else None), ((latest + eat).date() if latest else None)


# ---- Import ----

def import_rows(file_name, rows, user_id, commit):
    """Classifies the export's rows (the 'moved' rule reads the new system's
    recharges) and compares them with what is saved. Dry run unless commit:
    the same call, so the preview is exactly what a save does."""
    with db_cursor() as (conn, cur):
        if not available(cur):
            raise RuntimeError("Run migrations/0040_billing_history.sql on this database first.")
        cur.execute("SELECT username, recharged_at FROM pppoe_recharges")
        logic.classify(rows, cur.fetchall())
        cur.execute("SELECT invoice, username, plan, paid_at, expires_at FROM billing_history")
        saved = set(cur.fetchall())
        result = logic.preview(rows, saved)
        if not commit:
            return dict(result, committed=False)
        cur.execute(
            """INSERT INTO billing_history_imports (file_name, rows_in_file, rows_added, first_at, latest_at, imported_by)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (file_name, result["in_file"], result["new"], result["first"], result["latest"], user_id))
        import_id = cur.fetchone()[0]
        execute_values(
            cur,
            """INSERT INTO billing_history (row_no, invoice, username, account, plan, kind, segment, router, amount,
                                            method, paid_at, expires_at, left_out, import_id)
               VALUES %s ON CONFLICT (invoice, username, plan, paid_at, expires_at) DO NOTHING""",
            [(r["row_no"], r["invoice"], r["username"], r["account"], r["plan"], r["kind"], r["segment"], r["router"],
              r["amount"], r["method"], r["paid_at"], r["expires_at"], r["left_out"], import_id)
             for r in rows if logic.row_key(r) not in saved],
            page_size=2000)
        conn.commit()
    return dict(result, committed=True)


# ---- Hotspot (same shapes as monitoring's hotspot functions) ----

def hotspot_part(cur, history_from, first_day, last_day, until):
    """The hotspot income parts for days before `until` (the router's first
    day; None = no limit): daily over history_from..last_day, hourly and
    packages over the month. Empty lists when there is no history."""
    if not available(cur):
        return {"daily": [], "hourly": [], "packages": []}
    end = last_day if until is None else min(last_day, until - timedelta(days=1))
    if end < history_from:
        return {"daily": [], "hourly": [], "packages": []}
    cur.execute(f"""SELECT {_EAT}::date, sum(b.amount), count(*) FROM billing_history b
                    WHERE {_HOTSPOT} AND b.paid_at >= %s - interval '3 hours' AND b.paid_at < %s - interval '3 hours'
                    GROUP BY 1 ORDER BY 1""", _range(history_from, end))
    daily = cur.fetchall()
    hourly, packages = [], []
    if end >= first_day:
        month = _range(first_day, end)
        cur.execute(f"""SELECT extract(hour FROM {_EAT})::int, sum(b.amount), count(*) FROM billing_history b
                        WHERE {_HOTSPOT} AND b.paid_at >= %s - interval '3 hours' AND b.paid_at < %s - interval '3 hours'
                        GROUP BY 1 ORDER BY 1""", month)
        hourly = cur.fetchall()
        cur.execute(f"""SELECT b.plan, sum(b.amount), count(*) FROM billing_history b
                        WHERE {_HOTSPOT} AND b.paid_at >= %s - interval '3 hours' AND b.paid_at < %s - interval '3 hours'
                        GROUP BY 1 ORDER BY 2 DESC, 1""", month)
        packages = cur.fetchall()
    return {"daily": daily, "hourly": hourly, "packages": packages}


def hotspot_buyers(cur, first_day, last_day, known_from):
    """{buyers, first_time, repeat} over the month from the history. first_time
    is None when the month starts on the history's first day (nobody's
    earlier purchases are on record)."""
    cur.execute(f"""WITH period AS (
                        SELECT b.account, count(*) AS purchases FROM billing_history b
                        WHERE {_HOTSPOT} AND b.paid_at >= %s - interval '3 hours' AND b.paid_at < %s - interval '3 hours'
                        GROUP BY 1
                    ), earlier AS (
                        SELECT DISTINCT b.account FROM billing_history b JOIN period p ON p.account = b.account
                        WHERE {_HOTSPOT} AND b.paid_at < %s - interval '3 hours'
                    )
                    SELECT count(*), count(*) FILTER (WHERE account NOT IN (SELECT account FROM earlier)),
                           count(*) FILTER (WHERE purchases > 1)
                    FROM period""", (*_range(first_day, last_day), first_day))
    buyers, first_time, repeat = cur.fetchone()
    if not buyers:
        return None
    return {"buyers": buyers, "first_time": None if first_day <= known_from else first_time, "repeat": repeat}


def hotspot_daily_totals(cur, first_day, last_day, until):
    """[(EAT day, total)] for days before `until`."""
    if not available(cur):
        return []
    end = last_day if until is None else min(last_day, until - timedelta(days=1))
    if end < first_day:
        return []
    cur.execute(f"""SELECT {_EAT}::date, sum(b.amount) FROM billing_history b
                    WHERE {_HOTSPOT} AND b.paid_at >= %s - interval '3 hours' AND b.paid_at < %s - interval '3 hours'
                    GROUP BY 1 ORDER BY 1""", _range(first_day, end))
    return cur.fetchall()


def hotspot_sales(cur, first_day, last_day, search=None, day=None, package=None, limit=50, offset=0):
    """One page of counted hotspot transactions, newest first, in the router
    sale shape (id, at, account, package, amount, site, kind, attribution).
    'site' holds how it was paid; 'kind' is 'sale' for an account's first
    purchase on record, else 'renewal'. limit None returns every match."""
    if not available(cur) or last_day < first_day:
        return {"rows": [], "matching": 0, "matching_total": Decimal("0")}
    where = [_HOTSPOT, "b.paid_at >= %s - interval '3 hours'", "b.paid_at < %s - interval '3 hours'"]
    args = list(_range(first_day, last_day))
    if day:
        where.append(f"{_EAT}::date = %s")
        args.append(day)
    if package:
        where.append("b.plan = %s")
        args.append(package)
    if search:
        where.append("(b.username ILIKE %s OR b.plan ILIKE %s OR b.method ILIKE %s)")
        args += [f"%{search}%"] * 3
    where_sql = " AND ".join(where)
    cur.execute(f"SELECT count(*), COALESCE(sum(b.amount), 0) FROM billing_history b WHERE {where_sql}", args)
    matching, matching_total = cur.fetchone()
    page_sql = "" if limit is None else " LIMIT %s OFFSET %s"
    cur.execute(
        f"""SELECT b.id, b.paid_at, b.username, b.plan, b.amount, b.method,
                   EXISTS (SELECT 1 FROM billing_history e WHERE e.account = b.account AND e.segment = 'hotspot'
                           AND e.left_out IS NULL AND e.paid_at < b.paid_at)
            FROM billing_history b WHERE {where_sql}
            ORDER BY b.paid_at DESC, b.row_no ASC NULLS LAST, b.id DESC{page_sql}""",
        args + ([] if limit is None else [limit, offset]))
    rows = [{"id": f"h{r[0]}", "at": utc_iso(r[1]), "sort_at": r[1], "account": r[2], "package": r[3], "amount": r[4],
             "site": logic.how_paid(r[5]), "kind": "renewal" if r[6] else "sale", "attribution": None}
            for r in cur.fetchall()]
    return {"rows": rows, "matching": matching, "matching_total": matching_total}
