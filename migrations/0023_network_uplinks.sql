-- Network page, several uplinks: each internet link the router uses (Airtel,
-- Veenet, any future one) is discovered from the router's default routes and
-- tracked on its own. Two new tables, nothing existing is altered; dropping
-- them (bottom of this file) returns the Network page to the single-link
-- build. Approved by the owner 2026-11-04.
--
--   - network_uplinks: one row per link, keyed by the interface the router
--     measures it on (the link's identity). Holds the link's name (the route
--     comment, e.g. "airtel Primary"), its gateway, its newest reading, and
--     the memory Ops keeps for it between readings (failed-ping streak, "has
--     this gateway ever answered ping", when error counters last rose) —
--     the router remembers nothing, see 0022.
--     CAPACITY: capacity_mbps is the contracted speed. The router can't know
--     it, so a link first seen starts at its port speed with
--     capacity_source = 'auto' (shown as an estimate, and no "busy" warnings
--     are raised from it); an admin who sets the real figure makes it
--     'admin'. A link that appears later needs no change here.
--   - network_uplink_minutes: the 1-minute rollups of network_minutes, per
--     link (sums + maxes). Purged after 30 days with the others.
--
-- The existing 'ccr2116' link keeps its 350 Mbps, copied from
-- network_routers.uplink_capacity_mbps as an admin-set figure.
--
-- RLS on, as for every network_* table (see 0022 and ARCHITECTURE.md
-- "Open: Supabase security advisory").
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0023_network_uplinks.sql
-- Idempotent — re-running is safe.

CREATE TABLE IF NOT EXISTS network_uplinks (
    router               text NOT NULL REFERENCES network_routers(slug) ON DELETE CASCADE,
    interface            text NOT NULL,
    label                text,
    gateway_address      text,
    link_rate_mbps       numeric(10,2),
    capacity_mbps        numeric(10,2),
    capacity_source      text NOT NULL DEFAULT 'auto' CHECK (capacity_source IN ('auto', 'admin')),
    active               boolean,
    reading              jsonb,
    reading_received_at  timestamp without time zone,
    gateway_fail_streak  integer NOT NULL DEFAULT 0,
    gateway_seen_alive   boolean NOT NULL DEFAULT false,
    counters_rose_at     timestamp without time zone,
    first_seen_at        timestamp without time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (router, interface)
);

CREATE TABLE IF NOT EXISTS network_uplink_minutes (
    router           text NOT NULL,
    interface        text NOT NULL,
    minute           timestamp without time zone NOT NULL,   -- UTC, truncated to the minute
    samples          integer NOT NULL DEFAULT 0,
    rx_bps_sum       numeric(20,0) NOT NULL DEFAULT 0,
    rx_bps_max       bigint NOT NULL DEFAULT 0,
    tx_bps_sum       numeric(20,0) NOT NULL DEFAULT 0,
    tx_bps_max       bigint NOT NULL DEFAULT 0,
    gw_rtt_ms_sum    numeric(12,2) NOT NULL DEFAULT 0,
    gw_rtt_samples   integer NOT NULL DEFAULT 0,
    gw_loss_pct_max  numeric(5,2),
    PRIMARY KEY (router, interface, minute),
    FOREIGN KEY (router, interface) REFERENCES network_uplinks(router, interface) ON DELETE CASCADE
);

-- The Airtel link as it was before this migration.
INSERT INTO network_uplinks (router, interface, capacity_mbps, capacity_source)
SELECT slug, 'sfp-sfpplus1', uplink_capacity_mbps, 'admin'
FROM network_routers WHERE slug = 'ccr2116'
ON CONFLICT (router, interface) DO NOTHING;

ALTER TABLE network_uplinks ENABLE ROW LEVEL SECURITY;
ALTER TABLE network_uplink_minutes ENABLE ROW LEVEL SECURITY;

-- ROLLBACK (by hand, only if wanted; the page falls back to the single-link build):
--   DROP TABLE network_uplink_minutes, network_uplinks;
