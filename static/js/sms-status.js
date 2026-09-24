import { authHeaders, showView, registerRoute, formatDate } from "./common.js";

function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const KIND_LABEL = { down: "Site down", recovered: "Recovered", flapping: "Flapping" };
const STATUS_LABEL = {
    sent: "Sent", failed: "Failed", not_configured: "Not configured",
    rate_limited: "Rate limited", skipped_quiet_hours: "Quiet hours",
};
const CHANNEL_LABEL = { sms: "SMS", whatsapp: "WhatsApp", in_app: "In-app" };

function isoDay(d) {
    return d.toLocaleDateString("en-CA", { timeZone: "Africa/Nairobi" });
}

function responseText(r) {
    if (r.provider_response == null) return "";
    return r.provider_response.error || r.provider_response.reason || JSON.stringify(r.provider_response);
}

async function loadSmsStatus() {
    const params = new URLSearchParams({
        start: document.getElementById("sms-status-start").value,
        end: document.getElementById("sms-status-end").value,
        channel: document.getElementById("sms-status-channel").value,
        status: document.getElementById("sms-status-status").value,
    });
    for (const [k, v] of [...params]) if (!v) params.delete(k);

    const rows = document.getElementById("sms-status-rows");
    const summary = document.getElementById("sms-status-summary");
    const res = await fetch(`/monitoring/sms-log?${params}`, { headers: authHeaders() });
    if (!res.ok) {
        summary.innerHTML = "";
        rows.innerHTML = `<tr><td colspan="9">Couldn't load the SMS log.</td></tr>`;
        return;
    }
    const data = await res.json();
    summary.innerHTML = Object.entries(data.counts).map(([s, n]) =>
        `<span class="sms-status-chip ${esc(s)}">${esc(STATUS_LABEL[s] || s)}: ${n}</span>`).join("");
    if (!data.rows.length) {
        rows.innerHTML = `<tr><td colspan="9">Nothing sent in this period.</td></tr>`;
        return;
    }
    rows.innerHTML = data.rows.map(r => `
        <tr>
            <td>${r.id}</td>
            <td>${esc(KIND_LABEL[r.kind] || r.kind)}</td>
            <td>${esc(CHANNEL_LABEL[r.channel] || r.channel)}</td>
            <td>${esc(r.user_name || "—")}</td>
            <td>${esc(r.recipient || "—")}</td>
            <td>${esc(r.site_name || "—")}</td>
            <td>${esc(STATUS_LABEL[r.status] || r.status)}</td>
            <td class="sms-status-response" title="${esc(responseText(r))}">${esc(responseText(r))}</td>
            <td>${esc(formatDate(r.sent_at))}</td>
        </tr>
    `).join("");
}

export function initSmsStatus() {
    const today = new Date();
    document.getElementById("sms-status-end").value = isoDay(today);
    document.getElementById("sms-status-start").value = isoDay(new Date(today.getTime() - 30 * 86400000));
    ["sms-status-start", "sms-status-end", "sms-status-channel", "sms-status-status"]
        .forEach(id => document.getElementById(id).addEventListener("change", loadSmsStatus));

    registerRoute("sms-status", () => {
        showView("view-sms-status");
        loadSmsStatus();
    });
}
