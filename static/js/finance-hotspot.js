import { stateHtml } from "./common.js";
import { esc, money, fetchReport, markFresh, tipAttr, card, figures, legend, barChart, trendChart } from "./finance-common.js";

// The Revenue Overview, inside Finance > Income when the Hotspot segment is
// chosen (owner's call, 2026-11-16): the same two graphs as Reports > Revenue
// > Overview, drawn in Finance's own look. It reads the same server report
// (/monitoring/revenue-report/trend), so its figures are that page's own: the
// usual range, the recent average, the "unusual" flags and the direction
// label with its rule are all worked out there; this file only draws them.
//
//   - Income against the average: each day, week or month as a bar, against
//     the average of the finished ones. Total, average, highest and lowest
//     above; the same figures as a table below.
//   - Trend and usual range: one line, the range it usually stays in, its
//     recent average, a ring on anything outside the range, and whether the
//     whole series is rising, falling or stable (with the rule it used).
//
// Shown only to people who may see revenue (sites:view_revenue), like the
// Revenue page itself.

const INCOME_PERIODS = [["daily_7", "Last 7 days"], ["daily_14", "Last 14 days"], ["daily_30", "Last 30 days"],
                        ["weekly_12", "Last 12 weeks"], ["monthly_12", "Last 12 months"], ["year_current", "This year, by month"]];
const TREND_PERIODS = [["daily_7", "Last 7 days"], ["daily_30", "Last 30 days"], ["daily_90", "Last 90 days"], ["weekly_12", "Last 12 weeks"],
                       ["monthly_12", "Last 12 months"], ["year_current", "This year, by month"], ["year_previous", "Last year, by month"]];
const PER = { day: "per day", week: "per week", month: "per month", year: "per month" };
const DIRECTION = { Increasing: "▲ Increasing", Decreasing: "▼ Decreasing", Stable: "► Stable" };

const state = { incomeSeries: "daily_14", trendSeries: "daily_30", income: null, trend: null, incomeToken: 0, trendToken: 0 };

function $(id) { return document.getElementById(id); }

function select(id, label, options, chosen) {
    return `<label class="fin-hot-select"><span class="sr-only">${esc(label)}</span><select class="control" id="${id}" aria-label="${esc(label)}">
        ${options.map(([value, text]) => `<option value="${value}"${value === chosen ? " selected" : ""}>${esc(text)}</option>`).join("")}</select></label>`;
}

function skeleton() {
    return [92, 70, 84].map(w => `<span class="skeleton" style="width:${w}%"></span>`).join("");
}

// What a bar or point says when hovered: its figure and how it compares.
function vsAverage(p) {
    if (p.partial) return "So far: not counted in the average";
    if (p.vs_average_pct == null) return "";
    return p.vs_average_pct >= 0 ? `▲ ${p.vs_average_pct}% above the average` : `▼ ${Math.abs(p.vs_average_pct)}% below the average`;
}

// ---- Income against the average ----

function drawIncome() {
    const t = state.income, body = document.querySelector("#fin-hot-income .fin-card-body");
    if (!body || !t) return;
    const o = t.overview, points = t.points || [];
    if (!o || !points.length) {
        body.innerHTML = `<p class="fin-empty">No completed ${t.granularity === "day" ? "days" : "periods"} in this range yet.</p>`;
        return;
    }
    const unit = t.granularity === "day" ? "day" : t.granularity === "week" ? "week" : "month";
    body.innerHTML = `
        ${figures([[o.total, `Total, ${o.periods} ${unit}${o.periods === 1 ? "" : "s"}`], [o.average, `Average ${PER[t.granularity] || ""}`],
                   [o.highest.display, `Highest, ${o.highest.name}`], [o.lowest.display, `Lowest, ${o.lowest.name}`]])}
        <div class="fin-chart-box" id="fin-hot-income-chart"></div>
        ${legend([{ key: "income", label: "Income" }, { key: "avg", label: `Average ${o.average}` },
                  o.partial_left_out ? { key: "partial", label: `${unit === "day" ? "Today" : `This ${unit}`} so far, not counted` } : null])}
        <details class="fin-table-view"><summary>Show as a table</summary>
            <div class="table-scroll table-compact"><table class="table-dense">
                <thead><tr><th>Period</th><th class="num">Income</th><th class="num">Against the average</th></tr></thead>
                <tbody>${[...points].reverse().map(p => `<tr><td>${esc(p.name)}</td><td class="num">${esc(p.display)}</td>
                    <td class="num">${p.partial ? "so far" : p.vs_average_pct == null ? "—" : `${p.vs_average_pct >= 0 ? "▲ +" : "▼ −"}${Math.abs(p.vs_average_pct)}%`}</td></tr>`).join("")}</tbody>
            </table></div></details>`;
    drawIncomeChart();
    markFresh(body);
}

function drawIncomeChart() {
    const box = $("fin-hot-income-chart"), t = state.income;
    if (!box || !t?.points?.length) return;
    box.innerHTML = barChart(t.points.map(p => ({
        label: p.label, value: p.value, partial: p.partial,
        tip: tipAttr(`${p.name}${p.partial ? " (so far)" : ""}`, [{ key: p.partial ? "partial" : "income", label: "Income", value: p.display }], vsAverage(p)),
    })), { width: box.clientWidth, id: "fin-hot-income-hatch", average: t.overview?.average_value ?? null, what: "Income per period against the average", labelWidth: 56 });
}

// ---- Trend and usual range ----

function drawTrend() {
    const t = state.trend, body = document.querySelector("#fin-hot-trend .fin-card-body");
    if (!body || !t) return;
    if (!t.points?.length) { body.innerHTML = `<p class="fin-empty">No history for this series yet.</p>`; return; }
    const tr = t.trend, hasBand = t.points.some(p => p.band), hasAvg = t.points.some(p => p.rolling_avg != null);
    body.innerHTML = `
        <p class="fin-hot-direction"><b>${esc(DIRECTION[tr.label] || tr.label)}</b>
            <span>${esc(tr.rule)}${tr.based_on ? `, over ${tr.based_on} finished periods` : ""}</span></p>
        <div class="fin-chart-box" id="fin-hot-trend-chart"></div>
        ${legend([{ key: "income", label: "Income" }, hasAvg ? { key: "avg", label: "7-day average" } : null,
                  hasBand ? { key: "band", label: "Usual range (the middle of the previous 28 days)" } : null,
                  t.unusual_count ? { key: "low", label: `Outside the usual range: ${t.unusual_count}` } : null,
                  t.points.some(p => p.partial) ? { key: "partial", label: "So far, not finished" } : null])}
        <details class="fin-table-view"><summary>Show as a table</summary>
            <div class="table-scroll table-compact"><table class="table-dense">
                <thead><tr><th>Period</th><th class="num">Income</th><th class="num">Usual range</th><th>Flag</th></tr></thead>
                <tbody>${[...t.points].reverse().map(p => `<tr><td>${esc(p.name)}${p.partial ? " (so far)" : ""}</td><td class="num">${esc(p.display)}</td>
                    <td class="num">${p.band ? `${money(p.band.low)} to ${money(p.band.high)}` : "—"}</td>
                    <td>${p.unusual ? `Unusually ${p.unusual}` : ""}</td></tr>`).join("")}</tbody>
            </table></div></details>`;
    drawTrendChart();
    markFresh(body);
}

function drawTrendChart() {
    const box = $("fin-hot-trend-chart"), t = state.trend;
    if (!box || !t?.points?.length) return;
    box.innerHTML = trendChart(t.points.map(p => ({
        label: p.label, value: p.value, avg: p.rolling_avg, band: p.band, partial: p.partial, unusual: p.unusual,
        tip: tipAttr(`${p.name}${p.partial ? " (so far)" : ""}`, [
            { key: p.partial ? "partial" : "income", label: "Income", value: p.display },
            p.rolling_avg != null ? { key: "avg", label: "7-day average", value: money(p.rolling_avg) } : null,
            p.band ? { key: "band", label: "Usual range", value: `${money(p.band.low)} to ${money(p.band.high)}` } : null,
        ], p.unusual ? `Unusually ${p.unusual}. ${p.rule}` : p.partial ? "Still counting: the day isn't over." : ""),
    })), { width: box.clientWidth, id: "fin-hot-trend", what: "Income trend with its usual range" });
}

// ---- Loading ----

async function loadIncome() {
    const token = ++state.incomeToken, body = document.querySelector("#fin-hot-income .fin-card-body");
    if (body && !state.income) body.innerHTML = skeleton();
    const result = await fetchReport(`/monitoring/revenue-report/trend?series=${state.incomeSeries}`);
    if (token !== state.incomeToken) return;
    if (!result.ok) { if (body) body.innerHTML = stateHtml("error", result.error); return; }
    state.income = result.data;
    drawIncome();
}

async function loadTrend() {
    const token = ++state.trendToken, body = document.querySelector("#fin-hot-trend .fin-card-body");
    if (body && !state.trend) body.innerHTML = skeleton();
    const result = await fetchReport(`/monitoring/revenue-report/trend?series=${state.trendSeries}`);
    if (token !== state.trendToken) return;
    if (!result.ok) { if (body) body.innerHTML = stateHtml("error", result.error); return; }
    state.trend = result.data;
    drawTrend();
}

// Fills `box` (a place on the Income page) with the two cards and loads them.
export function mountHotspotOverview(box) {
    state.income = state.trend = null;
    box.innerHTML = `<h2 class="section-title fin-hot-title">Hotspot revenue overview</h2>
        <p class="fin-card-note fin-hot-note">From the router's sales: the same figures as Reports &gt; Revenue &gt; Overview.</p>
        <div class="fin-grid">
            ${card({ id: "fin-hot-income", span: 12, title: "Income against the average", sub: "Each period against the average of the finished ones.",
                     tools: select("fin-hot-income-series", "Income period", INCOME_PERIODS, state.incomeSeries), body: skeleton() })}
            ${card({ id: "fin-hot-trend", span: 12, title: "Trend and usual range", sub: "Whether income is rising, falling or steady, and what is out of the ordinary.",
                     tools: select("fin-hot-trend-series", "Trend period", TREND_PERIODS, state.trendSeries), body: skeleton() })}
        </div>`;
    loadIncome();
    loadTrend();
}

// The period pickers (on a stable ancestor: the Income page redraws its body).
export function initHotspotOverview(root) {
    root.addEventListener("change", (e) => {
        if (e.target.id === "fin-hot-income-series") { state.incomeSeries = e.target.value; loadIncome(); }
        if (e.target.id === "fin-hot-trend-series") { state.trendSeries = e.target.value; loadTrend(); }
    });
}

export function redrawHotspotOverview() {
    drawIncomeChart();
    drawTrendChart();
}
