import { authHeaders, showView, navigate, registerRoute } from "./common.js";

// Same four states Status uses (see status.js) — duplicated rather than
// imported, matching how every view here keeps its own esc/money/pill
// rather than sharing them across files.
const STATE_INFO = {
    online:   { shape: "●", label: "Online",   cls: "st-online" },
    flapping: { shape: "▲", label: "Flapping", cls: "st-flapping" },
    offline:  { shape: "■", label: "Down",     cls: "st-offline" },
    unknown:  { shape: "○", label: "Unknown",  cls: "st-unknown" },
};

function info(state) {
    return STATE_INFO[state] || STATE_INFO.unknown;
}

function pill(state) {
    const i = info(state);
    return `<span class="status-pill ${i.cls}"><span class="st-shape" aria-hidden="true">${i.shape}</span>${i.label}</span>`;
}

function esc(value) {
    const d = document.createElement("div");
    d.textContent = value == null ? "" : String(value);
    return d.innerHTML;
}

function money(kes) {
    return `KES ${Number(kes || 0).toLocaleString()}`;
}

function fmtWhen(iso) {
    if (!iso) return "–";
    return new Date(iso).toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
}

function fmtDuration(totalSeconds) {
    if (totalSeconds == null) return "–";
    const s = Math.max(0, Math.round(totalSeconds));
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m`;
    return `${s}s`;
}

function attributionBadge(e) {
    if (e.attribution !== "inferred") return "";
    return ` <span class="site-detail-inferred" title="Site inferred from this user's last sale — not seen live on this VLAN">*</span>`;
}

/* ---- Charts: plain inline SVG, no library — same "raw SVG string from a
   small function" convention design.md documents for the icon helpers in
   common.js. Both charts share one canvas size and stretch to their panel
   via preserveAspectRatio="none", so proportions aren't guaranteed exact
   but the shape always fills the space it's given. ---- */
const CHART_W = 640;
const CHART_H = 160;
const CHART_PAD = 10;

function downsample(points, target) {
    if (points.length <= target) return points;
    const step = points.length / target;
    const out = [];
    for (let i = 0; i < target; i++) out.push(points[Math.floor(i * step)]);
    out.push(points[points.length - 1]);
    return out;
}

function sessionsChartSvg(points) {
    if (points.length === 0) {
        return `<div class="site-detail-chart-empty">No session data in this window yet.</div>`;
    }
    const pts = downsample(points, 180);
    const n = pts.length;
    const max = Math.max(1, ...pts.map(p => p.sessions || 0));
    const x = i => CHART_PAD + (i / Math.max(1, n - 1)) * (CHART_W - CHART_PAD * 2);
    const y = v => CHART_H - CHART_PAD - (v / max) * (CHART_H - CHART_PAD * 2);
    const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.sessions || 0).toFixed(1)}`).join(" ");
    const base = (CHART_H - CHART_PAD).toFixed(1);
    const area = `${line} L${x(n - 1).toFixed(1)},${base} L${x(0).toFixed(1)},${base} Z`;
    return `
        <svg viewBox="0 0 ${CHART_W} ${CHART_H}" preserveAspectRatio="none" class="site-detail-chart-svg">
            <line x1="${CHART_PAD}" y1="${base}" x2="${CHART_W - CHART_PAD}" y2="${base}" class="chart-axis" />
            <path d="${area}" class="chart-area"></path>
            <path d="${line}" class="chart-line"></path>
        </svg>
        <div class="site-detail-chart-labels">
            <span>${esc(fmtWhen(pts[0].at))}</span>
            <span class="site-detail-chart-max">peak ${max}</span>
            <span>${esc(fmtWhen(pts[n - 1].at))}</span>
        </div>`;
}

function revenueChartSvg(days) {
    if (!days || days.length === 0 || days.every(d => d.kes === 0)) {
        return `<div class="site-detail-chart-empty">No sales recorded in this window.</div>`;
    }
    const n = days.length;
    const max = Math.max(1, ...days.map(d => d.kes));
    const barW = (CHART_W - CHART_PAD * 2) / n;
    const bars = days.map((d, i) => {
        const h = Math.max(1, (d.kes / max) * (CHART_H - CHART_PAD * 2));
        const x = CHART_PAD + i * barW + barW * 0.15;
        const w = barW * 0.7;
        const y = CHART_H - CHART_PAD - h;
        const label = `${d.day}: ${money(d.kes)} (${d.sales} sale${d.sales === 1 ? "" : "s"})`;
        return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" class="chart-bar"><title>${esc(label)}</title></rect>`;
    }).join("");
    return `
        <svg viewBox="0 0 ${CHART_W} ${CHART_H}" preserveAspectRatio="none" class="site-detail-chart-svg">
            <line x1="${CHART_PAD}" y1="${CHART_H - CHART_PAD}" x2="${CHART_W - CHART_PAD}" y2="${CHART_H - CHART_PAD}" class="chart-axis" />
            ${bars}
        </svg>
        <div class="site-detail-chart-labels">
            <span>${esc(days[0].day)}</span>
            <span class="site-detail-chart-max">peak ${money(max)}</span>
            <span>${esc(days[n - 1].day)}</span>
        </div>`;
}

/* ---- Render ---- */

function renderSummary(data) {
    const watched = data.liveness_source === "pppoe"
        ? "PPPoE-watched"
        : data.liveness_source === "activity"
            ? "Activity-watched — can only show Online or Unknown, never Down"
            : "Not watched";
    const meta = [data.vlan_id != null ? `VLAN ${data.vlan_id}` : null, watched, data.notes]
        .filter(Boolean).map(esc).join(" · ");
    document.getElementById("site-detail-summary").innerHTML = `
        ${pill(data.state)}
        <span class="site-detail-since">since ${esc(fmtWhen(data.state_since))}</span>
        <span class="site-detail-meta">${meta}</span>`;
}

function renderStats(data) {
    const lastSessions = data.sessions.length ? data.sessions[data.sessions.length - 1].sessions : null;
    const cards = [
        { label: "Online now", value: lastSessions == null ? "–" : lastSessions, cls: "charged" },
        {
            label: "Uptime",
            value: data.uptime_pct == null ? "n/a" : `${data.uptime_pct}%`,
            cls: data.uptime_pct == null ? "unknown" : data.uptime_pct >= 99 ? "charged" : data.uptime_pct >= 95 ? "deployed" : "low",
            small: data.uptime_pct == null,
        },
        { label: "Downtime", value: fmtDuration(data.downtime_seconds), cls: data.downtime_seconds ? "low" : "charged" },
        { label: "Outages", value: data.outages.length, cls: data.outages.length ? "deployed" : "charged" },
    ];
    if (data.revenue_daily) {
        const revTotal = data.revenue_daily.reduce((s, d) => s + d.kes, 0);
        const revSales = data.revenue_daily.reduce((s, d) => s + d.sales, 0);
        cards.push({
            label: `Revenue (${data.days}d)`, value: money(revTotal), small: true, cls: "",
            caption: `${revSales} sale${revSales === 1 ? "" : "s"}`,
        });
    }
    document.getElementById("site-detail-stats").innerHTML = cards.map(c => `
        <div class="stat-card ${c.cls}">
            <div class="stat-label">${esc(c.label)}</div>
            <div class="stat-value ${c.small ? "is-small" : ""}">${esc(c.value)}</div>
            ${c.caption ? `<div class="stat-caption">${esc(c.caption)}</div>` : ""}
        </div>`).join("");
}

function renderOutages(data) {
    const el = document.getElementById("site-detail-outages");
    if (data.liveness_source !== "pppoe") {
        el.innerHTML = `<div class="site-detail-empty">This site is watched by activity, not PPPoE — it can only ever
            show Online or Unknown, never Down, so there's no outage list to show here.</div>`;
        return;
    }
    if (data.outages.length === 0) {
        el.innerHTML = `<div class="site-detail-empty">No outages in this window.</div>`;
        return;
    }
    el.innerHTML = data.outages.map(o => `
        <div class="site-detail-outage-row ${o.ongoing ? "is-ongoing" : ""}">
            <span class="site-detail-outage-start">${esc(fmtWhen(o.start))}</span>
            <span class="site-detail-outage-duration">${o.ongoing ? "ongoing, " : ""}${esc(fmtDuration(o.duration_seconds))}</span>
        </div>`).join("");
}

function renderActivity(data) {
    const el = document.getElementById("site-detail-activity");
    if (data.history.length === 0) {
        el.innerHTML = `<div class="site-detail-empty">No state changes recorded in this window.</div>`;
        return;
    }
    el.innerHTML = data.history.map(h => `
        <div class="site-detail-activity-row">
            ${pill(h.state)}
            <span class="site-detail-activity-when">${esc(fmtWhen(h.at))}</span>
        </div>`).join("");
}

function renderSales(data) {
    const section = document.getElementById("site-detail-revenue-section");
    // Absent, not empty: matches Status's own "no revenue key at all" gate.
    if (!("revenue_events" in data)) {
        section.hidden = true;
        return;
    }
    section.hidden = false;
    document.getElementById("site-detail-revenue-chart").innerHTML = revenueChartSvg(data.revenue_daily);
    const rows = document.getElementById("site-detail-sales-rows");
    if (data.revenue_events.length === 0) {
        rows.innerHTML = `<tr><td colspan="5" class="loading-text">No sales in this window.</td></tr>`;
        return;
    }
    rows.innerHTML = data.revenue_events.map(e => `
        <tr>
            <td>${esc(e.username)}</td>
            <td>${esc(e.profile)}</td>
            <td>${money(e.price_kes)}</td>
            <td>${esc(e.event_type)}${attributionBadge(e)}</td>
            <td>${esc(fmtWhen(e.at))}</td>
        </tr>`).join("");
}

function render(data) {
    document.getElementById("site-detail-body").hidden = false;
    document.getElementById("site-detail-notfound").hidden = true;
    document.getElementById("site-detail-title").textContent = data.name;
    renderSummary(data);
    renderStats(data);
    document.getElementById("site-detail-sessions-chart").innerHTML = sessionsChartSvg(data.sessions);
    renderOutages(data);
    renderSales(data);
    renderActivity(data);
}

let currentId = null;
let currentDays = 7;

function setRangeButtons(days) {
    document.querySelectorAll("#site-detail-range button").forEach(b => {
        b.classList.toggle("is-on", Number(b.dataset.days) === days);
    });
}

async function load(id, days) {
    document.getElementById("site-detail-body").hidden = true;
    document.getElementById("site-detail-notfound").hidden = true;
    try {
        const res = await fetch(`/monitoring/sites/${id}?days=${days}`, { headers: authHeaders() });
        if (res.status === 404) {
            document.getElementById("site-detail-title").textContent = "Site not found";
            document.getElementById("site-detail-notfound").textContent = "This site isn't there anymore, or the link is wrong.";
            document.getElementById("site-detail-notfound").hidden = false;
            return;
        }
        if (!res.ok) throw new Error(`server said ${res.status}`);
        render(await res.json());
    } catch (err) {
        console.error("site detail:", err);
        document.getElementById("site-detail-notfound").textContent = "Could not load this site right now.";
        document.getElementById("site-detail-notfound").hidden = false;
    }
}

export function initSiteDetail() {
    document.getElementById("site-detail-back-btn").addEventListener("click", () => navigate("status"));
    document.querySelectorAll("#site-detail-range button").forEach(b => {
        b.addEventListener("click", () => {
            currentDays = Number(b.dataset.days);
            setRangeButtons(currentDays);
            load(currentId, currentDays);
        });
    });

    registerRoute("site-detail", (params) => {
        showView("view-site-detail");
        const id = params[0];
        if (!id) {
            navigate("status", { replace: true });
            return;
        }
        currentId = id;
        currentDays = 7;
        setRangeButtons(currentDays);
        load(currentId, currentDays);
    });
}
