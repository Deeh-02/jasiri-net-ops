-- Phase 7 Finance: PPPoE income, from the billing system's recharge export.
-- Source chosen by the owner on 2026-11-16 (open question 1 in phase.md):
-- the FreeISPRadius "recharges" export (User, Name, Plan, Amount, Status,
-- Recharge Date, Created By, ...), uploaded on Finance > Income > PPPoE the
-- same way the customer export is uploaded on Customers. Two new tables;
-- nothing existing is altered.
--
--   - pppoe_recharge_imports: one row per saved upload. The latest one is
--     how far PPPoE income is recorded ("recorded up to the last upload").
--   - pppoe_recharges: one row per recharge in the export. Income is never
--     typed: these rows come only from the file. A row the rules leave out
--     (left_out: 'test' = a test or staff account, 'free' = KSh 0,
--     'repeat' = the same account, plan and amount again within minutes)
--     is kept so the decision can be seen and reversed, but it is not
--     counted as income.
--     recharged_at is naive UTC like every timestamp here (the export's
--     Nairobi time minus 3 hours). The same recharge in a later export is
--     matched on (username, recharged_at, plan, amount), so re-uploading a
--     full export only adds the new ones.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0039_pppoe_recharges.sql
-- Idempotent: re-running is safe.

BEGIN;

CREATE TABLE IF NOT EXISTS pppoe_recharge_imports (
    id              serial PRIMARY KEY,
    file_name       text,
    rows_in_file    integer NOT NULL,
    rows_added      integer NOT NULL,
    latest_recharge timestamp without time zone,
    imported_by     integer REFERENCES users(id) ON DELETE SET NULL,
    imported_at     timestamp without time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pppoe_recharges (
    id             serial PRIMARY KEY,
    username       text NOT NULL,
    customer_name  text,
    plan           text NOT NULL,
    base_plan      text,
    amount         numeric(12,2) NOT NULL CHECK (amount >= 0),
    recharged_at   timestamp without time zone NOT NULL,
    billing_status text,
    quantity       text,
    created_by     text,
    left_out       text CHECK (left_out IN ('test', 'free', 'repeat')),
    import_id      integer NOT NULL REFERENCES pppoe_recharge_imports(id),
    UNIQUE (username, recharged_at, plan, amount)
);

CREATE INDEX IF NOT EXISTS pppoe_recharges_recharged_at_idx ON pppoe_recharges (recharged_at);

COMMIT;
