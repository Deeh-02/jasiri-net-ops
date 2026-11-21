-- Network Health: `dns` records (2026-11-21). Every 5 minutes the CCR2116
-- times two lookups (www.youtube.com, api.safaricom.co.ke) against each
-- resolver: 8.8.8.8, 9.9.9.9 and the router itself
-- (docs/network-health-ingest-contract.md). Drawn as "DNS lookup time";
-- the router resolver failing or slow two runs in a row opens an incident.
--
-- One new nullable column on nh_readings, like load / probes / link /
-- speed / route: same insert, same dedupe on `key`. Nothing existing
-- changes; existing rows get NULL. Deleted after 10 days like the other
-- frequent readings (288 a day). The existing (kind, ts) index covers it.
--
-- Run once:  psql "$DATABASE_URL" -f migrations/0043_network_health_dns.sql
-- Idempotent: re-running is safe. Undo: ALTER TABLE nh_readings DROP COLUMN dns;

BEGIN;

ALTER TABLE nh_readings ADD COLUMN IF NOT EXISTS dns jsonb;   -- dns: {resolvers: [{name, server, worst_ms, fails}]}

COMMIT;
