// ---- Auth state ----
let authToken = localStorage.getItem("authToken");
let currentUser = JSON.parse(localStorage.getItem("currentUser") || "null");
let currentPermissions = new Set();

export function can(section, action) {
    if (currentUser && currentUser.role === "admin") return true;
    return currentPermissions.has(`${section}:${action}`);
}

export function authHeaders(extra = {}) {
    return authToken
        ? { ...extra, "Authorization": `Bearer ${authToken}` }
        : extra;
}

async function loadPermissions() {
    if (currentUser && currentUser.role === "admin") {
        currentPermissions = new Set();
        return;
    }
    const res = await fetch("/me/permissions", { headers: authHeaders() });
    if (res.ok) {
        const perms = await res.json();
        currentPermissions = new Set(
            perms.filter(p => p.allowed).map(p => `${p.section}:${p.action}`)
        );
    } else {
        currentPermissions = new Set();
    }
}

function applyPermissionVisibility() {
    const SECTION_VIEW_MAP = {
        dashboard: ["batteries", "view"],
        sites: ["sites", "view"],
        users: ["users", "view"],
        roles: ["roles", "view"]
    };

    let firstAllowed = null;

    document.querySelectorAll(".nav-heading[data-view]").forEach(btn => {
        const mapping = SECTION_VIEW_MAP[btn.dataset.view];
        const category = btn.closest(".nav-category");
        const allowed = mapping ? can(mapping[0], mapping[1]) : true;
        if (category) category.hidden = !allowed;
        if (allowed && !firstAllowed) firstAllowed = btn.dataset.view;
    });

    document.querySelectorAll("[data-view]").forEach(l => l.classList.remove("active"));
    document.querySelectorAll(".view").forEach(v => v.hidden = true);
    if (firstAllowed) {
        document.querySelector(`.nav-heading[data-view="${firstAllowed}"]`).classList.add("active");
        document.getElementById("view-" + firstAllowed).hidden = false;
    }

    const addBtnMap = {
        "add-battery-open-btn": ["batteries", "add"],
        "add-site-open-btn": ["sites", "add"],
        "add-user-open-btn": ["users", "add"],
        "add-role-open-btn": ["roles", "add"]
    };
    Object.entries(addBtnMap).forEach(([id, mapping]) => {
        const el = document.getElementById(id);
        if (el) el.hidden = !can(mapping[0], mapping[1]);
    });

    const usersManageTh = document.getElementById("users-manage-th");
    if (usersManageTh) usersManageTh.hidden = !(can("users", "edit") || can("users", "delete"));

    const rolesActionsTh = document.getElementById("roles-actions-th");
    if (rolesActionsTh) rolesActionsTh.hidden = !(can("roles", "edit") || can("roles", "delete"));

    const sitesActionsTh = document.getElementById("sites-actions-th");
    if (sitesActionsTh) sitesActionsTh.hidden = !(can("sites", "edit") || can("sites", "delete"));

    const movementsLinkBtn = document.getElementById("movements-link-btn");
    if (movementsLinkBtn) movementsLinkBtn.hidden = !can("movements", "view");

    const checkSitesLinkBtn = document.getElementById("check-sites-link-btn");
    if (checkSitesLinkBtn) checkSitesLinkBtn.hidden = !can("site_checks", "view");
}

export function initials(name) {
    if (!name) return "?";
    const parts = name.trim().split(/\s+/);
    return parts.length === 1 ? parts[0][0].toUpperCase() : (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function renderTopbarUser() {
    if (!currentUser) return;
    document.getElementById("topbar-avatar-btn").textContent = initials(currentUser.name);
    document.getElementById("topbar-avatar-name").textContent = currentUser.name;
    document.getElementById("topbar-avatar-role").textContent = currentUser.role;
}

export function getCurrentUser() {
    return currentUser;
}

// Single source of truth for the logged-in user's profile fields — mutates
// the shared in-memory copy (the same one `can()` reads), persists it, and
// re-renders the topbar so avatar initials stay in sync.
export function updateCurrentUser(patch) {
    currentUser = { ...currentUser, ...patch };
    localStorage.setItem("currentUser", JSON.stringify(currentUser));
    renderTopbarUser();
}

export function showMessage(elementId, text, isError) {
    const el = document.getElementById(elementId);
    el.textContent = text;
    el.className = "form-msg " + (isError ? "error" : "success");
    setTimeout(() => { el.textContent = ""; el.className = "form-msg"; }, 3000);
}

export function formatDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString([], { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

export function batteryIconSvg(status) {
    const fillWidths = { charged: 16, charging: 10, low: 4, unknown: 0 };
    const w = fillWidths[status] !== undefined ? fillWidths[status] : 0;
    return `
        <svg width="20" height="11" viewBox="0 0 22 12">
            <rect x="0.5" y="0.5" width="18" height="11" rx="2" stroke="var(--text-dim)" fill="none"/>
            <rect x="19" y="4" width="2" height="4" rx="1" fill="var(--text-dim)"/>
            <rect class="battery-fill ${status}" x="2" y="2" width="${w}" height="7" rx="1"/>
        </svg>
    `;
}

export function moveIconSvg() {
    return `
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M2 5.5H12M12 5.5L9 2.5M12 5.5L9 8.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M14 10.5H4M4 10.5L7 7.5M4 10.5L7 13.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
    `;
}

export function editIconSvg() {
    return `
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
            <path d="M11.5 2.5L13.5 4.5L5 13H3V11L11.5 2.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
        </svg>
    `;
}

export function viewIconSvg() {
    return `
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
            <path d="M1 8C1 8 3.5 3 8 3C12.5 3 15 8 15 8C15 8 12.5 13 8 13C3.5 13 1 8 1 8Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
            <circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.3"/>
        </svg>
    `;
}

export function deleteIconSvg() {
    return `
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
            <path d="M3 4.5H13M6 4.5V3a1 1 0 011-1h2a1 1 0 011 1v1.5M4.5 4.5L5 13a1 1 0 001 1h4a1 1 0 001-1l.5-8.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
    `;
}

export function showView(viewId) {
    document.querySelectorAll(".view").forEach(v => v.hidden = true);
    document.getElementById(viewId).hidden = false;
}

function initHeaderLinkIcons() {
    document.getElementById("movements-link-icon").innerHTML = moveIconSvg();
    document.getElementById("check-sites-link-icon").innerHTML = `
        <svg viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M8 1.5L14 4V8C14 11.5 11.5 13.8 8 14.5C4.5 13.8 2 11.5 2 8V4L8 1.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
            <path d="M5.5 8L7.2 9.7L10.5 6.2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
    `;
}

export async function refreshBadges() {
    const [movRes, siteRes, notifRes] = await Promise.all([
        fetch("/movements/active-count", { headers: authHeaders() }),
        fetch("/locations/unconfirmed-count", { headers: authHeaders() }),
        fetch("/notifications/unread-count", { headers: authHeaders() })
    ]);

    if (movRes.ok) {
        const { count } = await movRes.json();
        const badge = document.getElementById("movements-badge");
        badge.textContent = count;
        badge.hidden = count === 0;
        const navBadge = document.getElementById("battery-nav-badge");
        navBadge.textContent = count;
        navBadge.hidden = count === 0;
    }
    if (siteRes.ok) {
        const { count } = await siteRes.json();
        const badge = document.getElementById("sites-badge");
        badge.textContent = count;
        badge.hidden = count === 0;
        const navBadge = document.getElementById("sites-nav-badge");
        navBadge.textContent = count;
        navBadge.hidden = count === 0;
    }
    if (notifRes.ok) {
        const { count } = await notifRes.json();
        const badge = document.getElementById("notifications-badge");
        badge.textContent = count;
        badge.hidden = count === 0;
    }
}

// ---- App-shown handlers: view modules register their own initial data load,
// run once (concurrently) right after login/session-restore reveals the app. ----
const appShownHandlers = [];
export function registerAppShownHandler(fn) {
    appShownHandlers.push(fn);
}

export async function showApp() {
    document.getElementById("login-screen").hidden = true;
    document.getElementById("global-topbar").hidden = false;
    document.getElementById("app-layout").hidden = false;
    await loadPermissions();
    applyPermissionVisibility();
    renderTopbarUser();
    initHeaderLinkIcons();
    await Promise.all([
        ...appShownHandlers.map(fn => fn()),
        refreshBadges(),
    ]);
}

// ---- Command palette: view modules register a provider (their own cache +
// actions) instead of common.js reaching into each view's data directly. ----
const cmdkProviders = [];
export function registerCmdkProvider(provider) {
    cmdkProviders.push(provider);
}

function getSectionItems() {
    return Array.from(document.querySelectorAll("[data-view]"))
        .filter(el => {
            const category = el.closest(".nav-category");
            return !category || !category.hidden;
        })
        .map(el => ({
            type: "section",
            label: el.dataset.label || el.textContent.trim(),
            action: () => el.click(),
        }));
}

function getEntityItems() {
    return cmdkProviders.flatMap(p => p.getItems());
}

function cmdkTypeLabel(type) {
    return { section: "Go to", battery: "Battery", site: "Site", user: "User", role: "Role" }[type] || "";
}

let cmdkOverlay, cmdkInput, cmdkResults;
let cmdkSelectedIndex = 0;
let cmdkCurrentItems = [];

async function openCmdk() {
    cmdkOverlay.hidden = false;
    cmdkInput.value = "";
    cmdkSelectedIndex = 0;
    cmdkResults.innerHTML = `<div class="cmdk-empty">Loading...</div>`;
    cmdkInput.focus();

    // Let providers lazily load anything not yet fetched, so search covers
    // sections the user hasn't visited yet.
    await Promise.all(cmdkProviders.map(p => p.ensureLoaded ? p.ensureLoaded() : Promise.resolve()));

    renderCmdkResults("");
}

function closeCmdk() {
    cmdkOverlay.hidden = true;
}

function renderCmdkResults(query) {
    const q = query.toLowerCase().trim();
    const sectionItems = getSectionItems();
    const entityItems = getEntityItems();

    cmdkCurrentItems = q
        ? [...sectionItems, ...entityItems].filter(i => i.label.toLowerCase().includes(q))
        : sectionItems;

    if (cmdkCurrentItems.length === 0) {
        cmdkResults.innerHTML = `<div class="cmdk-empty">No matches</div>`;
        return;
    }

    cmdkResults.innerHTML = cmdkCurrentItems.map((item, i) => `
        <div class="cmdk-item ${i === cmdkSelectedIndex ? "selected" : ""}" data-index="${i}">
            <span class="cmdk-item-type">${cmdkTypeLabel(item.type)}</span>
            <span class="cmdk-item-label">${item.label}</span>
            ${item.sublabel ? `<span class="cmdk-item-sub">${item.sublabel}</span>` : ""}
        </div>
    `).join("");

    cmdkResults.querySelectorAll(".cmdk-item").forEach(el => {
        el.addEventListener("click", () => {
            cmdkCurrentItems[Number(el.dataset.index)].action();
            closeCmdk();
        });
    });
}

// ---- Fragment loader: fetches every view's HTML and injects it into its
// mount point. Loaded eagerly, all at once, at startup — the app is small
// enough that lazy-per-nav loading isn't worth the added state-tracking. ----
const VIEW_NAMES = ["dashboard", "sites", "movements", "check-sites", "users", "roles", "settings"];

export async function loadViewFragments() {
    await Promise.all(VIEW_NAMES.map(async (name) => {
        const mount = document.querySelector(`[data-view-mount="${name}"]`);
        const res = await fetch(`/static/views/${name}.html`);
        mount.outerHTML = await res.text();
    }));
}

// ---- Shell wiring: elements that live in index.html itself (not a
// fragment), so this can run before view fragments are loaded. ----
export function initShell() {
    cmdkOverlay = document.getElementById("cmdk-overlay");
    cmdkInput = document.getElementById("cmdk-input");
    cmdkResults = document.getElementById("cmdk-results");

    // ---- Login: password show/hide toggle ----
    document.getElementById("login-password-toggle").addEventListener("click", () => {
        const input = document.getElementById("login-password");
        const btn = document.getElementById("login-password-toggle");
        const isPassword = input.type === "password";
        input.type = isPassword ? "text" : "password";
        btn.querySelector(".eye-open").hidden = isPassword;
        btn.querySelector(".eye-closed").hidden = !isPassword;
        btn.classList.toggle("is-visible", isPassword);
    });

    // ---- Login: animated traveling border on the login card ----
    (function animateLoginBorder() {
        const box = document.querySelector(".login-box");
        if (!box) return;

        function sizeTrace() {
            const size = Math.max(box.offsetWidth, box.offsetHeight) * 1.6;
            box.style.setProperty("--trace-size", size + "px");
        }
        sizeTrace();
        window.addEventListener("resize", sizeTrace);

        let angle = 0;
        function tick() {
            angle = (angle + 0.6) % 360;
            box.style.setProperty("--border-angle", angle + "deg");
            requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
    })();

    // ---- Login ----
    document.getElementById("login-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const email = document.getElementById("login-email").value;
        const password = document.getElementById("login-password").value;

        const response = await fetch("/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, password })
        });

        if (response.ok) {
            const data = await response.json();
            authToken = data.access_token;
            currentUser = data.user;
            localStorage.setItem("authToken", authToken);
            localStorage.setItem("currentUser", JSON.stringify(currentUser));
            showApp();
        } else {
            showMessage("login-msg", "Invalid email or password", true);
        }
    });

    // ---- Settings ----
    document.getElementById("settings-open-btn").addEventListener("click", () => {
        document.getElementById("topbar-avatar-menu").hidden = true;
        document.querySelectorAll("[data-view]").forEach(l => l.classList.remove("active"));
        showView("view-settings");
    });

    document.getElementById("topbar-search-btn").addEventListener("click", openCmdk);

    document.getElementById("topbar-avatar-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        const menu = document.getElementById("topbar-avatar-menu");
        menu.hidden = !menu.hidden;
    });

    document.addEventListener("click", () => {
        document.getElementById("topbar-avatar-menu").hidden = true;
    });

    document.getElementById("logout-btn").addEventListener("click", () => {
        localStorage.removeItem("authToken");
        localStorage.removeItem("currentUser");
        authToken = null;
        currentUser = null;
        document.getElementById("global-topbar").hidden = true;
        document.getElementById("app-layout").hidden = true;
        document.getElementById("login-screen").hidden = false;
    });

    // ---- Off-canvas nav (phone widths only — the sidebar is always visible
    // on desktop, where the toggle button is hidden by CSS) ----
    const sidebar = document.getElementById("sidebar");
    const sidebarBackdrop = document.getElementById("sidebar-backdrop");

    function setNavOpen(isOpen) {
        sidebar.classList.toggle("open", isOpen);
        sidebarBackdrop.hidden = !isOpen;
    }

    document.getElementById("nav-toggle-btn").addEventListener("click", () => {
        setNavOpen(!sidebar.classList.contains("open"));
    });

    sidebarBackdrop.addEventListener("click", () => setNavOpen(false));

    // ---- Nav view switching ----
    document.querySelectorAll("[data-view]").forEach(link => {
        link.addEventListener("click", () => {
            const category = link.closest(".nav-category");
            if (category && category.hidden) return; // no permission — don't switch
            document.querySelectorAll("[data-view]").forEach(l => l.classList.remove("active"));
            link.classList.add("active");
            showView("view-" + link.dataset.view);
            setNavOpen(false); // picking a section dismisses the drawer on phones
        });
    });

    // ---- Command palette (Ctrl+K / Cmd+K) ----
    document.addEventListener("keydown", (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === "k") {
            e.preventDefault();
            openCmdk();
        } else if (e.key === "Escape") {
            closeCmdk();
        }
    });

    cmdkInput.addEventListener("input", () => {
        cmdkSelectedIndex = 0;
        renderCmdkResults(cmdkInput.value);
    });

    cmdkInput.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown") {
            e.preventDefault();
            cmdkSelectedIndex = Math.min(cmdkSelectedIndex + 1, cmdkCurrentItems.length - 1);
            renderCmdkResults(cmdkInput.value);
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            cmdkSelectedIndex = Math.max(cmdkSelectedIndex - 1, 0);
            renderCmdkResults(cmdkInput.value);
        } else if (e.key === "Enter" && cmdkCurrentItems[cmdkSelectedIndex]) {
            cmdkCurrentItems[cmdkSelectedIndex].action();
            closeCmdk();
        }
    });

    cmdkOverlay.addEventListener("click", (e) => {
        if (e.target === cmdkOverlay) closeCmdk();
    });
}

// ---- Resolves whether a saved session should jump straight back into the
// app, once view fragments are loaded and every view has registered. ----
export function bootAuth() {
    if (authToken) {
        showApp();
    } else {
        document.getElementById("login-screen").hidden = false;
    }
}
