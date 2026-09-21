import { loadViewFragments, initShell, resolveAuthUI, bootAuth } from "./common.js";
import { initDashboard } from "./dashboard.js";
import { initSites } from "./sites.js";
import { initMovements } from "./movements.js";
import { initCheckSites } from "./check-sites.js";
import { initStatus } from "./status.js";
import { initManageSites } from "./manage-sites.js";
import { initPackages } from "./packages.js";
import { initUsers } from "./users.js";
import { initRoles } from "./roles.js";
import { initSettings } from "./settings.js";
import { initInventory } from "./inventory.js";
import { initStock } from "./stock.js";
import { initInventoryManage } from "./inventory-manage.js";
import { initInventoryLog } from "./inventory-log.js";
import { initIssueMaterials } from "./issue-materials.js";
import { initReturnMaterials } from "./return-materials.js";
import { initInventoryReports } from "./inventory-reports.js";

async function boot() {
    initShell();
    resolveAuthUI();
    await loadViewFragments();

    initDashboard();
    initSites();
    initMovements();
    initCheckSites();
    initStatus();
    initManageSites();
    initPackages();
    initUsers();
    initRoles();
    initSettings();
    initInventory();
    initStock();
    initInventoryManage();
    initInventoryLog();
    initIssueMaterials();
    initReturnMaterials();
    initInventoryReports();

    bootAuth();
}

document.addEventListener("DOMContentLoaded", boot);
