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

export function initSettings() {
    registerRoute("settings", () => {
        populateProfileForm();
        showView("view-settings");
    });

    document.querySelectorAll(".settings-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".settings-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            const target = tab.dataset.settingsTab;
            document.getElementById("settings-tab-profile").hidden = target !== "profile";
            document.getElementById("settings-tab-password").hidden = target !== "password";
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
}
