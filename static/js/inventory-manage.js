import {
    can, authHeaders, showMessage, editIconSvg, deleteIconSvg,
    registerAppShownHandler, showView, registerRoute,
} from "./common.js";
import {
    trackingTypeLabel, loadInventoryCategories, getInventoryCategories,
} from "./inventory-common.js";

let editCategoryId = null;

// ---- Categories ----
// Locations no longer have a management screen here (or anywhere) —
// Issue/Return Materials' free-typed Site field creates them implicitly.
// See inventory-common.js's getInventoryLocations and
// locations_db.get_or_create_location_by_name.

const CUSTODY_TYPE_LABELS = { per_job: "Per-Job", custody: "Custody" };

async function refreshCategories() {
    await loadInventoryCategories();
    renderCategoryList(getInventoryCategories());
}

function renderCategoryList(categories) {
    const tbody = document.getElementById("inventory-categories-rows");
    if (!tbody) return;

    const hasActions = can("inventory_categories", "edit") || can("inventory_categories", "delete");

    tbody.innerHTML = categories.map(cat => `
        <tr>
            <td>${cat.name}</td>
            <td><span class="tracking-type-badge">${trackingTypeLabel(cat.tracking_type)}</span></td>
            <td>${CUSTODY_TYPE_LABELS[cat.custody_type] || cat.custody_type}</td>
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
    document.getElementById("edit-inventory-category-custody-type").value = cat.custody_type || "per_job";
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
        const custody_type = document.getElementById("inventory-category-custody-type").value;
        const description = document.getElementById("inventory-category-description").value || null;

        const response = await fetch("/inventory/categories", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ name, tracking_type, custody_type, description })
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
            custody_type: document.getElementById("edit-inventory-category-custody-type").value,
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

export function initInventoryManage() {
    initCategoryForms();

    registerAppShownHandler(async () => {
        await refreshCategories();
    });

    registerRoute("inventory-manage", async () => {
        showView("view-inventory-manage");
        // Re-fetched on every visit, not just at login, so an edit made
        // elsewhere in the same session (or in another tab) shows up here
        // immediately rather than needing a full reload.
        await refreshCategories();
    });
}
