-- Phase 6: log LOS alert messages in alert_deliveries, the same table site
-- alerts use, so Reports > SMS Status shows them, failures included.
-- Approved by the owner on 2026-11-11.
--
-- A delivery now belongs to a site alert (monitored_site_id, as before) or
-- to an ONU LOS alert (onu_alert_id). A customer's ONU has no site, so
-- monitored_site_id can be NULL for LOS rows. Existing rows are untouched:
-- they all have a site, so the new CHECK holds for them. The site alert code
-- and the per-site SMS rate limit only look rows up by site, so LOS rows
-- never count against a site's limit.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0036_onu_alert_deliveries.sql

BEGIN;

ALTER TABLE alert_deliveries ALTER COLUMN monitored_site_id DROP NOT NULL;
ALTER TABLE alert_deliveries
    ADD COLUMN IF NOT EXISTS onu_alert_id bigint REFERENCES onu_alerts(id) ON DELETE CASCADE;
ALTER TABLE alert_deliveries DROP CONSTRAINT IF EXISTS alert_deliveries_site_or_onu;
ALTER TABLE alert_deliveries ADD CONSTRAINT alert_deliveries_site_or_onu
    CHECK (monitored_site_id IS NOT NULL OR onu_alert_id IS NOT NULL);
CREATE INDEX IF NOT EXISTS idx_alert_deliveries_onu_alert ON alert_deliveries (onu_alert_id)
    WHERE onu_alert_id IS NOT NULL;

COMMIT;

-- ROLLBACK (LOS delivery records are lost; site rows stay):
--   BEGIN;
--   DELETE FROM alert_deliveries WHERE monitored_site_id IS NULL;
--   ALTER TABLE alert_deliveries DROP CONSTRAINT alert_deliveries_site_or_onu;
--   ALTER TABLE alert_deliveries DROP COLUMN onu_alert_id;
--   ALTER TABLE alert_deliveries ALTER COLUMN monitored_site_id SET NOT NULL;
--   COMMIT;
