import {
    can, authHeaders, showMessage, editIconSvg, deleteIconSvg,
    registerAppShownHandler, showView, registerRoute, navigate,
} from "./common.js";
import {
    trackingTypeLabel, loadInventoryCategories, getInventoryCategories,
    loadInventoryLocations, getInventoryLocations,
    loadInventoryAssignableUsers, getInventoryAssignableUsers, assignableUserName,
} from "./inventory-common.js";

let editCategoryId = null;
let editLocationId = null;
let editItemId = null;
let itemsCache = [];
let itemsCategoryFilter = "";

// ---- Categories ----

async function refreshCategories() {
    await loadInventoryCategories();
    renderCategoryList(getInventoryCategories());
    populateCategoryDropdowns();
}

function renderCategoryList(categories) {
    const tbody = document.getElementById("inventory-categories-rows");
    if (!tbody) return;

    const hasActions = can("inventory_categories", "edit") || can("inventory_categories", "delete");

    tbody.innerHTML = categories.map(cat => `
        <tr>
            <td>${cat.name}</td>
            <td><span class="tracking-type-badge">${trackingTypeLabel(cat.tracking_type)}</span></td>
            <td>${cat.description || "—"}</td>
            ${hasActions ? `
            <td>
                ${can("inventory_categories", "edit") ? `
                <button type="button" class="inventory-icon-btn edit edit-category-btn" data-id="${cat.id}" title="Edit category">
                    ${editIconSvg()}
                </button>` : ""}
                ${can("inventory_categories", "delete") ? `
                <button type="button" class="inventory-icon-btn delete delete-category-btn" data-id="${cat.id}" data-name="${cat.name}" title="Delete category">
                    ${deleteIconSvg()}
                </button>` : ""}
            </td>` : ""}
        </tr>
    `).join("");

    tbody.querySelectorAll(".edit-category-btn").forEach(btn => {
        btn.addEventListener("click", () => openEditCategoryModal(btn.dataset.id));
    });
    tbody.querySelectorAll(".delete-category-btn").forEach(btn => {
        btn.addEventListener("click", () => deleteCategory(btn.dataset.id, btn.dataset.name));
    });
}

async function deleteCategory(id, name) {
    if (!confirm(`Delete category "${name}"? This can't be undone.`)) return;

    const response = await fetch(`/inventory/categories/${id}`, { method: "DELETE", headers: authHeaders() });
    if (response.ok) {
        await refreshCategories();
    } else {
        alert("Failed to delete category — it may still have items tied to it.");
    }
}

function openEditCategoryModal(categoryId) {
    const cat = getInventoryCategories().find(c => String(c.id) === String(categoryId));
    if (!cat) return;

    editCategoryId = cat.id;
    document.getElementById("edit-inventory-category-name").value = cat.name || "";
    document.getElementById("edit-inventory-category-tracking-type").value = cat.tracking_type;
    document.getElementById("edit-inventory-category-description").value = cat.description || "";
    document.getElementById("edit-inventory-category-overlay").hidden = false;
}

function closeEditCategoryModal() {
    document.getElementById("edit-inventory-category-overlay").hidden = true;
    editCategoryId = null;
}

function initCategoryForms() {
    const addOverlay = document.getElementById("add-inventory-category-overlay");
    const addOpenBtn = document.getElementById("add-inventory-category-open-btn");
    const addCancelBtn = document.getElementById("add-inventory-category-cancel");

    addOpenBtn.addEventListener("click", () => {
        document.getElementById("inventory-category-form").reset();
        addOverlay.hidden = false;
    });

    addCancelBtn.addEventListener("click", () => { addOverlay.hidden = true; });
    addOverlay.addEventListener("click", (e) => { if (e.target === addOverlay) addOverlay.hidden = true; });

    document.getElementById("inventory-category-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const name = document.getElementById("inventory-category-name").value;
        const tracking_type = document.getElementById("inventory-category-tracking-type").value;
        const description = document.getElementById("inventory-category-description").value || null;

        const response = await fetch("/inventory/categories", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ name, tracking_type, description })
        });

        if (response.ok) {
            showMessage("inventory-category-msg", "Category added", false);
            e.target.reset();
            addOverlay.hidden = true;
            await refreshCategories();
        } else {
            showMessage("inventory-category-msg", "Failed to add category", true);
        }
    });

    const editOverlay = document.getElementById("edit-inventory-category-overlay");
    const editCancelBtn = document.getElementById("edit-inventory-category-cancel");
    const editTrackingTypeSelect = document.getElementById("edit-inventory-category-tracking-type");
    const lockHint = document.getElementById("edit-inventory-category-lock-hint");

    editCancelBtn.addEventListener("click", closeEditCategoryModal);
    editOverlay.addEventListener("click", (e) => { if (e.target === editOverlay) closeEditCategoryModal(); });

    document.getElementById("edit-inventory-category-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!editCategoryId) return;

        const body = {
            name: document.getElementById("edit-inventory-category-name").value,
            tracking_type: editTrackingTypeSelect.value,
            description: document.getElementById("edit-inventory-category-description").value || null,
        };

        const response = await fetch(`/inventory/categories/${editCategoryId}`, {
            method: "PATCH",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body)
        });

        if (response.ok) {
            lockHint.hidden = true;
            closeEditCategoryModal();
            await refreshCategories();
        } else if (response.status === 400) {
            lockHint.hidden = false;
        } else {
            alert("Failed to update category");
        }
    });

    // The tracking type can't change once the category has items — the
    // router is the source of truth on that (400 on mismatch); this just
    // hides the hint again once the user picks the type back.
    editTrackingTypeSelect.addEventListener("change", () => { lockHint.hidden = true; });
}

// ---- Locations ----

async function refreshInventoryLocations() {
    await loadInventoryLocations();
    renderLocationList(getInventoryLocations());
    populateLocationDropdowns();
}

function renderLocationList(locations) {
    const tbody = document.getElementById("inventory-locations-rows");
    if (!tbody) return;

    const hasActions = can("inventory_locations", "edit") || can("inventory_locations", "delete");

    tbody.innerHTML = locations.map(loc => `
        <tr>
            <td>${loc.name}</td>
            <td><span class="store-tag">${loc.is_store ? "Store" : "Site"}</span></td>
            <td>${loc.contact_name || "—"}</td>
            <td>${loc.contact_phone || "—"}</td>
            <td>${loc.address || "—"}</td>
            ${hasActions ? `
            <td>
                ${can("inventory_locations", "edit") ? `
                <button type="button" class="inventory-icon-btn edit edit-inv-location-btn" data-id="${loc.id}" title="Edit location">
                    ${editIconSvg()}
                </button>` : ""}
                ${can("inventory_locations", "delete") ? `
                <button type="button" class="inventory-icon-btn delete delete-inv-location-btn" data-id="${loc.id}" data-name="${loc.name}" title="Delete location">
                    ${deleteIconSvg()}
                </button>` : ""}
            </td>` : ""}
        </tr>
    `).join("");

    tbody.querySelectorAll(".edit-inv-location-btn").forEach(btn => {
        btn.addEventListener("click", () => openEditLocationModal(btn.dataset.id));
    });
    tbody.querySelectorAll(".delete-inv-location-btn").forEach(btn => {
        btn.addEventListener("click", () => deleteInventoryLocation(btn.dataset.id, btn.dataset.name));
    });
}

async function deleteInventoryLocation(id, name) {
    if (!confirm(`Delete location "${name}"? This can't be undone.`)) return;

    const response = await fetch(`/inventory/locations/${id}`, { method: "DELETE", headers: authHeaders() });
    if (response.ok) {
        await refreshInventoryLocations();
    } else {
        alert("Failed to delete location — it may still have items tied to it.");
    }
}

function openEditLocationModal(locationId) {
    const loc = getInventoryLocations().find(l => String(l.id) === String(locationId));
    if (!loc) return;

    editLocationId = loc.id;
    document.getElementById("edit-inventory-location-name").value = loc.name || "";
    document.getElementById("edit-inventory-location-contact-name").value = loc.contact_name || "";
    document.getElementById("edit-inventory-location-contact-phone").value = loc.contact_phone || "";
    document.getElementById("edit-inventory-location-address").value = loc.address || "";
    document.getElementById("edit-inventory-location-notes").value = loc.notes || "";
    document.getElementById("edit-inventory-location-is-store").checked = !!loc.is_store;
    document.getElementById("edit-inventory-location-overlay").hidden = false;
}

function closeEditLocationModal() {
    document.getElementById("edit-inventory-location-overlay").hidden = true;
    editLocationId = null;
}

function initLocationForms() {
    const addOverlay = document.getElementById("add-inventory-location-overlay");
    const addOpenBtn = document.getElementById("add-inventory-location-open-btn");
    const addCancelBtn = document.getElementById("add-inventory-location-cancel");

    addOpenBtn.addEventListener("click", () => {
        document.getElementById("inventory-location-form").reset();
        addOverlay.hidden = false;
    });

    addCancelBtn.addEventListener("click", () => { addOverlay.hidden = true; });
    addOverlay.addEventListener("click", (e) => { if (e.target === addOverlay) addOverlay.hidden = true; });

    document.getElementById("inventory-location-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const name = document.getElementById("inventory-location-name").value;
        const contact_name = document.getElementById("inventory-location-contact-name").value || null;
        const contact_phone = document.getElementById("inventory-location-contact-phone").value || null;
        const address = document.getElementById("inventory-location-address").value || null;
        const notes = document.getElementById("inventory-location-notes").value || null;
        const is_store = document.getElementById("inventory-location-is-store").checked;

        const response = await fetch("/inventory/locations", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ name, is_store, address, contact_name, contact_phone, notes })
        });

        if (response.ok) {
            showMessage("inventory-location-msg", "Location added", false);
            e.target.reset();
            addOverlay.hidden = true;
            await refreshInventoryLocations();
        } else {
            showMessage("inventory-location-msg", "Failed to add location", true);
        }
    });

    const editOverlay = document.getElementById("edit-inventory-location-overlay");
    const editCancelBtn = document.getElementById("edit-inventory-location-cancel");

    editCancelBtn.addEventListener("click", closeEditLocationModal);
    editOverlay.addEventListener("click", (e) => { if (e.target === editOverlay) closeEditLocationModal(); });

    document.getElementById("edit-inventory-location-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!editLocationId) return;

        const body = {
            name: document.getElementById("edit-inventory-location-name").value,
            contact_name: document.getElementById("edit-inventory-location-contact-name").value || null,
            contact_phone: document.getElementById("edit-inventory-location-contact-phone").value || null,
            address: document.getElementById("edit-inventory-location-address").value || null,
            notes: document.getElementById("edit-inventory-location-notes").value || null,
            is_store: document.getElementById("edit-inventory-location-is-store").checked,
        };

        const response = await fetch(`/inventory/locations/${editLocationId}`, {
            method: "PATCH",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body)
        });

        if (response.ok) {
            closeEditLocationModal();
            await refreshInventoryLocations();
        } else {
            alert("Failed to update location");
        }
    });
}

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

function populateLocationDropdowns() {
    const locations = getInventoryLocations();
    const options = locations.map(l => `<option value="${l.id}">${l.name}</option>`).join("");

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

async function refreshItems() {
    const qs = itemsCategoryFilter ? `?category_id=${itemsCategoryFilter}` : "";
    const res = await fetch(`/inventory/items${qs}`, { headers: authHeaders() });
    itemsCache = res.ok ? await res.json() : [];
    renderItemsTable(itemsCache);
}

function formatItemDetail(item) {
    if (item.tracking_type === "asset_serialized") {
        const assignee = assignableUserName(item.assigned_to_user_id);
        return `SN ${item.serial_number || "—"} — ${item.asset_status || "—"}` + (assignee ? ` (${assignee})` : "");
    }
    if (item.tracking_type === "inventory_quantity") {
        const qty = `${item.quantity_on_hand ?? "—"} ${item.unit_of_measure || ""}`.trim();
        return item.batch_lot ? `${qty} · Lot ${item.batch_lot}` : qty;
    }
    if (item.tracking_type === "inventory_length") {
        return `${item.length_remaining ?? "—"}/${item.length_received ?? "—"} ${item.unit_of_measure || "m"} — ${item.length_status || "—"}`;
    }
    return "—";
}

function itemActionsCell(item) {
    const hasActions = can("inventory_items", "edit") || can("inventory_items", "delete");
    if (!hasActions) return "";
    return `
        <td>
            ${can("inventory_items", "edit") ? `
            <button type="button" class="inventory-icon-btn edit edit-item-btn" data-id="${item.id}" title="Edit item">
                ${editIconSvg()}
            </button>` : ""}
            ${can("inventory_items", "delete") ? `
            <button type="button" class="inventory-icon-btn delete delete-item-btn" data-id="${item.id}" data-name="${item.name}" title="Delete item">
                ${deleteIconSvg()}
            </button>` : ""}
        </td>`;
}

function renderItemsTable(items) {
    const thead = document.getElementById("inventory-items-thead");
    const tbody = document.getElementById("inventory-items-rows");
    if (!thead || !tbody) return;

    const hasActions = can("inventory_items", "edit") || can("inventory_items", "delete");
    const filterCategory = getInventoryCategories().find(c => String(c.id) === String(itemsCategoryFilter));
    const actionsTh = `<th id="inventory-items-actions-th">Actions</th>`;

    if (!filterCategory) {
        thead.innerHTML = `<tr><th>Name</th><th>SKU</th><th>Category</th><th>Location</th><th>Detail</th>${hasActions ? actionsTh : ""}</tr>`;
        tbody.innerHTML = items.map(item => `
            <tr>
                <td>${item.name}</td>
                <td>${item.sku}</td>
                <td>${item.category_name}</td>
                <td>${item.location_name || "—"}</td>
                <td>${formatItemDetail(item)}</td>
                ${itemActionsCell(item)}
            </tr>
        `).join("");
    } else if (filterCategory.tracking_type === "asset_serialized") {
        thead.innerHTML = `<tr><th>Name</th><th>SKU</th><th>Serial Number</th><th>Status</th><th>Assigned To</th><th>Location</th>${hasActions ? actionsTh : ""}</tr>`;
        tbody.innerHTML = items.map(item => `
            <tr>
                <td>${item.name}</td>
                <td>${item.sku}</td>
                <td>${item.serial_number || "—"}</td>
                <td>${item.asset_status || "—"}</td>
                <td>${assignableUserName(item.assigned_to_user_id) || "—"}</td>
                <td>${item.location_name || "—"}</td>
                ${itemActionsCell(item)}
            </tr>
        `).join("");
    } else if (filterCategory.tracking_type === "inventory_quantity") {
        thead.innerHTML = `<tr><th>Name</th><th>SKU</th><th>Batch/Lot</th><th>Qty on Hand</th><th>Expiry</th><th>Location</th>${hasActions ? actionsTh : ""}</tr>`;
        tbody.innerHTML = items.map(item => `
            <tr>
                <td>${item.name}</td>
                <td>${item.sku}</td>
                <td>${item.batch_lot || "—"}</td>
                <td>${item.quantity_on_hand ?? "—"} ${item.unit_of_measure || ""}</td>
                <td>${item.expiry_date || "—"}</td>
                <td>${item.location_name || "—"}</td>
                ${itemActionsCell(item)}
            </tr>
        `).join("");
    } else {
        thead.innerHTML = `<tr><th>Name</th><th>SKU</th><th>Cut/Reel ID</th><th>Remaining/Received</th><th>Status</th><th>Location</th>${hasActions ? actionsTh : ""}</tr>`;
        tbody.innerHTML = items.map(item => `
            <tr>
                <td>${item.name}</td>
                <td>${item.sku}</td>
                <td>${item.cut_reel_id || "—"}</td>
                <td>${item.length_remaining ?? "—"}/${item.length_received ?? "—"} ${item.unit_of_measure || "m"}</td>
                <td>${item.length_status || "—"}</td>
                <td>${item.location_name || "—"}</td>
                ${itemActionsCell(item)}
            </tr>
        `).join("");
    }

    tbody.querySelectorAll(".edit-item-btn").forEach(btn => {
        btn.addEventListener("click", () => openEditItemModal(btn.dataset.id));
    });
    tbody.querySelectorAll(".delete-item-btn").forEach(btn => {
        btn.addEventListener("click", () => deleteItem(btn.dataset.id, btn.dataset.name));
    });
}

async function deleteItem(id, name) {
    if (!confirm(`Delete item "${name}"? This can't be undone.`)) return;

    const response = await fetch(`/inventory/items/${id}`, { method: "DELETE", headers: authHeaders() });
    if (response.ok) {
        await refreshItems();
    } else {
        alert("Failed to delete item — it may still have transaction history tied to it.");
    }
}

function openEditItemModal(itemId) {
    const item = itemsCache.find(i => String(i.id) === String(itemId));
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
        const item = itemsCache.find(i => String(i.id) === String(editItemId));
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
            await refreshItems();
        } else {
            const err = await response.json().catch(() => ({}));
            alert(err.detail || "Failed to update item");
        }
    });

    document.getElementById("inventory-items-category-filter").addEventListener("change", async (e) => {
        itemsCategoryFilter = e.target.value;
        await refreshItems();
    });
}

export function initInventory() {
    initCategoryForms();
    initLocationForms();
    initItemForms();

    // Lives on the Inventory view's header as a quick link, wired here since
    // the action itself is this view's concern (same pattern as movements.js's
    // and check-sites.js's own header link buttons).
    document.getElementById("inventory-log-link-btn").addEventListener("click", () => {
        navigate("inventory-log");
    });

    registerAppShownHandler(async () => {
        await Promise.all([refreshCategories(), refreshInventoryLocations(), loadInventoryAssignableUsers()]);
        populateAssignedToDropdowns();
        await refreshItems();
    });

    registerRoute("inventory", () => showView("view-inventory"));
}
