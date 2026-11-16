"""PPPoE income (Phase 7): the billing system's recharges, uploaded on
Finance > Income > PPPoE. Owns pppoe_recharges and pppoe_recharge_imports
(migrations/0039_pppoe_recharges.sql). The rules (what counts, what is left
out and why) are in db/pppoe_recharge_logic.py.

Only rows with left_out IS NULL are income. Every query here returns the
same shapes as db/monitoring.py's hotspot ones, so Finance > Income treats
both segments alike.
"""
from datetime import timedelta
from decimal import Decimal

import psycopg2

from db import pppoe_recharge_logic as logic
from db.connection import db_cursor, to_eat, utc_iso

_EAT = "(r.recharged_at + interval '3 hours')"
_COUNTS = "r.left_out IS NULL"


class PppoeNotSetUp(RuntimeError):
    """migrations/0039_pppoe_recharges.sql hasn't been run on this database."""


def _range(first_day, last_day):
    return first_day, last_day + timedelta(days=1)


def _guard(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except psycopg2.errors.UndefinedTable:
            raise PppoeNotSetUp()
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


# ---- Upload ----

@_guard
def import_recharges(file_name, rows, user_id, commit):
    """Classifies the export's rows and compares them with what is saved.
    Dry run unless commit: the same call, so the preview is exactly what a
    save does. Saving adds only recharges not saved before."""
    logic.classify(rows)
    with db_cursor() as (conn, cur):
        cur.execute("SELECT username, recharged_at, plan, amount FROM pppoe_recharges")
        saved = {(u, at, p, Decimal(a)) for u, at, p, a in cur.fetchall()}
        result = logic.preview(rows, saved)
        latest = result["latest"]
        result.update(first=utc_iso(result["first"]), latest=utc_iso(latest))
        if not commit:
            return dict(result, committed=False)
        # Saved even when nothing is new: the upload itself says the file
        # was checked up to now (how far PPPoE income is recorded).
        cur.execute(
            """INSERT INTO pppoe_recharge_imports (file_name, rows_in_file, rows_added, latest_recharge, imported_by)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (file_name, result["in_file"], result["new"], latest, user_id),
        )
        import_id = cur.fetchone()[0]
        for r in rows:
            if logic.recharge_key(r) in saved:
                continue
            cur.execute(
                """INSERT INTO pppoe_recharges (username, customer_name, plan, base_plan, amount, recharged_at,
                                                billing_status, quantity, created_by, left_out, import_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (username, recharged_at, plan, amount) DO NOTHING""",
                (r["username"], r["customer_name"], r["plan"], r["base_plan"], r["amount"], r["recharged_at"],
                 r["billing_status"], r["quantity"], r["created_by"], r["left_out"], import_id),
            )
        # A later export can show an older recharge's status changed
        # (Inactive -> Active once the period starts): keep that current.
        for r in rows:
            if logic.recharge_key(r) in saved:
                cur.execute(
                    """UPDATE pppoe_recharges SET billing_status = %s, quantity = %s
                       WHERE username = %s AND recharged_at = %s AND plan = %s AND amount = %s
                         AND (billing_status IS DISTINCT FROM %s OR quantity IS DISTINCT FROM %s)""",
                    (r["billing_status"], r["quantity"], *logic.recharge_key(r), r["billing_status"], r["quantity"]),
                )
        conn.commit()
    return dict(result, committed=True)


def coverage(cur):
    """(first EAT day with a recharge, last upload time, latest recharge),
    all None when nothing is uploaded yet."""
    cur.execute(f"SELECT min({_EAT})::date FROM pppoe_recharges r")
    first = cur.fetchone()[0]
    cur.execute("SELECT imported_at, latest_recharge FROM pppoe_recharge_imports ORDER BY imported_at DESC LIMIT 1")
    last = cur.fetchone()
    return first, (last[0] if last else None), (last[1] if last else None)


@_guard
def status():
    """For the upload box: what is recorded so far."""
    with db_cursor() as (conn, cur):
        first, imported_at, latest = coverage(cur)
        cur.execute(f"SELECT count(*), COALESCE(sum(r.amount), 0) FROM pppoe_recharges r WHERE {_COUNTS}")
        count, total = cur.fetchone()
    return {"first_day": first, "last_upload": utc_iso(imported_at), "latest_recharge": utc_iso(latest),
            "recharges": count, "total": total}


# ---- Income (same shapes as monitoring.hotspot_income_for_month) ----

@_guard
def income_for_month(history_from, first_day, last_day):
    """first_day (records start), daily, hourly, packages (plans), buyers,
    plus last_upload / latest_recharge (naive UTC), not_used (count, total)
    and held (count, total of repeats) for Needs attention."""
    month = _range(first_day, last_day)
    with db_cursor() as (conn, cur):
        tracking_start, imported_at, latest = coverage(cur)
        cur.execute(
            f"""SELECT {_EAT}::date, sum(r.amount), count(*) FROM pppoe_recharges r
                WHERE {_COUNTS} AND {_EAT} >= %s AND {_EAT} < %s GROUP BY 1 ORDER BY 1""",
            _range(history_from, last_day))
        daily = cur.fetchall()
        cur.execute(
            f"""SELECT extract(hour FROM {_EAT})::int, sum(r.amount), count(*) FROM pppoe_recharges r
                WHERE {_COUNTS} AND {_EAT} >= %s AND {_EAT} < %s GROUP BY 1 ORDER BY 1""", month)
        hourly = cur.fetchall()
        cur.execute(
            f"""SELECT r.plan, sum(r.amount), count(*) FROM pppoe_recharges r
                WHERE {_COUNTS} AND {_EAT} >= %s AND {_EAT} < %s GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 8""", month)
        packages = cur.fetchall()
        cur.execute(
            f"""WITH period AS (
                    SELECT r.username, count(*) AS recharges FROM pppoe_recharges r
                    WHERE {_COUNTS} AND {_EAT} >= %s AND {_EAT} < %s GROUP BY 1
                ), earlier AS (
                    SELECT DISTINCT r.username FROM pppoe_recharges r JOIN period p ON p.username = r.username
                    WHERE r.left_out IS DISTINCT FROM 'test' AND {_EAT} < %s
                )
                SELECT count(*), count(*) FILTER (WHERE username NOT IN (SELECT username FROM earlier)),
                       count(*) FILTER (WHERE recharges > 1)
                FROM period""", (*month, first_day))
        buyers, first_time, repeat = cur.fetchone()
        cur.execute(
            f"""SELECT count(*), COALESCE(sum(r.amount), 0) FROM pppoe_recharges r
                WHERE {_COUNTS} AND lower(r.billing_status) = 'inactive' AND r.quantity LIKE '1/%%'
                  AND {_EAT} >= %s AND {_EAT} < %s""", month)
        not_used = cur.fetchone()
        cur.execute(
            f"""SELECT count(*), COALESCE(sum(r.amount), 0) FROM pppoe_recharges r
                WHERE r.left_out = 'repeat' AND {_EAT} >= %s AND {_EAT} < %s""", month)
        held = cur.fetchone()
    return {"first_day": tracking_start, "daily": daily, "hourly": hourly, "packages": packages,
            "buyers": {"buyers": buyers, "first_time": first_time, "repeat": repeat} if buyers else None,
            "last_upload": imported_at, "latest_recharge": latest, "not_used": not_used, "held": held}


@_guard
def daily_totals(first_day, last_day):
    """Finance > Performance: ([(EAT day, total)], records start)."""
    with db_cursor() as (conn, cur):
        cur.execute(
            f"""SELECT {_EAT}::date, sum(r.amount) FROM pppoe_recharges r
                WHERE {_COUNTS} AND {_EAT} >= %s AND {_EAT} < %s GROUP BY 1 ORDER BY 1""",
            _range(first_day, last_day))
        daily = cur.fetchall()
        first, _, _ = coverage(cur)
    return daily, first


@_guard
def recharges(first_day, last_day, search=None, day=None, package=None, limit=50, offset=0):
    """One page of counted recharges, newest first, in the hotspot sale shape
    (at, account, package, site, kind, amount) plus 'segment'. 'site' holds
    how it was paid; 'kind' is 'new' for an account's first recharge on
    record, else 'renewal'."""
    where = [_COUNTS, f"{_EAT} >= %s", f"{_EAT} < %s"]
    args = list(_range(first_day, last_day))
    if day:
        where.append(f"{_EAT}::date = %s")
        args.append(day)
    if package:
        where.append("r.plan = %s")
        args.append(package)
    if search:
        where.append("(r.username ILIKE %s OR r.customer_name ILIKE %s OR r.plan ILIKE %s OR r.created_by ILIKE %s)")
        args += [f"%{search}%"] * 4
    where_sql = " AND ".join(where)
    with db_cursor() as (conn, cur):
        cur.execute(f"SELECT count(*), COALESCE(sum(r.amount), 0) FROM pppoe_recharges r WHERE {where_sql}", args)
        matching, matching_total = cur.fetchone()
        page_sql = "" if limit is None else " LIMIT %s OFFSET %s"
        cur.execute(
            f"""SELECT r.id, r.recharged_at, r.username, r.customer_name, r.plan, r.amount, r.created_by,
                       NOT EXISTS (SELECT 1 FROM pppoe_recharges e WHERE e.username = r.username
                                   AND e.left_out IS DISTINCT FROM 'test' AND e.recharged_at < r.recharged_at)
                FROM pppoe_recharges r WHERE {where_sql}
                ORDER BY r.recharged_at DESC, r.id DESC{page_sql}""",
            args + ([] if limit is None else [limit, offset]))
        rows = [{"id": f"p{r[0]}", "at": utc_iso(r[1]), "sort_at": r[1],
                 "account": f"{r[2]} · {r[3]}" if r[3] else r[2], "package": r[4], "amount": r[5],
                 "site": logic.how_paid(r[6]), "kind": "new" if r[7] else "renewal", "segment": "pppoe"}
                for r in cur.fetchall()]
    return {"rows": rows, "matching": matching, "matching_total": matching_total}


def eat(dt):
    """Naive UTC -> naive EAT, for the attention rules."""
    return to_eat(dt).replace(tzinfo=None) if dt else None
