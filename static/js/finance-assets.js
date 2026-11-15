import { authHeaders, showView, registerRoute, can, stateHtml, stateRow, showMessage } from "./common.js";

// Finance > Assets (Phase 7F). Inventory stays the equipment register; this
// page shows what each serialised item is worth and costs each month, from
// the money facts Finance keeps beside it (purchase date, useful life,
// salvage, warranty, segment, the capital expense that paid for it).
// An item without a purchase date has no book value: nothing is invented.

const SEGMENT_LABEL = { pppoe: "PPPoE", hotspot: "Hotspot", shared: "Shared" };
const LEVEL_LABEL = { bad: "Act now", warn: "Check", info: "Note" };
let data = null;
let openItem = null;
let openCategory = null;

function $(id) { return document.getElementById(id); }

function esc(value) {
    const d = document.createElement("div");
    d.textContent = value == null ? "" : String(value);
    return d.innerHTML;
}

function money(v) {
    if (v == null) return `<span class="muted">no data</span>`;
    return `KSh ${Number(v).toLocaleString("en-KE", { maximumFractionDigits: 0 })}`;
}

function dayLabel(iso) {
    if (!iso) return "—";
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

async function errorText(res, fallback) {
    if (!res) return "Couldn't reach the server. Check your connection and try again.";
    const body = await res.json().catch(() => ({}));
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]?.msg) return body.detail[0].msg;
    return fallback;
}

// ---- Loading ----

async function load() {
    const res = await fetch("/finance/assets", { headers: authHeaders() }).catch(() => null);
    if (!res || !res.ok) {
        $("fin-ast-top").innerHTML = stateHtml("error", await errorText(res, "Couldn't load assets. Try again in a moment."));
        $("fin-ast-rows").innerHTML = "";
        $("fin-ast-cat-rows").innerHTML = "";
        return;
    }
    data = await res.json();
    renderTop();
    renderCategories();
    fillFilters();
    renderRows();
}

// ---- Tiles and alerts ----

function tile(label, value, sub, cls = "") {
    return `<div class="fin-ast-tile ${cls}"><div class="fin-ast-tile-label">${label}</div>
        <div class="fin-ast-tile-value">${value}</div><div class="fin-ast-tile-sub">${sub}</div></div>`;
}

function renderTop() {
    const s = data.summary, m = s.this_month;
    const book = s.book_value == null ? money(null) : money(s.book_value);
    const tiles = `<div class="fin-ast-tiles">
        ${tile("Total cost", money(s.total_cost), `${s.count} items in Inventory`, "cost")}
        ${tile("Book value", book, s.valued === s.count ? "All items valued" : `${s.valued} of ${s.count} items have the dates to value`)}
        ${tile("This month's depreciation", m.counted ? money(m.total) : money(null),
               m.counted ? `${m.counted} item${m.counted === 1 ? "" : "s"}${m.left_out ? `, ${m.left_out} without dates left out` : ""}` : "Needs purchase dates")}
        ${tile("Deployed", s.deployed, "Status Active")}
        ${tile("In stock", s.in_stock, "Spare, in storage")}
        ${tile("Faulty / in repair", s.needs_repair, s.needs_repair ? "See below" : "None", s.needs_repair ? "warn" : "")}
        ${tile("Warranty ending", s.warranty_ending, "Within 60 days", s.warranty_ending ? "warn" : "")}
    </div>`;
    const alerts = data.alerts.length ? `<section class="fin-ast-card"><h2 class="section-title">Needs attention</h2><ul class="fin-ast-alerts">
        ${data.alerts.map(a => `<li class="fin-ast-alert ${a.level}"><span class="fin-ast-alert-level">${LEVEL_LABEL[a.level]}</span>
            <div><strong>${esc(a.title)}</strong><div class="fin-ast-alert-detail">${esc(a.detail)}</div>
            ${a.item_id ? `<button type="button" class="fin-ast-link" data-item="${a.item_id}">Open it</button>` : ""}</div></li>`).join("")}
    </ul></section>` : "";
    $("fin-ast-top").innerHTML = tiles + alerts;
}

// ---- Categories ----

function renderCategories() {
    const canManage = can("finance", "manage_assets");
    $("fin-ast-cat-manage-th").hidden = !canManage;
    const cols = canManage ? 7 : 6;
    if (!data.categories.length) {
        $("fin-ast-cat-rows").innerHTML = stateRow(cols, "empty", "Add serialised equipment in Inventory first.", "No equipment in Inventory");
        return;
    }
    $("fin-ast-cat-rows").innerHTML = data.categories.map(c => `<tr>
        <td>${esc(c.name)}</td><td class="num">${c.items}</td><td class="num">${money(c.cost)}</td>
        <td class="num">${c.life ? `${c.life.life_months} months` : `<span class="muted">not set</span>`}</td>
        <td class="num">${c.life ? `${Number(c.life.salvage_pct)}%` : "—"}</td>
        <td class="num ${c.dated < c.items ? "fin-ast-short" : ""}">${c.dated} of ${c.items}</td>
        ${canManage ? `<td><button type="button" class="btn-secondary fin-ast-small" data-category="${c.id}">Edit</button></td>` : ""}
    </tr>`).join("");
}

function openCategoryDialog(id) {
    openCategory = data.categories.find(c => c.id === id);
    if (!openCategory) return;
    const c = openCategory;
    $("fin-ast-cat-title").textContent = c.name;
    $("fin-ast-cat-life").value = c.life ? c.life.life_months : "";
    $("fin-ast-cat-salvage").value = c.life ? Number(c.life.salvage_pct) : 0;
    $("fin-ast-cat-date").value = "";
    const undated = c.items - c.dated;
    $("fin-ast-cat-hint").textContent = undated
        ? `${undated} item${undated === 1 ? " has" : "s have"} no purchase date. A date here fills only those; dates already set stay as they are.`
        : "Every item in this category already has a purchase date.";
    $("fin-ast-cat-msg").textContent = "";
    $("fin-ast-cat-overlay").hidden = false;
}

async function saveCategory(e) {
    e.preventDefault();
    const body = {
        life_months: Number($("fin-ast-cat-life").value),
        salvage_pct: Number($("fin-ast-cat-salvage").value || 0),
        purchased_on_for_undated: $("fin-ast-cat-date").value || null,
    };
    const res = await fetch(`/finance/assets/categories/${openCategory.id}`, {
        method: "PUT", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(body),
    }).catch(() => null);
    if (!res || !res.ok) {
        showMessage("fin-ast-cat-msg", await errorText(res, "Couldn't save."), true);
        return;
    }
    $("fin-ast-cat-overlay").hidden = true;
    load();
}

// ---- Register ----

function fillFilters() {
    const keep = (id) => $(id).value;
    const category = keep("fin-ast-f-category"), status = keep("fin-ast-f-status");
    $("fin-ast-f-category").innerHTML = `<option value="">All</option>` +
        data.categories.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
    const statuses = [...new Set(data.assets.map(a => a.status))].sort();
    $("fin-ast-f-status").innerHTML = `<option value="">All</option>` + statuses.map(s => `<option>${esc(s)}</option>`).join("");
    $("fin-ast-f-category").value = category;
    $("fin-ast-f-status").value = status;
}

function filtered() {
    const category = $("fin-ast-f-category").value, status = $("fin-ast-f-status").value;
    const segment = $("fin-ast-f-segment").value, q = $("fin-ast-f-search").value.trim().toLowerCase();
    return data.assets.filter(a =>
        (!category || String(a.category_id) === category) &&
        (!status || a.status === status) &&
        (!segment || (segment === "none" ? !a.segment : a.segment === segment)) &&
        (!q || [a.name, a.serial, a.location, a.make_model].some(v => (v || "").toLowerCase().includes(q))));
}

function renderRows() {
    const rows = filtered();
    const total = rows.reduce((sum, a) => sum + Number(a.terms.cost || 0), 0);
    $("fin-ast-count").textContent = `${rows.length} item${rows.length === 1 ? "" : "s"} · ${money(total).replace(/<[^>]+>/g, "")}`;
    if (!rows.length) {
        $("fin-ast-rows").innerHTML = stateRow(8, "empty", "Try another filter.", "No matching assets");
        return;
    }
    $("fin-ast-rows").innerHTML = rows.map(a => `<tr class="fin-ast-row" data-item="${a.id}" tabindex="0" title="Open">
        <td class="fin-ast-serial">${esc(a.serial || "—")}</td>
        <td>${esc(a.name)}${a.make_model ? `<div class="cell-sub">${esc(a.make_model)}</div>` : ""}</td>
        <td class="muted">${esc(a.category)}</td>
        <td class="muted">${esc(SEGMENT_LABEL[a.segment] || "—")}</td>
        <td>${a.location ? esc(a.location) : `<span class="muted">none</span>`}</td>
        <td class="num">${money(a.terms.cost)}</td>
        <td class="num">${a.book_value == null ? `<span class="muted" title="Missing: ${esc(a.terms.missing.join(", "))}">no data</span>` : money(a.book_value)}</td>
        <td><span class="fin-ast-status ${a.status.toLowerCase().replace(/[^a-z]+/g, "-")}">${esc(a.status)}</span>${a.warranty_ending ? `<div class="cell-sub fin-ast-warn">Warranty ends ${esc(dayLabel(a.warranty_until))}</div>` : ""}</td>
    </tr>`).join("");
}

// ---- One item ----

function fact(label, value) {
    return `<div><dt>${label}</dt><dd>${value}</dd></div>`;
}

async function openItemDialog(id) {
    openItem = data.assets.find(a => a.id === id);
    if (!openItem) return;
    const a = openItem, t = a.terms;
    $("fin-ast-item-title").textContent = `${a.name}${a.serial ? ` · ${a.serial}` : ""}`;
    $("fin-ast-item-facts").innerHTML = [
        fact("Category", esc(a.category)), fact("Status", esc(a.status)), fact("Location", esc(a.location || "none")),
        fact("Supplier", esc(a.supplier || "—")), fact("Cost", money(t.cost)),
        fact("Useful life", t.life_months ? `${t.life_months} months${a.life_override ? "" : " (category)"}` : "not set"),
        fact("Salvage", `${money(t.salvage)}${a.salvage_override ? "" : " (category)"}`),
        fact("Per month", a.monthly == null ? money(null) : money(a.monthly)),
        fact("Depreciated so far", a.depreciated == null ? money(null) : money(a.depreciated)),
        fact("Book value", a.book_value == null ? `<span class="muted">no data: needs ${esc(t.missing.join(", "))}</span>` : money(a.book_value)),
    ].join("");
    $("fin-ast-purchased").value = t.purchased_on || "";
    $("fin-ast-life").value = a.life_override ? t.life_months : "";
    $("fin-ast-salvage").value = a.salvage_override ? Number(t.salvage) : "";
    $("fin-ast-warranty").value = a.warranty_until || "";
    $("fin-ast-segment").value = a.segment || "";
    const options = [...data.capital_expenses];
    if (a.expense_id && !options.some(e => e.id === a.expense_id)) options.unshift({ id: a.expense_id, linked: true });
    $("fin-ast-expense").innerHTML = `<option value="">None</option>` + options.map(e => e.linked
        ? `<option value="${e.id}">Expense #${e.id} (linked)</option>`
        : `<option value="${e.id}">${esc(dayLabel(e.spent_on))} · ${esc(e.payee || e.description || "Cash")} · ${money(e.amount)}</option>`).join("");
    $("fin-ast-expense").value = a.expense_id || "";
    const canManage = can("finance", "manage_assets");
    $("fin-ast-item-form").querySelectorAll("input, select").forEach(el => { el.disabled = !canManage; });
    $("fin-ast-item-save").hidden = !canManage;
    $("fin-ast-item-msg").textContent = "";
    $("fin-ast-history").innerHTML = "";
    $("fin-ast-history-box").open = false;
    $("fin-ast-item-overlay").hidden = false;
}

async function loadHistory() {
    if (!openItem || $("fin-ast-history").innerHTML) return;
    $("fin-ast-history").innerHTML = stateHtml("loading", "Loading movements…");
    const res = await fetch(`/finance/assets/${openItem.id}/history`, { headers: authHeaders() }).catch(() => null);
    if (!res || !res.ok) { $("fin-ast-history").innerHTML = stateHtml("error", "Couldn't load the movements."); return; }
    const rows = await res.json();
    $("fin-ast-history").innerHTML = rows.length ? `<ul class="fin-ast-moves">${rows.map(r => `<li>
        <span class="muted">${esc(new Date(r.created_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }))}</span>
        ${esc(r.action)}${r.to_location_name ? ` → ${esc(r.to_location_name)}` : ""}${r.issued_to_name ? ` (${esc(r.issued_to_name)})` : ""}
        <span class="muted">by ${esc(r.logged_by_name || "—")}</span></li>`).join("")}</ul>` : `<p class="muted">No movements logged.</p>`;
}

async function saveItem(e) {
    e.preventDefault();
    const number = (id) => $(id).value === "" ? null : Number($(id).value);
    const body = {
        purchased_on: $("fin-ast-purchased").value || null,
        life_months: number("fin-ast-life"),
        salvage: number("fin-ast-salvage"),
        warranty_until: $("fin-ast-warranty").value || null,
        segment: $("fin-ast-segment").value || null,
        expense_id: number("fin-ast-expense"),
    };
    const res = await fetch(`/finance/assets/${openItem.id}`, {
        method: "PATCH", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(body),
    }).catch(() => null);
    if (!res || !res.ok) {
        showMessage("fin-ast-item-msg", await errorText(res, "Couldn't save."), true);
        return;
    }
    $("fin-ast-item-overlay").hidden = true;
    load();
}

// ---- Wiring ----

export function initFinanceAssets() {
    ["fin-ast-f-category", "fin-ast-f-status", "fin-ast-f-segment"].forEach(id => $(id).addEventListener("change", renderRows));
    $("fin-ast-f-search").addEventListener("input", renderRows);
    $("fin-ast-rows").addEventListener("click", (e) => {
        const row = e.target.closest("[data-item]");
        if (row) openItemDialog(Number(row.dataset.item));
    });
    $("fin-ast-rows").addEventListener("keydown", (e) => {
        const row = e.target.closest("[data-item]");
        if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openItemDialog(Number(row.dataset.item)); }
    });
    $("fin-ast-top").addEventListener("click", (e) => {
        const link = e.target.closest("[data-item]");
        if (link) openItemDialog(Number(link.dataset.item));
    });
    $("fin-ast-cat-rows").addEventListener("click", (e) => {
        const b = e.target.closest("[data-category]");
        if (b) openCategoryDialog(Number(b.dataset.category));
    });
    $("fin-ast-item-form").addEventListener("submit", saveItem);
    $("fin-ast-item-cancel").addEventListener("click", () => { $("fin-ast-item-overlay").hidden = true; });
    $("fin-ast-history-box").addEventListener("toggle", (e) => { if (e.target.open) loadHistory(); });
    $("fin-ast-cat-form").addEventListener("submit", saveCategory);
    $("fin-ast-cat-cancel").addEventListener("click", () => { $("fin-ast-cat-overlay").hidden = true; });

    registerRoute("finance-assets", () => {
        showView("view-finance-assets");
        load();
    });
}
