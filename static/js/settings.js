import { authHeaders, showMessage, getCurrentUser, updateCurrentUser, replaceAuthToken, showView, registerRoute } from "./common.js";
import { currentTheme, setTheme, motionOn, setMotion } from "./theme.js";

function fillProfileForm(currentUser) {
    const parts = (currentUser.name || "").trim().split(/\s+/);
    document.getElementById("profile-first-name").value = parts[0] || "";
    document.getElementById("profile-last-name").value = parts.slice(1).join(" ") || "";
    document.getElementById("profile-phone").value = currentUser.phone || "";
    document.getElementById("profile-email").value = currentUser.email || "";
}

// The browser's copy first (instant), then what the server has, which is the
// only place the phone number comes from. Saving is held until that arrives,
// so a blank phone field can't overwrite a real number.
async function populateProfileForm() {
    const cached = getCurrentUser();
    if (cached) fillProfileForm(cached);
    const save = document.querySelector("#profile-form [type=submit]");
    if (save) save.disabled = true;
    try {
        const res = await fetch("/me", { headers: authHeaders() });
        if (!res.ok) throw new Error();
        const me = await res.json();
        fillProfileForm(me);
        updateCurrentUser({ name: me.name, email: me.email, phone: me.phone });
        if (save) save.disabled = false;
    } catch {
        showMessage("profile-form-msg", "Couldn't load your profile. Reload the page to edit it.", true);
    }
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
            document.getElementById("settings-tab-appearance").hidden = target !== "appearance";
            if (target === "notifications") populateNotificationsForm();
        });
    });

    // Appearance: theme and animations, both per browser (theme.js). The
    // buttons follow the real state, so the top bar's theme button and these
    // never disagree.
    const themeBox = document.getElementById("appearance-theme");
    const motionBox = document.getElementById("appearance-motion");
    const press = (box, choice) => box.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.choice === choice)));
    const showAppearance = () => { press(themeBox, currentTheme()); press(motionBox, motionOn() ? "on" : "off"); };
    themeBox.addEventListener("click", e => {
        const btn = e.target.closest("button[data-choice]");
        if (btn) setTheme(btn.dataset.choice, btn);
    });
    motionBox.addEventListener("click", e => {
        const btn = e.target.closest("button[data-choice]");
        if (btn) setMotion(btn.dataset.choice === "on");
    });
    document.addEventListener("appearancechange", showAppearance);
    showAppearance();

    // Mirrors the login screen's password show/hide toggle (initShell in
    // common.js) — same .password-field/.eye-open/.eye-closed markup and
    // behavior, just wired here since these three fields live in this view.
    document.querySelectorAll("#view-settings .password-toggle-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const input = document.getElementById(btn.dataset.target);
            const isPassword = input.type === "password";
            input.type = isPassword ? "text" : "password";
            // toggleAttribute, not .hidden: an <svg> has no "hidden" property, so setting it did nothing.
            btn.querySelector(".eye-open").toggleAttribute("hidden", isPassword);
            btn.querySelector(".eye-closed").toggleAttribute("hidden", !isPassword);
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
            const data = await response.json().catch(() => ({}));
            replaceAuthToken(data.access_token);
            updateCurrentUser({ name, email, phone });
            showMessage("profile-form-msg", "Profile updated", false);
        } else {
            const err = await response.json().catch(() => ({}));
            const detail = Array.isArray(err.detail) ? "Check the email address." : err.detail;
            showMessage("profile-form-msg", detail || "Failed to update profile", true);
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
