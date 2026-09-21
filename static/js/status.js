import { authHeaders, showView, navigate, registerRoute } from "./common.js";

const POLL_MS = 30000;
// Stale if no snapshot for this long: the router beats every 60s.
const STALE_MS = 3 * 60 * 1000;
let pollTimer = null;

// Colour + shape + text, always: a screenshot in WhatsApp loses the colour.
const STATE_INFO = {
    online:   { shape: "●", label: "Online",   cls: "st-online" },
    flapping: { shape: "▲", label: "Flapping", cls: "st-flapping" },
    offline:  { shape: "■", label: "Offline",  cls: "st-offline" },
    unknown:  { shape: "○", label: "Unknown",  cls: "st-unknown" },
};

function esc(v) {
    const d = document.createElement("div");
    d.textContent = v == null ? "" : String(v);
    return d.innerHTML;
}

function ago(iso) {
    if (!iso) return "never";
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    const h = Math.floor(mins / 60);
    if (h < 24) return `${h} h ago`;
    return `${Math.floor(h / 24)} d ago`;
}

function plural(n, word) {
    return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function heroSentence(counts, stale) {
    if (stale) return "No news from the router";
    const down = counts.offline || 0;
    const flap = counts.flapping || 0;
    if (down) return `${plural(down, "site")} down`;
    if (flap) return `${plural(flap, "site")} flapping`;
    return "All sites up";
}

function stateBadge(state) {
    const i = STATE_INFO[state] || STATE_INFO.unknown;
    return `<span class="st-badge ${i.cls}"><span class="st-shape" aria-hidden="true">${i.shape}</span>${i.label}</span>`;
}

function revenueCell(site, canRevenue) {
    if (!canRevenue) return `<span class="st-revenue-locked">🔒 Hidden</span>`;
    return `<span class="st-revenue">KES ${Number(site.revenue_today_kes || 0).toLocaleString()}</span>`;
}

function render(data) {
    const canRevenue = "revenue_today_kes" in data;
    const lastMs = data.last_ingest_at ? new Date(data.last_ingest_at).getTime() : 0;
    const stale = !lastMs || Date.now() - lastMs > STALE_MS;

    const hero = document.getElementById("status-hero");
    const problem = (data.counts.offline || 0) + (data.counts.flapping || 0) > 0 || stale;
    hero.className = "status-hero" + (problem ? " has-problem" : "");
    hero.innerHTML = `
        <div class="status-hero-text">${esc(heroSentence(data.counts, stale))}</div>
        <div class="status-hero-sub">Last report from the router: ${esc(ago(data.last_ingest_at))}</div>`;

    const problems = data.sites.filter(s => s.state === "offline" || s.state === "flapping");
    document.getElementById("status-problems").innerHTML = problems.map(s => `
        <div class="status-problem-card ${(STATE_INFO[s.state] || STATE_INFO.unknown).cls}">
            <div class="status-problem-name">${esc(s.name)}</div>
            ${stateBadge(s.state)}
            <div class="status-problem-meta">Since ${esc(ago(s.state_since))} · ${plural(s.sessions || 0, "session")} at last count</div>
        </div>`).join("");

    const c = data.counts;
    const totals = [
        ["Online", c.online || 0, "charged"],
        ["Offline", c.offline || 0, "low"],
        ["Flapping", c.flapping || 0, "deployed"],
        ["Unknown", c.unknown || 0, ""],
    ];
    if (canRevenue) totals.push(["Revenue today", `KES ${Number(data.revenue_today_kes).toLocaleString()}`, ""]);
    else totals.push(["Revenue today", "🔒 Hidden", ""]);
    // Calm day: the totals still show, but the problem area collapses to nothing.
    document.getElementById("status-totals").innerHTML = totals.map(([label, value, cls]) => `
        <div class="stat-card ${cls}"><div class="stat-label">${label}</div><div class="stat-value">${value}</div></div>`).join("");

    document.getElementById("status-list").innerHTML = data.sites.map(s => `
        <div class="status-row ${(STATE_INFO[s.state] || STATE_INFO.unknown).cls}">
            <span class="status-row-name">${esc(s.name)}</span>
            ${stateBadge(s.state)}
            <span class="status-row-sessions">${s.sessions == null ? "–" : s.sessions} online</span>
            ${revenueCell(s, canRevenue)}
        </div>`).join("");
}

function setConn(ok, reason) {
    const el = document.getElementById("status-conn");
    el.textContent = ok ? "Live · updates every 30 s" : `Connection lost — showing old data (${reason})`;
    el.className = "status-conn" + (ok ? "" : " lost");
    document.getElementById("view-status").classList.toggle("dimmed", !ok);
}

async function load() {
    try {
        const res = await fetch("/monitoring/status", { headers: authHeaders() });
        if (!res.ok) {
            setConn(false, res.status === 403 ? "no permission" : `server said ${res.status}`);
            return;
        }
        render(await res.json());
        setConn(true);
    } catch (err) {
        console.error("status tab:", err);
        setConn(false, err && err.message ? err.message : "unknown error");
    }
}

function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
}

export function initStatus() {
    document.getElementById("status-link-btn").addEventListener("click", () => navigate("status"));

    registerRoute("status", () => {
        showView("view-status");
        load();
        stopPolling();
        pollTimer = setInterval(() => {
            if (document.getElementById("view-status").hidden) { stopPolling(); return; }
            load();
        }, POLL_MS);
    });
}
