-- Phase 4.9 — Reconcile monitor against human confirm-online. One new
-- table. Nothing existing is altered — same additive shape as every
-- migration since 0004.
--
-- NO ISOLATION AMENDMENT NEEDED HERE, unlike Task 3's battery read. This
-- table only references monitored_sites (monitoring's own) and users
-- (already-precedented direction, same as alert_deliveries.user_id in
-- 0012). It does NOT reference battery_movements or locations at all —
-- the movements domain's `arrived_at` needed for "12m after you marked
-- arrived" is read by the FRONTEND from the movement it already has in
-- hand, not looked up here. `locations`/`battery_movements` gain zero
-- columns and zero awareness of monitoring, same as always.
--
-- WHAT THIS RECORDS. PHASES.md's "collision" decision: monitoring becomes
-- the system of record for "is this site's uplink up", but the human
-- confirm-online question (tied to a specific site-down movement) stays —
-- "the site is online" and "the battery I just installed is powering it"
-- are different claims. This table is where BOTH answers land side by
-- side, written once per human confirm-online tap by
-- routers/monitoring.py's new /monitoring/sites/{id}/confirmation-check
-- (called by the frontend right after the existing
-- POST /movements/{id}/confirm-online, which is completely unchanged).
-- A mismatch is exactly the "finding" PHASES.md calls out — surfaced via
-- the existing notifications table (0012), not a new alerting path.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0014_site_confirmations.sql

CREATE TABLE IF NOT EXISTS site_confirmations (
    id serial PRIMARY KEY,
    monitored_site_id integer NOT NULL REFERENCES monitored_sites(id) ON DELETE RESTRICT,
    human_says_online boolean NOT NULL,
    -- Monitoring's own last-known state at the moment of the human's
    -- answer. NULL when monitoring had nothing to compare against yet
    -- (a brand-new site, or one with liveness_source='activity', which
    -- can never assert "offline" — see Correction 6 in PHASES.md).
    monitoring_says_online boolean,
    -- NULL exactly when monitoring_says_online is NULL — "no comparison
    -- was possible" is a different state from "they agreed", and
    -- collapsing the two would make an unmonitored site look confirmed.
    agrees boolean,
    confirmed_by integer REFERENCES users(id),
    created_at timestamp without time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_site_confirmations_site_time
    ON site_confirmations(monitored_site_id, created_at DESC);
