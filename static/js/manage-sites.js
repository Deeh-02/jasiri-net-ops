import {
    authHeaders, showMessage, editIconSvg,
    showView, navigate, registerRoute,
} from "./common.js";

let sitesCache = [];
let locationsCache = [];
let inboxCache = [];
let ignoredCache = [];
let editSiteId = null;
let pendingInboxItem = null;

function esc(value) {
    const d = document.createElement("div");
    d.textContent = value == null ? "" : String(value);
    return d.innerHTML;
}

async function loadManageSites() {
    const [sitesRes, inboxRes, ignoredRes] = await Promise.all([
        fetch("/monitoring/manage/sites", { headers: authHeaders() }),
        fetch("/monitoring/inbox", { headers: authHeaders() }),
        fetch("/monitoring/inbox/dismissed", { headers: authHeaders() }),
    ]);
    if (!sitesRes.ok) {
        document.getElementById("manage-sites-rows").innerHTML =
            `<tr><td colspan="7" class="loading-text">Could not load sites.</td></tr>`;
        return;
    }
    const payload = await sitesRes.json();
    sitesCache = payload.sites;
    locationsCache = payload.locations;
    inboxCache = inboxRes.ok ? await inboxRes.json() : [];
    ignoredCache = ignoredRes.ok ? await ignoredRes.json() : [];
    renderInbox();
    renderIgnored();
    renderSites();
}

/* ---- The inbox. This is what makes adding a site a click rather than a
   database edit: the router already told us the username or the VLAN, so
   nobody has to type either one. ---- */

function inboxLabel(item) {
    return item.reason === "unknown_vlan" ? `VLAN ${item.vlan_id}` : item.pppoe_username;
}

function renderInbox() {
    const block = document.getElementById("inbox-block");
    block.hidden = inboxCache.length === 0;

    document.getElementById("inbox-items").innerHTML = inboxCache.map(item => {
        const guess = suggestLocation(inboxLabel(item));
        return `
        <div class="inbox-item">
            <div class="inbox-item-main">
                <span class="inbox-item-name">${esc(inboxLabel(item))}</span>
                <span class="inbox-item-kind">${item.reason === "unknown_vlan" ? "VLAN with no site" : "PPPoE login"}</span>
                ${guess ? `<span class="inbox-item-guess">looks like ${esc(guess.name)}</span>` : ""}
            </div>
            <div class="inbox-item-actions">
                <button type="button" class="btn-primary inbox-add" data-id="${item.id}">Add as site</button>
                <button type="button" class="btn-secondary inbox-dismiss" data-id="${item.id}" data-label="${esc(inboxLabel(item))}">Not a site</button>
            </div>
        </div>`;
    }).join("");

    document.getElementById("inbox-items").querySelectorAll(".inbox-add").forEach(btn => {
        btn.addEventListener("click", () => navigate(`manage-sites/new/${btn.dataset.id}`));
    });
    document.getElementById("inbox-items").querySelectorAll(".inbox-dismiss").forEach(btn => {
        btn.addEventListener("click", () => dismissItem(btn.dataset.id, btn.dataset.label));
    });
}

function renderIgnored() {
    const block = document.getElementById("ignored-block");
    block.hidden = ignoredCache.length === 0;
    document.getElementById("ignored-count").textContent = ignoredCache.length;

    const list = document.getElementById("ignored-items");
    list.innerHTML = ignoredCache.map(item => `
        <div class="inbox-item">
            <div class="inbox-item-main">
                <span class="inbox-item-name">${esc(inboxLabel(item))}</span>
                <span class="inbox-item-kind">${item.reason === "unknown_vlan" ? "VLAN with no site" : "PPPoE login"}</span>
            </div>
            <div class="inbox-item-actions">
                <button type="button" class="btn-secondary ignored-restore" data-id="${item.id}">Restore</button>
            </div>
        </div>`).join("");

    list.querySelectorAll(".ignored-restore").forEach(btn => {
        btn.addEventListener("click", () => restoreItem(btn.dataset.id));
    });
}

async function restoreItem(id) {
    const res = await fetch(`/monitoring/inbox/${id}/restore`, { method: "POST", headers: authHeaders() });
    if (res.ok) {
        await loadManageSites();
    } else {
        alert("Could not restore that item");
    }
}

async function dismissItem(id, label) {
    if (!confirm(`Mark "${label}" as not a site?\n\nA home customer's PPPoE login is the usual reason. It moves to Ignored at the bottom of this list, where you can restore it.`)) return;
    const res = await fetch(`/monitoring/inbox/${id}/dismiss`, { method: "POST", headers: authHeaders() });
    if (res.ok) {
        await loadManageSites();
    } else {
        alert("Could not dismiss that item");
    }
}

/* The names line up closely enough to be worth offering ("Kamutini_Hotspot"
   against a site called "Kamutini"), but never worth applying silently — a
   wrong link attributes one site's outages to another. */
function normalise(text) {
    return String(text || "").toLowerCase().replace(/[^a-z0-9]/g, "").replace(/hotspot$/, "");
}

function suggestLocation(label) {
    const key = normalise(label);
    if (!key) return null;
    return locationsCache.find(l => normalise(l.name) === key)
        || locationsCache.find(l => key.startsWith(normalise(l.name)) && normalise(l.name).length >= 4)
        || null;
}

/* ---- The site list ---- */

function renderSites() {
    const tbody = document.getElementById("manage-sites-rows");

    if (sitesCache.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="loading-text">No sites yet.</td></tr>`;
        return;
    }

    tbody.innerHTML = sitesCache.map(s => {
        const name = s.location_name || s.name || `VLAN ${s.vlan_id}`;
        return `
        <tr class="${s.is_active ? "" : "site-row-off"}">
            <td class="col-frozen">${esc(name)}</td>
            <td>${s.location_name ? esc(s.location_name) : "<span class='dim-cell'>Not linked</span>"}</td>
            <td>${s.vlan_id == null ? "<span class='dim-cell'>&ndash;</span>" : esc(s.vlan_id)}</td>
            <td>${s.pppoe_username ? esc(s.pppoe_username) : "<span class='dim-cell'>&ndash;</span>"}</td>
            <td><span class="status-pill liveness-${esc(s.liveness_source)}">${esc(s.liveness_source)}</span></td>
            <td>
                <label class="site-toggle">
                    <input type="checkbox" class="site-active-toggle" data-id="${s.id}" ${s.is_active ? "checked" : ""}>
                    <span class="site-toggle-track"></span>
                </label>
            </td>
            <td>
                <button type="button" class="edit-site-btn" data-id="${s.id}" title="Edit site">${editIconSvg()}</button>
            </td>
        </tr>`;
    }).join("");

    tbody.querySelectorAll(".edit-site-btn").forEach(btn => {
        btn.addEventListener("click", () => navigate(`manage-sites/${btn.dataset.id}/edit`));
    });
    tbody.querySelectorAll(".site-active-toggle").forEach(box => {
        box.addEventListener("change", () => toggleActive(box));
    });
}

async function toggleActive(box) {
    const res = await fetch(`/monitoring/sites/${box.dataset.id}`, {
        method: "PATCH",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ is_active: box.checked }),
    });
    if (!res.ok) {
        // Put the switch back rather than leave it showing a change that
        // never reached the server.
        box.checked = !box.checked;
        const err = await res.json().catch(() => ({}));
        alert(err.detail || "Could not change that site");
        return;
    }
    await loadManageSites();
}

/* ---- Add / edit form ---- */

function selectedLiveness() {
    return document.querySelector("#site-form-liveness .liveness-opt.is-on").dataset.value;
}

function setLiveness(value) {
    document.querySelectorAll("#site-form-liveness .liveness-opt").forEach(btn => {
        btn.classList.toggle("is-on", btn.dataset.value === value);
    });
    const pppoe = document.getElementById("site-form-pppoe");
    pppoe.hidden = value !== "pppoe";
    pppoe.required = value === "pppoe";
}

function fillLocations(selectedId) {
    const select = document.getElementById("site-form-location");
    select.innerHTML = '<option value="">Not linked to a site in Sites</option>';
    locationsCache.forEach(l => {
        const opt = document.createElement("option");
        opt.value = String(l.id);
        opt.textContent = l.name;
        select.appendChild(opt);
    });
    select.value = selectedId == null ? "" : String(selectedId);
}

async function openSiteForm(siteId, inboxItemId) {
    if (sitesCache.length === 0 && locationsCache.length === 0) await loadManageSites();

    const form = document.getElementById("site-form");
    form.reset();
    editSiteId = null;
    pendingInboxItem = null;

    const origin = document.getElementById("site-form-origin");
    origin.hidden = true;

    if (siteId) {
        const site = sitesCache.find(s => String(s.id) === String(siteId));
        if (!site) return navigate("manage-sites");
        editSiteId = site.id;
        document.getElementById("site-form-title").textContent = "Edit Site";
        document.getElementById("site-form-name").value = site.name || "";
        document.getElementById("site-form-vlan").value = site.vlan_id == null ? "" : site.vlan_id;
        document.getElementById("site-form-pppoe").value = site.pppoe_username || "";
        document.getElementById("site-form-notes").value = site.notes || "";
        fillLocations(site.location_id);
        setLiveness(site.liveness_source === "activity" ? "activity" : "pppoe");
        return;
    }

    document.getElementById("site-form-title").textContent = "Add Site";
    const item = inboxCache.find(i => String(i.id) === String(inboxItemId));

    if (item) {
        pendingInboxItem = item.id;
        const label = inboxLabel(item);
        origin.hidden = false;
        origin.innerHTML = `Adding <b>${esc(label)}</b>, as reported by the router.`;

        if (item.reason === "unknown_vlan") {
            document.getElementById("site-form-vlan").value = item.vlan_id;
            setLiveness("activity");
        } else {
            document.getElementById("site-form-pppoe").value = item.pppoe_username;
            setLiveness("pppoe");
        }

        const guess = suggestLocation(label);
        fillLocations(guess ? guess.id : null);
        if (!guess) document.getElementById("site-form-name").value = label.replace(/_?[Hh]otspot$/, "").replace(/_/g, " ");
        return;
    }

    fillLocations(null);
    setLiveness("pppoe");
}

async function submitSiteForm(event) {
    event.preventDefault();

    const locationValue = document.getElementById("site-form-location").value;
    const vlanValue = document.getElementById("site-form-vlan").value;
    const liveness = selectedLiveness();
    // The PPPoE box keeps its text while hidden, so read it only when it is
    // the field in use — otherwise switching a site to Activity would save,
    // and keep the UNIQUE lock on, a username nothing reads.
    const pppoe = liveness === "pppoe"
        ? document.getElementById("site-form-pppoe").value.trim() || null
        : null;
    const body = {
        name: document.getElementById("site-form-name").value.trim() || null,
        location_id: locationValue ? parseInt(locationValue, 10) : null,
        vlan_id: vlanValue ? parseInt(vlanValue, 10) : null,
        pppoe_username: pppoe,
        liveness_source: liveness,
        notes: document.getElementById("site-form-notes").value.trim() || null,
    };

    let res;
    if (editSiteId) {
        res = await fetch(`/monitoring/sites/${editSiteId}`, {
            method: "PATCH",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body),
        });
    } else {
        res = await fetch("/monitoring/sites", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ ...body, inbox_item_id: pendingInboxItem }),
        });
    }

    if (res.ok) {
        await loadManageSites();
        navigate("manage-sites");
        return;
    }
    const err = await res.json().catch(() => ({}));
    showMessage("site-form-msg", err.detail || "Could not save that site", true);
}

export function initManageSites() {
    document.getElementById("manage-sites-link-btn").addEventListener("click", () => navigate("manage-sites"));
    document.getElementById("add-site-open-btn").addEventListener("click", () => navigate("manage-sites/new"));
    document.getElementById("site-form-cancel").addEventListener("click", () => navigate("manage-sites"));
    document.getElementById("site-form").addEventListener("submit", submitSiteForm);

    document.querySelectorAll("#site-form-liveness .liveness-opt").forEach(btn => {
        btn.addEventListener("click", () => setLiveness(btn.dataset.value));
    });

    registerRoute("manage-sites", async (params) => {
        if (params[0] === "new") {
            await openSiteForm(null, params[1]);
            showView("view-site-form");
        } else if (params[0] && params[1] === "edit") {
            await openSiteForm(params[0], null);
            showView("view-site-form");
        } else {
            showView("view-manage-sites");
            loadManageSites();
        }
    });
}
