import {
    authHeaders, showMessage, showView, navigate, registerRoute, refreshBadges, timeAgo, formatDate, getCurrentUser,
} from "./common.js";

function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- Alerts > Notifications: the full inbox the bell only shows the top of ----

let notifFilter = "all";

async function loadNotificationsPage() {
    const list = document.getElementById("notif-page-list");
    const res = await fetch("/notifications?limit=200", { headers: authHeaders() });
    if (!res.ok) {
        list.innerHTML = `<div class="notifications-empty">Couldn't load notifications.</div>`;
        return;
    }
    const items = (await res.json()).filter(n => notifFilter === "all" || !n.read);
    if (!items.length) {
        list.innerHTML = `<div class="notifications-empty">${notifFilter === "unread" ? "No unread notifications." : "Nothing here yet."}</div>`;
        return;
    }
    list.innerHTML = items.map(n => `
        <div class="notification-item${n.read ? "" : " unread"}" data-id="${n.id}" data-link="${esc(n.link)}">
            <div class="notification-item-title">${esc(n.title)}</div>
            <div class="notification-item-body">${esc(n.body)}</div>
            <div class="notification-item-time" title="${esc(formatDate(n.created_at))}">${timeAgo(n.created_at)}</div>
        </div>
    `).join("");
}

// ---- Alerts > SMS Templates ----

// Mirrors db/alert_templates.render() exactly, so the preview line is what
// actually gets sent (with example values in place of real ones).
function renderTemplate(body, values) {
    let text = body.replace(/\{\{\s*([A-Za-z_]+)\s*\}\}/g, (_, name) => values[name.toUpperCase()] ?? "");
    text = text.replace(/[ \t]+/g, " ").replace(/ +([.,;:!?)])/g, "$1").replace(/ *\n */g, "\n");
    return text.trim();
}

// HostPinnacle bills per SMS part. Plain text fits 160 characters in one
// SMS (153 per part once it splits); anything with ⚠, ✅, an em dash etc.
// has to go as unicode, which fits only 70 (67 per part).
function smsParts(text) {
    const unicode = /[^\x00-\x7F]/.test(text);
    const [single, multi] = unicode ? [70, 67] : [160, 153];
    const len = text.length;
    return { len, unicode, parts: len <= single ? 1 : Math.ceil(len / multi) };
}

// Sentences that get slotted into another message rather than sent alone.
const FRAGMENT_OF = { battery_recommended: "still_down", battery_none: "still_down" };

let templates = [];

function cardFor(kind) {
    return document.querySelector(`.template-card[data-kind="${kind}"]`);
}

function templateCardHtml(t) {
    const chips = t.placeholders.map(p =>
        `<button type="button" class="template-chip" data-name="${esc(p.name)}" title="${esc(p.hint)}">{{${esc(p.name)}}}</button>`
    ).join("");
    return `
        <div class="template-card" data-kind="${esc(t.kind)}">
            <div class="template-card-head">
                <h3>${esc(t.label)}</h3>
                <span class="template-badge" hidden>Edited</span>
            </div>
            <p class="template-desc">${esc(t.description)}</p>
            <fieldset class="template-field">
                <legend>Message text</legend>
                <textarea rows="3" spellcheck="true"></textarea>
            </fieldset>
            <div class="template-preview"></div>
            <div class="template-meta"></div>
            <div class="template-chips">${chips}</div>
            <div class="template-actions">
                <button type="button" class="template-reset-btn">Reset to default</button>
                <button type="button" class="template-save-btn">Save</button>
            </div>
            <div class="form-msg" id="template-msg-${esc(t.kind)}"></div>
        </div>
    `;
}

function updateCard(kind) {
    const t = templates.find(x => x.kind === kind);
    const card = cardFor(kind);
    if (!t || !card) return;

    const values = { ...t.sample };
    // The still-down preview shows whatever the battery sentence currently
    // says in its own box (saved or not), not a frozen example.
    if (kind === "still_down") {
        const battery = templates.find(x => x.kind === "battery_recommended");
        values.BATTERY_NOTE = renderTemplate(cardFor("battery_recommended").querySelector("textarea").value, battery.sample);
    }
    const text = renderTemplate(card.querySelector("textarea").value, values);
    card.querySelector(".template-preview").textContent = text || "(empty)";

    const meta = [];
    if (FRAGMENT_OF[kind]) {
        meta.push(`Added to the end of "${templates.find(x => x.kind === FRAGMENT_OF[kind]).label}".`);
    } else {
        const { len, unicode, parts } = smsParts(text);
        meta.push(`About ${len} characters · ${parts} SMS per person${unicode ? " (uses emoji or special characters like — so only 70 fit per SMS instead of 160)" : ""}`);
    }
    meta.push(t.is_custom
        ? `Edited${t.updated_by ? ` by ${t.updated_by}` : ""}${t.updated_at ? ` ${timeAgo(t.updated_at)}` : ""}`
        : "Default wording");
    card.querySelector(".template-meta").textContent = meta.join(" · ");
    card.querySelector(".template-badge").hidden = !t.is_custom;
    card.querySelector(".template-reset-btn").hidden = !t.is_custom;
}

async function saveTemplate(kind) {
    const t = templates.find(x => x.kind === kind);
    const body = cardFor(kind).querySelector("textarea").value;
    const res = await fetch(`/monitoring/alert-templates/${kind}`, {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ body }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showMessage(`template-msg-${kind}`, err.detail || "Failed to save", true);
        return;
    }
    Object.assign(t, { body: body.trim(), is_custom: true, updated_by: getCurrentUser()?.name, updated_at: new Date().toISOString() });
    updateCard(kind);
    showMessage(`template-msg-${kind}`, "Saved — used from the next alert on", false);
}

async function resetTemplate(kind) {
    const t = templates.find(x => x.kind === kind);
    if (!confirm(`Put "${t.label}" back to the default wording?`)) return;
    const res = await fetch(`/monitoring/alert-templates/${kind}`, { method: "DELETE", headers: authHeaders() });
    if (!res.ok) {
        showMessage(`template-msg-${kind}`, "Failed to reset", true);
        return;
    }
    Object.assign(t, { body: t.default, is_custom: false, updated_by: null, updated_at: null });
    cardFor(kind).querySelector("textarea").value = t.default;
    updateCard(kind);
    if (kind === "battery_recommended") updateCard("still_down");
    showMessage(`template-msg-${kind}`, "Back to default", false);
}

async function loadTemplatesPage() {
    const wrap = document.getElementById("alert-templates-list");
    const res = await fetch("/monitoring/alert-templates", { headers: authHeaders() });
    if (!res.ok) {
        wrap.innerHTML = `<div class="notifications-empty">Couldn't load message templates.</div>`;
        return;
    }
    templates = await res.json();
    wrap.innerHTML = templates.map(templateCardHtml).join("");

    templates.forEach(t => {
        const card = cardFor(t.kind);
        const textarea = card.querySelector("textarea");
        textarea.value = t.body;
        textarea.addEventListener("input", () => {
            updateCard(t.kind);
            if (t.kind === "battery_recommended") updateCard("still_down");
        });
        card.querySelectorAll(".template-chip").forEach(chip => {
            chip.addEventListener("click", () => {
                textarea.focus();
                textarea.setRangeText(`{{${chip.dataset.name}}}`, textarea.selectionStart, textarea.selectionEnd, "end");
                textarea.dispatchEvent(new Event("input"));
            });
        });
        card.querySelector(".template-save-btn").addEventListener("click", () => saveTemplate(t.kind));
        card.querySelector(".template-reset-btn").addEventListener("click", () => resetTemplate(t.kind));
    });
    templates.forEach(t => updateCard(t.kind));
}

export function initAlerts() {
    document.getElementById("notif-page-settings-btn").addEventListener("click", () => navigate("settings/notifications"));

    document.getElementById("notif-page-mark-all-btn").addEventListener("click", async () => {
        await fetch("/notifications/read-all", { method: "POST", headers: authHeaders() });
        await Promise.all([loadNotificationsPage(), refreshBadges()]);
    });

    document.querySelectorAll(".alerts-filter").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".alerts-filter").forEach(b => b.classList.toggle("active", b === btn));
            notifFilter = btn.dataset.filter;
            loadNotificationsPage();
        });
    });

    document.getElementById("notif-page-list").addEventListener("click", (e) => {
        const item = e.target.closest(".notification-item");
        if (!item) return;
        if (item.classList.contains("unread")) {
            item.classList.remove("unread");
            fetch(`/notifications/${item.dataset.id}/read`, { method: "POST", headers: authHeaders() })
                .then(refreshBadges)
                .catch(() => {});
        }
        if (item.dataset.link) navigate(item.dataset.link);
    });

    registerRoute("notifications", () => {
        showView("view-notifications");
        loadNotificationsPage();
    });

    registerRoute("alert-templates", () => {
        showView("view-alert-templates");
        loadTemplatesPage();
    });
}
