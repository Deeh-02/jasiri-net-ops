import { authHeaders, registerLogoutHandler } from "./common.js";

// Shared by the five Finance pages (Snapshot, Income, Expenses, Performance,
// Assets), the same way inventory-common.js is shared by the inventory pages.
// It holds what every Finance page draws the same way, so a tile, a bar or a
// hover label can't look or behave differently from one page to the next:
//
//   - text: money, dates, escaping
//   - fetchReport(): a report is remembered for a few minutes, so going from
//     the Snapshot to Income and back doesn't wait for the server each time
//   - the building blocks: headline tile, card, legend, alerts list
//   - the charts: bars over time, income against expenses by month, the
//     month's running total, ranked bars, a split bar, the profit bridge
//   - one floating label (the hover card) for all of them
//
// Chart rules (design.md, "Finance charts"): one axis per chart; bars are
// thin with a rounded top; a value nobody recorded is a gap, never a zero;
// every chart has a legend when it shows more than one thing, and a table
// underneath or beside it, so hovering is never the only way to read a figure.

// ---- Text and dates -----------------------------------------------------------

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

// Safe to put inside HTML text or a quoted attribute.
export function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, c => ESCAPES[c]);
}

// "KSh 12,430", "−KSh 3,220". Plain text, never HTML.
export function money(amount, digits = 0) {
    const n = Number(amount || 0);
    return `${n < 0 ? "−" : ""}KSh ${Math.abs(n).toLocaleString("en-KE", { maximumFractionDigits: digits })}`;
}

// The same, or a quiet "no data" when the figure doesn't exist (HTML).
export function moneyOrNone(amount, none = "no data") {
    return amount == null ? `<span class="fin-none">${esc(none)}</span>` : money(amount);
}

// Short figures for a chart's axis: 950, 12.5k, 1.2M.
export function shortMoney(value) {
    const a = Math.abs(value), sign = value < 0 ? "−" : "";
    const trim = n => String(Number(n.toFixed(1)));
    if (a >= 1e6) return `${sign}${trim(a / 1e6)}M`;
    if (a >= 1e3) return `${sign}${trim(a / 1e3)}k`;
    return `${sign}${Math.round(a)}`;
}

// "2026-11-14" (or "2026-11") read as a calendar day, with no time-zone shift.
export function asDay(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d || 1);
}

export function dayLabel(iso, opts = { weekday: "short", day: "numeric", month: "short" }) {
    return asDay(iso).toLocaleDateString("en-GB", opts);
}

export function monthLabel(iso, long = true) {
    return asDay(iso).toLocaleDateString("en-GB", long ? { month: "long", year: "numeric" } : { month: "short" });
}

export function nairobiDate(d = new Date()) {
    return d.toLocaleDateString("en-CA", { timeZone: "Africa/Nairobi" });
}

export function nairobiMonth() {
    return nairobiDate().slice(0, 7);
}

// What to tell the person when a request fails: the server's own sentence
// when it sent one, else the fallback.
export async function errorText(res, fallback) {
    if (!res) return "Couldn't reach the server. Check your connection and try again.";
    const body = await res.json().catch(() => ({}));
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]?.msg) return body.detail[0].msg;
    return fallback;
}

// ---- Reports, remembered for a few minutes --------------------------------------
// The Finance reports are worked out from many rows and can take seconds.
// Each is kept for three minutes, so moving between Finance pages shows what
// was just loaded straight away. Anything that changes the figures (saving,
// editing or voiding an expense, a target, an asset's dates) calls
// forgetReports(), so a page never shows figures from before the change.

const REPORT_KEPT_MS = 3 * 60_000;
const reports = new Map();   // url -> { at, promise }

// GET a report. Resolves to { ok: true, data } or { ok: false, error }.
export function fetchReport(url) {
    const kept = reports.get(url);
    if (kept && Date.now() - kept.at < REPORT_KEPT_MS) return kept.promise;
    const promise = (async () => {
        const res = await fetch(url, { headers: authHeaders() }).catch(() => null);
        if (!res || !res.ok) {
            reports.delete(url);   // a failure is never remembered
            return { ok: false, error: await errorText(res, "Couldn't load this. Try again in a moment.") };
        }
        return { ok: true, data: await res.json() };
    })();
    reports.set(url, { at: Date.now(), promise });
    return promise;
}

export function forgetReports() {
    reports.clear();
}

// The next person to sign in on this browser must not see the last one's figures.
registerLogoutHandler(forgetReports);

// ---- The floating label -----------------------------------------------------------
// One for all Finance pages. It lives on <body> (not inside a page), so a
// page's entrance animation can't move it, and it follows the pointer while
// staying inside the card it belongs to: it sits to the right of the pointer
// and turns to the left once it would cross the card's right edge (the same
// as the Dashboard's).

let tipBox = null;
let tipTimer = null;

export function showTip(html, x, y, card = null) {
    if (!tipBox) {
        tipBox = document.createElement("div");
        tipBox.className = "fin-tip";
        document.body.appendChild(tipBox);
    }
    clearTimeout(tipTimer);
    tipBox.innerHTML = html;
    tipBox.hidden = false;
    const w = tipBox.offsetWidth, h = tipBox.offsetHeight, gap = 14, pad = 8;
    const bounds = card?.getBoundingClientRect();
    const minX = bounds ? Math.max(pad, bounds.left + pad) : pad;
    const maxX = bounds ? Math.min(window.innerWidth - pad, bounds.right - pad) : window.innerWidth - pad;
    let left = x + gap;
    if (left + w > maxX) left = x - gap - w;
    left = Math.max(minX, Math.min(left, maxX - w));   // a card narrower than the label: stay on the card
    const above = y - h - 12;                    // no room under the top bar: go below the pointer
    tipBox.style.transform = `translate(${Math.round(left)}px, ${Math.round(above < 70 ? y + 18 : above)}px)`;
}

export function hideTip() {
    clearTimeout(tipTimer);
    if (tipBox) tipBox.hidden = true;
}

// The label's content: a title, rows of "name ... value" (each with the
// colour key of what it describes), and an optional note. Everything passed
// in is treated as plain text.
export function tipHtml(title, rows = [], note = "") {
    return `<b>${esc(title)}</b>` + rows.filter(Boolean).map(r =>
        `<span class="fin-tip-row">${r.key ? `<i class="fin-key ${r.key}"></i>` : ""}<span>${esc(r.label)}</span><b>${esc(r.value)}</b></span>`).join("") +
        (note ? `<span class="fin-tip-note">${esc(note)}</span>` : "");
}

// The same, as an attribute for the thing being hovered: <g ${tipAttr(...)}>.
export function tipAttr(title, rows, note) {
    return `data-tip="${esc(tipHtml(title, rows, note))}"`;
}

// Make every [data-tip] inside `root` show the floating label on hover, tap
// or keyboard focus. Safe to call again: it wires a container once.
export function wireTips(root) {
    if (!root || root.dataset.finTips) return;
    root.dataset.finTips = "1";
    const show = e => {
        // A chart with its own crosshair (mountPace) shows its own card: this
        // handler runs after it as the event bubbles, and hiding the card here
        // because nothing under the pointer has a data-tip made it vanish.
        if (e.target.closest?.("[data-own-tip]")) return;
        const el = e.target.closest?.("[data-tip]");
        if (!el || !root.contains(el)) { hideTip(); return; }
        showTip(el.dataset.tip, e.clientX, e.clientY, el.closest(".fin-card"));
    };
    root.addEventListener("pointermove", show);
    root.addEventListener("pointerdown", show);
    // A finger lifts as soon as it taps: leave the label up a moment so it can be read.
    root.addEventListener("pointerleave", e => {
        if (e.pointerType === "touch") { clearTimeout(tipTimer); tipTimer = setTimeout(hideTip, 2600); }
        else hideTip();
    });
    root.addEventListener("focusin", e => {
        const el = e.target.closest?.("[data-tip]");
        if (!el) return;
        const r = el.getBoundingClientRect();
        showTip(el.dataset.tip, r.left + r.width / 2, r.top, el.closest(".fin-card"));
    });
    root.addEventListener("focusout", hideTip);
}

window.addEventListener("hashchange", hideTip);
window.addEventListener("scroll", hideTip, { capture: true, passive: true });

// ---- Building blocks ---------------------------------------------------------------

const ARROW = `<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 2.5L8 6l-3.5 3.5"/></svg>`;

// Bars and lines grow in once, when a page's figures arrive; a redraw for a
// new width or another view of the same figures doesn't replay it.
export function markFresh(el) {
    if (!el) return;
    el.classList.add("fin-fresh");
    setTimeout(() => el.classList.remove("fin-fresh"), 1800);
}

// "▲ 12.4%" with the word for it, coloured by whether that direction is good.
// change: { pct, goodWhenUp, note }.
function deltaChip({ pct, goodWhenUp = true, note = "" }) {
    const n = Number(pct);
    const tone = n === 0 ? "flat" : (n > 0) === goodWhenUp ? "good" : "bad";
    const text = n === 0 ? "► No change" : `${n > 0 ? "▲ Up" : "▼ Down"} ${Math.abs(n)}%`;
    return `<span class="fin-kpi-change"><span class="fin-delta ${tone}">${text}</span>${note ? `<span>${esc(note)}</span>` : ""}</span>`;
}

// A headline tile: a label, one big figure, what it rests on, and optionally
// the change from last time and a small trend line.
//   value: text, or null for a figure that doesn't exist ("No data")
//   href:  a page to open ("finance-income/2026-11/all")
//   pick:  attributes that make it a button (e.g. `data-day="2026-11-14"`)
export function kpi({ label, value, none = "No data", sub = "", change = null, flag = null, href = null, pick = "", spark = "", title = "" }) {
    const text = value == null ? none : String(value);
    const tag = href ? "a" : pick ? "button" : "div";
    const open = href ? ` href="#/${esc(href)}"` : pick ? ` type="button" ${pick}` : "";
    return `<${tag} class="fin-kpi${href || pick ? " is-link" : ""}"${open}${title ? ` title="${esc(title)}"` : ""}>
        <span class="fin-kpi-label">${esc(label)}</span>
        <span class="fin-kpi-value${value == null ? " is-none" : ""}" style="--chars:${text.length}">${esc(text)}</span>
        ${change ? deltaChip(change) : ""}
        ${flag ? `<span class="fin-flag ${flag[1]}"><i class="fin-dot"></i>${esc(flag[0])}</span>` : ""}
        ${sub ? `<span class="fin-kpi-sub">${esc(sub)}</span>` : ""}
        ${spark}
    </${tag}>`;
}

// A card: a title, a line saying what it shows, an optional link to the page
// behind it, and the body. `span` is columns out of 12 inside a .fin-grid.
//   open:  ["finance-income/2026-11/all", "Income"]
//   tools: HTML for the right of the header in place of a link (a Day/Week switch)
export function card({ title, sub = "", span = 12, open = null, tools = "", body = "", id = "", cls = "" }) {
    const side = tools || (open ? `<a class="fin-open" href="#/${esc(open[0])}">${esc(open[1])}${ARROW}</a>` : "");
    return `<section class="fin-card ${cls}" style="--span:${span}"${id ? ` id="${id}"` : ""}>
        <div class="fin-card-head"><div><h2>${esc(title)}</h2>${sub ? `<p>${esc(sub)}</p>` : ""}</div>${side}</div>
        <div class="fin-card-body">${body}</div>
    </section>`;
}

// What each colour or line in a chart is. items: [{ key, label }], or
// { note } for a plain remark at the end.
export function legend(items) {
    return `<div class="fin-legend">${items.filter(Boolean).map(i => i.note
        ? `<span class="fin-legend-note">${esc(i.note)}</span>`
        : `<span><i class="fin-key ${i.key}"></i>${esc(i.label)}</span>`).join("")}</div>`;
}

const LEVEL_WORD = { bad: "Act now", warn: "Check", info: "Note" };

// Things to act on, worst first. items: [{ level, title, detail, href, link,
// pick }] where href+link is a page to open and pick+link a button on this page.
export function alertList(items) {
    return `<ul class="fin-alerts">${items.map(i => `<li class="fin-alert ${i.level}">
        <span class="fin-alert-level"><i class="fin-dot"></i>${LEVEL_WORD[i.level] || "Note"}</span>
        <div><strong>${esc(i.title)}</strong>${i.detail ? `<div class="fin-alert-detail">${esc(i.detail)}</div>` : ""}
        ${i.href ? `<a class="fin-link" href="#/${esc(i.href)}">${esc(i.link)}</a>` : ""}
        ${i.pick ? `<button type="button" class="fin-link" ${i.pick}>${esc(i.link)}</button>` : ""}</div>
    </li>`).join("")}</ul>`;
}

// A few plain figures in a row (inside a card, above its chart).
export function figures(items) {
    return `<div class="fin-figures">${items.filter(Boolean).map(([value, label]) =>
        `<div class="fin-figure"><b${value == null ? ` class="is-none"` : ""}>${esc(value ?? "No data")}</b><span>${esc(label)}</span></div>`).join("")}</div>`;
}

// ---- What two Finance pages work out the same way ---------------------------------------

// The change between two whole months, as a tile's chip: { pct, goodWhenUp,
// note }, or null when either figure is missing or last month was 0.
export function changeBetween(now, before, goodWhenUp, note) {
    if (now == null || before == null || !Number(before)) return null;
    const pct = Math.round((Number(now) - Number(before)) / Math.abs(Number(before)) * 1000) / 10;
    return { pct, goodWhenUp, note };
}

// The Performance report's monthly trend, ready for monthsChart(): from the
// first month with anything recorded (but never fewer than six months, so
// two months of records don't become two bars in an empty chart), each with
// its axis label and hover label.
export function monthPoints(trend) {
    const first = trend.findIndex(p => p.income != null || p.expenses != null);
    const from = Math.max(0, Math.min(first < 0 ? trend.length : first, trend.length - 6));
    return trend.slice(from).map(p => ({
        ...p, label: monthLabel(p.month, false),
        tip: tipAttr(`${monthLabel(p.month)}${p.partial ? " (so far)" : ""}`, [
            { key: "income", label: "Income", value: p.income == null ? "no data" : money(p.income) },
            { key: "expenses", label: "Expenses", value: p.expenses == null ? "not recorded" : money(p.expenses) },
            { key: "profit", label: "Profit", value: p.profit == null ? "no figure" : money(p.profit) },
            Number(p.capital) ? { label: "Equipment bought", value: money(p.capital) } : null,
        ], p.partial ? "Still counting: the month isn't over." : ""),
    }));
}

// The same months as a table (under the chart).
export function monthsTable(points) {
    return `<details class="fin-table-view"><summary>Show as a table</summary>
        <div class="table-scroll table-compact"><table class="table-dense">
            <thead><tr><th>Month</th><th class="num">Income</th><th class="num">Expenses</th><th class="num">Profit</th></tr></thead>
            <tbody>${points.map(p => `<tr><td>${esc(monthLabel(p.month))}${p.partial ? " (so far)" : ""}</td>
                <td class="num">${p.income == null ? "no data" : money(p.income)}</td><td class="num">${p.expenses == null ? "not recorded" : money(p.expenses)}</td>
                <td class="num">${p.profit == null ? "no figure" : money(p.profit)}</td></tr>`).join("")}</tbody>
        </table></div></details>`;
}

// What a month's income is missing (the statement's income_missing:
// segments not recorded for the whole month), as a tag ("Hotspot only"),
// the same mid-sentence ("hotspot only"; PPPoE keeps its capitals) and a
// sentence. null when nothing is missing.
const SEGMENT_TITLE = { pppoe: "PPPoE", hotspot: "Hotspot" };
const SEGMENT_INLINE = { pppoe: "PPPoE", hotspot: "hotspot" };
export function incomeGap(t) {
    const missing = t?.income_missing || [];
    if (!missing.length) return null;
    const have = Object.keys(SEGMENT_TITLE).filter(k => !missing.includes(k));
    const names = missing.map(k => SEGMENT_TITLE[k] || k).join(" and ");
    return {
        tag: have.length ? `${SEGMENT_TITLE[have[0]]} only` : "Not recorded",
        short: have.length ? `${SEGMENT_INLINE[have[0]]} only` : "not recorded",
        text: `${names} income isn't recorded for all of this month, so income and profit here are understated.`,
    };
}

// One month's statement (the Performance report's `this`) as the steps of
// the bridge: income, less each kind of cost, leaving net profit; then the
// equipment bought, leaving the cash.
export function profitSteps(t) {
    const tip = (label, amount, note) => amount == null ? "" : tipAttr(label, [{ label: "Amount", value: money(amount) }], note);
    return [
        { kind: "start", label: "Income", amount: t.income_total, tip: tip("Income", t.income_total, incomeGap(t)?.tag || "") },
        { kind: "minus", label: "Direct costs", amount: t.direct_total, none: "not recorded", tip: tip("Direct costs", t.direct_total, "Bandwidth and fibre rental") },
        { kind: "minus", label: "Operating expenses", amount: t.operating_total, none: "not recorded", tip: tip("Operating expenses", t.operating_total, "Power, rent, salaries, airtime and the rest") },
        { kind: "minus", label: "Taxes and charges", amount: t.finance_total, none: "not recorded", tip: tip("Taxes and charges", t.finance_total, "M-Pesa and bank charges, licences, taxes") },
        { kind: "result", label: "Net profit", amount: t.net_profit, none: "no figure yet", tip: tip("Net profit", t.net_profit, t.net_margin != null ? `${t.net_margin}% of income` : "") },
        { kind: "minus", label: "Equipment bought", amount: t.equipment_total, none: "not recorded", tip: tip("Equipment bought", t.equipment_total, "Kept, not a running cost, so not in profit") },
        { kind: "result", label: "Cash left", amount: t.cash_left, none: "no figure yet", tip: tip("Cash left", t.cash_left, "What the month left after costs and equipment") },
    ];
}

// ---- Ranked bars, split bar, meter (HTML, no SVG) ------------------------------------

// A ranking: one row per thing, longest bar first, each named and counted.
// rows: [{ label, amount, value, share, sub, tip, pick }]
//   amount: the number the bar is drawn from (null = no bar, "no data")
//   value:  the text shown for it;  share: a small "45%" at the end
//   pick:   attributes that make the row a button (drill-down)
export function ranked(rows, { key = "income", empty = "Nothing recorded yet." } = {}) {
    if (!rows.length) return `<p class="fin-empty">${esc(empty)}</p>`;
    const max = Math.max(1, ...rows.map(r => Number(r.amount || 0)));
    const hasShare = rows.some(r => r.share != null);
    return `<div class="fin-ranked${hasShare ? " has-share" : ""}">${rows.map((r, i) => {
        const tag = r.pick ? "button" : "div";
        const width = r.amount == null ? 0 : Math.max(0, Number(r.amount) / max * 100);
        return `<${tag} class="fin-rank"${r.pick ? ` type="button" ${r.pick}` : ""} ${r.tip || ""}>
            <span class="fin-rank-label">${esc(r.label)}${r.sub ? `<em>${esc(r.sub)}</em>` : ""}</span>
            <span class="fin-rank-track"><span class="fin-rank-fill ${key}" style="width:${width.toFixed(1)}%;--i:${i}"></span></span>
            <b>${r.amount == null ? `<span class="fin-none">no data</span>` : esc(r.value)}</b>
            ${hasShare ? `<span class="fin-rank-share">${esc(r.share ?? "")}</span>` : ""}
        </${tag}>`;
    }).join("")}</div>`;
}

// One whole split into its parts: a bar, and under it each part named with
// its figure, so colour is never the only cue.
// parts: [{ key, label, amount, value }]; amount null = "no data yet" (no slice).
export function splitBar(parts, what) {
    const known = parts.filter(p => p.amount != null && Number(p.amount) > 0);
    const total = known.reduce((sum, p) => sum + Number(p.amount), 0);
    const share = p => `${Math.round(Number(p.amount) / total * 100)}%`;
    const bar = total ? `<div class="fin-split" role="img" aria-label="${esc(what)}">${known.map((p, i) =>
        `<span class="fin-slice ${p.key}" style="flex-grow:${Number(p.amount)};--i:${i}" ${tipAttr(p.label, [{ key: p.key, label: "Amount", value: p.value }, { label: "Share", value: share(p) }])}></span>`).join("")}</div>` : "";
    return `${bar}<ul class="fin-split-legend">${parts.map(p => `<li>
        <i class="fin-key ${p.key}${p.amount == null ? " is-none" : ""}"></i><span>${esc(p.label)}</span>
        ${p.amount == null ? `<b class="fin-none">no data yet</b>` : `<b>${esc(p.value)}</b><em>${total && Number(p.amount) > 0 ? share(p) : ""}</em>`}</li>`).join("")}</ul>`;
}

// How far along something is: a filled track. tone: "" | "good" | "warn".
export function meter(fraction, tone = "") {
    const pct = Math.max(0, Math.min(1, fraction || 0)) * 100;
    return `<span class="fin-meter"><span class="fin-meter-fill ${tone}" style="width:${pct.toFixed(1)}%"></span></span>`;
}

// From income down to profit, one step per row: income as a full bar, each
// cost as the piece it takes away (starting where the last one ended), and
// what is left. A result can be followed by more steps (net profit, less the
// equipment bought, leaves the cash).
// steps: [{ kind: "start" | "minus" | "result", label, amount, none }]
//   amount null = not recorded: the row says so (`none`) and draws no bar.
export function bridge(steps) {
    let running = 0;
    const laid = steps.map(s => {
        if (s.amount == null) return { ...s, from: null };
        const amount = Number(s.amount);
        if (s.kind === "start") { running = amount; return { ...s, from: 0, to: amount }; }
        if (s.kind === "minus") { const before = running; running -= amount; return { ...s, from: running, to: before }; }
        running = amount;
        return { ...s, from: Math.min(0, amount), to: Math.max(0, amount) };
    });
    const ends = laid.filter(s => s.from != null).flatMap(s => [s.from, s.to]);
    const low = Math.min(0, ...ends), high = Math.max(1, ...ends);
    const at = v => ((v - low) / (high - low) * 100).toFixed(2);
    return `<div class="fin-bridge">${laid.map((s, i) => {
        const sign = s.kind === "minus" ? "− " : s.kind === "result" ? "= " : "";
        const loss = s.kind === "result" && s.amount != null && Number(s.amount) < 0;
        const shown = s.amount == null ? `<span class="fin-none">${esc(s.none || "no data")}</span>`
            : esc(money(s.kind === "minus" && Number(s.amount) ? -Number(s.amount) : s.amount));
        const bar = s.from == null || s.from === s.to ? ""
            : `<span class="fin-bridge-bar ${s.kind}${loss ? " loss" : ""}" style="left:${at(s.from)}%;width:max(2px, ${(at(s.to) - at(s.from)).toFixed(2)}%);--i:${i}"></span>`;
        return `<div class="fin-bridge-row ${s.kind}" ${s.tip || ""}>
            <span class="fin-bridge-name">${sign}${esc(s.label)}</span>
            <span class="fin-bridge-track">${low < 0 ? `<i class="fin-bridge-zero" style="left:${at(0)}%"></i>` : ""}${bar}</span>
            <b>${shown}</b></div>`;
    }).join("")}</div>`;
}

// ---- Charts (SVG, drawn at the width they are shown at) ----------------------------------

// Round steps for a money axis that covers min..max (and always 0), about
// `lines` gridlines apart: every gridline is a figure you can read.
function axisScale(min, max, lines = 4) {
    const low = Math.min(0, min), high = max > 0 ? max : (low < 0 ? 0 : 4);
    const raw = (high - low) / lines, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw * 0.999);
    const top = Math.ceil(high / step - 1e-9) * step, bottom = Math.floor(low / step + 1e-9) * step;
    const ticks = [];
    for (let v = bottom; v <= top + step / 1000; v += step) ticks.push(Math.abs(v) < step / 1000 ? 0 : v);
    return { top, bottom, ticks };
}

// The drawing area for a chart `width` pixels wide.
function frame(width, { tall = 250, short = 210 } = {}) {
    const W = Math.max(300, Math.min(1100, Math.round(width || 720)));
    return { W, H: W < 520 ? short : tall, L: 46, R: 12, T: 22, B: 26 };
}

// Gridlines with their figures down the left; the zero line is the baseline.
function gridSvg(f, scale, y) {
    return scale.ticks.map(v => `<line class="${v === 0 ? "base" : "grid"}" x1="${f.L}" x2="${f.W - f.R}" y1="${y(v)}" y2="${y(v)}"/>
        <text class="tick" x="${f.L - 8}" y="${y(v) + 3.5}" text-anchor="end">${shortMoney(v)}</text>`).join("");
}

// A bar from the baseline to its value: rounded at the value end, square at
// the baseline (a negative value hangs below it).
function barPath(x, width, yValue, yBase, cls, i) {
    const height = Math.abs(yBase - yValue);
    if (height < 0.5) return "";
    const r = Math.min(4, width / 2, height), up = yValue < yBase ? 1 : -1;
    return `<path class="${cls}" style="--i:${i}" d="M${x},${yBase} V${yValue + r * up} Q${x},${yValue} ${x + r},${yValue} H${x + width - r} Q${x + width},${yValue} ${x + width},${yValue + r * up} V${yBase} Z"/>`;
}

// The diagonal lines that mark a bar as "still counting".
function hatchDefs(id, keys) {
    return `<defs>${keys.map(key => `<pattern id="${id}-${key}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <line x1="0" y1="0" x2="0" y2="6" class="hatch ${key}"/></pattern>`).join("")}</defs>`;
}

// Points joined into lines, broken wherever a value is missing.
function runs(points) {
    const out = [];
    let run = [];
    for (const p of points) {
        if (!p) { if (run.length) out.push(run); run = []; } else run.push(p);
    }
    if (run.length) out.push(run);
    return out;
}

const pathOf = run => run.map((p, n) => `${n ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");

// One bar per period (a day, a week, a month).
// points: [{ label, value, compare, partial, tip, pick }]
//   value null = no data (a gap);  compare = the same period last time (a line)
//   partial = still counting (hatched);  pick = attributes that make it clickable
// options: width, id (unique per chart on the page), average (a dashed rule),
//          what (the chart's name for screen readers), labelWidth (pixels one
//          axis label needs, so longer labels are shown every few bars)
export function barChart(points, { width, id, average = null, what = "Income over time", labelWidth = 26 }) {
    const f = frame(width), { W, H, L, R, T, B } = f;
    const values = points.flatMap(p => [p.value, p.compare]).filter(v => v != null).map(Number);
    const scale = axisScale(0, Math.max(0, ...values, Number(average || 0)));
    const y = v => T + (scale.top - v) / (scale.top - scale.bottom) * (H - T - B);
    const slot = (W - L - R) / points.length, bar = Math.max(3, Math.min(24, slot * 0.62));
    const every = Math.max(1, Math.ceil(labelWidth / slot));
    // The highest finished bar carries its figure; the axis and the hover label carry the rest.
    let best = -1;
    points.forEach((p, i) => {
        if (p.value == null || p.partial) return;
        if (best < 0 || Number(p.value) > Number(points[best].value)) best = i;
    });
    const slots = points.map((p, i) => {
        const cx = L + slot * i + slot / 2;
        const mark = p.value == null ? "" : barPath(cx - bar / 2, bar, y(Number(p.value)), y(0), `bar${p.partial ? " partial" : ""}`, i);
        const top = i === best && points.length > 2 ? `<text class="val" x="${cx}" y="${y(Number(p.value)) - 6}" text-anchor="middle">${shortMoney(Number(p.value))}</text>` : "";
        const name = i % every === 0 ? `<text class="tick" x="${cx}" y="${H - 8}" text-anchor="middle">${esc(p.label)}</text>` : "";
        return `<g class="slot${p.pick ? " is-link" : ""}" ${p.pick || ""} ${p.tip || ""}>
            <rect class="hit" x="${L + slot * i}" y="${T - 10}" width="${slot}" height="${y(0) - T + 10}" rx="4"/>${mark}${top}</g>${name}`;
    }).join("");
    const compare = runs(points.map((p, i) => p.compare == null ? null : [L + slot * i + slot / 2, y(Number(p.compare))]))
        .map(run => run.length > 1 ? `<path class="line compare" d="${pathOf(run)}"/>` : `<circle class="dot compare" cx="${run[0][0]}" cy="${run[0][1]}" r="2.5"/>`).join("");
    const rule = average == null ? "" : `<line class="avg" x1="${L}" x2="${W - R}" y1="${y(Number(average))}" y2="${y(Number(average))}"/>`;
    return `<svg class="fin-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(what)}" style="--hatch-income:url(#${id}-income)">
        ${hatchDefs(id, ["income"])}${gridSvg(f, scale, y)}${rule}${slots}${compare}</svg>`;
}

// One line over time with the range it usually stays in as a soft band, its
// recent average as a dashed line, and a ring on any period that fell outside
// the usual range. The ranges and the flags come from the server; this only
// draws them.
// points: [{ label, value, avg, band: { low, high } | null, partial,
//            unusual: "low" | "high" | null, tip }]
export function trendChart(points, { width, id, what = "Income trend" }) {
    const f = frame(width, { tall: 250, short: 210 }), { W, H, L, R, T, B } = f;
    const values = points.flatMap(p => [p.value, p.avg, p.band?.high]).filter(v => v != null).map(Number);
    const scale = axisScale(0, Math.max(0, ...values));
    const y = v => T + (scale.top - v) / (scale.top - scale.bottom) * (H - T - B);
    const slot = (W - L - R) / points.length, x = i => L + slot * i + slot / 2;
    const every = Math.max(1, Math.ceil(60 / slot));
    const bands = runs(points.map((p, i) => p.band ? [i, p.band] : null)).filter(run => run.length > 1).map(run => {
        const up = run.map(([i, b]) => `${x(i).toFixed(1)},${y(Number(b.high)).toFixed(1)}`);
        const down = [...run].reverse().map(([i, b]) => `${x(i).toFixed(1)},${y(Number(b.low)).toFixed(1)}`);
        return `<polygon class="band" points="${[...up, ...down].join(" ")}"/>`;
    }).join("");
    const average = runs(points.map((p, i) => p.avg == null ? null : [x(i), y(Number(p.avg))]))
        .map(run => run.length > 1 ? `<path class="line avg" d="${pathOf(run)}"/>` : "").join("");
    const finished = points.map((p, i) => p.partial || p.value == null ? null : [x(i), y(Number(p.value))]);
    const line = runs(finished).map(run => run.length > 1 ? `<path class="line income" d="${pathOf(run)}"/>` : "").join("");
    // A dot on every point while there are few enough to tell apart; with
    // more, only the latest and the unusual ones.
    const dense = points.length > 31, lastFinished = finished.map((p, i) => p && i).filter(i => i !== null && i !== false).pop();
    const dots = points.map((p, i) => {
        if (p.value == null) return "";
        const cx = x(i), cy = y(Number(p.value));
        const ring = p.unusual ? `<circle class="ring ${p.unusual}" cx="${cx}" cy="${cy}" r="8"/>` : "";
        const dot = p.partial ? `<circle class="dot projected" cx="${cx}" cy="${cy}" r="4"/>`
            : !dense || i === lastFinished ? `<circle class="dot income" cx="${cx}" cy="${cy}" r="4"/>` : "";
        return ring + dot;
    }).join("");
    const slots = points.map((p, i) => `<g class="slot" ${p.tip || ""}><rect class="hit" x="${L + slot * i}" y="${T - 10}" width="${slot}" height="${y(0) - T + 10}" rx="4"/></g>`
        // Every few points, and always the last, unless that would sit on top of the one before it.
        + (i === points.length - 1 || (i % every === 0 && points.length - 1 - i >= every) ? `<text class="tick" x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(p.label)}</text>` : "")).join("");
    return `<svg class="fin-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(what)}">
        ${gridSvg(f, scale, y)}${bands}${average}${line}${dots}${slots}</svg>`;
}

// Income and expenses side by side for each month, with profit as a line
// over them. All three are KSh on the one axis.
// points: [{ label, income, expenses, profit, partial, tip }]; null = not recorded.
export function monthsChart(points, { width, id, what = "Income, expenses and profit by month" }) {
    const f = frame(width, { tall: 260, short: 220 }), { W, H, L, R, T, B } = f;
    const values = points.flatMap(p => [p.income, p.expenses, p.profit]).filter(v => v != null).map(Number);
    const scale = axisScale(Math.min(0, ...values), Math.max(0, ...values));
    const y = v => T + (scale.top - v) / (scale.top - scale.bottom) * (H - T - B);
    const slot = (W - L - R) / points.length, bar = Math.max(3, Math.min(22, slot * 0.28));
    const slots = points.map((p, i) => {
        const cx = L + slot * i + slot / 2, cls = p.partial ? " partial" : "";
        return `<g class="slot" ${p.tip || ""}><rect class="hit" x="${L + slot * i}" y="${T - 10}" width="${slot}" height="${H - T - B + 10}" rx="4"/>
            ${p.income == null ? "" : barPath(cx - bar - 1, bar, y(Number(p.income)), y(0), `bar income${cls}`, i)}
            ${p.expenses == null ? "" : barPath(cx + 1, bar, y(Number(p.expenses)), y(0), `bar expenses${cls}`, i)}</g>
            <text class="tick" x="${cx}" y="${H - 8}" text-anchor="middle">${esc(p.label)}</text>`;
    }).join("");
    const profit = points.map((p, i) => p.profit == null ? null : [L + slot * i + slot / 2, y(Number(p.profit))]);
    const line = runs(profit).map(run => (run.length > 1 ? `<path class="line profit" d="${pathOf(run)}"/>` : "") +
        run.map(p => `<circle class="dot profit" cx="${p[0]}" cy="${p[1]}" r="4"/>`).join("")).join("");
    // The latest profit carries its figure.
    let last = -1;
    profit.forEach((p, i) => { if (p) last = i; });
    let end = "";
    if (last >= 0) {
        const p = points[last], tallest = Math.min(profit[last][1], ...[p.income, p.expenses].filter(v => v != null).map(v => y(Number(v))));
        end = `<text class="val" x="${profit[last][0]}" y="${Math.max(T - 8, tallest - 8)}" text-anchor="${profit[last][0] > W - R - 50 ? "end" : "middle"}">${shortMoney(Number(p.profit))} profit</text>`;
    }
    return `<svg class="fin-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(what)}" style="--hatch-income:url(#${id}-income);--hatch-expenses:url(#${id}-expenses)">
        ${hatchDefs(id, ["income", "expenses"])}${gridSvg(f, scale, y)}${slots}${line}${end}</svg>`;
}

// The month's income adding up day by day, against last month's by the same
// day, the month's target, and where the month is heading.
// model: { days: [{ name, total, last }], target: { amount, label } | null,
//          projected: number | null, thisName, lastName }
//   total = income from the 1st up to and including that day (null for days
//   not reached yet);  last = the same for last month (null when unknown)
const paceCharts = new WeakMap();   // chart box -> what its pointer handler needs

export function mountPace(box, model) {
    const f = frame(box.clientWidth, { tall: 270, short: 220 }), { W, H, L, R, T, B } = f;
    const days = model.days, n = days.length;
    const reached = days.reduce((k, d, i) => (d.total != null ? i : k), -1);
    const values = days.flatMap(d => [d.total, d.last]).filter(v => v != null).map(Number);
    const scale = axisScale(0, Math.max(0, ...values, Number(model.target?.amount || 0), Number(model.projected || 0)));
    const x = i => L + (n === 1 ? 0.5 : i / (n - 1)) * (W - L - R);
    const y = v => T + (scale.top - v) / (scale.top - scale.bottom) * (H - T - B);
    const names = days.map((d, i) => i === 0 || (i + 1) % 5 === 0
        ? `<text class="tick" x="${x(i)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : "middle"}">${i + 1}</text>` : "").join("");
    const lastRun = runs(days.map((d, i) => d.last == null ? null : [x(i), y(Number(d.last))]))
        .map(run => run.length > 1 ? `<path class="line compare" d="${pathOf(run)}"/>` : "").join("");
    const thisRun = days.slice(0, reached + 1).map((d, i) => [x(i), y(Number(d.total || 0))]);
    let now = "";
    if (thisRun.length) {
        const [ex, ey] = thisRun[thisRun.length - 1];
        const area = thisRun.length > 1 ? `<path class="area income" d="${pathOf(thisRun)} L${ex.toFixed(1)},${y(0)} L${thisRun[0][0].toFixed(1)},${y(0)} Z"/>` : "";
        const stroke = thisRun.length > 1 ? `<path class="line income" d="${pathOf(thisRun)}"/>` : "";
        const left = ex > L + 110;
        // Above the dot, unless last month's line runs just above it there:
        // then below, inside the shaded area, so the two never cross.
        const lastHere = days[reached].last, crowded = lastHere != null && y(Number(lastHere)) < ey && ey - y(Number(lastHere)) < 48;
        const ly = crowded ? Math.min(y(0) - 6, ey + 20) : Math.max(T + 4, ey - 9);
        now = `${area}${stroke}<circle class="dot income" cx="${ex}" cy="${ey}" r="4.5"/>
            <text class="val now" x="${ex + (left ? -9 : 9)}" y="${ly}" text-anchor="${left ? "end" : "start"}">${esc(money(days[reached].total))}</text>`;
    }
    let ahead = "";
    if (model.projected != null && reached >= 0 && reached < n - 1) {
        const px = x(n - 1), py = y(Number(model.projected));
        // No figure on the chart for this one: the lines converge at the right
        // edge and a label there is crossed by them. The legend carries it.
        ahead = `<path class="line projected" d="M${x(reached).toFixed(1)},${y(Number(days[reached].total)).toFixed(1)} L${px.toFixed(1)},${py.toFixed(1)}"/>
            <circle class="dot projected" cx="${px}" cy="${py}" r="4"/>`;
    }
    const goal = model.target ? `<line class="target" x1="${L}" x2="${W - R}" y1="${y(Number(model.target.amount))}" y2="${y(Number(model.target.amount))}"/>
        <text class="val soft" x="${W - R - 4}" y="${y(Number(model.target.amount)) + 13}" text-anchor="end">${esc(model.target.label)} ${shortMoney(Number(model.target.amount))}</text>` : "";
    box.innerHTML = `<svg class="fin-chart fin-pace" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(model.thisName)} income adding up day by day">
        ${gridSvg(f, scale, y)}${names}${goal}${lastRun}${ahead}${now}
        <g class="hover" hidden><line class="rule" y1="${T}" y2="${y(0)}"/><circle class="dot compare" r="4"/><circle class="dot income" r="4"/></g>
        <rect class="hit cross" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"/></svg>`;
    paceCharts.set(box, { model, W, L, R, x, y, n });
    if (box.dataset.finPace) return;
    box.dataset.finPace = "1";
    box.dataset.ownTip = "1";
    // Moving over the chart: a rule on the nearest day, a dot on each line,
    // and both running totals in the floating label.
    const move = e => {
        const m = paceCharts.get(box), svg = box.querySelector("svg");
        if (!m || !svg) return;
        const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) / r.width * m.W;
        const i = Math.max(0, Math.min(m.n - 1, Math.round((px - m.L) / (m.W - m.L - m.R) * (m.n - 1))));
        const d = m.model.days[i], hover = svg.querySelector(".hover");
        hover.removeAttribute("hidden");
        svg.classList.add("is-hovering");
        hover.querySelector(".rule").setAttribute("x1", m.x(i));
        hover.querySelector(".rule").setAttribute("x2", m.x(i));
        [["income", d.total], ["compare", d.last]].forEach(([cls, v]) => {
            const dot = hover.querySelector(`.dot.${cls}`);
            dot.toggleAttribute("hidden", v == null);
            if (v != null) { dot.setAttribute("cx", m.x(i)); dot.setAttribute("cy", m.y(Number(v))); }
        });
        const gap = d.total != null && d.last != null ? Number(d.total) - Number(d.last) : null;
        showTip(tipHtml(d.name, [
            { key: "income", label: `${m.model.thisName}, up to this day`, value: d.total == null ? "not reached yet" : money(d.total) },
            { key: "compare", label: `${m.model.lastName}, up to the same day`, value: d.last == null ? "no data" : money(d.last) },
        ], gap == null ? "" : gap === 0 ? "Level with last month" : `${gap > 0 ? "▲ Ahead of" : "▼ Behind"} last month by ${money(Math.abs(gap))}`), e.clientX, e.clientY, box.closest(".fin-card"));
    };
    const leave = () => {
        box.querySelector(".hover")?.setAttribute("hidden", "");
        box.querySelector("svg")?.classList.remove("is-hovering");
        hideTip();
    };
    box.addEventListener("pointermove", move);
    box.addEventListener("pointerdown", move);
    box.addEventListener("pointerleave", leave);
}

// A small trend line for a headline tile: no axis, the latest point marked.
// values: numbers, null for a gap.
export function sparkline(values, { width = 120, height = 30 } = {}) {
    const known = values.filter(v => v != null).map(Number);
    if (known.length < 3) return "";
    const low = Math.min(...known), high = Math.max(...known), pad = 4;
    const x = i => pad + i / (values.length - 1) * (width - pad * 2);
    const y = v => high === low ? height / 2 : pad + (1 - (v - low) / (high - low)) * (height - pad * 2);
    const points = values.map((v, i) => v == null ? null : [x(i), y(Number(v))]);
    const end = points.filter(Boolean).pop();
    return `<svg class="fin-spark" viewBox="0 0 ${width} ${height}" aria-hidden="true">
        ${runs(points).map(run => run.length > 1 ? `<path d="${pathOf(run)}"/>` : "").join("")}
        <circle cx="${end[0].toFixed(1)}" cy="${end[1].toFixed(1)}" r="2.6"/></svg>`;
}

// Redraw a page's charts when the window changes width (they are drawn at
// the width they are shown at). `viewId` is the page; `draw` redraws it.
export function redrawOnResize(viewId, draw) {
    let timer = null;
    window.addEventListener("resize", () => {
        clearTimeout(timer);
        timer = setTimeout(() => { if (!document.getElementById(viewId).hidden) draw(); }, 150);
    });
}
