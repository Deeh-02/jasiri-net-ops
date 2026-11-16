import { authHeaders, showView, registerRoute, can, stateHtml, stateRow, showMessage, skeletonCards } from "./common.js";
import {
    esc, money, moneyOrNone, errorText, fetchReport, forgetReports, wireTips, tipAttr, markFresh,
    kpi, card, alertList, ranked, splitBar, meter,
} from "./finance-common.js";

// Finance > Assets (Phase 7F). Inventory stays the equipment register; this
// page shows what each serialised item is worth and costs each month, from
// the money facts Finance keeps beside it (purchase date, useful life,
// salvage, warranty, segment, the capital expense that paid for it).
// An item without a purchase date has no book value: nothing is invented.
//
// Top to bottom: the headline figures, what needs attention, cost by
// category beside where the equipment stands, then the category defaults and
// the register. Tiles, cards and bars are Finance's shared ones
// (finance-common.js).

const SEGMENT_LABEL = { pppoe: "PPPoE", hotspot: "Hotspot", shared: "Shared" };
// The colour each Inventory status takes in the status bar.
const STATUS_KEY = { "Active": "good", "Faulty": "bad", "In Repair": "warn", "Decommissioned": "idle" };
let data = null;
let openItem = null;
let openCategory = null;

function $(id) { return document.getElementById(id); }

function dayLabel(iso) {
    if (!iso) return "—";
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

// ---- Loading ----

async function load() {
    if (!data) $("fin-ast-top").innerHTML = `<div class="fin-kpis">${skeletonCards(5)}</div>`;
    const result = await fetchReport("/finance/assets");
    if (!result.ok) {
        $("fin-ast-top").innerHTML = stateHtml("error", result.error);
        $("fin-ast-rows").innerHTML = "";
        $("fin-ast-cat-rows").innerHTML = "";
        return;
    }
    data = result.data;
    renderTop();
    markFresh($("fin-ast-top"));
    renderCategories();
    fillFilters();
    renderRows();
}

// After a save: the figures changed, so nothing remembered may be reused.
function reload() {
    forgetReports();
    load();
}

// ---- Headline figures, alerts, and the two charts ----

function tilesHtml(s) {
    const m = s.this_month;
    return `<div class="fin-kpis">
        ${kpi({ label: "Total cost", value: money(s.total_cost), sub: `${s.count} serialised items in Inventory` })}
        ${kpi({ label: "Worth today", value: s.book_value == null ? null : money(s.book_value),
                sub: s.valued === s.count ? "Book value of every item" : `Book value. ${s.valued} of ${s.count} items have the dates to value` })}
        ${kpi({ label: "Depreciation this month", value: m.counted ? money(m.total) : null,
                sub: m.counted ? `${m.counted} item${m.counted === 1 ? "" : "s"}${m.left_out ? `; ${m.left_out} without dates left out` : ""}` : "Needs purchase dates" })}
        ${kpi({ label: "Faulty or in repair", value: String(s.needs_repair), flag: s.needs_repair ? ["Needs a look", "warn"] : ["None", "good"],
                sub: `${s.deployed} deployed, ${s.in_stock} in stock` })}
        ${kpi({ label: "Warranty ending", value: String(s.warranty_ending), flag: s.warranty_ending ? ["Within 60 days", "warn"] : null,
                sub: s.warranty_ending ? "Claim before it runs out" : "None within 60 days" })}
    </div>`;
}

// Cost per category, largest first; a bar opens that category in the register.
function categoryCard() {
    const worth = {};   // category id -> { value, valued }
    for (const a of data.assets) {
        const c = worth[a.category_id] || (worth[a.category_id] = { value: 0, valued: 0 });
        if (a.book_value != null) { c.value += Number(a.book_value); c.valued += 1; }
    }
    const rows = [...data.categories].sort((a, b) => Number(b.cost) - Number(a.cost)).map(c => {
        const w = worth[c.id] || { value: 0, valued: 0 };
        return {
            label: c.name, sub: String(c.items), amount: Number(c.cost), value: money(c.cost),
            pick: `data-open-category="${c.id}" title="Show these in the register"`,
            tip: tipAttr(c.name, [
                { key: "cost", label: "Cost", value: money(c.cost) },
                { label: "Worth today", value: w.valued === c.items && c.items ? money(w.value) : w.valued ? `${money(w.value)} (${w.valued} of ${c.items} valued)` : "no data" },
                { label: "Items", value: String(c.items) },
                { label: "Useful life", value: c.life ? `${c.life.life_months} months` : "not set" },
            ], "Click to see them in the register"),
        };
    });
    return card({ span: 7, title: "Cost by category", sub: "What each kind of equipment cost, with how many items. Click one to see them.",
                  body: ranked(rows, { key: "cost", empty: "Add serialised equipment in Inventory first." }) });
}

// Where the equipment stands (Inventory's status), and how much of it has
// the purchase date that book value needs.
function statusCard(s) {
    const counts = {};
    for (const a of data.assets) counts[a.status] = (counts[a.status] || 0) + 1;
    const parts = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([status, n]) => ({
        key: STATUS_KEY[status] || "info", label: status, amount: n, value: `${n} item${n === 1 ? "" : "s"}`,
    }));
    return card({ span: 5, title: "Where it stands", sub: "By Inventory status.", body: `
        ${parts.length ? splitBar(parts, "Assets by status") : `<p class="fin-empty">No equipment yet.</p>`}
        <div class="fin-meter-row"><div><span>Items with a purchase date</span><b>${s.valued} of ${s.count}</b></div>
            ${meter(s.count ? s.valued / s.count : 0, s.valued === s.count ? "good" : "warn")}</div>
        <p class="fin-card-note">${s.valued === s.count ? "Every item can be valued." : "An item is valued only once it has a purchase date. Set one for a whole category under Categories."}</p>` });
}

function renderTop() {
    const s = data.summary;
    const alerts = data.alerts.length ? card({ title: "Needs attention", sub: "Worst first.", body: alertList(data.alerts.map(a => ({
        level: a.level, title: a.title, detail: a.detail, pick: a.item_id ? `data-item="${a.item_id}"` : "", link: "Open it",
    }))) }) : "";
    $("fin-ast-top").innerHTML = `${tilesHtml(s)}${alerts}<div class="fin-grid">${categoryCard()}${statusCard(s)}</div>`;
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
        <td>${esc(c.name)}</td><td class="num">${c.items}</td><td class="num">${moneyOrNone(c.cost)}</td>
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
    reload();
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
    $("fin-ast-count").textContent = `${rows.length} item${rows.length === 1 ? "" : "s"} · ${money(total)}`;
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
        <td class="num">${moneyOrNone(a.terms.cost)}</td>
        <td class="num">${a.book_value == null ? `<span class="muted" title="Missing: ${esc(a.terms.missing.join(", "))}">no data</span>` : moneyOrNone(a.book_value)}</td>
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
        fact("Supplier", esc(a.supplier || "—")), fact("Cost", moneyOrNone(t.cost)),
        fact("Useful life", t.life_months ? `${t.life_months} months${a.life_override ? "" : " (category)"}` : "not set"),
        fact("Salvage", `${moneyOrNone(t.salvage)}${a.salvage_override ? "" : " (category)"}`),
        fact("Per month", a.monthly == null ? moneyOrNone(null) : moneyOrNone(a.monthly)),
        fact("Depreciated so far", a.depreciated == null ? moneyOrNone(null) : moneyOrNone(a.depreciated)),
        fact("Book value", a.book_value == null ? `<span class="muted">no data: needs ${esc(t.missing.join(", "))}</span>` : moneyOrNone(a.book_value)),
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
        : `<option value="${e.id}">${esc(dayLabel(e.spent_on))} · ${esc(e.payee || e.description || "Cash")} · ${moneyOrNone(e.amount)}</option>`).join("");
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
    reload();
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
        if (link) { openItemDialog(Number(link.dataset.item)); return; }
        // A category's bar narrows the register to that category.
        const category = e.target.closest("[data-open-category]");
        if (!category) return;
        $("fin-ast-f-category").value = category.dataset.openCategory;
        renderRows();
        $("fin-ast-register").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    wireTips($("fin-ast-top"));
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
