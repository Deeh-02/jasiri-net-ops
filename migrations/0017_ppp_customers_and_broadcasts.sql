-- Customer broadcast SMS (Alerts > Customers / Broadcast). Four new tables,
-- nothing existing is altered.
--
-- WHY A SEPARATE CUSTOMER LIST. The routers hold no phone numbers; the
-- billing system (FreeISPRadius) does. Ops keeps its own copy, loaded from
-- that system's customer export (.xlsx/.csv), one upload per router.
--
--   - ppp_routers: the audiences the Broadcast page offers. Seeded with
--     "CCR2116 PPPoE clients" and "Router 2" (the hotspot PPPoE router, to be
--     named when its customers arrive); rename them in place if you like.
--   - ppp_customers: one row per (router, PPPoE username). Re-uploading a
--     file refreshes only plan, expiry, account_status and enabled on
--     existing rows — name and phone are never overwritten, so a number
--     corrected in Ops survives. Nothing is ever deleted: a customer who
--     expires keeps their row with account_status = 'Expired'.
--     account_status and enabled are stored exactly as the export gives them
--     ('Active' / 'Expired' / 'Inactive', Enable/Disable); "who counts as a
--     paying customer" is decided at send time, not baked in here.
--     PHONE IS NOT UNIQUE. Two accounts can share a phone (one person, two
--     shops); the broadcast sends one SMS per distinct phone.
--     phone is NULL when the export's number couldn't be read as a Kenyan
--     mobile — those customers are listed but never texted.
--     expiry is UTC like every other timestamp here (the export's local
--     Nairobi time minus 3h).
--   - customer_broadcasts / customer_broadcast_deliveries: what was sent, to
--     whom, and what the SMS provider answered. customer_id is SET NULL on
--     delete so the log outlives a removed customer.
--
-- status values (text, no CHECK, same convention as the monitoring tables):
--   customer_broadcasts.state              'sending' | 'done'
--   customer_broadcast_deliveries.status   'sent' | 'failed'
--
-- Safe to deploy the code before running this: only the Customers and
-- Broadcast pages error until it is applied; alerts and everything else are
-- untouched.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0017_ppp_customers_and_broadcasts.sql
-- Idempotent — re-running is safe.

CREATE TABLE IF NOT EXISTS ppp_routers (
    id          serial PRIMARY KEY,
    name        text NOT NULL UNIQUE
);

-- Router 1 was first seeded as 'Router 1'. If this ran before, rename it in
-- place (BEFORE the insert below, or the insert would add a third router);
-- on a fresh database this matches nothing.
UPDATE ppp_routers SET name = 'CCR2116 PPPoE clients' WHERE name = 'Router 1';

INSERT INTO ppp_routers (name) VALUES ('CCR2116 PPPoE clients'), ('Router 2')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS ppp_customers (
    id              serial PRIMARY KEY,
    router_id       integer NOT NULL REFERENCES ppp_routers(id),
    username        text NOT NULL,
    external_id     text,
    name            text,
    phone           text,
    plan            text,
    expiry          timestamp without time zone,
    account_status  text NOT NULL,
    enabled         boolean NOT NULL DEFAULT true,
    imported_at     timestamp without time zone NOT NULL DEFAULT now(),
    UNIQUE (router_id, username)
);

CREATE INDEX IF NOT EXISTS ppp_customers_router_idx ON ppp_customers (router_id);

CREATE TABLE IF NOT EXISTS customer_broadcasts (
    id               serial PRIMARY KEY,
    message          text NOT NULL,
    router_ids       integer[] NOT NULL,
    status_filter    text NOT NULL,
    recipient_count  integer NOT NULL,
    sent_count       integer NOT NULL DEFAULT 0,
    failed_count     integer NOT NULL DEFAULT 0,
    state            text NOT NULL DEFAULT 'sending',
    sent_by          integer REFERENCES users(id) ON DELETE SET NULL,
    created_at       timestamp without time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_broadcast_deliveries (
    id                bigserial PRIMARY KEY,
    broadcast_id      integer NOT NULL REFERENCES customer_broadcasts(id) ON DELETE CASCADE,
    customer_id       integer REFERENCES ppp_customers(id) ON DELETE SET NULL,
    phone             text NOT NULL,
    status            text NOT NULL,
    provider_response jsonb,
    sent_at           timestamp without time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS customer_broadcast_deliveries_broadcast_idx
    ON customer_broadcast_deliveries (broadcast_id);
