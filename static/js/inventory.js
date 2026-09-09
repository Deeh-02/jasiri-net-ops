import {
    can, authHeaders, showMessage, editIconSvg, deleteIconSvg, viewIconSvg,
    registerAppShownHandler, showView, registerRoute, navigate,
} from "./common.js";
import {
    loadInventoryCategories, getInventoryCategories,
    loadInventoryLocations, getInventoryLocations,
    loadInventoryAssignableUsers, getInventoryAssignableUsers, assignableUserName,
    openUnitDetailModal, initUnitDetailModal,
    openSkuHistoryModal, initSkuHistoryModal,
} from "./inventory-common.js";

let editItemId = null;
// Holds whichever unit list is currently open in the unit-list modal — the
// only place individual item rows are edited/deleted from now that the main
// Items table is a SKU-level rollup, not a per-row list.
let unitListCache = [];
let itemsSummaryCache = [];
let itemsCategoryFilter = "";
let itemsStatusFilter = "";
let itemsCustodyFilter = "";

// ---- Items ----

// Keeps the category select (add form + the items table's filter) and the
// location selects (add + edit forms) in sync with the cached lists —
// called after every categories/locations refresh, not just once at load,
// so a category added mid-session shows up without a full page reload.
function populateCategoryDropdowns() {
    const categories = getInventoryCategories();
    const options = categories.map(c => `<option value="${c.id}">${c.name}</option>`).join("");

    const addSelect = document.getElementById("inventory-item-category");
    if (addSelect) addSelect.innerHTML = `<option value="" disabled selected>Category...</option>${options}`;

    const filterSelect = document.getElementById("inventory-items-category-filter");
    if (filterSelect) {
        const previous = filterSelect.value;
        filterSelect.innerHTML = `<option value="">All Categories</option>${options}`;
        filterSelect.value = categories.some(c => String(c.id) === previous) ? previous : "";
    }
}

// The item add/edit form's Location picker answers "which store is this new
// stock initially shelved at" — scoped to is_store rows only, distinct from
// the free-typed Site field on Issue/Return Materials (see
// inventory-common.js's getInventoryLocations, which caches every location
// unfiltered for that autocomplete's use).
function populateLocationDropdowns() {
    const storeLocations = getInventoryLocations().filter(l => l.is_store);
    const options = storeLocations.map(l => `<option value="${l.id}">${l.name}</option>`).join("");

    const addSelect = document.getElementById("inventory-item-location");
    if (addSelect) addSelect.innerHTML = `<option value="">Location (optional)</option>${options}`;

    const editSelect = document.getElementById("edit-inventory-item-location");
    if (editSelect) editSelect.innerHTML = `<option value="">Location (optional)</option>${options}`;
}

function populateAssignedToDropdowns() {
    const users = getInventoryAssignableUsers();
    const options = users.map(u => `<option value="${u.id}">${u.name}</option>`).join("");

    const addSelect = document.getElementById("inventory-item-assigned-to");
    if (addSelect) addSelect.innerHTML = `<option value="">Assigned to (optional)</option>${options}`;

    const editSelect = document.getElementById("edit-inventory-item-assigned-to");
    if (editSelect) editSelect.innerHTML = `<option value="">Assigned to (optional)</option>${options}`;
}

// Shows only the field block matching the selected/existing tracking_type —
// the plain if/else this drives (not a schema-driven form builder) matches
// the phase plan's explicit instruction.
function showTypeFields(form, trackingType) {
    form.querySelectorAll("[data-item-type-fields]").forEach(block => {
        block.hidden = block.dataset.itemTypeFields !== trackingType;
    });
}

function numOrNull(value) {
    return value === "" || value === null || value === undefined ? null : Number(value);
}

function money(n) {
    return n == null ? "—" : Number(n).toFixed(2);
}

async function refreshItems() {
    const qs = itemsCategoryFilter ? `?category_id=${itemsCategoryFilter}` : "";
    const res = await fetch(`/inventory/items/summary${qs}`, { headers: authHeaders() });
    itemsSummaryCache = res.ok ? await res.json() : [];
    renderItemsTable();
}

// Status/Custody narrow the already-fetched rows client-side and just
// switch which of on_hand/deployed/total is displayed — no re-fetch, since
// get_items_summary already returns both halves of the split per row.
function filteredItemsForDisplay() {
    return itemsSummaryCache.filter(row => {
        if (itemsCustodyFilter && row.custody_type !== itemsCustodyFilter) return false;
        return true;
    });
}

function qtyAndValueFor(row) {
    if (itemsStatusFilter === "on_hand") return { qty: row.on_hand_qty, value: row.on_hand_value };
    if (itemsStatusFilter === "deployed") return { qty: row.deployed_qty, value: row.deployed_value };
    return { qty: row.total_qty, value: row.total_value };
}

function unitCostCell(row) {
    if (row.avg_unit_cost == null) return "—";
    // Assets get the "~" cue since it's a computed average across
    // differently-priced batches — Consumables/Cable show a plain number.
    return row.tracking_type === "asset_serialized"
        ? `<span class="inventory-avg-cost" title="Weighted average across all units of this SKU">~${money(row.avg_unit_cost)}</span>`
        : money(row.avg_unit_cost);
}

function renderItemsTable() {
    const thead = document.getElementById("inventory-items-thead");
    const tbody = document.getElementById("inventory-items-rows");
    if (!thead || !tbody) return;

    thead.innerHTML = `<tr><th>Name</th><th>SKU</th><th>Category</th><th>Qty</th><th>Unit Cost</th><th>Total Value</th><th>Actions</th></tr>`;

    const rows = filteredItemsForDisplay();
    tbody.innerHTML = rows.map(row => {
        const { qty, value } = qtyAndValueFor(row);
        return `
            <tr>
                <td>${row.name || "—"}</td>
                <td>${row.sku_or_spec}</td>
                <td>${row.category_name}</td>
                <td>${qty}</td>
                <td>${unitCostCell(row)}</td>
                <td>${money(value)}</td>
                <td>
                    <button type="button" class="inventory-icon-btn view view-item-btn"
                        data-category-id="${row.category_id}" data-sku="${encodeURIComponent(row.sku_or_spec)}"
                        data-tracking-type="${row.tracking_type}" data-category-name="${row.category_name}"
                        title="View stock history">
                        ${viewIconSvg()}
                    </button>
                </td>
            </tr>
        `;
    }).join("");

    tbody.querySelectorAll(".view-item-btn").forEach(btn => {
        btn.addEventListener("click", () => onViewSku(btn.dataset));
    });
}

// Reframes View as "stock history", branching by Core: Asset/Cable open a
// list of the individual units under this SKU/spec (drilling further into
// the tabbed unit detail); Consumable has no individual units, so it opens
// the flat running-balance history directly.
function onViewSku({ categoryId, sku, trackingType, categoryName }) {
    const skuDecoded = decodeURIComponent(sku);
    if (trackingType === "inventory_quantity") {
        openSkuHistoryModal(categoryId, skuDecoded, `${categoryName} — ${skuDecoded}`);
    } else {
        openUnitListModal(Number(categoryId), skuDecoded, trackingType, categoryName);
    }
}

// ---- Unit list (Asset/Cable) — the individual serials/reels behind one SKU
// row, each carrying its own View/Edit/Delete now that the main table is an
// aggregate. Lives here (not inventory-common.js) since Edit/Delete need
// this view's own item-form modal and itemsCache-backed lookups.

function unitRowCells(item) {
    if (item.tracking_type === "asset_serialized") {
        const holder = assignableUserName(item.assigned_to_user_id);
        return `
            <td>${item.id}</td>
            <td>${item.serial_number || "—"}</td>
            <td>${item.asset_status || "—"}</td>
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

function renderUnitList() {
    const thead = document.getElementById("unit-list-thead");
    const tbody = document.getElementById("unit-list-rows");
    if (!thead || !tbody || unitListCache.length === 0) return;

    const trackingType = unitListCache[0].tracking_type;
    thead.innerHTML = unitListHeadRow(trackingType);

    tbody.innerHTML = unitListCache.map(item => `
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
        btn.addEventListener("click", () => openEditItemModal(btn.dataset.id));
    });
    tbody.querySelectorAll(".unit-delete-btn").forEach(btn => {
        btn.addEventListener("click", () => deleteItem(btn.dataset.id, btn.dataset.name));
    });
}

async function openUnitListModal(categoryId, sku, trackingType, categoryName) {
    const overlay = document.getElementById("unit-list-overlay");
    if (!overlay) return;

    document.getElementById("unit-list-label").textContent = `— ${categoryName} — ${sku}`;
    const res = await fetch(`/inventory/items?category_id=${categoryId}&sku=${encodeURIComponent(sku)}`, { headers: authHeaders() });
    unitListCache = res.ok ? await res.json() : [];
    renderUnitList();
    overlay.hidden = false;
}

async function refreshUnitListIfOpen() {
    const overlay = document.getElementById("unit-list-overlay");
    if (!overlay || overlay.hidden || unitListCache.length === 0) return;
    const { category_id, sku, tracking_type } = unitListCache[0];
    const sku_or_spec = tracking_type === "inventory_length" ? unitListCache[0].spec : sku;
    const res = await fetch(`/inventory/items?category_id=${category_id}&sku=${encodeURIComponent(sku_or_spec)}`, { headers: authHeaders() });
    unitListCache = res.ok ? await res.json() : [];
    renderUnitList();
}

async function deleteItem(id, name) {
    if (!confirm(`Delete item "${name}"? This can't be undone.`)) return;

    const response = await fetch(`/inventory/items/${id}`, { method: "DELETE", headers: authHeaders() });
    if (response.ok) {
        await Promise.all([refreshItems(), refreshUnitListIfOpen()]);
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

    document.getElementById("edit-inventory-item-sku").value = item.sku || "";
    document.getElementById("edit-inventory-item-name").value = item.name || "";
    document.getElementById("edit-inventory-item-location").value = item.location_id || "";
    document.getElementById("edit-inventory-item-unit-cost").value = item.unit_cost ?? "";
    document.getElementById("edit-inventory-item-supplier").value = item.supplier || "";
    document.getElementById("edit-inventory-item-uom").value = item.unit_of_measure || "";
    document.getElementById("edit-inventory-item-notes").value = item.notes || "";

    if (item.tracking_type === "asset_serialized") {
        document.getElementById("edit-inventory-item-serial-number").value = item.serial_number || "";
        document.getElementById("edit-inventory-item-asset-status").value = item.asset_status || "Active";
        document.getElementById("edit-inventory-item-assigned-to").value = item.assigned_to_user_id || "";
        document.getElementById("edit-inventory-item-make-model").value = item.make_model || "";
        document.getElementById("edit-inventory-item-spec-capacity").value = item.spec_capacity || "";
        document.getElementById("edit-inventory-item-install-date").value = item.install_date || "";
    } else if (item.tracking_type === "inventory_quantity") {
        document.getElementById("edit-inventory-item-batch-lot").value = item.batch_lot || "";
        document.getElementById("edit-inventory-item-expiry-date").value = item.expiry_date || "";
        document.getElementById("edit-inventory-item-quantity-on-hand").value = item.quantity_on_hand ?? "";
    } else if (item.tracking_type === "inventory_length") {
        document.getElementById("edit-inventory-item-cut-reel-id").value = item.cut_reel_id || "";
        document.getElementById("edit-inventory-item-spec").value = item.spec || "";
        document.getElementById("edit-inventory-item-length-received").value = item.length_received ?? "";
        document.getElementById("edit-inventory-item-length-remaining").value = item.length_remaining ?? "";
        document.getElementById("edit-inventory-item-length-status").value = item.length_status || "In Stock";
    }

    document.getElementById("edit-inventory-item-overlay").hidden = false;
}

function closeEditItemModal() {
    document.getElementById("edit-inventory-item-overlay").hidden = true;
    editItemId = null;
}

function initItemForms() {
    const addOverlay = document.getElementById("add-inventory-item-overlay");
    const addOpenBtn = document.getElementById("add-inventory-item-open-btn");
    const addCancelBtn = document.getElementById("add-inventory-item-cancel");
    const addForm = document.getElementById("inventory-item-form");
    const categorySelect = document.getElementById("inventory-item-category");

    addOpenBtn.addEventListener("click", () => {
        addForm.reset();
        addForm.querySelectorAll("[data-item-type-fields]").forEach(block => { block.hidden = true; });
        addOverlay.hidden = false;
    });

    addCancelBtn.addEventListener("click", () => { addOverlay.hidden = true; });
    addOverlay.addEventListener("click", (e) => { if (e.target === addOverlay) addOverlay.hidden = true; });

    categorySelect.addEventListener("change", () => {
        const category = getInventoryCategories().find(c => String(c.id) === categorySelect.value);
        if (category) showTypeFields(addForm, category.tracking_type);
    });

    addForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const category = getInventoryCategories().find(c => String(c.id) === categorySelect.value);

        const body = {
            category_id: Number(categorySelect.value),
            sku: document.getElementById("inventory-item-sku").value,
            name: document.getElementById("inventory-item-name").value,
            location_id: numOrNull(document.getElementById("inventory-item-location").value),
            unit_cost: numOrNull(document.getElementById("inventory-item-unit-cost").value),
            supplier: document.getElementById("inventory-item-supplier").value || null,
            unit_of_measure: document.getElementById("inventory-item-uom").value || null,
            notes: document.getElementById("inventory-item-notes").value || null,
        };

        if (category && category.tracking_type === "asset_serialized") {
            body.serial_number = document.getElementById("inventory-item-serial-number").value || null;
            body.asset_status = document.getElementById("inventory-item-asset-status").value;
            body.assigned_to_user_id = numOrNull(document.getElementById("inventory-item-assigned-to").value);
            body.make_model = document.getElementById("inventory-item-make-model").value || null;
            body.spec_capacity = document.getElementById("inventory-item-spec-capacity").value || null;
            body.install_date = document.getElementById("inventory-item-install-date").value || null;
        } else if (category && category.tracking_type === "inventory_quantity") {
            body.batch_lot = document.getElementById("inventory-item-batch-lot").value || null;
            body.expiry_date = document.getElementById("inventory-item-expiry-date").value || null;
            body.quantity_on_hand = numOrNull(document.getElementById("inventory-item-quantity-on-hand").value);
        } else if (category && category.tracking_type === "inventory_length") {
            body.cut_reel_id = document.getElementById("inventory-item-cut-reel-id").value || null;
            body.spec = document.getElementById("inventory-item-spec").value || null;
            body.length_received = numOrNull(document.getElementById("inventory-item-length-received").value);
        }

        const response = await fetch("/inventory/items", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body)
        });

        if (response.ok) {
            showMessage("inventory-item-msg", "Item added", false);
            addForm.reset();
            addOverlay.hidden = true;
            await refreshItems();
        } else {
            const err = await response.json().catch(() => ({}));
            showMessage("inventory-item-msg", err.detail || "Failed to add item", true);
        }
    });

    const editOverlay = document.getElementById("edit-inventory-item-overlay");
    const editCancelBtn = document.getElementById("edit-inventory-item-cancel");
    const editForm = document.getElementById("edit-inventory-item-form");

    editCancelBtn.addEventListener("click", closeEditItemModal);
    editOverlay.addEventListener("click", (e) => { if (e.target === editOverlay) closeEditItemModal(); });

    editForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!editItemId) return;
        const item = unitListCache.find(i => String(i.id) === String(editItemId));
        if (!item) return;

        const body = {
            sku: document.getElementById("edit-inventory-item-sku").value,
            name: document.getElementById("edit-inventory-item-name").value,
            location_id: numOrNull(document.getElementById("edit-inventory-item-location").value),
            unit_cost: numOrNull(document.getElementById("edit-inventory-item-unit-cost").value),
            supplier: document.getElementById("edit-inventory-item-supplier").value || null,
            unit_of_measure: document.getElementById("edit-inventory-item-uom").value || null,
            notes: document.getElementById("edit-inventory-item-notes").value || null,
        };

        if (item.tracking_type === "asset_serialized") {
            body.serial_number = document.getElementById("edit-inventory-item-serial-number").value || null;
            body.asset_status = document.getElementById("edit-inventory-item-asset-status").value;
            body.assigned_to_user_id = numOrNull(document.getElementById("edit-inventory-item-assigned-to").value);
            body.make_model = document.getElementById("edit-inventory-item-make-model").value || null;
            body.spec_capacity = document.getElementById("edit-inventory-item-spec-capacity").value || null;
            body.install_date = document.getElementById("edit-inventory-item-install-date").value || null;
        } else if (item.tracking_type === "inventory_quantity") {
            body.batch_lot = document.getElementById("edit-inventory-item-batch-lot").value || null;
            body.expiry_date = document.getElementById("edit-inventory-item-expiry-date").value || null;
            body.quantity_on_hand = numOrNull(document.getElementById("edit-inventory-item-quantity-on-hand").value);
        } else if (item.tracking_type === "inventory_length") {
            body.cut_reel_id = document.getElementById("edit-inventory-item-cut-reel-id").value || null;
            body.spec = document.getElementById("edit-inventory-item-spec").value || null;
            body.length_received = numOrNull(document.getElementById("edit-inventory-item-length-received").value);
            body.length_remaining = numOrNull(document.getElementById("edit-inventory-item-length-remaining").value);
            body.length_status = document.getElementById("edit-inventory-item-length-status").value;
        }

        const response = await fetch(`/inventory/items/${editItemId}`, {
            method: "PATCH",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body)
        });

        if (response.ok) {
            closeEditItemModal();
            await Promise.all([refreshItems(), refreshUnitListIfOpen()]);
        } else {
            const err = await response.json().catch(() => ({}));
            alert(err.detail || "Failed to update item");
        }
    });

    document.getElementById("inventory-items-category-filter").addEventListener("change", async (e) => {
        itemsCategoryFilter = e.target.value;
        await refreshItems();
    });

    document.getElementById("inventory-items-status-filter").addEventListener("change", (e) => {
        itemsStatusFilter = e.target.value;
        renderItemsTable();
    });

    document.getElementById("inventory-items-custody-filter").addEventListener("change", (e) => {
        itemsCustodyFilter = e.target.value;
        renderItemsTable();
    });
}

function initUnitListModal() {
    const overlay = document.getElementById("unit-list-overlay");
    document.getElementById("unit-list-close").addEventListener("click", () => { overlay.hidden = true; });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });
}

export function initInventory() {
    initItemForms();
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
        await Promise.all([loadInventoryCategories(), loadInventoryLocations(), loadInventoryAssignableUsers()]);
        populateCategoryDropdowns();
        populateLocationDropdowns();
        populateAssignedToDropdowns();
        await refreshItems();
    });

    registerRoute("inventory", async () => {
        showView("view-inventory");
        // Re-fetched on every visit, not just at login, so a category or
        // location added/edited on the Manage page shows up here immediately.
        await Promise.all([loadInventoryCategories(), loadInventoryLocations()]);
        populateCategoryDropdowns();
        populateLocationDropdowns();
        await refreshItems();
    });
}
