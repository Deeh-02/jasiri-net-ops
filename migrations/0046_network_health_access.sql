-- Network Health: "Access network" (owner, 2026-11-22): the OLT's PON
-- ports and ONTs, and the PPPoE sessions on the CCR2116 and CCR2004.
--
-- New record kinds from the Frankfurt shipper, every 5 minutes:
--   pon   one per PON port with ONTs: {pon, online, offline}
--   ont   one per ONT (~62): {pon, ont_id, status, rx_dbm, olt_rx_dbm,
--         distance_m, last_down_cause, last_down_text, desc}
--   ppp   one per router: {dev, count, names}
--
-- 1. Three nullable columns on nh_readings, like every other kind: same
--    insert, same key. Existing rows get NULL. Raw `ont` rows (~18,000 a
--    day) are deleted after 3 days by the app (config.KEEP_ONT_DAYS); `pon`
--    and `ppp` after 10 like the rest.
-- 2. Five small tables of the feature's own (nh_-prefixed, nothing else
--    reads them):
--      nh_onts              one row per ONT (pon + ont_id): its latest
--                           reading, since when it has that status, whether
--                           it was ever online (stale registrations).
--                           ~62 rows. Events are worked out against it.
--      nh_ont_hourly        per ONT per hour: sums and counts of rx_dbm and
--                           olt_rx_dbm. The 7-day light sparklines and the
--                           24 h vs 7 day degradation check read this, not
--                           the raw rows. ~1,500 rows a day, kept 30 days.
--      nh_ppp_sessions      one row per router + session name: present or
--                           missing since when. ~60 rows.
--      nh_access_events     ONT down / back, PPPoE session dropped / back.
--                           A few hundred a day at most, kept 90 days. An
--                           ONT down waits ungrouped until its poll is
--                           judged with the rest of its PON.
--      nh_ont_ppp_matches   the page's manual ONT <-> PPPoE matches, for
--                           pairs the names don't match on their own. An
--                           admin sets them; ppp_name NULL = "this ONT has
--                           no PPPoE session, don't cross-check it".
--
-- Run once:  psql "$DATABASE_URL" -f migrations/0046_network_health_access.sql
-- Idempotent: re-running is safe.
-- Undo:
--   DROP TABLE nh_ont_ppp_matches, nh_access_events, nh_ppp_sessions, nh_ont_hourly, nh_onts;
--   ALTER TABLE nh_readings DROP COLUMN pon, DROP COLUMN ont, DROP COLUMN ppp;

BEGIN;

ALTER TABLE nh_readings ADD COLUMN IF NOT EXISTS pon jsonb;   -- pon: {pon, online, offline}
ALTER TABLE nh_readings ADD COLUMN IF NOT EXISTS ont jsonb;   -- ont: {pon, ont_id, status, rx_dbm, olt_rx_dbm, ...}
ALTER TABLE nh_readings ADD COLUMN IF NOT EXISTS ppp jsonb;   -- ppp: {dev, count, names}

CREATE TABLE IF NOT EXISTS nh_onts (
    pon             text NOT NULL,
    ont_id          integer NOT NULL,
    descr           text,                       -- the name set on the OLT (JS code and/or site)
    status          text NOT NULL CHECK (status IN ('online', 'offline')),
    rx_dbm          double precision,           -- at the latest reading (null while offline)
    olt_rx_dbm      double precision,
    distance_m      double precision,           -- last known
    last_down_cause integer,
    last_down_text  text,
    first_seen_at   timestamptz NOT NULL,
    last_seen_at    timestamptz NOT NULL,
    status_since    timestamptz NOT NULL,       -- the first reading with the current status
    ever_online     boolean NOT NULL DEFAULT false,
    last_online_at  timestamptz,
    PRIMARY KEY (pon, ont_id)
);

CREATE TABLE IF NOT EXISTS nh_ont_hourly (
    pon        text NOT NULL,
    ont_id     integer NOT NULL,
    hour       timestamptz NOT NULL,
    rx_sum     double precision NOT NULL DEFAULT 0,
    rx_n       integer NOT NULL DEFAULT 0,
    olt_rx_sum double precision NOT NULL DEFAULT 0,
    olt_rx_n   integer NOT NULL DEFAULT 0,
    PRIMARY KEY (pon, ont_id, hour)
);
CREATE INDEX IF NOT EXISTS nh_ont_hourly_hour ON nh_ont_hourly (hour);

CREATE TABLE IF NOT EXISTS nh_ppp_sessions (
    dev           text NOT NULL,
    name          text NOT NULL,
    present       boolean NOT NULL,
    first_seen_at timestamptz NOT NULL,
    last_seen_at  timestamptz NOT NULL,         -- the last reading it was in
    missing_since timestamptz,                  -- the first reading it was missing from
    PRIMARY KEY (dev, name)
);

CREATE TABLE IF NOT EXISTS nh_access_events (
    id      bigserial PRIMARY KEY,
    at      timestamptz NOT NULL,
    kind    text NOT NULL CHECK (kind IN ('ont_down', 'ont_back', 'ppp_dropped', 'ppp_back')),
    pon     text,
    ont_id  integer,
    dev     text,
    name    text,
    detail  jsonb NOT NULL DEFAULT '{}',
    grouped boolean NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS nh_access_events_at ON nh_access_events (at DESC);
CREATE INDEX IF NOT EXISTS nh_access_events_waiting ON nh_access_events (at) WHERE NOT grouped;

CREATE TABLE IF NOT EXISTS nh_ont_ppp_matches (
    pon        text NOT NULL,
    ont_id     integer NOT NULL,
    ppp_name   text,                            -- NULL: no PPPoE session (not cross-checked)
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by integer REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (pon, ont_id)
);

COMMIT;
