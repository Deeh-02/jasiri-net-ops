import {
    can, authHeaders, showMessage, formatDate, capitalize, editIconSvg, deleteIconSvg,
    showView, navigate, registerRoute, registerCmdkProvider,
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
        btn.addEventListener("click", () => navigate("roles/" + btn.dataset.id + "/edit"));
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
        key: "batteries", label: "Batteries",
        actions: [
            "add", "edit", "delete",
            // Flat checkbox alongside the others — initiating a move (the
            // move icon/modal on the battery table) is a distinct
            // capability from managing movements already in progress
            // (see the nested Movements section below). Maps to
            // movements:create, which routers/batteries.py's create_movement
            // checks specifically for this.
            { label: "Move Battery", section: "movements", action: "create" },
        ],
        children: [
            // The Movements tracking page (pending/in-transit/etc.) — its
            // own toggle gates seeing the page at all (movements:view); the
            // "Manage Movement" checkbox inside gates acting on movements
            // already in progress there (mark in-transit/arrived, cancel,
            // confirm site online — movements:manage, checked by
            // movements.js and those specific router endpoints). Separate
            // from "Move Battery" above, which only covers starting a new
            // move.
            { key: "movements", label: "Movements", actions: [{ label: "Manage Movement", action: "manage" }] }
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
    // A plain master toggle, same shape as Users/Roles above — no flat
    // actions or children, since "view" is the only capability the Reports
    // page needs (every panel on it, SKU Summary/Cable Summary/Offcuts, is
    // read-only). Deliberately its own section, independent of
    // inventory_items:view, so a role can see item stock without seeing
    // rollup reports, or the reverse — mirrors ROUTE_PERMISSION_MAP's
    // "inventory-reports" entry and every report endpoint's own check in
    // routers/inventory.py (both now read reports:view, not
    // inventory_items:view).
    { key: "reports", label: "Reports", actions: [] },
    {
        // "Inventory" is the master switch for the whole domain — its own
        // toggle reads/writes inventory_items:view, which now gates only the
        // domain as a whole (the entire Inventory nav group hides when this
        // is off, see NAV_GROUP_MASTER_PERMISSION in common.js) rather than
        // literally being the same permission any specific sub-view checks.
        // No flat actions of its own any more — every capability below is a
        // nested, independently toggleable/checkable child; each is fully
        // hidden/unchecked — cascading through every checkbox and toggle
        // beneath it — the moment this master toggle goes off (see
        // renderPermGrid's change handler, unchanged from before this
        // reorganization).
        key: "inventory_items", label: "Inventory",
        actions: [],
        children: [
            // Real toggle now (not noToggle) — view_items is its own
            // backend-enforced permission (routers/inventory.py, and
            // common.js's ROUTE_PERMISSION_MAP for the "inventory" route),
            // independent of the master's inventory_items:view, so a role
            // can have Stock access without Items access or vice versa. `id`
            // keeps its menu-visibility wiring from colliding with the
            // master's own (both key off "inventory_items").
            // "Add item" here covers both creating a brand-new product/SKU
            // (POST /inventory/items) and adding stock to one that already
            // exists (POST /inventory/units[/batch]) — collapsed into one
            // permission (previously split as "Add Stock", its own toggle)
            // since the two-permission version was more granularity than
            // this app actually wants.
            {
                id: "inventory_items_view_items", key: "inventory_items", label: "Inventory Items",
                toggleAction: "view_items", actions: ["add", "edit", "delete"],
            },
            // Stock's own view permission (view_stock) gates the "stock"
            // route the same way view_items gates "inventory" above. The
            // four checkboxes nested under it are each their own
            // backend-enforced permission already (edit_reorder_level,
            // issue, return, view_history) — moved here from directly under
            // the master, unchanged in what they write, just regrouped so a
            // role can be denied Stock access outright without having to
            // deny each of the four individually.
            {
                id: "inventory_items_view_stock", key: "inventory_items", label: "Stock",
                toggleAction: "view_stock",
                actions: [
                    { label: "Edit Reorder Level", action: "edit_reorder_level" },
                    { label: "Issue Materials", section: "inventory_transactions", action: "issue" },
                    { label: "Return Materials", section: "inventory_transactions", action: "return" },
                    { label: "View Stock History", action: "view_history" },
                ],
            },
            { key: "inventory_categories", label: "Inventory Categories", actions: ["add", "edit", "delete"] },
            // Inventory Locations has no permission section of its own any
            // more — Issue/Return Materials' free-typed Site field creates a
            // location implicitly, gated the same as issuing/returning
            // itself, not as a separate grantable capability.
            // "add" covers logging In/Transfer/Adjustment/Return/Write-off
            // through the generic Log Transaction form specifically (Issue
            // and Return Materials are their own dedicated permissions
            // above, not this "add"); "Reconcile Cut" is separate and gated
            // Manager-level per the phase plan, since it closes out a job.
            { key: "inventory_transactions", label: "Inventory Log", actions: ["add", { label: "Reconcile Cut", action: "reconcile" }] },
        ]
    },
];

const ACTION_NOUN = {
    batteries: "battery", movements: "movement", sites: "site", site_checks: "site check",
    users: "user", roles: "role", inventory_items: "item",
    inventory_categories: "category",
    inventory_transactions: "transaction",
};

function renderPermGrid(permissions) {
    const allowedSet = new Set(
        (permissions || []).filter(p => p.allowed).map(p => `${p.section}:${p.action}`)
    );

    const grid = document.getElementById("perm-grid");

    function renderActionCheckboxes(sectionKey, actions, noun) {
        return actions.map(item => {
            // A plain string writes its own section's permission with the
            // generic "Capitalized action + noun" label (the common case).
            // An object can override the label and/or remap to a different
            // section — see "Move Battery" (rendered under Batteries, but
            // writes to the movements section).
            const isMapped = typeof item === "object";
            const dataSection = isMapped && item.section ? item.section : sectionKey;
            const dataAction = isMapped ? item.action : item;
            const actionLabel = isMapped ? item.label : capitalize(dataAction) + " " + noun;
            const checked = allowedSet.has(`${dataSection}:${dataAction}`);
            return `
                <label class="perm-checkbox-row">
                    <input type="checkbox" class="perm-action-checkbox" data-section="${dataSection}" data-action="${dataAction}" ${checked ? "checked" : ""}>
                    ${actionLabel}
                </label>
            `;
        }).join("");
    }

    // A child normally toggles its own inventory_items:view-shaped
    // permission and reveals nested actions — but two variants exist here:
    // toggleAction lets the switch itself target a different action (e.g.
    // "Inventory Items" toggles inventory_items:view_items, "Stock" toggles
    // inventory_items:view_stock, neither is :view), and noToggle (currently
    // unused, but still supported for a future child with no independent
    // permission of its own) drops the switch entirely, leaving the master
    // above it as the only control. id lets a child's own DOM
    // menu-visibility wiring stay unique even when its key duplicates
    // another section's — "Inventory Items", "Stock" and the master toggle
    // above all key off "inventory_items" but must not fight over the same
    // [data-section-menu] element.
    function renderChild(child) {
        const noun = ACTION_NOUN[child.key];
        if (child.noToggle) {
            return `
                <div class="perm-subsection" style="margin-left:20px;margin-top:10px;">
                    <div class="perm-section-header perm-subsection-header">
                        <span class="perm-section-name">${child.label}</span>
                    </div>
                    <div class="perm-menu">
                        <div class="perm-checkbox-grid">${renderActionCheckboxes(child.key, child.actions, noun)}</div>
                    </div>
                </div>
            `;
        }
        const toggleAction = child.toggleAction || "view";
        const menuId = child.id || child.key;
        const viewChecked = allowedSet.has(`${child.key}:${toggleAction}`);
        return `
            <div class="perm-subsection" style="margin-left:20px;margin-top:10px;">
                <div class="perm-section-header perm-subsection-header">
                    <span class="perm-section-name">${child.label}</span>
                    <label class="perm-toggle">
                        <input type="checkbox" class="perm-view-toggle" data-section="${child.key}" data-action="${toggleAction}" data-menu="${menuId}" ${viewChecked ? "checked" : ""}>
                        <span class="perm-toggle-track"></span>
                        <span class="perm-toggle-thumb"></span>
                    </label>
                </div>
                <div class="perm-menu" data-section-menu="${menuId}" ${viewChecked ? "" : "hidden"}>
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
                        <input type="checkbox" class="perm-view-toggle" data-section="${section.key}" data-action="view" data-menu="${section.key}" ${viewChecked ? "checked" : ""}>
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
            const menuId = toggle.dataset.menu || toggle.dataset.section;
            const menu = grid.querySelector(`[data-section-menu="${menuId}"]`);
            if (!menu) return;
            menu.hidden = !toggle.checked;
            if (!toggle.checked) {
                menu.querySelectorAll(".perm-action-checkbox").forEach(cb => cb.checked = false);
                menu.querySelectorAll(".perm-view-toggle").forEach(childToggle => {
                    childToggle.checked = false;
                    const childMenuId = childToggle.dataset.menu || childToggle.dataset.section;
                    const childMenu = grid.querySelector(`[data-section-menu="${childMenuId}"]`);
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
        if (rolesCache.length === 0) await loadRoles();
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
}

export function initRoles() {
    document.getElementById("add-role-open-btn").addEventListener("click", () => navigate("roles/new"));

    document.getElementById("role-form-cancel").addEventListener("click", () => {
        navigate("roles");
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
            // Most toggles still gate :view (hardcoding it was harmless
            // before), but "Inventory Items" and "Stock" target view_items/
            // view_stock instead — reading dataset.action
            // (set for every toggle at render time, see renderPermGrid)
            // instead of assuming "view" is what actually saves that
            // distinction, rather than silently writing every toggle here
            // to :view regardless of what it displayed as controlling.
            ...Array.from(document.querySelectorAll(".perm-view-toggle")).map(t => ({
                section: t.dataset.section,
                action: t.dataset.action || "view",
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
            navigate("roles");
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
            action: () => navigate(can("roles", "edit") ? `roles/${r.id}/edit` : "roles"),
        })) : [],
    });

    registerRoute("roles", async (params) => {
        if (params[0] === "new") {
            await openRoleForm(null);
            showView("view-role-form");
        } else if (params[0] && params[1] === "edit") {
            await openRoleForm(params[0]);
            showView("view-role-form");
        } else {
            showView("view-roles");
            loadRoles();
        }
    });
}
