import { authHeaders, showView, registerRoute, can, stateHtml, stateRow, showMessage, skeletonCards } from "./common.js";
import {
    esc, money, moneyOrNone, errorText, fetchReport, forgetReports, wireTips, tipAttr, markFresh,
    kpi, card, alertList, ranked, splitBar,
} from "./finance-common.js";

// Finance > Assets (Phase 7F). Inventory stays the equipment register; this
// page shows what the serialised equipment cost, where it stands and when
// warranties run out, from the money facts Finance keeps beside it (purchase
// date, warranty, segment, the capital expense that paid for it). No book
// value or depreciation (owner's call, 2026-11-16): the purchase date is
// optional and only gives an item's age.
//
// Top to bottom: the headline figures, what needs attention, cost by
// category beside where the equipment stands, then the categories (with a
// way to date a whole category at once) and the register. Tiles, cards and bars are Finance's shared ones
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

// "3 months", "2 yr 4 mo": how long ago an item was bought.
function ageText(months) {
    if (months == null) return null;
    if (months < 1) return "under a month";
    if (months < 12) return `${months} month${months === 1 ? "" : "s"}`;
    const years = Math.floor(months / 12), rest = months % 12;
    return `${years} yr${rest ? ` ${rest} mo` : ""}`;
}

function tilesHtml(s) {
    return `<div class="fin-kpis">
        ${kpi({ label: "Total cost", value: money(s.total_cost), sub: `What the ${s.count} serialised items in Inventory cost` })}
        ${kpi({ label: "Deployed", value: String(s.deployed), sub: `In use. ${s.in_stock} more in stock` })}
        ${kpi({ label: "Faulty or in repair", value: String(s.needs_repair), flag: s.needs_repair ? ["Needs a look", "warn"] : ["None", "good"],
                sub: s.needs_repair ? "See Needs attention below" : "Everything is working" })}
        ${kpi({ label: "Warranty ending", value: String(s.warranty_ending), flag: s.warranty_ending ? ["Within 60 days", "warn"] : null,
                sub: s.warranty_ending ? "Claim before it runs out" : "None within 60 days" })}
    </div>`;
}

// Cost per category, largest first; a bar opens that category in the register.
function categoryCard() {
    const rows = [...data.categories].sort((a, b) => Number(b.cost) - Number(a.cost)).map(c => ({
        label: c.name, sub: String(c.items), amount: Number(c.cost), value: money(c.cost),
        pick: `data-open-category="${c.id}" title="Show these in the register"`,
        tip: tipAttr(c.name, [{ key: "cost", label: "Cost", value: money(c.cost) }, { label: "Items", value: String(c.items) }],
                     "Click to see them in the register"),
    }));
    return card({ span: 7, title: "Cost by category", sub: "What each kind of equipment cost, with how many items. Click one to see them.",
                  body: ranked(rows, { key: "cost", empty: "Add serialised equipment in Inventory first." }) });
}

// Where the equipment stands, by Inventory's own status.
function statusCard() {
    const counts = {};
    for (const a of data.assets) counts[a.status] = (counts[a.status] || 0) + 1;
    const parts = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([status, n]) => ({
        key: STATUS_KEY[status] || "info", label: status, amount: n, value: `${n} item${n === 1 ? "" : "s"}`,
    }));
    return card({ span: 5, title: "Where it stands", sub: "By Inventory status.",
                  body: parts.length ? splitBar(parts, "Assets by status") : `<p class="fin-empty">No equipment yet.</p>` });
}

function renderTop() {
    const s = data.summary;
    const alerts = data.alerts.length ? card({ title: "Needs attention", sub: "Worst first.", body: alertList(data.alerts.map(a => ({
        level: a.level, title: a.title, detail: a.detail, pick: a.item_id ? `data-item="${a.item_id}"` : "", link: "Open it",
    }))) }) : "";
    $("fin-ast-top").innerHTML = `${tilesHtml(s)}${alerts}<div class="fin-grid">${categoryCard()}${statusCard()}</div>`;
}

// ---- Categories ----

function renderCategories() {
    const canManage = can("finance", "manage_assets");
    $("fin-ast-cat-manage-th").hidden = !canManage;
    const cols = canManage ? 5 : 4;
    if (!data.categories.length) {
        $("fin-ast-cat-rows").innerHTML = stateRow(cols, "empty", "Add serialised equipment in Inventory first.", "No equipment in Inventory");
        return;
    }
    $("fin-ast-cat-rows").innerHTML = data.categories.map(c => `<tr>
        <td>${esc(c.name)}</td><td class="num">${c.items}</td><td class="num">${moneyOrNone(c.cost)}</td>
        <td class="num muted">${c.dated} of ${c.items}</td>
        ${canManage ? `<td>${c.dated < c.items ? `<button type="button" class="btn-secondary fin-ast-small" data-category="${c.id}">Set date</button>` : ""}</td>` : ""}
    </tr>`).join("");
}

// One date for every item in a category that has none yet (optional: it
// only gives the items' age).
function openCategoryDialog(id) {
    openCategory = data.categories.find(c => c.id === id);
    if (!openCategory) return;
    const c = openCategory, undated = c.items - c.dated;
    $("fin-ast-cat-title").textContent = c.name;
    $("fin-ast-cat-date").value = "";
    $("fin-ast-cat-hint").textContent = `${undated} item${undated === 1 ? " has" : "s have"} no purchase date. This date fills only those; dates already set stay as they are.`;
    $("fin-ast-cat-msg").textContent = "";
    $("fin-ast-cat-overlay").hidden = false;
    $("fin-ast-cat-date").focus();
}

async function saveCategory(e) {
    e.preventDefault();
    const res = await fetch(`/finance/assets/categories/${openCategory.id}/purchase-date`, {
        method: "PUT", headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ purchased_on: $("fin-ast-cat-date").value }),
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
    const total = rows.reduce((sum, a) => sum + Number(a.cost || 0), 0);
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
        <td class="num">${moneyOrNone(a.cost)}</td>
        <td class="muted">${a.purchased_on ? `${esc(dayLabel(a.purchased_on))}<div class="cell-sub">${esc(ageText(a.age_months))} ago</div>` : "—"}</td>
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
    const a = openItem;
    $("fin-ast-item-title").textContent = `${a.name}${a.serial ? ` · ${a.serial}` : ""}`;
    $("fin-ast-item-facts").innerHTML = [
        fact("Category", esc(a.category)), fact("Status", esc(a.status)), fact("Location", esc(a.location || "none")),
        fact("Supplier", esc(a.supplier || "—")), fact("Cost", moneyOrNone(a.cost)),
        fact("Age", a.age_months == null ? `<span class="muted">no purchase date</span>` : esc(ageText(a.age_months))),
    ].join("");
    $("fin-ast-purchased").value = a.purchased_on || "";
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
