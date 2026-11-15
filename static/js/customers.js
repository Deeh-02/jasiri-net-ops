import { authHeaders, showView, registerRoute, navigate, showMessage, formatDate, stateRow } from "./common.js";

function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let routers = [];
let customersCache = [];
let editCustomerId = null;

function routerOptions(withAll) {
    const all = withAll ? `<option value="">All routers</option>` : "";
    return all + routers.map(r => `<option value="${r.id}">${esc(r.name)}</option>`).join("");
}

function importForm() {
    const file = document.getElementById("customers-import-file").files[0];
    if (!file) return null;
    const form = new FormData();
    form.append("router_id", document.getElementById("customers-import-router").value);
    form.append("file", file);
    return form;
}

function listItems(label, names) {
    if (!names.length) return "";
    return `<li>${names.length} ${label}: ${esc(names.slice(0, 8).join(", "))}${names.length > 8 ? ", …" : ""}</li>`;
}

function phoneDiffsHtml(diffs, commit) {
    if (!diffs.length) return "";
    const rows = diffs.map(d => `
        <tr>
            <td>${esc(d.username)}</td>
            <td>${esc(d.name || "—")}</td>
            <td>${esc(d.ops_phone || "none")}</td>
            <td>${esc(d.export_phone)}</td>
        </tr>`).join("");
    return `
        <p><strong>${diffs.length} phone number${diffs.length === 1 ? "" : "s"} differ</strong> between the export and Ops.
        ${commit ? "They were left as they are in Ops." : "They will be left as they are in Ops — use Edit to change any you want."}</p>
        <div class="table-scroll table-compact">
            <table>
                <thead><tr><th>Username</th><th>Name</th><th>Phone in Ops</th><th>Phone in export</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>`;
}

async function runImport(commit) {
    const result = document.getElementById("customers-import-result");
    const commitBtn = document.getElementById("customers-import-commit");
    const form = importForm();
    if (!form) {
        result.innerHTML = `<span class="error">Choose a file first.</span>`;
        return;
    }
    form.append("commit", commit);
    commitBtn.hidden = true;
    const res = await fetch("/customers/import", { method: "POST", headers: authHeaders(), body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        result.innerHTML = `<span class="error">${esc(data.detail || "Couldn't read that file.")}</span>`;
        return;
    }
    result.innerHTML = `
        <strong>${commit ? "Imported" : "Checked — nothing saved yet"}:</strong> ${data.total} customers in the file
        <ul>
            <li>${data.new} new, ${data.updated} changed, ${data.unchanged} unchanged</li>
            ${data.missing ? `<li>${data.missing} already on the list but not in this file (left as they are)</li>` : ""}
            ${listItems("new customers without a usable phone number (listed, but never texted)", data.bad_phone)}
            ${listItems("repeated usernames skipped", data.duplicates)}
        </ul>${phoneDiffsHtml(data.phone_differs, commit)}`;
    if (commit) {
        document.getElementById("customers-import-file").value = "";
        loadCustomers();
    } else {
        commitBtn.hidden = false;
    }
}

// The customer's fibre ONU (OLT → Customers links it), from SmartOLT.
// Glyph + word for every status, never colour alone.
const ONU_GLYPH = { Online: ["●", "good"], LOS: ["▼", "bad"], Offline: ["▼", "bad"], "Power fail": ["◆", "bad"] };
function onuCell(o) {
    if (!o) return `<span class="cell-muted">—</span>`;
    const [glyph, tone] = ONU_GLYPH[o.status] || ["○", ""];
    const since = o.last_status_change
        ? new Date(o.last_status_change).toLocaleString("en-GB", { timeZone: "Africa/Nairobi", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
    const signal = o.signal_1490_dbm != null ? `${o.signal_1490_dbm.toFixed(2)} dBm` : "no reading";
    const grade = o.signal_quality === "Warning" || o.signal_quality === "Critical" ? ` · ${esc(o.signal_quality)}` : "";
    return `<span class="customer-onu ${tone}">${glyph} ${esc(o.status || "Unknown")}</span>${o.stale ? ` <span class="cell-muted">(not current)</span>` : ""}
        <div class="cell-muted">${esc(signal)}${grade}${since ? ` · since ${esc(since)}` : ""}</div>`;
}

async function loadCustomers() {
    const routerId = document.getElementById("customers-filter-router").value;
    const rows = document.getElementById("customers-rows");
    const res = await fetch(`/customers${routerId ? `?router_id=${routerId}` : ""}`, { headers: authHeaders() });
    if (!res.ok) {
        rows.innerHTML = stateRow(9, "error", "Couldn't load customers. Try again in a moment.");
        return;
    }
    const customers = await res.json();
    customersCache = customers;
    const counts = {};
    customers.forEach(c => { counts[c.account_status] = (counts[c.account_status] || 0) + 1; });
    const tone = (s) => (s.toLowerCase() === "active" ? "good" : s.toLowerCase() === "expired" ? "bad" : "");
    document.getElementById("customers-summary").innerHTML =
        `<span class="summary-item"><strong>${customers.length}</strong> customers</span>` +
        Object.entries(counts).map(([s, n]) => `<span class="summary-item ${tone(s)}"><strong>${n}</strong> ${esc(s)}</span>`).join("");
    if (!customers.length) {
        rows.innerHTML = stateRow(9, "empty", "Import an export above to fill this list.", "No customers yet");
        return;
    }
    // The date on one line, the time small under it.
    const expiryCell = iso => {
        const text = formatDate(iso) || "";
        const cut = text.lastIndexOf(", ");
        return cut < 0 ? esc(text || "—") : `${esc(text.slice(0, cut))}<div class="cell-sub">${esc(text.slice(cut + 2))}</div>`;
    };
    rows.innerHTML = customers.map(c => `
        <tr>
            <td class="muted cust-router">${esc(c.router)}</td>
            <td class="cust-user">${esc(c.username)}</td>
            <td>${esc(c.name || "—")}</td>
            <td class="cust-nowrap">${esc(c.phone || "no valid number")}</td>
            <td class="cust-nowrap">${esc(c.plan || "—")}</td>
            <td class="cust-nowrap">${expiryCell(c.expiry)}</td>
            <td><span class="status-pill acct-${esc(c.account_status)}">${esc(c.account_status)}</span>${c.enabled ? "" : ` <span class="cell-sub">disabled</span>`}</td>
            <td class="cust-onu">${onuCell(c.onu)}</td>
            <td><button type="button" class="customer-edit-btn" data-id="${c.id}">Edit</button></td>
        </tr>`).join("");
    // ONU data older than 15 minutes is labelled, never shown as current.
    const stale = customers.some(c => c.onu?.stale);
    const note = document.getElementById("customers-onu-stale");
    note.hidden = !stale;
    note.textContent = stale ? "Fibre ONU status hasn't updated from SmartOLT for over 15 minutes; it may not be current." : "";
}

async function loadPage() {
    const res = await fetch("/customers/routers", { headers: authHeaders() });
    if (res.ok) routers = await res.json();
    const filter = document.getElementById("customers-filter-router");
    const keep = filter.value;
    document.getElementById("customers-import-router").innerHTML = routerOptions(false);
    filter.innerHTML = routerOptions(true);
    filter.value = keep;
    loadCustomers();
}

// ---- Add/Edit Customer form (full screen) ----
function isoDay(iso) {
    return iso ? new Date(iso).toLocaleDateString("en-CA", { timeZone: "Africa/Nairobi" }) : "";
}

async function openCustomerForm(customerId) {
    document.getElementById("customer-form").reset();
    document.getElementById("customer-form-msg").textContent = "";
    if (!routers.length) {
        const res = await fetch("/customers/routers", { headers: authHeaders() });
        if (res.ok) routers = await res.json();
    }
    const routerSelect = document.getElementById("customer-form-router");
    routerSelect.innerHTML = routerOptions(false);
    const username = document.getElementById("customer-form-username");

    editCustomerId = null;
    document.getElementById("customer-form-title").textContent = "Add Customer";
    routerSelect.disabled = username.disabled = false;
    if (!customerId) return true;

    if (!customersCache.length) {
        const res = await fetch("/customers", { headers: authHeaders() });
        if (res.ok) customersCache = await res.json();
    }
    const c = customersCache.find(x => String(x.id) === String(customerId));
    if (!c) return false;
    editCustomerId = c.id;
    document.getElementById("customer-form-title").textContent = "Edit Customer";
    // Router and username are the key an import matches on — never edited.
    routerSelect.value = routers.find(r => r.name === c.router)?.id ?? "";
    routerSelect.disabled = username.disabled = true;
    username.value = c.username;
    document.getElementById("customer-form-name").value = c.name || "";
    document.getElementById("customer-form-phone").value = c.phone || "";
    document.getElementById("customer-form-plan").value = c.plan || "";
    document.getElementById("customer-form-account").value = c.account_status;
    document.getElementById("customer-form-expiry").value = isoDay(c.expiry);
    document.getElementById("customer-form-disabled").checked = !c.enabled;
    return true;
}

async function saveCustomer(e) {
    e.preventDefault();
    const body = {
        name: document.getElementById("customer-form-name").value,
        phone: document.getElementById("customer-form-phone").value,
        plan: document.getElementById("customer-form-plan").value,
        account_status: document.getElementById("customer-form-account").value,
        enabled: !document.getElementById("customer-form-disabled").checked,
        expiry: document.getElementById("customer-form-expiry").value || null,
    };
    if (!editCustomerId) {
        body.router_id = Number(document.getElementById("customer-form-router").value);
        body.username = document.getElementById("customer-form-username").value;
    }
    const res = await fetch(editCustomerId ? `/customers/${editCustomerId}` : "/customers", {
        method: editCustomerId ? "PATCH" : "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showMessage("customer-form-msg", typeof err.detail === "string" ? err.detail : "Check the fields and try again", true);
        return;
    }
    customersCache = [];
    navigate("customers");
}

export function initCustomers() {
    document.getElementById("customers-import-check").addEventListener("click", () => runImport(false));
    document.getElementById("customers-import-commit").addEventListener("click", () => runImport(true));
    // A different file or router invalidates what was checked.
    ["customers-import-file", "customers-import-router"].forEach(id =>
        document.getElementById(id).addEventListener("change", () => {
            document.getElementById("customers-import-commit").hidden = true;
            document.getElementById("customers-import-result").innerHTML = "";
        }));
    document.getElementById("customers-filter-router").addEventListener("change", loadCustomers);

    document.getElementById("customer-add-open-btn").addEventListener("click", () => navigate("customers/new"));
    document.getElementById("customers-rows").addEventListener("click", (e) => {
        const btn = e.target.closest(".customer-edit-btn");
        if (btn) navigate(`customers/${btn.dataset.id}/edit`);
    });
    document.getElementById("customer-form-cancel").addEventListener("click", () => navigate("customers"));
    document.getElementById("customer-form").addEventListener("submit", saveCustomer);

    registerRoute("customers", async (params) => {
        if (params[0] === "new" || params[1] === "edit") {
            const found = await openCustomerForm(params[0] === "new" ? null : params[0]);
            if (found) showView("view-customer-form");
            else navigate("customers");
        } else {
            showView("view-customers");
            loadPage();
        }
    });
}
