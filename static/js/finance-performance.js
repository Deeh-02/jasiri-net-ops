import { showView, registerRoute, navigate, stateHtml, skeletonCards } from "./common.js";
import {
    esc, money, moneyOrNone, dayLabel, monthLabel, nairobiMonth, fetchReport, wireTips, tipAttr, markFresh,
    kpi, card, legend, ranked, bridge, monthsChart, redrawOnResize, changeBetween, monthPoints, monthsTable, profitSteps, incomeGap,
} from "./finance-common.js";

// Finance > Performance (Phase 7D): the monthly profit statement. Read only.
// Every line opens what made it: an income line goes to Income for that
// month and segment; an expense line unfolds into its expenses.
//
// What's missing is said, never shown as 0: a segment's income not recorded
// for the whole month (PPPoE recharges start 20 Jun 2026, hotspot sales 21
// Sep 2026) marks every total that needs it, a month with no expenses recorded has no profit figure, and a month still
// running is a Draft with no change column.
//
// Below Net profit the statement takes off the equipment bought, leaving the
// Cash left: equipment isn't a running cost, so it stays out of profit, but it
// is money that left the account. No depreciation (owner's call, 2026-11-16).
//
// Top to bottom: the headline figures, the steps from income to cash left
// beside where the money went, twelve months of income, expenses and profit,
// then the statement itself. The tiles, cards and charts are Finance's
// shared ones (finance-common.js).
//
// URL: #/finance-performance/<YYYY-MM>/<all|pppoe|hotspot>.

const SEGMENTS = ["all", "pppoe", "hotspot"];
const state = { month: null, segment: "all", report: null, points: null };

function $(id) { return document.getElementById(id); }

const monthName = iso => monthLabel(iso, true);
const dayName = iso => dayLabel(iso);

function changeHtml(c) {
    if (!c) return "";
    const n = Number(c.amount);
    if (n === 0) return `<span class="muted">no change</span>`;
    const pct = c.pct == null ? "" : ` (${Math.abs(Number(c.pct))}%)`;
    return `<span class="fin-perf-change ${n > 0 ? "up" : "down"}">${n > 0 ? "▲" : "▼"} ${money(Math.abs(n))}${pct}</span>`;
}

// ---- Loading ----

async function load() {
    $("fin-perf-body").innerHTML = `<div class="fin-kpis">${skeletonCards(5)}</div>${stateHtml("loading", "Loading the statement…")}`;
    const asked = `${state.month}/${state.segment}`;
    const result = await fetchReport(`/finance/performance?month=${state.month}&segment=${state.segment}`);
    if (asked !== `${state.month}/${state.segment}`) return;
    if (!result.ok) {
        $("fin-perf-body").innerHTML = stateHtml("error", result.error);
        return;
    }
    state.report = result.data;
    render();
    markFresh($("fin-perf-body"));
}

function render() {
    const r = state.report;
    $("fin-perf-status").innerHTML = r.not_started ? "" : r.is_draft
        ? `<span class="fin-badge draft" title="This month is still running: figures will change"><i class="fin-dot"></i>Draft</span>`
        : `<span class="fin-badge" title="The month is over. Its figures still change if an expense is added or edited.">Month ended</span>`;
    if (r.not_started) {
        $("fin-perf-body").innerHTML = stateHtml("empty", "Pick this month or an earlier one.", "This month hasn't started yet");
        return;
    }
    state.points = r.trend ? monthPoints(r.trend) : null;
    $("fin-perf-body").innerHTML = [
        tilesHtml(r), basisHtml(r),
        `<div class="fin-grid">${bridgeCard(r)}${r.where ? whereCard(r) : ""}</div>`,
        state.points ? trendCard(state.points) : "", statementHtml(r), notesHtml(r),
    ].join("");
    drawChart();
}

// ---- Basis and caveats ----

function basisHtml(r) {
    const t = r.this, warnings = [];
    const gap = incomeGap(t);
    if (gap) warnings.push(gap.text);
    if (r.segment === "all") warnings.push("Installation and equipment income has no source in the app yet, so it isn't included.");
    if (!t.expenses_recorded) warnings.push(`No expenses are recorded for ${esc(monthName(r.month))}, so there is no profit figure. Add them on the <a href="#/finance-expenses/${r.month.slice(0, 7)}">Expenses page</a>.`);
    else warnings.push(`These figures rest on the ${t.expenses_recorded} expense${t.expenses_recorded === 1 ? "" : "s"} recorded for ${esc(monthName(r.month))}. Anything not entered isn't counted.`);
    if (r.segment !== "all") warnings.push(`Only direct costs tagged ${r.segment === "pppoe" ? "PPPoE" : "Hotspot"} count here; tag an expense's segment by editing it on the Expenses page. Shared costs (upstream, salaries, rent) aren't split between segments.`);
    return `<div class="fin-perf-basis">
        <p>${esc(r.basis)}</p>
        <ul>${warnings.map(w => `<li>${w}</li>`).join("")}</ul>
    </div>`;
}

// ---- Headline figures ----

function tilesHtml(r) {
    const t = r.this, last = r.last;
    // Against last month only once this month is over (a part-month would read as a collapse).
    const versus = (now, before, goodWhenUp) => r.is_draft ? null : changeBetween(now, before, goodWhenUp, `vs ${monthName(r.previous_month).split(" ")[0]}`);
    if (r.segment !== "all") {
        const left = t.before_shared_costs;
        return `<div class="fin-kpis">
            ${kpi({ label: "Income", value: t.income_total == null ? null : money(t.income_total), none: "Not recorded",
                    change: r.income_comparable ? versus(t.income_total, last.income_total, true) : null, sub: r.segment === "pppoe" ? "PPPoE recharges" : "Hotspot sales" })}
            ${kpi({ label: "Direct costs", value: t.direct_total == null ? null : money(t.direct_total), none: "Not recorded", sub: "Tagged to this segment" })}
            ${kpi({ label: "Before shared costs", value: left == null ? null : money(left), none: "No figure yet",
                    flag: left == null ? null : Number(left) < 0 ? ["Costs more than it earns", "bad"] : null,
                    sub: t.gross_margin == null ? "Income less direct costs" : `${t.gross_margin}% of income` })}
        </div>`;
    }
    const net = t.net_profit, even = r.break_even;
    const evenTile = even?.day ? kpi({ label: "Break-even", value: dayLabel(even.day, { day: "numeric", month: "short" }), flag: ["Reached", "good"], sub: "The day income passed this month's costs" })
        : even ? kpi({ label: "Break-even", value: "Not yet", flag: ["Not reached", "warn"], sub: `${money(even.to_go)} to go` })
        : kpi({ label: "Break-even", value: null, none: "No costs yet", sub: "Needs this month's expenses" });
    const cash = t.cash_left;
    return `<div class="fin-kpis">
        ${kpi({ label: "Net profit", value: net == null ? null : money(net), none: "No figure yet", change: r.income_comparable ? versus(net, last.net_profit, true) : null,
                flag: net != null && Number(net) < 0 ? ["A loss", "bad"] : null,
                sub: net == null ? "Needs this month's expenses"
                    : `${t.net_margin != null ? `${t.net_margin}% of income, ` : ""}after all running costs${incomeGap(t) ? ` (${incomeGap(t).short})` : ""}` })}
        ${kpi({ label: "Equipment bought", value: t.equipment_total == null ? null : money(t.equipment_total), none: "Not recorded",
                sub: "Kept, not a running cost, so not in profit" })}
        ${kpi({ label: "Cash left", value: cash == null ? null : money(cash), none: "No figure yet", change: r.income_comparable ? versus(cash, last.cash_left, true) : null,
                flag: cash != null && Number(cash) < 0 ? ["More went out than came in", "bad"] : null,
                sub: cash == null ? "Needs this month's expenses" : "Net profit less the equipment bought" })}
        ${kpi({ label: "Income", value: t.income_total == null ? null : money(t.income_total), change: r.income_comparable ? versus(t.income_total, last.income_total, true) : null,
                sub: incomeGap(t)?.tag || "PPPoE and hotspot", href: `finance-income/${r.month.slice(0, 7)}/all` })}
        ${kpi({ label: "Expenses", value: t.expenses_total == null ? null : money(t.expenses_total), none: "Not recorded",
                change: versus(t.expenses_total, last.expenses_total, false),
                sub: "Operating; equipment bought to keep is left out", href: `finance-expenses/${r.month.slice(0, 7)}` })}
        ${evenTile}
    </div>`;
}

// ---- From income to profit, and where the money went ----

function bridgeCard(r) {
    const t = r.this;
    if (r.segment !== "all") {
        const tip = (label, amount) => amount == null ? "" : tipAttr(label, [{ label: "Amount", value: money(amount) }]);
        return card({ span: 12, title: "From income to what is left", sub: "This segment's income, less the direct costs tagged to it. Shared costs aren't split between segments.", body: bridge([
            { kind: "start", label: "Income", amount: t.income_total, tip: tip("Income", t.income_total) },
            { kind: "minus", label: "Direct costs", amount: t.direct_total, none: "not recorded", tip: tip("Direct costs", t.direct_total) },
            { kind: "result", label: "Before shared costs", amount: t.before_shared_costs, none: "no figure yet", tip: tip("Before shared costs", t.before_shared_costs) },
        ]) });
    }
    const note = !t.expenses_recorded ? `No expenses are recorded for ${monthName(r.month)}, so the costs and the profit are blank.` : "";
    return card({ span: r.where?.categories.length ? 7 : 12, title: "From income to cash left", sub: "Income, then what each kind of cost takes away, down to profit; then the equipment bought.",
                  body: bridge(profitSteps(t)) + (note ? `<p class="fin-card-note">${esc(note)}</p>` : "") });
}

function whereCard(r) {
    const w = r.where;
    if (!w.categories.length) return "";
    const rows = w.categories.map(c => ({
        label: c.category, amount: Number(c.total), value: money(c.total), share: c.share_pct == null ? "" : `${Math.round(Number(c.share_pct))}%`,
        tip: tipAttr(c.category, [{ key: "expenses", label: "Spent", value: money(c.total) }, { label: "Share of expenses", value: `${c.share_pct}%` },
                                  c.change ? { label: "Against last month", value: `${Number(c.change.amount) >= 0 ? "+" : "−"}${money(Math.abs(Number(c.change.amount)))}` } : null]),
    }));
    const movers = w.biggest_changes.length ? `<p class="fin-card-note">Biggest changes from last month: ${w.biggest_changes
        .map(m => `${esc(m.category)} ${changeHtml(m.change)}`).join(" · ")}</p>` : "";
    return card({ span: 5, title: "Where the money went", sub: "This month's operating expenses, by category.",
                  open: [`finance-expenses/${r.month.slice(0, 7)}`, "Expenses"], body: ranked(rows, { key: "expenses" }) + movers });
}

// ---- Month by month: income and expenses as bars, profit as a line ----

function trendCard(points) {
    return card({
        title: "Month by month", sub: "Income and operating expenses side by side, with profit as the line.",
        body: `<div class="fin-chart-box" id="fin-perf-chart"></div>
            ${legend([{ key: "income", label: "Income" }, { key: "expenses", label: "Expenses (operating)" }, { key: "profit", label: "Profit" },
                      points.some(p => p.partial) ? { key: "partial", label: "Still counting" } : null,
                      { note: "A month with no expenses recorded has no expense bar and no profit point." }])}
            ${monthsTable(points)}`,
    });
}

function drawChart() {
    const box = $("fin-perf-chart");
    if (box && state.points) box.innerHTML = monthsChart(state.points, { width: box.clientWidth, id: "fin-perf-hatch" });
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
        const gap = row.incomplete ? incomeGap(r.this) : null;
        const flag = gap ? ` <span class="fin-perf-flag" title="${esc(gap.text)}">${esc(gap.short)}</span>` : "";
        const main = `<tr class="fin-perf-${row.type}"><td>${label}${flag}</td>
            <td class="num">${moneyOrNone(row.this)}${margin}</td><td class="num">${moneyOrNone(row.last)}</td><td class="num">${changeHtml(row.change)}</td></tr>`;
        if (!hasItems) return main;
        const items = row.items.map(e => `<tr class="fin-perf-item" data-items="${id}" hidden>
            <td>${esc(dayName(e.spent_on))} · ${esc(e.payee || e.description || "Cash")}${e.reference ? ` <span class="fin-perf-code">${esc(e.reference)}</span>` : ""}</td>
            <td class="num">${moneyOrNone(e.amount)}</td><td></td><td></td></tr>`).join("");
        return main + items;
    }).join("");
    return card({ title: "Statement", sub: "Click an expense line to see the payments in it; an income line opens Income.",
                  body: `<div class="table-scroll table-compact"><table class="fin-perf-statement">${head}<tbody>${body}</tbody></table></div>` });
}

// ---- Notes ----

function notesHtml(r) {
    const n = r.notes, items = [];
    if (n.uncategorised.count) {
        items.push(`<li>${n.uncategorised.count} expense${n.uncategorised.count === 1 ? " is" : "s are"} in "Other" (${moneyOrNone(n.uncategorised.total)}). Give ${n.uncategorised.count === 1 ? "it" : "them"} a category on the <a href="#/finance-expenses/${r.month.slice(0, 7)}">Expenses page</a> so the statement is accurate.</li>`);
    }
    if (n.no_direct_costs.length) {
        items.push(`<li>Nothing recorded for ${n.no_direct_costs.map(esc).join(" or ")} this month. If it was paid, add it on the <a href="#/finance-expenses/${r.month.slice(0, 7)}">Expenses page</a>.</li>`);
    }
    if (!items.length) return "";
    return card({ title: "Notes", sub: "What the statement leaves out, or may be missing.", body: `<ul class="fin-perf-notes">${items.join("")}</ul>` });
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

    wireTips($("fin-perf-body"));
    // The chart is drawn at the width it's shown at.
    redrawOnResize("view-finance-performance", drawChart);

    registerRoute("finance-performance", (params) => {
        state.month = /^\d{4}-\d{2}$/.test(params[0] || "") ? params[0] : nairobiMonth();
        state.segment = SEGMENTS.includes(params[1]) ? params[1] : "all";
        $("fin-perf-month").value = state.month;
        $("fin-perf-segment").querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.segment === state.segment)));
        showView("view-finance-performance");
        load();
    });
}
