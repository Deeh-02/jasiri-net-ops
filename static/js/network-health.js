import { authHeaders, showView, registerRoute, registerRouteResetter, navigate, can, getCurrentUser, registerLogoutHandler } from "./common.js";

// Network Health (#/network-health): is the uplink fast and clean right now,
// and what happened lately. Reads only /network/health/* (its own domain) and
// imports only common.js. Every value goes onto the page as text; the graphs
// are small SVGs drawn here so this page doesn't depend on another page's code.

const REFRESH_MS = 60000;
const tz = { timeZone: "Africa/Nairobi" };
const SVG = "http://www.w3.org/2000/svg";

const state = { tab: "uplink", range: "24h", timer: null, overview: null, charts: null, inside: null, access: null, box: null, resizeTimer: null, openRoutes: new Set() };
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
    BOX_BIG_LOSS: "Customer path losing big packets (CRS-OLT link?)", BOX_SLOW: "Customer line slow",
};
const DEVICE_LABELS = { ccr2116: "CCR2116", ccr2004: "CCR2004", feed: "Frankfurt feed", olt: "OLT", crs326: "CRS326", box: "Customer line" };
const PEAK_UNITS = { UPLINK_CONGESTED: "%", LATENCY_HIGH: " ms", LOSS: "%", JITTER_HIGH: " ms", CPU_HIGH: "%",
                     OPTIC_DEGRADING: " dBm", SPEED_LOW: " Mbps", ROUTER_SILENT: " min", FEED_SILENT: " min",
                     OLT_CRC: "%", OLT_SNMP_SILENT: " min", INNER_PATH_LOSS: "%", CUSTOMER_PATH_LOSS: "%",
                     AREA_SILENT: " Mbps", PON_DOWN: " ONTs", PON_FIBRE_FAULT: " ONTs", PON_POWER_CUT: " ONTs",
                     PON_LIGHT_DEGRADING: " dB", ONT_LIGHT_DEGRADING: " dB", SITE_PPP_DOWN: " min", PPP_MASS_DROP: " sessions",
                     LINE_NO_INTERNET: " min", BOX_BIG_LOSS: "%", BOX_SLOW: " Mbps" };
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

const rangeHours = () => (state.range === "24h" ? 24 : 168);

function timeTicks(start, end, hours) {
    const hourMs = 3600000;
    const step = hours <= 24 ? 4 * hourMs : 24 * hourMs;
    // Ticks fall on Nairobi hours / midnights (UTC+3, no DST).
    const offset = 3 * hourMs;
    const ticks = [];
    for (let t = Math.ceil((start + offset) / step) * step - offset; t <= end; t += step) ticks.push(t);
    return ticks;
}

function tickLabel(t, hours) {
    const d = new Date(t);
    return hours <= 24 ? d.toLocaleTimeString("en-GB", { ...tz, hour: "2-digit", minute: "2-digit" })
        : d.toLocaleDateString("en-GB", { ...tz, weekday: "short", day: "numeric" });
}

/* One graph. spec: {title, sub, unit, series: [{name, colour, dash, dots, values: [{t, v, note}]}],
   refs: [{v, label}], marks: [{t, label}] (red ticks on the baseline: outages), yFloor0,
   fmt (optional: the y-axis number format, default num),
   hours (the window; default the page's range), bucketSeconds (the points' spacing; default the charts'),
   yMax (the y-axis reaches at least this: panels that must compare),
   events: [{t, label}] (dashed vertical lines with their label: the owner's markers);
   a point may carry `raw` (the reading behind a rolling average: a faint dot)}. */
function drawGraph(fig, spec) {
    fig.replaceChildren();
    const head = el("figcaption", "nh-graph-head");
    head.append(el("span", "nh-graph-title", spec.title));
    if (spec.sub) head.append(el("span", "nh-graph-sub", spec.sub));
    fig.append(head);
    // A point may carry `raw`: the reading behind a rolling average, drawn as a faint dot.
    const values = spec.series.flatMap(s => s.values.flatMap(p => [p.v, p.raw]).filter(v => v != null));
    if (!values.length) {
        fig.append(el("div", "nh-graph-empty", "No readings in this range yet."));
        return;
    }
    const width = Math.max(fig.clientWidth - 2, 260), height = 168;
    // Reference lines are labelled in a right margin, clear of the data.
    const pad = { l: 44, r: spec.refs?.length ? 64 : 10, t: 8, b: 22 };
    const hours = spec.hours ?? rangeHours();
    const bucketSeconds = spec.bucketSeconds ?? state.charts?.bucket_seconds ?? 300;
    const end = Date.now(), start = end - hours * 3600000;
    const refValues = (spec.refs || []).map(r => r.v);
    let lo = Math.min(...values, ...refValues), hi = Math.max(...values, ...refValues, spec.yMax ?? -Infinity);
    if (spec.yFloor0) lo = Math.min(0, lo);
    if (hi === lo) hi = lo + 1;
    const margin = (hi - lo) * 0.08;
    hi += margin;
    if (!spec.yFloor0) lo -= margin;
    const x = t => pad.l + (t - start) / (end - start) * (width - pad.l - pad.r);
    const y = v => pad.t + (hi - v) / (hi - lo) * (height - pad.t - pad.b);

    const root = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
                              "aria-label": `${spec.title} over the last ${hours <= 24 ? `${hours} hours` : `${hours / 24} days`}` });
    for (const v of niceTicks(lo, hi)) {
        root.append(svg("line", { x1: pad.l, x2: width - pad.r, y1: y(v), y2: y(v), class: "nh-grid" }));
        const label = svg("text", { x: pad.l - 6, y: y(v) + 3, class: "nh-axis", "text-anchor": "end" });
        label.textContent = (spec.fmt || num)(v);
        root.append(label);
    }
    // Every other time label when they'd sit closer than 56 px (phones).
    let times = timeTicks(start, end, hours);
    while (times.length > 1 && (width - pad.l - pad.r) / times.length < 56) times = times.filter((_, i) => i % 2 === 0);
    for (const t of times) {
        const label = svg("text", { x: x(t), y: height - 6, class: "nh-axis", "text-anchor": "middle" });
        label.textContent = tickLabel(t, hours);
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
    for (const event of (spec.events || []).filter(e => e.t >= start && e.t <= end)) {
        const ex = x(event.t);
        const line = svg("line", { x1: ex, x2: ex, y1: pad.t, y2: height - pad.b, class: "nh-event" });
        const tip = svg("title");
        tip.textContent = `${when(new Date(event.t).toISOString())}: ${event.label}`;
        line.append(tip);
        root.append(line);
        const text = svg("text", { x: ex + 4, y: pad.t + 2, class: "nh-axis nh-event-label", transform: `rotate(90 ${ex + 4} ${pad.t + 2})` });
        text.textContent = event.label.length > 26 ? `${event.label.slice(0, 25)}…` : event.label;
        root.append(text);
    }
    const gapMs = bucketSeconds * 2500;
    for (const s of spec.series) {
        const colour = `var(${s.colour})`;
        if (s.dots) {
            for (const p of s.values.filter(p => p.v != null)) {
                root.append(svg("circle", { cx: x(p.t), cy: y(p.v), r: 4.5, fill: s.hollow ? "var(--surface)" : colour,
                                            stroke: colour, "stroke-width": 2, class: "nh-dot" }));
            }
            continue;
        }
        const dots = s.values.filter(p => p.raw != null).map(p => `M${x(p.t).toFixed(1)},${y(p.raw).toFixed(1)}h0`).join("");
        if (dots) root.append(svg("path", { d: dots, fill: "none", stroke: colour, "stroke-width": 3.2, "stroke-linecap": "round", opacity: 0.28, class: "nh-raw" }));
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
    addHover(box, root, spec, { x, start, end, width, height, pad, bucketSeconds });
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
        const reach = spec.series.some(s => s.dots) ? 6 * 3600000 : g.bucketSeconds * 1500;
        const rows = [];
        let at = null;
        for (const s of spec.series) {
            const p = nearest(s.values, t);
            if (!p || Math.abs(p.t - t) > reach) continue;
            at = at ?? p.t;
            const reading = p.raw != null && p.v != null && p.raw !== p.v ? ` (reading ${num(p.raw)}${spec.unit})` : "";
            rows.push(`${s.name}: ${p.note || (p.v == null ? "no reply" : `${num(p.v)}${spec.unit}${reading}`)}`);
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

// ---- Inside links: port counters on the CCR2116, CCR2004 and CRS326 ----

// Counts: in full under 10,000, then short (118.2K, 207.7M); the full number is on hover.
const COMPACT = new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 });
const fmtCount = v => (v == null ? "—" : Math.abs(v) < 10000 ? Math.round(v).toLocaleString("en-GB") : COMPACT.format(v));
const fullCount = v => (v == null ? "" : Math.round(v).toLocaleString("en-GB"));
const fmtShare = v => (v == null ? null : `${v >= 10 ? Math.round(v) : +v.toFixed(2)} %`);
const DAMAGE_LEVEL = {
    red: ["◆", "Damaged now", "nh-bad"],
    amber: ["▲", "In last 24 h", "nh-warn"],
    green: ["●", "None", "nh-ok"],
};
const nowrap = (text, title) => {
    const span = el("span", "nh-nowrap", text);
    if (title) span.title = title;
    return span;
};

// Damage one way: the errors the FAR end received. Red if they went up in the
// last 5 min (or over 1 % of the frames), amber if any in 24 h. Lifetime is small grey.
function damageCell(dir, cable) {
    const wrap = el("div", "nh-damage");
    wrap.append(el("span", "nh-damage-dir", `${dir.from} → ${dir.to}`));
    if (!dir.received) {
        wrap.append(el("span", "nh-route-note", `not monitored (${dir.to} end isn't read)`));
        return wrap;
    }
    const r = dir.received;
    const [glyph, word, cls] = DAMAGE_LEVEL[dir.level] || ["○", "No reading", "nh-unknown"];
    const head = el("div", "nh-damage-head");
    head.append(el("span", `nh-badge ${cls}`, `${glyph} ${word}`));
    const share = fmtShare(r.last_pct ?? r.day_pct);
    if (share) head.append(el("strong", "nh-damage-pct", `${share} damaged`));
    wrap.append(head);
    const counts = el("span", `nh-damage-now${r.last ? " is-rising" : ""}`);
    counts.append(nowrap(`+${fmtCount(r.last)} in 5 min`, fullCount(r.last)), " · ",
                  nowrap(`+${fmtCount(r.day)} in 24 h`, fullCount(r.day)));
    wrap.append(counts);
    const end = cable.ends.find(e => e.label === dir.to);
    const grey = [dir.to === "OLT" ? "the OLT's CRC counter" : `counted at ${dir.to}`, `lifetime ${fmtCount(r.total)}`];
    if (end?.stale) grey.push("reading is old");
    const note = el("span", "nh-damage-note", grey.join(" · "));
    note.title = `lifetime ${fullCount(r.total)}`;
    wrap.append(note);
    return wrap;
}

// Each end's link-downs and TX drops: +24 h, lifetime in small grey.
function endsCell(cable) {
    const grid = el("div", "nh-ends");
    grid.append(el("span"), el("span", "nh-ends-head", "Link-downs"), el("span", "nh-ends-head", "TX drops"));
    for (const end of cable.ends) {
        grid.append(el("span", "nh-end-name", end.label));
        if (!end.reported || !end.link_downs) {
            const why = !end.reported && end.port === null ? "not monitored" : "not reported";
            const note = el("span", "nh-route-note nh-ends-span", why);
            grid.append(note);
            continue;
        }
        for (const counter of [end.link_downs, end.tx_drop]) {
            const value = el("span", "nh-end-value");
            value.append(el("span", counter.day ? "is-rising" : null, `+${fmtCount(counter.day)}`),
                         el("small", null, ` (${fmtCount(counter.total)})`));
            value.title = `+${fullCount(counter.day)} in 24 h · lifetime ${fullCount(counter.total)}`;
            grid.append(value);
        }
    }
    return grid;
}

function drawLanlinks(d) {
    const tbody = $("nh-lanlinks");
    $("nh-lanlinks-sub").textContent = "One row per cable. A port only counts errors on frames it receives, "
        + "so damage on the way A → B shows at B. Red = errors rose in the last 5 min (or over 1 % of frames), amber = some in 24 h. "
        + `Alerts: a link down, or over ${d.thresholds.lanlink_damage_over} damaged frames in 5 min.`;
    if (!d.cables?.length) return emptyRow(tbody, 4, "No inside link readings yet.");
    tbody.replaceChildren(...d.cables.map(cable => {
        const tr = el("tr", cable.ends.some(e => e.label === "OLT") ? "nh-olt-row" : null);
        const name = el("span", "nh-route-name nh-cable-name");
        name.append(el("strong", null, cable.name),
                    ...cable.ends.map(e => el("span", "nh-route-host", e.port ? `${e.label} ${e.port}` : e.label)));
        tr.append(cell("Cable", name), cell("Damage A → B", damageCell(cable.a_to_b, cable)),
                  cell("Damage B → A", damageCell(cable.b_to_a, cable)), cell("Each end, 24 h", endsCell(cable)));
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
    $("nh-inside-sub").textContent = "The OLT and the links between the boxes · the CRS326 → OLT uplink is read by the CCR2116 every 5 min";
    $("nh-restarts-sub").textContent = `last ${d.lookback_days} days · power switchovers restart it`;
    $("nh-links-sub").textContent = `port up/down on the CRS326, CCR2116 and CCR2004, last ${d.lookback_days} days · the OLT uplink is the CRS326's ${d.olt_interface}, marked ◆`;
    drawInsideActive(d);
    drawCrcNow(d);
    drawSnmp(d);
    drawCrcGraph();
    drawRestarts(d);
    drawLinkEvents(d);
    drawLanlinks(d);
    drawInsideProbes();
    fillGuide();
}

// ---- Customer line (probe): a probe machine on a real customer line ----
// `box` (pings every 5 min, each with a 1400-byte "-big" twin) and `boxspeed`
// (6-second downloads every 30 min). Always the last 7 days.

const BOX_HOURS = 168;
const BOX_TARGET_NAMES = { fra: "Frankfurt", gw: "the first router past the OLT (gw, CCR2004)", core: "the CCR2116 (core)", airtel: "the Airtel gateway" };
const BOX_SIZES = [["", "Small packets", "--nh-c1"], ["-big", "Big packets (1400 bytes)", "--nh-c2"]];
const pctOrDash = (v, digits = 0) => (v == null ? "—" : `${num(v, digits)} %`);

function drawBox(d) {
    state.box = d;
    const lead = $("nh-box-lead"), box = $("nh-box");
    if (d.error) {
        lead.textContent = `couldn't load: ${d.error}`;
        box.hidden = true;
        return;
    }
    $("nh-box-sub").textContent = d.label ? `${d.label}` : "";
    box.hidden = !d.dev;
    lead.textContent = d.dev
        ? `A probe machine on a real customer line (${d.label}), last ${d.days} days, Nairobi time · Frankfurt is the main measure of loss; the lines are ${d.window_hours}-hour rolling averages, the faint dots the 5-minute readings`
        : "No readings yet. They start once the shipper sends the probe machine's box and boxspeed records.";
    if (d.dev) {
        drawBoxTiles(d);
        drawBoxGraphs();
    }
    drawBoxMarkers(d);
}

function drawBoxTiles(d) {
    const t = d.thresholds;
    // The headline: Frankfurt's big packets over the last hour.
    const lossBox = $("nh-box-loss-now");
    const h = d.headline;
    if (!h || h.fra_big == null) {
        lossBox.replaceChildren(el("div", "nh-tile-label", "Big-packet loss to Frankfurt, last hour"),
            el("p", "nh-tile-line", "No Frankfurt readings in the last hour. One every 5 min."));
    } else {
        const head = el("div", "nh-tile-head");
        head.append(el("span", "nh-tile-value", pctOrDash(h.fra_big, 1)));
        head.append(h.fra_big >= t.fra_big_pct ? el("span", "nh-badge nh-warn", "▲ Losing big packets") : el("span", "nh-badge nh-ok", "● Clean"));
        const hops = d.hops.map(hop => `${hop} ${pctOrDash(h.hops_big[hop])}`).join(" · ");
        const old = d.latest && (Date.now() - new Date(d.latest.at)) / 60000 > 20;
        lossBox.replaceChildren(
            el("div", "nh-tile-label", "Big-packet loss to Frankfurt, last hour"), head,
            el("p", "nh-tile-line", `Small packets to Frankfurt ${pctOrDash(h.fra, 1)} · router hops, big packets: ${hops}`),
            el("p", "nh-tile-sub", `${h.readings} reading${h.readings === 1 ? "" : "s"}${d.latest ? ` · latest ${hm(d.latest.at)}${old ? " (old)" : ""}` : ""}`
                + ` · alert: ${t.window_minutes}-minute average of Frankfurt's big packets ${t.fra_big_pct} % or more, or the first router's ${t.gw_big_pct} %; clears under half of those`));
    }
    const speed = d.speed_now;
    const speedBox = $("nh-box-speed-now");
    if (!speed) {
        speedBox.replaceChildren(el("div", "nh-tile-label", "Speed now"), el("p", "nh-tile-line", "No speed runs yet. One every 30 min."));
        return;
    }
    const head = el("div", "nh-tile-head");
    head.append(el("span", "nh-tile-value nh-tile-value-sm", speed.pct_of_plan == null ? "—" : `${num(speed.pct_of_plan, 0)} % of plan`));
    if (speed.pct_of_plan != null) {
        head.append(speed.pct_of_plan < t.slow_pct ? el("span", "nh-badge nh-warn", "▲ Slow") : el("span", "nh-badge nh-ok", "● OK"));
    }
    const old = (Date.now() - new Date(speed.at)) / 60000 > 90;
    speedBox.replaceChildren(
        el("div", "nh-tile-label", "Speed now (6-second downloads)"), head,
        el("p", "nh-tile-line", `Cloudflare (South Africa) ${speed.cf_mbps == null ? "—" : `${num(speed.cf_mbps, 1)} Mbps`}`
            + ` · Frankfurt ${speed.fra_mbps == null ? "—" : `${num(speed.fra_mbps, 2)} Mbps`}`
            + ` · plan ${speed.plan_mbps == null ? "—" : `${num(speed.plan_mbps, 0)} Mbps`}`),
        el("p", "nh-tile-sub", `run at ${hm(speed.at)}${old ? " (old)" : ""} · alert: Cloudflare under ${t.slow_pct} % of plan, ${t.slow_in_a_row} runs in a row`));
}

function drawBoxGraphs() {
    const d = state.box;
    if (!d || d.error || !d.dev) return;
    const events = d.markers.map(m => ({ t: T(m.at), label: m.label }));
    const common = { hours: BOX_HOURS, bucketSeconds: d.bucket_seconds, events };
    // A line per series: its rolling average, each point carrying the reading behind it.
    const line = (times, probe) => times.map((t, i) => ({ t: t * 1000, v: probe.avg[i], raw: probe.raw[i] }));
    const speed = d.speed;
    drawGraph($("nh-g-box-speed"), {
        ...common, bucketSeconds: 1800, title: "Speed against the plan", unit: " Mbps", yFloor0: true,
        sub: `${d.window_hours}-hour rolling average · dots: the 6-second downloads, one every 30 min`,
        series: [
            { name: "Cloudflare (South Africa)", colour: "--nh-c1", values: line(speed.t, speed.cf) },
            { name: "Frankfurt", colour: "--nh-c2", values: line(speed.t, speed.fra) },
            { name: "Plan", colour: "--nh-other", dash: "6 4", values: speed.t.map((t, i) => ({ t: t * 1000, v: speed.plan[i] })) },
        ],
    });
    // Every loss panel shares one scale, so the gap between small and big reads the same on each.
    const peak = Math.max(...Object.values(d.loss.probes).flatMap(p => p.raw.filter(v => v != null)), 0);
    const top = Math.max(d.thresholds.fra_big_pct * 1.5, d.thresholds.gw_big_pct * 1.2, peak);
    const refs = { fra: d.thresholds.fra_big_pct, gw: d.thresholds.gw_big_pct };
    for (const target of [d.primary, ...d.hops]) {
        const primary = target === d.primary;
        drawGraph($(`nh-g-box-loss-${target}`), {
            ...common, title: `Packet loss to ${BOX_TARGET_NAMES[target] || target}`, unit: " %", yFloor0: true, yMax: top,
            sub: primary ? `${target} · the main measure: a real end-to-end server · ${d.window_hours}-hour rolling average over the 5-minute readings`
                : `${target} · ${d.window_hours}-hour rolling average`,
            refs: refs[target] != null ? [{ v: refs[target], label: `alert ${refs[target]}%` }] : [],
            series: BOX_SIZES.map(([suffix, name, colour]) => {
                const probe = d.loss.probes[`${target}${suffix}`];
                return { name, colour, values: probe ? line(d.loss.t, probe) : [] };
            }),
        });
    }
}

function drawBoxMarkers(d) {
    const admin = can("network_health", "admin");
    $("nh-marker-form").hidden = !admin;
    const list = $("nh-box-markers");
    if (!d.markers.length) {
        list.replaceChildren(el("li", "nh-none", admin ? "None yet. Add one when something changes on the line (a cable swapped, a port moved)."
            : "None yet."));
        return;
    }
    list.replaceChildren(...[...d.markers].reverse().map(m => {
        const item = el("li");
        item.append(el("span", "nh-marker-at", when(m.at)), el("span", null, m.label));
        if (admin) {
            const remove = el("button", "nh-link-btn", "Remove");
            remove.type = "button";
            remove.dataset.marker = m.id;
            remove.setAttribute("aria-label", `Remove the marker ${m.label}`);
            item.append(remove);
        }
        return item;
    }));
}

async function saveMarker(e) {
    e.preventDefault();
    const msg = $("nh-marker-msg");
    try {
        await api("/network/health/box/markers", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ at: `${$("nh-marker-at").value}:00+03:00`, label: $("nh-marker-label").value.trim() }),
        });
        msg.textContent = "Added.";
        $("nh-marker-label").value = "";
        load();
    } catch (err) {
        msg.textContent = err.message;
    }
}

async function removeMarker(e) {
    const button = e.target.closest("button[data-marker]");
    if (!button) return;
    try {
        await api(`/network/health/box/markers?id=${encodeURIComponent(button.dataset.marker)}`, { method: "DELETE" });
        load();
    } catch (err) {
        $("nh-marker-msg").textContent = err.message;
    }
}

// ---- Site visits: what SmartOLT reports (status and its own signal grades) ----
// Worked out on the server (db/olt_pons.py, GET /olt/visits); nothing here
// judges a dBm. A visit opens the PON's ONUs, or the one ONU, on the OLT page.

function moreNames(names, count = 4) {
    if (!names.length) return "";
    return names.slice(0, count).join(", ") + (names.length > count ? ` +${names.length - count}` : "");
}

const VISIT_TONES = { bad: "nh-bad", warn: "nh-warn" };

function drawVisits(d) {
    const list = $("nh-visits");
    if (d.error) {
        $("nh-visits-sub").textContent = "";
        list.replaceChildren(el("li", "nh-none", d.forbidden ? "Site visits come from the OLT page, which needs Sites > Network on your role."
            : `Couldn't load the site visits: ${d.error}`));
        return;
    }
    const notes = [];
    if (!d.configured) notes.push("SmartOLT isn't connected on this server");
    else if (d.status_stale) notes.push(`status last updated ${when(d.status_polled_at)}`);
    else if (d.signal_stale && d.signal_polled_at) notes.push(`signal last updated ${when(d.signal_polled_at)}`);
    $("nh-visits-sub").textContent = `what SmartOLT reports: lost light and weak signal, most urgent first${notes.length ? ` · ${notes.join(", ")}` : ""}`;
    if (!d.visits.length) return list.replaceChildren(el("li", "nh-none", "No site visits: SmartOLT reports no lost light and no weak signal."));
    list.replaceChildren(...d.visits.map(v => {
        const item = el("li", VISIT_TONES[v.urgency]);
        const open = el("a", "nh-visit-link", v.title);
        open.href = v.onus.length === 1 ? `#/olt/onu/${encodeURIComponent(v.onus[0])}` : `#/olt/port/${v.pon}`;
        open.title = v.onus.length === 1 ? "Open this ONU on the OLT page" : `Open PON ${v.pon_label} on the OLT page`;
        const title = el("strong");
        title.append(open);
        item.append(title, el("span", "nh-job-detail", v.detail));
        const facts = [];
        if (v.onus.length > 1 && v.names.length) facts.push(moreNames(v.names, 8));
        if (v.signal_dbm != null) facts.push(`${v.signal_dbm.toFixed(1)} dBm, graded ${v.grade || "—"} by SmartOLT`);
        if (v.kind === "los" && v.since) facts.push(`LOS since ${when(v.since)}`);
        if (facts.length) item.append(el("span", "nh-job-names", facts.join(" · ")));
        return item;
    }));
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
    if (meta.lookups?.length) bits.push(`looked up: ${meta.lookups.join(", ")}`);
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
                              meta: { model_label: a.model_label, effort: a.effort, cost_usd: a.cost_usd, truncated: a.truncated, lookups: a.lookups || [] } });
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
    $("nh-tab-settings").hidden = admin.hidden;
    if (admin.hidden && state.tab === "settings") showRoute("uplink", { updateUrl: true });
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

// ---- "What's on the OLT": the side panel ----
// Plain text in the page; the numbers in it are filled in from the same
// thresholds the page and the alerts use, so it can't quote a stale one.

function fillGuide() {
    const a = state.access && !state.access.error ? state.access.thresholds : null;
    const inside = state.inside && !state.inside.error ? state.inside : null;
    const values = {};
    if (a) {
        Object.assign(values, { rx_weak: a.rx_weak_below, rx_bad: a.rx_bad_below, rx_over: a.rx_overload_above,
            olt_weak: a.olt_weak_below, olt_bad: a.olt_bad_below, group_min: a.group_min, whole_pon_min: a.whole_pon_min,
            drop_ont_db: a.drop_ont_db, drop_shared_db: a.drop_shared_db, shared_min: a.shared_min,
            site_polls: a.site_polls, line_polls: a.line_polls, mass_drop_pct: a.mass_drop_pct, mass_drop_min: a.mass_drop_min,
            splitter_m: a.splitter_m, splitter_together: a.splitter_together });
    }
    if (inside) {
        Object.assign(values, { crc_red: inside.crc.red_over_pct, snmp_silent: inside.snmp.stale_after_minutes,
                                olt_port: inside.olt_interface });
    }
    document.querySelectorAll("#nh-guide [data-g]").forEach(node => {
        const v = values[node.dataset.g];
        if (v != null) node.textContent = String(v);
        else if (node.dataset.g !== "olt_port") node.textContent = "—";
    });
}

function openGuide(isOpen) {
    const bar = document.getElementById("global-topbar");
    if (bar) $("nh-guide").style.setProperty("--nh-guide-top", `${Math.round(bar.getBoundingClientRect().bottom)}px`);
    $("nh-guide").hidden = !isOpen;
    $("nh-guide-open").setAttribute("aria-expanded", String(isOpen));
    document.body.classList.toggle("nh-guide-is-open", isOpen);   // the Diagnose button steps aside (css)
    if (isOpen) $("nh-guide-close").focus();
}

// ---- Tabs ----
// One tab row, like Hotspot Status's: Uplink, Inside network, Incidents and
// Settings (admins). The tab is in the URL (#/network-health/inside) so a
// link or a reload lands on it; switching only replaces the URL, so Back
// still leaves the page. The ONUs and PON ports are on the OLT page.

const TABS = ["uplink", "inside", "incidents", "settings"];
const RANGED_TABS = new Set(["uplink", "inside"]);   // the 24 h / 7 d toggle applies to these

function showRoute(first, { updateUrl = false } = {}) {
    let tab = state.tab;
    if (first === "routes") tab = "uplink";              // Content routes is part of Uplink now
    else if (TABS.includes(first)) tab = first;
    if (tab === "settings" && $("nh-tab-settings").hidden) tab = "uplink";
    state.tab = tab;
    for (const name of TABS) {
        $(`nh-tab-${name}`).setAttribute("aria-selected", String(name === tab));
        $(`nh-tab-${name}`).tabIndex = name === tab ? 0 : -1;
        $(`nh-panel-${name}`).hidden = name !== tab;
    }
    document.querySelector(".nh-rangebar").hidden = !RANGED_TABS.has(tab);
    // A graph drawn while its panel was hidden had no width to fit.
    redrawVisibleGraphs();
    if (updateUrl) {
        const hash = `#/network-health/${tab}`;
        if (location.hash !== hash) history.replaceState(null, "", hash);
    }
}

function redrawVisibleGraphs() {
    if (state.tab === "uplink") drawGraphs();
    if (state.tab === "inside" && state.inside && !state.inside.error) { drawCrcGraph(); drawInsideProbes(); }
    if (state.tab === "inside") drawBoxGraphs();
}

// The number of open problems on a tab: the worst one's glyph + the count.
function setTabCount(id, rules) {
    const badgeEl = $(id);
    badgeEl.hidden = !rules.length;
    if (!rules.length) return;
    const red = rules.some(r => r.severity === "RED");
    const [glyph, , cls] = red ? STATUS.RED : STATUS.AMBER;
    badgeEl.className = `nh-tab-count ${cls}`;
    badgeEl.textContent = `${glyph} ${rules.length}`;
    badgeEl.title = `${rules.length} open problem${rules.length === 1 ? "" : "s"}`;
}

async function load() {
    if ($("view-network-health").hidden) return;
    try {
        const [overview, charts, incidents, routes, inside, boxData, visits] = await Promise.all([
            api("/network/health/overview"),
            api(`/network/health/charts?range=${state.range}`),
            api("/network/health/incidents?limit=100"),
            api("/network/health/routes"),
            // Its own failure doesn't take the rest of the page down.
            api(`/network/health/inside?range=${state.range}`).catch(err => ({ error: err.message })),
            api("/network/health/box").catch(err => ({ error: err.message })),
            // Its own source (the OLT page's permission): its failure doesn't take the rest down either.
            api("/olt/visits").catch(err => ({ error: err.message, forbidden: /permission/i.test(err.message) })),
        ]);
        state.overview = overview;
        state.charts = charts;
        drawBanner(overview);
        drawDevices(overview);
        drawGraphs();
        drawInside(inside);
        drawBox(boxData);
        drawVisits(visits);
        drawRoutes(routes);
        drawIncidents(incidents);
        setTabCount("nh-count-inside", inside.error ? [] : inside.active);
        setTabCount("nh-count-incidents", incidents.filter(i => !i.ended_at));
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
    $("nh-guide-open").addEventListener("click", () => openGuide($("nh-guide").hidden));
    $("nh-guide-close").addEventListener("click", () => { openGuide(false); $("nh-guide-open").focus(); });
    document.addEventListener("keydown", e => {
        if (e.key === "Escape" && !$("nh-guide").hidden) { openGuide(false); $("nh-guide-open").focus(); }
    });
    registerRouteResetter(() => openGuide(false));   // leaving the page closes it
    const tabs = $("nh-tabs");
    tabs.addEventListener("click", e => {
        const tab = e.target.closest("[data-nh-tab]");
        if (tab) showRoute(tab.dataset.nhTab, { updateUrl: true });
    });
    tabs.addEventListener("keydown", e => {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        const shown = [...tabs.querySelectorAll("[data-nh-tab]:not([hidden])")];
        const at = shown.findIndex(b => b.dataset.nhTab === state.tab);
        const next = shown[(at + (e.key === "ArrowRight" ? 1 : shown.length - 1)) % shown.length];
        showRoute(next.dataset.nhTab, { updateUrl: true });
        next.focus();
    });
    $("nh-settings-form").addEventListener("submit", saveSettings);
    $("nh-marker-form").addEventListener("submit", saveMarker);
    $("nh-box-markers").addEventListener("click", removeMarker);
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
        state.resizeTimer = setTimeout(redrawVisibleGraphs, 150);
    });
    observer.observe($("nh-graphs"));
    observer.observe($("nh-inside-probes"));
    observer.observe($("nh-g-crc"));
    observer.observe($("nh-box-graphs"));

    registerRoute("network-health", params => {
        // The Access network page moved to the OLT page (PON tab): old links land there.
        if (params[0] === "access") { navigate("olt/pon", { replace: true }); return; }
        showView("view-network-health");
        // #/network-health/<tab>; the bare link keeps the tab last open.
        $("nh-tab-settings").hidden = !can("network_health", "admin");
        showRoute(params[0], { updateUrl: true });
        load();
        if (!state.access) {
            // The guide panel's numbers (the access rules' limits): a small read, once.
            api("/network/health/access/thresholds").then(t => { state.access = { thresholds: t }; fillGuide(); }).catch(() => {});
        }
        loadSettings().catch(err => { $("nh-settings-msg").textContent = err.message; });
        if (!dx.convo || dx.userKey !== dxKey()) { dxLoad(); dx.userKey = dxKey(); }
        loadDxInfo().catch(() => {});
        if (!state.timer) state.timer = setInterval(load, REFRESH_MS);
    });
}
