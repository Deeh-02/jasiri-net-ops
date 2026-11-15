import { authHeaders, showView, registerRoute, navigate, stateHtml } from "./common.js";

// Finance > Performance (Phase 7D): the monthly profit statement. Read only.
// Every line opens what made it: an income line goes to Income for that
// month and segment; an expense line unfolds into its expenses.
//
// What's missing is said, never shown as 0: PPPoE income isn't recorded yet,
// a month with no expenses recorded has no profit figure, and a month still
// running is a Draft with no change column.
//
// URL: #/finance-performance/<YYYY-MM>/<all|pppoe|hotspot>.

const SEGMENTS = ["all", "pppoe", "hotspot"];
const state = { month: null, segment: "all", report: null };

function $(id) { return document.getElementById(id); }

function esc(value) {
    const d = document.createElement("div");
    d.textContent = value == null ? "" : String(value);
    return d.innerHTML;
}

function money(v) {
    if (v == null) return `<span class="muted">no data</span>`;
    const n = Number(v);
    return `${n < 0 ? "−" : ""}KSh ${Math.abs(n).toLocaleString("en-KE", { maximumFractionDigits: 0 })}`;
}

function shortMoney(v) {
    const a = Math.abs(v), sign = v < 0 ? "−" : "";
    if (a >= 1e6) return `${sign}${(a / 1e6).toFixed(a % 1e6 ? 1 : 0)}M`;
    if (a >= 1e3) return `${sign}${(a / 1e3).toFixed(a % 1e3 ? 1 : 0)}k`;
    return `${sign}${Math.round(a)}`;
}

function asDay(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d || 1);
}

function monthName(iso, long = true) {
    return asDay(iso).toLocaleDateString("en-GB", long ? { month: "long", year: "numeric" } : { month: "short" });
}

function dayName(iso) {
    return asDay(iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

function nairobiMonth() {
    return new Date().toLocaleDateString("en-CA", { timeZone: "Africa/Nairobi" }).slice(0, 7);
}

function changeHtml(c) {
    if (!c) return "";
    const n = Number(c.amount);
    if (n === 0) return `<span class="muted">no change</span>`;
    const pct = c.pct == null ? "" : ` (${Math.abs(Number(c.pct))}%)`;
    return `<span class="fin-perf-change ${n > 0 ? "up" : "down"}">${n > 0 ? "▲" : "▼"} ${money(Math.abs(n))}${pct}</span>`;
}

// ---- Loading ----

async function load() {
    $("fin-perf-body").innerHTML = stateHtml("loading", "Loading the statement…");
    const asked = `${state.month}/${state.segment}`;
    const res = await fetch(`/finance/performance?month=${state.month}&segment=${state.segment}`, { headers: authHeaders() }).catch(() => null);
    if (asked !== `${state.month}/${state.segment}`) return;
    if (!res || !res.ok) {
        const body = res ? await res.json().catch(() => ({})) : {};
        $("fin-perf-body").innerHTML = stateHtml("error", typeof body.detail === "string" ? body.detail : "Couldn't load the statement. Try again in a moment.");
        return;
    }
    state.report = await res.json();
    render();
}

function render() {
    const r = state.report;
    $("fin-perf-status").innerHTML = r.not_started ? "" : r.is_draft
        ? `<span class="fin-perf-badge draft" title="This month is still running: figures will change">Draft</span>`
        : `<span class="fin-perf-badge" title="Closing a month to lock it comes in Phase 7G">Not closed yet</span>`;
    if (r.not_started) {
        $("fin-perf-body").innerHTML = stateHtml("empty", "Pick this month or an earlier one.", "This month hasn't started yet");
        return;
    }
    $("fin-perf-body").innerHTML = [
        basisHtml(r), tilesHtml(r), r.trend ? trendHtml(r.trend) : "", statementHtml(r),
        r.where ? whereHtml(r.where) : "", notesHtml(r),
    ].join("");
    drawChart();
}

// ---- Basis and caveats ----

function basisHtml(r) {
    const t = r.this, warnings = [];
    if (t.income_incomplete) warnings.push("PPPoE income isn't recorded yet, so income and profit here are understated.");
    if (!t.expenses_recorded) warnings.push(`No expenses are recorded for ${esc(monthName(r.month))}, so there is no profit figure. Add them on the Expenses page.`);
    else warnings.push(`These figures rest on the ${t.expenses_recorded} expense${t.expenses_recorded === 1 ? "" : "s"} recorded for ${esc(monthName(r.month))}. Anything not entered isn't counted.`);
    if (r.segment !== "all") warnings.push(`Only direct costs tagged ${r.segment === "pppoe" ? "PPPoE" : "Hotspot"} count here; tag an expense's segment by editing it on the Expenses page. Shared costs (upstream, salaries, rent) aren't split between segments.`);
    return `<div class="fin-perf-basis">
        <p>${esc(r.basis)}</p>
        <ul>${warnings.map(w => `<li>${w}</li>`).join("")}</ul>
    </div>`;
}

// ---- KPI tiles ----

function tile(label, value, sub, cls = "") {
    return `<div class="fin-perf-tile ${cls}"><div class="fin-perf-tile-label">${label}</div>
        <div class="fin-perf-tile-value">${value}</div><div class="fin-perf-tile-sub">${sub}</div></div>`;
}

function tilesHtml(r) {
    const t = r.this;
    const star = t.income_incomplete ? " (hotspot only)" : "";
    if (r.segment !== "all") {
        return `<div class="fin-perf-tiles">
            ${tile("Income", money(t.income_total), r.segment === "pppoe" ? "Not recorded yet" : "Hotspot sales")}
            ${tile("Direct costs", money(t.direct_total), "Tagged to this segment")}
            ${tile("Before shared costs", money(t.before_shared_costs), t.gross_margin == null ? "—" : `${t.gross_margin}% of income`,
                   t.before_shared_costs == null ? "" : Number(t.before_shared_costs) >= 0 ? "up" : "down")}
        </div>`;
    }
    const net = t.net_profit;
    let breakEven = tile("Break-even", "—", "No costs recorded yet");
    if (r.break_even?.day) breakEven = tile("Break-even", esc(dayName(r.break_even.day)), "Income passed this month's costs", "up");
    else if (r.break_even) breakEven = tile("Break-even", "Not yet", `${money(r.break_even.to_go)} to go`, "down");
    return `<div class="fin-perf-tiles">
        ${tile("Net profit", money(net), net == null ? "Needs this month's expenses" : `after all recorded costs${star}`,
               net == null ? "" : Number(net) >= 0 ? "up" : "down")}
        ${tile("Margin", t.net_margin == null ? "—" : `${t.net_margin}%`, "Net profit as a share of income")}
        ${tile("Income", money(t.income_total), t.income_incomplete ? "Hotspot only: PPPoE not recorded" : "All segments", "income")}
        ${tile("Expenses", money(t.expenses_total), "Operating; equipment bought to keep is left out")}
        ${breakEven}
    </div>`;
}

// ---- 12-month trend: income and expenses as bars, profit as a line ----

function niceStep(range) {
    const raw = range / 4, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * mag >= raw) return m * mag;
    return 10 * mag;
}

function trendSvg(points, width) {
    const W = Math.max(300, Math.min(1000, Math.round(width || 720))), H = W < 520 ? 220 : 260;
    const L = 46, R = 8, T = 12, B = 26;
    const values = points.flatMap(p => [p.income, p.expenses, p.profit]).filter(v => v != null).map(Number);
    const step = niceStep(Math.max(1, ...values) - Math.min(0, ...values));
    const top = Math.ceil(Math.max(1, ...values) / step) * step;
    const bottom = Math.floor(Math.min(0, ...values) / step) * step;
    const y = v => T + (top - v) / (top - bottom) * (H - T - B);
    const grid = [];
    for (let v = bottom; v <= top + 1e-9; v += step) {
        grid.push(`<line class="${v === 0 ? "base" : "grid"}" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>
            <text x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${shortMoney(v)}</text>`);
    }
    const slot = (W - L - R) / points.length, bw = Math.max(3, Math.min(22, slot * 0.3));
    const bar = (cx, v, cls, partial) => {
        if (v == null) return "";
        const top0 = Math.min(y(v), y(0)), h = Math.abs(y(v) - y(0));
        return h ? `<rect class="bar ${cls}${partial ? " partial" : ""}" x="${cx}" y="${top0}" width="${bw}" height="${h}" rx="2"/>` : "";
    };
    const slots = points.map((p, i) => {
        const cx = L + slot * i + slot / 2;
        const tip = `${monthName(p.month)}${p.partial ? " (so far)" : ""}: income ${p.income == null ? "no data" : "KSh " + Number(p.income).toLocaleString()}` +
            `, expenses ${p.expenses == null ? "not recorded" : "KSh " + Number(p.expenses).toLocaleString()}` +
            `, profit ${p.profit == null ? "—" : (Number(p.profit) < 0 ? "−" : "") + "KSh " + Math.abs(Number(p.profit)).toLocaleString()}`;
        return `<g class="slot"><title>${esc(tip)}</title><rect class="hit" x="${L + slot * i}" y="${T}" width="${slot}" height="${H - T - B}"/>
            ${bar(cx - bw - 1, p.income, "income", p.partial)}${bar(cx + 1, p.expenses, "expenses", p.partial)}</g>
            <text x="${cx}" y="${H - 8}" text-anchor="middle">${esc(monthName(p.month, false))}</text>`;
    }).join("");
    const known = points.map((p, i) => p.profit == null ? null : [L + slot * i + slot / 2, y(Number(p.profit))]);
    const runs = [];
    let run = [];
    known.forEach(pt => { if (!pt) { if (run.length) runs.push(run); run = []; } else run.push(pt); });
    if (run.length) runs.push(run);
    const line = runs.map(r => (r.length > 1 ? `<polyline class="profit" points="${r.map(p => p.join(",")).join(" ")}"/>` : "") +
        r.map(p => `<circle class="profit-dot" cx="${p[0]}" cy="${p[1]}" r="4"/>`).join("")).join("");
    return `<svg class="fin-perf-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Income, expenses and profit by month">${grid.join("")}${slots}${line}</svg>`;
}

function trendHtml(points) {
    const rows = points.map(p => `<tr><td>${esc(monthName(p.month))}${p.partial ? " (so far)" : ""}</td>
        <td class="num">${money(p.income)}</td><td class="num">${p.expenses == null ? `<span class="muted">not recorded</span>` : money(p.expenses)}</td>
        <td class="num">${p.profit == null ? "—" : money(p.profit)}</td></tr>`).join("");
    return `<section class="fin-perf-card">
        <h2 class="section-title">Last 12 months</h2>
        <div class="fin-perf-legend">
            <span><i class="fin-perf-key income"></i>Income</span>
            <span><i class="fin-perf-key expenses"></i>Expenses (operating)</span>
            <span><i class="fin-perf-key profit"></i>Profit before depreciation</span>
            <span class="muted">A month with no expenses recorded has no expense bar or profit point.</span>
        </div>
        <div id="fin-perf-chart"></div>
        <details class="fin-perf-table-view"><summary>Show as a table</summary>
            <div class="table-scroll table-compact"><table class="table-dense">
                <thead><tr><th>Month</th><th class="num">Income</th><th class="num">Expenses</th><th class="num">Profit</th></tr></thead>
                <tbody>${rows}</tbody></table></div></details>
    </section>`;
}

function drawChart() {
    const box = $("fin-perf-chart");
    if (box && state.report?.trend) box.innerHTML = trendSvg(state.report.trend, box.clientWidth);
}

// ---- The statement ----

function statementHtml(r) {
    const head = `<thead><tr><th>${r.segment === "all" ? "Statement" : r.segment === "pppoe" ? "PPPoE" : "Hotspot"}</th>
        <th class="num">${esc(monthName(r.month))}</th><th class="num">${esc(monthName(r.previous_month))}</th>
        <th class="num">${r.is_draft ? "Change (when the month ends)" : "Change"}</th></tr></thead>`;
    let index = 0;
    const body = r.rows.map(row => {
        if (row.type === "section") return `<tr class="fin-perf-section"><th colspan="4">${esc(row.label)}</th></tr>`;
        const id = `fin-perf-items-${index++}`;
        const hasItems = row.items && row.items.length;
        let label = esc(row.label);
        if (row.link) label = `<a href="#/finance-income/${r.month.slice(0, 7)}/${row.link === "other" ? "all" : row.link}">${label}</a>`;
        if (hasItems) label = `<button type="button" class="fin-perf-open" aria-expanded="false" aria-controls="${id}">${label} <span class="muted">· ${row.items.length}</span></button>`;
        if (row.note) label += `<div class="cell-sub">${esc(row.note)}</div>`;
        const margin = row.margin != null ? `<div class="cell-sub">${row.margin}% margin</div>` : "";
        const flag = row.incomplete ? ` <span class="fin-perf-flag" title="PPPoE income isn't recorded yet">hotspot only</span>` : "";
        const main = `<tr class="fin-perf-${row.type}"><td>${label}${flag}</td>
            <td class="num">${money(row.this)}${margin}</td><td class="num">${money(row.last)}</td><td class="num">${changeHtml(row.change)}</td></tr>`;
        if (!hasItems) return main;
        const items = row.items.map(e => `<tr class="fin-perf-item" data-items="${id}" hidden>
            <td>${esc(dayName(e.spent_on))} · ${esc(e.payee || e.description || "Cash")}${e.reference ? ` <span class="fin-perf-code">${esc(e.reference)}</span>` : ""}</td>
            <td class="num">${money(e.amount)}</td><td></td><td></td></tr>`).join("");
        return main + items;
    }).join("");
    return `<section class="fin-perf-card">
        <h2 class="section-title">Statement</h2>
        <div class="table-scroll table-compact"><table class="fin-perf-statement">${head}<tbody>${body}</tbody></table></div>
    </section>`;
}

// ---- Where the money went ----

function whereHtml(w) {
    if (!w.categories.length) return "";
    const rows = w.categories.map(c => `<div class="fin-perf-row">
        <span class="fin-perf-row-name">${esc(c.category)}</span>
        <span class="fin-perf-meter"><span style="width:${c.share_pct ?? 0}%"></span></span>
        <span class="fin-perf-row-value">${money(c.total)}</span>
        <span class="fin-perf-row-n muted">${c.share_pct}%</span></div>`).join("");
    const movers = w.biggest_changes.length ? `<p class="fin-perf-card-note">Biggest changes from last month: ${w.biggest_changes
        .map(m => `${esc(m.category)} ${changeHtml(m.change)}`).join(" · ")}</p>` : "";
    return `<section class="fin-perf-card"><h2 class="section-title">Where the money went</h2>
        <p class="fin-perf-card-note">This month's operating expenses by category.</p>${rows}${movers}</section>`;
}

// ---- Notes ----

function notesHtml(r) {
    const n = r.notes, items = [];
    if (n.capital.rows.length) {
        items.push(`<li><strong>Equipment bought to keep: ${money(n.capital.total)}</strong>, left out of profit above. It counts later as depreciation, once Assets is built (Phase 7F).
            <ul>${n.capital.rows.map(e => `<li>${esc(dayName(e.spent_on))} · ${esc(e.payee || e.description || "Cash")} · ${money(e.amount)}</li>`).join("")}</ul></li>`);
    }
    if (n.uncategorised.count) {
        items.push(`<li>${n.uncategorised.count} expense${n.uncategorised.count === 1 ? " is" : "s are"} in "Other" (${money(n.uncategorised.total)}). Give ${n.uncategorised.count === 1 ? "it" : "them"} a category on the <a href="#/finance-expenses/${r.month.slice(0, 7)}">Expenses page</a> so the statement is accurate.</li>`);
    }
    if (n.no_direct_costs.length) {
        items.push(`<li>Nothing recorded for ${n.no_direct_costs.map(esc).join(" or ")} this month. If it was paid, add it on the <a href="#/finance-expenses/${r.month.slice(0, 7)}">Expenses page</a>.</li>`);
    }
    if (!items.length) return "";
    return `<section class="fin-perf-card"><h2 class="section-title">Notes</h2><ul class="fin-perf-notes">${items.join("")}</ul></section>`;
}

// ---- Wiring ----

function go(month, segment) {
    navigate(`finance-performance/${month}/${segment}`, { replace: true });
}

export function initFinancePerformance() {
    $("fin-perf-segment").addEventListener("click", (e) => {
        const b = e.target.closest("[data-segment]");
        if (b) go(state.month, b.dataset.segment);
    });
    $("fin-perf-month").addEventListener("change", (e) => { if (e.target.value) go(e.target.value, state.segment); });

    $("fin-perf-body").addEventListener("click", (e) => {
        const open = e.target.closest(".fin-perf-open");
        if (!open) return;
        const expanded = open.getAttribute("aria-expanded") !== "true";
        open.setAttribute("aria-expanded", String(expanded));
        document.querySelectorAll(`[data-items="${open.getAttribute("aria-controls")}"]`).forEach(row => { row.hidden = !expanded; });
    });

    // The chart is drawn at the width it's shown at.
    let resizeTimer = null;
    window.addEventListener("resize", () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => { if (!$("view-finance-performance").hidden) drawChart(); }, 150);
    });

    registerRoute("finance-performance", (params) => {
        state.month = /^\d{4}-\d{2}$/.test(params[0] || "") ? params[0] : nairobiMonth();
        state.segment = SEGMENTS.includes(params[1]) ? params[1] : "all";
        $("fin-perf-month").value = state.month;
        $("fin-perf-segment").querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.segment === state.segment)));
        showView("view-finance-performance");
        load();
    });
}
