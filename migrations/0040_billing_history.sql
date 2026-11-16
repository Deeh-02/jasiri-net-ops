-- Phase 7 Finance: the old billing history, from the billing system's
-- "transactions" export (owner's file transactions_2026-05-01_to_2026-11-16.csv,
-- 2026-11-16). That system sells the hotspot, and ran PPPoE until PPPoE moved
-- to the new billing system on 20 Jun 2026. Its export fills in:
--   - hotspot income before the router started recording sales (21 Sep 2026)
--   - PPPoE income before the move (the recharge export starts at the move)
--
--   - billing_history_imports: one row per saved load of the export.
--   - billing_history: one row per transaction in the export. Income is
--     never typed: these rows come only from the file. A row the rules in
--     db/billing_history_logic.py leave out keeps its reason in left_out
--     ('correction', 'free', 'repeat', 'balance', 'moved') so the call can be
--     seen and reversed; it is not income. paid_at and expires_at are naive
--     UTC like every timestamp here (the export's Nairobi time minus 3 hours).
--     account is who paid: the hotspot username, or the PPPoE account with
--     the old system's "unknown " prefix taken off. The same row in a later
--     export is matched on (invoice, username, plan, paid_at, expires_at).
--
-- Also, on pppoe_recharges (migrations/0039): a new left_out reason 'moved'
-- for the entries staff made on 20 Jun 2026, the day PPPoE moved over. They
-- copied each customer's already-paid month across; the money is counted
-- from the old system's export instead (see db/pppoe_recharge_logic.py).
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0040_billing_history.sql
-- Idempotent: re-running is safe.

BEGIN;

CREATE TABLE IF NOT EXISTS billing_history_imports (
    id           serial PRIMARY KEY,
    file_name    text,
    rows_in_file integer NOT NULL,
    rows_added   integer NOT NULL,
    first_at     timestamp without time zone,
    latest_at    timestamp without time zone,
    imported_by  integer REFERENCES users(id) ON DELETE SET NULL,
    imported_at  timestamp without time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS billing_history (
    id         serial PRIMARY KEY,
    row_no     integer,
    invoice    text NOT NULL,
    username   text NOT NULL,
    account    text NOT NULL,
    plan       text NOT NULL,
    kind       text NOT NULL CHECK (kind IN ('hotspot', 'pppoe', 'balance')),
    segment    text NOT NULL CHECK (segment IN ('hotspot', 'pppoe')),
    router     text,
    amount     numeric(12,2) NOT NULL,
    method     text,
    paid_at    timestamp without time zone NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    left_out   text CHECK (left_out IN ('correction', 'free', 'repeat', 'balance', 'moved')),
    import_id  integer NOT NULL REFERENCES billing_history_imports(id),
    UNIQUE (invoice, username, plan, paid_at, expires_at)
);

CREATE INDEX IF NOT EXISTS billing_history_segment_paid_idx ON billing_history (segment, paid_at);
CREATE INDEX IF NOT EXISTS billing_history_account_paid_idx ON billing_history (account, paid_at);

ALTER TABLE pppoe_recharges DROP CONSTRAINT IF EXISTS pppoe_recharges_left_out_check;
ALTER TABLE pppoe_recharges ADD CONSTRAINT pppoe_recharges_left_out_check
    CHECK (left_out IN ('test', 'free', 'repeat', 'moved'));

-- The move-day entries (db/pppoe_recharge_logic.py MOVE_DAY): made by staff,
-- not the bank, on 20 Jun 2026 Nairobi time.
UPDATE pppoe_recharges SET left_out = 'moved'
WHERE left_out IS NULL
  AND (recharged_at + interval '3 hours')::date = DATE '2026-06-20'
  AND lower(COALESCE(created_by, '')) <> 'ncbaserver';

COMMIT;
