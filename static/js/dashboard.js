import {
    can, authHeaders, showMessage, formatDate, capitalize,
    batteryIconSvg, moveIconSvg, editIconSvg, viewIconSvg, deleteIconSvg,
    registerAppShownHandler, registerCmdkProvider, refreshBadges,
} from "./common.js";
import { MOVEMENT_STATUS_META } from "./movements.js";

const CHARGE_OPTIONS = ["charged", "charging", "low", "unknown"];
let batteriesCache = [];
let locationsCache = []; // scoped to this view — just the move-modal's destination list
let movedByUsersCache = []; // active users, for the "Moved by" typeahead — free text stays allowed either way
let moveModalBatteryId = null;

// ---- Stats: 5 boxes, no Total, distinct colors ----
// Same predicate used for both the card's count and its click-through
// filter (buildStats/openStatDetail), so the two never drift apart.
const STAT_FILTERS = {
    deployed: b => b.status === "Deployed",
    charged: b => b.charge_status === "charged",
    charging: b => b.charge_status === "charging",
    low: b => b.charge_status === "low",
    unknown: b => b.charge_status === "unknown",
};

function buildStats(batteries) {
    return [
        { label: "Deployed", cls: "deployed" },
        { label: "Charged", cls: "charged" },
        { label: "Charging", cls: "charging" },
        { label: "Low", cls: "low" },
        { label: "Unknown", cls: "unknown" },
    ].map(s => ({ ...s, value: batteries.filter(STAT_FILTERS[s.cls]).length }));
}

// ---- Initial load: shows loading text once ----
async function loadDashboard() {
    document.getElementById("stat-grid").innerHTML = '<div class="loading-text">Loading...</div>';
    document.getElementById("battery-rows").innerHTML = '<tr><td colspan="9" class="loading-text">Loading batteries...</td></tr>';
    await refreshData();
}

// ---- Quiet refresh: no blanking, just swap content in place ----
export async function refreshData() {
    const [batteriesRes, locationsRes] = await Promise.all([
        fetch("/batteries", { headers: authHeaders() }),
        fetch("/locations", { headers: authHeaders() })
    ]);
    batteriesCache = batteriesRes.ok ? await batteriesRes.json() : [];
    locationsCache = locationsRes.ok ? await locationsRes.json() : [];

    // Gated on the permission itself (not just a .ok check) so a role
    // without users:view never even sends the request — the typeahead
    // just has no suggestions for them, free text still works fine.
    if (can("users", "view")) {
        const usersRes = await fetch("/users", { headers: authHeaders() });
        movedByUsersCache = usersRes.ok ? (await usersRes.json()).filter(u => u.status !== "inactive") : [];
    } else {
        movedByUsersCache = [];
    }

    renderStats(buildStats(batteriesCache));
    renderTable(batteriesCache);
    populateMoveLocationSelect(locationsCache);
}

function renderStats(stats) {
    const grid = document.getElementById("stat-grid");
    grid.innerHTML = stats.map(s => `
        <div class="stat-card ${s.cls}" data-filter="${s.cls}" role="button" tabindex="0">
            <div class="stat-label">${s.label}</div>
            <div class="stat-value">${s.value}</div>
        </div>
    `).join("");

    grid.querySelectorAll(".stat-card").forEach(card => {
        card.addEventListener("click", () => openStatDetail(card.dataset.filter));
        card.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                openStatDetail(card.dataset.filter);
            }
        });
    });
}

// Overrides just for this modal's Status column — the Movements page itself
// keeps showing "Site Confirmed Online" unchanged (movements.js's own
// rendering doesn't go through this function).
const STAT_DETAIL_STATUS_LABELS = {
    site_confirmed_online: "On Site",
    completed: "On Site",
};

function movementStatusLabel(battery) {
    if (!battery.movement_status) return "—";
    if (STAT_DETAIL_STATUS_LABELS[battery.movement_status]) {
        return STAT_DETAIL_STATUS_LABELS[battery.movement_status];
    }
    return (MOVEMENT_STATUS_META[battery.movement_status] || { label: capitalize(battery.movement_status) }).label;
}

function openStatDetail(filterKey) {
    const stat = buildStats(batteriesCache).find(s => s.cls === filterKey);
    const matches = batteriesCache.filter(STAT_FILTERS[filterKey]);

    document.getElementById("stat-detail-title").textContent = `${stat.label} (${matches.length})`;

    const list = document.getElementById("stat-detail-list");
    if (matches.length === 0) {
        list.innerHTML = `<div class="dashboard-log-empty">No batteries in this state.</div>`;
    } else {
        list.innerHTML = `
            <table class="dashboard-logs-table">
                <colgroup>
                    <col style="width:14%">
                    <col style="width:24%">
                    <col style="width:16%">
                    <col style="width:46%">
                </colgroup>
                <tbody>
                    ${matches.map(b => `
                        <tr>
                            <td>${b.battery_number}</td>
                            <td>${b.current_location}</td>
                            <td>${movementStatusLabel(b)}</td>
                            <td>${formatDate(b.moved_at)}</td>
                        </tr>
                    `).join("")}
                </tbody>
            </table>
        `;
    }

    document.getElementById("stat-detail-overlay").hidden = false;
}

function renderTable(batteries) {
    const tbody = document.getElementById("battery-rows");
    tbody.innerHTML = "";

    batteries.forEach(battery => {
        const charge = (battery.charge_status || "unknown").toLowerCase();
        const statusClass = battery.status === "Deployed" ? "deployed" : "at-base";

        const menuItems = CHARGE_OPTIONS.map(c => `
            <div class="charge-option ${c === charge ? "active" : ""}" data-value="${c}">
                ${batteryIconSvg(c)}
                <span>${capitalize(c)}</span>
            </div>
        `).join("");

        const row = document.createElement("tr");
        row.innerHTML = `
            <td class="battery-number col-frozen">${battery.battery_number}</td>
            <td>${battery.model || "-"}</td>
            <td><span class="status-pill ${statusClass}">${battery.status}</span></td>
            <td>
                <div class="charge-dropdown" data-id="${battery.id}">
                    <button type="button" class="charge-btn">
                        ${batteryIconSvg(charge)}
                        <span class="charge-label">${capitalize(charge)}</span>
                        <span class="charge-caret">▾</span>
                    </button>
                    <div class="charge-menu" hidden>${menuItems}</div>
                </div>
            </td>
            <td>${battery.current_location}</td>
            <td>${battery.moved_by || "—"}</td>
            <td>${formatDate(battery.moved_at)}</td>
            <td>
    <div class="actions-cell">
        ${can("movements", "create") ? `
        <button type="button" class="move-btn" data-id="${battery.id}" data-number="${battery.battery_number}" title="Move battery">
            ${moveIconSvg()}
        </button>` : ""}
        ${can("batteries", "view") ? `
<button type="button" class="view-battery-btn" data-id="${battery.id}" title="View battery">
    ${viewIconSvg()}
</button>` : ""}
        ${can("batteries", "edit") ? `
        <button type="button" class="edit-battery-btn" data-id="${battery.id}" title="Edit battery">
            ${editIconSvg()}
        </button>` : ""}
        ${can("batteries", "delete") ? `
        <button type="button" class="delete-battery-btn" data-id="${battery.id}" data-number="${battery.battery_number}" title="Deactivate battery">
            ${deleteIconSvg()}
        </button>` : ""}
    </div>
</td>
        `;
        tbody.appendChild(row);
    });

    tbody.querySelectorAll(".charge-dropdown").forEach(dropdown => {
        const btn = dropdown.querySelector(".charge-btn");
        const menu = dropdown.querySelector(".charge-menu");

        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const isOpen = !menu.hidden;
            closeAllChargeMenus();
            menu.hidden = isOpen;
        });

        menu.querySelectorAll(".charge-option").forEach(opt => {
            opt.addEventListener("click", async (e) => {
                e.stopPropagation();
                const batteryId = dropdown.dataset.id;
                const value = opt.dataset.value;
                menu.hidden = true;

                await fetch(`/batteries/${batteryId}/charge-status`, {
                    method: "PATCH",
                    headers: authHeaders({ "Content-Type": "application/json" }),
                    body: JSON.stringify({ charge_status: value })
                });
                await refreshData();
            });
        });
    });

    tbody.querySelectorAll(".move-btn").forEach(btn => {
        btn.addEventListener("click", () => openMoveModal(btn.dataset.id, btn.dataset.number));
    });

    tbody.querySelectorAll(".delete-battery-btn").forEach(btn => {
        btn.addEventListener("click", () => deactivateBattery(btn.dataset.id, btn.dataset.number));
    });

    tbody.querySelectorAll(".view-battery-btn").forEach(btn => {
        btn.addEventListener("click", () => openViewBatteryModal(btn.dataset.id));
    });

    tbody.querySelectorAll(".edit-battery-btn").forEach(btn => {
        btn.addEventListener("click", () => openEditBatteryModal(btn.dataset.id));
    });
}

function closeAllChargeMenus() {
    document.querySelectorAll(".charge-menu").forEach(m => m.hidden = true);
}

async function deactivateBattery(id, number) {
    if (!confirm(`Deactivate battery "${number}"? It will be hidden from the list.`)) return;

    const response = await fetch(`/batteries/${id}`, { method: "DELETE", headers: authHeaders() });
    if (response.ok) {
        await refreshData();
    } else {
        alert("Failed to deactivate battery");
    }
}

function populateMoveLocationSelect(locations) {
    const sel = document.getElementById("move-location");
    sel.innerHTML = '<option value="">Move to...</option>' +
        locations.map(l => `<option value="${l.id}">${l.name}${l.is_home_base ? " (Home Base)" : ""}</option>`).join("");
}

// ---- Move modal ----
let moveOverlay, moveForm, moveBatteryLabel;

function openMoveModal(batteryId, batteryNumber) {
    moveModalBatteryId = batteryId;
    moveBatteryLabel.textContent = batteryNumber ? `— ${batteryNumber}` : "";
    moveForm.reset();
    document.getElementById("move-by-suggestions").hidden = true;
    document.getElementById("move-by-warning").hidden = true;
    moveOverlay.hidden = false;
}

function closeMoveModal() {
    moveOverlay.hidden = true;
    moveModalBatteryId = null;
}

// ---- "Moved by" typeahead: free text stays allowed (a contractor or
// anyone not in the system is a valid entry), the dropdown is just a
// convenience and the warning is just a heads-up — neither ever blocks
// what gets typed or submitted. ----
function renderMoveBySuggestions(query) {
    const box = document.getElementById("move-by-suggestions");
    const q = query.trim().toLowerCase();
    const matches = q ? movedByUsersCache.filter(u => u.name.toLowerCase().includes(q)) : [];

    if (matches.length === 0) {
        box.hidden = true;
        box.innerHTML = "";
        return;
    }

    box.innerHTML = matches.map(u => `<div class="move-by-option" data-name="${u.name}">${u.name}</div>`).join("");
    box.hidden = false;

    box.querySelectorAll(".move-by-option").forEach(opt => {
        // mousedown (not click) + preventDefault so the input never blurs
        // on the way to this handler — no blur-vs-click race to work around.
        opt.addEventListener("mousedown", (e) => {
            e.preventDefault();
            const input = document.getElementById("move-by");
            input.value = opt.dataset.name;
            box.hidden = true;
            updateMoveByWarning();
        });
    });
}

function updateMoveByWarning() {
    const value = document.getElementById("move-by").value.trim();
    const warning = document.getElementById("move-by-warning");
    if (!value) {
        warning.hidden = true;
        return;
    }
    const isKnownUser = movedByUsersCache.some(u => u.name.toLowerCase() === value.toLowerCase());
    warning.hidden = isKnownUser;
}

function initMoveByTypeahead() {
    const input = document.getElementById("move-by");
    input.addEventListener("input", (e) => {
        renderMoveBySuggestions(e.target.value);
        updateMoveByWarning();
    });
    input.addEventListener("focus", (e) => renderMoveBySuggestions(e.target.value));
    input.addEventListener("blur", () => {
        document.getElementById("move-by-suggestions").hidden = true;
    });
}

// ---- View Battery modal (details + paginated movement logs) ----
let viewMovementsCache = [];
let viewLogsPage = 1;
let viewLogsPageSize = 10;

async function openViewBatteryModal(id) {
    const overlay = document.getElementById("view-battery-overlay");
    const label = document.getElementById("view-battery-label");
    overlay.hidden = false;

    document.querySelectorAll(".dashboard-tab-slant").forEach(t => t.classList.remove("active"));
    document.querySelector('.dashboard-tab-slant[data-tab="details"]').classList.add("active");
    document.getElementById("view-tab-details").hidden = false;
    document.getElementById("view-tab-logs").hidden = true;

    const res = await fetch(`/batteries/${id}`, { headers: authHeaders() });
    const battery = await res.json();

    label.textContent = battery.battery_number;
    document.getElementById("detail-serial").textContent = battery.serial_number || "—";
    document.getElementById("detail-model").textContent = battery.model || "—";
    document.getElementById("detail-capacity").textContent = battery.capacity || "—";
    document.getElementById("detail-status").textContent = battery.status;
    document.getElementById("detail-charge").textContent = capitalize(battery.charge_status || "unknown");
    document.getElementById("detail-location").textContent = battery.current_location;

    const logsRes = await fetch(`/batteries/${id}/movements`, { headers: authHeaders() });
    viewMovementsCache = await logsRes.json();
    viewLogsPage = 1;
    renderLogsTable();
}

function renderLogsTable() {
    const logsList = document.getElementById("view-logs-list");
    const pagination = document.getElementById("logs-pagination");
    const total = viewMovementsCache.length;

    if (total === 0) {
        logsList.innerHTML = `<div class="dashboard-log-empty">No movement history yet.</div>`;
        pagination.innerHTML = "";
        return;
    }

    const totalPages = Math.max(1, Math.ceil(total / viewLogsPageSize));
    if (viewLogsPage > totalPages) viewLogsPage = totalPages;
    const start = (viewLogsPage - 1) * viewLogsPageSize;
    const pageItems = viewMovementsCache.slice(start, start + viewLogsPageSize);

    logsList.innerHTML = `
        <table class="dashboard-logs-table">
            <colgroup>
                <col style="width:30%">
                <col style="width:23%">
                <col style="width:23%">
                <col style="width:24%">
            </colgroup>
            <tbody>
                ${pageItems.map(m => `
                    <tr>
                        <td>${formatDate(m.created_at)}</td>
                        <td>${m.from_location || "—"}</td>
                        <td>${m.to_location}</td>
                        <td>${m.moved_by || "—"}</td>
                    </tr>
                `).join("")}
            </tbody>
        </table>
    `;

    pagination.innerHTML = `
        <div class="dashboard-logs-pagination-row">
            <div class="dashboard-logs-page-size">
                <span>Show</span>
                <select id="logs-page-size-select">
                    ${[10, 20, 50, 100].map(n => `<option value="${n}" ${n === viewLogsPageSize ? "selected" : ""}>${n}</option>`).join("")}
                </select>
                <span>entries</span>
            </div>
            <div class="dashboard-logs-page-nav">
                <button type="button" id="logs-prev-btn" ${viewLogsPage === 1 ? "disabled" : ""}>Prev</button>
                <span>Page ${viewLogsPage} of ${totalPages}</span>
                <button type="button" id="logs-next-btn" ${viewLogsPage === totalPages ? "disabled" : ""}>Next</button>
            </div>
        </div>
    `;

    document.getElementById("logs-page-size-select").addEventListener("change", (e) => {
        viewLogsPageSize = parseInt(e.target.value);
        viewLogsPage = 1;
        renderLogsTable();
    });
    document.getElementById("logs-prev-btn").addEventListener("click", () => {
        if (viewLogsPage > 1) { viewLogsPage--; renderLogsTable(); }
    });
    document.getElementById("logs-next-btn").addEventListener("click", () => {
        if (viewLogsPage < totalPages) { viewLogsPage++; renderLogsTable(); }
    });
}

// ---- Edit Battery modal ----
let editBatteryOverlay, editBatteryCancelBtn;
let editBatteryId = null;

async function openEditBatteryModal(id) {
    const res = await fetch(`/batteries/${id}`, { headers: authHeaders() });
    if (!res.ok) {
        alert("Failed to load battery");
        return;
    }
    const battery = await res.json();

    editBatteryId = battery.id;
    document.getElementById("edit-battery-number").value = battery.battery_number || "";
    document.getElementById("edit-battery-serial").value = battery.serial_number || "";
    document.getElementById("edit-battery-model").value = battery.model || "";
    document.getElementById("edit-battery-capacity").value = battery.capacity || "";
    editBatteryOverlay.hidden = false;
}

function closeEditBatteryModal() {
    editBatteryOverlay.hidden = true;
    editBatteryId = null;
}

// ---- Add Battery modal ----
let addBatteryOverlay, addBatteryOpenBtn, addBatteryCancelBtn;

export function initDashboard() {
    moveOverlay = document.getElementById("move-overlay");
    moveForm = document.getElementById("move-form");
    moveBatteryLabel = document.getElementById("move-battery-label");
    initMoveByTypeahead();
    editBatteryOverlay = document.getElementById("edit-battery-overlay");
    editBatteryCancelBtn = document.getElementById("edit-battery-cancel");
    addBatteryOverlay = document.getElementById("add-battery-overlay");
    addBatteryOpenBtn = document.getElementById("add-battery-open-btn");
    addBatteryCancelBtn = document.getElementById("add-battery-cancel");

    document.getElementById("move-cancel").addEventListener("click", closeMoveModal);
    moveOverlay.addEventListener("click", (e) => {
        if (e.target === moveOverlay) closeMoveModal();
    });

    moveForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!moveModalBatteryId) return;

        const to_location_id = parseInt(document.getElementById("move-location").value);
        const reason = document.getElementById("move-reason").value || null;
        const moved_by = document.getElementById("move-by").value || null;

        const response = await fetch("/movements", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ battery_id: parseInt(moveModalBatteryId), to_location_id, reason, moved_by })
        });

        if (response.ok) {
            closeMoveModal();
            await refreshData();
            await refreshBadges();
        } else {
            alert("Failed to record move");
        }
    });

    document.querySelectorAll(".dashboard-tab-slant").forEach(tab => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".dashboard-tab-slant").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            const target = tab.dataset.tab;
            document.getElementById("view-tab-details").hidden = target !== "details";
            document.getElementById("view-tab-logs").hidden = target !== "logs";
        });
    });

    document.getElementById("view-battery-close").addEventListener("click", () => {
        document.getElementById("view-battery-overlay").hidden = true;
    });

    document.getElementById("view-battery-overlay").addEventListener("click", (e) => {
        if (e.target.id === "view-battery-overlay") {
            document.getElementById("view-battery-overlay").hidden = true;
        }
    });

    document.getElementById("stat-detail-close").addEventListener("click", () => {
        document.getElementById("stat-detail-overlay").hidden = true;
    });

    document.getElementById("stat-detail-overlay").addEventListener("click", (e) => {
        if (e.target.id === "stat-detail-overlay") {
            document.getElementById("stat-detail-overlay").hidden = true;
        }
    });

    editBatteryCancelBtn.addEventListener("click", closeEditBatteryModal);
    editBatteryOverlay.addEventListener("click", (e) => {
        if (e.target === editBatteryOverlay) closeEditBatteryModal();
    });

    document.getElementById("edit-battery-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!editBatteryId) return;

        const body = {
            battery_number: document.getElementById("edit-battery-number").value,
            serial_number: document.getElementById("edit-battery-serial").value || null,
            model: document.getElementById("edit-battery-model").value || null,
            capacity: document.getElementById("edit-battery-capacity").value || null
        };

        const response = await fetch(`/batteries/${editBatteryId}`, {
            method: "PATCH",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body)
        });

        if (response.ok) {
            closeEditBatteryModal();
            await refreshData();
        } else {
            const err = await response.json().catch(() => ({}));
            showMessage("edit-battery-msg", err.detail || "Failed to update battery", true);
        }
    });

    addBatteryOpenBtn.addEventListener("click", () => {
        document.getElementById("battery-form").reset();
        addBatteryOverlay.hidden = false;
    });

    addBatteryCancelBtn.addEventListener("click", () => {
        addBatteryOverlay.hidden = true;
    });

    addBatteryOverlay.addEventListener("click", (e) => {
        if (e.target === addBatteryOverlay) addBatteryOverlay.hidden = true;
    });

    document.getElementById("battery-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const battery_number = document.getElementById("battery-number").value;
        const serial_number = document.getElementById("battery-serial").value || null;
        const model = document.getElementById("battery-model").value || null;
        const capacity = document.getElementById("battery-capacity").value || null;

        const response = await fetch("/batteries", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ battery_number, serial_number, model, capacity })
        });

        if (response.ok) {
            showMessage("battery-msg", "Battery added", false);
            e.target.reset();
            addBatteryOverlay.hidden = true;
            await refreshData();
        } else {
            showMessage("battery-msg", "Failed to add battery (number may already exist)", true);
        }
    });

    document.addEventListener("click", closeAllChargeMenus);

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            closeMoveModal();
            closeAllChargeMenus();
        }
    });

    registerAppShownHandler(loadDashboard);

    registerCmdkProvider({
        getItems: () => {
            const actions = [];
            if (can("batteries", "add")) {
                actions.push({
                    type: "action",
                    label: "Add Battery",
                    action: () => {
                        document.querySelector('[data-view="dashboard"]').click();
                        addBatteryOpenBtn.click();
                    }
                });
            }
            if (can("movements", "create")) {
                // No standalone "new movement" modal — a move is always
                // started from a specific battery's row, so this lands on
                // the Battery Tracker table rather than a blank form.
                actions.push({
                    type: "action",
                    label: "Add Movement",
                    sublabel: "Move a battery from the Battery Tracker table",
                    action: () => document.querySelector('[data-view="dashboard"]').click(),
                });
            }
            const batteries = can("batteries", "view") ? batteriesCache.map(b => ({
                type: "battery",
                label: b.battery_number,
                sublabel: [b.model, b.current_location].filter(Boolean).join(" — "),
                action: () => {
                    document.querySelector('[data-view="dashboard"]').click();
                    openViewBatteryModal(b.id);
                }
            })) : [];
            return [...actions, ...batteries];
        },
    });
}
