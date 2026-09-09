import {
    authHeaders, showMessage, deleteIconSvg, showView, registerRoute,
} from "./common.js";
import {
    loadAllInventoryItems, getAllInventoryItems,
} from "./inventory-common.js";

// item_id -> { item, asset_status }. asset_status must be explicitly picked
// by the user for an asset_serialized line before submit — never inferred
// from the asset's prior state (see routers/inventory.py's _plan_return).
// Quantity/Length lines carry no status.
let cart = new Map();

const ASSET_RETURN_STATUSES = ["Active", "Faulty", "In Repair", "Decommissioned"];

// Same one-search-across-everything as Issue Materials — any active item is
// returnable regardless of its current status/location, so an unused,
// never-deployed enclosure goes through the same flow as a deployed one.
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
    const container = document.getElementById("return-results");
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
    cart.set(itemId, { item, asset_status: null });
    renderCart();
    renderResults(searchItems(document.getElementById("return-search").value));
}

function removeFromCart(itemId) {
    cart.delete(itemId);
    renderCart();
    renderResults(searchItems(document.getElementById("return-search").value));
}

function renderCart() {
    const container = document.getElementById("return-cart");
    if (!container) return;

    if (cart.size === 0) {
        container.innerHTML = `<div class="issue-empty">Search above to add materials to return.</div>`;
        document.getElementById("return-submit").disabled = true;
        return;
    }
    document.getElementById("return-submit").disabled = false;

    container.innerHTML = Array.from(cart.values()).map(({ item, asset_status }) => `
        <div class="issue-cart-line">
            <div class="issue-cart-line-main">
                <span class="issue-cart-line-name">${item.name}</span>
                <span class="issue-cart-line-meta">${item.sku} · ${item.location_name || "no location"}</span>
            </div>
            ${item.tracking_type === "asset_serialized" ? `
                <select class="return-cart-status" data-id="${item.id}" required>
                    <option value="" disabled ${asset_status ? "" : "selected"}>Status...</option>
                    ${ASSET_RETURN_STATUSES.map(s => `<option value="${s}" ${s === asset_status ? "selected" : ""}>${s}</option>`).join("")}
                </select>
            ` : `
                <span class="issue-cart-whole">${item.tracking_type === "inventory_length"
                    ? `whole cut · ${item.length_remaining ?? "—"}m`
                    : `${item.quantity_on_hand ?? "—"} ${item.unit_of_measure || ""} on hand`}</span>
            `}
            <button type="button" class="inventory-icon-btn delete issue-cart-remove" data-id="${item.id}" title="Remove">
                ${deleteIconSvg()}
            </button>
        </div>
    `).join("");

    container.querySelectorAll(".return-cart-status").forEach(select => {
        select.addEventListener("change", () => {
            const line = cart.get(Number(select.dataset.id));
            if (line) line.asset_status = select.value;
        });
    });

    container.querySelectorAll(".issue-cart-remove").forEach(btn => {
        btn.addEventListener("click", () => removeFromCart(Number(btn.dataset.id)));
    });
}

async function submitCart(e) {
    e.preventDefault();
    if (cart.size === 0) return;

    const notes = document.getElementById("return-notes").value.trim();
    if (!notes) { showMessage("return-msg", "Notes are required", true); return; }

    const missingStatus = Array.from(cart.values()).some(
        ({ item, asset_status }) => item.tracking_type === "asset_serialized" && !asset_status
    );
    if (missingStatus) { showMessage("return-msg", "Pick a status for every asset in the cart", true); return; }

    const body = {
        lines: Array.from(cart.values()).map(({ item, asset_status }) => ({
            item_id: item.id,
            asset_status: item.tracking_type === "asset_serialized" ? asset_status : null,
        })),
        notes,
    };

    const response = await fetch("/inventory/return", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body)
    });

    if (response.ok) {
        const result = await response.json();
        showMessage("return-msg", `Returned ${result.transaction_ids.length} line(s) in one movement`, false);
        cart.clear();
        document.getElementById("return-form").reset();
        document.getElementById("return-search").value = "";
        await loadAllInventoryItems();
        renderCart();
        renderResults([]);
    } else {
        const err = await response.json().catch(() => ({}));
        showMessage("return-msg", err.detail || "Failed to return materials", true);
    }
}

export function initReturnMaterials() {
    document.getElementById("return-search").addEventListener("input", (e) => {
        renderResults(searchItems(e.target.value));
    });

    document.getElementById("return-form").addEventListener("submit", submitCart);

    registerRoute("return-materials", async () => {
        showView("view-return-materials");
        await loadAllInventoryItems();
        renderCart();
        renderResults(searchItems(document.getElementById("return-search").value));
    });
}
