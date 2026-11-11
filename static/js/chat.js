import { authHeaders, getCurrentUser, registerAppShownHandler, registerLogoutHandler } from "./common.js";

// The assistant: a floating "Ask" button on every page that opens a chat
// panel. Shown to anyone the server says has something to ask about
// (GET /chat/access: their permissions decide which lookups they get, and
// revenue is admins only); POST /chat checks again. The browser only talks
// to /chat; the DeepSeek key and address live on the server.
//
// The conversation is a plain array of {role, content} (user/assistant turns
// only), sent in full with each question and mirrored to sessionStorage so a
// page refresh keeps it. Assistant text is always put on the page with
// textContent, never as HTML.

const MAX_TURNS = 20;   // matches the server's CHAT_MAX_MESSAGES default
const MAX_CHARS = 4000; // matches CHAT_MAX_CHARS

let history = [];
let isSending = false;
let els = null;

const storageKey = () => `chat-history-${getCurrentUser()?.id ?? "none"}`;

function saveHistory() {
    try { sessionStorage.setItem(storageKey(), JSON.stringify(history)); } catch { /* private mode: memory only */ }
}

function loadHistory() {
    try {
        const saved = JSON.parse(sessionStorage.getItem(storageKey()) || "[]");
        history = Array.isArray(saved) ? saved.filter(t => (t.role === "user" || t.role === "assistant") && typeof t.content === "string") : [];
    } catch {
        history = [];
    }
}

function buildWidget() {
    const launcher = document.createElement("button");
    launcher.type = "button";
    launcher.className = "chat-launcher";
    launcher.setAttribute("aria-label", "Open the assistant");
    launcher.innerHTML = `
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M4 5.5A2.5 2.5 0 016.5 3h11A2.5 2.5 0 0120 5.5v8a2.5 2.5 0 01-2.5 2.5H10l-4.5 4v-4h0A1.5 1.5 0 014 14.5z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>
        </svg>
        <span>Ask</span>`;

    const panel = document.createElement("section");
    panel.className = "chat-panel";
    panel.hidden = true;
    panel.setAttribute("aria-label", "Assistant");
    panel.innerHTML = `
        <header class="chat-header">
            <div>
                <div class="chat-title">Assistant</div>
                <div class="chat-subtitle">Looks things up for you. Can't change anything.</div>
            </div>
            <button type="button" class="chat-header-btn" data-chat="new" title="Start a new chat">New</button>
            <button type="button" class="chat-header-btn" data-chat="close" aria-label="Close the assistant">✕</button>
        </header>
        <div class="chat-feed" role="log" aria-live="polite"></div>
        <form class="chat-form">
            <textarea class="chat-input" rows="1" maxlength="${MAX_CHARS}" placeholder="Ask about a customer, a site, the OLT, batteries…" aria-label="Your question"></textarea>
            <button type="submit" class="chat-send">Send</button>
        </form>`;

    document.body.append(launcher, panel);
    return {
        launcher, panel,
        feed: panel.querySelector(".chat-feed"),
        form: panel.querySelector(".chat-form"),
        input: panel.querySelector(".chat-input"),
        send: panel.querySelector(".chat-send"),
    };
}

// `checked`: the lookups the server says gave this answer its data. Shown
// under the answer so the admin can tell a looked-up answer from a reply that
// used no data at all.
function bubble(className, text, checked) {
    const div = document.createElement("div");
    div.className = `chat-bubble ${className}`;
    div.textContent = text;
    if (checked && checked.length) {
        const line = document.createElement("div");
        line.className = "chat-checked";
        line.textContent = `Checked: ${checked.join(", ")}`;
        div.appendChild(line);
    }
    return div;
}

function scrollToNewest() {
    els.feed.scrollTop = els.feed.scrollHeight;
}

function renderFeed() {
    els.feed.replaceChildren();
    if (!history.length) {
        els.feed.appendChild(bubble("chat-hint",
            "Try: \"How much did we make yesterday?\", \"Which sites are down?\", " +
            "\"What does the monthly package cost?\" or \"Look up customer 0712345678\"."));
    }
    history.forEach(t => els.feed.appendChild(bubble(t.role === "user" ? "chat-user" : "chat-assistant", t.content, t.checked)));
    scrollToNewest();
}

function setSending(sending) {
    isSending = sending;
    els.input.disabled = sending;
    els.send.disabled = sending;
}

function showTyping() {
    const typing = document.createElement("div");
    typing.className = "chat-bubble chat-assistant chat-typing";
    typing.setAttribute("aria-label", "The assistant is typing");
    typing.innerHTML = "<span></span><span></span><span></span>";
    els.feed.appendChild(typing);
    scrollToNewest();
    return typing;
}

function showError(message) {
    const box = bubble("chat-error", message);
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "chat-retry";
    retry.textContent = "Retry";
    retry.addEventListener("click", () => { box.remove(); askAssistant(); });
    box.appendChild(retry);
    els.feed.appendChild(box);
    scrollToNewest();
}

// Oldest turns go first; what's sent must still start with a question.
function trimmedHistory() {
    let turns = history.slice(-MAX_TURNS);
    while (turns.length && turns[0].role !== "user") turns = turns.slice(1);
    // Only role + text go back to the server (it rejects anything else).
    return turns.map(t => ({ role: t.role, content: t.content }));
}

async function askAssistant() {
    if (isSending) return;
    setSending(true);
    const typing = showTyping();
    try {
        const res = await fetch("/chat", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ messages: trimmedHistory() }),
        });
        if (res.status === 401) {
            // Session expired: same path as pressing Log out, back to the login screen.
            document.getElementById("logout-btn").click();
            return;
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The assistant couldn't answer. Try again.");
        const checked = Array.isArray(data.checked) ? data.checked.filter(c => typeof c === "string") : [];
        history.push({ role: "assistant", content: data.reply, checked });
        saveHistory();
        typing.remove();
        els.feed.appendChild(bubble("chat-assistant", data.reply, checked));
        scrollToNewest();
    } catch (e) {
        typing.remove();
        showError(e.message === "Failed to fetch" ? "Couldn't reach the server. Check your connection." : e.message);
    } finally {
        typing.remove();
        setSending(false);
        els.input.focus();
    }
}

function submitQuestion() {
    const text = els.input.value.trim();
    if (!text || isSending) return;
    if (!history.length) els.feed.replaceChildren(); // drop the hint
    els.feed.querySelectorAll(".chat-error").forEach(e => e.remove());
    history.push({ role: "user", content: text });
    saveHistory();
    els.feed.appendChild(bubble("chat-user", text));
    els.input.value = "";
    autoGrow();
    askAssistant();
}

function autoGrow() {
    els.input.style.height = "auto";
    els.input.style.height = `${Math.min(els.input.scrollHeight, 140)}px`;
}

function openPanel(isOpen) {
    els.panel.hidden = !isOpen;
    els.launcher.hidden = isOpen;
    if (isOpen) { renderFeed(); els.input.focus(); }
}

export function initChat() {
    els = buildWidget();
    els.launcher.hidden = true;

    els.launcher.addEventListener("click", () => openPanel(true));
    els.panel.querySelector('[data-chat="close"]').addEventListener("click", () => openPanel(false));
    els.panel.querySelector('[data-chat="new"]').addEventListener("click", () => {
        if (isSending) return;
        history = [];
        saveHistory();
        renderFeed();
        els.input.focus();
    });
    els.form.addEventListener("submit", e => { e.preventDefault(); submitQuestion(); });
    els.input.addEventListener("keydown", e => {
        // Enter sends; Shift+Enter is a new line.
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitQuestion(); }
    });
    els.input.addEventListener("input", autoGrow);

    registerAppShownHandler(async () => {
        els.launcher.hidden = true;
        els.panel.hidden = true;
        let available = false;
        try {
            const res = await fetch("/chat/access", { headers: authHeaders() });
            available = res.ok && (await res.json()).available === true;
        } catch { /* offline: no button */ }
        els.launcher.hidden = !available;
        if (available) loadHistory();
    });
    registerLogoutHandler(() => {
        try { sessionStorage.removeItem(storageKey()); } catch { /* nothing stored */ }
        history = [];
        els.launcher.hidden = true;
        els.panel.hidden = true;
    });
}
