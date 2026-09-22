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

/* ---- Periods ----
   The report is always one named calendar period, Nairobi time — a day
   ("YYYY-MM-DD"), a Monday–Sunday week ("YYYY-MM-DD", its Monday) or a month
   ("YYYY-MM") — never a rolling "last N days" that gives a different answer
   every time it's opened. Duplicated in trends.js, per this codebase's
   no-shared-util habit. */

const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const PERIOD_RE = { day: DAY_RE, week: DAY_RE, month: MONTH_RE };
const DAY_MS = 86400000;

// Today in Nairobi as a UTC-midnight Date, so these lists agree with the
// backend's EAT calendar whatever timezone the viewer's device is in.
function eatToday() {
    const n = new Date(Date.now() + 3 * 3600000);
    return new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate()));
}

const isoDay = date => date.toISOString().slice(0, 10);
const utcFmt = (date, opts) => date.toLocaleDateString("en-GB", { timeZone: "UTC", ...opts });
const mondayOf = date => new Date(date.getTime() - ((date.getUTCDay() + 6) % 7) * DAY_MS);

function monthLabel(month) {
    const [y, m] = month.split("-").map(Number);
    return utcFmt(new Date(Date.UTC(y, m - 1, 1)), { month: "long", year: "numeric" });
}

// "15–21 Sep", or "29 Sep – 5 Oct" when the week straddles a month.
function weekLabel(monday) {
    const start = new Date(`${monday}T00:00:00Z`);
    const end = new Date(start.getTime() + 6 * DAY_MS);
    if (start.getUTCMonth() === end.getUTCMonth()) {
        return `${start.getUTCDate()}–${utcFmt(end, { day: "numeric", month: "short" })}`;
    }
    return `${utcFmt(start, { day: "numeric", month: "short" })} – ${utcFmt(end, { day: "numeric", month: "short" })}`;
}

const dayLabel = day => utcFmt(new Date(`${day}T00:00:00Z`), { weekday: "short", day: "numeric", month: "short" });

function currentPeriod(kind) {
    const today = eatToday();
    if (kind === "day") return isoDay(today);
    if (kind === "month") return isoDay(today).slice(0, 7);
    return isoDay(mondayOf(today));
}

/* Every week or month from now back to the first one with anything
   recorded in it (dataFrom, from the backend) — a period from before the
   data starts would only ever be an empty page. With no data at all, just
   the current one. Days use a date picker instead, bounded the same way. */
function periodOptions(kind, dataFrom) {
    const today = eatToday();
    const from = dataFrom ? new Date(`${dataFrom}T00:00:00Z`) : today;
    const out = [];
    if (kind === "month") {
        const stop = isoDay(from).slice(0, 7);
        for (let i = 0; ; i++) {
            const value = isoDay(new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - i, 1))).slice(0, 7);
            out.push({ value, label: i === 0 ? `This month · ${monthLabel(value)}` : monthLabel(value) });
            if (value <= stop) return out;
        }
    }
    const stop = isoDay(mondayOf(from));
    for (let i = 0; ; i++) {
        const value = isoDay(new Date(mondayOf(today).getTime() - i * 7 * DAY_MS));
        const prefix = i === 0 ? "This week · " : i === 1 ? "Last week · " : "";
        out.push({ value, label: `${prefix}${weekLabel(value)}` });
        if (value <= stop) return out;
    }
}

const kindOf = d => (d.day ? "day" : d.month ? "month" : "week");
const valueOf = d => d.day || d.month || d.week;

// The noun phrase for the report's period — "Tue 22 Sep", "21–27 Sep" or
// "September 2026". Call sites supply their own preposition ("of ~", "over ~").
function windowPhrase(d) {
    if (d.day) return dayLabel(d.day);
    return d.month ? monthLabel(d.month) : weekLabel(d.week);
}

// Still running: the backend has capped it at now, so its totals are "so
// far", not final — worth saying before anyone reconciles against them.
const inProgress = d => valueOf(d) === currentPeriod(kindOf(d));

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

function renderRangeNote(d) {
    const watching = d.watching_since ? ` · watching since ${shortDay(d.watching_since)}` : "";
    const running = inProgress(d) ? " · in progress, figures so far" : "";
    document.getElementById("sr-range-note").textContent = `${windowPhrase(d)}${running}${watching}`;
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
            sub: `of ${windowPhrase(d)}`,
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
            sub: `of ${windowPhrase(d)}`,
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
            sub: `${sales} sale${sales === 1 ? "" : "s"} ${d.day ? "on" : "in"} ${windowPhrase(d)}`,
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

/* A 3-tick scale (max / half / 0) beside a bar chart — the only way to read
   a bar's actual value without hovering every one of them. `fmt` formats
   the tick label (plain numbers for people, compact KES for revenue). */
function yAxis(max, fmt = String) {
    // max defaults to 1 for an all-empty chart (Math.max(1, ...)) — at that
    // scale the half-tick rounds to the same label as the top one ("1/1/0"),
    // which reads as a mistake rather than "there is nothing here".
    const mid = max > 1 ? `<span>${esc(fmt(max / 2))}</span>` : "";
    return `<div class="sr-yaxis"><span>${esc(fmt(max))}</span>${mid}<span>0</span></div>`;
}

/* A Day report gets a bar per hour; a week or a month a bar per day. Different
   enough — data field, x-axis unit, "not yet" story — to be two renderers. */
function renderPeople(d) {
    const wrap = document.getElementById("sr-people-wrap");
    if (d.day) { renderPeopleHourly(d, wrap); return; }
    renderPeopleDaily(d, wrap);
}

/* The backend returns only hours that have data. The day's full hour grid
   is built here instead, so an hour nobody was watching renders as its own
   marker rather than silently closing the gap. Stops at `until` — today's
   report doesn't draw the hours that haven't happened yet. */
function peopleBuckets(d) {
    const byHour = new Map();
    (d.people || []).forEach(p => byHour.set(new Date(p.hour).getTime(), p));
    const hours = [];
    const end = new Date(d.until).getTime();
    for (let t = new Date(d.since).getTime(); t < end; t += 3600000) {
        const hit = byHour.get(t);
        hours.push({
            at: new Date(t),
            avg: hit ? hit.avg : null,
            peak: hit && hit.has_peak ? hit.peak : null,
            hasPeak: !!(hit && hit.has_peak),
        });
    }
    return hours;
}

/* The People charts' scale: the tallest *average* (what the bars are), not
   the peak — scaling to a peak no bar reaches left every bar short under a
   number that belonged to none of them. ~15% headroom so the number above
   the tallest bar stays inside the chart; rounded up to even so the middle
   tick is a whole number rather than 6.5 printed as "7". */
function peopleScale(values) {
    const top = Math.max(0, ...values);
    // Nothing to scale (an empty period): 1, which yAxis prints as a bare 1/0.
    return top === 0 ? 1 : Math.max(2, Math.ceil(top * 1.15 / 2) * 2);
}

/* Each bar's own value, pinned just above it like the revenue bars' — so the
   bar reads without hovering. The busiest one is emphasised. */
const barNum = (value, strong) => `<span class="sr-bar-num ${strong ? "is-peak" : ""}">${value}</span>`;

/* Which state covered a moment, from the timeline runs. */
function stateAt(timeline, ms) {
    for (const s of timeline) {
        if (ms >= new Date(s.start).getTime() && ms < new Date(s.end).getTime()) return s.state;
    }
    return null;
}

function renderPeopleHourly(d, wrap) {
    const buckets = peopleBuckets(d);
    const timeline = d.timeline || [];
    if (!buckets.some(b => b.avg != null)) {
        wrap.innerHTML = `<div class="sr-empty">No headcounts recorded on this day.</div>`;
        document.getElementById("sr-people-hint").textContent = "";
        return;
    }

    const max = peopleScale(buckets.map(b => b.avg || 0));
    const busiest = buckets.reduce((b, x) => ((x.avg ?? -1) > (b?.avg ?? -1) ? x : b), null);
    const peakBucket = buckets.reduce((b, x) => ((x.peak || 0) > (b?.peak || 0) ? x : b), null);
    const bars = buckets.map(b => {
        let cls = "";
        let height;
        let title;
        if (b.avg == null) {
            // Nothing recorded. If the site was down, say down; otherwise this
            // is a hole in what we were told, and it is not a zero.
            const state = stateAt(timeline, b.at.getTime());
            cls = state === "offline" ? "is-down" : "is-unknown";
            height = 3;
            title = `${timeOf(b.at.toISOString())} — ${state === "offline" ? "site was down" : "nothing recorded"}`;
        } else {
            height = Math.max(2, Math.round((b.avg / max) * 100));
            if (!b.hasPeak) cls = "is-averaged";
            const peakBit = b.hasPeak ? `, peak ${b.peak}` : " (hourly average only)";
            title = `${timeOf(b.at.toISOString())} — ${b.avg} on average${peakBit}`;
        }
        const num = b.avg != null ? barNum(b.avg, b === busiest) : "";
        return `<div class="sr-bar-slot" title="${esc(title)}"><div class="sr-bar ${cls}" style="height:${height}%">${num}</div></div>`;
    }).join("");

    // The bars are averages; the most people on at one moment is a different
    // number, so it gets its own words rather than a label on some bar.
    const peakSentence = (busiest ? ` Busiest hour was ${esc(timeOf(busiest.at.toISOString()))}, <strong>${busiest.avg}</strong> on average.` : "")
        + (peakBucket && peakBucket.hasPeak
            ? ` The most on at once was <strong>${peakBucket.peak}</strong>, at ${esc(timeOf(peakBucket.at.toISOString()))}.`
            : "");
    // Past the raw horizon a whole day is hourly averages — one sentence for
    // it, rather than a dashed divider with nothing on one side of it.
    const averagedSentence = buckets.some(b => b.avg != null && !b.hasPeak)
        ? ` Paler bars are hourly averages — the per-minute rows behind them are deleted after ${d.peak_horizon_days} days, so no peak exists for them.`
        : "";

    wrap.innerHTML = `
        <div class="sr-chart-row">
            ${yAxis(max, n => String(Math.round(n)))}
            <div class="sr-chart-body">
                <div class="sr-bars is-sparse is-dense">${bars}</div>
                <div class="sr-axis">
                    <span>${esc(timeOf(buckets[0].at.toISOString()))}</span>
                    <span>${esc(timeOf(buckets[buckets.length - 1].at.toISOString()))}</span>
                </div>
            </div>
        </div>
        <p class="sr-says">Typically <strong>${d.avg_people}</strong> people online.${peakSentence}${averagedSentence}</p>`;

    document.getElementById("sr-people-hint").textContent = `${buckets.length} hours`;
}

/* A hatched overlay spanning the leading stretch of a bar row that isn't
   real data yet — "not tracked" (revenue, before recording started) or "not
   watched" (people, before the site existed). Shared shape, different word. */
function hatchBand(count, total, label) {
    if (count === 0) return "";
    return `<div class="sr-untracked" style="left:0;width:${(count / total) * 100}%">
        <span class="sr-untracked-chip">${esc(label)}</span>
    </div>`;
}

/* One bar per EAT calendar day — a bar per hour over a week or a month is a
   couple of pixels wide and unreadable (intra-day shape is what the
   separate "Busiest hours" card already answers). Mirrors renderRevenue's
   shape: a hatched band for days before the site existed, each bar's average
   written above it, a dashed divider where true daily peaks give way to
   hourly-average-only days. */
function renderPeopleDaily(d, wrap) {
    const days = d.people_daily || [];
    if (days.length === 0) {
        wrap.innerHTML = `<div class="sr-empty">No headcounts recorded in this window.</div>`;
        document.getElementById("sr-people-hint").textContent = "";
        return;
    }

    const max = peopleScale(days.map(x => x.avg || 0));
    const busiest = days.reduce((b, x) => ((x.avg ?? -1) > (b?.avg ?? -1) ? x : b), null);
    const peakDay = days.reduce((b, x) => ((x.peak || 0) > (b?.peak || 0) ? x : b), null);
    const notWatchedCount = days.filter(x => !x.watched).length;
    const showsAveraged = days.some(x => x.watched && x.avg != null && !x.has_peak);

    const bars = days.map(x => {
        if (!x.watched) {
            return `<div class="sr-bar-slot" title="${esc(dayOf(`${x.day}T12:00:00`))} — before this site was added"></div>`;
        }
        if (x.avg == null) {
            return `<div class="sr-bar-slot" title="${esc(dayOf(`${x.day}T12:00:00`))} — nothing recorded">
                <div class="sr-bar is-unknown" style="height:3px"></div></div>`;
        }
        const height = Math.max(2, Math.round((x.avg / max) * 100));
        const cls = x.has_peak ? "" : "is-averaged";
        const peakBit = x.has_peak ? `, peak ${x.peak}` : " (hourly averages only)";
        return `<div class="sr-bar-slot" title="${esc(dayOf(`${x.day}T12:00:00`))} — ${x.avg} on average${peakBit}">
            <div class="sr-bar ${cls}" style="height:${height}%">${barNum(x.avg, x === busiest)}</div></div>`;
    }).join("");

    let divider = "";
    if (showsAveraged) {
        const idx = days.findIndex(x => x.watched && x.has_peak);
        if (idx > 0) divider = `<div class="sr-divider" style="left:${(idx / days.length) * 100}%"></div>`;
    }

    // Bars are daily averages; "most at once" is a peak, and is said as one.
    const peakSentence = (busiest && busiest.avg != null ? ` Busiest day was ${esc(dayOf(`${busiest.day}T12:00:00`))}, <strong>${busiest.avg}</strong> on average.` : "")
        + (peakDay && peakDay.has_peak
            ? ` The most on at once was <strong>${peakDay.peak}</strong>, on ${esc(dayOf(`${peakDay.day}T12:00:00`))}.`
            : "");
    const averagedSentence = showsAveraged
        ? ` Days left of the dashed line are hourly averages only — the per-minute rows are deleted after ${d.peak_horizon_days} days, so no peak exists for that stretch.`
        : "";

    wrap.innerHTML = `
        <div class="sr-chart-row">
            ${yAxis(max, n => String(Math.round(n)))}
            <div class="sr-chart-body">
                <div style="position:relative">
                    <div class="sr-bars is-sparse ${days.length > 14 ? "is-dense" : ""}">${bars}</div>
                    ${divider}
                    ${hatchBand(notWatchedCount, days.length, "not watched yet")}
                </div>
                <div class="sr-axis">
                    <span>${esc(dayOf(`${days[0].day}T12:00:00`))}</span>
                    <span>${esc(dayOf(`${days[days.length - 1].day}T12:00:00`))}</span>
                </div>
            </div>
        </div>
        <p class="sr-says">Typically <strong>${d.avg_people}</strong> people online.${peakSentence}${averagedSentence}</p>`;

    document.getElementById("sr-people-hint").textContent = `${days.length} days`;
}

/* ---- Revenue ---- */

/* Bars plus the hatched "not tracked yet" band — never a KES 0 bar for a
   stretch nobody was counting. */
function revenueBars(buckets, titleFn) {
    const max = Math.max(1, ...buckets.map(x => x.kes));
    const untrackedCount = buckets.filter(x => !x.tracked).length;
    const bars = buckets.map(x => {
        if (!x.tracked) {
            // Never a KES 0 bar for a stretch nobody was counting.
            return `<div class="sr-bar-slot" title="${esc(titleFn(x))}"></div>`;
        }
        const h = x.kes > 0 ? Math.max(3, Math.round((x.kes / max) * 100)) : 2;
        const label = x.kes > 0 ? `<div class="sr-bar-value">${esc(compact(x.kes))}</div>` : "";
        return `<div class="sr-bar-slot" title="${esc(titleFn(x))}">${label}<div class="sr-bar" style="height:${h}%"></div></div>`;
    }).join("");
    return { bars, band: hatchBand(untrackedCount, buckets.length, "not tracked yet"), untrackedCount };
}

/* revenue_hourly only exists on a Day report — checking for the key, not
   re-deriving the period here, keeps this in step with the backend. */
function renderRevenue(d) {
    const wrap = document.getElementById("sr-revenue-wrap");
    document.getElementById("sr-revenue-title").textContent = d.revenue_hourly ? "Revenue per hour" : "Revenue per day";
    if (d.revenue_hourly) { renderRevenueHourly(d, wrap); return; }
    const days = d.revenue_daily || [];
    if (days.length === 0) {
        wrap.innerHTML = `<div class="sr-empty">No revenue data.</div>`;
        return;
    }
    const { bars, band, untrackedCount } = revenueBars(days, x => x.tracked
        ? `${x.day} — ${money(x.kes)}, ${x.sales} sale${x.sales === 1 ? "" : "s"}`
        : `${x.day} — before revenue tracking started`);

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

function renderRevenueHourly(d, wrap) {
    const hours = d.revenue_hourly;
    const { bars, band, untrackedCount } = revenueBars(hours, x => x.tracked
        ? `${timeOf(x.hour)} — ${money(x.kes)}, ${x.sales} sale${x.sales === 1 ? "" : "s"}`
        : `${timeOf(x.hour)} — before revenue tracking started`);

    const total = hours.reduce((s, x) => s + x.kes, 0);
    const sales = hours.reduce((s, x) => s + x.sales, 0);
    const best = hours.reduce((b, x) => (x.kes > (b?.kes || 0) ? x : b), null);
    const bestBit = best && best.kes > 0 ? ` Busiest hour was ${esc(timeOf(best.hour))} at <strong>${esc(money(best.kes))}</strong>.` : "";
    const trackedBit = untrackedCount > 0
        ? ` The hatched stretch is before revenue tracking started — those hours are unknown, not zero.`
        : "";

    wrap.innerHTML = `
        <div style="position:relative">
            <div class="sr-bars">${bars}</div>
            ${band}
        </div>
        <div class="sr-axis">
            <span>${esc(timeOf(hours[0].hour))}</span>
            <span>${esc(timeOf(hours[hours.length - 1].hour))}</span>
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
            Averaged over ${windowPhrase(d)}, Nairobi time.</p>`;
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
    // `history` is newest-first. `source` used to sit on the right, but it is
    // just this site's liveness_source — identical on every row, and already
    // stated once in the header's "measured by" line. How long the new state
    // actually lasted changes per row, so that goes in its place instead.
    wrap.innerHTML = history.map((h, idx) => {
        const st = info(h.state);
        const endMs = idx === 0 ? Date.now() : new Date(history[idx - 1].at).getTime();
        const lastedSec = (endMs - new Date(h.at).getTime()) / 1000;
        const lasted = idx === 0 ? `ongoing · ${dur(lastedSec)}` : `lasted ${dur(lastedSec)}`;
        return `<div class="sr-event">
            <span class="sr-event-glyph ${st.cls}" aria-hidden="true">${st.glyph}</span>
            <span class="sr-event-time">${esc(dayTimeOf(h.at))}</span>
            <span class="sr-event-text">${esc(EVENT_SENTENCE[h.state] || h.state)}</span>
            <span class="sr-event-duration">${esc(lasted)}</span>
        </div>`;
    }).join("");
}

/* ---- Load / route ---- */

function render(d, canRevenue) {
    const windowStart = new Date(d.since);
    document.getElementById("site-detail-body").hidden = false;
    document.getElementById("site-detail-notfound").hidden = true;
    renderHeader(d);
    renderRangeNote(d);
    renderLocked(canRevenue);
    renderKpis(d, canRevenue, windowStart);
    renderTimeline(d);
    renderPeople(d);
    if (canRevenue) {
        renderRevenue(d);
        renderPackages(d);
    }
    renderHours(d);
    renderEvents(d);
}

let currentId = null;
let currentKind = "week";
let currentValue = null;
let dataFrom = null; // first day this site has anything recorded, from the last response

/* Day | Week | Month, then either a date picker (day) or a dropdown of the
   weeks/months that have data. Re-run after every load, since the data
   range only arrives with the response. A period from a pasted link that
   falls outside the list still gets its own option, so the control never
   claims to show something other than what's on screen. */
function setPeriodControls() {
    document.querySelectorAll("#sr-range button").forEach(b => {
        b.setAttribute("aria-pressed", String(b.dataset.mode === currentKind));
    });
    const select = document.getElementById("sr-period-select");
    const dayInput = document.getElementById("sr-day-input");
    select.hidden = currentKind === "day";
    dayInput.hidden = currentKind !== "day";
    if (currentKind === "day") {
        dayInput.min = dataFrom || currentPeriod("day");
        dayInput.max = currentPeriod("day");
        dayInput.value = currentValue;
        return;
    }
    const options = periodOptions(currentKind, dataFrom);
    if (!options.some(o => o.value === currentValue)) {
        options.push({ value: currentValue, label: currentKind === "month" ? monthLabel(currentValue) : weekLabel(currentValue) });
    }
    select.innerHTML = options.map(o => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
    select.value = currentValue;
}

function fail(message) {
    document.getElementById("site-detail-body").hidden = true;
    const box = document.getElementById("site-detail-notfound");
    box.textContent = message;
    box.hidden = false;
}

async function load(id, kind, value) {
    try {
        const url = `/monitoring/sites/${id}?${kind}=${value}`;
        const res = await fetch(url, { headers: authHeaders() });
        if (res.status === 404) return fail("This site isn't there anymore, or the link is wrong.");
        // A hand-edited or future period in the link: say which, not "could not load".
        if (res.status === 400) return fail(`No report for that period — ${(await res.json()).detail}.`);
        if (!res.ok) throw new Error(`server said ${res.status}`);
        const data = await res.json();
        dataFrom = data.data_from;
        setPeriodControls();
        // Absent, not null: the key only exists with sites:view_revenue.
        render(data, "revenue_daily" in data);
    } catch (err) {
        console.error("site report:", err);
        fail("Could not load this site right now.");
    }
}

export function initSiteDetail() {
    document.getElementById("site-detail-back-btn").addEventListener("click", () => navigate("status"));
    // The period lives in the route, so back and a pasted link both work.
    const go = (kind, value) => navigate(`site-detail/${currentId}/${kind}/${value}`);
    document.querySelectorAll("#sr-range button").forEach(b => {
        b.addEventListener("click", () => go(b.dataset.mode, currentPeriod(b.dataset.mode)));
    });
    document.getElementById("sr-period-select").addEventListener("change", (e) => go(currentKind, e.target.value));
    document.getElementById("sr-day-input").addEventListener("change", (e) => {
        if (e.target.value) go("day", e.target.value); // cleared picker: stay put
    });

    registerRoute("site-detail", (params) => {
        showView("view-site-detail");
        const id = params[0];
        if (!id) {
            navigate("status", { replace: true });
            return;
        }
        if (id !== currentId) dataFrom = null; // another site's range doesn't apply
        currentId = id;
        const [kind, value] = [params[1], params[2]];
        // Anything else — no period, or an older link shape — opens on this
        // week. A future period is left to the backend to refuse; it answers
        // 400 and the page says so.
        if (PERIOD_RE[kind] && PERIOD_RE[kind].test(value || "")) {
            currentKind = kind;
            currentValue = value;
        } else {
            currentKind = "week";
            currentValue = currentPeriod("week");
        }
        setPeriodControls();
        load(currentId, currentKind, currentValue);
    });
}
