-- Network page: the CCR2116's uplink, gateway and PPPoE sessions.
-- Five new tables, nothing existing is altered; dropping them (bottom of this
-- file) removes the feature and leaves everything else exactly as it was.
-- Approved by the owner 2026-11-04.
--
-- HOW DATA ARRIVES. A read-only script on the router POSTs two kinds of
-- message to /network/ingest: "live" every 15 s (uplink rates, gateway check,
-- CPU, PPPoE count) and "sessions" every 60 s (the PPPoE list, in parts).
-- Every timestamp below is the SERVER's receive time — the router sends no
-- clock, so a wrong router clock (see phase.md Correction 5) can't skew it.
--
--   - network_routers: one row per monitored router, keyed by the slug the
--     router puts in every message. Only 'ccr2116' in v1. ppp_router_id says
--     which customer list (migration 0017) names its PPPoE users.
--   - network_latest: the newest reading per router, plus the little state
--     Ops keeps BETWEEN readings, because the router can't: on this fleet
--     RouterOS globals did not survive between scheduled runs (seen on the
--     CCR2004, 2026-09-21), so "two failed gateway checks in a row", "has
--     this gateway ever answered ping" and "uptime last time" live here.
--   - network_session_parts: a session list arrives in parts; parts wait
--     here until the whole batch is in, then replace the list in one go.
--     An incomplete batch older than 2 minutes is thrown away.
--   - network_minutes: 1-minute rollups (sums + maxes, so averages are exact
--     however many 15-second samples landed). No raw samples are kept.
--     Purged after 30 days.
--   - network_events: outages, restarts and link changes, open (ended_at
--     NULL) or closed. source is 'ops' (worked out here from the readings)
--     or 'router' (reported by the router itself, deduplicated on
--     router_event_id). Purged after 90 days.
--
-- RLS is switched on for every table: Ops connects as the table owner, which
-- RLS doesn't restrict, while Supabase's public API (anon key) gets nothing.
-- See ARCHITECTURE.md "Open: Supabase security advisory".
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0022_network_monitor.sql
-- Idempotent — re-running is safe.

CREATE TABLE IF NOT EXISTS network_routers (
    slug                  text PRIMARY KEY,
    name                  text NOT NULL,
    is_enabled            boolean NOT NULL DEFAULT true,
    uplink_capacity_mbps  numeric(8,2) NOT NULL DEFAULT 350,
    ppp_router_id         integer REFERENCES ppp_routers(id) ON DELETE SET NULL,
    created_at            timestamp without time zone NOT NULL DEFAULT now()
);

INSERT INTO network_routers (slug, name, uplink_capacity_mbps, ppp_router_id)
SELECT 'ccr2116', 'CCR2116', 350, (SELECT id FROM ppp_routers WHERE name = 'CCR2116 PPPoE clients')
ON CONFLICT (slug) DO NOTHING;

CREATE TABLE IF NOT EXISTS network_latest (
    router                text PRIMARY KEY REFERENCES network_routers(slug) ON DELETE CASCADE,
    live                  jsonb,
    live_received_at      timestamp without time zone,
    last_uptime_s         bigint,
    gateway_fail_streak   integer NOT NULL DEFAULT 0,
    gateway_seen_alive    boolean NOT NULL DEFAULT false,
    counters_rose_at      timestamp without time zone,
    sessions              jsonb,
    sessions_received_at  timestamp without time zone
);

CREATE TABLE IF NOT EXISTS network_session_parts (
    router       text NOT NULL REFERENCES network_routers(slug) ON DELETE CASCADE,
    batch_id     text NOT NULL,
    part         integer NOT NULL,
    parts        integer NOT NULL,
    rows         jsonb NOT NULL,
    received_at  timestamp without time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (router, batch_id, part)
);

CREATE TABLE IF NOT EXISTS network_minutes (
    router           text NOT NULL REFERENCES network_routers(slug) ON DELETE CASCADE,
    minute           timestamp without time zone NOT NULL,   -- UTC, truncated to the minute
    samples          integer NOT NULL DEFAULT 0,
    rx_bps_sum       numeric(20,0) NOT NULL DEFAULT 0,
    rx_bps_max       bigint NOT NULL DEFAULT 0,
    tx_bps_sum       numeric(20,0) NOT NULL DEFAULT 0,
    tx_bps_max       bigint NOT NULL DEFAULT 0,
    pppoe_online     integer,
    cpu_pct_sum      numeric(10,2) NOT NULL DEFAULT 0,
    cpu_samples      integer NOT NULL DEFAULT 0,
    gw_rtt_ms_sum    numeric(12,2) NOT NULL DEFAULT 0,
    gw_rtt_samples   integer NOT NULL DEFAULT 0,
    gw_loss_pct_max  numeric(5,2),
    PRIMARY KEY (router, minute)
);

CREATE TABLE IF NOT EXISTS network_events (
    id               bigserial PRIMARY KEY,
    router           text NOT NULL REFERENCES network_routers(slug) ON DELETE CASCADE,
    type             text NOT NULL,      -- gateway_down | uplink_down | router_restarted | not_reporting
    started_at       timestamp without time zone NOT NULL,
    ended_at         timestamp without time zone,
    source           text NOT NULL,      -- 'ops' | 'router'
    router_event_id  text,
    details          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamp without time zone NOT NULL DEFAULT now(),
    UNIQUE (router, router_event_id)
);

CREATE INDEX IF NOT EXISTS network_events_router_started_idx ON network_events (router, started_at DESC);

ALTER TABLE network_routers ENABLE ROW LEVEL SECURITY;
ALTER TABLE network_latest ENABLE ROW LEVEL SECURITY;
ALTER TABLE network_session_parts ENABLE ROW LEVEL SECURITY;
ALTER TABLE network_minutes ENABLE ROW LEVEL SECURITY;
ALTER TABLE network_events ENABLE ROW LEVEL SECURITY;

-- ROLLBACK (removes the feature's data entirely; run by hand only if wanted):
--   DROP TABLE network_events, network_minutes, network_session_parts, network_latest, network_routers;
