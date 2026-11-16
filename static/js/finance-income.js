import { authHeaders, showView, registerRoute, navigate, can, stateHtml, stateRow, markRefreshing, skeletonCards } from "./common.js";
import {
    esc, money, asDay, dayLabel, monthLabel, nairobiDate, errorText, fetchReport, forgetReports, wireTips, tipAttr, markFresh,
    kpi, card, legend, alertList, figures, ranked, splitBar, barChart, redrawOnResize,
} from "./finance-common.js";
import { mountHotspotOverview, initHotspotOverview, redrawHotspotOverview } from "./finance-hotspot.js";
import { mountPppoeUpload, hidePppoeUpload } from "./finance-pppoe.js";

// Finance > Income (Phase 7C). Everything is read from data the app already
// records; nothing here is typed. Hotspot income comes from the router's
// sales; PPPoE income from the billing system's recharge export, uploaded on
// the PPPoE view (finance-pppoe.js). All adds the two together.
//
// Every figure leads to its rows: the best day, a bar in the daily trend and
// a package each narrow the transactions table below to what made them up.
//
// The tiles, cards, charts and hover labels are Finance's shared ones
// (finance-common.js); this file decides what goes in them.
//
// URL: #/finance-income/<YYYY-MM>/<all|pppoe|hotspot>, so a view can be
// bookmarked (spec section 3).

const SEGMENTS = ["all", "pppoe", "hotspot"];
const SEGMENT_NAMES = { hotspot: "Hotspot", pppoe: "PPPoE", other: "Installation & equipment" };
const WEEKDAY_NAMES = { Mon: "Mondays", Tue: "Tuesdays", Wed: "Wednesdays", Thu: "Thursdays", Fri: "Fridays", Sat: "Saturdays", Sun: "Sundays" };
const state = {
    month: null, segment: "all", gran: "day", report: null,
    tx: { search: "", day: null, package: null, page: 1 },
};
let searchTimer = null;
let txRequest = 0;

function $(id) { return document.getElementById(id); }

// What one row of income is called in each view.
const UNIT = { all: ["payment", "payments"], pppoe: ["recharge", "recharges"], hotspot: ["sale", "sales"] };
const unit = (n, segment = state.segment) => UNIT[segment][Number(n) === 1 ? 0 : 1];

const hourSpan = h => `${String(h).padStart(2, "0")}:00–${String((h + 1) % 24).padStart(2, "0")}:00`;

// ---- Loading ----

async function load() {
    const asked = `${state.month}/${state.segment}`;
    $("fin-inc-body").innerHTML = `<div class="fin-kpis">${skeletonCards(5)}</div>${stateHtml("loading", "Loading income…")}`;
    const result = await fetchReport(`/finance/income?month=${state.month}&segment=${state.segment}`);
    if (asked !== `${state.month}/${state.segment}`) return;   // the user moved on meanwhile
    if (!result.ok) {
        $("fin-inc-body").innerHTML = stateHtml("error", result.error);
        $("fin-inc-tx").hidden = true;
        return;
    }
    state.report = result.data;
    render();
    markFresh($("fin-inc-body"));
    $("fin-inc-tx").hidden = !state.report.has_data;
    if (state.report.has_data) loadTransactions();
}

function render() {
    const r = state.report;
    if (!r.has_data) {
        const title = r.segment === "pppoe" && !r.records_since ? "No PPPoE income recorded yet" : "No income records";
        $("fin-inc-body").innerHTML = stateHtml("empty", r.reason, title);
        return;
    }
    $("fin-inc-body").innerHTML = [
        tilesHtml(r), attentionHtml(r.attention), targetHtml(r.target, r.summary.is_current_month, r.segment),
        trendHtml(), patternsHtml(r),
        showsRevenueOverview() ? `<div id="fin-inc-revenue"></div>` : "",
    ].join("");
    drawChart();
    if (showsRevenueOverview()) mountHotspotOverview($("fin-inc-revenue"));
}

// The Revenue Overview graphs join the Hotspot view, for people who may see
// revenue (the same permission as the Revenue page).
function showsRevenueOverview() {
    return state.segment === "hotspot" && can("sites", "view_revenue");
}

// ---- Headline figures ----

function tilesHtml(r) {
    const s = r.summary, vs = r.vs_last_month, t = r.target;
    const lastMonth = vs ? monthLabel(vs.previous_month, true).split(" ")[0] : "";
    const change = vs && vs.change_pct != null ? {
        pct: Number(vs.change_pct), goodWhenUp: true,
        note: vs.days ? `vs first ${vs.days} days of ${lastMonth}` : `vs ${lastMonth}`,
    } : null;
    const started = s.records_start ? `, records start ${dayLabel(s.records_start, { day: "numeric", month: "short" })}` : "";
    const noCompare = vs ? "" : s.is_current_month && s.completed_days === 0 ? ". Nothing to compare until tomorrow" : ". Last month isn't fully recorded, so no comparison";
    return `<div class="fin-kpis">
        ${kpi({ label: "Total income", value: money(s.total), change, sub: `${Number(s.sales).toLocaleString()} ${unit(s.sales)}${started}${noCompare}`,
                title: vs ? `${money(vs.this)} against ${money(vs.previous)}` : "" })}
        ${s.is_current_month ? kpi({ label: "Today so far", value: money(s.today || 0), sub: "Still counting: not in the average",
                                     pick: `data-day="${esc(nairobiDate())}"`, title: `Show today's ${unit(2)}` }) : ""}
        ${kpi({ label: "Average a day", value: s.average_per_day == null ? null : money(s.average_per_day), none: "No day yet",
                sub: s.completed_days ? `over ${s.completed_days} completed day${s.completed_days === 1 ? "" : "s"}` : "No completed day yet" })}
        ${s.best_day ? kpi({ label: "Best day", value: money(s.best_day.total), sub: dayLabel(s.best_day.day), pick: `data-day="${esc(s.best_day.day)}"`, title: `Show this day's ${unit(2)}` })
                     : kpi({ label: "Best day", value: null, none: "No day yet", sub: "No completed day yet" })}
        ${t?.projected ? kpi({ label: "Month-end estimate", value: money(t.projected.projected), sub: `If the rest goes like the last ${t.projected.weeks} weeks` }) : ""}
    </div>`;
}

// ---- Needs attention (worst first; a day item opens that day's sales) ----

function attentionHtml(items) {
    if (!items) return "";
    if (!items.length) {
        const clear = state.segment === "pppoe" ? "the recharge upload is recent, no double entries held back"
            : "no silent days, no unusually low days, no unpriced packages";
        return `<p class="fin-clear fin-inc-clear"><i class="fin-dot"></i>Nothing needs attention this month: ${clear}.</p>`;
    }
    return card({ title: "Needs attention", sub: "Worst first.", body: alertList(items.map(i => ({
        level: i.level, title: i.title, detail: i.detail, pick: i.day ? `data-day="${esc(i.day)}"` : "", link: `See that day's ${unit(2)}`,
    }))) });
}

// ---- Month target: one bar, the target, and markers for the rest ----

function targetHtml(t, isCurrent, segment) {
    if (!t) return "";
    const markers = [];
    if (t.break_even && t.target_source !== "break_even") markers.push(["break-even", "Break-even", t.break_even.amount]);
    if (t.last_month != null) markers.push(["last-month", "Last month", t.last_month]);
    if (t.projected) markers.push(["projected", "Month-end estimate", t.projected.projected]);
    const scale = Math.max(1, Number(t.income), Number(t.target || 0), ...markers.map(m => Number(m[2]))) * 1.06;
    const at = v => `${Math.min(100, Number(v) / scale * 100).toFixed(1)}%`;
    const reached = t.target != null && Number(t.income) >= Number(t.target);
    let sub;
    if (t.target_source === "break_even") {
        sub = `Break-even, ${money(t.target)}: the average monthly expenses over ${t.break_even.months} recorded month${t.break_even.months === 1 ? "" : "s"}.`;
    } else if (t.target_source === "manual") {
        sub = `${money(t.target)}, set by you.`;
    } else if (segment !== "all") {
        sub = "Break-even covers the whole business, so it shows on the All view.";
    } else {
        sub = "No target yet: record expenses and break-even appears here.";
    }
    const keys = [
        `<span><i class="fin-inc-key${reached ? " reached" : ""}"></i>Income ${isCurrent ? "so far " : ""}${money(t.income)}</span>`,
        t.target != null ? `<span><i class="fin-inc-tick target"></i>Target ${money(t.target)}</span>` : "",
        ...markers.map(([cls, label, v]) => `<span><i class="fin-inc-tick ${cls}"></i>${label} ${money(v)}${cls === "projected" ? ` <span class="fin-legend-note">&nbsp;(same weekdays, last ${t.projected.weeks} weeks)</span>` : ""}</span>`),
    ].join("");
    const canSet = segment === "all" && can("finance", "manage_assets");
    const editor = canSet ? `<span class="fin-inc-target-edit" id="fin-inc-target-edit">
        <button type="button" class="fin-link" data-target-open>${t.target_source === "manual" ? "Change target" : "Set your own target"}</button></span>` : "";
    const headline = t.progress_pct == null ? "" : figures([
        [`${t.progress_pct}%`, reached ? "of the target: reached" : "of the target so far"],
        reached ? null : [money(Number(t.target) - Number(t.income)), "still to go"],
    ]);
    return card({ title: "Month target", sub, tools: editor, cls: "fin-inc-target", body: `${headline}
        <div class="fin-inc-target-bar" role="img" aria-label="Income against the month target">
            <span class="fin-inc-target-fill${reached ? " reached" : ""}" style="width:${at(t.income)}"></span>
            ${t.target != null ? `<span class="fin-inc-target-tick target" style="left:${at(t.target)}"></span>` : ""}
            ${markers.map(([cls, , v]) => `<span class="fin-inc-target-tick ${cls}" style="left:${at(v)}"></span>`).join("")}
        </div>
        <div class="fin-legend">${keys}</div>` });
}

// Typing a target for the month (saved per month; empty clears it and the
// bar goes back to break-even).
function openTargetEditor() {
    const t = state.report.target;
    $("fin-inc-target-edit").innerHTML = `<form class="fin-inc-target-form" id="fin-inc-target-form">
        <input type="number" class="control" id="fin-inc-target-amount" min="1" step="1" inputmode="numeric" placeholder="KSh"
            value="${t.target_source === "manual" ? Number(t.target) : ""}" aria-label="Income target for the month">
        <button type="submit" class="btn-primary fin-inc-small">Save</button>
        ${t.target_source === "manual" ? `<button type="button" class="btn-secondary fin-inc-small" data-target-clear>Clear</button>` : ""}
        <span class="fin-inc-target-error" id="fin-inc-target-error" role="alert"></span></form>`;
    $("fin-inc-target-amount").focus();
}

async function saveTarget(amount) {
    const res = await fetch(`/finance/targets/${state.month}`, {
        method: "PUT", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ amount }),
    }).catch(() => null);
    if (!res || !res.ok) {
        $("fin-inc-target-error").textContent = await errorText(res, "Couldn't save the target.");
        return;
    }
    forgetReports();   // the target is part of the Income report and the Snapshot
    load();
}

// ---- Trend chart ----

function trendHtml() {
    const g = state.gran;
    const button = (v, label) => `<button type="button" data-gran="${v}" aria-pressed="${g === v}">${label}</button>`;
    return card({
        title: "Trend", sub: `Income for each day, week or month. Click a day to see its ${unit(2)}.`,
        tools: `<div class="fleet-range" id="fin-inc-gran" role="group" aria-label="Trend by">${button("day", "Day")}${button("week", "Week")}${button("month", "Month")}</div>`,
        body: `<div id="fin-inc-legend"></div>
            <div class="fin-chart-box" id="fin-inc-chart"></div>
            <details class="fin-table-view"><summary>Show as a table</summary><div id="fin-inc-trend-table"></div></details>`,
    });
}

// The chosen granularity's points in one shape: label (axis), name (hover
// label), value (null = no data), compare (last month, day view only),
// partial, day.
function trendPoints() {
    const t = state.report.trend;
    if (state.gran === "week") {
        return t.week.map(p => ({ label: dayLabel(p.week_start, { day: "numeric", month: "short" }),
            name: `Week of ${dayLabel(p.week_start, { day: "numeric", month: "short" })}`, value: p.value, partial: p.partial }));
    }
    if (state.gran === "month") {
        return t.month.map(p => ({ label: monthLabel(p.month, false), name: monthLabel(p.month), value: p.value, partial: p.partial }));
    }
    return t.day.map(p => ({ label: String(asDay(p.day).getDate()), name: dayLabel(p.day), value: p.value,
        compare: p.last_month, partial: p.partial, day: p.day }));
}

function drawChart() {
    const box = $("fin-inc-chart");
    if (!box) return;
    const byDay = state.gran === "day", average = byDay ? state.report.summary.average_per_day : null;
    const pts = trendPoints();
    box.innerHTML = barChart(pts.map(p => ({
        ...p,
        pick: p.day && p.value != null ? `data-day="${p.day}"` : "",
        tip: tipAttr(`${p.name}${p.partial ? " (so far)" : ""}`, [
            { key: p.partial ? "partial" : "income", label: "Income", value: p.value == null ? "no data" : money(p.value) },
            byDay ? { key: "compare", label: "Same day last month", value: p.compare == null ? "no data" : money(p.compare) } : null,
        ], p.day && p.value != null ? `Click to see this day's ${unit(2)}` : ""),
    })), { width: box.clientWidth, id: "fin-inc-hatch", average, what: "Income trend" });
    $("fin-inc-legend").innerHTML = legend([
        { key: "income", label: byDay ? monthLabel(state.month) : state.gran === "week" ? "Each week" : "Each month" },
        byDay ? { key: "compare", label: "Last month, same day" } : null,
        average != null ? { key: "avg", label: `Average a day ${money(average)}` } : null,
        pts.some(p => p.partial) ? { key: "partial", label: "Still counting" } : null,
    ]);
    $("fin-inc-trend-table").innerHTML = `<div class="table-scroll table-compact"><table class="table-dense">
        <thead><tr><th>Period</th><th class="num">Income</th>${byDay ? `<th class="num">Last month</th>` : ""}</tr></thead>
        <tbody>${pts.map(p => `<tr><td>${esc(p.name)}${p.partial ? " (so far)" : ""}</td>
            <td class="num">${p.value == null ? "no data" : money(p.value)}</td>
            ${byDay ? `<td class="num">${p.compare == null ? "no data" : money(p.compare)}</td>` : ""}</tr>`).join("")}</tbody>
    </table></div>`;
}

// ---- Patterns: weekday, hour of day, packages, segments, customers ----

function weekdayCard(r) {
    return card({ span: 4, title: "By weekday", sub: "What a completed day brings in, on average.", body: ranked(r.weekdays.map(w => ({
        label: w.weekday, sub: `${w.days} day${w.days === 1 ? "" : "s"}`, amount: w.average == null ? null : Number(w.average), value: w.average == null ? "" : money(w.average),
        tip: tipAttr(WEEKDAY_NAMES[w.weekday] || w.weekday, [{ key: "income", label: "Average a day", value: w.average == null ? "no data" : money(w.average) },
                                                            { label: "Days it rests on", value: String(w.days) }]),
    }))) });
}

// One column per hour; the busiest hour is the only one in full colour.
function hourCard(r) {
    const max = Math.max(1, ...r.hours.map(h => Number(h.total)));
    const peak = r.hours.reduce((a, b) => (Number(b.total) > Number(a.total) ? b : a));
    const columns = r.hours.map((h, i) => `<span class="fin-inc-hour${h === peak ? " peak" : ""}" ${tipAttr(hourSpan(h.hour), [
        { key: "income", label: "Income this month", value: money(h.total) }, { label: unit(2)[0].toUpperCase() + unit(2).slice(1), value: Number(h.sales).toLocaleString() }])}>
        <span style="height:${(Number(h.total) / max * 100).toFixed(1)}%;--i:${i}"></span></span>`).join("");
    return card({ span: 4, title: "By hour of day", sub: `Busiest: ${hourSpan(peak.hour)}, ${money(peak.total)} this month.`, body: `
        <div class="fin-inc-hours" role="img" aria-label="Income by hour of day. Busiest: ${hourSpan(peak.hour)}">${columns}</div>
        <div class="fin-inc-hours-axis"><span>00</span><span>06</span><span>12</span><span>18</span><span>23</span></div>` });
}

function packageCard(r) {
    return card({ span: 4, title: "Top packages", sub: `Share of this month's income. Click one to see its ${unit(2)}.`, body: ranked(r.packages.map(p => ({
        label: p.package, amount: Number(p.total), value: money(p.total), share: p.share_pct == null ? "" : `${Math.round(Number(p.share_pct))}%`,
        pick: `data-package="${esc(p.package)}" title="Show this package's ${unit(2)}"`,
        tip: tipAttr(p.package, [{ key: "income", label: "Income", value: money(p.total) }, { label: unit(2)[0].toUpperCase() + unit(2).slice(1), value: Number(p.sales).toLocaleString() },
                                 { label: "Share of income", value: `${p.share_pct}%` }]),
    })), { empty: "No packages sold this month." }) });
}

function segmentCard(split) {
    const parts = Object.entries(split).map(([key, v]) => ({ key, label: SEGMENT_NAMES[key] || key, amount: v, value: v == null ? "" : money(v) }));
    return card({ span: 4, title: "By segment", sub: "Where this month's income came from.", body: splitBar(parts, "Income by segment") });
}

function buyersCard(b, span) {
    const sub = {
        hotspot: "An account is one phone on one device, so one person with two phones counts twice.",
        pppoe: "PPPoE customers who recharged this month.",
        all: "Hotspot accounts (one phone on one device) and PPPoE customers together.",
    }[state.segment];
    return card({ span, title: "Customers", sub, body: figures([
        [Number(b.buyers).toLocaleString(), "accounts paid"],
        [Number(b.first_time).toLocaleString(), "paid for the first time"],
        [Number(b.repeat).toLocaleString(), "paid more than once"],
    ]) });
}

function patternsHtml(r) {
    return `<div class="fin-grid">
        ${weekdayCard(r)}${hourCard(r)}${packageCard(r)}
        ${r.split ? segmentCard(r.split) : ""}${r.buyers ? buyersCard(r.buyers, r.split ? 8 : 12) : ""}
    </div>`;
}

// ---- Transactions ----

function txParams(extra = {}) {
    const q = new URLSearchParams({ month: state.month, segment: state.segment, ...extra });
    if (state.tx.search) q.set("search", state.tx.search);
    if (state.tx.day) q.set("day", state.tx.day);
    if (state.tx.package) q.set("package", state.tx.package);
    return q;
}

function renderFilters() {
    const chips = [];
    if (state.tx.day) chips.push(`<button type="button" class="fin-inc-chip" data-clear="day">${esc(dayLabel(state.tx.day))} ×</button>`);
    if (state.tx.package) chips.push(`<button type="button" class="fin-inc-chip" data-clear="package">${esc(state.tx.package)} ×</button>`);
    $("fin-inc-filters").innerHTML = chips.join("");
}

async function loadTransactions() {
    renderFilters();
    const tbody = $("fin-inc-rows");
    markRefreshing(tbody, 6, "Loading transactions…");
    const request = ++txRequest;
    const res = await fetch(`/finance/income/transactions?${txParams({ page: state.tx.page })}`, { headers: authHeaders() }).catch(() => null);
    if (request !== txRequest) return;
    if (!res || !res.ok) {
        tbody.innerHTML = stateRow(6, "error", await errorText(res, "Couldn't load transactions."));
        $("fin-inc-pager").innerHTML = "";
        return;
    }
    const data = await res.json();
    $("fin-inc-count").textContent = `${Number(data.matching).toLocaleString()} ${unit(data.matching)} · ${money(data.matching_total)}`;
    if (!data.rows.length) {
        tbody.innerHTML = stateRow(6, "empty", "Try another search, or clear the filters.", `No matching ${unit(2)}`);
        $("fin-inc-pager").innerHTML = "";
        return;
    }
    tbody.innerHTML = data.rows.map(r => `<tr>
        <td class="muted">${esc(new Date(r.at).toLocaleString("en-GB", { timeZone: "Africa/Nairobi", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }))}</td>
        <td class="fin-inc-account">${state.segment === "all" ? `<span class="fin-inc-seg">${r.segment === "pppoe" ? "PPPoE" : "Hotspot"}</span> ` : ""}${esc(r.account)}</td>
        <td>${esc(r.package)}</td>
        <td>${r.site ? esc(r.site) : `<span class="muted">Not linked to a site</span>`}</td>
        <td class="muted">${r.kind === "renewal" ? "Renewal" : r.segment === "pppoe" ? "First recharge" : "New account"}</td>
        <td class="num">${money(r.amount)}</td></tr>`).join("");
    const pages = Math.max(1, Math.ceil(data.matching / data.per_page));
    $("fin-inc-pager").innerHTML = pages === 1 ? "" : `
        <button type="button" class="btn-secondary" data-page="${data.page - 1}" ${data.page <= 1 ? "disabled" : ""}>Newer</button>
        <span class="muted">Page ${data.page} of ${pages}</span>
        <button type="button" class="btn-secondary" data-page="${data.page + 1}" ${data.page >= pages ? "disabled" : ""}>Older</button>`;
}

function filterTransactions(changes) {
    Object.assign(state.tx, changes, { page: 1 });
    loadTransactions();
    $("fin-inc-tx").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function exportCsv() {
    const button = $("fin-inc-export");
    button.disabled = true;
    const res = await fetch(`/finance/income/transactions.csv?${txParams()}`, { headers: authHeaders() }).catch(() => null);
    button.disabled = false;
    if (!res || !res.ok) return;
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: `income-${state.month}-${state.segment}.csv` });
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

// ---- Wiring ----

function go(month, segment) {
    navigate(`finance-income/${month}/${segment}`, { replace: true });
}

export function initFinanceIncome() {
    $("fin-inc-segment").addEventListener("click", (e) => {
        const b = e.target.closest("[data-segment]");
        if (b) go(state.month, b.dataset.segment);
    });
    $("fin-inc-month").addEventListener("change", (e) => { if (e.target.value) go(e.target.value, state.segment); });
    $("fin-inc-export").addEventListener("click", exportCsv);

    $("fin-inc-body").addEventListener("submit", (e) => {
        if (e.target.id !== "fin-inc-target-form") return;
        e.preventDefault();
        const amount = Number($("fin-inc-target-amount").value);
        if (amount > 0) saveTarget(amount);
        else $("fin-inc-target-error").textContent = "Type an amount above zero, or use Clear.";
    });
    $("fin-inc-body").addEventListener("click", (e) => {
        if (e.target.closest("[data-target-open]")) { openTargetEditor(); return; }
        if (e.target.closest("[data-target-clear]")) { saveTarget(null); return; }
        const gran = e.target.closest("[data-gran]");
        if (gran) {
            state.gran = gran.dataset.gran;
            $("fin-inc-gran").querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b === gran)));
            drawChart();
            return;
        }
        const day = e.target.closest("[data-day]");
        if (day) { filterTransactions({ day: day.dataset.day }); return; }
        const pkg = e.target.closest("[data-package]");
        if (pkg) filterTransactions({ package: pkg.dataset.package });
    });

    $("fin-inc-search").addEventListener("input", (e) => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => { state.tx.search = e.target.value.trim(); state.tx.page = 1; loadTransactions(); }, 350);
    });
    $("fin-inc-filters").addEventListener("click", (e) => {
        const chip = e.target.closest("[data-clear]");
        if (chip) { state.tx[chip.dataset.clear] = null; state.tx.page = 1; loadTransactions(); }
    });
    $("fin-inc-pager").addEventListener("click", (e) => {
        const b = e.target.closest("[data-page]");
        if (b && !b.disabled) { state.tx.page = Number(b.dataset.page); loadTransactions(); }
    });

    wireTips($("fin-inc-body"));
    // The chart is drawn at the width it's shown at; redraw when that changes.
    initHotspotOverview($("fin-inc-body"));
    redrawOnResize("view-finance-income", () => { if (state.report?.has_data) { drawChart(); if (showsRevenueOverview()) redrawHotspotOverview(); } });

    registerRoute("finance-income", (params) => {
        const month = /^\d{4}-\d{2}$/.test(params[0] || "") ? params[0] : nairobiDate().slice(0, 7);
        const segment = SEGMENTS.includes(params[1]) ? params[1] : "all";
        if (month !== state.month || segment !== state.segment) {
            state.tx = { search: "", day: null, package: null, page: 1 };
            $("fin-inc-search").value = "";
        }
        state.month = month;
        state.segment = segment;
        $("fin-inc-month").value = month;
        $("fin-inc-segment").querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.segment === segment)));
        showView("view-finance-income");
        if (segment === "pppoe") mountPppoeUpload($("fin-inc-pppoe"), () => { forgetReports(); load(); mountPppoeUpload($("fin-inc-pppoe"), null); });
        else hidePppoeUpload($("fin-inc-pppoe"));
        load();
    });
}
