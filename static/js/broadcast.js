import { authHeaders, showView, registerRoute, formatDate } from "./common.js";

function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const STATUS_LABEL = { active: "Active", expired: "Expired", all: "Everyone", selected: "Selected customers" };
const STATE_LABEL = { sending: "Sending…", done: "Done", interrupted: "Interrupted" };

let routers = [];
// The audience the user last checked: Send is only allowed for exactly this.
let checked = null;
let pollTimer = null;

function selectedRouterIds() {
    return [...document.querySelectorAll("#broadcast-routers input:checked")].map(i => Number(i.value));
}

// "Pick customers myself" lists everyone and starts with nobody ticked; every
// other choice lists its matching customers, all ticked.
function currentAudience() {
    const choice = document.getElementById("broadcast-status").value;
    return {
        router_ids: selectedRouterIds(),
        status_filter: choice === "pick" ? "all" : choice,
        startEmpty: choice === "pick",
    };
}

function setMessage(text, isError) {
    const el = document.getElementById("broadcast-msg");
    el.textContent = text;
    el.className = `form-msg ${isError ? "error" : "success"}`;
}

function invalidateCheck() {
    checked = null;
    document.getElementById("broadcast-audience").textContent = "";
    document.getElementById("broadcast-recipients").hidden = true;
    updateSendButton();
}

function updateSendButton() {
    const message = document.getElementById("broadcast-message").value.trim();
    document.getElementById("broadcast-send-btn").disabled = !(checked && checked.selected.size > 0 && message);
    document.getElementById("broadcast-counter").textContent = `${document.getElementById("broadcast-message").value.length} / 480`;
}

function matchesSearch(r, q) {
    return !q || `${r.name || ""} ${r.username} ${r.phone}`.toLowerCase().includes(q);
}

function visibleRecipients() {
    const q = document.getElementById("broadcast-recipient-search").value.trim().toLowerCase();
    return checked.recipients.filter(r => matchesSearch(r, q));
}

function updateRecipientCount() {
    const shown = visibleRecipients();
    document.getElementById("broadcast-recipients-count").textContent =
        `${checked.selected.size} of ${checked.recipients.length} ticked` +
        (shown.length !== checked.recipients.length ? ` · showing ${shown.length}` : "");
    updateSendButton();
}

function renderRecipients() {
    const shown = visibleRecipients();
    updateRecipientCount();
    document.getElementById("broadcast-recipients-list").innerHTML = shown.length ? shown.map(r => `
        <label class="broadcast-recipient">
            <input type="checkbox" data-id="${r.customer_id}" ${checked.selected.has(r.customer_id) ? "checked" : ""}>
            <span class="broadcast-recipient-main">${esc(r.name || r.username)}
                <span class="broadcast-recipient-sub">${esc(r.username)} · ${esc(r.phone)} · ${esc(r.router)}</span>
            </span>
            <span class="broadcast-recipient-status ${esc(r.account_status.toLowerCase())}">${esc(r.account_status)}${r.enabled ? "" : " (disabled)"}</span>
        </label>`).join("") : `<div class="broadcast-recipients-count">Nobody matches that search.</div>`;
}

async function checkAudience() {
    const audience = currentAudience();
    const box = document.getElementById("broadcast-audience");
    if (!audience.router_ids.length) {
        box.innerHTML = `<span class="warn">Pick at least one router.</span>`;
        return;
    }
    const res = await fetch("/customers/broadcasts/preview", {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ router_ids: audience.router_ids, status_filter: audience.status_filter }),
    });
    if (!res.ok) {
        box.innerHTML = `<span class="warn">Couldn't check the audience.</span>`;
        return;
    }
    const data = await res.json();
    checked = {
        router_ids: audience.router_ids,
        status_filter: audience.status_filter,
        recipients: data.recipients,
        selected: new Set(audience.startEmpty ? [] : data.recipients.map(r => r.customer_id)),
    };
    box.innerHTML = `<strong>${data.count}</strong> ${data.count === 1 ? "person matches" : "people match"}.` +
        (data.no_phone ? ` <span class="warn">${data.no_phone} matching customer${data.no_phone === 1 ? " has" : "s have"} no usable phone number and can't be texted.</span>` : "") +
        (audience.startEmpty ? " Tick the people to text." : " Untick anyone you don't want to reach.");
    document.getElementById("broadcast-recipient-search").value = "";
    document.getElementById("broadcast-recipients").hidden = !data.recipients.length;
    renderRecipients();
}

function setShownTicked(ticked) {
    visibleRecipients().forEach(r => ticked ? checked.selected.add(r.customer_id) : checked.selected.delete(r.customer_id));
    renderRecipients();
}

async function sendBroadcast() {
    if (!checked || !checked.selected.size) return;
    const message = document.getElementById("broadcast-message").value.trim();
    const people = checked.recipients.filter(r => checked.selected.has(r.customer_id));
    const who = people.length <= 5
        ? people.map(r => r.name || r.username).join(", ")
        : `${people.length} people on ${routers.filter(r => checked.router_ids.includes(r.id)).map(r => r.name).join(" + ")}`;
    if (!confirm(`Send this SMS to ${who}?\n\n${message}\n\nIt cannot be recalled.`)) return;

    const btn = document.getElementById("broadcast-send-btn");
    btn.disabled = true;
    const res = await fetch("/customers/broadcasts", {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
            router_ids: checked.router_ids, status_filter: checked.status_filter, message,
            customer_ids: [...checked.selected],
        }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        setMessage(data.detail || "Couldn't send.", true);
        updateSendButton();
        return;
    }
    setMessage(`Sending to ${data.recipient_count} ${data.recipient_count === 1 ? "person" : "people"}…`, false);
    document.getElementById("broadcast-message").value = "";
    invalidateCheck();
    loadHistory();
}

async function loadHistory() {
    clearTimeout(pollTimer);
    const rows = document.getElementById("broadcast-rows");
    const res = await fetch("/customers/broadcasts", { headers: authHeaders() });
    if (!res.ok) {
        rows.innerHTML = `<tr><td colspan="7">Couldn't load sent broadcasts.</td></tr>`;
        return;
    }
    const list = await res.json();
    const names = Object.fromEntries(routers.map(r => [r.id, r.name]));
    rows.innerHTML = list.length ? list.map(b => `
        <tr>
            <td>${esc(formatDate(b.created_at))}</td>
            <td>${esc(b.sent_by || "—")}</td>
            <td>${esc(b.router_ids.map(id => names[id] || `#${id}`).join(" + "))} · ${esc(STATUS_LABEL[b.status_filter] || b.status_filter)}</td>
            <td class="broadcast-message-cell" title="${esc(b.message)}">${esc(b.message)}</td>
            <td>${b.sent_count} / ${b.recipient_count}</td>
            <td>${b.failed_count}</td>
            <td>${esc(STATE_LABEL[b.state] || b.state)}</td>
        </tr>`).join("") : `<tr><td colspan="7">Nothing sent yet.</td></tr>`;
    // Keep refreshing only while a send is running and the page is open.
    if (list.some(b => b.state === "sending") && !document.getElementById("view-broadcast").hidden) {
        pollTimer = setTimeout(loadHistory, 3000);
    }
}

async function loadPage() {
    const res = await fetch("/customers/routers", { headers: authHeaders() });
    if (res.ok) routers = await res.json();
    const box = document.getElementById("broadcast-routers");
    if (!box.children.length || box.children.length !== routers.length) {
        box.innerHTML = routers.map(r =>
            `<label class="checkbox-row"><input type="checkbox" value="${r.id}"> ${esc(r.name)}</label>`).join("");
    }
    invalidateCheck();
    loadHistory();
}

export function initBroadcast() {
    document.getElementById("broadcast-routers").addEventListener("change", invalidateCheck);
    document.getElementById("broadcast-status").addEventListener("change", invalidateCheck);
    document.getElementById("broadcast-message").addEventListener("input", updateSendButton);
    document.getElementById("broadcast-preview-btn").addEventListener("click", checkAudience);
    document.getElementById("broadcast-send-btn").addEventListener("click", sendBroadcast);
    document.getElementById("broadcast-recipient-search").addEventListener("input", () => checked && renderRecipients());
    document.getElementById("broadcast-select-visible").addEventListener("click", () => checked && setShownTicked(true));
    document.getElementById("broadcast-clear-visible").addEventListener("click", () => checked && setShownTicked(false));
    document.getElementById("broadcast-recipients-list").addEventListener("change", (e) => {
        const id = Number(e.target.dataset.id);
        if (!checked || !id) return;
        if (e.target.checked) checked.selected.add(id); else checked.selected.delete(id);
        updateRecipientCount();
    });
    document.getElementById("broadcast-name-chip").addEventListener("click", () => {
        const t = document.getElementById("broadcast-message");
        t.focus();
        t.setRangeText("{{NAME}}", t.selectionStart, t.selectionEnd, "end");
        updateSendButton();
    });

    registerRoute("broadcast", () => {
        showView("view-broadcast");
        loadPage();
    });
}
