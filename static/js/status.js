import { authHeaders, showView, navigate, registerRoute, can } from "./common.js";

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
// Below this, a site's 30-day uptime goes amber in the table — a look-back
// flag, not a live alert (the row's own status pill still says whether it's
// actually up right now). Deliberately loose while the fleet is still
// getting reliable: 85%, not the 98% target we're working towards. Raise
// this as real uptime improves — it should tighten, not stay a permanent 85.
const UPTIME_WARN_PCT = 85;

let pollTimer = null;
// The site whose Acknowledge form is open. While it is, polling leaves the
// problem cards alone — a re-render every 30s would wipe a half-typed note.
let ackEditing = null;
let lastData = null;

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

function pill(state, label) {
    const i = info(state);
    return `<span class="status-pill ${i.cls}"><span class="st-shape" aria-hidden="true">${i.shape}</span>${esc(label || i.label)}</span>`;
}

function money(kes) {
    return `KES ${Number(kes || 0).toLocaleString()}`;
}

/* "27m", "1h 38m", "2d 4h" — the length of a state is what decides how
   urgent it is, so it goes in the label rather than behind a click. */
function span(iso) {
    if (!iso) return "";
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (mins < 60) return `${mins}m`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ${mins % 60}m`;
    return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

/* An acknowledged site keeps its real state everywhere — table, uptime,
   the Down card — and only leaves the headline and the problem cards. */
function isAcked(s) {
    return !!s.ack && (s.state === "offline" || s.state === "flapping");
}

function headlineCounts(data) {
    const c = { ...data.counts };
    const acked = data.sites.filter(isAcked);
    acked.forEach(s => { c[s.state] -= 1; });
    c.acknowledged = acked.length;
    return c;
}

function clock(iso) {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

/* The hero is a SENTENCE, not a number: "2 sites down" is readable by
   someone who has never been shown what the colours mean. */
function heroSentence(counts, total) {
    const down = counts.offline || 0;
    const flapping = counts.flapping || 0;
    if (down && flapping) return `${plural(down, "site")} down, ${flapping} flapping`;
    if (down) return `${plural(down, "site")} down`;
    if (flapping) return `${plural(flapping, "site")} flapping`;
    // Everything broken is already acknowledged: calm, but never "all
    // online" — those sites are still down, just no longer news.
    if (counts.acknowledged) return `Nothing new — ${plural(counts.acknowledged, "known issue")}`;
    if (counts.unknown) return `${total - counts.unknown} of ${total} sites online`;
    return total === 1 ? "The site is online" : `All ${total} sites online`;
}

function readout(label, value) {
    return `<div class="status-readout">
        <div class="status-readout-label">${esc(label)}</div>
        <div class="status-readout-value">${esc(value)}</div>
    </div>`;
}

/* What a calm page says instead of an empty problems block: when the fleet
   last changed at all, and when the last outage ended. */
function calmLine(data) {
    const parts = [];
    if (data.last_change_at) parts.push(`Nothing new since ${clock(data.last_change_at)}.`);
    if (data.last_incident_closed_at) parts.push(`Last outage ended ${ago(data.last_incident_closed_at)}.`);
    return parts.join(" ");
}

function renderHero(data, stale) {
    const hero = document.getElementById("status-hero");
    const c = headlineCounts(data);
    const total = data.sites.length;
    const hasDown = (c.offline || 0) > 0;
    const hasProblem = hasDown || (c.flapping || 0) > 0;

    hero.className = "status-hero" + (stale ? " is-stale" : hasDown ? " has-problem" : hasProblem ? " has-flapping" : "");

    const people = data.sites.reduce((sum, site) => sum + (site.sessions || 0), 0);
    const busiest = data.sites.reduce(
        (best, site) => ((site.sessions || 0) > (best.sessions || 0) ? site : best),
        { sessions: 0, name: "–" });

    let glyph, line, sub, note = "";
    if (stale) {
        /* A page of green dots that has quietly stopped updating is the worst
           thing this page can show — so it refuses to claim any state. */
        glyph = STATE_INFO.unknown.shape;
        line = "Status unknown";
        sub = data.last_ingest_at
            ? `No update for ${span(data.last_ingest_at)} — this page may be out of date`
            : "The router has never reported";
        note = data.last_ingest_at
            ? `Last good report ${clock(data.last_ingest_at)}, when ${plural(c.online || 0, "site")} ${(c.online || 0) === 1 ? "was" : "were"} online. This gap is recorded as not watched, not as downtime.`
            : "";
    } else {
        glyph = hasDown ? STATE_INFO.offline.shape : hasProblem ? STATE_INFO.flapping.shape : STATE_INFO.online.shape;
        line = heroSentence(c, total);
        sub = `${c.online || 0} online · ${c.offline || 0} down · ${c.flapping || 0} flapping`
            + (c.acknowledged ? ` · ${c.acknowledged} acknowledged` : "")
            + ` · ${c.unknown || 0} not watched`;
        note = hasProblem ? "" : calmLine(data);
    }

    // The right-hand side carries what the cards below do NOT: the cards
    // count sites, these count people.
    const readouts = stale ? "" : readout("People online", people)
        + readout("Busiest site", busiest.sessions ? `${busiest.name} (${busiest.sessions})` : "–");

    hero.innerHTML = `
        <div class="status-hero-main">
            <div class="status-hero-kicker">Fleet status &middot; ${plural(total, "site")}</div>
            <div class="status-hero-line"><span class="status-hero-glyph" aria-hidden="true">${glyph}</span>${esc(line)}</div>
            <div class="status-hero-sub">${esc(sub)}</div>
            ${note ? `<div class="status-hero-note">${esc(note)}</div>` : ""}
        </div>
        <div class="status-hero-side">${readouts}</div>`;
}

/* The line that decides whether anyone drives out tonight. */
function dropLine(s) {
    if (s.state === "flapping") return "Going up and down repeatedly. Up right now, but not holding.";
    if (s.sessions_at_drop == null) return "No headcount from before it dropped.";
    if (s.sessions_at_drop === 0) return "Already empty when it dropped — nobody affected.";
    return `${plural(s.sessions_at_drop, "person")} ${s.sessions_at_drop === 1 ? "was" : "were"} online when it dropped.`;
}

function ackForm(id) {
    return `
        <form class="status-ack-form" data-ack-form="${id}">
            <input type="text" class="status-ack-input" maxlength="200"
                   placeholder="Why it's known, e.g. battery replacement, tech Thursday" aria-label="Acknowledgement note">
            <div class="status-problem-actions">
                <button type="submit" class="status-btn-primary">Acknowledge</button>
                <button type="button" class="status-problem-open" data-ack-cancel>Cancel</button>
            </div>
            <div class="status-ack-msg" hidden></div>
        </form>`;
}

function renderProblems(sites, stale) {
    // Leave an open note alone; the next poll after it closes catches up.
    if (ackEditing !== null && !stale) return;
    const problems = stale ? [] : sites.filter(s => (s.state === "offline" || s.state === "flapping") && !isAcked(s));
    const section = document.getElementById("status-problems-section");
    section.hidden = problems.length === 0;
    document.getElementById("status-problems-note").textContent = `${plural(problems.length, "site")} — not acknowledged`;
    const canAck = can("sites", "manage_monitoring");

    const box = document.getElementById("status-problems");
    box.innerHTML = problems.map(s => {
        const i = info(s.state);
        const big = s.state === "offline" ? `Down ${span(s.state_since)}` : "Flapping";
        const ident = [s.vlan_id != null ? `vlan ${s.vlan_id}` : "", s.pppoe_username || ""].filter(Boolean).join(" · ");
        return `
        <div class="status-problem ${s.state === "flapping" ? "is-flapping" : ""}">
            <div class="status-problem-top">
                <span class="status-problem-glyph" aria-hidden="true">${i.shape}</span>
                <div class="status-problem-id">
                    <div class="status-problem-name">${esc(s.name)}</div>
                    ${ident ? `<div class="status-problem-ident">${esc(ident)}</div>` : ""}
                </div>
                <div class="status-problem-state">${esc(big)}</div>
            </div>
            <div class="status-problem-detail">${esc(dropLine(s))}</div>
            <div class="status-problem-actions" data-ack-actions="${s.id}">
                ${canAck ? `<button type="button" class="status-btn-primary" data-ack-open="${s.id}">Acknowledge</button>` : ""}
                <button type="button" class="status-problem-open" data-open="${s.id}">Open site</button>
            </div>
        </div>`;
    }).join("");
}

function shortDate(iso) {
    return new Date(iso).toLocaleDateString([], { day: "numeric", month: "short" });
}

/* Known issues: acknowledged, still broken, deliberately quiet. Dashed and
   muted so it reads as "handled", but never gone — a site nobody can see
   is a site nobody remembers to un-acknowledge. */
function renderKnown(sites, stale) {
    const known = stale ? [] : sites.filter(isAcked);
    document.getElementById("status-known-section").hidden = known.length === 0;
    const canAck = can("sites", "manage_monitoring");
    document.getElementById("status-known").innerHTML = known.map(s => {
        const i = info(s.state);
        const how = s.state === "offline" ? `down ${span(s.state_since)}` : "flapping";
        const who = [s.ack.by, s.ack.at ? shortDate(s.ack.at) : "", s.ack.note].filter(Boolean).join(" · ");
        return `
        <div class="status-known-row">
            <span class="status-known-glyph" aria-hidden="true">${i.shape}</span>
            <button type="button" class="status-known-name" data-open="${s.id}">${esc(s.name)}</button>
            <span class="status-known-meta">${s.vlan_id != null ? `vlan ${s.vlan_id} · ` : ""}${esc(how)}</span>
            <span class="status-known-note">${esc(who)}</span>
            ${canAck ? `<button type="button" class="status-problem-open" data-unack="${s.id}">Un-acknowledge</button>` : ""}
        </div>`;
    }).join("");
}

async function sendAck(siteId, method, note) {
    const res = await fetch(`/monitoring/sites/${siteId}/acknowledge`, {
        method,
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: method === "POST" ? JSON.stringify({ note }) : undefined,
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Server said ${res.status}`);
    }
}

/* One delegated handler per block — both are rebuilt every poll, so
   per-button listeners would leak one set per tick. */
function wireProblemActions() {
    const onClick = async (e) => {
        const open = e.target.closest("[data-open]");
        if (open) { navigate(`site-detail/${open.dataset.open}`); return; }

        const start = e.target.closest("[data-ack-open]");
        if (start) {
            const id = Number(start.dataset.ackOpen);
            document.querySelector(`[data-ack-actions="${id}"]`).outerHTML = ackForm(id);
            ackEditing = id;
            document.querySelector(`[data-ack-form="${id}"] input`).focus();
            return;
        }

        if (e.target.closest("[data-ack-cancel]")) {
            ackEditing = null;
            if (lastData) render(lastData);
            return;
        }

        const unack = e.target.closest("[data-unack]");
        if (unack) {
            unack.disabled = true;
            try {
                await sendAck(unack.dataset.unack, "DELETE");
            } catch (err) {
                unack.disabled = false;
                unack.textContent = err.message;
                return;
            }
            load();
        }
    };
    document.getElementById("status-problems").addEventListener("click", onClick);
    document.getElementById("status-known").addEventListener("click", onClick);

    document.getElementById("status-problems").addEventListener("submit", async (e) => {
        const form = e.target.closest("[data-ack-form]");
        if (!form) return;
        e.preventDefault();
        const btn = form.querySelector("button[type=submit]");
        const msg = form.querySelector(".status-ack-msg");
        btn.disabled = true;
        try {
            await sendAck(form.dataset.ackForm, "POST", form.querySelector("input").value);
        } catch (err) {
            btn.disabled = false;
            msg.hidden = false;
            msg.textContent = err.message;
            return;
        }
        ackEditing = null;
        load();
    });
}

/* Revenue is the one number on this page that is not observed continuously:
   it moves only when a customer list arrives. So it carries its own age,
   always — a total with no timestamp cannot be told apart from a stale one. */
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

    // Says what is wrong with the NUMBER, not what is wrong with the router —
    // the failure this exists to catch leaves the router looking perfect.
    const line = feed.known
        ? `The router last sent a customer list ${ago(data.revenue_feed.last_users_at)}.`
        : "No customer list has reached Ops yet.";

    box.hidden = false;
    box.innerHTML = `
        <div class="status-alert-title">Sales may be behind</div>
        <div class="status-alert-body">${esc(line)} Sites and headcounts below are unaffected — they arrive every minute. Revenue only moves when that list does.</div>`;
}

/* The four states, then money. Zeros are kept rather than hidden -- on this
   page a zero in Down is the reassurance, and a card that comes and goes is
   one you stop trusting. The money card is the exception: without
   sites:view_revenue it is left out entirely (owner's call, 2026-09-22),
   and the grid closes up to four columns rather than leaving a hole. */
function renderTotals(data, canRevenue) {
    const c = data.counts;
    const feed = salesFeedAge(data);
    const ackedDown = data.sites.filter(s => s.state === "offline" && isAcked(s)).length;
    const cards = [
        { label: "Online", value: c.online || 0, cls: "charged" },
        {
            label: "Down", value: c.offline || 0, cls: "low",
            // The card keeps the true count; the headline is what drops the
            // acknowledged ones, and this says how many that is.
            caption: ackedDown ? `${ackedDown} acknowledged` : "",
        },
        { label: "Flapping", value: c.flapping || 0, cls: "deployed" },
        { label: "Not watched", value: c.unknown || 0, cls: "unknown" },
    ];
    if (canRevenue) {
        cards.push({
            label: "Revenue today",
            value: money(data.revenue_today_kes),
            cls: "",
            caption: data.revenue_feed ? feed.text : "",
            captionStale: feed.stale,
            /* The total includes sales Ops could not place at a site; this
               says how much of it that is. Hidden at zero — on a good day
               every sale lands somewhere, and a line reading "KES 0 unplaced"
               every day is noise you learn to scroll past. The sites below
               will not add up to the total while this is showing, and that
               is the thing it exists to explain. */
            subcaption: data.revenue_unplaced_kes
                ? `${money(data.revenue_unplaced_kes)} not tied to a site yet`
                : "",
        });
    }

    const grid = document.getElementById("status-totals");
    grid.classList.toggle("is-four", !canRevenue);
    grid.innerHTML = cards.map(card => `
        <div class="stat-card ${card.cls}">
            <div class="stat-label">${card.label}</div>
            <div class="stat-value">${esc(card.value)}</div>
            ${card.caption ? `<div class="stat-caption ${card.captionStale ? "is-stale" : ""}">${esc(card.caption)}</div>` : ""}
            ${card.subcaption ? `<div class="stat-caption is-unplaced">${esc(card.subcaption)}</div>` : ""}
        </div>`).join("");
}

/* When the site was last known to be up: right now for a live site, the
   moment it dropped for one that is down. */
function lastSeen(s) {
    if (s.state === "online" || s.state === "flapping") return ago(s.sessions_at || s.state_since);
    return s.state_since ? ago(s.state_since) : "never";
}

function uptimeCell(s) {
    // An activity site cannot report Down, so it gets no percentage at all
    // rather than a flattering 100%.
    if (s.liveness_source !== "pppoe") return `<span class="status-uptime is-na" title="Activity site — down cannot be measured">activity only</span>`;
    if (s.uptime_30d_pct == null) return `<span class="status-uptime is-na">&ndash;</span>`;
    const low = s.uptime_30d_pct < UPTIME_WARN_PCT;
    return `<span class="status-uptime ${low ? "is-low" : ""}">${s.uptime_30d_pct.toFixed(1)}%</span>`;
}

function renderRows(sites, canRevenue, stale) {
    const tbody = document.getElementById("status-rows");
    document.getElementById("status-revenue-th").hidden = !canRevenue;
    const cols = canRevenue ? 6 : 5;

    if (sites.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${cols}" class="loading-text">No monitored sites yet.</td></tr>`;
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
        // With the feed dead no row may keep claiming a state: every dot goes
        // hollow and says Unknown, whatever it said at the last good report.
        const state = stale ? "unknown" : s.state;
        const acked = !stale && isAcked(s);
        const rowCls = acked ? "status-row-acked"
            : state === "offline" ? "status-row-down"
            : state === "flapping" ? "status-row-flapping" : "";
        const label = stale ? "Unknown"
            : s.state === "offline" ? `Down ${span(s.state_since)}` : null;
        const revenue = canRevenue
            ? `<td><span class="status-revenue ${s.revenue_today_kes ? "has-value" : ""}">${money(s.revenue_today_kes)}</span></td>`
            : "";
        const sessions = state === "offline" || stale || s.sessions == null ? "–" : s.sessions;
        return `
        <tr class="${rowCls} status-row-clickable" data-id="${s.id}">
            <td class="col-frozen">
                <span class="status-site-name">${esc(s.name)}</span>
                <span class="status-site-vlan">${s.vlan_id != null ? `vlan ${s.vlan_id}` : ""}${acked ? `<span class="status-ack-tag" title="${esc(s.ack.note || "Acknowledged")}">ACK</span>` : ""}</span>
            </td>
            <td>${pill(state, label)}</td>
            <td><span class="status-sessions">${sessions}</span></td>
            <td class="status-col-wide">${uptimeCell(s)}</td>
            <td class="status-col-wide"><span class="status-since">${esc(lastSeen(s))}</span></td>
            ${revenue}
        </tr>`;
    }).join("");
    // One delegated listener rather than one per row — rows are rebuilt
    // wholesale every poll, which would otherwise leak a listener per tick.
    tbody.onclick = (e) => {
        const row = e.target.closest("tr[data-id]");
        if (row) navigate(`site-detail/${row.dataset.id}`);
    };
}

function renderFootnote(canRevenue) {
    document.getElementById("status-footnote").textContent =
        "Uptime is over the last 30 days and leaves out time nobody was watching."
        + (canRevenue ? " Revenue counts sales the router reported — not billing ground truth." : "");
}

function render(data) {
    // Absent, not null: the key only exists with sites:view_revenue.
    const canRevenue = "revenue_today_kes" in data;
    lastData = data;
    const lastMs = data.last_ingest_at ? new Date(data.last_ingest_at).getTime() : 0;
    const stale = !lastMs || Date.now() - lastMs > STALE_MS;

    renderHero(data, stale);
    renderProblems(data.sites, stale);
    renderKnown(data.sites, stale);
    renderSalesAlert(data, canRevenue);
    renderTotals(data, canRevenue);
    renderRows(data.sites, canRevenue, stale);
    renderFootnote(canRevenue);
    dataStale = stale;
    lastRenderedIngest = data.last_ingest_at;
}

let lastRenderedIngest = null;
let dataStale = false;
let connectionOk = true;

/* The connection light answers "is this page still being fed", and the stale
   check in render() answers "is the ROUTER still feeding Ops" — either one
   failing dims the page, and neither can clear the other's dimming. */
function setFeed(ok, reason) {
    connectionOk = ok;
    const feed = document.getElementById("status-feed");
    feed.className = "status-feed" + (ok ? "" : " lost");
    feed.dataset.reason = reason || "";
    setFeedText();
    document.getElementById("view-status").classList.toggle("stale-feed", !ok || dataStale);
}

function setFeedText() {
    const feed = document.getElementById("status-feed");
    document.getElementById("status-feed-text").textContent = !connectionOk
        ? `Offline — ${feed.dataset.reason}`
        : lastRenderedIngest ? `Live · router ${ago(lastRenderedIngest)}` : "Live";
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
    document.querySelector("#view-status [data-tab-route]")
        .addEventListener("click", (e) => navigate(e.currentTarget.dataset.tabRoute));
    wireProblemActions();

    registerRoute("status", () => {
        showView("view-status");
        // A form left open on a previous visit would otherwise freeze the
        // problem cards for good.
        ackEditing = null;
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
