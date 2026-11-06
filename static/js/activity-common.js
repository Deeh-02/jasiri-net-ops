// Shared by the three Technical Activity Log views (Submit, Reports,
// Overview), the same way inventory-common.js is shared by the inventory
// views. Imports only from common.js.
import { authHeaders } from "./common.js";

export function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// One call shape for every request: never throws. A dropped connection comes
// back as {ok:false, network:true} so a form can keep what was typed and let
// the person retry (poor mobile data is the normal case for this feature).
export async function api(path, { method = "GET", body } = {}) {
    let res;
    try {
        res = await fetch(path, {
            method,
            headers: authHeaders(body ? { "Content-Type": "application/json" } : {}),
            body: body ? JSON.stringify(body) : undefined,
        });
    } catch {
        return { ok: false, status: 0, network: true, error: "Couldn't reach the server. Check your connection and try again." };
    }
    let data = null;
    try { data = await res.json(); } catch { /* an empty or non-JSON body */ }
    if (!res.ok) {
        let error = typeof data?.detail === "string" ? data.detail : "Something went wrong. Please try again.";
        if (res.status === 401) error = "Your session has expired. Sign in again, then retry.";
        return { ok: false, status: res.status, error };
    }
    return { ok: true, status: res.status, data };
}

// activity_date is a plain calendar date (YYYY-MM-DD) — parsed by hand so the
// viewer's timezone can never move it to the neighbouring day.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function formatDay(iso, { weekday = false, year = true } = {}) {
    if (!iso) return "—";
    const [y, m, d] = iso.split("-").map(Number);
    const dow = weekday ? ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][new Date(Date.UTC(y, m - 1, d)).getUTCDay()] + " " : "";
    return `${dow}${d} ${MONTHS[m - 1]}${year ? " " + y : ""}`;
}

// Today in Nairobi, as YYYY-MM-DD, whatever the device's own timezone is.
export function todayEat() {
    return new Date().toLocaleDateString("en-CA", { timeZone: "Africa/Nairobi" });
}

export function addDays(iso, days) {
    const [y, m, d] = iso.split("-").map(Number);
    const dt = new Date(Date.UTC(y, m - 1, d + days));
    return dt.toISOString().slice(0, 10);
}

// Same four colours the rest of the app uses for good / warning / info / bad.
export const OUTCOME_COLOR = {
    completed: "var(--accent)",
    partially_completed: "var(--warn)",
    inspection_only: "var(--info)",
    unable_to_resolve: "var(--danger)",
};

export function outcomePill(key, label) {
    return `<span class="act-pill"><span class="act-dot" style="background:${OUTCOME_COLOR[key] || "var(--text-dim)"}"></span>${esc(label)}</span>`;
}

export function setError(el, message) {
    el.textContent = message || "";
    el.hidden = !message;
}
