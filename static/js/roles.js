import {
    can, authHeaders, showMessage, formatDate, capitalize, editIconSvg, deleteIconSvg,
    showView, registerCmdkProvider,
} from "./common.js";

let rolesCache = [];
let editRoleId = null;

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
    showView("view-role-form");
}

export function initRoles() {
    document.querySelector('[data-view="roles"]').addEventListener("click", loadRoles);

    document.getElementById("add-role-open-btn").addEventListener("click", () => openRoleForm(null));

    document.getElementById("role-form-cancel").addEventListener("click", () => {
        showView("view-roles");
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
        ];

        const permRes = await fetch(`/roles/${roleId}/permissions`, {
            method: "PUT",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ permissions })
        });

        if (permRes.ok) {
            showMessage("role-form-msg", editRoleId ? "Role updated" : "Role added", false);
            showView("view-roles");
            await loadRoles();
        } else {
            showMessage("role-form-msg", "Role saved but permissions failed to save", true);
        }
    });

    registerCmdkProvider({
        ensureLoaded: async () => {
            if (can("roles", "view") && rolesCache.length === 0) await loadRoles();
        },
        getItems: () => can("roles", "view") ? rolesCache.map(r => ({
            type: "role",
            label: r.name,
            sublabel: "Role",
            action: () => {
                document.querySelector('[data-view="roles"]').click();
                if (can("roles", "edit")) openRoleForm(r.id);
            }
        })) : [],
    });
}
