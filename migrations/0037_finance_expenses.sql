-- Phase 7A (Finance): the expense tables and the finance audit log.
-- Approved by the owner on 2026-11-15. Four new tables; nothing existing is
-- altered.
--
--   - expense_categories: the allowed category list. Each belongs to a group
--     the Performance page uses to build the statement (direct costs above
--     gross profit, operating below it, and so on). Retired, never deleted:
--     is_active = false hides a category from pickers while old expenses
--     keep pointing at it.
--   - expenses: one row per money-out. Linked to a category by id, so
--     renaming a category never orphans a row. Never deleted: a wrong row is
--     VOIDED (voided_at + voided_by + void_reason) and the change written to
--     finance_audit_log. Every report leaves voided rows out.
--       reference is the M-Pesa code (or bank ref). It is UNIQUE, which is
--       what makes pasting the same message twice harmless: the second copy
--       is skipped. The M-Pesa fee is its own row with reference
--       '<CODE>-FEE'. Cash expenses have no reference (NULL never clashes).
--       A voided row still holds its code, so pasting that message again is
--       skipped too: un-void it rather than re-paste.
--       source_message keeps the original text the row was read from, so
--       any figure can be checked against the phone later.
--       kind 'capital' is equipment bought to keep: left out of monthly
--       profit, offered to Assets instead.
--   - supplier_rules: a known payee gives its category and kind with no AI
--     involved. payee_key is the payee name upper-cased with spaces
--     collapsed. A rule is only written when the user confirms a category.
--   - finance_audit_log: before/after of every finance edit and void, with
--     who and why. Named finance_ so a future app-wide log doesn't clash.
--
-- Times are UTC timestamp without time zone like every other table here
-- (db/connection.py sets the session to UTC); spent_on is the EAT calendar
-- day the money left.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0037_finance_expenses.sql
-- Idempotent: re-running is safe.

BEGIN;

CREATE TABLE IF NOT EXISTS expense_categories (
    id          serial PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    grp         text NOT NULL CHECK (grp IN ('direct_cost', 'operating', 'finance_tax', 'other')),
    sort_order  integer NOT NULL DEFAULT 0,
    is_active   boolean NOT NULL DEFAULT true
);

INSERT INTO expense_categories (name, grp, sort_order) VALUES
    ('Upstream bandwidth',          'direct_cost', 10),
    ('Last-mile / fibre rental',    'direct_cost', 20),
    ('Network equipment',           'operating',   30),
    ('Spares & repairs',            'operating',   40),
    ('Power & solar',               'operating',   50),
    ('Salaries',                    'operating',   60),
    ('Agent commissions',           'operating',   70),
    ('Office rent',                 'operating',   80),
    ('Transport & fuel',            'operating',   90),
    ('Airtime & SMS',               'operating',  100),
    ('Software & licences',         'operating',  110),
    ('Marketing',                   'operating',  120),
    ('M-Pesa & bank charges',       'finance_tax', 130),
    ('Licences & taxes',            'finance_tax', 140),
    ('Other',                       'other',      150)
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS expenses (
    id              serial PRIMARY KEY,
    reference       text UNIQUE,
    spent_on        date NOT NULL,
    amount          numeric(12,2) NOT NULL CHECK (amount > 0),
    payee           text,
    category_id     integer NOT NULL REFERENCES expense_categories(id),
    kind            text NOT NULL DEFAULT 'operating' CHECK (kind IN ('operating', 'capital')),
    segment         text CHECK (segment IN ('pppoe', 'hotspot', 'shared')),
    description     text,
    method          text CHECK (method IN ('mpesa', 'bank', 'cash')),
    source_message  text,
    ai_suggested    boolean NOT NULL DEFAULT false,
    created_by      integer REFERENCES users(id) ON DELETE SET NULL,
    created_at      timestamp without time zone NOT NULL DEFAULT now(),
    updated_at      timestamp without time zone NOT NULL DEFAULT now(),
    voided_at       timestamp without time zone,
    voided_by       integer REFERENCES users(id) ON DELETE SET NULL,
    void_reason     text,
    CONSTRAINT expenses_void_needs_reason CHECK (voided_at IS NULL OR void_reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_expenses_spent_on ON expenses (spent_on);
CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses (category_id);

CREATE TABLE IF NOT EXISTS supplier_rules (
    payee_key    text PRIMARY KEY,
    category_id  integer NOT NULL REFERENCES expense_categories(id),
    kind         text NOT NULL DEFAULT 'operating' CHECK (kind IN ('operating', 'capital')),
    created_by   integer REFERENCES users(id) ON DELETE SET NULL,
    updated_at   timestamp without time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS finance_audit_log (
    id          bigserial PRIMARY KEY,
    at          timestamp without time zone NOT NULL DEFAULT now(),
    user_id     integer REFERENCES users(id) ON DELETE SET NULL,
    entity      text NOT NULL,
    entity_id   text NOT NULL,
    action      text NOT NULL,
    before      jsonb,
    after       jsonb,
    reason      text
);

CREATE INDEX IF NOT EXISTS idx_finance_audit_entity ON finance_audit_log (entity, entity_id);

COMMIT;

-- ROLLBACK (every expense, rule and audit entry is lost):
--   BEGIN;
--   DROP TABLE IF EXISTS finance_audit_log;
--   DROP TABLE IF EXISTS supplier_rules;
--   DROP TABLE IF EXISTS expenses;
--   DROP TABLE IF EXISTS expense_categories;
--   COMMIT;
