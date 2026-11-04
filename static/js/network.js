import { authHeaders, showView, registerRoute } from "./common.js";

// Network (CCR2116): uplink, gateway and router health, events, and the
// PPPoE clients online right now. Every figure comes from the server; every
// piece of text is put on the page with textContent — usernames come
// straight from the router, so nothing here is ever parsed as HTML.

const ROUTER = "ccr2116";
const OVERVIEW_MS = 15000;   // header, uplink card, events
const SESSIONS_MS = 30000;   // PPPoE table
const HISTORY_MS = 60000;    // chart (1-minute rollups — faster is pointless)

const state = {
    range: "1h",
    search: "",
    status: "",
    sessions: null,      // last /network/pppoe answer, re-filtered in place
    lastOkAt: null,      // last successful overview fetch
    isOffline: false,
    timers: [],
};

const $ = id => document.getElementById(id);

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
}

async function api(path) {
    const res = await fetch(path, { headers: authHeaders() });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Couldn't load (${res.status})`);
    }
    return res.json();
}

const timeOptions = { timeZone: "Africa/Nairobi", hour: "2-digit", minute: "2-digit", second: "2-digit" };
const nairobiTime = date => date.toLocaleTimeString("en-GB", timeOptions);
const nairobiWhen = iso => new Date(iso).toLocaleString("en-GB", { ...timeOptions, weekday: "short", day: "2-digit", month: "2-digit" });
const nairobiDate = iso => new Date(iso).toLocaleDateString("en-GB", { timeZone: "Africa/Nairobi", day: "2-digit", month: "short", year: "numeric" });

function duration(seconds) {
    if (seconds == null) return "—";
    const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m ${seconds % 60}s`;
    return `${seconds}s`;
}

const mbps = v => (v == null ? "—" : `${v.toLocaleString("en-GB", { maximumFractionDigits: 1 })} Mbps`);

// ---- Header strip ----
// Three states that must never look alike: not reporting (striped, "since"),
// gateway not responding (solid red), and 0 clients online (green, says so).

function headline(o) {
    const s = o.status;
    if (s.state === "not_reporting") {
        return { cls: "red not-reporting", line: `■ Router not reporting since ${nairobiTime(new Date(s.since))}`,
                 note: "This can mean an upstream (Airtel) outage, a power cut, or the script stopping. The router " +
                       "reports through the same link it monitors, so nothing arrives during an outage; what " +
                       "happened is worked out when it reconnects (see Events)." };
    }
    if (s.state === "never_reported") {
        return { cls: "red not-reporting", line: "■ Router has not reported yet",
                 note: "Waiting for the first reading from the CCR2116 script. See docs/network-monitor-runbook.md." };
    }
    if (s.state === "gateway_down") return { cls: "red", line: "▲ Gateway not responding" };
    if (s.state === "uplink_down") return { cls: "red", line: "▲ Uplink down" };
    if (s.level === "amber") return { cls: "amber", line: "◆ Running, with warnings" };
    if ((o.live.pppoe_online ?? 0) === 0) {
        return { cls: "", line: "● Up — 0 PPPoE clients online",
                 note: "The router, uplink and gateway are fine; no PPPoE client is connected right now." };
    }
    return { cls: "", line: "● All good" };
}

function renderHero(o) {
    const h = headline(o);
    $("net-hero").className = `net-hero ${h.cls}`;
    $("net-hero-kicker").textContent = [o.router.name,
        o.received_at ? `last reading ${nairobiTime(new Date(o.received_at))}` : null].filter(Boolean).join(" · ");
    $("net-hero-line").textContent = h.line;
    const reasons = $("net-hero-reasons");
    reasons.replaceChildren(...(o.status.state === "not_reporting" ? [] : o.status.reasons.map(r => el("li", null, r.text))));
    const notes = [h.note];
    if (o.last_outage) {
        const out = o.last_outage;
        notes.push(`Last outage: ${nairobiWhen(out.started_at)}, ${out.ongoing ? "still going" : duration(out.duration_s)}.`);
    }
    $("net-hero-note").textContent = notes.filter(Boolean).join(" ");
}

function renderUpdated() {
    const box = $("net-updated");
    if (!state.lastOkAt) return;
    box.textContent = state.isOffline
        ? `offline, last updated ${nairobiTime(state.lastOkAt)}`
        : `updated ${nairobiTime(state.lastOkAt)}`;
    box.classList.toggle("is-offline", state.isOffline);
}

// ---- Uplink card ----

function renderRate(prefix, value, pct, peak, capacity, threshold) {
    $(`net-${prefix}`).textContent = mbps(value);
    const bar = $(`net-${prefix}-bar`);
    bar.style.width = `${Math.min(pct ?? 0, 100)}%`;
    bar.classList.toggle("busy", (pct ?? 0) >= threshold);
    $(`net-${prefix}-note`).textContent = [
        pct == null ? null : `${pct}% of ${capacity} Mbps contracted`,
        peak == null ? null : `peak today ${mbps(peak)}`,
    ].filter(Boolean).join(" · ");
}

function fact(label, value, tone) {
    const wrap = el("div");
    wrap.append(el("dt", null, label), el("dd", tone || null, value));
    return wrap;
}

function renderUplink(o) {
    const live = o.live, uplink = live.uplink || {}, gw = live.gateway || {}, sys = live.system || {};
    const cap = o.router.uplink_capacity_mbps;
    const amberAt = o.thresholds.utilization_amber_pct;
    $("net-uplink-sub").textContent = `${uplink.interface || "uplink"} · Airtel · every customer's traffic, PPPoE and hotspot`;
    renderRate("down", o.uplink.download_mbps, o.uplink.download_utilization_pct, o.uplink.peak_today_download_mbps, cap, amberAt);
    renderRate("up", o.uplink.upload_mbps, o.uplink.upload_utilization_pct, o.uplink.peak_today_upload_mbps, cap, amberAt);

    const gwTone = { alive: "good", missed: "warn", not_responding: "bad", no_ping: null, not_detectable: "warn" }[o.gateway.state];
    const gwDetail = [gw.address, gw.rtt_ms_avg != null ? `${gw.rtt_ms_avg} ms avg` : null,
                      gw.rtt_ms_max != null ? `${gw.rtt_ms_max} max` : null,
                      gw.loss_pct != null ? `${gw.loss_pct}% loss` : null].filter(Boolean).join(" · ");
    const errors = ["rx_errors", "tx_errors", "rx_drops", "tx_drops"].map(k => uplink[k]).filter(v => v != null);
    const facts = [
        fact("Link", uplink.running === false ? "Down" : `Up${uplink.link_rate ? ` · ${uplink.link_rate}` : ""}`,
             uplink.running === false ? "bad" : "good"),
        fact("Gateway", `${o.gateway.label}${gwDetail ? ` — ${gwDetail}` : ""}`, gwTone),
    ];
    if (live.internet) {
        const i = live.internet;
        facts.push(fact(`Internet (${i.target})`, i.alive ? `Reachable${i.rtt_ms_avg != null ? ` · ${i.rtt_ms_avg} ms` : ""}` : "Not reachable",
                        i.alive ? "good" : "warn"));
    }
    facts.push(
        fact("Errors / drops", errors.length ? `${uplink.rx_errors ?? "—"} / ${uplink.tx_errors ?? "—"} errors · ${uplink.rx_drops ?? "—"} / ${uplink.tx_drops ?? "—"} drops (rx / tx)` : "—",
             o.status.reasons.some(r => r.code === "errors") ? "warn" : null),
        fact("CPU", sys.cpu_pct == null ? "—" : `${sys.cpu_pct}%`, (sys.cpu_pct ?? 0) >= o.thresholds.cpu_amber_pct ? "warn" : null),
        fact("Router temperature", sys.temp_c == null ? "—" : `${sys.temp_c} °C`),
        fact("Router uptime", duration(live.uptime_s)),
        fact("PPPoE online", live.pppoe_online == null ? "—" : String(live.pppoe_online)),
        fact("RouterOS", sys.routeros || "—"),
    );
    const sfp = uplink.sfp;
    if (sfp && (sfp.temp_c != null || sfp.rx_dbm != null || sfp.tx_dbm != null)) {
        facts.push(fact("SFP", [sfp.temp_c != null ? `${sfp.temp_c} °C` : null, sfp.rx_dbm != null ? `rx ${sfp.rx_dbm} dBm` : null,
                                sfp.tx_dbm != null ? `tx ${sfp.tx_dbm} dBm` : null].filter(Boolean).join(" · ")));
    }
    $("net-facts").replaceChildren(...facts);
}

// ---- Chart ----

const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attrs, text) {
    const node = document.createElementNS(SVG, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    if (text != null) node.textContent = text;
    return node;
}

function renderHistory(h) {
    const box = $("net-chart");
    const pts = h.points.filter(p => p.download_mbps_avg != null);
    if (!pts.length) {
        box.replaceChildren(el("div", "net-empty", "No readings in this window yet."));
        return;
    }
    const W = 720, H = 220, L = 46, R = 10, T = 10, B = 24;
    const t0 = new Date(h.from).getTime(), t1 = new Date(h.to).getTime();
    const x = t => L + ((t - t0) / (t1 - t0)) * (W - L - R);
    const top = Math.max(10, ...pts.map(p => Math.max(p.download_mbps_avg, p.upload_mbps_avg || 0))) * 1.1;
    const y = v => T + (1 - v / top) * (H - T - B);
    const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Uplink traffic" });

    h.outages.forEach(o => {
        const a = Math.max(x(new Date(o.started_at).getTime()), L);
        const b = Math.min(x(o.ended_at ? new Date(o.ended_at).getTime() : t1), W - R);
        if (b > a) chart.append(svg("rect", { class: "outage", x: a, y: T, width: Math.max(b - a, 2), height: H - T - B }));
    });
    [0, 0.25, 0.5, 0.75, 1].forEach(f => {
        const v = top * f;
        chart.append(svg("line", { class: "grid", x1: L, x2: W - R, y1: y(v), y2: y(v) }),
                     svg("text", { x: L - 6, y: y(v) + 3, "text-anchor": "end" }, `${Math.round(v)}`));
    });
    chart.append(svg("text", { x: 4, y: T + 8 }, "Mbps"));
    const line = (key, cls) => svg("polyline", { class: cls,
        points: pts.filter(p => p[key] != null).map(p => `${x(new Date(p.t).getTime())},${y(p[key])}`).join(" ") });
    chart.append(line("download_mbps_avg", "down"), line("upload_mbps_avg", "up"));
    const fmt = t => new Date(t).toLocaleString("en-GB", h.range === "7d"
        ? { timeZone: "Africa/Nairobi", weekday: "short", day: "2-digit" }
        : { timeZone: "Africa/Nairobi", hour: "2-digit", minute: "2-digit" });
    for (let i = 0; i <= 4; i++) {
        const t = t0 + ((t1 - t0) * i) / 4;
        chart.append(svg("text", { x: x(t), y: H - 6, "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle" }, fmt(t)));
    }
    box.replaceChildren(chart);
}

// ---- Events ----

const EVENT_TEXT = {
    not_reporting: e => e.cause === "restart"
        ? "Router not reporting — it restarted or lost power"
        : "Router not reporting — it stayed up, so most likely an upstream or gateway outage",
    gateway_down: () => "Gateway not responding",
    uplink_down: () => "Uplink down",
    router_restarted: () => "Router restarted",
};

function renderEvents(list) {
    const box = $("net-events");
    if (!list.length) {
        box.replaceChildren(el("div", "net-empty", "No events in the last 90 days."));
        return;
    }
    box.replaceChildren(...list.map(e => {
        const item = el("div", `net-event ${e.type === "router_restarted" ? "info" : "bad"}`);
        item.append(
            el("div", "net-event-when", `${nairobiWhen(e.started_at)} · ${e.ongoing ? "ongoing" : e.type === "router_restarted" ? "" : duration(e.duration_s)}`),
            el("div", null, (EVENT_TEXT[e.type] || (() => e.type))(e)),
        );
        if (e.reported_after_reconnecting) item.append(el("div", "net-event-note", "Reported by router after reconnecting"));
        return item;
    }));
}

// ---- PPPoE table ----

const STATUS_TEXT = { active: "● Active", expired: "▲ Expired", unknown: "○ Unknown" };

function renderCounts(p) {
    const chip = (label, n, warn) => {
        const box = el("div", `net-count${warn && n > 0 ? " warn" : ""}`);
        box.append(el("strong", null, String(n)), document.createTextNode(label));
        return box;
    };
    $("net-counts").replaceChildren(
        chip("Online now", p.counts.online),
        chip("Active", p.counts.active),
        chip("Expired but connected", p.counts.expired_but_online, true),
        chip("Unknown accounts", p.counts.unknown, true),
    );
    const stale = $("net-stale");
    stale.hidden = !p.is_stale;
    stale.textContent = p.received_at
        ? `Session list last updated ${nairobiWhen(p.received_at)} — not live.`
        : "No session list has arrived from the router yet.";
}

function cell(label, child) {
    const td = el("td");
    td.dataset.label = label;
    if (typeof child === "string") td.textContent = child;
    else td.append(...[].concat(child));
    return td;
}

function renderSessions() {
    const p = state.sessions;
    if (!p) return;
    const q = state.search.trim().toLowerCase();
    const rows = p.sessions.filter(s =>
        (!state.status || s.status === state.status) &&
        (!q || [s.display_name, s.username, s.address].some(v => (v || "").toLowerCase().includes(q))));
    // Expired-but-connected first: that's the list someone acts on.
    const order = { expired: 0, unknown: 1, active: 2 };
    rows.sort((a, b) => order[a.status] - order[b.status] || (a.display_name || a.username).localeCompare(b.display_name || b.username));
    const body = $("net-sessions");
    if (!rows.length) {
        const tr = el("tr");
        const td = el("td", "net-empty", p.sessions.length ? "No client matches the filter." : "No PPPoE clients online.");
        td.colSpan = 6;
        tr.append(td);
        body.replaceChildren(tr);
        return;
    }
    body.replaceChildren(...rows.map(s => {
        const tr = el("tr", s.status === "expired" ? "is-expired" : null);
        const status = [el("span", `net-badge ${s.status}`, STATUS_TEXT[s.status])];
        const detail = [s.expiry ? `expiry ${nairobiDate(s.expiry)}` : null, s.status_reason].filter(Boolean).join(" · ");
        if (detail) status.push(el("div", "net-dim", detail));
        tr.append(
            cell("Name", s.display_name ? s.display_name : [el("span", "net-dim", "not in the customer list")]),
            cell("Username", s.username),
            cell("Package", s.package || "—"),
            cell("Status", status),
            cell("IP", s.address || "—"),
            cell("Uptime", duration(s.uptime_s)),
        );
        return tr;
    }));
}

// ---- Loading and polling ----

async function loadOverview() {
    try {
        const [overview, events] = await Promise.all([
            api(`/network/overview?router=${ROUTER}`),
            api(`/network/events?router=${ROUTER}&limit=30`),
        ]);
        renderHero(overview);
        renderUplink(overview);
        renderEvents(events.events);
        state.lastOkAt = new Date();
        state.isOffline = false;
    } catch (e) {
        // Keep what's on screen and say so, rather than going quietly stale.
        state.isOffline = true;
        if (!state.lastOkAt) $("net-hero-line").textContent = e.message;
    }
    renderUpdated();
}

async function loadSessions() {
    try {
        state.sessions = await api(`/network/pppoe?router=${ROUTER}`);
        renderCounts(state.sessions);
        renderSessions();
    } catch (e) {
        if (!state.sessions) $("net-sessions").replaceChildren(el("tr", null, e.message));
    }
}

async function loadHistory() {
    try {
        renderHistory(await api(`/network/uplink-history?router=${ROUTER}&range=${state.range}`));
    } catch (e) {
        $("net-chart").replaceChildren(el("div", "net-empty", e.message));
    }
}

const viewIsOpen = () => !$("view-network").hidden;

function stopPolling() {
    state.timers.forEach(clearInterval);
    state.timers = [];
}

function startPolling() {
    stopPolling();
    if (document.hidden) return;
    const every = (fn, ms) => setInterval(() => {
        if (document.hidden || !viewIsOpen()) stopPolling();
        else fn();
    }, ms);
    state.timers = [every(loadOverview, OVERVIEW_MS), every(loadSessions, SESSIONS_MS), every(loadHistory, HISTORY_MS)];
}

function loadAll() {
    loadOverview();
    loadSessions();
    loadHistory();
}

document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopPolling();
    else if (viewIsOpen()) { loadAll(); startPolling(); }
});

export function initNetwork() {
    $("net-range").addEventListener("click", e => {
        const btn = e.target.closest("button[data-range]");
        if (!btn) return;
        state.range = btn.dataset.range;
        document.querySelectorAll("#net-range button").forEach(b => b.setAttribute("aria-pressed", String(b === btn)));
        loadHistory();
    });
    $("net-status-filter").addEventListener("click", e => {
        const btn = e.target.closest("button[data-status]");
        if (!btn) return;
        state.status = btn.dataset.status;
        document.querySelectorAll("#net-status-filter button").forEach(b => b.setAttribute("aria-pressed", String(b === btn)));
        renderSessions();
    });
    $("net-search").addEventListener("input", e => { state.search = e.target.value; renderSessions(); });

    registerRoute("network", () => {
        showView("view-network");
        loadAll();
        startPolling();
    });
}
