-- Phase 6B (SmartOLT): which customer each ONU belongs to. Approved by the
-- owner on 2026-11-11.
--
-- One row per ONU at most (primary key) and one ONU per customer at most
-- (unique customer_id), so neither can be linked twice. A row is either a
-- link to a customer, or a note that the ONU isn't a customer's at all (a
-- hotspot, a site, a monitoring link) so it stops showing as unmatched.
-- Every row is confirmed by a person (confirmed_by); OPS only suggests.
--
-- Rejected suggestions aren't stored: a rejected pair is suggested again.
-- Linking never changes an ONU, a customer or a billing record.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0033_onu_customer_links.sql

BEGIN;

CREATE TABLE IF NOT EXISTS onu_customer_links (
    unique_external_id  text PRIMARY KEY,
    customer_id         int UNIQUE REFERENCES ppp_customers(id) ON DELETE CASCADE,
    not_a_customer      boolean NOT NULL DEFAULT false,
    matched_by          text,                 -- pppoe_username / name / manual
    confirmed_by        int REFERENCES users(id) ON DELETE SET NULL,
    confirmed_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (customer_id IS NOT NULL OR not_a_customer)
);

COMMIT;

-- ROLLBACK (removes every link and nothing else):
--   DROP TABLE onu_customer_links;
