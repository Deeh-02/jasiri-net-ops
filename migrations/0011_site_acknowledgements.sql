-- Phase 4.5 — acknowledgement of a known-bad site. One new table, nothing
-- existing is altered; dropping it leaves every other table untouched.
--
-- WHY THIS EXISTS. A site waiting three days for a battery holds the Status
-- headline red the whole time, and a page that is always red trains everyone
-- to stop reading it. Acknowledging moves that site into a muted "Known
-- issues" strip and out of the headline count, WITHOUT changing its state:
-- it is still Down in the table and in uptime, only no longer "news".
--
-- An acknowledgement ends in one of two ways:
--   * someone presses Un-acknowledge (cleared_by is set), or
--   * the site comes back online — cleared in the same transaction that
--     records the recovery (ingest_snapshot in db/monitoring.py), so the
--     next drop always alerts fresh rather than staying muted.
-- Rows are never deleted: the history of who acknowledged what, and why, is
-- the record of how each outage was handled.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0011_site_acknowledgements.sql
--
-- Additive and IF NOT EXISTS, so re-running is safe. Run it BEFORE deploying
-- the code that reads this table — /monitoring/status queries it on every
-- request and will fail without it.

CREATE TABLE IF NOT EXISTS site_acknowledgements (
    id serial PRIMARY KEY,
    monitored_site_id integer NOT NULL REFERENCES monitored_sites(id) ON DELETE RESTRICT,
    note text,
    acknowledged_by integer REFERENCES users(id),
    acknowledged_at timestamp without time zone NOT NULL DEFAULT now(),
    -- NULL while the acknowledgement is in force. Set on Un-acknowledge, or
    -- automatically when the site is next seen online (cleared_by NULL then).
    cleared_at timestamp without time zone,
    cleared_by integer REFERENCES users(id)
);

-- At most one acknowledgement in force per site. Also what makes a double
-- click on Acknowledge a conflict rather than two rows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_site_ack_open
    ON site_acknowledgements(monitored_site_id) WHERE cleared_at IS NULL;
