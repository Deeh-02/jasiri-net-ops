-- Revenue reporting, step 1 — the derived tables every revenue report reads.
-- Three new tables, nothing existing is altered; dropping them leaves
-- monitoring and revenue_events exactly as they were.
--
-- WHY A DERIVED TABLE AT ALL. revenue_events is the money record, but it is
-- not stable: _backfill_unplaced (db/monitoring.py) can attach a sale to a
-- site up to a day after the fact, so a site's total for a past day can move
-- under you. A report for 6 October must say the same thing every time it is
-- opened, so each day is computed once, then FROZEN. Only today and
-- yesterday stay open — the same ~24h reach the backfill has.
--
-- WHAT GOES IN. Hotspot sales only (revenue_events is hotspot-only; PPPoE
-- billing is a different domain and is never read), event_type <> 'baseline'
-- (baseline rows are the first-ever sighting of existing users, priced 0),
-- and only sales attributed to a site: unplaced sales (monitored_site_id
-- NULL) are deliberately left out — owner's call 2026-10-14.
--
-- DAYS ARE EAT. first_seen_at is naive UTC; a sale at 23:30 UTC belongs to
-- the NEXT Nairobi day. EAT has no DST, so +3h is exact (same reasoning as
-- db/connection.py's EAT constant).
--
-- A row is written for every site that was active, for every day, including
-- zeros: "no sales" and "no row" must never mean the same thing, or a silent
-- site can't be told apart from one that wasn't there.
--
-- hourly_site_revenue exists because "today at 19:00 vs yesterday 00:00-19:00"
-- (and MTD at 19:00 vs last month's same point) needs sub-day totals for
-- days that are long past. One row per site per EAT hour with sales; small
-- (a few thousand rows a month), so it is kept for all history rather than
-- just today. hour_start is the EAT wall-clock hour as a naive timestamp.
--
-- Run this once against the target database, then backfill:
--   psql "$DATABASE_URL" -f migrations/0019_daily_site_revenue.sql
--   python -m db.revenue_daily backfill
-- Additive and IF NOT EXISTS, so re-running is safe.

CREATE TABLE IF NOT EXISTS daily_site_revenue (
    site_id     integer NOT NULL REFERENCES monitored_sites(id) ON DELETE RESTRICT,
    date        date NOT NULL,                      -- EAT calendar day
    source      text NOT NULL DEFAULT 'hotspot',    -- only 'hotspot' in v1
    revenue     numeric(12,2) NOT NULL DEFAULT 0,
    txn_count   integer NOT NULL DEFAULT 0,
    is_frozen   boolean NOT NULL DEFAULT false,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, date, source)
);

CREATE INDEX IF NOT EXISTS idx_daily_site_revenue_date
    ON daily_site_revenue(date, source);

CREATE TABLE IF NOT EXISTS hourly_site_revenue (
    site_id     integer NOT NULL REFERENCES monitored_sites(id) ON DELETE RESTRICT,
    hour_start  timestamp without time zone NOT NULL,   -- EAT wall-clock hour
    source      text NOT NULL DEFAULT 'hotspot',
    revenue     numeric(12,2) NOT NULL DEFAULT 0,
    txn_count   integer NOT NULL DEFAULT 0,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, hour_start, source)
);

CREATE INDEX IF NOT EXISTS idx_hourly_site_revenue_hour
    ON hourly_site_revenue(hour_start, source);

-- Every explicit re-freeze of a closed day, with who and why. A frozen day
-- never changes silently; this is the record of when it was changed on purpose.
CREATE TABLE IF NOT EXISTS daily_revenue_refreeze_log (
    id            serial PRIMARY KEY,
    date_from     date NOT NULL,
    date_to       date NOT NULL,
    reason        text NOT NULL,
    performed_by  text NOT NULL,
    old_total     numeric(12,2) NOT NULL,
    new_total     numeric(12,2) NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
