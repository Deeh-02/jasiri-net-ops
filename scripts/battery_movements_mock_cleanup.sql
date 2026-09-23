-- Battery log (battery_movements) mock-data cleanup — STAGED, NOT RUN.
--
-- SCOPE NOTE: this is battery-domain data hygiene, not Phase 4 (Network
-- Monitoring) work — phase.md's own rule is "if a request doesn't match the
-- current phase's declared scope, flag it, don't do it anyway." Flagged
-- here; staged anyway per explicit request, with the review/backup/no-
-- auto-execute steps that request specified. No agent ran this against a
-- real database — the owner reviews and runs it by hand, same convention
-- as every numbered file in migrations/.
--
-- TABLE NAME, CONFIRMED NOT ASSUMED: there is no table literally named
-- "battery_log" anywhere in schema.sql or migrations/. The append-only,
-- one-row-per-event table that plays that role is `battery_movements`
-- (schema.sql, Phase 0) — one row per battery move, timestamped by
-- `created_at`, which is what this script filters on. `batteries` itself
-- is current-state only (one row per physical battery, no history).
--
-- WHY created_at, NOT arrived_at/confirmed_at/in_transit_at: those three
-- are nullable lifecycle timestamps (a cancelled or still-pending movement
-- may have none of them set). created_at is NOT NULL DEFAULT now() on
-- every row without exception — the one timestamp guaranteed to exist and
-- to mean "when this log entry was made", which is what "dated before
-- Sept 5, 2026" means here.
--
-- CUTOFF: rows with created_at < 2026-09-05 00:00:00 are deleted. Rows
-- from 2026-09-05 00:00:00 onward are kept, untouched. This is a UTC
-- boundary (created_at is stored as UTC wall-clock per db/connection.py) —
-- if "Sept 5" was meant in EAT (UTC+3), the real cutoff is 2026-09-04
-- 21:00:00 UTC. Confirm which was meant before running; the query below
-- uses the UTC reading. Check both counts against what you expect before
-- proceeding to DELETE.
--
-- FK CHECK (verified 2026-09-23 against schema.sql + every migrations/*.sql
-- file): NOTHING references battery_movements.id. Its own FKs point OUT —
-- battery_id -> batteries, from_location_id/to_location_id -> locations,
-- moved_by_user_id -> users — so deleting rows here cannot violate a
-- constraint or silently orphan a row in another table. Step 0 below reruns
-- that check live against information_schema, in case something changed
-- since this file was written — if it prints any rows, STOP and read them
-- before going further.
--
-- THE REAL RISK ISN'T AN FK — IT'S COMPUTED STATE. db/batteries.py derives
-- a battery's current status/location LIVE from its latest surviving
-- battery_movements row (get_last_movement / _battery_status), not from a
-- stored column. A battery whose ENTIRE movement history is mock data
-- (every row before the cutoff) will, after this delete, have zero
-- movement rows left — already a handled state ("Unknown (no movements
-- recorded)"), not a crash, but a real visible change for that battery.
-- Step 2 below lists exactly which batteries that would hit, so it's a
-- known list, not a surprise.
--
-- HOW TO RUN THIS: open it in psql (`psql "$DATABASE_URL" -f
-- scripts/battery_movements_mock_cleanup.sql`) or, better, paste it
-- section by section into an interactive psql session so each step's
-- output can be read before the next one runs. Steps 0-3 are read-only.
-- Step 4 creates a backup table. Step 5 is the only step that deletes
-- anything, and it is wrapped in BEGIN/COMMIT below — reading the row
-- counts it prints and typing ROLLBACK instead of COMMIT is always an
-- option if anything looks wrong.


-- ============================================================
-- STEP 0 — live FK check (read-only). Expect ZERO rows.
-- ============================================================
SELECT
    tc.table_name AS referencing_table,
    kcu.column_name AS referencing_column
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage ccu
    ON tc.constraint_name = ccu.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND ccu.table_name = 'battery_movements';


-- ============================================================
-- STEP 1 — row counts, both sides of the split (read-only).
-- ============================================================
SELECT
    count(*) FILTER (WHERE created_at < '2026-09-05 00:00:00') AS to_delete,
    count(*) FILTER (WHERE created_at >= '2026-09-05 00:00:00') AS to_keep,
    count(*) AS total,
    min(created_at) AS oldest_row,
    max(created_at) AS newest_row
FROM battery_movements;


-- ============================================================
-- STEP 2 — batteries that would lose ALL movement history
-- (read-only). Not an error, just worth knowing before Step 5.
-- ============================================================
SELECT b.id, b.battery_number, b.status, b.charge_status,
       count(bm.id) AS movement_rows_total,
       count(bm.id) FILTER (WHERE bm.created_at >= '2026-09-05 00:00:00') AS movement_rows_kept
FROM batteries b
JOIN battery_movements bm ON bm.battery_id = b.id
GROUP BY b.id, b.battery_number, b.status, b.charge_status
HAVING count(bm.id) FILTER (WHERE bm.created_at >= '2026-09-05 00:00:00') = 0
ORDER BY b.battery_number;


-- ============================================================
-- STEP 3 — sample of what's about to be deleted (read-only).
-- Eyeball a handful of rows before trusting the count in Step 1.
-- ============================================================
SELECT id, battery_id, from_location_id, to_location_id, reason, status, created_at
FROM battery_movements
WHERE created_at < '2026-09-05 00:00:00'
ORDER BY created_at DESC
LIMIT 20;


-- ============================================================
-- STEP 4 — backup. Creates a same-database copy of every row
-- about to be deleted, timestamped in its own name so re-running
-- this script later (a second cleanup pass) can't collide with
-- an earlier backup. ALSO take a real pg_dump export outside the
-- database before Step 5 if this is production — a backup table
-- in the same DB doesn't help if the DB itself is the thing that
-- goes wrong:
--   pg_dump "$DATABASE_URL" -t battery_movements \
--     --data-only -f battery_movements_backup_2026-09-23.sql
-- ============================================================
CREATE TABLE IF NOT EXISTS battery_movements_backup_20260923 AS
SELECT * FROM battery_movements WHERE created_at < '2026-09-05 00:00:00';

SELECT count(*) AS rows_backed_up FROM battery_movements_backup_20260923;


-- ============================================================
-- STEP 5 — the actual delete. Read Step 1's counts and Step 4's
-- backup count above before running this. COMMIT is explicit and
-- separate on purpose: if the count printed after DELETE doesn't
-- match Step 1's to_delete figure, run ROLLBACK instead.
-- ============================================================
BEGIN;

DELETE FROM battery_movements WHERE created_at < '2026-09-05 00:00:00';
-- Compare this to Step 1's `to_delete` count before committing.

SELECT count(*) AS remaining_rows FROM battery_movements;
-- Compare this to Step 1's `to_keep` count before committing.

-- COMMIT;   -- uncomment once both counts above check out
-- ROLLBACK; -- or uncomment this instead if anything looks wrong
