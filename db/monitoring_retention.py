import logging
import threading
import time
from datetime import timedelta
from db.connection import db_cursor

log = logging.getLogger(__name__)

# Free-tier retention — see the RETENTION note in migrations/0006. Raw rows
# older than these are folded into one 'hourly' row per hour and then deleted.
SNAPSHOT_RAW_KEEP = timedelta(days=30)
# Per-minute rows: ~30k/day across 21 sites, so 7 days is ~210k rows (~30 MB).
# Was 14 days when rows arrived every 5 minutes. Older data survives as hourly
# averages, which is all the graphs beyond a week need.
SESSION_RAW_KEEP = timedelta(days=7)

# The job is cheap and idempotent, so this only stops it running on every
# 60s heartbeat.
RUN_EVERY_SECONDS = 3600

_lock = threading.Lock()
_last_run = 0.0


def _roll_up_session_counts(cur, keep, dry_run):
    # The cutoff is truncated to a whole hour, so an hour is always entirely
    # raw or entirely rolled up — never half of each. That is what makes
    # a second run a no-op.
    cur.execute(
        "SELECT count(*) FROM site_session_counts "
        "WHERE granularity = 'raw' AND received_at < date_trunc('hour', now() - %s)",
        (keep,),
    )
    raw_rows = cur.fetchone()[0]
    if dry_run or raw_rows == 0:
        return raw_rows, 0
    cur.execute(
        """
        INSERT INTO site_session_counts (monitored_site_id, sessions, granularity, received_at)
        SELECT monitored_site_id, round(avg(sessions))::int, 'hourly', date_trunc('hour', received_at)
        FROM site_session_counts
        WHERE granularity = 'raw' AND received_at < date_trunc('hour', now() - %s)
        GROUP BY monitored_site_id, date_trunc('hour', received_at)
        """,
        (keep,),
    )
    hourly_rows = cur.rowcount
    cur.execute(
        "DELETE FROM site_session_counts "
        "WHERE granularity = 'raw' AND received_at < date_trunc('hour', now() - %s)",
        (keep,),
    )
    return raw_rows, hourly_rows


def _roll_up_snapshots(cur, keep, dry_run):
    cur.execute(
        "SELECT count(*) FROM ingest_snapshots "
        "WHERE granularity = 'raw' AND received_at < date_trunc('hour', now() - %s)",
        (keep,),
    )
    raw_rows = cur.fetchone()[0]
    if dry_run or raw_rows == 0:
        return raw_rows, 0
    # router_ts / payload_hash are deliberately left NULL: an hourly row proves
    # "we were watching this hour", and nothing looks a rolled-up snapshot up
    # by hash. site_status_log.snapshot_id goes NULL when the raw row is
    # deleted (ON DELETE SET NULL), which is the intended behaviour.
    cur.execute(
        """
        INSERT INTO ingest_snapshots (seq, sites_reporting, granularity, received_at)
        SELECT min(seq), round(avg(sites_reporting))::int, 'hourly', date_trunc('hour', received_at)
        FROM ingest_snapshots
        WHERE granularity = 'raw' AND received_at < date_trunc('hour', now() - %s)
        GROUP BY date_trunc('hour', received_at)
        """,
        (keep,),
    )
    hourly_rows = cur.rowcount
    cur.execute(
        "DELETE FROM ingest_snapshots "
        "WHERE granularity = 'raw' AND received_at < date_trunc('hour', now() - %s)",
        (keep,),
    )
    return raw_rows, hourly_rows


def roll_up_and_trim(dry_run=False):
    """Rolls raw rows past their retention window into hourly rows, then
    deletes the raw ones. Each table is one transaction: the hourly rows and
    the deletion commit together or not at all, so a crash can never leave
    the raw data deleted without its rollup. dry_run only counts."""
    result = {}
    with db_cursor() as (conn, cur):
        for name, step, keep in (
            ("site_session_counts", _roll_up_session_counts, SESSION_RAW_KEEP),
            ("ingest_snapshots", _roll_up_snapshots, SNAPSHOT_RAW_KEEP),
        ):
            try:
                raw_rows, hourly_rows = step(cur, keep, dry_run)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            result[name] = {"raw_rows": raw_rows, "hourly_rows": hourly_rows}
    return result


def run_if_due():
    """Called after an ingest response has gone out. Never raises — a
    retention failure must not affect the heartbeat."""
    global _last_run
    if time.monotonic() - _last_run < RUN_EVERY_SECONDS and _last_run:
        return
    if not _lock.acquire(blocking=False):
        return
    try:
        _last_run = time.monotonic()
        result = roll_up_and_trim()
        if any(t["raw_rows"] for t in result.values()):
            log.info("monitoring retention: %s", result)
    except Exception:
        log.exception("monitoring retention failed")
    finally:
        _lock.release()
