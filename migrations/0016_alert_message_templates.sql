-- Editable alert message text (Alerts > SMS Templates). One new table,
-- nothing existing is altered.
--
-- ONLY OVERRIDES LIVE HERE. Every alert kind's default wording is in code
-- (db/alert_templates.py's TEMPLATES); a row here replaces it, and deleting
-- the row is "Reset to default". An empty table means every message is
-- sent exactly as before this migration.
--
-- kind is one of the keys in TEMPLATES ('down', 'still_down',
-- 'battery_recommended', 'battery_none', 'recovered', 'flapping',
-- 'mass_down') — validated in the API, not with a CHECK constraint, so
-- adding a new alert kind later is a code change only.
--
-- Safe to deploy the code before running this: db/alert_templates.load_all()
-- falls back to the code defaults if this table doesn't exist yet, so alerts
-- keep going out — only the editor page errors until it's applied.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0016_alert_message_templates.sql

CREATE TABLE IF NOT EXISTS alert_message_templates (
    kind        text PRIMARY KEY,
    body        text NOT NULL,
    updated_by  integer REFERENCES users(id) ON DELETE SET NULL,
    updated_at  timestamp without time zone NOT NULL DEFAULT now()
);
