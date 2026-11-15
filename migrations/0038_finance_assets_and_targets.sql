-- Phase 7F (Finance > Assets) and the typed month target from 7E.
-- Approved by the owner on 2026-11-15. Three new tables; nothing existing is
-- altered. Inventory's own tables are only referenced, never changed.
--
--   - asset_category_lives: the default useful life and salvage share for an
--     Inventory category, so 125 items don't each need typing. Seeded below
--     with the lives the owner agreed on 2026-11-15 (vehicles keep 20% as
--     salvage, everything else 0). A category with no row has no default:
--     its items show "no data" until a life is set.
--   - asset_values: the money facts Finance keeps for one Inventory item.
--     The item's COST is not here: it is Inventory's own unit_cost, read
--     through db/inventory_items.py, so the two can never disagree.
--       purchased_on NULL means "not known yet" (owner's call 2026-11-15,
--       option A): no book value or depreciation is invented for it.
--       life_months / salvage NULL mean "use the category default".
--       expense_id links the capital expense that paid for it (spec 7: an
--       expense marked capital is linked to its asset).
--   - finance_month_targets: an optional income target typed once per month
--     (spec 4). Without one, Income's target bar uses break-even.
--
-- Depreciation is straight-line from the purchase month: (cost - salvage)
-- / life per month, a management figure only (tax treatment is for the
-- accountant).
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0038_finance_assets_and_targets.sql
-- Idempotent: re-running is safe.

BEGIN;

CREATE TABLE IF NOT EXISTS asset_category_lives (
    category_id   integer PRIMARY KEY REFERENCES inventory_categories(id),
    life_months   integer NOT NULL CHECK (life_months > 0),
    salvage_pct   numeric(5,2) NOT NULL DEFAULT 0 CHECK (salvage_pct >= 0 AND salvage_pct < 100),
    updated_by    integer REFERENCES users(id) ON DELETE SET NULL,
    updated_at    timestamp without time zone NOT NULL DEFAULT now()
);

INSERT INTO asset_category_lives (category_id, life_months, salvage_pct)
SELECT c.id, d.life_months, d.salvage_pct
FROM (VALUES
    ('Access Network Equipment (Last-Mile)', 36, 0),
    ('Network Infrastructure Equipment',     60, 0),
    ('Power Equipment',                      60, 0),
    ('Field Tools',                          36, 0),
    ('Office Furniture',                     60, 0),
    ('Personal Equipment',                   36, 0),
    ('Vehicles',                             60, 20),
    ('Passive Network Material',            120, 0),
    ('PPE / Apparel',                        12, 0)
) AS d(name, life_months, salvage_pct)
JOIN inventory_categories c ON c.name = d.name
ON CONFLICT (category_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS asset_values (
    item_id         integer PRIMARY KEY REFERENCES inventory_items(id),
    purchased_on    date,
    life_months     integer CHECK (life_months > 0),
    salvage         numeric(12,2) CHECK (salvage >= 0),
    warranty_until  date,
    segment         text CHECK (segment IN ('pppoe', 'hotspot', 'shared')),
    expense_id      integer REFERENCES expenses(id),
    updated_by      integer REFERENCES users(id) ON DELETE SET NULL,
    updated_at      timestamp without time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_asset_values_expense ON asset_values (expense_id) WHERE expense_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS finance_month_targets (
    month       date PRIMARY KEY CHECK (extract(day FROM month) = 1),
    amount      numeric(12,2) NOT NULL CHECK (amount > 0),
    set_by      integer REFERENCES users(id) ON DELETE SET NULL,
    set_at      timestamp without time zone NOT NULL DEFAULT now()
);

COMMIT;

-- ROLLBACK (purchase dates, lives, links and targets are lost; Inventory is untouched):
--   BEGIN;
--   DROP TABLE IF EXISTS finance_month_targets;
--   DROP TABLE IF EXISTS asset_values;
--   DROP TABLE IF EXISTS asset_category_lives;
--   COMMIT;
