import { authHeaders, showView, navigate, registerRoute } from "./common.js";

/* Sites → Trends: every site over one window, ranked against each other.
   Same honesty rules as the site report — unwatched time is never downtime,
   an activity site never gets an uptime percentage, a peak only exists
   inside the raw-row horizon — and money only when the response carries it. */

const UPTIME_TARGET = 95;
const THIN_COVERAGE = 90;

// Same scheme as Status and the site report: ▲ means Down everywhere.
const GLYPH = {
    online:   { shape: "●", cls: "is-online" },
    offline:  { shape: "▲", cls: "is-down" },
    flapping: { shape: "◆", cls: "is-flapping" },
    unknown:  { shape: "○", cls: "is-unknown" },
};

let kind = "week";   // "day" | "week" | "month"
let value = null;    // "YYYY-MM-DD" (a day, or a week's Monday) or "YYYY-MM"
let dataFrom = null; // first day the fleet has anything recorded, from the last response
let sort = { key: "uptime_pct", dir: 1 };
let lastData = null;
let loadSeq = 0;

function esc(value) {
    const d = document.createElement("div");
    d.textContent = value == null ? "" : String(value);
    return d.innerHTML;
}

function money(kes) {
    return `KES ${Math.round(Number(kes || 0)).toLocaleString()}`;
}

function dur(seconds) {
    const mins = Math.round((seconds || 0) / 60);
    if (mins < 60) return `${mins}m`;
    const hours = Math.floor(mins / 60);
    return `${hours}h ${String(mins % 60).padStart(2, "0")}m`;
}

function shortDay(iso) {
    return new Date(iso).toLocaleDateString([], { day: "numeric", month: "short" });
}

/* ---- Periods ----
   Always one named day, week (Monday–Sunday) or month, Nairobi time — same
   helpers as the site report's, duplicated per this codebase's habit. */

const DAY_MS = 86400000;

// Today in Nairobi as a UTC-midnight Date, so the lists agree with the
// backend's EAT calendar whatever timezone the viewer's device is in.
function eatToday() {
    const n = new Date(Date.now() + 3 * 3600000);
    return new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate()));
}

const isoDay = date => date.toISOString().slice(0, 10);
const utcFmt = (date, opts) => date.toLocaleDateString("en-GB", { timeZone: "UTC", ...opts });
const mondayOf = date => new Date(date.getTime() - ((date.getUTCDay() + 6) % 7) * DAY_MS);

function monthLabel(m) {
    const [y, mo] = m.split("-").map(Number);
    return utcFmt(new Date(Date.UTC(y, mo - 1, 1)), { month: "long", year: "numeric" });
}

// "15–21 Sep", or "29 Sep – 5 Oct" when the week straddles a month.
function weekLabel(monday) {
    const start = new Date(`${monday}T00:00:00Z`);
    const end = new Date(start.getTime() + 6 * DAY_MS);
    if (start.getUTCMonth() === end.getUTCMonth()) {
        return `${start.getUTCDate()}–${utcFmt(end, { day: "numeric", month: "short" })}`;
    }
    return `${utcFmt(start, { day: "numeric", month: "short" })} – ${utcFmt(end, { day: "numeric", month: "short" })}`;
}

const dayLabel = day => utcFmt(new Date(`${day}T00:00:00Z`), { weekday: "short", day: "numeric", month: "short" });

function currentPeriod(k) {
    const today = eatToday();
    if (k === "day") return isoDay(today);
    if (k === "month") return isoDay(today).slice(0, 7);
    return isoDay(mondayOf(today));
}

// Every week or month from now back to the first with any data in it.
function periodOptions(k) {
    const today = eatToday();
    const from = dataFrom ? new Date(`${dataFrom}T00:00:00Z`) : today;
    const out = [];
    if (k === "month") {
        const stop = isoDay(from).slice(0, 7);
        for (let i = 0; ; i++) {
            const v = isoDay(new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - i, 1))).slice(0, 7);
            out.push({ value: v, label: i === 0 ? `This month · ${monthLabel(v)}` : monthLabel(v) });
            if (v <= stop) return out;
        }
    }
    const stop = isoDay(mondayOf(from));
    for (let i = 0; ; i++) {
        const v = isoDay(new Date(mondayOf(today).getTime() - i * 7 * DAY_MS));
        const prefix = i === 0 ? "This week · " : i === 1 ? "Last week · " : "";
        out.push({ value: v, label: `${prefix}${weekLabel(v)}` });
        if (v <= stop) return out;
    }
}

function periodPhrase(k, v) {
    if (k === "day") return dayLabel(v);
    return k === "month" ? monthLabel(v) : weekLabel(v);
}

// Day gets a date picker bounded by the data; week/month a dropdown.
function setPeriodControls() {
    document.querySelectorAll("#fleet-range button").forEach(b =>
        b.setAttribute("aria-pressed", String(b.dataset.mode === kind)));
    const select = document.getElementById("fleet-period-select");
    const dayInput = document.getElementById("fleet-day-input");
    select.hidden = kind === "day";
    dayInput.hidden = kind !== "day";
    if (kind === "day") {
        dayInput.min = dataFrom || currentPeriod("day");
        dayInput.max = currentPeriod("day");
        dayInput.value = value;
        return;
    }
    const options = periodOptions(kind);
    if (!options.some(o => o.value === value)) options.push({ value, label: periodPhrase(kind, value) });
    select.innerHTML = options.map(o => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
    select.value = value;
}

function dayTime(iso) {
    const d = new Date(iso);
    const day = d.toLocaleDateString([], { weekday: "short", day: "numeric" }).replace(/(\d+)\s+(\D+)/, "$2 $1");
    return `${day} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}`;
}

function pct(value) {
    return value == null ? "—" : `${value.toFixed(1)}%`;
}

function kpi(label, figure, sub, tone = "") {
    return `<div class="fleet-kpi">
        <div class="fleet-kpi-label">${esc(label)}</div>
        <div class="fleet-kpi-figure ${tone ? `is-${tone}` : ""}">${esc(figure)}</div>
        <div class="fleet-kpi-sub">${esc(sub)}</div>
    </div>`;
}

function renderKpis(d, canRevenue) {
    const t = d.totals;
    const cards = [
        kpi("Fleet uptime", pct(t.uptime_pct), "weighted by watched time",
            t.uptime_pct != null && t.uptime_pct < UPTIME_TARGET ? "bad" : ""),
        kpi("Coverage", pct(t.coverage_pct),
            t.thin_coverage_sites ? `${t.thin_coverage_sites} site${t.thin_coverage_sites === 1 ? "" : "s"} under ${THIN_COVERAGE}% — marked below` : "time Ops was being told anything",
            t.thin_coverage_sites ? "warn" : ""),
        kpi("Sites with an outage", `${t.sites_with_outage} of ${t.measured_sites}`,
            t.activity_sites ? `${t.activity_sites} activity site${t.activity_sites === 1 ? "" : "s"} can't be measured` : "every site measured"),
        kpi("Average people", t.avg_people,
            t.peak_people != null ? `across all sites · peak ${t.peak_people} ${dayTime(t.peak_at)}` : "across all sites · devices, not people"),
    ];
    if (canRevenue) {
        cards.push(kpi("Revenue", money(t.revenue_kes), `${t.sales_count.toLocaleString()} sales · incl. not placed`));
    }
    const box = document.getElementById("fleet-kpis");
    box.classList.toggle("is-four", !canRevenue);
    box.innerHTML = cards.join("");
}

// A day gets a bar per hour; a week or a month a bar per day, since a bar
// per hour over that many days is unreadable slivers.
function renderPeople(d) {
    const wrap = document.getElementById("fleet-people-wrap");
    document.getElementById("fleet-people-hint").textContent =
        d.day ? "hourly · all sites" : "daily · all sites";
    if (d.day) { renderPeopleHourly(d, wrap); return; }
    const days = d.people_daily;
    if (!days.some(x => x.avg != null)) {
        wrap.innerHTML = `<div class="fleet-empty">No headcounts recorded in this window.</div>`;
        return;
    }
    const max = Math.max(1, ...days.map(x => Math.max(x.avg || 0, x.peak || 0)));
    const busiest = days.reduce((b, x) => ((x.avg || 0) > (b ? b.avg : -1) ? x : b), null);
    wrap.innerHTML = `
        <div class="fleet-bars ${days.length <= 31 ? "is-sparse" : ""}">
            ${days.map(x => x.avg == null
                ? `<div class="fleet-bar-slot" title="${esc(shortDay(`${x.day}T12:00:00`))} — nothing recorded"><div class="fleet-bar is-averaged" style="height:3px"></div></div>`
                : `<div class="fleet-bar-slot" title="${esc(shortDay(`${x.day}T12:00:00`))} — ${x.avg} on average${x.has_peak ? `, peak ${x.peak}` : ""}">
                    <div class="fleet-bar ${x.has_peak ? "" : "is-averaged"}" style="height:${Math.max(2, Math.round(x.avg / max * 100))}%"></div>
                   </div>`
            ).join("")}
        </div>
        <div class="fleet-axis"><span>${esc(shortDay(`${days[0].day}T12:00:00`))}</span><span>${esc(shortDay(`${days[days.length - 1].day}T12:00:00`))}</span></div>
        <p class="fleet-says">Busiest day was <strong>${busiest.avg}</strong> people on average, ${esc(shortDay(`${busiest.day}T12:00:00`))}.</p>`;
}

function renderPeopleHourly(d, wrap) {
    if (!d.people.length) {
        wrap.innerHTML = `<div class="fleet-empty">No headcounts recorded on this day.</div>`;
        return;
    }
    const max = Math.max(1, ...d.people.map(p => p.avg));
    const busiest = d.people.reduce((b, p) => (p.avg > (b ? b.avg : -1) ? p : b), null);
    wrap.innerHTML = `
        <div class="fleet-bars is-sparse">
            ${d.people.map(p => `<div class="fleet-bar-slot" title="${esc(hourOf(p.hour))} — ${p.avg} on average${p.has_peak ? `, peak ${p.peak}` : ""}">
                <div class="fleet-bar ${p.has_peak ? "" : "is-averaged"}" style="height:${Math.max(2, Math.round(p.avg / max * 100))}%"></div>
            </div>`).join("")}
        </div>
        <div class="fleet-axis"><span>${esc(hourOf(d.people[0].hour))}</span><span>${esc(hourOf(d.people[d.people.length - 1].hour))}</span></div>
        <p class="fleet-says">Busiest hour was <strong>${busiest.avg}</strong> people on average, at ${esc(hourOf(busiest.hour))}.</p>`;
}

// "14:00", Nairobi time — Trends' other helpers use the device clock, but
// an hour of a Nairobi day has to read as that hour wherever it's opened.
const hourOf = iso => new Date(iso).toLocaleTimeString("en-GB", { timeZone: "Africa/Nairobi", hour: "2-digit", minute: "2-digit", hour12: false });

function renderRevenue(d, canRevenue) {
    const card = document.getElementById("fleet-revenue-card");
    card.hidden = !canRevenue;
    document.getElementById("fleet-charts").classList.toggle("is-single", !canRevenue);
    if (!canRevenue) return;

    // A day report charts its hours; otherwise one bar per day. Same bar
    // rules either way — only the bucket's name and axis labels differ.
    const hourly = !!d.revenue_hourly;
    document.getElementById("fleet-revenue-title").textContent = hourly ? "Revenue per hour" : "Revenue per day";
    document.getElementById("fleet-revenue-hint").textContent = `Nairobi ${hourly ? "hours" : "days"} · all sites`;
    const daily = hourly
        ? d.revenue_hourly.map(r => ({ ...r, label: hourOf(r.hour) }))
        : d.revenue_daily.map(r => ({ ...r, label: shortDay(`${r.day}T12:00:00`) }));
    const max = Math.max(1, ...daily.map(r => r.kes));
    const tracked = daily.filter(r => r.tracked);
    const best = tracked.reduce((b, r) => (r.kes > (b ? b.kes : -1) ? r : b), null);
    const untracked = daily.some(r => !r.tracked);
    // A week names every day; a month labels the 1st and every 5th, a day
    // every 6th hour — or the labels run together into one unreadable string.
    const axisLabel = (r, i) => {
        if (hourly) return i % 6 === 0 ? r.label.slice(0, 2) : "";
        if (daily.length <= 7) return new Date(`${r.day}T12:00:00`).toLocaleDateString([], { weekday: "short" });
        const n = Number(r.day.slice(8));
        return n === 1 || n % 5 === 0 ? String(n) : "";
    };

    document.getElementById("fleet-revenue-wrap").innerHTML = `
        <div class="fleet-bars fleet-rev-bars ${daily.length <= 7 ? "is-sparse" : ""}">
            ${daily.map(r => r.tracked
                ? `<div class="fleet-bar-slot" title="${esc(r.label)} — ${money(r.kes)} from ${r.sales} sales">
                    ${daily.length <= 7 ? `<div class="fleet-bar-value">${(r.kes / 1000).toFixed(1)}k</div>` : ""}
                    <div class="fleet-bar is-money" style="height:${Math.max(2, Math.round(r.kes / max * 100))}%"></div>
                   </div>`
                : `<div class="fleet-bar-slot" title="${esc(r.label)} — not tracked yet"><div class="fleet-bar is-untracked"></div></div>`
            ).join("")}
        </div>
        <div class="fleet-axis fleet-axis-days">${daily.map((r, i) => `<span>${esc(axisLabel(r, i))}</span>`).join("")}</div>
        <p class="fleet-says">${best && best.kes > 0 ? `Best ${hourly ? "hour" : "day"} ${esc(best.label)}, <strong>${money(best.kes)}</strong>.` : "Nothing sold in this window."}${untracked ? ` Hatched ${hourly ? "hours" : "days"} are before revenue tracking started — unknown, not zero.` : ""}</p>`;
}

/* Columns, in order. `revenue: true` columns exist only with the
   permission — left out of the table entirely, not locked (owner's call,
   2026-09-22). */
const COLUMNS = [
    { key: "name", label: "Site" },
    { key: "uptime_pct", label: "Uptime" },
    { key: "coverage_pct", label: "Coverage" },
    { key: "outages", label: "Outages" },
    { key: "avg_people", label: "Avg" },
    { key: "peak_people", label: "Peak" },
    { key: "revenue_kes", label: "Revenue", revenue: true },
    { key: "sales_count", label: "Sales", revenue: true },
];

function sortRows(rows) {
    const { key, dir } = sort;
    return [...rows].sort((a, b) => {
        const av = a[key], bv = b[key];
        // Missing values sink to the bottom whichever way the column is
        // sorted: an unmeasured site is not the best or the worst.
        if (av == null && bv == null) return a.name.localeCompare(b.name);
        if (av == null) return 1;
        if (bv == null) return -1;
        const diff = typeof av === "string" ? av.localeCompare(bv) : av - bv;
        return diff !== 0 ? diff * dir : a.name.localeCompare(b.name);
    });
}

function siteCell(s) {
    const g = GLYPH[s.state] || GLYPH.unknown;
    return `<td class="col-frozen">
        <span class="fleet-glyph ${g.cls}" aria-hidden="true">${g.shape}</span>
        <span class="fleet-site-name">${esc(s.name)}</span>
        ${s.vlan_id != null ? `<span class="fleet-site-vlan">vlan ${s.vlan_id}</span>` : ""}
    </td>`;
}

function coverageCell(s) {
    const thin = s.coverage_pct != null && s.coverage_pct < THIN_COVERAGE;
    const young = s.watching_since && new Date(s.watching_since) > new Date(lastData.since);
    return `<td><span class="fleet-num ${thin ? "is-warn" : "is-dim"}">${pct(s.coverage_pct)}</span>${thin ? `<span class="fleet-flag is-warn">thin</span>` : ""}${young ? `<span class="fleet-sub">since ${esc(shortDay(s.watching_since))}</span>` : ""}</td>`;
}

function revenueCells(s, canRevenue) {
    if (!canRevenue) return "";
    return `<td><span class="fleet-num">${money(s.revenue_kes)}</span></td>
        <td><span class="fleet-num is-dim">${s.sales_count}</span></td>`;
}

function measuredRow(s, canRevenue) {
    const below = s.uptime_pct != null && s.uptime_pct < UPTIME_TARGET;
    return `<tr class="fleet-row" data-id="${s.id}">
        ${siteCell(s)}
        <td><span class="fleet-num ${below ? "is-bad" : ""}">${pct(s.uptime_pct)}</span>${below ? `<span class="fleet-flag is-bad">below ${UPTIME_TARGET}%</span>` : ""}</td>
        ${coverageCell(s)}
        <td><span class="fleet-num ${s.outages ? "" : "is-dim"}">${s.outages}</span>${s.outages ? `<span class="fleet-sub">longest ${dur(s.longest_outage_seconds)}</span>` : ""}</td>
        <td><span class="fleet-num">${s.avg_people ?? "—"}</span></td>
        <td><span class="fleet-num">${s.peak_people ?? "—"}</span></td>
        ${revenueCells(s, canRevenue)}
    </tr>`;
}

/* An activity site has no session to lose, so "down" is not measurable: the
   uptime cell reports the thing it can see — time with people on it. */
function activityRow(s, canRevenue) {
    return `<tr class="fleet-row is-activity" data-id="${s.id}">
        ${siteCell(s)}
        <td><span class="fleet-num is-dim">${dur(s.online_seconds)}</span><span class="fleet-sub">with people on it</span></td>
        ${coverageCell(s)}
        <td><span class="fleet-num is-dim">—</span></td>
        <td><span class="fleet-num">${s.avg_people ?? "—"}</span></td>
        <td><span class="fleet-num">${s.peak_people ?? "—"}</span></td>
        ${revenueCells(s, canRevenue)}
    </tr>`;
}

function renderTable(d, canRevenue) {
    const cols = COLUMNS.filter(c => canRevenue || !c.revenue);
    document.getElementById("fleet-thead").innerHTML = `<tr>${cols.map((c, i) => {
        const active = sort.key === c.key;
        const arrow = active ? (sort.dir === 1 ? " ▲" : " ▼") : "";
        return `<th class="${i === 0 ? "col-frozen" : ""}"><button type="button" class="fleet-sort ${active ? "is-active" : ""}" data-key="${c.key}" aria-sort="${active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}">${esc(c.label)}${arrow}</button></th>`;
    }).join("")}</tr>`;

    const measured = sortRows(d.sites.filter(s => s.liveness_source === "pppoe"));
    // Activity sites are never ranked by uptime against the others; any other
    // column still sorts them, within their own group.
    const activity = sort.key === "uptime_pct" || sort.key === "outages"
        ? [...d.sites.filter(s => s.liveness_source !== "pppoe")].sort((a, b) => a.name.localeCompare(b.name))
        : sortRows(d.sites.filter(s => s.liveness_source !== "pppoe"));

    const span = cols.length;
    let html = measured.map(s => measuredRow(s, canRevenue)).join("");
    if (activity.length) {
        html += `<tr class="fleet-group"><td colspan="${span}">Activity sites — no PPPoE, so “down” cannot be measured. No uptime percentage, and not ranked against the sites above.</td></tr>`;
        html += activity.map(s => activityRow(s, canRevenue)).join("");
    }
    if (canRevenue) {
        html += `<tr class="fleet-unplaced">
            <td class="col-frozen"><span class="fleet-glyph is-unknown" aria-hidden="true">○</span><span class="fleet-site-name">Not tied to a site</span><span class="fleet-site-vlan">sales Ops could not place — fleet money, not a row's</span></td>
            <td colspan="5"></td>
            <td><span class="fleet-num">${money(d.unattributed_revenue_kes)}</span></td>
            <td><span class="fleet-num is-dim">${d.unattributed_sales_count}</span></td>
        </tr>`;
    }
    const t = d.totals;
    html += `<tr class="fleet-total">
        <td class="col-frozen"><span class="fleet-site-name">Fleet</span><span class="fleet-site-vlan">${d.sites.length} sites</span></td>
        <td><span class="fleet-num">${pct(t.uptime_pct)}</span></td>
        <td><span class="fleet-num">${pct(t.coverage_pct)}</span></td>
        <td><span class="fleet-num">${d.sites.reduce((n, s) => n + s.outages, 0)}</span></td>
        <td><span class="fleet-num">${t.avg_people}</span></td>
        <td><span class="fleet-num">${t.peak_people ?? "—"}</span></td>
        ${canRevenue ? `<td><span class="fleet-num">${money(t.revenue_kes)}</span></td><td><span class="fleet-num">${t.sales_count}</span></td>` : ""}
    </tr>`;

    const tbody = document.getElementById("fleet-rows");
    tbody.innerHTML = d.sites.length ? html : `<tr><td colspan="${span}" class="loading-text">No monitored sites yet.</td></tr>`;
}

function renderFootnote(d) {
    document.getElementById("fleet-footnote").innerHTML =
        "Uptime is online ÷ (online + down); time nobody was watching is left out of both and shows up in coverage instead. "
        + `<strong>Peak</strong> only exists inside ${d.peak_horizon_days} days — past that only hourly averages survive, and the cell reads “—”.`;
}

function render(d) {
    // Absent, not null: the totals only carry revenue with sites:view_revenue.
    const canRevenue = "revenue_kes" in d.totals;
    lastData = d;
    const k = d.day ? "day" : d.month ? "month" : "week";
    const v = d.day || d.month || d.week;
    const running = v === currentPeriod(k) ? " · in progress, figures so far" : "";
    document.getElementById("fleet-range-note").textContent = `${periodPhrase(k, v)}${running} · Africa/Nairobi`;
    renderKpis(d, canRevenue);
    renderPeople(d);
    renderRevenue(d, canRevenue);
    renderTable(d, canRevenue);
    renderFootnote(d);
}

async function load() {
    const seq = ++loadSeq;
    document.getElementById("view-trends").classList.add("is-loading");
    try {
        const url = `/monitoring/fleet?${kind}=${value}`;
        const res = await fetch(url, { headers: authHeaders() });
        if (seq !== loadSeq) return; // a later range click won the race
        if (!res.ok) {
            document.getElementById("fleet-rows").innerHTML =
                `<tr><td class="loading-text">Couldn't load trends (${res.status}).</td></tr>`;
            return;
        }
        const data = await res.json();
        dataFrom = data.data_from;
        setPeriodControls();
        render(data);
    } catch (err) {
        console.error("trends tab:", err);
        if (seq === loadSeq) {
            document.getElementById("fleet-rows").innerHTML = `<tr><td class="loading-text">No connection.</td></tr>`;
        }
    } finally {
        if (seq === loadSeq) document.getElementById("view-trends").classList.remove("is-loading");
    }
}

export function initTrends() {
    document.querySelector("#view-trends [data-tab-route]")
        .addEventListener("click", (e) => navigate(e.currentTarget.dataset.tabRoute));

    document.getElementById("fleet-range").addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-mode]");
        if (!btn) return;
        kind = btn.dataset.mode;
        value = currentPeriod(kind);
        setPeriodControls();
        load();
    });
    document.getElementById("fleet-period-select").addEventListener("change", (e) => {
        value = e.target.value;
        load();
    });
    document.getElementById("fleet-day-input").addEventListener("change", (e) => {
        if (!e.target.value) return; // cleared picker: stay put
        value = e.target.value;
        load();
    });

    document.getElementById("fleet-thead").addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-key]");
        if (!btn || !lastData) return;
        const key = btn.dataset.key;
        // Uptime and coverage open worst-first; counts and money open
        // biggest-first; names open A–Z.
        const firstDir = key === "uptime_pct" || key === "coverage_pct" || key === "name" ? 1 : -1;
        sort = sort.key === key ? { key, dir: -sort.dir } : { key, dir: firstDir };
        renderTable(lastData, "revenue_kes" in lastData.totals);
    });

    document.getElementById("fleet-rows").addEventListener("click", (e) => {
        const row = e.target.closest("tr[data-id]");
        // Same period on the site's own report, not a different one.
        if (row) navigate(`site-detail/${row.dataset.id}/${kind}/${value}`);
    });

    registerRoute("trends", () => {
        showView("view-trends");
        if (!value) value = currentPeriod(kind);
        setPeriodControls();
        load();
    });
}
