-- Phase 6 (SmartOLT): the OLT page. Approved by the owner on 2026-11-11.
--
-- 1. onu_state gains the Configured list's extra columns (zone, splitter,
--    mode, WAN mode, VLANs, ONU type, authorization date...), kept as one
--    jsonb of whitelisted fields from SmartOLT's get_all_onus_details, polled
--    every 30 minutes (that call is limited to 15 an hour). Never stored:
--    PPPoE username/password, address, contact, coordinates.
--
-- 2. onu_actions: the audit log of every write OPS sends to SmartOLT
--    (authorize for now), failures included: who, what was sent, SmartOLT's
--    answer, and what SmartOLT actually had afterwards (read-back).
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0032_onu_details_and_actions.sql

BEGIN;

ALTER TABLE onu_state ADD COLUMN IF NOT EXISTS details jsonb;
ALTER TABLE onu_state ADD COLUMN IF NOT EXISTS details_polled_at timestamptz;

CREATE TABLE IF NOT EXISTS onu_actions (
    id                  bigserial PRIMARY KEY,
    action              text NOT NULL,          -- 'authorize' for now
    sn                  text,
    unique_external_id  text,
    requested_by        int REFERENCES users(id),
    requested_at        timestamptz NOT NULL DEFAULT now(),
    request             jsonb,                  -- what was sent
    result              text,                   -- pending / succeeded / failed / mismatch
    response            text,                   -- SmartOLT's answer or the error
    read_back           jsonb,                  -- what SmartOLT had afterwards
    finished_at         timestamptz
);
CREATE INDEX IF NOT EXISTS onu_actions_requested_at_idx ON onu_actions (requested_at DESC);

COMMIT;

-- ROLLBACK:
--   DROP TABLE onu_actions;
--   ALTER TABLE onu_state DROP COLUMN details, DROP COLUMN details_polled_at;
