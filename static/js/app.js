import { loadViewFragments, initShell, bootAuth } from "./common.js";
import { initDashboard } from "./dashboard.js";
import { initSites } from "./sites.js";
import { initMovements } from "./movements.js";
import { initCheckSites } from "./check-sites.js";
import { initUsers } from "./users.js";
import { initRoles } from "./roles.js";
import { initSettings } from "./settings.js";

async function boot() {
    initShell();
    await loadViewFragments();

    initDashboard();
    initSites();
    initMovements();
    initCheckSites();
    initUsers();
    initRoles();
    initSettings();

    bootAuth();
}

document.addEventListener("DOMContentLoaded", boot);
