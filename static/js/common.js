// ---- Auth state ----
let authToken = localStorage.getItem("authToken");
let currentUser = JSON.parse(localStorage.getItem("currentUser") || "null");
let currentPermissions = new Set();

// Reads a JWT's payload without verifying its signature — that's the
// server's job on every request; this only exists so the client can avoid
// trusting a token it can already tell is expired, before ever making a
// network call. Returns null for anything that doesn't parse, which
// isTokenExpired() below treats the same as "expired": a token we can't
// read the expiry of isn't one we can trust either.
function decodeJwtPayload(token) {
    try {
        const base64Url = token.split(".")[1];
        const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
        const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
        return JSON.parse(atob(padded));
    } catch {
        return null;
    }
}

function isTokenExpired(token) {
    const payload = decodeJwtPayload(token);
    if (!payload || typeof payload.exp !== "number") return true;
    return Date.now() >= payload.exp * 1000;
}

// Single place that forgets a session — used on explicit logout, and by
// resolveAuthUI() below when the stored token has already expired. Keeping
// this in one function means the two can't drift (e.g. one clearing
// currentUser from localStorage but not the in-memory copy `can()` reads).
function clearSession() {
    authToken = null;
    currentUser = null;
    localStorage.removeItem("authToken");
    localStorage.removeItem("currentUser");
}

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

const ROUTE_PERMISSION_MAP = {
    dashboard: ["batteries", "view"],
    sites: ["sites", "view"],
    users: ["users", "view"],
    roles: ["roles", "view"],
    movements: ["movements", "view"],
    "check-sites": ["site_checks", "view"],
    // Independently gated from the Inventory master permission (below) —
    // "Inventory Items" and "Stock" are each their own toggle in roles.js's
    // permissions panel now, controlling access to these two routes
    // specifically, distinct from inventory_items:view (the domain-level
    // master toggle that hides the whole Inventory nav group regardless of
    // these — see NAV_GROUP_MASTER_PERMISSION below).
    inventory: ["inventory_items", "view_items"],
    stock: ["inventory_items", "view_stock"],
    "inventory-log": ["inventory_transactions", "view"],
    "issue-materials": ["inventory_transactions", "issue"],
    "return-materials": ["inventory_transactions", "return"],
    // Its own dedicated master permission (reports:view) — not
    // inventory_items:view, so Reports can be denied/granted independently
    // of the rest of the Inventory domain (see the "Reports" top-level
    // section in roles.js's PERM_SECTIONS). Reports is its own top-level
    // .nav-category (not nested under Inventory's group, unlike inventory/
    // stock/inventory-log/inventory-manage above), so it's gated by the
    // plain heading[data-view] branch in applyPermissionVisibility() below —
    // no NAV_GROUP_MASTER_PERMISSION entry needed for it.
    "inventory-reports": ["reports", "view"],
    "inventory-manage": ["inventory_categories", "view"],
};

function isRouteAllowed(name) {
    const mapping = ROUTE_PERMISSION_MAP[name];
    return mapping ? can(mapping[0], mapping[1]) : true;
}

// The first route the current user is actually allowed to see, computed once
// per login by applyPermissionVisibility() and cached here so dispatchRoute
// (below) has somewhere to redirect a denied navigation — permissions don't
// change mid-session (loadPermissions() also only runs once, at login), so a
// single cached value stays correct for the life of the session.
let cachedFirstAllowedRoute = null;

// The domain-level master permission for a sub-grouped nav category
// (currently just Inventory) — this must be true for the whole group to
// show at all, on top of each sub-link's own permission. Mirrors the
// permissions panel's own master-toggle-collapses-everything cascade
// (roles.js), so turning "Inventory" off hides Items/Stock/Log/Manage
// together even though each is now independently permissioned.
const NAV_GROUP_MASTER_PERMISSION = { inventory: ["inventory_items", "view"] };

function applyPermissionVisibility() {
    let firstAllowed = null;

    // Iterating .nav-category (not .nav-heading[data-view]) so DOM order
    // still drives firstAllowed correctly once a group — currently just
    // Inventory — has no data-view of its own on its heading and needs an
    // OR-of-its-sub-items visibility rule instead of a single route.
    document.querySelectorAll(".nav-category").forEach(category => {
        const subLinks = category.querySelectorAll(".nav-link.sub[data-view]");
        if (subLinks.length) {
            const groupMapping = NAV_GROUP_MASTER_PERMISSION[category.dataset.navGroup];
            const masterAllowed = groupMapping ? can(groupMapping[0], groupMapping[1]) : true;
            let anyAllowed = false;
            subLinks.forEach(link => {
                const mapping = ROUTE_PERMISSION_MAP[link.dataset.view];
                const allowed = masterAllowed && (mapping ? can(mapping[0], mapping[1]) : true);
                link.hidden = !allowed;
                if (allowed) {
                    anyAllowed = true;
                    if (!firstAllowed) firstAllowed = link.dataset.view;
                }
            });
            category.hidden = !anyAllowed;
            return;
        }
        const heading = category.querySelector(".nav-heading[data-view]");
        if (!heading) return;
        const mapping = ROUTE_PERMISSION_MAP[heading.dataset.view];
        const allowed = mapping ? can(mapping[0], mapping[1]) : true;
        category.hidden = !allowed;
        if (allowed && !firstAllowed) firstAllowed = heading.dataset.view;
    });

    document.querySelectorAll(".view").forEach(v => v.hidden = true);

    const addBtnMap = {
        "add-battery-open-btn": ["batteries", "add"],
        "add-site-open-btn": ["sites", "add"],
        "add-user-open-btn": ["users", "add"],
        "add-role-open-btn": ["roles", "add"],
        "add-inventory-category-open-btn": ["inventory_categories", "add"],
        "add-inventory-transaction-open-btn": ["inventory_transactions", "add"],
        // Add Item opens a wizard covering both New Product (create a new
        // SKU) and Existing Product (add stock to one that already exists)
        // — one permission gates both now that "Add Stock" was folded into
        // "Add item".
        "add-inventory-item-open-btn": ["inventory_items", "add"],
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

    // Manage is Categories-only now (Locations has no management screen —
    // see inventory-manage.js), so ROUTE_PERMISSION_MAP already covers the
    // whole view via inventory-manage's own entry; this just gates the
    // Actions column within it, same as every other items-actions column.
    const inventoryCategoriesActionsTh = document.getElementById("inventory-categories-actions-th");
    if (inventoryCategoriesActionsTh) inventoryCategoriesActionsTh.hidden = !(can("inventory_categories", "edit") || can("inventory_categories", "delete"));

    // No visibility toggle needed for the Items or Stock tables' Actions
    // columns: Items' Edit/Delete/View buttons are individually gated inline
    // in inventory.js's renderItemsTable and stock.js's renderStockTable —
    // View specifically now checks inventory_items:view_history, not just
    // :view (see roles.js's "View Stock History" permission).

    const movementsLinkBtn = document.getElementById("movements-link-btn");
    if (movementsLinkBtn) movementsLinkBtn.hidden = !can("movements", "view");

    const checkSitesLinkBtn = document.getElementById("check-sites-link-btn");
    if (checkSitesLinkBtn) checkSitesLinkBtn.hidden = !can("site_checks", "view");

    const issueMaterialsLinkBtn = document.getElementById("issue-materials-link-btn");
    if (issueMaterialsLinkBtn) issueMaterialsLinkBtn.hidden = !can("inventory_transactions", "issue");

    const returnMaterialsLinkBtn = document.getElementById("return-materials-link-btn");
    if (returnMaterialsLinkBtn) returnMaterialsLinkBtn.hidden = !can("inventory_transactions", "return");

    return firstAllowed;
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

// Every timestamp in the app displays in East Africa Time regardless of the
// viewer's own device/browser timezone — EAT has no DST, so this offset
// never needs revisiting. The backend sends explicit UTC ("...Z") timestamps
// for exactly this reason: without an explicit zone, `new Date(iso)` would
// otherwise be ambiguous about what instant it even refers to.
const DISPLAY_TIMEZONE = "Africa/Nairobi";

export function formatDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString([], {
        year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
        timeZone: DISPLAY_TIMEZONE,
    });
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

// ---- Router: reflects the current section (and, for a couple of views, a
// drill-down param like a battery id) in the URL hash via pushState, so
// browser back/forward moves between views instead of doing nothing. View
// modules register a handler for their own route name — common.js just
// parses the hash and dispatches, it doesn't know what any view contains.
const routeHandlers = {};
export function registerRoute(name, handler) {
    routeHandlers[name] = handler;
}

// Runs before every dispatch, regardless of which route is landed on — lets
// a view close its own modals/overlays when navigation moves away from it,
// without every other route handler needing to know those modals exist.
const routeResetters = [];
export function registerRouteResetter(fn) {
    routeResetters.push(fn);
}

function parseRoute(hash) {
    const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
    return { name: parts[0] || "dashboard", params: parts.slice(1) };
}

// Maps a collapsible sidebar group's id prefix to the route names that
// belong to it. Drives the group heading's .active state exactly the same
// way every plain [data-view] nav item's active state works (Batteries lit
// up only while you're on the Batteries page, no separate click needed to
// "arm" it) — the heading has no data-view of its own to be picked up by the
// generic loop below, so this fills that in for it — plus auto-*expands*
// (never auto-collapses) the group when the active route lands inside it,
// e.g. a page refresh or a deep link straight to a sub-route while the group
// still shows collapsed.
const NAV_GROUP_ROUTES = { inventory: ["inventory", "stock", "inventory-log", "inventory-manage"] };

function setActiveNav(name) {
    document.querySelectorAll("[data-view]").forEach(l => l.classList.toggle("active", l.dataset.view === name));

    Object.entries(NAV_GROUP_ROUTES).forEach(([group, routes]) => {
        const toggle = document.getElementById(`${group}-nav-toggle`);
        const inGroup = routes.includes(name);
        toggle?.classList.toggle("active", inGroup);
        if (!inGroup) return;
        toggle?.classList.remove("collapsed");
        document.getElementById(`${group}-nav-subitems`)?.classList.remove("collapsed");
    });
}

function dispatchRoute() {
    const { name, params } = parseRoute(location.hash);
    const handler = routeHandlers[name];
    if (!handler) return;
    // Nav links/headings for a denied route are already hidden by
    // applyPermissionVisibility, but that alone doesn't stop a hash typed
    // directly into the address bar (or set via the console) — every
    // navigation, not just the first one at login, has to re-check the
    // permission a route maps to, or hiding the link is only cosmetic.
    if (!isRouteAllowed(name)) {
        if (cachedFirstAllowedRoute && cachedFirstAllowedRoute !== name) {
            navigate(cachedFirstAllowedRoute, { replace: true });
        }
        return;
    }
    routeResetters.forEach(fn => fn());
    setActiveNav(name);
    handler(params);
}

export function navigate(path, { replace = false } = {}) {
    const hash = "#/" + path.replace(/^\/+/, "");
    if (hash === location.hash) return;
    if (replace) history.replaceState(null, "", hash);
    else history.pushState(null, "", hash);
    dispatchRoute();
}

window.addEventListener("popstate", dispatchRoute);

// Called once per login/session-restore, after permissions are known, so an
// unauthorized or stale hash falls back to the first section the user can
// actually see instead of dispatching to nothing.
function startRouter(fallback) {
    const { name } = parseRoute(location.hash);
    if (routeHandlers[name] && isRouteAllowed(name)) {
        // A bare "" hash parses to the default route but doesn't say so in
        // the address bar — normalize it so the URL always names the view
        // that's actually showing (and so the very first history entry is
        // "#/<name>", not "", which back() would otherwise land on).
        if (!location.hash) history.replaceState(null, "", "#/" + name);
        dispatchRoute();
    } else if (fallback) {
        navigate(fallback, { replace: true });
    }
}

function initHeaderLinkIcons() {
    document.getElementById("movements-link-icon").innerHTML = moveIconSvg();
    document.getElementById("check-sites-link-icon").innerHTML = `
        <svg viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M8 1.5L14 4V8C14 11.5 11.5 13.8 8 14.5C4.5 13.8 2 11.5 2 8V4L8 1.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
            <path d="M5.5 8L7.2 9.7L10.5 6.2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
    `;
    const issueMaterialsLinkIcon = document.getElementById("issue-materials-link-icon");
    if (issueMaterialsLinkIcon) issueMaterialsLinkIcon.innerHTML = `
        <svg viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M2 4.5H10.5V11.5H2V4.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
            <path d="M13.5 8H8M8 8L10 6M8 8L10 10" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
    `;
    const returnMaterialsLinkIcon = document.getElementById("return-materials-link-icon");
    if (returnMaterialsLinkIcon) returnMaterialsLinkIcon.innerHTML = `
        <svg viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M14 4.5H5.5V11.5H14V4.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
            <path d="M2.5 8H8M8 8L6 6M8 8L6 10" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
    `;
}

export async function refreshBadges() {
    // No /notifications/unread-count call here: there's no notifications
    // feature on the backend yet (no route, no table) — the bell icon in
    // the topbar is a placeholder with no click handler either. Add the
    // fetch back once that feature actually exists server-side.
    const [movRes, siteRes] = await Promise.all([
        fetch("/movements/active-count", { headers: authHeaders() }),
        fetch("/locations/unconfirmed-count", { headers: authHeaders() }),
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
}

// ---- App-shown handlers: view modules register their own initial data load,
// run once (concurrently) right after login/session-restore reveals the app. ----
const appShownHandlers = [];
export function registerAppShownHandler(fn) {
    appShownHandlers.push(fn);
}

// ---- Logout handlers: view modules register cleanup that must happen the
// instant the session ends, run synchronously from the logout click below —
// currently just each view's live-sync interval. Without this, an interval
// started at boot (dashboard.js, movements.js) has no way to learn a logout
// happened: it isn't gated on auth state, only on its own view being
// visible, and logging out hides #app-layout, not the view element inside
// it. A tick landing in the gap between logout and the next login fires
// with authToken already null, hitting protected endpoints with no
// Authorization header and drawing a 401 — reproducible specifically via
// logout-then-immediate-relogin, not a fresh page load, since only the
// logout path leaves the interval running unattended. ----
const logoutHandlers = [];
export function registerLogoutHandler(fn) {
    logoutHandlers.push(fn);
}

export async function showApp() {
    document.getElementById("login-screen").hidden = true;
    document.getElementById("global-topbar").hidden = false;
    document.getElementById("app-layout").hidden = false;
    await loadPermissions();
    const firstAllowed = applyPermissionVisibility();
    cachedFirstAllowedRoute = firstAllowed;
    renderTopbarUser();
    initHeaderLinkIcons();
    await Promise.all([
        ...appShownHandlers.map(fn => fn()),
        refreshBadges(),
    ]);
    startRouter(firstAllowed);
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
            // Sidebar nav items are gated by their .nav-category being
            // hidden; the Movements/Check Sites header quick-links aren't
            // in a category at all — they're gated by their own .hidden,
            // set directly in applyPermissionVisibility(). Checking both
            // covers either flavor without assuming which one applies.
            const category = el.closest(".nav-category");
            return (!category || !category.hidden) && !el.hidden;
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
    return {
        section: "Go to", action: "Action", battery: "Battery", site: "Site", user: "User", role: "Role",
        "inventory-item": "Item", "inventory-transaction": "Transaction",
    }[type] || "";
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

    // searchText is an optional richer haystack (e.g. SKU/serial/category
    // alongside the name) an entity can supply when its label alone isn't
    // enough to find it by — falls back to label for every provider that
    // doesn't set one (battery/site/user/role), so their matching is
    // unchanged.
    cmdkCurrentItems = q
        ? [...sectionItems, ...entityItems].filter(i => (i.searchText || i.label).toLowerCase().includes(q))
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
const VIEW_NAMES = ["dashboard", "sites", "movements", "check-sites", "users", "roles", "settings", "inventory", "stock", "inventory-log", "issue-materials", "return-materials", "inventory-reports", "inventory-manage"];

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
        navigate("settings");
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
        logoutHandlers.forEach(fn => fn());
        clearSession();
        document.getElementById("global-topbar").hidden = true;
        document.getElementById("app-layout").hidden = true;
        document.getElementById("login-screen").hidden = false;
        history.replaceState(null, "", location.pathname + location.search);
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

    // ---- Desktop-only scroll handoff between nav and content ----
    // The app shell is a fixed-height flexbox on desktop (see common.css) —
    // .sidebar and .content each scroll independently, the same contained-
    // scroll-region technique .table-scroll already uses on mobile, just
    // applied to the outer shell. Once the nav hits either end of its own
    // scroll range, continued scrolling in that direction hands off to the
    // content panel instead of the nav just stopping dead; there's no
    // reverse handoff (content maxing out never scrolls the nav). Scoped to
    // desktop widths — mobile's sidebar is an off-canvas overlay, not a
    // persistent side-by-side region, so there's nothing to hand off there.
    const contentPanel = document.querySelector(".content");
    if (contentPanel) {
        sidebar.addEventListener("wheel", (e) => {
            if (window.innerWidth <= 760) return;
            const atTop = sidebar.scrollTop <= 0;
            const atBottom = sidebar.scrollTop + sidebar.clientHeight >= sidebar.scrollHeight - 1;
            if ((e.deltaY < 0 && atTop) || (e.deltaY > 0 && atBottom)) {
                e.preventDefault();
                contentPanel.scrollTop += e.deltaY;
            }
        }, { passive: false });
    }

    // ---- Collapsible sidebar groups: the heading (Inventory) is a pure
    // expand/collapse toggle, not a link — clicking it only opens or closes
    // its submenu and never navigates or changes the active page; only the
    // submenu's own items (Items, Stock, Transaction Log, Manage) do that,
    // via the generic [data-view] listener below, which explicitly skips
    // any element that has a group heading's shape (see isGroupHeading
    // there). The chevron is a second, dedicated way to toggle without
    // relying on the label area — stopPropagation keeps its click from
    // also re-triggering this same toggle via bubbling. Landing on one of
    // this group's routes from somewhere else (a submenu click, a deep
    // link, back/forward) still auto-expands the group — that's
    // setActiveNav's NAV_GROUP_ROUTES handling elsewhere in this file, not
    // this listener. ----
    document.querySelectorAll(".nav-heading").forEach(toggle => {
        const subitems = toggle.nextElementSibling;
        if (!subitems || !subitems.classList.contains("nav-subitems")) return;
        toggle.addEventListener("click", () => {
            toggle.classList.toggle("collapsed");
            subitems.classList.toggle("collapsed");
        });
        toggle.querySelector(".chevron")?.addEventListener("click", (e) => {
            e.stopPropagation();
            toggle.classList.toggle("collapsed");
            subitems.classList.toggle("collapsed");
        });
    });

    // ---- Nav view switching ----
    document.querySelectorAll("[data-view]").forEach(link => {
        // A collapsible group's heading (has a .nav-subitems sibling) is a
        // pure expand/collapse toggle now, not a link — the listener above
        // already handles opening/closing its submenu. Only its sub-items
        // (Items, Stock, Transaction Log, Manage) carry their own data-view
        // and should actually navigate; the heading itself keeps its
        // data-view attribute only so setActiveNav can style it, but this
        // listener must never call navigate() for it, or clicking the
        // group would both toggle the submenu *and* jump to whichever
        // route the heading happens to be tagged with.
        const isGroupHeading = link.nextElementSibling?.classList.contains("nav-subitems");
        if (isGroupHeading) return;
        link.addEventListener("click", () => {
            const category = link.closest(".nav-category");
            if (category && category.hidden) return; // no permission — don't switch
            navigate(link.dataset.view);
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

// ---- Called synchronously, before view fragments are fetched, so a
// refresh with a saved token never paints the login screen at all: the DOM
// starts on a neutral #auth-loading placeholder (see index.html) and this
// either reveals the login form (no token, so we already know for certain
// there's no session) or leaves everything hidden for showApp() below to
// take over once it's ready. Splitting this out of bootAuth() matters
// because loadViewFragments() is an async gap — dispatching the "logged
// out" UI only after that gap resolves is exactly what caused the flash. ----
export function resolveAuthUI() {
    // A present-but-expired token is exactly as unauthenticated as no token
    // at all — trusting its mere presence here is what used to render the
    // full app shell (sidebar, topbar, whichever route was in the hash)
    // against a session the server was already going to reject on the
    // very first request, instead of the login screen. This runs before
    // bootAuth() below ever checks `authToken`, so clearing it here means
    // that check — and everything downstream of it — sees "logged out"
    // exactly like the no-token case, with no separate expiry check needed
    // there.
    if (authToken && isTokenExpired(authToken)) {
        clearSession();
    }
    document.getElementById("auth-loading").hidden = true;
    if (!authToken) {
        document.getElementById("login-screen").hidden = false;
    }
}

// ---- Resolves whether a saved session should jump straight back into the
// app, once view fragments are loaded and every view has registered. ----
export function bootAuth() {
    if (authToken) {
        showApp();
    }
}
