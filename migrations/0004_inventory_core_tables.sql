-- Phase 3 — Ops Inventory System. Six new tables for tracking field
-- assets/inventory (enclosures, cabling, consumables, power/network gear),
-- entirely separate from the battery-tracking tables. Nothing here touches
-- an existing table or column — this is additive only.
--
-- Design reasoning lives in the Phase 3 plan (see PHASES.md / the owner's
-- brief); the short version:
--   - inventory_locations is deliberately separate from `locations` (which
--     is the battery-tracking sites table) — a client job site here should
--     not show up in Check Sites.
--   - inventory_categories.tracking_type drives which columns on
--     inventory_items apply (Asset-Serialized / Quantity-based /
--     Length-based) — it's plain text with no CHECK constraint, validated
--     in the router instead, so a new allowed value never needs a
--     migration (same convention as battery_movements.reason).
--   - inventory_items is one wide table with nullable columns per tracking
--     type, not JSONB — see the plan for why.
--   - inventory_transactions is the append-only log; item_id is nullable
--     because cable reconciliation can create a brand-new cut row that the
--     log entry references. event_group_id links multiple log rows that
--     belong to one user action (a mixed issue-cart checkout, or a split
--     Transfer that touches two item rows).
--   - inventory_sku_thresholds holds Reorder Level, which is owner-entered
--     data that has to live somewhere other than a per-batch/per-cut row.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0004_inventory_core_tables.sql
-- (or, for local dev with no DATABASE_URL set, against the local
-- battery_tracker DB directly: psql -U postgres -d battery_tracker -f ...)
--
-- All statements are idempotent (IF NOT EXISTS) so re-running this is safe.

CREATE TABLE IF NOT EXISTS inventory_locations (
    id serial PRIMARY KEY,
    name text NOT NULL,
    is_store boolean NOT NULL DEFAULT false, -- true for a warehouse/store (the "home base" equivalent for inventory)
    address text,
    contact_name text,
    contact_phone text,
    notes text,
    is_active boolean NOT NULL DEFAULT true, -- soft delete, same convention as `locations`
    created_at timestamp without time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inventory_categories (
    id serial PRIMARY KEY,
    name text NOT NULL UNIQUE,
    -- One of: asset_serialized / inventory_quantity / inventory_length.
    -- Plain text (see file header) — validated in routers/inventory.py.
    tracking_type text NOT NULL,
    description text,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp without time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inventory_items (
    id serial PRIMARY KEY,
    category_id integer NOT NULL REFERENCES inventory_categories(id),
    sku text NOT NULL, -- identifies the product/model, not this physical row
    name text NOT NULL,
    location_id integer REFERENCES inventory_locations(id),
    unit_cost numeric,
    supplier text,
    unit_of_measure text, -- pcs / pack / box / meter — plain text, router-validated
    notes text,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp without time zone NOT NULL DEFAULT now(),

    -- Asset (Serialized) only:
    serial_number text,
    asset_status text, -- Active / Faulty / In Repair / Decommissioned / Spare — In Storage
    assigned_to_user_id integer REFERENCES users(id),
    make_model text,
    spec_capacity text,
    install_date date,

    -- Inventory (Quantity-based) only — one row per batch/lot:
    batch_lot text,
    expiry_date date,
    quantity_on_hand numeric,

    -- Inventory (Length-based) only — one row per cut/reel:
    cut_reel_id text,
    spec text,
    length_received numeric,
    length_remaining numeric,
    length_status text -- In Stock / Out — Pending Reconciliation / Depleted
);

-- A cut/reel ID is only meaningful (and only needs to be unique) for
-- Length-based rows; NULLs (every Asset/Quantity row) are never compared
-- equal by a unique index, so this doesn't constrain those rows at all.
CREATE UNIQUE INDEX IF NOT EXISTS inventory_items_cut_reel_id_idx
    ON inventory_items (cut_reel_id) WHERE cut_reel_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS inventory_transactions (
    id serial PRIMARY KEY,
    -- Nullable: a Stage-2 cable reconciliation can create a brand-new cut
    -- row (the returned-remainder "-R" cut) that this log entry is the
    -- first reference to — the item row is inserted before this row, in
    -- the same commit, but the column still has to allow it.
    item_id integer REFERENCES inventory_items(id),
    -- Denormalized so this row stays readable even if the item is later
    -- retired/deleted.
    category_id integer NOT NULL REFERENCES inventory_categories(id),
    sku_or_spec text NOT NULL,
    -- Out / In / Transfer / Adjustment / Return / Write-off / Reconciled
    action text NOT NULL,
    qty_or_length numeric,
    from_location_id integer REFERENCES inventory_locations(id),
    to_location_id integer REFERENCES inventory_locations(id),
    -- Distinct from to_location_id: "what job this is for", not "where it
    -- physically ended up" — a store-to-store transfer has no site.
    site_location_id integer REFERENCES inventory_locations(id),
    -- Installation / Expansion / Maintenance / Repair-Replacement /
    -- Relocation / Decommission
    activity text,
    issued_to_user_id integer REFERENCES users(id),
    logged_by_user_id integer NOT NULL REFERENCES users(id),
    status text, -- Open/Pending or Closed — Length-based reconciliation only
    length_used numeric,
    length_returned numeric,
    -- Links multiple log rows that are one user action: a mixed
    -- issue-cart checkout (N lines), or a split Transfer (origin decrement
    -- + new destination row = 2 linked rows). NULL for a plain single-row
    -- action.
    event_group_id uuid,
    notes text,
    created_at timestamp without time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inventory_sku_thresholds (
    id serial PRIMARY KEY,
    category_id integer NOT NULL REFERENCES inventory_categories(id),
    sku_or_spec text NOT NULL,
    reorder_level numeric NOT NULL,
    updated_at timestamp without time zone NOT NULL DEFAULT now(),
    UNIQUE (category_id, sku_or_spec)
);
