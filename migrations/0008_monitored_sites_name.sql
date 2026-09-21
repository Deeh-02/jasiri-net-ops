-- Phase 4 — display name for monitored sites that have no `locations` row.
-- Owner-approved 2026-09-21.
--
-- Sites linked to a location show that location's name (so renaming it in
-- Sites propagates). `name` is only the fallback for the unlinked ones, which
-- otherwise could only be labelled "VLAN 20". Nullable and editable; nothing
-- reads it once location_id is set.
--
-- The backfill uses the owner-supplied names recorded in 0007's notes, and
-- only fills rows whose name is still NULL, so re-running cannot overwrite an
-- edit.
--
--   psql "$DATABASE_URL" -f migrations/0008_monitored_sites_name.sql

ALTER TABLE monitored_sites ADD COLUMN IF NOT EXISTS name text;

UPDATE monitored_sites SET name = 'Street 10'  WHERE vlan_id = 20 AND location_id IS NULL AND name IS NULL;
UPDATE monitored_sites SET name = 'Monitoring' WHERE vlan_id = 55 AND location_id IS NULL AND name IS NULL;
UPDATE monitored_sites SET name = 'Cyber'      WHERE vlan_id = 77 AND location_id IS NULL AND name IS NULL;
UPDATE monitored_sites SET name = 'Street 9'   WHERE vlan_id = 99 AND location_id IS NULL AND name IS NULL;
