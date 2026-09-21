import { authHeaders, showView, navigate, registerRoute } from "./common.js";

const POLL_MS = 30000;
// The router beats every 60s, so a gap this long means we have stopped being
// told, not that everything is fine. Three misses, to ride out one slow poll.
const STALE_MS = 3 * 60 * 1000;

let pollTimer = null;

/* Colour alone does not survive a greyscale screenshot or a colour-blind
   reader, so every state carries a shape and a word too. */
const STATE_INFO = {
    online:   { shape: "●", label: "Online",   cls: "st-online" },
    flapping: { shape: "▲", label: "Flapping", cls: "st-flapping" },
    offline:  { shape: "■", label: "Down",     cls: "st-offline" },
    unknown:  { shape: "○", label: "Unknown",  cls: "st-unknown" },
};

function info(state) {
    return STATE_INFO[state] || STATE_INFO.unknown;
}

function esc(value) {
    const d = document.createElement("div");
    d.textContent = value == null ? "" : String(value);
    return d.innerHTML;
}

function plural(n, word) {
    return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function ago(iso) {
    if (!iso) return "never";
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
}

function pill(state) {
    const i = info(state);
    return `<span class="status-pill ${i.cls}"><span class="st-shape" aria-hidden="true">${i.shape}</span>${i.label}</span>`;
}

function money(kes) {
    return `KES ${Number(kes || 0).toLocaleString()}`;
}

/* The hero is a SENTENCE, not a number: "2 sites down" is readable by
   someone who has never been shown what the colours mean. */
function heroSentence(counts, stale) {
    if (stale) return "No word from the router";
    const down = counts.offline || 0;
    const flapping = counts.flapping || 0;
    if (down && flapping) return `${plural(down, "site")} down, ${flapping} flapping`;
    if (down) return `${plural(down, "site")} down`;
    if (flapping) return `${plural(flapping, "site")} flapping`;
    return "All sites up";
}

function renderHero(data, stale) {
    const hero = document.getElementById("status-hero");
    const hasProblem = (data.counts.offline || 0) + (data.counts.flapping || 0) > 0;

    hero.className = "status-hero" + (stale ? " is-stale" : hasProblem ? " has-problem" : "");

    const online = data.counts.online || 0;
    const detail = stale
        ? "The last report is too old to trust — these numbers may have moved"
        : `${online} of ${plural(data.sites.length, "site")} reporting sessions`;

    hero.innerHTML = `
        <div class="status-hero-line">${esc(heroSentence(data.counts, stale))}</div>
        <div class="status-hero-sub">${esc(detail)} &middot; router reported ${esc(ago(data.last_ingest_at))}</div>`;
}

function renderProblems(sites) {
    const problems = sites.filter(s => s.state === "offline" || s.state === "flapping");
    document.getElementById("status-problems").innerHTML = problems.map(s => `
        <div class="status-problem ${s.state === "flapping" ? "is-flapping" : ""}">
            <div class="status-problem-top">
                <span class="status-problem-name">${esc(s.name)}</span>
                ${pill(s.state)}
            </div>
            <div class="status-problem-figure">${s.sessions == null ? "–" : s.sessions}</div>
            <div class="status-problem-meta">online at last count &middot; ${esc(ago(s.state_since))}</div>
        </div>`).join("");
}

function renderTotals(data, canRevenue) {
    const c = data.counts;
    const cards = [
        { label: "Online", value: c.online || 0, cls: "charged" },
        { label: "Down", value: c.offline || 0, cls: "low" },
        { label: "Flapping", value: c.flapping || 0, cls: "deployed" },
        { label: "Unknown", value: c.unknown || 0, cls: "unknown" },
        {
            label: "Revenue today",
            value: canRevenue ? money(data.revenue_today_kes) : "\uD83D\uDD12 Hidden",
            cls: "",
        },
    ];
    document.getElementById("status-totals").innerHTML = cards.map(card => `
        <div class="stat-card ${card.cls}">
            <div class="stat-label">${card.label}</div>
            <div class="stat-value">${esc(card.value)}</div>
        </div>`).join("");
}

function renderRows(sites, canRevenue) {
    const tbody = document.getElementById("status-rows");

    if (sites.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="loading-text">No monitored sites yet.</td></tr>`;
        return;
    }

    /* Problems first, then the calm ones — the same exception-first ordering
       as the cards above, so the table does not bury a down site in the Ms. */
    const rank = { offline: 0, flapping: 1, unknown: 2, online: 3 };
    const ordered = [...sites].sort((a, b) => {
        const diff = (rank[a.state] ?? 9) - (rank[b.state] ?? 9);
        return diff !== 0 ? diff : a.name.localeCompare(b.name);
    });

    tbody.innerHTML = ordered.map(s => {
        const rowCls = s.state === "offline" ? "status-row-down"
            : s.state === "flapping" ? "status-row-flapping" : "";
        const revenue = canRevenue
            ? `<span class="status-revenue ${s.revenue_today_kes ? "has-value" : ""}">${money(s.revenue_today_kes)}</span>`
            : `<span class="status-revenue-locked">&#128274; Hidden</span>`;
        return `
        <tr class="${rowCls}">
            <td class="col-frozen">${esc(s.name)}</td>
            <td>${pill(s.state)}</td>
            <td><span class="status-sessions">${s.sessions == null ? "–" : s.sessions}</span></td>
            <td><span class="status-since">${esc(ago(s.state_since))}</span></td>
            <td>${revenue}</td>
        </tr>`;
    }).join("");
}

function render(data) {
    // Absent, not null: the key only exists with sites:view_revenue.
    const canRevenue = "revenue_today_kes" in data;
    const lastMs = data.last_ingest_at ? new Date(data.last_ingest_at).getTime() : 0;
    const stale = !lastMs || Date.now() - lastMs > STALE_MS;

    renderHero(data, stale);
    renderProblems(data.sites);
    renderTotals(data, canRevenue);
    renderRows(data.sites, canRevenue);
}

function setFeed(ok, reason) {
    const feed = document.getElementById("status-feed");
    feed.className = "status-feed" + (ok ? "" : " lost");
    document.getElementById("status-feed-text").textContent = ok ? "Live" : `Offline — ${reason}`;
    document.getElementById("view-status").classList.toggle("stale-feed", !ok);
}

async function load() {
    try {
        const res = await fetch("/monitoring/status", { headers: authHeaders() });
        if (!res.ok) {
            setFeed(false, res.status === 403 ? "no permission" : `server said ${res.status}`);
            return;
        }
        render(await res.json());
        setFeed(true);
    } catch (err) {
        console.error("status tab:", err);
        setFeed(false, "no connection");
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
            // Navigating away hides the view but does not unregister the
            // timer; polling a page nobody is looking at wakes the free-tier
            // dyno for nothing.
            if (document.getElementById("view-status").hidden) {
                stopPolling();
                return;
            }
            load();
        }, POLL_MS);
    });
}
