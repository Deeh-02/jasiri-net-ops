import { authHeaders, showView, registerRoute, stateRow } from "./common.js";

// Network (CCR2116): uplink, gateway and router health, events, and the
// PPPoE clients online right now. Every figure comes from the server; every
// piece of text is put on the page with textContent — usernames come
// straight from the router, so nothing here is ever parsed as HTML.

const ROUTER = "ccr2116";
const OVERVIEW_MS = 15000;   // header, uplink card, events
const SESSIONS_MS = 30000;   // PPPoE table
const HISTORY_MS = 60000;    // chart, 1h and longer (1-minute rollups — faster is pointless)
const LIVE_MS = 5000;        // chart, Live (readings land every 15 s; 5 s keeps the lag short)
const ONUS_MS = 60000;       // Fibre panel (SmartOLT is polled every 6 / 20 min server-side)

const state = {
    range: "live",
    historyAt: 0,        // when the chart was last fetched (Live refetches more often)
    link: "",            // chart: "" = all links added together, else a link's interface
    linkSig: "",         // the links the chart picker was built for
    editing: null,       // interface whose capacity form is open (the page leaves it alone)
    search: "",
    status: "",
    sessions: null,      // last /network/pppoe answer, re-filtered in place
    onus: null,          // last /network/onus answer
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
const nairobiHM = iso => new Date(iso).toLocaleTimeString("en-GB", { timeZone: "Africa/Nairobi", hour: "2-digit", minute: "2-digit" });
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
    // The reason already names the link when there are several ("airtel Primary: Gateway not responding").
    if (s.state === "gateway_down" || s.state === "uplink_down") return { cls: "red", line: `▲ ${s.reasons[0].text}` };
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
    // When the headline IS the first reason, don't say it twice underneath.
    const listed = o.status.state === "not_reporting" ? []
        : o.status.reasons.filter((r, i) => !(i === 0 && h.line.endsWith(r.text)));
    reasons.replaceChildren(...listed.map(r => el("li", null, r.text)));
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

// ---- Links and totals ----

function fact(label, value, tone) {
    const wrap = el("div");
    wrap.append(el("dt", null, label), el("dd", tone || null, value));
    return wrap;
}

function badge(text, tone) {
    return el("span", `net-link-badge${tone ? ` ${tone}` : ""}`, text);
}

function rateBlock(label, value, pct, capacity, threshold) {
    const block = el("div", "net-rate");
    const bar = el("div", "net-bar");
    bar.setAttribute("aria-hidden", "true");
    const fill = el("div", "net-bar-fill");
    fill.style.width = `${Math.min(pct ?? 0, 100)}%`;
    fill.classList.toggle("busy", (pct ?? 0) >= threshold && !capacity.estimated);
    bar.append(fill);
    block.append(el("div", "net-rate-label", label), el("div", "net-rate-value net-rate-small", mbps(value)), bar,
                 el("div", "net-rate-note", pct == null ? "" : `${pct}% of ${capacity.mbps} Mbps${capacity.estimated ? " (estimated)" : ""}`));
    return block;
}

function capacityLine(l, canEdit) {
    const cap = l.capacity;
    const line = el("div", "net-link-cap");
    line.append(el("span", null, cap.mbps == null
        ? "Capacity not known"
        : `Capacity ${cap.mbps} Mbps · ${cap.estimated ? "estimated from the port speed (no busy warnings until confirmed)" : "set by an admin"}`));
    if (canEdit) {
        const edit = el("button", "net-link-edit", cap.estimated ? "Set the real speed" : "Edit");
        edit.type = "button";
        edit.addEventListener("click", () => openCapacityForm(l, line));
        line.append(edit);
    }
    return line;
}

function openCapacityForm(l, line) {
    state.editing = l.interface;   // the 15-second refresh leaves the form alone
    const form = el("form", "net-link-form");
    const input = el("input");
    input.type = "number"; input.min = "1"; input.max = "100000"; input.step = "any"; input.required = true;
    input.value = l.capacity.mbps ?? "";
    input.setAttribute("aria-label", `Contracted speed of ${l.name} in Mbps`);
    const msg = el("span", "net-link-msg");
    const button = (text, onClick, kind = "button") => {
        const b = el("button", null, text);
        b.type = kind;
        if (onClick) b.addEventListener("click", onClick);
        return b;
    };
    const close = () => { state.editing = null; loadOverview(); };
    form.addEventListener("submit", async e => {
        e.preventDefault();
        msg.textContent = "Saving…";
        msg.textContent = await saveCapacity(l.interface, Number(input.value)) || "";
        if (!msg.textContent) close();
    });
    form.append(input, el("span", null, "Mbps"), button("Save", null, "submit"),
                button("Use port speed", async () => {
                    msg.textContent = (await saveCapacity(l.interface, null)) || "";
                    if (!msg.textContent) close();
                }),
                button("Cancel", close), msg);
    line.replaceChildren(form);
    input.focus();
}

// Returns an error message, or "" when saved.
async function saveCapacity(interfaceName, capacity) {
    try {
        const res = await fetch("/network/uplinks/capacity", {
            method: "PUT", headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ router: ROUTER, interface: interfaceName, capacity_mbps: capacity }),
        });
        if (res.ok) return "";
        const err = await res.json().catch(() => ({}));
        return typeof err.detail === "string" ? err.detail : "Couldn't save that figure";
    } catch (e) {
        return "Couldn't reach the server";
    }
}

// An admin switches a link off when it is plugged in but not paid for or not
// in use yet: it stays on the page, greyed, but raises no warnings.
async function saveService(interfaceName, inService) {
    try {
        const res = await fetch("/network/uplinks/service", {
            method: "PUT", headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ router: ROUTER, interface: interfaceName, in_service: inService }),
        });
        if (res.ok) return "";
        const err = await res.json().catch(() => ({}));
        return typeof err.detail === "string" ? err.detail : "Couldn't change that";
    } catch (e) {
        return "Couldn't reach the server";
    }
}

function serviceLine(l) {
    const line = el("div", "net-link-cap net-link-svc");
    const off = l.in_service === false;
    const msg = el("span", "net-link-msg");
    const button = el("button", "net-link-edit", off ? "Put back in service" : "Mark not in service");
    button.type = "button";
    button.addEventListener("click", async () => {
        button.disabled = true;
        msg.textContent = (await saveService(l.interface, off)) || "";
        if (msg.textContent) button.disabled = false;
        else loadOverview();
    });
    line.append(el("span", null, off
        ? "Not in service: no warnings or events from this link."
        : "Plugged in but not paid for or not in use yet?"), button, msg);
    return line;
}

function linkCard(l, o) {
    const amberAt = o.thresholds.utilization_amber_pct;
    const off = l.in_service === false;
    const card = el("div", `net-link${l.listed ? "" : " is-gone"}${off ? " is-off" : ""}${l.running === false && !off ? " is-down" : ""}`);
    const head = el("div", "net-link-head");
    const badges = el("div", "net-link-badges");
    badges.append(badge(l.primary ? "Primary" : "Backup"));
    if (off) badges.append(badge("Not in service", "warn"));
    else if (!l.listed) badges.append(badge("No longer reported", "warn"));
    else if (l.active === true) badges.append(badge("Carrying traffic", "good"));
    else if (l.active === false) badges.append(badge("Standby"));
    head.append(el("h3", "net-link-name", l.name), badges);
    card.append(head, el("div", "net-link-sub",
        [l.running === false ? "▲ Down" : "● Up", l.interface, l.link_rate].filter(Boolean).join(" · ")));

    const rates = el("div", "net-link-rates");
    rates.append(rateBlock("Download", l.download_mbps, l.download_utilization_pct, l.capacity, amberAt),
                 rateBlock("Upload", l.upload_mbps, l.upload_utilization_pct, l.capacity, amberAt));
    card.append(rates, capacityLine(l, o.can_edit_capacity));
    if (o.can_edit_capacity) card.append(serviceLine(l));

    const peaks = [l.peak_today.download_mbps != null
                       ? `today ↓ ${mbps(l.peak_today.download_mbps)}${l.peak_today.download_at ? ` (${nairobiHM(l.peak_today.download_at)})` : ""} ↑ ${mbps(l.peak_today.upload_mbps)}` : null,
                   l.peak_30d.download_mbps != null ? `30 days ↓ ${mbps(l.peak_30d.download_mbps)} ↑ ${mbps(l.peak_30d.upload_mbps)}` : null]
        .filter(Boolean).join(" · ");
    const gw = l.gateway;
    const gwTone = off ? null : { alive: "good", missed: "warn", not_responding: "bad", no_ping: null, not_detectable: "warn" }[gw.state];
    const gwDetail = [gw.address, gw.rtt_ms_avg != null ? `${gw.rtt_ms_avg} ms avg` : null,
                      gw.rtt_ms_max != null ? `${gw.rtt_ms_max} max` : null,
                      gw.loss_pct != null ? `${gw.loss_pct}% loss` : null].filter(Boolean).join(" · ");
    const facts = [];
    if (peaks) facts.push(fact("Peak seen", peaks));
    facts.push(fact("Gateway", `${gw.label}${gwDetail ? ` — ${gwDetail}` : ""}`, gwTone));
    const counts = Object.values(l.errors).filter(v => v != null);
    if (counts.length) {
        const e = l.errors;
        facts.push(fact("Errors / drops", `${e.rx_errors ?? "—"} / ${e.tx_errors ?? "—"} errors · ${e.rx_drops ?? "—"} / ${e.tx_drops ?? "—"} drops (rx / tx)`,
                        o.status.reasons.some(r => r.code === "errors") ? "warn" : null));
    }
    const sfp = l.sfp;
    if (sfp && (sfp.temp_c != null || sfp.rx_dbm != null || sfp.tx_dbm != null)) {
        facts.push(fact("SFP", [sfp.temp_c != null ? `${sfp.temp_c} °C` : null, sfp.rx_dbm != null ? `rx ${sfp.rx_dbm} dBm` : null,
                                sfp.tx_dbm != null ? `tx ${sfp.tx_dbm} dBm` : null].filter(Boolean).join(" · ")));
    }
    const list = el("dl", "net-facts net-link-facts");
    list.append(...facts);
    card.append(list);
    return card;
}

function renderLinks(o) {
    if (state.editing) return;   // someone is typing a capacity; don't wipe the form
    const box = $("net-links");
    if (!o.links.length) {
        box.replaceChildren(el("div", "net-empty", "No link has been reported yet."));
        return;
    }
    box.replaceChildren(...o.links.map(l => linkCard(l, o)));
}

function renderTotals(o) {
    const live = o.live, sys = live.system || {};
    const current = o.links.filter(l => l.listed && l.in_service !== false).length;
    $("net-uplink-sub").textContent = `${current} link${current === 1 ? "" : "s"} added together · every customer's traffic, PPPoE and hotspot`;
    $("net-down").textContent = mbps(o.total.download_mbps);
    $("net-up").textContent = mbps(o.total.upload_mbps);
    // The peak is the single highest 15-second reading; the chart's solid lines are
    // averages, so the peak sits on its faint "peak" line, not on the average.
    const peakNote = (mbpsValue, at) => mbpsValue == null ? "" :
        `peak today ${mbps(mbpsValue)}${at ? ` at ${nairobiHM(at)}` : ""} (highest single reading)`;
    $("net-down-note").textContent = peakNote(o.total.peak_today_download_mbps, o.total.peak_today_download_at);
    $("net-up-note").textContent = peakNote(o.total.peak_today_upload_mbps, o.total.peak_today_upload_at);

    const facts = [];
    if (live.internet) {
        const i = live.internet;
        facts.push(fact(`Internet (${i.target})`, i.alive ? `Reachable${i.rtt_ms_avg != null ? ` · ${i.rtt_ms_avg} ms` : ""}` : "Not reachable",
                        i.alive ? "good" : "warn"));
    }
    facts.push(
        fact("CPU", sys.cpu_pct == null ? "—" : `${sys.cpu_pct}%`, (sys.cpu_pct ?? 0) >= o.thresholds.cpu_amber_pct ? "warn" : null),
        fact("Router temperature", sys.temp_c == null ? "—" : `${sys.temp_c} °C`),
        fact("Router uptime", duration(live.uptime_s)),
        fact("PPPoE online", live.pppoe_online == null ? "—" : String(live.pppoe_online)),
        fact("RouterOS", sys.routeros || "—"),
    );
    $("net-facts").replaceChildren(...facts);
}

// The chart can show all links together or one link. The picker is rebuilt
// only when the set of links changes, so a click isn't undone by a refresh.
function renderLinkPicker(o) {
    const signature = o.links.map(l => l.interface).join("|");
    if (signature === state.linkSig) return;
    state.linkSig = signature;
    if (state.link && !o.links.some(l => l.interface === state.link)) state.link = "";
    const box = $("net-link-pick");
    box.hidden = o.links.length < 2;
    const choices = [["", "All links"], ...o.links.map(l => [l.interface, l.name])];
    box.replaceChildren(...choices.map(([key, label]) => {
        const b = el("button", null, label);
        b.type = "button";
        b.dataset.link = key;
        b.setAttribute("aria-pressed", String(key === state.link));
        return b;
    }));
}

// ---- Chart ----

const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attrs, text) {
    const node = document.createElementNS(SVG, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    if (text != null) node.textContent = text;
    return node;
}

// Axis for a chart whose highest value is `peak`: round steps (1, 2 or 5 times
// a power of ten) with 3 to 5 intervals, topping out just above the peak. It
// still follows the traffic: a quiet hour gets a small axis, a busy one a big one.
function niceAxis(peak) {
    const want = Math.max(10, peak * 1.05);
    const magnitude = 10 ** Math.floor(Math.log10(want));
    for (const step of [magnitude / 10, magnitude / 5, magnitude / 2, magnitude, magnitude * 2, magnitude * 5]) {
        const intervals = Math.ceil(want / step);
        if (intervals >= 3 && intervals <= 5) return { step, top: intervals * step };
    }
    return { step: want / 4, top: want };
}

function renderHistory(h) {
    const box = $("net-chart");
    const pts = h.points.filter(p => p.download_mbps_avg != null);
    if (!pts.length) {
        box.replaceChildren(el("div", "net-empty", "No readings in this window yet."));
        return;
    }
    const W = 720, H = 230, L = 46, R = 10, T = 22, B = 24;   // T leaves room for the "Mbps" label
    const t0 = new Date(h.from).getTime(), t1 = new Date(h.to).getTime();
    const x = t => L + ((t - t0) / (t1 - t0)) * (W - L - R);
    const axis = niceAxis(Math.max(0, ...pts.map(p => Math.max(p.download_mbps_max ?? 0, p.upload_mbps_max ?? 0,
                                                                p.download_mbps_avg, p.upload_mbps_avg || 0))));
    const top = axis.top;
    const y = v => T + (1 - v / top) * (H - T - B);
    const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Uplink traffic" });

    h.outages.forEach(o => {
        const a = Math.max(x(new Date(o.started_at).getTime()), L);
        const b = Math.min(x(o.ended_at ? new Date(o.ended_at).getTime() : t1), W - R);
        if (b > a) chart.append(svg("rect", { class: "outage", x: a, y: T, width: Math.max(b - a, 2), height: H - T - B }));
    });
    for (let v = 0; v <= top + axis.step / 2; v += axis.step) {
        chart.append(svg("line", { class: "grid", x1: L, x2: W - R, y1: y(v), y2: y(v) }),
                     svg("text", { x: L - 6, y: y(v) + 3, "text-anchor": "end" }, `${Math.round(v)}`));
    }
    chart.append(svg("text", { x: 4, y: 10 }, "Mbps"));
    const line = (key, cls) => svg("polyline", { class: cls,
        points: pts.filter(p => p[key] != null).map(p => `${x(new Date(p.t).getTime())},${y(p[key])}`).join(" ") });
    // Peaks first (faint, underneath): the highest single reading in each slice.
    // The averages on top smooth spikes away, which is why "peak today" can be
    // higher than anything the solid lines reach.
    chart.append(line("download_mbps_max", "peak-down"), line("upload_mbps_max", "peak-up"),
                 line("download_mbps_avg", "down"), line("upload_mbps_avg", "up"));
    const fmt = t => new Date(t).toLocaleString("en-GB", h.range === "7d"
        ? { timeZone: "Africa/Nairobi", weekday: "short", day: "2-digit" }
        : { timeZone: "Africa/Nairobi", hour: "2-digit", minute: "2-digit", ...(h.range === "live" ? { second: "2-digit" } : {}) });
    for (let i = 0; i <= 4; i++) {
        const t = t0 + ((t1 - t0) * i) / 4;
        chart.append(svg("text", { x: x(t), y: H - 6, "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle" }, fmt(t)));
    }
    box.replaceChildren(chart);
    addHover(box, chart, pts, h, { W, H, L, R, T, B, x, y, t0, t1 });
}

// Hover/touch readout: a guide line that follows the pointer along the chart,
// a dot on each line, and a box with the time and both rates. The chart is
// redrawn every minute, so this is attached to each new drawing. Everything
// shown is text (no HTML from data).
function addHover(box, chart, pts, h, g) {
    const times = pts.map(p => new Date(p.t).getTime());
    const guide = svg("line", { class: "hover-line", x1: 0, x2: 0, y1: g.T, y2: g.H - g.B });
    const dotDown = svg("circle", { class: "hover-dot down", r: 4 });
    const dotUp = svg("circle", { class: "hover-dot up", r: 4 });
    const marks = [guide, dotDown, dotUp];
    marks.forEach(n => { n.style.display = "none"; chart.append(n); });
    const tip = el("div", "net-tip");
    tip.hidden = true;
    box.append(tip);

    const when = t => new Date(t).toLocaleString("en-GB", { timeZone: "Africa/Nairobi",
        ...(h.range === "7d" ? { weekday: "short", day: "2-digit", month: "short" } : {}),
        hour: "2-digit", minute: "2-digit", ...(h.range === "live" ? { second: "2-digit" } : {}) });
    const outageAt = t => h.outages.some(o =>
        new Date(o.started_at).getTime() <= t && t <= (o.ended_at ? new Date(o.ended_at).getTime() : g.t1));
    const nearest = target => {
        let lo = 0, hi = times.length - 1;
        while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (times[mid] < target) lo = mid; else hi = mid; }
        return Math.abs(times[lo] - target) <= Math.abs(times[hi] - target) ? lo : hi;
    };
    const row = (cls, label, value) => {
        const line = el("div", `net-tip-row ${cls}`);
        line.append(el("span", null, label), el("b", null, value == null ? "n/a" : `${value.toFixed(1)} Mbps`));
        return line;
    };
    const hide = () => { marks.forEach(n => { n.style.display = "none"; }); tip.hidden = true; };

    chart.addEventListener("pointermove", e => {
        const rect = chart.getBoundingClientRect();
        const px = ((e.clientX - rect.left) / rect.width) * g.W;
        if (px < g.L || px > g.W - g.R) { hide(); return; }
        const target = g.t0 + ((px - g.L) / (g.W - g.L - g.R)) * (g.t1 - g.t0);
        const i = nearest(target), p = pts[i], cx = g.x(times[i]);
        guide.setAttribute("x1", cx); guide.setAttribute("x2", cx);
        dotDown.setAttribute("cx", cx); dotDown.setAttribute("cy", g.y(p.download_mbps_avg));
        dotUp.setAttribute("cx", cx);
        if (p.upload_mbps_avg != null) dotUp.setAttribute("cy", g.y(p.upload_mbps_avg));
        marks.forEach(n => { n.style.display = ""; });
        if (p.upload_mbps_avg == null) dotUp.style.display = "none";

        const peakRow = el("div", "net-tip-peak", p.download_mbps_max == null ? "" :
            `Highest reading: ↓ ${p.download_mbps_max.toFixed(1)} · ↑ ${(p.upload_mbps_max ?? 0).toFixed(1)} Mbps`);
        tip.replaceChildren(el("div", "net-tip-time", when(times[i])),
                            row("down", "Download", p.download_mbps_avg), row("up", "Upload", p.upload_mbps_avg), peakRow);
        if (outageAt(times[i])) tip.append(el("div", "net-tip-out", "Router not reporting"));
        tip.hidden = false;
        tip.style.left = `${(cx / g.W) * rect.width}px`;
        tip.classList.toggle("flip", cx > g.W * 0.6);   // flip to the left near the right edge
    });
    chart.addEventListener("pointerleave", hide);
    chart.addEventListener("pointercancel", hide);
}

// ---- Events ----

const EVENT_TEXT = {
    not_reporting: e => e.cause === "restart"
        ? "Router not reporting — it restarted or lost power"
        : "Router not reporting — it stayed up, so most likely an upstream or gateway outage",
    gateway_down: e => `Gateway not responding${e.link ? ` — ${e.link}` : ""}`,
    uplink_down: e => `Uplink down${e.link ? ` — ${e.link}` : ""}`,
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
        // The shared empty state (escaped text only).
        body.innerHTML = p.sessions.length
            ? stateRow(6, "empty", "Try another name, or show all statuses.", "No client matches the filter")
            : stateRow(6, "empty", "No PPPoE clients online.");
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

// ---- Fibre ONUs (SmartOLT) ----
// Read only. ONU names come from SmartOLT, so text only, never HTML.

const ONU_DOWN_GLYPH = "▼";
const GRADE = { Critical: ["▲ Critical", "onu-critical"], Warning: ["◆ Warning", "onu-warn"] };
const dbm = v => (v == null ? "no reading" : `${v.toFixed(2)} dBm`);

function onuStatus(status) {
    return status === "Online"
        ? el("span", "net-badge onu-online", "● Online")
        : el("span", "net-badge onu-down", `${ONU_DOWN_GLYPH} ${status || "Unknown"}`);
}

function renderOnus(p) {
    const parts = [`${p.total} ONU${p.total === 1 ? "" : "s"}`];
    if (p.status_polled_at) parts.push(`status ${nairobiHM(p.status_polled_at)}`);
    if (p.signal_polled_at) parts.push(`signal ${nairobiHM(p.signal_polled_at)}`);
    $("onu-sub").textContent = p.total ? `from SmartOLT · ${parts.join(" · ")}` : "from SmartOLT";

    const stale = [];
    if (!p.configured && !p.total) stale.push("SmartOLT isn't connected on this server yet (SMARTOLT_URL and SMARTOLT_API_KEY).");
    else if (!p.status_polled_at) stale.push("No ONU data from SmartOLT yet; the first poll runs within a minute.");
    else {
        if (p.status_stale) stale.push(`ONU status last updated ${nairobiWhen(p.status_polled_at)}, not current.`);
        if (p.signal_stale) stale.push(p.signal_polled_at
            ? `Signal readings last updated ${nairobiWhen(p.signal_polled_at)}, not current.`
            : "No signal readings from SmartOLT yet.");
    }
    $("onu-stale").hidden = !stale.length;
    $("onu-stale").textContent = stale.join(" ");

    // Online first, then every other status SmartOLT uses, in its own words.
    const chip = (label, n, tone) => {
        const box = el("div", `net-count${tone && n > 0 ? ` ${tone}` : ""}`);
        box.append(el("strong", null, String(n)), document.createTextNode(label));
        return box;
    };
    const others = Object.keys(p.counts).filter(k => k !== "Online").sort();
    $("onu-counts").replaceChildren(...(p.total ? [
        chip("Online", p.counts.Online || 0),
        ...others.map(k => chip(k, p.counts[k], "bad")),
        chip("Weak signal", p.weak.length, "warn"),
    ] : []));

    const down = $("onu-down");
    if (!p.down.length) {
        down.innerHTML = stateRow(6, "empty", p.total ? "Every ONU is online." : "No ONU data yet.");
    } else {
        down.replaceChildren(...p.down.map(o => {
            const tr = el("tr");
            tr.append(
                cell("Name", o.name || "—"),
                cell("Status", [onuStatus(o.status)]),
                cell("For", o.for_s == null ? "—" : duration(o.for_s)),
                cell("Since", o.last_status_change ? nairobiWhen(o.last_status_change) : "—"),
                cell("Port", o.location || "—"),
                cell("Serial", o.sn || o.id),
            );
            return tr;
        }));
    }

    const weak = $("onu-weak");
    if (!p.weak.length) {
        weak.innerHTML = stateRow(6, "empty", p.total ? "SmartOLT grades no ONU as Warning or Critical." : "No ONU data yet.");
    } else {
        weak.replaceChildren(...p.weak.map(o => {
            const [label, tone] = GRADE[o.signal_quality];
            const tr = el("tr");
            tr.append(
                cell("Name", o.name || "—"),
                cell("Grade", [el("span", `net-badge ${tone}`, label)]),
                cell("At ONU", [el("span", "onu-dbm", dbm(o.signal_1490_dbm))]),
                cell("At OLT", [el("span", "onu-dbm", dbm(o.signal_1310_dbm))]),
                cell("Status", [onuStatus(o.status)]),
                cell("Port", o.location || "—"),
            );
            return tr;
        }));
    }
}

// ---- Loading and polling ----

async function loadOverview() {
    try {
        const [overview, events] = await Promise.all([
            api(`/network/overview?router=${ROUTER}`),
            api(`/network/events?router=${ROUTER}&limit=30`),
        ]);
        renderHero(overview);
        renderLinks(overview);
        renderTotals(overview);
        renderLinkPicker(overview);
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
        if (!state.sessions) $("net-sessions").innerHTML = stateRow(6, "error", e.message);
    }
}

async function loadHistory() {
    state.historyAt = Date.now();
    try {
        const link = state.link ? `&link=${encodeURIComponent(state.link)}` : "";
        renderHistory(await api(`/network/uplink-history?router=${ROUTER}&range=${state.range}${link}`));
    } catch (e) {
        $("net-chart").replaceChildren(el("div", "net-empty", e.message));
    }
}

async function loadOnus() {
    try {
        state.onus = await api("/network/onus");
        renderOnus(state.onus);
    } catch (e) {
        if (!state.onus) {
            $("onu-down").innerHTML = stateRow(6, "error", e.message);
            $("onu-weak").innerHTML = stateRow(6, "error", e.message);
        } else {
            $("onu-stale").hidden = false;
            $("onu-stale").textContent = `Couldn't refresh ONU data: ${e.message}`;
        }
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
    // The chart timer ticks every 5 s: Live redraws each tick, the longer ranges
    // only once a minute.
    const historyDue = () => { if (state.range === "live" || Date.now() - state.historyAt >= HISTORY_MS) loadHistory(); };
    state.timers = [every(loadOverview, OVERVIEW_MS), every(loadSessions, SESSIONS_MS), every(historyDue, LIVE_MS),
                    every(loadOnus, ONUS_MS)];
}

function loadAll() {
    loadOverview();
    loadSessions();
    loadHistory();
    loadOnus();
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
    $("net-link-pick").addEventListener("click", e => {
        const btn = e.target.closest("button[data-link]");
        if (!btn) return;
        state.link = btn.dataset.link;
        document.querySelectorAll("#net-link-pick button").forEach(b => b.setAttribute("aria-pressed", String(b === btn)));
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
