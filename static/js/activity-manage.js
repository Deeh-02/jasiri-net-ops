import { showView, registerRoute, confirmDialog } from "./common.js";
import { api, esc, setError } from "./activity-common.js";

// Admin only (the server refuses anyone else). Every change goes to the
// server, which answers with the whole catalog, and the screen redraws from
// that: what's shown is always what's saved. Only an open fields editor holds
// unsaved edits, and any other change closes it.

const $ = (id) => document.getElementById(id);
const KIND_LABELS = { text: "Short text", longtext: "Long text (a few lines)", number: "Number", customer: "Customer (search)" };

let catalog = [];
let editor = null;      // {typeId, fields: [...], applyAll} while a type's fields are open

function render() {
    $("act-manage-body").innerHTML = catalog.map((c, ci) => `
        <section class="act-mcat${c.is_active ? "" : " act-inactive"}" data-cat="${c.id}">
            <header class="act-mcat-head">
                <h2>${esc(c.name)}${c.is_active ? "" : ` <span class="act-badge">Inactive</span>`}</h2>
                <div class="act-mbtns">
                    ${moveButtons("cat", ci, catalog.length)}
                    <button type="button" class="act-btn act-btn-small" data-act="rename-cat">Rename</button>
                    <button type="button" class="act-btn act-btn-small" data-act="toggle-cat">${c.is_active ? "Deactivate" : "Activate"}</button>
                </div>
            </header>
            <label class="act-check"><input type="checkbox" data-act="repeat-cat" ${c.repeat_visit_tracking ? "checked" : ""}> Count these reports in the Overview's repeat visits</label>
            <ul class="act-mtypes">${c.types.map((t, ti) => typeRow(c, t, ti)).join("")}</ul>
            <button type="button" class="act-link-btn" data-act="add-type">+ Add type</button>
        </section>`).join("");
}

function moveButtons(kind, index, count) {
    return `<button type="button" class="act-btn act-btn-small" data-act="up-${kind}" aria-label="Move up" ${index === 0 ? "disabled" : ""}>↑</button>`
        + `<button type="button" class="act-btn act-btn-small" data-act="down-${kind}" aria-label="Move down" ${index === count - 1 ? "disabled" : ""}>↓</button>`;
}

function typeRow(c, t, index) {
    const open = editor?.typeId === t.id;
    return `<li class="act-mtype${t.is_active ? "" : " act-inactive"}" data-type="${t.id}">
        <div class="act-mtype-row">
            <div class="act-mtype-name">${esc(t.name)}${t.is_active ? "" : ` <span class="act-badge">Inactive</span>`}
                <span class="act-sub">${t.report_count} ${t.report_count === 1 ? "report" : "reports"} · ${t.fields.length ? `${t.fields.length} extra ${t.fields.length === 1 ? "field" : "fields"}` : "no extra fields"}</span></div>
            <div class="act-mbtns">
                ${moveButtons("type", index, c.types.length)}
                <button type="button" class="act-btn act-btn-small" data-act="rename-type">Rename</button>
                <button type="button" class="act-btn act-btn-small" data-act="fields">${open ? "Close" : "Fields"}</button>
                <button type="button" class="act-btn act-btn-small" data-act="toggle-type">${t.is_active ? "Deactivate" : "Activate"}</button>
            </div>
        </div>
        ${open ? editorHtml(c) : ""}
    </li>`;
}

function editorHtml(category) {
    const rows = editor.fields.map((f, i) => `
        <div class="act-fieldrow" data-index="${i}">
            <input class="act-input" data-f="label" value="${esc(f.label)}" maxlength="60" placeholder="Question, e.g. Fault encountered" aria-label="Field label">
            <select class="act-input" data-f="kind" aria-label="Kind">${Object.entries(KIND_LABELS).map(([k, label]) => `<option value="${k}" ${f.kind === k ? "selected" : ""}>${label}</option>`).join("")}</select>
            ${f.kind === "number" ? `<input class="act-input act-narrow" data-f="min" value="${esc(f.min ?? "")}" inputmode="decimal" placeholder="min" aria-label="Minimum"><input class="act-input act-narrow" data-f="max" value="${esc(f.max ?? "")}" inputmode="decimal" placeholder="max" aria-label="Maximum">` : ""}
            <label class="act-check"><input type="checkbox" data-f="required" ${f.required ? "checked" : ""}> Required</label>
            <button type="button" class="act-btn act-btn-small act-btn-danger" data-act="remove-field" aria-label="Remove field">Remove</button>
        </div>`).join("");
    return `<div class="act-editor">
        ${rows || `<p class="act-none">No extra fields: this type uses the standard form only.</p>`}
        <button type="button" class="act-link-btn" data-act="add-field">+ Add field</button>
        <label class="act-check"><input type="checkbox" data-f="applyAll" ${editor.applyAll ? "checked" : ""}> Give every type in ${esc(category.name)} these same fields</label>
        <div class="act-actions">
            <button type="button" class="act-btn act-btn-primary" data-act="save-fields">Save fields</button>
            <button type="button" class="act-btn" data-act="fields">Cancel</button>
        </div>
    </div>`;
}

// ---- Talking to the server --------------------------------------------------

async function send(path, method, body) {
    setError($("act-manage-error"), "");
    const res = await api(path, { method, body });
    if (!res.ok) { setError($("act-manage-error"), res.error); return false; }
    catalog = res.data.catalog;
    editor = null;
    render();
    return true;
}

function slug(label, taken) {
    const base = (label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "field").slice(0, 24);
    let key = /^[a-z]/.test(base) ? base : `f_${base}`;
    for (let n = 2; taken.has(key); n++) key = `${key.replace(/_\d+$/, "")}_${n}`;
    return key;
}

function fieldsToSave() {
    const taken = new Set(editor.fields.map(f => f.key).filter(Boolean));
    return editor.fields.map(f => {
        const out = { key: f.key || (() => { const k = slug(f.label || "", taken); taken.add(k); return k; })(), label: (f.label || "").trim(), kind: f.kind, required: !!f.required };
        if (f.kind === "number") {
            if (f.min !== undefined && String(f.min).trim() !== "") out.min = String(f.min).trim();
            if (f.max !== undefined && String(f.max).trim() !== "") out.max = String(f.max).trim();
        }
        return out;
    });
}

function typeAndCategory(el) {
    const typeEl = el.closest("[data-type]");
    const catEl = el.closest("[data-cat]");
    const category = catalog.find(c => String(c.id) === catEl?.dataset.cat);
    return { category, type: typeEl ? category?.types.find(t => String(t.id) === typeEl.dataset.type) : null };
}

function moved(list, id, delta) {
    const ids = list.map(x => x.id);
    const i = ids.indexOf(id);
    const j = i + delta;
    if (j < 0 || j >= ids.length) return null;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    return ids;
}

async function onClick(e) {
    const button = e.target.closest("[data-act]");
    if (!button || button.type === "checkbox") return;
    const { category, type } = typeAndCategory(button);
    switch (button.dataset.act) {
        case "rename-cat": {
            const name = prompt("Rename category", category.name);
            if (name !== null && name.trim()) await send(`/activity/admin/categories/${category.id}`, "PATCH", { name });
            break;
        }
        case "toggle-cat":
            await send(`/activity/admin/categories/${category.id}`, "PATCH", { is_active: !category.is_active });
            break;
        case "up-cat": case "down-cat": {
            const ids = moved(catalog, category.id, button.dataset.act === "up-cat" ? -1 : 1);
            if (ids) await send("/activity/admin/categories/order", "PUT", { ids });
            break;
        }
        case "add-type": {
            const name = prompt(`New activity type in ${category.name}`);
            if (name !== null && name.trim()) await send("/activity/admin/types", "POST", { category_id: category.id, name });
            break;
        }
        case "rename-type": {
            const name = prompt("Rename activity type", type.name);
            if (name !== null && name.trim()) await send(`/activity/admin/types/${type.id}`, "PATCH", { name });
            break;
        }
        case "toggle-type":
            await send(`/activity/admin/types/${type.id}`, "PATCH", { is_active: !type.is_active });
            break;
        case "up-type": case "down-type": {
            const ids = moved(category.types, type.id, button.dataset.act === "up-type" ? -1 : 1);
            if (ids) await send(`/activity/admin/categories/${category.id}/types/order`, "PUT", { ids });
            break;
        }
        case "fields":
            editor = editor?.typeId === type.id ? null : { typeId: type.id, fields: type.fields.map(f => ({ ...f })), applyAll: false };
            render();
            break;
        case "add-field":
            editor.fields.push({ key: "", label: "", kind: "text", required: false });
            render();
            break;
        case "remove-field":
            editor.fields.splice(Number(button.closest("[data-index]").dataset.index), 1);
            render();
            break;
        case "save-fields": {
            if (editor.applyAll && !(await confirmDialog({ title: `Replace the questions of every type in ${category.name}?`, message: "Every activity type in this category gets these extra questions. Answers already saved on reports are not changed.", confirmLabel: "Replace all" }))) break;
            await send(`/activity/admin/types/${type.id}/fields`, "PUT", { fields: fieldsToSave(), apply_to_category: editor.applyAll });
            break;
        }
    }
}

function onChange(e) {
    const box = e.target;
    if (box.dataset.act === "repeat-cat") {
        const { category } = typeAndCategory(box);
        send(`/activity/admin/categories/${category.id}`, "PATCH", { repeat_visit_tracking: box.checked });
        return;
    }
    if (!editor || !box.dataset.f) return;
    if (box.dataset.f === "applyAll") { editor.applyAll = box.checked; return; }
    const row = box.closest("[data-index]");
    if (!row) return;
    const field = editor.fields[Number(row.dataset.index)];
    field[box.dataset.f] = box.type === "checkbox" ? box.checked : box.value;
    if (box.dataset.f === "kind") render();      // the number kind shows min and max
}

function onInput(e) {
    const box = e.target;
    const row = box.closest("[data-index]");
    if (editor && row && box.dataset.f && box.type !== "checkbox" && box.tagName === "INPUT") {
        editor.fields[Number(row.dataset.index)][box.dataset.f] = box.value;
    }
}

export function initActivityManage() {
    const body = $("act-manage-body");
    body.addEventListener("click", onClick);
    body.addEventListener("change", onChange);
    body.addEventListener("input", onInput);
    $("act-m-add-category").addEventListener("click", () => {
        const name = prompt("New category");
        if (name !== null && name.trim()) send("/activity/admin/categories", "POST", { name });
    });

    registerRoute("activity-manage", async () => {
        showView("view-activity-manage");
        editor = null;
        setError($("act-manage-error"), "");
        const res = await api("/activity/admin/catalog");
        if (!res.ok) { setError($("act-manage-error"), res.error); return; }
        catalog = res.data.catalog;
        render();
    });
}
