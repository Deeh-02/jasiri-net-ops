import { showView, registerRoute } from "./common.js";
import { api, esc, formatDay, todayEat, addDays, OUTCOME_COLOR, setError } from "./activity-common.js";

const $ = (id) => document.getElementById(id);

const PERIODS = [
    { key: "today", label: "Today" },
    { key: "7", label: "Last 7 days" },
    { key: "30", label: "Last 30 days" },
    { key: "custom", label: "Custom" },
];
const BUCKETS = [{ key: "day", label: "Day" }, { key: "week", label: "Week" }, { key: "month", label: "Month" }];
const LIST_LIMIT = 10;

let period = "7";
let range = null;          // {start, end} — what the cards currently show
let bucket = "day";
let bucketChosen = false;  // false: follow the range (day for a month or less, week, then month)
let data = null;
let loadSeq = 0;

function rangeFor(key) {
    const today = todayEat();
    if (key === "today") return { start: today, end: today };
    if (key === "7") return { start: addDays(today, -6), end: today };
    if (key === "30") return { start: addDays(today, -29), end: today };
    return { start: $("act-o-from").value, end: $("act-o-to").value };
}

function autoBucket({ start, end }) {
    const days = (Date.parse(end) - Date.parse(start)) / 86400000 + 1;
    return days <= 31 ? "day" : days <= 120 ? "week" : "month";
}

function drawControls() {
    $("act-period-chips").innerHTML = PERIODS.map(p => `<button type="button" class="act-chip${p.key === period ? " selected" : ""}" data-period="${p.key}" role="radio" aria-checked="${p.key === period}">${p.label}</button>`).join("");
    $("act-bucket-chips").innerHTML = `<span class="act-chip-label">Trend by</span>` + BUCKETS.map(b => `<button type="button" class="act-chip${b.key === bucket ? " selected" : ""}" data-bucket="${b.key}" role="radio" aria-checked="${b.key === bucket}">${b.label}</button>`).join("");
    $("act-custom").hidden = period !== "custom";
}

async function load() {
    if (!range?.start || !range?.end) return;
    const seq = ++loadSeq;
    setError($("act-overview-error"), "");
    // Refetch keeps the frame: the old figures stay, dimmed, until the new ones arrive.
    $("act-overview-body").classList.add("act-loading");
    const res = await api(`/activity/overview?start=${range.start}&end=${range.end}&bucket=${bucket}`);
    if (seq !== loadSeq) return;
    $("act-overview-body").classList.remove("act-loading");
    if (!res.ok) { setError($("act-overview-error"), res.error); return; }
    data = res.data;
    render();
}

// ---- Drawing ---------------------------------------------------------------

function render() {
    const d = data;
    const span = d.start === d.end ? formatDay(d.start) : `${formatDay(d.start, { year: false })} – ${formatDay(d.end)}`;
    $("act-overview-summary").textContent = `${d.total} ${d.total === 1 ? "report" : "reports"} · ${span}`;
    const body = $("act-overview-body");
    if (!d.total) {
        body.innerHTML = `<div class="act-card"><p class="act-none">No reports in this period.</p></div>`;
        return;
    }
    body.innerHTML = `
        <div class="act-card act-card-wide"><h2>Activities over time</h2><div id="act-trend" class="act-trend"></div></div>
        <div class="act-card"><h2>By category and type</h2>${categoriesHtml(d.by_category)}</div>
        <div class="act-card"><h2>By area</h2>${barsHtml(d.by_area.map(a => ({ name: a.area, value: a.count })), "reports")}</div>
        <div class="act-card"><h2>By technician</h2>${barsHtml(d.by_technician.map(t => ({ name: t.name, value: t.count })), "reports")}<p class="act-note">Counts every report a person is listed on.</p></div>
        <div class="act-card"><h2>Outcome mix</h2>${outcomesHtml(d.outcomes)}</div>
        <div class="act-card"><h2>Time spent</h2>${timeHtml(d.time_spent)}</div>
        ${d.repeat_visits.enabled ? `<div class="act-card act-card-wide"><h2>Repeat visits</h2>${repeatHtml(d.repeat_visits)}</div>` : ""}`;
    drawTrend();
}

function niceStep(max) {
    // A round step so the y axis shows 0 / 5 / 10 …, never 0 / 3.7 / 7.4.
    const rough = Math.max(1, max / 4);
    const pow = Math.pow(10, Math.floor(Math.log10(rough)));
    const n = rough / pow;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * pow;
}

function periodLabel(iso) {
    if (data.bucket === "month") {
        const [y, m] = iso.split("-").map(Number);
        return `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m - 1]} ${y}`;
    }
    return data.bucket === "week" ? `Week of ${formatDay(iso, { year: false })}` : formatDay(iso, { weekday: true });
}

function shortLabel(iso) {
    if (data.bucket === "month") return periodLabel(iso).slice(0, 3);
    return formatDay(iso, { year: false });
}

function drawTrend() {
    const host = $("act-trend");
    if (!host) return;
    const points = data.over_time;
    const width = Math.max(host.clientWidth, 280), height = 220;
    const m = { left: 34, right: 8, top: 18, bottom: 26 };
    const plotW = width - m.left - m.right, plotH = height - m.top - m.bottom;
    const max = Math.max(...points.map(p => p.count), 1);
    const step = niceStep(max);
    const top = Math.ceil(max / step) * step;
    const slot = plotW / points.length;
    const barW = Math.min(24, Math.max(3, slot * 0.7));
    const y = (v) => m.top + plotH - (v / top) * plotH;
    const peak = points.reduce((best, p, i) => (p.count > points[best].count ? i : best), 0);

    let svg = `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="Reports per ${data.bucket}">`;
    for (let v = 0; v <= top; v += step) {
        svg += `<line x1="${m.left}" x2="${width - m.right}" y1="${y(v)}" y2="${y(v)}" class="act-grid"/>`
            + `<text x="${m.left - 6}" y="${y(v) + 4}" text-anchor="end" class="act-axis">${v}</text>`;
    }
    // Few enough labels that they never touch, whatever the width.
    const every = Math.ceil(points.length / Math.max(2, Math.floor(plotW / 64)));
    points.forEach((p, i) => {
        const cx = m.left + slot * i + slot / 2;
        if (p.count) {
            svg += `<path class="act-col" data-col="${i}" d="M${cx - barW / 2},${m.top + plotH} V${y(p.count) + 4} Q${cx - barW / 2},${y(p.count)} ${cx - barW / 2 + 4},${y(p.count)} H${cx + barW / 2 - 4} Q${cx + barW / 2},${y(p.count)} ${cx + barW / 2},${y(p.count) + 4} V${m.top + plotH} Z"/>`;
        }
        if (i === peak && p.count) svg += `<text x="${cx}" y="${y(p.count) - 5}" text-anchor="middle" class="act-peak">${p.count}</text>`;
        if (i % every === 0) svg += `<text x="${cx}" y="${height - 8}" text-anchor="middle" class="act-axis">${esc(shortLabel(p.period))}</text>`;
        // The hit area is the whole slot, far bigger than the bar.
        svg += `<rect class="act-hit" data-col="${i}" x="${m.left + slot * i}" y="${m.top}" width="${slot}" height="${plotH}" tabindex="0" aria-label="${esc(periodLabel(p.period))}: ${p.count}" data-tip="${esc(periodLabel(p.period))}|${p.count} ${p.count === 1 ? "report" : "reports"}"/>`;
    });
    svg += `<line x1="${m.left}" x2="${width - m.right}" y1="${m.top + plotH}" y2="${m.top + plotH}" class="act-baseline"/></svg>`;
    host.innerHTML = svg;
}

function barRowHtml(i, top, unit) {
    const pct = Math.max(2, (i.value / top) * 100);
    return `<div class="act-bar-row" tabindex="0" data-tip="${esc(i.name)}|${esc(i.tip ?? `${i.value} ${unit}`)}">
        <span class="act-bar-name">${esc(i.name)}</span>
        <span class="act-bar-track"><span class="act-bar" style="width:${pct}%"></span></span>
        <span class="act-bar-value">${esc(i.display ?? i.value)}</span>
    </div>`;
}

// Ranked bars, largest first as the server sends them. Past LIST_LIMIT the
// rest folds away rather than making the card endless.
function barsHtml(items, unit) {
    if (!items.length) return `<p class="act-none">Nothing yet.</p>`;
    const top = Math.max(...items.map(i => i.value), 1);
    const rest = items.slice(LIST_LIMIT);
    return `<div class="act-bars">${items.slice(0, LIST_LIMIT).map(i => barRowHtml(i, top, unit)).join("")}`
        + `${rest.length ? `<details class="act-more"><summary>Show ${rest.length} more</summary>${rest.map(i => barRowHtml(i, top, unit)).join("")}</details>` : ""}</div>`;
}

// Every bar here shares one scale (the biggest category), so a type's bar is
// comparable with any category's.
function categoriesHtml(categories) {
    const top = Math.max(...categories.map(c => c.count), 1);
    return `<div class="act-bars">${categories.map(c => `
        <details class="act-cat">
            <summary>${barRowHtml({ name: c.category, value: c.count }, top, "reports")}</summary>
            <div class="act-types">${c.types.map(t => barRowHtml({ name: t.type, value: t.count }, top, "reports")).join("")}</div>
        </details>`).join("")}</div><p class="act-note">Tap a category to see its types.</p>`;
}

function outcomesHtml(mix) {
    const pct = (share) => `${Math.round(share * 100)}%`;
    const segments = mix.outcomes.filter(o => o.count).map(o => `
        <span class="act-seg" style="flex:${o.count};background:${OUTCOME_COLOR[o.outcome]}" tabindex="0" data-tip="${esc(o.label)}|${o.count} · ${pct(o.share)}">${o.share >= 0.12 ? pct(o.share) : ""}</span>`).join("");
    const label = mix.outcomes.map(o => `${o.label} ${o.count}`).join(", ");
    return `
        <div class="act-segbar" role="img" aria-label="${esc(label)}">${segments}</div>
        <ul class="act-legend">${mix.outcomes.map(o => `<li><span class="act-dot" style="background:${OUTCOME_COLOR[o.outcome]}"></span>${esc(o.label)} <span class="act-legend-count">${o.count} · ${pct(o.share)}</span></li>`).join("")}</ul>
        <p class="act-note">Unable to resolve: <strong>${pct(mix.unable_rate)}</strong> of ${mix.total} ${mix.total === 1 ? "report" : "reports"}.</p>`;
}

function hours(h) {
    return `${Number.isInteger(h) ? h : h.toFixed(1)} h`;
}

function timeHtml(rows) {
    const timed = rows.filter(r => r.reports_with_time);
    const withTime = rows.reduce((n, r) => n + r.reports_with_time, 0);
    const all = rows.reduce((n, r) => n + r.reports, 0);
    if (!timed.length) return `<p class="act-none">No report in this period gave a time spent.</p>`;
    const items = timed.map(r => ({
        name: r.category, value: r.hours, display: hours(r.hours),
        tip: `${hours(r.hours)} from ${r.reports_with_time} of ${r.reports} reports`,
    }));
    return `${barsHtml(items, "h")}<p class="act-note">Only reports that gave a time count: ${withTime} of ${all}. Chips are counted as 30 min, 1 h, 4 h and 8 h.</p>`;
}

// Customers and areas visited on more than one day. The window is always the
// 30 days up to the end of the chosen period, whatever the period's own length.
function repeatHtml(rv) {
    const list = (title, groups, empty) => `<div class="act-repeat-col"><h3>${title}</h3>${groups.length
        ? barsHtml(groups.map(g => ({ name: g.name, value: g.count, display: `${g.count}×`, tip: `${g.count} visits, last ${formatDay(g.last, { year: false })}` })), "visits")
        : `<p class="act-none">${empty}</p>`}</div>`;
    return `<p class="act-note act-note-top">${esc(rv.categories.join(", "))} reports on more than one day, ${esc(formatDay(rv.window_start, { year: false }))} – ${esc(formatDay(rv.window_end))}.</p>
        <div class="act-repeat">
            ${list("Customers", rv.customers, "None. Customers show here when a report names one.")}
            ${list("Areas", rv.areas, "No area was visited more than once.")}
        </div>`;
}

// ---- Tooltip (one shared element; names are inserted as text, never HTML) ---

function showTip(target, clientX, clientY) {
    const [title, value] = (target.dataset.tip || "").split("|");
    const tip = $("act-tip");
    tip.replaceChildren();
    const strong = document.createElement("strong");
    strong.textContent = value ?? "";
    const small = document.createElement("span");
    small.textContent = title ?? "";
    tip.append(strong, small);
    tip.hidden = false;
    const box = tip.getBoundingClientRect();
    const x = Math.min(Math.max(8, clientX - box.width / 2), window.innerWidth - box.width - 8);
    const y = clientY - box.height - 14 < 8 ? clientY + 18 : clientY - box.height - 14;
    tip.style.left = `${x}px`;
    tip.style.top = `${y}px`;
}

function setHover(target) {
    document.querySelectorAll(".act-col.hover").forEach(el => el.classList.remove("hover"));
    if (target?.dataset.col !== undefined) {
        $("act-trend")?.querySelector(`.act-col[data-col="${target.dataset.col}"]`)?.classList.add("hover");
    }
}

export function initActivityOverview() {
    $("act-period-chips").addEventListener("click", (e) => {
        const chip = e.target.closest("[data-period]");
        if (!chip) return;
        period = chip.dataset.period;
        if (period === "custom") {
            const base = range || rangeFor("7");
            $("act-o-from").value = $("act-o-from").value || base.start;
            $("act-o-to").value = $("act-o-to").value || base.end;
            $("act-o-from").max = $("act-o-to").max = todayEat();
            drawControls();
            return;
        }
        range = rangeFor(period);
        if (!bucketChosen) bucket = autoBucket(range);
        drawControls();
        load();
    });
    $("act-bucket-chips").addEventListener("click", (e) => {
        const chip = e.target.closest("[data-bucket]");
        if (!chip) return;
        bucket = chip.dataset.bucket;
        bucketChosen = true;
        drawControls();
        load();
    });
    $("act-o-apply").addEventListener("click", () => {
        const r = rangeFor("custom");
        if (!r.start || !r.end) return setError($("act-overview-error"), "Pick both dates");
        if (r.start > r.end) return setError($("act-overview-error"), "The start date is after the end date");
        range = r;
        if (!bucketChosen) bucket = autoBucket(range);
        drawControls();
        load();
    });

    const body = $("act-overview-body");
    body.addEventListener("pointermove", (e) => {
        const target = e.target.closest("[data-tip]");
        if (!target) { $("act-tip").hidden = true; setHover(null); return; }
        setHover(target);
        showTip(target, e.clientX, e.clientY);
    });
    body.addEventListener("pointerleave", () => { $("act-tip").hidden = true; setHover(null); });
    body.addEventListener("focusin", (e) => {
        const target = e.target.closest("[data-tip]");
        if (!target) return;
        setHover(target);
        const box = target.getBoundingClientRect();
        showTip(target, box.left + box.width / 2, box.top);
    });
    body.addEventListener("focusout", () => { $("act-tip").hidden = true; setHover(null); });

    let resizeTimer = null;
    window.addEventListener("resize", () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => { if (data && !$("view-activity-overview").hidden) drawTrend(); }, 150);
    });

    registerRoute("activity-overview", () => {
        showView("view-activity-overview");
        range = rangeFor(period === "custom" ? "7" : period);
        if (period === "custom") period = "7";
        if (!bucketChosen) bucket = autoBucket(range);
        drawControls();
        load();
    });
}
