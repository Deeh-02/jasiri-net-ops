import {
    can, authHeaders, showMessage, formatDate, showView, registerRoute,
} from "./common.js";

function renderSkuSummaryTable(rows) {
    const tbody = document.getElementById("sku-summary-rows");
    if (!tbody) return;
    const canEdit = can("inventory_items", "edit");

    tbody.innerHTML = rows.map(row => `
        <tr class="${row.below_threshold ? "sku-row-below-threshold" : ""}">
            <td>${row.category_name}</td>
            <td>${row.sku_or_spec}</td>
            <td>${row.total_on_hand}</td>
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

    registerRoute("inventory-reports", async () => {
        showView("view-inventory-reports");
        await Promise.all([refreshSkuSummary(), refreshOffcutSummary()]);
    });
}
