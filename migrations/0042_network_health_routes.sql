-- Network Health: `route` records (2026-11-20). A traceroute from the
-- CCR2116 to www.youtube.com, www.facebook.com, www.tiktok.com and
-- api.safaricom.co.ke, daily at 21:00 and 04:00, through the same ingest
-- (docs/network-health-ingest-contract.md). Shown on the page as "Where
-- content is served from".
--
-- One new nullable column on nh_readings, like load / probes / link /
-- speed: the record goes through the same insert and the same dedupe on
-- `key`. Nothing existing changes; existing rows get NULL.
--
-- Route rows are KEPT (the app's 10-day cleanup skips kind = 'route'):
-- they are what gets compared after the December provider switch. 8 a day,
-- about 3,000 a year, a couple of KB each.
--
-- The partial index serves "each site's checks, newest first" for the card.
--
-- Run once:  psql "$DATABASE_URL" -f migrations/0042_network_health_routes.sql
-- Idempotent: re-running is safe. Undo: DROP INDEX nh_readings_route_host;
-- ALTER TABLE nh_readings DROP COLUMN route;

BEGIN;

ALTER TABLE nh_readings ADD COLUMN IF NOT EXISTS route jsonb;   -- route: {host, ip, status, last_ms, hops}

CREATE INDEX IF NOT EXISTS nh_readings_route_host
    ON nh_readings ((route->>'host'), ts DESC) WHERE kind = 'route';

COMMIT;
