import { authHeaders, showMessage, getCurrentUser, updateCurrentUser, showView, registerRoute } from "./common.js";

function populateProfileForm() {
    const currentUser = getCurrentUser();
    if (!currentUser) return;
    const parts = (currentUser.name || "").trim().split(/\s+/);
    document.getElementById("profile-first-name").value = parts[0] || "";
    document.getElementById("profile-last-name").value = parts.slice(1).join(" ") || "";
    document.getElementById("profile-phone").value = currentUser.phone || "";
    document.getElementById("profile-email").value = currentUser.email || "";
}

async function populateNotificationsForm() {
    const res = await fetch("/monitoring/alert-subscription", { headers: authHeaders() });
    if (!res.ok) return;
    const sub = await res.json();
    document.getElementById("notif-in-app").checked = sub.in_app_enabled;
    document.getElementById("notif-sms").checked = sub.sms_enabled;
    document.getElementById("notif-whatsapp").checked = sub.whatsapp_enabled;
}

export function initSettings() {
    registerRoute("settings", (params) => {
        populateProfileForm();
        showView("view-settings");
        if (params[0]) document.querySelector(`.settings-tab[data-settings-tab="${params[0]}"]`)?.click();
    });

    document.querySelectorAll(".settings-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".settings-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            const target = tab.dataset.settingsTab;
            document.getElementById("settings-tab-profile").hidden = target !== "profile";
            document.getElementById("settings-tab-password").hidden = target !== "password";
            document.getElementById("settings-tab-notifications").hidden = target !== "notifications";
            if (target === "notifications") populateNotificationsForm();
        });
    });

    // Mirrors the login screen's password show/hide toggle (initShell in
    // common.js) — same .password-field/.eye-open/.eye-closed markup and
    // behavior, just wired here since these three fields live in this view.
    document.querySelectorAll(".password-toggle-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const input = document.getElementById(btn.dataset.target);
            const isPassword = input.type === "password";
            input.type = isPassword ? "text" : "password";
            btn.querySelector(".eye-open").hidden = isPassword;
            btn.querySelector(".eye-closed").hidden = !isPassword;
            btn.classList.toggle("is-visible", isPassword);
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
            updateCurrentUser({ name, email, phone });
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

    document.getElementById("notifications-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const body = {
            in_app_enabled: document.getElementById("notif-in-app").checked,
            sms_enabled: document.getElementById("notif-sms").checked,
            whatsapp_enabled: document.getElementById("notif-whatsapp").checked,
        };
        const response = await fetch("/monitoring/alert-subscription", {
            method: "PUT",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body),
        });
        if (response.ok) {
            showMessage("notifications-form-msg", "Notification preferences saved", false);
        } else {
            showMessage("notifications-form-msg", "Failed to save preferences", true);
        }
    });
}
