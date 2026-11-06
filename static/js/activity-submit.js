import { showView, registerRoute, navigate, getCurrentUser, can } from "./common.js";
import { api, esc, setError } from "./activity-common.js";

// The form's values live in `state`; the chips are drawn from it. A failed
// submit changes nothing here, so the technician just taps Submit again. For a
// new report the state is also kept in the browser (a draft) so a reload or a
// dead battery doesn't lose it.
const DRAFT_KEY = "activityDraft";

let options = null;        // from GET /activity/options (augmented when editing)
let state = null;
let editing = null;        // {id, reference, submitter_id, submitter_name} while editing
let sending = false;
let lastSaved = null;      // {id, reference} of the report just submitted

const $ = (id) => document.getElementById(id);

function newToken() {
    const raw = crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2);
    return raw.slice(0, 64);
}

function blankState() {
    const last = options.last_location;
    let location = "", other = "";
    if (last?.location_id && options.locations.some(l => l.id === last.location_id)) location = String(last.location_id);
    else if (last?.other_location) { location = "other"; other = last.other_location; }
    return {
        activity_date: options.today, category_id: null, type_id: null,
        location, other_location: other, outcome: options.default_outcome,
        technician_ids: [], time_spent: null, description: "", remarks: "",
        details: {},
        client_token: newToken(),
    };
}

function saveDraft() {
    hideDuplicate();    // any change means the warning no longer describes what's on screen
    if (editing) return;
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify(state)); } catch { /* private mode or full: the form still works */ }
}
function clearDraft() {
    try { localStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ }
}

// A saved draft is only trusted where it still matches today's options
// (a type may have been deactivated since).
function restoredDraft() {
    let draft;
    try { draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null"); } catch { return null; }
    if (!draft || typeof draft !== "object") return null;
    const base = blankState();
    const category = options.categories.find(c => c.id === draft.category_id);
    const type = category?.types.find(t => t.id === draft.type_id);
    const me = getCurrentUser()?.id;
    return {
        ...base,
        activity_date: draft.activity_date && draft.activity_date <= options.today ? draft.activity_date : base.activity_date,
        category_id: category ? category.id : null,
        type_id: type ? type.id : null,
        location: draft.location === "other" || options.locations.some(l => String(l.id) === draft.location) ? draft.location : base.location,
        other_location: typeof draft.other_location === "string" ? draft.other_location : "",
        outcome: options.outcomes.some(o => o.key === draft.outcome) ? draft.outcome : base.outcome,
        technician_ids: (draft.technician_ids || []).filter(id => id !== me && options.technicians.some(t => t.id === id)),
        time_spent: options.time_spent.some(t => t.key === draft.time_spent) ? draft.time_spent : null,
        description: typeof draft.description === "string" ? draft.description : "",
        remarks: typeof draft.remarks === "string" ? draft.remarks : "",
        client_token: typeof draft.client_token === "string" && draft.client_token ? draft.client_token : base.client_token,
        details: draft.details && typeof draft.details === "object" ? draft.details : {},
    };
}

// ---- Drawing ---------------------------------------------------------------

function chipHtml(kind, value, label, selected, extra = "") {
    return `<button type="button" class="act-chip${selected ? " selected" : ""}" data-chip="${kind}" data-value="${esc(value)}" role="${kind === "time" || kind === "tech" ? "button" : "radio"}" aria-pressed="${selected}" aria-checked="${selected}"${extra}>${esc(label)}</button>`;
}

function drawCategories() {
    $("act-categories").innerHTML = options.categories.map(c => chipHtml("category", c.id, c.name, c.id === state.category_id)).join("");
    const category = options.categories.find(c => c.id === state.category_id);
    $("act-type-field").hidden = !category;
    $("act-types").innerHTML = category ? category.types.map(t => chipHtml("type", t.id, t.name, t.id === state.type_id)).join("") : "";
}

// ---- Extra fields. Which ones appear comes from the chosen type's own
// configuration (options.categories[].types[].fields), never from code here.

function currentFields() {
    const category = options.categories.find(c => c.id === state.category_id);
    return category?.types.find(t => t.id === state.type_id)?.fields || [];
}

function detailInput(f) {
    const value = state.details[f.key];
    const id = `act-d-${f.key}`;
    const label = `<label class="act-sublabel" for="${id}">${esc(f.label)}${f.required ? "" : ` <span class="act-optional">optional</span>`}</label>`;
    if (f.kind === "customer") {
        const picked = value && typeof value === "object"
            ? `<span class="act-chip selected removable" data-customer-clear="${esc(f.key)}">${esc(value.name)} · ${esc(value.username)} <span aria-hidden="true">×</span></span>` : "";
        return `<div class="act-detail-field" data-customer-field="${esc(f.key)}">${label}
            ${picked || `<input type="search" id="${id}" class="act-input" data-customer-search="${esc(f.key)}" placeholder="Search by name or username" autocomplete="off">`}
            <div class="act-results" data-customer-results="${esc(f.key)}" hidden></div>
            ${picked ? "" : `<p class="act-hint">Not found? Leave it blank and name them in the description.</p>`}</div>`;
    }
    const attrs = f.kind === "number" ? `type="number" step="any"${f.min != null ? ` min="${f.min}"` : ""}${f.max != null ? ` max="${f.max}"` : ""}` : `type="text" maxlength="200"`;
    return `<div class="act-detail-field">${label}<input ${attrs} id="${id}" class="act-input" data-detail="${esc(f.key)}" value="${esc(value ?? "")}"></div>`;
}

function drawDetails() {
    const fields = currentFields();
    $("act-details-field").hidden = !fields.length;
    $("act-details").innerHTML = fields.map(detailInput).join("");
}

// Changing the type keeps the answers the new type also asks for.
function keepMatchingDetails() {
    const fields = currentFields();
    state.details = Object.fromEntries(Object.entries(state.details).filter(([key, value]) => {
        const f = fields.find(x => x.key === key);
        return f && ((f.kind === "customer") === (typeof value === "object"));
    }));
}

let customerTimer = null;
async function searchCustomers(input) {
    const results = document.querySelector(`[data-customer-results="${input.dataset.customerSearch}"]`);
    const text = input.value.trim();
    if (text.length < 2) { results.hidden = true; return; }
    const res = await api(`/activity/customers?q=${encodeURIComponent(text)}`);
    if (input.value.trim() !== text) return;          // typed on since: a newer search is coming
    results.hidden = false;
    if (!res.ok) { results.innerHTML = `<p class="act-hint">${esc(res.error)}</p>`; return; }
    results.innerHTML = res.data.length
        ? res.data.map(c => `<button type="button" class="act-result" data-customer-pick="${esc(input.dataset.customerSearch)}" data-id="${c.id}" data-name="${esc(c.name)}" data-username="${esc(c.username)}" data-plan="${esc(c.plan || "")}">${esc(c.name)} <span class="act-sub">${esc(c.username)}${c.plan ? " · " + esc(c.plan) : ""}</span></button>`).join("")
        : `<p class="act-hint">No customer found. Leave it blank and name them in the description.</p>`;
}

function onDetailsEvent(e) {
    const input = e.target.closest("[data-detail]");
    if (input) { state.details[input.dataset.detail] = input.value; saveDraft(); return; }
    const search = e.target.closest("[data-customer-search]");
    if (search) { clearTimeout(customerTimer); customerTimer = setTimeout(() => searchCustomers(search), 250); }
}

function onDetailsClick(e) {
    const pick = e.target.closest("[data-customer-pick]");
    if (pick) {
        const { customerPick: key, id, name, username, plan } = pick.dataset;
        state.details[key] = { id: Number(id), name, username };
        // The package is nearly always the customer's own plan: save a typing.
        const packageField = currentFields().find(f => f.key === "package" && f.kind === "text");
        if (plan && packageField && !state.details.package) state.details.package = plan;
        drawDetails();
        saveDraft();
        return;
    }
    const clear = e.target.closest("[data-customer-clear]");
    if (clear) { delete state.details[clear.dataset.customerClear]; drawDetails(); saveDraft(); }
}

function drawLocation() {
    const select = $("act-location");
    select.innerHTML = `<option value="">Choose a location…</option>`
        + options.locations.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("")
        + `<option value="other">Other…</option>`;
    select.value = state.location;
    const other = $("act-other-location");
    other.hidden = state.location !== "other";
    other.value = state.other_location;
}

function drawOutcomes() {
    $("act-outcomes").innerHTML = options.outcomes.map(o => chipHtml("outcome", o.key, o.label, o.key === state.outcome)).join("");
    const required = options.explanation_outcomes.includes(state.outcome);
    $("act-description-label").innerHTML = `What was done <span class="act-optional">${required ? "required: one line" : "optional"}</span>`;
    $("act-description").placeholder = required ? "Say what happened, in one line" : "One line is enough";
}

function drawTechnicians() {
    const submitterId = editing ? editing.submitter_id : getCurrentUser()?.id;
    const submitterName = editing ? editing.submitter_name : (state.technician_ids.length ? "You" : "Just me");
    const names = Object.fromEntries(options.technicians.map(t => [t.id, t.name]));
    const locked = `<span class="act-chip selected locked" title="Always included">${esc(submitterName)}</span>`;
    const added = state.technician_ids.map(id => `<button type="button" class="act-chip selected removable" data-chip="tech" data-value="${id}" aria-label="Remove ${esc(names[id] || "technician")}">${esc(names[id] || "Technician")} <span aria-hidden="true">×</span></button>`).join("");
    const addable = options.technicians.filter(t => t.id !== submitterId && !state.technician_ids.includes(t.id));
    $("act-technicians").innerHTML = locked + added
        + (addable.length ? `<button type="button" class="act-chip add" data-chip="tech-add">+ add</button>` : "");
    $("act-tech-picker").innerHTML = `<option value="">Pick a technician…</option>` + addable.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join("");
    if (!addable.length) $("act-tech-picker").hidden = true;
}

function drawTime() {
    $("act-time").innerHTML = options.time_spent.map(t => chipHtml("time", t.key, t.label, t.key === state.time_spent)).join("");
}

function drawText() {
    $("act-date").value = state.activity_date;
    $("act-date").max = options.today;
    $("act-description").value = state.description;
    $("act-remarks").value = state.remarks;
    const hasRemarks = !!state.remarks;
    $("act-remarks-wrap").hidden = !hasRemarks;
    $("act-remarks-open").hidden = hasRemarks;
}

function drawAll() {
    drawCategories(); drawDetails(); drawLocation(); drawOutcomes(); drawTechnicians(); drawTime(); drawText();
    setError($("act-form-error"), "");
}

// ---- Events ----------------------------------------------------------------

function onChip(e) {
    const chip = e.target.closest("[data-chip]");
    if (!chip) return;
    const { chip: kind, value } = chip.dataset;
    if (kind === "category") {
        const id = Number(value);
        if (state.category_id !== id) { state.category_id = id; state.type_id = null; keepMatchingDetails(); }
        drawCategories();
        drawDetails();
    } else if (kind === "type") {
        state.type_id = Number(value);
        keepMatchingDetails();
        drawCategories();
        drawDetails();
    } else if (kind === "outcome") {
        state.outcome = value;
        drawOutcomes();
    } else if (kind === "time") {
        state.time_spent = state.time_spent === value ? null : value;   // tap again to clear
        drawTime();
    } else if (kind === "tech") {
        state.technician_ids = state.technician_ids.filter(id => id !== Number(value));
        drawTechnicians();
    } else if (kind === "tech-add") {
        const picker = $("act-tech-picker");
        picker.hidden = !picker.hidden;
        if (!picker.hidden) picker.focus();
    }
    saveDraft();
}

function problems() {
    const out = [];
    if (!state.type_id) out.push("Pick a category and an activity type");
    if (!state.location) out.push("Pick a location");
    else if (state.location === "other" && !state.other_location.trim()) out.push("Say where it was (a few words)");
    if (options.explanation_outcomes.includes(state.outcome) && !state.description.trim()) out.push("Add a one-line explanation for this outcome");
    if (!state.activity_date) out.push("Pick the date");
    for (const f of currentFields()) {
        const v = state.details[f.key];
        if (f.required && (v === undefined || v === null || String(typeof v === "object" ? v.id : v).trim() === "")) out.push(`${f.label} is required`);
    }
    return out;
}

function detailsForSending() {
    const out = {};
    for (const f of currentFields()) {
        const v = state.details[f.key];
        if (v === undefined || v === null) continue;
        if (typeof v === "object") out[f.key] = { id: v.id };    // the server fills in the rest
        else if (String(v).trim() !== "") out[f.key] = String(v).trim();
    }
    return out;
}

function hideDuplicate() {
    const box = document.getElementById("act-dup");
    if (box) box.hidden = true;
}

function showDuplicate(found) {
    const one = found[0];
    const text = found.length === 1
        ? `${one.reference} (by ${one.submitted_by_name}) was already filed for this activity type, location and date.`
        : `${found.map(f => f.reference).join(", ")} were already filed for this activity type, location and date.`;
    $("act-dup-text").textContent = `${text} Is yours a different job?`;
    $("act-dup").hidden = false;
    $("act-dup").scrollIntoView({ block: "center", behavior: "smooth" });
}

function onSubmit(e) {
    e.preventDefault();
    submit(false);
}

async function submit(confirmDuplicate) {
    if (sending) return;
    hideDuplicate();
    const found = problems();
    if (found.length) { setError($("act-form-error"), found[0]); return; }

    const body = {
        activity_date: state.activity_date,
        type_id: state.type_id,
        location_id: state.location === "other" ? null : Number(state.location),
        other_location: state.location === "other" ? state.other_location.trim() : null,
        outcome: state.outcome,
        description: state.description.trim() || null,
        time_spent: state.time_spent,
        remarks: state.remarks.trim() || null,
        technician_ids: state.technician_ids,
        details: detailsForSending(),
    };
    if (!editing) {
        body.client_token = state.client_token;
        if (confirmDuplicate) body.confirm_duplicate = true;
    }

    sending = true;
    const button = $("act-submit-btn");
    button.disabled = true;
    button.textContent = editing ? "Saving…" : "Submitting…";
    setError($("act-form-error"), "");
    const res = await api(editing ? `/activity/reports/${editing.id}` : "/activity/reports",
        { method: editing ? "PATCH" : "POST", body });
    sending = false;
    button.disabled = false;
    button.textContent = editing ? "Save changes" : "Submit report";

    if (!res.ok) {
        // Nothing is cleared: the same form, the same token, ready to retry.
        if (res.status === 409 && res.detail?.possible_duplicates) {
            showDuplicate(res.detail.possible_duplicates);    // a question, not an error
            return;
        }
        setError($("act-form-error"), res.error);
        return;
    }
    if (editing) {
        navigate(`activity-reports/${editing.id}`);
        return;
    }
    clearDraft();
    lastSaved = res.data;
    // The next report starts from where this one was.
    options.last_location = state.location === "other"
        ? { location_id: null, other_location: state.other_location.trim() }
        : { location_id: Number(state.location), other_location: null };
    $("act-success-ref").textContent = res.data.reference;
    $("act-view-btn").hidden = !can("activity_log", "view");
    $("act-form").hidden = true;
    $("act-success").hidden = false;
    window.scrollTo({ top: 0 });
}

function startAnother() {
    state = blankState();
    $("act-success").hidden = true;
    $("act-form").hidden = false;
    drawAll();
    window.scrollTo({ top: 0 });
}

// ---- Opening ---------------------------------------------------------------

function showLoadError(message) {
    $("act-form").hidden = true;
    $("act-success").hidden = true;
    $("act-submit-error").hidden = false;
    $("act-submit-error-text").textContent = message;
}

function showForm(title) {
    $("act-submit-error").hidden = true;
    $("act-success").hidden = true;
    $("act-form").hidden = false;
    $("act-submit-title").textContent = title;
    $("act-submit-btn").textContent = editing ? "Save changes" : "Submit report";
    $("act-cancel-btn").hidden = !editing;
    setError($("act-form-error"), "");
}

async function openNew() {
    editing = null;
    const res = await api("/activity/options");
    if (!res.ok) return showLoadError(res.error);
    options = res.data;
    state = restoredDraft() || blankState();
    showForm("Submit Activity Report");
    drawAll();
}

async function openEdit(id) {
    const [opt, rep] = await Promise.all([api("/activity/options"), api(`/activity/reports/${id}`)]);
    if (!opt.ok || !rep.ok) {
        if (rep.status === 404) return navigate("activity-reports");
        return showLoadError((!opt.ok ? opt : rep).error);
    }
    const report = rep.data;
    if (!report.can_edit) return navigate(`activity-reports/${id}`);
    options = opt.data;
    // Things the report already uses stay selectable even if they have since
    // been deactivated.
    let category = options.categories.find(c => c.id === report.category_id);
    if (!category) { category = { id: report.category_id, name: report.category, types: [] }; options.categories.push(category); }
    if (!category.types.some(t => t.id === report.type_id)) category.types.push({ id: report.type_id, name: report.type, fields: report.type_fields });
    if (report.location_id && !options.locations.some(l => l.id === report.location_id)) {
        options.locations.push({ id: report.location_id, name: report.location });
    }
    report.technicians.forEach(t => { if (!options.technicians.some(o => o.id === t.id)) options.technicians.push(t); });

    editing = { id: report.id, reference: report.reference, submitter_id: report.submitted_by, submitter_name: report.submitted_by_name };
    state = {
        activity_date: report.activity_date, category_id: report.category_id, type_id: report.type_id,
        location: report.location_id ? String(report.location_id) : "other",
        other_location: report.other_location || "", outcome: report.outcome,
        technician_ids: report.technicians.map(t => t.id).filter(tid => tid !== report.submitted_by),
        time_spent: report.time_spent, description: report.description || "", remarks: report.remarks || "",
        details: report.details || {},
        client_token: "",
    };
    showForm(`Edit ${report.reference}`);
    drawAll();
}

export function initActivitySubmit() {
    const form = $("act-form");
    form.addEventListener("click", onChip);
    form.addEventListener("submit", onSubmit);
    $("act-details").addEventListener("input", onDetailsEvent);
    $("act-details").addEventListener("click", onDetailsClick);
    $("act-dup-go").addEventListener("click", () => submit(true));
    $("act-dup-back").addEventListener("click", hideDuplicate);

    $("act-date").addEventListener("change", (e) => { state.activity_date = e.target.value; saveDraft(); });
    $("act-location").addEventListener("change", (e) => {
        state.location = e.target.value;
        $("act-other-location").hidden = state.location !== "other";
        if (state.location === "other") $("act-other-location").focus();
        saveDraft();
    });
    $("act-other-location").addEventListener("input", (e) => { state.other_location = e.target.value; saveDraft(); });
    $("act-description").addEventListener("input", (e) => { state.description = e.target.value; saveDraft(); });
    $("act-remarks").addEventListener("input", (e) => { state.remarks = e.target.value; saveDraft(); });
    $("act-remarks-open").addEventListener("click", () => {
        $("act-remarks-wrap").hidden = false;
        $("act-remarks-open").hidden = true;
        $("act-remarks").focus();
    });
    $("act-tech-picker").addEventListener("change", (e) => {
        const id = Number(e.target.value);
        if (id && !state.technician_ids.includes(id)) state.technician_ids.push(id);
        e.target.hidden = true;
        drawTechnicians();
        saveDraft();
    });

    $("act-another-btn").addEventListener("click", startAnother);
    $("act-view-btn").addEventListener("click", () => lastSaved && navigate(`activity-reports/${lastSaved.id}`));
    $("act-cancel-btn").addEventListener("click", () => navigate(editing ? `activity-reports/${editing.id}` : "activity-reports"));
    $("act-submit-retry").addEventListener("click", () => handleRoute(location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).slice(1)));

    registerRoute("activity-submit", handleRoute);
}

async function handleRoute(params) {
    showView("view-activity-submit");
    if (params[1] === "edit") await openEdit(params[0]);
    else await openNew();
}
