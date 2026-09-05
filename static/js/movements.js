import { can, authHeaders, formatDate, capitalize, deleteIconSvg, showView, navigate, registerRoute, refreshBadges } from "./common.js";
import { refreshData as refreshDashboardData } from "./dashboard.js";

let movementsCache = [];
let showMovementHistory = false;

export const MOVEMENT_STATUS_META = {
    pending: { label: "Pending", cls: "pending" },
    in_transit: { label: "In Transit", cls: "in-transit" },
    arrived: { label: "Arrived", cls: "arrived" },
    completed: { label: "Completed", cls: "confirmed" },
    site_confirmed_online: { label: "Site Confirmed Online", cls: "confirmed" },
    site_still_down: { label: "Site Still Down", cls: "down" },
    cancelled: { label: "Cancelled", cls: "cancelled" },
};

const MOVEMENT_REASON_LABELS = {
    site_down: "Site down",
    storage: "Storage",
};

// Quiet refresh: no blanking, just swaps rows in place — used by the live
// sync poll and after an action, so a tick or a click doesn't flash
// "Loading movements..." over a table that's already showing data.
async function refreshMovements() {
    const res = await fetch(`/movements${showMovementHistory ? "?history=true" : ""}`, { headers: authHeaders() });
    if (!res.ok) {
        document.getElementById("movements-rows").innerHTML = '<tr><td colspan="6" class="loading-text">Failed to load movements</td></tr>';
        return;
    }
    movementsCache = await res.json();
    renderMovementsList(movementsCache);
}

// Initial/tab-switch load: shows the loading text once, then defers to the
// quiet refresh above.
async function loadMovements() {
    document.getElementById("movements-rows").innerHTML = '<tr><td colspan="6" class="loading-text">Loading movements...</td></tr>';
    await refreshMovements();
}

function renderMovementsList(movements) {
    const tbody = document.getElementById("movements-rows");

    if (movements.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="loading-text">No movements${showMovementHistory ? "" : " in progress"}.</td></tr>`;
        return;
    }

    const canMove = can("movements", "manage");

    tbody.innerHTML = movements.map(m => {
        const meta = MOVEMENT_STATUS_META[m.status] || { label: capitalize(m.status), cls: "" };
        return `
            <tr>
                <td class="battery-number">${m.battery_number}</td>
                <td>${m.from_location || "—"} &rarr; ${m.to_location}</td>
                <td>${MOVEMENT_REASON_LABELS[m.reason] || "—"}</td>
                <td><span class="status-pill movement-${meta.cls}">${meta.label}</span></td>
                <td>${formatDate(m.created_at)}</td>
                <td>${canMove ? renderMovementActions(m) : "—"}</td>
            </tr>
        `;
    }).join("");

    attachMovementActionListeners();
}

function renderMovementActions(m) {
    switch (m.status) {
        case "pending":
            return `
                <div class="actions-cell">
                    <button type="button" class="movement-action-btn" data-action="mark-in-transit" data-id="${m.id}">Mark In Transit</button>
                    <button type="button" class="movement-cancel-btn" data-id="${m.id}" title="Cancel movement">${deleteIconSvg()}</button>
                </div>
            `;
        case "in_transit":
            return `
                <div class="actions-cell">
                    <button type="button" class="movement-action-btn" data-action="mark-arrived" data-id="${m.id}">Mark Arrived</button>
                    <button type="button" class="movement-cancel-btn" data-id="${m.id}" title="Cancel movement">${deleteIconSvg()}</button>
                </div>
            `;
        case "arrived":
            return `
                <div class="actions-cell">
                    <button type="button" class="movement-site-check-btn" data-answer="true" data-id="${m.id}">Site Online</button>
                    <button type="button" class="movement-site-check-btn" data-answer="false" data-id="${m.id}">Still Down</button>
                </div>
            `;
        default:
            return "—";
    }
}

function attachMovementActionListeners() {
    document.querySelectorAll(".movement-action-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
            const res = await fetch(`/movements/${btn.dataset.id}/${btn.dataset.action}`, {
                method: "POST",
                headers: authHeaders()
            });
            if (res.ok) {
                await refreshMovements();
                await refreshBadges();
            } else {
                alert("Failed to update movement");
            }
        });
    });

    document.querySelectorAll(".movement-cancel-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
            if (!confirm("Cancel this movement?")) return;
            const res = await fetch(`/movements/${btn.dataset.id}/cancel`, {
                method: "POST",
                headers: authHeaders()
            });
            if (res.ok) {
                await refreshMovements();
                await refreshBadges();
                // A cancelled movement drops out of get_last_movement()'s
                // consideration, which can change what the battery table
                // shows for this battery (location/status/moved-by/since
                // falls back to the prior movement) — without this it stays
                // stale until the page reloads.
                await refreshDashboardData();
            } else {
                alert("Failed to cancel movement");
            }
        });
    });

    document.querySelectorAll(".movement-site-check-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
            const is_online = btn.dataset.answer === "true";
            const res = await fetch(`/movements/${btn.dataset.id}/confirm-online`, {
                method: "POST",
                headers: authHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify({ is_online })
            });
            if (res.ok) {
                await refreshMovements();
                await refreshBadges();
            } else {
                alert("Failed to record site check");
            }
        });
    });
}

// Same reasoning as dashboard.js's live sync: another user's action here
// (mark in transit, site-check answer, ...) is exactly what drives the
// battery table's status/charge/location/moved-by/since — so this list
// needs to stay live too, not just the table it feeds. Matches dashboard.js's
// interval so a change made here is reflected there in under 2s either way.
const LIVE_SYNC_INTERVAL_MS = 1500;

function startLiveSync() {
    setInterval(() => {
        const view = document.getElementById("view-movements");
        if (!view || view.hidden) return;
        if (document.visibilityState !== "visible") return;
        refreshMovements();
    }, LIVE_SYNC_INTERVAL_MS);
}

export function initMovements() {
    // Lives on the dashboard view's header as a quick link, wired here since
    // the action itself (load + show movements) is this view's concern.
    document.getElementById("movements-link-btn").addEventListener("click", () => {
        navigate("movements");
    });

    document.getElementById("movements-show-history").addEventListener("change", (e) => {
        showMovementHistory = e.target.checked;
        loadMovements();
    });

    registerRoute("movements", () => {
        showView("view-movements");
        loadMovements();
    });

    startLiveSync();
}
