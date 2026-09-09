import {
    can, authHeaders, showMessage, formatDate, showView, registerRoute,
} from "./common.js";
import { openUnitDetailModal } from "./inventory-common.js";

function renderSkuSummaryTable(rows) {
    const tbody = document.getElementById("sku-summary-rows");
    if (!tbody) return;
    const canEdit = can("inventory_items", "edit");

    tbody.innerHTML = rows.map(row => `
        <tr class="${row.below_threshold ? "sku-row-below-threshold" : ""}">
            <td>${row.category_name}</td>
            <td>${row.sku_or_spec}</td>
            <td>${row.total_on_hand}</td>
            <td>${row.avg_unit_cost != null ? row.avg_unit_cost.toFixed(2) : "—"}</td>
            <td>${canEdit
                ? `<input type="number" step="any" min="0" class="sku-reorder-input" value="${row.reorder_level ?? ""}" data-category-id="${row.category_id}" data-sku-or-spec="${encodeURIComponent(row.sku_or_spec)}">`
                : (row.reorder_level ?? "—")}</td>
            <td>${canEdit
                ? `<button type="button" class="btn-secondary sku-reorder-save-btn" data-category-id="${row.category_id}" data-sku-or-spec="${encodeURIComponent(row.sku_or_spec)}">Save</button>`
                : ""}</td>
        </tr>
    `).join("");

    tbody.querySelectorAll(".sku-reorder-save-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
            const categoryId = Number(btn.dataset.categoryId);
            const skuOrSpec = decodeURIComponent(btn.dataset.skuOrSpec);
            const input = tbody.querySelector(
                `.sku-reorder-input[data-category-id="${btn.dataset.categoryId}"][data-sku-or-spec="${btn.dataset.skuOrSpec}"]`
            );
            const reorderLevel = Number(input.value);
            if (input.value === "" || Number.isNaN(reorderLevel) || reorderLevel < 0) {
                showMessage("sku-summary-msg", "Enter a valid reorder level", true);
                return;
            }

            const response = await fetch("/inventory/reorder-level", {
                method: "PATCH",
                headers: authHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify({ category_id: categoryId, sku_or_spec: skuOrSpec, reorder_level: reorderLevel }),
            });

            if (response.ok) {
                showMessage("sku-summary-msg", "Reorder level saved", false);
                await refreshSkuSummary();
            } else {
                const err = await response.json().catch(() => ({}));
                showMessage("sku-summary-msg", err.detail || "Failed to save reorder level", true);
            }
        });
    });
}

async function refreshSkuSummary() {
    const res = await fetch("/inventory/sku-summary", { headers: authHeaders() });
    const rows = res.ok ? await res.json() : [];
    renderSkuSummaryTable(rows);
}

function renderCableSummaryTable(rows) {
    const tbody = document.getElementById("cable-summary-rows");
    if (!tbody) return;

    tbody.innerHTML = rows.map(row => `
        <tr>
            <td>${row.spec}</td>
            <td>${row.reels_in_stock}</td>
            <td>${row.total_length_remaining}m</td>
            <td><button type="button" class="btn-secondary cable-drilldown-btn" data-spec="${encodeURIComponent(row.spec)}">View</button></td>
        </tr>
    `).join("");

    tbody.querySelectorAll(".cable-drilldown-btn").forEach(btn => {
        btn.addEventListener("click", () => openCableDrilldown(decodeURIComponent(btn.dataset.spec)));
    });
}

async function refreshCableSummary() {
    const res = await fetch("/inventory/cable-summary", { headers: authHeaders() });
    const rows = res.ok ? await res.json() : [];
    renderCableSummaryTable(rows);
}

async function openCableDrilldown(spec) {
    document.getElementById("cable-drilldown-label").textContent = `— ${spec}`;
    const res = await fetch(`/inventory/cable-summary/drill-down?spec=${encodeURIComponent(spec)}`, { headers: authHeaders() });
    const rows = res.ok ? await res.json() : [];

    document.getElementById("cable-drilldown-rows").innerHTML = rows.map(r => `
        <tr>
            <td>${r.cut_reel_id}</td>
            <td>${r.length_remaining}m</td>
            <td>${r.location_name || "—"}</td>
            <td>${r.unit_cost ?? "—"}</td>
            <td>${formatDate(r.created_at)}</td>
            <td><button type="button" class="btn-secondary cable-drilldown-view-item-btn" data-item-id="${r.item_id}">View</button></td>
        </tr>
    `).join("");

    document.getElementById("cable-drilldown-rows").querySelectorAll(".cable-drilldown-view-item-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            // Close this overlay first — both share the same modal z-index,
            // so stacking two at once leaves paint order ambiguous.
            document.getElementById("cable-drilldown-overlay").hidden = true;
            openUnitDetailModal(btn.dataset.itemId);
        });
    });

    document.getElementById("cable-drilldown-overlay").hidden = false;
}

function renderOffcutSummaryTable(rows) {
    const tbody = document.getElementById("offcut-summary-rows");
    if (!tbody) return;

    tbody.innerHTML = rows.map(row => `
        <tr>
            <td>${row.spec}</td>
            <td>${row.total_length}m</td>
            <td>${row.cut_count}</td>
            <td><button type="button" class="btn-secondary offcut-drilldown-btn" data-spec="${encodeURIComponent(row.spec)}">View</button></td>
        </tr>
    `).join("");

    tbody.querySelectorAll(".offcut-drilldown-btn").forEach(btn => {
        btn.addEventListener("click", () => openOffcutDrilldown(decodeURIComponent(btn.dataset.spec)));
    });
}

async function refreshOffcutSummary() {
    const res = await fetch("/inventory/offcuts", { headers: authHeaders() });
    const rows = res.ok ? await res.json() : [];
    renderOffcutSummaryTable(rows);
}

async function openOffcutDrilldown(spec) {
    document.getElementById("offcut-drilldown-label").textContent = `— ${spec}`;
    const res = await fetch(`/inventory/offcuts/drill-down?spec=${encodeURIComponent(spec)}`, { headers: authHeaders() });
    const rows = res.ok ? await res.json() : [];

    document.getElementById("offcut-drilldown-rows").innerHTML = rows.map(r => `
        <tr>
            <td>${r.cut_reel_id}</td>
            <td>${r.length_remaining}m</td>
            <td>${r.length_status}</td>
            <td>${r.location_name || "—"}</td>
            <td>${formatDate(r.created_at)}</td>
        </tr>
    `).join("");

    document.getElementById("offcut-drilldown-overlay").hidden = false;
}

export function initInventoryReports() {
    const overlay = document.getElementById("offcut-drilldown-overlay");
    document.getElementById("offcut-drilldown-close").addEventListener("click", () => { overlay.hidden = true; });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });

    const cableOverlay = document.getElementById("cable-drilldown-overlay");
    document.getElementById("cable-drilldown-close").addEventListener("click", () => { cableOverlay.hidden = true; });
    cableOverlay.addEventListener("click", (e) => { if (e.target === cableOverlay) cableOverlay.hidden = true; });

    registerRoute("inventory-reports", async () => {
        showView("view-inventory-reports");
        await Promise.all([refreshSkuSummary(), refreshCableSummary(), refreshOffcutSummary()]);
    });
}
