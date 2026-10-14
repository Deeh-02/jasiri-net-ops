"""Revenue reporting, data layer — builds daily_site_revenue and
hourly_site_revenue (migration 0019) from revenue_events.

Everything a revenue report reads comes from those two tables, never from
revenue_events directly, so a report for a past date is reproducible: a day
is computed, then frozen (see the migration header for why revenue_events
itself can't be relied on to hold still).

LIFECYCLE. Today and yesterday are open and recomputed every
REFRESH_EVERY_SECONDS. A day older than yesterday is computed one last time
and frozen; after that nothing touches it except refreeze(), which is
explicit, needs a reason, and is logged to daily_revenue_refreeze_log.

SCOPE. Hotspot sales only, attributed sales only — see the migration header.

CLI (run from the repo root):
    python -m db.revenue_daily backfill
    python -m db.revenue_daily refresh
    python -m db.revenue_daily refreeze 2026-10-01 2026-10-05 --reason "..."
"""
import argparse
import logging
import threading
import time
from datetime import date, timedelta

from psycopg2.extras import execute_values

from db.connection import db_cursor, now_eat

log = logging.getLogger(__name__)

SOURCE = "hotspot"
REFRESH_EVERY_SECONDS = 900  # 15 minutes

_lock = threading.Lock()
_last_run = 0.0

# EAT calendar day / hour of a sale. first_seen_at is naive UTC; +3h is exact
# because EAT has no DST.
_EAT_TS = "(re.first_seen_at + interval '3 hours')"


def eat_today():
    return now_eat().date()


def _hourly_rows(cur, from_date, to_date):
    """[(site_id, hour_start, revenue, txn_count)] for sales on EAT days
    from_date..to_date inclusive."""
    cur.execute(
        f"""
        SELECT re.monitored_site_id, date_trunc('hour', {_EAT_TS}) AS hour_start,
               sum(re.price_kes), count(*)
        FROM revenue_events re
        WHERE re.event_type <> 'baseline'
          AND re.monitored_site_id IS NOT NULL
          AND {_EAT_TS} >= %s AND {_EAT_TS} < %s
        GROUP BY 1, 2
        """,
        (from_date, to_date + timedelta(days=1)),
    )
    return cur.fetchall()


def _site_days(cur, from_date, to_date, sold):
    """Every (site_id, day) that should have a row: each site that was active
    on that day (created on or before it, still is_active) plus any site that
    sold on it, so a zero is a real zero."""
    cur.execute(
        "SELECT id, (created_at + interval '3 hours')::date FROM monitored_sites WHERE is_active"
    )
    active = cur.fetchall()
    pairs = set(sold)
    day = from_date
    while day <= to_date:
        for site_id, created in active:
            if created <= day:
                pairs.add((site_id, day))
        day += timedelta(days=1)
    return pairs


def _build(cur, from_date, to_date, freeze_before):
    """Recomputes both tables for from_date..to_date inclusive. Days strictly
    before freeze_before are written frozen. Rows already frozen are never
    overwritten here (refreeze() clears the flag first, deliberately).
    Returns the number of daily rows written."""
    hourly = _hourly_rows(cur, from_date, to_date)

    daily = {}  # (site_id, day) -> [revenue, txn_count]
    for site_id, hour_start, revenue, n in hourly:
        key = (site_id, hour_start.date())
        agg = daily.setdefault(key, [0, 0])
        agg[0] += revenue
        agg[1] += n

    pairs = _site_days(cur, from_date, to_date, daily.keys())

    cur.execute(
        "SELECT site_id, date FROM daily_site_revenue WHERE source = %s AND is_frozen AND date BETWEEN %s AND %s",
        (SOURCE, from_date, to_date),
    )
    frozen = set(cur.fetchall())

    hour_rows = [
        (site_id, hour_start, SOURCE, revenue, n)
        for site_id, hour_start, revenue, n in hourly
        # A frozen day's hours are frozen with it.
        if (site_id, hour_start.date()) not in frozen
    ]
    if hour_rows:
        execute_values(
            cur,
            """
            INSERT INTO hourly_site_revenue (site_id, hour_start, source, revenue, txn_count) VALUES %s
            ON CONFLICT (site_id, hour_start, source) DO UPDATE
                SET revenue = EXCLUDED.revenue, txn_count = EXCLUDED.txn_count, computed_at = now()
            """,
            hour_rows,
        )

    day_rows = [
        (site_id, day, SOURCE, *daily.get((site_id, day), (0, 0)), day < freeze_before)
        for site_id, day in sorted(pairs)
    ]
    if not day_rows:
        return 0
    written = execute_values(
        cur,
        """
        INSERT INTO daily_site_revenue (site_id, date, source, revenue, txn_count, is_frozen) VALUES %s
        ON CONFLICT (site_id, date, source) DO UPDATE
            SET revenue = EXCLUDED.revenue, txn_count = EXCLUDED.txn_count,
                is_frozen = EXCLUDED.is_frozen, computed_at = now()
            WHERE NOT daily_site_revenue.is_frozen
        RETURNING 1
        """,
        day_rows,
        fetch=True,
    )
    return len(written)


def first_sale_date(cur):
    cur.execute(
        f"""
        SELECT min({_EAT_TS})::date FROM revenue_events re
        WHERE re.event_type <> 'baseline' AND re.monitored_site_id IS NOT NULL
        """
    )
    row = cur.fetchone()
    return row[0] if row else None


def backfill():
    """All history, first sale to today. Days before yesterday are frozen as
    they're written. Safe to re-run: frozen rows are left alone, so a second
    run only refreshes today and yesterday."""
    with db_cursor() as (conn, cur):
        start = first_sale_date(cur)
        if start is None:
            return 0
        today = eat_today()
        written = _build(cur, start, today, freeze_before=today - timedelta(days=1))
        conn.commit()
    log.info("revenue backfill: %s daily rows written (%s to %s)", written, start, today)
    return written


def refresh():
    """Today and yesterday are recomputed; any older day still open is
    computed one last time and frozen."""
    with db_cursor() as (conn, cur):
        today = eat_today()
        cur.execute(
            "SELECT min(date) FROM daily_site_revenue WHERE source = %s AND NOT is_frozen AND date < %s",
            (SOURCE, today - timedelta(days=1)),
        )
        oldest_open = cur.fetchone()[0]
        start = min(oldest_open, today - timedelta(days=1)) if oldest_open else today - timedelta(days=1)
        written = _build(cur, start, today, freeze_before=today - timedelta(days=1))
        conn.commit()
    return written


def run_if_due():
    """Cheap throttle for a background task that fires on every heartbeat:
    refreshes at most every REFRESH_EVERY_SECONDS, one at a time."""
    global _last_run
    if time.monotonic() - _last_run < REFRESH_EVERY_SECONDS:
        return
    if not _lock.acquire(blocking=False):
        return
    try:
        if time.monotonic() - _last_run < REFRESH_EVERY_SECONDS:
            return
        _last_run = time.monotonic()
        refresh()
    except Exception:
        log.exception("revenue refresh failed")
    finally:
        _lock.release()


def refreeze(date_from, date_to, reason, performed_by):
    """Explicitly recomputes closed days from revenue_events and freezes them
    again. Logged with the old and new totals — a frozen day never changes
    without this record. Returns (old_total, new_total)."""
    if not reason or not reason.strip():
        raise ValueError("A reason is required to re-freeze a closed day.")
    with db_cursor() as (conn, cur):
        today = eat_today()
        if date_to >= today - timedelta(days=1):
            raise ValueError("Today and yesterday are still open; re-freeze only applies to closed days.")
        cur.execute(
            "SELECT COALESCE(sum(revenue), 0) FROM daily_site_revenue WHERE source = %s AND date BETWEEN %s AND %s",
            (SOURCE, date_from, date_to),
        )
        old_total = cur.fetchone()[0]
        cur.execute(
            "UPDATE daily_site_revenue SET is_frozen = false WHERE source = %s AND date BETWEEN %s AND %s",
            (SOURCE, date_from, date_to),
        )
        _build(cur, date_from, date_to, freeze_before=today - timedelta(days=1))
        cur.execute(
            "SELECT COALESCE(sum(revenue), 0) FROM daily_site_revenue WHERE source = %s AND date BETWEEN %s AND %s",
            (SOURCE, date_from, date_to),
        )
        new_total = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO daily_revenue_refreeze_log (date_from, date_to, reason, performed_by, old_total, new_total)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (date_from, date_to, reason.strip(), performed_by, old_total, new_total),
        )
        conn.commit()
    return old_total, new_total


def _main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="python -m db.revenue_daily")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backfill")
    sub.add_parser("refresh")
    rf = sub.add_parser("refreeze")
    rf.add_argument("date_from", type=date.fromisoformat)
    rf.add_argument("date_to", type=date.fromisoformat)
    rf.add_argument("--reason", required=True)
    rf.add_argument("--by", default="cli")
    args = parser.parse_args()

    if args.cmd == "backfill":
        print(f"{backfill()} daily rows written")
    elif args.cmd == "refresh":
        print(f"{refresh()} daily rows written")
    else:
        old, new = refreeze(args.date_from, args.date_to, args.reason, args.by)
        print(f"re-frozen {args.date_from}..{args.date_to}: {old} -> {new}")


if __name__ == "__main__":
    _main()
