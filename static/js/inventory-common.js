import {
    can, authHeaders, showMessage, formatDate,
    editIconSvg, deleteIconSvg, viewIconSvg,
} from "./common.js";

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

// Shared free-text match across every unit-identifying field — name,
// SKU/spec, serial, batch/lot, reel id, category, location. This is the one
// search behavior ("name, SKU, serial, lot, reel...") used everywhere an
// individual unit is picked: Issue Materials, Return Materials, and the Log
// Transaction form's Item field — each layers its own type/status exclusion
// on top of this (a depleted reel excluded from Issue, cable excluded
// entirely from Return, nothing excluded from Log Transaction since a
// manual entry may need to target any item regardless of state).
export function inventoryItemMatchesQuery(item, query) {
    const q = query.toLowerCase().trim();
    if (!q) return false;
    const haystack = [
        item.name, item.sku, item.category_name, item.serial_number,
        item.batch_lot, item.cut_reel_id, item.spec, item.location_name,
    ].filter(Boolean).join(" ").toLowerCase();
    return haystack.includes(q);
}

// Matches an Items/Stock summary row (one row per SKU/spec group, from
// get_items_summary — no serial_number of its own) against a free-text
// filter: checks the row's own Name/SKU/Category first, then falls back to
// scanning the raw units under that group via getAllInventoryItems() using
// the same matching fields inventoryItemMatchesQuery already uses — so
// typing a serial number, batch/lot, or reel id still finds the SKU it
// belongs to, even though the aggregate row doesn't display one itself.
export function inventorySummaryRowMatchesQuery(row, query) {
    const q = query.toLowerCase().trim();
    if (!q) return true;
    const rowHaystack = [row.name, row.sku_or_spec, row.category_name].filter(Boolean).join(" ").toLowerCase();
    if (rowHaystack.includes(q)) return true;
    return getAllInventoryItems().some(item =>
        item.category_id === row.category_id && item.sku === row.sku_or_spec && inventoryItemMatchesQuery(item, query)
    );
}

// Shows only the field block matching the selected/existing tracking_type —
// the plain if/else this drives (not a schema-driven form builder) matches
// the phase plan's explicit instruction. Shared because three separate forms
// need it: Items' Edit Product, Stock's Add Unit / Edit Unit.
export function showTypeFields(form, trackingType) {
    form.querySelectorAll("[data-item-type-fields]").forEach(block => {
        block.hidden = block.dataset.itemTypeFields !== trackingType;
    });
}

// One row per (category_id, sku-or-spec) group, deduped from the already-
// loaded all-items cache — the same identity get_items_summary groups by.
// Shared because both Items' catalog table and Stock's Add Item wizard
// (product search + the "which product is this a unit of" question) need
// the same "one row per product" view of the same underlying cache.
export function buildProductList() {
    const seen = new Map();
    for (const item of getAllInventoryItems()) {
        const key = `${item.category_id}:${item.tracking_type === "inventory_length" ? item.spec : item.sku}`;
        if (!seen.has(key)) seen.set(key, item);
    }
    return [...seen.values()];
}

function detailRow(label, value) {
    return `<div class="dashboard-detail-row"><span>${label}</span><span>${value ?? "—"}</span></div>`;
}

// Must match ASSET_STATUSES in routers/inventory.py. Color-codes every
// place an asset's status is shown as read-only text (the unit list, the
// unit detail modal, Issue/Return Materials' search results) with the same
// .status-pill component the rest of the app already uses for status
// (battery/movement/site) — colors live in inventory.css per that pattern's
// own convention (base class here, view-specific variants in the view's own
// CSS file). Editable status <select> controls (Add/Edit Unit, Return's
// cart line) are left as plain selects, not reskinned as pills — a picker
// needs to look pickable, not like a static readout.
const ASSET_STATUS_CLASSES = {
    Active: "asset-active",
    Deployed: "asset-deployed",
    Faulty: "asset-faulty",
    "In Repair": "asset-in-repair",
    Decommissioned: "asset-decommissioned",
};

export function assetStatusBadgeHtml(status) {
    if (!status) return "—";
    return `<span class="status-pill ${ASSET_STATUS_CLASSES[status] || ""}">${status}</span>`;
}

// ---- Small formatting helpers shared by Items' and Stock's tables — both
// render the same Qty/Unit Cost/Total Value columns off the same
// get_items_summary rows now, so these live here instead of being copied
// into each page's JS.
export function numOrNull(value) {
    return value === "" || value === null || value === undefined ? null : Number(value);
}

// Issue/Return Materials cart-line quantity control — same .qty-stepper
// look as every other qty field in the app, built fresh per cart line (each
// needs its own unique DOM id for initQtyStepper below to wire correctly,
// since a cart can hold several Quantity lines at once). data-id carries the
// item id the qty belongs to, read by the calling view's own "input"
// listener — separate from the element's own DOM id.
export function cartQtyStepperHtml({ id, itemId, value, max }) {
    return `
        <div class="qty-stepper issue-cart-qty-wrapper">
            <input type="number" step="any" min="0" ${max != null ? `max="${max}"` : ""}
                   class="issue-cart-qty" id="${id}" data-id="${itemId}" value="${value}">
            <div class="qty-stepper-buttons">
                <button type="button" class="qty-stepper-btn" data-dir="up" tabindex="-1" aria-label="Increase">▲</button>
                <button type="button" class="qty-stepper-btn" data-dir="down" tabindex="-1" aria-label="Decrease">▼</button>
            </div>
        </div>
    `;
}

// Shared custom up/down stepper — replaces a number input's native spinner
// (an unstyled white box that clashes with the dark theme) with two themed
// buttons, reused by every qty/length/cost field across the inventory
// domain (Log Transaction's Qty/Length, Add Item's Reorder Level/Quantity/
// Unit Cost, etc). Scoped to the input's own .qty-stepper ancestor rather
// than a document-wide selector, so wiring one instance never also grabs
// another field's buttons elsewhere on the page. step="any" means the
// input doesn't support stepUp()/stepDown() natively, so this increments/
// decrements the parsed value by 1 directly.
export function initQtyStepper(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const wrapper = input.closest(".qty-stepper");
    if (!wrapper) return;
    wrapper.querySelectorAll(".qty-stepper-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const current = Number(input.value) || 0;
            const next = btn.dataset.dir === "up" ? current + 1 : current - 1;
            input.value = Math.max(0, next);
            input.dispatchEvent(new Event("input"));
        });
    });
}

// "Today" is almost always the right default for a date input (Install
// Date, Expiry Date, etc.) — pre-filling it saves a manual entry on the
// common case while staying fully editable for genuine backdating. Built
// from local Y/M/D rather than toISOString() (which is UTC-based and can
// read as tomorrow or yesterday depending on the viewer's timezone/time of
// day) so it matches what the viewer's own calendar says "today" is.
export function todayDateString() {
    const d = new Date();
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd}`;
}

export function money(n) {
    return n == null ? "—" : Number(n).toFixed(2);
}

function csvField(value) {
    const s = value == null ? "" : String(value);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// Builds a CSV client-side from data a page already has in hand (so it
// naturally respects whatever filters produced those rows — nothing here
// re-fetches an unfiltered set) and triggers a browser download. `columns`
// is [{ header, value(row) }]; fields are quoted per RFC 4180 only when they
// contain a comma, quote, or newline.
export function exportRowsToCsv(filename, columns, rows) {
    const lines = [
        columns.map(c => csvField(c.header)).join(","),
        ...rows.map(row => columns.map(c => csvField(c.value(row))).join(",")),
    ];
    const blob = new Blob([lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

export function unitCostCell(row) {
    if (row.avg_unit_cost == null) return "—";
    // Assets get the "~" cue since it's a computed average across
    // differently-priced batches — Consumables/Cable show a plain number.
    return row.tracking_type === "asset_serialized"
        ? `<span class="inventory-avg-cost" title="Weighted average across all units of this SKU">~${money(row.avg_unit_cost)}</span>`
        : money(row.avg_unit_cost);
}

// Same "~" cue as unitCostCell, as plain text — for CSV export, where the
// HTML span isn't meaningful.
export function unitCostText(row) {
    if (row.avg_unit_cost == null) return "—";
    return row.tracking_type === "asset_serialized" ? `~${money(row.avg_unit_cost)}` : money(row.avg_unit_cost);
}

// The item add/edit form's Location picker answers "which store is this new
// stock initially shelved at" — scoped to is_store rows only, distinct from
// the free-typed Site field on Issue/Return Materials (getInventoryLocations
// above caches every location unfiltered for that autocomplete's use).
// Shared because the Edit Unit modal is reachable from both Items and
// Stock now (via the shared unit list below), and Items' own Add Item
// wizard also needs these same two selects populated.
// Free-typed with autocomplete, same pattern as Issue/Return Materials'
// Site field (populateIssueDropdowns in issue-materials.js) — suggests
// every previously-used store name but a brand-new one needs zero
// pre-configuration; the backend creates it on submit
// (get_or_create_location_by_name(name, is_store=True), via
// _resolve_unit_location_id in routers/inventory.py). Scoped to
// is_store=true rows only, unlike Site's suggestion list, which is
// deliberately unfiltered — this field must never resolve to a job site.
export function populateLocationDropdowns() {
    const storeLocations = getInventoryLocations().filter(l => l.is_store);
    const options = storeLocations.map(l => `<option value="${l.name}"></option>`).join("");

    const addList = document.getElementById("add-item-unit-location-suggestions");
    if (addList) addList.innerHTML = options;

    const addBatchList = document.getElementById("add-item-batch-location-suggestions");
    if (addBatchList) addBatchList.innerHTML = options;

    const editList = document.getElementById("edit-inventory-item-location-suggestions");
    if (editList) editList.innerHTML = options;

    // Log Transaction's Transfer From/To fields — same store-to-store
    // question as Add/Edit Unit's Location field, so the same is_store-
    // filtered suggestion list applies.
    const toList = document.getElementById("inventory-transaction-to-location-suggestions");
    if (toList) toList.innerHTML = options;

    const fromList = document.getElementById("inventory-transaction-from-location-suggestions");
    if (fromList) fromList.innerHTML = options;
}

export function populateAssignedToDropdowns() {
    const users = getInventoryAssignableUsers();
    const options = users.map(u => `<option value="${u.id}">${u.name}</option>`).join("");

    const addSelect = document.getElementById("add-item-unit-assigned-to");
    if (addSelect) addSelect.innerHTML = `<option value="">Assigned to (optional)</option>${options}`;

    const addBatchSelect = document.getElementById("add-item-batch-assigned-to");
    if (addBatchSelect) addBatchSelect.innerHTML = `<option value="">Assigned to (optional)</option>${options}`;

    const editSelect = document.getElementById("edit-inventory-item-assigned-to");
    if (editSelect) editSelect.innerHTML = `<option value="">Assigned to (optional)</option>${options}`;
}

// Drops the year (unlike the shared formatDate) so the Date column fits its
// narrower 5-column allotment in the per-unit Logs tab without truncating —
// a record from a prior year is rare enough in one unit's own history that
// this is an acceptable trade, and the year is still visible on the row's
// full detail if ever needed.
function formatDateTimeShort(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString([], {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
        timeZone: "Africa/Nairobi",
    });
}

// ---- Per-unit tabbed Details/Logs modal — a second real instance of
// dashboard.js's openViewBatteryModal/renderLogsTable pattern (same CSS
// classes, same client-side pagination, same page-size 10/20/50/100
// convention), reimplemented as its own inventory-domain code rather than
// importing the battery-specific function, per architecture.md's
// no-cross-domain-import rule. Its own .inventory-item-tab class (not
// .dashboard-tab-slant) avoids colliding with dashboard.js's page-wide
// tab-click listener, which is hardcoded to the battery modal's own panel
// ids — see inventory.css's comment on that class. Deliberately deviates
// from the battery modal's .dashboard-logs-scroll (a nested 360px scrollbox)
// though: the Logs tab renders exactly one page's worth of rows and stops,
// and the modal box itself (not an inner box) scrolls if that page is
// taller than the viewport — see the #item-detail-overlay .modal-box rule
// in inventory.css.
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
            detailRow("Status", assetStatusBadgeHtml(item.asset_status)),
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
            <colgroup><col style="width:26%"><col style="width:13%"><col style="width:13%"><col style="width:24%"><col style="width:24%"></colgroup>
            <tbody>
                ${pageRows.map(t => `
                    <tr>
                        <td>${formatDateTimeShort(t.created_at)}</td>
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

// Both the Items and Stock pages call this at boot now (both need View),
// so it guards against double-binding the same singleton modal's listeners
// twice rather than requiring callers to coordinate who initializes it.
let unitDetailModalInitialized = false;

export function initUnitDetailModal() {
    if (unitDetailModalInitialized) return;
    unitDetailModalInitialized = true;
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

// ---- Asset/Cable Core's "View" drill-down — the list of individual units
// (serials/reels) behind one SKU/spec row, each carrying its own View (into
// openUnitDetailModal above) / Edit / Delete. Shared by both the Items and
// Stock pages — a single implementation, not two copies, since it shows the
// exact same data regardless of which page opened it. `onChanged` (passed
// in via openStockHistory below) is called after any unit-level edit or
// delete so the caller's own SKU-aggregate table (Qty/Cost/Value) can
// refresh — the unit list doesn't know or care which page that is.
let unitListCache = [];
let unitListOnChanged = null;
let editItemId = null;
let unitListPage = 1;
let unitListPageSize = 10;
// Persisted separately from unitListCache (rather than read off its first
// row) because the depleted-reel filter below can legitimately leave the
// cache empty — a spec whose every reel is used up still needs to know what
// it's looking at in order to refetch/relabel itself.
let unitListCategoryId = null;
let unitListSkuOrSpec = null;
let unitListTrackingType = null;

// The Units view answers "what do I currently have" — a Cable Core reel
// that's been fully used and reconciled to 0 remaining isn't current stock
// (functionally different from an Asset being "deployed", which is still
// owned/trackable). Excluded here, by default, for Length-type rows only;
// every reel ever made — depleted included — is still reachable via
// Reports' Cable Type Summary drill-down (openCableDrillDownModal below).
function filterUnitListDepleted(items) {
    return items.filter(item => item.tracking_type !== "inventory_length" || item.length_remaining > 0);
}

function unitRowCells(item) {
    if (item.tracking_type === "asset_serialized") {
        const holder = assignableUserName(item.assigned_to_user_id);
        return `
            <td>${item.id}</td>
            <td>${item.serial_number || "—"}</td>
            <td>${assetStatusBadgeHtml(item.asset_status)}</td>
            <td>${holder || item.location_name || "—"}</td>
        `;
    }
    return `
        <td>${item.cut_reel_id || "—"}</td>
        <td>${item.length_remaining ?? "—"} ${item.unit_of_measure || "m"}</td>
        <td>${item.location_name || "—"}</td>
    `;
}

// "Asset ID" is the row's own id — a stable per-unit identifier distinct
// from the SKU shared by every row in this list (already shown in the
// modal's header) and from Serial Number, which can be blank before one's
// assigned.
function unitListHeadRow(trackingType) {
    return trackingType === "asset_serialized"
        ? `<tr><th>Asset ID</th><th>Serial Number</th><th>Status</th><th>Location/Holder</th><th>Actions</th></tr>`
        : `<tr><th>Reel ID</th><th>Remaining</th><th>Location</th><th>Actions</th></tr>`;
}

// Paginated the same way the per-unit Logs tab and Reports' drill-downs
// already are (page-size 10/20/50/100 + Prev/Next, client-side over the
// already-fetched cache) — batch-add can now create dozens of units under
// one SKU in a single sitting, so this list needs to handle that gracefully
// rather than rendering every row at once.
function renderUnitList() {
    const thead = document.getElementById("unit-list-thead");
    const tbody = document.getElementById("unit-list-rows");
    const paginationEl = document.getElementById("unit-list-pagination");
    if (!thead || !tbody) return;

    thead.innerHTML = unitListHeadRow(unitListTrackingType);

    if (unitListCache.length === 0) {
        // Reachable now that depleted Cable Core reels are filtered out by
        // default — a spec that's fully used up still gets an explicit
        // empty state rather than a blank box.
        const colspan = unitListTrackingType === "asset_serialized" ? 5 : 4;
        tbody.innerHTML = `<tr><td colspan="${colspan}" class="dashboard-log-empty">No units currently in stock.</td></tr>`;
        if (paginationEl) paginationEl.innerHTML = "";
        return;
    }

    const total = unitListCache.length;
    const totalPages = Math.ceil(total / unitListPageSize);
    if (unitListPage > totalPages) unitListPage = totalPages;
    const start = (unitListPage - 1) * unitListPageSize;
    const pageRows = unitListCache.slice(start, start + unitListPageSize);

    tbody.innerHTML = pageRows.map(item => `
        <tr>
            ${unitRowCells(item)}
            <td>
                <button type="button" class="inventory-icon-btn view unit-view-btn" data-id="${item.id}" title="View history">
                    ${viewIconSvg()}
                </button>
                ${can("inventory_items", "edit") ? `
                <button type="button" class="inventory-icon-btn edit unit-edit-btn" data-id="${item.id}" title="Edit">
                    ${editIconSvg()}
                </button>` : ""}
                ${can("inventory_items", "delete") ? `
                <button type="button" class="inventory-icon-btn delete unit-delete-btn" data-id="${item.id}" data-name="${item.name}" title="Delete">
                    ${deleteIconSvg()}
                </button>` : ""}
            </td>
        </tr>
    `).join("");

    tbody.querySelectorAll(".unit-view-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.getElementById("unit-list-overlay").hidden = true;
            openUnitDetailModal(btn.dataset.id);
        });
    });
    tbody.querySelectorAll(".unit-edit-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            // Two .modal-overlay elements stack by DOM order at equal
            // z-index, so the still-open unit list (later in the DOM) would
            // otherwise paint over the edit modal and swallow its clicks.
            document.getElementById("unit-list-overlay").hidden = true;
            openEditItemModal(btn.dataset.id);
        });
    });
    tbody.querySelectorAll(".unit-delete-btn").forEach(btn => {
        btn.addEventListener("click", () => deleteItem(btn.dataset.id, btn.dataset.name));
    });

    if (!paginationEl) return;
    paginationEl.innerHTML = `
        <div class="dashboard-logs-page-size">
            <span>Rows:</span>
            <select id="unit-list-page-size-select">
                ${[10, 20, 50, 100].map(n => `<option value="${n}" ${n === unitListPageSize ? "selected" : ""}>${n}</option>`).join("")}
            </select>
        </div>
        <div class="dashboard-logs-page-nav">
            <button type="button" id="unit-list-prev-btn" ${unitListPage === 1 ? "disabled" : ""}>Prev</button>
            <span>Page ${unitListPage} of ${totalPages}</span>
            <button type="button" id="unit-list-next-btn" ${unitListPage === totalPages ? "disabled" : ""}>Next</button>
        </div>
    `;
    document.getElementById("unit-list-page-size-select").addEventListener("change", (e) => {
        unitListPageSize = Number(e.target.value);
        unitListPage = 1;
        renderUnitList();
    });
    document.getElementById("unit-list-prev-btn").addEventListener("click", () => {
        unitListPage = Math.max(1, unitListPage - 1);
        renderUnitList();
    });
    document.getElementById("unit-list-next-btn").addEventListener("click", () => {
        unitListPage = Math.min(totalPages, unitListPage + 1);
        renderUnitList();
    });
}

async function openUnitListModal(categoryId, sku, trackingType, categoryName) {
    const overlay = document.getElementById("unit-list-overlay");
    if (!overlay) return;

    unitListCategoryId = categoryId;
    unitListSkuOrSpec = sku;
    unitListTrackingType = trackingType;

    document.getElementById("unit-list-label").textContent = `— ${categoryName} — ${sku}`;
    const res = await fetch(`/inventory/items?category_id=${categoryId}&sku=${encodeURIComponent(sku)}`, { headers: authHeaders() });
    unitListCache = filterUnitListDepleted(res.ok ? await res.json() : []);
    unitListPage = 1;
    renderUnitList();
    overlay.hidden = false;
}

async function refreshUnitListIfOpen() {
    const overlay = document.getElementById("unit-list-overlay");
    if (!overlay || overlay.hidden || unitListCategoryId === null) return;
    const res = await fetch(`/inventory/items?category_id=${unitListCategoryId}&sku=${encodeURIComponent(unitListSkuOrSpec)}`, { headers: authHeaders() });
    unitListCache = filterUnitListDepleted(res.ok ? await res.json() : []);
    renderUnitList();
}

async function deleteItem(id, name) {
    if (!confirm(`Delete item "${name}"? This can't be undone.`)) return;

    const response = await fetch(`/inventory/items/${id}`, { method: "DELETE", headers: authHeaders() });
    if (response.ok) {
        await refreshUnitListIfOpen();
        if (unitListOnChanged) unitListOnChanged();
    } else {
        alert("Failed to delete item — it may still have transaction history tied to it.");
    }
}

function openEditItemModal(itemId) {
    const item = unitListCache.find(i => String(i.id) === String(itemId));
    if (!item) return;

    editItemId = item.id;
    const form = document.getElementById("edit-inventory-item-form");
    showTypeFields(form, item.tracking_type);

    // Unit-level fields only — SKU/Name/Spec/UoM are product-level and
    // edited via the Edit Product modal instead (see architecture.md's note
    // on this split). Make/Model and Supplier moved down to this unit-level
    // form (they can genuinely vary batch-to-batch or unit-to-unit even
    // under one SKU) — Supplier is universal across all three Cores, so it
    // sits outside the type-specific blocks below; Make/Model only exists
    // on Asset Core. Never Qty either — quantity_on_hand and
    // length_remaining aren't offered as form fields here at all, since
    // quantity only ever changes as the result of a logged transaction (In,
    // Transfer, Adjustment, Issue, Return, Write-off), not a direct field
    // edit. A miscount goes through an Adjustment transaction on the
    // Transaction Log instead, which leaves an attributed record rather
    // than a silent overwrite.
    document.getElementById("edit-inventory-item-location").value = item.location_name || "";
    document.getElementById("edit-inventory-item-unit-cost").value = item.unit_cost ?? "";
    document.getElementById("edit-inventory-item-supplier").value = item.supplier || "";
    document.getElementById("edit-inventory-item-notes").value = item.notes || "";

    if (item.tracking_type === "asset_serialized") {
        document.getElementById("edit-inventory-item-serial-number").value = item.serial_number || "";
        document.getElementById("edit-inventory-item-asset-status").value = item.asset_status || "Active";
        // Assigned To is only relevant for custody-type categories — a
        // per-job asset doesn't have a "who's holding it" concept the same
        // way, per the Add Item wizard's same rule.
        const category = getInventoryCategories().find(c => c.id === item.category_id);
        document.getElementById("edit-inventory-item-assigned-to-row").hidden = !(category && category.custody_type === "custody");
        document.getElementById("edit-inventory-item-assigned-to").value = item.assigned_to_user_id || "";
        document.getElementById("edit-inventory-item-make-model").value = item.make_model || "";
        // Defaults to today when unset (not left blank) — see
        // todayDateString()'s comment; still fully editable for backdating.
        document.getElementById("edit-inventory-item-install-date").value = item.install_date || todayDateString();
    } else if (item.tracking_type === "inventory_quantity") {
        document.getElementById("edit-inventory-item-batch-lot").value = item.batch_lot || "";
        document.getElementById("edit-inventory-item-expiry-date").value = item.expiry_date || todayDateString();
    } else if (item.tracking_type === "inventory_length") {
        document.getElementById("edit-inventory-item-cut-reel-id").value = item.cut_reel_id || "";
        document.getElementById("edit-inventory-item-length-received").value = item.length_received ?? "";
        document.getElementById("edit-inventory-item-length-status").value = item.length_status || "In Stock";
    }

    document.getElementById("edit-inventory-item-overlay").hidden = false;
}

function closeEditItemModal() {
    document.getElementById("edit-inventory-item-overlay").hidden = true;
    editItemId = null;
}

let unitListModalInitialized = false;

// Wires both the unit-list overlay's own close behavior and the Edit Unit
// form it opens into — one shared init, called from both pages' boot.
export function initUnitListModal() {
    if (unitListModalInitialized) return;
    unitListModalInitialized = true;

    const overlay = document.getElementById("unit-list-overlay");
    document.getElementById("unit-list-close").addEventListener("click", () => { overlay.hidden = true; });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });

    const editOverlay = document.getElementById("edit-inventory-item-overlay");
    const editForm = document.getElementById("edit-inventory-item-form");
    document.getElementById("edit-inventory-item-cancel").addEventListener("click", closeEditItemModal);
    editOverlay.addEventListener("click", (e) => { if (e.target === editOverlay) closeEditItemModal(); });

    editForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!editItemId) return;
        const item = unitListCache.find(i => String(i.id) === String(editItemId));
        if (!item) return;

        // SKU/Name/Spec/UoM aren't shown or editable in this form — carried
        // through unchanged from the existing item so the PATCH (which
        // still expects sku/name as required fields) doesn't blank them
        // out. Editing those for real goes through the Edit Product modal /
        // PATCH /inventory/products instead. Supplier (and, for Asset Core,
        // Make/Model below) IS read fresh from this form now — both moved
        // down to unit level since they can genuinely vary per unit under
        // one SKU (see architecture.md's note on this split).
        const body = {
            sku: item.sku,
            name: item.name,
            supplier: document.getElementById("edit-inventory-item-supplier").value || null,
            unit_of_measure: item.unit_of_measure,
            // Free-typed — resolved server-side to a location_id (creating
            // a new is_store=true row if the name doesn't match one yet),
            // same as Issue/Return Materials' Site field.
            location_name: document.getElementById("edit-inventory-item-location").value.trim() || null,
            unit_cost: numOrNull(document.getElementById("edit-inventory-item-unit-cost").value),
            notes: document.getElementById("edit-inventory-item-notes").value || null,
        };

        if (item.tracking_type === "asset_serialized") {
            body.serial_number = document.getElementById("edit-inventory-item-serial-number").value || null;
            body.asset_status = document.getElementById("edit-inventory-item-asset-status").value;
            const assignedToHidden = document.getElementById("edit-inventory-item-assigned-to-row").hidden;
            body.assigned_to_user_id = assignedToHidden ? null : numOrNull(document.getElementById("edit-inventory-item-assigned-to").value);
            body.make_model = document.getElementById("edit-inventory-item-make-model").value || null;
            body.spec_capacity = item.spec_capacity;
            body.install_date = document.getElementById("edit-inventory-item-install-date").value || null;
        } else if (item.tracking_type === "inventory_quantity") {
            body.batch_lot = document.getElementById("edit-inventory-item-batch-lot").value || null;
            body.expiry_date = document.getElementById("edit-inventory-item-expiry-date").value || null;
            // Carried through unchanged, not read from a form field — the
            // backend requires it on every PATCH, but this form doesn't
            // offer a way to change it (see the comment above and
            // stock.html's note on the removed input).
            body.quantity_on_hand = item.quantity_on_hand;
        } else if (item.tracking_type === "inventory_length") {
            body.cut_reel_id = document.getElementById("edit-inventory-item-cut-reel-id").value || null;
            body.spec = item.spec;
            body.length_received = numOrNull(document.getElementById("edit-inventory-item-length-received").value);
            // Carried through unchanged — same reasoning as quantity_on_hand
            // above.
            body.length_remaining = item.length_remaining;
            body.length_status = document.getElementById("edit-inventory-item-length-status").value;
        }

        const response = await fetch(`/inventory/items/${editItemId}`, {
            method: "PATCH",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body)
        });

        if (response.ok) {
            closeEditItemModal();
            // Refreshes the location cache too — a brand-new store typed
            // into the Location field above should show up immediately if
            // this same modal is used to edit another unit next, same as
            // Issue Materials already does for its own Site field.
            await Promise.all([refreshUnitListIfOpen(), loadInventoryLocations()]);
            populateLocationDropdowns();
            if (unitListOnChanged) unitListOnChanged();
        } else {
            const err = await response.json().catch(() => ({}));
            alert(err.detail || "Failed to update item");
        }
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

let skuHistoryModalInitialized = false;

export function initSkuHistoryModal() {
    if (skuHistoryModalInitialized) return;
    skuHistoryModalInitialized = true;
    const overlay = document.getElementById("sku-history-overlay");
    document.getElementById("sku-history-close").addEventListener("click", () => { overlay.hidden = true; });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });
}

// ---- The single "View" entry point — identical behavior no matter which
// page (Items or Stock) opens it, per the owner's explicit requirement that
// this be one shared component, not two copies. Branches by Core: Asset/
// Cable open the unit list above; Consumable has no individual units, so it
// opens the flat running-balance history instead. `onChanged` is only
// meaningful for the unit-list branch (Consumable's history has no
// edit/delete of its own to react to) — stored for the unit list's own
// edit/delete handlers to call once they're done.
export function openStockHistory({ categoryId, sku, trackingType, categoryName, onChanged }) {
    if (trackingType === "inventory_quantity") {
        openSkuHistoryModal(categoryId, sku, `${categoryName} — ${sku}`);
    } else {
        unitListOnChanged = onChanged || null;
        openUnitListModal(Number(categoryId), sku, trackingType, categoryName);
    }
}

// ---- Product edit/delete — the Items and Stock tables' shared row actions
// (both pages show the same SKU-aggregate rows now). Applies to every
// active unit under a SKU/spec group at once (see
// db/inventory_items.py's update_product). Deliberately separate from the
// unit-level Edit Unit modal above: SKU/Name/Category/Spec/UoM live here
// only, never in the per-unit form. Make/Model and Supplier live the other
// way around — unit-level only, never here — since both can genuinely vary
// batch-to-batch or unit-to-unit even under one SKU (different suppliers at
// different costs; potentially different actual brands under one loosely-
// defined SKU). And never Qty, on either form, per the
// no-manual-quantity-edit rule.
let editProductOriginal = null; // { category_id, sku_or_spec, tracking_type }
let editProductOnSaved = null;

function populateEditProductCategoryOptions(trackingType) {
    const select = document.getElementById("edit-product-category");
    const options = getInventoryCategories().filter(c => c.tracking_type === trackingType);
    select.innerHTML = options.map(c => `<option value="${c.id}">${c.name}</option>`).join("");
}

// No products table — the representative unit is just the first active
// item in this SKU/spec group, read from the already-loaded all-items
// cache (same lookup buildProductList() uses).
export function openEditProductModal(categoryId, skuOrSpec, onSaved) {
    const representative = getAllInventoryItems().find(i =>
        i.category_id === categoryId && (i.tracking_type === "inventory_length" ? i.spec : i.sku) === skuOrSpec
    );
    if (!representative) {
        alert("No units found under this product.");
        return;
    }

    editProductOriginal = { category_id: categoryId, sku_or_spec: skuOrSpec, tracking_type: representative.tracking_type };
    editProductOnSaved = onSaved || null;

    const form = document.getElementById("edit-product-form");
    showTypeFields(form, representative.tracking_type);
    populateEditProductCategoryOptions(representative.tracking_type);
    // A `required` field inside a hidden block still fails checkValidity()
    // in Chromium — see the same fix on the Add Item wizard's Step 1.
    const isCable = representative.tracking_type === "inventory_length";
    document.getElementById("edit-product-spec").required = isCable;

    // Cable Core has no separate SKU concept — Spec is the sole identifying
    // field, so the SKU input is hidden (not just optional) for this Core.
    // The category dropdown above only ever offers same-Core categories
    // (see populateEditProductCategoryOptions), so this can't change mid-
    // edit the way Add Product's can — a one-time toggle at open is enough,
    // no live change listener needed here.
    const skuField = document.getElementById("edit-product-sku");
    skuField.hidden = isCable;
    skuField.required = !isCable;

    document.getElementById("edit-product-category").value = categoryId;
    skuField.value = isCable ? "" : (representative.sku || "");
    document.getElementById("edit-product-name").value = representative.name || "";
    document.getElementById("edit-product-spec-capacity").value = representative.spec_capacity || "";
    document.getElementById("edit-product-spec").value = representative.spec || "";

    document.getElementById("edit-product-msg").textContent = "";
    document.getElementById("edit-product-overlay").hidden = false;
}

function closeEditProductModal() {
    document.getElementById("edit-product-overlay").hidden = true;
    editProductOriginal = null;
    editProductOnSaved = null;
}

export async function deleteProduct(categoryId, skuOrSpec, name, onDeleted) {
    if (!confirm(`Delete product "${name}"? This only succeeds if no units remain under it.`)) return;

    const response = await fetch(
        `/inventory/products?category_id=${categoryId}&sku_or_spec=${encodeURIComponent(skuOrSpec)}`,
        { method: "DELETE", headers: authHeaders() }
    );
    if (response.ok) {
        if (onDeleted) await onDeleted();
    } else {
        const err = await response.json().catch(() => ({}));
        alert(err.detail || "Failed to delete product");
    }
}

let editProductModalInitialized = false;

export function initEditProductModal() {
    if (editProductModalInitialized) return;
    editProductModalInitialized = true;

    const overlay = document.getElementById("edit-product-overlay");
    document.getElementById("edit-product-cancel").addEventListener("click", closeEditProductModal);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) closeEditProductModal(); });

    document.getElementById("edit-product-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!editProductOriginal) return;

        const body = {
            category_id: editProductOriginal.category_id,
            sku_or_spec: editProductOriginal.sku_or_spec,
            new_category_id: Number(document.getElementById("edit-product-category").value),
            sku: document.getElementById("edit-product-sku").value,
            name: document.getElementById("edit-product-name").value,
            spec_capacity: document.getElementById("edit-product-spec-capacity").value || null,
            spec: document.getElementById("edit-product-spec").value || null,
        };

        const response = await fetch("/inventory/products", {
            method: "PATCH",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body)
        });

        if (response.ok) {
            const onSaved = editProductOnSaved;
            closeEditProductModal();
            await Promise.all([loadAllInventoryItems(), onSaved ? onSaved() : Promise.resolve()]);
        } else {
            const err = await response.json().catch(() => ({}));
            showMessage("edit-product-msg", err.detail || "Failed to update product", true);
        }
    });
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
