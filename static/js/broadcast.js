import { authHeaders, showView, registerRoute, formatDate } from "./common.js";

function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const STATUS_LABEL = { active: "Active", expired: "Expired", all: "Everyone", selected: "Selected customers" };
const STATE_LABEL = { sending: "Sending…", done: "Done", interrupted: "Interrupted" };

let routers = [];
// The audience currently listed: Send is only allowed for exactly this.
let checked = null;
let pollTimer = null;
// Bumped on every refresh so a slow answer for an earlier choice can't
// overwrite the list for the current one.
let refreshToken = 0;

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

function clearAudience(hint) {
    checked = null;
    refreshToken++;
    document.getElementById("broadcast-audience").textContent = hint || "";
    document.getElementById("broadcast-recipients").hidden = true;
    updateSendButton();
}

function updateSendButton() {
    const message = document.getElementById("broadcast-message").value.trim();
    document.getElementById("broadcast-send-btn").disabled = !(checked && checked.selected.size > 0 && message);
    document.getElementById("broadcast-counter").textContent = `${document.getElementById("broadcast-message").value.length} / 480`;
}

// A phone is stored as 2547XXXXXXXX; people search the way they write it
// (0712…, +254712…, 712…), so compare digits only and treat a leading 0 as 254.
function matchesSearch(r, q) {
    if (!q) return true;
    if (`${r.name || ""} ${r.username}`.toLowerCase().includes(q)) return true;
    const digits = q.replace(/\D/g, "");
    if (!digits) return false;
    return r.phone.includes(digits.startsWith("0") ? "254" + digits.slice(1) : digits);
}

function visibleRecipients() {
    const q = document.getElementById("broadcast-recipient-search").value.trim().toLowerCase();
    return checked.recipients.filter(r => matchesSearch(r, q));
}

// The line above the list, updated on every tick so it reads live.
function updateSummary() {
    const total = checked.recipients.length;
    const ticked = checked.selected.size;
    const shown = visibleRecipients();
    const searching = shown.length !== total;
    document.getElementById("broadcast-audience").innerHTML =
        (ticked
            ? `<strong>${ticked}</strong> of ${total} ${total === 1 ? "person" : "people"} will get this message.` +
              (ticked === total ? " Untick anyone you don't want to reach." : "")
            : `<strong>Nobody ticked yet.</strong> ${total} ${total === 1 ? "person matches" : "people match"} — tick who to text.`) +
        (searching ? ` <span class="dim">Showing ${shown.length} matching your search.</span>` : "") +
        (checked.no_phone ? ` <span class="warn">${checked.no_phone} matching customer${checked.no_phone === 1 ? " has" : "s have"} no usable phone number and can't be texted.</span>` : "");
    document.getElementById("broadcast-select-visible").textContent = searching ? `Tick shown (${shown.length})` : "Tick all";
    document.getElementById("broadcast-clear-visible").textContent = searching ? `Untick shown (${shown.length})` : "Untick all";
}

function updateRecipientCount() {
    updateSummary();
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
        </label>`).join("") : `<div class="broadcast-recipients-empty">${checked.recipients.length ? "Nobody matches that search." : "No customers with a phone number match."}</div>`;
}

// Runs whenever the routers or the customers choice changes (and on opening
// the page), so the list is always the one Send would use.
async function refreshAudience() {
    const audience = currentAudience();
    if (!audience.router_ids.length) {
        clearAudience("Pick at least one router to see who would be texted.");
        return;
    }
    const token = ++refreshToken;
    const res = await fetch("/customers/broadcasts/preview", {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ router_ids: audience.router_ids, status_filter: audience.status_filter }),
    });
    if (token !== refreshToken) return;
    if (!res.ok) {
        clearAudience("Couldn't load the customers.");
        return;
    }
    const data = await res.json();
    if (token !== refreshToken) return;
    checked = {
        router_ids: audience.router_ids,
        status_filter: audience.status_filter,
        recipients: data.recipients,
        no_phone: data.no_phone,
        selected: new Set(audience.startEmpty ? [] : data.recipients.map(r => r.customer_id)),
    };
    document.getElementById("broadcast-recipient-search").value = "";
    document.getElementById("broadcast-recipients").hidden = false;
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
    refreshAudience();
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
    refreshAudience();
    loadHistory();
}

export function initBroadcast() {
    document.getElementById("broadcast-routers").addEventListener("change", refreshAudience);
    document.getElementById("broadcast-status").addEventListener("change", refreshAudience);
    document.getElementById("broadcast-message").addEventListener("input", updateSendButton);
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
