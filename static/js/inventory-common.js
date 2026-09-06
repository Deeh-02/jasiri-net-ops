import { authHeaders } from "./common.js";

// Shared across every inventory view (this file, items, log, issue cart,
// reports) so a category/location cache and the tracking-type vocabulary
// exist in exactly one place — mirrors the domain-wide db/inventory_*.py
// split while keeping frontend state in one module those views all import.

// Must match TRACKING_TYPES in routers/inventory.py exactly — the router is
// the source of truth for which values are valid.
export const TRACKING_TYPE_LABELS = {
    asset_serialized: "Asset (Serialized)",
    inventory_quantity: "Inventory (Quantity)",
    inventory_length: "Inventory (Length)",
};

export function trackingTypeLabel(trackingType) {
    return TRACKING_TYPE_LABELS[trackingType] || trackingType;
}

let categoriesCache = [];
let locationsCache = [];

export async function loadInventoryCategories() {
    const res = await fetch("/inventory/categories", { headers: authHeaders() });
    categoriesCache = res.ok ? await res.json() : [];
    return categoriesCache;
}

export function getInventoryCategories() {
    return categoriesCache;
}

export async function loadInventoryLocations() {
    const res = await fetch("/inventory/locations", { headers: authHeaders() });
    locationsCache = res.ok ? await res.json() : [];
    return locationsCache;
}

export function getInventoryLocations() {
    return locationsCache;
}

let assignableUsersCache = [];

// Powers the Assigned To picker on Asset items. Reuses the existing /users
// endpoint (gated on users:view, same as the Users page) rather than adding
// a new permission section just for one dropdown — a role that can't see
// Users simply gets an empty picker here and leaves Assigned To blank.
export async function loadInventoryAssignableUsers() {
    const res = await fetch("/users", { headers: authHeaders() });
    assignableUsersCache = res.ok ? await res.json() : [];
    return assignableUsersCache;
}

export function getInventoryAssignableUsers() {
    return assignableUsersCache;
}

export function assignableUserName(userId) {
    if (!userId) return null;
    const user = assignableUsersCache.find(u => String(u.id) === String(userId));
    return user ? user.name : null;
}

let allItemsCache = [];

// The unfiltered, all-categories item list — powers pickers (the log's
// "which item" select, and later the issue cart / reconciliation search)
// that need every active item regardless of category. inventory.js's own
// Items panel keeps its own category-filtered fetch separate from this,
// since that one's scoped to whichever category the panel's own filter
// select is showing.
export async function loadAllInventoryItems() {
    const res = await fetch("/inventory/items", { headers: authHeaders() });
    allItemsCache = res.ok ? await res.json() : [];
    return allItemsCache;
}

export function getAllInventoryItems() {
    return allItemsCache;
}

// A picker label unique enough to tell apart two rows that share the same
// name/SKU — which happens routinely once a batch splits (a Transfer can
// leave the same SKU sitting in two locations with two different
// quantities). Selecting the wrong row here means logging a transaction
// against the wrong physical batch, so name+SKU alone is not enough.
export function itemPickerLabel(item) {
    const parts = [`${item.name} — ${item.sku}`];
    if (item.tracking_type === "asset_serialized" && item.serial_number) {
        parts.push(`SN ${item.serial_number}`);
    } else if (item.tracking_type === "inventory_quantity") {
        parts.push(`${item.quantity_on_hand ?? "—"} on hand${item.batch_lot ? ` · Lot ${item.batch_lot}` : ""}`);
    } else if (item.tracking_type === "inventory_length" && item.cut_reel_id) {
        parts.push(`Reel ${item.cut_reel_id}`);
    }
    parts.push(`@ ${item.location_name || "no location"}`);
    return parts.join(" — ");
}
