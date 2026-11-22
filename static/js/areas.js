import { authHeaders, showView, navigate, registerRoute, registerRouteResetter, can } from "./common.js";

// Hotspot Areas (#/areas): each hotspot area's download and upload now and over
// the last 24 hours, read every 5 min from the CCR2004's VLANs. It lives under
// Hotspot Status as a third tab (Status / Trends / Areas). The data is Network
// Health's (/network/health/areas), so the tab and the page need Network Health
// > View. Every value goes onto the page as text.

const REFRESH_MS = 60000;
const SVG = "http://www.w3.org/2000/svg";
const $ = id => document.getElementById(id);
let timer = null;

// Small helpers of its own: a page script never imports another page's script.
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

const hm = iso => new Date(iso).toLocaleTimeString("en-GB", { timeZone: "Africa/Nairobi", hour: "2-digit", minute: "2-digit" });
const T = iso => new Date(iso).getTime();

async function api(path) {
    const res = await fetch(path, { headers: authHeaders() });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(typeof err.detail === "string" ? err.detail : `Couldn't load (${res.status})`);
    }
    return res.json();
}

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

function drawActive(active) {
    const list = $("areas-active");
    list.hidden = !active.length;
    list.replaceChildren(...active.map(rule => {
        const item = el("li");
        item.append(el("span", "nh-rule-glyph nh-warn", "▲"), el("strong", null, ` ${rule.label}`),
                    el("span", "nh-rule-text", ` ${rule.text}. Since ${hm(rule.started_at)}`));
        return item;
    }));
}

function draw(d) {
    $("areas-sub").textContent = `Per VLAN on the CCR2004 uplink, every 5 min. Network Health alerts when an area that's usually busy`
        + ` falls under ${d.thresholds.area_silent_mbps} Mbps twice in a row (07:00–23:00).`;
    drawActive(d.active);
    const tbody = $("areas-rows");
    if (!d.areas.length) return emptyRow(tbody, 5, "No area readings yet.");
    const silent = new Set(d.active.map(a => a.subject));
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

async function load() {
    if ($("view-areas").hidden) return;
    try {
        draw(await api("/network/health/areas"));
        $("areas-updated").textContent = `Updated ${hm(new Date().toISOString())}`;
    } catch (err) {
        $("areas-updated").textContent = `Couldn't load: ${err.message}`;
    }
}

export function initAreas() {
    document.querySelectorAll("#view-areas [data-tab-route]")
        .forEach(b => b.addEventListener("click", () => navigate(b.dataset.tabRoute)));
    // The Areas tab on Hotspot Status / Trends / Areas shows only to those who
    // may see Network Health. Runs before every navigation, so it is right
    // by the time either page is on screen.
    registerRouteResetter(() => {
        document.querySelectorAll('[data-tab-route="areas"]').forEach(b => { b.hidden = !can("network_health", "view"); });
    });
    registerRoute("areas", () => {
        showView("view-areas");
        load();
        if (!timer) timer = setInterval(load, REFRESH_MS);
    });
}
