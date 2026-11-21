-- Network Health: "Inside network", part 2 (2026-11-21).
--
--   area     every 5 min, one record per VLAN on the CCR2004 uplink: running
--            down/up byte counters (Mbps = delta * 8 / seconds).
--   lanlink  every 5 min, inside port counters (CCR2116, CCR2004, CRS326).
--   hop      every minute, to-box probes: same `probes` list as kind `probe`,
--            so it reuses the existing `probes` column (no change here).
--
-- Two new nullable columns on nh_readings, like the other kinds: same
-- insert, same key. Nothing existing changes; existing rows get NULL. The
-- existing (kind, ts) index covers every read. Deleted after 10 days like
-- the other frequent readings (area: ~20 VLANs x 288 a day).
--
-- The ingest's switch from "skip a known key" to "replace a known key whose
-- values differ" (the loss-scale fix) is app code: it uses the existing
-- unique key, no schema change.
--
-- Run once:  psql "$DATABASE_URL" -f migrations/0045_network_health_area_lanlink.sql
-- Idempotent: re-running is safe.
-- Undo: ALTER TABLE nh_readings DROP COLUMN area, DROP COLUMN lanlink;

BEGIN;

ALTER TABLE nh_readings ADD COLUMN IF NOT EXISTS area jsonb;      -- area: {vlan, if, down_bytes, up_bytes}
ALTER TABLE nh_readings ADD COLUMN IF NOT EXISTS lanlink jsonb;   -- lanlink: {dev, port, link_downs, rx_fcs, rx_err, rx_overflow, tx_drop}

COMMIT;
