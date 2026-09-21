import {
    authHeaders, editIconSvg,
    showView, navigate, registerRoute,
} from "./common.js";

let packagesCache = [];
let unpricedCache = [];
let stacksCache = [];
// The package being edited, or null when the modal is pricing a profile that
// has no row yet (in which case pendingProfile carries its name).
let editPackageId = null;
let pendingProfile = null;

function esc(value) {
    const d = document.createElement("div");
    d.textContent = value == null ? "" : String(value);
    return d.innerHTML;
}

function money(kes) {
    return `KES ${Number(kes || 0).toLocaleString()}`;
}

/* Minutes in, the largest clean unit out: 1440 -> "1d", 120 -> "2h". The
   column has to be skimmable next to a price, not exact to the minute. */
function durationLabel(mins) {
    if (mins == null) return null;
    if (mins % 1440 === 0) return `${mins / 1440}d`;
    if (mins % 60 === 0) return `${mins / 60}h`;
    return `${mins}m`;
}

function ago(iso) {
    if (!iso) return "never";
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
}

async function loadPackages() {
    const res = await fetch("/monitoring/packages", { headers: authHeaders() });
    if (!res.ok) {
        document.getElementById("pkg-rows").innerHTML =
            `<tr><td colspan="7" class="loading-text">Could not load packages.</td></tr>`;
        return;
    }
    const payload = await res.json();
    packagesCache = payload.packages;
    unpricedCache = payload.unpriced;
    stacksCache = payload.stacks || [];
    renderUnpriced();
    renderStacks();
    renderPackages();
}

/* ---- Sold but not priced. The reason this screen exists: without it, a
   package the vendor renamed books at 0 and says nothing. ---- */

function renderUnpriced() {
    const block = document.getElementById("pkg-unpriced-block");
    block.hidden = unpricedCache.length === 0;

    document.getElementById("pkg-unpriced-items").innerHTML = unpricedCache.map(u => `
        <div class="inbox-item">
            <div class="inbox-item-main">
                <span class="inbox-item-name">${esc(u.profile_name)}</span>
                <span class="inbox-item-kind">${u.seen} sold</span>
                ${u.name_price_kes != null
                    ? `<span class="inbox-item-guess">name says ${esc(u.name_price_kes)}</span>`
                    : ""}
            </div>
            <div class="inbox-item-actions">
                <button type="button" class="btn-primary pkg-price-btn" data-name="${esc(u.profile_name)}">Set a price</button>
            </div>
        </div>`).join("");

    document.getElementById("pkg-unpriced-items").querySelectorAll(".pkg-price-btn").forEach(btn => {
        btn.addEventListener("click", () => openPackageModal(null, btn.dataset.name));
    });
}

/* ---- Possible stacked purchases. Flagged, never applied: see
   _flag_possible_stack in db/monitoring.py for why Ops refuses to multiply
   money on an inference. ---- */

function renderStacks() {
    const block = document.getElementById("pkg-stacks-block");
    block.hidden = stacksCache.length === 0;

    document.getElementById("pkg-stacks-items").innerHTML = stacksCache.map(s => {
        const implied = s.implied_purchases || 2;
        return `
        <div class="inbox-item">
            <div class="inbox-item-main">
                <span class="inbox-item-name">${esc(s.hotspot_username)}</span>
                <span class="inbox-item-kind">${esc(s.profile_name)}</span>
                <span class="inbox-item-guess">looks like ${implied} &times;</span>
                <span class="pkg-stack-detail">expiry jumped ${esc(durationLabel(s.jump_minutes) || "?")} on a ${esc(durationLabel(s.package_minutes) || "?")} package &middot; ${esc(ago(s.seen_at))}</span>
            </div>
            <div class="inbox-item-actions">
                <button type="button" class="btn-secondary pkg-stack-ok" data-id="${s.id}">Looks right</button>
            </div>
        </div>`;
    }).join("");

    document.getElementById("pkg-stacks-items").querySelectorAll(".pkg-stack-ok").forEach(btn => {
        btn.addEventListener("click", () => clearStack(btn.dataset.id));
    });
}

/* Reuses the quarantine dismiss endpoint — same table, same permission. It
   only closes the note; nothing about the recorded sale changes either way. */
async function clearStack(id) {
    const res = await fetch(`/monitoring/inbox/${id}/dismiss`, { method: "POST", headers: authHeaders() });
    if (res.ok) {
        await loadPackages();
    } else {
        alert("Could not clear that one");
    }
}

/* ---- The price list ---- */

function renderPackages() {
    const tbody = document.getElementById("pkg-rows");

    if (packagesCache.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="loading-text">No packages yet.</td></tr>`;
        return;
    }

    tbody.innerHTML = packagesCache.map(p => {
        // The name carries the price on this network ("Full day pass30"), so
        // a stored price that disagrees with it is worth a second look —
        // shown as a question, never corrected silently.
        const warn = p.price_disagrees
            ? `<span class="pkg-mismatch" title="The profile name suggests a different price">name says ${esc(p.name_price_kes)}</span>`
            : "";
        const price = p.is_comped
            ? `<span class="pkg-comped">Free</span>`
            : `<span class="pkg-price">${esc(money(p.price_kes))}</span>`;
        const lasts = durationLabel(p.duration_minutes);
        return `
        <tr class="${p.is_active ? "" : "site-row-off"}">
            <td class="col-frozen">${esc(p.profile_name)}</td>
            <td>${price}${warn}</td>
            <td>${lasts ? `<span class="pkg-sold">${esc(lasts)}</span>` : `<span class="dim-cell">not set</span>`}</td>
            <td><span class="pkg-sold">${p.sold || 0}</span></td>
            <td><span class="status-since">${esc(ago(p.last_seen_at))}</span></td>
            <td>
                <label class="site-toggle">
                    <input type="checkbox" class="pkg-active-toggle" data-id="${p.id}" ${p.is_active ? "checked" : ""}>
                    <span class="site-toggle-track"></span>
                </label>
            </td>
            <td>
                <button type="button" class="edit-site-btn pkg-edit-btn" data-id="${p.id}" title="Edit package">${editIconSvg()}</button>
            </td>
        </tr>`;
    }).join("");

    tbody.querySelectorAll(".pkg-edit-btn").forEach(btn => {
        btn.addEventListener("click", () => openPackageModal(btn.dataset.id, null));
    });
    tbody.querySelectorAll(".pkg-active-toggle").forEach(box => {
        box.addEventListener("change", () => toggleActive(box));
    });
}

/* Switching a package off does NOT make it worthless — it stops Ops pricing
   NEW sales of it, which is what "removed from billing" means. Sales already
   recorded keep the price they were recorded at. */
async function toggleActive(box) {
    const res = await fetch(`/monitoring/packages/${box.dataset.id}`, {
        method: "PATCH",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ is_active: box.checked }),
    });
    if (!res.ok) {
        box.checked = !box.checked;
        const err = await res.json().catch(() => ({}));
        alert(err.detail || "Could not change that package");
        return;
    }
    await loadPackages();
}

/* ---- Edit / price modal ---- */

function openPackageModal(packageId, profileName) {
    const form = document.getElementById("pkg-edit-form");
    form.reset();
    editPackageId = null;
    pendingProfile = null;
    document.getElementById("pkg-edit-msg").textContent = "";

    const hint = document.getElementById("pkg-edit-hint");
    hint.hidden = true;

    if (packageId) {
        const pkg = packagesCache.find(p => String(p.id) === String(packageId));
        if (!pkg) return;
        editPackageId = pkg.id;
        document.getElementById("pkg-edit-title").textContent = "Edit Package";
        document.getElementById("pkg-edit-name").textContent = pkg.profile_name;
        document.getElementById("pkg-edit-price").value = pkg.price_kes;
        document.getElementById("pkg-edit-comped").checked = pkg.is_comped;
        setDurationFields(pkg.duration_minutes);
        document.getElementById("pkg-edit-notes").value = pkg.notes || "";
        if (pkg.price_disagrees) {
            hint.hidden = false;
            hint.textContent = `The name suggests KES ${pkg.name_price_kes}. Ops has it at ${money(pkg.price_kes)}.`;
        }
    } else {
        const found = unpricedCache.find(u => u.profile_name === profileName);
        pendingProfile = profileName;
        document.getElementById("pkg-edit-title").textContent = "Set a Price";
        document.getElementById("pkg-edit-name").textContent = profileName;
        setDurationFields(null);
        if (found && found.name_price_kes != null) {
            document.getElementById("pkg-edit-price").value = found.name_price_kes;
            hint.hidden = false;
            hint.textContent = `Filled in from the name. Change it if billing charges something else.`;
        }
        // Sales already recorded at 0 under this name are NOT repriced — the
        // recorded price is a snapshot of the moment of sale by design.
        const already = found ? found.seen : 0;
        if (already) {
            hint.hidden = false;
            hint.textContent = (hint.textContent ? hint.textContent + " " : "")
                + `The ${already} already recorded stay at KES 0; this applies from the next sale.`;
        }
    }

    document.getElementById("pkg-edit-overlay").hidden = false;
}

/* Shown back in whatever unit reads cleanest, so 1440 does not come back as
   "1440 minutes" and get retyped wrong. */
function setDurationFields(mins) {
    const box = document.getElementById("pkg-edit-duration");
    const unit = document.getElementById("pkg-edit-duration-unit");
    if (mins == null) {
        box.value = "";
        unit.value = "60";
        return;
    }
    const size = mins % 1440 === 0 ? 1440 : mins % 60 === 0 ? 60 : 1;
    unit.value = String(size);
    box.value = mins / size;
}

function readDurationMinutes() {
    const raw = document.getElementById("pkg-edit-duration").value;
    if (raw === "") return null;
    const size = parseInt(document.getElementById("pkg-edit-duration-unit").value, 10) || 1;
    const n = parseInt(raw, 10);
    return Number.isFinite(n) && n > 0 ? n * size : null;
}

function closePackageModal() {
    document.getElementById("pkg-edit-overlay").hidden = true;
    editPackageId = null;
    pendingProfile = null;
}

async function submitPackageForm(event) {
    event.preventDefault();
    document.getElementById("pkg-edit-msg").textContent = "";

    const priceValue = document.getElementById("pkg-edit-price").value;
    const body = {
        price_kes: priceValue === "" ? 0 : parseFloat(priceValue),
        is_comped: document.getElementById("pkg-edit-comped").checked,
        duration_minutes: readDurationMinutes(),
        notes: document.getElementById("pkg-edit-notes").value.trim() || null,
    };

    let res;
    try {
        if (editPackageId) {
            res = await fetch(`/monitoring/packages/${editPackageId}`, {
                method: "PATCH",
                headers: authHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify(body),
            });
        } else {
            res = await fetch("/monitoring/packages", {
                method: "POST",
                headers: authHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify({ ...body, profile_name: pendingProfile }),
            });
        }
    } catch (err) {
        showFormError("Could not reach the server");
        return;
    }

    if (res.ok) {
        closePackageModal();
        await loadPackages();
        return;
    }
    const err = await res.json().catch(() => ({}));
    showFormError(err.detail || `Could not save that package (server said ${res.status})`);
}

function showFormError(text) {
    const el = document.getElementById("pkg-edit-msg");
    el.textContent = text;
    el.className = "form-msg error";
}

export function initPackages() {
    document.getElementById("pkg-back-btn").addEventListener("click", () => navigate("manage-sites"));
    document.getElementById("packages-link-btn").addEventListener("click", () => navigate("packages"));
    document.getElementById("pkg-edit-cancel").addEventListener("click", closePackageModal);
    document.getElementById("pkg-edit-form").addEventListener("submit", submitPackageForm);

    registerRoute("packages", () => {
        closePackageModal();
        showView("view-packages");
        loadPackages();
    });
}
