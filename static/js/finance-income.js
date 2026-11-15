import { authHeaders, showView, registerRoute, navigate, stateHtml, stateRow, markRefreshing } from "./common.js";

// Finance > Income (Phase 7C). Everything is read from data the app already
// records; nothing here is typed. Hotspot income comes from the router's
// sales; PPPoE has no source yet, so its view says so (never KSh 0).
//
// Every figure leads to its rows: the best day, a bar in the daily trend and
// a package each narrow the transactions table below to what made them up.
//
// URL: #/finance-income/<YYYY-MM>/<all|pppoe|hotspot>, so a view can be
// bookmarked (spec section 3).

const SEGMENTS = ["all", "pppoe", "hotspot"];
const state = {
    month: null, segment: "all", gran: "day", report: null,
    tx: { search: "", day: null, package: null, page: 1 },
};
let searchTimer = null;
let txRequest = 0;

function $(id) { return document.getElementById(id); }

function esc(value) {
    const d = document.createElement("div");
    d.textContent = value == null ? "" : String(value);
    return d.innerHTML;
}

function money(amount) {
    return "KSh " + Number(amount || 0).toLocaleString("en-KE", { maximumFractionDigits: 0 });
}

function shortMoney(v) {
    if (v >= 1e6) return `${(v / 1e6).toFixed(v % 1e6 ? 1 : 0)}M`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(v % 1e3 ? 1 : 0)}k`;
    return String(Math.round(v));
}

function nairobiDate(d = new Date()) {
    return d.toLocaleDateString("en-CA", { timeZone: "Africa/Nairobi" });
}

// "2026-11-14" read as a calendar day, no time-zone shift.
function asDay(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d);
}

function dayLabel(iso, opts = { weekday: "short", day: "numeric", month: "short" }) {
    return asDay(iso).toLocaleDateString("en-GB", opts);
}

function monthLabel(iso) {
    return asDay(iso).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
}

async function errorText(res, fallback) {
    if (!res) return "Couldn't reach the server. Check your connection and try again.";
    const body = await res.json().catch(() => ({}));
    return typeof body.detail === "string" ? body.detail : fallback;
}

// ---- Loading ----

async function load() {
    $("fin-inc-body").innerHTML = stateHtml("loading", "Loading income…");
    const asked = `${state.month}/${state.segment}`;
    const res = await fetch(`/finance/income?month=${state.month}&segment=${state.segment}`, { headers: authHeaders() }).catch(() => null);
    if (asked !== `${state.month}/${state.segment}`) return;   // the user moved on meanwhile
    if (!res || !res.ok) {
        $("fin-inc-body").innerHTML = stateHtml("error", await errorText(res, "Couldn't load income. Try again in a moment."));
        $("fin-inc-tx").hidden = true;
        return;
    }
    state.report = await res.json();
    render();
    $("fin-inc-tx").hidden = !state.report.has_data;
    if (state.report.has_data) loadTransactions();
}

function render() {
    const r = state.report;
    if (!r.has_data) {
        const title = r.segment === "pppoe" ? "No PPPoE income recorded yet" : "No income records";
        $("fin-inc-body").innerHTML = stateHtml("empty", r.reason, title);
        return;
    }
    $("fin-inc-body").innerHTML = [
        tilesHtml(r), attentionHtml(r.attention), targetHtml(r.target, r.summary.is_current_month, r.segment),
        r.split ? splitHtml(r.split) : "", trendHtml(), patternsHtml(r), buyersHtml(r.buyers),
    ].join("");
    drawChart();
}

// ---- KPI tiles ----

function tile(label, value, sub, { cls = "", day = null } = {}) {
    const tag = day ? "button" : "div";
    const extra = day ? ` type="button" data-day="${esc(day)}" title="Show this day's transactions"` : "";
    return `<${tag} class="fin-inc-tile ${cls}"${extra}><div class="fin-inc-tile-label">${label}</div>
        <div class="fin-inc-tile-value">${value}</div><div class="fin-inc-tile-sub">${sub}</div></${tag}>`;
}

function tilesHtml(r) {
    const s = r.summary, vs = r.vs_last_month;
    const started = s.records_start ? ` · records start ${esc(dayLabel(s.records_start, { day: "numeric", month: "short" }))}` : "";
    let vsTile;
    if (!vs) {
        vsTile = tile("vs last month", "—", s.is_current_month && s.completed_days === 0
            ? "Nothing to compare until tomorrow" : "Last month isn't fully recorded");
    } else {
        const up = Number(vs.change_pct) >= 0;
        const pct = vs.change_pct == null ? "—" : `${up ? "▲" : "▼"} ${Math.abs(Number(vs.change_pct))}%`;
        const span = vs.days ? `first ${vs.days} days of each month` : `whole of ${esc(monthLabel(vs.previous_month))}`;
        vsTile = tile("vs last month", pct, `${money(vs.this)} vs ${money(vs.previous)}, ${span}`, { cls: up ? "up" : "down" });
    }
    return `<div class="fin-inc-tiles">
        ${tile("Total income", money(s.total), `${Number(s.sales).toLocaleString()} sales${started}`, { cls: "total" })}
        ${vsTile}
        ${tile("Average a day", s.average_per_day == null ? "—" : money(s.average_per_day),
               s.completed_days ? `over ${s.completed_days} completed day${s.completed_days === 1 ? "" : "s"}` : "No completed day yet")}
        ${s.best_day ? tile("Best day", money(s.best_day.total), esc(dayLabel(s.best_day.day)), { day: s.best_day.day })
                     : tile("Best day", "—", "No completed day yet")}
        ${s.is_current_month ? tile("Today so far", money(s.today), "Still counting: not in the average", { day: nairobiDate() }) : ""}
    </div>`;
}

// ---- Needs attention (worst first; a day item opens that day's sales) ----

const LEVEL_LABEL = { bad: "Act now", warn: "Check", info: "Note" };

function attentionHtml(items) {
    if (!items) return "";
    if (!items.length) {
        return `<p class="fin-inc-all-clear">Nothing needs attention this month: no silent days, no unusually low days, no unpriced packages.</p>`;
    }
    return `<section class="fin-inc-card fin-inc-attention" aria-labelledby="fin-inc-attention-title">
        <h2 class="section-title" id="fin-inc-attention-title">Needs attention</h2>
        <ul>${items.map(i => `<li class="fin-inc-alert ${i.level}">
            <span class="fin-inc-alert-level">${LEVEL_LABEL[i.level] || "Note"}</span>
            <div><strong>${esc(i.title)}</strong><div class="fin-inc-alert-detail">${esc(i.detail)}</div>
            ${i.day ? `<button type="button" class="fin-inc-link" data-day="${esc(i.day)}">See that day's sales</button>` : ""}</div>
        </li>`).join("")}</ul>
    </section>`;
}

// ---- Month target: one bar, the target, and markers for the rest ----

function targetHtml(t, isCurrent, segment) {
    if (!t) return "";
    const markers = [];
    if (t.break_even && t.target_source !== "break_even") markers.push(["break-even", "Break-even", t.break_even.amount]);
    if (t.last_month != null) markers.push(["last-month", "Last month", t.last_month]);
    if (t.projected) markers.push(["projected", "Projected", t.projected.projected]);
    const scale = Math.max(1, Number(t.income), Number(t.target || 0), ...markers.map(m => Number(m[2]))) * 1.06;
    const at = v => `${Math.min(100, Number(v) / scale * 100).toFixed(1)}%`;
    const reached = t.target != null && Number(t.income) >= Number(t.target);
    let heading;
    if (t.target_source === "break_even") {
        heading = `Target: break-even, ${money(t.target)} <span class="muted">(average monthly expenses over ${t.break_even.months} recorded month${t.break_even.months === 1 ? "" : "s"})</span>`;
    } else if (t.target_source === "manual") {
        heading = `Target: ${money(t.target)} <span class="muted">(set by you)</span>`;
    } else if (segment !== "all") {
        heading = `<span class="muted">Break-even covers the whole business, so it shows on the All view.</span>`;
    } else {
        heading = `<span class="muted">No target yet: record expenses and break-even appears here.</span>`;
    }
    const legend = [
        `<span><i class="fin-inc-key bar${reached ? " reached" : ""}"></i>Income ${isCurrent ? "so far" : ""} ${money(t.income)}${t.progress_pct != null ? ` · ${t.progress_pct}% of target` : ""}</span>`,
        t.target != null ? `<span><i class="fin-inc-tick target"></i>Target</span>` : "",
        ...markers.map(([cls, label, v]) => `<span><i class="fin-inc-tick ${cls}"></i>${label} ${money(v)}${cls === "projected" ? ` <span class="muted">(estimate: same weekdays, last ${t.projected.weeks} weeks)</span>` : ""}</span>`),
    ].join("");
    return `<section class="fin-inc-card fin-inc-target">
        <div class="fin-inc-target-head"><h2 class="section-title">Month target</h2><span>${heading}</span></div>
        <div class="fin-inc-target-bar" role="img" aria-label="Income against the month target">
            <span class="fin-inc-target-fill${reached ? " reached" : ""}" style="width:${at(t.income)}"></span>
            ${t.target != null ? `<span class="fin-inc-target-tick target" style="left:${at(t.target)}"></span>` : ""}
            ${markers.map(([cls, , v]) => `<span class="fin-inc-target-tick ${cls}" style="left:${at(v)}"></span>`).join("")}
        </div>
        <div class="fin-inc-legend">${legend}</div>
    </section>`;
}

// ---- The thin PPPoE / Hotspot / Other bar (All only) ----

function splitHtml(split) {
    const known = Object.entries(split).filter(([, v]) => v != null);
    const total = known.reduce((sum, [, v]) => sum + Number(v), 0);
    const names = { pppoe: "PPPoE", hotspot: "Hotspot", other: "Other" };
    const bar = known.map(([k, v]) => `<span class="fin-inc-split-seg ${k}" style="width:${total ? (Number(v) / total * 100).toFixed(1) : 0}%"></span>`).join("");
    const legend = Object.entries(split).map(([k, v]) => v == null
        ? `<span class="muted">${names[k]}: no data yet</span>`
        : `<span><i class="fin-inc-key ${k}"></i>${names[k]} ${total ? Math.round(Number(v) / total * 100) : 0}%</span>`).join(" · ");
    return `<div class="fin-inc-split" aria-label="Income by segment">
        <div class="fin-inc-split-bar">${bar}</div>
        <div class="fin-inc-split-legend">${legend}</div>
    </div>`;
}

// ---- Trend chart ----

function trendHtml() {
    const g = state.gran;
    const button = (v, label) => `<button type="button" data-gran="${v}" aria-pressed="${g === v}">${label}</button>`;
    return `<section class="fin-inc-card">
        <div class="fin-inc-card-head">
            <h2 class="section-title">Trend</h2>
            <div class="fleet-range" id="fin-inc-gran" role="group" aria-label="Trend by">${button("day", "Day")}${button("week", "Week")}${button("month", "Month")}</div>
        </div>
        <div class="fin-inc-legend" id="fin-inc-legend"></div>
        <div id="fin-inc-chart"></div>
        <details class="fin-inc-table-view"><summary>Show as a table</summary><div id="fin-inc-trend-table"></div></details>
    </section>`;
}

// The chosen granularity's points in one shape: label (axis), name (tooltip),
// value (null = no data), compare (last month, day view only), partial, day.
function trendPoints() {
    const t = state.report.trend;
    if (state.gran === "week") {
        return t.week.map(p => ({ label: dayLabel(p.week_start, { day: "numeric", month: "short" }),
            name: `Week of ${dayLabel(p.week_start, { day: "numeric", month: "short" })}`, value: p.value, partial: p.partial }));
    }
    if (state.gran === "month") {
        return t.month.map(p => ({ label: asDay(p.month).toLocaleDateString("en-GB", { month: "short" }),
            name: monthLabel(p.month), value: p.value, partial: p.partial }));
    }
    return t.day.map(p => ({ label: String(asDay(p.day).getDate()), name: dayLabel(p.day), value: p.value,
        compare: p.last_month, partial: p.partial, day: p.day }));
}

// The chart's top value: the smallest of these that fits, chosen so that a
// quarter of it is always a round figure (15k would give 3.75k gridlines).
function niceTop(v) {
    const mag = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 1.2, 1.6, 2, 2.4, 3.2, 4, 6, 8, 10]) if (m * mag >= v) return m * mag;
    return 10 * mag;
}

function trendSvg(pts, width) {
    const W = Math.max(300, Math.min(1000, Math.round(width || 720))), H = W < 520 ? 210 : 250;
    const L = 40, R = 8, T = 12, B = 26;
    const values = pts.flatMap(p => [p.value, p.compare]).filter(v => v != null).map(Number);
    const top = niceTop(Math.max(1, ...values));
    const y = v => T + (1 - v / top) * (H - T - B);
    const slot = (W - L - R) / pts.length, bw = Math.max(3, Math.min(44, slot * 0.64));
    const grid = [0, 0.25, 0.5, 0.75, 1].map(f => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(top * f)}" y2="${y(top * f)}"/>
        <text x="${L - 6}" y="${y(top * f) + 3}" text-anchor="end">${shortMoney(top * f)}</text>`).join("");
    const step = Math.max(1, Math.ceil(30 / slot));
    const bars = pts.map((p, i) => {
        const cx = L + slot * i + slot / 2, x = cx - bw / 2;
        let bar = "";
        if (p.value != null) {
            const v = Number(p.value), h = Math.max(0, y(0) - y(v)), r = Math.min(4, bw / 2, h);
            if (h > 0) bar = `<path class="bar${p.partial ? " partial" : ""}" d="M${x},${y(0)} V${y(v) + r} Q${x},${y(v)} ${x + r},${y(v)} H${x + bw - r} Q${x + bw},${y(v)} ${x + bw},${y(v) + r} V${y(0)} Z"/>`;
        }
        const tip = `${p.name}: ${p.value == null ? "no data" : money(p.value) + (p.partial ? " so far" : "")}` +
            (state.gran === "day" ? ` · last month ${p.compare == null ? "no data" : money(p.compare)}` : "");
        const label = (i % step === 0 || i === pts.length - 1) ? `<text x="${cx}" y="${H - 8}" text-anchor="middle">${esc(p.label)}</text>` : "";
        const click = p.day && p.value != null ? ` data-day="${p.day}"` : "";
        return `<g class="slot${click ? " clickable" : ""}"${click}><title>${esc(tip)}</title>
            <rect class="hit" x="${L + slot * i}" y="${T}" width="${slot}" height="${y(0) - T}"/>${bar}</g>${label}`;
    }).join("");
    // Last month as a dashed line, broken wherever it has no data.
    let line = "";
    if (state.gran === "day") {
        const runs = [];
        let run = [];
        pts.forEach((p, i) => {
            if (p.compare == null) { if (run.length) runs.push(run); run = []; return; }
            run.push(`${(L + slot * i + slot / 2).toFixed(1)},${y(Number(p.compare)).toFixed(1)}`);
        });
        if (run.length) runs.push(run);
        line = runs.map(r => r.length > 1 ? `<polyline class="compare" points="${r.join(" ")}"/>` : "").join("");
    }
    return `<svg class="fin-inc-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Income trend">
        <defs><pattern id="fin-inc-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" class="hatch"/></pattern></defs>
        ${grid}${bars}${line}<line class="base" x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}"/></svg>`;
}

function drawChart() {
    const box = $("fin-inc-chart");
    if (!box) return;
    const pts = trendPoints();
    box.innerHTML = trendSvg(pts, box.clientWidth);
    const thisLabel = state.gran === "day" ? monthLabel(state.month + "-01") : state.gran === "week" ? "Each week" : "Each month";
    $("fin-inc-legend").innerHTML = `<span><i class="fin-inc-key bar"></i>${esc(thisLabel)}</span>` +
        (state.gran === "day" ? `<span><i class="fin-inc-key compare"></i>Last month, same day</span>` : "") +
        `<span><i class="fin-inc-key partial"></i>Still counting</span>` +
        (state.gran === "day" ? `<span class="muted">Click a day to see its transactions</span>` : "");
    $("fin-inc-trend-table").innerHTML = `<div class="table-scroll table-compact"><table class="table-dense">
        <thead><tr><th>Period</th><th class="num">Income</th>${state.gran === "day" ? `<th class="num">Last month</th>` : ""}</tr></thead>
        <tbody>${pts.map(p => `<tr><td>${esc(p.name)}${p.partial ? " (so far)" : ""}</td>
            <td class="num">${p.value == null ? "no data" : money(p.value)}</td>
            ${state.gran === "day" ? `<td class="num">${p.compare == null ? "no data" : money(p.compare)}</td>` : ""}</tr>`).join("")}</tbody>
    </table></div>`;
}

// ---- Patterns ----

function patternsHtml(r) {
    const maxWeekday = Math.max(1, ...r.weekdays.map(w => Number(w.average || 0)));
    const weekdays = r.weekdays.map(w => `<div class="fin-inc-row">
        <span class="fin-inc-row-name">${w.weekday}</span>
        <span class="fin-inc-meter"><span style="width:${w.average == null ? 0 : (Number(w.average) / maxWeekday * 100).toFixed(1)}%"></span></span>
        <span class="fin-inc-row-value">${w.average == null ? "no data" : money(w.average)}</span>
        <span class="fin-inc-row-n muted" title="Days this average is based on">n=${w.days}</span></div>`).join("");

    const maxHour = Math.max(1, ...r.hours.map(h => Number(h.total)));
    const hours = r.hours.map(h => `<span class="fin-inc-hour" title="${String(h.hour).padStart(2, "0")}:00–${String((h.hour + 1) % 24).padStart(2, "0")}:00 · ${money(h.total)} · ${h.sales} sales">
        <span style="height:${(Number(h.total) / maxHour * 100).toFixed(1)}%"></span></span>`).join("");
    const peak = r.hours.reduce((a, b) => (Number(b.total) > Number(a.total) ? b : a));

    const packages = r.packages.map(p => `<button type="button" class="fin-inc-row fin-inc-row-btn" data-package="${esc(p.package)}" title="Show this package's transactions">
        <span class="fin-inc-row-name">${esc(p.package)}</span>
        <span class="fin-inc-meter"><span style="width:${p.share_pct ?? 0}%"></span></span>
        <span class="fin-inc-row-value">${money(p.total)}</span>
        <span class="fin-inc-row-n muted">${p.share_pct}%</span></button>`).join("");

    return `<div class="fin-inc-patterns">
        <section class="fin-inc-card"><h2 class="section-title">By weekday</h2>
            <p class="fin-inc-card-note">Average per completed day. n = how many days it rests on.</p>${weekdays}</section>
        <section class="fin-inc-card"><h2 class="section-title">By hour</h2>
            <p class="fin-inc-card-note">Busiest: ${String(peak.hour).padStart(2, "0")}:00–${String((peak.hour + 1) % 24).padStart(2, "0")}:00, ${money(peak.total)} this month.</p>
            <div class="fin-inc-hours" role="img" aria-label="Income by hour of day">${hours}</div>
            <div class="fin-inc-hours-axis"><span>00</span><span>06</span><span>12</span><span>18</span><span>23</span></div></section>
        <section class="fin-inc-card"><h2 class="section-title">Top packages</h2>
            <p class="fin-inc-card-note">Share of this month's income. Click one to see its sales.</p>${packages}</section>
    </div>`;
}

function buyersHtml(b) {
    if (!b) return "";
    return `<section class="fin-inc-card fin-inc-buyers"><h2 class="section-title">Customers</h2>
        <div class="summary-strip">
            <div class="summary-item"><strong>${Number(b.buyers).toLocaleString()}</strong> accounts paid</div>
            <div class="summary-item"><strong>${Number(b.first_time).toLocaleString()}</strong> paid for the first time</div>
            <div class="summary-item"><strong>${Number(b.repeat).toLocaleString()}</strong> paid more than once</div>
        </div>
        <p class="fin-inc-card-note">An account is one phone on one device, so one person with two phones counts twice.</p>
    </section>`;
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
    $("fin-inc-count").textContent = `${Number(data.matching).toLocaleString()} sales · ${money(data.matching_total)}`;
    if (!data.rows.length) {
        tbody.innerHTML = stateRow(6, "empty", "Try another search, or clear the filters.", "No matching sales");
        $("fin-inc-pager").innerHTML = "";
        return;
    }
    tbody.innerHTML = data.rows.map(r => `<tr>
        <td class="muted">${esc(new Date(r.at).toLocaleString("en-GB", { timeZone: "Africa/Nairobi", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }))}</td>
        <td class="fin-inc-account">${esc(r.account)}</td>
        <td>${esc(r.package)}</td>
        <td>${r.site ? esc(r.site) : `<span class="muted">Not linked to a site</span>`}</td>
        <td class="muted">${r.kind === "renewal" ? "Renewal" : "New account"}</td>
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

    $("fin-inc-body").addEventListener("click", (e) => {
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

    // The chart is drawn at the width it's shown at; redraw when that changes.
    let resizeTimer = null;
    window.addEventListener("resize", () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => { if (!$("view-finance-income").hidden && state.report?.has_data) drawChart(); }, 150);
    });

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
        load();
    });
}
