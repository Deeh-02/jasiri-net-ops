import { authHeaders, stateRow, markRefreshing, formatDate } from "./common.js";

// The "Network Health alerts" tab of SMS Status: the alarm texts Network Health
// sent to the team (or, in log-only mode, would have). Read from
// /network/health/sms, so it needs Network Health > View. Every value goes onto
// the page as text.

const DEVICES = { ccr2116: "CCR2116", ccr2004: "CCR2004", feed: "Frankfurt feed", olt: "OLT", crs326: "CRS326" };
const COLUMNS = 4;

function cell(label, child) {
    const td = document.createElement("td");
    td.dataset.label = label;
    td.append(child);
    return td;
}

export async function loadNetworkHealthAlerts() {
    const rows = document.getElementById("sms-nh-rows");
    const days = document.getElementById("sms-nh-days").value;
    markRefreshing(rows, COLUMNS, "Loading the alerts…");
    const res = await fetch(`/network/health/sms?days=${encodeURIComponent(days)}`, { headers: authHeaders() }).catch(() => null);
    if (!res || !res.ok) {
        rows.innerHTML = stateRow(COLUMNS, "error", "Couldn't load the alerts. Try again in a moment.");
        return;
    }
    const d = await res.json();
    const mode = d.mode === "live" ? "sending" : "log-only (nothing is sent)";
    document.getElementById("sms-nh-sub").textContent =
        `${d.sent_today} of ${d.cap} sent today · ${mode} · ${d.recipients} recipient${d.recipients === 1 ? "" : "s"}`;
    if (!d.log.length) {
        rows.innerHTML = stateRow(COLUMNS, "empty", "Nothing was sent or held back in this period.", "No alerts");
        return;
    }
    rows.replaceChildren(...d.log.map(a => {
        const tr = document.createElement("tr");
        const outcome = document.createElement("span");
        const word = document.createElement("span");
        word.className = `nh-outcome nh-outcome-${a.outcome}`;
        word.textContent = a.outcome;
        outcome.append(word);
        if (a.reason) {
            const why = document.createElement("span");
            why.className = "nh-reason";
            why.textContent = ` ${a.reason}`;
            outcome.append(why);
        }
        tr.append(cell("Time", document.createTextNode(formatDate(a.created_at))),
                  cell("Device", document.createTextNode(DEVICES[a.device] || a.device || "—")),
                  cell("Message", document.createTextNode(a.message)),
                  cell("Outcome", outcome));
        return tr;
    }));
}
