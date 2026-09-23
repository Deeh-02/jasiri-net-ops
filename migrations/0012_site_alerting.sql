-- Phase 4.8 — Alerting. Four new tables. Nothing existing is altered — same
-- additive shape as every migration since 0004. Dropping all four leaves
-- 4.1-4.7 and Phases 0-3 completely unaffected.
--
-- WHAT THIS BUILDS ON. site_status_log (0006) already records every state
-- transition; site_acknowledgements (0011) already lets a person mute a
-- known-bad site. This migration adds the layer PHASES.md 4.8 calls for on
-- top of both: debounced down-alerts (don't fire the instant a site drops —
-- only once it's STAYED down), flapping detection (a site bouncing all
-- night is one alert, not forty), and three delivery channels (in-app,
-- SMS, WhatsApp) rather than only ever being visible on the Status page.
--
-- ISOLATION INVARIANT, same as 0006's header: `users` gains ZERO new
-- columns. A user's per-channel opt-in lives in
-- monitoring_alert_subscriptions (this file), which READS users.id by FK
-- and nothing else — same pattern monitored_sites already uses against
-- `locations`. `notifications` is deliberately domain-agnostic (see its own
-- comment below) rather than named `monitoring_notifications` — but it is
-- still monitoring's table to drop if monitoring itself is ever dropped,
-- since nothing else writes to it yet.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0012_site_alerting.sql
-- Additive and IF NOT EXISTS throughout, so re-running is safe.


-- Generic in-app notification inbox, one row per (user, event). The bell
-- icon in the topbar has been a placeholder with no backend since Phase 0
-- (see the comment this replaces in static/js/common.js) — this is that
-- backend. Deliberately NOT named for monitoring: Phase 4.8 is its first
-- writer, not its only intended one — a future phase (movements, tickets)
-- can reuse this table rather than inventing its own bell.
CREATE TABLE IF NOT EXISTS notifications (
    id serial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type text NOT NULL,              -- 'site_down' / 'site_recovered' / 'site_flapping' / ...
    title text NOT NULL,
    body text NOT NULL,
    link text,                       -- frontend hash-route to open on click, e.g. 'monitoring/sites/12'
    read_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_unread
    ON notifications(user_id, created_at DESC) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_notifications_user_created
    ON notifications(user_id, created_at DESC);


-- One row per user: their opt-in per delivery channel for monitoring
-- alerts. A user with no row here gets the defaults read in
-- db/monitoring_alerts.py (in-app on, SMS/WhatsApp off) — nobody is texted
-- or WhatsApped without explicitly turning it on in Settings, even if they
-- hold the sites:receive_alerts permission and have a phone on file.
CREATE TABLE IF NOT EXISTS monitoring_alert_subscriptions (
    id serial PRIMARY KEY,
    user_id integer NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    in_app_enabled boolean NOT NULL DEFAULT true,
    sms_enabled boolean NOT NULL DEFAULT false,
    whatsapp_enabled boolean NOT NULL DEFAULT false,
    updated_at timestamp without time zone NOT NULL DEFAULT now()
);


-- One row per continuous down-or-flapping episode at a site. Opened the
-- moment site_status_log logs an 'offline' transition with no episode
-- already open for that site; closed the moment the site is next seen
-- 'online'. site_status_log stays a pure transition record (0006's design);
-- this is what makes debounce, "alert once, not every 60s poll", and
-- flapping suppression possible without touching that table's shape.
CREATE TABLE IF NOT EXISTS site_alert_episodes (
    id serial PRIMARY KEY,
    monitored_site_id integer NOT NULL REFERENCES monitored_sites(id) ON DELETE RESTRICT,
    opened_at timestamp without time zone NOT NULL,    -- = the offline transition's received_at
    sessions_at_drop integer,                          -- last known headcount before the drop
    state text NOT NULL DEFAULT 'down',                -- 'down' / 'flapping'
    flap_count integer NOT NULL DEFAULT 0,             -- transitions seen while this episode is open
    down_alert_sent_at timestamp without time zone,    -- NULL until the debounce threshold sends one
    flapping_alert_sent_at timestamp without time zone,
    mass_event boolean NOT NULL DEFAULT false,         -- batched with others as one probable reboot/blip
    resolved_at timestamp without time zone,
    recovery_alert_sent_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL DEFAULT now()
);

-- At most one open episode per site — same convention as
-- uq_site_ack_open on site_acknowledgements.
CREATE UNIQUE INDEX IF NOT EXISTS uq_site_alert_episode_open
    ON site_alert_episodes(monitored_site_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_site_alert_episodes_site
    ON site_alert_episodes(monitored_site_id, opened_at DESC);


-- Audit log of every alert actually attempted, one row per (episode,
-- channel, recipient). This is what the per-site-per-hour SMS rate limit
-- reads (PHASES.md 4.8: "rate-limited per site per hour"), and what proves
-- what actually went out if an SMS bill ever needs reconciling.
CREATE TABLE IF NOT EXISTS alert_deliveries (
    id serial PRIMARY KEY,
    episode_id integer REFERENCES site_alert_episodes(id) ON DELETE CASCADE,
    monitored_site_id integer NOT NULL REFERENCES monitored_sites(id) ON DELETE RESTRICT,
    user_id integer REFERENCES users(id) ON DELETE SET NULL,
    channel text NOT NULL,        -- 'in_app' / 'sms' / 'whatsapp'
    kind text NOT NULL,           -- 'down' / 'recovered' / 'flapping'
    recipient text,               -- phone number for sms/whatsapp; NULL for in_app
    status text NOT NULL,         -- 'sent' / 'failed' / 'not_configured' / 'rate_limited' / 'skipped_quiet_hours'
    provider_response jsonb,
    sent_at timestamp without time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_alert_deliveries_site_channel_time
    ON alert_deliveries(monitored_site_id, channel, sent_at DESC);
