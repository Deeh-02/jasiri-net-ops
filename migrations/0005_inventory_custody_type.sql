-- Addendum 2 of Phase 3 (owner's second UI/UX refinement pass) distinguishes
-- two kinds of category: "per_job" items are expected back through Return
-- Materials when a job closes, "custody" items (uniforms, personally-
-- dedicated hand tools) stay with a person indefinitely with no such
-- expectation. No new transaction type or item status is needed for this —
-- Issue and Return work identically either way — it's purely a category-
-- level flag the Items table filters on.
--
-- Plain text with no CHECK constraint, validated in routers/inventory.py
-- instead (CUSTODY_TYPES = {"per_job", "custody"}) — same enum-as-text
-- convention as tracking_type on this same table.
--
-- Unlike migration 0004 (six brand-new tables, zero ALTERs), this DOES touch
-- an existing table — one additive, defaulted column, flagged per the
-- CLAUDE.md schema-review rule even though the blast radius is small.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0005_inventory_custody_type.sql

ALTER TABLE inventory_categories
    ADD COLUMN IF NOT EXISTS custody_type text NOT NULL DEFAULT 'per_job';
