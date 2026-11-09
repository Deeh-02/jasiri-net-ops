-- Revenue reporting, step 3 — editable thresholds. One new table, nothing
-- existing is altered.
--
-- ONLY OVERRIDES LIVE HERE, same shape as alert_message_templates (0016):
-- every key's default is in code (db/revenue_config.py's DEFAULTS), a row
-- here replaces it, and deleting the row puts the default back. An empty
-- table means the report uses exactly the defaults in the spec. A default
-- improved in code therefore reaches everyone who never changed that key.
--
-- Safe to deploy the code first: db/revenue_config.load() falls back to the
-- defaults if this table doesn't exist yet.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0020_revenue_report_config.sql

CREATE TABLE IF NOT EXISTS revenue_report_config (
    key         text PRIMARY KEY,
    value       numeric NOT NULL,
    updated_by  integer REFERENCES users(id) ON DELETE SET NULL,
    updated_at  timestamp without time zone NOT NULL DEFAULT now()
);
