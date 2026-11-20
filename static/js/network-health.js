import { authHeaders, showView, registerRoute, can } from "./common.js";

// Network Health (#/network-health): is the uplink fast and clean right now,
// and what happened lately. Reads only /network/health/* (its own domain) and
// imports only common.js. Every value goes onto the page as text; the graphs
// are small SVGs drawn here so this page doesn't depend on another page's code.

const REFRESH_MS = 60000;
const tz = { timeZone: "Africa/Nairobi" };
const SVG = "http://www.w3.org/2000/svg";

const state = { range: "24h", timer: null, overview: null, charts: null, resizeTimer: null };
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

const PROBE_NAMES = { "p-gw": "Airtel gateway", "p-cf": "1.1.1.1", "p-goog": "8.8.8.8", "p-nbo": "Nairobi (KIXP)", "p-fra": "Frankfurt" };
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
};
const DEVICE_LABELS = { ccr2116: "CCR2116", ccr2004: "CCR2004", feed: "Frankfurt feed" };
const PEAK_UNITS = { UPLINK_CONGESTED: "%", LATENCY_HIGH: " ms", LOSS: "%", JITTER_HIGH: " ms", CPU_HIGH: "%",
                     OPTIC_DEGRADING: " dBm", SPEED_LOW: " Mbps", ROUTER_SILENT: " min", FEED_SILENT: " min" };

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
        const problem = `${RULE_LABELS[i.rule] || i.rule}${i.subject ? ` (${probeName(i.subject)})` : ""}`;
        const sms = i.is_flapping ? "flapping, not sent" : (i.fire_sms_sent ? "sent" : "—");
        const peak = i.rule === "SPEED_LOW" && i.facts && i.facts.failed ? "test failed"
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

// ---- Graphs ----

// Fixed colour per probe target (never by rank): CCR2004 twins (i-*) share
// their p-* colour, dashed; new-provider twins are dotted.
const PROBE_COLOURS = { "p-gw": "--nh-c1", "p-cf": "--nh-c2", "p-nbo": "--nh-c3", "p-goog": "--nh-c4", "p-fra": "--nh-c5" };
function probeStyle(name) {
    const base = name.replace(/^i-/, "p-").replace(/-new$/, "");
    return { colour: PROBE_COLOURS[base] || "--nh-c6", dash: name.startsWith("i-") ? "6 4" : (name.endsWith("-new") ? "2 3" : null) };
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
   refs: [{v, label}], marks: [{t, label}] (red ticks on the baseline: outages), yFloor0}. */
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
        label.textContent = num(v);
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
            const swatch = el("span", `nh-swatch${s.dash ? " is-dashed" : ""}${s.hollow ? " is-hollow" : ""}`);
            swatch.style.setProperty("--swatch", `var(${s.colour})`);
            item.append(swatch, document.createTextNode(s.name));
            legend.append(item);
        }
        fig.append(legend);
    }
    if (spec.marks?.length) fig.append(el("div", "nh-graph-note", "Red ticks on the baseline: no reply at all (outage)."));
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
    const judged = names.filter(n => ["p-gw", "p-cf"].includes(n));
    drawGraph($("nh-g-loss"), {
        title: "Packet loss", sub: "Airtel gateway and 1.1.1.1", unit: "%", yFloor0: true,
        series: probeSeries(c.probes, "loss", judged),
        refs: [{ v: c.thresholds.loss_amber_pct, label: `${c.thresholds.loss_amber_pct}%` }, { v: c.thresholds.loss_red_pct, label: `${c.thresholds.loss_red_pct}%` }],
    });
    drawGraph($("nh-g-jitter"), {
        title: "Jitter", sub: "Airtel gateway and 1.1.1.1", unit: " ms", yFloor0: true,
        series: probeSeries(c.probes, "jitter", judged),
        refs: [{ v: c.thresholds.jitter_ms, label: `${c.thresholds.jitter_ms} ms` }],
    });
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
        const [overview, charts, incidents] = await Promise.all([
            api("/network/health/overview"),
            api(`/network/health/charts?range=${state.range}`),
            api("/network/health/incidents?limit=100"),
        ]);
        state.overview = overview;
        state.charts = charts;
        drawBanner(overview);
        drawDevices(overview);
        drawGraphs();
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
    new ResizeObserver(() => {
        clearTimeout(state.resizeTimer);
        state.resizeTimer = setTimeout(drawGraphs, 150);
    }).observe($("nh-graphs"));

    registerRoute("network-health", () => {
        showView("view-network-health");
        load();
        loadSettings().catch(err => { $("nh-settings-msg").textContent = err.message; });
        if (!state.timer) state.timer = setInterval(load, REFRESH_MS);
    });
}
