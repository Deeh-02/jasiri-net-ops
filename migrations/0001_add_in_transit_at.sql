-- Adds the timestamp of when a movement was actually marked "in transit",
-- separate from `created_at` (when it was first queued as 'pending').
-- The battery table's "Since" column now needs this: per the new
-- movement-lifecycle status rules, "since" should reflect when the battery
-- physically left, not when the move was first requested.
--
-- Run this once against the real (Render/Supabase) database — this repo's
-- schema.sql is a stale pg_dump snapshot (it's already missing arrived_at,
-- confirmed_at, and moved_by_user_id, which the app code has used for a
-- while), so it is NOT a reliable source to diff against or regenerate
-- from. Apply this file directly:
--   psql "$DATABASE_URL" -f migrations/0001_add_in_transit_at.sql

ALTER TABLE battery_movements
    ADD COLUMN IF NOT EXISTS in_transit_at timestamp without time zone;
