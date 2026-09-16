import {
    can, authHeaders, viewIconSvg,
    registerAppShownHandler, showView, registerRoute, navigate,
} from "./common.js";
import {
    loadInventoryCategories, getInventoryCategories,
    loadInventoryLocations, loadInventoryAssignableUsers,
    loadAllInventoryItems,
    money, unitCostCell, unitCostText, populateLocationDropdowns, populateAssignedToDropdowns,
    openStockHistory, initUnitListModal, initUnitDetailModal, initSkuHistoryModal,
    exportRowsToCsv, inventorySummaryRowMatchesQuery,
} from "./inventory-common.js";

// Stock: the operational counterpart to Items — same Name/SKU/Category/Qty/
// Unit Cost/Total Value shape (same get_items_summary rows, same shared
// View component as Items), scoped by Status and Custody in addition to
// Category, and it's where Issue/Return Materials live. Unlike Items,
// Stock's Actions column is View-only — no Edit/Delete here, per the
// owner's explicit correction. See architecture.md's note on the
// Items/Stock split.

let stockSummaryCache = [];
let stockCategoryFilter = "";
let stockStatusFilter = "";
let stockCustodyFilter = "";
let stockSearchQuery = "";

function populateCategoryFilter() {
    const categories = getInventoryCategories();
    const options = categories.map(c => `<option value="${c.id}">${c.name}</option>`).join("");

    const filterSelect = document.getElementById("stock-category-filter");
    if (filterSelect) {
        const previous = filterSelect.value;
        filterSelect.innerHTML = `<option value="">All Categories</option>${options}`;
        filterSelect.value = categories.some(c => String(c.id) === previous) ? previous : "";
    }
}

async function refreshStock() {
    const qs = stockCategoryFilter ? `?category_id=${stockCategoryFilter}` : "";
    const res = await fetch(`/inventory/items/summary${qs}`, { headers: authHeaders() });
    stockSummaryCache = res.ok ? await res.json() : [];
    renderStockTable();
}

// Status/Custody narrow the already-fetched rows client-side and just
// switch which of on_hand/deployed/total is displayed — no re-fetch, since
// get_items_summary already returns both halves of the split per row.
function filteredStockForDisplay() {
    return stockSummaryCache.filter(row => {
        if (stockCustodyFilter && row.custody_type !== stockCustodyFilter) return false;
        if (!inventorySummaryRowMatchesQuery(row, stockSearchQuery)) return false;
        return true;
    });
}

function qtyAndValueFor(row) {
    if (stockStatusFilter === "on_hand") return { qty: row.on_hand_qty, value: row.on_hand_value };
    if (stockStatusFilter === "deployed") return { qty: row.deployed_qty, value: row.deployed_value };
    return { qty: row.total_qty, value: row.total_value };
}

// The Low badge is computed live against whatever Qty is currently on
// screen (which itself already reflects the Status filter) — not a
// stored/separately-maintained flag, and not silently checked against a
// different, hidden number than the one the row is displaying. No
// threshold set, or at/above it, shows nothing — only the exceptions get
// flagged.
function qtyCell(row, qty) {
    const isLow = row.reorder_level != null && qty <= row.reorder_level;
    return `<span class="qty-with-badge">${qty}${isLow ? ` <span class="status-pill low-stock">Low</span>` : ""}</span>`;
}

// Reorder Level lives here (edited where the SKU already is), and is also
// settable at creation via the Add Item wizard's Step 1 — both write to
// the same inventory_sku_thresholds row via the same upserting PATCH, so
// there's no separate "set later" flow. Click-to-edit rather than a
// permanently-open input — swaps the span for a number input on click,
// saves on blur/Enter. Colored red/green against the same low-stock check
// qtyCell uses (same row, same currently-displayed qty) rather than a
// neutral badge, so the number itself communicates status; unset ("—")
// stays neutral since there's no threshold to be above or below.
function reorderLevelCell(row, qty) {
    const value = row.reorder_level ?? "";
    const display = row.reorder_level ?? "—";
    const hasLevel = row.reorder_level != null;
    const statusClass = hasLevel ? (qty <= row.reorder_level ? "reorder-level-low" : "reorder-level-ok") : "";
    if (!can("inventory_items", "edit_reorder_level")) return `<span class="${statusClass}">${display}</span>`;
    return `<span class="reorder-level-cell ${statusClass}" title="Click to edit"
        data-value="${value}" data-category-id="${row.category_id}" data-sku-or-spec="${encodeURIComponent(row.sku_or_spec)}">${display}</span>`;
}

function startEditingReorderLevel(span) {
    const originalValue = span.dataset.value;
    const { categoryId, skuOrSpec } = span.dataset;

    // Same shared numeric-stepper look as every other qty/cost field in the
    // app, built by hand here (rather than via inventory-common.js's
    // initQtyStepper, which wires a static input by id) since this input is
    // created fresh on each click-to-edit with no id of its own.
    const wrapper = document.createElement("div");
    wrapper.className = "qty-stepper reorder-level-input-wrapper";
    const input = document.createElement("input");
    input.type = "number";
    input.step = "any";
    input.min = "0";
    input.className = "reorder-level-input";
    input.value = originalValue;
    wrapper.appendChild(input);

    const buttons = document.createElement("div");
    buttons.className = "qty-stepper-buttons";
    buttons.innerHTML = `
        <button type="button" class="qty-stepper-btn" data-dir="up" tabindex="-1" aria-label="Increase">▲</button>
        <button type="button" class="qty-stepper-btn" data-dir="down" tabindex="-1" aria-label="Decrease">▼</button>
    `;
    wrapper.appendChild(buttons);

    span.replaceWith(wrapper);
    input.focus();
    input.select();

    // Clicking a stepper button would otherwise blur the input first (moving
    // focus to the button) and fire commit() with the pre-bump value before
    // the click handler below ever runs — preventDefault on mousedown keeps
    // focus on the input the whole time, so the bump behaves exactly like a
    // manual edit followed by blur/Enter.
    buttons.querySelectorAll(".qty-stepper-btn").forEach(btn => {
        btn.addEventListener("mousedown", (e) => e.preventDefault());
        btn.addEventListener("click", () => {
            const current = Number(input.value) || 0;
            const next = btn.dataset.dir === "up" ? current + 1 : current - 1;
            input.value = Math.max(0, next);
        });
    });

    let settled = false;
    async function commit() {
        if (settled) return;
        settled = true;
        const raw = input.value.trim();
        if (raw !== "" && raw !== originalValue) {
            const reorderLevel = Number(raw);
            if (!Number.isNaN(reorderLevel) && reorderLevel >= 0) {
                await fetch("/inventory/reorder-level", {
                    method: "PATCH",
                    headers: authHeaders({ "Content-Type": "application/json" }),
                    body: JSON.stringify({
                        category_id: Number(categoryId),
                        sku_or_spec: decodeURIComponent(skuOrSpec),
                        reorder_level: reorderLevel,
                    }),
                });
            }
        }
        await refreshStock();
    }

    input.addEventListener("blur", commit);
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); input.blur(); }
        if (e.key === "Escape") { settled = true; renderStockTable(); }
    });
}

// Exports the same rows the table is currently showing — filteredStockForDisplay()
// already applies the Custody filter, and qtyAndValueFor() the Status filter
// (both client-side, off the Category-scoped server response), so the CSV
// matches the on-screen table exactly rather than dumping every row.
function exportStockCsv() {
    exportRowsToCsv("stock.csv", [
        { header: "Name", value: row => row.name || "—" },
        { header: "SKU", value: row => row.sku_or_spec },
        { header: "Category", value: row => row.category_name },
        { header: "Qty", value: row => qtyAndValueFor(row).qty },
        { header: "Unit Cost", value: row => unitCostText(row) },
        { header: "Total Value", value: row => money(qtyAndValueFor(row).value) },
        { header: "Reorder Level", value: row => row.reorder_level ?? "—" },
    ], filteredStockForDisplay());
}

function renderStockTable() {
    const thead = document.getElementById("stock-thead");
    const tbody = document.getElementById("stock-rows");
    if (!thead || !tbody) return;

    thead.innerHTML = `<tr><th>Name</th><th>SKU</th><th>Category</th><th>Qty</th><th>Unit Cost</th><th>Total Value</th><th>Reorder Level</th><th>Actions</th></tr>`;

    const canViewHistory = can("inventory_items", "view_history");
    const rows = filteredStockForDisplay();
    tbody.innerHTML = rows.map(row => {
        const { qty, value } = qtyAndValueFor(row);
        return `
            <tr>
                <td>${row.name || "—"}</td>
                <td>${row.sku_or_spec}</td>
                <td>${row.category_name}</td>
                <td>${qtyCell(row, qty)}</td>
                <td>${unitCostCell(row)}</td>
                <td>${money(value)}</td>
                <td>${reorderLevelCell(row, qty)}</td>
                <td>
                    ${canViewHistory ? `
                    <button type="button" class="inventory-icon-btn view view-item-btn"
                        data-category-id="${row.category_id}" data-sku="${encodeURIComponent(row.sku_or_spec)}"
                        data-tracking-type="${row.tracking_type}" data-category-name="${row.category_name}"
                        title="View stock history">
                        ${viewIconSvg()}
                    </button>` : ""}
                </td>
            </tr>
        `;
    }).join("");

    tbody.querySelectorAll(".view-item-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const { categoryId, sku, trackingType, categoryName } = btn.dataset;
            openStockHistory({
                categoryId, sku: decodeURIComponent(sku), trackingType, categoryName,
                onChanged: refreshStock,
            });
        });
    });

    tbody.querySelectorAll(".reorder-level-cell").forEach(span => {
        span.addEventListener("click", () => startEditingReorderLevel(span));
    });
}

function initFilters() {
    document.getElementById("stock-search").addEventListener("input", (e) => {
        stockSearchQuery = e.target.value;
        renderStockTable();
    });

    document.getElementById("stock-category-filter").addEventListener("change", async (e) => {
        stockCategoryFilter = e.target.value;
        await refreshStock();
    });

    document.getElementById("stock-status-filter").addEventListener("change", (e) => {
        stockStatusFilter = e.target.value;
        renderStockTable();
    });

    document.getElementById("stock-custody-filter").addEventListener("change", (e) => {
        stockCustodyFilter = e.target.value;
        renderStockTable();
    });

    document.getElementById("export-stock-csv-btn").addEventListener("click", exportStockCsv);
}

export function initStock() {
    initFilters();
    initUnitListModal();
    initUnitDetailModal();
    initSkuHistoryModal();

    // These header buttons live in this view's own fragment (loaded after
    // initShell()'s generic [data-view] click wiring already ran over
    // index.html's contents), so — like every other header quick-link in
    // this codebase (movements.js, check-sites.js) — they need their own
    // explicit wiring rather than relying on that generic pass to find them.
    document.getElementById("issue-materials-link-btn").addEventListener("click", () => {
        navigate("issue-materials");
    });

    document.getElementById("return-materials-link-btn").addEventListener("click", () => {
        navigate("return-materials");
    });

    registerAppShownHandler(async () => {
        await Promise.all([
            loadInventoryCategories(), loadInventoryLocations(),
            loadInventoryAssignableUsers(), loadAllInventoryItems(),
        ]);
        populateCategoryFilter();
        populateLocationDropdowns();
        populateAssignedToDropdowns();
        await refreshStock();
    });

    registerRoute("stock", async () => {
        showView("view-stock");
        await Promise.all([loadInventoryCategories(), loadInventoryLocations(), loadAllInventoryItems()]);
        populateCategoryFilter();
        populateLocationDropdowns();
        await refreshStock();
    });
}
