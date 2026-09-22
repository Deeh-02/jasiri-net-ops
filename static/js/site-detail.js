import { authHeaders, showView, navigate, registerRoute } from "./common.js";

/* Site report. Built to the N.11 UI brief: no chart library, every chart is
   divs in a flex row with a computed height, and no state is ever carried by
   colour alone — colour + pattern + glyph + the word, every time, because
   this page's real distribution channel is a screenshot in WhatsApp. */

const TZ = "Africa/Nairobi";

const STATE_INFO = {
    online:   { glyph: "●", word: "Online",      cls: "st-online" },
    offline:  { glyph: "▲", word: "Down",        cls: "st-offline" },
    flapping: { glyph: "◆", word: "Flapping",    cls: "st-flapping" },
    unknown:  { glyph: "○", word: "Not watched", cls: "st-unknown" },
};

function info(state) {
    return STATE_INFO[state] || STATE_INFO.unknown;
}

function esc(value) {
    const d = document.createElement("div");
    d.textContent = value == null ? "" : String(value);
    return d.innerHTML;
}

function money(kes) {
    return `KES ${Number(kes || 0).toLocaleString()}`;
}

/* Compact, for a label sitting above a bar where the full figure won't fit. */
function compact(kes) {
    const n = Number(kes || 0);
    if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`;
    return String(Math.round(n));
}

function at(iso, opts) {
    return new Date(iso).toLocaleString("en-GB", { timeZone: TZ, ...opts });
}

const timeOf = iso => at(iso, { hour: "2-digit", minute: "2-digit", hour12: false });
const dayOf = iso => at(iso, { weekday: "short", day: "numeric", month: "short" });
const dayTimeOf = iso => at(iso, {
    weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit", hour12: false,
});
const shortDay = iso => at(iso, { day: "numeric", month: "short" });

/* Hours stay hours well past 24 — "26h 17m" is easier to reason about than
   "1d 2h" when you're reading an outage length. Days only past two of them. */
function dur(seconds) {
    if (seconds == null) return "–";
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60) % 60;
    const h = Math.floor(s / 3600);
    if (h >= 48) return `${Math.floor(h / 24)}d ${h % 24}h`;
    if (h) return `${h}h ${m}m`;
    return `${m}m`;
}

function pill(state, since) {
    const i = info(state);
    const age = since ? ` ${dur((Date.now() - new Date(since).getTime()) / 1000)}` : "";
    return `<span class="status-pill ${i.cls}"><span class="st-shape" aria-hidden="true">${i.glyph}</span>${esc(i.word)}${esc(age)}</span>`;
}

/* ---- Header ---- */

function measuredBy(d) {
    if (d.liveness_source === "pppoe") return "measured by PPPoE session + 60s heartbeat";
    if (d.liveness_source === "activity") return "measured by hotspot activity only — cannot report Down";
    if (d.liveness_source === "ping") return "measured by ping probe";
    return "not measured";
}

function renderHeader(d) {
    document.getElementById("sr-title").textContent = d.name;
    document.getElementById("sr-state-pill").innerHTML = pill(d.state, d.state_since);

    const bits = [
        d.vlan_id != null ? `vlan ${d.vlan_id}` : "no vlan",
        d.pppoe_username || null,
        measuredBy(d),
        TZ,
    ].filter(Boolean);
    document.getElementById("sr-provenance").textContent = bits.join(" · ");

    const contact = [];
    if (d.contact_name) contact.push(`<span>${esc(d.contact_name)}</span>`);
    if (d.contact_phone) {
        contact.push(`<a href="tel:${esc(d.contact_phone)}">${esc(d.contact_phone)}</a>`);
    }
    if (d.address) contact.push(`<span>${esc(d.address)}</span>`);
    document.getElementById("sr-contact").innerHTML = contact.join("");
}

function renderRangeNote(d, windowStart) {
    const watching = d.watching_since ? ` · watching since ${shortDay(d.watching_since)}` : "";
    document.getElementById("sr-range-note").textContent =
        `${shortDay(windowStart.toISOString())} – ${shortDay(new Date().toISOString())}${watching}`;
}

/* ---- KPI row ---- */

function uptimeCard(d) {
    // An activity site gets no percentage anywhere on this page — not a dash
    // in a percentage column, not a 100%. It gets a different measure with a
    // different name, so the two can never be mistaken for each other.
    if (d.liveness_source !== "pppoe") {
        const withPeople = (d.timeline || [])
            .filter(s => s.state === "online")
            .reduce((sum, s) => sum + s.duration_seconds, 0);
        return {
            label: "Time with people on it",
            figure: dur(withPeople),
            small: true,
            sub: `of the last ${d.days === 1 ? "24 hours" : `${d.days} days`}`,
            note: "No uptime percentage is shown here, anywhere — this site has no PPPoE uplink, so “down” cannot be measured.",
        };
    }
    const pct = d.uptime_pct;
    return {
        label: "Uptime",
        // Always one decimal: 99.4 and 99 are different answers at this scale,
        // and JS would print the second as a bare "99".
        figure: pct == null ? "–" : `${pct.toFixed(1)}%`,
        tone: pct == null ? "disabled" : pct < 95 ? "down" : pct < 99 ? "warn" : "ok",
        sub: "online ÷ (online + offline)",
    };
}

function renderKpis(d, canRevenue, windowStart) {
    const cov = d.coverage || {};
    // The site is younger than the window it's being shown over. Coverage
    // can never reach 100% here and that is not a fault — the card says so,
    // rather than reading as time somebody failed to watch.
    const youngSite = d.watching_since && new Date(d.watching_since) > windowStart;
    const cards = [
        uptimeCard(d),
        {
            label: "Coverage",
            figure: cov.pct == null ? "–" : `${cov.pct}%`,
            tone: cov.pct != null && cov.pct < 90 ? "warn" : "",
            warnCard: (cov.pct != null && cov.pct < 90) || youngSite,
            meter: cov.pct,
            sub: `${dur(cov.unwatched_seconds)} not watched`,
            note: youngSite ? `Measured from ${shortDay(d.watching_since)}, when this site was added — not the whole window.` : "",
        },
        {
            label: "Outages",
            figure: d.outages ? d.outages.length : 0,
            tone: d.outages && d.outages.length ? "down" : "",
            sub: d.outages && d.outages.length
                ? `longest ${dur(d.longest_outage_seconds)} · flapped ${d.flap_count}×`
                : `none · flapped ${d.flap_count}×`,
        },
        {
            label: "Average people",
            figure: d.avg_people == null ? "–" : d.avg_people,
            sub: `of the last ${d.days === 1 ? "24 hours" : `${d.days} days`}`,
        },
        {
            label: "Peak people",
            figure: d.peak_people == null ? "—" : d.peak_people,
            tone: d.peak_people == null ? "disabled" : "",
            // Past the raw horizon only hourly averages survive, so there is
            // genuinely no peak to report for the older part of a long window.
            sub: peakSub(d),
        },
    ];

    if (canRevenue) {
        const total = (d.revenue_daily || []).reduce((s, x) => s + x.kes, 0);
        const sales = (d.revenue_daily || []).reduce((s, x) => s + x.sales, 0);
        cards.push({
            label: "Revenue",
            figure: money(total),
            small: true,
            sub: `${sales} sale${sales === 1 ? "" : "s"} in ${d.days === 1 ? "24h" : `${d.days}d`}`,
        });
    } else {
        cards.push({ label: "Revenue", figure: "Hidden", small: true, tone: "disabled", sub: "🔒 not your role" });
    }

    document.getElementById("sr-kpis").innerHTML = cards.map(c => `
        <div class="sr-kpi ${c.warnCard ? "is-warn" : ""}">
            <div class="sr-kpi-label">${esc(c.label)}</div>
            <div class="sr-kpi-figure ${c.small ? "is-small" : ""} ${c.tone ? `is-${c.tone}` : ""}">${esc(c.figure)}</div>
            ${c.meter != null ? `<div class="sr-meter"><span style="width:${c.meter}%"></span></div>` : ""}
            <div class="sr-kpi-sub">${esc(c.sub)}</div>
            ${c.note ? `<div class="sr-kpi-sub is-warn">${esc(c.note)}</div>` : ""}
        </div>`).join("");
}

function peakSub(d) {
    const horizon = d.peak_horizon_days;
    if (d.peak_people == null) return `not kept past ${horizon}d — averages only`;
    const best = (d.people || []).filter(p => p.has_peak)
        .reduce((b, p) => (b && b.peak >= p.peak ? b : p), null);
    const when = best ? `${dayOf(best.hour)} ${timeOf(best.hour)} · ` : "";
    return `${when}raw kept ${horizon}d`;
}

/* ---- State timeline ---- */

function segTooltip(seg) {
    const i = info(seg.state);
    const base = `${i.word} — ${dayTimeOf(seg.start)} to ${timeOf(seg.end)} (${dur(seg.duration_seconds)})`;
    if (seg.state === "flapping") return `${base} · counted as online`;
    if (seg.state === "unknown") return `${base} · nothing to judge by, excluded from uptime`;
    return base;
}

const LEGEND = [
    { state: "online", note: "solid, heavy foot" },
    { state: "offline", note: "solid, heavy foot" },
    { state: "flapping", note: "vertical stripes" },
    { state: "unknown", note: "diagonal hatch · excluded" },
];

function timelineSummary(d) {
    const parts = [];
    const n = d.outages ? d.outages.length : 0;
    if (n) {
        parts.push(`Down <strong>${n} time${n === 1 ? "" : "s"}</strong> for <strong>${esc(dur(d.downtime_seconds))}</strong>, longest ${esc(dur(d.longest_outage_seconds))}.`);
    } else if (d.liveness_source === "pppoe") {
        parts.push(`<strong>No outages</strong> in this window.`);
    }
    if (d.unknown_seconds > 0) {
        parts.push(`<strong>${esc(dur(d.unknown_seconds))}</strong> had no signal to judge by — excluded from the figure above, <strong>not</strong> counted as down.`);
    }
    if (d.coverage && d.coverage.unwatched_seconds > 0) {
        parts.push(`${esc(dur(d.coverage.unwatched_seconds))} of the window had no heartbeat at all.`);
    }
    if (d.flap_count > 0) {
        parts.push(`${esc(dur(d.flapping_seconds))} of flapping counts as online here; it is in the flap count, not the outage count.`);
    }
    return parts.join(" ");
}

function renderTimeline(d) {
    const wrap = document.getElementById("sr-timeline-wrap");
    const segs = d.timeline || [];
    if (segs.length === 0) {
        wrap.innerHTML = `<div class="sr-empty">Nothing recorded for this site in this window${d.watching_since ? ` — it has only been watched since ${esc(shortDay(d.watching_since))}` : ""}.</div>`;
        document.getElementById("sr-timeline-hint").textContent = "";
        return;
    }
    const bar = segs.map(s => `
        <div class="sr-seg ${info(s.state).cls}"
             style="flex-grow:${Math.max(1, Math.round(s.duration_seconds / 60))}"
             title="${esc(segTooltip(s))}"></div>`).join("");

    const legend = LEGEND.map(l => {
        const i = info(l.state);
        return `<div class="sr-legend-item">
            <span class="sr-swatch ${i.cls}" aria-hidden="true"></span>
            <span aria-hidden="true">${i.glyph}</span>
            <span class="sr-legend-word">${esc(i.word)}</span>
            <span class="sr-legend-note">${esc(l.note)}</span>
        </div>`;
    }).join("");

    wrap.innerHTML = `
        <div class="sr-timeline">${bar}</div>
        <div class="sr-axis">
            <span>${esc(dayTimeOf(segs[0].start))}</span>
            <span>${esc(dayTimeOf(segs[segs.length - 1].end))}</span>
        </div>
        <div class="sr-legend">${legend}</div>
        <div class="sr-inset">${timelineSummary(d)}</div>`;

    document.getElementById("sr-timeline-hint").textContent =
        `${segs.length} run${segs.length === 1 ? "" : "s"}`;
}

/* ---- People ---- */

/* The backend returns only hours that have data. A continuous grid is built
   here instead, so an hour nobody was watching renders as its own marker
   rather than silently closing the gap — the defect this page had before. */
function peopleBuckets(d, windowStart) {
    const byHour = new Map();
    (d.people || []).forEach(p => byHour.set(new Date(p.hour).getTime(), p));

    const hours = [];
    const cursor = new Date(windowStart);
    cursor.setUTCMinutes(0, 0, 0);
    const end = Date.now();
    while (cursor.getTime() <= end) {
        const key = cursor.getTime();
        const hit = byHour.get(key);
        hours.push({
            at: new Date(key),
            avg: hit ? hit.avg : null,
            peak: hit && hit.has_peak ? hit.peak : null,
            hasPeak: !!(hit && hit.has_peak),
        });
        cursor.setUTCHours(cursor.getUTCHours() + 1);
    }

    // 30 days of hourly bars is unreadable below 3px each, so it bins to 4h.
    if (d.days <= 7) return hours;
    const binned = [];
    for (let i = 0; i < hours.length; i += 4) {
        const group = hours.slice(i, i + 4);
        const withData = group.filter(g => g.avg != null);
        const peaks = group.filter(g => g.hasPeak).map(g => g.peak);
        binned.push({
            at: group[0].at,
            avg: withData.length ? Math.round(withData.reduce((s, g) => s + g.avg, 0) / withData.length) : null,
            peak: peaks.length ? Math.max(...peaks) : null,
            hasPeak: peaks.length > 0,
        });
    }
    return binned;
}

/* Which state covered a moment, from the timeline runs. */
function stateAt(timeline, ms) {
    for (const s of timeline) {
        if (ms >= new Date(s.start).getTime() && ms < new Date(s.end).getTime()) return s.state;
    }
    return null;
}

function renderPeople(d, windowStart) {
    const wrap = document.getElementById("sr-people-wrap");
    const buckets = peopleBuckets(d, windowStart);
    const timeline = d.timeline || [];
    const values = buckets.filter(b => b.avg != null).map(b => b.avg);

    if (values.length === 0) {
        wrap.innerHTML = `<div class="sr-empty">No headcounts recorded in this window.</div>`;
        document.getElementById("sr-people-hint").textContent = "";
        return;
    }

    const max = Math.max(1, ...buckets.map(b => Math.max(b.avg || 0, b.peak || 0)));
    const peakBucket = buckets.reduce((b, x) => ((x.peak || 0) > (b?.peak || 0) ? x : b), null);
    const rawEdge = Date.now() - d.peak_horizon_days * 86400000;
    const showsAveraged = buckets.some(b => b.at.getTime() < rawEdge);

    const bars = buckets.map(b => {
        const ms = b.at.getTime();
        const state = stateAt(timeline, ms);
        let cls = "";
        let height = 0;
        let title;
        if (b.avg == null) {
            // Nothing recorded. If the site was down, say down; otherwise this
            // is a hole in what we were told, and it is not a zero.
            cls = state === "offline" ? "is-down" : "is-unknown";
            height = 3;
            title = `${dayTimeOf(b.at.toISOString())} — ${state === "offline" ? "site was down" : "nothing recorded"}`;
        } else {
            height = Math.max(2, Math.round((b.avg / max) * 100));
            if (ms < rawEdge) cls = "is-averaged";
            const peakBit = b.hasPeak ? `, peak ${b.peak}` : " (hourly average only)";
            title = `${dayTimeOf(b.at.toISOString())} — ${b.avg} on average${peakBit}`;
        }
        const label = (peakBucket && b === peakBucket && b.hasPeak)
            ? `<div class="sr-bar-value is-peak">${b.peak}</div>` : "";
        return `<div class="sr-bar-slot" title="${esc(title)}">${label}<div class="sr-bar ${cls}" style="height:${height}%"></div></div>`;
    }).join("");

    // The boundary where raw rows stop and only hourly averages remain.
    let divider = "";
    if (showsAveraged) {
        const idx = buckets.findIndex(b => b.at.getTime() >= rawEdge);
        if (idx > 0) divider = `<div class="sr-divider" style="left:${(idx / buckets.length) * 100}%"></div>`;
    }

    const peakSentence = peakBucket && peakBucket.hasPeak
        ? ` Busiest was <strong>${peakBucket.peak}</strong> at ${esc(dayOf(peakBucket.at.toISOString()))} ${esc(timeOf(peakBucket.at.toISOString()))}.`
        : "";
    const averagedSentence = showsAveraged
        ? ` Bars left of the dashed line are hourly averages — the per-minute rows behind them are deleted after ${d.peak_horizon_days} days, so no peak exists for that stretch.`
        : "";

    wrap.innerHTML = `
        <div style="position:relative">
            <div class="sr-bars">${bars}</div>
            ${divider}
        </div>
        <div class="sr-axis">
            <span>${esc(dayTimeOf(buckets[0].at.toISOString()))}</span>
            <span>${esc(dayTimeOf(buckets[buckets.length - 1].at.toISOString()))}</span>
        </div>
        <p class="sr-says">Typically <strong>${d.avg_people}</strong> people online${peakSentence}${averagedSentence}</p>`;

    document.getElementById("sr-people-hint").textContent =
        d.days <= 7 ? `${buckets.length} hours` : `${buckets.length} × 4h`;
}

/* ---- Revenue ---- */

function renderRevenue(d) {
    const wrap = document.getElementById("sr-revenue-wrap");
    const days = d.revenue_daily || [];
    if (days.length === 0) {
        wrap.innerHTML = `<div class="sr-empty">No revenue data.</div>`;
        return;
    }
    const max = Math.max(1, ...days.map(x => x.kes));
    const untrackedCount = days.filter(x => !x.tracked).length;

    const bars = days.map(x => {
        if (!x.tracked) {
            // Never a KES 0 bar for a day nobody was counting.
            return `<div class="sr-bar-slot" title="${esc(x.day)} — before revenue tracking started"></div>`;
        }
        const h = x.kes > 0 ? Math.max(3, Math.round((x.kes / max) * 100)) : 2;
        const label = x.kes > 0 ? `<div class="sr-bar-value">${esc(compact(x.kes))}</div>` : "";
        return `<div class="sr-bar-slot" title="${esc(`${x.day} — ${money(x.kes)}, ${x.sales} sale${x.sales === 1 ? "" : "s"}`)}">
            ${label}<div class="sr-bar" style="height:${h}%"></div></div>`;
    }).join("");

    const band = untrackedCount > 0
        ? `<div class="sr-untracked" style="left:0;width:${(untrackedCount / days.length) * 100}%">
               <span class="sr-untracked-chip">not tracked yet</span>
           </div>`
        : "";

    const total = days.reduce((s, x) => s + x.kes, 0);
    const sales = days.reduce((s, x) => s + x.sales, 0);
    const best = days.reduce((b, x) => (x.kes > (b?.kes || 0) ? x : b), null);
    const bestBit = best && best.kes > 0 ? ` Best day was ${esc(dayOf(best.day))} at <strong>${esc(money(best.kes))}</strong>.` : "";
    const trackedBit = untrackedCount > 0
        ? ` The hatched stretch is before revenue tracking started — those days are unknown, not zero.`
        : "";

    wrap.innerHTML = `
        <div style="position:relative">
            <div class="sr-bars is-sparse">${bars}</div>
            ${band}
        </div>
        <div class="sr-axis">
            <span>${esc(dayOf(days[0].day))}</span>
            <span>${esc(dayOf(days[days.length - 1].day))}</span>
        </div>
        <p class="sr-says"><strong>${esc(money(total))}</strong> across ${sales} sale${sales === 1 ? "" : "s"}.${bestBit}${trackedBit}</p>`;
}

function renderPackages(d) {
    const wrap = document.getElementById("sr-packages-wrap");
    const pkgs = d.revenue_packages || [];
    const attr = d.revenue_attribution || {};
    const direct = attr.direct || { sales: 0, kes: 0 };
    const inferred = attr.inferred || { sales: 0, kes: 0 };
    const totalSales = direct.sales + inferred.sales;

    if (pkgs.length === 0) {
        wrap.innerHTML = `<div class="sr-empty">Nothing sold here in this window.</div>`;
        return;
    }
    const max = Math.max(...pkgs.map(p => p.kes), 1);
    const rows = pkgs.map(p => `
        <div class="sr-pkg-row">
            <div class="sr-pkg-top">
                <span class="sr-pkg-name">${esc(p.profile)}</span>
                <span class="sr-pkg-figure">${esc(money(p.kes))} · ${p.sales}×</span>
            </div>
            <div class="sr-pkg-track"><span class="sr-pkg-fill" style="width:${(p.kes / max) * 100}%"></span></div>
        </div>`).join("");

    let attrBlock = "";
    if (totalSales > 0) {
        const dPct = Math.round((direct.sales / totalSales) * 100);
        attrBlock = `
            <div class="sr-attr">
                <div class="sr-attr-bar">
                    <div class="sr-attr-direct" style="flex-grow:${direct.sales}"></div>
                    <div class="sr-attr-inferred" style="flex-grow:${inferred.sales}"></div>
                </div>
                <div class="sr-attr-keys">
                    <span class="sr-attr-key"><span class="sr-attr-dot direct" aria-hidden="true"></span>${dPct}% seen on this VLAN</span>
                    <span class="sr-attr-key"><span class="sr-attr-dot inferred" aria-hidden="true"></span>${100 - dPct}% inferred</span>
                </div>
                <p class="sr-says">Inferred sales were assigned from where that customer last bought, not seen arriving here — a high share is a reason to trust this site's figure less, not a bug.</p>
            </div>`;
    }
    wrap.innerHTML = `<div class="sr-pkg">${rows}</div>${attrBlock}`;
}

function renderLocked(canRevenue) {
    const strip = document.getElementById("sr-locked-strip");
    const split = document.getElementById("sr-revenue-split");
    if (canRevenue) {
        strip.innerHTML = "";
        split.hidden = false;
        return;
    }
    // One lock and one sentence for the page — not a padlock in forty cells.
    strip.innerHTML = `
        <div class="sr-locked-strip">
            <span aria-hidden="true">🔒</span>
            <div>
                <div class="sr-locked-title">Revenue and sales are hidden for your role.</div>
                <div class="sr-locked-body">Uptime, coverage and people are unchanged — this is not a degraded page, just a narrower one.</div>
            </div>
        </div>`;
    split.hidden = true;
}

/* ---- Busiest hours ---- */

function renderHours(d) {
    const wrap = document.getElementById("sr-hours-wrap");
    const hours = d.busiest_hours || [];
    const max = Math.max(...hours.map(h => h.avg), 0);
    if (max === 0) {
        wrap.innerHTML = `<div class="sr-empty">Not enough headcount data yet to show a daily pattern.</div>`;
        return;
    }
    const level = v => (v === 0 ? 0 : v / max > 0.66 ? 3 : v / max > 0.33 ? 2 : 1);
    const cells = hours.map(h => `
        <div>
            <div class="sr-hour-cell lv${level(h.avg)}" title="${esc(`${String(h.hour).padStart(2, "0")}:00 — ${h.avg} on average`)}">${h.avg}</div>
            <div class="sr-hour-label">${String(h.hour).padStart(2, "0")}</div>
        </div>`).join("");

    const busiest = hours.reduce((b, h) => (h.avg > b.avg ? h : b), hours[0]);
    const quietest = hours.reduce((b, h) => (h.avg < b.avg ? h : b), hours[0]);
    wrap.innerHTML = `
        <div class="sr-hours">${cells}</div>
        <p class="sr-says">Busiest around <strong>${String(busiest.hour).padStart(2, "0")}:00</strong>
            with ${busiest.avg} people on average; quietest at ${String(quietest.hour).padStart(2, "0")}:00.
            Averaged over the last ${d.days === 1 ? "24 hours" : `${d.days} days`}, Nairobi time.</p>`;
}

/* ---- What happened ---- */

const EVENT_SENTENCE = {
    online: "Came back online",
    offline: "Went down",
    flapping: "Started flapping",
    unknown: "Stopped reporting anything to judge by",
};

function renderEvents(d) {
    const wrap = document.getElementById("sr-events-wrap");
    const history = d.history || [];
    if (history.length === 0) {
        wrap.innerHTML = `<div class="sr-empty">No state changes in this window — it held one state the whole time.</div>`;
        return;
    }
    wrap.innerHTML = history.map(h => {
        const i = info(h.state);
        return `<div class="sr-event">
            <span class="sr-event-glyph ${i.cls}" aria-hidden="true">${i.glyph}</span>
            <span class="sr-event-time">${esc(dayTimeOf(h.at))}</span>
            <span class="sr-event-text">${esc(EVENT_SENTENCE[h.state] || h.state)}</span>
            <span class="sr-event-source">${esc(h.source || "")}</span>
        </div>`;
    }).join("");
}

/* ---- Load / route ---- */

function render(d, canRevenue) {
    const windowStart = new Date(Date.now() - d.days * 86400000);
    document.getElementById("site-detail-body").hidden = false;
    document.getElementById("site-detail-notfound").hidden = true;
    renderHeader(d);
    renderRangeNote(d, windowStart);
    renderLocked(canRevenue);
    renderKpis(d, canRevenue, windowStart);
    renderTimeline(d);
    renderPeople(d, windowStart);
    if (canRevenue) {
        renderRevenue(d);
        renderPackages(d);
    }
    renderHours(d);
    renderEvents(d);
}

let currentId = null;
let currentDays = 7;

function setRangeButtons(days) {
    document.querySelectorAll("#sr-range button").forEach(b => {
        b.setAttribute("aria-pressed", String(Number(b.dataset.days) === days));
    });
}

function fail(message) {
    document.getElementById("site-detail-body").hidden = true;
    const box = document.getElementById("site-detail-notfound");
    box.textContent = message;
    box.hidden = false;
}

async function load(id, days) {
    try {
        const res = await fetch(`/monitoring/sites/${id}?days=${days}`, { headers: authHeaders() });
        if (res.status === 404) return fail("This site isn't there anymore, or the link is wrong.");
        if (!res.ok) throw new Error(`server said ${res.status}`);
        const data = await res.json();
        // Absent, not null: the key only exists with sites:view_revenue.
        render(data, "revenue_daily" in data);
    } catch (err) {
        console.error("site report:", err);
        fail("Could not load this site right now.");
    }
}

export function initSiteDetail() {
    document.getElementById("site-detail-back-btn").addEventListener("click", () => navigate("status"));
    document.querySelectorAll("#sr-range button").forEach(b => {
        b.addEventListener("click", () => {
            // The range lives in the route, so back and a pasted link both work.
            navigate(`site-detail/${currentId}/${b.dataset.days}`);
        });
    });

    registerRoute("site-detail", (params) => {
        showView("view-site-detail");
        const id = params[0];
        if (!id) {
            navigate("status", { replace: true });
            return;
        }
        currentId = id;
        currentDays = [1, 7, 30].includes(Number(params[1])) ? Number(params[1]) : 7;
        setRangeButtons(currentDays);
        load(currentId, currentDays);
    });
}
