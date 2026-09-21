-- Phase 4 — Network Monitoring (Site Status). Seven new tables for the
-- monitoring domain, fed by a read-only heartbeat from the MikroTik CCR2004.
-- Nothing here touches an existing table or column — this is additive only,
-- same shape as migration 0004.
--
-- The isolation invariant from PHASES.md is load-bearing here: monitoring
-- READS `locations` by id and nothing else. `locations`, `batteries`,
-- `movements`, `inventory`, `users` and `roles` gain zero columns and zero
-- awareness of monitoring. In particular there is deliberately NO `vlan_id`
-- on `locations` — dropping every table in this file must leave Phases 0-3
-- passing unchanged, and that is only true while the FK points this way.
--
-- Design reasoning (long version in PHASES.md's Phase 4 brief):
--
--   - NOTHING ABOUT THE FLEET IS HARDCODED. The owner has flagged that VLAN
--     numbering and naming will be reorganised at some point. So: `vlan_id`
--     is an ordinary mutable column, NOT a primary key and NOT part of any
--     other table's key. Every other table points at monitored_sites(id), a
--     surrogate serial that survives any renumbering. Re-VLANing the whole
--     network is then an UPDATE of one column in one table, with all history
--     still attached — not a migration and not a data loss event.
--
--   - Enum-ish columns (`liveness_source`, `state`, `source`, `event_type`,
--     `attribution`, `granularity`) are plain text with NO CHECK constraint,
--     validated in routers/monitoring.py instead. Same convention as
--     inventory_categories.tracking_type / custody_type and
--     battery_movements.reason — a new allowed value never needs a migration.
--
--   - monitored_sites.location_id is NULLABLE. PHASES.md's isolation note
--     says it outright: "a location may be unmonitored; a VLAN may exist
--     before anyone maps it. Separate lifecycles." Concretely, four known
--     VLANs (20 "Street 10", 55 "Monitoring", 77 "Cyber", 99 "Street 9")
--     are real sites with no `locations` row yet. A nullable FK lets them be
--     monitored today and linked whenever someone creates those rows —
--     rather than forcing this migration to invent rows in the battery
--     domain's table, which would make them appear in Check Sites.
--
--   - vlan_id and pppoe_username are both NULLABLE UNIQUE. Correction 3 in
--     PHASES.md: not every site has a PPPoE uplink, and at least one site
--     appears to have no hotspot VLAN at all. Postgres permits multiple
--     NULLs under a UNIQUE constraint, so this is exactly right. There is
--     deliberately no CHECK requiring one of the two — a placeholder row for
--     a site that is planned but not yet provisioned is a legitimate state.
--
--   - is_active is a soft delete, same convention as `locations`. Retiring a
--     site keeps its status/revenue history intact and computable.
--
--   - TIMESTAMPS. Per the owner's 2026-09-21 decision, the router's clock is
--     NOT being corrected on the router — it stays on Indian/Mauritius
--     (UTC+4) while the network is in Nairobi (UTC+3). So every table that
--     records an observation stores BOTH `router_ts` (raw, exactly as the
--     router reported it, never mutated) and `received_at` (when Ops saw
--     it). All uptime and all day boundaries compute from `received_at`.
--     ingest_snapshots additionally stores `router_gmt_offset_minutes`, the
--     offset the router itself transmitted, so normalisation is DATA rather
--     than a -60 constant compiled into Ops. If anyone ever does fix the
--     router clock, the offset simply starts arriving as +180 and nothing
--     silently corrupts.
--
--     Everything here is `timestamp without time zone` holding UTC, matching
--     the rest of the schema — db/connection.py pins every session to UTC
--     and exposes EAT as a fixed +03:00 for display/aggregation.
--
--   - revenue_events carries `price_kes` as a SNAPSHOT of the price at the
--     moment of sale rather than joining hotspot_packages at read time.
--     Prices live in the router's profile NAMES today ("Quick Surf10" = 10),
--     and the billing vendor can rename or reprice a profile at any time.
--     Joining for price would silently rewrite historical revenue when that
--     happens; a snapshot cannot.
--
--   - revenue_events is UNIQUE on (hotspot_username, expiry_seen). This is
--     the idempotency guard for 4.2's "accepts retries" requirement: the
--     same user with the same expiry is the same sale no matter how many
--     times the heartbeat re-reports it, so a retried or duplicated payload
--     cannot double-count revenue.
--
-- RETENTION. Confirmed 2026-09-21: both Render and Supabase are on the FREE
-- tier, so the Supabase cap is 500 MB total and retention is a hard
-- requirement rather than good hygiene. Measured row counts:
--
--   ingest_snapshots      1,440/day =  525,600/yr  =  58-100 MB/yr
--   site_session_counts   6,048/day (21 sites x 5-min) = 12.7 MB per 14 days
--   site_status_log       transitions only — negligible
--   revenue_events        a few hundred/day at most — negligible
--
-- ingest_snapshots is therefore the dominant consumer BY FAR, and it is the
-- one table the brief originally said to keep forever. Left raw it would eat
-- the entire free tier in roughly three years on its own. So it carries the
-- same `granularity` column as site_session_counts and is downsampled the
-- same way: kept raw for 30 days, then rolled up to one row per hour
-- (43,200 raw rows at any time, plus 8,760 hourly rows per year — trivial).
-- The rollup still proves "we were watching", which is the only thing the
-- old rows were being kept for.
--
-- The purge/rollup jobs themselves are Ops-side code, not schema:
--   site_status_log      transitions only, keep forever
--   ingest_snapshots     raw 30 days -> hourly, keep forever
--   site_session_counts  raw 14 days -> hourly, keep forever
--   revenue_events       keep forever (it is the money record)
--
-- Note also that Render's free tier bills 750 instance-hours/month across
-- the whole account. A 60s heartbeat keeps the service awake 24/7, which is
-- ~730 hours — nearly the entire monthly allowance. That is the correct
-- trade (a sleeping service cold-starts in 30-60s and the router has no
-- retry, so every wake would lose readings), but it means there is no room
-- for a second free Render service alongside this one.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0006_monitoring_core_tables.sql
-- (or, for local dev with no DATABASE_URL set, against the local
-- battery_tracker DB directly: psql -U postgres -d battery_tracker -f ...)
--
-- All statements are idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING) so
-- re-running this is safe.


-- One row per monitored VLAN/site. This is the hand-maintained mapping from
-- PHASES.md 4.0c — the table the whole phase is blocked on. It is ordinary
-- CRUD data: rows are added, edited and soft-deleted from the UI, never
-- redeployed.
CREATE TABLE IF NOT EXISTS monitored_sites (
    id serial PRIMARY KEY,
    -- Nullable on purpose — see file header. RESTRICT rather than CASCADE:
    -- deleting a location out from under live monitoring history should
    -- fail loudly, not silently delete the history with it.
    location_id integer REFERENCES locations(id) ON DELETE RESTRICT,
    -- Mutable. Not a key. The fleet will be renumbered one day.
    vlan_id integer UNIQUE,
    gateway_cidr text,                  -- e.g. 10.50.35.1/24, informational
    pppoe_username text UNIQUE,         -- NULL where the site has no PPPoE uplink
    ap_ip text,                         -- for liveness_source='ping'
    -- One of: pppoe / ping / activity. Plain text (see file header) —
    -- validated in routers/monitoring.py. 'activity' is the safe default:
    -- it can only ever report online-or-unknown, never a false 'down'.
    liveness_source text NOT NULL DEFAULT 'activity',
    -- Local (EAT) wall-clock, not UTC — these are "when is this site
    -- expected to be dark" in human terms, e.g. a solar site overnight.
    -- Both NULL means no quiet hours.
    quiet_hours_start time without time zone,
    quiet_hours_end time without time zone,
    notes text,
    is_active boolean NOT NULL DEFAULT true,  -- soft delete, same convention as `locations`
    created_at timestamp without time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_monitored_sites_location ON monitored_sites(location_id);


-- One row per FLEET snapshot (not per site). Makes "unknown" time
-- computable: a gap in seq, or in received_at, is provably time during
-- which Ops was not watching, as opposed to time when every site was up.
CREATE TABLE IF NOT EXISTS ingest_snapshots (
    id serial PRIMARY KEY,
    seq bigint,                             -- router-supplied sequence; gaps are detectable
    router_ts timestamp without time zone,  -- RAW as reported — currently +1h. Never mutated.
    -- The offset the router itself reported, in minutes (+240 today for
    -- Indian/Mauritius, +180 if the clock is ever corrected). Normalisation
    -- is data, not a constant in Ops — see file header.
    router_gmt_offset_minutes integer,
    router_ts_utc timestamp without time zone,  -- derived on ingest from the two above
    received_at timestamp without time zone NOT NULL DEFAULT now(),
    sites_reporting integer,
    payload_hash text,                      -- idempotency for retried POSTs
    -- raw / hourly. Free-tier retention — see file header. Rows arrive 'raw'
    -- and are rolled up to one 'hourly' row after 30 days, at which point the
    -- raw rows for that window are deleted. Without this, this table alone
    -- consumes the entire 500 MB Supabase free tier in about three years.
    granularity text NOT NULL DEFAULT 'raw',
    created_at timestamp without time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingest_snapshots_received ON ingest_snapshots(received_at);
CREATE INDEX IF NOT EXISTS idx_ingest_snapshots_seq ON ingest_snapshots(seq);


-- TRANSITIONS ONLY — one row when a site CHANGES state, never one row per
-- poll. This is what keeps the table small enough to keep forever.
CREATE TABLE IF NOT EXISTS site_status_log (
    id serial PRIMARY KEY,
    -- Points at monitored_sites, NOT at locations — see file header. This
    -- survives re-VLANing and works for sites with no `locations` row yet.
    monitored_site_id integer NOT NULL REFERENCES monitored_sites(id) ON DELETE RESTRICT,
    -- One of: online / offline / unknown / flapping. Plain text — validated
    -- in routers/monitoring.py. 'flapping' is a first-class state because a
    -- site bouncing all night reads green on any single poll.
    state text NOT NULL,
    source text,                            -- pppoe / ping / activity — which signal decided this
    router_ts timestamp without time zone,  -- RAW as reported
    received_at timestamp without time zone NOT NULL DEFAULT now(),  -- compute uptime from THIS
    snapshot_id integer REFERENCES ingest_snapshots(id) ON DELETE SET NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_site_status_log_site_time
    ON site_status_log(monitored_site_id, received_at DESC);


-- Device headcount per site, downsampled. Written every 5 minutes, not
-- every 60s: hotspot data is inherently up to 2 minutes stale anyway
-- (keepalive-timeout=2m, status-autorefresh=1m), so a 1-minute write rate
-- would be 30k rows/day of false precision.
CREATE TABLE IF NOT EXISTS site_session_counts (
    id serial PRIMARY KEY,
    monitored_site_id integer NOT NULL REFERENCES monitored_sites(id) ON DELETE RESTRICT,
    sessions integer NOT NULL,
    -- raw / hourly. Rows are written 'raw' and rolled up to 'hourly' after
    -- 14 days, at which point the raw rows for that window are deleted.
    granularity text NOT NULL DEFAULT 'raw',
    router_ts timestamp without time zone,
    received_at timestamp without time zone NOT NULL DEFAULT now(),
    snapshot_id integer REFERENCES ingest_snapshots(id) ON DELETE SET NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_site_session_counts_site_time
    ON site_session_counts(monitored_site_id, received_at DESC);


-- One row per hotspot user profile, with its price. Seeded below from the
-- prices encoded in the router's profile names, but this is EDITABLE
-- reference data, not a constant: the billing vendor owns those profiles and
-- can add, rename or reprice one without telling anyone.
CREATE TABLE IF NOT EXISTS hotspot_packages (
    id serial PRIMARY KEY,
    profile_name text NOT NULL UNIQUE,
    price_kes numeric NOT NULL DEFAULT 0,
    is_comped boolean NOT NULL DEFAULT false,
    notes text,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp without time zone NOT NULL DEFAULT now()
);


-- One row per sale or renewal, detected by polling /ip hotspot user
-- read-only. Never written from the customer login path — see Correction 2
-- in PHASES.md for why that approach was deleted.
CREATE TABLE IF NOT EXISTS revenue_events (
    id serial PRIMARY KEY,
    -- Nullable: a sale can be seen before it can be attributed to a site.
    monitored_site_id integer REFERENCES monitored_sites(id) ON DELETE RESTRICT,
    hotspot_username text NOT NULL,         -- e.g. 254796130050-F:D2
    profile_name text NOT NULL,             -- as reported, even if unknown to hotspot_packages
    price_kes numeric NOT NULL DEFAULT 0,   -- SNAPSHOT at time of sale — see file header
    -- sale (username never seen before) / renewal (its Exp: moved forward).
    event_type text NOT NULL DEFAULT 'sale',
    -- direct (user was active on this site's subnet) / inferred (attributed
    -- to whichever site last saw it). Surfaced in the UI so an inferred
    -- number is never mistaken for a measured one.
    attribution text NOT NULL DEFAULT 'direct',
    -- The Exp: value parsed from the user's comment. NOTE: this is written
    -- by the billing system over the freeisphotspotap tunnel, NOT by the
    -- router's clock — so it must NOT inherit the router's +1h correction.
    -- Its timezone is still unestablished; see PHASES.md Correction 5.
    expiry_seen timestamp without time zone,
    first_seen_at timestamp without time zone NOT NULL DEFAULT now(),
    snapshot_id integer REFERENCES ingest_snapshots(id) ON DELETE SET NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now()
);

-- Idempotency guard — see file header. Same user + same expiry = same sale.
CREATE UNIQUE INDEX IF NOT EXISTS uq_revenue_events_user_expiry
    ON revenue_events(hotspot_username, expiry_seen);

CREATE INDEX IF NOT EXISTS idx_revenue_events_site_time
    ON revenue_events(monitored_site_id, first_seen_at DESC);


-- 4.2's quarantine. An unknown vlan_id or PPPoE username returns 200 and
-- lands here rather than 4xx-ing: the RouterOS script has no retry and
-- nobody reads its logs, so a rejection is a silent permanent data loss.
-- This is also the table that tells you a new site appeared on the network
-- before anyone added it to monitored_sites.
CREATE TABLE IF NOT EXISTS ingest_quarantine (
    id serial PRIMARY KEY,
    reason text NOT NULL,                   -- unknown_vlan / unknown_pppoe_user / parse_error / ...
    vlan_id integer,                        -- as reported, if it parsed at all
    pppoe_username text,
    raw_payload jsonb,
    received_at timestamp without time zone NOT NULL DEFAULT now(),
    resolved boolean NOT NULL DEFAULT false,
    resolved_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingest_quarantine_unresolved
    ON ingest_quarantine(resolved, received_at DESC);


-- Seed: prices as encoded in the router's profile names (PHASES.md
-- Correction 2). ON CONFLICT DO NOTHING so re-running never clobbers an
-- owner's later edit to a price.
INSERT INTO hotspot_packages (profile_name, price_kes, is_comped, notes) VALUES
    ('Quick Surf10',      10,  false, NULL),
    ('Half day pass20',   20,  false, NULL),
    ('Full day pass30',   30,  false, NULL),
    ('24hr pass40',       40,  false, NULL),
    ('3day pass70',       70,  false, NULL),
    ('5day pass100',      100, false, NULL),
    ('10day pass150',     150, false, NULL),
    ('Monthly pass400',   400, false, NULL),
    ('Monthly pass500',   500, false, NULL),
    ('hp support users',  0,   true,  'Comped, shared-users=2'),
    ('default',           0,   true,  'Priced at 0 pending owner confirmation that this is genuinely comped and not a misconfiguration')
ON CONFLICT (profile_name) DO NOTHING;
