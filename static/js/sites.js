import {
    can, authHeaders, showMessage, editIconSvg, deleteIconSvg,
    registerAppShownHandler, registerCmdkProvider,
} from "./common.js";

let locationsCache = [];
let editSiteId = null;

async function loadSites() {
    const res = await fetch("/locations", { headers: authHeaders() });
    locationsCache = res.ok ? await res.json() : [];
    renderSiteList(locationsCache);
}

// ---- Sites table (name + HB tag, contact info, edit/delete actions) ----
function renderSiteList(locations) {
    const tbody = document.getElementById("sites-rows");
    if (!tbody) return;

    const hasActions = can("sites", "edit") || can("sites", "delete");

    tbody.innerHTML = locations.map(loc => `
        <tr>
            <td>
                ${loc.name}
                ${loc.is_home_base ? `<span class="hb-tag">HB</span>` : ""}
            </td>
            <td>${loc.contact_name || "—"}</td>
            <td>${loc.contact_phone || "—"}</td>
            <td>${loc.address || "—"}</td>
            ${hasActions ? `
            <td>
                ${can("sites", "edit") ? `
                <button type="button" class="edit-site-btn" data-id="${loc.id}" title="Edit site">
                    ${editIconSvg()}
                </button>` : ""}
                ${can("sites", "delete") ? `
                <button type="button" class="delete-site-btn" data-id="${loc.id}" data-name="${loc.name}" title="Delete site">
                    ${deleteIconSvg()}
                </button>` : ""}
            </td>` : ""}
        </tr>
    `).join("");

    tbody.querySelectorAll(".edit-site-btn").forEach(btn => {
        btn.addEventListener("click", () => openEditSiteModal(btn.dataset.id));
    });

    tbody.querySelectorAll(".delete-site-btn").forEach(btn => {
        btn.addEventListener("click", () => deleteSite(btn.dataset.id, btn.dataset.name));
    });
}

async function deleteSite(id, name) {
    if (!confirm(`Delete site "${name}"? This can't be undone.`)) return;

    const response = await fetch(`/locations/${id}`, { method: "DELETE", headers: authHeaders() });
    if (response.ok) {
        await loadSites();
    } else {
        alert("Failed to delete site — it may still have battery movement history tied to it.");
    }
}

function openEditSiteModal(locationId) {
    const loc = locationsCache.find(l => String(l.id) === String(locationId));
    if (!loc) return;

    editSiteId = loc.id;
    document.getElementById("edit-site-name").value = loc.name || "";
    document.getElementById("edit-site-contact-name").value = loc.contact_name || "";
    document.getElementById("edit-site-contact-phone").value = loc.contact_phone || "";
    document.getElementById("edit-site-address").value = loc.address || "";
    document.getElementById("edit-site-home-base").checked = !!loc.is_home_base;
    document.getElementById("edit-site-overlay").hidden = false;
}

function closeEditSiteModal() {
    document.getElementById("edit-site-overlay").hidden = true;
    editSiteId = null;
}

export function initSites() {
    const addSiteOverlay = document.getElementById("add-site-overlay");
    const addSiteOpenBtn = document.getElementById("add-site-open-btn");
    const addSiteCancelBtn = document.getElementById("add-site-cancel");

    addSiteOpenBtn.addEventListener("click", () => {
        document.getElementById("location-form").reset();
        addSiteOverlay.hidden = false;
    });

    addSiteCancelBtn.addEventListener("click", () => {
        addSiteOverlay.hidden = true;
    });

    addSiteOverlay.addEventListener("click", (e) => {
        if (e.target === addSiteOverlay) addSiteOverlay.hidden = true;
    });

    document.getElementById("location-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const name = document.getElementById("location-name").value;
        const contact_name = document.getElementById("location-contact-name").value || null;
        const contact_phone = document.getElementById("location-contact-phone").value || null;
        const address = document.getElementById("location-address").value || null;
        const is_home_base = document.getElementById("location-home-base").checked;

        const response = await fetch("/locations", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ name, contact_name, contact_phone, address, is_home_base })
        });

        if (response.ok) {
            showMessage("location-msg", "Site added", false);
            e.target.reset();
            addSiteOverlay.hidden = true;
            await loadSites();
        } else {
            showMessage("location-msg", "Failed to add site", true);
        }
    });

    const editSiteOverlay = document.getElementById("edit-site-overlay");
    const editSiteCancelBtn = document.getElementById("edit-site-cancel");

    editSiteCancelBtn.addEventListener("click", closeEditSiteModal);
    editSiteOverlay.addEventListener("click", (e) => {
        if (e.target === editSiteOverlay) closeEditSiteModal();
    });

    document.getElementById("edit-site-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!editSiteId) return;

        const body = {
            name: document.getElementById("edit-site-name").value,
            contact_name: document.getElementById("edit-site-contact-name").value || null,
            contact_phone: document.getElementById("edit-site-contact-phone").value || null,
            address: document.getElementById("edit-site-address").value || null,
            is_home_base: document.getElementById("edit-site-home-base").checked
        };

        const response = await fetch(`/locations/${editSiteId}`, {
            method: "PATCH",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body)
        });

        if (response.ok) {
            closeEditSiteModal();
            await loadSites();
        } else {
            alert("Failed to update site");
        }
    });

    // Eager-loaded at login (not just on first nav visit) so cmdk site search
    // works immediately, matching the original's always-fetched locationsCache.
    registerAppShownHandler(loadSites);

    registerCmdkProvider({
        getItems: () => {
            const actions = can("sites", "add") ? [{
                type: "action",
                label: "Add Site",
                action: () => {
                    document.querySelector('[data-view="sites"]').click();
                    addSiteOpenBtn.click();
                }
            }] : [];
            const sites = can("sites", "view") ? locationsCache.map(l => ({
                type: "site",
                label: l.name,
                sublabel: l.address || (l.is_home_base ? "Home base" : ""),
                action: () => {
                    document.querySelector('[data-view="sites"]').click();
                    if (can("sites", "edit")) openEditSiteModal(l.id);
                }
            })) : [];
            return [...actions, ...sites];
        },
    });
}
