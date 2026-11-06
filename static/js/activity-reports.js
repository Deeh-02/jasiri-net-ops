import { authHeaders, showView, registerRoute, navigate, can, formatDate } from "./common.js";
import { api, esc, formatDay, outcomePill, setError } from "./activity-common.js";

const PAGE_SIZE = 25;
const $ = (id) => document.getElementById(id);

let filterOptions = null;
let offset = 0;
let loadSeq = 0;           // a slow answer to an older request must not overwrite a newer one
let currentDetail = null;

function currentFilters() {
    const f = {
        q: $("act-f-q").value.trim(), date_from: $("act-f-from").value, date_to: $("act-f-to").value,
        category_id: $("act-f-category").value, type_id: $("act-f-type").value,
        location_id: $("act-f-location").value, technician_id: $("act-f-tech").value,
        outcome: $("act-f-outcome").value,
    };
    return Object.fromEntries(Object.entries(f).filter(([, v]) => v));
}

function queryString(extra = {}) {
    return new URLSearchParams({ ...currentFilters(), ...extra }).toString();
}

function fillSelect(id, allLabel, items, keep) {
    const select = $(id);
    select.innerHTML = `<option value="">${esc(allLabel)}</option>` + items.map(i => `<option value="${esc(i.value)}">${esc(i.label)}</option>`).join("");
    select.value = items.some(i => String(i.value) === keep) ? keep : "";
}

function fillFilters() {
    const o = filterOptions;
    fillSelect("act-f-category", "All", o.categories.map(c => ({ value: c.id, label: c.name })), $("act-f-category").value);
    fillTypeFilter();
    fillSelect("act-f-location", "All", o.locations.map(l => ({ value: l.id, label: l.name })), $("act-f-location").value);
    fillSelect("act-f-tech", "All", o.technicians.map(t => ({ value: t.id, label: t.name })), $("act-f-tech").value);
    fillSelect("act-f-outcome", "All", o.outcomes.map(x => ({ value: x.key, label: x.label })), $("act-f-outcome").value);
    // Someone who can only see their own reports gains nothing from this one.
    $("act-f-tech-wrap").hidden = !can("activity_log", "view_all");
}

function fillTypeFilter() {
    const categoryId = $("act-f-category").value;
    const categories = filterOptions.categories.filter(c => !categoryId || String(c.id) === categoryId);
    fillSelect("act-f-type", "All", categories.flatMap(c => c.types.map(t => ({ value: t.id, label: categoryId ? t.name : `${c.name}: ${t.name}` }))), $("act-f-type").value);
}

async function loadReports() {
    const seq = ++loadSeq;
    setError($("act-reports-error"), "");
    const res = await api(`/activity/reports?${queryString({ limit: PAGE_SIZE, offset })}`);
    if (seq !== loadSeq) return;
    if (!res.ok) { setError($("act-reports-error"), res.error); return; }

    const { total, reports } = res.data;
    $("act-reports-rows").innerHTML = reports.length ? reports.map(r => `
        <tr class="act-row" data-id="${r.id}" tabindex="0">
            <td><span class="act-ref">${esc(r.reference)}</span></td>
            <td>${esc(formatDay(r.activity_date))}</td>
            <td>${esc(r.category)}<div class="act-sub">${esc(r.type)}</div></td>
            <td>${esc(r.area)}</td>
            <td>${esc(r.technicians.map(t => t.name).join(", "))}</td>
            <td>${outcomePill(r.outcome, r.outcome_label)}</td>
        </tr>`).join("")
        : `<tr><td colspan="6" class="act-empty">No reports match.</td></tr>`;

    const from = total ? offset + 1 : 0;
    $("act-pager-text").textContent = `${from}–${Math.min(offset + PAGE_SIZE, total)} of ${total}`;
    $("act-prev-btn").disabled = offset === 0;
    $("act-next-btn").disabled = offset + PAGE_SIZE >= total;
}

function refilter() { offset = 0; loadReports(); }

async function exportCsv() {
    const button = $("act-export-btn");
    button.disabled = true;
    setError($("act-reports-error"), "");
    try {
        const res = await fetch(`/activity/reports/export?${queryString()}`, { headers: authHeaders() });
        if (!res.ok) throw new Error();
        const url = URL.createObjectURL(await res.blob());
        const link = document.createElement("a");
        link.href = url;
        link.download = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || "activity-reports.csv";
        link.click();
        URL.revokeObjectURL(url);
    } catch {
        setError($("act-reports-error"), "Couldn't export. Check your connection and try again.");
    }
    button.disabled = false;
}

// ---- Detail ----------------------------------------------------------------

function row(label, valueHtml) {
    return `<div class="act-detail-row"><dt>${esc(label)}</dt><dd>${valueHtml}</dd></div>`;
}

function text(value) {
    return value ? esc(value).replace(/\n/g, "<br>") : `<span class="act-none">—</span>`;
}

async function openDetail(id) {
    showView("view-activity-report-detail");
    setError($("act-detail-error"), "");
    $("act-detail-body").innerHTML = "";
    $("act-detail-edit").hidden = true;
    $("act-detail-delete").hidden = true;
    $("act-detail-title").textContent = "Report";
    const res = await api(`/activity/reports/${id}`);
    if (!res.ok) {
        if (res.status === 404) { navigate("activity-reports"); return; }
        setError($("act-detail-error"), res.error);
        return;
    }
    const r = currentDetail = res.data;
    $("act-detail-title").textContent = r.reference;
    $("act-detail-edit").hidden = !r.can_edit;
    $("act-detail-delete").hidden = !r.can_delete;
    $("act-detail-body").innerHTML = [
        row("Activity date", esc(formatDay(r.activity_date, { weekday: true }))),
        row("Category", esc(r.category)),
        row("Activity type", esc(r.type)),
        row("Location", r.location ? esc(r.location) : `${esc(r.other_location)} <span class="act-none">(other)</span>`),
        row("Outcome", outcomePill(r.outcome, r.outcome_label)),
        row("Technicians", esc(r.technicians.map(t => t.name).join(", "))),
        row("Time spent", r.time_spent_label ? esc(r.time_spent_label) : `<span class="act-none">Not given</span>`),
        row("Description", text(r.description)),
        row("Remarks", text(r.remarks)),
        row("Submitted", `${esc(r.submitted_by_name)} · ${esc(formatDate(r.submitted_at))}`),
        row("Last edited", r.last_edited_at ? `${esc(r.last_edited_by_name)} · ${esc(formatDate(r.last_edited_at))}` : `<span class="act-none">Not edited</span>`),
    ].join("");
}

async function deleteCurrent() {
    if (!currentDetail) return;
    if (!confirm(`Delete ${currentDetail.reference}? It will disappear from the lists and the totals.`)) return;
    const res = await api(`/activity/reports/${currentDetail.id}`, { method: "DELETE" });
    if (!res.ok) { setError($("act-detail-error"), res.error); return; }
    navigate("activity-reports");
}

// ---- Setup -----------------------------------------------------------------

async function openList() {
    showView("view-activity-reports");
    $("act-reports-new-btn").hidden = !can("activity_log", "submit");
    const res = await api("/activity/filter-options");
    if (res.ok) { filterOptions = res.data; fillFilters(); }
    loadReports();
}

export function initActivityReports() {
    let timer = null;
    $("act-f-q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(refilter, 300); });
    ["act-f-from", "act-f-to", "act-f-location", "act-f-tech", "act-f-outcome", "act-f-type"]
        .forEach(id => $(id).addEventListener("change", refilter));
    $("act-f-category").addEventListener("change", () => { fillTypeFilter(); refilter(); });
    $("act-f-clear").addEventListener("click", () => {
        ["act-f-q", "act-f-from", "act-f-to", "act-f-category", "act-f-location", "act-f-tech", "act-f-outcome"]
            .forEach(id => { $(id).value = ""; });
        if (filterOptions) fillTypeFilter();
        refilter();
    });
    $("act-prev-btn").addEventListener("click", () => { offset = Math.max(0, offset - PAGE_SIZE); loadReports(); });
    $("act-next-btn").addEventListener("click", () => { offset += PAGE_SIZE; loadReports(); });
    $("act-export-btn").addEventListener("click", exportCsv);
    $("act-reports-new-btn").addEventListener("click", () => navigate("activity-submit"));

    const open = (target) => {
        const tr = target.closest(".act-row");
        if (tr) navigate(`activity-reports/${tr.dataset.id}`);
    };
    $("act-reports-rows").addEventListener("click", (e) => open(e.target));
    $("act-reports-rows").addEventListener("keydown", (e) => { if (e.key === "Enter") open(e.target); });

    $("act-detail-back").addEventListener("click", () => navigate("activity-reports"));
    $("act-detail-edit").addEventListener("click", () => currentDetail && navigate(`activity-submit/${currentDetail.id}/edit`));
    $("act-detail-delete").addEventListener("click", deleteCurrent);

    registerRoute("activity-reports", (params) => (params[0] ? openDetail(params[0]) : openList()));
}
