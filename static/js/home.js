// Dashboard (2026-11-14): the landing page. A row of headline figures, then
// one card of charts per area of the app (revenue, sites, people online,
// internet uplink, fibre, customers, batteries, technician activity, stock).
//
// GET /home says which areas this person may see; each area's summary is its
// own request (GET /home/<area>, db/home.py), loaded two at a time so the
// page never holds more than two database connections, and drawn the moment
// it arrives. Every figure is boiled down on the server from what the area's
// own page shows, so the two can't disagree; money arrives already formatted.
// The page re-reads quietly every two minutes while it is open and in view.
//
// Charts: a split bar for "how many of each" (always with its counts beside
// it, so colour is never the only cue), plain bars for rankings, a line for
// anything over time. One hue per chart except download/upload, which keep
// the two colours they have on Network Analysis.

import { authHeaders, getCurrentUser, navigate, registerLogoutHandler, registerRoute, showView } from "./common.js";
import { incomeBars } from "./revenue.js";

const REFRESH_MS = 120_000;
const TZ = "Africa/Nairobi";
const EAT_MS = 3 * 3600_000;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = n => Number(n ?? 0).toLocaleString("en-KE", { maximumFractionDigits: 1 });
const plural = (n, one, many = one + "s") => `${num(n)} ${n === 1 ? one : many}`;
const when = (iso, opts) => new Date(iso).toLocaleString("en-GB", { timeZone: TZ, ...opts });

const state = { sections: null, data: {}, raw: {}, failed: new Set(), gen: 0, timer: null, width: 0 };

// ---- What's on the page -----------------------------------------------------

// The headline tiles, in order; the first six this person has data for show.
const KPIS = [
    { section: "sites", route: "status", label: "Sites up",
      make: d => ({ value: num(d.up), sub: `of ${num(d.total)} sites`, flag: d.total - d.up ? [`${num(d.total - d.up)} not up`, "bad"] : ["All up", "good"] }) },
    { section: "sites", route: "trends", label: "People online", make: d => ({ value: num(d.people_online), sub: "across every site, right now" }) },
    { section: "revenue", route: "revenue", label: "Revenue today",
      make: d => ({ value: d.today ?? "No sales yet", sub: d.overview ? `so far · a day averages ${d.overview.average}` : "so far" }) },
    { section: "fibre", route: "olt", label: "Fibre ONUs online",
      make: d => d.configured ? { value: num(d.online), sub: `of ${num(d.total)}`, flag: d.weak ? [`${num(d.weak)} weak signal`, "warn"] : null } : null },
    { section: "customers", route: "customers", label: "Active customers",
      make: d => ({ value: num(d.active), sub: `of ${num(d.total)} accounts`, flag: d.expiring_soon ? [`${num(d.expiring_soon)} expire within ${d.expiring_days} days`, "warn"] : null }) },
    { section: "activity", route: "activity-overview", label: "Jobs this week",
      make: d => ({ value: num(d.total), sub: "reports in the last 7 days", flag: d.still_open ? [`${num(d.still_open)} still open`, "warn"] : null }) },
    { section: "batteries", route: "dashboard", label: "Batteries",
      make: d => ({ value: num(d.total), sub: d.moving ? `${plural(d.moving, "movement")} open` : "in the fleet" }) },
    { section: "stock", route: "stock", label: "Low stock",
      make: d => ({ value: num(d.low_count), sub: d.with_level ? "items at or under their reorder level" : "no reorder levels set yet",
                    flag: d.out ? [`${num(d.out)} out of stock`, "bad"] : null }) },
];
const KPI_LIMIT = 6;

// The cards, in page order. `span` is columns out of 12 on a wide screen.
const CARDS = [
    { id: "revenue", section: "revenue", title: "Revenue", sub: "Last 14 days", span: 8, go: ["revenue-overview", "Revenue overview"], draw: drawRevenue },
    { id: "sites", section: "sites", title: "Sites", sub: "Right now", span: 4, go: ["status", "Network Status"], draw: drawSites },
    { id: "people", section: "people", title: "People online", sub: "This week, hour by hour", span: 8, go: ["trends", "Trends"], draw: drawPeople },
    { id: "busiest", section: "sites", title: "Busiest sites", sub: "People online right now", span: 4, go: ["status", "Network Status"], draw: drawBusiest },
    { id: "uplink", section: "uplink", title: "Internet uplink", sub: "Last 24 hours", span: 8, go: ["network", "Network Analysis"], draw: drawUplink },
    { id: "stock", section: "stock", title: "Stock", sub: "What needs reordering", span: 4, go: ["stock", "Stock"], draw: drawStock },
    { id: "fibre", section: "fibre", title: "Fibre", sub: "ONUs on the OLT", span: 4, go: ["olt", "OLT"], draw: drawFibre },
    { id: "customers", section: "customers", title: "Customers", sub: "PPPoE accounts", span: 4, go: ["customers", "Customers"], draw: drawCustomers },
    { id: "batteries", section: "batteries", title: "Batteries", sub: "Where they are and their charge", span: 4, go: ["dashboard", "Batteries"], draw: drawBatteries },
    { id: "activity", section: "activity", title: "Technician activity", sub: "Last 7 days", span: 12, go: ["activity-overview", "Activity overview"], draw: drawActivity },
];

// ---- Small building blocks ---------------------------------------------------

const tip = text => `data-tip="${esc(text)}"`;
const share = (n, total) => (total ? `${Math.round((n / total) * 100)}%` : "0%");
const note = (text, tone = "") => `<p class="home-note ${tone ? "tone-" + tone : ""}">${tone ? '<i class="home-dot"></i>' : ""}${esc(text)}</p>`;
const figure = (value, label) => `<div class="home-figure"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
const figures = items => `<div class="home-figures">${items.filter(Boolean).map(([v, l]) => figure(v, l)).join("")}</div>`;
const heading = text => `<h3 class="home-h">${esc(text)}</h3>`;

// "How many of each": one bar split by share, and the counts spelled out
// beside it. Slices are separated by a gap in the card's own colour.
function split(parts, what) {
    const total = parts.reduce((a, p) => a + p.count, 0);
    if (!total) return `<p class="home-none">Nothing recorded yet.</p>`;
    const said = parts.map(p => `${p.count} ${p.label}`).join(", ");
    return `<div class="home-split" role="img" aria-label="${esc(what)}: ${esc(said)}">${parts.map((p, i) =>
        `<span class="home-slice tone-${p.tone}" style="flex-grow:${p.count};--i:${i}" ${tip(`${p.label}: ${num(p.count)} (${share(p.count, total)})`)}></span>`).join("")}</div>
        <ul class="home-legend">${parts.map(p =>
        `<li class="tone-${p.tone}"><i class="home-dot"></i><span>${esc(p.label)}</span><b>${num(p.count)}</b><em>${share(p.count, total)}</em></li>`).join("")}</ul>`;
}

// A ranking: longest bar first, every row named and counted.
function ranked(rows, unit, many) {
    if (!rows.length) return `<p class="home-none">Nothing recorded yet.</p>`;
    const max = Math.max(1, ...rows.map(r => r.count));
    return `<div class="home-ranked">${rows.map((r, i) => `<div class="home-rank" ${tip(`${r.label}: ${plural(r.count, unit, many)}`)}>
        <span class="home-rank-label">${esc(r.label)}</span>
        <span class="home-rank-track"><span class="home-rank-fill" style="width:${((r.count / max) * 100).toFixed(1)}%;--i:${i}"></span></span>
        <b>${num(r.count)}</b></div>`).join("")}</div>`;
}

function howLong(iso) {
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (mins < 60) return `${mins} min`;
    if (mins < 48 * 60) return `${Math.round(mins / 60)} h`;
    return `${Math.round(mins / 1440)} days`;
}

// A round number at or just above `max`, so the top gridline is readable.
function niceTop(max) {
    if (!(max > 0)) return 1;
    const mag = Math.pow(10, Math.floor(Math.log10(max)));
    return [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map(m => m * mag).find(v => v >= max);
}

// ---- Line chart (people online; download and upload) -------------------------
// Drawn at the width it is shown at, so its text is the same size as the
// page's. A reading nobody took is a gap in the line, never a zero. Moving
// over the chart shows a rule, a dot per line and the values at that time.

const lineCharts = new WeakMap();   // chart box -> what its pointer handler needs

function lineChart(points, series, { width, unit = "", area = false }) {
    const W = Math.max(300, Math.min(1200, Math.round(width || 720))), H = W < 520 ? 190 : 230;
    const L = 44, R = 12, T = 12, B = 26;
    const ts = points.map(p => new Date(p.t).getTime());
    const t0 = ts[0], t1 = ts[ts.length - 1], span = Math.max(1, t1 - t0);
    const top = niceTop(Math.max(0, ...points.flatMap(p => series.map(s => p[s.key] ?? 0))));
    const x = t => (ts.length === 1 ? (L + W - R) / 2 : L + ((t - t0) / span) * (W - L - R));
    const y = v => T + (1 - v / top) * (H - T - B);

    const grid = [0, 0.5, 1].map(f => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(top * f)}" y2="${y(top * f)}"/>
        <text x="${L - 8}" y="${y(top * f) + 3.5}" text-anchor="end">${num(top * f)}</text>`).join("");

    // Time labels on round EAT hours (a day or less) or EAT midnights (longer).
    const week = span > 36 * 3600_000;
    const stepMs = week ? 86_400_000 : (span > 13 * 3600_000 ? 6 : 3) * 3600_000;
    const ticks = [];
    for (let t = Math.ceil((t0 + EAT_MS) / stepMs) * stepMs - EAT_MS; t <= t1; t += stepMs) ticks.push(t);
    const every = Math.max(1, Math.ceil((ticks.length * 62) / (W - L - R)));
    const labels = ticks.filter((_, i) => i % every === 0).map(t => {
        const text = week ? when(t, { weekday: "short", day: "2-digit" }) : when(t, { hour: "2-digit", minute: "2-digit" });
        const anchor = x(t) < L + 24 ? "start" : x(t) > W - R - 24 ? "end" : "middle";
        return `<line class="tick" x1="${x(t)}" x2="${x(t)}" y1="${H - B}" y2="${H - B + 4}"/><text x="${x(t)}" y="${H - 7}" text-anchor="${anchor}">${esc(text)}</text>`;
    }).join("");

    const lines = series.map(s => {
        const runs = [];                               // unbroken stretches of readings
        points.forEach((p, i) => {
            if (p[s.key] == null) return;
            const last = runs[runs.length - 1];
            if (last && points[i - 1]?.[s.key] != null) last.push(i); else runs.push([i]);
        });
        return runs.map(run => {
            if (run.length === 1) return `<circle class="pt ${s.cls}" cx="${x(ts[run[0]])}" cy="${y(points[run[0]][s.key])}" r="3"/>`;
            const d = run.map((i, n) => `${n ? "L" : "M"}${x(ts[i]).toFixed(1)},${y(points[i][s.key]).toFixed(1)}`).join(" ");
            const fill = area && s === series[0]
                ? `<path class="area ${s.cls}" d="${d} L${x(ts[run[run.length - 1]]).toFixed(1)},${y(0)} L${x(ts[run[0]]).toFixed(1)},${y(0)} Z"/>` : "";
            return `${fill}<path class="line ${s.cls}" d="${d}"/>`;
        }).join("");
    }).join("");

    const label = series.map(s => s.label).join(" and ");
    const svg = `<svg class="home-line" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)} over time">
        ${grid}<line class="base" x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}"/>${labels}${lines}
        <g class="hover" hidden><line class="rule" y1="${T}" y2="${y(0)}"/>${series.map(s => `<circle class="dot ${s.cls}" r="4"/>`).join("")}</g>
        <rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"/></svg>`;
    return { svg, meta: { points, series, ts, W, x, y, unit, week } };
}

function mountLine(box, points, series, opts) {
    const { svg, meta } = lineChart(points, series, { ...opts, width: box.clientWidth });
    box.innerHTML = svg;
    lineCharts.set(box, meta);
    if (box.dataset.wired) return;
    box.dataset.wired = "1";
    const move = e => {
        const m = lineCharts.get(box), el = box.querySelector("svg");
        if (!m || !el) return;
        const r = el.getBoundingClientRect(), px = ((e.clientX - r.left) / r.width) * m.W;
        // The nearest moment that has at least one reading.
        let best = -1;
        m.ts.forEach((t, i) => {
            if (m.series.every(s => m.points[i][s.key] == null)) return;
            if (best < 0 || Math.abs(m.x(t) - px) < Math.abs(m.x(m.ts[best]) - px)) best = i;
        });
        if (best < 0) return;
        const p = m.points[best], hover = el.querySelector(".hover");
        hover.removeAttribute("hidden");
        hover.querySelector(".rule").setAttribute("x1", m.x(m.ts[best]));
        hover.querySelector(".rule").setAttribute("x2", m.x(m.ts[best]));
        hover.querySelectorAll(".dot").forEach((dot, i) => {
            const v = p[m.series[i].key];
            dot.toggleAttribute("hidden", v == null);
            if (v != null) { dot.setAttribute("cx", m.x(m.ts[best])); dot.setAttribute("cy", m.y(v)); }
        });
        const at = when(p.t, m.week ? { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" });
        showTip(`<b>${esc(at)}</b>${m.series.map(s => `<span class="home-tip-row"><i class="home-key ${s.cls}"></i>${esc(s.label)}<b>${p[s.key] == null ? "no reading" : num(p[s.key]) + m.unit}</b></span>`).join("")}`, e);
    };
    const leave = () => { box.querySelector(".hover")?.setAttribute("hidden", ""); hideTip(); };
    box.addEventListener("pointermove", move);
    box.addEventListener("pointerdown", move);
    box.addEventListener("pointerleave", leave);
}

// ---- The floating label ------------------------------------------------------

function showTip(html, e) {
    const el = $("home-tip"), view = $("view-home").getBoundingClientRect();
    el.innerHTML = html;
    el.hidden = false;
    const w = el.offsetWidth, h = el.offsetHeight;
    let left = e.clientX - view.left + 14;
    if (left + w > view.width - 4) left = e.clientX - view.left - w - 14;
    const above = e.clientY - h - 12;                          // under the top bar there's no room: go below the pointer
    const top = (above < 76 ? e.clientY + 18 : above) - view.top;
    el.style.transform = `translate(${Math.max(4, left)}px, ${top}px)`;
}
function hideTip() { $("home-tip").hidden = true; }

// ---- Each card ---------------------------------------------------------------

function drawRevenue(d, body) {
    const o = d.overview;
    if (!o || !d.points.length) { body.innerHTML = `<p class="home-none">No completed days of sales in the last 14 yet.</p>`; return; }
    body.innerHTML = `${figures([
        [d.today ?? "No sales yet", "Today so far"], [o.average, "A day, on average"], [o.total, `${plural(o.periods, "day")} in total`],
        d.direction ? [d.direction, "Direction"] : null])}
        <div class="home-chart" id="home-revenue-chart"></div>
        <div class="rev-legend"><span><i class="rev-key above"></i>Above average</span><span><i class="rev-key below"></i>Below average</span>
        <span><i class="rev-key avg"></i>Average ${esc(o.average)}</span>${o.partial_left_out ? `<span><i class="rev-key partial"></i>Today so far, not counted</span>` : ""}</div>`;
    const box = $("home-revenue-chart");
    box.innerHTML = incomeBars(d.points, o.average_value, box.clientWidth, "home-rev-hatch");
}

function drawSites(d, body) {
    const down = d.down.map(s => `<li><button type="button" class="home-row-link" data-go="site-detail/${s.id}">
        <span>${esc(s.name)}</span><em>${s.since ? `down ${howLong(s.since)}` : "down"}</em></button></li>`).join("");
    body.innerHTML = `${split(d.states, "Sites by state")}
        ${d.down.length ? `${heading("Down now")}<ul class="home-rows">${down}</ul>${d.down_more ? note(`and ${plural(d.down_more, "more site")}`) : ""}`
                        : d.total ? note("Every site is up.", "good") : ""}`;
}

function drawBusiest(d, body) {
    body.innerHTML = d.busiest.length ? ranked(d.busiest.map(s => ({ label: s.name, count: s.people })), "person online", "people online")
                                      : `<p class="home-none">No one is counted online at any site right now.</p>`;
}

function drawPeople(d, body) {
    const measured = d.points.filter(p => p.people != null);
    const peak = d.peak != null ? [num(d.peak), d.peak_at ? `Peak, ${when(d.peak_at, { weekday: "short", hour: "2-digit", minute: "2-digit" })}` : "Peak"] : null;
    const now = state.data.sites ? [num(state.data.sites.people_online), "Right now"] : null;
    body.innerHTML = `${figures([now, d.average != null ? [num(d.average), "Average this week"] : null, peak])}
        ${measured.length >= 2 ? `<div class="home-chart" id="home-people-chart"></div>`
                               : `<p class="home-none">${measured.length ? "Only one hour has been counted this week so far, so there is no line to draw yet." : "No counts have come in this week yet."}</p>`}`;
    if (measured.length >= 2) mountLine($("home-people-chart"), d.points, [{ key: "people", label: "People online", cls: "one" }], { area: true });
}

function drawUplink(d, body) {
    if (!d.configured) { body.innerHTML = `<p class="home-none">The router isn't reporting to OPS yet.</p>`; return; }
    const tone = { green: "good", amber: "warn", red: "bad" }[d.level] || "idle";
    const word = { green: "Healthy", amber: "Needs a look", red: "Problem" }[d.level] || "Unknown";
    const mbps = v => (v == null ? "No reading" : `${num(v)} Mbps`);
    body.innerHTML = `${note(d.reason ? `${word}: ${d.reason}` : word, tone)}
        ${figures([[mbps(d.download_mbps), "Download now"], [mbps(d.upload_mbps), "Upload now"],
                   d.peak_download_mbps != null ? [mbps(d.peak_download_mbps), "Peak download today"] : null])}
        ${d.points.length >= 2 ? `<div class="home-keys"><span><i class="home-key down"></i>Download</span><span><i class="home-key up"></i>Upload</span><span class="home-unit">Mbps, 5-minute averages</span></div>
        <div class="home-chart" id="home-uplink-chart"></div>` : `<p class="home-none">No traffic readings in the last 24 hours.</p>`}`;
    if (d.points.length >= 2) mountLine($("home-uplink-chart"), d.points,
        [{ key: "download", label: "Download", cls: "down" }, { key: "upload", label: "Upload", cls: "up" }], { unit: " Mbps" });
}

function drawFibre(d, body) {
    if (!d.configured) { body.innerHTML = `<p class="home-none">SmartOLT isn't connected to OPS.</p>`; return; }
    body.innerHTML = `${heading("Status")}${split(d.status, "ONUs by status")}
        ${d.signal.length ? `${heading("Signal")}${split(d.signal, "ONUs by signal quality")}` : ""}
        ${d.stale && d.as_of ? note(`Status last read ${when(d.as_of, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}; it may have changed since.`, "warn") : ""}`;
}

function drawCustomers(d, body) {
    body.innerHTML = `${split(d.status, "Customer accounts by state")}
        ${d.expiring_soon ? note(`${plural(d.expiring_soon, "active account")} ${d.expiring_soon === 1 ? "expires" : "expire"} within ${d.expiring_days} days.`, "warn") : ""}
        ${d.switched_off ? note(`${plural(d.switched_off, "account")} switched off.`) : ""}
        ${d.by_plan.length ? `${heading("Active accounts by package")}${ranked(d.by_plan, "account")}` : ""}`;
}

function drawBatteries(d, body) {
    body.innerHTML = `${heading("Where they are")}${split(d.where, "Batteries by place")}
        ${heading("Charge")}${split(d.charge, "Batteries by charge")}
        ${d.at_dark_sites ? note(`${plural(d.at_dark_sites, "battery", "batteries")} at a site that is down.`, "warn") : ""}
        ${d.moving ? note(`${plural(d.moving, "movement")} open.`) : ""}`;
}

function drawActivity(d, body) {
    const max = Math.max(1, ...d.per_day.map(p => p.count)), peak = d.per_day.reduce((a, p) => (p.count > a.count ? p : a), { count: 0 });
    const cols = d.per_day.map((p, i) => {
        const day = new Date(p.day + "T12:00:00Z");
        const name = day.toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" });
        const full = day.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", timeZone: "UTC" });
        return `<div class="home-col" ${tip(`${full}: ${plural(p.count, "report")}`)}>
            <span class="home-col-plot"><span class="home-col-bar${p.count ? "" : " none"}" style="height:${((p.count / max) * 100).toFixed(1)}%;--i:${i}">
            ${p.count && p === peak ? `<b>${num(p.count)}</b>` : ""}</span></span><span class="home-col-label">${name}</span></div>`;
    }).join("");
    body.innerHTML = `<div class="home-two"><div>
            ${figures([[num(d.total), "Reports"], [num(d.technicians), d.technicians === 1 ? "Technician reporting" : "Technicians reporting"], [num(d.still_open), "Still open"]])}
            ${d.total ? `<div class="home-cols" role="img" aria-label="Reports per day">${cols}</div>` : `<p class="home-none">No reports in the last 7 days.</p>`}
        </div><div>${heading("By category")}${ranked(d.by_category, "report")}</div></div>`;
}

function drawStock(d, body) {
    if (!d.items) { body.innerHTML = `<p class="home-none">No stock items yet.</p>`; return; }
    const rows = d.low.map((r, i) => {
        const out = r.on_hand <= 0, fill = r.reorder_level ? Math.min(100, (r.on_hand / r.reorder_level) * 100) : 0;
        return `<div class="home-stock tone-${out ? "bad" : "warn"}" ${tip(`${r.name}: ${num(r.on_hand)} on hand, reorder at ${num(r.reorder_level)}`)}>
            <span class="home-stock-name">${esc(r.name)}</span><span class="home-stock-qty"><b>${num(r.on_hand)}</b> of ${num(r.reorder_level)}</span>
            <span class="home-rank-track"><span class="home-rank-fill" style="width:${fill.toFixed(1)}%;--i:${i}"></span></span></div>`;
    }).join("");
    body.innerHTML = !d.with_level ? `${note("No item has a reorder level yet, so nothing can be flagged as low.")}${heading("Items by category")}${ranked(d.by_category, "item")}`
        : d.low_count ? `${note(`${plural(d.low_count, "item")} at or under ${d.low_count === 1 ? "its" : "their"} reorder level${d.out ? `, ${num(d.out)} with none left` : ""}.`, "warn")}
            <div class="home-stocks">${rows}</div>${d.low_count > d.low.length ? note(`and ${plural(d.low_count - d.low.length, "more item")}`) : ""}`
        : `${note(`All ${plural(d.with_level, "item")} with a reorder level ${d.with_level === 1 ? "is" : "are"} above it.`, "good")}${heading("Items by category")}${ranked(d.by_category, "item")}`;
}

// ---- Building and filling the page -------------------------------------------

function greet() {
    const first = (getCurrentUser()?.name || "").trim().split(/\s+/)[0];
    const hour = Number(when(Date.now(), { hour: "2-digit", hour12: false }));
    const part = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
    $("home-greeting").textContent = first ? `${part}, ${first}` : part;
    $("home-sub").textContent = `${when(Date.now(), { weekday: "long", day: "numeric", month: "long" })} · Jasiri Net at a glance.`;
}

function build() {
    const have = new Set(state.sections);
    $("home-nothing").hidden = have.size > 0;
    $("home-kpis").innerHTML = KPIS.filter(k => have.has(k.section)).slice(0, KPI_LIMIT).map(k =>
        `<button type="button" class="home-kpi is-loading" data-kpi="${KPIS.indexOf(k)}" data-go="${k.route}">
            <span class="home-kpi-label">${esc(k.label)}</span><span class="home-kpi-value"><span class="skeleton" style="width:60%"></span></span>
            <span class="home-kpi-sub">&nbsp;</span></button>`).join("");
    $("home-grid").innerHTML = CARDS.filter(c => have.has(c.section)).map(c =>
        `<article class="home-card" data-card="${c.id}" style="--span:${c.span}">
            <header class="home-card-head"><div><h2>${esc(c.title)}</h2><p>${esc(c.sub)}</p></div>
            <button type="button" class="home-open" data-go="${c.go[0]}" aria-label="Open ${esc(c.go[1])}">${esc(c.go[1])}
                <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 2.5L8 6l-3.5 3.5"/></svg></button></header>
            <div class="home-card-body"><span class="skeleton" style="width:45%"></span><span class="skeleton" style="height:72px"></span><span class="skeleton" style="width:70%"></span></div>
        </article>`).join("");
}

function drawSection(name) {
    const d = state.data[name];
    document.querySelectorAll("#home-kpis .home-kpi").forEach(el => {
        const k = KPIS[Number(el.dataset.kpi)];
        if (k.section !== name) return;
        const made = d ? k.make(d) : null;
        el.classList.remove("is-loading");
        if (!made) { el.hidden = true; return; }
        el.querySelector(".home-kpi-value").textContent = made.value;
        el.querySelector(".home-kpi-sub").innerHTML = esc(made.sub) +
            (made.flag ? `<span class="home-flag tone-${made.flag[1]}"><i class="home-dot"></i>${esc(made.flag[0])}</span>` : "");
    });
    CARDS.filter(c => c.section === name).forEach(c => {
        const body = document.querySelector(`.home-card[data-card="${c.id}"] .home-card-body`);
        if (!body) return;
        if (!d) { body.innerHTML = `<p class="home-none">Couldn't load this just now. It will try again shortly.</p>`; return; }
        c.draw(d, body);
    });
    // People's "Right now" figure comes from the sites summary.
    if (name === "sites" && state.data.people) drawSection("people");
}

async function loadSection(name, gen) {
    try {
        const res = await fetch(`/home/${name}`, { headers: authHeaders() });
        if (gen !== state.gen) return;
        if (!res.ok) throw new Error(String(res.status));
        const text = await res.text();
        if (gen !== state.gen) return;
        state.failed.delete(name);
        if (state.raw[name] === text) return;                 // nothing changed: leave the card alone
        state.raw[name] = text;
        state.data[name] = JSON.parse(text);
        drawSection(name);
    } catch (e) {
        if (gen !== state.gen) return;
        state.failed.add(name);
        if (!state.data[name]) drawSection(name);             // keep the last good figures if there are any
    }
}

async function loadAll() {
    const gen = state.gen;
    if (!state.sections) {
        const res = await fetch("/home", { headers: authHeaders() });
        if (gen !== state.gen || !res.ok) return;
        state.sections = (await res.json()).sections;
        build();
    }
    const queue = [...state.sections];
    const worker = async () => { while (queue.length && gen === state.gen) await loadSection(queue.shift(), gen); };
    await Promise.all([worker(), worker()]);                  // two at a time: see the note at the top
    if (gen !== state.gen) return;
    const at = when(Date.now(), { hour: "2-digit", minute: "2-digit" });
    $("home-updated").textContent = state.failed.size ? `Some figures couldn't be refreshed at ${at}` : `Updated ${at}`;
}

const isOpen = () => !$("view-home").hidden && !$("app-layout").hidden;

function reset() {
    state.gen++;
    Object.assign(state, { sections: null, data: {}, raw: {} });
    state.failed.clear();
    $("home-kpis").innerHTML = $("home-grid").innerHTML = $("home-updated").textContent = "";
    hideTip();
}

export function initHome() {
    registerRoute("home", () => {
        showView("view-home");
        greet();
        // Redraw what's already here at the size it now has (a summary can
        // land while another page is open), then re-read everything.
        Object.keys(state.data).forEach(drawSection);
        loadAll();
    });

    const view = $("view-home");
    view.addEventListener("click", e => {
        const go = e.target.closest("[data-go]");
        if (go) navigate(go.dataset.go);
    });
    // The floating label for anything that carries data-tip.
    view.addEventListener("pointermove", e => {
        const el = e.target.closest?.("[data-tip]");
        if (el) showTip(esc(el.dataset.tip), e);
        else if (!e.target.closest?.(".home-chart")) hideTip();
    });
    view.addEventListener("pointerleave", hideTip);

    // Charts are drawn at their box's width, so redraw them when it changes.
    new ResizeObserver(() => {
        const w = $("home-grid").clientWidth;
        if (!w || w === state.width) return;
        state.width = w;
        ["revenue", "people", "uplink"].forEach(name => { if (state.data[name]) drawSection(name); });
    }).observe($("home-grid"));

    state.timer = setInterval(() => { if (isOpen() && document.visibilityState === "visible") loadAll(); }, REFRESH_MS);
    // A different person may sign in next: nothing of this one's stays behind.
    registerLogoutHandler(reset);
}
