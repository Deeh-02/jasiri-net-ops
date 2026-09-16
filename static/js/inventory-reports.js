import {
    authHeaders, formatDate, showView, registerRoute,
} from "./common.js";
import { openUnitDetailModal, loadInventoryCategories, getInventoryCategories, exportRowsToCsv } from "./inventory-common.js";

let reportsCategoryFilter = "";
let skuSummaryCache = [];
let cableSummaryCache = [];
let offcutSummaryCache = [];

function money(n) {
    return n == null ? "—" : Number(n).toFixed(2);
}

// Reorder Level is read-only here now — it's edited from the Items page
// (see inventory.js's reorder-level edit-on-click cell) since it's config
// data, not something that belongs in a report. This just displays the
// currently-set value and a Low badge when Total On Hand falls under it.
function renderSkuSummaryTable(rows) {
    const tbody = document.getElementById("sku-summary-rows");
    if (!tbody) return;

    tbody.innerHTML = rows.map(row => `
        <tr>
            <td>${row.name || "—"}</td>
            <td>${row.sku_or_spec}</td>
            <td>${row.total_on_hand}</td>
            <td>${money(row.avg_unit_cost)}</td>
            <td>${money(row.total_value)}</td>
            <td>${row.reorder_level ?? "—"}${row.below_threshold ? ` <span class="status-pill low-stock">Low</span>` : ""}</td>
        </tr>
    `).join("");

    // Respects whatever Category filter is currently applied — rows are
    // already scoped server-side by reportsCategoryFilter before this renders.
    const total = rows.reduce((sum, row) => sum + (row.total_value || 0), 0);
    const totalEl = document.getElementById("sku-summary-total");
    if (totalEl) totalEl.textContent = `Total Value: ${money(total)}`;
}

async function refreshSkuSummary() {
    const qs = reportsCategoryFilter ? `?category_id=${reportsCategoryFilter}` : "";
    const res = await fetch(`/inventory/sku-summary${qs}`, { headers: authHeaders() });
    const rows = res.ok ? await res.json() : [];
    skuSummaryCache = rows;
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
    const qs = reportsCategoryFilter ? `?category_id=${reportsCategoryFilter}` : "";
    const res = await fetch(`/inventory/cable-summary${qs}`, { headers: authHeaders() });
    const rows = res.ok ? await res.json() : [];
    cableSummaryCache = rows;
    renderCableSummaryTable(rows);
}

async function openCableDrilldown(spec) {
    document.getElementById("cable-drilldown-label").textContent = `— ${spec}`;
    const res = await fetch(`/inventory/cable-summary/drill-down?spec=${encodeURIComponent(spec)}`, { headers: authHeaders() });
    const rows = res.ok ? await res.json() : [];

    document.getElementById("cable-drilldown-rows").innerHTML = rows.map(r => `
        <tr>
            <td>${r.cut_reel_id}</td>
            <td>${r.length_status || "—"}</td>
            <td>${r.location_name || "—"}</td>
            <td>${r.length_remaining}m</td>
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
    const qs = reportsCategoryFilter ? `?category_id=${reportsCategoryFilter}` : "";
    const res = await fetch(`/inventory/offcuts${qs}`, { headers: authHeaders() });
    const rows = res.ok ? await res.json() : [];
    offcutSummaryCache = rows;
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

function populateReportsCategoryFilter() {
    const select = document.getElementById("inventory-reports-category-filter");
    if (!select) return;
    const previous = select.value;
    const categories = getInventoryCategories();
    select.innerHTML = `<option value="">All Categories</option>` +
        categories.map(c => `<option value="${c.id}">${c.name}</option>`).join("");
    select.value = categories.some(c => String(c.id) === previous) ? previous : "";
}

async function refreshAllReportTabs() {
    await Promise.all([refreshSkuSummary(), refreshCableSummary(), refreshOffcutSummary()]);
}

// One tab panel visible at a time — same `.inventory-item-tab` class the
// item-detail modal's Details/Logs tabs already use (domain-generic, not
// modal-specific — see inventory.css's comment on that class). Scoped by id
// rather than the bare class, since every view fragment (including the item
// detail modal's own .inventory-item-tab-row) is injected into the DOM at
// boot regardless of which view is active — a class-only query would find
// whichever one happens to come first in the document.
function initReportsTabs() {
    const tabRow = document.getElementById("reports-tab-row");
    if (!tabRow) return;
    tabRow.querySelectorAll(".inventory-item-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            tabRow.querySelectorAll(".inventory-item-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            ["sku", "cable", "offcut"].forEach(name => {
                document.getElementById(`reports-tab-${name}`).hidden = name !== tab.dataset.tab;
            });
        });
    });
}

// Exports whichever tab is currently active, off that tab's own
// already-filtered cache (all three respect reportsCategoryFilter) — one
// button rather than three, since exactly one tab panel is ever visible.
function exportActiveReportTabCsv() {
    const activeTab = document.querySelector("#reports-tab-row .inventory-item-tab.active");
    const tab = activeTab ? activeTab.dataset.tab : "sku";

    if (tab === "cable") {
        exportRowsToCsv("cable-type-summary.csv", [
            { header: "Spec", value: row => row.spec },
            { header: "Reels In Stock", value: row => row.reels_in_stock },
            { header: "Total Remaining (m)", value: row => row.total_length_remaining },
        ], cableSummaryCache);
    } else if (tab === "offcut") {
        exportRowsToCsv("offcut-rollup.csv", [
            { header: "Spec", value: row => row.spec },
            { header: "Total Length In Stock (m)", value: row => row.total_length },
            { header: "Cuts", value: row => row.cut_count },
        ], offcutSummaryCache);
    } else {
        exportRowsToCsv("sku-summary.csv", [
            { header: "Name", value: row => row.name || "—" },
            { header: "SKU/Spec", value: row => row.sku_or_spec },
            { header: "Total On Hand", value: row => row.total_on_hand },
            { header: "Avg Unit Cost", value: row => money(row.avg_unit_cost) },
            { header: "Total Value", value: row => money(row.total_value) },
            { header: "Reorder Level", value: row => row.reorder_level ?? "—" },
        ], skuSummaryCache);
    }
}

export function initInventoryReports() {
    const overlay = document.getElementById("offcut-drilldown-overlay");
    document.getElementById("offcut-drilldown-close").addEventListener("click", () => { overlay.hidden = true; });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });

    const cableOverlay = document.getElementById("cable-drilldown-overlay");
    document.getElementById("cable-drilldown-close").addEventListener("click", () => { cableOverlay.hidden = true; });
    cableOverlay.addEventListener("click", (e) => { if (e.target === cableOverlay) cableOverlay.hidden = true; });

    initReportsTabs();

    document.getElementById("inventory-reports-category-filter").addEventListener("change", async (e) => {
        reportsCategoryFilter = e.target.value;
        await refreshAllReportTabs();
    });

    document.getElementById("export-reports-csv-btn").addEventListener("click", exportActiveReportTabCsv);

    registerRoute("inventory-reports", async () => {
        showView("view-inventory-reports");
        // Re-fetched on every visit so a category added elsewhere shows up
        // in the filter immediately, same convention as the other inventory views.
        await loadInventoryCategories();
        populateReportsCategoryFilter();
        await refreshAllReportTabs();
    });
}
