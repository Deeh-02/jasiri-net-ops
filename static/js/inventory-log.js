import {
    can, authHeaders, showMessage, registerAppShownHandler, showView, registerRoute,
} from "./common.js";
import {
    loadInventoryCategories, getInventoryCategories,
    loadInventoryLocations, getInventoryLocations,
    loadAllInventoryItems, getAllInventoryItems, itemPickerLabel,
    loadInventoryAssignableUsers, getInventoryAssignableUsers,
} from "./inventory-common.js";

let logCategoryFilter = "";
let logActionFilter = "";

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

    const items = getAllInventoryItems();
    const itemSelect = document.getElementById("inventory-transaction-item");
    if (itemSelect) {
        itemSelect.innerHTML = `<option value="" disabled selected>Item...</option>` +
            items.map(i => `<option value="${i.id}">${itemPickerLabel(i)}</option>`).join("");
    }

    const locations = getInventoryLocations();
    const locationOptions = locations.map(l => `<option value="${l.id}">${l.name}</option>`).join("");
    const toSelect = document.getElementById("inventory-transaction-to-location");
    if (toSelect) toSelect.innerHTML = `<option value="">To location...</option>${locationOptions}`;
    const fromSelect = document.getElementById("inventory-transaction-from-location");
    if (fromSelect) fromSelect.innerHTML = `<option value="">From location (optional)</option>${locationOptions}`;
    const siteSelect = document.getElementById("inventory-transaction-site-location");
    if (siteSelect) siteSelect.innerHTML = `<option value="">Site (optional)</option>${locationOptions}`;

    const users = getInventoryAssignableUsers();
    const issuedToSelect = document.getElementById("inventory-transaction-issued-to");
    if (issuedToSelect) {
        issuedToSelect.innerHTML = `<option value="">Issued to (optional)</option>` +
            users.map(u => `<option value="${u.id}">${u.name}</option>`).join("");
    }
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

function renderLogTable(rows) {
    const tbody = document.getElementById("inventory-log-rows");
    if (!tbody) return;

    tbody.innerHTML = rows.map(row => `
        <tr>
            <td>${formatDateTime(row.created_at)}</td>
            <td>${row.action}</td>
            <td>${row.sku_or_spec}</td>
            <td>${row.qty_or_length ?? "—"}</td>
            <td>${row.from_location_name || "—"}</td>
            <td>${row.to_location_name || "—"}</td>
            <td>${row.site_location_name || "—"}</td>
            <td>${row.activity || "—"}</td>
            <td>${row.issued_to_name || "—"}</td>
            <td>${row.logged_by_name}</td>
            <td>${row.notes || "—"}</td>
        </tr>
    `).join("");
}

function initTransactionForm() {
    const addOverlay = document.getElementById("add-inventory-transaction-overlay");
    const addOpenBtn = document.getElementById("add-inventory-transaction-open-btn");
    const addCancelBtn = document.getElementById("add-inventory-transaction-cancel");
    const form = document.getElementById("inventory-transaction-form");

    addOpenBtn.addEventListener("click", () => {
        form.reset();
        addOverlay.hidden = false;
    });

    addCancelBtn.addEventListener("click", () => { addOverlay.hidden = true; });
    addOverlay.addEventListener("click", (e) => { if (e.target === addOverlay) addOverlay.hidden = true; });

    form.addEventListener("submit", async (e) => {
        e.preventDefault();

        const body = {
            action: document.getElementById("inventory-transaction-action").value,
            item_id: Number(document.getElementById("inventory-transaction-item").value),
            qty_or_length: numOrNull(document.getElementById("inventory-transaction-qty").value),
            to_location_id: numOrNull(document.getElementById("inventory-transaction-to-location").value),
            from_location_id: numOrNull(document.getElementById("inventory-transaction-from-location").value),
            site_location_id: numOrNull(document.getElementById("inventory-transaction-site-location").value),
            activity: document.getElementById("inventory-transaction-activity").value || null,
            issued_to_user_id: numOrNull(document.getElementById("inventory-transaction-issued-to").value),
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
            await Promise.all([loadAllInventoryItems().then(populateLogDropdowns), refreshLog()]);
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
}

export function initInventoryLog() {
    initTransactionForm();

    registerAppShownHandler(async () => {
        await Promise.all([
            loadInventoryCategories(), loadInventoryLocations(),
            loadAllInventoryItems(), loadInventoryAssignableUsers(),
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
}
