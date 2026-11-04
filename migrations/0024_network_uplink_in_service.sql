-- Network page: a per-link "in service" switch. A link that is plugged in but
-- not paid for or not yet turned on (Veenet, 2026-11-04) never answers its
-- gateway, which would keep the page amber for as long as it lasts. An admin
-- switches such a link off here: it stays on the page, greyed and labelled
-- "Not in service", but is left out of the status and the events, and its
-- failed pings raise nothing. Switching it back on restores normal checking.
-- One additive column; nothing existing is altered. Approved by the owner
-- 2026-11-04.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0024_network_uplink_in_service.sql
-- Idempotent — re-running is safe.

ALTER TABLE network_uplinks ADD COLUMN IF NOT EXISTS in_service boolean NOT NULL DEFAULT true;

-- ROLLBACK (by hand, only if wanted):
--   ALTER TABLE network_uplinks DROP COLUMN in_service;
