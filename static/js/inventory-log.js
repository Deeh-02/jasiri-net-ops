import {
    can, authHeaders, showMessage, registerAppShownHandler, showView, registerRoute,
    registerCmdkProvider, navigate,
} from "./common.js";
import {
    loadInventoryCategories, getInventoryCategories,
    loadInventoryLocations, populateLocationDropdowns,
    loadAllInventoryItems, getAllInventoryItems, itemPickerLabel,
    inventoryItemMatchesQuery, exportRowsToCsv, initQtyStepper,
} from "./inventory-common.js";

let logCategoryFilter = "";
let logActionFilter = "";
let logSearchQuery = "";

function numOrNull(value) {
    return value === "" || value === null || value === undefined ? null : Number(value);
}

function populateLogDropdowns() {
    const categories = getInventoryCategories();
    const categoryFilter = document.getElementById("inventory-log-category-filter");
    if (categoryFilter) {
        categoryFilter.innerHTML = `<option value="">All Categories</option>` +
            categories.map(c => `<option value="${c.id}">${c.name}</option>`).join("");
    }

    // Transfer's From/To fields are free-typed autocomplete now (see
    // populateLocationDropdowns), not a picklist built here — Site/Activity/
    // Issued To no longer exist on this form at all (Return always resolves
    // to the default store server-side; the other three actions never had a
    // "which job" concept in the first place — that's what Issue Materials
    // is for).
    populateLocationDropdowns();
}

async function refreshLog() {
    const params = new URLSearchParams();
    if (logCategoryFilter) params.set("category_id", logCategoryFilter);
    if (logActionFilter) params.set("action", logActionFilter);
    const qs = params.toString() ? `?${params.toString()}` : "";

    const res = await fetch(`/inventory/transactions${qs}`, { headers: authHeaders() });
    const rows = res.ok ? await res.json() : [];
    renderLogTable(rows);
}

function formatDateTime(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleString([], {
        year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
        timeZone: "Africa/Nairobi",
    });
}

// "Out" is Issue Materials' stored action (see issue_cart in
// db/inventory_transactions.py) — displayed as "Issue" everywhere except a
// still-open cable cut, which reads "Pending" instead. This is a pure
// display-time computation off the row's own `status` field (already
// flipped from 'open_pending' to 'closed' by reconcile_cut, in the same
// commit that writes the Reconciled row) — the stored `action` value itself
// is never touched, preserving the append-only log.
function displayAction(row) {
    if (row.action !== "Out") return row.action;
    return row.status === "open_pending" ? "Pending" : "Issue";
}

// One adaptive string replacing the old separate From/To/Site columns —
// what "moved" means differs by action, so this is the one place that
// distinction lives now that the table itself doesn't show it column by column.
function movementCell(row) {
    switch (row.action) {
        case "In":
            return row.to_location_name ? `→ ${row.to_location_name}` : "—";
        case "Transfer":
        case "Return":
            return `${row.from_location_name || "—"} → ${row.to_location_name || "—"}`;
        case "Out": {
            const who = row.issued_to_name ? `${row.issued_to_name}, ` : "";
            return `→ ${who}${row.site_location_name || "—"}`;
        }
        default:
            // Adjustment / Write-off / Reconciled: nothing physically moved.
            return "—";
    }
}

let logRowsCache = [];
let logPage = 1;
let logPageSize = 10;

// Matches a log row against the in-page search box: the row's own SKU/Spec
// and "By" first, then falls back to the underlying item (via item_id) for
// a serial number, batch/lot, or reel id the row itself doesn't display —
// same fallback shape as inventorySummaryRowMatchesQuery on Items/Stock.
function logRowMatchesQuery(row, query) {
    const q = query.toLowerCase().trim();
    if (!q) return true;
    const haystack = [row.sku_or_spec, row.logged_by_name].filter(Boolean).join(" ").toLowerCase();
    if (haystack.includes(q)) return true;
    const item = getAllInventoryItems().find(i => i.id === row.item_id);
    return item ? inventoryItemMatchesQuery(item, q) : false;
}

// The full server-filtered (category/action) set the search box narrows
// client-side — pagination and CSV export both read from this, not
// logRowsCache directly, so a search always applies to the full result set,
// not just whatever page happens to be showing.
function visibleLogRows() {
    return logRowsCache.filter(row => logRowMatchesQuery(row, logSearchQuery));
}

// Paginated client-side, same convention as the Battery view modal's Logs
// tab (page-size 10/20/50/100 + Prev/Next) — only the current page's rows
// are ever rendered into the table, and the page itself scrolls normally if
// needed; there's no separate fixed-height scroll box nested inside it.
function renderLogTable(rows) {
    if (rows !== undefined) {
        logRowsCache = rows;
        logPage = 1;
    }
    const tbody = document.getElementById("inventory-log-rows");
    const paginationEl = document.getElementById("inventory-log-pagination");
    if (!tbody || !paginationEl) return;

    const visibleRows = visibleLogRows();
    const total = visibleRows.length;
    if (total === 0) {
        const emptyMsg = logRowsCache.length === 0 ? "No transactions yet." : "No matching transactions.";
        tbody.innerHTML = `<tr><td colspan="6" class="dashboard-log-empty">${emptyMsg}</td></tr>`;
        paginationEl.innerHTML = "";
        return;
    }

    const totalPages = Math.ceil(total / logPageSize);
    if (logPage > totalPages) logPage = totalPages;
    const start = (logPage - 1) * logPageSize;
    const pageRows = visibleRows.slice(start, start + logPageSize);

    tbody.innerHTML = pageRows.map((row) => {
        const index = visibleRows.indexOf(row);
        // Same condition displayAction() uses to show "Pending" as the
        // Action text — this just adds a visual accent on top of it so a
        // pending row is scannable without reading that text on every row.
        const isPending = row.action === "Out" && row.status === "open_pending";
        return `
        <tr class="inventory-log-row${isPending ? " inventory-log-row-pending" : ""}" data-index="${index}">
            <td>${formatDateTime(row.created_at)}</td>
            <td>${displayAction(row)}</td>
            <td>${row.sku_or_spec}</td>
            <td>${row.qty_or_length ?? "—"}</td>
            <td>${movementCell(row)}</td>
            <td>${row.logged_by_name}</td>
        </tr>
    `;
    }).join("");

    tbody.querySelectorAll(".inventory-log-row").forEach(tr => {
        tr.addEventListener("click", () => openTransactionDetail(visibleRows[Number(tr.dataset.index)]));
    });

    paginationEl.innerHTML = `
        <div class="dashboard-logs-page-size">
            <span>Rows:</span>
            <select id="inventory-log-page-size-select">
                ${[10, 20, 50, 100].map(n => `<option value="${n}" ${n === logPageSize ? "selected" : ""}>${n}</option>`).join("")}
            </select>
        </div>
        <div class="dashboard-logs-page-nav">
            <button type="button" id="inventory-log-prev-btn" ${logPage === 1 ? "disabled" : ""}>Prev</button>
            <span>Page ${logPage} of ${totalPages}</span>
            <button type="button" id="inventory-log-next-btn" ${logPage === totalPages ? "disabled" : ""}>Next</button>
        </div>
    `;

    document.getElementById("inventory-log-page-size-select").addEventListener("change", (e) => {
        logPageSize = Number(e.target.value);
        logPage = 1;
        renderLogTable();
    });
    document.getElementById("inventory-log-prev-btn").addEventListener("click", () => {
        logPage = Math.max(1, logPage - 1);
        renderLogTable();
    });
    document.getElementById("inventory-log-next-btn").addEventListener("click", () => {
        logPage = Math.min(totalPages, logPage + 1);
        renderLogTable();
    });
}

// Exports the full filtered set (logRowsCache — already scoped by
// logCategoryFilter/logActionFilter server-side), not just the current
// page, using the same six on-screen columns and display formatting.
function exportLogCsv() {
    exportRowsToCsv("transaction-log.csv", [
        { header: "Date", value: row => formatDateTime(row.created_at) },
        { header: "Action", value: row => displayAction(row) },
        { header: "SKU/Spec", value: row => row.sku_or_spec },
        { header: "Qty/Length", value: row => row.qty_or_length ?? "—" },
        { header: "Movement", value: row => movementCell(row) },
        { header: "By", value: row => row.logged_by_name },
    ], visibleLogRows());
}

function detailRow(label, value) {
    return `<div class="dashboard-detail-row"><span>${label}</span><span>${value ?? "—"}</span></div>`;
}

// Everything the table used to show as its own column (Site, Activity,
// Issued To, exact From/To, Status, Notes) still exists on the row — it just
// moved here, into a click-through detail view, rather than being deleted.
async function openTransactionDetail(row) {
    const overlay = document.getElementById("transaction-detail-overlay");
    if (!overlay) return;

    document.getElementById("transaction-detail-label").textContent = `— ${row.sku_or_spec}`;

    const fields = [
        detailRow("Date", formatDateTime(row.created_at)),
        detailRow("Action", displayAction(row)),
        detailRow("Category", row.category_name),
        detailRow("Qty/Length", row.qty_or_length),
        detailRow("From location", row.from_location_name),
        detailRow("To location", row.to_location_name),
        detailRow("Site", row.site_location_name),
        detailRow("Activity", row.activity),
        detailRow("Issued to", row.issued_to_name),
    ];
    if (row.action === "Reconciled") {
        fields.push(detailRow("Length used", row.length_used), detailRow("Length returned", row.length_returned));
    }
    fields.push(detailRow("Logged by", row.logged_by_name), detailRow("Notes", row.notes));
    document.getElementById("transaction-detail-fields").innerHTML = fields.join("");

    // Reconcile used to be reachable only from the standalone Pending Cable
    // Cuts table — folding pending status into this row's own detail view
    // means this is its new (only) entry point.
    const actionsEl = document.getElementById("transaction-detail-actions");
    actionsEl.innerHTML = "";
    const isPendingCut = row.action === "Out" && row.status === "open_pending";
    if (isPendingCut && can("inventory_transactions", "reconcile")) {
        const res = await fetch(`/inventory/items/${row.item_id}`, { headers: authHeaders() });
        if (res.ok) {
            const item = await res.json();
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "btn-primary";
            btn.textContent = "Reconcile";
            btn.addEventListener("click", () => {
                overlay.hidden = true;
                openReconcileModal({ item_id: item.id, cut_reel_id: item.cut_reel_id, length_out: row.qty_or_length });
            });
            actionsEl.appendChild(btn);
        }
    }

    overlay.hidden = false;
}

function initTransactionDetailModal() {
    const overlay = document.getElementById("transaction-detail-overlay");
    document.getElementById("transaction-detail-close").addEventListener("click", () => { overlay.hidden = true; });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });
}

// Type-to-filter Item search for the Log Transaction form — same
// interaction and same field set (inventoryItemMatchesQuery) as Issue/Return
// Materials' search box, just scoped to one form field with a single
// required selection instead of a whole search+cart layout. No type/status
// exclusion is applied here (unlike Issue/Return): a manual log entry may
// legitimately need to target any item regardless of its current state —
// e.g. Write-off against a depleted reel, or Adjustment against an item
// that's not currently in stock.
function renderItemSearchSuggestions(query) {
    const box = document.getElementById("inventory-transaction-item-suggestions");
    if (!query.trim()) {
        box.hidden = true;
        box.innerHTML = "";
        return;
    }

    const matches = getAllInventoryItems()
        .filter(item => inventoryItemMatchesQuery(item, query))
        .slice(0, 20);

    box.innerHTML = matches.length === 0
        ? `<div class="item-search-empty">No matching items</div>`
        : matches.map(item => `<div class="item-search-option" data-id="${item.id}">${itemPickerLabel(item)}</div>`).join("");
    box.hidden = false;

    box.querySelectorAll(".item-search-option").forEach(opt => {
        // mousedown (not click) + preventDefault so the input never blurs
        // (and hides the box) before the click lands — same trick as the
        // battery "Moved by"/"Move to" typeaheads.
        opt.addEventListener("mousedown", (e) => {
            e.preventDefault();
            selectTransactionItem(Number(opt.dataset.id));
        });
    });
}

function selectTransactionItem(itemId) {
    const item = getAllInventoryItems().find(i => i.id === itemId);
    if (!item) return;
    document.getElementById("inventory-transaction-item-search").value = itemPickerLabel(item);
    const hidden = document.getElementById("inventory-transaction-item");
    hidden.value = String(item.id);
    hidden.dispatchEvent(new Event("change"));
    document.getElementById("inventory-transaction-item-suggestions").hidden = true;
}

function initItemSearchField() {
    const input = document.getElementById("inventory-transaction-item-search");
    const hidden = document.getElementById("inventory-transaction-item");

    input.addEventListener("input", (e) => {
        // Typing anything invalidates a previous selection — the hidden id
        // only ever gets set back by actually clicking a suggestion, so a
        // stale item can never be submitted under edited-but-unselected text.
        if (hidden.value) {
            hidden.value = "";
            hidden.dispatchEvent(new Event("change"));
        }
        renderItemSearchSuggestions(e.target.value);
    });
    input.addEventListener("focus", (e) => renderItemSearchSuggestions(e.target.value));
    input.addEventListener("blur", () => {
        document.getElementById("inventory-transaction-item-suggestions").hidden = true;
    });
}

// The field set changes shape by Action, per the owner's brief — a Return
// never needed a "To location" (see _plan_return, always the default store)
// and Adjustment/Write-off never touch location at all; Transfer is the only
// action that shows either field. Site/Activity/Issued To are gone from this
// form entirely — those only ever applied to Issue, which has its own
// dedicated Issue Materials screen.
function updateActionFieldVisibility() {
    const actionSelect = document.getElementById("inventory-transaction-action");
    const itemSelect = document.getElementById("inventory-transaction-item");
    const assetStatusRow = document.getElementById("inventory-transaction-asset-status-row");
    const assetStatusSelect = document.getElementById("inventory-transaction-asset-status");
    const toLocationRow = document.getElementById("inventory-transaction-to-location-row");
    const toLocationInput = document.getElementById("inventory-transaction-to-location");
    const fromLocationRow = document.getElementById("inventory-transaction-from-location-row");

    const action = actionSelect.value;
    const item = getAllInventoryItems().find(i => String(i.id) === itemSelect.value);

    const isTransfer = action === "Transfer";
    toLocationRow.hidden = !isTransfer;
    toLocationInput.required = isTransfer;
    fromLocationRow.hidden = !isTransfer;
    if (!isTransfer) { toLocationInput.value = ""; document.getElementById("inventory-transaction-from-location").value = ""; }

    // Required (and shown) only for a Return of a serialized asset — never
    // inferred from the asset's prior status (see routers/inventory.py's
    // _plan_return).
    const showAssetStatus = action === "Return" && item && item.tracking_type === "asset_serialized";
    assetStatusRow.hidden = !showAssetStatus;
    assetStatusSelect.required = showAssetStatus;
    if (!showAssetStatus) assetStatusSelect.value = "";
}

function initTransactionForm() {
    const addOverlay = document.getElementById("add-inventory-transaction-overlay");
    const addOpenBtn = document.getElementById("add-inventory-transaction-open-btn");
    const addCancelBtn = document.getElementById("add-inventory-transaction-cancel");
    const form = document.getElementById("inventory-transaction-form");

    initItemSearchField();
    initQtyStepper("inventory-transaction-qty");

    addOpenBtn.addEventListener("click", () => {
        form.reset();
        document.getElementById("inventory-transaction-item-suggestions").hidden = true;
        updateActionFieldVisibility();
        addOverlay.hidden = false;
    });

    addCancelBtn.addEventListener("click", () => { addOverlay.hidden = true; });
    addOverlay.addEventListener("click", (e) => { if (e.target === addOverlay) addOverlay.hidden = true; });

    const actionSelect = document.getElementById("inventory-transaction-action");
    const itemSelect = document.getElementById("inventory-transaction-item");
    actionSelect.addEventListener("change", updateActionFieldVisibility);
    itemSelect.addEventListener("change", updateActionFieldVisibility);

    form.addEventListener("submit", async (e) => {
        e.preventDefault();

        const itemId = document.getElementById("inventory-transaction-item").value;
        if (!itemId) {
            showMessage("inventory-transaction-msg", "Search for and select an item", true);
            return;
        }

        const assetStatusRow = document.getElementById("inventory-transaction-asset-status-row");
        const assetStatusSelect = document.getElementById("inventory-transaction-asset-status");
        const isTransfer = document.getElementById("inventory-transaction-action").value === "Transfer";

        const body = {
            action: document.getElementById("inventory-transaction-action").value,
            item_id: Number(itemId),
            qty_or_length: numOrNull(document.getElementById("inventory-transaction-qty").value),
            to_location_name: isTransfer ? (document.getElementById("inventory-transaction-to-location").value.trim() || null) : null,
            from_location_name: isTransfer ? (document.getElementById("inventory-transaction-from-location").value.trim() || null) : null,
            asset_status: assetStatusRow.hidden ? null : (assetStatusSelect.value || null),
            notes: document.getElementById("inventory-transaction-notes").value || null,
        };

        const response = await fetch("/inventory/transactions", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body)
        });

        if (response.ok) {
            showMessage("inventory-transaction-msg", "Transaction logged", false);
            form.reset();
            addOverlay.hidden = true;
            await Promise.all([loadAllInventoryItems().then(populateLogDropdowns), loadInventoryLocations().then(populateLocationDropdowns), refreshLog()]);
        } else {
            const err = await response.json().catch(() => ({}));
            showMessage("inventory-transaction-msg", err.detail || "Failed to log transaction", true);
        }
    });

    document.getElementById("inventory-log-category-filter").addEventListener("change", async (e) => {
        logCategoryFilter = e.target.value;
        await refreshLog();
    });

    document.getElementById("inventory-log-action-filter").addEventListener("change", async (e) => {
        logActionFilter = e.target.value;
        await refreshLog();
    });

    document.getElementById("inventory-log-search").addEventListener("input", (e) => {
        logSearchQuery = e.target.value;
        logPage = 1;
        renderLogTable();
    });

    document.getElementById("export-inventory-log-csv-btn").addEventListener("click", exportLogCsv);
}

// Must match USABLE_LENGTH_THRESHOLD_M in routers/inventory.py — this is a
// cosmetic hint only, the backend is authoritative and re-validates
// independently.
const USABLE_LENGTH_THRESHOLD_M = 20;

let reconcileItemId = null;

function openReconcileModal(row) {
    reconcileItemId = row.item_id;
    document.getElementById("reconcile-cut-label").textContent = `— ${row.cut_reel_id}`;
    document.getElementById("reconcile-cut-out-hint").textContent = `${row.length_out}m went out — length used + length returned must add up to that.`;
    document.getElementById("reconcile-length-used").value = "";
    document.getElementById("reconcile-length-returned").value = "";
    document.getElementById("reconcile-new-cut-id").value = `${row.cut_reel_id}-R`;
    document.getElementById("reconcile-usable-hint").textContent = "";
    document.getElementById("reconcile-cut-overlay").hidden = false;
}

function closeReconcileModal() {
    document.getElementById("reconcile-cut-overlay").hidden = true;
    reconcileItemId = null;
}

function initReconcileForm() {
    const overlay = document.getElementById("reconcile-cut-overlay");
    const cancelBtn = document.getElementById("reconcile-cut-cancel");
    cancelBtn.addEventListener("click", closeReconcileModal);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) closeReconcileModal(); });

    document.getElementById("reconcile-length-returned").addEventListener("input", (e) => {
        const hint = document.getElementById("reconcile-usable-hint");
        const value = e.target.value;
        if (value === "") {
            hint.textContent = "";
        } else if (Number(value) >= USABLE_LENGTH_THRESHOLD_M) {
            const suggestedId = document.getElementById("reconcile-new-cut-id").value;
            hint.textContent = `Usable — will create a new cut "${suggestedId}"`;
        } else {
            hint.textContent = `Below ${USABLE_LENGTH_THRESHOLD_M}m — logged as scrap against the original cut`;
        }
    });

    document.getElementById("reconcile-cut-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!reconcileItemId) return;

        const body = {
            item_id: reconcileItemId,
            length_used: Number(document.getElementById("reconcile-length-used").value),
            length_returned: Number(document.getElementById("reconcile-length-returned").value),
            new_cut_reel_id: document.getElementById("reconcile-new-cut-id").value || null,
            notes: document.getElementById("reconcile-notes").value || null,
        };

        const response = await fetch("/inventory/reconcile", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body)
        });

        if (response.ok) {
            showMessage("reconcile-cut-msg", "Cut reconciled", false);
            closeReconcileModal();
            await Promise.all([refreshLog(), loadAllInventoryItems().then(populateLogDropdowns)]);
        } else {
            const err = await response.json().catch(() => ({}));
            showMessage("reconcile-cut-msg", err.detail || "Failed to reconcile cut", true);
        }
    });
}

export function initInventoryLog() {
    initTransactionForm();
    initReconcileForm();
    initTransactionDetailModal();

    registerAppShownHandler(async () => {
        await Promise.all([
            loadInventoryCategories(), loadInventoryLocations(), loadAllInventoryItems(),
        ]);
        populateLogDropdowns();
    });

    registerRoute("inventory-log", async () => {
        showView("view-inventory-log");
        // Re-fetched on every visit (not just at login) so an item added
        // earlier in the same session shows up in the picker immediately.
        await loadAllInventoryItems();
        populateLogDropdowns();
        await refreshLog();
    });

    // Global ⌘K coverage for the Transaction Log — its own small cache
    // (separate from logRowsCache, which is scoped to whatever
    // Category/Action filter the page itself currently has applied),
    // (re)fetched via ensureLoaded every time the palette opens so a page
    // never visited this session still gets fresh, unfiltered results.
    let cmdkTransactionsCache = [];
    registerCmdkProvider({
        ensureLoaded: async () => {
            if (!can("inventory_transactions", "view")) return;
            const res = await fetch("/inventory/transactions", { headers: authHeaders() });
            cmdkTransactionsCache = res.ok ? await res.json() : [];
        },
        getItems: () => {
            if (!can("inventory_transactions", "view")) return [];
            return cmdkTransactionsCache.map(row => {
                const item = getAllInventoryItems().find(i => i.id === row.item_id);
                return {
                    type: "inventory-transaction",
                    label: `${displayAction(row)} — ${row.sku_or_spec}`,
                    sublabel: `${formatDateTime(row.created_at)} — ${row.logged_by_name}`,
                    searchText: [
                        row.sku_or_spec, row.logged_by_name,
                        item && item.serial_number, item && item.batch_lot, item && item.cut_reel_id,
                    ].filter(Boolean).join(" "),
                    action: () => {
                        navigate("inventory-log");
                        openTransactionDetail(row);
                    },
                };
            });
        },
    });
}
