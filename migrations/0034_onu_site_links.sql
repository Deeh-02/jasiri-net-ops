-- Phase 6B: an ONU can also belong to a hotspot site (a monitored_sites row),
-- not only to a customer. Approved by the owner on 2026-11-11.
--
-- Each row is now exactly one of: a customer, a site, or "not a customer".
-- One ONU per site (UNIQUE site_id), like one ONU per customer. Network
-- Status and the site page read the link to show the site's fibre ONU; they
-- never write it. Linking changes nothing in SmartOLT or in monitoring.
--
-- If an existing row broke the new rule, the ADD CONSTRAINT below fails and
-- the whole migration is rolled back (nothing half-applied).
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0034_onu_site_links.sql

BEGIN;

ALTER TABLE onu_customer_links
    ADD COLUMN IF NOT EXISTS site_id int UNIQUE REFERENCES monitored_sites(id) ON DELETE CASCADE;

ALTER TABLE onu_customer_links DROP CONSTRAINT IF EXISTS onu_customer_links_check;
ALTER TABLE onu_customer_links DROP CONSTRAINT IF EXISTS onu_link_one_kind;
ALTER TABLE onu_customer_links ADD CONSTRAINT onu_link_one_kind
    CHECK (num_nonnulls(customer_id, site_id) + not_a_customer::int = 1);

COMMIT;

-- ROLLBACK (site links are lost; customer links stay):
--   BEGIN;
--   DELETE FROM onu_customer_links WHERE site_id IS NOT NULL;
--   ALTER TABLE onu_customer_links DROP CONSTRAINT onu_link_one_kind;
--   ALTER TABLE onu_customer_links DROP COLUMN site_id;
--   ALTER TABLE onu_customer_links ADD CHECK (customer_id IS NOT NULL OR not_a_customer);
--   COMMIT;
