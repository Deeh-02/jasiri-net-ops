import { showView, registerRoute, navigate, stateHtml } from "./common.js";
import {
    esc, money, dayLabel, monthLabel, nairobiMonth, fetchReport, wireTips, tipAttr, markFresh,
    kpi, card, legend, alertList, figures, ranked, splitBar, bridge, monthsChart, mountPace, sparkline, redrawOnResize,
    changeBetween, monthPoints, monthsTable, profitSteps,
} from "./finance-common.js";

// Finance > Snapshot: the month on one screen. It owns no figures: every
// number comes from the report behind the page it links to (Income,
// Performance, Assets), so the Snapshot and that page can never disagree.
//
// Top to bottom: one sentence saying where the month stands, five headline
// figures, then cards: income adding up day by day against last month, what
// needs attention, money in and out by month, where the money went, where
// income came from, the steps from income to profit, and equipment.
//
// Each report is its own request and its part of the page is drawn the
// moment it arrives. What isn't recorded says so; it is never shown as 0.
//
// URL: #/finance-overview/<YYYY-MM>.

const state = { month: null, performance: null, income: null, assets: null };
const LEVEL_ORDER = { bad: 0, warn: 1, info: 2 };
const ATTENTION_LIMIT = 5;

function $(id) { return document.getElementById(id); }

const monthName = () => monthLabel(state.month, true);
const shortMonth = iso => asMonthName(iso, { month: "long" });

function asMonthName(iso, opts) {
    const [y, m] = iso.split("-").map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString("en-GB", opts);
}

// A report that arrived and has figures; null while loading or after a failure.
const got = name => (state[name]?.ok ? state[name].data : null);

function skeleton(lines = 4) {
    return Array.from({ length: lines }, (_, i) => `<span class="skeleton" style="width:${[92, 78, 85, 60, 70][i % 5]}%"></span>`).join("");
}

// What a card shows while its report is on the way, or if it failed.
function waiting(name, lines) {
    const report = state[name];
    return report && !report.ok ? stateHtml("error", report.error) : skeleton(lines);
}

// ---- The page's frame: drawn once per month, then filled in ----

function drawFrame() {
    $("fin-ov-status").innerHTML = "";
    $("fin-ov-lead").innerHTML = `<p class="fin-lead">${skeleton(1)}</p>`;
    $("fin-ov-kpis").innerHTML = ["in", "out", "profit", "cash", "ahead"].map(id =>
        `<div id="fin-ov-kpi-${id}" style="display:contents"><div class="skeleton-card" aria-hidden="true"><span class="skeleton" style="width:55%"></span><span class="skeleton"></span></div></div>`).join("");
    const month = state.month;
    $("fin-ov-grid").innerHTML = [
        card({ id: "fin-ov-pace", span: 8, title: `${shortMonth(month)} income, day by day`,
               sub: "The running total, against last month by the same day.", open: [`finance-income/${month}/all`, "Income"], body: skeleton(6) }),
        card({ id: "fin-ov-attention", span: 4, title: "Needs attention", sub: "From Income, Expenses and Assets. Worst first.", body: skeleton(4) }),
        card({ id: "fin-ov-months", span: 8, title: "Money in and money out, month by month",
               sub: "Profit is income less operating expenses.", open: [`finance-performance/${month}/all`, "Performance"], body: skeleton(6) }),
        card({ id: "fin-ov-where", span: 4, title: "Where the money went", sub: `${shortMonth(month)}'s operating expenses, by category.`,
               open: [`finance-expenses/${month}`, "Expenses"], body: skeleton(5) }),
        card({ id: "fin-ov-sources", span: 4, title: "Where income came from", sub: "By segment, then the packages that earned most.",
               open: [`finance-income/${month}/all`, "Income"], body: skeleton(5) }),
        card({ id: "fin-ov-bridge", span: 4, title: "From income to cash left", sub: "What each kind of cost takes away, then the equipment bought.",
               open: [`finance-performance/${month}/all`, "Performance"], body: skeleton(5) }),
        card({ id: "fin-ov-assets", span: 4, title: "Equipment", sub: "What Inventory's serialised equipment cost, and what needs a look.",
               open: ["finance-assets", "Assets"], body: skeleton(5) }),
    ].join("");
}

function fill(cardId, html) {
    const body = document.querySelector(`#${cardId} .fin-card-body`);
    if (!body) return null;
    body.innerHTML = html;
    markFresh(body);
    return body;
}

// ---- The sentence at the top ----

function drawLead() {
    const perf = got("performance"), inc = got("income");
    if (!state.performance || !state.income) return;               // wait for both
    if (!perf && !inc) { $("fin-ov-lead").innerHTML = ""; return; }
    const draft = perf?.is_draft, t = perf?.this;
    const income = inc?.has_data ? inc.summary.total : t?.income_total;
    const start = `<b>${esc(monthName())}</b>${draft ? " so far" : ""}:`;
    let text;
    if (perf?.not_started) text = `${start} this month hasn't started yet.`;
    else if (income == null) text = `${start} no income records${t?.expenses_total != null ? `, and <b>${money(t.expenses_total)}</b> went out` : ""}.`;
    else if (!perf) text = `${start} <b>${money(income)}</b> came in.`;
    else if (t.net_profit == null) text = `${start} <b>${money(income)}</b> came in. No expenses are recorded yet, so there is no profit figure.`;
    else if (Number(t.net_profit) < 0) text = `${start} <b>${money(income)}</b> came in and <b>${money(t.expenses_total)}</b> went out: a loss of <b>${money(Math.abs(Number(t.net_profit)))}</b>.`;
    else text = `${start} <b>${money(income)}</b> came in, <b>${money(t.expenses_total)}</b> went out, leaving <b>${money(t.net_profit)}</b>${t.net_margin != null ? ` (${t.net_margin}% of income)` : ""}.`;
    // Equipment bought comes off what is left: say so when there was some.
    if (t?.cash_left != null && Number(t.equipment_total)) {
        const cash = Number(t.cash_left);
        text += ` Equipment bought took <b>${money(t.equipment_total)}</b>, ` +
            (cash < 0 ? `so <b>${money(Math.abs(cash))}</b> more went out than came in.` : `leaving <b>${money(cash)}</b> in cash.`);
    }
    const notes = ["Cash basis: money counts on the day it is paid."];
    if (t?.income_incomplete) notes.unshift("Hotspot income only: PPPoE isn't recorded yet, so income and profit are understated.");
    if (draft) notes.push("The month is still running, so these figures will change.");
    $("fin-ov-lead").innerHTML = `<p class="fin-lead">${text}</p><p class="fin-lead-note">${notes.map(esc).join(" ")}</p>`;
    $("fin-ov-status").innerHTML = draft ? `<span class="fin-badge draft" title="This month is still running: figures will change"><i class="fin-dot"></i>Draft</span>` : "";
}

// ---- Headline figures ----

function setKpi(id, html) {
    const slot = $(`fin-ov-kpi-${id}`);
    if (slot) slot.innerHTML = html;
}

// A change from last month is shown only once the month is over: half a
// month against a whole one would read as a collapse.
function monthChange(perf, now, before, goodWhenUp) {
    if (perf.is_draft) return null;
    return changeBetween(now, before, goodWhenUp, `vs ${asMonthName(perf.previous_month, { month: "long" })}`);
}

function drawIncomeKpis() {
    const report = state.income, inc = got("income"), month = state.month;
    if (!report) return;
    if (!inc?.has_data) {
        const why = report.ok ? inc.reason : report.error;
        setKpi("in", kpi({ label: "Money in", value: null, sub: why, href: `finance-income/${month}/all` }));
        setKpi("ahead", kpi({ label: "Average a day", value: null, sub: "Needs income records" }));
        return;
    }
    const s = inc.summary, vs = inc.vs_last_month;
    const change = vs && vs.change_pct != null ? {
        pct: Number(vs.change_pct), goodWhenUp: true,
        note: vs.days ? `vs first ${vs.days} days of ${asMonthName(vs.previous_month, { month: "long" })}` : `vs ${asMonthName(vs.previous_month, { month: "long" })}`,
    } : null;
    const daily = inc.trend.day.filter(p => !p.partial && p.value != null).map(p => Number(p.value));
    setKpi("in", kpi({ label: "Money in", value: money(s.total), change, sub: `${Number(s.sales).toLocaleString()} sales${s.is_current_month ? " so far" : ""}`,
                       href: `finance-income/${month}/all`, spark: sparkline(daily) }));
    const projected = inc.target?.projected;
    setKpi("ahead", projected
        ? kpi({ label: "Month-end estimate", value: money(projected.projected), href: `finance-income/${month}/all`,
                sub: `Income by month end, if the rest goes like the last ${projected.weeks} weeks` })
        : kpi({ label: "Average a day", value: s.average_per_day == null ? null : money(s.average_per_day), href: `finance-income/${month}/all`,
                sub: s.best_day ? `Best day ${dayLabel(s.best_day.day, { day: "numeric", month: "short" })}: ${money(s.best_day.total)}` : "No completed day yet" }));
}

function drawPerformanceKpis() {
    const report = state.performance, perf = got("performance"), month = state.month;
    if (!report) return;
    if (!perf) {
        setKpi("out", kpi({ label: "Money out", value: null, sub: report.error }));
        setKpi("profit", kpi({ label: "Profit", value: null, sub: report.error }));
        return;
    }
    const t = perf.this, last = perf.last;
    setKpi("out", kpi({
        label: "Money out", value: t.expenses_total == null ? null : money(t.expenses_total), none: "Not recorded",
        change: monthChange(perf, t.expenses_total, last.expenses_total, false),
        sub: t.expenses_total == null ? "Add this month's expenses to see profit"
            : `${t.expenses_recorded} expense${t.expenses_recorded === 1 ? "" : "s"} recorded; equipment bought to keep is left out`,
        href: `finance-expenses/${month}`,
    }));
    const net = t.net_profit;
    setKpi("profit", kpi({
        label: "Profit", value: net == null ? null : money(net), none: "No figure yet",
        change: monthChange(perf, net, last.net_profit, true),
        flag: net == null ? null : Number(net) < 0 ? ["A loss", "bad"] : null,
        sub: net == null ? "Needs this month's expenses" : t.net_margin != null ? `${t.net_margin}% of income is left after costs` : "After all recorded costs",
        href: `finance-performance/${month}/all`,
    }));
    const cash = t.cash_left;
    setKpi("cash", kpi({
        label: "Cash left", value: cash == null ? null : money(cash), none: "No figure yet",
        change: monthChange(perf, cash, last.cash_left, true),
        flag: cash != null && Number(cash) < 0 ? ["More went out than came in", "bad"] : null,
        sub: cash == null ? "Needs this month's expenses"
            : Number(t.equipment_total) ? `Profit less ${money(t.equipment_total)} of equipment bought` : "Profit; no equipment bought this month",
        href: `finance-performance/${month}/all`,
    }));
}

// ---- Income adding up day by day ----

// The running totals the chart draws: this month up to the last day counted,
// and last month up to the same day (only when last month is recorded from
// its first day: a total that starts mid-month would look like a bad month).
function paceModel(inc) {
    const points = inc.trend.day;
    const reached = points.reduce((k, p, i) => (p.value != null ? i : k), -1);
    const lastKnown = points[0]?.last_month != null;
    let total = 0, last = 0;
    const days = points.map((p, i) => {
        if (i <= reached) total += Number(p.value || 0);
        const hasLast = lastKnown && p.last_month != null;
        if (hasLast) last += Number(p.last_month);
        return { day: p.day, name: dayLabel(p.day), value: p.value, partial: p.partial,
                 total: i <= reached ? total : null, last: hasLast ? last : null };
    });
    const t = inc.target || {};
    return {
        days, lastKnown, thisName: shortMonth(state.month),
        lastName: asMonthName(previousMonth(), { month: "long" }),
        target: t.target != null ? { amount: t.target, label: t.target_source === "manual" ? "Target" : "Break-even" } : null,
        projected: t.projected ? t.projected.projected : null,
    };
}

function previousMonth() {
    const [y, m] = state.month.split("-").map(Number);
    return new Date(y, m - 2, 1).toLocaleDateString("en-CA").slice(0, 7);
}

function drawPace() {
    const inc = got("income");
    if (!state.income) return;
    if (!inc?.has_data) {
        fill("fin-ov-pace", state.income.ok ? `<p class="fin-empty">${esc(inc.reason || "No income records for this month.")}</p>` : stateHtml("error", state.income.error));
        return;
    }
    const s = inc.summary, model = paceModel(inc);
    const body = fill("fin-ov-pace", `
        ${figures([
            s.is_current_month ? [money(s.today || 0), "Today so far"] : [money(s.total), "The whole month"],
            [s.average_per_day == null ? null : money(s.average_per_day), "A day, on average"],
            s.best_day ? [money(s.best_day.total), `Best day, ${dayLabel(s.best_day.day, { day: "numeric", month: "short" })}`] : null,
        ])}
        <div class="fin-chart-box" id="fin-ov-pace-chart"></div>
        ${legend([
            { key: "income", label: `${model.thisName}, adding up` },
            model.lastKnown ? { key: "compare", label: `${model.lastName}, by the same day` } : { note: `${model.lastName} isn't recorded from its first day, so it isn't drawn.` },
            model.target ? { key: "target", label: `${model.target.label} ${money(model.target.amount)}` } : null,
            model.projected != null ? { key: "projected", label: `Estimate to month end ${money(model.projected)}` } : null,
        ])}
        <details class="fin-table-view"><summary>Show as a table</summary>
            <div class="table-scroll table-compact"><table class="table-dense">
                <thead><tr><th>Day</th><th class="num">That day</th><th class="num">Running total</th><th class="num">${esc(model.lastName)}, same day</th></tr></thead>
                <tbody>${model.days.map(d => `<tr><td>${esc(d.name)}${d.partial ? " (so far)" : ""}</td>
                    <td class="num">${d.total == null ? "not yet" : d.value == null ? "no data" : money(d.value)}</td>
                    <td class="num">${d.total == null ? "" : money(d.total)}</td>
                    <td class="num">${d.last == null ? "no data" : money(d.last)}</td></tr>`).join("")}</tbody>
            </table></div></details>`);
    if (body) mountPace($("fin-ov-pace-chart"), model);
}

// ---- Needs attention: what each page flags, in one list ----

function attentionItems() {
    const perf = got("performance"), inc = got("income"), assets = got("assets"), month = state.month, items = [];
    if (perf && !perf.not_started) {
        const t = perf.this, n = perf.notes;
        if (t.net_profit != null && Number(t.net_profit) < 0) {
            items.push({ level: "bad", title: `${monthName()} is running at a loss`, detail: `Costs are ${money(Math.abs(Number(t.net_profit)))} more than income so far.`,
                         href: `finance-performance/${month}/all`, link: "See the statement" });
        }
        if (!t.expenses_recorded) {
            items.push({ level: "warn", title: `No expenses recorded for ${monthName()}`, detail: "Profit can't be worked out until they are added.",
                         href: `finance-expenses/${month}`, link: "Add expenses" });
        }
        if (n.uncategorised.count) {
            items.push({ level: "warn", title: `${n.uncategorised.count} expense${n.uncategorised.count === 1 ? " is" : "s are"} in "Other"`,
                         detail: `${money(n.uncategorised.total)} without a real category, so the breakdown is off.`, href: `finance-expenses/${month}`, link: "Give them a category" });
        }
        if (n.no_direct_costs.length) {
            items.push({ level: "info", title: `Nothing recorded for ${n.no_direct_costs.join(" or ")}`, detail: "If it was paid this month, add it, or profit is overstated.",
                         href: `finance-expenses/${month}`, link: "Open Expenses" });
        }
    }
    for (const a of inc?.attention || []) items.push({ level: a.level, title: a.title, detail: a.detail, href: `finance-income/${month}/all`, link: "Open Income" });
    for (const a of assets?.alerts || []) items.push({ level: a.level, title: a.title, detail: a.detail, href: "finance-assets", link: "Open Assets" });
    return items.sort((a, b) => (LEVEL_ORDER[a.level] ?? 3) - (LEVEL_ORDER[b.level] ?? 3));
}

function drawAttention() {
    if (!state.performance || !state.income || !state.assets) return;   // one list, once everything has answered
    const items = attentionItems();
    const failed = ["performance", "income", "assets"].filter(name => !state[name].ok);
    const missing = failed.length ? `<p class="fin-card-note">Couldn't check ${failed.join(", ")} just now.</p>` : "";
    if (!items.length) {
        fill("fin-ov-attention", `<p class="fin-clear"><i class="fin-dot"></i>Nothing needs attention this month.</p>${missing}`);
        return;
    }
    const more = items.length - ATTENTION_LIMIT;
    fill("fin-ov-attention", alertList(items.slice(0, ATTENTION_LIMIT)) +
        (more > 0 ? `<p class="fin-card-note">and ${more} more on the pages themselves.</p>` : "") + missing);
}

// ---- Money in and out by month ----

function drawMonths() {
    const perf = got("performance");
    if (!state.performance) return;
    if (!perf?.trend) { fill("fin-ov-months", state.performance.ok ? `<p class="fin-empty">Nothing to show for this month.</p>` : stateHtml("error", state.performance.error)); return; }
    const points = monthPoints(perf.trend);
    state.monthPoints = points;
    fill("fin-ov-months", `<div class="fin-chart-box" id="fin-ov-months-chart"></div>
        ${legend([{ key: "income", label: "Income" }, { key: "expenses", label: "Expenses (operating)" }, { key: "profit", label: "Profit" },
                  points.some(p => p.partial) ? { key: "partial", label: "Still counting" } : null,
                  { note: "A month with no expenses recorded has no expense bar and no profit point." }])}
        ${monthsTable(points)}`);
    drawMonthsChart();
}

function drawMonthsChart() {
    const box = $("fin-ov-months-chart");
    if (box && state.monthPoints) box.innerHTML = monthsChart(state.monthPoints, { width: box.clientWidth, id: "fin-ov-months-hatch" });
}

// ---- Where the money went; from income to profit ----

function drawWhere() {
    const perf = got("performance");
    if (!state.performance) return;
    if (!perf) { fill("fin-ov-where", stateHtml("error", state.performance.error)); return; }
    const rows = (perf.where?.categories || []).map(c => ({
        label: c.category, amount: Number(c.total), value: money(c.total), share: c.share_pct == null ? "" : `${Math.round(Number(c.share_pct))}%`,
        tip: tipAttr(c.category, [{ key: "expenses", label: "Spent", value: money(c.total) }, { label: "Share of expenses", value: `${c.share_pct}%` }]),
    }));
    const capital = perf.notes.capital, t = perf.this;
    const top = t.expenses_total == null ? "" : figures([[money(t.expenses_total), "Operating expenses"], [String(t.expenses_recorded), `expense${t.expenses_recorded === 1 ? "" : "s"} recorded`]]);
    fill("fin-ov-where", top + ranked(rows.slice(0, 7), { key: "expenses", empty: `No expenses are recorded for ${monthName()} yet.` }) +
        (rows.length > 7 ? `<p class="fin-card-note">and ${rows.length - 7} smaller categor${rows.length - 7 === 1 ? "y" : "ies"} on the Expenses page.</p>` : "") +
        (capital.rows.length ? `<p class="fin-card-note">Plus ${money(capital.total)} of equipment bought to keep, which is not an operating expense.</p>` : ""));
}

function drawBridge() {
    const perf = got("performance");
    if (!state.performance) return;
    if (!perf) { fill("fin-ov-bridge", stateHtml("error", state.performance.error)); return; }
    const t = perf.this, notes = [];
    if (!t.expenses_recorded) notes.push(`No expenses are recorded for ${monthName()}, so the costs and the profit are blank.`);
    fill("fin-ov-bridge", bridge(profitSteps(t)) + notes.map(n => `<p class="fin-card-note">${esc(n)}</p>`).join(""));
}

// ---- Where income came from ----

function drawSources() {
    const inc = got("income");
    if (!state.income) return;
    if (!inc?.has_data) { fill("fin-ov-sources", state.income.ok ? `<p class="fin-empty">No income records for this month.</p>` : stateHtml("error", state.income.error)); return; }
    const names = { hotspot: "Hotspot", pppoe: "PPPoE", other: "Installation & equipment" };
    const parts = Object.entries(inc.split || {}).map(([key, v]) => ({ key, label: names[key] || key, amount: v, value: v == null ? "" : money(v) }));
    const packages = inc.packages.slice(0, 5).map(p => ({
        label: p.package, amount: Number(p.total), value: money(p.total), share: p.share_pct == null ? "" : `${Math.round(Number(p.share_pct))}%`,
        tip: tipAttr(p.package, [{ key: "income", label: "Income", value: money(p.total) }, { label: "Sales", value: Number(p.sales).toLocaleString() },
                                 { label: "Share of income", value: `${p.share_pct}%` }]),
    }));
    fill("fin-ov-sources", `${parts.length ? splitBar(parts, "Income by segment") : ""}
        <h3 class="fin-h">Top packages</h3>${ranked(packages, { key: "income", empty: "No packages sold this month." })}`);
}

// ---- Equipment ----

function drawAssets() {
    const a = got("assets");
    if (!state.assets) return;
    if (!a) { fill("fin-ov-assets", stateHtml("error", state.assets.error)); return; }
    const s = a.summary;
    const rows = [...a.categories].sort((x, y) => Number(y.cost) - Number(x.cost)).slice(0, 5).map(c => ({
        label: c.name, sub: String(c.items), amount: Number(c.cost), value: money(c.cost),
        tip: tipAttr(c.name, [{ key: "cost", label: "Cost", value: money(c.cost) }, { label: "Items", value: String(c.items) }]),
    }));
    fill("fin-ov-assets", `
        ${figures([[money(s.total_cost), `Cost of ${s.count} items`], [String(s.needs_repair), "Faulty or in repair"],
                   [String(s.warranty_ending), "Warranty ending soon"]])}
        <h3 class="fin-h">Cost by category</h3>${ranked(rows, { key: "cost", empty: "No serialised equipment in Inventory yet." })}`);
}

// ---- Loading ----

function arrived(name, report) {
    state[name] = report;
    if (name === "income") { drawIncomeKpis(); drawPace(); drawSources(); }
    if (name === "performance") { drawPerformanceKpis(); drawMonths(); drawWhere(); drawBridge(); }
    if (name === "assets") drawAssets();
    drawLead();
    drawAttention();
}

async function load() {
    const month = state.month;
    Object.assign(state, { performance: null, income: null, assets: null, monthPoints: null });
    drawFrame();
    const current = () => month === state.month && !$("view-finance-overview").hidden;
    // Two requests at a time, so the page never holds more than two database connections.
    await Promise.all([
        fetchReport(`/finance/performance?month=${month}&segment=all`).then(r => { if (current()) arrived("performance", r); }),
        fetchReport(`/finance/income?month=${month}&segment=all`).then(r => { if (current()) arrived("income", r); }),
    ]);
    const assets = await fetchReport("/finance/assets");
    if (current()) arrived("assets", assets);
}

export function initFinanceOverview() {
    $("fin-ov-month").addEventListener("change", (e) => {
        if (e.target.value) navigate(`finance-overview/${e.target.value}`, { replace: true });
    });
    wireTips($("fin-ov-grid"));
    // The charts are drawn at the width they are shown at.
    redrawOnResize("view-finance-overview", () => {
        drawMonthsChart();
        const inc = got("income");
        if (inc?.has_data && $("fin-ov-pace-chart")) mountPace($("fin-ov-pace-chart"), paceModel(inc));
    });

    registerRoute("finance-overview", (params) => {
        state.month = /^\d{4}-\d{2}$/.test(params[0] || "") ? params[0] : nairobiMonth();
        $("fin-ov-month").value = state.month;
        showView("view-finance-overview");
        load();
    });
}
