import { showView, registerRoute } from "./common.js";

// Finance > Income. A placeholder page until its sub-phase is built (see
// phase.md, Phase 7); the route and permission (finance:view) are already real.
export function initFinanceIncome() {
    registerRoute("finance-income", () => {
        showView("view-finance-income");
    });
}
