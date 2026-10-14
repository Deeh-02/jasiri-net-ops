-- Alert sending window (Alerts > SMS Templates > "When alerts are sent").
-- One new table holding a single row, nothing existing is altered.
--
-- WHAT IT DOES. When enabled, site alerts (down, still down, back online,
-- flapping) are only SENT between send_window_start and send_window_end,
-- East Africa Time. Outside the window nothing is sent but nothing is
-- lost either: the site's episode keeps running, and the next reminder that
-- came due overnight goes out once, when the window opens.
--
-- Separate from monitored_sites.quiet_hours_*, which is per site and set in
-- the database; both apply, and either one muting a site mutes its alerts.
--
-- No row, or send_window_enabled = false, means "alert at any hour" — i.e.
-- exactly how alerting behaved before this migration. Safe to deploy the
-- code first: db/alert_settings.get() falls back to that on a missing table.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0018_alert_send_window.sql

CREATE TABLE IF NOT EXISTS alert_settings (
    id                  integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    send_window_enabled boolean NOT NULL DEFAULT false,
    send_window_start   time without time zone NOT NULL DEFAULT '08:00',
    send_window_end     time without time zone NOT NULL DEFAULT '18:00',
    updated_by          integer REFERENCES users(id) ON DELETE SET NULL,
    updated_at          timestamp without time zone NOT NULL DEFAULT now()
);

INSERT INTO alert_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
