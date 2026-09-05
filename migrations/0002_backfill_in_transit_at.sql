-- 0001 added in_transit_at but left every existing row NULL, since the
-- column didn't exist yet when those movements went through their
-- lifecycle. Without this backfill, every battery whose last movement
-- predates 0001 shows a blank "Since" on the battery table even though it
-- has a real historical date.
--
-- created_at is the closest approximation we have for movements that never
-- got a real in_transit_at recorded — only backfilling non-'pending' rows,
-- since a still-pending movement legitimately hasn't transitioned yet and
-- should keep showing NULL (falls back to the movement before it).
--
-- Run this once against the real (Render/Supabase) database, the same way
-- as 0001:
--   psql "$DATABASE_URL" -f migrations/0002_backfill_in_transit_at.sql

UPDATE battery_movements
SET in_transit_at = created_at
WHERE in_transit_at IS NULL
  AND status != 'pending';
