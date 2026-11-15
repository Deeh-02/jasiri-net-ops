import { showView, registerRoute } from "./common.js";

// Finance > Performance. A placeholder page until its sub-phase is built (see
// phase.md, Phase 7); the route and permission (finance:view) are already real.
export function initFinancePerformance() {
    registerRoute("finance-performance", () => {
        showView("view-finance-performance");
    });
}
