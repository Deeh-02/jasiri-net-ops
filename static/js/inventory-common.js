import { authHeaders, formatDate } from "./common.js";

// Shared across every inventory view (this file, items, log, issue cart,
// reports) so a category/location cache and the tracking-type vocabulary
// exist in exactly one place — mirrors the domain-wide db/inventory_*.py
// split while keeping frontend state in one module those views all import.

// Must match TRACKING_TYPES in routers/inventory.py exactly — the router is
// the source of truth for which values are valid. Labels are "Core" naming
// per the owner's terminology pass (display-only — the underlying
// tracking_type values/columns are unchanged, so this is the one place that
// needs to change to relabel every screen that shows a tracking type).
export const TRACKING_TYPE_LABELS = {
    asset_serialized: "Asset Core",
    inventory_quantity: "Consumables Core",
    inventory_length: "Cable Core",
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

function detailRow(label, value) {
    return `<div class="dashboard-detail-row"><span>${label}</span><span>${value ?? "—"}</span></div>`;
}

// ---- Per-unit tabbed Details/Logs modal — a second real instance of
// dashboard.js's openViewBatteryModal/renderLogsTable pattern (same CSS
// classes, same client-side pagination), reimplemented as its own
// inventory-domain code rather than importing the battery-specific
// function, per architecture.md's no-cross-domain-import rule. Its own
// .inventory-item-tab class (not .dashboard-tab-slant) avoids colliding with
// dashboard.js's page-wide tab-click listener, which is hardcoded to the
// battery modal's own panel ids — see inventory.css's comment on that class.
//
// Shared by the Items page's unit list and Reports' cable drill-down (both
// need to open the same per-unit record), which is why it lives here rather
// than in inventory.js — the modal markup itself lives once in
// inventory.html since every view fragment is injected at boot regardless of
// which view is active, so it's addressable from either caller.
let unitDetailLogsCache = [];
let unitDetailLogsPage = 1;
let unitDetailLogsPageSize = 10;

export async function openUnitDetailModal(itemId) {
    const overlay = document.getElementById("item-detail-overlay");
    if (!overlay) return;

    const [itemRes, txnRes] = await Promise.all([
        fetch(`/inventory/items/${itemId}`, { headers: authHeaders() }),
        fetch(`/inventory/transactions?item_id=${itemId}`, { headers: authHeaders() }),
    ]);
    if (!itemRes.ok) return;
    const item = await itemRes.json();
    unitDetailLogsCache = txnRes.ok ? await txnRes.json() : [];

    document.getElementById("item-detail-label").textContent = `${item.name} — ${item.sku}`;

    const rows = [
        detailRow("Category", item.category_name),
        detailRow("Location", item.location_name),
        detailRow("Unit cost", item.unit_cost),
        detailRow("Supplier", item.supplier),
        detailRow("Unit of measure", item.unit_of_measure),
    ];

    if (item.tracking_type === "asset_serialized") {
        rows.push(
            detailRow("Serial number", item.serial_number),
            detailRow("Status", item.asset_status),
            detailRow("Assigned to", assignableUserName(item.assigned_to_user_id)),
            detailRow("Make/Model", item.make_model),
            detailRow("Spec/Capacity", item.spec_capacity),
            detailRow("Install date", item.install_date),
        );
    } else if (item.tracking_type === "inventory_quantity") {
        rows.push(
            detailRow("Batch/Lot", item.batch_lot),
            detailRow("Expiry", item.expiry_date),
            detailRow("Quantity on hand", item.quantity_on_hand),
        );
    } else if (item.tracking_type === "inventory_length") {
        rows.push(
            detailRow("Cut/Reel ID", item.cut_reel_id),
            detailRow("Spec", item.spec),
            detailRow("Length received", item.length_received),
            detailRow("Length remaining", item.length_remaining),
            detailRow("Status", item.length_status),
        );
    }

    rows.push(detailRow("Notes", item.notes));
    document.getElementById("item-detail-fields").innerHTML = rows.join("");

    // Always resets to the Details tab on open, matching openViewBatteryModal.
    document.querySelectorAll("#item-detail-overlay .inventory-item-tab").forEach(t => t.classList.remove("active"));
    document.getElementById("item-detail-tab-btn-details").classList.add("active");
    document.getElementById("item-detail-tab-details").hidden = false;
    document.getElementById("item-detail-tab-logs").hidden = true;

    unitDetailLogsPage = 1;
    renderUnitDetailLogs();

    overlay.hidden = false;
}

function renderUnitDetailLogs() {
    const listEl = document.getElementById("item-detail-logs-list");
    const paginationEl = document.getElementById("item-detail-logs-pagination");
    if (!listEl || !paginationEl) return;

    const total = unitDetailLogsCache.length;
    if (total === 0) {
        listEl.innerHTML = `<div class="dashboard-log-empty">No transaction history yet.</div>`;
        paginationEl.innerHTML = "";
        return;
    }

    const totalPages = Math.ceil(total / unitDetailLogsPageSize);
    if (unitDetailLogsPage > totalPages) unitDetailLogsPage = totalPages;
    const start = (unitDetailLogsPage - 1) * unitDetailLogsPageSize;
    const pageRows = unitDetailLogsCache.slice(start, start + unitDetailLogsPageSize);

    // Notes stays here (unlike the battery modal's 4-column, notes-less
    // table) — a deliberate deviation since Asset/Cable history is
    // explicitly where notes carry real context.
    listEl.innerHTML = `
        <table class="dashboard-logs-table">
            <colgroup><col style="width:20%"><col style="width:13%"><col style="width:13%"><col style="width:24%"><col style="width:30%"></colgroup>
            <tbody>
                ${pageRows.map(t => `
                    <tr>
                        <td>${formatDate(t.created_at)}</td>
                        <td>${t.action}</td>
                        <td>${t.qty_or_length ?? "—"}</td>
                        <td>${t.to_location_name || t.from_location_name || "—"}</td>
                        <td>${t.notes || "—"}</td>
                    </tr>
                `).join("")}
            </tbody>
        </table>
    `;

    paginationEl.innerHTML = `
        <div class="dashboard-logs-page-size">
            <span>Rows:</span>
            <select id="item-detail-logs-page-size-select">
                ${[10, 20, 50, 100].map(n => `<option value="${n}" ${n === unitDetailLogsPageSize ? "selected" : ""}>${n}</option>`).join("")}
            </select>
        </div>
        <div class="dashboard-logs-page-nav">
            <button type="button" id="item-detail-logs-prev-btn" ${unitDetailLogsPage === 1 ? "disabled" : ""}>Prev</button>
            <span>Page ${unitDetailLogsPage} of ${totalPages}</span>
            <button type="button" id="item-detail-logs-next-btn" ${unitDetailLogsPage === totalPages ? "disabled" : ""}>Next</button>
        </div>
    `;

    document.getElementById("item-detail-logs-page-size-select").addEventListener("change", (e) => {
        unitDetailLogsPageSize = Number(e.target.value);
        unitDetailLogsPage = 1;
        renderUnitDetailLogs();
    });
    document.getElementById("item-detail-logs-prev-btn").addEventListener("click", () => {
        unitDetailLogsPage = Math.max(1, unitDetailLogsPage - 1);
        renderUnitDetailLogs();
    });
    document.getElementById("item-detail-logs-next-btn").addEventListener("click", () => {
        unitDetailLogsPage = Math.min(totalPages, unitDetailLogsPage + 1);
        renderUnitDetailLogs();
    });
}

// Wired once (from inventory.js, since it owns the modal's markup file) —
// every caller of openUnitDetailModal shares this same close/tab behavior.
export function initUnitDetailModal() {
    const overlay = document.getElementById("item-detail-overlay");
    document.getElementById("item-detail-close").addEventListener("click", () => { overlay.hidden = true; });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });

    overlay.querySelectorAll(".inventory-item-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            overlay.querySelectorAll(".inventory-item-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            document.getElementById("item-detail-tab-details").hidden = tab.dataset.tab !== "details";
            document.getElementById("item-detail-tab-logs").hidden = tab.dataset.tab !== "logs";
        });
    });
}

// ---- Consumable Core's flat stock history — no individual units exist for
// this type, so View skips straight to a running-balance table instead of a
// unit list. No Notes column (per the brief: nobody explains why they issued
// cable ties) and no edit/delete here, unlike the unit list.
export async function openSkuHistoryModal(categoryId, sku, label) {
    const overlay = document.getElementById("sku-history-overlay");
    if (!overlay) return;

    document.getElementById("sku-history-label").textContent = `— ${label}`;
    const res = await fetch(`/inventory/sku-history?category_id=${categoryId}&sku=${encodeURIComponent(sku)}`, { headers: authHeaders() });
    const history = res.ok ? await res.json() : [];

    document.getElementById("sku-history-rows").innerHTML = history.length === 0
        ? `<div class="dashboard-log-empty">No transaction history yet.</div>`
        : `
            <table class="dashboard-logs-table">
                <colgroup><col style="width:32%"><col style="width:22%"><col style="width:20%"><col style="width:26%"></colgroup>
                <tbody>
                    ${history.map(h => `
                        <tr>
                            <td>${formatDate(h.created_at)}</td>
                            <td>${h.action}</td>
                            <td>${h.qty_or_length ?? "—"}</td>
                            <td>${h.balance}</td>
                        </tr>
                    `).join("")}
                </tbody>
            </table>
        `;

    overlay.hidden = false;
}

export function initSkuHistoryModal() {
    const overlay = document.getElementById("sku-history-overlay");
    document.getElementById("sku-history-close").addEventListener("click", () => { overlay.hidden = true; });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });
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
