-- Network Health (owner's scope, 2026-11-16, with the ingest contract of
-- 2026-11-20: docs/network-health-ingest-contract.md). Readings arrive from
-- the Frankfurt server's shipper, not from the routers directly.
--
-- Its own domain: every table is nh_-prefixed, nothing existing is altered,
-- and no other domain reads these tables. Removing the feature = dropping
-- these six tables (and the code); nothing else depends on them.
--
--   - nh_readings:    one row per shipped record, unique on the shipper's
--                     `key` (it resends a batch whenever it doesn't get a
--                     2xx, so the same record can arrive twice). `ts` is when
--                     Frankfurt received the line. Kept 10 days
--                     (config.KEEP_READINGS_DAYS), deleted by the app.
--   - nh_feed:        one row: when the shipper last posted (an empty batch
--                     every minute is the heartbeat), for FEED_SILENT.
--   - nh_settings:    the two values an admin sets on the page: uplink
--                     capacity and when the provider was switched.
--   - nh_rule_state:  each rule's hysteresis counters (bad/good checks in a
--                     row) and its open incident. There is no background
--                     worker, so this is how "N checks to fire, N to clear"
--                     survives between ingests.
--   - nh_incidents:   every rule firing and clearing, kept.
--   - nh_alerts_sent: every SMS sent, failed or suppressed (and why). The
--                     message text is kept; phone numbers never are.
--
-- Timestamps here are timestamptz (the contract asks for it), unlike the
-- rest of the app's naive-UTC columns; the session runs in UTC either way.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0041_network_health.sql
-- Idempotent: re-running is safe.

BEGIN;

CREATE TABLE IF NOT EXISTS nh_readings (
    id          bigserial PRIMARY KEY,
    key         text NOT NULL UNIQUE,
    ts          timestamptz NOT NULL,
    kind        text NOT NULL,
    from_ip     text,
    raw         text,
    load        jsonb,          -- probe / inner
    probes      jsonb,          -- probe / inner: [{name, rtt_ms, jitter_ms, loss_pct}]
    link        jsonb,          -- link / linknew
    speed       jsonb,          -- speed / speednew
    received_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS nh_readings_kind_ts ON nh_readings (kind, ts DESC);
CREATE INDEX IF NOT EXISTS nh_readings_ts ON nh_readings (ts);

CREATE TABLE IF NOT EXISTS nh_feed (
    id           integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    last_post_at timestamptz,
    last_sent_at timestamptz,
    last_source  text,
    last_from_ip text
);
INSERT INTO nh_feed (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS nh_settings (
    key        text PRIMARY KEY,
    value      text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by integer REFERENCES users(id) ON DELETE SET NULL
);
INSERT INTO nh_settings (key, value) VALUES ('uplink_capacity_mbps', '350') ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS nh_incidents (
    id             serial PRIMARY KEY,
    device         text NOT NULL,
    rule           text NOT NULL,
    subject        text NOT NULL DEFAULT '',
    severity       text NOT NULL CHECK (severity IN ('AMBER', 'RED', 'OFFLINE')),
    started_at     timestamptz NOT NULL,
    ended_at       timestamptz,
    peak_value     double precision,
    last_value     double precision,
    facts          jsonb,
    is_flapping    boolean NOT NULL DEFAULT false,
    fire_sms_sent  boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS nh_incidents_started ON nh_incidents (started_at DESC);
CREATE INDEX IF NOT EXISTS nh_incidents_rule ON nh_incidents (device, rule, subject, ended_at DESC);

CREATE TABLE IF NOT EXISTS nh_rule_state (
    device          text NOT NULL,
    rule            text NOT NULL,
    subject         text NOT NULL DEFAULT '',
    bad_count       integer NOT NULL DEFAULT 0,
    good_count      integer NOT NULL DEFAULT 0,
    is_active       boolean NOT NULL DEFAULT false,
    severity        text,
    incident_id     integer REFERENCES nh_incidents(id) ON DELETE SET NULL,
    last_checked_at timestamptz,
    PRIMARY KEY (device, rule, subject)
);

CREATE TABLE IF NOT EXISTS nh_alerts_sent (
    id              serial PRIMARY KEY,
    created_at      timestamptz NOT NULL DEFAULT now(),
    kind            text NOT NULL CHECK (kind IN ('fire', 'clear', 'combined', 'cap')),
    device          text,
    rule            text,
    incident_ids    integer[] NOT NULL DEFAULT '{}',
    recipient_count integer NOT NULL DEFAULT 0,
    outcome         text NOT NULL CHECK (outcome IN ('pending', 'sent', 'failed', 'suppressed')),
    reason          text,
    message         text NOT NULL
);
CREATE INDEX IF NOT EXISTS nh_alerts_sent_created ON nh_alerts_sent (created_at DESC);

COMMIT;
