-- Revenue reporting, step 6 — stored alert results. One new table, nothing
-- existing is altered.
--
-- One row per site per EAT day, written when that day is evaluated (the
-- 15-minute revenue refresh evaluates yesterday). status is 'red' / 'amber'
-- / 'green', or NULL for a normal day; alerts holds every rule that fired
-- with the values that triggered it, and config the thresholds in force —
-- so a past day reads back exactly as it was judged, even after someone
-- changes a threshold. (Days still inside the freeze window are re-judged
-- on each refresh; days at or past it are not.)
--
-- Safe to deploy the code first: reads fall back to evaluating live, and the
-- write swallows the missing-table error, so alerts on screen are unaffected.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0021_revenue_site_status.sql

CREATE TABLE IF NOT EXISTS revenue_site_status (
    site_id       integer NOT NULL REFERENCES monitored_sites(id) ON DELETE RESTRICT,
    date          date NOT NULL,
    status        text,
    alerts        jsonb NOT NULL DEFAULT '[]',
    value         numeric(12,2),
    config        jsonb NOT NULL DEFAULT '{}',
    evaluated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, date)
);

CREATE INDEX IF NOT EXISTS idx_revenue_site_status_date_status
    ON revenue_site_status(date, status);
