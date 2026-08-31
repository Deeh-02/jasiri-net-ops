// ---- Auth ----
let authToken = localStorage.getItem("authToken");
let currentUser = JSON.parse(localStorage.getItem("currentUser") || "null");
let currentPermissions = new Set();

function can(section, action) {
    if (currentUser && currentUser.role === "admin") return true;
    return currentPermissions.has(`${section}:${action}`);
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

function authHeaders(extra = {}) {
    return authToken
        ? { ...extra, "Authorization": `Bearer ${authToken}` }
        : extra;
}

async function showApp() {
    document.getElementById("login-screen").hidden = true;
    document.getElementById("global-topbar").hidden = false;
    document.getElementById("app-layout").hidden = false;
    await loadPermissions();
    applyPermissionVisibility();
    renderTopbarUser();
    loadDashboard();
    initHeaderLinkIcons();
    refreshBadges();
}

function initials(name) {
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

// ---- Settings ----
document.getElementById("settings-open-btn").addEventListener("click", () => {
    document.getElementById("topbar-avatar-menu").hidden = true;
    openSettings();
});

function openSettings() {
    document.querySelectorAll("[data-view]").forEach(l => l.classList.remove("active"));
    document.querySelectorAll(".view").forEach(v => v.hidden = true);
    document.getElementById("view-settings").hidden = false;
    populateProfileForm();
}

function populateProfileForm() {
    if (!currentUser) return;
    const parts = (currentUser.name || "").trim().split(/\s+/);
    document.getElementById("profile-first-name").value = parts[0] || "";
    document.getElementById("profile-last-name").value = parts.slice(1).join(" ") || "";
    document.getElementById("profile-phone").value = currentUser.phone || "";
    document.getElementById("profile-email").value = currentUser.email || "";
}

document.querySelectorAll(".settings-tab").forEach(tab => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".settings-tab").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        const target = tab.dataset.settingsTab;
        document.getElementById("settings-tab-profile").hidden = target !== "profile";
        document.getElementById("settings-tab-password").hidden = target !== "password";
    });
});

document.getElementById("profile-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const first = document.getElementById("profile-first-name").value.trim();
    const last = document.getElementById("profile-last-name").value.trim();
    const name = [first, last].filter(Boolean).join(" ");
    const phone = document.getElementById("profile-phone").value || null;
    const email = document.getElementById("profile-email").value;

    const response = await fetch("/me", {
        method: "PATCH",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ name, email, phone })
    });

    if (response.ok) {
        currentUser = { ...currentUser, name, email, phone };
        localStorage.setItem("currentUser", JSON.stringify(currentUser));
        renderTopbarUser();
        showMessage("profile-form-msg", "Profile updated", false);
    } else {
        showMessage("profile-form-msg", "Failed to update profile", true);
    }
});

document.getElementById("password-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const current_password = document.getElementById("password-current").value;
    const new_password = document.getElementById("password-new").value;
    const confirm_password = document.getElementById("password-confirm").value;

    if (new_password !== confirm_password) {
        showMessage("password-form-msg", "New passwords don't match", true);
        return;
    }

    const response = await fetch("/me/password", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ current_password, new_password })
    });

    if (response.ok) {
        document.getElementById("password-form").reset();
        showMessage("password-form-msg", "Password updated", false);
    } else {
        const err = await response.json().catch(() => ({}));
        showMessage("password-form-msg", err.detail || "Failed to update password", true);
    }
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
// ---- Nav view switching ----
document.querySelectorAll("[data-view]").forEach(link => {
    link.addEventListener("click", () => {
        const category = link.closest(".nav-category");
        if (category && category.hidden) return; // no permission — don't switch
        document.querySelectorAll("[data-view]").forEach(l => l.classList.remove("active"));
        link.classList.add("active");
        document.querySelectorAll(".view").forEach(v => v.hidden = true);
        document.getElementById("view-" + link.dataset.view).hidden = false;
    });
});
// ---- Users screen ----
let usersCache = [];
let editUserId = null;

document.querySelector('[data-view="users"]').addEventListener("click", loadUsers);

async function loadUsers() {
    const res = await fetch("/users", { headers: authHeaders() });
    if (!res.ok) {
        alert("Failed to load users");
        return;
    }
    usersCache = await res.json();
    renderUsersList(usersCache);
}

function renderUsersList(users) {
    const tbody = document.getElementById("users-rows");
    if (!tbody) return;

    const activeUsers = users.filter(u => u.status !== "inactive");
    const hasManage = can("users", "edit") || can("users", "delete");

    tbody.innerHTML = activeUsers.map(u => `
        <tr>
            <td>${u.name}</td>
            <td>${u.phone || "—"}</td>
            <td>${u.email}</td>
            <td>${capitalize(u.role)}</td>
            ${hasManage ? `
            <td>
                ${can("users", "edit") ? `
                <button type="button" class="edit-user-btn" data-id="${u.id}" title="Edit user">
                    ${editIconSvg()}
                </button>` : ""}
                ${can("users", "delete") ? `
                <button type="button" class="delete-user-btn" data-id="${u.id}" data-name="${u.name}" title="Deactivate user">
                    ${deleteIconSvg()}
                </button>` : ""}
            </td>` : ""}
        </tr>
    `).join("");

    tbody.querySelectorAll(".edit-user-btn").forEach(btn => {
        btn.addEventListener("click", () => openUserForm(btn.dataset.id));
    });

    tbody.querySelectorAll(".delete-user-btn").forEach(btn => {
        btn.addEventListener("click", () => deactivateUser(btn.dataset.id, btn.dataset.name));
    });
}

async function deactivateUser(id, name) {
    if (!confirm(`Deactivate user "${name}"? They will no longer be able to log in.`)) return;

    const response = await fetch(`/users/${id}`, { method: "DELETE", headers: authHeaders() });
    if (response.ok) {
        await loadUsers();
    } else {
        alert("Failed to deactivate user");
    }
}

// ---- Add/Edit User form (full screen) ----
async function openUserForm(userId) {
    const form = document.getElementById("user-form");
    form.reset();

    const roleSelect = document.getElementById("user-form-role");
    roleSelect.innerHTML = '<option value="admin">Admin</option>';

    const rolesRes = await fetch("/roles", { headers: authHeaders() });
    if (rolesRes.ok) {
        const roles = await rolesRes.json();
        roles.forEach(r => {
            const opt = document.createElement("option");
            opt.value = String(r.id);
            opt.textContent = r.name;
            roleSelect.appendChild(opt);
        });
    }

    if (userId) {
        const u = usersCache.find(x => String(x.id) === String(userId));
        if (!u) return;
        editUserId = u.id;
        document.getElementById("user-form-title").textContent = "Edit User";
        document.getElementById("user-form-id").value = u.id;
        document.getElementById("user-form-name").value = u.name || "";
        document.getElementById("user-form-phone").value = u.phone || "";
        document.getElementById("user-form-email").value = u.email || "";
        roleSelect.value = u.role === "admin" ? "admin" : String(u.role_id || "");
        document.getElementById("user-form-password").hidden = true;
        document.getElementById("user-form-password").required = false;
    } else {
        editUserId = null;
        document.getElementById("user-form-title").textContent = "Add User";
        document.getElementById("user-form-password").hidden = false;
        document.getElementById("user-form-password").required = true;
    }

    document.querySelectorAll("[data-view]").forEach(l => l.classList.remove("active"));
    document.querySelectorAll(".view").forEach(v => v.hidden = true);
    document.getElementById("view-user-form").hidden = false;
}

document.getElementById("add-user-open-btn").addEventListener("click", () => openUserForm(null));

document.getElementById("user-form-cancel").addEventListener("click", () => {
    document.querySelectorAll(".view").forEach(v => v.hidden = true);
    document.getElementById("view-users").hidden = false;
});

document.getElementById("user-form").addEventListener("submit", async (e) => {
    e.preventDefault();

    const name = document.getElementById("user-form-name").value;
    const phone = document.getElementById("user-form-phone").value || null;
    const email = document.getElementById("user-form-email").value;
    const password = document.getElementById("user-form-password").value;

    const roleSelect = document.getElementById("user-form-role");
    const selectedValue = roleSelect.value;
    const isAdmin = selectedValue === "admin";
    const role = isAdmin ? "admin" : roleSelect.options[roleSelect.selectedIndex].textContent.toLowerCase();
    const role_id = isAdmin ? null : parseInt(selectedValue);

    let response;
    if (editUserId) {
        response = await fetch(`/users/${editUserId}`, {
            method: "PATCH",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ name, email, phone, role, role_id })
        });
    } else {
        response = await fetch("/users", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ name, email, phone, role, role_id, password })
        });
    }

    if (response.ok) {
        showMessage("user-form-msg", editUserId ? "User updated" : "User added", false);
        document.querySelectorAll(".view").forEach(v => v.hidden = true);
        document.getElementById("view-users").hidden = false;
        await loadUsers();
    } else {
        const err = await response.json().catch(() => ({}));
        showMessage("user-form-msg", err.detail || "Failed to save user", true);
    }
});

// ---- Roles screen ----
let rolesCache = [];
let editRoleId = null;

document.querySelector('[data-view="roles"]').addEventListener("click", loadRoles);

async function loadRoles() {
    const res = await fetch("/roles", { headers: authHeaders() });
    if (!res.ok) {
        alert("Failed to load roles");
        return;
    }
    rolesCache = await res.json();
    renderRolesList(rolesCache);
}

function renderRolesList(roles) {
    const tbody = document.getElementById("roles-rows");
    if (!tbody) return;

    const hasActions = can("roles", "edit") || can("roles", "delete");

    tbody.innerHTML = roles.map(r => `
        <tr>
            <td>${r.name}</td>
            <td>${formatDate(r.created_at)}</td>
            ${hasActions ? `
            <td>
                ${can("roles", "edit") ? `
                <button type="button" class="edit-role-btn" data-id="${r.id}" title="Edit role">
                    ${editIconSvg()}
                </button>` : ""}
                ${can("roles", "delete") ? `
                <button type="button" class="delete-role-btn" data-id="${r.id}" data-name="${r.name}" title="Delete role">
                    ${deleteIconSvg()}
                </button>` : ""}
            </td>` : ""}
        </tr>
    `).join("");

    tbody.querySelectorAll(".edit-role-btn").forEach(btn => {
        btn.addEventListener("click", () => openRoleForm(btn.dataset.id));
    });

    tbody.querySelectorAll(".delete-role-btn").forEach(btn => {
        btn.addEventListener("click", () => deleteRole(btn.dataset.id, btn.dataset.name));
    });
}

async function deleteRole(id, name) {
    if (!confirm(`Delete role "${name}"? This can't be undone.`)) return;

    const response = await fetch(`/roles/${id}`, { method: "DELETE", headers: authHeaders() });
    if (response.ok) {
        await loadRoles();
    } else {
        alert("Failed to delete role");
    }
}

const PERM_SECTIONS = [
    {
        key: "batteries", label: "Batteries", actions: ["add", "edit", "delete"],
        children: [
            { key: "movements", label: "Movements", actions: ["manage"] }
        ]
    },
    {
        key: "sites", label: "Sites", actions: ["add", "edit", "delete"],
        children: [
            { key: "site_checks", label: "Check Sites", actions: ["confirm"] }
        ]
    },
    { key: "users", label: "Users", actions: ["add", "edit", "delete"] },
    { key: "roles", label: "Roles", actions: ["add", "edit", "delete"] },
];

const ACTION_NOUN = { batteries: "battery", movements: "movement", sites: "site", site_checks: "site check", users: "user", roles: "role" };

function renderPermGrid(permissions) {
    const allowedSet = new Set(
        (permissions || []).filter(p => p.allowed).map(p => `${p.section}:${p.action}`)
    );

    const grid = document.getElementById("perm-grid");

    function renderActionCheckboxes(sectionKey, actions, noun) {
        return actions.map(action => {
            const checked = allowedSet.has(`${sectionKey}:${action}`);
            const actionLabel = capitalize(action) + " " + noun;
            return `
                <label class="perm-checkbox-row">
                    <input type="checkbox" class="perm-action-checkbox" data-section="${sectionKey}" data-action="${action}" ${checked ? "checked" : ""}>
                    ${actionLabel}
                </label>
            `;
        }).join("");
    }

    function renderChild(child) {
        const viewChecked = allowedSet.has(`${child.key}:view`);
        const noun = ACTION_NOUN[child.key];
        return `
            <div class="perm-subsection" style="margin-left:20px;margin-top:10px;">
                <div class="perm-section-header perm-subsection-header">
                    <span class="perm-section-name">${child.label}</span>
                    <label class="perm-toggle">
                        <input type="checkbox" class="perm-view-toggle" data-section="${child.key}" ${viewChecked ? "checked" : ""}>
                        <span class="perm-toggle-track"></span>
                        <span class="perm-toggle-thumb"></span>
                    </label>
                </div>
                <div class="perm-menu" data-section-menu="${child.key}" ${viewChecked ? "" : "hidden"}>
                    <div class="perm-checkbox-grid">${renderActionCheckboxes(child.key, child.actions, noun)}</div>
                </div>
            </div>
        `;
    }

    grid.innerHTML = PERM_SECTIONS.map(section => {
        const viewChecked = allowedSet.has(`${section.key}:view`);
        const noun = ACTION_NOUN[section.key];

        return `
            <div class="perm-section">
                <div class="perm-section-header">
                    <span class="perm-section-name">${section.label}</span>
                    <label class="perm-toggle">
                        <input type="checkbox" class="perm-view-toggle" data-section="${section.key}" ${viewChecked ? "checked" : ""}>
                        <span class="perm-toggle-track"></span>
                        <span class="perm-toggle-thumb"></span>
                    </label>
                </div>
                <div class="perm-menu" data-section-menu="${section.key}" ${viewChecked ? "" : "hidden"}>
                    <div class="perm-checkbox-grid">${renderActionCheckboxes(section.key, section.actions, noun)}</div>
                    ${(section.children || []).map(renderChild).join("")}
                </div>
            </div>
        `;
    }).join("");

    grid.querySelectorAll(".perm-view-toggle").forEach(toggle => {
        toggle.addEventListener("change", () => {
            const sectionKey = toggle.dataset.section;
            const menu = grid.querySelector(`[data-section-menu="${sectionKey}"]`);
            menu.hidden = !toggle.checked;
            if (!toggle.checked) {
                menu.querySelectorAll(".perm-action-checkbox").forEach(cb => cb.checked = false);
                menu.querySelectorAll(".perm-view-toggle").forEach(childToggle => {
                    childToggle.checked = false;
                    const childMenu = grid.querySelector(`[data-section-menu="${childToggle.dataset.section}"]`);
                    if (childMenu) {
                        childMenu.hidden = true;
                        childMenu.querySelectorAll(".perm-action-checkbox").forEach(cb => cb.checked = false);
                    }
                });
            }
        });
    });
}


async function openRoleForm(roleId) {
    const form = document.getElementById("role-form");
    form.reset();
    renderPermGrid([]);

    if (roleId) {
        const r = rolesCache.find(x => String(x.id) === String(roleId));
        if (!r) return;
        editRoleId = r.id;
        document.getElementById("role-form-title").textContent = "Edit Role";
        document.getElementById("role-form-id").value = r.id;
        document.getElementById("role-form-name").value = r.name || "";

        const permRes = await fetch(`/roles/${r.id}/permissions`, { headers: authHeaders() });
        if (permRes.ok) {
            const permissions = await permRes.json();
            renderPermGrid(permissions);
        }
    } else {
        editRoleId = null;
        document.getElementById("role-form-title").textContent = "Add Role";
    }

    document.querySelectorAll("[data-view]").forEach(l => l.classList.remove("active"));
    document.querySelectorAll(".view").forEach(v => v.hidden = true);
    document.getElementById("view-role-form").hidden = false;
}

document.getElementById("add-role-open-btn").addEventListener("click", () => openRoleForm(null));

document.getElementById("role-form-cancel").addEventListener("click", () => {
    document.querySelectorAll(".view").forEach(v => v.hidden = true);
    document.getElementById("view-roles").hidden = false;
});

document.getElementById("role-form").addEventListener("submit", async (e) => {
    e.preventDefault();

    const name = document.getElementById("role-form-name").value;

    let response;
    if (editRoleId) {
        response = await fetch(`/roles/${editRoleId}`, {
            method: "PATCH",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ name })
        });
    } else {
        response = await fetch("/roles", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ name })
        });
    }

    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        showMessage("role-form-msg", err.detail || "Failed to save role", true);
        return;
    }

    const saved = await response.json();
    const roleId = editRoleId || saved.id;

    const permissions = [
    ...Array.from(document.querySelectorAll(".perm-view-toggle")).map(t => ({
        section: t.dataset.section,
        action: "view",
        allowed: t.checked
    })),
    ...Array.from(document.querySelectorAll(".perm-action-checkbox")).map(cb => ({
        section: cb.dataset.section,
        action: cb.dataset.action,
        allowed: cb.checked
    }))
];;

    const permRes = await fetch(`/roles/${roleId}/permissions`, {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ permissions })
    });

    if (permRes.ok) {
        showMessage("role-form-msg", editRoleId ? "Role updated" : "Role added", false);
        document.querySelectorAll(".view").forEach(v => v.hidden = true);
        document.getElementById("view-roles").hidden = false;
        await loadRoles();
    } else {
        showMessage("role-form-msg", "Role saved but permissions failed to save", true);
    }
});

const CHARGE_OPTIONS = ["charged", "charging", "low", "unknown"];
let batteriesCache = [];
let locationsCache = [];
let moveModalBatteryId = null;
let editSiteId = null;

// ---- Stats: 5 boxes, no Total, distinct colors ----
function buildStats(batteries) {
    const deployed = batteries.filter(b => b.status === "Deployed").length;
    const charged = batteries.filter(b => b.charge_status === "charged").length;
    const charging = batteries.filter(b => b.charge_status === "charging").length;
    const low = batteries.filter(b => b.charge_status === "low").length;
    const unknown = batteries.filter(b => b.charge_status === "unknown").length;

    return [
        { label: "Deployed", value: deployed, cls: "deployed" },
        { label: "Charged", value: charged, cls: "charged" },
        { label: "Charging", value: charging, cls: "charging" },
        { label: "Low", value: low, cls: "low" },
        { label: "Unknown", value: unknown, cls: "unknown" },
    ];
}

// ---- Initial load: shows loading text once ----
async function loadDashboard() {
    document.getElementById("stat-grid").innerHTML = '<div class="loading-text">Loading...</div>';
    document.getElementById("battery-rows").innerHTML = '<tr><td colspan="9" class="loading-text">Loading batteries...</td></tr>';
    await refreshData();
}

// ---- Quiet refresh: no blanking, just swap content in place ----
async function refreshData() {
    const [batteriesRes, locationsRes] = await Promise.all([
    fetch("/batteries", { headers: authHeaders() }),
    fetch("/locations", { headers: authHeaders() })
]);
    batteriesCache = batteriesRes.ok ? await batteriesRes.json() : [];
    locationsCache = locationsRes.ok ? await locationsRes.json() : [];

    renderStats(buildStats(batteriesCache));
    renderTable(batteriesCache);
    populateMoveLocationSelect(locationsCache);
    renderSiteList(locationsCache);
}

function renderStats(stats) {
    const grid = document.getElementById("stat-grid");
    grid.innerHTML = stats.map(s => `
        <div class="stat-card ${s.cls}">
            <div class="stat-label">${s.label}</div>
            <div class="stat-value">${s.value}</div>
        </div>
    `).join("");
}

function batteryIconSvg(status) {
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

function moveIconSvg() {
    return `
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M2 5.5H12M12 5.5L9 2.5M12 5.5L9 8.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M14 10.5H4M4 10.5L7 7.5M4 10.5L7 13.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
    `;
}

function editIconSvg() {
    return `
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
            <path d="M11.5 2.5L13.5 4.5L5 13H3V11L11.5 2.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
        </svg>
    `;
}

function viewIconSvg() {
    return `
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
            <path d="M1 8C1 8 3.5 3 8 3C12.5 3 15 8 15 8C15 8 12.5 13 8 13C3.5 13 1 8 1 8Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
            <circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.3"/>
        </svg>
    `;
}

function deleteIconSvg() {
    return `
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
            <path d="M3 4.5H13M6 4.5V3a1 1 0 011-1h2a1 1 0 011 1v1.5M4.5 4.5L5 13a1 1 0 001 1h4a1 1 0 001-1l.5-8.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
    `;
}

function formatDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString([], { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

function renderTable(batteries) {
    const tbody = document.getElementById("battery-rows");
    tbody.innerHTML = "";

    batteries.forEach(battery => {
        const charge = (battery.charge_status || "unknown").toLowerCase();
        const statusClass = battery.status === "Deployed" ? "deployed" : "at-base";

        const menuItems = CHARGE_OPTIONS.map(c => `
            <div class="charge-option ${c === charge ? "active" : ""}" data-value="${c}">
                ${batteryIconSvg(c)}
                <span>${capitalize(c)}</span>
            </div>
        `).join("");

        const row = document.createElement("tr");
        row.innerHTML = `
            <td class="battery-number">${battery.battery_number}</td>
            <td>${battery.model || "-"}</td>
            <td><span class="status-pill ${statusClass}">${battery.status}</span></td>
            <td>
                <div class="charge-dropdown" data-id="${battery.id}">
                    <button type="button" class="charge-btn">
                        ${batteryIconSvg(charge)}
                        <span class="charge-label">${capitalize(charge)}</span>
                        <span class="charge-caret">▾</span>
                    </button>
                    <div class="charge-menu" hidden>${menuItems}</div>
                </div>
            </td>
            <td>${battery.current_location}</td>
            <td>${battery.moved_by || "—"}</td>
            <td>${formatDate(battery.moved_at)}</td>
            <td>
    <div class="actions-cell">
        ${can("movements", "manage") ? `
        <button type="button" class="move-btn" data-id="${battery.id}" data-number="${battery.battery_number}" title="Move battery">
            ${moveIconSvg()}
        </button>` : ""}
        ${can("batteries", "view") ? `
<button type="button" class="view-battery-btn" data-id="${battery.id}" title="View battery">
    ${viewIconSvg()}
</button>` : ""}
        ${can("batteries", "edit") ? `
        <button type="button" class="edit-battery-btn" data-id="${battery.id}" title="Edit battery">
            ${editIconSvg()}
        </button>` : ""}
        ${can("batteries", "delete") ? `
        <button type="button" class="delete-battery-btn" data-id="${battery.id}" data-number="${battery.battery_number}" title="Deactivate battery">
            ${deleteIconSvg()}
        </button>` : ""}
    </div>
</td>
        `;
        tbody.appendChild(row);
    });

    tbody.querySelectorAll(".charge-dropdown").forEach(dropdown => {
        const btn = dropdown.querySelector(".charge-btn");
        const menu = dropdown.querySelector(".charge-menu");

        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const isOpen = !menu.hidden;
            closeAllChargeMenus();
            menu.hidden = isOpen;
        });

        menu.querySelectorAll(".charge-option").forEach(opt => {
            opt.addEventListener("click", async (e) => {
                e.stopPropagation();
                const batteryId = dropdown.dataset.id;
                const value = opt.dataset.value;
                menu.hidden = true;

                await fetch(`/batteries/${batteryId}/charge-status`, {
                    method: "PATCH",
                    headers: authHeaders({ "Content-Type": "application/json" }),
                    body: JSON.stringify({ charge_status: value })
                });
                await refreshData();
            });
        });
    });

    tbody.querySelectorAll(".move-btn").forEach(btn => {
        btn.addEventListener("click", () => openMoveModal(btn.dataset.id, btn.dataset.number));
    });

    tbody.querySelectorAll(".delete-battery-btn").forEach(btn => {
        btn.addEventListener("click", () => deactivateBattery(btn.dataset.id, btn.dataset.number));
    });

    tbody.querySelectorAll(".view-battery-btn").forEach(btn => {
        btn.addEventListener("click", () => openViewBatteryModal(btn.dataset.id));
    });

    tbody.querySelectorAll(".edit-battery-btn").forEach(btn => {
        btn.addEventListener("click", () => openEditBatteryModal(btn.dataset.id));
    });
}

function closeAllChargeMenus() {
    document.querySelectorAll(".charge-menu").forEach(m => m.hidden = true);
}

document.addEventListener("click", closeAllChargeMenus);

// ---- Sites table (name + HB tag, contact info, edit/delete actions) ----
function renderSiteList(locations) {
    const tbody = document.getElementById("sites-rows");
    if (!tbody) return;

    const hasActions = can("sites", "edit") || can("sites", "delete");

    tbody.innerHTML = locations.map(loc => `
        <tr>
            <td>
                ${loc.name}
                ${loc.is_home_base ? `<span class="hb-tag">HB</span>` : ""}
            </td>
            <td>${loc.contact_name || "—"}</td>
            <td>${loc.contact_phone || "—"}</td>
            <td>${loc.address || "—"}</td>
            ${hasActions ? `
            <td>
                ${can("sites", "edit") ? `
                <button type="button" class="edit-site-btn" data-id="${loc.id}" title="Edit site">
                    ${editIconSvg()}
                </button>` : ""}
                ${can("sites", "delete") ? `
                <button type="button" class="delete-site-btn" data-id="${loc.id}" data-name="${loc.name}" title="Delete site">
                    ${deleteIconSvg()}
                </button>` : ""}
            </td>` : ""}
        </tr>
    `).join("");

    tbody.querySelectorAll(".edit-site-btn").forEach(btn => {
        btn.addEventListener("click", () => openEditSiteModal(btn.dataset.id));
    });

    tbody.querySelectorAll(".delete-site-btn").forEach(btn => {
        btn.addEventListener("click", () => deleteSite(btn.dataset.id, btn.dataset.name));
    });

}

async function deleteSite(id, name) {
    if (!confirm(`Delete site "${name}"? This can't be undone.`)) return;

    const response = await fetch(`/locations/${id}`, { method: "DELETE", headers: authHeaders() });
    if (response.ok) {
        await refreshData();
    } else {
        alert("Failed to delete site — it may still have battery movement history tied to it.");
    }
}

function populateMoveLocationSelect(locations) {
    const sel = document.getElementById("move-location");
    sel.innerHTML = '<option value="">Move to...</option>' +
        locations.map(l => `<option value="${l.id}">${l.name}${l.is_home_base ? " (Home Base)" : ""}</option>`).join("");
}

function showMessage(elementId, text, isError) {
    const el = document.getElementById(elementId);
    el.textContent = text;
    el.className = "form-msg " + (isError ? "error" : "success");
    setTimeout(() => { el.textContent = ""; el.className = "form-msg"; }, 3000);
}

// ---- Move modal ----
const moveOverlay = document.getElementById("move-overlay");
const moveForm = document.getElementById("move-form");
const moveBatteryLabel = document.getElementById("move-battery-label");

function openMoveModal(batteryId, batteryNumber) {
    moveModalBatteryId = batteryId;
    moveBatteryLabel.textContent = batteryNumber ? `— ${batteryNumber}` : "";
    moveForm.reset();
    moveOverlay.hidden = false;
}

function closeMoveModal() {
    moveOverlay.hidden = true;
    moveModalBatteryId = null;
}

document.getElementById("move-cancel").addEventListener("click", closeMoveModal);
moveOverlay.addEventListener("click", (e) => {
    if (e.target === moveOverlay) closeMoveModal();
});

moveForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!moveModalBatteryId) return;

    const to_location_id = parseInt(document.getElementById("move-location").value);
    const reason = document.getElementById("move-reason").value || null;
    const moved_by = document.getElementById("move-by").value || null;

    const response = await fetch("/movements", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ battery_id: parseInt(moveModalBatteryId), to_location_id, reason, moved_by })
    });

    if (response.ok) {
        closeMoveModal();
        await refreshData();
    } else {
        alert("Failed to record move");
    }
});

let viewMovementsCache = [];
let viewLogsPage = 1;
let viewLogsPageSize = 10;

async function openViewBatteryModal(id) {
    const overlay = document.getElementById("view-battery-overlay");
    const label = document.getElementById("view-battery-label");
    overlay.hidden = false;

    document.querySelectorAll(".tab-slant").forEach(t => t.classList.remove("active"));
    document.querySelector('.tab-slant[data-tab="details"]').classList.add("active");
    document.getElementById("view-tab-details").hidden = false;
    document.getElementById("view-tab-logs").hidden = true;

    const res = await fetch(`/batteries/${id}`, { headers: authHeaders() });
    const battery = await res.json();

    label.textContent = battery.battery_number;
    document.getElementById("detail-serial").textContent = battery.serial_number || "—";
    document.getElementById("detail-model").textContent = battery.model || "—";
    document.getElementById("detail-capacity").textContent = battery.capacity || "—";
    document.getElementById("detail-status").textContent = battery.status;
    document.getElementById("detail-charge").textContent = capitalize(battery.charge_status || "unknown");
    document.getElementById("detail-location").textContent = battery.current_location;

    const logsRes = await fetch(`/batteries/${id}/movements`, { headers: authHeaders() });
    viewMovementsCache = await logsRes.json();
    viewLogsPage = 1;
    renderLogsTable();
}

function renderLogsTable() {
    const logsList = document.getElementById("view-logs-list");
    const pagination = document.getElementById("logs-pagination");
    const total = viewMovementsCache.length;

    if (total === 0) {
        logsList.innerHTML = `<div class="log-empty">No movement history yet.</div>`;
        pagination.innerHTML = "";
        return;
    }

    const totalPages = Math.max(1, Math.ceil(total / viewLogsPageSize));
    if (viewLogsPage > totalPages) viewLogsPage = totalPages;
    const start = (viewLogsPage - 1) * viewLogsPageSize;
    const pageItems = viewMovementsCache.slice(start, start + viewLogsPageSize);

    logsList.innerHTML = `
        <table class="logs-table">
            <colgroup>
                <col style="width:30%">
                <col style="width:23%">
                <col style="width:23%">
                <col style="width:24%">
            </colgroup>
            <tbody>
                ${pageItems.map(m => `
                    <tr>
                        <td>${formatDate(m.created_at)}</td>
                        <td>${m.from_location || "—"}</td>
                        <td>${m.to_location}</td>
                        <td>${m.moved_by || "—"}</td>
                    </tr>
                `).join("")}
            </tbody>
        </table>
    `;

    pagination.innerHTML = `
        <div class="logs-pagination-row">
            <div class="logs-page-size">
                <span>Show</span>
                <select id="logs-page-size-select">
                    ${[10, 20, 50, 100].map(n => `<option value="${n}" ${n === viewLogsPageSize ? "selected" : ""}>${n}</option>`).join("")}
                </select>
                <span>entries</span>
            </div>
            <div class="logs-page-nav">
                <button type="button" id="logs-prev-btn" ${viewLogsPage === 1 ? "disabled" : ""}>Prev</button>
                <span>Page ${viewLogsPage} of ${totalPages}</span>
                <button type="button" id="logs-next-btn" ${viewLogsPage === totalPages ? "disabled" : ""}>Next</button>
            </div>
        </div>
    `;

    document.getElementById("logs-page-size-select").addEventListener("change", (e) => {
        viewLogsPageSize = parseInt(e.target.value);
        viewLogsPage = 1;
        renderLogsTable();
    });
    document.getElementById("logs-prev-btn").addEventListener("click", () => {
        if (viewLogsPage > 1) { viewLogsPage--; renderLogsTable(); }
    });
    document.getElementById("logs-next-btn").addEventListener("click", () => {
        if (viewLogsPage < totalPages) { viewLogsPage++; renderLogsTable(); }
    });
}

document.querySelectorAll(".tab-slant").forEach(tab => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".tab-slant").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        const target = tab.dataset.tab;
        document.getElementById("view-tab-details").hidden = target !== "details";
        document.getElementById("view-tab-logs").hidden = target !== "logs";
    });
});

document.getElementById("view-battery-close").addEventListener("click", () => {
    document.getElementById("view-battery-overlay").hidden = true;
});

document.getElementById("view-battery-overlay").addEventListener("click", (e) => {
    if (e.target.id === "view-battery-overlay") {
        document.getElementById("view-battery-overlay").hidden = true;
    }
});

// ---- Add Site modal ----
const addSiteOverlay = document.getElementById("add-site-overlay");
const addSiteOpenBtn = document.getElementById("add-site-open-btn");
const addSiteCancelBtn = document.getElementById("add-site-cancel");

addSiteOpenBtn.addEventListener("click", () => {
    document.getElementById("location-form").reset();
    addSiteOverlay.hidden = false;
});

addSiteCancelBtn.addEventListener("click", () => {
    addSiteOverlay.hidden = true;
});

addSiteOverlay.addEventListener("click", (e) => {
    if (e.target === addSiteOverlay) addSiteOverlay.hidden = true;
});

document.getElementById("location-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("location-name").value;
    const contact_name = document.getElementById("location-contact-name").value || null;
    const contact_phone = document.getElementById("location-contact-phone").value || null;
    const address = document.getElementById("location-address").value || null;
    const is_home_base = document.getElementById("location-home-base").checked;

    const response = await fetch("/locations", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ name, contact_name, contact_phone, address, is_home_base })
    });

    if (response.ok) {
        showMessage("location-msg", "Site added", false);
        e.target.reset();
        addSiteOverlay.hidden = true;
        await refreshData();
    } else {
        showMessage("location-msg", "Failed to add site", true);
    }
});

// ---- Edit Site modal ----
const editSiteOverlay = document.getElementById("edit-site-overlay");
const editSiteCancelBtn = document.getElementById("edit-site-cancel");

function openEditSiteModal(locationId) {
    const loc = locationsCache.find(l => String(l.id) === String(locationId));
    if (!loc) return;

    editSiteId = loc.id;
    document.getElementById("edit-site-name").value = loc.name || "";
    document.getElementById("edit-site-contact-name").value = loc.contact_name || "";
    document.getElementById("edit-site-contact-phone").value = loc.contact_phone || "";
    document.getElementById("edit-site-address").value = loc.address || "";
    document.getElementById("edit-site-home-base").checked = !!loc.is_home_base;
    editSiteOverlay.hidden = false;
}

function closeEditSiteModal() {
    editSiteOverlay.hidden = true;
    editSiteId = null;
}

editSiteCancelBtn.addEventListener("click", closeEditSiteModal);
editSiteOverlay.addEventListener("click", (e) => {
    if (e.target === editSiteOverlay) closeEditSiteModal();
});

document.getElementById("edit-site-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!editSiteId) return;

    const body = {
        name: document.getElementById("edit-site-name").value,
        contact_name: document.getElementById("edit-site-contact-name").value || null,
        contact_phone: document.getElementById("edit-site-contact-phone").value || null,
        address: document.getElementById("edit-site-address").value || null,
        is_home_base: document.getElementById("edit-site-home-base").checked
    };

    const response = await fetch(`/locations/${editSiteId}`, {
        method: "PATCH",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body)
    });

    if (response.ok) {
        closeEditSiteModal();
        await refreshData();
    } else {
        alert("Failed to update site");
    }
});

// ---- Edit Battery modal ----
const editBatteryOverlay = document.getElementById("edit-battery-overlay");
const editBatteryCancelBtn = document.getElementById("edit-battery-cancel");
let editBatteryId = null;

async function openEditBatteryModal(id) {
    const res = await fetch(`/batteries/${id}`, { headers: authHeaders() });
    if (!res.ok) {
        alert("Failed to load battery");
        return;
    }
    const battery = await res.json();

    editBatteryId = battery.id;
    document.getElementById("edit-battery-number").value = battery.battery_number || "";
    document.getElementById("edit-battery-serial").value = battery.serial_number || "";
    document.getElementById("edit-battery-model").value = battery.model || "";
    document.getElementById("edit-battery-capacity").value = battery.capacity || "";
    editBatteryOverlay.hidden = false;
}

function closeEditBatteryModal() {
    editBatteryOverlay.hidden = true;
    editBatteryId = null;
}

editBatteryCancelBtn.addEventListener("click", closeEditBatteryModal);
editBatteryOverlay.addEventListener("click", (e) => {
    if (e.target === editBatteryOverlay) closeEditBatteryModal();
});

document.getElementById("edit-battery-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!editBatteryId) return;

    const body = {
        battery_number: document.getElementById("edit-battery-number").value,
        serial_number: document.getElementById("edit-battery-serial").value || null,
        model: document.getElementById("edit-battery-model").value || null,
        capacity: document.getElementById("edit-battery-capacity").value || null
    };

    const response = await fetch(`/batteries/${editBatteryId}`, {
        method: "PATCH",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body)
    });

    if (response.ok) {
        closeEditBatteryModal();
        await refreshData();
    } else {
        const err = await response.json().catch(() => ({}));
        showMessage("edit-battery-msg", err.detail || "Failed to update battery", true);
    }
});

// ---- Add Battery modal ----
const addBatteryOverlay = document.getElementById("add-battery-overlay");
const addBatteryOpenBtn = document.getElementById("add-battery-open-btn");
const addBatteryCancelBtn = document.getElementById("add-battery-cancel");

addBatteryOpenBtn.addEventListener("click", () => {
    document.getElementById("battery-form").reset();
    addBatteryOverlay.hidden = false;
});

addBatteryCancelBtn.addEventListener("click", () => {
    addBatteryOverlay.hidden = true;
});

addBatteryOverlay.addEventListener("click", (e) => {
    if (e.target === addBatteryOverlay) addBatteryOverlay.hidden = true;
});

document.getElementById("battery-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const battery_number = document.getElementById("battery-number").value;
    const serial_number = document.getElementById("battery-serial").value || null;
    const model = document.getElementById("battery-model").value || null;
    const capacity = document.getElementById("battery-capacity").value || null;

    const response = await fetch("/batteries", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ battery_number, serial_number, model, capacity })
    });

    if (response.ok) {
        showMessage("battery-msg", "Battery added", false);
        e.target.reset();
        addBatteryOverlay.hidden = true;
        await refreshData();
    } else {
        showMessage("battery-msg", "Failed to add battery (number may already exist)", true);
    }
});

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

// ---- Command palette (Ctrl+K / Cmd+K) ----
const cmdkOverlay = document.getElementById("cmdk-overlay");
const cmdkInput = document.getElementById("cmdk-input");
const cmdkResults = document.getElementById("cmdk-results");
let cmdkSelectedIndex = 0;
let cmdkCurrentItems = [];

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
    const items = [];

    if (can("batteries", "view")) {
        batteriesCache.forEach(b => {
            items.push({
                type: "battery",
                label: b.battery_number,
                sublabel: [b.model, b.current_location].filter(Boolean).join(" — "),
                action: () => {
                    document.querySelector('[data-view="dashboard"]').click();
                    openViewBatteryModal(b.id);
                }
            });
        });
    }

    if (can("sites", "view")) {
        locationsCache.forEach(l => {
            items.push({
                type: "site",
                label: l.name,
                sublabel: l.address || (l.is_home_base ? "Home base" : ""),
                action: () => {
                    document.querySelector('[data-view="sites"]').click();
                    if (can("sites", "edit")) openEditSiteModal(l.id);
                }
            });
        });
    }

    if (can("users", "view")) {
        usersCache.filter(u => u.status !== "inactive").forEach(u => {
            items.push({
                type: "user",
                label: u.name,
                sublabel: u.email,
                action: () => {
                    document.querySelector('[data-view="users"]').click();
                    if (can("users", "edit")) openUserForm(u.id);
                }
            });
        });
    }

    if (can("roles", "view")) {
        rolesCache.forEach(r => {
            items.push({
                type: "role",
                label: r.name,
                sublabel: "Role",
                action: () => {
                    document.querySelector('[data-view="roles"]').click();
                    if (can("roles", "edit")) openRoleForm(r.id);
                }
            });
        });
    }

    return items;
}

async function openCmdk() {
    cmdkOverlay.hidden = false;
    cmdkInput.value = "";
    cmdkSelectedIndex = 0;
    cmdkResults.innerHTML = `<div class="cmdk-empty">Loading...</div>`;
    cmdkInput.focus();

    // Load caches for sections the user hasn't visited yet, so search covers everything
    const loads = [];
    if (can("users", "view") && usersCache.length === 0) loads.push(loadUsers());
    if (can("roles", "view") && rolesCache.length === 0) loads.push(loadRoles());
    if (loads.length) await Promise.all(loads);

    renderCmdkResults("");
}

function closeCmdk() {
    cmdkOverlay.hidden = true;
}

function cmdkTypeLabel(type) {
    return { section: "Go to", battery: "Battery", site: "Site", user: "User", role: "Role" }[type] || "";
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

document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        openCmdk();
    } else if (e.key === "Escape") {
        closeCmdk();
        closeMoveModal();
        closeAllChargeMenus();
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

if (authToken) {
    showApp();
} else {
    document.getElementById("login-screen").hidden = false;
}

async function deactivateBattery(id, number) {
    if (!confirm(`Deactivate battery "${number}"? It will be hidden from the list.`)) return;

    const response = await fetch(`/batteries/${id}`, { method: "DELETE", headers: authHeaders() });
    if (response.ok) {
        await refreshData();
    } else {
        alert("Failed to deactivate battery");
    }
}

// ============================================================
// Movements & Check Sites (header sub-links)
// ============================================================

function initHeaderLinkIcons() {
    document.getElementById("movements-link-icon").innerHTML = moveIconSvg();
    document.getElementById("check-sites-link-icon").innerHTML = `
        <svg viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M8 1.5L14 4V8C14 11.5 11.5 13.8 8 14.5C4.5 13.8 2 11.5 2 8V4L8 1.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
            <path d="M5.5 8L7.2 9.7L10.5 6.2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
    `;
}

async function refreshBadges() {
    const [movRes, siteRes, notifRes] = await Promise.all([
        fetch("/movements/overdue-count", { headers: authHeaders() }),
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

function showView(viewId) {
    document.querySelectorAll(".view").forEach(v => v.hidden = true);
    document.getElementById(viewId).hidden = false;
}

// ---- Movements list ----
let movementsCache = [];
let showMovementHistory = false;

const MOVEMENT_STATUS_META = {
    pending: { label: "Pending", cls: "pending" },
    in_transit: { label: "In Transit", cls: "in-transit" },
    arrived: { label: "Arrived", cls: "arrived" },
    completed: { label: "Completed", cls: "confirmed" },
    site_confirmed_online: { label: "Site Confirmed Online", cls: "confirmed" },
    site_still_down: { label: "Site Still Down", cls: "down" },
    cancelled: { label: "Cancelled", cls: "cancelled" },
};

const MOVEMENT_REASON_LABELS = {
    site_down: "Site down",
    storage: "Storage",
};

document.getElementById("movements-link-btn").addEventListener("click", () => {
    showView("view-movements");
    loadMovements();
});

document.getElementById("movements-show-history").addEventListener("change", (e) => {
    showMovementHistory = e.target.checked;
    loadMovements();
});

async function loadMovements() {
    document.getElementById("movements-rows").innerHTML = '<tr><td colspan="6" class="loading-text">Loading movements...</td></tr>';
    const res = await fetch(`/movements${showMovementHistory ? "?history=true" : ""}`, { headers: authHeaders() });
    if (!res.ok) {
        document.getElementById("movements-rows").innerHTML = '<tr><td colspan="6" class="loading-text">Failed to load movements</td></tr>';
        return;
    }
    movementsCache = await res.json();
    renderMovementsList(movementsCache);
}

function renderMovementsList(movements) {
    const tbody = document.getElementById("movements-rows");

    if (movements.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="loading-text">No movements${showMovementHistory ? "" : " in progress"}.</td></tr>`;
        return;
    }

    const canMove = can("movements", "manage");

    tbody.innerHTML = movements.map(m => {
        const meta = MOVEMENT_STATUS_META[m.status] || { label: capitalize(m.status), cls: "" };
        return `
            <tr>
                <td class="battery-number">${m.battery_number}</td>
                <td>${m.from_location || "—"} &rarr; ${m.to_location}</td>
                <td>${MOVEMENT_REASON_LABELS[m.reason] || "—"}</td>
                <td><span class="status-pill movement-${meta.cls}">${meta.label}</span></td>
                <td>${formatDate(m.created_at)}</td>
                <td>${canMove ? renderMovementActions(m) : "—"}</td>
            </tr>
        `;
    }).join("");

    attachMovementActionListeners();
}

function renderMovementActions(m) {
    switch (m.status) {
        case "pending":
            return `
                <div class="actions-cell">
                    <button type="button" class="movement-action-btn" data-action="mark-in-transit" data-id="${m.id}">Mark In Transit</button>
                    <button type="button" class="movement-cancel-btn" data-id="${m.id}" title="Cancel movement">${deleteIconSvg()}</button>
                </div>
            `;
        case "in_transit":
            return `
                <div class="actions-cell">
                    <button type="button" class="movement-action-btn" data-action="mark-arrived" data-id="${m.id}">Mark Arrived</button>
                    <button type="button" class="movement-cancel-btn" data-id="${m.id}" title="Cancel movement">${deleteIconSvg()}</button>
                </div>
            `;
        case "arrived":
            return `
                <div class="actions-cell">
                    <button type="button" class="movement-site-check-btn" data-answer="true" data-id="${m.id}">Site Online</button>
                    <button type="button" class="movement-site-check-btn" data-answer="false" data-id="${m.id}">Still Down</button>
                </div>
            `;
        default:
            return "—";
    }
}

function attachMovementActionListeners() {
    document.querySelectorAll(".movement-action-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
            const res = await fetch(`/movements/${btn.dataset.id}/${btn.dataset.action}`, {
                method: "POST",
                headers: authHeaders()
            });
            if (res.ok) {
                await loadMovements();
                await refreshBadges();
            } else {
                alert("Failed to update movement");
            }
        });
    });

    document.querySelectorAll(".movement-cancel-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
            if (!confirm("Cancel this movement?")) return;
            const res = await fetch(`/movements/${btn.dataset.id}/cancel`, {
                method: "POST",
                headers: authHeaders()
            });
            if (res.ok) {
                await loadMovements();
                await refreshBadges();
            } else {
                alert("Failed to cancel movement");
            }
        });
    });

    document.querySelectorAll(".movement-site-check-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
            const is_online = btn.dataset.answer === "true";
                    const res = await fetch(`/movements/${btn.dataset.id}/confirm-online`, {
                method: "POST",
                headers: authHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify({ is_online })
            });
            if (res.ok) {
                await loadMovements();
                await refreshBadges();
            } else {
                alert("Failed to record site check");
            }
        });
    });
}

// ---- Check Sites list ----
let checkSitesCache = [];

document.getElementById("check-sites-link-btn").addEventListener("click", () => {
    showView("view-check-sites");
    loadCheckSites();
});

async function loadCheckSites() {
    document.getElementById("check-sites-rows").innerHTML = '<tr><td colspan="5" class="loading-text">Loading sites...</td></tr>';
    const res = await fetch("/locations/verification", { headers: authHeaders() });
    if (!res.ok) {
        document.getElementById("check-sites-rows").innerHTML = '<tr><td colspan="5" class="loading-text">Failed to load sites</td></tr>';
        return;
    }
    checkSitesCache = await res.json();
    renderCheckSitesList(checkSitesCache);
}

function buildCheckSitesStats(sites) {
    const online = sites.filter(s => s.is_online).length;
    const offline = sites.filter(s => !s.is_online).length;
    const needsCheck = sites.filter(s => s.needs_check).length;

    return [
        { label: "Online", value: online, cls: "charged" },
        { label: "Offline", value: offline, cls: "low" },
        { label: "Needs Check", value: needsCheck, cls: "deployed" },
    ];
}

function renderCheckSitesStats(sites) {
    const grid = document.getElementById("check-sites-stat-grid");
    grid.innerHTML = buildCheckSitesStats(sites).map(s => `
        <div class="stat-card ${s.cls}">
            <div class="stat-label">${s.label}</div>
            <div class="stat-value">${s.value}</div>
        </div>
    `).join("");
}

function renderCheckSitesList(sites) {
    const tbody = document.getElementById("check-sites-rows");

    renderCheckSitesStats(sites);

    if (sites.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="loading-text">No sites found.</td></tr>`;
        return;
    }

    tbody.innerHTML = sites.map(s => `
        <tr class="${s.is_online ? "" : "site-row-offline"}">
            <td>${s.name}</td>
            <td><span class="status-pill ${s.is_online ? "site-online" : "site-offline"}">${s.is_online ? "Online" : "Offline"}</span></td>
            <td>${s.verification_confirmed_at ? formatDate(s.verification_confirmed_at) : "Never checked"}</td>
            <td><span class="hour-check-pill ${s.needs_check ? "pending" : "done"}">${s.needs_check ? "Needs check" : "Checked"}</span></td>
            <td>
                <div class="actions-cell">
                    <button type="button" class="site-check-btn" data-answer="true" data-id="${s.id}">Online</button>
                    <button type="button" class="site-check-btn" data-answer="false" data-id="${s.id}">Offline</button>
                </div>
            </td>
        </tr>
    `).join("");

    tbody.querySelectorAll(".site-check-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
            const is_online = btn.dataset.answer === "true";
            const res = await fetch(`/locations/${btn.dataset.id}/confirm`, {
                method: "POST",
                headers: authHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify({ is_online })
            });
            if (res.ok) {
                await loadCheckSites();
                await refreshBadges();
            } else {
                alert("Failed to update site status");
            }
        });
    });
}