-- Phase 4.8 follow-up — flip the alert-channel default from opt-in to
-- opt-out. Column defaults only, nothing existing is altered or dropped.
--
-- WHY THIS TABLE-LEVEL DEFAULT DOESN'T ACTUALLY CHANGE ANYTHING BY ITSELF.
-- No code path does a bare INSERT into monitoring_alert_subscriptions that
-- relies on these column defaults — a row only ever gets created by
-- set_subscription() or admin_set_channels() (db/monitoring_alerts.py),
-- both of which always supply explicit values for every column. The real
-- behavioral default — what a user with NO row at all gets treated as —
-- lives in Python (get_subscription()'s no-row return value and
-- load_recipients()'s COALESCE), not here. This migration exists only so
-- the column definition doesn't silently disagree with that Python
-- default (DEFAULT false while the app's own default is true would be a
-- landmine for the next person reading 0012 without reading the code).
--
-- Decided 2026-09-23: applies retroactively to every current no-row user,
-- not just future ones — reach over SMS cost-control, an explicit choice,
-- not a side effect.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0015_alert_channels_default_on.sql

ALTER TABLE monitoring_alert_subscriptions
    ALTER COLUMN sms_enabled SET DEFAULT true,
    ALTER COLUMN whatsapp_enabled SET DEFAULT true;
