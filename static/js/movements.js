import { can, authHeaders, formatDate, capitalize, deleteIconSvg, showView, navigate, registerRoute, refreshBadges, registerAppShownHandler, registerLogoutHandler } from "./common.js";
import { refreshData as refreshDashboardData } from "./dashboard.js";

// null (not []) whenever nothing currently on screen can be trusted as
// "the cache" — before the first fetch ever completes, and again every
// time loadMovements() blanks the tbody to the loading placeholder (see
// there). That keeps the invariant "cache is non-null only while it
// accurately describes what's rendered" true by construction, so the
// dedup in refreshMovements() below can never compare a fresh fetch
// against stale data left over from a previous visit and wrongly decide
// nothing changed.
let movementsCache = null;
let showMovementHistory = false;

// Phase 4.9 — the confirm-online prefill. Keyed by location_id (what
// GET /monitoring/status returns per site) so a movement row's own
// to_location_id can look itself up with no join, no new backend read.
// null (not {}) before the first fetch completes or when the viewer lacks
// sites:view_status — renderMovementActions treats null as "no prefill
// data available" and falls back to the plain two-button UI unchanged,
// exactly like today for anyone without that permission.
let siteLivenessByLocation = null;

async function refreshSiteLiveness() {
    if (!can("sites", "view_status")) {
        siteLivenessByLocation = null;
        return;
    }
    try {
        const res = await fetch("/monitoring/status", { headers: authHeaders() });
        if (!res.ok) { siteLivenessByLocation = null; return; }
        const data = await res.json();
        const map = {};
        for (const s of data.sites || []) {
            if (s.location_id != null) map[s.location_id] = s;
        }
        siteLivenessByLocation = map;
    } catch {
        // A prefill hint is a convenience, not a requirement — any failure
        // here just means the plain two-button UI, never a broken page.
        siteLivenessByLocation = null;
    }
}

// Two separate staleness checks. They used to be one counter bumped by
// every call, which meant each 1.5s poll tick invalidated whatever request
// was still in flight. On a slow server (any response over 1.5s) nothing
// ever got to render, and the table sat on "Loading movements..." for good.
//
// movementsGeneration: bumped only by loadMovements() (navigation, history
// toggle). A response from an older generation answered a different
// question (e.g. history off vs on) and is dropped.
// movementsSeq / movementsAppliedSeq: within one generation, a response is
// dropped only if a *newer* one has already been rendered. A slower older
// response can never overwrite a newer one, but a newer request no longer
// cancels an older one that hasn't been answered yet.
let movementsGeneration = 0;
let movementsSeq = 0;
let movementsAppliedSeq = 0;
let movementsInFlight = 0;
let livenessInFlight = false;

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
// "Loading movements..." over a table that's already showing data. Also
// the one function every fetch of this list goes through, load or poll
// alike, so the staleness guard below covers both.
async function refreshMovements() {
    const generation = movementsGeneration;
    const seq = ++movementsSeq;
    const isStale = () => generation !== movementsGeneration || seq < movementsAppliedSeq;
    movementsInFlight++;
    let res, data;
    // Phase 4.9's prefill data — refreshed every cycle alongside the list
    // (not gated behind the dedup below), but not awaited: it's only a hint,
    // and /monitoring/status can be slow, so the table shouldn't wait on it.
    // An "arrived" row always re-renders on the next tick (see hasArrived
    // below), which is when a late answer shows up.
    if (!livenessInFlight) {
        livenessInFlight = true;
        refreshSiteLiveness().finally(() => { livenessInFlight = false; });
    }
    try {
        res = await fetch(`/movements${showMovementHistory ? "?history=true" : ""}`, { headers: authHeaders() });
        if (res.ok) data = await res.json();
    } catch {
        res = null;
    } finally {
        movementsInFlight--;
    }
    if (isStale()) return;
    movementsAppliedSeq = seq;
    if (!res || !res.ok) {
        // Only replace the loading placeholder — a failed background poll
        // shouldn't wipe a table that's already showing good data.
        if (movementsCache === null) {
            document.getElementById("movements-rows").innerHTML = '<tr><td colspan="6" class="loading-text">Failed to load movements</td></tr>';
        }
        return;
    }
    // movementsCache is null exactly when there's nothing on screen yet to
    // compare against (see its declaration above) — most other polls land
    // on an unchanged list, where rebuilding the tbody anyway would tear
    // down and recreate every action button, dropping whatever button the
    // mouse happens to be hovering (its :hover style blinks off then back
    // on) even though nothing actually changed. The one exception: an
    // "arrived" row's prefill can go stale-to-fresh purely from
    // siteLivenessByLocation changing underneath an otherwise-identical
    // movements list, so an unchanged list still re-renders while one is
    // waiting on a site-check answer.
    const unchanged = movementsCache !== null && JSON.stringify(data) === JSON.stringify(movementsCache);
    const hasArrived = data.some(m => m.status === "arrived");
    if (unchanged && !hasArrived) return;
    movementsCache = data;
    renderMovementsList(movementsCache);
}

// Initial/tab-switch/history-toggle load: shows the loading text once, then
// defers to the quiet refresh above. Resetting movementsCache here — not
// just at declaration — is what makes refreshMovements() always render on
// this path even when the fetch happens to return exactly what was on
// screen during a previous visit: there's nothing left on screen right
// now (it's the loading placeholder), so nothing can legitimately compare
// equal to it.
async function loadMovements() {
    document.getElementById("movements-rows").innerHTML = '<tr><td colspan="6" class="loading-text">Loading movements...</td></tr>';
    movementsCache = null;
    movementsGeneration++;
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

// Phase 4.9 — "Monitoring saw Sunton come back online at 14:32, 12m after
// you marked arrived" (PHASES.md's own example). Two cases suggest an
// answer: monitoring currently shows offline (supports "Still Down"), or
// it shows online with the recovery happening AFTER this movement's own
// arrival (supports "Site Online") — a site that was already online
// before arrival isn't evidence of anything this movement did, so that
// case deliberately returns no hint at all rather than a misleading one.
function siteCheckPrefill(m) {
    if (!siteLivenessByLocation || m.to_location_id == null) return null;
    const site = siteLivenessByLocation[m.to_location_id];
    if (!site) return null;
    if (site.state === "offline") return { suggestOnline: false, site };
    if (site.state === "online" && m.arrived_at && site.state_since
        && new Date(site.state_since) > new Date(m.arrived_at)) {
        return { suggestOnline: true, site };
    }
    return null;
}

function eatTime(iso) {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: "Africa/Nairobi" });
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
        case "arrived": {
            const prefill = siteCheckPrefill(m);
            let hint = "";
            if (prefill) {
                hint = prefill.suggestOnline
                    ? `<div class="site-check-hint">Monitoring saw it back online at ${eatTime(prefill.site.state_since)}, ${Math.round((new Date(prefill.site.state_since) - new Date(m.arrived_at)) / 60000)}m after you marked arrived.</div>`
                    : `<div class="site-check-hint">Monitoring still shows this site offline.</div>`;
            }
            const siteIdAttr = prefill ? ` data-monitored-site-id="${prefill.site.id}"` : "";
            const onlineCls = prefill && prefill.suggestOnline ? " suggested" : "";
            const downCls = prefill && !prefill.suggestOnline ? " suggested" : "";
            return `
                ${hint}
                <div class="actions-cell">
                    <button type="button" class="movement-site-check-btn${onlineCls}" data-answer="true" data-id="${m.id}"${siteIdAttr}>Site Online</button>
                    <button type="button" class="movement-site-check-btn${downCls}" data-answer="false" data-id="${m.id}"${siteIdAttr}>Still Down</button>
                </div>
            `;
        }
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
                // Phase 4.9 — reconciliation, not the source of truth: fired
                // alongside confirm-online above (which just succeeded and
                // is what actually moved the battery's lifecycle forward),
                // never instead of it, and only when this movement's site
                // was one monitoring could match at all (see
                // siteCheckPrefill — a movement with no monitored site at
                // its destination has no data-monitored-site-id to send).
                // Best-effort: a failure here is a missed reconciliation
                // record, not a reason to tell the tech their tap failed.
                if (btn.dataset.monitoredSiteId) {
                    fetch(`/monitoring/sites/${btn.dataset.monitoredSiteId}/confirmation-check`, {
                        method: "POST",
                        headers: authHeaders({ "Content-Type": "application/json" }),
                        body: JSON.stringify({ is_online }),
                    }).catch(() => {});
                }
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

// Not gated on auth state — only on this view being visible — so, like
// dashboard.js's live sync, it's started/stopped in step with
// login/logout (appShownHandler / logoutHandler below) rather than once
// at boot. Otherwise it keeps ticking after logout and a tick landing
// before the next login finishes fires /movements with no Authorization
// header, drawing a 401 — only visible in a logout-then-relogin flow.
let liveSyncIntervalId = null;

function startLiveSync() {
    clearInterval(liveSyncIntervalId); // idempotent — see dashboard.js's startLiveSync
    liveSyncIntervalId = setInterval(() => {
        const view = document.getElementById("view-movements");
        if (!view || view.hidden) return;
        if (document.visibilityState !== "visible") return;
        // Don't pile a new request on top of one still waiting — on a slow
        // server that just queues more work and answers nothing sooner.
        if (movementsInFlight > 0) return;
        refreshMovements();
    }, LIVE_SYNC_INTERVAL_MS);
}

function stopLiveSync() {
    clearInterval(liveSyncIntervalId);
    liveSyncIntervalId = null;
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

    registerAppShownHandler(startLiveSync);
    registerLogoutHandler(stopLiveSync);
}
