import {
    authHeaders, showMessage, deleteIconSvg, showView, registerRoute,
} from "./common.js";
import {
    loadInventoryLocations, getInventoryLocations,
    loadAllInventoryItems, getAllInventoryItems,
    loadInventoryAssignableUsers, getInventoryAssignableUsers,
} from "./inventory-common.js";

// item_id -> { item, qty }. Quantity lines carry a number; Asset and Length
// lines don't (the serial is the line, and a cut always goes out whole).
let cart = new Map();

function populateIssueDropdowns() {
    const locations = getInventoryLocations();
    const siteSelect = document.getElementById("issue-site");
    if (siteSelect) {
        siteSelect.innerHTML = `<option value="" disabled selected>Site / destination...</option>` +
            locations.map(l => `<option value="${l.id}">${l.name}</option>`).join("");
    }

    const users = getInventoryAssignableUsers();
    const issuedToSelect = document.getElementById("issue-issued-to");
    if (issuedToSelect) {
        issuedToSelect.innerHTML = `<option value="">Issued to (optional)</option>` +
            users.map(u => `<option value="${u.id}">${u.name}</option>`).join("");
    }
}

// One search across every category — the Assets vs Inventory split is a
// backend concern and must not surface anywhere in this flow.
function searchItems(query) {
    const q = query.toLowerCase().trim();
    if (!q) return [];
    return getAllInventoryItems().filter(item => {
        const haystack = [
            item.name, item.sku, item.category_name, item.serial_number,
            item.batch_lot, item.cut_reel_id, item.spec, item.location_name,
        ].filter(Boolean).join(" ").toLowerCase();
        return haystack.includes(q);
    }).slice(0, 20);
}

function availabilityLabel(item) {
    if (item.tracking_type === "asset_serialized") {
        return `SN ${item.serial_number || "—"} · ${item.asset_status || "—"}`;
    }
    if (item.tracking_type === "inventory_quantity") {
        return `${item.quantity_on_hand ?? 0} ${item.unit_of_measure || ""} on hand`.replace(/\s+/g, " ");
    }
    return `Reel ${item.cut_reel_id || "—"} · ${item.length_remaining ?? "—"}m · ${item.length_status || "—"}`;
}

function renderResults(results) {
    const container = document.getElementById("issue-results");
    if (!container) return;

    if (results.length === 0) {
        container.innerHTML = `<div class="issue-empty">No matching stock</div>`;
        return;
    }

    container.innerHTML = results.map(item => `
        <button type="button" class="issue-result" data-id="${item.id}" ${cart.has(item.id) ? "disabled" : ""}>
            <span class="issue-result-name">${item.name}</span>
            <span class="issue-result-meta">${item.sku} · ${item.category_name} · ${item.location_name || "no location"}</span>
            <span class="issue-result-avail">${availabilityLabel(item)}</span>
        </button>
    `).join("");

    container.querySelectorAll(".issue-result").forEach(btn => {
        btn.addEventListener("click", () => addToCart(Number(btn.dataset.id)));
    });
}

function addToCart(itemId) {
    const item = getAllInventoryItems().find(i => i.id === itemId);
    if (!item || cart.has(itemId)) return;
    cart.set(itemId, { item, qty: item.tracking_type === "inventory_quantity" ? 1 : null });
    renderCart();
    renderResults(searchItems(document.getElementById("issue-search").value));
}

function removeFromCart(itemId) {
    cart.delete(itemId);
    renderCart();
    renderResults(searchItems(document.getElementById("issue-search").value));
}

function renderCart() {
    const container = document.getElementById("issue-cart");
    if (!container) return;

    if (cart.size === 0) {
        container.innerHTML = `<div class="issue-empty">Search above to add materials — assets, consumables and cable all go in the same cart.</div>`;
        document.getElementById("issue-submit").disabled = true;
        return;
    }
    document.getElementById("issue-submit").disabled = false;

    container.innerHTML = Array.from(cart.values()).map(({ item, qty }) => `
        <div class="issue-cart-line">
            <div class="issue-cart-line-main">
                <span class="issue-cart-line-name">${item.name}</span>
                <span class="issue-cart-line-meta">${item.sku} · ${item.location_name || "no location"}</span>
            </div>
            ${item.tracking_type === "inventory_quantity" ? `
                <input type="number" step="any" min="0" class="issue-cart-qty" data-id="${item.id}"
                       value="${qty}" max="${item.quantity_on_hand ?? ""}">
                <span class="issue-cart-unit">${item.unit_of_measure || "units"}</span>
            ` : `
                <span class="issue-cart-whole">${item.tracking_type === "inventory_length"
                    ? `whole cut · ${item.length_remaining ?? "—"}m`
                    : `SN ${item.serial_number || "—"}`}</span>
            `}
            <button type="button" class="inventory-icon-btn delete issue-cart-remove" data-id="${item.id}" title="Remove">
                ${deleteIconSvg()}
            </button>
        </div>
    `).join("");

    container.querySelectorAll(".issue-cart-qty").forEach(input => {
        input.addEventListener("input", () => {
            const line = cart.get(Number(input.dataset.id));
            if (line) line.qty = input.value === "" ? null : Number(input.value);
        });
    });

    container.querySelectorAll(".issue-cart-remove").forEach(btn => {
        btn.addEventListener("click", () => removeFromCart(Number(btn.dataset.id)));
    });
}

async function submitCart(e) {
    e.preventDefault();
    if (cart.size === 0) return;

    const body = {
        lines: Array.from(cart.values()).map(({ item, qty }) => ({
            item_id: item.id,
            qty_or_length: item.tracking_type === "inventory_quantity" ? qty : null,
        })),
        site_location_id: Number(document.getElementById("issue-site").value),
        activity: document.getElementById("issue-activity").value || null,
        issued_to_user_id: document.getElementById("issue-issued-to").value
            ? Number(document.getElementById("issue-issued-to").value) : null,
        notes: document.getElementById("issue-notes").value || null,
    };

    const response = await fetch("/inventory/issue", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body)
    });

    if (response.ok) {
        const result = await response.json();
        showMessage("issue-msg", `Issued ${result.transaction_ids.length} line(s) in one movement`, false);
        cart.clear();
        document.getElementById("issue-form").reset();
        document.getElementById("issue-search").value = "";
        await loadAllInventoryItems();
        renderCart();
        renderResults([]);
    } else {
        const err = await response.json().catch(() => ({}));
        showMessage("issue-msg", err.detail || "Failed to issue materials", true);
    }
}

export function initIssueMaterials() {
    document.getElementById("issue-search").addEventListener("input", (e) => {
        renderResults(searchItems(e.target.value));
    });

    document.getElementById("issue-form").addEventListener("submit", submitCart);

    registerRoute("issue-materials", async () => {
        showView("view-issue-materials");
        await Promise.all([
            loadAllInventoryItems(), loadInventoryLocations(), loadInventoryAssignableUsers(),
        ]);
        populateIssueDropdowns();
        renderCart();
        renderResults(searchItems(document.getElementById("issue-search").value));
    });
}
