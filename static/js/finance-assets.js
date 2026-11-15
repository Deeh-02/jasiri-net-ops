import { showView, registerRoute } from "./common.js";

// Finance > Assets. A placeholder page until its sub-phase is built (see
// phase.md, Phase 7); the route and permission (finance:view) are already real.
export function initFinanceAssets() {
    registerRoute("finance-assets", () => {
        showView("view-finance-assets");
    });
}
