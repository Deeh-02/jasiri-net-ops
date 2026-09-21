-- Phase 4.7 addendum — two additive columns, flagged per the CLAUDE.md
-- schema-review rule even though the blast radius is small. Both are
-- nullable with no default, so no row is rewritten and nothing behaves
-- differently until code starts populating them.
--
-- WHY THIS EXISTS. On 2026-09-21 revenue detection went silent for two
-- hours and nothing anywhere said so. The heartbeat carries the hotspot user
-- list only every Nth run, that "every Nth" was decided by a RouterOS global
-- that did not survive between scheduled runs, and so the list simply
-- stopped being sent. Ops kept reporting green: every site was up, the
-- headcount was live, the snapshot table was filling normally. The only
-- symptom was a revenue figure that looked slightly low, which is a thing a
-- person notices by luck rather than by monitoring.
--
-- The lesson is not "that bug"; it is that Ops could not tell the difference
-- between NO SALES and NO DATA. ingest_snapshots.users_reported closes that:
-- it records how many hotspot users a given heartbeat actually carried, so
-- "when did a customer list last arrive" is a question the database can
-- answer. NULL means that run carried no list at all, which is the normal
-- state four runs out of five — it is the AGE of the newest non-NULL row
-- that matters, never any single row.
--
-- It also gives the payload cap a paper trail. When the user list outgrows
-- $maxPayload the router drops it from that run and logs a warning to a log
-- that rotates, so today that failure is retroactively invisible. A run of
-- NULLs is not.
--
-- hotspot_packages.duration_minutes is how long a package buys, in minutes.
-- It is NOT derivable from the profile name: "Full day pass30" and "24hr
-- pass40" are both about a day at different prices, so this is owner-entered
-- reference data like price_kes beside it. NULL means "not stated", and
-- every code path treats an unstated duration as "draw no conclusion"
-- rather than guessing one.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0009_monitoring_feed_health.sql
--
-- Both statements are IF NOT EXISTS, so re-running is safe.

ALTER TABLE ingest_snapshots
    ADD COLUMN IF NOT EXISTS users_reported integer;

ALTER TABLE hotspot_packages
    ADD COLUMN IF NOT EXISTS duration_minutes integer;

-- Partial index: the only query against this column is "the newest heartbeat
-- that carried a list", and the overwhelming majority of rows are NULL.
CREATE INDEX IF NOT EXISTS idx_ingest_snapshots_users_reported
    ON ingest_snapshots(received_at DESC) WHERE users_reported IS NOT NULL;
