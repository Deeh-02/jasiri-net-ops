import { authHeaders, showView, registerRoute, navigate, stateRow, confirmDialog } from "./common.js";

// OLT (SmartOLT, Phase 6): Configured and Unconfigured tabs, one ONU's
// details, and Authorize (admin only). Every value comes from OPS's server
// (which scrubs passwords out of SmartOLT's replies) and is put on the page
// with textContent: ONU names are typed by people in SmartOLT.

const LIST_MS = 60000;            // Configured list (OPS's own copy; polled server-side)
const tz = { timeZone: "Africa/Nairobi" };
const when = iso => (iso ? new Date(iso).toLocaleString("en-GB", { ...tz, day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
const hm = iso => new Date(iso).toLocaleTimeString("en-GB", { ...tz, hour: "2-digit", minute: "2-digit" });

const state = {
    tab: "configured",
    list: null,
    filter: "",           // "" | a status | "weak"
    search: "",
    zone: "",
    port: "",
    unconfigured: null,
    canAuthorize: false,
    writesEnabled: true,
    onuId: null,
    period: "daily",
    graphUrls: [],
    timer: null,
    authRow: null,
    authOptions: null,
    authBusy: false,
};

const $ = id => document.getElementById(id);

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
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

function duration(seconds) {
    if (seconds == null) return "—";
    const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    return `${m}m`;
}

function cell(label, child, className) {
    const td = el("td", className || null);
    td.dataset.label = label;
    if (child == null || typeof child === "string" || typeof child === "number") td.textContent = child ?? "—";
    else td.append(...[].concat(child));
    return td;
}

// Glyph + word for every status, never colour alone.
const STATUS = {
    Online: ["●", "olt-online"],
    LOS: ["▼", "olt-down"],
    Offline: ["▼", "olt-down"],
    "Power fail": ["◆", "olt-down"],
};
function statusBadge(status) {
    const [glyph, cls] = STATUS[status] || ["○", "olt-unknown"];
    return el("span", `olt-badge ${cls}`, `${glyph} ${status || "Unknown"}`);
}

const GRADE = { "Very good": "olt-good", Warning: "olt-warn", Critical: "olt-bad" };
function signalCell(dbm, grade) {
    const box = el("span", `olt-dbm ${GRADE[grade] || ""}`);
    box.textContent = dbm == null ? "no reading" : `${dbm.toFixed(2)} dBm`;
    if (grade === "Warning" || grade === "Critical") box.title = `SmartOLT grades this ${grade}`;
    return box;
}

const ponName = o => (o.board == null ? "—" : `gpon-onu_0/${o.board}/${o.port}:${o.onu}`);

// ---- Configured ----

function renderChips() {
    const p = state.list;
    const chip = (key, label, n, tone) => {
        const b = el("button", `summary-item${tone && n ? ` ${tone}` : ""}`);
        b.type = "button";
        b.dataset.filter = key;
        b.setAttribute("aria-pressed", String(state.filter === key));
        b.append(el("strong", null, String(n)), document.createTextNode(` ${label}`));
        return b;
    };
    const others = Object.keys(p.counts).filter(k => k !== "Online").sort();
    $("olt-chips").replaceChildren(...(p.total ? [
        chip("", "All", p.total),
        chip("Online", "Online", p.counts.Online || 0, "good"),
        ...others.map(k => chip(k, k, p.counts[k], "bad")),
        chip("weak", "Weak signal", p.weak, "warn"),
    ] : []));
}

function fillSelect(select, values, allLabel) {
    const current = select.value;
    select.replaceChildren(new Option(allLabel, ""), ...values.map(v => new Option(v, v)));
    select.value = values.includes(current) ? current : "";
}

function renderFreshness() {
    const p = state.list;
    const notes = [];
    if (!p.configured && !p.total) notes.push("SmartOLT isn't connected on this server yet (SMARTOLT_URL and SMARTOLT_API_KEY).");
    else if (!p.status_polled_at) notes.push("No ONU data from SmartOLT yet; the first fetch runs within a minute.");
    else {
        if (p.status_stale) notes.push(`Status last updated ${when(p.status_polled_at)}, not current.`);
        if (p.signal_stale && p.signal_polled_at) notes.push(`Signal last updated ${when(p.signal_polled_at)}.`);
        if (p.details_stale && !p.details_polled_at) notes.push("Zone, splitter, mode and VLAN arrive with the next details fetch (every 30 minutes).");
    }
    $("olt-stale").hidden = !notes.length;
    $("olt-stale").textContent = notes.join(" ");
    const parts = [];
    if (p.status_polled_at) parts.push(`status ${hm(p.status_polled_at)}`);
    if (p.signal_polled_at) parts.push(`signal ${hm(p.signal_polled_at)}`);
    $("olt-updated").textContent = parts.length ? `updated ${parts.join(" · ")}` : "";
}

function filteredOnus() {
    const q = state.search.trim().toLowerCase();
    return state.list.onus.filter(o =>
        (!state.filter || (state.filter === "weak" ? ["Warning", "Critical"].includes(o.signal_quality) : o.status === state.filter)) &&
        (!state.zone || o.zone === state.zone) &&
        (!state.port || `${o.board}/${o.port}` === state.port) &&
        (!q || [o.name, o.sn, o.zone, o.odb, o.onu_type, ...(o.vlans || []).map(String)].some(v => (v || "").toLowerCase().includes(q))));
}

function renderList() {
    const p = state.list;
    if (!p) return;
    const rows = filteredOnus();
    $("olt-shown").textContent = `${rows.length} of ${p.total} ONUs`;
    const body = $("olt-onus");
    if (!rows.length) {
        body.innerHTML = p.total
            ? stateRow(12, "empty", "Try another search, zone or port, or show all.", "No ONU matches the filters")
            : stateRow(12, "empty", "No ONUs from SmartOLT yet.");
        return;
    }
    body.replaceChildren(...rows.map(o => {
        const tr = el("tr", "olt-row");
        tr.dataset.id = o.id;
        const status = [statusBadge(o.status)];
        if (o.status !== "Online" && o.for_s != null) status.push(el("div", "olt-dim", duration(o.for_s)));
        const view = el("button", "btn-secondary olt-view-btn", "View");
        view.type = "button";
        view.dataset.id = o.id;
        tr.append(
            cell("Name", o.name || "—", "olt-name-cell"),
            cell("Status", status),
            cell("SN", o.sn || o.id, "olt-mono"),
            cell("ONU", [el("span", "olt-mono", ponName(o))], "olt-onu-cell"),
            cell("Zone", o.zone || "—"),
            cell("Splitter", o.odb || "None"),
            cell("Signal", [signalCell(o.signal_1310_dbm, o.signal_quality)]),
            // SmartOLT's own shorthand: Bridge, R: PPPoE, Router.
            cell("Mode", !o.mode ? "—" : o.mode === "Bridging" ? "Bridge" : o.wan_mode === "PPPoE" ? "R: PPPoE" : "Router", "olt-nowrap"),
            cell("VLAN", o.vlans.length ? o.vlans.join(", ") : "—"),
            cell("Type", o.onu_type || "—"),
            cell("Authorized", o.authorization_date ? when(o.authorization_date) : "—", "olt-nowrap"),
            cell("", [view]),
        );
        return tr;
    }));
}

async function loadList() {
    try {
        state.list = await api("/olt/onus");
        state.canAuthorize = state.list.can_authorize;
        state.writesEnabled = state.list.writes_enabled;
        $("olt-count-configured").textContent = state.list.total ? String(state.list.total) : "";
        fillSelect($("olt-zone"), [...new Set(state.list.onus.map(o => o.zone).filter(Boolean))].sort(), "All zones");
        fillSelect($("olt-port"), [...new Set(state.list.onus.filter(o => o.board != null).map(o => `${o.board}/${o.port}`))]
            .sort((a, b) => a.localeCompare(b, undefined, { numeric: true })), "All ports");
        state.zone = $("olt-zone").value;
        state.port = $("olt-port").value;
        renderChips();
        renderFreshness();
        renderList();
    } catch (e) {
        if (!state.list) $("olt-onus").innerHTML = stateRow(12, "error", e.message);
        else { $("olt-stale").hidden = false; $("olt-stale").textContent = `Couldn't refresh: ${e.message}`; }
    }
}

// ---- Unconfigured ----

async function loadUnconfigured() {
    const body = $("olt-unconfigured");
    if (!state.unconfigured) body.innerHTML = stateRow(6, "loading", "Asking SmartOLT…");
    try {
        const p = await api("/olt/unconfigured");
        state.unconfigured = p.onus;
        state.canAuthorize = p.can_authorize;
        state.writesEnabled = p.writes_enabled;
        $("olt-count-unconfigured").textContent = p.onus.length ? String(p.onus.length) : "";
        $("olt-writes-off").hidden = p.writes_enabled || !p.can_authorize;
        if (!p.onus.length) {
            body.innerHTML = stateRow(6, "empty", "When a new ONU is connected to a PON port it appears here. Press Refresh to check again.", "No unconfigured ONUs");
        } else {
            body.replaceChildren(...p.onus.map((o, i) => {
                const tr = el("tr");
                const action = [];
                if (state.canAuthorize && o.can_authorize !== false) {
                    const b = el("button", "btn-primary olt-auth-btn", "Authorize");
                    b.type = "button";
                    b.dataset.index = String(i);
                    b.disabled = !state.writesEnabled;
                    action.push(b);
                }
                tr.append(
                    cell("SN", o.sn || "—", "olt-mono"),
                    cell("PON port", [el("div", "olt-mono", `${o.board}/${o.port}`), el("div", "olt-dim", o.pon_description || "")]),
                    cell("ONU type", o.onu_type || "—"),
                    cell("PON type", (o.pon_type || "").toUpperCase() || "—"),
                    cell("OLT", o.olt_id == null ? "—" : String(o.olt_id)),
                    cell("", action),
                );
                return tr;
            }));
        }
    } catch (e) {
        body.innerHTML = stateRow(6, "error", e.message);
    }
    loadActions();
}

const RESULT = { succeeded: ["● Done", "olt-online"], failed: ["▲ Failed", "olt-down"], mismatch: ["◆ Check", "olt-warn-text"], pending: ["○ Pending", "olt-unknown"] };

async function loadActions() {
    const body = $("olt-actions");
    try {
        const { actions } = await api("/olt/actions");
        if (!actions.length) { body.innerHTML = stateRow(6, "empty", "Nothing authorized from OPS yet."); return; }
        body.replaceChildren(...actions.map(a => {
            const [label, cls] = RESULT[a.result] || [a.result || "—", ""];
            const result = [el("span", `olt-badge ${cls}`, label)];
            if (a.result !== "succeeded" && a.response) result.push(el("div", "olt-dim", a.response));
            const tr = el("tr");
            tr.append(cell("When", when(a.at), "olt-nowrap"), cell("SN", a.sn || "—", "olt-mono"), cell("Name", a.name || "—"),
                      cell("Zone", a.zone || "—"), cell("By", a.by || "—"), cell("Result", result));
            return tr;
        }));
    } catch (e) {
        body.innerHTML = stateRow(6, "error", e.message);
    }
}

// ---- Authorize ----

function option(value, label) {
    return new Option(label ?? value, value);
}

function authMessage(text, tone = "error") {
    const box = $("olt-auth-msg");
    box.hidden = !text;
    box.className = `olt-auth-msg ${tone}`;
    box.textContent = text || "";
}

async function openAuthorize(row) {
    state.authRow = row;
    authMessage("");
    $("olt-auth-where").textContent = `${row.sn} · OLT ${row.olt_id} · board ${row.board} / port ${row.port} · ${(row.pon_type || "gpon").toUpperCase()}`;
    $("olt-auth-name").value = "";
    $("olt-auth-comment").value = "";
    $("olt-auth-submit").disabled = true;
    $("olt-auth-submit").textContent = "Review and authorize";
    $("olt-auth-cancel").textContent = "Cancel";
    $("olt-auth-cancel").disabled = false;
    $("olt-auth-overlay").hidden = false;
    $("olt-auth-type").replaceChildren(option("", "Loading…"));
    try {
        const o = await api(`/olt/authorize-options?olt_id=${encodeURIComponent(row.olt_id || 1)}&pon_type=${encodeURIComponent(row.pon_type || "gpon")}`);
        state.authOptions = o;
        $("olt-auth-type").replaceChildren(option("", "Choose…"), ...o.onu_types.map(t => option(t.name, `${t.name}${t.capability ? ` (${t.capability})` : ""}`)));
        if (row.onu_type && o.onu_types.some(t => t.name === row.onu_type)) $("olt-auth-type").value = row.onu_type;
        syncMode();
        const vlans = o.vlans.filter(v => v.vlan != null).sort((a, b) => a.vlan - b.vlan);
        $("olt-auth-vlan").replaceChildren(option("", "Choose…"), ...vlans.map(v =>
            option(String(v.vlan), `${v.vlan}${v.description ? ` · ${v.description}` : ""}${v.scope ? ` (${v.scope})` : ""}`)));
        const def = vlans.find(v => v.default);
        if (def) $("olt-auth-vlan").value = String(def.vlan);
        $("olt-auth-zone").replaceChildren(option("", "Choose…"), ...o.zones.map(z => {
            const opt = option(z.name);
            opt.dataset.id = String(z.id);
            return opt;
        }));
        $("olt-auth-odb").replaceChildren(option("", "None"));
        const speeds = dir => o.speed_profiles.filter(s => s.direction === dir && s.type !== "iptv").map(s => option(s.name));
        $("olt-auth-down").replaceChildren(option("", "OLT default"), ...speeds("download"));
        $("olt-auth-up").replaceChildren(option("", "OLT default"), ...speeds("upload"));
        $("olt-auth-submit").disabled = false;
        $("olt-auth-type").focus();
    } catch (e) {
        authMessage(`Couldn't load the choices from SmartOLT: ${e.message}`);
    }
}

// A bridging-only ONU type can't route.
function syncMode() {
    const type = state.authOptions?.onu_types.find(t => t.name === $("olt-auth-type").value);
    const bridgeOnly = type?.capability === "Bridging";
    $("olt-auth-mode").value = bridgeOnly ? "Bridging" : $("olt-auth-mode").value;
    $("olt-auth-mode").querySelector('option[value="Routing"]').disabled = bridgeOnly;
}

async function loadOdbs() {
    const zone = $("olt-auth-zone").selectedOptions[0];
    $("olt-auth-odb").replaceChildren(option("", "None"));
    if (!zone?.dataset.id) return;
    try {
        const { odbs } = await api(`/olt/zones/${encodeURIComponent(zone.dataset.id)}/odbs`);
        $("olt-auth-odb").replaceChildren(option("", "None"), ...odbs.map(o => option(o.name)));
    } catch { /* splitter is optional: leave "None" */ }
}

function closeAuthorize() {
    if (state.authBusy) return;
    $("olt-auth-overlay").hidden = true;
    state.authRow = null;
}

async function submitAuthorize(e) {
    e.preventDefault();
    if (state.authBusy) return;
    const row = state.authRow;
    const v = id => $(id).value.trim();
    const body = {
        olt_id: row.olt_id || 1, pon_type: row.pon_type || "gpon", sn: row.sn, board: row.board, port: row.port,
        onu_type: v("olt-auth-type"), onu_mode: v("olt-auth-mode"), vlan: v("olt-auth-vlan") ? Number(v("olt-auth-vlan")) : null,
        zone: v("olt-auth-zone"), odb: v("olt-auth-odb") || null, name: v("olt-auth-name"),
        address_or_comment: v("olt-auth-comment") || null,
        download_speed_profile_name: v("olt-auth-down") || null, upload_speed_profile_name: v("olt-auth-up") || null,
    };
    const missing = [["onu_type", "ONU type"], ["vlan", "VLAN"], ["zone", "zone"], ["name", "name"]].filter(([k]) => !body[k]).map(([, l]) => l);
    if (missing.length) { authMessage(`Choose or fill in: ${missing.join(", ")}.`); return; }
    if (!/^[A-Za-z0-9 @#$&()\-`.+,/_]*$/.test(body.name + (body.address_or_comment || ""))) {
        authMessage("Name and comment can only use letters, numbers, spaces and @#$&()-`.+,/_"); return;
    }
    const ok = await confirmDialog({
        title: "Authorize this ONU on the OLT?",
        message: [
            `Serial: ${body.sn}`,
            `OLT ${body.olt_id}, board ${body.board}, port ${body.port} (${body.pon_type.toUpperCase()})`,
            `Type: ${body.onu_type} · Mode: ${body.onu_mode} · VLAN ${body.vlan}`,
            `Zone: ${body.zone}${body.odb ? ` · Splitter: ${body.odb}` : ""}`,
            `Name: ${body.name}${body.address_or_comment ? ` · ${body.address_or_comment}` : ""}`,
            body.download_speed_profile_name || body.upload_speed_profile_name
                ? `Speed: ${body.download_speed_profile_name || "default"} down / ${body.upload_speed_profile_name || "default"} up` : "Speed: OLT default",
            "",
            "This writes the configuration to the live OLT through SmartOLT.",
        ].join("\n"),
        confirmLabel: "Authorize",
    });
    if (!ok) return;
    state.authBusy = true;
    $("olt-auth-submit").disabled = true;
    $("olt-auth-cancel").disabled = true;
    $("olt-auth-submit").textContent = "Authorizing… (can take a minute)";
    authMessage("");
    try {
        const r = await api("/olt/authorize", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        if (r.result === "succeeded") {
            authMessage(`Authorized. SmartOLT: ${r.message}. OPS read it back and it matches.`, "good");
        } else {
            const diffs = (r.mismatches || []).map(m => `${m.field}: sent ${m.sent}, SmartOLT has ${m.smartolt_has ?? "nothing"}`).join("; ");
            authMessage(`${r.message}${diffs ? ` Differences: ${diffs}.` : ""}`, "warn");
        }
        state.unconfigured = null;
        loadUnconfigured();
        loadList();
        $("olt-auth-submit").textContent = "Done";
        $("olt-auth-cancel").textContent = "Close";
        $("olt-auth-cancel").disabled = false;
        state.authBusy = false;
        return;
    } catch (err) {
        authMessage(`Not authorized: ${err.message}`);
        loadActions();
    }
    state.authBusy = false;
    $("olt-auth-submit").disabled = false;
    $("olt-auth-cancel").disabled = false;
    $("olt-auth-submit").textContent = "Review and authorize";
}

// ---- One ONU ----

function fact(label, value, opts = {}) {
    const wrap = el("div", opts.wide ? "olt-fact-wide" : null);
    const dd = el("dd", opts.className || null);
    if (value instanceof Node) dd.append(value);
    else dd.textContent = value == null || value === "" ? "—" : String(value);
    wrap.append(el("dt", null, label), dd);
    return wrap;
}

function signalBox(label, dbm, grade) {
    const box = el("div", "olt-signal");
    box.append(el("div", "olt-signal-label", label), signalCell(dbm, grade));
    return box;
}

function renderOnu(o) {
    $("olt-onu-title").textContent = o.name || o.sn || "ONU";
    $("olt-onu-facts").replaceChildren(
        fact("OLT", o.olt_name ? `${o.olt_id} - ${o.olt_name}` : o.olt_id),
        fact("Board", o.board),
        fact("Port", o.port),
        fact("ONU", ponName(o), { className: "olt-mono" }),
        fact("GPON channel", (o.gpon_channel || o.pon_type || "").toUpperCase()),
        fact("SN", o.sn, { className: "olt-mono" }),
        fact("ONU type", o.onu_type),
        fact("Zone", o.zone),
        fact("Splitter", o.odb || "None"),
        fact("Name", o.name),
        fact("Address or comment", o.address || "None"),
        fact("Contact", o.contact || "None"),
        fact("Authorization date", when(o.authorization_date)),
        fact("ONU external ID", o.id, { className: "olt-mono" }),
    );
    const status = $("olt-onu-status");
    status.replaceChildren(statusBadge(o.status));
    if (o.last_status_change) status.append(el("span", "olt-dim", ` since ${when(o.last_status_change)}`));
    if (o.distance_m != null) status.append(el("span", "olt-dim olt-distance", `${(o.distance_m / 1000).toFixed(2)} km`));
    $("olt-onu-signals").replaceChildren(
        signalBox("ONU Rx 1490nm", o.signal_1490_dbm, o.signal_quality),
        signalBox("OLT Rx 1310nm", o.signal_1310_dbm, o.signal_quality),
    );
    const wan = o.wan_mode ? `${o.wan_mode}${o.ip_address ? ` - ${o.ip_address}` : ""}` : null;
    $("olt-onu-service").replaceChildren(
        fact("TR069 profile", o.tr069 === "Enabled" ? (o.tr069_profile || "Enabled") : (o.tr069 || "Inactive")),
        fact("Mgmt IP", o.mgmt_ip_address || (o.mgmt_ip_mode ? `${o.mgmt_ip_mode}` : "Inactive")),
        fact("Attached VLANs", o.attached_vlans.length ? o.attached_vlans.join(", ") : "—"),
        fact("ONU mode", o.mode ? `${o.mode}${o.vlan ? ` - WAN vlan: ${o.vlan}` : ""}` : null),
        fact("WAN setup mode", wan),
        fact("PPPoE username", o.pppoe_username),
        fact("PPPoE password", "Not shown in OPS", { className: "olt-dim" }),
        fact("VoIP service", o.voip_service),
        fact("CATV", o.catv),
        fact("Admin state", o.administrative_status),
    );
    $("olt-onu-speeds").innerHTML = "";
    const sp = o.service_ports || [];
    if (!sp.length) $("olt-onu-speeds").innerHTML = stateRow(5, "empty", "No service ports.");
    else $("olt-onu-speeds").replaceChildren(...sp.map((s, i) => {
        const tr = el("tr");
        tr.append(cell("Service", i === 0 ? "Internet" : `Service ${i + 1}`), cell("Service-port ID", s.service_port),
                  cell("User-VLAN", s.vlan), cell("Download", s.download_speed), cell("Upload", s.upload_speed));
        return tr;
    }));
    state.ethPorts = o.ethernet_ports || [];
    renderPorts();
    $("olt-onu-body").hidden = false;
    $("olt-onu-more").hidden = false;
}

function renderPorts(lan = state.lanPorts || []) {
    const body = $("olt-onu-ports");
    const ports = state.ethPorts || [];
    if (!ports.length) { body.innerHTML = stateRow(6, "empty", "No Ethernet ports reported."); return; }
    body.replaceChildren(...ports.map((p, i) => {
        const link = lan[i];
        const tr = el("tr");
        tr.append(cell("Port", p.port), cell("Admin state", p.admin_state), cell("Mode", p.mode), cell("VLAN", p.vlan || "—"),
                  cell("DHCP", p.dhcp), cell("Link", link ? `${link.link === "up" ? "● up" : "○ down"}${link.speed && link.speed !== "-" ? ` · ${link.speed} Mbps` : ""}` : "—"));
        return tr;
    }));
}

function renderLive(s) {
    $("olt-live-loading").hidden = true;
    const on = s.onu, opt = s.optical;
    $("olt-live-facts").replaceChildren(
        fact("Run state", on.run_state),
        fact("Online for", on.online_duration),
        fact("Distance", on.distance_m ? `${on.distance_m} m` : null),
        fact("Last down cause", on.last_down_cause),
        fact("Last up", on.last_up_time ? when(on.last_up_time) : null),
        fact("Last down", on.last_down_time ? when(on.last_down_time) : null),
        fact("ONU Rx / Tx", [opt.onu_rx_dbm, opt.onu_tx_dbm].every(v => v == null) ? null : `${opt.onu_rx_dbm ?? "—"} / ${opt.onu_tx_dbm ?? "—"} dBm`),
        fact("OLT Rx", opt.olt_rx_dbm ? `${opt.olt_rx_dbm} dBm` : null),
        fact("Temperature", opt.temperature_c ? `${opt.temperature_c} °C` : null),
        fact("Line profile", on.line_profile),
        fact("Service profile", on.service_profile),
        fact("TR069 ACS profile", on.tr069_acs_profile),
    );
    state.lanPorts = s.lan_ports;
    renderPorts();
    const hist = $("olt-onu-history");
    if (!s.history.length) hist.innerHTML = stateRow(4, "empty", "No history from the OLT.");
    else hist.replaceChildren(...s.history.map(h => {
        const tr = el("tr");
        tr.append(cell("#", h.n), cell("Up at", h.up_at ? when(h.up_at) : "—", "olt-nowrap"),
                  cell("Down at", h.down_at ? when(h.down_at) : "—", "olt-nowrap"), cell("Cause", h.cause));
        return tr;
    }));
}

async function loadGraphs() {
    state.graphUrls.forEach(u => URL.revokeObjectURL(u));
    state.graphUrls = [];
    const id = state.onuId;
    await Promise.all(["traffic", "signal"].map(async kind => {
        const box = $(`olt-graph-${kind}`);
        box.replaceChildren(el("div", "state state-loading", "Loading graph…"));
        try {
            const res = await fetch(`/olt/onus/${encodeURIComponent(id)}/graph/${kind}/${state.period}`, { headers: authHeaders() });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `Couldn't load (${res.status})`);
            }
            if (id !== state.onuId) return;
            const url = URL.createObjectURL(await res.blob());
            state.graphUrls.push(url);
            const img = el("img");
            img.src = url;
            img.alt = `${kind === "traffic" ? "Traffic" : "Signal and status"} graph, ${state.period}`;
            box.replaceChildren(img);
        } catch (e) {
            box.replaceChildren(el("div", "state", e.message));
        }
    }));
}

async function openOnu(id) {
    state.onuId = id;
    $("olt-list-mode").hidden = true;
    $("olt-onu-mode").hidden = false;
    $("olt-onu-body").hidden = true;
    $("olt-onu-more").hidden = true;
    $("olt-onu-error").hidden = true;
    $("olt-onu-title").textContent = state.list?.onus.find(o => o.id === id)?.name || "ONU";
    $("olt-onu-updated").textContent = "Asking SmartOLT…";
    $("olt-live-loading").hidden = false;
    $("olt-live-facts").replaceChildren();
    $("olt-onu-history").innerHTML = stateRow(4, "loading", "Asking the OLT…");
    state.lanPorts = [];
    window.scrollTo(0, 0);
    try {
        const o = await api(`/olt/onus/${encodeURIComponent(id)}`);
        if (id !== state.onuId) return;
        renderOnu(o);
        $("olt-onu-updated").textContent = `read ${new Date().toLocaleTimeString("en-GB", { ...tz, hour: "2-digit", minute: "2-digit" })}`;
    } catch (e) {
        $("olt-onu-error").hidden = false;
        $("olt-onu-error").textContent = e.message;
        $("olt-onu-updated").textContent = "";
        return;
    }
    loadGraphs();
    try {
        const s = await api(`/olt/onus/${encodeURIComponent(id)}/status`);
        if (id === state.onuId) renderLive(s);
    } catch (e) {
        $("olt-live-loading").hidden = false;
        $("olt-live-loading").className = "state";
        $("olt-live-loading").textContent = `The OLT didn't answer: ${e.message}`;
        $("olt-onu-history").innerHTML = stateRow(4, "empty", "Not available.");
    }
}

// ---- Tabs, routing, polling ----

function showTab(tab) {
    state.tab = tab;
    state.onuId = null;
    $("olt-list-mode").hidden = false;
    $("olt-onu-mode").hidden = true;
    document.querySelectorAll(".olt-tab").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
    $("olt-configured-panel").hidden = tab !== "configured";
    $("olt-unconfigured-panel").hidden = tab !== "unconfigured";
    // Both lists load either way, so each tab's count is right.
    loadList();
    loadUnconfigured();
}

function stopPolling() {
    clearInterval(state.timer);
    state.timer = null;
}

function startPolling() {
    stopPolling();
    state.timer = setInterval(() => {
        if (document.hidden || $("view-olt").hidden) { stopPolling(); return; }
        if (!state.onuId && state.tab === "configured") loadList();
    }, LIST_MS);
}

document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !$("view-olt").hidden && !state.onuId) { loadList(); startPolling(); }
});

export function initOlt() {
    document.querySelector(".olt-tabs").addEventListener("click", e => {
        const b = e.target.closest(".olt-tab");
        if (b) navigate(b.dataset.tab === "unconfigured" ? "olt/unconfigured" : "olt");
    });
    $("olt-chips").addEventListener("click", e => {
        const b = e.target.closest("button[data-filter]");
        if (!b) return;
        state.filter = b.dataset.filter === state.filter ? "" : b.dataset.filter;
        renderChips();
        renderList();
    });
    $("olt-search").addEventListener("input", e => { state.search = e.target.value; renderList(); });
    $("olt-zone").addEventListener("change", e => { state.zone = e.target.value; renderList(); });
    $("olt-port").addEventListener("change", e => { state.port = e.target.value; renderList(); });
    $("olt-onus").addEventListener("click", e => {
        const row = e.target.closest("tr.olt-row");
        if (row) navigate(`olt/onu/${encodeURIComponent(row.dataset.id)}`);
    });
    $("olt-back").addEventListener("click", () => navigate("olt"));
    $("olt-unconfigured-refresh").addEventListener("click", () => loadUnconfigured());
    $("olt-unconfigured").addEventListener("click", e => {
        const b = e.target.closest(".olt-auth-btn");
        if (b && state.unconfigured) openAuthorize(state.unconfigured[Number(b.dataset.index)]);
    });
    $("olt-graph-range").addEventListener("click", e => {
        const b = e.target.closest("button[data-period]");
        if (!b || !state.onuId) return;
        state.period = b.dataset.period;
        document.querySelectorAll("#olt-graph-range button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
        loadGraphs();
    });
    $("olt-auth-type").addEventListener("change", syncMode);
    $("olt-auth-zone").addEventListener("change", loadOdbs);
    $("olt-auth-form").addEventListener("submit", submitAuthorize);
    $("olt-auth-cancel").addEventListener("click", closeAuthorize);
    $("olt-auth-overlay").addEventListener("click", e => { if (e.target === $("olt-auth-overlay")) closeAuthorize(); });
    document.addEventListener("keydown", e => {
        // The confirm dialog handles its own Escape first (capture phase).
        if (e.key === "Escape" && !$("olt-auth-overlay").hidden) closeAuthorize();
    });

    registerRoute("olt", (params) => {
        showView("view-olt");
        if (params[0] === "onu" && params[1]) {
            stopPolling();
            if (!state.list) loadList();
            openOnu(decodeURIComponent(params[1]));
            return;
        }
        showTab(params[0] === "unconfigured" ? "unconfigured" : "configured");
        startPolling();
    });
}
