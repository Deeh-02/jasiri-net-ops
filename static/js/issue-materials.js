import {
    authHeaders, showMessage, deleteIconSvg, showView, registerRoute,
} from "./common.js";
import {
    loadInventoryLocations, getInventoryLocations,
    loadAllInventoryItems, getAllInventoryItems,
    loadInventoryAssignableUsers, getInventoryAssignableUsers,
    inventoryItemMatchesQuery, cartQtyStepperHtml, initQtyStepper,
    assetStatusBadgeHtml,
} from "./inventory-common.js";

// Keyed either by item_id -> { item, qty } (a manually-picked single line —
// qty is carried for Quantity lines only; Asset and Length lines don't since
// the serial is the line and a cut always goes out whole) OR by a synthetic
// "grp:<categoryId>:<sku>" key -> { isGroup: true, categoryId, sku, name,
// categoryName, itemIds: Set<number> } for a batch of Asset-core serials
// picked via Quick Issue (see quickAddAsset below). Both shapes coexist in
// the same map — a user can quick-add 5 of a SKU and then separately hand-
// pick one more specific serial of the same SKU, landing as two cart lines.
let cart = new Map();

function assetGroupKey(categoryId, sku) {
    return `grp:${categoryId}:${sku}`;
}

// True if a serial is already spoken for anywhere in the cart — either as
// its own manually-picked line, or inside a Quick Issue group's itemIds.
// Search results and quick-add eligibility both need this single check so
// neither path can double-pick the same physical unit.
function isItemPicked(itemId) {
    for (const entry of cart.values()) {
        if (entry.isGroup) { if (entry.itemIds.has(itemId)) return true; }
        else if (entry.item.id === itemId) return true;
    }
    return false;
}

// "Eligible" for Issue = in stock and not currently unavailable — Active is
// the sole asset_status that means that (Deployed is already issued,
// Faulty/In Repair/Decommissioned are all explicitly unavailable). Read off
// the full item cache rather than the capped 20-result search list, so a
// SKU with more in-stock serials than fit in one search page can still be
// quick-added past that cap.
function eligibleIssueSerials(categoryId, sku) {
    return getAllInventoryItems().filter(item =>
        item.tracking_type === "asset_serialized" &&
        item.category_id === categoryId && item.sku === sku &&
        item.asset_status === "Active" && !isItemPicked(item.id)
    );
}

// Deterministic, not random: picks from the bottom of the eligible list
// (the same order the individual serial results below render in) and works
// upward — never an arbitrary DB-order pick. getAllInventoryItems() is
// itself now stably ordered (name, then id as a tiebreaker — see
// db/inventory_items.py's get_all_items), so this returns the same serials
// every time for the same eligible set, not just a consistent-looking one.
// There's no existing FIFO/received-date stock rotation convention in this
// app to defer to instead (costing math — including any draw-down ordering
// — was explicitly deferred in the original phase plan and never built).
function quickAddAsset(categoryId, sku, qty) {
    const eligible = eligibleIssueSerials(categoryId, sku).slice(-Math.max(1, qty));
    if (eligible.length === 0) return;

    const key = assetGroupKey(categoryId, sku);
    let group = cart.get(key);
    if (!group) {
        const rep = eligible[0];
        group = { isGroup: true, categoryId, sku, name: rep.name, categoryName: rep.category_name, itemIds: new Set() };
        cart.set(key, group);
    }
    eligible.forEach(item => group.itemIds.add(item.id));

    renderCart();
    document.getElementById("issue-search").value = "";
    renderResults([]);
}

function removeGroupSerial(key, itemId) {
    const group = cart.get(key);
    if (!group) return;
    group.itemIds.delete(itemId);
    if (group.itemIds.size === 0) cart.delete(key);
    renderCart();
    renderResults(searchItems(document.getElementById("issue-search").value));
}

function populateIssueDropdowns() {
    // Free-typed with autocomplete, not a picklist — suggests every
    // previously-used location (store or job site) but a brand-new name
    // needs zero pre-configuration; the backend creates it on submit.
    const locations = getInventoryLocations();
    const siteSuggestions = document.getElementById("issue-site-suggestions");
    if (siteSuggestions) siteSuggestions.innerHTML = locations.map(l => `<option value="${l.name}"></option>`).join("");

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
    if (!query.trim()) return [];
    return getAllInventoryItems().filter(item => {
        // A depleted reel (zero length remaining, whether from full usage
        // or a fully-reconciled cut) is out of stock the same way a
        // zero-quantity Consumable would be — filtered here at
        // search/selection time rather than left to fail on submit.
        if (item.tracking_type === "inventory_length" && !(item.length_remaining > 0)) return false;
        return inventoryItemMatchesQuery(item, query);
    }).slice(0, 20);
}

function availabilityLabel(item) {
    if (item.tracking_type === "asset_serialized") {
        return `SN ${item.serial_number || "—"} · ${assetStatusBadgeHtml(item.asset_status)}`;
    }
    if (item.tracking_type === "inventory_quantity") {
        return `${item.quantity_on_hand ?? 0} ${item.unit_of_measure || ""} on hand`.replace(/\s+/g, " ");
    }
    return `Reel ${item.cut_reel_id || "—"} · ${item.length_remaining ?? "—"}m · ${item.length_status || "—"}`;
}

// Asset-core results are grouped by SKU so a Quick Issue control can sit
// once above every serial that SKU matched, rather than once per row — the
// individual serial buttons still follow underneath, unchanged, for manual
// picking. Non-asset results (Quantity/Length) render exactly as before.
function renderResults(results) {
    const container = document.getElementById("issue-results");
    if (!container) return;

    if (results.length === 0) {
        container.innerHTML = `<div class="issue-empty">No matching stock</div>`;
        return;
    }

    const seenAssetGroups = new Set();
    container.innerHTML = results.map(item => {
        let quickAddHtml = "";
        if (item.tracking_type === "asset_serialized") {
            const groupKey = assetGroupKey(item.category_id, item.sku);
            if (!seenAssetGroups.has(groupKey)) {
                seenAssetGroups.add(groupKey);
                const eligibleCount = eligibleIssueSerials(item.category_id, item.sku).length;
                quickAddHtml = `
                    <div class="issue-quick-add" data-category-id="${item.category_id}" data-sku="${item.sku}">
                        <div class="issue-quick-add-label">
                            <span class="issue-quick-add-name">${item.name}</span>
                            <span class="issue-quick-add-meta">${item.sku} · ${eligibleCount} available</span>
                        </div>
                        <input type="number" class="issue-quick-add-qty" min="1" max="${eligibleCount}"
                               value="${eligibleCount > 0 ? 1 : 0}" ${eligibleCount === 0 ? "disabled" : ""}>
                        <button type="button" class="btn-secondary issue-quick-add-btn"
                                data-category-id="${item.category_id}" data-sku="${item.sku}"
                                ${eligibleCount === 0 ? "disabled" : ""}>Quick Issue</button>
                    </div>
                `;
            }
        }
        return quickAddHtml + `
            <button type="button" class="issue-result" data-id="${item.id}" ${isItemPicked(item.id) ? "disabled" : ""}>
                <span class="issue-result-name">${item.name}</span>
                <span class="issue-result-meta">${item.sku} · ${item.category_name} · ${item.location_name || "no location"}</span>
                <span class="issue-result-avail">${availabilityLabel(item)}</span>
            </button>
        `;
    }).join("");

    container.querySelectorAll(".issue-result").forEach(btn => {
        btn.addEventListener("click", () => addToCart(Number(btn.dataset.id)));
    });
    container.querySelectorAll(".issue-quick-add-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const qtyInput = btn.closest(".issue-quick-add").querySelector(".issue-quick-add-qty");
            quickAddAsset(Number(btn.dataset.categoryId), btn.dataset.sku, Number(qtyInput.value) || 1);
        });
    });
}

function addToCart(itemId) {
    const item = getAllInventoryItems().find(i => i.id === itemId);
    if (!item || isItemPicked(itemId)) return;
    cart.set(itemId, { item, qty: item.tracking_type === "inventory_quantity" ? 1 : null });
    renderCart();
    // Dismiss the results list the same way it would look if the search box
    // were cleared by hand, instead of leaving stale matches open underneath
    // — the item just picked is already in the cart, so re-running the same
    // query has nothing new to show anyway.
    document.getElementById("issue-search").value = "";
    renderResults([]);
}

function removeFromCart(itemId) {
    cart.delete(itemId);
    renderCart();
    renderResults(searchItems(document.getElementById("issue-search").value));
}

// A Quick Issue group renders as one collapsible line — name/SKU/count in
// the summary, the specific serials picked underneath so the user can review
// or remove one before confirming (per-serial "swap" is just remove-then-
// manually-pick-a-different-one via search, no separate swap control).
function assetGroupCartLineHtml(key, group) {
    const items = Array.from(group.itemIds)
        .map(id => getAllInventoryItems().find(i => i.id === id))
        .filter(Boolean);
    return `
        <div class="issue-cart-line issue-cart-group">
            <details class="issue-cart-group-details">
                <summary>
                    <span class="issue-cart-line-name">${group.name}</span>
                    <span class="issue-cart-line-meta">${group.sku} · ${items.length} serial${items.length === 1 ? "" : "s"} selected</span>
                </summary>
                <div class="issue-cart-group-list">
                    ${items.map(item => `
                        <div class="issue-cart-group-item">
                            <span>SN ${item.serial_number || "—"}</span>
                            <button type="button" class="inventory-icon-btn delete issue-cart-group-remove" data-key="${key}" data-item-id="${item.id}" title="Remove">
                                ${deleteIconSvg()}
                            </button>
                        </div>
                    `).join("")}
                </div>
            </details>
            <button type="button" class="inventory-icon-btn delete issue-cart-remove-group" data-key="${key}" title="Remove all">
                ${deleteIconSvg()}
            </button>
        </div>
    `;
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

    container.innerHTML = Array.from(cart.entries()).map(([key, entry]) => {
        if (entry.isGroup) return assetGroupCartLineHtml(key, entry);
        const { item, qty } = entry;
        return `
            <div class="issue-cart-line">
                <div class="issue-cart-line-main">
                    <span class="issue-cart-line-name">${item.name}</span>
                    <span class="issue-cart-line-meta">${item.sku} · ${item.location_name || "no location"}</span>
                </div>
                ${item.tracking_type === "inventory_quantity" ? `
                    ${cartQtyStepperHtml({ id: `issue-cart-qty-${item.id}`, itemId: item.id, value: qty, max: item.quantity_on_hand })}
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
        `;
    }).join("");

    container.querySelectorAll(".issue-cart-qty").forEach(input => {
        initQtyStepper(input.id);
        input.addEventListener("input", () => {
            const line = cart.get(Number(input.dataset.id));
            if (line) line.qty = input.value === "" ? null : Number(input.value);
        });
    });

    container.querySelectorAll(".issue-cart-remove").forEach(btn => {
        btn.addEventListener("click", () => removeFromCart(Number(btn.dataset.id)));
    });
    container.querySelectorAll(".issue-cart-remove-group").forEach(btn => {
        btn.addEventListener("click", () => {
            cart.delete(btn.dataset.key);
            renderCart();
            renderResults(searchItems(document.getElementById("issue-search").value));
        });
    });
    container.querySelectorAll(".issue-cart-group-remove").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.preventDefault(); // inside <details> — don't toggle open/closed on remove
            removeGroupSerial(btn.dataset.key, Number(btn.dataset.itemId));
        });
    });
}

async function submitCart(e) {
    e.preventDefault();
    if (cart.size === 0) return;

    const siteName = document.getElementById("issue-site").value.trim();
    const notes = document.getElementById("issue-notes").value.trim();
    if (!siteName) { showMessage("issue-msg", "A site/destination is required", true); return; }
    if (!notes) { showMessage("issue-msg", "Notes are required", true); return; }

    const lines = [];
    for (const entry of cart.values()) {
        if (entry.isGroup) {
            entry.itemIds.forEach(id => lines.push({ item_id: id, qty_or_length: null }));
        } else {
            lines.push({
                item_id: entry.item.id,
                qty_or_length: entry.item.tracking_type === "inventory_quantity" ? entry.qty : null,
            });
        }
    }

    const body = {
        lines,
        site_location_name: siteName,
        activity: document.getElementById("issue-activity").value || null,
        issued_to_user_id: document.getElementById("issue-issued-to").value
            ? Number(document.getElementById("issue-issued-to").value) : null,
        notes,
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
        await Promise.all([loadAllInventoryItems(), loadInventoryLocations()]);
        populateIssueDropdowns();
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
