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
    links: null,
    rejected: new Set(),
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
    // A webhook in the last half hour means SmartOLT is pushing changes as they happen.
    if (p.webhook_at && Date.now() - new Date(p.webhook_at).getTime() < 30 * 60 * 1000) parts.push("live updates on");
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
        if ((o.administrative_status || "").toLowerCase() === "disabled") status.push(el("div", "olt-badge olt-warn-text", "◆ Disabled"));
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

const RESULT = { succeeded: ["● Done", "olt-online"], failed: ["▲ Failed", "olt-down"], mismatch: ["◆ Check", "olt-warn-text"],
                 pending: ["○ In progress", "olt-unknown"], unknown: ["○ Unknown", "olt-unknown"] };

async function loadActions() {
    const body = $("olt-actions");
    try {
        const { actions } = await api("/olt/actions?action=authorize");
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

// A fact an admin can click to change (opens that change's dialog).
function editFact(o, label, value, kind) {
    if (!o.can_act || !o.actions?.[kind]) return fact(label, value);
    const b = el("button", "olt-fact-link", value == null || value === "" ? "—" : String(value));
    b.type = "button";
    b.dataset.onuConfig = kind;
    b.title = `Change ${label.toLowerCase()}`;
    return fact(label, b);
}

// PPPoE password: starred; an admin's click reads it live from SmartOLT
// (never kept in OPS; the reveal is recorded in the ONU's history).
function passwordFact(o) {
    if (!o.pppoe_username) return fact("PPPoE password", "—");
    if (!o.can_act) return fact("PPPoE password", "Admins only", { className: "olt-dim" });
    const wrap = el("span", "olt-secret");
    const text = el("span", "olt-mono", "••••••••");
    text.id = "olt-pppoe-pass";
    const b = el("button", "olt-eye", "Show");
    b.type = "button";
    b.id = "olt-pppoe-eye";
    b.setAttribute("aria-label", "Show the PPPoE password");
    wrap.append(text, b);
    return fact("PPPoE password", wrap);
}

let passwordTimer = null;
async function togglePassword() {
    const text = $("olt-pppoe-pass"), b = $("olt-pppoe-eye"), o = state.onu;
    if (!text || !b || !o) return;
    clearTimeout(passwordTimer);
    if (b.dataset.shown) {
        text.textContent = "••••••••";
        b.textContent = "Show";
        delete b.dataset.shown;
        return;
    }
    b.disabled = true;
    b.textContent = "…";
    try {
        const { password } = await api(`/olt/onus/${encodeURIComponent(o.id)}/pppoe-password`);
        if (state.onu?.id !== o.id) return;
        text.textContent = password || "(none set)";
        b.textContent = "Hide";
        b.dataset.shown = "1";
        passwordTimer = setTimeout(() => { if (b.dataset.shown) togglePassword(); }, 30000);
        loadOnuActions(o.id);
    } catch (e) {
        b.textContent = "Show";
        actionStatus(`Couldn't read the password: ${e.message}`, "bad");
    } finally {
        b.disabled = false;
    }
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
        editFact(o, "Zone", o.zone, "location"),
        editFact(o, "Splitter", o.odb ? `${o.odb}${o.odb_port ? ` · port ${o.odb_port}` : ""}` : "None", "location"),
        editFact(o, "Seal number", o.seal_number || "None", "location"),
        editFact(o, "Name", o.name, "location"),
        editFact(o, "Address or comment", o.address || "None", "location"),
        editFact(o, "Contact", o.contact || "None", "location"),
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
    const c = o.customer;
    $("olt-onu-customer").replaceChildren(...(c?.site ? [fact("Hotspot site", c.site.name)]
        : c?.not_a_customer ? [fact("Customer", "Not a customer")]
        : c ? [fact("Customer", c.name || c.username), fact("Username", c.username, { className: "olt-mono" }),
               fact("Plan", c.plan), fact("Account", [c.account_status, c.expiry ? `expires ${when(c.expiry)}` : null].filter(Boolean).join(" · "))]
        : [fact("Customer", "Not linked yet (OLT → Links)", { className: "olt-dim" })]));
    const wan = o.wan_mode ? `${o.wan_mode}${o.ip_address ? ` - ${o.ip_address}` : ""}` : null;
    $("olt-onu-service").replaceChildren(
        fact("TR069 profile", o.tr069 === "Enabled" ? (o.tr069_profile || "Enabled") : (o.tr069 || "Inactive")),
        fact("Mgmt IP", o.mgmt_ip_address || (o.mgmt_ip_mode ? `${o.mgmt_ip_mode}` : "Inactive")),
        editFact(o, "Attached VLANs", o.attached_vlans.length ? o.attached_vlans.join(", ") : "—", "vlans"),
        editFact(o, "ONU mode", o.mode ? `${o.mode}${o.vlan ? ` - WAN vlan: ${o.vlan}` : ""}` : null, "mode"),
        editFact(o, "WAN setup mode", wan, "mode"),
        fact("PPPoE username", o.pppoe_username),
        passwordFact(o),
        fact("VoIP service", o.voip_service),
        fact("CATV", o.catv),
        fact("Admin state", o.administrative_status),
        fact("Config resync", o.resync_failed ? el("span", "olt-flag", "▲ Last resync failed") : "OK"),
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

async function openOnu(id, { keepStatus = false } = {}) {
    state.onuId = id;
    state.onu = null;
    if (!keepStatus) actionStatus("");
    $("olt-list-mode").hidden = true;
    $("olt-onu-mode").hidden = false;
    $("olt-onu-body").hidden = true;
    $("olt-onu-more").hidden = true;
    $("olt-onu-los").hidden = true;
    loadLos();       // from OPS's own data: shows even when SmartOLT doesn't answer
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
        state.onu = o;
        renderOnu(o);
        renderActionButtons(o);
        loadOnuActions(id);
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

// ---- Customers (6B): linking ONUs to customers ----
// OPS suggests; an admin confirms each link. "Reject" only hides a suggestion
// until the page is reloaded (rejections aren't stored).

const MATCHED = { pppoe_username: "PPPoE username", name: "ONU name", manual: "Picked by hand" };

function onuLabel(o) {
    const box = el("div");
    box.append(el("div", "olt-name-cell", o?.name || "—"), el("div", "olt-dim olt-mono", o ? `${o.sn || o.id}${o.location ? ` · ${o.location}` : ""}` : "no longer in SmartOLT"));
    return box;
}

function customerLabel(c) {
    const box = el("div");
    box.append(el("div", "olt-name-cell", c?.name || c?.username || "—"),
               el("div", "olt-dim", c ? [c.username, c.account_status].filter(Boolean).join(" · ") : ""));
    return box;
}

function siteLabel(x) {
    const box = el("div");
    box.append(el("div", "olt-name-cell", x?.name || "—"), el("div", "olt-dim", x?.vlan_id != null ? `vlan ${x.vlan_id}` : ""));
    return box;
}

async function confirmSiteLink(onu, site, how) {
    const ok = await confirmDialog({
        title: "Link this ONU to this hotspot site?",
        message: `ONU: ${onu.name || "—"} (${onu.sn || onu.id})\nSite: ${site.name}${site.vlan_id != null ? ` · vlan ${site.vlan_id}` : ""}\n\nNetwork Status and the site's page will show this ONU's status and signal.`,
        confirmLabel: "Link",
    });
    if (ok && await postLink("/olt/links/site", { onu_id: onu.id, site_id: site.id, matched_by: how })) loadLinks();
}

function button(label, cls, data) {
    const b = el("button", cls, label);
    b.type = "button";
    Object.assign(b.dataset, data);
    return b;
}

function renderLinks() {
    const p = state.links;
    if (!p) return;
    const edit = p.can_edit;
    const visible = p.suggestions.filter(s => !state.rejected.has(`${s.onu.id}|${s.customer.id}`));
    const siteWaiting = p.site_suggestions.filter(x => !state.rejected.has(`${x.onu.id}|site${x.site.id}`)).length;
    $("olt-count-links").textContent = visible.length + siteWaiting ? String(visible.length + siteWaiting) : "";
    const chip = (n, label, tone) => {
        const b = el("span", `summary-item${tone && n ? ` ${tone}` : ""}`);
        b.append(el("strong", null, String(n)), document.createTextNode(` ${label}`));
        return b;
    };
    $("olt-links-summary").replaceChildren(
        chip(p.linked.length, "linked", "good"),
        chip(visible.length, "suggested", "warn"),
        chip(p.onus_without_customer.length, "ONUs with nothing linked"),
        chip(p.customers_without_onu.length, "active customers with no ONU"),
        chip(p.linked_sites.length, "hotspot sites", "good"),
        chip(p.not_customers.length, "not customers"),
    );
    // ---- hotspot sites ----
    const siteSug = p.site_suggestions.filter(x => !state.rejected.has(`${x.onu.id}|site${x.site.id}`));
    const siteBulk = siteSug.filter(x => !x.ambiguous).length;
    $("olt-accept-sites").hidden = !edit || !siteBulk;
    $("olt-accept-sites").textContent = `Accept all ${siteBulk} site matches`;
    const ss = $("olt-site-suggestions");
    if (!siteSug.length) ss.innerHTML = stateRow(4, "empty", "No site suggestions right now.");
    else ss.replaceChildren(...siteSug.map(x => {
        const tr = el("tr");
        const how = [el("div", null, MATCHED[x.matched_by] || x.matched_by)];
        if (x.ambiguous) how.push(el("div", "olt-warn-text olt-dim", "◆ more than one possible match"));
        const actions = el("div", "olt-actions");
        if (edit) actions.append(button("Accept", "btn-primary olt-small", { act: "siteaccept", onu: x.onu.id, site: x.site.id, how: x.matched_by }),
                                 button("Reject", "btn-secondary olt-small", { act: "sitereject", onu: x.onu.id, site: x.site.id }));
        tr.append(cell("ONU", [onuLabel(x.onu)]), cell("Site", [siteLabel(x.site)]), cell("Matched by", how), cell("", [actions]));
        return tr;
    }));
    const ls = $("olt-linked-sites");
    if (!p.linked_sites.length) ls.innerHTML = stateRow(6, "empty", "No hotspot site linked yet.");
    else ls.replaceChildren(...p.linked_sites.map(l => {
        const tr = el("tr");
        tr.append(cell("Site", [siteLabel(l.site)]), cell("ONU", [onuLabel(l.onu)]), cell("Status", l.onu ? [statusBadge(l.onu.status)] : "—"),
                  cell("Signal", l.onu ? [signalCell(l.onu.signal_1310_dbm, l.onu.signal_quality)] : "—"),
                  cell("Linked by", [el("div", null, l.by || "—"), el("div", "olt-dim", `${MATCHED[l.matched_by] || ""} · ${when(l.at)}`)]),
                  cell("", edit ? [button("Unlink", "btn-secondary olt-small", { act: "unlink", onu: l.onu_id, label: l.site?.name || "This site" })] : []));
        return tr;
    }));
    const bulk = visible.filter(s => s.bulk_ok).length;
    $("olt-accept-all").hidden = !edit || !bulk;
    $("olt-accept-all").textContent = `Accept all ${bulk} exact username matches`;

    const sug = $("olt-suggestions");
    if (!visible.length) sug.innerHTML = stateRow(4, "empty", "No suggestions right now.");
    else sug.replaceChildren(...visible.map(s => {
        const tr = el("tr");
        const how = [el("div", null, MATCHED[s.matched_by] || s.matched_by)];
        if (s.ambiguous) how.push(el("div", "olt-warn-text olt-dim", "◆ more than one possible match"));
        const actions = edit ? [button("Accept", "btn-primary olt-small", { act: "accept", onu: s.onu.id, customer: s.customer.id, how: s.matched_by }),
                                button("Reject", "btn-secondary olt-small", { act: "reject", onu: s.onu.id, customer: s.customer.id })] : [];
        tr.append(cell("ONU", [onuLabel(s.onu)]), cell("Customer", [customerLabel(s.customer)]), cell("Matched by", how),
                  cell("", [el("div", "olt-actions", null)]));
        tr.lastChild.firstChild.append(...actions);
        return tr;
    }));

    const free = $("olt-free-onus");
    if (!p.onus_without_customer.length) free.innerHTML = stateRow(5, "empty", "Every ONU is linked, suggested, or marked as not a customer.");
    else free.replaceChildren(...p.onus_without_customer.map(o => {
        const tr = el("tr");
        const actions = el("div", "olt-actions");
        if (edit) actions.append(button("Link to a customer…", "btn-secondary olt-small", { act: "pick", onu: o.id }),
                                 button("Link to a site…", "btn-secondary olt-small", { act: "picksite", onu: o.id }),
                                 button("Not a customer", "btn-secondary olt-small", { act: "notcust", onu: o.id }));
        tr.append(cell("ONU", [onuLabel(o)]), cell("Status", [statusBadge(o.status)]), cell("Zone", o.zone || "—"),
                  cell("Setup", o.wan_mode || "—"), cell("", [actions]));
        return tr;
    }));

    const custs = $("olt-free-customers");
    if (!p.customers_without_onu.length) custs.innerHTML = stateRow(4, "empty", "Every active customer has an ONU.");
    else custs.replaceChildren(...p.customers_without_onu.map(c => {
        const tr = el("tr");
        const actions = el("div", "olt-actions");
        if (edit) {
            const pick = el("select", "control olt-onu-pick");
            pick.dataset.customer = String(c.id);
            pick.append(new Option("Choose its ONU…", ""), ...p.unlinked_onus.map(o => new Option(`${o.name || o.sn} (${o.sn})`, o.id)));
            actions.append(pick, button("Link", "btn-primary olt-small", { act: "linkpick", customer: c.id }));
        }
        tr.append(cell("Customer", c.name || c.username), cell("Username", c.username, "olt-mono"), cell("Plan", c.plan || "—"), cell("", [actions]));
        return tr;
    }));

    const linked = $("olt-linked");
    if (!p.linked.length) linked.innerHTML = stateRow(7, "empty", "Nothing linked yet. Start with the suggestions above.");
    else linked.replaceChildren(...p.linked.map(l => {
        const tr = el("tr");
        tr.append(cell("Customer", l.customer?.name || l.customer?.username || "—"), cell("Username", l.customer?.username || "—", "olt-mono"),
                  cell("ONU", [onuLabel(l.onu)]), cell("Status", l.onu ? [statusBadge(l.onu.status)] : "—"),
                  cell("Signal", l.onu ? [signalCell(l.onu.signal_1310_dbm, l.onu.signal_quality)] : "—"),
                  cell("Linked by", [el("div", null, l.by || "—"), el("div", "olt-dim", `${MATCHED[l.matched_by] || ""} · ${when(l.at)}`)]),
                  cell("", edit ? [button("Unlink", "btn-secondary olt-small", { act: "unlink", onu: l.onu_id, label: l.customer?.name || l.customer?.username || "" })] : []));
        return tr;
    }));

    const notc = $("olt-not-customers");
    if (!p.not_customers.length) notc.innerHTML = stateRow(4, "empty", "None marked.");
    else notc.replaceChildren(...p.not_customers.map(l => {
        const tr = el("tr");
        tr.append(cell("ONU", [onuLabel(l.onu)]), cell("Zone", l.onu?.zone || "—"), cell("Marked by", `${l.by || "—"} · ${when(l.at)}`),
                  cell("", edit ? [button("Undo", "btn-secondary olt-small", { act: "unlink", onu: l.onu_id, label: "" })] : []));
        return tr;
    }));
}

async function loadLinks() {
    try {
        state.links = await api("/olt/links");
        $("olt-links-msg").hidden = true;
        renderLinks();
    } catch (e) {
        $("olt-links-msg").hidden = false;
        $("olt-links-msg").textContent = e.message;
        if (!state.links) $("olt-suggestions").innerHTML = stateRow(4, "error", e.message);
    }
}

function linksMessage(text) {
    $("olt-links-msg").hidden = !text;
    $("olt-links-msg").textContent = text || "";
}

async function postLink(path, body, method = "POST") {
    try {
        await api(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
        linksMessage("");
        return true;
    } catch (e) {
        linksMessage(e.message);
        return false;
    }
}

function findOnu(id) {
    const p = state.links;
    return p.suggestions.find(s => s.onu.id === id)?.onu || p.site_suggestions.find(s => s.onu.id === id)?.onu
        || p.onus_without_customer.find(o => o.id === id)
        || p.unlinked_onus.find(o => o.id === id) || { id, name: id };
}

async function confirmLink(onu, customer, how) {
    const ok = await confirmDialog({
        title: "Link this ONU to this customer?",
        message: `ONU: ${onu.name || "—"} (${onu.sn || onu.id})\nCustomer: ${customer.name || customer.username} · ${customer.username}${customer.plan ? ` · ${customer.plan}` : ""}\n\nThe customer's account will show this ONU's status and signal.`,
        confirmLabel: "Link",
    });
    if (ok && await postLink("/olt/links", { onu_id: onu.id, customer_id: customer.id, matched_by: how })) loadLinks();
}

async function onLinksClick(e) {
    const b = e.target.closest("button[data-act]");
    if (!b || !state.links) return;
    const { act, onu, customer, how } = b.dataset;
    const p = state.links;
    if (act === "accept") {
        const s = p.suggestions.find(x => x.onu.id === onu && String(x.customer.id) === customer);
        if (s) confirmLink(s.onu, s.customer, s.matched_by || how);
    } else if (act === "reject") {
        state.rejected.add(`${onu}|${customer}`);
        renderLinks();
    } else if (act === "notcust") {
        const o = findOnu(onu);
        if (await confirmDialog({ title: "Mark as not a customer?", message: `${o.name || o.id} will stop showing as an unmatched ONU (for a hotspot, a site or a link). You can undo it below.`, confirmLabel: "Mark" })
            && await postLink("/olt/links/not-a-customer", { onu_id: onu })) loadLinks();
    } else if (act === "unlink") {
        const ok = await confirmDialog({ title: b.dataset.label ? "Unlink this customer?" : "Undo?",
            message: b.dataset.label ? `${b.dataset.label} will no longer show this ONU.` : "This ONU goes back to the unmatched list.",
            confirmLabel: b.dataset.label ? "Unlink" : "Undo", danger: !!b.dataset.label });
        if (ok && await postLink(`/olt/links/${encodeURIComponent(onu)}`, null, "DELETE")) loadLinks();
    } else if (act === "siteaccept") {
        const x = p.site_suggestions.find(y => y.onu.id === onu && String(y.site.id) === b.dataset.site);
        if (x) confirmSiteLink(x.onu, x.site, x.matched_by);
    } else if (act === "sitereject") {
        state.rejected.add(`${onu}|site${b.dataset.site}`);
        renderLinks();
    } else if (act === "picksite") {
        openLinkPicker(findOnu(onu), "site");
    } else if (act === "pick") {
        openLinkPicker(findOnu(onu));
    } else if (act === "linkpick") {
        const pick = document.querySelector(`select.olt-onu-pick[data-customer="${CSS.escape(customer)}"]`);
        if (!pick?.value) { linksMessage("Choose the customer's ONU first."); return; }
        const c = p.customers_without_onu.find(x => String(x.id) === customer);
        confirmLink(findOnu(pick.value), c, "manual");
    }
}

async function acceptAll() {
    const picks = state.links.suggestions.filter(s => s.bulk_ok && !state.rejected.has(`${s.onu.id}|${s.customer.id}`));
    const ok = await confirmDialog({
        title: `Link ${picks.length} ONUs to their customers?`,
        message: `Each of these ONUs has exactly the same PPPoE username as one customer, and nothing else matches either of them:\n\n${picks.slice(0, 12).map(s => `${s.onu.name || s.onu.sn} → ${s.customer.name || s.customer.username}`).join("\n")}${picks.length > 12 ? `\n…and ${picks.length - 12} more` : ""}`,
        confirmLabel: `Link ${picks.length}`,
    });
    if (!ok) return;
    try {
        // Rejected rows are excluded by linking one by one when any were rejected.
        if (picks.length !== state.links.suggestions.filter(s => s.bulk_ok).length) {
            for (const s of picks) await api("/olt/links", { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ onu_id: s.onu.id, customer_id: s.customer.id, matched_by: "pppoe_username" }) });
        } else {
            const r = await api("/olt/links/accept-username-matches", { method: "POST" });
            linksMessage(r.linked !== picks.length ? `Linked ${r.linked}; the list changed while you were looking.` : "");
        }
    } catch (e) {
        linksMessage(e.message);
    }
    loadLinks();
}

async function acceptAllSites() {
    const picks = state.links.site_suggestions.filter(x => !x.ambiguous && !state.rejected.has(`${x.onu.id}|site${x.site.id}`));
    const ok = await confirmDialog({
        title: `Link ${picks.length} ONUs to their hotspot sites?`,
        message: `Each ONU's name contains exactly one site's name, and no other ONU matches that site:\n\n${picks.slice(0, 14).map(x => `${x.onu.name || x.onu.sn} → ${x.site.name}`).join("\n")}${picks.length > 14 ? `\n…and ${picks.length - 14} more` : ""}`,
        confirmLabel: `Link ${picks.length}`,
    });
    if (!ok) return;
    try {
        if (picks.length !== state.links.site_suggestions.filter(x => !x.ambiguous).length) {
            for (const x of picks) await api("/olt/links/site", { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ onu_id: x.onu.id, site_id: x.site.id, matched_by: x.matched_by }) });
        } else {
            const r = await api("/olt/links/accept-site-matches", { method: "POST" });
            linksMessage(r.linked !== picks.length ? `Linked ${r.linked}; the list changed while you were looking.` : "");
        }
    } catch (e) {
        linksMessage(e.message);
    }
    loadLinks();
}

// The "Link to a customer" picker.
let linkOnu = null, linkTimer = null, linkMode = "customer";
function openLinkPicker(onu, mode = "customer") {
    linkOnu = onu;
    linkMode = mode;
    $("olt-link-title").textContent = mode === "site" ? "Link to a hotspot site" : "Link to a customer";
    $("olt-link-search").placeholder = mode === "site" ? "Filter sites by name or vlan" : "Search by name or username";
    $("olt-link-where").textContent = `${onu.name || "—"} · ${onu.sn || onu.id}`;
    $("olt-link-search").value = "";
    $("olt-link-results").replaceChildren(el("div", "olt-dim", "Type at least two letters."));
    $("olt-link-overlay").hidden = false;
    if (mode === "site") showSites();
    $("olt-link-search").focus();
}

// Sites are few (the monitored ones without an ONU), so they're filtered here.
function showSites() {
    const q = $("olt-link-search").value.trim().toLowerCase();
    const sites = state.links.free_sites.filter(x => !q || (x.name || "").toLowerCase().includes(q) || String(x.vlan_id ?? "").includes(q));
    $("olt-link-results").replaceChildren(...(sites.length ? sites.map(x => {
        const b = button("", "olt-link-result", { id: x.id });
        b.append(el("span", "olt-name-cell", x.name || "—"), el("span", "olt-dim", x.vlan_id != null ? ` vlan ${x.vlan_id}` : ""));
        b.addEventListener("click", () => { const o = linkOnu; closeLinkPicker(); confirmSiteLink(o, x, "manual"); });
        return b;
    }) : [el("div", "olt-dim", "No monitored site without an ONU matches.")]));
}
function closeLinkPicker() { $("olt-link-overlay").hidden = true; linkOnu = null; }
async function searchLinkCustomers() {
    const q = $("olt-link-search").value.trim();
    if (q.length < 2) { $("olt-link-results").replaceChildren(el("div", "olt-dim", "Type at least two letters.")); return; }
    try {
        const { customers } = await api(`/olt/customer-search?q=${encodeURIComponent(q)}`);
        const linkedIds = new Set(state.links.linked.map(l => l.customer_id));
        $("olt-link-results").replaceChildren(...(customers.length ? customers.map(c => {
            const b = button("", "olt-link-result", { id: c.id });
            b.append(el("span", "olt-name-cell", c.name || c.username), el("span", "olt-dim olt-mono", ` ${c.username}${c.plan ? ` · ${c.plan}` : ""}`));
            if (linkedIds.has(c.id)) { b.disabled = true; b.append(el("span", "olt-dim", " · already has an ONU")); }
            b.addEventListener("click", () => { const o = linkOnu; closeLinkPicker(); confirmLink(o, c, "manual"); });
            return b;
        }) : [el("div", "olt-dim", "No customer matches.")]));
    } catch (e) {
        $("olt-link-results").replaceChildren(el("div", "state-error", e.message));
    }
}

// ---- Actions on one ONU (6C) ----

const ACTION_TEXT = {
    reboot: { label: "Reboot", verb: "Reboot", doing: "Rebooting", warn: "The ONU restarts; the customer is offline for a minute or two." },
    disable: { label: "Disable ONU", verb: "Disable", doing: "Disabling", warn: "The customer loses service until the ONU is enabled again.", danger: true },
    enable: { label: "Enable ONU", verb: "Enable", doing: "Enabling", warn: "The ONU is switched back on; service returns once it reconnects." },
};

function whoIsOn(o) {
    const c = o.customer;
    if (c?.site) return `Hotspot site: ${c.site.name}`;
    if (c?.not_a_customer) return "Not a customer";
    if (c) return `Customer: ${c.name || c.username} (${c.username})`;
    return "Not linked to any customer";
}

function renderActionButtons(o) {
    const box = $("olt-onu-actions");
    box.hidden = !o.can_act;
    if (!o.can_act) return;
    const disabled = (o.administrative_status || "").toLowerCase() === "disabled";
    const list = ["reboot", disabled ? "enable" : "disable"];
    $("olt-action-buttons").replaceChildren(...list.map(a => {
        const t = ACTION_TEXT[a];
        const b = button(t.label, t.danger ? "btn-danger" : "btn-secondary", { onuAction: a });
        if (!o.actions?.[a]) { b.disabled = true; b.title = "Switched off on this server"; }
        return b;
    }));
    $("olt-config-buttons").replaceChildren(...Object.entries(CONFIG_TEXT).map(([k, t]) => {
        const b = button(t.button, t.danger ? "btn-danger" : "btn-secondary", { onuConfig: k });
        if (!o.actions?.[k]) { b.disabled = true; b.title = "Switched off on this server"; }
        else if (k === "delete" && linkedTo(o)) { b.disabled = true; b.title = "Linked to a customer or site: unlink it on OLT → Links first"; }
        return b;
    }));
}

function actionStatus(text, tone = "") {
    const box = $("olt-action-status");
    box.hidden = !text;
    box.className = `olt-action-status ${tone}`;
    box.textContent = text || "";
}

async function runAction(action) {
    const o = state.onu;
    if (!o) return;
    const t = ACTION_TEXT[action];
    const ok = await confirmDialog({
        title: `${t.verb} this ONU?`,
        message: `${whoIsOn(o)}\nONU: ${o.name || "—"}\nSerial: ${o.sn || o.id}\n\n${t.warn}`,
        confirmLabel: t.verb,
        danger: !!t.danger,
    });
    if (!ok) return;
    document.querySelectorAll("#olt-action-buttons button").forEach(b => { b.disabled = true; });
    actionStatus(`${t.doing}…`);
    try {
        let row = await api(`/olt/onus/${encodeURIComponent(o.id)}/actions`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
        loadOnuActions(o.id);
        // Queued actions: follow them until SmartOLT says it's finished.
        for (let i = 0; row.result === "pending" && i < 80 && state.onuId === o.id; i++) {
            actionStatus(`${t.doing}… SmartOLT has queued it; waiting for the OLT to finish.`);
            await new Promise(r => setTimeout(r, 3000));
            row = await api(`/olt/actions/${row.id}`);
        }
        if (state.onuId !== o.id) return;
        const [label, cls] = RESULT[row.result] || [row.result, ""];
        actionStatus(`${label}: ${row.response || ""}`, row.result === "succeeded" ? "good" : row.result === "pending" ? "" : "bad");
        loadOnuActions(o.id);
        if (row.result !== "pending") setTimeout(() => { if (state.onuId === o.id) openOnu(o.id, { keepStatus: true }); }, 1500);
    } catch (e) {
        actionStatus(`Not done: ${e.message}`, "bad");
        renderActionButtons(o);
        loadOnuActions(o.id);
    }
}

async function loadOnuActions(id) {
    const body = $("olt-onu-action-log");
    try {
        const { actions } = await api(`/olt/actions?onu=${encodeURIComponent(id)}`);
        if (id !== state.onuId) return;
        if (!actions.length) { body.innerHTML = stateRow(4, "empty", "No actions from OPS on this ONU yet."); return; }
        body.replaceChildren(...actions.map(a => {
            const [label, cls] = RESULT[a.result] || [a.result || "—", ""];
            const result = [el("span", `olt-badge ${cls}`, label)];
            if (a.response) result.push(el("div", "olt-dim", a.response));
            const tr = el("tr");
            const what = [el("div", null, ACTION_TEXT[a.action]?.verb || CONFIG_TEXT[a.action]?.verb || OTHER_VERB[a.action] || a.action)];
            if (a.summary && CONFIG_TEXT[a.action]) what.push(el("div", "olt-dim", a.summary));
            tr.append(cell("When", when(a.at), "olt-nowrap"), cell("Action", what),
                      cell("By", a.by || "—"), cell("Result", result));
            return tr;
        }));
    } catch (e) {
        body.innerHTML = stateRow(4, "error", e.message);
    }
}

// ---- Configuration changes on one ONU (6D) ----

const CONFIG_TEXT = {
    resync: { button: "Resync config", verb: "Resync config", title: "Resync config",
              warn: "SmartOLT sends its saved configuration to the ONU again. Service may drop for a moment." },
    speed: { button: "Speed…", verb: "Speed profiles", title: "Change speed profiles",
             warn: "The ONU's ceiling changes at the OLT. The router's PPPoE plan still applies on top: a profile below the plan slows the customer down." },
    port: { button: "Ethernet port…", verb: "Ethernet port", title: "Ethernet port mode",
            warn: "Whatever is plugged into this port loses its connection if the mode or VLAN is wrong." },
    location: { button: "Location…", verb: "Location details", title: "Update location details",
                warn: "" },
    vlans: { button: "VLANs…", verb: "Attached VLANs", title: "Update attached VLANs",
             warn: "After changing the VLANs, check the Ethernet ports and assign VLANs as needed." },
    mode: { button: "ONU mode…", verb: "ONU mode", title: "Update ONU mode",
            warn: "A wrong WAN VLAN, mode or login takes the customer offline until it's put back. After changing the WAN VLAN-ID, check the Ethernet ports." },
    delete: { button: "Delete ONU…", verb: "Delete ONU", title: "Delete ONU", danger: true,
              warn: "SmartOLT removes the ONU and all its configuration. While plugged in it shows under Unconfigured; authorizing it again starts from scratch." },
};
const WAN_LABEL = { webpage: "Setup via ONU webpage", dhcp: "DHCP", static: "Static IP", pppoe: "PPPoE" };
const OTHER_VERB = { view_password: "Viewed PPPoE password" };
const PORT_LABEL = { lan: "LAN", access: "Access", shutdown: "Shut down" };
const DHCP_CHOICES = ["No control", "From ISP", "From ONU", "Forbidden"];

const cfg = { kind: null, busy: false, speed: null };

function linkedTo(o) {
    return !!(o.customer && !o.customer.not_a_customer);
}

function field(label, control, wide = false) {
    const l = el("label", wide ? "olt-auth-wide" : null);
    l.append(document.createTextNode(label), control);
    return l;
}

function selectOf(id, pairs, value) {
    const s = el("select");
    s.id = id;
    s.replaceChildren(...pairs.map(([v, label]) => option(v, label)));
    if (value != null) s.value = value;
    return s;
}

function inputOf(id, type, value = "", attrs = {}) {
    const i = el("input");
    Object.assign(i, { id, type, value, autocomplete: "off", spellcheck: false, ...attrs });
    return i;
}

const val = id => ($(id)?.value ?? "").trim();

function configMessage(text, tone = "error") {
    const box = $("olt-config-msg");
    box.hidden = !text;
    box.className = `olt-auth-msg ${tone === "error" ? "" : tone}`;
    box.textContent = text || "";
}

function portText(p) {
    if (!p) return "?";
    return `${p.mode || "?"}${p.mode === "Access" && p.vlan ? ` ${p.vlan}` : ""}${p.admin_state === "Disabled" ? " (shut down)" : ""}`;
}

// What will be sent, and the before → after line. null = not ready yet.
function configRequest() {
    const o = state.onu;
    if (!$("olt-config-fields").querySelector("select, input")) return { body: null, preview: "" };   // still loading
    switch (cfg.kind) {
    case "speed": {
        const up = val("cfg-up"), down = val("cfg-down"), cur = cfg.speed?.current || {};
        const body = { change: "speed" };
        const parts = [];
        if (down && down !== cur.download) { body.download = down; parts.push(`Download ${cur.download || "?"} → ${down}`); }
        if (up && up !== cur.upload) { body.upload = up; parts.push(`Upload ${cur.upload || "?"} → ${up}`); }
        return parts.length ? { body, preview: parts.join(" · ") } : { body: null, preview: "No change yet." };
    }
    case "port": {
        const name = val("cfg-port"), mode = val("cfg-mode"), p = (o.ethernet_ports || []).find(x => x.port === name);
        const vlanBox = $("cfg-vlan-wrap"), dhcpBox = $("cfg-dhcp-wrap");
        if (vlanBox) vlanBox.hidden = mode !== "access";
        if (dhcpBox) dhcpBox.hidden = mode === "shutdown";
        const body = { change: "port", port: name, mode };
        if (mode === "access") {
            if (!val("cfg-vlan")) return { body: null, preview: "Pick a VLAN." };
            body.vlan = Number(val("cfg-vlan"));
        }
        if (mode !== "shutdown" && val("cfg-dhcp")) body.dhcp = val("cfg-dhcp");
        if (p && !body.dhcp && (mode === "shutdown" ? p.admin_state === "Disabled"
            : p.admin_state !== "Disabled" && p.mode === (mode === "lan" ? "LAN" : "Access") && (mode === "lan" || String(p.vlan) === String(body.vlan)))) {
            return { body: null, preview: `${name} is already ${portText(p)}.` };
        }
        const to = mode === "access" ? `Access ${body.vlan}` : PORT_LABEL[mode];
        return { body, preview: `${name}: ${portText(p)} → ${to}${body.dhcp ? `, DHCP ${body.dhcp}` : ""}` };
    }
    case "location": {
        const now = { zone: o.zone || "", odb: o.odb || "", odb_port: o.odb_port ?? null, seal_number: o.seal_number || "",
                      name: o.name || "", address_or_comment: o.address || "", contact: o.contact || "" };
        const body = { change: "location" }, parts = [];
        const zone = val("cfg-zone"), odb = val("cfg-odb"), port = val("cfg-odb-port");
        if (zone && zone !== now.zone) { body.zone = zone; parts.push(`Zone ${now.zone || "none"} → ${zone}`); }
        if (!odb && now.odb && !body.zone) return { body: null, preview: "SmartOLT's API can't take a splitter off; do that in SmartOLT." };
        if (odb && odb !== now.odb) { body.odb = odb; parts.push(`Splitter ${now.odb || "none"} → ${odb}`); }
        if (port && (Number(port) !== now.odb_port || body.odb)) { body.odb_port = Number(port); parts.push(`Splitter port ${now.odb_port ?? "none"} → ${port}`); }
        for (const [id, key, label] of [["cfg-seal", "seal_number", "Seal number"], ["cfg-name", "name", "Name"],
                                         ["cfg-address", "address_or_comment", "Address or comment"], ["cfg-contact", "contact", "Contact"]]) {
            const v = val(id);
            if (v === now[key]) continue;
            if (key === "name" && !v) return { body: null, preview: "Name can't be empty." };
            body[key] = v;
            parts.push(`${label} ${now[key] || "none"} → ${v || "none"}`);
        }
        return parts.length ? { body, preview: parts.join(" · ") } : { body: null, preview: "No change yet." };
    }
    case "vlans": {
        const want = [...document.querySelectorAll("#olt-config-fields input[data-vlan]:checked")].map(i => Number(i.dataset.vlan));
        const have = o.attached_vlans || [];
        const add = want.filter(v => !have.includes(v)), remove = have.filter(v => !want.includes(v));
        if (!add.length && !remove.length) return { body: null, preview: "No change yet." };
        const parts = [];
        if (add.length) parts.push(`Add VLAN ${add.join(", ")}`);
        if (remove.length) parts.push(`Remove VLAN ${remove.join(", ")}`);
        const used = (o.ethernet_ports || []).filter(p => p.mode === "Access" && remove.includes(Number(p.vlan)));
        if (used.length) parts.push(`⚠ ${used.map(p => p.port).join(", ")} use${used.length === 1 ? "s" : ""} a removed VLAN`);
        return { body: { change: "vlans", vlans: want }, preview: parts.join(" · ") };
    }
    case "mode": {
        const body = { change: "mode" }, parts = [];
        const vlan = Number(val("cfg-wan-vlan"));
        if (vlan && vlan !== Number(o.vlan)) { body.vlan = vlan; parts.push(`WAN VLAN ${o.vlan || "?"} → ${vlan}`); }
        const mode = val("cfg-onu-mode");
        body.onu_mode = mode;
        if (mode !== o.mode) parts.push(`ONU mode ${o.mode || "?"} → ${mode}`);
        $("cfg-wan-block").hidden = mode !== "Routing";
        if (mode === "Routing") {
            const wan = val("cfg-wan"), method = val("cfg-method");
            $("cfg-pppoe").hidden = wan !== "pppoe";
            $("cfg-static").hidden = wan !== "static";
            const wanBody = { wan_mode: wan };
            if (method) wanBody.configuration_method = method;
            let changes = WAN_LABEL[wan] !== o.wan_mode || !!method, extra = "";
            if (wan === "pppoe") {
                const user = val("cfg-user"), pass = $("cfg-pass").value;
                if (!user) return { body: null, preview: "Fill in the PPPoE username." };
                if (!pass && o.wan_mode !== "PPPoE") return { body: null, preview: "Type the PPPoE password." };
                if (user !== (o.pppoe_username || "") || pass) changes = true;
                Object.assign(wanBody, { username: user }, pass ? { password: pass } : {});
                extra = `, username ${user}${pass ? "" : " (same password)"}`;
            } else if (wan === "static") {
                const f = { ipv4_address: val("cfg-ip"), subnet_mask: val("cfg-mask"), gateway: val("cfg-gw"), dns1: val("cfg-dns1") };
                if (Object.values(f).some(v => !v)) return { body: null, preview: "Fill in IP address, subnet mask, gateway and DNS 1." };
                if (val("cfg-dns2")) f.dns2 = val("cfg-dns2");
                Object.assign(wanBody, f);
                changes = true;
                extra = `, ${f.ipv4_address}/${f.subnet_mask} via ${f.gateway}`;
            }
            if (changes) { Object.assign(body, wanBody); parts.push(`WAN mode ${o.wan_mode || "?"} → ${WAN_LABEL[wan]}${extra}`); }
        }
        const remote = val("cfg-remote");
        if (remote) { body.remote_access = remote; parts.push(`WAN remote access ${remote === "enable" ? "on" : "off"}`); }
        return parts.length ? { body, preview: parts.join(" · ") } : { body: null, preview: "No change yet." };
    }
    case "delete": {
        if (linkedTo(o)) return { body: null, preview: "Linked to a customer or hotspot site: unlink it on OLT → Links first." };
        const typed = val("cfg-sn").toUpperCase();
        return typed === (o.sn || "").toUpperCase()
            ? { body: { change: "delete", confirm_sn: typed }, preview: `Delete ONU ${o.sn} (${o.name || "no name"}) from SmartOLT.` }
            : { body: null, preview: `Type ${o.sn} to confirm.` };
    }
    }
    return { body: null, preview: "" };
}

function refreshConfig() {
    const { body, preview } = configRequest();
    $("olt-config-preview").textContent = preview;
    $("olt-config-submit").disabled = !body || cfg.busy;
}

async function openConfig(kind) {
    const o = state.onu;
    if (!o) return;
    if (kind === "resync") return runResync();
    cfg.kind = kind;
    const t = CONFIG_TEXT[kind];
    configMessage("");
    $("olt-config-title").textContent = t.title;
    $("olt-config-where").textContent = `${whoIsOn(o)} · ${o.name || "—"} · ${o.sn || o.id}`;
    $("olt-config-warn").textContent = t.warn;
    $("olt-config-submit").textContent = kind === "delete" ? "Delete" : "Apply";
    $("olt-config-submit").className = kind === "delete" ? "btn-danger" : "btn-primary";
    $("olt-config-cancel").textContent = "Cancel";
    const box = $("olt-config-fields");
    box.replaceChildren();
    $("olt-config-overlay").hidden = false;

    if (kind === "speed") {
        box.append(el("p", "olt-dim olt-auth-wide", "Loading SmartOLT's speed profiles…"));
        refreshConfig();
        try {
            cfg.speed = await api(`/olt/onus/${encodeURIComponent(o.id)}/speed`);
        } catch (e) {
            box.replaceChildren();
            configMessage(`Couldn't load speed profiles: ${e.message}`);
            return;
        }
        if (cfg.kind !== "speed") return;
        const { current, choices } = cfg.speed;
        const pairs = list => list.map(n => [n, n]);
        box.replaceChildren(
            field(`Download (now ${current.download || "?"})`, selectOf("cfg-down", pairs(choices.download), current.download)),
            field(`Upload (now ${current.upload || "?"})`, selectOf("cfg-up", pairs(choices.upload), current.upload)),
        );
    } else if (kind === "port") {
        const ports = o.ethernet_ports || [];
        const vlans = o.attached_vlans || [];
        const vlanWrap = field("VLAN (attached to this ONU)", selectOf("cfg-vlan", [["", "Choose…"], ...vlans.map(v => [String(v), String(v)])]));
        vlanWrap.id = "cfg-vlan-wrap";
        const dhcpWrap = field("DHCP", selectOf("cfg-dhcp", [["", "Leave as is"], ...DHCP_CHOICES.map(d => [d, d])]));
        dhcpWrap.id = "cfg-dhcp-wrap";
        box.append(
            field("Port", selectOf("cfg-port", ports.map(p => [p.port, `${p.port} - now ${portText(p)}`]))),
            field("Mode", selectOf("cfg-mode", Object.entries(PORT_LABEL))),
            vlanWrap, dhcpWrap,
        );
        const syncPort = () => {
            const p = ports.find(x => x.port === val("cfg-port"));
            if (!p) return;
            $("cfg-mode").value = p.admin_state === "Disabled" ? "shutdown" : p.mode === "Access" ? "access" : "lan";
            if (p.vlan && vlans.map(String).includes(p.vlan)) $("cfg-vlan").value = p.vlan;
        };
        $("cfg-port").addEventListener("change", () => { syncPort(); refreshConfig(); });
        syncPort();
    } else if (kind === "location" || kind === "vlans" || kind === "mode") {
        box.append(el("p", "olt-dim olt-auth-wide", "Loading SmartOLT's lists…"));
        refreshConfig();
        let opts;
        try {
            opts = await api(`/olt/authorize-options?olt_id=${encodeURIComponent(o.olt_id || 1)}&pon_type=${encodeURIComponent((o.pon_type || "gpon").toLowerCase())}`);
        } catch (e) {
            box.replaceChildren();
            configMessage(`Couldn't load SmartOLT's lists: ${e.message}`);
            return;
        }
        if (cfg.kind !== kind) return;
        box.replaceChildren();
        if (kind === "location") buildLocation(box, o, opts);
        else if (kind === "vlans") buildVlans(box, o, opts);
        else buildMode(box, o, opts);
    } else if (kind === "delete") {
        box.append(field(`Type the serial (${o.sn}) to confirm`, inputOf("cfg-sn", "text", "", { maxLength: 64 }), true));
    }
    refreshConfig();
    box.querySelector("select, input")?.focus();
}

function vlanLabel(v) {
    return `${v.vlan}${v.description ? ` - ${v.description}` : ""}`;
}

function buildLocation(box, o, opts) {
    const zones = opts.zones || [];
    const zoneSel = selectOf("cfg-zone", [...(o.zone && !zones.some(z => z.name === o.zone) ? [[o.zone, o.zone]] : []),
                                          ...zones.map(z => [z.name, z.name])], o.zone || "");
    const odbSel = selectOf("cfg-odb", [["", "None"]]);
    const portSel = selectOf("cfg-odb-port", [["", "None"]]);
    let splitters = [];
    const fillPorts = keep => {
        const sp = splitters.find(x => x.name === val("cfg-odb"));
        const n = sp?.ports || 0;
        portSel.replaceChildren(option("", "None"), ...Array.from({ length: n }, (_, i) => option(String(i + 1), String(i + 1))));
        if (keep && o.odb_port) portSel.value = String(o.odb_port);
    };
    const loadSplitters = async keep => {
        const z = zones.find(x => x.name === val("cfg-zone"));
        odbSel.replaceChildren(option("", "Loading…"));
        try { splitters = z ? (await api(`/olt/zones/${z.id}/odbs`)).odbs || [] : []; } catch { splitters = []; }
        odbSel.replaceChildren(option("", "None"), ...splitters.map(x => option(x.name, x.name)));
        if (keep && o.odb && splitters.some(x => x.name === o.odb)) odbSel.value = o.odb;
        fillPorts(keep);
        refreshConfig();
    };
    zoneSel.addEventListener("change", () => loadSplitters(false));
    odbSel.addEventListener("change", () => fillPorts(false));
    box.append(
        field("Zone", zoneSel), field("Splitter", odbSel), field("Splitter port", portSel),
        field("Seal number", inputOf("cfg-seal", "text", o.seal_number || "", { maxLength: 64 })),
        field("Name", inputOf("cfg-name", "text", o.name || "", { maxLength: 120 })),
        field("Address or comment", inputOf("cfg-address", "text", o.address || "", { maxLength: 120 })),
        field("Contact", inputOf("cfg-contact", "text", o.contact || "", { maxLength: 120 }), true),
    );
    loadSplitters(true);
}

function buildVlans(box, o, opts) {
    const have = o.attached_vlans || [];
    const all = [...(opts.vlans || [])];
    for (const v of have) if (!all.some(x => x.vlan === v)) all.push({ vlan: v });
    all.sort((a, b) => a.vlan - b.vlan);
    const list = el("div", "olt-vlan-list olt-auth-wide");
    for (const v of all) {
        const l = el("label", "olt-vlan");
        const cb = el("input");
        cb.type = "checkbox";
        cb.dataset.vlan = String(v.vlan);
        cb.checked = have.includes(v.vlan);
        const wan = Number(o.vlan) === v.vlan;
        if (wan) { cb.disabled = true; cb.title = "The WAN VLAN: change it under ONU mode first"; }
        l.append(cb, document.createTextNode(` ${vlanLabel(v)}${wan ? " (WAN)" : ""}`));
        list.append(l);
    }
    box.append(el("p", "olt-dim olt-auth-wide", "Tick every VLAN this ONU should carry."), list);
}

function buildMode(box, o, opts) {
    const vlans = [...(opts.vlans || [])];
    if (o.vlan && !vlans.some(v => v.vlan === Number(o.vlan))) vlans.push({ vlan: Number(o.vlan) });
    vlans.sort((a, b) => a.vlan - b.vlan);
    const now = Object.entries(WAN_LABEL).find(([, l]) => l === o.wan_mode)?.[0] || "webpage";
    const wanBlock = el("div", "olt-config-fields olt-auth-wide");
    wanBlock.id = "cfg-wan-block";
    const pppoe = el("div", "olt-config-fields olt-auth-wide");
    pppoe.id = "cfg-pppoe";
    const user = o.pppoe_username || (o.customer && !o.customer.site && !o.customer.not_a_customer ? o.customer.username : "") || "";
    pppoe.append(
        field("PPPoE username", inputOf("cfg-user", "text", user, { maxLength: 64 })),
        field("PPPoE password", inputOf("cfg-pass", "password", "", { maxLength: 64, autocomplete: "new-password",
              placeholder: o.wan_mode === "PPPoE" ? "Leave empty to keep the current one" : "" })),
    );
    const stat = el("div", "olt-config-fields olt-auth-wide");
    stat.id = "cfg-static";
    stat.append(
        field("IP address", inputOf("cfg-ip", "text", o.wan_mode === "Static IP" ? (o.ip_address || "") : "", { maxLength: 40 })),
        field("Subnet mask", inputOf("cfg-mask", "text", "", { maxLength: 40, placeholder: "255.255.255.0" })),
        field("Gateway", inputOf("cfg-gw", "text", "", { maxLength: 40 })),
        field("DNS 1", inputOf("cfg-dns1", "text", "", { maxLength: 40 })),
        field("DNS 2 (optional)", inputOf("cfg-dns2", "text", "", { maxLength: 40 })),
    );
    wanBlock.append(
        field(`WAN mode (now ${o.wan_mode || "?"})`, selectOf("cfg-wan", Object.entries(WAN_LABEL), now)),
        field("Configuration method", selectOf("cfg-method", [["", "SmartOLT's default"], ["OMCI", "OMCI"], ["TR069", "TR069"]])),
        pppoe, stat,
    );
    box.append(
        field("WAN VLAN-ID", selectOf("cfg-wan-vlan", vlans.map(v => [String(v.vlan), vlanLabel(v)]), o.vlan ? String(Number(o.vlan)) : "")),
        field("ONU mode", selectOf("cfg-onu-mode", [["Routing", "Routing"], ["Bridging", "Bridging"]], o.mode || "Routing")),
        wanBlock,
        field("WAN remote access", selectOf("cfg-remote", [["", "Leave as is"], ["enable", "Enabled"], ["disable", "Disabled"]])),
    );
}

function closeConfig() {
    if (cfg.busy) return;
    $("olt-config-overlay").hidden = true;
    const pass = $("cfg-pass");
    if (pass) pass.value = "";
    cfg.kind = null;
    cfg.speed = null;
}

async function sendConfig(body) {
    const o = state.onu;
    return api(`/olt/onus/${encodeURIComponent(o.id)}/config`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

function showResult(kind, row) {
    const [label] = RESULT[row.result] || [row.result, ""];
    actionStatus(`${CONFIG_TEXT[kind].verb} - ${label}: ${row.response || ""}`, row.result === "succeeded" ? "good" : "bad");
}

async function afterConfig(kind, row, id) {
    if (kind === "delete" && row.result === "succeeded") {
        state.list = null;
        setTimeout(() => navigate("olt"), 2500);
        return;
    }
    loadOnuActions(id);
    setTimeout(() => { if (state.onuId === id) openOnu(id, { keepStatus: true }); }, 1200);
}

async function submitConfig(e) {
    e.preventDefault();
    const o = state.onu, kind = cfg.kind;
    const { body, preview } = configRequest();
    if (!o || !body || cfg.busy) return;
    const t = CONFIG_TEXT[kind];
    const ok = await confirmDialog({
        title: `${t.title}?`,
        message: `${whoIsOn(o)}\nONU: ${o.name || "—"}\nSerial: ${o.sn || o.id}\n\n${preview}\n\n${t.warn}`,
        confirmLabel: kind === "delete" ? "Delete" : "Apply",
        danger: kind === "delete" || kind === "mode",
    });
    if (!ok) return;
    cfg.busy = true;
    $("olt-config-submit").disabled = true;
    $("olt-config-cancel").disabled = true;
    configMessage("Sending to SmartOLT… the OLT can take up to a minute.", "warn");
    try {
        const row = await sendConfig(body);
        cfg.busy = false;
        $("olt-config-cancel").disabled = false;
        closeConfig();
        showResult(kind, row);
        afterConfig(kind, row, o.id);
    } catch (err) {
        cfg.busy = false;
        $("olt-config-cancel").disabled = false;
        configMessage(`Not done: ${err.message}`);
        refreshConfig();
        loadOnuActions(o.id);
    }
}

async function runResync() {
    const o = state.onu;
    const t = CONFIG_TEXT.resync;
    const ok = await confirmDialog({
        title: "Resync this ONU's config?",
        message: `${whoIsOn(o)}\nONU: ${o.name || "—"}\nSerial: ${o.sn || o.id}\n\n${o.resync_failed ? "SmartOLT says its last resync failed.\n\n" : ""}${t.warn}`,
        confirmLabel: "Resync",
    });
    if (!ok) return;
    document.querySelectorAll("#olt-config-buttons button, #olt-action-buttons button").forEach(b => { b.disabled = true; });
    actionStatus("Resyncing… the OLT can take up to a minute.");
    try {
        const row = await sendConfig({ change: "resync" });
        showResult("resync", row);
        afterConfig("resync", row, o.id);
    } catch (err) {
        actionStatus(`Not done: ${err.message}`, "bad");
        renderActionButtons(o);
        loadOnuActions(o.id);
    }
}

// ---- LOS alerts ----

function losCard(a, compact = false) {
    const card = el("div", `olt-los-card${a.acked_at ? " is-acked" : ""}`);
    const top = el("div", "olt-los-top");
    const title = el("div", "olt-los-who", `▼ ${a.who}${a.pon ? `: ${a.count} ONUs without fibre` : " — LOS"}`);
    const since = el("div", "olt-dim", `since ${when(a.started_at)} · ${duration(Math.floor((Date.now() - new Date(a.started_at)) / 1000))}`);
    top.append(title, since);
    card.append(top);
    if (a.pon && a.members.length) card.append(el("div", "olt-dim", a.members.join(", ")));
    const foot = el("div", "olt-los-foot");
    if (a.acked_at) {
        foot.append(el("span", "olt-dim", `Acknowledged by ${a.acked_by || "someone"} ${when(a.acked_at)}${a.ack_note ? ` · ${a.ack_note}` : ""}. Reminders at 08:00 and 16:00 until it's back.`));
    } else {
        foot.append(el("span", "olt-dim", `${a.messages_sent ? `${a.messages_sent} alert${a.messages_sent === 1 ? "" : "s"} sent` : "First alert after 2 minutes"}.`));
        const b = button("Acknowledge", "btn-secondary olt-small", { ack: a.id });
        foot.append(b);
    }
    if (!compact && !a.pon) {
        const open = el("a", "olt-los-open", "Open ONU");
        open.href = `#/olt/onu/${encodeURIComponent(a.onu)}`;
        foot.append(open);
    }
    card.append(foot);
    return card;
}

async function loadLos() {
    try {
        const { alerts } = await api("/olt/alerts");
        state.los = alerts;
        $("olt-los").hidden = !alerts.length;
        $("olt-los-sub").textContent = alerts.length ? `${alerts.length} open` : "";
        $("olt-los-list").replaceChildren(...alerts.map(a => losCard(a)));
        renderOnuLos();
    } catch { /* the list below still works without it */ }
}

function renderOnuLos() {
    const box = $("olt-onu-los");
    const a = state.onuId && (state.los || []).find(x => x.onu === state.onuId && !x.pon);
    box.hidden = !a;
    box.replaceChildren(...(a ? [losCard(a, true)] : []));
}

async function ackLos(id) {
    const ok = await confirmDialog({
        title: "Acknowledge this LOS?",
        message: "Someone is dealing with it. The fast reminders stop; it will only be brought up at 08:00 and 16:00 until the fibre is back.",
        confirmLabel: "Acknowledge",
    });
    if (!ok) return;
    try {
        await api(`/olt/alerts/${id}/ack`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    } catch (e) {
        $("olt-stale").hidden = false;
        $("olt-stale").textContent = e.message;
    }
    loadLos();
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
    $("olt-links-panel").hidden = tab !== "customers";
    // Every tab's data loads either way, so each tab's count is right.
    loadList();
    loadLos();
    loadUnconfigured();
    loadLinks();
}

function stopPolling() {
    clearInterval(state.timer);
    state.timer = null;
}

function startPolling() {
    stopPolling();
    state.timer = setInterval(() => {
        if (document.hidden || $("view-olt").hidden) { stopPolling(); return; }
        if (!state.onuId && state.tab === "configured") { loadList(); loadLos(); }
    }, LIST_MS);
}

document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !$("view-olt").hidden && !state.onuId) { loadList(); startPolling(); }
});

export function initOlt() {
    document.querySelector(".olt-tabs").addEventListener("click", e => {
        const b = e.target.closest(".olt-tab");
        if (b) navigate(b.dataset.tab === "configured" ? "olt" : `olt/${b.dataset.tab}`);
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
    for (const id of ["olt-los-list", "olt-onu-los"]) {
        $(id).addEventListener("click", e => {
            const b = e.target.closest("button[data-ack]");
            if (b) ackLos(b.dataset.ack);
        });
    }
    $("olt-action-buttons").addEventListener("click", e => {
        const b = e.target.closest("button[data-onu-action]");
        if (b && !b.disabled) runAction(b.dataset.onuAction);
    });
    $("olt-config-buttons").addEventListener("click", e => {
        const b = e.target.closest("button[data-onu-config]");
        if (b && !b.disabled) openConfig(b.dataset.onuConfig);
    });
    for (const id of ["olt-onu-facts", "olt-onu-service"]) {
        $(id).addEventListener("click", e => {
            const b = e.target.closest("button[data-onu-config]");
            if (b) openConfig(b.dataset.onuConfig);
            if (e.target.closest("#olt-pppoe-eye")) togglePassword();
        });
    }
    $("olt-config-form").addEventListener("input", refreshConfig);
    $("olt-config-form").addEventListener("change", refreshConfig);
    $("olt-config-form").addEventListener("submit", submitConfig);
    $("olt-config-cancel").addEventListener("click", closeConfig);
    $("olt-graph-range").addEventListener("click", e => {
        const b = e.target.closest("button[data-period]");
        if (!b || !state.onuId) return;
        state.period = b.dataset.period;
        document.querySelectorAll("#olt-graph-range button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
        loadGraphs();
    });
    $("olt-links-panel").addEventListener("click", onLinksClick);
    $("olt-accept-all").addEventListener("click", acceptAll);
    $("olt-link-search").addEventListener("input", () => {
        if (linkMode === "site") { showSites(); return; }
        clearTimeout(linkTimer); linkTimer = setTimeout(searchLinkCustomers, 250);
    });
    $("olt-accept-sites").addEventListener("click", acceptAllSites);
    $("olt-link-cancel").addEventListener("click", closeLinkPicker);
    $("olt-link-overlay").addEventListener("click", e => { if (e.target === $("olt-link-overlay")) closeLinkPicker(); });
    $("olt-auth-type").addEventListener("change", syncMode);
    $("olt-auth-zone").addEventListener("change", loadOdbs);
    $("olt-auth-form").addEventListener("submit", submitAuthorize);
    $("olt-auth-cancel").addEventListener("click", closeAuthorize);
    $("olt-auth-overlay").addEventListener("click", e => { if (e.target === $("olt-auth-overlay")) closeAuthorize(); });
    document.addEventListener("keydown", e => {
        // The confirm dialog handles its own Escape first (capture phase).
        if (e.key === "Escape" && !$("olt-auth-overlay").hidden) closeAuthorize();
        if (e.key === "Escape" && !$("olt-link-overlay").hidden) closeLinkPicker();
        if (e.key === "Escape" && !$("olt-config-overlay").hidden) closeConfig();
    });

    registerRoute("olt", (params) => {
        showView("view-olt");
        if (params[0] === "onu" && params[1]) {
            stopPolling();
            if (!state.list) loadList();
            openOnu(decodeURIComponent(params[1]));
            return;
        }
        showTab(["unconfigured", "customers"].includes(params[0]) ? params[0] : "configured");
        startPolling();
    });
}
