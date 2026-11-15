import {
    authHeaders, showView, registerRoute, navigate, can,
    stateRow, markRefreshing, showMessage, editIconSvg
} from "./common.js";

// Finance > Expenses (Phase 7B). Two parts:
//  - the month list, with edit (category, type, segment, description) and void;
//  - adding: paste messages + cash lines -> the server reads them (/read) ->
//    the user checks and picks categories -> /save. Save sends the same text
//    again plus the user's choices; the server reads the text itself, so an
//    amount, code or date never comes from this page.

const KIND_LABEL = { operating: "Operating", capital: "Capital" };
const METHOD_LABEL = { mpesa: "M-Pesa", bank: "Bank", cash: "Cash" };
const NOT_SAVED_LABEL = {
    unrecognised: "Couldn't read",
    reversal: "Reversal: void the original expense instead",
    money_in: "Money received, not an expense",
    repeated: "Pasted twice: kept once",
};

// The ✨ mark for anything DeepSeek suggested. Drawn as SVG like every other
// icon in the app: an emoji character shows as an empty box where the
// device has no emoji font.
const SPARKLE = `<svg class="fin-exp-sparkle" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
    <path d="M7 1.5l1.2 3.3L11.5 6 8.2 7.2 7 10.5 5.8 7.2 2.5 6l3.3-1.2z"/>
    <path d="M12.5 9.5l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6z"/></svg>`;

let categories = [];
let monthRows = [];
// Whether DeepSeek suggestions are switched on (FINANCE_AI_ENABLED on the
// server); read once when Add expenses is first opened.
let aiEnabled = null;
let editingExpense = null;
// The review in progress: the exact text that was read (sent again on save),
// what the server made of it, and the user's choice per item key.
let review = null;

function esc(value) {
    const d = document.createElement("div");
    d.textContent = value == null ? "" : String(value);
    return d.innerHTML;
}

function money(amount) {
    return "KSh " + Number(amount || 0).toLocaleString("en-KE", { maximumFractionDigits: 2 });
}

// "2026-10-13" -> "13 Oct 2026", read as a calendar day (no time zone shift).
function dayLabel(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function thisMonth() {
    return new Date().toLocaleDateString("en-CA", { timeZone: "Africa/Nairobi" }).slice(0, 7);
}

function today() {
    return new Date().toLocaleDateString("en-CA", { timeZone: "Africa/Nairobi" });
}

async function errorText(res, fallback) {
    if (!res) return "Couldn't reach the server. Check your connection and try again.";
    const body = await res.json().catch(() => ({}));
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]?.msg) return body.detail[0].msg;
    return fallback;
}

function categoryOptions(selectedId, { placeholder = false, includeRetiredId = null } = {}) {
    const shown = categories.filter(c => c.is_active || c.id === includeRetiredId);
    return (placeholder ? `<option value="">Pick a category…</option>` : "") +
        shown.map(c => `<option value="${c.id}" ${c.id === selectedId ? "selected" : ""}>${esc(c.name)}${c.is_active ? "" : " (retired)"}</option>`).join("");
}

async function loadCategories() {
    if (categories.length) return true;
    const res = await fetch("/finance/categories?include_retired=true", { headers: authHeaders() }).catch(() => null);
    if (!res || !res.ok) {
        showMessage("fin-exp-page-msg", await errorText(res, "Couldn't load the expense categories."), true);
        return false;
    }
    categories = await res.json();
    return true;
}

// ---- The month list ----

async function loadMonth() {
    const month = document.getElementById("fin-exp-month").value || thisMonth();
    const includeVoided = document.getElementById("fin-exp-show-voided").checked;
    const tbody = document.getElementById("fin-exp-rows");
    const cols = can("finance", "manage_expenses") ? 8 : 7;
    markRefreshing(tbody, cols, "Loading expenses…");
    const res = await fetch(`/finance/expenses?month=${encodeURIComponent(month)}&include_voided=${includeVoided}`,
        { headers: authHeaders() }).catch(() => null);
    if (!res || !res.ok) {
        document.getElementById("fin-exp-summary").innerHTML = "";
        document.getElementById("fin-exp-categories").innerHTML = "";
        tbody.innerHTML = stateRow(cols, "error", await errorText(res, "Couldn't load expenses. Try again in a moment."));
        return;
    }
    const data = await res.json();
    monthRows = data.rows;
    renderSummary(data.summary);
    renderRows(cols);
}

function renderSummary(summary) {
    const voided = summary.voided_count ? `<div class="summary-item"><strong>${summary.voided_count}</strong> voided</div>` : "";
    // No expenses yet is "no data", not KSh 0 (spec principle 4).
    document.getElementById("fin-exp-summary").innerHTML = summary.count ? `
        <div class="summary-item"><strong>${money(summary.total)}</strong> spent</div>
        <div class="summary-item"><strong>${money(summary.operating)}</strong> operating</div>
        <div class="summary-item"><strong>${money(summary.capital)}</strong> capital (not in profit)</div>
        ${voided}` : voided;
    document.getElementById("fin-exp-categories").innerHTML = summary.by_category.map(c =>
        `<span class="fin-exp-chip">${esc(c.category)} <strong>${money(c.total)}</strong> <span class="muted">${c.count}</span></span>`).join("");
    document.getElementById("fin-exp-count").textContent = summary.count ? `${summary.count} expense${summary.count === 1 ? "" : "s"}` : "";
}

function renderRows(cols) {
    const tbody = document.getElementById("fin-exp-rows");
    const canManage = can("finance", "manage_expenses");
    if (!monthRows.length) {
        tbody.innerHTML = stateRow(cols, "empty",
            canManage ? "Pick another month, or use + Add expenses to paste messages." : "Pick another month.",
            "No expenses recorded for this month");
        return;
    }
    tbody.innerHTML = monthRows.map(r => `
        <tr class="${r.voided_at ? "fin-exp-voided" : ""}">
            <td class="muted">${esc(dayLabel(r.spent_on))}</td>
            <td>${esc(r.payee || r.description || "—")}${r.payee && r.description ? `<div class="cell-sub">${esc(r.description)}</div>` : ""}
                ${r.voided_at ? `<div class="cell-sub">Voided: ${esc(r.void_reason)}</div>` : ""}</td>
            <td>${esc(r.category)}${r.ai_suggested ? ` <span class="fin-exp-ai-tag" title="Category suggested by DeepSeek, or the message was read by it">${SPARKLE}</span>` : ""}</td>
            <td class="muted">${esc(KIND_LABEL[r.kind] || r.kind)}</td>
            <td class="muted">${esc(METHOD_LABEL[r.method] || r.method || "—")}</td>
            <td class="muted fin-exp-code">${esc(r.reference || "—")}</td>
            <td class="num">${money(r.amount)}</td>
            ${canManage ? `<td>${r.voided_at ? "" : `<button type="button" class="fin-exp-edit-btn" data-edit="${r.id}" title="Edit or void" aria-label="Edit or void">${editIconSvg()}</button>`}</td>` : ""}
        </tr>`).join("");
}

// ---- Edit and void ----

function openEdit(id) {
    editingExpense = monthRows.find(r => r.id === id);
    if (!editingExpense) return;
    const r = editingExpense;
    document.getElementById("fin-exp-edit-what").textContent =
        `${r.payee || r.description || "Cash"} · ${money(r.amount)} · ${dayLabel(r.spent_on)}${r.reference ? " · " + r.reference : ""}`;
    document.getElementById("fin-exp-edit-payee").value = r.payee || "";
    document.getElementById("fin-exp-edit-category").innerHTML = categoryOptions(r.category_id, { includeRetiredId: r.category_id });
    document.getElementById("fin-exp-edit-kind").value = r.kind;
    document.getElementById("fin-exp-edit-segment").value = r.segment || "";
    document.getElementById("fin-exp-edit-description").value = r.description || "";
    document.getElementById("fin-exp-edit-source").textContent = r.source_message || "Typed by hand.";
    document.getElementById("fin-exp-edit-msg").textContent = "";
    document.getElementById("fin-exp-edit-overlay").hidden = false;
}

async function saveEdit(e) {
    e.preventDefault();
    const body = {
        payee: document.getElementById("fin-exp-edit-payee").value,
        category_id: Number(document.getElementById("fin-exp-edit-category").value),
        kind: document.getElementById("fin-exp-edit-kind").value,
        segment: document.getElementById("fin-exp-edit-segment").value || null,
        description: document.getElementById("fin-exp-edit-description").value,
    };
    const res = await fetch(`/finance/expenses/${editingExpense.id}`, {
        method: "PATCH", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(body),
    }).catch(() => null);
    if (!res || !res.ok) {
        showMessage("fin-exp-edit-msg", await errorText(res, "Couldn't save the change."), true);
        return;
    }
    document.getElementById("fin-exp-edit-overlay").hidden = true;
    loadMonth();
}

function openVoid() {
    document.getElementById("fin-exp-edit-overlay").hidden = true;
    document.getElementById("fin-exp-void-what").textContent = document.getElementById("fin-exp-edit-what").textContent;
    document.getElementById("fin-exp-void-reason").value = "";
    document.getElementById("fin-exp-void-msg").textContent = "";
    document.getElementById("fin-exp-void-overlay").hidden = false;
    document.getElementById("fin-exp-void-reason").focus();
}

async function saveVoid(e) {
    e.preventDefault();
    const reason = document.getElementById("fin-exp-void-reason").value.trim();
    const res = await fetch(`/finance/expenses/${editingExpense.id}/void`, {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ reason }),
    }).catch(() => null);
    if (!res || !res.ok) {
        showMessage("fin-exp-void-msg", await errorText(res, "Couldn't void it."), true);
        return;
    }
    document.getElementById("fin-exp-void-overlay").hidden = true;
    loadMonth();
}

// ---- Adding: paste and read ----

async function loadAiSetting() {
    if (aiEnabled !== null) return;
    const res = await fetch("/finance/settings", { headers: authHeaders() }).catch(() => null);
    aiEnabled = !!(res && res.ok && (await res.json()).ai_enabled);
    document.getElementById("fin-exp-note-field").hidden = !aiEnabled;
}

function openAdd() {
    document.getElementById("fin-exp-add").hidden = false;
    document.getElementById("fin-exp-review").hidden = true;
    if (!document.getElementById("fin-exp-cash-date").value) document.getElementById("fin-exp-cash-date").value = today();
    document.getElementById("fin-exp-messages").focus();
}

function closeAdd() {
    document.getElementById("fin-exp-add").hidden = true;
    document.getElementById("fin-exp-review").hidden = true;
    review = null;
}

async function readPaste() {
    const text = {
        messages: document.getElementById("fin-exp-messages").value,
        cash: document.getElementById("fin-exp-cash").value,
        cash_date: document.getElementById("fin-exp-cash-date").value || null,
    };
    if (!text.messages.trim() && !text.cash.trim()) {
        showMessage("fin-exp-add-msg", "Paste at least one message or type a cash line.", true);
        return;
    }
    const button = document.getElementById("fin-exp-read-btn");
    button.disabled = true;
    const res = await fetch("/finance/expenses/read", {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(text),
    }).catch(() => null);
    button.disabled = false;
    if (!res || !res.ok) {
        showMessage("fin-exp-add-msg", await errorText(res, "Couldn't read the messages."), true);
        return;
    }
    const data = await res.json();
    categories = data.categories;
    review = { text, note: document.getElementById("fin-exp-note").value, items: data.items, choices: {},
               readings: {}, ai: null };
    for (const item of data.items) {
        if (item.status === "ready") {
            review.choices[item.key] = { category_id: item.suggestion.category_id, kind: item.suggestion.kind,
                                         payee: item.payee || "", remember: false, skip: false, suggested: item.suggestion };
        } else if (item.status === "needs_category") {
            review.choices[item.key] = { category_id: null, kind: "operating", payee: item.payee || "", remember: true, skip: false };
        }
    }
    document.getElementById("fin-exp-add").hidden = true;
    document.getElementById("fin-exp-review").hidden = false;
    document.getElementById("fin-exp-save-msg").textContent = "";
    renderReview();
    requestSuggestions();
}

// ---- ✨ DeepSeek suggestions (only when switched on) ----

// Worth asking only for new business payees and messages the app couldn't
// read; people, bank transfers and cash are never sent.
function hasAiWork() {
    return review.items.some(i => (i.status === "needs_category" && i.ai_may_see_payee)
                                  || (i.status === "unrecognised" && i.key.startsWith("message-")));
}

async function requestSuggestions() {
    if (!aiEnabled || !hasAiWork()) return;
    const asked = review;
    asked.ai = { state: "asking" };
    renderReview();
    const res = await fetch("/finance/expenses/suggest", {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ ...asked.text, note: asked.note }),
    }).catch(() => null);
    if (review !== asked) return;  // the user started another paste meanwhile
    const data = res && res.ok ? await res.json() : null;
    if (!data || data.error || !data.enabled) {
        asked.ai = { state: "error", text: data?.error || "AI suggestions aren't available right now." };
        renderReview();
        return;
    }
    let count = 0;
    for (const item of asked.items) {
        const suggestion = item.status === "needs_category" && item.rule_keys?.length ? data.payees[item.rule_keys[0]] : null;
        const choice = asked.choices[item.key];
        // Never overwrite a category the user has already picked.
        if (!suggestion || !choice || choice.category_id) continue;
        Object.assign(choice, { category_id: suggestion.category_id, kind: suggestion.kind, ai_suggested: true,
                                aiMark: { category_id: suggestion.category_id, kind: suggestion.kind } });
        if (suggestion.description) choice.description = suggestion.description;
        count++;
    }
    for (const [key, reading] of Object.entries(data.readings || {})) {
        asked.readings[key] = reading;
        asked.choices[key] = { category_id: null, kind: "operating", payee: reading.payee || "", remember: false, skip: false,
                               ai_reading: { amount_text: reading.amount_text, date_text: reading.date_text,
                                             code: reading.code, payee: reading.payee } };
        count++;
    }
    asked.ai = { state: "done", count };
    renderReview();
}

function aiStatusHtml() {
    const ai = review.ai;
    if (!ai) return "";
    if (ai.state === "asking") return `<p class="fin-exp-ai-status" role="status">${SPARKLE} Asking DeepSeek for suggestions…</p>`;
    if (ai.state === "error") return `<p class="fin-exp-ai-status">${esc(ai.text)} Pick categories yourself; nothing else changes.</p>`;
    if (!ai.count) return `<p class="fin-exp-ai-status">${SPARKLE} No suggestions this time.</p>`;
    return `<p class="fin-exp-ai-status">${SPARKLE} ${ai.count} suggestion${ai.count === 1 ? "" : "s"} added, marked ${SPARKLE}. Check each before saving.</p>`;
}

// What a review item is worth: its own amount, or the AI's checked reading.
function amountOf(item) {
    return Number(item.amount ?? review.readings[item.key]?.amount ?? 0);
}

// Unknown payees are grouped so each is categorised once. Cash lines have no
// payee, so each is its own group.
function needsGroups() {
    const groups = new Map();
    for (const item of review.items.filter(i => i.status === "needs_category")) {
        const key = item.rule_keys?.length ? item.rule_keys[0] : item.key;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item);
    }
    return [...groups.entries()];
}

function itemLine(item) {
    const fee = item.fee ? ` <span class="muted">+ fee ${money(item.fee)}</span>` : "";
    const when = item.spent_on ? dayLabel(item.spent_on) : "cash";
    const warn = (item.warnings || []).map(w => `<div class="fin-exp-warn">${esc(w)}</div>`).join("");
    return `<span class="muted">${esc(when)}</span> · <strong>${money(item.amount)}</strong>${fee}` +
           (item.reference ? ` · <span class="fin-exp-code">${esc(item.reference)}</span>` : "") + warn;
}

// What to call a payment on screen: the name the message gave, else the
// message's own label (a masked bank account), else the cash description.
function payeeName(item) {
    return item.payee || item.label || item.description || "Cash";
}

function renderReview() {
    const items = review.items;
    const groups = needsGroups();
    const ready = items.filter(i => i.status === "ready");
    const duplicates = items.filter(i => i.status === "duplicate");
    const aiRead = items.filter(i => i.status === "unrecognised" && review.readings[i.key]);
    const notSaved = items.filter(i => NOT_SAVED_LABEL[i.status] && !review.readings[i.key]);
    const parts = [aiStatusHtml()];

    if (aiRead.length) {
        parts.push(`<h3 class="fin-exp-sub">${SPARKLE} Read by AI <span class="muted">· ${aiRead.length} · check the amount, date and code against the message</span></h3>`);
        parts.push(aiRead.map(i => {
            const r = review.readings[i.key];
            const choice = review.choices[i.key];
            return `
            <div class="fin-exp-group fin-exp-ai-read ${choice.skip ? "is-skipped" : ""}" data-key="${esc(i.key)}">
                <div class="fin-exp-group-head">
                    <strong>${money(r.amount)}</strong> · ${esc(dayLabel(r.spent_on))} · <span class="fin-exp-code">${esc(r.reference)}</span>
                </div>
                <pre class="fin-exp-ai-source">${esc(i.source_message)}</pre>
                <div class="fin-exp-group-controls">
                    <input type="text" class="control fin-exp-payee-input" data-field="payee" maxlength="120" value="${esc(choice.payee)}"
                        placeholder="Who was paid (optional)" aria-label="Who was paid">
                    <select class="control" data-field="category_id" aria-label="Category">${categoryOptions(choice.category_id, { placeholder: true })}</select>
                    <select class="control" data-field="kind" aria-label="Type">
                        <option value="operating" ${choice.kind === "operating" ? "selected" : ""}>Operating</option>
                        <option value="capital" ${choice.kind === "capital" ? "selected" : ""}>Capital</option>
                    </select>
                    <label class="checkbox-row"><input type="checkbox" data-field="skip" ${choice.skip ? "checked" : ""}> Skip (wrong, or not an expense)</label>
                </div>
            </div>`;
        }).join(""));
    }

    if (groups.length) {
        parts.push(`<h3 class="fin-exp-sub">Needs a category <span class="muted">· ${groups.length} payee${groups.length === 1 ? "" : "s"}</span></h3>`);
        parts.push(groups.map(([groupKey, members], index) => {
            const first = members[0];
            const choice = review.choices[first.key];
            const total = members.reduce((sum, m) => sum + amountOf(m), 0);
            const hint = first.is_person ? `<span class="fin-exp-tag">Person</span>` : (first.method === "bank" ? `<span class="fin-exp-tag">Bank, no name</span>` : "");
            return `
            <div class="fin-exp-group ${choice.skip ? "is-skipped" : ""}" data-group="${index}">
                <div class="fin-exp-group-head">
                    <strong>${esc(payeeName(first))}</strong> ${hint}
                    ${choice.aiMark ? `<span class="fin-exp-ai-tag" data-ai-tag title="Suggested by DeepSeek: check it" ${choice.ai_suggested ? "" : "hidden"}>${SPARKLE} suggested</span>` : ""}
                    <span class="muted">· ${members.length} payment${members.length === 1 ? "" : "s"} · ${money(total)}</span>
                    ${first.account ? `<div class="cell-sub">Account ${esc(first.account)}</div>` : ""}
                    ${choice.aiMark && choice.description ? `<div class="cell-sub">${SPARKLE} ${esc(choice.description)}</div>` : ""}
                </div>
                <ul class="fin-exp-group-rows">${members.map(m => `<li>${itemLine(m)}</li>`).join("")}</ul>
                <div class="fin-exp-group-controls">
                    <input type="text" class="control fin-exp-payee-input" data-group-field="payee" maxlength="120" value="${esc(choice.payee)}"
                        placeholder="${first.payee ? "Who was paid" : "Who was paid (optional)"}" aria-label="Who was paid">
                    <select class="control" data-group-field="category_id" aria-label="Category for ${esc(payeeName(first))}">${categoryOptions(choice.category_id, { placeholder: true })}</select>
                    <select class="control" data-group-field="kind" aria-label="Type">
                        <option value="operating" ${choice.kind === "operating" ? "selected" : ""}>Operating</option>
                        <option value="capital" ${choice.kind === "capital" ? "selected" : ""}>Capital</option>
                    </select>
                    ${first.rule_keys?.length ? `<label class="checkbox-row"><input type="checkbox" data-group-field="remember" ${choice.remember ? "checked" : ""}> Remember for next time</label>` : ""}
                    <label class="checkbox-row"><input type="checkbox" data-group-field="skip" ${choice.skip ? "checked" : ""}> Skip (not a business expense)</label>
                </div>
            </div>`;
        }).join(""));
        parts.push(`<p class="fin-exp-hint">Money moved to your own M-Pesa or bank account isn't spending: skip it, or it will be counted twice.</p>`);
    }

    if (ready.length) {
        const readyTotal = ready.reduce((sum, i) => sum + Number(i.amount), 0);
        parts.push(`
            <details class="fin-exp-ready" ${groups.length ? "" : "open"}>
                <summary><span class="fin-exp-sub-inline">Ready</span> <span class="muted">· ${ready.length} · ${money(readyTotal)} · category filled in from what you chose before; change it if wrong</span></summary>
                <div class="table-scroll table-compact"><table class="table-dense">
                    <thead><tr><th>Paid to</th><th>Payment</th><th>Category</th><th>Type</th><th>Skip</th></tr></thead>
                    <tbody>${ready.map(i => {
                        const choice = review.choices[i.key];
                        return `<tr data-key="${esc(i.key)}" class="${choice.skip ? "is-skipped" : ""}">
                            <td><input type="text" class="control fin-exp-payee-input" data-field="payee" maxlength="120" value="${esc(choice.payee)}"
                                placeholder="${esc(payeeName(i))}" aria-label="Who was paid"></td>
                            <td>${itemLine(i)}</td>
                            <td><select class="control" data-field="category_id">${categoryOptions(choice.category_id)}</select></td>
                            <td><select class="control" data-field="kind">
                                <option value="operating" ${choice.kind === "operating" ? "selected" : ""}>Operating</option>
                                <option value="capital" ${choice.kind === "capital" ? "selected" : ""}>Capital</option></select></td>
                            <td><input type="checkbox" data-field="skip" ${choice.skip ? "checked" : ""} aria-label="Skip"></td>
                        </tr>`;
                    }).join("")}</tbody>
                </table></div>
            </details>`);
    }

    if (duplicates.length) {
        parts.push(`
            <details class="fin-exp-ready">
                <summary><span class="fin-exp-sub-inline">Already saved</span> <span class="muted">· ${duplicates.length} skipped</span></summary>
                <ul class="fin-exp-group-rows">${duplicates.map(i => `<li>${esc(payeeName(i))} · ${itemLine(i)}</li>`).join("")}</ul>
            </details>`);
    }

    if (notSaved.length) {
        parts.push(`<h3 class="fin-exp-sub">Not saved <span class="muted">· ${notSaved.length}</span></h3>`);
        parts.push(notSaved.map(i => `
            <div class="fin-exp-raw">
                <div class="fin-exp-raw-why">${esc(NOT_SAVED_LABEL[i.status])}${i.reason ? `: ${esc(i.reason)}` : ""}</div>
                <pre>${esc(i.source_message)}</pre>
            </div>`).join(""));
    }

    document.getElementById("fin-exp-review-body").innerHTML = parts.join("") ||
        `<p class="fin-exp-hint">Nothing in that paste could be saved.</p>`;
    renderSaveSummary();
}

// The choices that will actually be saved, and what's still missing.
function chosenToSave() {
    const toSave = [], missing = [];
    for (const item of review.items) {
        const choice = review.choices[item.key];
        if (!choice || choice.skip) continue;
        if (!choice.category_id) { missing.push(item); continue; }
        toSave.push(item);
    }
    return { toSave, missing };
}

function renderSaveSummary() {
    const { toSave, missing } = chosenToSave();
    const total = toSave.reduce((sum, i) => sum + amountOf(i), 0);
    const fees = toSave.reduce((sum, i) => sum + Number(i.fee || 0), 0);
    let text = toSave.length ? `Save ${toSave.length} expense${toSave.length === 1 ? "" : "s"} · ${money(total)}` : "Nothing chosen to save yet";
    if (fees) text += ` (+ ${money(fees)} fees)`;
    if (missing.length) text += ` · ${missing.length} still need a category`;
    document.getElementById("fin-exp-save-summary").textContent = text;
    document.getElementById("fin-exp-save-btn").disabled = !toSave.length || missing.length > 0;
}

function onReviewChange(e) {
    const field = e.target.dataset.groupField || e.target.dataset.field;
    if (!field) return;
    const value = e.target.type === "checkbox" ? e.target.checked
        : field === "category_id" ? (Number(e.target.value) || null) : e.target.value;
    let keys;
    if (e.target.dataset.groupField) {
        const index = Number(e.target.closest("[data-group]").dataset.group);
        keys = needsGroups()[index][1].map(i => i.key);
    } else {
        keys = [e.target.closest("[data-key]").dataset.key];
    }
    for (const key of keys) {
        const choice = review.choices[key];
        choice[field] = value;
        // Correcting a category that came from a saved rule updates that rule.
        if (choice.suggested && (field === "category_id" || field === "kind")) {
            choice.remember = choice.category_id !== choice.suggested.category_id || choice.kind !== choice.suggested.kind;
        }
        // The ✨ stays only while the user keeps exactly what the AI suggested.
        if (choice.aiMark && (field === "category_id" || field === "kind")) {
            choice.ai_suggested = choice.category_id === choice.aiMark.category_id && choice.kind === choice.aiMark.kind;
        }
    }
    const holder = e.target.closest("[data-group], [data-key]");
    if (field === "skip") holder.classList.toggle("is-skipped", value);
    const tag = holder.querySelector("[data-ai-tag]");
    if (tag) tag.hidden = !review.choices[keys[0]].ai_suggested;
    renderSaveSummary();
}

async function saveReview() {
    const { toSave, missing } = chosenToSave();
    if (missing.length || !toSave.length) return;
    const choices = {};
    for (const item of toSave) {
        const c = review.choices[item.key];
        choices[item.key] = { category_id: c.category_id, kind: c.kind, payee: c.payee || "", remember: !!c.remember, skip: false,
                              ai_suggested: !!c.ai_suggested };
        if (c.description) choices[item.key].description = c.description;
        if (c.ai_reading) choices[item.key].ai_reading = c.ai_reading;
    }
    const button = document.getElementById("fin-exp-save-btn");
    button.disabled = true;
    const res = await fetch("/finance/expenses/save", {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ ...review.text, choices }),
    }).catch(() => null);
    if (!res || !res.ok) {
        button.disabled = false;
        showMessage("fin-exp-save-msg", await errorText(res, "Couldn't save. Nothing was saved; try again."), true);
        return;
    }
    const result = await res.json();
    // Show the month the newest saved expense falls in.
    const newest = toSave.map(i => i.spent_on || review.readings[i.key]?.spent_on || review.text.cash_date || today()).sort().pop();
    let text = `Saved ${result.saved} expense${result.saved === 1 ? "" : "s"}, ${money(result.total)}.`;
    if (result.skipped_references.length) text += ` ${result.skipped_references.length} had just been saved by someone else and were skipped.`;
    if (result.rules_saved) text += ` ${result.rules_saved} payee${result.rules_saved === 1 ? "" : "s"} remembered.`;
    document.getElementById("fin-exp-messages").value = "";
    document.getElementById("fin-exp-cash").value = "";
    document.getElementById("fin-exp-note").value = "";
    closeAdd();
    showMessage("fin-exp-page-msg", text, false);
    const target = `finance-expenses/${newest.slice(0, 7)}`;
    // navigate() does nothing for the page already showing, so reload by hand.
    if (location.hash === "#/" + target) loadMonth();
    else navigate(target, { replace: true });
}

// ---- Wiring ----

export function initFinanceExpenses() {
    document.getElementById("fin-exp-add-open-btn").addEventListener("click", async () => {
        if (await loadCategories()) { await loadAiSetting(); openAdd(); }
    });
    document.getElementById("fin-exp-add-cancel").addEventListener("click", closeAdd);
    document.getElementById("fin-exp-read-btn").addEventListener("click", readPaste);
    document.getElementById("fin-exp-review-back").addEventListener("click", () => {
        document.getElementById("fin-exp-review").hidden = true;
        document.getElementById("fin-exp-add").hidden = false;
    });
    document.getElementById("fin-exp-save-btn").addEventListener("click", saveReview);
    document.getElementById("fin-exp-review-body").addEventListener("change", onReviewChange);

    document.getElementById("fin-exp-month").addEventListener("change", (e) => {
        if (e.target.value) navigate(`finance-expenses/${e.target.value}`, { replace: true });
    });
    document.getElementById("fin-exp-show-voided").addEventListener("change", loadMonth);
    document.getElementById("fin-exp-rows").addEventListener("click", async (e) => {
        const button = e.target.closest("[data-edit]");
        if (button && await loadCategories()) openEdit(Number(button.dataset.edit));
    });

    document.getElementById("fin-exp-edit-form").addEventListener("submit", saveEdit);
    document.getElementById("fin-exp-edit-cancel").addEventListener("click", () => {
        document.getElementById("fin-exp-edit-overlay").hidden = true;
    });
    document.getElementById("fin-exp-void-open").addEventListener("click", openVoid);
    document.getElementById("fin-exp-void-form").addEventListener("submit", saveVoid);
    document.getElementById("fin-exp-void-cancel").addEventListener("click", () => {
        document.getElementById("fin-exp-void-overlay").hidden = true;
    });

    // #/finance-expenses/2026-10 opens that month, so a month can be bookmarked.
    registerRoute("finance-expenses", (params) => {
        const canManage = can("finance", "manage_expenses");
        document.getElementById("fin-exp-add-open-btn").hidden = !canManage;
        document.getElementById("fin-exp-manage-th").hidden = !canManage;
        const month = /^\d{4}-\d{2}$/.test(params[0] || "") ? params[0] : thisMonth();
        document.getElementById("fin-exp-month").value = month;
        showView("view-finance-expenses");
        loadMonth();
    });
}
