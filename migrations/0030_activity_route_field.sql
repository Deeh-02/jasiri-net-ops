-- Technical Activity Log: an optional "Route" question on the cable jobs, for
-- work that runs from one place to another (W - X - Y - Z), per the owner's
-- call of 2026-11-08. Location stays the main area or the starting point and
-- is what the Overview counts; Route is typed text, filled only when the work
-- covered more than one place. Data only: no table or column changes, and no
-- code is needed (it is an ordinary "text" field).
--
-- Types that get it, as the first question (their other questions are kept):
--   Network Maintenance        Fibre repair, Splicing, Cable re-tensioning
--   Infrastructure Deployment  New fibre deployment, Cable installation
--
-- Safe to re-run and it never overwrites an admin's work: it only adds the
-- field to a type that has no "route" question yet. Reports already saved are
-- not touched.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0030_activity_route_field.sql

BEGIN;

UPDATE activity_types t
SET details_schema = jsonb_build_array(jsonb_build_object(
        'key', 'route',
        'label', 'Route (if more than one place)',
        'kind', 'text',
        'required', false
    )) || COALESCE(t.details_schema, '[]'::jsonb)
FROM activity_categories c
WHERE c.id = t.category_id
  AND (c.name, t.name) IN (
        ('Network Maintenance', 'Fibre repair'),
        ('Network Maintenance', 'Splicing'),
        ('Network Maintenance', 'Cable re-tensioning'),
        ('Infrastructure Deployment', 'New fibre deployment'),
        ('Infrastructure Deployment', 'Cable installation')
      )
  AND NOT COALESCE(t.details_schema, '[]'::jsonb) @> '[{"key": "route"}]'::jsonb;

COMMIT;

-- ROLLBACK: remove the Route question on the Manage Activity Types screen.
-- Answers already saved on reports stay on those reports.
