-- Network page "Live" chart: the raw 15-second readings, one row per link per
-- reading, kept for about an hour (purged hourly from ingest, so never more
-- than two hours old). The 1-minute rollups (0022/0023) stay the source for
-- every longer range; this only feeds the last ~10 minutes, point by point.
-- About 4 rows a minute per link: a few hundred rows at any time.
-- One new table; nothing existing is altered. Approved by the owner 2026-11-04.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0025_network_samples.sql
-- Idempotent — re-running is safe.

CREATE TABLE IF NOT EXISTS network_samples (
    router       text NOT NULL REFERENCES network_routers(slug) ON DELETE CASCADE,
    interface    text NOT NULL,
    received_at  timestamp without time zone NOT NULL,   -- UTC, the server's receive time
    rx_bps       bigint NOT NULL,
    tx_bps       bigint NOT NULL,
    PRIMARY KEY (router, interface, received_at)
);
CREATE INDEX IF NOT EXISTS network_samples_router_time_idx ON network_samples (router, received_at);

ALTER TABLE network_samples ENABLE ROW LEVEL SECURITY;

-- ROLLBACK (by hand, only if wanted; the Live button then has nothing to show):
--   DROP TABLE network_samples;
