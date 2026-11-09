-- Technical Activity Log, Phase 2: extra fields per activity type (config, no
-- code per field), and the repeat-visit metric.
--
-- Two columns added to tables from 0026; nothing is dropped or rewritten:
--   activity_types.details_schema         jsonb, NULL = the type has no extra
--                                         fields. A list of field definitions:
--                                         {"key","label","kind","required"[, "min","max"]}
--                                         kind: 'text' | 'number' | 'customer'.
--                                         The answers are stored in
--                                         activity_reports.details (already there).
--   activity_categories.repeat_visit_tracking
--                                         boolean, default false. Categories
--                                         whose reports count toward the
--                                         Overview's "repeat visits". Seeded true
--                                         for Customer Support; an admin changes
--                                         it on the Manage screen. A flag, not a
--                                         hardcoded name, so the code never names
--                                         a category.
--
-- Seed (the scope's two starting sets):
--   Customer Installation types: customer (searchable), package, optical
--       signal reading (dBm)
--   Network Maintenance types:   fault encountered, repair performed
-- Cable type and cable length are NOT here on purpose: cable length is a
-- material quantity and belongs to Materials, when that is built.
--
-- Safe and backwards compatible: the Phase 1 code ignores both columns, so
-- this can be applied before or after the new code is deployed. The seed
-- runs only on the first run (and only fills types that have no schema
-- yet), so re-running never undoes an admin's changes on the Manage screen.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0027_activity_log_phase2.sql
-- Idempotent — re-running is safe.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'activity_categories' AND column_name = 'repeat_visit_tracking') THEN
        ALTER TABLE activity_categories ADD COLUMN repeat_visit_tracking boolean NOT NULL DEFAULT false;
        UPDATE activity_categories SET repeat_visit_tracking = true WHERE name = 'Customer Support';
    END IF;
END $$;

ALTER TABLE activity_types ADD COLUMN IF NOT EXISTS details_schema jsonb;

UPDATE activity_types t
SET details_schema = '[
    {"key": "customer",   "label": "Customer",                     "kind": "customer", "required": false},
    {"key": "package",    "label": "Package",                      "kind": "text",     "required": false},
    {"key": "signal_dbm", "label": "Optical signal reading (dBm)", "kind": "number",   "required": false, "min": -60, "max": 10}
]'::jsonb
FROM activity_categories c
WHERE c.id = t.category_id AND c.name = 'Customer Installation' AND t.details_schema IS NULL;

UPDATE activity_types t
SET details_schema = '[
    {"key": "fault",  "label": "Fault encountered", "kind": "text", "required": false},
    {"key": "repair", "label": "Repair performed",  "kind": "text", "required": false}
]'::jsonb
FROM activity_categories c
WHERE c.id = t.category_id AND c.name = 'Network Maintenance' AND t.details_schema IS NULL;

-- ROLLBACK (by hand, only if wanted; extra-field answers already on reports
-- stay in activity_reports.details, only the field definitions go):
--   ALTER TABLE activity_types DROP COLUMN details_schema;
--   ALTER TABLE activity_categories DROP COLUMN repeat_visit_tracking;
