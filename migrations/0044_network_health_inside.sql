-- Network Health: "Inside network" records (2026-11-21).
--
--   olt       every 5 min: the CCR2116 reads the Huawei MA5680T over SNMP:
--             uptime, and the uplink port 0/19/0's good / errored received
--             frames (the frames the CRS326 sends towards customers).
--   crs_link  on each CRS326 port up/down (sfp-sfpplus3 = the OLT uplink).
--
-- Two new nullable columns on nh_readings, like load / probes / link / speed
-- / route / dns: same insert, same dedupe on `key`. Nothing existing
-- changes; existing rows get NULL. The existing (kind, ts) index covers
-- every read (olt is 288 rows a day, crs_link a handful).
--
-- Retention (app code, not this file): `olt` rows are kept 90 days, not 10,
-- so the damaged-frames graph shows the before/after of the DAC swap and
-- the restart list reaches back past a week (~2 restarts a week, ~26,000
-- rows). `crs_link` rows are kept, like `route` (a few a week).
--
-- Run once:  psql "$DATABASE_URL" -f migrations/0044_network_health_inside.sql
-- Idempotent: re-running is safe.
-- Undo: ALTER TABLE nh_readings DROP COLUMN olt, DROP COLUMN crs_link;

BEGIN;

ALTER TABLE nh_readings ADD COLUMN IF NOT EXISTS olt jsonb;        -- olt: {uptime_s, up_rx, up_crc}, any of them null
ALTER TABLE nh_readings ADD COLUMN IF NOT EXISTS crs_link jsonb;   -- crs_link: {device, interface, state, speed}

COMMIT;
