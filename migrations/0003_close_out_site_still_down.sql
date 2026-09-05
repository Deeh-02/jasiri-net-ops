-- mark_site_still_down now closes a movement out as 'completed' when a
-- site-check answers "still down" (the movement's lifecycle ends there;
-- the battery's real-world resolution is tracked on the location's
-- is_online, not on the movement). This backfills any row written before
-- that change, which is still sitting with the old literal
-- 'site_still_down' status and therefore keeps showing up in the
-- Movements page's active (not-yet-resolved) list even though it was
-- fully answered.
--
-- Run this once against the real (Render/Supabase) database, the same way
-- as 0001/0002:
--   psql "$DATABASE_URL" -f migrations/0003_close_out_site_still_down.sql

UPDATE battery_movements
SET status = 'completed'
WHERE status = 'site_still_down';
