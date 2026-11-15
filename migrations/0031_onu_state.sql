-- Phase 6A (SmartOLT, read only): the latest status and signal of every ONU,
-- as SmartOLT last reported it. Approved by the owner on 2026-11-10.
--
-- One row per ONU, keyed by SmartOLT's unique_external_id (the ONU's serial).
-- Written only by db/smartolt.py: the status poll (every 6 minutes) and the
-- signal poll (every 20 minutes) each upsert their own columns, and the status
-- poll removes ONUs that SmartOLT no longer lists. Nothing else reads or writes
-- it yet, and nothing in OPS ever writes back to SmartOLT from it.
--
-- Deliberately not stored: SmartOLT's `address` and `contact` (phone) fields.
--
-- Signals are numbers in dBm, NULL when SmartOLT has no reading ("-").
-- last_status_change arrives as Nairobi wall-clock time with no zone; the
-- code reads it as UTC+3 before it gets here.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0031_onu_state.sql

BEGIN;

CREATE TABLE IF NOT EXISTS onu_state (
    unique_external_id  text PRIMARY KEY,
    sn                  text,
    olt_id              int,
    board               int,
    port                int,
    onu                 int,          -- the ONU's number on its PON port
    zone_id             int,
    name                text,
    status              text,         -- Online / Offline / LOS / Power fail
    last_status_change  timestamptz,
    signal_1310_dbm     numeric(6,2), -- received at the OLT (upstream)
    signal_1490_dbm     numeric(6,2), -- received at the ONU (downstream)
    signal_quality      text,         -- SmartOLT's grade: Very good / Warning / Critical
    status_polled_at    timestamptz,
    signal_polled_at    timestamptz
);

COMMIT;

-- ROLLBACK (removes Phase 6A's data and nothing else):
--   DROP TABLE onu_state;
