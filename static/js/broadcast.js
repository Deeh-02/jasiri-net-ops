import { authHeaders, showView, registerRoute, formatDate } from "./common.js";

function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const STATUS_LABEL = { active: "Active", expired: "Expired", all: "Everyone" };
const STATE_LABEL = { sending: "Sending…", done: "Done", interrupted: "Interrupted" };

let routers = [];
// The audience the user last checked: Send is only allowed for exactly this.
let checked = null;
let pollTimer = null;

function selectedRouterIds() {
    return [...document.querySelectorAll("#broadcast-routers input:checked")].map(i => Number(i.value));
}

function currentAudience() {
    return { router_ids: selectedRouterIds(), status_filter: document.getElementById("broadcast-status").value };
}

function setMessage(text, isError) {
    const el = document.getElementById("broadcast-msg");
    el.textContent = text;
    el.className = `form-msg ${isError ? "error" : "success"}`;
}

function invalidateCheck() {
    checked = null;
    document.getElementById("broadcast-audience").textContent = "";
    updateSendButton();
}

function updateSendButton() {
    const message = document.getElementById("broadcast-message").value.trim();
    document.getElementById("broadcast-send-btn").disabled = !(checked && checked.count > 0 && message);
    document.getElementById("broadcast-counter").textContent = `${document.getElementById("broadcast-message").value.length} / 480`;
}

async function checkAudience() {
    const audience = currentAudience();
    const box = document.getElementById("broadcast-audience");
    if (!audience.router_ids.length) {
        box.innerHTML = `<span class="warn">Pick at least one router.</span>`;
        return;
    }
    const res = await fetch("/customers/broadcasts/preview", {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(audience),
    });
    if (!res.ok) {
        box.innerHTML = `<span class="warn">Couldn't check the audience.</span>`;
        return;
    }
    const data = await res.json();
    checked = { ...audience, count: data.count };
    box.innerHTML = `<strong>${data.count}</strong> ${data.count === 1 ? "person" : "people"} will be texted.` +
        (data.no_phone ? ` <span class="warn">${data.no_phone} matching customer${data.no_phone === 1 ? " has" : "s have"} no usable phone number and will be skipped.</span>` : "");
    updateSendButton();
}

async function sendBroadcast() {
    if (!checked) return;
    const message = document.getElementById("broadcast-message").value.trim();
    const routerNames = routers.filter(r => checked.router_ids.includes(r.id)).map(r => r.name).join(" + ");
    const ok = confirm(`Send this SMS to ${checked.count} ${STATUS_LABEL[checked.status_filter].toLowerCase()} customers on ${routerNames}?\n\n${message}\n\nIt cannot be recalled.`);
    if (!ok) return;

    const btn = document.getElementById("broadcast-send-btn");
    btn.disabled = true;
    const res = await fetch("/customers/broadcasts", {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ ...checked, message, expected_count: checked.count }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        setMessage(data.detail || "Couldn't send.", true);
        updateSendButton();
        return;
    }
    setMessage(`Sending to ${data.recipient_count} people…`, false);
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
