import {
    can, authHeaders, showMessage, capitalize, editIconSvg, deleteIconSvg,
    showView, navigate, registerRoute, registerCmdkProvider,
} from "./common.js";

let usersCache = [];
let editUserId = null;

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
        btn.addEventListener("click", () => navigate("users/" + btn.dataset.id + "/edit"));
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
        if (usersCache.length === 0) await loadUsers();
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
}

export function initUsers() {
    document.getElementById("add-user-open-btn").addEventListener("click", () => navigate("users/new"));

    document.getElementById("user-form-cancel").addEventListener("click", () => {
        navigate("users");
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
            navigate("users");
            await loadUsers();
        } else {
            const err = await response.json().catch(() => ({}));
            showMessage("user-form-msg", err.detail || "Failed to save user", true);
        }
    });

    registerCmdkProvider({
        ensureLoaded: async () => {
            if (can("users", "view") && usersCache.length === 0) await loadUsers();
        },
        getItems: () => {
            const actions = can("users", "add") ? [{
                type: "action",
                label: "Add User",
                action: () => navigate("users/new"),
            }] : [];
            const users = can("users", "view") ? usersCache.filter(u => u.status !== "inactive").map(u => ({
                type: "user",
                label: u.name,
                sublabel: u.email,
                action: () => navigate(can("users", "edit") ? `users/${u.id}/edit` : "users"),
            })) : [];
            return [...actions, ...users];
        },
    });

    registerRoute("users", async (params) => {
        if (params[0] === "new") {
            await openUserForm(null);
            showView("view-user-form");
        } else if (params[0] && params[1] === "edit") {
            await openUserForm(params[0]);
            showView("view-user-form");
        } else {
            showView("view-users");
            loadUsers();
        }
    });
}
