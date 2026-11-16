import { authHeaders, can } from "./common.js";
import { esc, money, monthLabel, errorText, card } from "./finance-common.js";

// Finance > Income > PPPoE: the upload box for the billing system's recharge
// export, the PPPoE income source (owner's choice, 2026-11-16). A section of
// the Income page kept in its own file, like finance-hotspot.js.
//
// Check, then save: the same call twice (commit false, then true), so what
// the check showed is exactly what saving does. Only recharges not saved
// before are added; what the rules leave out (test accounts, KSh 0, the
// set-up burst, double entries) is listed with the reason.

let box = null;
let onSaved = null;

const when = iso => iso ? new Date(iso).toLocaleString("en-GB", {
    timeZone: "Africa/Nairobi", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
}) : "";

export async function mountPppoeUpload(el, saved) {
    box = el;
    onSaved = saved;
    box.hidden = false;
    const res = await fetch("/finance/pppoe/status", { headers: authHeaders() }).catch(() => null);
    const status = res && res.ok ? await res.json() : null;
    const notSetUp = res && res.status === 503 ? await errorText(res, "") : "";
    let sub;
    if (notSetUp) sub = notSetUp;
    else if (status?.last_upload) {
        sub = `Recorded up to ${when(status.latest_recharge)}: ${Number(status.recharges).toLocaleString()} recharges, ${money(status.total)}. Last upload ${when(status.last_upload)}.`;
    } else sub = "Nothing uploaded yet.";
    const canUpload = can("finance", "manage_expenses") && !notSetUp;
    box.innerHTML = card({ title: "PPPoE recharges", sub, cls: "fin-ppp-upload", body: canUpload ? `
        <p class="muted fin-ppp-intro">On the billing system, export the recharge list (.csv or .xlsx) and upload it here.
            Uploading the full list again is fine: recharges already saved are skipped.</p>
        <div class="toolbar">
            <label class="toolbar-field fin-ppp-file"><span>Recharge export</span>
                <input type="file" class="control" id="fin-ppp-file" accept=".csv,.xlsx"></label>
            <button type="button" class="btn-secondary" id="fin-ppp-check">Check file</button>
            <button type="button" class="btn-primary" id="fin-ppp-save" hidden>Save</button>
        </div>
        <div class="fin-ppp-result" id="fin-ppp-result" role="status"></div>` : "" });
    if (!canUpload) return;
    box.querySelector("#fin-ppp-check").addEventListener("click", () => send(false));
    box.querySelector("#fin-ppp-save").addEventListener("click", () => send(true));
    box.querySelector("#fin-ppp-file").addEventListener("change", () => {
        box.querySelector("#fin-ppp-save").hidden = true;
        box.querySelector("#fin-ppp-result").innerHTML = "";
    });
}

export function hidePppoeUpload(el) {
    el.hidden = true;
    el.innerHTML = "";
}

async function send(commit) {
    const result = box.querySelector("#fin-ppp-result");
    const save = box.querySelector("#fin-ppp-save");
    const file = box.querySelector("#fin-ppp-file").files[0];
    if (!file) {
        result.innerHTML = `<span class="error">Choose the recharge export first.</span>`;
        return;
    }
    const form = new FormData();
    form.append("file", file);
    form.append("commit", commit);
    save.hidden = true;
    result.innerHTML = `<span class="muted">${commit ? "Saving…" : "Reading the file…"}</span>`;
    const res = await fetch("/finance/pppoe/import", { method: "POST", headers: authHeaders(), body: form }).catch(() => null);
    if (!res || !res.ok) {
        result.innerHTML = `<span class="error">${esc(await errorText(res, "Couldn't read that file."))}</span>`;
        return;
    }
    const d = await res.json();
    result.innerHTML = summary(d, commit);
    if (commit) {
        box.querySelector("#fin-ppp-file").value = "";
        onSaved?.();
    } else if (d.new) {
        save.hidden = false;
        save.textContent = `Save ${Number(d.new).toLocaleString()} new recharge${d.new === 1 ? "" : "s"}`;
    }
}

function summary(d, commit) {
    const head = commit ? `<strong>Saved.</strong>` : `<strong>Checked: nothing saved yet.</strong>`;
    const counts = `${Number(d.in_file).toLocaleString()} recharges in the file, ${when(d.first)} to ${when(d.latest)}:
        ${Number(d.new).toLocaleString()} new, ${Number(d.already_saved).toLocaleString()} already saved.`;
    if (!d.new) return `<p>${head} ${counts} Nothing to add.</p>`;
    const months = d.months.length ? `<div class="table-scroll table-compact"><table class="table-dense">
        <thead><tr><th>Month</th><th class="num">Recharges</th><th class="num">Income</th></tr></thead>
        <tbody>${d.months.map(m => `<tr><td>${esc(monthLabel(`${m.month}-01`))}</td><td class="num">${m.recharges}</td>
            <td class="num">${money(m.total)}</td></tr>`).join("")}</tbody></table></div>` : "";
    const left = d.left_out.map(l => `<li><strong>${l.recharges} left out: ${esc(l.label)}</strong> (${money(l.total)}, not counted)
        <details><summary>Which</summary><ul class="fin-ppp-examples">${l.examples.map(e => `<li>${esc(e)}</li>`).join("")}
        ${l.recharges > l.examples.length ? `<li class="muted">and ${l.recharges - l.examples.length} more</li>` : ""}</ul></details></li>`).join("");
    const unreadable = d.unreadable?.length ? `<li>${d.unreadable.length} line${d.unreadable.length === 1 ? "" : "s"} couldn't be read (no amount or date): line ${d.unreadable.slice(0, 8).join(", ")}</li>` : "";
    return `<p>${head} ${counts}</p>
        <ul class="fin-ppp-list">
            <li><strong>${Number(d.counted).toLocaleString()} count as income: ${money(d.counted_total)}</strong>,
                ${Number(d.by_bank).toLocaleString()} paid through the NCBA paybill, the rest recharged by staff.</li>
            ${left}${unreadable}
        </ul>${months}`;
}
