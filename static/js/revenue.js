import { authHeaders, showView, registerRoute, navigate } from "./common.js";

function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Every figure here comes pre-formatted from the API (KSh 10,810 / -KSh
// 3,220): the page never formats or rounds money itself, so it can't
// disagree with a report that was frozen on the server.

const MODE_LABEL = {
    same_weekday_last_week: "Same weekday last week",
    previous_day: "Previous day",
    baseline_median: "Median of recent same weekdays",
    previous_week: "Previous week",
    same_week_last_year: "Same week last year",
    previous_month: "Previous month",
    same_month_last_year: "Same month last year",
    previous_year: "Previous year",
    custom: "Custom period…",
};

const DIRECTION = {
    "↑": { cls: "rev-up", text: "▲ Up" },
    "↓": { cls: "rev-down", text: "▼ Down" },
    "→": { cls: "rev-flat", text: "► Flat" },
};

const STATUS = {
    red: { text: "● Investigate", cls: "red" },
    amber: { text: "● Attention", cls: "amber" },
    green: { text: "● Positive", cls: "green" },
};

const state = {
    gran: "day",
    anchor: "",          // "" = yesterday (the server's default)
    mode: "",            // "" = the granularity's default
    custom: "",
    sort: "largest_loss",  // a preset button
    col: null,             // a clicked column header overrides the preset
    colDir: 1,
    series: "daily_30",
    cardModes: {},
    report: null,        // the last /sites response, re-sorted in place
};

// The server returns every site, so a sort is just a re-order.
const PRESETS = {
    largest_loss: { col: "abs_change", dir: 1 },
    largest_gain: { col: "abs_change", dir: -1 },
    largest_pct_decline: { col: "pct_change", dir: 1 },
    largest_pct_growth: { col: "pct_change", dir: -1 },
    revenue: { col: "current", dir: -1 },
};

let loadToken = 0;

async function api(path) {
    const res = await fetch(path, { headers: authHeaders() });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Couldn't load (${res.status})`);
    }
    return res.json();
}

const $ = id => document.getElementById(id);

function directionHtml(d, label) {
    if (label === "No comparable data") return `<span class="rev-flat">No comparable data</span>`;
    const dir = DIRECTION[d] || DIRECTION["→"];
    return `<span class="${dir.cls}">${dir.text}</span>`;
}

function shortDay(iso) {
    return new Date(`${iso}T12:00:00`).toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "2-digit" });
}

// ---- 2. Headline cards ----

function cardHtml(c) {
    const d = c.display;
    const modes = c.available_modes.filter(m => m !== "custom");
    const change = c.previous == null
        ? `<div class="rev-card-change rev-flat">No comparable data</div>`
        : `<div class="rev-card-change">${directionHtml(c.direction, c.label)}
              ${esc(d.abs_change)}${c.pct_change != null ? ` (${esc(d.pct_change)})` : ""}${c.label ? ` · ${esc(c.label)}` : ""}</div>`;
    const select = modes.length > 1
        ? `<select data-card="${esc(c.key)}" aria-label="Compare ${esc(c.title)} with">
              ${modes.map(m => `<option value="${m}" ${m === c.mode ? "selected" : ""}>${esc(MODE_LABEL[m] || m)}</option>`).join("")}
           </select>` : "";
    return `
        <div class="rev-card">
            <div class="rev-card-title">${esc(c.title)}</div>
            <div class="rev-card-period">${esc(c.period_label)}</div>
            <div class="rev-card-value">${esc(d.current)}</div>
            <div class="rev-card-vs">${c.previous == null ? "" : `${esc(d.previous)} · `}${esc(c.comparison_label)}</div>
            ${change}
            ${c.notes.length ? `<div class="rev-card-notes">${c.notes.map(esc).join("<br>")}</div>` : ""}
            ${select}
        </div>`;
}

function renderCards(o) {
    $("rev-header-line").textContent = o.header;
    const asOf = o.as_of ? new Date(o.as_of).toLocaleTimeString("en-GB", { timeZone: "Africa/Nairobi", hour: "2-digit", minute: "2-digit" }) : null;
    $("rev-header-meta").textContent = [
        "Hotspot sales only · Nairobi time",
        o.history_start ? `history from ${shortDay(o.history_start)}` : null,
        asOf ? `updated ${asOf}` : null,
    ].filter(Boolean).join(" · ");

    const n = o.attention_count;
    const attention = `
        <div class="rev-card is-attention ${n > 0 ? "has-red" : ""}" id="rev-attention-card" role="button" tabindex="0">
            <div class="rev-card-title">Needs attention</div>
            <div class="rev-card-period">Last completed day</div>
            <div class="rev-attention-count">${n == null ? "—" : `● ${n}`}</div>
            <div class="rev-card-vs">${n == null ? "Couldn't be worked out" : n === 1 ? "site to investigate" : "sites to investigate"}</div>
        </div>`;
    $("rev-cards").innerHTML = o.cards.map(cardHtml).join("") + attention;
    $("rev-summary").innerHTML = o.summary.length
        ? o.summary.map(s => `<p>${esc(s)}</p>`).join("")
        : `<div class="rev-empty">Nothing to summarise yet.</div>`;
}

// ---- 3. Key findings ----

function renderFindings(report, trend) {
    const out = [];
    const c = report.contributions;
    const r = report.rankings;
    const net = report.network;

    if (net.previous != null) {
        out.push(finding("Net change", `${esc(net.display.abs_change)}${net.pct_change != null ? ` (${esc(net.display.pct_change)})` : ""} across all sites`));
    }
    if (r) {
        const loss = r.largest_losses[0], gain = r.largest_gains[0];
        out.push(finding("Largest loss", loss
            ? `${esc(loss.name)} · ${esc(loss.display.abs_change)}${loss.pct_change != null ? ` (${esc(loss.display.pct_change)})` : ""}`
            : "No site fell"));
        out.push(finding("Largest gain", gain
            ? `${esc(gain.name)} · ${esc(gain.display.abs_change)}${gain.pct_change != null ? ` (${esc(gain.display.pct_change)})` : ""}`
            : "No site rose"));
    }
    if (c) {
        out.push(finding("Decline contributors", sideText(c.decline, "gross decline")));
        out.push(finding("Gain contributors", sideText(c.gain, "gross gain")));
    }
    if (trend) {
        const unusual = trend.points.filter(p => p.unusual);
        out.push(finding("Unusual points", unusual.length
            ? unusual.map(p => `${esc(p.label)} (${p.unusual})`).join(", ")
            : "None in this window",
            unusual.length ? unusual[0].rule : "Outside median ± k×MAD of the previous 28 completed days"));
        out.push(finding("Trend", `${esc(trend.trend.label)}`, trend.trend.rule));
    }
    $("rev-findings").innerHTML = out.length ? out.join("") : `<div class="rev-empty">No findings for this period.</div>`;
}

function finding(label, html, rule) {
    return `<div class="rev-finding"><div class="rev-finding-label">${esc(label)}</div>${html}${rule ? `<span class="rev-rule">Rule: ${esc(rule)}</span>` : ""}</div>`;
}

function sideText(side, noun) {
    if (!side.site_count) return `No site moved this way`;
    const names = side.contributors.map(x => `${esc(x.name)} (${esc(x.display.share)})`).join(", ");
    const other = side.other ? ` and ${side.other.count} other site${side.other.count === 1 ? "" : "s"} (${esc(side.other.display.share)})` : "";
    return `${names}${other} account for the ${esc(side.gross_display)} ${noun}. Listed: the fewest sites reaching ${side.cutoff_pct}%.`;
}

// ---- 5. Attention ----

function renderAttention(a) {
    $("rev-attention-sub").textContent = `${a.day_label || ""} · ${a.counts.red} investigate · ${a.counts.amber} attention · ${a.counts.green} positive`;
    if (!a.sites.length) {
        $("rev-attention").innerHTML = `<div class="rev-empty">No site is flagged for ${esc(a.day_label || "this day")}.</div>`;
        return;
    }
    const link = s => `site-detail/${s.site_id}/day/${a.day}`;
    $("rev-attention").innerHTML = a.sites.map(s => {
        const st = STATUS[s.status];
        return `
        <div class="rev-alert ${st.cls}" data-link="${esc(link(s))}" tabindex="0">
            <div class="rev-alert-head">
                <span class="rev-badge ${st.cls}">${st.text}</span>
                <span class="rev-alert-name">${esc(s.name)}</span>
                <span class="rev-alert-value">${esc(s.display.value)} on ${esc(a.day_label)}</span>
            </div>
            ${s.alerts.map(x => `<div class="rev-alert-line">${esc(x.text)}<span class="rev-rule" title="${esc(JSON.stringify(x.values))}">${esc(x.rule_id)}</span></div>`).join("")}
        </div>`;
    }).join("") + `<div class="rev-footnote">${esc(a.basis)}.</div>`;
}

// ---- 6. Site table ----

// null = "no value": those rows go last in either direction.
const COLS = {
    name: r => r.name.toLowerCase(),
    current: r => r.current,
    previous: r => r.previous,
    abs_change: r => r.abs_change,
    pct_change: r => r.pct_change,
    share: r => r.share,
    trend7: r => r.trend7?.pct_change ?? null,
    status: r => ({ red: 0, amber: 1, green: 2 }[r.status] ?? null),
};

function activeSort() {
    return state.col ? { col: state.col, dir: state.colDir } : PRESETS[state.sort];
}

function sortedSites() {
    const { col, dir } = activeSort();
    const key = COLS[col];
    return [...state.report.sites].sort((a, b) => {
        const ka = key(a), kb = key(b);
        if (ka == null || kb == null) return (ka == null) - (kb == null) || a.name.localeCompare(b.name);
        return (ka < kb ? -1 : ka > kb ? 1 : 0) * dir || a.name.localeCompare(b.name);
    });
}

function renderSites(report) {
    state.report = report;
    const body = $("rev-sites-body");
    if (!report.sites.length) {
        body.innerHTML = `<tr><td colspan="8">No site data for this period.</td></tr>`;
        return;
    }
    const { col, dir } = activeSort();
    document.querySelectorAll("#rev-presets button").forEach(b => b.classList.toggle("is-active", !state.col && b.dataset.sort === state.sort));
    document.querySelectorAll("#rev-sites-table th[data-col]").forEach(th => {
        th.classList.toggle("is-sorted", th.dataset.col === col);
        th.classList.toggle("asc", th.dataset.col === col && dir === 1);
    });
    body.innerHTML = sortedSites().map(r => {
        const d = r.display;
        const pct = r.label && r.pct_change == null
            ? `<span class="rev-label">${esc(r.label)}</span>`
            : r.label === "Silent" || r.label === "No sales yet"
                ? `<span class="rev-label" title="${esc(r.label_rule || "")}">${esc(r.label)}</span>`
                : esc(d.pct_change ?? "—");
        const t = r.trend7;
        const trend = t ? `${directionHtml(t.direction, t.label)} ${t.pct_change != null ? esc(`${t.pct_change > 0 ? "+" : ""}${t.pct_change}%`) : esc(t.label || "")}` : `<span class="rev-label">—</span>`;
        const st = r.status ? `<span class="rev-badge ${STATUS[r.status].cls}" title="${esc((r.status_alerts || []).map(a => a.text).join("; "))}">${STATUS[r.status].text}</span>` : "";
        const share = r.share != null ? `${esc(d.share)} <span class="rev-label">${r.share_side === "decline" ? "of decline" : "of gain"}</span>` : "";
        return `<tr data-link="${esc(r.link)}">
            <td>${esc(r.name)}${r.active ? "" : ` <span class="rev-label">· inactive</span>`}</td>
            <td class="num">${esc(d.current)}</td>
            <td class="num">${esc(d.previous ?? "—")}</td>
            <td class="num">${esc(d.abs_change ?? "—")}</td>
            <td class="num">${pct}</td>
            <td class="num">${share}</td>
            <td>${trend}</td>
            <td>${st}</td>
        </tr>`;
    }).join("");
    $("rev-sites-foot").textContent =
        `${report.site_count} sites · ${report.active_site_count} active (average of at least the active-site minimum over the last 14 completed days). ` +
        `Status is as of ${report.status_day ? shortDay(report.status_day) : "the last completed day"}, whatever period is shown.`;
}

// ---- 7. Rankings ----

const RANKS = [
    ["top_revenue", "Top 5 by revenue", "Current period", r => r.display.current],
    ["largest_losses", "Largest losses", "KSh lost", r => r.display.abs_change],
    ["largest_gains", "Largest gains", "KSh gained", r => r.display.abs_change],
    ["largest_pct_declines", "Largest % declines", "Only sites that earned at least the % floor before", r => r.display.pct_change],
    ["fastest_pct_growth", "Fastest % growth", "Only sites that earned at least the % floor before", r => r.display.pct_change],
];

function renderRankings(report) {
    const box = $("rev-rankings");
    if (!report.rankings) {
        box.innerHTML = `<div class="rev-empty">Rankings need a comparison period with data.</div>`;
        return;
    }
    box.innerHTML = RANKS.map(([key, title, hint, val]) => {
        const rows = report.rankings[key];
        return `<div class="rev-rank"><h3>${esc(title)}</h3><div class="rev-rank-hint">${esc(hint)}</div>
            ${rows.length ? `<ol>${rows.map(r => `<li data-link="${esc(r.link)}"><span class="rev-rank-name">${esc(r.name)}</span><span class="rev-rank-val">${esc(val(r) ?? "—")}</span></li>`).join("")}</ol>`
                          : `<div class="rev-empty">None</div>`}</div>`;
    }).join("");
}

// ---- 8. Trend chart ----

function renderTrend(t) {
    const box = $("rev-trend");
    const pts = t.points;
    if (!pts.length) {
        box.innerHTML = `<div class="rev-empty">No history for this series yet.</div>`;
        return;
    }
    const W = 720, H = 240, L = 46, R = 12, T = 12, B = 26;
    const maxVal = Math.max(1, ...pts.map(p => Math.max(p.value, p.band ? p.band.high : 0, p.rolling_avg || 0)));
    const top = niceCeil(maxVal);
    const x = i => L + (pts.length === 1 ? (W - L - R) / 2 : (i / (pts.length - 1)) * (W - L - R));
    const y = v => T + (1 - v / top) * (H - T - B);

    const grid = [0, 0.25, 0.5, 0.75, 1].map(f => {
        const v = top * f;
        return `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${v >= 1000 ? `${Math.round(v / 100) / 10}k` : Math.round(v)}</text>`;
    }).join("");

    // The band is drawn in runs of consecutive points that have one.
    const bands = [];
    let run = [];
    pts.forEach((p, i) => {
        if (p.band) run.push([i, p.band]);
        else if (run.length) { bands.push(run); run = []; }
    });
    if (run.length) bands.push(run);
    const bandSvg = bands.filter(r => r.length > 1).map(r => {
        const up = r.map(([i, b]) => `${x(i)},${y(b.high)}`);
        const down = [...r].reverse().map(([i, b]) => `${x(i)},${y(b.low)}`);
        return `<polygon class="band" points="${[...up, ...down].join(" ")}"/>`;
    }).join("");

    const solid = pts.filter(p => !p.partial).map(p => `${x(pts.indexOf(p))},${y(p.value)}`).join(" ");
    const avgSegs = [];
    let seg = [];
    pts.forEach((p, i) => {
        if (p.rolling_avg != null) seg.push(`${x(i)},${y(p.rolling_avg)}`);
        else if (seg.length) { avgSegs.push(seg); seg = []; }
    });
    if (seg.length) avgSegs.push(seg);

    const step = Math.max(1, Math.ceil(pts.length / 8));
    const labels = pts.map((p, i) => i % step === 0 || i === pts.length - 1
        ? `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(p.label)}</text>` : "").join("");

    const dots = pts.map((p, i) => {
        const tip = `${p.label}: ${p.display}${p.partial ? " (so far)" : ""}${p.unusual ? ` — unusual ${p.unusual}. ${p.rule}` : ""}`;
        return `<g><title>${esc(tip)}</title>
            <circle class="pt ${p.partial ? "partial" : ""}" cx="${x(i)}" cy="${y(p.value)}" r="3"/>
            ${p.unusual ? `<circle class="unusual ${p.unusual}" cx="${x(i)}" cy="${y(p.value)}" r="7"/>` : ""}</g>`;
    }).join("");

    const tr = t.trend;
    const dirClass = tr.label === "Increasing" ? "rev-up" : tr.label === "Decreasing" ? "rev-down" : "rev-flat";
    const glyph = tr.label === "Increasing" ? "▲ " : tr.label === "Decreasing" ? "▼ " : tr.label === "Stable" ? "► " : "";
    const hasBand = pts.some(p => p.band), hasAvg = pts.some(p => p.rolling_avg != null);
    box.innerHTML = `
        <svg class="rev-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Revenue trend">
            ${grid}${bandSvg}
            ${avgSegs.map(s => `<polyline class="avg" points="${s.join(" ")}"/>`).join("")}
            <polyline class="line" points="${solid}"/>
            ${dots}${labels}
        </svg>
        <div class="rev-legend">
            <span>● Revenue${pts.some(p => p.partial) ? " (○ so far)" : ""}</span>
            ${hasAvg ? "<span>- - 7-day average</span>" : ""}
            ${hasBand ? "<span>▒ Usual range (median ± k×MAD, previous 28 days)</span>" : ""}
            ${t.unusual_count ? `<span>◯ Unusual (${t.unusual_count})</span>` : ""}
        </div>
        <div class="rev-trend-label"><span class="${dirClass}">${glyph}${esc(tr.label)}</span></div>
        <div class="rev-trend-rule">Rule: ${esc(tr.rule)}${tr.based_on ? ` · ${tr.based_on} completed periods` : ""}</div>`;
}

function niceCeil(v) {
    const mag = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * mag >= v) return m * mag;
    return 10 * mag;
}

// ---- Loading ----

function reportQuery() {
    const q = new URLSearchParams({ granularity: state.gran });
    if (state.anchor) q.set("anchor", state.anchor);
    if (state.mode) q.set("mode", state.mode);
    if (state.mode === "custom" && state.custom) q.set("custom_anchor", state.custom);
    return q;
}

function overviewQuery() {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(state.cardModes)) if (v) q.set(`${k}_mode`, v);
    return q;
}

function fail(id, err) {
    $(id).innerHTML = `<div class="rev-empty">${esc(err.message || "Couldn't load.")}</div>`;
}

async function loadReport() {
    const token = ++loadToken;
    const view = $("view-revenue");
    view.classList.add("is-loading");
    const guard = fn => d => { if (token === loadToken) fn(d); };

    const overview = api(`/monitoring/revenue-report/overview?${overviewQuery()}`);
    const sites = api(`/monitoring/revenue-report/sites?${reportQuery()}`);
    const alerts = api("/monitoring/revenue-report/alerts");
    const trend = api(`/monitoring/revenue-report/trend?series=${state.series}`);
    const findingsTrend = state.series === "daily_30" ? trend : api("/monitoring/revenue-report/trend?series=daily_30");

    overview.then(guard(renderCards)).catch(e => { fail("rev-cards", e); $("rev-header-line").textContent = "Revenue report unavailable"; });
    alerts.then(guard(renderAttention)).catch(e => fail("rev-attention", e));
    trend.then(guard(renderTrend)).catch(e => fail("rev-trend", e));

    try {
        const [report, ft] = await Promise.all([sites, findingsTrend.catch(() => null)]);
        if (token !== loadToken) return;
        renderPeriod(report);
        renderSites(report);
        renderRankings(report);
        renderFindings(report, ft);
    } catch (e) {
        fail("rev-findings", e);
        $("rev-sites-body").innerHTML = `<tr><td colspan="8">${esc(e.message)}</td></tr>`;
    } finally {
        await Promise.allSettled([overview, alerts, trend]);
        if (token === loadToken) view.classList.remove("is-loading");
    }
}

function renderPeriod(report) {
    const p = report.period;
    const sel = $("rev-mode");
    sel.innerHTML = p.available_modes.map(m => `<option value="${m}" ${m === p.mode ? "selected" : ""}>${esc(MODE_LABEL[m] || m)}</option>`).join("");
    $("rev-custom-wrap").hidden = p.mode !== "custom";
    // Show the day actually reported, so "yesterday" is a date, not a blank.
    $("rev-anchor").value = p.current_range[0].slice(0, 10);
    const bits = [`<strong>${esc(p.period_label)}</strong> vs ${esc(p.comparison_label)}`];
    if (p.is_partial) bits.push("period still in progress — compared to the same point");
    if (!p.equal_length) bits.push(`<span class="rev-warn">unequal lengths</span>`);
    if (p.coverage === "partial") bits.push(`<span class="rev-warn">comparison only partly covered by history</span>`);
    $("rev-period-note").innerHTML = bits.join(" · ") + (p.notes.length ? `<br><span class="rev-warn">${p.notes.map(esc).join("<br>")}</span>` : "");
}

// ---- Wiring ----

export function initRevenue() {
    document.querySelectorAll("#rev-gran button").forEach(btn => {
        btn.addEventListener("click", () => {
            state.gran = btn.dataset.gran;
            state.mode = "";
            state.custom = "";
            document.querySelectorAll("#rev-gran button").forEach(b => b.setAttribute("aria-pressed", String(b === btn)));
            loadReport();
        });
    });
    $("rev-anchor").addEventListener("change", e => { state.anchor = e.target.value; loadReport(); });
    $("rev-mode").addEventListener("change", e => {
        state.mode = e.target.value;
        $("rev-custom-wrap").hidden = state.mode !== "custom";
        if (state.mode !== "custom") loadReport();
    });
    $("rev-custom").addEventListener("change", e => { state.custom = e.target.value; if (state.custom) loadReport(); });
    $("rev-series").addEventListener("change", e => { state.series = e.target.value; loadReport(); });

    $("rev-cards").addEventListener("change", e => {
        const sel = e.target.closest("select[data-card]");
        if (!sel) return;
        state.cardModes[sel.dataset.card] = sel.value;
        loadReport();
    });
    $("rev-cards").addEventListener("click", e => {
        if (e.target.closest("#rev-attention-card")) $("rev-attention").scrollIntoView({ behavior: "smooth", block: "start" });
    });

    $("rev-presets").addEventListener("click", e => {
        const btn = e.target.closest("button[data-sort]");
        if (!btn) return;
        state.sort = btn.dataset.sort;
        state.col = null;
        if (state.report) renderSites(state.report);
    });
    document.querySelector("#rev-sites-table thead").addEventListener("click", e => {
        const th = e.target.closest("th[data-col]");
        if (!th || !state.report) return;
        if (state.col === th.dataset.col) state.colDir *= -1;
        else { state.col = th.dataset.col; state.colDir = th.classList.contains("num") ? -1 : 1; }
        renderSites(state.report);
    });

    const goLink = e => {
        const el = e.target.closest("[data-link]");
        if (el && el.dataset.link) navigate(el.dataset.link);
    };
    ["rev-sites-body", "rev-attention", "rev-rankings"].forEach(id => $(id).addEventListener("click", goLink));

    registerRoute("revenue", () => {
        showView("view-revenue");
        loadReport();
    });
}
