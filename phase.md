# PHASES.md — Live Status Tracker (JASIRI NET OPS)

Check this file before starting any work. If a request doesn't match the
current phase's declared scope, flag it — don't do it anyway.

Completed phases collapse to one summary line (name + completion date)
once confirmed done — full detail below is only kept for the active phase
and whatever's still upcoming.

---

## ACTIVE PHASE: none currently

Phase 3 confirmed done — see COMPLETED PHASES below. Phase 4 has not been
kicked off yet; see UPCOMING PHASES for what's next once the owner starts
it.

---

## UPCOMING PHASES (order locked; each stays a one-liner per the usual
## convention until it becomes active, at which point its full brief goes
## here the same way Phase 3's did before it was completed)

**Phase 4 — Notifications.** Lowest-effort new addition — SMS templates
already designed, this is mostly wiring them in. (Previously numbered
Phase 3; renumbered to make room for the Phase 3 Ops Inventory System
brief, per owner's explicit call 2026-09-06 — see COMPLETED PHASES for
that phase's outcome.)

**Phase 5 — Ticketing.** Close in shape to the existing site verification/
check-in flow.

**Phase 6 — Basic CRM.** Likely just views/notes on top of the existing
customers table — to be CONFIRMED, not assumed, once this phase starts.

**Phase 7 — Chat.** Deliberately deferred and flagged for reassessment.
Most technically demanding of the set, and WhatsApp already works as a
contact channel. Confirm this solves a real operational gap before
building anything.

Each phase runs in its own branch (one branch per phase — see CLAUDE.md).
Before confirming any phase done, the owner checks it out locally
(`git checkout <phase-branch>`) and runs it on localhost — not just
Claude's word that "done when" criteria are met. Merges to `main` happen
only after that. Detail (scope, done-when criteria) expands here from a
one-liner when a phase becomes active. The DeepSeek delegation
confirmation checkpoint (see DELEGATION.md) resets at the start of each
new phase.

---

## COMPLETED PHASES

**Phase 3 — Ops Inventory System.** Completed 2026-09-17, merged to
`main`. Full inventory/asset-tracking domain for field operations
(enclosures, cable, consumables, power/network gear), entirely separate
from battery tracking. User-created categories tagged with one of three
fixed Cores (Asset-Serialized / Consumables-Quantity / Cable-Length) drive
row granularity and which fields apply — locked once a category has
items, since the three Cores use disjoint, non-convertible column sets.
Every item state change happens only as a side effect of a logged
transaction (In/Transfer/Adjustment/Issue/Return/Write-off/Reconciled),
never a direct edit. Delivered: the Categories/Items/Transaction Log core;
a unified Issue/Return Materials cart covering mixed-Core carts in one
action, with Quick Issue/Return auto-selecting eligible serials by
quantity; two-stage cable reconciliation (full cut out, actual usage
reconciled once the job closes, with usable-remainder/offcut split);
SKU/Spec Summary + Cable Type Summary + Offcut Rollup reporting; a full
permissions hierarchy for the domain (Inventory Items/Stock/Categories/
Log, plus an independent Reports toggle), enforced at both the UI/route
layer and the backend, not just the permissions panel. Went through
several owner-review refinement passes after the initial build (nav
restructure, Items/Stock split, Quick Issue/Return, on-hand-vs-total Qty
reporting, among others — see changelog.md's Phase 3 section for the full
list) before this final confirmation. See architecture.md's "Inventory:
Core system & permissions" section for the settled design reasoning.
Production DB migrations (`0004_inventory_core_tables.sql`,
`0005_inventory_custody_type.sql`) confirmed applied ahead of the `main`
merge. Owner confirmed done (2026-09-17).

**Phase 2 — Finish Incomplete Functionality.** Completed 2026-09-06.
> This is **Phase 3** of an ongoing project to build an inventory/asset
> tracking system for field operations (telecom-adjacent: enclosures,
> cabling, and power/network equipment). Phases 1–2 established the data
> model and category structure through design discussion. Phase 3 is
> about **implementing the system** (spreadsheet or lightweight app)
> based on the finalized structure below.
>
> ### Goal
> Build a working inventory + asset tracking system that:
> 1. Separates **Assets** (reusable, tracked individually, long-lived)
>    from **Inventory** (consumed/depleted, tracked by quantity or
>    length)
> 2. Gives every category consistent core fields, with category-specific
>    extensions layered on top
> 3. Logs every movement/transaction in one central log, so current-state
>    tables are always derived from history — never manually overwritten
> 4. Handles **partial/delayed reconciliation** for bulk cable (a full
>    reel/cut goes out, but actual usage is only known once the job
>    closes)
> 5. Provides a **single unified "issue materials" entry point** so a
>    user handing over a mixed batch (e.g. 1 enclosure + 2 packs of ties
>    + 150m of cable) never has to navigate between separate
>    Assets/Inventory views to do it — that separation is a backend
>    data-organization choice, not a user-facing workflow
>
> ### Top-Level Structure: Assets vs Inventory, with User-Defined
> ### Categories
> Rather than hardcoding fixed categories (Enclosures, Consumables,
> Bulk-Reel, Power-Network Assets) into the schema, categories should be
> **user-created and user-assignable**. A user can create a new category
> at any time (e.g. "Enclosures", "Solar Equipment", "Tools", "Cabling")
> without needing a developer/schema change.
>
> #### Category Table
> | Field | Notes |
> |---|---|
> | Category Name | Free text, user-defined (e.g. "Enclosures", "Power/Network Assets", "Consumables", "ADSS/Drop Cable") |
> | Tracking Type | Fixed dropdown, chosen when the category is created — this is what drives which fields/behavior apply to items in that category (see below). Options: `Asset (Serialized)`, `Inventory (Quantity-based)`, `Inventory (Length-based)` |
> | Description | Optional, free text |
>
> The **Tracking Type** is the only thing that must be fixed at the
> system level, because it determines behavior (individual lifecycle
> tracking vs. depletion tracking vs. length/reel tracking). Everything
> else about a category — its name, what items belong to it — is fully
> user-defined and open-ended.
>
> #### Item Table (applies to every item, regardless of category)
> Row granularity differs by Tracking Type — see the per-type breakdown
> below (one row per serial for Asset-Serialized, one row per Batch/Lot
> for Quantity-based, one row per Cut/Reel for Length-based). The fields
> below are universal regardless of that granularity:
> | Field | Notes |
> |---|---|
> | Category | Dropdown, references the user-created Category table |
> | SKU | Identifies the product/model, NOT the individual physical unit. Stays constant across purchases even if unit cost changes (e.g. same enclosure bought at 600 then 800 — same SKU, different cost recorded per batch) |
> | Name/Description | Human-readable label |
> | Location | Current physical location — updates live as items move |
> | Unit Cost | Per unit / per meter, ties into costing method below |
> | Supplier | If applicable — some assets may have no repeat supplier |
> | Unit of Measure (UoM) | pcs / pack / box / meter — explicit field to avoid ambiguity |
> | Notes | Free text catch-all |
>
> **Reorder Level is deliberately NOT listed here.** See the SKU/Spec
> Summary section below — it doesn't belong on a per-row Item record for
> any tracking type.
>
> ### Fields Driven by Tracking Type (not by category name)
> Instead of hardcoding fields per named category, fields should
> show/apply based on the category's **Tracking Type**. This means a
> newly created category automatically gets the right fields just by
> picking its type — no schema change needed.
>
> #### Tracking Type: `Asset (Serialized)`
> (applies to whatever categories the user tags this way — e.g.
> Enclosures, Power/Network Assets, or any future category like "Tools")
> - Serial Number (manufacturer's if present, otherwise internal — e.g.
>   `ENC-2026-001`)
> - Unit Cost recorded directly per serialized unit — no costing method
>   (FIFO/weighted-average) needed here, since each unit has a known
>   individual cost tied to its own serial. Costing methods exist to
>   handle *fungible* stock where you don't know which physical unit was
>   consumed; a serialized asset never has that ambiguity.
> - Status: `Active`, `Faulty`, `In Repair`, `Decommissioned`, `Spare —
>   In Storage`
> - Assigned To (person/site currently holding it)
> - Make/Model + Spec/Capacity (optional — useful for anything where
>   compatibility matters, e.g. inverter kW, battery Ah, CCR core count)
> - Install Date (optional)
>
> #### Tracking Type: `Inventory (Quantity-based)`
> (applies to categories like Consumables, or any future quantity-based
> category)
> - **Row granularity: one row per Batch/Lot, not one row per SKU.** If a
>   SKU has multiple batches with different unit costs or expiry dates,
>   each batch gets its own row (its own Quantity On Hand, Unit Cost,
>   Expiry). SKU-level totals (e.g. "total cable ties across all
>   batches") are a rollup/pivot over these rows, not a field maintained
>   directly on any single row. This avoids the system defaulting to
>   one-row-per-SKU and then breaking when batches diverge in cost or
>   expiry.
> - Quantity On Hand (by UoM — pack/box/piece), per batch
> - Batch/Lot + Expiry (Expiry optional — only relevant where
>   applicable, e.g. adhesives)
> - **Costing method applies here**: since units within a SKU are
>   fungible (you can't tell which physical tie or patch cord came from
>   which batch once mixed), use FIFO (recommended) or weighted average
>   to determine which batch's cost is drawn down as stock is consumed,
>   and to value what's left on hand
> - No serials — tracked at bin/shelf level, not individually
>
> #### Tracking Type: `Inventory (Length-based)`
> (applies to categories like ADSS/Drop Cable, or any future reel/
> length-based category)
> - **Row granularity: one row per Cut/Reel ID** — this one is already
>   unambiguous, since each physical cut is a distinct unit even when
>   nominal lengths repeat
> - Cut/Reel ID (unique per physical cut, even if the same nominal
>   length recurs — e.g. two separate 400m purchases get two different
>   Cut IDs)
> - Spec (core count, cable type)
> - Length Received
> - Length Remaining
> - **Costing method applies here too**, at the aggregate/reporting
>   level: if you need a blended cost-per-meter figure across multiple
>   cuts of the same spec (e.g. for job costing), apply FIFO or weighted
>   average across the contributing cuts — the individual cut's own
>   recorded Unit Cost stays fixed, but consumption reporting draws down
>   using the chosen method
> - Status: `In Stock`, `Out — Pending Reconciliation`, `Depleted` (see
>   reconciliation workflow below)
> - Usable flag: computed field — e.g. `=IF(Length_Remaining < 20m, "No
>   — Offcut", "Yes")`, threshold configurable
>
> This structure means: if the user later creates a brand-new category —
> say "Tools" or "Vehicles" — they just assign it a Tracking Type at
> creation, and it automatically inherits the right fields and behavior
> without any redesign.
>
> ### SKU/Spec Summary (where Reorder Level actually lives)
> Reorder Level is an **aggregate concept**, not a per-row one — it
> doesn't matter that one batch or one cut or one serial is low if the
> SKU/Spec as a whole still has plenty on hand. It needs its own rollup
> layer, grouped by SKU (for Asset-Serialized and Quantity-based) or by
> Spec (for Length-based, since that's the level at which
> interchangeability matters — e.g. "24-core ADSS" as a whole, not one
> specific cut).
>
> | Field | Notes |
> |---|---|
> | SKU / Spec | The grouping key — SKU for Asset/Quantity types, Spec for Length-based |
> | Total On Hand | Rollup: sum of all serials currently `Active`/`Spare` (Asset), sum of Quantity On Hand across all batches (Quantity-based), sum of Length Remaining across all cuts (Length-based) |
> | Reorder Level | Threshold set once per SKU/Spec, not per row |
> | Below Threshold? | Computed flag: `Total On Hand < Reorder Level` |
>
> This is a live formula/pivot view over the Item table (grouped by SKU
> or Spec), same as the offcut Summary view described earlier — not a
> manually maintained field. It answers "do we need to reorder SKU X"
> correctly regardless of how many individual batches, cuts, or serials
> that SKU is currently split across.
>
> ### Transaction Log (central, shared across all categories)
> This is the **primary "doing" surface** — the only place a user should
> regularly interact with directly. All category tables (Enclosures,
> Consumables, Bulk-Reel, Power-Network Assets) should be **derived
> views**, ideally with quantity/status fields computed from this log
> rather than manually edited.
>
> | Field | Notes |
> |---|---|
> | Log ID | Sequential unique ID, e.g. `TXN-0001`, auto-incrementing |
> | Date | |
> | SKU / Category | Links to the relevant item, whatever category/tracking type it belongs to |
> | Serial / Cut / Reel ID | If applicable |
> | Action | Fixed set of values: `Out`, `In`, `Transfer`, `Adjustment`, `Return`, `Write-off`, `Reconciled` |
> | Qty / Length | Amount moved |
> | From Location | |
> | To Location | |
> | Site | Where the work is happening |
> | Activity | Fixed list: `Installation`, `Expansion`, `Maintenance`, `Repair/Replacement`, `Relocation`, `Decommission` (no formal job IDs exist yet — Activity + Site + Date serve as the de facto reference) |
> | Issued To | Person accountable in the field |
> | Logged By | Person who recorded the transaction (accountability on the record-keeping side) |
> | Status | For bulk/reel transactions: `Open/Pending` or `Closed` (see reconciliation below) |
> | Notes | Free text |
>
> #### Access control principle
> - Implemented as a **Roles/Permissions section in the webapp** — an
>   admin screen where someone can click through and assign, per role
>   (or per user), what they're allowed to do: add transaction entries
>   only, edit item records, manage categories, edit historical log
>   rows, etc. No code change needed to adjust who can do what.
> - Default recommended roles: **Field/Store users** (can only add new
>   transaction log entries — issue/receive), **Managers** (can also
>   edit item records directly — fix errors, add new SKUs/categories),
>   **Admin** (full access, including editing historical log entries if
>   ever needed)
> - Master item table quantities/statuses should still be
>   formula-derived from the Transaction Log wherever feasible, so even
>   users with edit access aren't tempted to overwrite computed fields
>   directly — permissions and formula-derivation work together, not as
>   substitutes for each other
> - This role-based permission model is realistic to build in an app
>   (row/field-level permissions tied to roles) but is one of the harder
>   things to enforce cleanly in a spreadsheet (would need protected
>   ranges + Apps Script, and is easy to accidentally break) —
>   reinforces the earlier recommendation toward an app over a
>   spreadsheet for this phase
>
> ### Reconciliation Workflow (Length-based Inventory — cable usage
> ### known only after job closes)
> This applies to any category using Tracking Type `Inventory
> (Length-based)` (e.g. ADSS/Drop Cable). This is a **two-stage
> transaction**, not a single deduction, because the exact length
> consumed is unknown until the job finishes:
>
> **Stage 1 — Cable issued (full cut goes out):**
> - Log entry: Action = `Out`, full length of the cut taken, Status =
>   `Open/Pending`
> - Item record: that Cut ID's status becomes `Out — Pending
>   Reconciliation`, and **Location updates immediately to the
>   destination site** (it's physically there — Location should always
>   reflect physical whereabouts). The pending/unconfirmed state is
>   carried entirely by the **Status** field, not by holding Location
>   back at the warehouse. This keeps the two fields answering two
>   different questions cleanly: Location = "where is it," Status = "is
>   its consumption confirmed yet." Length is NOT yet deducted from
>   "available" stock reporting — it's in a pending state, neither
>   available nor confirmed consumed.
>
> **Stage 2 — Job closes, actual usage confirmed:**
> - Log entry: Action = `Reconciled`, records Length Used + Length
>   Returned
> - Item record updates: Length Remaining on that Cut ID adjusts to
>   reflect actual return (if any)
> - If returned remainder is long enough to be usable → becomes a **new
>   Cut ID** (e.g. `ADSS-2026-002-R`) with its own tracked remaining
>   length
> - If returned remainder is too short → logged directly as
>   offcut/scrap against the original Cut ID
>
> **Aging consideration**: flag any cut still in `Open/Pending` status
> beyond a configurable threshold (e.g. 14 days) so pending items don't
> get forgotten indefinitely while a job drags on.
>
> ### Offcut/Usable-Length Reporting
> Two linked views, not two separate data sets:
> 1. **Roll-up view** (by Spec): Total Remaining, Usable Remaining,
>    Offcut Remaining, # of Cuts Contributing
> 2. **Drill-down view** (filtered): lists the actual Cut IDs and their
>    individual remaining lengths that make up the offcut total for a
>    given spec
>
> Both should be built as live formulas/pivots off the item table
> (filtered to Length-based categories) — never maintained as separate
> manually-updated data.
>
> ### UX Requirement: Single Unified Issue Point
> **Critical constraint**: the Assets/Inventory category split is a
> backend data-organization decision only. A user issuing a mixed batch
> of materials (e.g., 1 enclosure + 2 packs of cable ties + 150m of ADSS
> for one job) must be able to do so via **one entry action** — not by
> navigating into an "Assets" section and then separately into an
> "Inventory" section. The system should route each line item to the
> correct underlying table automatically based on what's selected, while
> the person doing the handover experiences it as a single
> transaction/list, similar to a checkout cart.
>
> ### Suggested Implementation Structure
> Core tables/views:
> 1. `Categories` — user-managed list of categories, each tagged with a
>    Tracking Type (Asset-Serialized / Inventory-Quantity /
>    Inventory-Length)
> 2. `Items` — single table for all items across all categories, with
>    Tracking-Type-driven fields shown/used as applicable (one row per
>    serial / batch / cut, per type)
> 3. `Transaction Log` — primary data-entry surface, single source of
>    truth for all movement
> 4. `SKU/Spec Summary` — rollup view holding Reorder Level and Total On
>    Hand per SKU (or per Spec for Length-based), computed from the Items
>    table
> 5. `Offcut/Usable-Length Summary` — formula-driven roll-up + drill-down
>    views (filtered to Length-based items)
>
> This is naturally better suited to a lightweight app/database (e.g.
> Airtable-style or a small custom app) than a rigid spreadsheet, since
> user-created categories with type-driven fields are harder to maintain
> cleanly across separate static spreadsheet tabs. If a spreadsheet is
> still preferred for now, the `Items` tab can hold all categories
> together with conditional formatting/filtering by Category and
> Tracking Type, rather than splitting into separate tabs per fixed
> category.
>
> A single "New Transaction" entry screen/form should remain the main UI
> surface for day-to-day use, letting someone pick any item regardless
> of category and log it in one action — the category/tracking-type
> structure underneath stays invisible to that workflow.
>
> ### Deliverables for Phase 3
> 1. Implement the Categories table (user-creatable, each assigned a
>    Tracking Type) and the single Items table with type-driven fields
>    (correct row granularity per type — one row per serial, per batch,
>    or per cut)
> 2. Wire up the Transaction Log as the single source of truth, with item
>    records computing their current state from it
> 3. Implement the SKU/Spec Summary rollup (Total On Hand + Reorder Level
>    + Below-Threshold flag), grouped correctly (by SKU for
>    Asset/Quantity types, by Spec for Length-based)
> 4. Implement the two-stage reconciliation flow for Length-based
>    inventory items
> 5. Implement the offcut roll-up + drill-down reporting view
> 6. Implement a single unified "issue materials" entry flow covering
>    mixed items from any category/tracking type in one transaction
> 7. Build the Roles/Permissions admin screen (default roles: Field/
>    Store, Manager, Admin)
> 8. Seed with real starting data: 48 enclosures (batch costs noted,
>    category = "Enclosures", type = Asset-Serialized), current
>    consumables stock, current ADSS/drop cable cuts, current power/
>    network assets on hand
> 9. Confirm categories are fully user-manageable going forward
>    (add/rename/retire a category without any code change) — only the
>    Tracking Type options themselves are fixed at the system level

**Phase 4 — Notifications.** Lowest-effort new addition — SMS templates
already designed, this is mostly wiring them in. (Previously numbered
Phase 3; renumbered to make room for the Phase 3 Ops Inventory System
brief above, per owner's explicit call 2026-09-06.)

**Phase 5 — Ticketing.** Close in shape to the existing site verification/
check-in flow.

**Phase 6 — Basic CRM.** Likely just views/notes on top of the existing
customers table — to be CONFIRMED, not assumed, once this phase starts.

**Phase 7 — Chat.** Deliberately deferred and flagged for reassessment.
Most technically demanding of the set, and WhatsApp already works as a
contact channel. Confirm this solves a real operational gap before
building anything.

Each phase runs in its own branch (one branch per phase — see CLAUDE.md).
Before confirming any phase done, the owner checks it out locally
(`git checkout <phase-branch>`) and runs it on localhost — not just
Claude's word that "done when" criteria are met. Merges to `main` happen
only after that. Detail (scope, done-when criteria) expands here from a
one-liner when a phase becomes active. The DeepSeek delegation
confirmation checkpoint (see DELEGATION.md) resets at the start of each
new phase.

---

## COMPLETED PHASES
**Phase 2 — Finish Incomplete Functionality.** Completed 2026-09-06.
Seventeen incomplete-feature items resolved (button styling, movement
"Moved by" typeahead + recording fix, movement notifications/badges,
password show/hide, settings tab theming, global search reach, status-card
click-through detail, role-based move authorization, movement-lifecycle-
driven battery status, site-down flagging, EAT timestamps, Reason dropdown
restyle, live-sync render flicker, stale static assets) plus several
found-along-the-way fixes (schema drift migrations, stale battery table on
cancel, `.table-scroll` blocking page scroll); one item (inline record
detail in global search) dropped by owner's explicit call. Remaining loose
end — migrations `0002_backfill_in_transit_at.sql` and
`0003_close_out_site_still_down.sql` not yet run against production —
discarded per owner's explicit call (2026-09-06), not pursued further.

**Phase 1 — Mobile Fixes.** Completed 2026-09-04. Viewport meta tag added
(previously missing entirely). Sidebar nav rebuilt as a push-open
off-canvas drawer (a bottom tab bar was tried and explicitly rejected);
topbar search collapsed to an icon-only trigger; every view's table
wrapped in a `.table-scroll` box, horizontally contained always and
height-capped with a sticky header on phones — Batteries alone additionally
gets a frozen first column, a deliberate per-table decision, not a general
pattern. Stat-card grid tightened to 2 columns on phones. DESIGN.md's
Mobile Behavior section filled in from the resulting code, including
several real bugs found and fixed along the way (a topbar `overflow:hidden`
that was silently clipping the profile dropdown; a `border-collapse`
setting that silently broke `position:sticky` on table cells; a flex
`min-width:auto` default that was forcing the page wider than the
viewport). The five-view table-wrapper replication was delegated to
DeepSeek via `ask_deepseek.py` — its first real run against the live API,
verified clean (diff-checked) on all 5 calls. Owner confirmed through
extensive real-device (not just dev-tools) testing throughout the phase,
per this phase's own "before confirming" requirement.

**Phase 0 — Separate & Polish.** Completed 2026-09-04. Backend split into
`routers/` + `db/` (one file per domain: auth, permissions, sites,
batteries, users); frontend split into per-view HTML fragments, ES
modules, and CSS files; shared permission-check function consolidated into
one place. ARCHITECTURE.md and DESIGN.md drafted from the resulting code.
Owner confirmed by running the `phase-0-separate` branch locally and
clicking through sign-in, batteries, sites, and permissions.