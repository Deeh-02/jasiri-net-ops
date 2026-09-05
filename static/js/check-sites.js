import { authHeaders, formatDate, showView, navigate, registerRoute, refreshBadges } from "./common.js";

async function loadCheckSites() {
    document.getElementById("check-sites-rows").innerHTML = '<tr><td colspan="5" class="loading-text">Loading sites...</td></tr>';
    const res = await fetch("/locations/verification", { headers: authHeaders() });
    if (!res.ok) {
        document.getElementById("check-sites-rows").innerHTML = '<tr><td colspan="5" class="loading-text">Failed to load sites</td></tr>';
        return;
    }
    const checkSitesCache = await res.json();
    renderCheckSitesList(checkSitesCache);
}

function buildCheckSitesStats(sites) {
    const online = sites.filter(s => s.is_online).length;
    const offline = sites.filter(s => !s.is_online).length;
    const needsCheck = sites.filter(s => s.needs_check).length;

    return [
        { label: "Online", value: online, cls: "charged" },
        { label: "Offline", value: offline, cls: "low" },
        { label: "Needs Check", value: needsCheck, cls: "deployed" },
    ];
}

function renderCheckSitesStats(sites) {
    const grid = document.getElementById("check-sites-stat-grid");
    grid.innerHTML = buildCheckSitesStats(sites).map(s => `
        <div class="stat-card ${s.cls}">
            <div class="stat-label">${s.label}</div>
            <div class="stat-value">${s.value}</div>
        </div>
    `).join("");
}

function renderCheckSitesList(sites) {
    const tbody = document.getElementById("check-sites-rows");

    renderCheckSitesStats(sites);

    if (sites.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="loading-text">No sites found.</td></tr>`;
        return;
    }

    tbody.innerHTML = sites.map(s => `
        <tr class="${s.is_online ? "" : "site-row-offline"}">
            <td>${s.name}</td>
            <td><span class="status-pill ${s.is_online ? "site-online" : "site-offline"}">${s.is_online ? "Online" : "Offline"}</span></td>
            <td>${s.verification_confirmed_at ? formatDate(s.verification_confirmed_at) : "Never checked"}</td>
            <td><span class="hour-check-pill ${s.needs_check ? "pending" : "done"}">${s.needs_check ? "Needs check" : "Checked"}</span></td>
            <td>
                <div class="actions-cell">
                    <button type="button" class="site-check-btn" data-answer="true" data-id="${s.id}">Online</button>
                    <button type="button" class="site-check-btn" data-answer="false" data-id="${s.id}">Offline</button>
                </div>
            </td>
        </tr>
    `).join("");

    tbody.querySelectorAll(".site-check-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
            const is_online = btn.dataset.answer === "true";
            const res = await fetch(`/locations/${btn.dataset.id}/confirm`, {
                method: "POST",
                headers: authHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify({ is_online })
            });
            if (res.ok) {
                await loadCheckSites();
                await refreshBadges();
            } else {
                alert("Failed to update site status");
            }
        });
    });
}

export function initCheckSites() {
    document.getElementById("check-sites-link-btn").addEventListener("click", () => {
        navigate("check-sites");
    });

    registerRoute("check-sites", () => {
        showView("view-check-sites");
        loadCheckSites();
    });
}
