import {
    can, authHeaders, showMessage, editIconSvg, deleteIconSvg, viewIconSvg,
    registerAppShownHandler, showView, registerRoute, registerCmdkProvider, navigate,
} from "./common.js";
import {
    loadInventoryCategories, getInventoryCategories,
    loadInventoryLocations, getInventoryLocations, loadInventoryAssignableUsers,
    loadAllInventoryItems, getAllInventoryItems,
    showTypeFields, buildProductList, numOrNull, money, unitCostCell, unitCostText, todayDateString,
    populateLocationDropdowns, populateAssignedToDropdowns,
    openStockHistory, initUnitListModal, initUnitDetailModal, initSkuHistoryModal,
    openEditProductModal, deleteProduct, initEditProductModal,
    exportRowsToCsv, inventorySummaryRowMatchesQuery, initQtyStepper,
} from "./inventory-common.js";

// Items: the catalog-plus-holdings reference page — Name, SKU, Category,
// Qty/Cost/Value (off get_items_summary, same as Stock), Location, and
// View/Edit/Delete. Filtered only by Category — the Status/Custody filter
// selects live on Stock instead. Qty/Total Value here default to the
// on-hand figures (on_hand_qty/on_hand_value), same basis Stock's "In Store"
// filter computes — not total_qty/total_value (on-hand + deployed), since a
// deployed unit isn't available to issue right now and showing it in "Qty"
// read as the app under-reporting what got issued out. The combined
// total-owned figure (for asset-register purposes) is still reachable via
// Stock's Status filter set to "All" — not duplicated here. See
// architecture.md's note on the Items/Stock split. The Add Item wizard (Add
// Product / Add Unit) is this page's own header action; Issue/Return
// Materials live on Stock.

let itemsSummaryCache = [];
let itemsCategoryFilter = "";
let itemsSearchQuery = "";

// ---- Items table ----

function populateCategoryFilter() {
    const categories = getInventoryCategories();
    const options = categories.map(c => `<option value="${c.id}">${c.name}</option>`).join("");

    const filterSelect = document.getElementById("inventory-items-category-filter");
    if (filterSelect) {
        const previous = filterSelect.value;
        filterSelect.innerHTML = `<option value="">All Categories</option>${options}`;
        filterSelect.value = categories.some(c => String(c.id) === previous) ? previous : "";
    }

    const addSelect = document.getElementById("add-item-product-category");
    if (addSelect) addSelect.innerHTML = `<option value="" disabled selected>Category...</option>${options}`;
}

async function refreshCatalog() {
    const qs = itemsCategoryFilter ? `?category_id=${itemsCategoryFilter}` : "";
    const res = await fetch(`/inventory/items/summary${qs}`, { headers: authHeaders() });
    itemsSummaryCache = res.ok ? await res.json() : [];
    renderItemsTable();
}

// Narrows the already-fetched (category-scoped) rows client-side by the
// search box — no refetch, same "filter what's already on screen"
// convention as the Status/Custody filters on Stock.
function visibleItemsRows() {
    return itemsSummaryCache.filter(row => inventorySummaryRowMatchesQuery(row, itemsSearchQuery));
}

function renderItemsTable() {
    const thead = document.getElementById("inventory-items-thead");
    const tbody = document.getElementById("inventory-items-rows");
    if (!thead || !tbody) return;

    thead.innerHTML = `<tr><th>Name</th><th>SKU</th><th>Category</th><th>Qty</th><th>Unit Cost</th><th>Total Value</th><th>Location</th><th>Actions</th></tr>`;

    const canEdit = can("inventory_items", "edit");
    const canDelete = can("inventory_items", "delete");
    const canViewHistory = can("inventory_items", "view_history");
    tbody.innerHTML = visibleItemsRows().map(row => `
        <tr>
            <td>${row.name || "—"}</td>
            <td>${row.sku_or_spec}</td>
            <td>${row.category_name}</td>
            <td>${row.on_hand_qty}</td>
            <td>${unitCostCell(row)}</td>
            <td>${money(row.on_hand_value)}</td>
            <td>${row.location_names || "—"}</td>
            <td>
                ${canViewHistory ? `
                <button type="button" class="inventory-icon-btn view view-item-btn"
                    data-category-id="${row.category_id}" data-sku="${encodeURIComponent(row.sku_or_spec)}"
                    data-tracking-type="${row.tracking_type}" data-category-name="${row.category_name}"
                    title="View stock history">
                    ${viewIconSvg()}
                </button>` : ""}
                ${canEdit ? `
                <button type="button" class="inventory-icon-btn edit edit-product-btn"
                    data-category-id="${row.category_id}" data-sku="${encodeURIComponent(row.sku_or_spec)}"
                    title="Edit product">
                    ${editIconSvg()}
                </button>` : ""}
                ${canDelete ? `
                <button type="button" class="inventory-icon-btn delete delete-product-btn"
                    data-category-id="${row.category_id}" data-sku="${encodeURIComponent(row.sku_or_spec)}"
                    data-name="${row.name || row.sku_or_spec}"
                    title="Delete product">
                    ${deleteIconSvg()}
                </button>` : ""}
            </td>
        </tr>
    `).join("");

    tbody.querySelectorAll(".view-item-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const { categoryId, sku, trackingType, categoryName } = btn.dataset;
            openStockHistory({
                categoryId, sku: decodeURIComponent(sku), trackingType, categoryName,
                onChanged: refreshCatalog,
            });
        });
    });

    tbody.querySelectorAll(".edit-product-btn").forEach(btn => {
        btn.addEventListener("click", () => openEditProductModal(Number(btn.dataset.categoryId), decodeURIComponent(btn.dataset.sku), refreshCatalog));
    });

    tbody.querySelectorAll(".delete-product-btn").forEach(btn => {
        btn.addEventListener("click", () => deleteProduct(Number(btn.dataset.categoryId), decodeURIComponent(btn.dataset.sku), btn.dataset.name, refreshCatalog));
    });
}

// Exports whatever's currently in the table — already scoped by
// itemsCategoryFilter (server-side) and itemsSearchQuery (client-side, same
// rows visibleItemsRows() renders), not an unfiltered dump.
function exportItemsCsv() {
    exportRowsToCsv("items.csv", [
        { header: "Name", value: row => row.name || "—" },
        { header: "SKU", value: row => row.sku_or_spec },
        { header: "Category", value: row => row.category_name },
        { header: "Qty", value: row => row.on_hand_qty },
        { header: "Unit Cost", value: row => unitCostText(row) },
        { header: "Total Value", value: row => money(row.on_hand_value) },
        { header: "Location", value: row => row.location_names || "—" },
    ], visibleItemsRows());
}

// ---- Add Item wizard: Add Product (Step 1, one-time per SKU/spec) then
// Add Unit (Step 2, repeats per physical item/batch/cut) — splits what used
// to be one form, so adding another unit of an already-known product never
// re-asks for Name/Make-Model/Spec/Supplier. "New product" walks Step 1
// first (and also creates the catalog entry that shows up on this table);
// "Existing product" searches and skips straight to Step 2.
let addItemMode = null; // "existing" | "new"
let addItemProduct = null; // { category_id, tracking_type, sku, name, make_model, spec_capacity, spec, supplier, unit_of_measure }
let addItemEventGroupId = null; // shared across every unit saved in one sitting, once the first save returns it
let addItemHasSavedInSitting = false;
let addItemBatchItems = []; // items just created by the batch endpoint, powers the Batch Review step's editable list

function showAddItemStep(step) {
    ["choice", "search", "product", "unit", "batch", "batch-review", "success"].forEach(name => {
        document.getElementById(`add-item-step-${name}`).hidden = name !== step;
    });
}

function resetAddItemWizard() {
    addItemMode = null;
    addItemProduct = null;
    addItemEventGroupId = null;
    addItemHasSavedInSitting = false;
    addItemBatchItems = [];

    document.getElementById("add-item-product-search-input").value = "";
    document.getElementById("add-item-product-results").innerHTML = "";
    document.getElementById("add-item-choice-existing").classList.remove("active");
    document.getElementById("add-item-choice-new").classList.remove("active");

    const productForm = document.getElementById("add-item-product-form");
    productForm.reset();
    productForm.querySelectorAll("[data-item-type-fields]").forEach(block => { block.hidden = true; });
    document.getElementById("add-item-product-spec").required = false;
    // Restored to its HTML default (visible, required) until a category
    // picks a Core — the category-change handler hides/unrequires it again
    // for Cable Core specifically.
    const skuField = document.getElementById("add-item-product-sku");
    skuField.hidden = false;
    skuField.required = true;

    document.getElementById("add-item-unit-form").reset();
    document.getElementById("add-item-batch-form").reset();
    applyAddItemDateDefaults();
    document.getElementById("inventory-item-msg").textContent = "";

    showAddItemStep("choice");
}

// "Today" is almost always right for Install Date/Expiry Date, so every
// date input across the wizard pre-fills with it — form.reset() reverts to
// each field's HTML default (none set), not whatever was last assigned via
// .value, so this has to run again after every reset, not just once at
// module load.
function applyAddItemDateDefaults() {
    const today = todayDateString();
    const installDate = document.getElementById("add-item-unit-install-date");
    if (installDate) installDate.value = today;
    const expiryDate = document.getElementById("add-item-unit-expiry-date");
    if (expiryDate) expiryDate.value = today;
    const batchInstallDate = document.getElementById("add-item-batch-install-date");
    if (batchInstallDate) batchInstallDate.value = today;
}

function productSummaryText() {
    if (!addItemProduct) return "";
    const skuOrSpec = addItemProduct.tracking_type === "inventory_length" ? addItemProduct.spec : addItemProduct.sku;
    return `${addItemProduct.name} — ${skuOrSpec}`;
}

function renderUnitProductSummary() {
    const el = document.getElementById("add-item-unit-product-summary");
    if (el) el.textContent = productSummaryText();
}

function renderBatchProductSummary() {
    const el = document.getElementById("add-item-batch-product-summary");
    if (el) el.textContent = productSummaryText();
}

// Assigned To only matters for custody-type categories (per_job assets
// don't have a "who's holding it" concept the same way) — everything else
// here just mirrors showTypeFields' plain if/else dispatch.
function showUnitTypeFields(trackingType) {
    const form = document.getElementById("add-item-unit-form");
    form.querySelectorAll("[data-item-type-fields]").forEach(block => {
        block.hidden = block.dataset.itemTypeFields !== trackingType;
    });
    if (trackingType === "asset_serialized") {
        const category = getInventoryCategories().find(c => c.id === addItemProduct.category_id);
        document.getElementById("add-item-unit-assigned-to-row").hidden = !(category && category.custody_type === "custody");
    }
}

function enterUnitStep() {
    renderUnitProductSummary();
    showUnitTypeFields(addItemProduct.tracking_type);
    // Once a unit's already been saved this sitting, there's nothing to go
    // "back" to — re-picking a different product mid-batch isn't the flow.
    document.getElementById("add-item-unit-back").hidden = addItemHasSavedInSitting;
    showAddItemStep("unit");
}

// Existing-product path, Asset Core only (see selectExistingProduct) — asks
// how many units arrived together instead of repeating the single-unit form
// per serial. Location/Status/Cost/Install Date/Notes are entered once and
// apply to the whole batch; each unit's own serial number is filled in
// afterward on the Batch Review step.
function enterBatchStep() {
    renderBatchProductSummary();
    const category = getInventoryCategories().find(c => c.id === addItemProduct.category_id);
    document.getElementById("add-item-batch-assigned-to-row").hidden = !(category && category.custody_type === "custody");
    showAddItemStep("batch");
}

function renderProductSearchResults(query) {
    const resultsEl = document.getElementById("add-item-product-results");
    const q = query.trim().toLowerCase();
    const products = buildProductList().filter(p => {
        if (!q) return true;
        const skuOrSpec = p.tracking_type === "inventory_length" ? p.spec : p.sku;
        return (p.name || "").toLowerCase().includes(q) || (skuOrSpec || "").toLowerCase().includes(q);
    }).slice(0, 30);

    if (products.length === 0) {
        resultsEl.innerHTML = `<div class="add-item-product-empty">No matching products — try "New product" instead.</div>`;
        return;
    }

    resultsEl.innerHTML = products.map(p => {
        const skuOrSpec = p.tracking_type === "inventory_length" ? p.spec : p.sku;
        return `
            <button type="button" class="add-item-product-result" data-item-id="${p.id}">
                <span class="add-item-product-result-name">${p.name}</span>
                <span class="add-item-product-result-meta">${skuOrSpec} — ${p.category_name}</span>
            </button>
        `;
    }).join("");

    resultsEl.querySelectorAll(".add-item-product-result").forEach(btn => {
        btn.addEventListener("click", () => {
            const item = products.find(p => String(p.id) === btn.dataset.itemId);
            if (item) selectExistingProduct(item);
        });
    });
}

function selectExistingProduct(item) {
    addItemProduct = {
        category_id: item.category_id, tracking_type: item.tracking_type,
        sku: item.sku, name: item.name,
        spec_capacity: item.spec_capacity, spec: item.spec,
        unit_of_measure: item.unit_of_measure,
    };
    // Batch quantity only makes sense for Asset Core — Consumables Core's
    // batch total already lives in one row's Quantity field, and Cable
    // Core's units are individually distinct cuts with their own lengths.
    if (item.tracking_type === "asset_serialized") {
        enterBatchStep();
    } else {
        enterUnitStep();
    }
}

function unitFormBody() {
    const trackingType = addItemProduct.tracking_type;
    const body = {
        category_id: addItemProduct.category_id,
        sku: addItemProduct.sku,
        name: addItemProduct.name,
        spec_capacity: addItemProduct.spec_capacity,
        spec: addItemProduct.spec,
        unit_of_measure: addItemProduct.unit_of_measure,
        // Free-typed — resolved server-side to a location_id (creating a
        // new is_store=true row if the name doesn't match one yet), same
        // pattern as Issue/Return Materials' Site field.
        location_name: document.getElementById("add-item-unit-location").value.trim() || null,
        unit_cost: numOrNull(document.getElementById("add-item-unit-cost").value),
        // Unit-level, not product-level — read fresh from this form every
        // time rather than carried from addItemProduct, since Supplier can
        // genuinely differ per unit even under one SKU.
        supplier: document.getElementById("add-item-unit-supplier").value || null,
        notes: document.getElementById("add-item-unit-notes").value || null,
        event_group_id: addItemEventGroupId,
    };

    if (trackingType === "asset_serialized") {
        body.serial_number = document.getElementById("add-item-unit-serial-number").value || null;
        body.make_model = document.getElementById("add-item-unit-make-model").value || null;
        body.asset_status = document.getElementById("add-item-unit-asset-status").value;
        const assignedToHidden = document.getElementById("add-item-unit-assigned-to-row").hidden;
        body.assigned_to_user_id = assignedToHidden ? null : numOrNull(document.getElementById("add-item-unit-assigned-to").value);
        body.install_date = document.getElementById("add-item-unit-install-date").value || null;
    } else if (trackingType === "inventory_quantity") {
        body.batch_lot = document.getElementById("add-item-unit-batch-lot").value || null;
        body.expiry_date = document.getElementById("add-item-unit-expiry-date").value || null;
        body.quantity_on_hand = numOrNull(document.getElementById("add-item-unit-quantity-on-hand").value);
    } else if (trackingType === "inventory_length") {
        body.cut_reel_id = document.getElementById("add-item-unit-cut-reel-id").value || null;
        body.length_received = numOrNull(document.getElementById("add-item-unit-length-received").value);
    }

    return body;
}

// Same six values as every other Asset Status <select> in this codebase
// (inventory.html's Step 2 unit form, Stock's Edit Unit form) — kept as a
// plain inline option list here too rather than a shared JS constant, since
// no other page's <select> here is dynamically generated the way this
// per-row one is.
const ASSET_STATUS_OPTIONS = `
    <option value="Active">Active</option>
    <option value="Deployed">Deployed</option>
    <option value="Faulty">Faulty</option>
    <option value="In Repair">In Repair</option>
    <option value="Decommissioned">Decommissioned</option>
`;

// Batch Review — the editable list shown right after a batch save. Each
// field commits immediately via PATCH /inventory/items/{id} on blur/change,
// same click-to-edit-and-save pattern as Stock's Reorder Level column,
// rather than a single "Save all" button — a click-away partway through the
// list shouldn't lose the rows already filled in.
function renderBatchReviewTable() {
    const heading = document.getElementById("add-item-batch-review-heading");
    if (heading) {
        heading.textContent = `${addItemBatchItems.length} unit${addItemBatchItems.length === 1 ? "" : "s"} added. `
            + `Enter each one's manufacturer serial number if known, and correct Location/Status for any unit that differs from the rest.`;
    }

    const storeLocations = getInventoryLocations().filter(l => l.is_store);
    const locationOptions = storeLocations.map(l => `<option value="${l.id}">${l.name}</option>`).join("");

    const tbody = document.getElementById("add-item-batch-review-rows");
    tbody.innerHTML = addItemBatchItems.map(item => `
        <tr data-item-id="${item.id}">
            <td>${item.id}</td>
            <td><input type="text" class="add-item-batch-review-serial" placeholder="Manufacturer serial (optional)"></td>
            <td><select class="add-item-batch-review-location"><option value="">—</option>${locationOptions}</select></td>
            <td><select class="add-item-batch-review-status">${ASSET_STATUS_OPTIONS}</select></td>
        </tr>
    `).join("");

    tbody.querySelectorAll("tr").forEach(row => {
        const item = addItemBatchItems.find(i => String(i.id) === row.dataset.itemId);
        if (!item) return;

        const serialInput = row.querySelector(".add-item-batch-review-serial");
        serialInput.value = item.serial_number || "";
        const locationSelect = row.querySelector(".add-item-batch-review-location");
        locationSelect.value = item.location_id || "";
        const statusSelect = row.querySelector(".add-item-batch-review-status");
        statusSelect.value = item.asset_status || "Active";

        serialInput.addEventListener("blur", () => saveBatchReviewRow(item, { serial_number: serialInput.value || null }));
        locationSelect.addEventListener("change", () => saveBatchReviewRow(item, { location_id: numOrNull(locationSelect.value) }));
        statusSelect.addEventListener("change", () => saveBatchReviewRow(item, { asset_status: statusSelect.value }));
    });
}

async function saveBatchReviewRow(item, changes) {
    // Carries every other field through unchanged from the just-created item
    // — same "unedited fields ride along as-is" convention the Edit Unit
    // form already uses, since PATCH /inventory/items/{id} still requires
    // the full unit-level field set.
    const body = {
        sku: item.sku, name: item.name,
        make_model: item.make_model, spec_capacity: item.spec_capacity,
        supplier: item.supplier, unit_of_measure: item.unit_of_measure,
        location_id: item.location_id, unit_cost: item.unit_cost, notes: item.notes,
        serial_number: item.serial_number, asset_status: item.asset_status,
        assigned_to_user_id: item.assigned_to_user_id, install_date: item.install_date,
        ...changes,
    };

    const response = await fetch(`/inventory/items/${item.id}`, {
        method: "PATCH",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
    });

    if (response.ok) {
        Object.assign(item, changes);
        await Promise.all([refreshCatalog(), loadAllInventoryItems()]);
    } else {
        showMessage("inventory-item-msg", "Failed to save — try again", true);
    }
}

function initAddItemWizard() {
    const addOverlay = document.getElementById("add-inventory-item-overlay");
    const addOpenBtn = document.getElementById("add-inventory-item-open-btn");

    function closeWizard() {
        addOverlay.hidden = true;
        resetAddItemWizard();
    }

    addOpenBtn.addEventListener("click", () => {
        resetAddItemWizard();
        addOverlay.hidden = false;
        // Both paths — New Product (create a fresh SKU) and Existing
        // Product (add a unit to one already on file) — are gated by the
        // same "Add item" permission now that "Add Stock" was folded into
        // it, so both pills show/hide together. Evaluated here (on open),
        // not at initAddItemWizard's own call time — that runs once at app
        // boot, before login has populated currentUser/currentPermissions,
        // so can() would see no one logged in yet and hide both pills for
        // everyone, admins included.
        choiceNewBtn.hidden = !can("inventory_items", "add");
        choiceExistingBtn.hidden = !can("inventory_items", "add");
    });
    document.getElementById("add-item-close").addEventListener("click", closeWizard);
    addOverlay.addEventListener("click", (e) => { if (e.target === addOverlay) closeWizard(); });

    initQtyStepper("add-item-product-reorder-level");
    initQtyStepper("add-item-unit-quantity-on-hand");
    initQtyStepper("add-item-unit-length-received");
    initQtyStepper("add-item-unit-cost");
    initQtyStepper("add-item-batch-quantity");
    initQtyStepper("add-item-batch-unit-cost");

    // Picking either pill immediately advances the wizard, so without a
    // beat between the two, the .active green tint and the step swap land
    // in the same synchronous tick — the browser never gets a chance to
    // paint the pill before its container is hidden, so the click reads as
    // dead. This delay is just long enough to let that one frame render.
    const choiceExistingBtn = document.getElementById("add-item-choice-existing");
    const choiceNewBtn = document.getElementById("add-item-choice-new");
    choiceExistingBtn.addEventListener("click", () => {
        choiceExistingBtn.classList.add("active");
        choiceNewBtn.classList.remove("active");
        addItemMode = "existing";
        renderProductSearchResults("");
        setTimeout(() => showAddItemStep("search"), 160);
    });
    choiceNewBtn.addEventListener("click", () => {
        choiceNewBtn.classList.add("active");
        choiceExistingBtn.classList.remove("active");
        addItemMode = "new";
        setTimeout(() => showAddItemStep("product"), 160);
    });

    document.getElementById("add-item-product-search-input").addEventListener("input", (e) => {
        renderProductSearchResults(e.target.value);
    });
    document.getElementById("add-item-search-back").addEventListener("click", () => showAddItemStep("choice"));

    const productForm = document.getElementById("add-item-product-form");
    const productCategorySelect = document.getElementById("add-item-product-category");
    productCategorySelect.addEventListener("change", () => {
        const category = getInventoryCategories().find(c => String(c.id) === productCategorySelect.value);
        if (category) {
            productForm.querySelectorAll("[data-item-type-fields]").forEach(block => {
                block.hidden = block.dataset.itemTypeFields !== category.tracking_type;
            });
            // A `required` field inside a hidden block still fails
            // checkValidity() in Chromium — hiding an ancestor isn't enough,
            // the attribute itself has to come off (same fix already used in
            // inventory-log.js's updateAssetStatusVisibility).
            document.getElementById("add-item-product-spec").required = category.tracking_type === "inventory_length";

            // Cable Core has no separate SKU concept — Spec is the sole
            // identifying field, so the SKU input is hidden (not just
            // optional) for this Core specifically. The backend forces
            // sku to mirror spec regardless of what's sent, so it's safe
            // to leave this field blank/stale rather than special-casing
            // its value on submit.
            const isCable = category.tracking_type === "inventory_length";
            const skuField = document.getElementById("add-item-product-sku");
            skuField.hidden = isCable;
            skuField.required = !isCable;
            if (isCable) skuField.value = "";
        }
    });
    document.getElementById("add-item-product-back").addEventListener("click", () => showAddItemStep("choice"));

    productForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const category = getInventoryCategories().find(c => String(c.id) === productCategorySelect.value);
        if (!category) return;

        addItemProduct = {
            category_id: category.id, tracking_type: category.tracking_type,
            sku: document.getElementById("add-item-product-sku").value,
            name: document.getElementById("add-item-product-name").value,
            spec_capacity: document.getElementById("add-item-product-spec-capacity").value || null,
            spec: document.getElementById("add-item-product-spec").value || null,
            // Optional — can be left blank here and set later via
            // edit-on-click on Stock's Reorder Level column. Both write to
            // the same inventory_sku_thresholds row (PATCH /inventory/
            // reorder-level is an upsert), so there's no separate "set it
            // later" flow distinct from "edit it now".
            reorder_level: numOrNull(document.getElementById("add-item-product-reorder-level").value),
        };
        enterUnitStep();
    });

    document.getElementById("add-item-unit-back").addEventListener("click", () => {
        showAddItemStep(addItemMode === "existing" ? "search" : "product");
    });

    document.getElementById("add-item-unit-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!addItemProduct) return;

        const response = await fetch("/inventory/units", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(unitFormBody())
        });

        if (response.ok) {
            const result = await response.json();
            addItemEventGroupId = result.event_group_id;
            // Reorder Level (Step 1, New product path only) is product-level,
            // so it's only ever applied once per sitting — on the first unit
            // saved, not on every "+Add another unit" that follows.
            const isFirstSaveThisSitting = !addItemHasSavedInSitting;
            addItemHasSavedInSitting = true;
            const identifier = addItemProduct.tracking_type === "inventory_length"
                ? result.item.cut_reel_id
                : (result.item.serial_number || result.item.sku);
            document.getElementById("add-item-success-message").textContent =
                `Added ${result.item.name}${identifier ? ` (${identifier})` : ""} — logged as an In transaction.`;

            const setReorderLevel = isFirstSaveThisSitting && addItemProduct.reorder_level != null
                ? fetch("/inventory/reorder-level", {
                    method: "PATCH",
                    headers: authHeaders({ "Content-Type": "application/json" }),
                    body: JSON.stringify({
                        category_id: addItemProduct.category_id,
                        sku_or_spec: addItemProduct.tracking_type === "inventory_length" ? addItemProduct.spec : addItemProduct.sku,
                        reorder_level: addItemProduct.reorder_level,
                    }),
                })
                : Promise.resolve();

            // Refreshes the location cache too — a brand-new store typed
            // into the Location field above needs to show up in the
            // autocomplete's suggestions for the next unit in this same
            // "+Add another unit" sitting, same as Issue Materials already
            // does for its own Site field after a submit.
            await Promise.all([refreshCatalog(), loadAllInventoryItems(), loadInventoryLocations(), setReorderLevel]);
            populateLocationDropdowns();
            showAddItemStep("success");
        } else {
            const err = await response.json().catch(() => ({}));
            showMessage("inventory-item-msg", err.detail || "Failed to add unit", true);
        }
    });

    document.getElementById("add-item-add-another-btn").addEventListener("click", () => {
        document.getElementById("add-item-unit-form").reset();
        applyAddItemDateDefaults();
        enterUnitStep();
    });
    document.getElementById("add-item-done-btn").addEventListener("click", closeWizard);

    document.getElementById("add-item-batch-back").addEventListener("click", () => {
        showAddItemStep("search");
    });

    document.getElementById("add-item-batch-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!addItemProduct) return;

        const quantity = Number(document.getElementById("add-item-batch-quantity").value);
        const assignedToHidden = document.getElementById("add-item-batch-assigned-to-row").hidden;

        const response = await fetch("/inventory/units/batch", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({
                category_id: addItemProduct.category_id,
                sku: addItemProduct.sku,
                name: addItemProduct.name,
                // Make/Model and Supplier are unit-level, not product-level
                // — shared across this one batch sitting (like Location/
                // Status/Cost below), read fresh from the batch form rather
                // than carried from addItemProduct.
                make_model: document.getElementById("add-item-batch-make-model").value || null,
                spec_capacity: addItemProduct.spec_capacity,
                supplier: document.getElementById("add-item-batch-supplier").value || null,
                unit_of_measure: addItemProduct.unit_of_measure,
                quantity,
                // Free-typed, resolved server-side — same as Add Unit's
                // Location field above.
                location_name: document.getElementById("add-item-batch-location").value.trim() || null,
                unit_cost: numOrNull(document.getElementById("add-item-batch-unit-cost").value),
                asset_status: document.getElementById("add-item-batch-asset-status").value,
                assigned_to_user_id: assignedToHidden ? null : numOrNull(document.getElementById("add-item-batch-assigned-to").value),
                install_date: document.getElementById("add-item-batch-install-date").value || null,
                notes: document.getElementById("add-item-batch-notes").value || null,
                event_group_id: addItemEventGroupId,
            }),
        });

        if (response.ok) {
            const result = await response.json();
            addItemEventGroupId = result.event_group_id;
            addItemHasSavedInSitting = true;
            addItemBatchItems = result.items;
            await Promise.all([refreshCatalog(), loadAllInventoryItems(), loadInventoryLocations()]);
            populateLocationDropdowns();
            renderBatchReviewTable();
            showAddItemStep("batch-review");
        } else {
            const err = await response.json().catch(() => ({}));
            showMessage("inventory-item-msg", err.detail || "Failed to add units", true);
        }
    });

    document.getElementById("add-item-batch-review-done-btn").addEventListener("click", closeWizard);
}

export function initInventory() {
    initAddItemWizard();
    initEditProductModal();
    initUnitListModal();
    initUnitDetailModal();
    initSkuHistoryModal();

    document.getElementById("inventory-items-category-filter").addEventListener("change", async (e) => {
        itemsCategoryFilter = e.target.value;
        await refreshCatalog();
    });

    document.getElementById("inventory-items-search").addEventListener("input", (e) => {
        itemsSearchQuery = e.target.value;
        renderItemsTable();
    });

    document.getElementById("export-items-csv-btn").addEventListener("click", exportItemsCsv);

    registerAppShownHandler(async () => {
        await Promise.all([
            loadInventoryCategories(), loadInventoryLocations(),
            loadInventoryAssignableUsers(), loadAllInventoryItems(),
        ]);
        populateCategoryFilter();
        populateLocationDropdowns();
        populateAssignedToDropdowns();
        await refreshCatalog();
    });

    registerRoute("inventory", async () => {
        showView("view-inventory");
        // Re-fetched on every visit, not just at login, so a category added
        // on Manage or a unit added on this page's own wizard shows up
        // immediately.
        await Promise.all([loadInventoryCategories(), loadInventoryLocations(), loadAllInventoryItems()]);
        populateCategoryFilter();
        populateLocationDropdowns();
        await refreshCatalog();
    });

    // Global ⌘K coverage for inventory — getAllInventoryItems() is already
    // populated at login (this view's own registerAppShownHandler above),
    // so no ensureLoaded is needed here. One result per (category, SKU/spec)
    // group, not per physical unit — Items and Stock are two views over the
    // same rows, and every unit in a group opens the identical action
    // anyway (the SKU-level stock-history view, type-dispatched correctly
    // for all three Cores rather than assuming a per-unit modal exists,
    // which Consumables don't have) — so one entry per real unit would just
    // be the same product repeated N times in the results. searchText still
    // covers every unit's own serial/batch/reel id under that group, so
    // typing a specific serial number still finds the right product.
    registerCmdkProvider({
        getItems: () => {
            if (!can("inventory_items", "view")) return [];
            const groups = new Map();
            getAllInventoryItems().forEach(item => {
                const key = `${item.category_id}::${item.sku}`;
                if (!groups.has(key)) groups.set(key, { item, extras: [] });
                groups.get(key).extras.push(item.serial_number, item.batch_lot, item.cut_reel_id);
            });
            return Array.from(groups.values()).map(({ item, extras }) => ({
                type: "inventory-item",
                label: item.name,
                sublabel: [item.sku, item.category_name].filter(Boolean).join(" — "),
                searchText: [item.name, item.sku, item.category_name, ...extras].filter(Boolean).join(" "),
                action: () => {
                    navigate("stock");
                    openStockHistory({
                        categoryId: item.category_id, sku: item.sku,
                        trackingType: item.tracking_type, categoryName: item.category_name,
                    });
                },
            }));
        },
    });
}
