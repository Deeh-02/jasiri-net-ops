import { authHeaders, showView, registerRoute, can, getCurrentUser, registerLogoutHandler } from "./common.js";

// Network Health (#/network-health): is the uplink fast and clean right now,
// and what happened lately. Reads only /network/health/* (its own domain) and
// imports only common.js. Every value goes onto the page as text; the graphs
// are small SVGs drawn here so this page doesn't depend on another page's code.

const REFRESH_MS = 60000;
const tz = { timeZone: "Africa/Nairobi" };
const SVG = "http://www.w3.org/2000/svg";

const state = { range: "24h", timer: null, overview: null, charts: null, inside: null, access: null, resizeTimer: null, openRoutes: new Set() };
const $ = id => document.getElementById(id);

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
}

function svg(tag, attrs = {}) {
    const node = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
}

async function api(path, options = {}) {
    const res = await fetch(path, { ...options, headers: authHeaders(options.headers || {}) });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        const detail = Array.isArray(err.detail) ? err.detail.map(d => d.msg).join("; ") : err.detail;
        throw new Error(detail || `Couldn't load (${res.status})`);
    }
    return res.json();
}

// ---- Formatting ----

const when = iso => (iso ? new Date(iso).toLocaleString("en-GB", { ...tz, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");
const hm = iso => new Date(iso).toLocaleTimeString("en-GB", { ...tz, hour: "2-digit", minute: "2-digit" });

function minutesText(minutes) {
    if (minutes == null) return "—";
    const m = Math.max(0, Math.round(minutes));
    const d = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60), r = m % 60;
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${r}m`;
    return `${r} min`;
}

const ago = iso => (iso ? `${minutesText((Date.now() - new Date(iso)) / 60000)} ago` : "never");

function num(v, digits = 1) {
    if (v == null || Number.isNaN(v)) return "—";
    return Math.abs(v) >= 100 ? Math.round(v).toLocaleString("en-GB") : (+v.toFixed(digits)).toString();
}

// Glyph + word for every status, never colour alone.
const STATUS = {
    GREEN: ["●", "All good", "nh-ok"],
    AMBER: ["▲", "Warning", "nh-warn"],
    RED: ["◆", "Problem", "nh-bad"],
    OFFLINE: ["▼", "Offline", "nh-bad"],
    UNKNOWN: ["○", "Unknown (no feed)", "nh-unknown"],
    NOT_REPORTING: ["○", "Not reporting yet", "nh-unknown"],
};
function badge(status) {
    const [glyph, word, cls] = STATUS[status] || STATUS.UNKNOWN;
    return el("span", `nh-badge ${cls}`, `${glyph} ${word}`);
}

const PROBE_NAMES = { "p-gw": "Airtel gateway", "p-cf": "1.1.1.1", "p-goog": "8.8.8.8", "p-nbo": "Nairobi (KIXP)", "p-fra": "Frankfurt",
                      "p-za": "South Africa (Vodacom)" };
function probeName(name) {
    if (name.startsWith("i-")) return `${PROBE_NAMES[`p-${name.slice(2)}`] || name} via CCR2004`;
    return PROBE_NAMES[name] || name;
}

// ---- Banner, devices ----

function drawBanner(ov) {
    const banner = $("nh-banner");
    banner.replaceChildren();
    const worst = ov.banner;
    if (!worst && ov.waiting) {
        banner.className = "nh-banner nh-unknown";
        banner.append(el("strong", null, "○ Waiting for data"),
                      el("span", null, " Nothing has arrived from the Frankfurt server yet."));
        return;
    }
    if (!worst) {
        banner.className = "nh-banner nh-ok";
        banner.append(el("strong", null, "● All good"), el("span", null, " No problem on any device right now."));
        return;
    }
    const [glyph, , cls] = STATUS[worst.severity] || STATUS.RED;
    banner.className = `nh-banner ${cls}`;
    banner.append(el("strong", null, `${glyph} ${worst.device_label}: ${worst.label}`),
                  el("span", null, ` ${worst.text}. Since ${hm(worst.started_at)}.`));
}

function drawDevices(ov) {
    const wrap = $("nh-devices");
    wrap.replaceChildren(...ov.devices.map(device => {
        const card = el("article", "nh-device");
        const head = el("div", "nh-device-head");
        head.append(el("h3", "nh-device-name", device.label), badge(device.status));
        card.append(head, el("div", "nh-device-seen", `Last ${device.device === "feed" ? "post" : "reading"}: ${ago(device.last_seen)}`));
        if (device.device === "feed" && ov.feed.source) {
            card.append(el("div", "nh-device-seen", `${ov.feed.source} · routers' public IP ${ov.feed.from_ip || "—"}`));
        }
        const list = el("ul", "nh-device-rules");
        if (!device.active.length) list.append(el("li", "nh-none", "No active problems"));
        for (const rule of device.active) {
            const item = el("li");
            const [glyph, , cls] = STATUS[rule.severity] || STATUS.AMBER;
            item.append(el("span", `nh-rule-glyph ${cls}`, glyph), el("strong", null, ` ${rule.label}`),
                        el("span", "nh-rule-text", ` ${rule.text}. Since ${hm(rule.started_at)}`));
            if (rule.is_flapping) item.append(el("span", "nh-tag", "flapping"));
            list.append(item);
        }
        card.append(list);
        return card;
    }));
}

// ---- Incidents and the SMS log ----

const RULE_LABELS = {
    FEED_SILENT: "No data from Frankfurt", ROUTER_SILENT: "No readings", UPLINK_CONGESTED: "Uplink busy",
    SPEED_LOW: "Night speed test low", LATENCY_HIGH: "Latency high", LOSS: "Packet loss", JITTER_HIGH: "Jitter high",
    OPTIC_DEGRADING: "SFP signal weak", LINK_DOWN: "Uplink dropped", LINK_ERRORS: "Link errors", CPU_HIGH: "CPU high",
    DNS_ROUTER: "Router DNS failing or slow", OLT_CRC: "OLT uplink damaging frames", OLT_RESTART: "OLT restarted",
    OLT_LINK_DOWN: "OLT link down", OLT_SNMP_SILENT: "OLT not answering SNMP",
    INNER_PATH_LOSS: "CCR2004 path out losing packets", INNER_PATH_DOWN: "CCR2004 path out down",
    CUSTOMER_PATH_LOSS: "Customer path losing packets", CUSTOMER_PATH_DOWN: "Customer path down",
    AREA_SILENT: "Area gone silent", LANLINK_DOWN: "Inside link went down", LANLINK_DAMAGE: "Inside link damaging frames",
    LANLINK_DROPS: "Inside link dropping frames",
    PON_DOWN: "Whole PON down", PON_FIBRE_FAULT: "Fibre/splitter fault likely", PON_POWER_CUT: "Power cut on a PON",
    PON_LIGHT_DEGRADING: "Shared fibre/splitter degrading", ONT_LIGHT_LOSS: "Light loss at an ONT",
    ONT_LIGHT_DEGRADING: "Light degrading at an ONT", SITE_PPP_DOWN: "Site offline (PPPoE)", PPP_MASS_DROP: "Mass PPPoE drop",
    LINE_NO_INTERNET: "Line up, no internet",
};
const DEVICE_LABELS = { ccr2116: "CCR2116", ccr2004: "CCR2004", feed: "Frankfurt feed", olt: "OLT", crs326: "CRS326" };
const PEAK_UNITS = { UPLINK_CONGESTED: "%", LATENCY_HIGH: " ms", LOSS: "%", JITTER_HIGH: " ms", CPU_HIGH: "%",
                     OPTIC_DEGRADING: " dBm", SPEED_LOW: " Mbps", ROUTER_SILENT: " min", FEED_SILENT: " min",
                     OLT_CRC: "%", OLT_SNMP_SILENT: " min", INNER_PATH_LOSS: "%", CUSTOMER_PATH_LOSS: "%",
                     AREA_SILENT: " Mbps", PON_DOWN: " ONTs", PON_FIBRE_FAULT: " ONTs", PON_POWER_CUT: " ONTs",
                     PON_LIGHT_DEGRADING: " dB", ONT_LIGHT_DEGRADING: " dB", SITE_PPP_DOWN: " min", PPP_MASS_DROP: " sessions",
                     LINE_NO_INTERNET: " min" };
// CRS326 ports (the incident subject of OLT_LINK_DOWN, the link events table).
const PORT_NAMES = { "sfp-sfpplus1": "CCR2116", "sfp-sfpplus2": "CCR2004", "sfp-sfpplus3": "OLT uplink", "sfp-sfpplus4": "Proxmox" };
// Inside links (lanlink): "dev port" subjects.
const LANLINK_NAMES = { "ccr2116 sfp-sfpplus4": "CCR2116 → CRS326", "ccr2004 sfp-sfpplus1": "CCR2004 → CRS326",
    "crs326 sfp-sfpplus1": "CRS326 → CCR2116", "crs326 sfp-sfpplus2": "CRS326 → CCR2004", "crs326 sfp-sfpplus3": "CRS326 → OLT",
    "crs326 sfp-sfpplus4": "CRS326 → Proxmox" };
const subjectName = s => (PORT_NAMES[s] ? `${PORT_NAMES[s]}, ${s}` : LANLINK_NAMES[s] || insideProbeName(s) || probeName(s));

function cell(label, child) {
    const td = el("td");
    td.dataset.label = label;
    if (child == null || typeof child === "string") td.textContent = child ?? "—";
    else td.append(child);
    return td;
}

function emptyRow(tbody, cols, text) {
    const tr = el("tr");
    const td = el("td", "nh-empty", text);
    td.colSpan = cols;
    tr.append(td);
    tbody.replaceChildren(tr);
}

function drawIncidents(list) {
    const tbody = $("nh-incidents");
    if (!list.length) return emptyRow(tbody, 7, "No incidents yet.");
    tbody.replaceChildren(...list.map(i => {
        const tr = el("tr");
        const lasted = i.ended_at ? minutesText((new Date(i.ended_at) - new Date(i.started_at)) / 60000) : "ongoing";
        const subject = i.facts?.label || (i.subject ? subjectName(i.subject) : "");
        const problem = `${RULE_LABELS[i.rule] || i.rule}${subject ? ` (${subject})` : ""}`;
        const sms = i.is_flapping ? "flapping, not sent" : (i.fire_sms_sent ? "sent" : "—");
        const peak = i.rule === "SPEED_LOW" && i.facts && i.facts.failed ? "test failed"
            : i.rule === "OLT_RESTART" ? (i.facts?.previous_uptime_s == null ? "—" : `had been up ${minutesText(i.facts.previous_uptime_s / 60)}`)
            : (i.peak_value == null ? "—" : `${num(i.peak_value)}${PEAK_UNITS[i.rule] || ""}`);
        tr.append(cell("Started", when(i.started_at)), cell("Device", DEVICE_LABELS[i.device] || i.device),
                  cell("Problem", problem), cell("Level", badge(i.severity)), cell("Lasted", lasted),
                  cell("Worst", peak), cell("SMS", sms));
        if (!i.ended_at) tr.classList.add("nh-open");
        return tr;
    }));
}

function drawSms(sms) {
    const mode = sms.mode === "live" ? "sending" : "log-only (nothing is sent)";
    $("nh-sms-sub").textContent = `${sms.sent_today} of ${sms.cap} sent · ${mode} · ${sms.recipients} recipient${sms.recipients === 1 ? "" : "s"}`;
    const tbody = $("nh-sms");
    if (!sms.log.length) return emptyRow(tbody, 3, "No alerts today.");
    tbody.replaceChildren(...sms.log.map(a => {
        const tr = el("tr");
        const outcome = el("span", `nh-outcome nh-outcome-${a.outcome}`, a.outcome);
        const wrap = el("span");
        wrap.append(outcome);
        if (a.reason) wrap.append(el("span", "nh-reason", ` ${a.reason}`));
        tr.append(cell("Time", hm(a.created_at)), cell("Message", a.message), cell("Outcome", wrap));
        return tr;
    }));
}

// ---- Where content is served from (traceroutes at 21:00 and 04:00) ----

// Glyph + word, never colour alone (design.md).
const SERVED = {
    kenya: ["●", "nh-ok"],
    south_africa: ["◆", "nh-info"],
    africa: ["◐", "nh-unknown"],
    abroad: ["▲", "nh-warn"],
};

function servedBadge(site, labels) {
    if (!site.verdict) return el("span", "nh-badge nh-unknown", site.status === "failed" ? "○ Check failed" : "○ No answer");
    const [glyph, cls] = SERVED[site.verdict] || ["◐", "nh-unknown"];
    return el("span", `nh-badge ${cls}`, `${glyph} ${labels[site.verdict] || site.verdict}`);
}

function hopList(site) {
    if (!site.hops.length) return el("p", "nh-hops-none", site.status === "failed" ? "The traceroute failed: no hops." : "No hops recorded.");
    const list = el("ol", "nh-hops");
    for (const hop of site.hops) {
        const li = el("li");
        li.append(el("span", "nh-hop-n", hop.hop == null ? "?" : String(hop.hop)),
                  el("span", "nh-hop-addr", hop.address || "no reply"),
                  el("span", "nh-hop-ms", hop.ms == null ? "—" : `${num(hop.ms)} ms`));
        if (!hop.address) li.classList.add("is-silent");
        list.append(li);
    }
    return list;
}

// The traceroute stops at 20 hops: a far target can end on the last hop
// that answered instead of the target. Said in words, not a failure.
function lastHopCell(site) {
    if (site.last_ms == null) return cell("Last hop", "—");
    const wrap = el("span", null, `${num(site.last_ms)} ms`);
    if (site.reached_target === false) {
        wrap.append(el("span", "nh-route-note", ` · stopped at hop ${site.last_hop_number ?? "?"}, before the target`));
    }
    return cell("Last hop", wrap);
}

function siteName(site) {
    const isOpen = state.openRoutes.has(site.host);
    const toggle = el("button", "nh-route-toggle", `${isOpen ? "▾" : "▸"} ${site.label}`);
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", String(isOpen));
    toggle.setAttribute("aria-controls", `nh-hops-${site.host}`);
    if (site.label === site.host) return toggle;
    const wrap = el("span", "nh-route-name");
    wrap.append(toggle, el("span", "nh-route-host", site.host));
    return wrap;
}

function routeRows(sites, data, nameHeader, verdictHeader) {
    const rows = [];
    for (const site of sites) {
        const isOpen = state.openRoutes.has(site.host);
        const tr = el("tr", "nh-route-row");
        const checked = `${when(site.checked_at)} (${data.slots[site.slot]})`;
        let change = el("span", "nh-route-same", !site.verdict ? "Not compared (no answer)"
            : (site.previous_checked_at ? "No change" : "First check of this time"));
        if (site.change) {
            change = el("span", "nh-badge nh-warn",
                `⇄ was ${data.labels[site.change.was]}, now ${data.labels[site.change.now]}`);
            change.title = `Compared with the ${data.slots[site.slot]} check of ${when(site.previous_checked_at)}`;
        }
        tr.append(cell(nameHeader, siteName(site)), cell(verdictHeader, servedBadge(site, data.labels)),
                  lastHopCell(site), cell("Checked", checked), cell("Since the last check", change));
        tr.addEventListener("click", () => {
            if (state.openRoutes.has(site.host)) state.openRoutes.delete(site.host);
            else state.openRoutes.add(site.host);
            drawRoutes(data);
        });
        rows.push(tr);
        if (isOpen) {
            const detail = el("tr", "nh-route-detail");
            detail.id = `nh-hops-${site.host}`;
            const td = el("td");
            td.colSpan = 5;
            const ends = site.reached_target === false
                ? ` · stopped at hop ${site.last_hop_number ?? "?"} (${site.last_hop_address}), the 20-hop limit` : "";
            td.append(el("div", "nh-hops-head", `${site.host} → ${site.ip || "no address"} · ${checked}${ends}`), hopList(site));
            detail.append(td);
            rows.push(detail);
        }
    }
    return rows;
}

function drawRoutes(data) {
    const l = data.limits;
    $("nh-routes-sub").textContent = `traceroute from the CCR2116 at 21:00 and 04:00 · last hop under ${l.kenya_under_ms} ms = Kenya, `
        + `${l.za_from_ms}–${l.za_under_ms} ms = South Africa (likely), ${l.abroad_from_ms} ms or more = abroad · tap a row for its hops`;
    const content = data.sites.filter(s => s.group !== "international");
    const international = data.sites.filter(s => s.group === "international");
    const tbody = $("nh-routes");
    if (!content.length) emptyRow(tbody, 5, "No traceroutes yet. They run at 21:00 and 04:00.");
    else tbody.replaceChildren(...routeRows(content, data, "Site", "Served from"));
    const intl = $("nh-routes-intl");
    if (!international.length) emptyRow(intl, 5, "No traceroutes yet. Frankfurt and 1.1.1.1 start with the 21:00 run.");
    else intl.replaceChildren(...routeRows(international, data, "Target", "Path ends in"));
}

// ---- Graphs ----

// Fixed colour per probe target (never by rank): CCR2004 twins (i-*) share
// their p-* colour, dashed; new-provider twins are dotted.
// p-za's green can't be told from 1.1.1.1's orange (colour-blind) or
// Nairobi's aqua on colour alone, so it is also dash-dotted. A probe this
// page doesn't know yet is grey, under its raw name.
const PROBE_COLOURS = { "p-gw": "--nh-c1", "p-cf": "--nh-c2", "p-nbo": "--nh-c3", "p-goog": "--nh-c4", "p-fra": "--nh-c5",
                        "p-za": "--nh-c6" };
const PROBE_DASHES = { "p-za": "9 3 2 3" };
function probeStyle(name) {
    const base = name.replace(/^i-/, "p-").replace(/-new$/, "");
    const dash = name.startsWith("i-") ? "6 4" : (name.endsWith("-new") ? "2 3" : (PROBE_DASHES[base] || null));
    return { colour: PROBE_COLOURS[base] || "--nh-other", dash };
}

function niceTicks(min, max, count = 4) {
    const span = max - min || 1;
    const raw = span / count;
    const power = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map(m => m * power).find(s => s >= raw) || raw;
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(+v.toFixed(6));
    return ticks;
}

function timeTicks(start, end) {
    const hourMs = 3600000;
    const step = state.range === "24h" ? 4 * hourMs : 24 * hourMs;
    // Ticks fall on Nairobi hours / midnights (UTC+3, no DST).
    const offset = 3 * hourMs;
    const ticks = [];
    for (let t = Math.ceil((start + offset) / step) * step - offset; t <= end; t += step) ticks.push(t);
    return ticks;
}

function tickLabel(t) {
    const d = new Date(t);
    return state.range === "24h" ? d.toLocaleTimeString("en-GB", { ...tz, hour: "2-digit", minute: "2-digit" })
        : d.toLocaleDateString("en-GB", { ...tz, weekday: "short", day: "numeric" });
}

/* One graph. spec: {title, sub, unit, series: [{name, colour, dash, dots, values: [{t, v, note}]}],
   refs: [{v, label}], marks: [{t, label}] (red ticks on the baseline: outages), yFloor0,
   fmt (optional: the y-axis number format, default num)}. */
function drawGraph(fig, spec) {
    fig.replaceChildren();
    const head = el("figcaption", "nh-graph-head");
    head.append(el("span", "nh-graph-title", spec.title));
    if (spec.sub) head.append(el("span", "nh-graph-sub", spec.sub));
    fig.append(head);
    const values = spec.series.flatMap(s => s.values.filter(p => p.v != null).map(p => p.v));
    if (!values.length) {
        fig.append(el("div", "nh-graph-empty", "No readings in this range yet."));
        return;
    }
    const width = Math.max(fig.clientWidth - 2, 260), height = 168;
    // Reference lines are labelled in a right margin, clear of the data.
    const pad = { l: 44, r: spec.refs?.length ? 64 : 10, t: 8, b: 22 };
    const end = Date.now(), start = end - (state.range === "24h" ? 24 : 168) * 3600000;
    const refValues = (spec.refs || []).map(r => r.v);
    let lo = Math.min(...values, ...refValues), hi = Math.max(...values, ...refValues);
    if (spec.yFloor0) lo = Math.min(0, lo);
    if (hi === lo) hi = lo + 1;
    const margin = (hi - lo) * 0.08;
    hi += margin;
    if (!spec.yFloor0) lo -= margin;
    const x = t => pad.l + (t - start) / (end - start) * (width - pad.l - pad.r);
    const y = v => pad.t + (hi - v) / (hi - lo) * (height - pad.t - pad.b);

    const root = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
                              "aria-label": `${spec.title} over the last ${state.range === "24h" ? "24 hours" : "7 days"}` });
    for (const v of niceTicks(lo, hi)) {
        root.append(svg("line", { x1: pad.l, x2: width - pad.r, y1: y(v), y2: y(v), class: "nh-grid" }));
        const label = svg("text", { x: pad.l - 6, y: y(v) + 3, class: "nh-axis", "text-anchor": "end" });
        label.textContent = (spec.fmt || num)(v);
        root.append(label);
    }
    // Every other time label when they'd sit closer than 56 px (phones).
    let times = timeTicks(start, end);
    while (times.length > 1 && (width - pad.l - pad.r) / times.length < 56) times = times.filter((_, i) => i % 2 === 0);
    for (const t of times) {
        const label = svg("text", { x: x(t), y: height - 6, class: "nh-axis", "text-anchor": "middle" });
        label.textContent = tickLabel(t);
        root.append(label);
    }
    for (const ref of spec.refs || []) {
        root.append(svg("line", { x1: pad.l, x2: width - pad.r, y1: y(ref.v), y2: y(ref.v), class: "nh-ref" }));
        const label = svg("text", { x: width - pad.r + 6, y: y(ref.v) + 3, class: "nh-axis" });
        label.textContent = ref.label;
        root.append(label);
    }
    const switched = state.charts?.settings?.provider_switched_at;
    if (switched && new Date(switched) > start) {
        const sx = x(new Date(switched).getTime());
        root.append(svg("line", { x1: sx, x2: sx, y1: pad.t, y2: height - pad.b, class: "nh-switch" }));
        const label = svg("text", { x: sx + 4, y: pad.t + 10, class: "nh-axis" });
        label.textContent = "provider switch";
        root.append(label);
    }
    const gapMs = (state.charts?.bucket_seconds || 300) * 2500;
    for (const s of spec.series) {
        const colour = `var(${s.colour})`;
        if (s.dots) {
            for (const p of s.values.filter(p => p.v != null)) {
                root.append(svg("circle", { cx: x(p.t), cy: y(p.v), r: 4.5, fill: s.hollow ? "var(--surface)" : colour,
                                            stroke: colour, "stroke-width": 2, class: "nh-dot" }));
            }
            continue;
        }
        let d = "", prev = null;
        for (const p of s.values) {
            if (p.v == null) { prev = null; continue; }
            d += `${prev && p.t - prev.t <= gapMs ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`;
            prev = p;
        }
        const path = svg("path", { d, fill: "none", stroke: colour, "stroke-width": 2, "stroke-linejoin": "round",
                                   "stroke-linecap": "round" });
        if (s.dash) path.setAttribute("stroke-dasharray", s.dash);
        root.append(path);
    }
    for (const mark of spec.marks || []) {
        root.append(svg("rect", { x: x(mark.t) - 1.5, y: height - pad.b - 6, width: 3, height: 6, class: "nh-outage" }));
    }
    const box = el("div", "nh-graph-box");
    box.append(root);
    addHover(box, root, spec, { x, start, end, width, height, pad });
    fig.append(box);
    if (spec.series.length >= 2) {
        const legend = el("div", "nh-legend");
        for (const s of spec.series) {
            const item = el("span", "nh-legend-item");
            let swatch;
            if (s.dots) {
                swatch = el("span", `nh-swatch is-hollow`);
                swatch.style.setProperty("--swatch", `var(${s.colour})`);
                if (!s.hollow) swatch.style.background = `var(${s.colour})`;
            } else {
                swatch = svg("svg", { width: 22, height: 6, class: "nh-swatch-line", "aria-hidden": "true" });
                const line = svg("line", { x1: 1, x2: 21, y1: 3, y2: 3, stroke: `var(${s.colour})`, "stroke-width": 2,
                                           "stroke-linecap": "round" });
                if (s.dash) line.setAttribute("stroke-dasharray", s.dash);
                swatch.append(line);
            }
            item.append(swatch, document.createTextNode(s.name));
            legend.append(item);
        }
        fig.append(legend);
    }
    if (spec.marks?.length) fig.append(el("div", "nh-graph-note", spec.markNote || "Red ticks on the baseline: no reply at all (outage)."));
}

// Crosshair + a label with every series' value at the nearest reading.
function addHover(box, root, spec, g) {
    const line = svg("line", { y1: g.pad.t, y2: g.height - g.pad.b, class: "nh-cross", visibility: "hidden" });
    root.append(line);
    const tip = el("div", "nh-tip");
    tip.hidden = true;
    box.append(tip);
    const nearest = (values, t) => values.reduce((best, p) => (!best || Math.abs(p.t - t) < Math.abs(best.t - t) ? p : best), null);
    root.addEventListener("pointermove", e => {
        const rect = root.getBoundingClientRect();
        const px = e.clientX - rect.left;
        if (px < g.pad.l || px > g.width - g.pad.r) { line.setAttribute("visibility", "hidden"); tip.hidden = true; return; }
        const t = g.start + (px - g.pad.l) / (g.width - g.pad.l - g.pad.r) * (g.end - g.start);
        const reach = spec.series.some(s => s.dots) ? 6 * 3600000 : (state.charts?.bucket_seconds || 300) * 1500;
        const rows = [];
        let at = null;
        for (const s of spec.series) {
            const p = nearest(s.values, t);
            if (!p || Math.abs(p.t - t) > reach) continue;
            at = at ?? p.t;
            rows.push(`${s.name}: ${p.note || (p.v == null ? "no reply" : `${num(p.v)}${spec.unit}`)}`);
        }
        if (!rows.length) { line.setAttribute("visibility", "hidden"); tip.hidden = true; return; }
        line.setAttribute("x1", g.x(at)); line.setAttribute("x2", g.x(at)); line.setAttribute("visibility", "visible");
        tip.replaceChildren(el("div", "nh-tip-time", when(new Date(at).toISOString())), ...rows.map(r => el("div", null, r)));
        tip.hidden = false;
        tip.style.left = `${Math.min(Math.max(px + 12, 0), g.width - 190)}px`;
    });
    root.addEventListener("pointerleave", () => { line.setAttribute("visibility", "hidden"); tip.hidden = true; });
}

const T = iso => new Date(iso).getTime();
const FAR_PROBE_MS = 100;

function median(values) {
    const sorted = values.filter(v => v != null).sort((a, b) => a - b);
    return sorted.length ? sorted[Math.floor(sorted.length / 2)] : null;
}

function probeSeries(rows, field, names) {
    return names.map(name => ({ name: probeName(name), ...probeStyle(name),
        values: rows.filter(r => r.name === name).map(r => ({ t: T(r.t), v: r[field] })) }));
}

// DNS lookup time: one line per resolver (the slower of its two lookups),
// averaged over the runs with no failure; a failed lookup is a red tick on
// the baseline and is named in the hover label, not drawn as 10 s.
const DNS_NAMES = { rt: "Router", g8: "Google 8.8.8.8", q9: "Quad9 9.9.9.9" };
const DNS_COLOURS = { rt: "--nh-c1", g8: "--nh-c4", q9: "--nh-c3" };
const DNS_ORDER = ["rt", "g8", "q9"];

function drawDnsGraph(c) {
    const rows = c.dns || [];
    const names = [...new Set(rows.map(r => r.name))]
        .sort((a, b) => (DNS_ORDER.includes(a) ? DNS_ORDER.indexOf(a) : 99) - (DNS_ORDER.includes(b) ? DNS_ORDER.indexOf(b) : 99) || a.localeCompare(b));
    const label = name => DNS_NAMES[name] || `${name} (${rows.find(r => r.name === name)?.server || "?"})`;
    const series = names.map(name => ({
        name: label(name), colour: DNS_COLOURS[name] || "--nh-other",
        values: rows.filter(r => r.name === name).map(r => ({
            t: T(r.t), v: r.ms,
            note: r.fails ? `${r.fails} failed lookup${r.fails === 1 ? "" : "s"}${r.ms == null ? "" : `, otherwise ${num(r.ms)} ms`}` : null,
        })),
    }));
    const slow = c.thresholds.dns_slow_ms;
    // The 500 ms line only once lookups get anywhere near it: on a healthy
    // day (1-10 ms) it would flatten every line to the floor.
    const worst = Math.max(0, ...rows.map(r => r.ms ?? 0));
    const failed = rows.filter(r => r.fails > 0);
    drawGraph($("nh-g-dns"), {
        title: "DNS lookup time", unit: " ms", yFloor0: true,
        sub: `slower of two lookups, average per ${c.bucket_seconds / 60} min · router alert: failing or over ${slow} ms twice in a row (page only)`,
        series,
        refs: worst > slow / 5 ? [{ v: slow, label: `${slow} ms` }] : [],
        marks: failed.map(r => ({ t: T(r.t) })),
        markNote: "Red ticks on the baseline: a failed lookup (hover for which resolver).",
    });
}

function drawGraphs() {
    const c = state.charts;
    if (!c) return;
    const capacity = c.settings.uplink_capacity_mbps;
    drawGraph($("nh-g-uplink"), {
        title: "Uplink traffic (sfp-sfpplus1)", unit: " Mbps", yFloor0: true,
        sub: `average per ${c.bucket_seconds / 60} min · capacity ${num(capacity)} Mbps`,
        series: [
            { name: "Download (from Airtel)", colour: "--nh-c1", values: c.load.map(r => ({ t: T(r.t), v: r.down })) },
            { name: "Upload", colour: "--nh-c3", values: c.load.map(r => ({ t: T(r.t), v: r.up })) },
            ...(c.load.some(r => r.new_down != null)
                ? [{ name: "New provider download", colour: "--nh-c1", dash: "2 3", values: c.load.map(r => ({ t: T(r.t), v: r.new_down })) }] : []),
        ],
        refs: [{ v: capacity, label: "capacity" }, { v: capacity * c.thresholds.uplink_pct / 100, label: `${c.thresholds.uplink_pct}% = busy` }],
    });
    const names = [...new Set(c.probes.map(r => r.name))].sort();
    const outages = c.probes.filter(r => r.loss != null && r.loss >= 100 && ["p-gw", "p-cf"].includes(r.name));
    // Frankfurt (~200 ms) on the same scale as the gateway (~2 ms) flattens
    // every nearby line, so far targets get their own graph. Decided by each
    // probe's typical round trip, not a list: new probes sort themselves.
    const isFar = name => median(c.probes.filter(r => r.name === name).map(r => r.rtt)) > FAR_PROBE_MS;
    drawGraph($("nh-g-latency"), {
        title: "Latency, nearby", sub: "average round trip; gaps are outages", unit: " ms", yFloor0: true,
        series: probeSeries(c.probes, "rtt", names.filter(n => !isFar(n))),
        marks: outages.map(r => ({ t: T(r.t) })),
    });
    drawGraph($("nh-g-latency-far"), {
        title: "Latency, far", sub: `targets over ${FAR_PROBE_MS} ms`, unit: " ms", yFloor0: true,
        series: probeSeries(c.probes, "rtt", names.filter(isFar)),
    });
    // Loss and jitter: the two probes the alerts judge, plus Nairobi, South
    // Africa and Frankfurt (drawn only), in this fixed order. Each point is the average
    // over its bucket (5 min on 24 h, 30 min on 7 d): one probe test is 10
    // pings, so a single minute's loss moves in steps of 10 %.
    const lossProbes = ["p-gw", "p-cf", "p-nbo", "p-za", "p-fra"].filter(n => names.includes(n));
    const qualitySub = `average per ${c.bucket_seconds / 60} min · alerts judge the Airtel gateway and 1.1.1.1 only`;
    drawGraph($("nh-g-loss"), {
        title: "Packet loss", sub: qualitySub, unit: "%", yFloor0: true,
        series: probeSeries(c.probes, "loss", lossProbes),
        refs: [{ v: c.thresholds.loss_amber_pct, label: `${c.thresholds.loss_amber_pct}%` }, { v: c.thresholds.loss_red_pct, label: `${c.thresholds.loss_red_pct}%` }],
    });
    drawGraph($("nh-g-jitter"), {
        title: "Jitter", sub: qualitySub, unit: " ms", yFloor0: true,
        series: probeSeries(c.probes, "jitter", lossProbes),
        refs: [{ v: c.thresholds.jitter_ms, label: `${c.thresholds.jitter_ms} ms` }],
    });
    drawDnsGraph(c);
    const speedPoint = r => ({ t: T(r.t), v: r.status === "failed" ? 0 : r.mbps,
        note: r.status === "failed" ? "test failed" : `${num(r.mbps)} Mbps, load before ${num(r.load_before_mbps)} Mbps` });
    const nightHour = r => new Date(T(r.t) + 3 * 3600000).getUTCHours() === 3;
    drawGraph($("nh-g-speed"), {
        title: "Speed tests", sub: "single stream to Frankfurt; only 03:30 is judged", unit: " Mbps", yFloor0: true,
        series: [
            { name: "03:30 (judged)", colour: "--nh-c2", dots: true, values: c.speed.filter(r => r.kind === "speed" && nightHour(r)).map(speedPoint) },
            { name: "20:30 (peak, shown only)", colour: "--nh-c4", dots: true, hollow: true, values: c.speed.filter(r => r.kind === "speed" && !nightHour(r)).map(speedPoint) },
            ...(c.speed.some(r => r.kind === "speednew")
                ? [{ name: "New provider", colour: "--nh-c5", dots: true, values: c.speed.filter(r => r.kind === "speednew").map(speedPoint) }] : []),
        ],
    });
    drawGraph($("nh-g-optic"), {
        title: "SFP receive power", sub: "uplink optic", unit: " dBm",
        series: [
            { name: "Airtel SFP", colour: "--nh-c1", values: c.link.filter(r => r.kind === "link").map(r => ({ t: T(r.t), v: r.rx })) },
            ...(c.link.some(r => r.kind === "linknew")
                ? [{ name: "New provider SFP", colour: "--nh-c5", dash: "2 3", values: c.link.filter(r => r.kind === "linknew").map(r => ({ t: T(r.t), v: r.rx })) }] : []),
        ],
        refs: [{ v: c.thresholds.optic_floor_dbm, label: `${c.thresholds.optic_floor_dbm} dBm` }],
    });
    drawGraph($("nh-g-ppp"), {
        title: "PPPoE sessions", sub: "on the CCR2116 only", unit: "", yFloor0: true,
        series: [{ name: "Sessions", colour: "--nh-c3", values: c.load.map(r => ({ t: T(r.t), v: r.ppp })) }],
    });
    drawGraph($("nh-g-cpu"), {
        title: "Router CPU", unit: "%", yFloor0: true,
        series: [
            { name: "CCR2116", colour: "--nh-c1", values: c.load.map(r => ({ t: T(r.t), v: r.cpu })) },
            { name: "CCR2004", colour: "--nh-c2", values: c.inner_cpu.map(r => ({ t: T(r.t), v: r.cpu })) },
        ],
        refs: [{ v: c.thresholds.cpu_pct, label: `${c.thresholds.cpu_pct}%` }],
    });
    $("nh-graphs-sub").textContent = state.range === "24h" ? "last 24 hours, Nairobi time" : "last 7 days, Nairobi time";
}

// ---- Inside network (the OLT's uplink, read by the CCR2116 over SNMP) ----

// A damaged-frames percentage: one decimal, more when it is small (0.004 %).
function pctText(v) {
    if (v == null) return "—";
    if (v === 0) return "0";
    if (v >= 0.1) return v.toFixed(1);
    return (+v.toPrecision(2)).toString();
}

function countText(n) {
    if (n == null) return "—";
    if (n >= 1e9) return `${(n / 1e9).toFixed(1)} billion`;
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)} million`;
    return Math.round(n).toLocaleString("en-GB");
}

// Glyph + word, never colour alone.
const CRC_LEVELS = { green: ["●", "Clean", "nh-ok"], amber: ["▲", "Some damage", "nh-warn"], red: ["◆", "Damaging", "nh-bad"] };

function secondsText(s) {
    if (s == null) return "—";
    const total = Math.max(0, Math.round(s));
    if (total >= 3600) return minutesText(total / 60);
    const m = Math.floor(total / 60), r = total % 60;
    return m ? `${m} min ${r} s` : `${r} s`;
}

function drawInsideActive(d) {
    const list = $("nh-inside-active");
    if (!d.active.length) {
        list.replaceChildren(el("li", "nh-none", "No active problems inside the network"));
        return;
    }
    list.replaceChildren(...d.active.map(rule => {
        const item = el("li");
        const [glyph, , cls] = STATUS[rule.severity] || STATUS.AMBER;
        item.append(el("span", `nh-rule-glyph ${cls}`, glyph), el("strong", null, ` ${rule.device_label}: ${rule.label}`),
                    el("span", "nh-rule-text", ` ${rule.text}. Since ${hm(rule.started_at)}`));
        if (rule.is_flapping) item.append(el("span", "nh-tag", "flapping"));
        return item;
    }));
}

function drawCrcNow(d) {
    const box = $("nh-crc-now");
    const now = d.crc.current;
    if (!now) {
        box.replaceChildren(el("div", "nh-tile-label", "OLT uplink damaged frames"),
                            el("p", "nh-tile-line", d.snmp.last_poll_at ? "No complete pair of OLT readings yet."
                                : "No OLT readings yet. They start once the CCR2116 reads the OLT over SNMP."));
        return;
    }
    const [glyph, word, cls] = CRC_LEVELS[now.level] || CRC_LEVELS.amber;
    const head = el("div", "nh-tile-head");
    head.append(el("span", "nh-tile-value", `${pctText(now.pct)} %`), el("span", `nh-badge ${cls}`, `${glyph} ${word}`));
    const line = now.level === "green"
        ? (now.pct === 0 ? "CRS→OLT link is clean: no damaged frames." : `CRS→OLT link is clean: ${pctText(now.pct)} % of frames damaged.`)
        : `CRS→OLT link is damaging ${pctText(now.pct)} % of frames going to customers.`;
    const range = state.range === "24h" ? "last 24 h" : "last 7 days";
    const old = (Date.now() - new Date(now.t)) / 60000 > d.snmp.stale_after_minutes;
    box.replaceChildren(
        el("div", "nh-tile-label", "OLT uplink damaged frames"), head, el("p", "nh-tile-line", line),
        el("p", "nh-tile-sub", `5 min to ${hm(now.t)}${old ? " (old: see SNMP)" : ""} · ${range}: ${pctText(d.crc.range_pct)} % overall`
            + ` · green under ${d.crc.green_under_pct} %, red over ${d.crc.red_over_pct} %`),
    );
}

function drawSnmp(d) {
    const box = $("nh-snmp");
    const s = d.snmp;
    const range = state.range === "24h" ? "24 h" : "7 days";
    let status;
    if (!s.last_poll_at) status = el("span", "nh-badge nh-unknown", "○ Not reporting yet");
    else if (!s.last_success_at || s.is_stale) status = el("span", "nh-badge nh-warn", "▲ Not answering");
    else status = el("span", "nh-badge nh-ok", "● Answering");
    const head = el("div", "nh-tile-head");
    head.append(el("span", "nh-tile-value nh-tile-value-sm", s.last_success_at ? ago(s.last_success_at) : "never"), status);
    const lines = [el("div", "nh-tile-label", "OLT SNMP: last good read"), head];
    if (s.is_stale) lines.push(el("p", "nh-tile-line", `No complete read for over ${s.stale_after_minutes} min (last at ${when(s.last_success_at)}).`));
    lines.push(el("p", "nh-tile-sub", s.polls ? `${s.failed_polls} of ${s.polls} polls had a failed read in the last ${range}`
        : `No polls in the last ${range}`));
    box.replaceChildren(...lines);
}

function drawCrcGraph() {
    const d = state.inside;
    if (!d || d.error) return;
    const fig = $("nh-g-crc");
    const series = d.crc.series.map(r => ({ t: T(r.t), v: r.pct,
        note: `${pctText(r.pct)} % damaged (${countText(r.d_crc)} of ${countText(r.frames)} frames)` }));
    const worst = Math.max(0, ...d.crc.series.map(r => r.pct));
    const start = Date.now() - (state.range === "24h" ? 24 : 168) * 3600000;
    // The OLT link going down (a DAC swap does this) and OLT restarts, on the baseline.
    const marks = [
        ...d.link_events.filter(e => e.is_olt && e.down_at && T(e.down_at) > start).map(e => ({ t: T(e.down_at) })),
        ...d.restarts.filter(r => T(r.booted_at) > start).map(r => ({ t: T(r.booted_at) })),
    ];
    drawGraph(fig, {
        title: "OLT uplink damaged frames",
        sub: `frames from the CRS326 arriving damaged at OLT port 0/19/0, per ${d.bucket_seconds / 60} min · alert over ${d.crc.red_over_pct} % twice in a row`,
        unit: "%", yFloor0: true, fmt: v => (v >= 1 ? num(v) : (+v.toPrecision(2)).toString()),
        series: [{ name: "Damaged frames", colour: "--nh-c1", values: series }],
        // Always the 1 % line, so after a fix the line visibly sits far under it;
        // the 0.01 % line only once everything is that small.
        refs: [{ v: d.crc.red_over_pct, label: `${d.crc.red_over_pct}% alert` },
               ...(worst < 0.1 ? [{ v: d.crc.green_under_pct, label: `${d.crc.green_under_pct}%` }] : [])],
        marks, markNote: "Red ticks on the baseline: the OLT link went down or the OLT restarted (see the tables below).",
    });
}

function drawRestarts(d) {
    const tbody = $("nh-restarts");
    if (!d.restarts.length) return emptyRow(tbody, 3, `No OLT restarts in the last ${d.lookback_days} days.`);
    tbody.replaceChildren(...d.restarts.map(r => {
        const tr = el("tr");
        tr.append(cell("Back up at", when(r.booted_at)), cell("Had been up", minutesText(r.previous_uptime_s / 60)),
                  cell("Seen at", when(r.detected_at)));
        return tr;
    }));
}

function drawLinkEvents(d) {
    const tbody = $("nh-link-events");
    if (!d.link_events.length) return emptyRow(tbody, 5, `No port went down in the last ${d.lookback_days} days.`);
    tbody.replaceChildren(...d.link_events.map(e => {
        const tr = el("tr", e.is_olt ? "nh-olt-row" : null);
        const port = el("span", "nh-route-name");
        port.append(el("strong", null, `${e.is_olt ? "◆ " : ""}${e.label}`),
                    el("span", "nh-route-host", `${e.device_label || "CRS326"} ${e.interface}`));
        let outage;
        if (e.status === "back") outage = secondsText(e.outage_s);
        else if (e.status === "down") outage = el("span", "nh-badge nh-bad", `▼ Still down, ${secondsText((Date.now() - new Date(e.down_at)) / 1000)} so far`);
        else if (e.status === "no_up") outage = el("span", "nh-route-note", "unknown (its up was never received)");
        else outage = el("span", "nh-route-note", "unknown (went down before this list)");
        tr.append(cell("Port", port), cell("Went down", e.down_at ? when(e.down_at) : "—"),
                  cell("Came back", e.up_at ? when(e.up_at) : "—"), cell("Outage", outage), cell("Speed", e.speed || "—"));
        return tr;
    }));
}

// ---- Areas: per-VLAN traffic on the CCR2004 uplink ----

function mbpsText(v) {
    if (v == null) return "—";
    if (v >= 100) return `${Math.round(v)} Mbps`;
    if (v >= 1) return `${v.toFixed(1)} Mbps`;
    return `${(+v.toPrecision(2)).toString()} Mbps`;
}

// A 24 h download sparkline; gaps where there was no reading. The figures
// are also in the row's text and the title, never only in the line.
function sparkline(points) {
    const width = 140, height = 30, pad = 2;
    const end = Date.now(), start = end - 24 * 3600000;
    const values = points.filter(p => p.down != null);
    const box = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, class: "nh-spark", role: "img" });
    if (!values.length) {
        box.setAttribute("aria-label", "no readings in the last 24 hours");
        return box;
    }
    const hi = Math.max(...values.map(p => p.down), 0.1);
    const x = t => pad + (t - start) / (end - start) * (width - 2 * pad);
    const y = v => height - pad - v / hi * (height - 2 * pad);
    let d = "", prev = null;
    for (const p of points) {
        if (p.down == null) { prev = null; continue; }
        const t = T(p.t);
        d += `${prev && t - prev <= 1800000 ? "L" : "M"}${x(t).toFixed(1)},${y(p.down).toFixed(1)}`;
        prev = t;
    }
    box.append(svg("line", { x1: pad, x2: width - pad, y1: height - pad, y2: height - pad, class: "nh-grid" }),
               svg("path", { d, fill: "none", stroke: "var(--nh-c1)", "stroke-width": 1.5, "stroke-linejoin": "round" }));
    const label = `download over 24 hours: peak ${mbpsText(hi)}`;
    box.setAttribute("aria-label", label);
    const title = svg("title");
    title.textContent = label;
    box.append(title);
    return box;
}

function drawAreas(d) {
    const tbody = $("nh-areas");
    $("nh-areas-sub").textContent = `per VLAN on the CCR2004 uplink, every 5 min · alert when an area that's usually busy`
        + ` falls under ${d.thresholds.area_silent_mbps} Mbps twice in a row (07:00–23:00)`;
    if (!d.areas.length) return emptyRow(tbody, 5, "No area readings yet.");
    const silent = new Set(d.active.filter(a => a.rule === "AREA_SILENT").map(a => a.subject));
    tbody.replaceChildren(...d.areas.map(a => {
        const tr = el("tr", a.is_total ? "nh-area-total" : null);
        const name = el("span", "nh-route-name");
        name.append(el("strong", null, a.name), el("span", "nh-route-host", a.is_total ? `VLAN ${a.vlan}, not an area` : `VLAN ${a.vlan}`));
        const down = el("span", null, mbpsText(a.down_mbps));
        if (silent.has(`${a.name} (VLAN ${a.vlan})`)) {
            down.append(document.createTextNode(" "), el("span", "nh-badge nh-warn", "▼ Silent"));
        }
        tr.append(cell("Area", name), cell("Download", down), cell("Upload", mbpsText(a.up_mbps)),
                  cell("Last 24 h (download)", sparkline(a.spark)), cell("Reading", a.at ? hm(a.at) : "—"));
        return tr;
    }));
}

// ---- Inside links: port counters on the CCR2116, CCR2004 and CRS326 ----

const LANLINK_COLUMNS = [["link_downs", "Link downs"], ["rx_fcs", "FCS errors"], ["rx_err", "RX errors"],
                         ["rx_overflow", "RX overflow"], ["tx_drop", "TX drops"]];

function counterCell(row, counter) {
    const total = row.totals?.[counter];
    if (total == null) return el("span", "nh-route-note", "not reported");
    const wrap = el("span", "nh-counter");
    wrap.append(el("span", null, Math.round(total).toLocaleString("en-GB")));
    const last = row.last?.[counter], day = row.day?.[counter];
    const change = el("span", "nh-counter-change", `+${last == null ? "—" : last.toLocaleString("en-GB")} in 5 min · +${day == null ? "—" : day.toLocaleString("en-GB")} in 24 h`);
    if (last) change.classList.add("is-rising");
    wrap.append(change);
    return wrap;
}

function drawLanlinks(d) {
    const tbody = $("nh-lanlinks");
    $("nh-lanlinks-sub").textContent = `port counters every 5 min · alerts: a link down, over ${d.thresholds.lanlink_damage_over}`
        + ` damaged frames in 5 min; over ${d.thresholds.lanlink_drops_over} TX drops is a warning on the page`;
    if (!d.lanlinks.length) return emptyRow(tbody, 6, "No inside link readings yet.");
    tbody.replaceChildren(...d.lanlinks.map(row => {
        const tr = el("tr", row.dev === "crs326" && row.port === d.olt_interface ? "nh-olt-row" : null);
        const name = el("span", "nh-route-name");
        name.append(el("strong", null, row.label), el("span", "nh-route-host", `${row.dev} ${row.port} · ${row.at ? hm(row.at) : "—"}`));
        tr.append(cell("Link", name), ...LANLINK_COLUMNS.map(([key, label]) => cell(label, counterCell(row, key))));
        return tr;
    }));
}

// ---- Probes: the CCR2004's way out (inner) and pings to the boxes (hop) ----

const INSIDE_PROBES = {
    "i-core": ["To the CCR2116", "--nh-c1"], "i-fra": ["To Frankfurt", "--nh-c5"],
    "h-crs": ["CRS326", "--nh-c3"], "h-2004": ["CCR2004", "--nh-c2"], "h-olt": ["OLT (management)", "--nh-c5"],
    "h-cust": ["Customer path", "--nh-c1"],
};
function insideProbeName(name) {
    const base = name.replace(/-big$/, "");
    if (!INSIDE_PROBES[base]) return null;
    return `${INSIDE_PROBES[base][0]}${name.endsWith("-big") ? ", 1300-byte" : ""}`;
}
// The 1300-byte twin is the same colour, dashed: same target, bigger packets.
function insideSeries(rows, field, names) {
    return names.map(name => {
        const base = name.replace(/-big$/, "");
        return { name: insideProbeName(name) || name, colour: INSIDE_PROBES[base]?.[1] || "--nh-other",
                 dash: name.endsWith("-big") ? "6 4" : null,
                 values: rows.filter(r => r.name === name).map(r => ({ t: T(r.t), v: r[field] })) };
    });
}
const byBaseThenSize = (a, b) => a.replace(/-big$/, "").localeCompare(b.replace(/-big$/, "")) || a.length - b.length;

function drawInsideProbes() {
    const c = state.charts, d = state.inside;
    if (!c || !d || d.error) return;
    const per = `average per ${c.bucket_seconds / 60} min`;
    const inner = c.probes.filter(r => /^i-(core|fra)(-big)?$/.test(r.name));
    const innerNames = [...new Set(inner.map(r => r.name))].sort(byBaseThenSize);
    const lossPct = d.thresholds.path_loss_pct;
    drawGraph($("nh-g-inner-loss"), {
        title: "CCR2004 path out: packet loss", unit: "%", yFloor0: true,
        sub: `${per} · alert: Frankfurt ${lossPct}% or more 3 readings in a row, or no reply twice`,
        series: insideSeries(inner, "loss", innerNames),
        refs: [{ v: lossPct, label: `${lossPct}%` }],
        marks: inner.filter(r => r.name.startsWith("i-fra") && r.loss != null && r.loss >= 100).map(r => ({ t: T(r.t) })),
    });
    drawGraph($("nh-g-inner-cpu"), {
        title: "CCR2004 CPU", unit: "%", yFloor0: true, sub: per,
        series: [{ name: "CCR2004", colour: "--nh-c2", values: c.inner_cpu.map(r => ({ t: T(r.t), v: r.cpu })) }],
        refs: [{ v: c.thresholds.cpu_pct, label: `${c.thresholds.cpu_pct}%` }],
    });
    drawGraph($("nh-g-inner-core"), {
        title: "CCR2004 → CCR2116 round trip", unit: " ms", yFloor0: true, sub: `${per} · through the CRS326`,
        series: insideSeries(inner, "rtt", innerNames.filter(n => n.startsWith("i-core"))),
    });
    drawGraph($("nh-g-inner-fra"), {
        title: "CCR2004 → Frankfurt round trip", unit: " ms", yFloor0: true, sub: `${per} · through the CRS326, CCR2116 and Airtel`,
        series: insideSeries(inner, "rtt", innerNames.filter(n => n.startsWith("i-fra"))),
    });
    const hopNames = [...new Set(d.hop.map(r => r.name))].sort(byBaseThenSize);
    const infoOnly = `${per} · info only: boxes answer pings at low priority`;
    drawGraph($("nh-g-hop-loss"), {
        title: "Pings to the boxes: packet loss", unit: "%", yFloor0: true,
        sub: `${infoOnly} (the OLT's 1300-byte loss follows the damaged DAC)`,
        series: insideSeries(d.hop, "loss", hopNames),
    });
    drawGraph($("nh-g-hop-rtt"), {
        title: "Pings to the boxes: round trip", unit: " ms", yFloor0: true, sub: infoOnly,
        series: insideSeries(d.hop, "rtt", hopNames),
    });
}

function drawInside(d) {
    state.inside = d;
    if (d.error) {
        $("nh-inside-sub").textContent = `couldn't load: ${d.error}`;
        return;
    }
    $("nh-inside-sub").textContent = "the CRS326 → OLT uplink, read by the CCR2116 every 5 min";
    $("nh-restarts-sub").textContent = `last ${d.lookback_days} days · power switchovers restart it`;
    $("nh-links-sub").textContent = `port up/down on the CRS326, CCR2116 and CCR2004, last ${d.lookback_days} days · the OLT uplink is the CRS326's ${d.olt_interface}, marked ◆`;
    drawInsideActive(d);
    drawCrcNow(d);
    drawSnmp(d);
    drawCrcGraph();
    drawRestarts(d);
    drawLinkEvents(d);
    drawAreas(d);
    drawLanlinks(d);
    drawInsideProbes();
}

// ---- Access network: PON ports, ONTs and PPPoE sessions ----

// Glyph + word, never colour alone. "good" shows the glyph only.
const LIGHT = { good: ["●", "", "nh-ok"], weak: ["▲", "weak", "nh-warn"], overloaded: ["▲", "overloaded", "nh-warn"],
                bad: ["◆", "bad", "nh-bad"] };
const LIGHT_BADGE = { good: "● Good", weak: "▲ Weak", overloaded: "▲ Overloaded", bad: "◆ Bad" };
const ROUTER_NAMES = { ccr2116: "CCR2116", ccr2004: "CCR2004" };

function lightCell(dbm, level) {
    if (dbm == null) return el("span", "nh-route-note", "—");
    const [glyph, word, cls] = LIGHT[level] || LIGHT.good;
    const wrap = el("span", `nh-light ${cls}`, `${glyph} ${dbm.toFixed(1)}`);
    if (word) wrap.append(el("span", "nh-light-word", word));
    return wrap;
}

function metres(m) {
    if (m == null) return "—";
    return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;
}

function moreNames(names, count = 4) {
    if (!names.length) return "";
    return names.slice(0, count).join(", ") + (names.length > count ? ` +${names.length - count}` : "");
}

// The 7-day light: what the ONT receives (solid) and what the OLT receives
// from it (dashed), on one fixed dBm scale so rows compare. The figures are
// in the row's cells and the title too, never only in the lines.
function lightSpark(spark, meta, t) {
    const width = 140, height = 30, pad = 2, top = -5, bottom = -35;
    const box = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, class: "nh-spark", role: "img" });
    const x = i => pad + i / Math.max(meta.points - 1, 1) * (width - 2 * pad);
    const y = v => pad + (top - Math.max(Math.min(v, top), bottom)) / (top - bottom) * (height - 2 * pad);
    box.append(svg("line", { x1: pad, x2: width - pad, y1: y(t.rx_weak_below), y2: y(t.rx_weak_below), class: "nh-ref" }));
    const parts = [];
    for (const [values, colour, dash, label] of [[spark.olt, "--nh-c5", "3 2", "OLT receives"], [spark.rx, "--nh-c1", null, "ONT receives"]]) {
        let d = "", prev = false;
        values.forEach((v, i) => {
            if (v == null) { prev = false; return; }
            d += `${prev ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
            prev = true;
        });
        const present = values.filter(v => v != null);
        if (!d) continue;
        box.append(svg("path", { d, fill: "none", stroke: `var(${colour})`, "stroke-width": 1.5, "stroke-linejoin": "round",
                                 ...(dash ? { "stroke-dasharray": dash } : {}) }));
        parts.push(`${label} ${Math.min(...present).toFixed(1)} to ${Math.max(...present).toFixed(1)} dBm`);
    }
    const label = parts.length ? `light over 7 days: ${parts.join("; ")}` : "no light readings in the last 7 days";
    box.setAttribute("aria-label", label);
    const title = svg("title");
    title.textContent = label;
    box.append(title);
    return box;
}

function drawActiveList(list, active, emptyText) {
    if (!active.length) {
        list.replaceChildren(el("li", "nh-none", emptyText));
        return;
    }
    list.replaceChildren(...active.map(rule => {
        const item = el("li");
        const [glyph, , cls] = STATUS[rule.severity] || STATUS.AMBER;
        item.append(el("span", `nh-rule-glyph ${cls}`, glyph), el("strong", null, ` ${rule.label}`),
                    el("span", "nh-rule-text", ` ${rule.text}. Since ${hm(rule.started_at)}`));
        if (rule.is_flapping) item.append(el("span", "nh-tag", "flapping"));
        return item;
    }));
}

function eventText(e) {
    const dev = ROUTER_NAMES[e.dev] || e.dev;
    switch (e.kind) {
        case "ont_down": {
            const group = { whole: "the whole PON went down", fibre: `${e.size} ONTs together (fibre/splitter?)`,
                            power: `${e.size} ONTs together (power cut?)`, few: `with ${e.size - 1} other` }[e.group];
            return ["▼ ONT down", "nh-bad", `${e.name} · PON ${e.pon} · ${e.text || "no reason given"}${group ? ` · ${group}` : ""}`];
        }
        case "ont_back":
            return ["● ONT back", "nh-ok", `${e.name} · PON ${e.pon} · ${e.first_time ? "online for the first time" : `after ${secondsText(e.outage_s)}`}`];
        case "ppp_dropped":
            return ["▼ PPPoE dropped", "nh-warn", `${e.site ? "◆ site " : ""}${e.name} on ${dev}`];
        case "ppp_back":
            return ["● PPPoE back", "nh-ok", `${e.site ? "◆ site " : ""}${e.name} on ${dev} · after ${secondsText(e.after_s)}`];
        case "link_down":
            return ["▼ Link down", "nh-bad", `${e.label} (${e.device_label} ${e.interface})`];
        case "link_up":
            return ["● Link up", "nh-ok", `${e.label} (${e.device_label} ${e.interface})${e.outage_s != null ? ` · after ${secondsText(e.outage_s)}` : ""}`];
        default:
            return [e.kind, "nh-unknown", e.name || ""];
    }
}

function drawPons(d) {
    const tbody = $("nh-pons");
    $("nh-pons-sub").textContent = "light = the worst ONT on the port · counts from the OLT every 5 min";
    if (!d.pons.length) return emptyRow(tbody, 6, "No PON readings yet.");
    tbody.replaceChildren(...d.pons.map(p => {
        const tr = el("tr");
        const name = el("span", "nh-route-name");
        name.append(el("strong", null, `PON ${p.pon}`), el("span", "nh-route-host", moreNames(p.names)));
        const light = p.health ? el("span", `nh-badge ${LIGHT[p.health][2]}`, LIGHT_BADGE[p.health]) : el("span", "nh-route-note", "no ONT online");
        const poor = [p.weak && `${p.weak} weak`, p.bad && `${p.bad} bad`, p.overloaded && `${p.overloaded} overloaded`].filter(Boolean);
        let last = "—";
        if (p.last_event) {
            const [word] = eventText({ ...p.last_event, ...p.last_event.detail, pon: p.pon });
            last = `${word}: ${p.last_event.name || ""} · ${when(p.last_event.at)}`;
        }
        tr.append(cell("PON", name), cell("Online", String(p.online ?? "—")),
                  cell("Offline", `${p.offline ?? "—"}${p.stale ? ` (${p.stale} stale)` : ""}`),
                  cell("Light", light), cell("Weak / bad", poor.length ? poor.join(" · ") : "none"), cell("Last event", last));
        return tr;
    }));
}

// The OLT's last down reason, short (the full text is the cell's title).
const DOWN_WHY = { light: "light loss", power: "power", reboot: "reboot" };

function pppCell(o) {
    const p = o.ppp;
    if (!p) return el("span", "nh-route-note", "no match");
    if (p.how === "manual_none") return el("span", "nh-route-note", "none (set by hand)");
    const wrap = el("span", "nh-route-name");
    let state;
    if (p.present == null) state = el("span", "nh-route-note", `${p.ppp} · not seen`);
    else if (p.present) state = el("span", "nh-light nh-ok", `● ${p.ppp}`);
    else state = el("span", "nh-light nh-warn", `▼ ${p.ppp} missing ${minutesText((Date.now() - new Date(p.missing_since)) / 60000)}`);
    wrap.append(state);
    if (p.how === "manual") wrap.append(el("span", "nh-route-host", "set by hand"));
    return wrap;
}

function ontRow(o) {
    const tr = el("tr", o.status === "online" ? null : "nh-offline");
    if (o.group != null) tr.classList.add("nh-in-group");
    const name = el("span", "nh-route-name");
    name.append(el("strong", null, o.name), el("span", "nh-route-host", `ONT ${o.ont_id}`));
    const why = el("span", null, DOWN_WHY[o.last_down_why] || o.last_down_text || "—");
    if (o.last_down_text) why.title = o.last_down_text;
    const status = el("span", "nh-route-name");
    status.append(o.status === "online" ? el("span", "nh-light nh-ok", "● Online")
        : el("span", "nh-light nh-bad", `▼ Offline ${minutesText((Date.now() - new Date(o.status_since)) / 60000)}`));
    for (const w of o.warnings) status.append(el("span", "nh-ont-warn", `▲ ${w.label}`));
    tr.append(cell("ONT", name), cell("Status", status), cell("ONT receives", lightCell(o.rx_dbm, o.rx_level)),
              cell("OLT receives", lightCell(o.olt_rx_dbm, o.olt_rx_level)), cell("Distance", metres(o.distance_m)),
              cell("Last down", why), cell("PPPoE", pppCell(o)),
              cell("Light, 7 days", lightSpark(o.spark, state.access.spark, state.access.thresholds)));
    return tr;
}

function drawOnts() {
    const d = state.access;
    const tbody = $("nh-onts");
    const pon = $("nh-ont-pon").value;
    const shown = d.onts.filter(o => !pon || o.pon === pon);
    if (!shown.length) return emptyRow(tbody, 8, d.onts.length ? "No ONTs on this PON." : "No ONT readings yet.");
    const rows = [];
    const byPon = new Map();
    for (const o of shown) byPon.set(o.pon, [...(byPon.get(o.pon) || []), o]);
    for (const [ponName, onts] of byPon) {
        d.groups.forEach((g, i) => {
            if (g.pon !== ponName) return;
            const members = onts.filter(o => o.group === i);
            if (!members.length) return;
            const why = [g.by_distance && g.min_m != null && `${metres(g.min_m)} to ${metres(g.max_m)} out`,
                         g.by_together && `went down together ${g.together}×`].filter(Boolean).join(" · ");
            const head = el("tr", "nh-group-row");
            const td = el("td", null, `Likely same splitter (PON ${ponName}): ${members.length} ONTs · ${why}`);
            td.colSpan = 8;
            head.append(td);
            rows.push(head, ...members.map(ontRow));
        });
        rows.push(...onts.filter(o => o.group == null).map(ontRow));
    }
    tbody.replaceChildren(...rows);
}

function drawEvents() {
    const d = state.access;
    const tbody = $("nh-events");
    const kind = $("nh-events-kind").value;
    const shown = d.events.filter(e => !kind || e.kind.startsWith(kind));
    if (!shown.length) return emptyRow(tbody, 3, "No events yet.");
    tbody.replaceChildren(...shown.map(e => {
        const tr = el("tr");
        const [word, cls, text] = eventText(e);
        tr.append(cell("Time", when(e.at)), cell("Event", el("span", `nh-light ${cls}`, word)), cell("Detail", text));
        return tr;
    }));
}

const JOB_TONES = { pon_light: "nh-bad", light_loss: "nh-bad", ont_bad: "nh-bad", one_way: "nh-warn", attenuator: "nh-warn" };

function drawJobs(d) {
    const list = $("nh-jobs");
    if (!d.jobs.length) return list.replaceChildren(el("li", "nh-none", "No field jobs: the light is fine everywhere."));
    list.replaceChildren(...d.jobs.map(job => {
        const item = el("li", JOB_TONES[job.kind]);
        item.append(el("strong", null, job.title), el("span", "nh-job-detail", job.detail));
        const facts = [];
        if (job.names) facts.push(moreNames(job.names, 8));
        if (job.rx_dbm != null || job.olt_rx_dbm != null) facts.push(`ONT receives ${num(job.rx_dbm)} dBm · OLT receives ${num(job.olt_rx_dbm)} dBm`);
        if (job.since) facts.push(`offline since ${when(job.since)}`);
        if (facts.length) item.append(el("span", "nh-job-names", facts.join(" · ")));
        return item;
    }));
}

function drawStale(d) {
    const tbody = $("nh-stale");
    if (!d.stale.length) return emptyRow(tbody, 3, "None.");
    tbody.replaceChildren(...d.stale.map(o => {
        const tr = el("tr");
        tr.append(cell("ONT", `${o.name} (ONT ${o.ont_id})`), cell("PON", o.pon), cell("First seen", when(o.first_seen_at)));
        return tr;
    }));
}

function drawPpp(d) {
    const tbody = $("nh-ppp");
    $("nh-ppp-sub").textContent = "every 5 min · a missing site (◆) texts after 5 min; 30 % of a router's sessions gone at once texts";
    if (!d.routers.length) return emptyRow(tbody, 4, "No PPPoE readings yet.");
    tbody.replaceChildren(...d.routers.map(r => {
        const tr = el("tr");
        const missing = el("span", "nh-route-name");
        if (!r.missing.length) missing.append(el("span", "nh-route-note", "none"));
        for (const m of r.missing.slice(0, 12)) {
            missing.append(el("span", m.site ? "nh-light nh-warn" : null, `${m.site ? "◆ " : ""}${m.name} · since ${when(m.since)}`));
        }
        if (r.missing.length > 12) missing.append(el("span", "nh-route-note", `+${r.missing.length - 12} more`));
        tr.append(cell("Router", r.label), cell("Sessions", `${r.count ?? "—"}${r.sites ? ` · ${r.sites} sites known` : ""}`),
                  cell("Missing now", missing), cell("Reading", r.at ? hm(r.at) : "—"));
        return tr;
    }));
}

function drawMatches(d) {
    const box = $("nh-matches-box");
    box.hidden = !can("network_health", "admin");
    if (box.hidden) return;
    const select = $("nh-match-ont");
    const chosen = select.value;
    select.replaceChildren(...d.onts.map(o => {
        const option = el("option", null, `${o.name} · ${o.key}`);
        option.value = o.key;
        return option;
    }));
    if (chosen) select.value = chosen;
    $("nh-ppp-names").replaceChildren(...d.ppp_names.map(n => { const o = el("option"); o.value = n; return o; }));
    const tbody = $("nh-matches");
    if (!d.matches.length) return emptyRow(tbody, 3, "None set: every match is worked out from the names.");
    tbody.replaceChildren(...d.matches.map(m => {
        const tr = el("tr");
        const remove = el("button", "nh-link-btn", "Remove");
        remove.type = "button";
        remove.dataset.key = m.key;
        tr.append(cell("ONT", `${m.name || "unknown ONT"} · ${m.key}`), cell("PPPoE session", m.ppp_name || "none"), cell("", remove));
        return tr;
    }));
}

function splitKey(key) {
    const at = key.lastIndexOf(":");
    return { pon: key.slice(0, at), ont_id: Number(key.slice(at + 1)) };
}

async function saveMatch(e) {
    e.preventDefault();
    const msg = $("nh-match-msg");
    const none = $("nh-match-none").checked;
    try {
        await api("/network/health/access/matches", {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...splitKey($("nh-match-ont").value), ppp_name: none ? null : $("nh-match-ppp").value.trim() || null,
                                   no_session: none }),
        });
        msg.textContent = "Saved.";
        $("nh-match-ppp").value = "";
        $("nh-match-none").checked = false;
        load();
    } catch (err) {
        msg.textContent = err.message;
    }
}

async function removeMatch(e) {
    const button = e.target.closest("button[data-key]");
    if (!button) return;
    const { pon, ont_id } = splitKey(button.dataset.key);
    try {
        await api(`/network/health/access/matches?pon=${encodeURIComponent(pon)}&ont_id=${ont_id}`, { method: "DELETE" });
        load();
    } catch (err) {
        $("nh-match-msg").textContent = err.message;
    }
}

function drawAccess(d) {
    state.access = d;
    if (d.error) {
        $("nh-access-sub").textContent = `couldn't load: ${d.error}`;
        return;
    }
    const t = d.thresholds;
    $("nh-access-sub").textContent = `${d.onts.length} ONTs on ${d.pons.length} PONs, read every 5 min · ONT receives: weak under ${t.rx_weak_below},`
        + ` bad under ${t.rx_bad_below}, overloaded over ${t.rx_overload_above} dBm · OLT receives: weak under ${t.olt_weak_below}, bad under ${t.olt_bad_below} dBm`;
    $("nh-onts-sub").textContent = `light sparkline: solid = ONT receives, dashed = OLT receives, line at ${t.rx_weak_below} dBm`
        + ` · "likely same splitter": within ${t.splitter_m} m, or down together ${t.splitter_together}× or more`;
    $("nh-events-sub").textContent = `ONTs, PPPoE sessions and port up/down, newest first · last ${d.lookback_days} days`;
    drawActiveList($("nh-access-active"), d.active, "No active problems in the access network");
    drawPons(d);
    const select = $("nh-ont-pon");
    const chosen = select.value;
    const all = el("option", null, "All PONs");
    all.value = "";
    select.replaceChildren(all, ...d.pons.map(p => { const o = el("option", null, `PON ${p.pon}`); o.value = p.pon; return o; }));
    select.value = d.pons.some(p => p.pon === chosen) ? chosen : "";
    drawOnts();
    drawEvents();
    drawJobs(d);
    drawStale(d);
    drawPpp(d);
    drawMatches(d);
}

// ---- Diagnose: a chat with Claude, in the corner where "Ask" usually is ----
// On this page the shared Ask button makes way for this one (network-health.css).
// Same look as Ask (chat.css classes), but it talks only to
// /network/health/diagnose. The conversation is kept in sessionStorage like
// Ask's. Claude's answers go on the page with textContent only.

const DX_MAX_TURNS = 20;    // matches DIAGNOSE_MAX_TURNS
const DX_MAX_CHARS = 4000;  // matches DIAGNOSE_QUESTION_MAX
const DX_STARTERS = {
    quick: "Quick check: is anything wrong right now, and what should I check first?",
    deep: "Detailed analysis: go through every layer and find what's wrong.",
};
const dx = { els: null, info: null, sending: false, convo: null };

const dxKey = () => `nh-diagnose-${getCurrentUser()?.id ?? "none"}`;

function newConversationId() {
    if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function newConvo(previous) {
    return { id: newConversationId(), model: previous?.model || "sonnet", effort: previous?.effort || "medium", turns: [] };
}

function dxLoad() {
    try {
        const saved = JSON.parse(sessionStorage.getItem(dxKey()) || "null");
        if (saved && typeof saved.id === "string" && Array.isArray(saved.turns)) {
            saved.turns = saved.turns.filter(t => (t.role === "user" || t.role === "assistant") && typeof t.content === "string");
            dx.convo = saved;
            return;
        }
    } catch { /* private mode or junk: start fresh */ }
    dx.convo = newConvo();
}

function dxSave() {
    try { sessionStorage.setItem(dxKey(), JSON.stringify(dx.convo)); } catch { /* memory only */ }
}

// Claude's answer is plain text: headings end with ":", bullets start "- ".
// Built with textContent only, so nothing in it can become markup.
function answerNodes(text) {
    const out = [];
    let list = null;
    for (const raw of text.split("\n")) {
        const line = raw.trim();
        if (!line) { list = null; continue; }
        if (/^[-•*] /.test(line)) {
            if (!list) { list = el("ul"); out.push(list); }
            list.append(el("li", null, line.slice(2)));
            continue;
        }
        list = null;
        if (line.endsWith(":") && line.length <= 60) {
            out.push(el("div", "nh-ask-h", line));
            continue;
        }
        // "Verdict: ..." on one line: the label in bold.
        const labelled = line.match(/^([A-Z][A-Za-z ]{0,24}):\s+(.+)$/);
        if (labelled) {
            const p = el("p");
            p.append(el("strong", null, `${labelled[1]}:`), ` ${labelled[2]}`);
            out.push(p);
            continue;
        }
        out.push(el("p", null, line));
    }
    return out;
}

function segment(label, options) {
    const group = el("div", "nh-seg");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", label);
    for (const [value, text] of options) {
        const b = el("button", null, text);
        b.type = "button";
        b.dataset.value = value;
        group.append(b);
    }
    return group;
}

function buildDx() {
    const launcher = el("button", "chat-launcher nh-ask-launcher");
    launcher.type = "button";
    launcher.setAttribute("aria-label", "Open Diagnose: ask Claude about the network");
    const icon = svg("svg", { width: 20, height: 20, viewBox: "0 0 24 24", fill: "none", "aria-hidden": "true" });
    icon.append(svg("path", { d: "M3 12h4l3-7 4 14 3-7h4", stroke: "currentColor", "stroke-width": 1.8,
                              "stroke-linecap": "round", "stroke-linejoin": "round" }));
    launcher.append(icon, el("span", null, "Diagnose"));

    const panel = el("section", "chat-panel nh-ask-panel");
    panel.hidden = true;
    panel.setAttribute("aria-label", "Diagnose");
    const header = el("header", "chat-header");
    const titles = el("div");
    titles.append(el("div", "chat-title", "Diagnose"),
                  el("div", "chat-subtitle", "Claude reads this page's data. Network health questions only."));
    const fresh = el("button", "chat-header-btn", "New");
    fresh.type = "button";
    fresh.title = "Start a new chat";
    const close = el("button", "chat-header-btn", "✕");
    close.type = "button";
    close.setAttribute("aria-label", "Close Diagnose");
    header.append(titles, fresh, close);

    const controls = el("div", "nh-ask-controls");
    const model = segment("Model", [["sonnet", "Sonnet 5.5"], ["opus", "Opus 5.5"]]);
    const effort = segment("Effort", [["medium", "Medium"], ["high", "High"]]);
    const cost = el("span", "nh-ask-cost");
    controls.append(model, effort, cost);

    const feed = el("div", "chat-feed");
    feed.setAttribute("role", "log");
    feed.setAttribute("aria-live", "polite");
    const form = el("form", "chat-form");
    const input = el("textarea", "chat-input");
    input.rows = 1;
    input.maxLength = DX_MAX_CHARS;
    input.placeholder = "Ask about the network…";
    input.setAttribute("aria-label", "Your question");
    const send = el("button", "chat-send", "Send");
    send.type = "submit";
    form.append(input, send);
    panel.append(header, controls, feed, form);
    document.body.append(launcher, panel);

    launcher.addEventListener("click", () => openDx(true));
    close.addEventListener("click", () => openDx(false));
    fresh.addEventListener("click", () => {
        if (dx.sending) return;
        dx.convo = newConvo(dx.convo);
        dxSave();
        renderDx();
        input.focus();
    });
    model.addEventListener("click", e => {
        const b = e.target.closest("button[data-value]");
        if (!b) return;
        dx.convo.model = b.dataset.value;
        dxSave();
        drawDxControls();
    });
    effort.addEventListener("click", e => {
        const b = e.target.closest("button[data-value]");
        if (!b) return;
        dx.convo.effort = b.dataset.value;
        dxSave();
        drawDxControls();
    });
    form.addEventListener("submit", e => { e.preventDefault(); submitDx(); });
    input.addEventListener("keydown", e => {
        // Enter sends; Shift+Enter is a new line.
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitDx(); }
    });
    input.addEventListener("input", () => {
        input.style.height = "auto";
        input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
    });
    return { launcher, panel, model, effort, cost, feed, input, send };
}

function openDx(isOpen) {
    dx.els.panel.hidden = !isOpen;
    dx.els.launcher.hidden = isOpen;
    if (isOpen) {
        renderDx();
        dx.els.input.focus();
        loadDxInfo().catch(() => {});
    }
}

async function loadDxInfo() {
    dx.info = await api("/network/health/diagnose");
    if (!dx.info.can_opus && dx.convo.model === "opus") { dx.convo.model = "sonnet"; dxSave(); }
    drawDxControls();
    if (!dx.els.panel.hidden && !dx.convo.turns.length) renderDx();
}

function usd(v) {
    return v < 0.01 ? "under US$0.01" : `US$${v.toFixed(2)}`;
}

function drawDxControls() {
    const { model, effort, cost, input, send } = dx.els;
    model.hidden = !dx.info?.can_opus;
    model.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.value === dx.convo.model)));
    effort.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.value === dx.convo.effort)));
    const spent = dx.convo.turns.reduce((sum, t) => sum + (t.meta?.cost_usd || 0), 0);
    cost.textContent = spent ? `This chat: ${usd(spent)}` : "";
    const off = dx.sending || dx.info?.available === false;
    input.disabled = send.disabled = off;
}

function metaLine(meta) {
    const bits = [meta.model_label, `${meta.effort} effort`];
    if (meta.cost_usd != null) bits.push(usd(meta.cost_usd));
    if (meta.truncated) bits.push("cut short: hit the length limit");
    return bits.join(" · ");
}

function dxBubble(turn) {
    if (turn.role === "user") return el("div", "chat-bubble chat-user", turn.content);
    const div = el("div", "chat-bubble chat-assistant nh-ask-answer");
    div.append(...answerNodes(turn.content));
    if (turn.meta) div.append(el("div", "chat-checked", metaLine(turn.meta)));
    return div;
}

function renderDx() {
    const { feed } = dx.els;
    feed.replaceChildren();
    if (!dx.convo.turns.length) {
        const hint = el("div", "chat-bubble chat-hint");
        if (dx.info && !dx.info.available) {
            hint.textContent = "ANTHROPIC_API_KEY isn't set on the server, so Claude can't be asked.";
        } else {
            hint.append(el("p", null, "Ask about the network: what's wrong right now, why an area is slow, "
                + "what happened overnight. Claude reads a summary of this page's data."));
            const starters = el("div", "nh-ask-starters");
            const quick = el("button", "chat-header-btn", "Quick check");
            quick.type = "button";
            quick.addEventListener("click", () => askDx(DX_STARTERS.quick));
            starters.append(quick);
            if (dx.info?.can_opus) {
                const deep = el("button", "chat-header-btn", "Detailed analysis (Opus, high)");
                deep.type = "button";
                deep.addEventListener("click", () => {
                    dx.convo.model = "opus";
                    dx.convo.effort = "high";
                    askDx(DX_STARTERS.deep);
                });
                starters.append(deep);
            }
            hint.append(starters);
        }
        feed.append(hint);
    }
    dx.convo.turns.forEach(t => feed.append(dxBubble(t)));
    drawDxControls();
    feed.scrollTop = feed.scrollHeight;
}

function submitDx() {
    const text = dx.els.input.value.trim();
    if (!text || dx.sending) return;
    dx.els.input.value = "";
    dx.els.input.style.height = "auto";
    askDx(text);
}

// Oldest turns go first; what's sent must still start with a question.
function dxMessages() {
    let turns = dx.convo.turns.slice(-DX_MAX_TURNS);
    while (turns.length && turns[0].role !== "user") turns = turns.slice(1);
    return turns.map(t => ({ role: t.role, content: t.content }));
}

async function askDx(question) {
    if (dx.sending) return;
    if (question != null) {
        // A question that failed and wasn't retried is replaced, so turns still alternate.
        if (dx.convo.turns.at(-1)?.role === "user") dx.convo.turns.pop();
        dx.convo.turns.push({ role: "user", content: question });
        dxSave();
        renderDx();
    }
    dx.sending = true;
    drawDxControls();
    const { feed } = dx.els;
    feed.querySelectorAll(".chat-error").forEach(e => e.remove());
    const typing = el("div", "chat-bubble chat-assistant chat-typing");
    typing.setAttribute("aria-label", "Claude is thinking");
    typing.append(el("span"), el("span"), el("span"));
    feed.append(typing);
    feed.scrollTop = feed.scrollHeight;
    try {
        const a = await api("/network/health/diagnose/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: dx.convo.id, model: dx.convo.model, effort: dx.convo.effort,
                                   messages: dxMessages() }),
        });
        dx.convo.turns.push({ role: "assistant", content: a.text,
                              meta: { model_label: a.model_label, effort: a.effort, cost_usd: a.cost_usd, truncated: a.truncated } });
        dxSave();
        typing.remove();
        feed.append(dxBubble(dx.convo.turns.at(-1)));
    } catch (err) {
        typing.remove();
        const box = el("div", "chat-bubble chat-error",
                       err.message === "Failed to fetch" ? "Couldn't reach the server. Check your connection." : err.message);
        const retry = el("button", "chat-retry", "Retry");
        retry.type = "button";
        retry.addEventListener("click", () => { box.remove(); askDx(); });
        box.append(retry);
        feed.append(box);
    } finally {
        typing.remove();
        dx.sending = false;
        drawDxControls();
        feed.scrollTop = feed.scrollHeight;
        dx.els.input.focus();
    }
}

// ---- Settings (Network Health > Admin) ----

function toLocalInput(iso) {
    if (!iso) return "";
    return new Date(new Date(iso).getTime() + 3 * 3600000).toISOString().slice(0, 16);
}

async function loadSettings() {
    const admin = $("nh-admin");
    admin.hidden = !can("network_health", "admin");
    if (admin.hidden) return;
    const data = await api("/network/health/settings");
    $("nh-capacity").value = data.settings.uplink_capacity_mbps;
    $("nh-switched").value = toLocalInput(data.settings.provider_switched_at);
    const env = $("nh-env");
    env.replaceChildren(
        el("div", null, `SMS: ${data.sms.mode === "live" ? "sending" : "log-only"} (NH_SMS_MODE) · daily cap ${data.sms.cap} (NH_SMS_DAILY_CAP) · ${data.sms.recipients} number${data.sms.recipients === 1 ? "" : "s"} in NH_ALERT_PHONES`),
        el("div", null, `Shipper token (NETWORK_HEALTH_TOKEN): ${data.token_is_set ? "set" : "NOT SET, every post is refused"} · Status key (NH_STATUS_KEY): ${data.status_key_is_set ? "set" : "NOT SET, /network/health/status refuses every call"}`),
        el("div", "nh-note", "These are Render environment variables; change them there. Phone numbers are never shown or stored."),
    );
    $("nh-thresholds").replaceChildren(...data.thresholds.map(t => {
        const tr = el("tr");
        tr.append(cell("Name", t.name), cell("Value", Array.isArray(t.value) ? t.value.join(", ") : String(t.value)));
        return tr;
    }));
}

async function saveSettings(e) {
    e.preventDefault();
    const msg = $("nh-settings-msg");
    const switched = $("nh-switched").value;
    try {
        await api("/network/health/settings", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ uplink_capacity_mbps: Number($("nh-capacity").value),
                                   provider_switched_at: switched ? `${switched}:00+03:00` : null }),
        });
        msg.textContent = "Saved.";
        load();
    } catch (err) {
        msg.textContent = err.message;
    }
}

// ---- Loading ----

async function load() {
    if ($("view-network-health").hidden) return;
    try {
        const [overview, charts, incidents, routes, inside, accessData] = await Promise.all([
            api("/network/health/overview"),
            api(`/network/health/charts?range=${state.range}`),
            api("/network/health/incidents?limit=100"),
            api("/network/health/routes"),
            // Its own failure doesn't take the rest of the page down.
            api(`/network/health/inside?range=${state.range}`).catch(err => ({ error: err.message })),
            api("/network/health/access").catch(err => ({ error: err.message })),
        ]);
        state.overview = overview;
        state.charts = charts;
        drawBanner(overview);
        drawDevices(overview);
        drawGraphs();
        drawInside(inside);
        drawAccess(accessData);
        drawRoutes(routes);
        drawIncidents(incidents);
        drawSms(overview.sms);
        $("nh-updated").textContent = `Updated ${hm(overview.generated_at)}`;
    } catch (err) {
        $("nh-banner").className = "nh-banner nh-unknown";
        $("nh-banner").textContent = `Couldn't load Network Health: ${err.message}`;
    }
}

export function initNetworkHealth() {
    document.querySelector(".nh-range").addEventListener("click", e => {
        const tab = e.target.closest("[data-range]");
        if (!tab || tab.dataset.range === state.range) return;
        state.range = tab.dataset.range;
        document.querySelectorAll(".nh-range [data-range]").forEach(b => b.setAttribute("aria-selected", String(b === tab)));
        load();
    });
    $("nh-settings-form").addEventListener("submit", saveSettings);
    $("nh-ont-pon").addEventListener("change", () => state.access && !state.access.error && drawOnts());
    $("nh-events-kind").addEventListener("change", () => state.access && !state.access.error && drawEvents());
    $("nh-match-form").addEventListener("submit", saveMatch);
    $("nh-matches").addEventListener("click", removeMatch);
    dx.els = buildDx();
    registerLogoutHandler(() => {
        try { sessionStorage.removeItem(dxKey()); } catch { /* nothing stored */ }
        dx.convo = null;
        dx.info = null;
        dx.els.panel.hidden = true;
        dx.els.launcher.hidden = false;
    });
    const observer = new ResizeObserver(() => {
        clearTimeout(state.resizeTimer);
        state.resizeTimer = setTimeout(() => { drawGraphs(); drawCrcGraph(); drawInsideProbes(); }, 150);
    });
    observer.observe($("nh-graphs"));
    observer.observe($("nh-inside-probes"));
    observer.observe($("nh-g-crc"));

    registerRoute("network-health", () => {
        showView("view-network-health");
        load();
        loadSettings().catch(err => { $("nh-settings-msg").textContent = err.message; });
        if (!dx.convo || dx.userKey !== dxKey()) { dxLoad(); dx.userKey = dxKey(); }
        loadDxInfo().catch(() => {});
        if (!state.timer) state.timer = setInterval(load, REFRESH_MS);
    });
}
