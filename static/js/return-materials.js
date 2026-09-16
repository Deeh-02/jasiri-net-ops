import {
    authHeaders, showMessage, deleteIconSvg, showView, registerRoute,
} from "./common.js";
import {
    loadAllInventoryItems, getAllInventoryItems, inventoryItemMatchesQuery,
    cartQtyStepperHtml, initQtyStepper,
    loadInventoryAssignableUsers, assignableUserName, assetStatusBadgeHtml,
} from "./inventory-common.js";

// Keyed either by item_id -> { item, asset_status, qty } (a manually-picked
// single line — asset_status must be explicitly picked by the user for an
// asset_serialized line before submit, never inferred from the asset's
// prior state, see routers/inventory.py's _plan_return; qty is only
// meaningful for inventory_quantity, defaulting to the item's full
// quantity_on_hand — "return everything"; Length never reaches this cart at
// all, see searchItems below) OR by a synthetic
// "grp:<categoryId>:<sku>:<sourceKey>" key -> { isGroup: true, categoryId,
// sku, name, sourceKey, sourceLabel, itemIds: Set<number>, asset_status }
// for a batch of Asset-core serials picked via Quick Return — one status
// applies to the whole batch, same as a manually-picked line's own select.
let cart = new Map();

const ASSET_RETURN_STATUSES = ["Active", "Faulty", "In Repair", "Decommissioned"];

function assetGroupKey(categoryId, sku, sourceKey) {
    return `grp:${categoryId}:${sku}:${sourceKey}`;
}

// True if a serial is already spoken for anywhere in the cart — either as
// its own manually-picked line, or inside a Quick Return group's itemIds.
function isItemPicked(itemId) {
    for (const entry of cart.values()) {
        if (entry.isGroup) { if (entry.itemIds.has(itemId)) return true; }
        else if (entry.item.id === itemId) return true;
    }
    return false;
}

// A returned asset's "source" is wherever it's currently checked out to —
// the person holding it if one's assigned (custody-type categories), else
// the site it's physically at (per-job categories). Mirrors the same
// holder-vs-location precedence the unit list/detail modal already use
// elsewhere in the app.
function sourceKeyOf(item) {
    return item.assigned_to_user_id ? `user:${item.assigned_to_user_id}` : `loc:${item.location_id ?? "none"}`;
}

function sourceLabelOf(item) {
    return item.assigned_to_user_id
        ? `Person: ${assignableUserName(item.assigned_to_user_id) || "Unknown"}`
        : `Site: ${item.location_name || "Unknown"}`;
}

// Every distinct source currently holding a Deployed serial of this SKU —
// powers the Quick Return source picker so "eligible" can be scoped to
// exactly one source rather than the full universe of issued units
// elsewhere, per the owner's explicit requirement.
function distinctReturnSources(categoryId, sku) {
    const seen = new Map();
    getAllInventoryItems().forEach(item => {
        if (item.tracking_type !== "asset_serialized" || item.category_id !== categoryId ||
            item.sku !== sku || item.asset_status !== "Deployed") return;
        const key = sourceKeyOf(item);
        if (!seen.has(key)) seen.set(key, { key, label: sourceLabelOf(item) });
    });
    return [...seen.values()];
}

// "Eligible" for Return = currently Deployed AND checked out to the exact
// source picked — not the full universe of issued units for this SKU
// elsewhere.
function eligibleReturnSerials(categoryId, sku, sourceKey) {
    return getAllInventoryItems().filter(item =>
        item.tracking_type === "asset_serialized" &&
        item.category_id === categoryId && item.sku === sku &&
        item.asset_status === "Deployed" && sourceKeyOf(item) === sourceKey &&
        !isItemPicked(item.id)
    );
}

// Deterministic, not random — same "pick from the bottom of the list and
// work upward" convention as Quick Issue (see issue-materials.js's
// quickAddAsset for why: a stable getAllInventoryItems() order plus no
// existing FIFO/received-date rotation convention to defer to instead).
function quickReturnAsset(categoryId, sku, sourceKey, qty) {
    const eligible = eligibleReturnSerials(categoryId, sku, sourceKey).slice(-Math.max(1, qty));
    if (eligible.length === 0) return;

    const key = assetGroupKey(categoryId, sku, sourceKey);
    let group = cart.get(key);
    if (!group) {
        const rep = eligible[0];
        group = {
            isGroup: true, categoryId, sku, name: rep.name,
            sourceKey, sourceLabel: sourceLabelOf(rep), itemIds: new Set(), asset_status: null,
        };
        cart.set(key, group);
    }
    eligible.forEach(item => group.itemIds.add(item.id));

    renderCart();
    document.getElementById("return-search").value = "";
    renderResults([]);
}

function removeGroupSerial(key, itemId) {
    const group = cart.get(key);
    if (!group) return;
    group.itemIds.delete(itemId);
    if (group.itemIds.size === 0) cart.delete(key);
    renderCart();
    renderResults(searchItems(document.getElementById("return-search").value));
}

// Scoped to what's actually issued out, not general/available stock — a
// Return is for bringing something back, so an in-stock serial has nothing
// to return (supersedes an earlier version of this search that deliberately
// included every status; the owner corrected that — an unused, never-
// deployed enclosure has no "return" to make either, so it's excluded too).
// Asset Core: only Deployed serials match. Quantity has no per-row "issued"
// flag to filter on at all — issuing decrements quantity_on_hand in place
// rather than moving stock into a separate issued-out row (see
// _plan_issue_line in routers/inventory.py), so there's no way to tell
// "issued" apart from "on hand" for this Core at the row level; every
// active Quantity row stays searchable here, same as before. Cable Core is
// still excluded outright: every outcome for an issued cut (fully used,
// partially used, never touched) is already expressible through
// Reconciliation's Length Used/Length Returned fields, so cable never
// belongs in this flow — rejected server-side too if this were ever
// bypassed (see _plan_return in routers/inventory.py).
function searchItems(query) {
    if (!query.trim()) return [];
    return getAllInventoryItems().filter(item => {
        if (item.tracking_type === "inventory_length") return false;
        if (item.tracking_type === "asset_serialized" && item.asset_status !== "Deployed") return false;
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

// Asset-core results are grouped by SKU so a Quick Return control (quantity
// + source picker) can sit once above every serial that SKU matched — the
// individual serial buttons still follow underneath, unchanged, for manual
// picking. A SKU with no currently-Deployed serials gets no quick-return
// control at all (nothing eligible to auto-select), same as issue-materials.
function renderResults(results) {
    const container = document.getElementById("return-results");
    if (!container) return;

    if (results.length === 0) {
        container.innerHTML = `<div class="issue-empty">No matching stock</div>`;
        return;
    }

    const seenAssetGroups = new Set();
    container.innerHTML = results.map(item => {
        let quickAddHtml = "";
        if (item.tracking_type === "asset_serialized") {
            const groupSeenKey = `${item.category_id}:${item.sku}`;
            if (!seenAssetGroups.has(groupSeenKey)) {
                seenAssetGroups.add(groupSeenKey);
                const sources = distinctReturnSources(item.category_id, item.sku);
                if (sources.length > 0) {
                    const firstEligible = eligibleReturnSerials(item.category_id, item.sku, sources[0].key).length;
                    quickAddHtml = `
                        <div class="issue-quick-add" data-category-id="${item.category_id}" data-sku="${item.sku}">
                            <div class="issue-quick-add-label">
                                <span class="issue-quick-add-name">${item.name}</span>
                                <span class="issue-quick-add-meta">${item.sku} · deployed</span>
                            </div>
                            <select class="issue-quick-add-source">
                                ${sources.map(s => `<option value="${s.key}">${s.label}</option>`).join("")}
                            </select>
                            <input type="number" class="issue-quick-add-qty" min="1" max="${firstEligible}"
                                   value="${firstEligible > 0 ? 1 : 0}" ${firstEligible === 0 ? "disabled" : ""}>
                            <button type="button" class="btn-secondary issue-quick-add-btn"
                                    data-category-id="${item.category_id}" data-sku="${item.sku}"
                                    ${firstEligible === 0 ? "disabled" : ""}>Quick Return</button>
                        </div>
                    `;
                }
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
    container.querySelectorAll(".issue-quick-add").forEach(wrap => {
        const categoryId = Number(wrap.dataset.categoryId);
        const sku = wrap.dataset.sku;
        const sourceSelect = wrap.querySelector(".issue-quick-add-source");
        const qtyInput = wrap.querySelector(".issue-quick-add-qty");
        const btn = wrap.querySelector(".issue-quick-add-btn");

        const syncMaxToSource = () => {
            const count = eligibleReturnSerials(categoryId, sku, sourceSelect.value).length;
            qtyInput.max = count;
            qtyInput.value = count > 0 ? 1 : 0;
            qtyInput.disabled = count === 0;
            btn.disabled = count === 0;
        };
        sourceSelect.addEventListener("change", syncMaxToSource);
        btn.addEventListener("click", () => {
            quickReturnAsset(categoryId, sku, sourceSelect.value, Number(qtyInput.value) || 1);
        });
    });
}

function addToCart(itemId) {
    const item = getAllInventoryItems().find(i => i.id === itemId);
    if (!item || isItemPicked(itemId)) return;
    cart.set(itemId, {
        item, asset_status: null,
        qty: item.tracking_type === "inventory_quantity" ? item.quantity_on_hand : null,
    });
    renderCart();
    // Dismiss the results list the same way it would look if the search box
    // were cleared by hand — see issue-materials.js's addToCart for the same
    // fix; Return Materials uses the identical search/results component.
    document.getElementById("return-search").value = "";
    renderResults([]);
}

function removeFromCart(itemId) {
    cart.delete(itemId);
    renderCart();
    renderResults(searchItems(document.getElementById("return-search").value));
}

// A Quick Return group renders as one collapsible line — name/SKU/source/
// count in the summary, the specific serials picked underneath so the user
// can review or remove one before confirming, plus one status select that
// applies to the whole batch (same requirement as a manually-picked line's
// own select — every asset returned needs an explicit status).
function assetGroupCartLineHtml(key, group) {
    const items = Array.from(group.itemIds)
        .map(id => getAllInventoryItems().find(i => i.id === id))
        .filter(Boolean);
    return `
        <div class="issue-cart-line issue-cart-group">
            <details class="issue-cart-group-details">
                <summary>
                    <span class="issue-cart-line-name">${group.name}</span>
                    <span class="issue-cart-line-meta">${group.sku} · ${group.sourceLabel} · ${items.length} serial${items.length === 1 ? "" : "s"} selected</span>
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
            <select class="return-cart-status return-cart-group-status" data-key="${key}" required>
                <option value="" disabled ${group.asset_status ? "" : "selected"}>Status...</option>
                ${ASSET_RETURN_STATUSES.map(s => `<option value="${s}" ${s === group.asset_status ? "selected" : ""}>${s}</option>`).join("")}
            </select>
            <button type="button" class="inventory-icon-btn delete issue-cart-remove-group" data-key="${key}" title="Remove all">
                ${deleteIconSvg()}
            </button>
        </div>
    `;
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

    container.innerHTML = Array.from(cart.entries()).map(([key, entry]) => {
        if (entry.isGroup) return assetGroupCartLineHtml(key, entry);
        const { item, asset_status, qty } = entry;
        return `
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
                ` : item.tracking_type === "inventory_quantity" ? `
                    ${cartQtyStepperHtml({ id: `return-cart-qty-${item.id}`, itemId: item.id, value: qty, max: item.quantity_on_hand })}
                    <span class="issue-cart-unit">${item.unit_of_measure || "units"}</span>
                ` : `
                    <span class="issue-cart-whole">whole cut · ${item.length_remaining ?? "—"}m</span>
                `}
                <button type="button" class="inventory-icon-btn delete issue-cart-remove" data-id="${item.id}" title="Remove">
                    ${deleteIconSvg()}
                </button>
            </div>
        `;
    }).join("");

    container.querySelectorAll(".return-cart-status:not(.return-cart-group-status)").forEach(select => {
        select.addEventListener("change", () => {
            const line = cart.get(Number(select.dataset.id));
            if (line) line.asset_status = select.value;
        });
    });
    container.querySelectorAll(".return-cart-group-status").forEach(select => {
        select.addEventListener("change", () => {
            const group = cart.get(select.dataset.key);
            if (group) group.asset_status = select.value;
        });
    });

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
            renderResults(searchItems(document.getElementById("return-search").value));
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

    const notes = document.getElementById("return-notes").value.trim();
    if (!notes) { showMessage("return-msg", "Notes are required", true); return; }

    const missingStatus = Array.from(cart.values()).some(entry =>
        entry.isGroup ? !entry.asset_status : (entry.item.tracking_type === "asset_serialized" && !entry.asset_status)
    );
    if (missingStatus) { showMessage("return-msg", "Pick a status for every asset in the cart", true); return; }

    const missingQty = Array.from(cart.values()).some(
        entry => !entry.isGroup && entry.item.tracking_type === "inventory_quantity" && !(entry.qty > 0)
    );
    if (missingQty) { showMessage("return-msg", "Enter a quantity greater than 0 for every item in the cart", true); return; }

    const lines = [];
    for (const entry of cart.values()) {
        if (entry.isGroup) {
            entry.itemIds.forEach(id => lines.push({ item_id: id, asset_status: entry.asset_status, qty_or_length: null }));
        } else {
            lines.push({
                item_id: entry.item.id,
                asset_status: entry.item.tracking_type === "asset_serialized" ? entry.asset_status : null,
                qty_or_length: entry.item.tracking_type === "inventory_quantity" ? entry.qty : null,
            });
        }
    }

    const body = { lines, notes };

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
        // Assignable users are needed for Quick Return's "Person: <name>"
        // source labels (sourceLabelOf) — Issue Materials already loads this
        // same cache for its own Issued To dropdown.
        await Promise.all([loadAllInventoryItems(), loadInventoryAssignableUsers()]);
        renderCart();
        renderResults(searchItems(document.getElementById("return-search").value));
    });
}
