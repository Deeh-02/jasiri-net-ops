-- Technical Activity Log: technicians record the work they did; management
-- sees where effort goes. An activity REPORT written after the fact, not a
-- ticket: no assignment, status or SLA.
--
-- Five new tables, nothing existing is altered:
--   activity_categories          the 7 categories (seeded below)
--   activity_types               each type belongs to exactly one category
--   activity_reports             one row per report; the category is derived
--                                from the type, never stored on the report
--   activity_report_technicians  who was involved, the submitter included
--   activity_reference_counters  per-year counter behind TAL-2026-00001
--
-- Reuses what exists: users (submitter, technicians) and locations (the
-- site/area). "Other" location is a free-text fallback on the report.
--
-- Later additions (Materials, Photos) are NEW tables pointing at
-- activity_reports(id), so nothing here needs to change for them.
-- activity_reports.details is the Phase 2 JSON column for per-type extra
-- fields; it stays NULL until then.
--
-- Conventions kept from earlier migrations: enum-like values are plain text
-- with no CHECK (validated in code, see db/activity_logic.py):
--   activity_reports.outcome      'completed' | 'partially_completed' |
--                                 'inspection_only' | 'unable_to_resolve'
--   activity_reports.time_spent   NULL | 'under_30m' | '1h' | 'half_day' | 'full_day'
-- Timestamps are UTC wall-clock, like every other table. activity_date is a
-- plain date: the Nairobi day the work was done.
--
-- Categories and types are deactivated, never deleted, so old reports keep
-- their history (is_active). Until the Phase 2 admin screen exists, change
-- them here or with a seed script.
--
-- client_token: the browser makes one per form and re-sends it on a retry.
-- If the first attempt actually saved but the answer was lost on bad mobile
-- data, the retry returns that report instead of creating a duplicate.
--
-- Safe to deploy the code before running this: only the Technical Activity
-- Log pages error until it is applied.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0026_technical_activity_log.sql
-- Idempotent — re-running is safe (the seed adds only what is missing and
-- never renames or re-activates anything you have changed).

CREATE TABLE IF NOT EXISTS activity_categories (
    id          serial PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    sort_order  integer NOT NULL DEFAULT 0,
    is_active   boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS activity_types (
    id           serial PRIMARY KEY,
    category_id  integer NOT NULL REFERENCES activity_categories(id),
    name         text NOT NULL,
    sort_order   integer NOT NULL DEFAULT 0,
    is_active    boolean NOT NULL DEFAULT true,
    UNIQUE (category_id, name)
);

CREATE TABLE IF NOT EXISTS activity_reports (
    id               serial PRIMARY KEY,
    reference        text NOT NULL UNIQUE,
    client_token     text UNIQUE,
    activity_date    date NOT NULL,
    type_id          integer NOT NULL REFERENCES activity_types(id),
    location_id      integer REFERENCES locations(id),
    other_location   text,
    outcome          text NOT NULL DEFAULT 'completed',
    description      text,
    time_spent       text,
    remarks          text,
    details          jsonb,
    submitted_by     integer NOT NULL REFERENCES users(id),
    submitted_at     timestamp without time zone NOT NULL DEFAULT now(),
    last_edited_by   integer REFERENCES users(id),
    last_edited_at   timestamp without time zone,
    is_deleted       boolean NOT NULL DEFAULT false,
    deleted_by       integer REFERENCES users(id),
    deleted_at       timestamp without time zone
);

CREATE INDEX IF NOT EXISTS activity_reports_date_idx ON activity_reports (activity_date) WHERE NOT is_deleted;
CREATE INDEX IF NOT EXISTS activity_reports_type_idx ON activity_reports (type_id);
CREATE INDEX IF NOT EXISTS activity_reports_location_idx ON activity_reports (location_id);
CREATE INDEX IF NOT EXISTS activity_reports_submitter_idx ON activity_reports (submitted_by);

CREATE TABLE IF NOT EXISTS activity_report_technicians (
    report_id  integer NOT NULL REFERENCES activity_reports(id) ON DELETE CASCADE,
    user_id    integer NOT NULL REFERENCES users(id),
    PRIMARY KEY (report_id, user_id)
);

CREATE INDEX IF NOT EXISTS activity_report_technicians_user_idx ON activity_report_technicians (user_id);

CREATE TABLE IF NOT EXISTS activity_reference_counters (
    year         integer PRIMARY KEY,
    last_number  integer NOT NULL DEFAULT 0
);

ALTER TABLE activity_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_types ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_report_technicians ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_reference_counters ENABLE ROW LEVEL SECURITY;

-- ---- Seed: the 7 categories and the starting types (a starting set, not a
-- final list). sort_order keeps the order the scope lists them in. ----

INSERT INTO activity_categories (name, sort_order) VALUES
    ('Customer Installation',     1),
    ('Customer Support',          2),
    ('Network Maintenance',       3),
    ('Infrastructure Deployment', 4),
    ('Infrastructure Relocation', 5),
    ('Network Inspection',        6),
    ('Power & Equipment',         7)
ON CONFLICT (name) DO NOTHING;

INSERT INTO activity_types (category_id, name, sort_order)
SELECT c.id, t.name, t.sort_order
FROM (VALUES
    ('Customer Installation', 'New PPPoE installation',        1),
    ('Customer Installation', 'Hotspot customer installation', 2),
    ('Customer Installation', 'Router installation',           3),
    ('Customer Installation', 'Customer relocation',           4),

    ('Customer Support', 'No internet',                 1),
    ('Customer Support', 'Slow internet',               2),
    ('Customer Support', 'Router troubleshooting',      3),
    ('Customer Support', 'LOS investigation',           4),
    ('Customer Support', 'Power adapter replacement',   5),

    ('Network Maintenance', 'Fibre repair',               1),
    ('Network Maintenance', 'Cable re-termination',       2),
    ('Network Maintenance', 'Splicing',                   3),
    ('Network Maintenance', 'Router restoration',         4),
    ('Network Maintenance', 'Hotspot chain restoration',  5),
    ('Network Maintenance', 'Faulty equipment replacement', 6),

    ('Infrastructure Deployment', 'New fibre deployment',          1),
    ('Infrastructure Deployment', 'New hotspot chain installation', 2),
    ('Infrastructure Deployment', 'FAT installation',              3),
    ('Infrastructure Deployment', 'New network distribution',      4),
    ('Infrastructure Deployment', 'Cable installation',            5),

    ('Infrastructure Relocation', 'Router relocation',           1),
    ('Infrastructure Relocation', 'Cable relocation',            2),
    ('Infrastructure Relocation', 'Network rerouting',           3),
    ('Infrastructure Relocation', 'Pole-to-building relocation', 4),

    ('Network Inspection', 'Site inspection',               1),
    ('Network Inspection', 'Signal level inspection',       2),
    ('Network Inspection', 'Network condition assessment',  3),
    ('Network Inspection', 'Post-maintenance inspection',   4),

    ('Power & Equipment', 'Battery replacement',   1),
    ('Power & Equipment', 'Power supply repair',   2),
    ('Power & Equipment', 'Adapter replacement',   3),
    ('Power & Equipment', 'Power system inspection', 4)
) AS t(category, name, sort_order)
JOIN activity_categories c ON c.name = t.category
ON CONFLICT (category_id, name) DO NOTHING;

-- ROLLBACK (by hand, only if wanted; reports already filed are lost):
--   DROP TABLE activity_report_technicians, activity_reports,
--              activity_types, activity_categories, activity_reference_counters;
