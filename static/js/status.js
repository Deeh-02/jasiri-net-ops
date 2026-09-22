import { authHeaders, showView, navigate, registerRoute } from "./common.js";

const POLL_MS = 30000;
// The router beats every 60s, so a gap this long means we have stopped being
// told, not that everything is fine. Three misses, to ride out one slow poll.
const STALE_MS = 3 * 60 * 1000;
// The customer list rides only every 5th heartbeat (usersEvery in
// ops-heartbeat.rsc), so the same three-misses rule is 15 minutes here. A
// heartbeat can keep arriving perfectly while this one is dead — that is
// exactly what happened on 2026-09-21, and why it gets its own indicator
// instead of sharing the one above.
const SALES_STALE_MS = 15 * 60 * 1000;

let pollTimer = null;

/* Colour alone does not survive a greyscale screenshot or a colour-blind
   reader, so every state carries a shape and a word too. */
/* Glyphs match the site report exactly — ▲ means Down on both pages. One
   scheme app-wide, or a screenshot from one page contradicts the other. */
const STATE_INFO = {
    online:   { shape: "●", label: "Online",      cls: "st-online" },
    offline:  { shape: "▲", label: "Down",        cls: "st-offline" },
    flapping: { shape: "◆", label: "Flapping",    cls: "st-flapping" },
    unknown:  { shape: "○", label: "Not watched", cls: "st-unknown" },
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
    if (word === "person") return `${n} ${n === 1 ? "person" : "people"}`;
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

function readout(label, value) {
    return `<div class="status-readout">
        <div class="status-readout-label">${esc(label)}</div>
        <div class="status-readout-value">${esc(value)}</div>
    </div>`;
}

function renderHero(data, stale) {
    const hero = document.getElementById("status-hero");
    const hasProblem = (data.counts.offline || 0) + (data.counts.flapping || 0) > 0;

    hero.className = "status-hero" + (stale ? " is-stale" : hasProblem ? " has-problem" : "");

    const people = data.sites.reduce((sum, site) => sum + (site.sessions || 0), 0);
    const busiest = data.sites.reduce(
        (best, site) => ((site.sessions || 0) > (best.sessions || 0) ? site : best),
        { sessions: 0, name: "\u2013" });

    const sub = stale
        ? "The last report is too old to trust \u2014 these numbers may have moved"
        : `Router reported ${ago(data.last_ingest_at)}`;

    // The right-hand side carries what the cards below do NOT: the cards
    // count sites, these count people.
    const readouts = readout("People online", people)
        + readout("Busiest site", busiest.sessions ? `${busiest.name} (${busiest.sessions})` : "\u2013");

    hero.innerHTML = `
        <div class="status-hero-main">
            <div class="status-hero-line">${esc(heroSentence(data.counts, stale))}</div>
            <div class="status-hero-sub">${esc(sub)}</div>
        </div>
        <div class="status-hero-side">${readouts}</div>`;
}

function renderProblems(sites) {
    const problems = sites.filter(s => s.state === "offline" || s.state === "flapping");
    const box = document.getElementById("status-problems");
    box.innerHTML = problems.map(s => `
        <div class="status-problem ${s.state === "flapping" ? "is-flapping" : ""}" data-id="${s.id}">
            <div class="status-problem-top">
                <span class="status-problem-name">${esc(s.name)}</span>
                ${pill(s.state)}
            </div>
            <div class="status-problem-figure">${s.sessions == null ? "–" : s.sessions}</div>
            <div class="status-problem-meta">online at last count &middot; ${esc(ago(s.state_since))}</div>
        </div>`).join("");
    box.querySelectorAll(".status-problem").forEach(card => {
        card.addEventListener("click", () => navigate(`site-detail/${card.dataset.id}`));
    });
}

/* Revenue is the one number on this page that is not observed continuously:
   it moves only when a customer list arrives. So it carries its own age,
   always \u2014 a total with no timestamp cannot be told apart from a stale one. */
function salesFeedAge(data) {
    const feed = data.revenue_feed;
    if (!feed) return { known: false, stale: false, text: "" };
    if (!feed.last_users_at) return { known: false, stale: true, text: "no list yet" };
    const ms = Date.now() - new Date(feed.last_users_at).getTime();
    return { known: true, stale: ms > SALES_STALE_MS, text: `checked ${ago(feed.last_users_at)}` };
}

function renderSalesAlert(data, canRevenue) {
    const box = document.getElementById("status-sales-alert");
    const feed = salesFeedAge(data);

    if (!canRevenue || !data.revenue_feed || !feed.stale) {
        box.hidden = true;
        box.innerHTML = "";
        return;
    }

    // Says what is wrong with the NUMBER, not what is wrong with the router \u2014
    // the failure this exists to catch leaves the router looking perfect.
    const line = feed.known
        ? `The router last sent a customer list ${ago(data.revenue_feed.last_users_at)}.`
        : "No customer list has reached Ops yet.";

    box.hidden = false;
    box.innerHTML = `
        <div class="status-alert-title">Sales may be behind</div>
        <div class="status-alert-body">${esc(line)} Sites and headcounts below are unaffected \u2014 they arrive every minute. Revenue only moves when that list does.</div>`;
}

/* Five cards, same shape as the Dashboard's: the four states, then money.
   Zeros are kept rather than hidden -- on this page a zero in Down is the
   reassurance, and a card that comes and goes is one you stop trusting. */
function renderTotals(data, canRevenue) {
    const c = data.counts;
    const feed = salesFeedAge(data);
    const cards = [
        { label: "Online", value: c.online || 0, cls: "charged" },
        { label: "Down", value: c.offline || 0, cls: "low" },
        { label: "Flapping", value: c.flapping || 0, cls: "deployed" },
        { label: "Unknown", value: c.unknown || 0, cls: "unknown" },
        {
            label: "Revenue today",
            value: canRevenue ? money(data.revenue_today_kes) : "\uD83D\uDD12 Hidden",
            cls: "",
            small: !canRevenue,
            caption: canRevenue && data.revenue_feed ? feed.text : "",
            captionStale: feed.stale,
            /* The total includes sales Ops could not place at a site; this
               says how much of it that is. Hidden at zero — on a good day
               every sale lands somewhere, and a line reading "KES 0 unplaced"
               every day is noise you learn to scroll past. The sites below
               will not add up to the total while this is showing, and that
               is the thing it exists to explain. */
            subcaption: canRevenue && data.revenue_unplaced_kes
                ? `${money(data.revenue_unplaced_kes)} not tied to a site yet`
                : "",
        },
    ];

    document.getElementById("status-totals").innerHTML = cards.map(card => `
        <div class="stat-card ${card.cls}">
            <div class="stat-label">${card.label}</div>
            <div class="stat-value ${card.small ? "is-small" : ""}">${esc(card.value)}</div>
            ${card.caption ? `<div class="stat-caption ${card.captionStale ? "is-stale" : ""}">${esc(card.caption)}</div>` : ""}
            ${card.subcaption ? `<div class="stat-caption is-unplaced">${esc(card.subcaption)}</div>` : ""}
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

    // Bars are relative to the busiest site, so the widths mean something
    // on a quiet network as well as a loaded one.
    const busiest = Math.max(...sites.map(site => site.sessions || 0));

    tbody.innerHTML = ordered.map(s => {
        const rowCls = s.state === "offline" ? "status-row-down"
            : s.state === "flapping" ? "status-row-flapping" : "";
        const revenue = canRevenue
            ? `<span class="status-revenue ${s.revenue_today_kes ? "has-value" : ""}">${money(s.revenue_today_kes)}</span>`
            : `<span class="status-revenue-locked">&#128274; Hidden</span>`;
        return `
        <tr class="${rowCls} status-row-clickable" data-id="${s.id}">
            <td class="col-frozen">${esc(s.name)}</td>
            <td>${pill(s.state)}</td>
            <td><span class="status-sessions">${s.sessions == null ? "–" : s.sessions}</span></td>
            <td><span class="status-since">${esc(ago(s.state_since))}</span></td>
            <td>${revenue}</td>
        </tr>`;
    }).join("");
    // One delegated listener rather than one per row — rows are rebuilt
    // wholesale every poll, which would otherwise leak a listener per tick.
    tbody.onclick = (e) => {
        const row = e.target.closest("tr[data-id]");
        if (row) navigate(`site-detail/${row.dataset.id}`);
    };
}

function render(data) {
    // Absent, not null: the key only exists with sites:view_revenue.
    const canRevenue = "revenue_today_kes" in data;
    const lastMs = data.last_ingest_at ? new Date(data.last_ingest_at).getTime() : 0;
    const stale = !lastMs || Date.now() - lastMs > STALE_MS;

    renderHero(data, stale);
    renderProblems(data.sites);
    renderSalesAlert(data, canRevenue);
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
