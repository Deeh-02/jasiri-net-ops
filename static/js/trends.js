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

let days = 7;
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

function renderPeople(d) {
    const wrap = document.getElementById("fleet-people-wrap");
    if (!d.people.length) {
        wrap.innerHTML = `<div class="fleet-empty">No headcounts recorded in this window.</div>`;
        return;
    }
    const max = Math.max(1, ...d.people.map(p => p.avg));
    const busiest = d.people.reduce((b, p) => (p.avg > (b ? b.avg : -1) ? p : b), null);
    wrap.innerHTML = `
        <div class="fleet-bars ${d.people.length <= 48 ? "is-sparse" : ""}">
            ${d.people.map(p => `<div class="fleet-bar-slot" title="${esc(dayTime(p.hour))} — ${p.avg} on average${p.has_peak ? `, peak ${p.peak}` : ""}">
                <div class="fleet-bar ${p.has_peak ? "" : "is-averaged"}" style="height:${Math.max(2, Math.round(p.avg / max * 100))}%"></div>
            </div>`).join("")}
        </div>
        <div class="fleet-axis"><span>${esc(shortDay(d.since))}</span><span>now</span></div>
        <p class="fleet-says">Busiest hour was <strong>${busiest.avg}</strong> people on average, ${esc(dayTime(busiest.hour))}.</p>`;
}

function renderRevenue(d, canRevenue) {
    const card = document.getElementById("fleet-revenue-card");
    card.hidden = !canRevenue;
    document.getElementById("fleet-charts").classList.toggle("is-single", !canRevenue);
    if (!canRevenue) return;

    const daily = d.revenue_daily;
    const max = Math.max(1, ...daily.map(r => r.kes));
    const tracked = daily.filter(r => r.tracked);
    const best = tracked.reduce((b, r) => (r.kes > (b ? b.kes : -1) ? r : b), null);
    const untracked = daily.some(r => !r.tracked);
    const weekday = (day) => new Date(`${day}T12:00:00`).toLocaleDateString([], daily.length > 7 ? { day: "numeric" } : { weekday: "short" });

    document.getElementById("fleet-revenue-wrap").innerHTML = `
        <div class="fleet-bars fleet-rev-bars ${daily.length <= 7 ? "is-sparse" : ""}">
            ${daily.map(r => r.tracked
                ? `<div class="fleet-bar-slot" title="${esc(shortDay(`${r.day}T12:00:00`))} — ${money(r.kes)} from ${r.sales} sales">
                    ${daily.length <= 7 ? `<div class="fleet-bar-value">${(r.kes / 1000).toFixed(1)}k</div>` : ""}
                    <div class="fleet-bar is-money" style="height:${Math.max(2, Math.round(r.kes / max * 100))}%"></div>
                   </div>`
                : `<div class="fleet-bar-slot" title="${esc(shortDay(`${r.day}T12:00:00`))} — not tracked yet"><div class="fleet-bar is-untracked"></div></div>`
            ).join("")}
        </div>
        <div class="fleet-axis fleet-axis-days">${daily.map(r => `<span>${esc(weekday(r.day))}</span>`).join("")}</div>
        <p class="fleet-says">${best ? `Best day ${esc(shortDay(`${best.day}T12:00:00`))}, <strong>${money(best.kes)}</strong>.` : "Nothing sold in this window."}${untracked ? " Hatched days are before revenue tracking started — unknown, not zero." : ""}</p>`;
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
    const now = new Date();
    document.getElementById("fleet-range-note").textContent =
        `${shortDay(d.since)} – ${shortDay(now.toISOString())} · Africa/Nairobi`;
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
        const res = await fetch(`/monitoring/fleet?days=${days}`, { headers: authHeaders() });
        if (seq !== loadSeq) return; // a later range click won the race
        if (!res.ok) {
            document.getElementById("fleet-rows").innerHTML =
                `<tr><td class="loading-text">Couldn't load trends (${res.status}).</td></tr>`;
            return;
        }
        render(await res.json());
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
        const btn = e.target.closest("button[data-days]");
        if (!btn) return;
        days = Number(btn.dataset.days);
        document.querySelectorAll("#fleet-range button").forEach(b =>
            b.setAttribute("aria-pressed", String(b === btn)));
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
        if (row) navigate(`site-detail/${row.dataset.id}`);
    });

    registerRoute("trends", () => {
        showView("view-trends");
        load();
    });
}
