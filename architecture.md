# ARCHITECTURE.md — System/Data Reasoning Reference (JASIRI NET OPS)

**Status: drafted by Claude from the post-Phase-0 codebase, pending owner
review.** Each section below is marked either approved or corrected once the
owner has looked it over.

## Schema choices

**Note on `schema.sql`:** the file in the repo root is stale — it's missing
`locations.is_active` / `is_online` / `verification_confirmed_at`,
`battery_movements.status` / `arrived_at` / `confirmed_at` /
`moved_by_user_id`, `users.role_id`, and the `roles` / `role_permissions`
tables entirely, all of which the running code queries against successfully.
Migrations were evidently applied straight to the live database without ever
re-dumping this file. The tables below are reconstructed from what the code
in `db/*.py` actually selects/inserts/updates — that's the ground truth, not
`schema.sql`. Worth a fresh `pg_dump` at some point so the file matches
reality again; not done here since that's a file/tooling fix outside Phase
0's declared scope, not a schema change.

**`batteries`** — one row per physical battery. `status` is a soft-delete
flag (`active`/`inactive`, set by `deactivate_battery`), separate from
`charge_status` (`unknown`/`charging`/`charged`/`low`) and separate again
from the *derived* "At Base"/"Deployed" label the API returns, which is
computed from the battery's last movement rather than stored.

**`locations`** — sites, including the single home base
(`is_home_base`, enforced unique by `one_home_base_idx` — only one row can
have it true). `is_active` is the soft-delete flag (`delete_location` just
flips it, never actually deletes a row — movement history stays intact).
`is_online` + `verification_confirmed_at` back the hourly site-verification
feature: `is_online` is the real persisted state, `verification_confirmed_at`
is compared against the current hour on read to derive "needs check" —
that derivation is never stored.

**`battery_movements`** — a state machine, not just a log. `status` moves
through `pending → in_transit → arrived → site_confirmed_online` for a
`site_down` move confirmed back online, or straight to `completed` for
any other reason — including a `site_down` move answered "still down"
(Phase 2: this used to land on its own `site_still_down` status and sit
there indefinitely; it now closes out as `completed` like everything
else, since the movement's own lifecycle is done either way). `cancelled`
is reachable from `pending`/`in_transit`. A NULL `from_location_id` is
legal (first-ever movement of a battery). `site_still_down` still exists
as a literal value purely for backward compatibility with rows written
before that change (`AWAY_STATUSES` in `db/batteries.py` still matches
it) — no code writes it going forward. This table is also where
`battery.charge_status` resets to `unknown` from, via
`mark_movement_in_transit` — once a battery is actually moving, nobody
can trust (or plug in to check) the last-known charge reading. This used
to fire earlier, at `record_movement` time (while the battery was still
just `pending`, physically sitting where it was) — moved later once
status became fully lifecycle-driven (Phase 2, see below).

**Battery status is fully derived from the last movement, not partly
stored** (Phase 2). `db/batteries.py`'s `_battery_status` returns "At
Base" (never moved, or the last movement landed back at the location
flagged `locations.is_home_base`), "Pending"/"In Transit" while a
movement is queued/underway, or "Deployed" once arrived anywhere else —
whether that arrival is a straight move or the site-down flow, confirmed
online or not. A site confirmed still down does NOT get its own status
label or its own count anywhere: it's surfaced instead as a "needs
attention" flag (`site_online === false` while `status === "Deployed"`,
computed identically on the frontend for both the main table's red pill
and the stat-detail modal's red row accent) driven by the destination
`locations.is_online`, independent of the movement's own status — this
is what makes the flag self-healing once the site is confirmed back
online later, by any means, without the movement itself needing to
reopen. The same `site_online` check also keeps `set_charge_status`
rejecting "charging" for a flagged battery even after its movement has
closed out.

**`users`** — `role` is a free-text label (`admin` is magic — see
Integration approach below); `role_id` optionally points at `roles` for
everyone else. `status` is the soft-delete flag.

**`roles` / `role_permissions`** — `role_permissions` is a sparse
`(role_id, section, action) → allowed` table; a missing row means not
allowed (`check_role_permission` returns `False` on no match, not an error).
Sections in use today: `batteries`, `movements`, `sites`, `site_checks`,
`users`, `roles`. No CHECK constraint on `action` — it's plain `text`, so a
new action string (like `movements`' `create`, added Phase 2) is just a
data-level addition, no migration needed. `movements` has three actions in
use, each gating something distinct: `view` (see the Movements tracking
page at all), `create` (initiate a new move from a battery's row), `manage`
(act on a movement already in progress — mark in-transit/arrived, cancel,
confirm site online). `create` and `manage` used to be one permission;
split in Phase 2 once it became clear "can start a move" and "can progress
one already started" are genuinely different capabilities, not the same
thing asked two ways.

**Inventory domain (Phase 3) — six new tables, entirely separate from the
battery-tracking tables above.** `inventory_locations` is deliberately its
own table rather than reusing `locations` — an inventory client job site
must never show up in Check Sites. `inventory_categories.tracking_type`
(`asset_serialized` / `inventory_quantity` / `inventory_length`) is what
drives behavior, not the category name — plain `text`, no CHECK constraint,
validated in `routers/inventory.py` (same enum-as-text convention as
`battery_movements.reason`), so a user creating a new category never needs
a migration. `inventory_items` is one wide table with nullable columns per
tracking type (not JSONB — the three types are fixed at the system level,
so JSONB would buy flexibility nothing here ever uses, at the cost of every
downstream report needing a `->>'field'` extraction instead of a plain
column). Row granularity is per-serial / per-batch-lot / per-cut depending
on tracking type — a batch with a different cost or expiry is a new row,
never a top-up of an existing one.

`inventory_transactions` is the append-only log and the single source of
truth — item state changes only as a side-effect of a logged transaction,
in the same commit/connection that writes the log row (the write-through
pattern, matching `battery_movements` → `batteries`). `item_id` is nullable
because a cable reconciliation's usable remainder creates a brand-new item
row that the log entry is the first reference to (the item row is always
inserted before the log row that points at it). `event_group_id` (uuid,
nullable) links multiple log rows produced by one user action: a mixed
issue-cart checkout (N lines), a split Transfer (origin decrement + new
destination row), or a reconciliation that spins off a new cut — a single-
row action leaves it null. `inventory_sku_thresholds` holds Reorder Level
(owner-entered, keyed on `category_id` + `sku_or_spec`) since that's
aggregate data that doesn't belong on any single batch/cut/serial row.

**Two-stage cable lifecycle:** issuing a cut moves its `location_id` to the
site immediately and sets `length_status = "Out — Pending Reconciliation"`,
but does *not* deduct `length_remaining` — the exact metres used aren't
known until the job closes. Reconciliation then always depletes the
original row and, only when the returned remainder clears
`USABLE_LENGTH_THRESHOLD_M` (20, a named constant — no settings UI for one
number), inserts a new `<original>-R` cut row; below that it's scrap logged
against the original with no new row. A cut still `Out — Pending
Reconciliation` past `PENDING_CUT_AGING_DAYS` (14) is flagged — computed on
read (`get_open_pending_cuts`), the same "derive on read, store nothing"
approach `locations.needs_check` already uses.

**SKU/Spec Summary's "Total On Hand" means available, not deployed** — the
same distinction the reconciliation aging flag draws, applied per tracking
type in the way each type represents "out": Asset rows count only those
still sitting at a store location (`is_store = true`) since an issued
asset's `location_id` moves to the site; Quantity rows sum
`quantity_on_hand` with no location filter at all, since issuing a
consumable draws it down in place rather than moving the row; Length rows
sum `length_remaining` only where `length_status = 'In Stock'`, since a cut
still pending reconciliation keeps its full `length_remaining` even though
it's physically gone. **Offcuts have no stored flag either** — an item row
is an offcut (a reconciliation-created remainder, not an original received
length) if it has a linked `Reconciled` transaction row with `length_used
IS NULL` — the original cut's own `Reconciled` row always carries
`length_used`, the new remainder's does not (see
`db/inventory_transactions.py`'s `reconcile_cut`), so that single column
check is enough to tell them apart on read.

**Post-implementation restructure (owner feedback, same phase branch):**
Categories/Locations moved off the daily-use Items page onto their own
`inventory-manage` sub-page (reached via a header button, same
header-link-sub-page shape `inventory-log`/`inventory-reports` already use)
— Items, Transactions, Reports, and Manage are now four separate pages
instead of one long stacked view. A per-item detail modal
(`openItemDetailModal` in `inventory-common.js`, reused by both the Items
page and the Reports drill-downs) now holds unit cost/supplier/notes/batch
detail and the item's full transaction history, off the main table — the
Length-type table itself was also trimmed to Cut/Reel ID · Spec ·
Remaining · Location, since Name/SKU aren't meaningful for a cable row.
`get_sku_summary()` gained a live weighted-average `avg_unit_cost` per SKU
(`SUM(cost * qty) / SUM(qty)` for Quantity batches, plain `AVG` for Asset
serials since each row is exactly one unit) — this reverses the original
Phase 3 plan's explicit "capture cost data only, defer the draw-down math"
call, on the owner's direct request; Length is deliberately left out, since
a blended cost-per-meter across cuts is a separate, not-yet-asked-for
feature. A new `get_cable_type_summary()`/`get_cable_drill_down()` pair
answers "what cable do we have" (every in-stock reel of a spec, reel count
+ total length) as a Reports panel distinct from the existing offcut
rollup (which answers "how much of it is unusable offcut"). Asset lifecycle
gained a `Deployed` status, set on issue and reset to `Active` on Return —
but only when the asset was `Deployed` at return time, so a Return never
silently "heals" one returned while `Faulty`/`In Repair`/`Decommissioned`.
No separate resting `Returned` status was added; the `Return` transaction
log row itself carries that history, the same way `Reconciled` is a log
verb rather than a resting `length_status`.

**Second UI/UX refinement pass (Addendum 2, same phase branch) — nav, Items
table restructure, and a unified explicit-status Return.** Several pieces of
the post-implementation restructure above were superseded before they were
ever committed:

- **Collapsible sidebar nav.** The Inventory sidebar entry is now a pure
  expand/collapse toggle (`static/index.html`'s `#inventory-nav-toggle`, no
  `data-view` of its own) revealing three `.nav-link.sub` children — Items,
  Transaction Log, Manage — replacing the header-link-sub-page pattern.
  Reports is now its own standalone top-level sidebar item, a sibling of
  Inventory rather than reached from inside it. The CSS for this
  (`.chevron`, `.nav-subitems`, `.nav-link.sub` in `common.css`) had existed,
  fully built, since Phase 0 — nothing had ever used it until now.
  `common.js`'s `applyPermissionVisibility()` handles this group specially
  (an OR of its three sub-routes' permissions drives the whole group's
  visibility, since the toggle itself has no single route to check), and
  `setActiveNav()` auto-*expands* (never auto-collapses) the group when the
  active route is one of its own — e.g. on a page refresh landing directly
  on a sub-route.
- **"Core" terminology.** `asset_serialized`/`inventory_quantity`/
  `inventory_length` now display as Asset Core / Consumables Core / Cable
  Core everywhere a tracking type is shown — display-only, via
  `inventory-common.js`'s `TRACKING_TYPE_LABELS`; the underlying column/
  variable names are unchanged.
- **Locations lost its management screen.** `inventory_locations` still
  backs both an item's storage location and a transaction's site (so a
  future site-cost report stays FK'd), but nothing creates/edits/deactivates
  a row by hand any more. Issue/Return Materials' Site field is a free-typed
  autocomplete (`<input list=...>` + `<datalist>`) that resolves to an
  existing row by case-insensitive name match or silently creates a new one
  (`is_store = false`) via `get_or_create_location_by_name`. The item
  add/edit form's Location `<select>` stays a real dropdown, scoped to
  `is_store = true` rows — "which store is new stock shelved at" is a
  different question from "what job is this going to." Exactly one row is
  expected to carry `is_store = true` as the default store Return Materials
  sends stock back to (`get_default_store_location`) — set via direct DB
  update, the same "named constant, no settings UI" precedent as the 20m/14d
  thresholds, since it's foundational setup data that changes rarely.
- **Items table is now a SKU-level rollup, not a per-row list.** New
  `get_items_summary()` (`db/inventory_reports.py`) groups by
  `(category_id, sku_or_spec)` and returns **total owned** (on-hand +
  deployed combined) alongside the on-hand/deployed split, so the frontend's
  Status filter (All/In Store/Deployed) can switch which number displays
  without a re-fetch. This is deliberately a *different* function from
  `get_sku_summary()` (unchanged, still on-hand-only) — the Reports page's
  reorder-level flagging genuinely needs "what's available to issue," the
  Items table needs "what do we own," and conflating them would make one of
  the two wrong. Unit Cost shows a `~`-prefixed weighted average for Assets
  (still the already-correct math), a plain weighted average for Quantity
  (unchanged from the first restructure), and the most-recently-received
  reel's cost for Cable (a single real value, not an average). Edit/Delete
  moved off the aggregate row (a SKU row can represent many physical rows)
  down to a per-unit list. A `custody_type` column on `inventory_categories`
  (`per_job`/`custody`, migration `0005`) backs a third Items-table filter —
  no new transaction type or item status, since Issue/Return behave
  identically for both; the only difference is this filter and a future
  reporting distinction.
- **View is now "stock history," branching by Core, two levels deep for
  Asset/Cable.** Clicking View on an Asset or Cable SKU row opens a list of
  its individual units (`GET /inventory/items` extended with an optional
  `sku` query param, matching either the `sku` or `spec` column depending on
  type); clicking a unit opens a **second real instance** of
  `dashboard.js`'s `openViewBatteryModal`/`renderLogsTable` pattern —
  `inventory-common.js`'s `openUnitDetailModal`, same tabbed Details/Logs
  layout and client-side pagination, reimplemented as inventory-domain code
  per the no-cross-domain-import rule below rather than calling the
  battery-specific function directly. It deliberately keeps a Notes column
  in the Logs tab (the battery modal's doesn't) since Asset/Cable history is
  explicitly where notes carry context. Consumable Core has no individual
  units, so View skips straight to a flat running-balance table
  (`openSkuHistoryModal` / new `get_sku_transaction_history()`, a `SUM(...)
  OVER (ORDER BY created_at)` window function) — no Notes column there,
  since a per-tie issue doesn't carry meaningful notes.
- **Return Materials is now a real cart flow, sharing one Return code path
  with the generic Log Transaction form.** Previously there was no dedicated
  Return UI — only the generic transaction-log modal, which auto-reset a
  `Deployed` asset back to `Active` on Return with no user input. Both entry
  points now go through the same `_plan_return()` helper
  (`routers/inventory.py`), which requires an explicit `asset_status` for
  every asset Return (validated against `ASSET_STATUSES`, never inferred)
  and always resolves the destination to the single default store rather
  than taking a client-supplied location — neither entry point collects a
  destination any more. New `POST /inventory/return` /
  `db/inventory_transactions.py`'s `return_cart()` mirror `issue_cart()`'s
  shape exactly (one connection, N linked log rows, one `event_group_id`).
- **A CSS gotcha worth flagging for future tab-based modals:**
  `dashboard.js` wires a single page-wide
  `document.querySelectorAll(".dashboard-tab-slant")` click listener that's
  hardcoded to toggle the *battery* modal's own `#view-tab-details`/
  `#view-tab-logs` panels — since every view fragment is injected into one
  DOM, a second tab UI reusing that literal class would have its clicks
  silently mishandled by that listener too. `settings.css`'s `.settings-tab`
  already established the fix (a domain-scoped class with identical CSS but
  its own click handler); `inventory.css`'s `.inventory-item-tab` follows
  the same precedent for the new unit detail modal.

**Third round — Transaction Log and Reports UI, same phase branch.** Owner
feedback on the two remaining inventory pages, no schema changes:

- **Pending Cable Cuts is no longer a standalone table.** The old
  `GET /inventory/pending-cuts` endpoint and `db/inventory_transactions.py`'s
  `get_open_pending_cuts()` are gone — dead code once the table that called
  them was removed. Pending/open status is now folded into the Transaction
  Log's own Action column, computed **at display time** in
  `inventory-log.js`'s `displayAction()`: a stored `"Out"` row (Issue
  Materials' action — see `issue_cart()`) reads as "Pending" while its own
  `status` field is still `'open_pending'`, and "Issue" once
  `reconcile_cut()` flips that same field to `'closed'` in the same commit
  that writes the `Reconciled` row. This reads an already-existing,
  already-correctly-written column — no new query, and critically the stored
  `action` value itself is never touched, so the append-only log guarantee
  holds. Reconcile's only entry point now is the transaction detail
  modal (below) rather than a dedicated table row action.
- **The main log table is now Date / Action / SKU/Spec / Qty/Length /
  Movement / By** (`inventory-log.js`'s `renderLogTable`). Site, Activity,
  Issued To, exact From/To, Status, and Notes are still on every row — they
  moved into a click-through detail view (`#transaction-detail-overlay`,
  `openTransactionDetail()`), not deleted. Movement
  (`movementCell()`) is one adaptive string per action rather than three
  columns: `In` → `→ [to]`, `Transfer`/`Return` → `[from] → [to]`, `Out`
  (Issue) → `→ [issued to], [site]`, and blank for `Adjustment`/`Write-off`/
  `Reconciled` (nothing physically moves). Every row is clickable
  (`.inventory-log-row`) rather than carrying its own view button, since the
  table is now fixed at exactly six columns.
- **Reports is now tabbed** (SKU Summary / Cable Summary / Offcut Rollup),
  reusing the same domain-scoped `.inventory-item-tab`/`.inventory-item-tab-row`
  classes as the unit detail modal above — but scoped by `id` in
  `inventory-reports.js`'s `initReportsTabs()`
  (`document.getElementById("reports-tab-row")`), not the bare class. This
  mattered in practice: the item detail modal's own `.inventory-item-tab-row`
  lives in `inventory.html`, which — like every view fragment — is injected
  into the DOM at boot regardless of which view is active, so a
  `document.querySelector(".inventory-item-tab-row")` on the Reports page
  silently grabbed the *wrong* one during testing. A Category filter now
  sits above the tabs (`#inventory-reports-category-filter`) and scopes all
  three report queries — `get_sku_summary`, `get_cable_type_summary`, and
  `get_offcut_summary_by_spec` all gained an optional `category_id` param.
- **SKU Summary drops its Category column for Name** (`MIN(name)` per
  SKU/spec group, alongside SKU/Spec itself), gains a **Total Value** column
  (`avg_unit_cost * total_on_hand`, `None`/"—" for Length rows, matching the
  pre-existing decision to leave cable out of the cost math), and a
  standalone **Total Value** line below the table summing whatever's
  currently visible — so it respects the Category filter without any extra
  query. **Reorder Level editing moved off this page entirely** — it's
  config data, not something that belongs in a report. The page now just
  displays the currently-set value plus a `.status-pill.low-stock` "Low"
  badge when Total On Hand is under it; the `PATCH /inventory/reorder-level`
  Save button/input that used to live in every SKU Summary row is gone.
- **Reorder Level is now edited from the Items page instead** — `get_items_summary()`
  gained the same `(category_id, sku_or_spec)` threshold join `get_sku_summary()`
  already had, and the Items table's new Reorder Level column
  (`inventory.js`'s `reorderLevelCell()`/`startEditingReorderLevel()`) is a
  click-to-edit span that swaps itself for a number input on click, saving
  via the same `PATCH /inventory/reorder-level` endpoint on blur/Enter — an
  edit-on-click affordance rather than a permanently-open input box. No Low
  flag is shown here deliberately: Reorder Level is compared against
  on-hand-only stock (`get_sku_summary`'s definition), while the Items
  table's own Qty is total-owned (on-hand + deployed) — showing a Low badge
  against the wrong quantity would be actively misleading, so that flag
  stays exclusive to the Reports page, which has the right number for it.

**Fourth round — Add Item split into Add Product / Add Unit, same phase
branch.** The single Add Item form conflated two different concerns:
defining a product/SKU (name, make/model, spec, supplier — one-time) and
adding a physical unit (repeats every time stock arrives). No schema
change — every field already existed on `inventory_items`, denormalized
per row; this is a frontend workflow split plus one new backend capability
(auto-logging a unit's arrival), not a data model change. A normalized
`inventory_products` table was considered and rejected: nothing asked for
that, and it would mean migrating every existing row and every query that
reads name/make_model/supplier/etc. off `inventory_items` — real scope
creep for what's actually a UI/workflow ask.

- **Add Item is now a wizard** (`inventory.js`'s `initAddItemWizard()`,
  `static/views/inventory.html`'s `#add-inventory-item-overlay`): a choice
  step (Existing product / New product) → for New, a Step 1 "Add Product"
  form (SKU, Name, Category, plus Make/Model+Spec/Capacity for Asset or Spec
  for Length, Supplier, UoM) → Step 2 "Add Unit" (Serial Number/Status/
  Assigned To/Install Date for Asset, Batch/Lot/Expiry/Quantity for
  Quantity, Cut/Reel ID/Length Received for Length, plus universal Location/
  Unit Cost/Notes). Existing product skips straight to Step 2 via a product
  search built from the already-loaded all-items cache
  (`buildProductList()`, deduped by `(category_id, sku-or-spec)` — the same
  identity `get_items_summary()` groups by), so re-adding stock of a known
  product never re-asks for its product-level fields.
- **A saved unit doesn't close the wizard** — it lands on a success panel
  with "+ Add another unit" (resets Step 2, same product, same sitting) and
  "Done", covering the "48 enclosures at once" case without re-navigating.
  Every unit saved in one sitting shares one `event_group_id`, threaded
  through the same way `issue_cart`/`return_cart` do: the first save's
  response hands back a fresh id, and the frontend passes that same id on
  every following save in the sitting rather than generating a new one.
- **New `POST /inventory/units` endpoint, kept separate from
  `POST /inventory/items`.** The two are genuinely different operations —
  `/units` always writes an `In` transaction row in the same commit as the
  item insert (`db/inventory_transactions.py`'s `add_unit()`, following the
  established "insert the item first, log row references its id" ordering
  from `issue_cart`/`reconcile_cut`), while plain `/inventory/items` never
  has and still doesn't — kept as-is rather than folded together, since a
  future bulk/CSV import (flagged as an open item back in the original
  phase plan) would want silent inserts, not a flood of In rows. The
  qty_or_length written to that row mirrors the original single-line
  `In` semantics from Milestone 4's Action Semantics table
  (`quantity_on_hand` for Quantity, `length_received` for Length, `None`
  for Asset).
- **Assigned To is now conditional on the category's `custody_type`** —
  shown only for `custody` categories, in both the Add Unit step and the
  Edit Unit form (`#add-item-unit-assigned-to-row` /
  `#edit-inventory-item-assigned-to-row`, toggled by looking up the item's
  category in the already-loaded categories cache — no backend change,
  since `custody_type` already came back on every category row from the
  second refinement pass). A `per_job` asset simply never sends
  `assigned_to_user_id`, regardless of what's sitting in the hidden select.
- **A Chromium constraint-validation gotcha, worth flagging for the next
  conditionally-shown required field:** hiding a `required` input's
  *ancestor* (via `hidden` + `[hidden]{display:none!important}`) is not
  enough to exempt it from `checkValidity()` in Chromium — `offsetParent`
  correctly reports `null` (not rendered), but `checkValidity()` still
  fails on it. `inventory-log.js`'s `updateAssetStatusVisibility()` had
  already worked around this once (toggling `.required` directly rather
  than relying on the block's `hidden` state); the Add Product step's Spec
  field hit the same thing and got the same fix.

**Fifth round — restore Edit/Delete on the Items table's aggregate row,
split Edit the same way Add Item was split.** The prior round's Items
table shipped View-only, which turned out to be a step too far — Edit and
Delete needed to come back, but scoped correctly this time instead of
reusing the old single-form-covers-everything modal (which was already
flagged as a product/unit conflation bug on Add Item, and turned out to
still exist on Edit too, since Edit had never been touched by that fix).

- **Two edit modals now, never one.** `#edit-inventory-item-overlay`
  ("Edit Unit", reachable only from the per-unit list inside View) is
  unit-level only — Location, Serial Number/Status/Assigned To/Install
  Date, Batch/Lot/Expiry/Quantity, Cut/Reel ID/Length fields, Unit Cost,
  Notes. `#edit-product-overlay` ("Edit Product", reachable from the Items
  table's aggregate row) is product-level only — SKU, Name, Category,
  Make/Model+Spec/Capacity or Spec, Supplier, UoM — mirroring the Add
  Product step's exact field set. Neither form shows the other's fields.
- **Editing a product bulk-updates every active unit under it.** There's
  still no separate products table (same reasoning as the Add Item round),
  so "editing the product" means `db/inventory_items.py`'s new
  `update_product(category_id, sku_or_spec, fields)` running one `UPDATE
  ... WHERE category_id = %s AND (sku = %s OR spec = %s) AND is_active =
  true` — the same `(sku = %s OR spec = %s)` grouping test used everywhere
  else in this domain (`get_all_items`'s `sku` param, `buildProductList()`).
  New `PATCH /inventory/products`, gated the same `inventory_items:edit`
  permission as unit-level edit. Moving a product to a different category
  is allowed only within the same Core (`tracking_type`) — crossing Cores
  would leave every unit's type-specific columns (make_model vs
  quantity_on_hand vs cut_reel_id) meaningless for its new category, so
  that's rejected with a 400 rather than silently corrupting the rows.
- **The Product Edit form has no server-stored "current" product to fetch
  from** — it reads its pre-fill values off the first matching item in the
  already-loaded all-items cache (same lookup `buildProductList()` uses for
  the Add Item wizard's product search), rather than a new endpoint.
- **Product delete is a guard, not a cascade.** New `DELETE
  /inventory/products?category_id=&sku_or_spec=` counts active units under
  the group first (`items_db.count_active_units`) and refuses with `400
  "Can't delete — N units still exist under this product, remove those
  first"` whenever that count is nonzero — which in practice is almost
  always, since `get_items_summary()` (what populates the Items table to
  begin with) only ever returns groups that have at least one active unit.
  That's intentional, not a bug to route around: the guard's job is to make
  bulk/cascading delete impossible, not to provide a normal-path way to
  delete a SKU — removing every unit individually via the unit list is
  still how a SKU actually empties out.
- **A layering bug found (and fixed) by testing this, unrelated to the
  product/unit split itself:** opening Edit Unit from inside the per-unit
  list left the unit-list overlay still visible behind it — both are
  `.modal-overlay` at equal z-index, so stacking falls back to DOM order,
  and the unit list (later in `inventory.html`) painted over the edit
  modal and ate its clicks. The View button already had the fix for this
  exact problem (`unit-list-overlay.hidden = true` before opening its own
  modal); the same one-line fix was missing from the Edit button and is
  now applied there too.

**Sixth round — split the Items page into two: Items (catalog) and Stock
(operational).** The prior five rounds all reworked one page that was
trying to answer two different questions at once — "what products exist"
and "how much of each do we have, and what's moving." The owner's
follow-up made that split explicit rather than continuing to layer more
columns/filters/actions onto one table.

- **New `stock` route/nav-item, sibling to `inventory` under the same
  collapsible Inventory group** (`Items | Stock | Transaction Log |
  Manage`). New `static/views/stock.html` + `static/js/stock.js`, wired
  into `app.js`/`common.js`'s `VIEW_NAMES`/`ROUTE_PERMISSION_MAP`/
  `NAV_GROUP_ROUTES` exactly like every other inventory sub-route — no new
  permission section, `stock` reuses the same `inventory_items:view` gate
  `inventory` already used, since it's the same underlying data just
  presented two ways.
- **No backend changes at all.** Every endpoint Stock needs
  (`/inventory/items/summary`, `/inventory/units`, `/inventory/reorder-
  level`, `/inventory/items?category_id&sku`, `/inventory/items/{id}`,
  `/inventory/sku-history`) already existed from earlier rounds — this
  split is purely a frontend reorganization of which page renders which
  part of the same data.
- **Items (`inventory.js`) is now catalog-only:** Name, SKU, Category,
  Actions (Edit product / Delete product — unchanged from the Fifth
  round's `PATCH`/`DELETE /inventory/products`). No quantities, cost,
  filters, or the Add Item entry point live here any more — it's a
  low-traffic reference page now, matching how rarely Categories/Locations
  get visited on the Manage page.
- **Stock (`stock.js`) is now the operational page and owns everything
  else:** the Qty/Unit Cost/Total Value/Reorder Level columns, the
  Category/Status/Custody filters, the View action (stock-history
  drill-down — unit list or the flat Consumable history), the Add Item
  wizard (Add Product / Add Unit), and the Issue Materials / Return
  Materials header buttons — all moved here verbatim from `inventory.js`/
  `inventory.html`. "New product" (Step 1 of the Add Item wizard) still
  creates the catalog entry that shows up on the Items page; only its
  entry point moved.
- **Two new shared exports on `inventory-common.js`:** `showTypeFields`
  and `buildProductList`, both lifted out of the old `inventory.js`
  unchanged. Both pages need them now — Items' Edit Product form uses
  `showTypeFields`; Stock's Add Unit/Edit Unit forms use `showTypeFields`
  and its Add Item wizard's product search uses `buildProductList`; Items'
  own catalog table also renders directly off `buildProductList()` rather
  than a new endpoint, since "one row per product" is exactly what that
  helper already computes from the all-items cache both pages already
  load.
- **Table/filter DOM ids were renamed, not reused, to keep the two pages
  unambiguous:** Stock's summary table is `stock-table`/`stock-thead`/
  `stock-rows` with `stock-category-filter`/`stock-status-filter`/`stock-
  custody-filter`; Items kept its original `inventory-items-table`/
  `-thead`/`-rows` ids since it's still, at its core, the items list — just
  a narrower one now. Every modal that used to live on the Items page and
  now lives on Stock (Add Item wizard, Edit Unit, unit list, SKU history,
  per-unit detail) kept its existing ids unchanged, since nothing on the
  new Items page references them any more and moving markup wholesale was
  simpler than a rename with no benefit.

**Seventh round — Items and Stock converge on the same Qty/Cost/Value row
shape; View becomes one shared component; quantity fields come out of
every edit form.** The Sixth round's catalog-vs-operational split (Items =
no qty/cost, Stock = qty/cost but View-only) didn't survive contact with
the owner's actual daily use — both pages turned out to need the same
holdings data, just filtered/actioned differently. This round is a "final
column spec" from the owner, taken literally rather than reconciled
against the prior round's reasoning. An initial pass over-generalized the
pattern and also gave Stock Edit/Delete — the owner corrected that
immediately after: Stock stays View-only, same as the Sixth round always
intended; only the row *data* converged, not the action set.

- **Items and Stock now render (almost) the same row, but not the same
  Actions.** Both fetch `GET /inventory/items/summary` and show Name/SKU/
  Category/Qty/Unit Cost/Total Value. Items adds a Location column, is
  filtered by Category only (always the combined on-hand+deployed total,
  i.e. `total_qty`/`total_value`), and its Actions column is View/Edit/
  Delete. Stock has no Location column, adds Status and Custody filters
  (unchanged from the Sixth round — client-side, switching between
  `on_hand`/`deployed`/`total` per row with no re-fetch), and its Actions
  column is **View only** — no Edit/Delete on Stock, full stop. Header
  actions diverge the same way they did in the Sixth round: Items owns the
  Add Item wizard (Add Product creates the catalog entry that shows up
  here; Add Unit is logged as an In transaction), Stock owns Issue
  Materials / Return Materials.
- **New `location_names` field on `get_items_summary()`** (`db/
  inventory_reports.py`) — a comma-joined `STRING_AGG(DISTINCT
  inventory_locations.name)` scoped to each type's own on-hand population
  (Asset: `asset_status != 'Deployed'`; Quantity: no split, all active
  rows; Length: `length_status = 'In Stock'`), left-joined so a null
  `location_id` doesn't drop the row. A SKU with on-hand units split across
  stores shows all of them, comma-separated, rather than picking one
  arbitrarily. Deployed assets aren't counted — "where this SKU is
  shelved" isn't a meaningful question for a unit that's out at a site.
- **View is a single shared implementation in `inventory-common.js`,
  called identically by both `inventory.js` and `stock.js`** — not two
  copies, per the owner's explicit requirement. `openStockHistory({
  categoryId, sku, trackingType, categoryName, onChanged})` is the one
  entry point either page's View button calls; it branches by Core exactly
  as before (unit list for Asset/Cable, flat running-balance history for
  Consumable). `openEditProductModal(categoryId, skuOrSpec, onSaved)` and
  `deleteProduct(categoryId, skuOrSpec, name, onDeleted)` live in the same
  shared module and are written the same reusable way (they accept a
  refresh callback exactly like `openStockHistory` does), but **only
  `inventory.js` (Items) actually calls them** — Stock's Actions column is
  View-only, so `stock.js` doesn't wire up Edit/Delete buttons at all and
  doesn't call `initEditProductModal()`. The functions being shareable
  doesn't mean both pages use them; it means if Stock ever needs Edit/
  Delete again, it's a two-line change, not a rewrite. All the modals'
  *markup* still lives in one physical HTML file each (Add Item wizard +
  Edit Product in `inventory.html`; Edit Unit + unit list + SKU history +
  item detail in `stock.html`) — that's just which fragment happens to
  declare the tags; every fragment is injected at boot regardless of
  active route, so it's addressable from either page's JS either way.
- **Callback-passing over pub-sub for the "refresh after I changed
  something" problem.** Since the shared modals are singletons but View is
  wired from both pages at boot, `initUnitListModal`/`initUnitDetailModal`/
  `initSkuHistoryModal` each guard themselves with a module-level "already
  initialized" flag so calling them twice — once from `inventory.js`'s
  `initInventory()`, once from `stock.js`'s `initStock()` — doesn't
  double-bind the same form's submit listener. (`initEditProductModal` has
  the same guard for when it's needed elsewhere, but only Items calls it
  today.) Refreshing whichever page is currently showing the edited/
  deleted row is handled by passing that page's own refresh function in at
  open-time (`onSaved`/`onDeleted`/`onChanged`), not by a global event bus
  — simpler, and each page only refreshes itself.
- **No manual quantity edits anywhere, full stop.** The Edit Unit modal
  (`inventory-common.js`'s `initUnitListModal`) no longer offers
  `quantity_on_hand` (Quantity Core) or `length_remaining` (Length Core) as
  form fields at all — both are exactly "the Qty number" the owner meant.
  The backend's `PATCH /inventory/items/{id}` still requires
  `quantity_on_hand` on every Quantity-type update (`routers/
  inventory.py`'s existing validation) and defaults `length_remaining` to
  `length_received` if omitted, so the submit handler now carries both
  through unchanged from the existing item rather than reading them from a
  (removed) input, preserving the current value without offering a way to
  change it from this form. The sanctioned correction path is unchanged
  and already existed: an Adjustment transaction via the Transaction Log,
  which takes a signed correction and leaves an attributed log row instead
  of a silent overwrite (`routers/inventory.py`'s `Adjustment` branch,
  confirmed already implemented and tested before removing the direct-edit
  fields). `length_received` (what a cut originally was, a one-time data
  fact) and Unit Cost stay editable — neither is a live stock-quantity
  figure. Edit Product was never a quantity-editing surface to begin with
  (product-level fields only), so no change was needed there beyond making
  it shared.
- **Reorder Level has no editable UI anywhere as of this round, on the
  owner's own "final column spec"** — neither Items nor Stock's column
  lists include it, so the click-to-edit affordance built in the Third and
  Sixth rounds was removed from both (its CSS too). `PATCH
  /inventory/reorder-level` and the underlying `inventory_sku_thresholds`
  table are untouched and still work; Reports' SKU Summary still displays
  it read-only with a "Low" badge (unaffected — that reads `get_sku_
  summary()`, a different function). This is flagged here deliberately
  rather than silently resolved: if Reorder Level needs an editable home
  again, that's a decision for the owner to make explicitly, not a gap to
  paper over by guessing where it should go. (Resolved next round — see
  the Eighth round below.)

**Eighth round — Reorder Level gets a home again: settable at creation on
Add Product, editable afterward on Stock, plus a live Low badge.** Direct
follow-up answering the gap the Seventh round flagged rather than guessed
at.

- **One field, two entry points, same value — no separate "set it later"
  flow.** A new optional Reorder Level input sits on the Add Item wizard's
  Step 1 (Add Product; `add-item-product-reorder-level` in
  `inventory.html`), alongside SKU/Name/Make-Model/Spec-Capacity/Supplier/
  UoM. Left blank, nothing is written (no threshold row gets created, same
  as any product that's never had one set). Filled in, `inventory.js`
  PATCHes `/inventory/reorder-level` once — after the *first* unit saved
  in the sitting only, gated by `!addItemHasSavedInSitting` checked before
  it flips true, since Reorder Level is product-level and re-sending it on
  every "+Add another unit" in the same sitting would be redundant, not
  wrong, but pointless. This reuses the exact endpoint Stock's edit-on-
  click already called — `set_reorder_level()` (`db/inventory_reports.py`)
  is an `INSERT ... ON CONFLICT (category_id, sku_or_spec) DO UPDATE`, so
  "create with a value" and "edit an existing value" are the same upsert,
  not two code paths. The "Existing product" wizard path doesn't show this
  field at all (it skips Step 1 entirely) — for an existing SKU, Stock's
  edit-on-click is the only entry point, exactly as before.
- **Reorder Level is back on Stock as a real column** (`stock.js`'s
  `renderStockTable`/`reorderLevelCell`/`startEditingReorderLevel` — the
  same click-to-edit affordance the Third and Sixth rounds built, restored
  verbatim along with its CSS in `inventory.css`). Not added to Items —
  the owner's instructions for this round only mentioned Stock, and Items
  still has no Status/Custody-style operational columns beyond what the
  Seventh round's final spec already gave it.
- **The Low badge is computed live, against whatever Qty is already on
  screen** — `stock.js`'s `qtyCell(row, qty)` checks `row.reorder_level !=
  null && qty <= row.reorder_level` at render time using the same `qty`
  value `qtyAndValueFor()` already resolved for the row (which itself
  already reflects the Status filter), not a separately-fetched or cached
  flag. A row with no threshold set, or comfortably above it, renders
  nothing — only the exceptions get flagged, so the column doesn't fill up
  with "OK" noise. Reuses the `.status-pill.low-stock` class and "Low"
  label Reports' SKU Summary already established (`inventory-reports.css`)
  rather than inventing a second visual language for the same concept.

**Ninth round — Add Unit's "Existing product" path gets a batch-quantity
mode, Asset Core only.** The single-unit Step 2 form previously had to be
repeated once per physical unit even when several arrived together (5
splicing machines, a fresh box of enclosures) — the owner asked for a
"quantity to add" number instead, with the shared fields (Location, Status,
Unit Cost, Install Date, Notes) entered once for the whole batch.

- **Scoped to Asset Core only** — deliberately, not applied across all three
  tracking types. Consumables Core's "batch total" already lives in one
  row's `quantity_on_hand`; there's nothing to multiply into N rows. Cable
  Core's units are individually distinct cuts/reels with their own lengths,
  so a "quantity" of them doesn't mean anything without also asking N
  different lengths, which isn't what was asked for. Only Asset Core's units
  are physically identical siblings of the same product, which is exactly
  the case a batch add helps. `selectExistingProduct()` in `inventory.js`
  branches on `item.tracking_type === "asset_serialized"` to
  `enterBatchStep()`; every other type still goes to the existing
  `enterUnitStep()`. The "New product" path (Step 1 + Step 2 combined) is
  untouched either way — it's a one-unit-at-a-time start for a brand-new
  SKU, not this flow.
- **New endpoint, not a variant of the existing one** — `POST
  /inventory/units/batch` (`InventoryUnitBatchCreate` in
  `routers/inventory.py`) takes `quantity` plus the same shared unit-level
  fields `POST /inventory/units` takes (minus `serial_number`, which it
  generates), 400s if the category isn't Asset Core or `quantity < 1`.
  `db/inventory_transactions.py::add_unit_batch()` inserts all `quantity`
  item rows and their matching `In` transaction rows in **one connection,
  one commit** (same atomicity as `issue_cart`/`return_cart` — a mid-batch
  failure shouldn't leave 2 of 5 units silently created), all sharing one
  `event_group_id` so the Transaction Log renders the whole arrival as one
  event, same grouping `add_unit()` already used for a manual "+ Add another
  unit" sitting.
- **Auto-assigned serial placeholder, never colliding with an existing
  unit under the SKU.** `serial_number` is a required column for Asset Core
  (`_validate_type_fields`), so each generated unit gets `{sku}-{NN}` (e.g.
  `SPL-001-01`, `SPL-001-02`, ...). `db/inventory_items.py::next_serial_seq()`
  scans every `serial_number` already under that `(category_id, sku)` —
  active or not, since a deactivated unit's identifier still shouldn't be
  reissued — pattern-matches `{sku}-(\d+)$`, and starts one past the
  highest match found. This is the same internal-Asset-ID-vs-manufacturer-
  serial split the unit list drill-down already draws (`inventory-
  common.js`'s comment: "Asset ID" is the row's own `id`; `serial_number` is
  the manufacturer's) — the placeholder fills the required
  `serial_number` column with a system-controlled value standing in for a
  manufacturer serial that isn't entered yet.
- **Batch Review step replaces the single-unit form's per-unit fields**,
  not an extra confirmation dialog. After the batch POST succeeds,
  `renderBatchReviewTable()` lists every created unit (its real DB `id` as
  Asset ID) with an editable Serial Number input and Location/Status
  selects, defaulted to what the batch submitted. Each field commits
  immediately on blur/change via a plain `PATCH /inventory/items/{id}` call
  (`saveBatchReviewRow()`) — same click-to-edit-and-save pattern as Stock's
  Reorder Level column — rather than one "Save all" button, so a click away
  partway through the list doesn't lose rows already corrected. This is
  genuinely just the existing per-unit edit endpoint called in a loop, not
  new backend surface — the batch endpoint's only job is fast creation with
  reasonable defaults, and per-unit correction reuses what already existed.

**Tenth round — four small owner-review fixes: statuses, dates, pagination,
Edit Product's field set.**

- **`Spare — In Storage` dropped from `ASSET_STATUSES`** (now five values:
  Active, Deployed, Faulty, In Repair, Decommissioned). It overlapped almost
  completely with Active (both meant "in stock, not deployed"), and having
  two statuses for the same state invited inconsistent use — a designated
  spare is better tracked via a note on the unit than a status value of its
  own. `routers/inventory.py`'s `ASSET_STATUSES` set is the single source of
  truth `_validate_type_fields` checks against; every `<select>` that offers
  this list (Add Unit, the batch form, Edit Unit, `inventory.js`'s Batch
  Review status dropdown) had its own hardcoded option removed to match —
  Return Materials' own status list never included it in the first place.
- **Every date input defaults to today**, not blank. `todayDateString()`
  (`inventory-common.js`) builds a local-timezone `YYYY-MM-DD` (not
  `toISOString()`, which is UTC-based and can read as the wrong day
  depending on the viewer's timezone/time of day) — applied to Install
  Date and Expiry Date on Add Unit, the batch form's Install Date, and Edit
  Unit's Install Date/Expiry Date (there, only when the item doesn't
  already have one set — an existing value is never silently overwritten
  with today). `form.reset()` reverts a field to its HTML default value,
  not whatever was last assigned via `.value`, so `inventory.js`'s
  `applyAddItemDateDefaults()` has to re-run after every `.reset()` call
  (wizard open, "+ Add another unit"), not just once.
- **Unit list drill-down (View on Items/Stock) is now paginated AND
  height-capped.** `inventory-common.js`'s `renderUnitList()` gained the
  same client-side page-size-10/20/50/100-plus-Prev/Next convention as the
  per-unit Logs tab (`renderUnitDetailLogs`) and Reports' drill-downs,
  reusing the same `.dashboard-logs-page-size`/`.dashboard-logs-page-nav`
  CSS. An initial pass added only those controls, wrapped in `.table-scroll`
  (the generic table wrapper used everywhere else in this domain) — which
  turned out not to cap height at all on desktop (`overflow-y: visible`,
  unconditionally, in `common.css`; a `max-height` only exists inside that
  rule's phone-only media query), so a page size of 100 would still grow
  the modal to fit 100 rows. Pagination controls alone don't bound a
  table's rendered height. Fixed by swapping the wrapper to
  `.dashboard-logs-scroll` — the exact fixed-height (`max-height: 360px`),
  internally-scrolling container the Battery logs table already uses
  (`static/views/dashboard.html`'s `#view-logs-list`/`#stat-detail-list`) —
  rather than inventing new height-capping CSS. That container's `overflow-
  x: hidden` assumes a static header row outside the scroll box (the
  Battery pattern renders header and body as two separate tables so the
  header never scrolls); this list's header is dynamic (Asset vs Cable
  columns), so replicating that exact two-table split wasn't practical.
  Instead the header stays inside the one scrolling table, pinned via
  `position: sticky; top: 0` on `.unit-list-table-compact thead th` — the
  same sticky-header technique `.table-scroll thead th` already uses for
  its own (phone-only) scroll case, just applied unconditionally here.
  Verified via Playwright with a 40-unit batch at page size 100: the scroll
  container's rendered height stays ~360px regardless, its `scrollHeight`
  exceeds that (real overflow, not just padding), and the header's
  bounding rect stays pinned to the container's top after scrolling.
  Rows are also visually denser than the Items/Stock tables
  (`.unit-list-table-compact` in `inventory.css`, reduced `td`/`th` padding
  and font-size) since this list is scanned many-rows-per-SKU rather than
  one-row-per-SKU — a deliberate visual distinction, not an oversight.

**Eleventh round — Add/Edit Unit's Location becomes a free-typed
autocomplete, and Unit of Measure is dropped from Add/Edit Product.**

- **Location, identical pattern to Site.** Add Unit (single and batch) and
  Edit Unit's Location field changes from a fixed `<select>` (scoped to
  `is_store=true`, and only ever able to show "Main store" — the one row
  set manually per Addendum 2, since Locations has no management screen)
  to a free-typed `<input list=...>` + `<datalist>`, exactly Issue/Return
  Materials' Site field's own markup shape. The only behavioral difference
  is which half of the `is_store` split it resolves against.
  `db/inventory_locations.py`'s `get_or_create_location_by_name()` gained
  an `is_store` parameter (default `False`, preserving Issue/Return's
  existing behavior unchanged) — and critically, `is_store` now gates the
  **match** query too, not just the insert. Before this round the match
  ignored `is_store` entirely, so a store and a job site sharing a name
  would've silently resolved to the same row; now each field's
  autocomplete can only ever match or create within its own half of the
  table, verified via a same-name-different-namespace test (a job site
  "S11 Shared Name" created through Issue, then a unit's Location field
  typed with the identical string, resolves to a *separate* new
  `is_store=true` row, not the job site's id).
- **Resolution is server-side**, mirroring `issue_cart`'s
  `site_location_name` → `site_location_id` pattern exactly: a new
  `_resolve_unit_location_id(location_id, location_name)` helper in
  `routers/inventory.py`, called at the top of `POST /inventory/units`,
  `POST /inventory/units/batch`, and `PATCH /inventory/items/{id}` (the
  three Add/Edit Unit endpoints), overwrites the request model's
  `location_id` in place before the existing `_fields_for_type`/`dict()`
  flow runs unchanged — `location_name` isn't in `_UNIVERSAL_ITEM_FIELDS`,
  so it's automatically dropped from what actually gets written, no extra
  filtering needed. `location_id` stays accepted (and takes over when no
  name is sent) so Batch Review's own per-row Location `<select>` —
  deliberately left as a plain dropdown, out of this round's scope, since
  it's correcting one already-created unit against already-known stores,
  not typing a brand-new one — keeps working unchanged.
- **Location suggestions refresh after every successful save**, not just
  on page load — `loadInventoryLocations()` + `populateLocationDropdowns()`
  now run after Add Unit, the batch form, and Edit Unit all succeed, same
  as Issue Materials already does for its own Site field after a cart
  submits. Without this, a store typed for the first time wouldn't appear
  in the suggestion list for the very next unit added in the same
  "+ Add another unit" sitting.
- **Unit of Measure dropped entirely from Add Product (Step 1) and Edit
  Product** — not reassigned elsewhere (unlike Make/Model/Supplier's move
  to unit level last round), since nothing currently needs it. The
  `unit_of_measure` column and `_UNIVERSAL_ITEM_FIELDS` entry stay
  untouched (Add/Edit Unit never exposed it either, so removing it here
  leaves zero UI surface writing it anywhere) — if a real need surfaces
  later (e.g. packets vs. pcs for a specific consumable), it can be added
  back then. `ProductUpdate`/`update_product()` in `routers/inventory.py`
  had `unit_of_measure` removed from their field list entirely, not just
  the form input — leaving it in would have meant every Edit Product save
  silently nulled the column out on every unit in the SKU group, since the
  frontend would stop sending a value the backend still expected.

**Twelfth round — Cable Core's SKU/Spec conflict, Cable excluded from
Return Materials, and a Pending-row visual marker.**

- **Cable Core has no separate SKU concept — Spec is now the sole
  identifying field on Add/Edit Product, enforced server-side.** Both
  forms previously showed SKU and Spec/Capacity as two separate fields for
  every Core, including Cable, which caused the Spec value to get typed
  into SKU by mistake (there being nothing else to put there) — this
  wasn't a UI-only cosmetic bug, since `sku_or_spec` already computes
  `spec` for Length type everywhere in reporting/grouping, so a
  drifted-from-spec `sku` column was silent junk data.
  `inventory.js`'s Add Product category-change handler and
  `inventory-common.js`'s `openEditProductModal()` now hide (not just
  make optional) the SKU input specifically for `inventory_length`
  categories — same "hidden AND un-required" pattern the Spec field's own
  Chromium `required`-in-a-hidden-block fix already established. But the
  real fix is server-side: `_validate_type_fields()`'s Length branch
  (covering `POST /inventory/items`, `POST /inventory/units`, and
  `PATCH /inventory/items/{id}`) now force-sets `fields["sku"] =
  fields["spec"]` unconditionally, and `update_product()`'s Length branch
  does the same with `payload.spec` — so this invariant (`sku === spec`
  for every Length-type row) holds regardless of what any caller sends,
  not just what the current frontend happens to collect. This incidentally
  fixed several other display spots that read `.sku` directly without a
  Length-aware branch (`itemPickerLabel()`, the per-unit detail modal's
  header) — they needed no code changes at all, since the data they read
  is now always correct by construction. Also closed a real validation
  gap while in there: `spec` was never actually checked as required
  server-side for Length creates before this (only the frontend's
  `required` attribute enforced it) — now a missing spec 400s cleanly
  instead of writing `sku = None` and hitting the column's `NOT NULL`
  constraint as a raw 500.
- **Cable/Length items are excluded from Return Materials entirely** — not
  a missing-field gap, but the wrong screen for cable altogether. Every
  outcome for an issued cut (fully used, partially used, job cancelled
  and never touched) is already expressible through Reconciliation's
  Length Used/Length Returned fields (a Length Returned equal to the full
  issued amount just closes the cut out clean with no new `-R` remainder
  reel — no special "cancelled" case needed). `return-materials.js`'s
  `searchItems()` filters out `tracking_type === "inventory_length"`
  before the text-match runs, so a cable cut can't be found by name, SKU,
  spec, or anything else through that screen. Backed by a server-side
  guard too, not just the search filter: `_plan_return()` in
  `routers/inventory.py` — shared by both `POST /inventory/return` and the
  generic Log Transaction form's Return option, the only two places that
  ever call it — now 400s outright for a `inventory_length` item,
  pointing at Reconciliation instead. This protects the admin/manual Log
  Transaction form too, which has no item-type-aware picker of its own
  and could otherwise still attempt a cable Return despite the dedicated
  screen excluding it.
- **Transaction Log rows showing "Pending" get a visual accent** — a 3px
  red left border, so an open cable cut is scannable while scrolling a
  long log without reading the Action column on every row.
  `inventory-log.js`'s `renderLogTable()` adds an
  `inventory-log-row-pending` class using the exact same condition
  `displayAction()` already computes the "Pending" label from
  (`row.action === "Out" && row.status === "open_pending"` — no new data,
  purely a display-time flag). The styling itself
  (`.inventory-log-row-pending` in `inventory-log.css`) reuses Check
  Sites' `.site-row-offline` technique verbatim — a border set directly on
  the `<tr>`, which only paints because this table (like Check Sites',
  unlike the dashboard's logs table) uses the default
  `border-collapse: collapse` rather than `separate`.

**Thirteenth round — depleted cable reels excluded from Issue Materials.**
A reel/cut at `length_remaining <= 0` (whether from full usage or a
fully-reconciled cut) is out of stock the same way a zero-quantity
Consumable would be — `issue-materials.js`'s `searchItems()` now filters
out any `inventory_length` item that doesn't have `length_remaining > 0`
*before* the text-match runs, so a depleted reel can't be found by name,
SKU, spec, or its own reel id, and never reaches `addToCart`/the cart at
all. This is a search-time filter, not a submit-time rejection — a
depleted reel was never actually issuable (the backend's own
`_plan_issue_line` would reject a cut whose `length_status` is already
`"Out — Pending Reconciliation"`, though that's a different condition than
zero remaining length and wasn't touched here), it just wasn't being
hidden from selection in the first place. Return Materials needed no
equivalent change — cable is excluded from that screen entirely as of the
Twelfth round, depleted or not.

**Fourteenth round — three Transaction Log / Item Logs UI fixes.**
1. *Date column truncation in the per-unit Details/Logs modal* — the 5-column
   Logs tab table (`#item-detail-logs-list`, in `stock.html` +
   `renderUnitDetailLogs()` in `inventory-common.js`) squeezed its Date
   column to 20% width to make room for its extra Notes column (Battery's
   own 4-column Logs tab, by contrast, gives Date 30%), which clipped the
   shared `formatDate()` string. Fixed two ways together: the Date column's
   `colgroup` width grew from 20% to 26% (Notes correspondingly shrank from
   30% to 24%, in both the static header table in `stock.html` and the
   JS-rendered body table's own matching `colgroup` — they have to stay in
   sync since it's a split header/body layout), and a new
   `formatDateTimeShort()` in `inventory-common.js` drops the year for this
   one table specifically (`"Sep 12, 05:21 PM"` instead of
   `"Sep 12, 2026, 05:21 PM"`) rather than changing the shared `formatDate()`
   used elsewhere (Battery logs, Roles, SKU/offcut reports) where the extra
   width isn't a problem. A prior-year record in one unit's own history is
   rare enough that dropping the year here is an acceptable trade.
2. *View action missing hover state* — `.inventory-icon-btn.edit:hover` and
   `.inventory-icon-btn.delete:hover` existed in `inventory.css`, but no
   `.inventory-icon-btn.view:hover` rule did, even though every View button
   (`view-item-btn`, `unit-view-btn`) carries the `.view` class already.
   Added the missing rule, same accent-color treatment as Edit's hover.
3. *Pagination page-size options, and the main Transaction Log page had none
   at all.* The per-unit Logs modal's page-size options changed from
   `10/20/50/100` to `10/30/100` per this round's spec. The main Transaction
   Log page (`inventory-log.html`'s `#inventory-log-table`) had **zero**
   pagination — it rendered every row into a `.table-scroll` wrapper, which
   (same root cause as the Tenth round's unit-list bug) has no `max-height`
   on desktop outside its own phone-only media query, so the table grew
   unbounded as history accumulated. Fixed by swapping the wrapper to
   `.dashboard-logs-scroll` (the same fixed-height container reused
   throughout this phase) and adding real client-side pagination to
   `renderLogTable()` in `inventory-log.js` — module-level `logPage`/
   `logPageSize` (default 10), a `10/30/100` page-size select, and Prev/Next,
   mirroring `renderUnitDetailLogs()`'s pattern exactly. Since only the
   current page's rows are in the DOM, each row's click handler now looks up
   its detail-view index via `logRowsCache.indexOf(row)` rather than a
   render-loop index, so `openTransactionDetail` still resolves the right
   row regardless of which page it's on. Also added `position: sticky` to
   `#inventory-log-table thead th` (in `inventory-log.css`) so the header
   stays pinned while the body scrolls inside the capped container — the
   same technique `.unit-list-table-compact thead th` already uses, since
   this table is likewise one combined header+body table rather than
   Battery's split-table layout.

**Fourteenth round, corrected — no nested scrollbox; page-size reverted to
10/20/50/100.** Point 3 above was wrong on two counts, caught by the owner
before merge. First, the Battery view-modal pattern being copied
(`view-logs-list` in `dashboard.js`) actually uses page-size options
`10/20/50/100`, not `10/30/100` — reverted in both `inventory-log.js` and
`renderUnitDetailLogs()`. Second, and more substantively: the fix wrapped
both tables in `.dashboard-logs-scroll` (a nested 360px `max-height` +
`overflow-y: auto` box), but the owner wants no inner scrollbar at all here
— the table renders exactly the selected page's rows and stops (already true
once pagination slices the rows; that part was correct), and whatever
container holds it scrolls as a whole if the content is taller than the
viewport, rather than nesting a second independent scrollbar inside it.
- Main Transaction Log page: wrapper reverted from `.dashboard-logs-scroll`
  back to `.table-scroll` (no vertical cap — the page's normal document flow
  handles overflow, not the earlier-diagnosed bug from the Tenth round, since
  row count is now genuinely bounded by pagination rather than unbounded).
  The `position: sticky` thead rule added for the scrollbox version was
  removed too — it had no purpose once there's no inner scroll to pin
  against, and risked sticking at the wrong offset under the fixed topbar.
- Per-unit Logs modal: `#item-detail-logs-list` dropped `.dashboard-logs-scroll`
  entirely (plain, unstyled wrapper now). Since it sits inside a
  `position: fixed` modal overlay rather than the normal page, "the page
  scrolls naturally" has no direct equivalent there — so the modal box itself
  now gets `max-height: 90vh; overflow-y: auto` (a new, narrowly-scoped
  `#item-detail-overlay .modal-box` rule in `inventory.css`), meaning a large
  page size makes the whole modal scroll as one unit instead of clipping or
  nesting a second scrollbar. This is scoped to that one overlay's ID, not
  the shared `.dashboard-modal-box-lg` class — Battery's own modals
  (`view-battery-overlay`, `stat-detail-overlay`) still use the original
  nested-360px-scrollbox pattern unchanged, since the owner's correction was
  specific to the inventory Log/Logs tables, not a request to change Battery.

**Fifteenth round — Log Transaction's Item field: search instead of a flat
dropdown.** The generic Log Transaction form (In/Transfer/Adjustment/
Write-off/Return) listed every individual unit in a plain `<select>` — every
serial, every reel — which only gets worse as unit counts grow. Replaced with
the same type-to-filter interaction Issue/Return Materials already use:
`populateLogDropdowns()` no longer builds that `<option>` list at all; the
`<select id="inventory-transaction-item">` became `<input type="hidden">`
(kept under the same id so every existing reader —
`updateAssetStatusVisibility()`, the submit handler — needed zero changes)
paired with a new visible `#inventory-transaction-item-search` text input and
an `#inventory-transaction-item-suggestions` dropdown box, wired up in a new
`initItemSearchField()`/`renderItemSearchSuggestions()`/
`selectTransactionItem()` trio in `inventory-log.js`. Typing filters live;
clicking a suggestion fills the visible label and the hidden id and fires a
synthetic `change` event on the hidden input so the existing asset-status
visibility logic keeps working unmodified; typing anything after a selection
immediately clears the hidden id again, so a stale id can never ride along
under edited text. Submitting with no real selection is blocked client-side
with a message rather than posting `item_id: NaN`.

The underlying matcher (`name`/`sku`/`category_name`/`serial_number`/
`batch_lot`/`cut_reel_id`/`spec`/`location_name`, case-insensitive substring)
was previously duplicated near-identically in `issue-materials.js` and
`return-materials.js` — factored out to a shared
`inventoryItemMatchesQuery(item, query)` in `inventory-common.js` so this
third caller doesn't create a third copy, and so the two existing ones can't
silently drift apart from each other or from this one. Each caller still
applies its own filtering on top: Issue excludes a depleted `inventory_length`
item (Thirteenth round), Return excludes `inventory_length` entirely (Twelfth
round), and Log Transaction excludes nothing — a manual entry may
legitimately need to target any item regardless of current state (e.g.
Write-off against an already-depleted reel). The suggestions dropdown itself
is a new inventory-scoped `.item-search-field`/`.item-search-suggestions`/
`.item-search-option` set in `inventory-log.css`, visually mirroring
`dashboard.css`'s `.move-by-suggestions` pattern (absolute-positioned box
under the input, internally scrollable past ~6 rows) without importing
Battery's battery-domain-named classes, matching the no-cross-domain-import
convention `.inventory-item-tab` already established for the same reason.

**Sixteenth round — Cable Core: Units view vs Reports drill-down, full
split.** A Cable Core reel that's been fully used and reconciled to
`length_remaining = 0` is functionally unlike an Asset being "deployed" — a
deployed asset is still owned and trackable, a depleted reel is consumed
material with nothing left to offer as stock. Three related changes:
1. *Units view excludes depleted reels by default.* `openUnitListModal`/
   `refreshUnitListIfOpen` in `inventory-common.js` now run every fetch
   through a new `filterUnitListDepleted()` (drops any `inventory_length` row
   with `length_remaining <= 0`; Asset/Quantity rows are untouched). This
   only mattered for the reconciled-to-zero case — a *written-off* reel was
   already excluded, since Write-off also sets `is_active = false`
   (`routers/inventory.py`'s Write-off branch) and `get_all_items()` already
   filters `is_active = true`; reconciliation's `original_updates` (in
   `reconcile_cut`) sets `length_status = "Depleted"` but deliberately leaves
   `is_active = true` untouched, since the row still has real transaction
   history and isn't being deleted, just used up. Filtering client-side
   (rather than adding a query param to `GET /inventory/items`) keeps that
   endpoint's existing behavior available as-is for the Reports drill-down
   below, which needs the opposite — everything, unfiltered.
   Because the Units modal can now legitimately end up with zero rows for a
   spec that's fully used up, `category_id`/`sku_or_spec`/`tracking_type`
   moved from being read off `unitListCache[0]` (which broke once the cache
   could be empty) to three persisted module variables
   (`unitListCategoryId`/`unitListSkuOrSpec`/`unitListTrackingType`) set once
   at `openUnitListModal` time; `renderUnitList()` now renders an explicit
   "No units currently in stock." empty-state row (reusing `.dashboard-log-empty`)
   instead of silently leaving the table blank.
2. *Reports' Cable Type Summary drill-down now shows every reel ever
   created for the spec, not just in-stock ones.* `get_cable_drill_down(spec)`
   in `db/inventory_reports.py` dropped both its `is_active = true` and
   `length_status = 'In Stock'` filters (keeping only the category/tracking-
   type/spec match) and now selects `length_status` too. The top-level Cable
   Type Summary rows (`get_cable_type_summary`) are unchanged — that
   function still answers "what do we have" (in-stock only), while the
   drill-down now answers a different, historical question: "what happened
   to every reel we've ever made for this spec, and where did it end up."
   `static/views/inventory-reports.html`'s drill-down modal grew a Status
   column and renamed Location to "Last Known Location" (widened to
   `dashboard-modal-box-lg` to fit the new column — it was a cramped 360px
   box for 6 columns even before this), rendering the raw `length_status`
   value as-is (`In Stock` / `Out — Pending Reconciliation` / `Depleted`)
   rather than collapsing to just two states, since the app shows that same
   raw value as-is everywhere else it appears (the Details tab, the Edit
   form) and a reel genuinely mid-job should read as such rather than being
   forced into one of the other two buckets.
3. *Clicking View on a drill-down row opens the same per-unit tabbed
   Details/Logs modal used everywhere else* (`openUnitDetailModal`) — this
   was already wired up from an earlier round and needed no change; verified
   it still works for a depleted reel specifically (its full Issue →
   Reconciled history renders in the Logs tab same as any other item).

**Seventeenth round — Log Transaction: "In" removed, field set per Action,
Qty/Length stepper, free-typed Transfer locations.** Four related changes,
all scoped to the generic Log Transaction form (`inventory-log.html`/`.js`):
1. *"In" is no longer an option, front- or back-end.* Receiving new stock
   only ever happens through Add Product/Add Unit (`transactions_db.add_unit`/
   `add_unit_batch`), which auto-generates serials/reel IDs, supports batch
   quantities, and writes its own `"In"` row in the same commit — a second,
   manually-typed `"In"` path here risked the same event being recorded two
   structurally different ways. `TRANSACTION_ACTIONS` in `routers/inventory.py`
   dropped `"In"` (now `{"Transfer", "Adjustment", "Return", "Write-off"}`),
   so `POST /inventory/transactions` now rejects it with a 400 even if called
   directly, not just hidden from the dropdown — the whole point was closing
   off the second path, not just hiding it. The now-unreachable `"In"` branch
   in `_plan_transaction` was deleted rather than left dead. Nothing else
   read `TRANSACTION_ACTIONS` or called `_plan_transaction` with `"In"` —
   `add_unit`/`add_unit_batch` build their log rows directly, bypassing this
   endpoint entirely, so historical `"In"` rows and the log's own
   category/action filters (which still list `"In"` — it's filtering
   *reads*, not gating a write) are unaffected.
2. *The form's field set now changes shape by Action*, via a single
   `updateActionFieldVisibility()` in `inventory-log.js` (triggered on both
   Action and Item change, same as the existing asset-status-row logic it
   replaces): Transfer shows To/From location; Adjustment and Write-off show
   neither; Return shows neither (it always resolves to the single default
   store server-side — see the Twelfth/Fifteenth rounds) and shows the
   required asset-status picker only when the selected item is a serialized
   asset, unchanged from before. Site, Activity, and Issued To are removed
   from the markup entirely, not just hidden — none of the four remaining
   actions ever had a "which job" concept; that's what Issue Materials is
   for. The Pydantic model (`InventoryTransactionCreate`) keeps
   `site_location_id`/`activity`/`issued_to_user_id` as optional fields for
   now (the admin-only historical-edit endpoint and the log's own read/
   display path still reference them), they just never get populated by this
   form going forward.
3. *Qty/Length gets a themed custom stepper*, replacing the number input's
   native spinner (rendered as a plain white OS-drawn box against the dark
   theme). Two `.qty-stepper-btn` buttons sit inside a `.qty-stepper`
   wrapper, absolutely positioned over the input's right edge;
   `-webkit-appearance: none` on `::-webkit-inner/outer-spin-button` plus
   `-moz-appearance: textfield` hide the native control in both engines.
   `initQtyStepper()` increments/decrements the parsed value by 1 and fires
   a synthetic `input` event — `step="any"` on the input means
   `stepUp()`/`stepDown()` aren't usable here (they throw on a non-numeric
   step), so the nudge is done by hand. One easy-to-miss gotcha hit during
   this round: `.panel-form button` (`common.css`) sets every button inside
   the form to solid accent-green with black text, and a plain
   `.qty-stepper-btn` selector (one class) loses to that rule (one class +
   one type = higher specificity) regardless of source order — both stepper
   buttons rendered solid green until the selector was changed to
   `.qty-stepper-buttons .qty-stepper-btn` (two classes) to win the
   specificity fight. Caught via a Playwright computed-style check
   (`getComputedStyle(btn).backgroundColor`), not by reading the CSS alone.
4. *Transfer's From/To location fields are free-typed autocomplete*, not
   dropdowns — same `<input list=... >` + `<datalist>` pattern as Add/Edit
   Unit's Location field, reusing that exact is_store-filtered suggestion
   list: `populateLocationDropdowns()` in `inventory-common.js` grew two more
   target ids (`inventory-transaction-to/from-location-suggestions`) rather
   than duplicating the fetch/filter logic. Resolution is also reused
   directly: `InventoryTransactionCreate` gained `to_location_name`/
   `from_location_name`, and `create_inventory_transaction` now calls the
   already-existing `_resolve_unit_location_id(id, name)` (is_store=True,
   `get_or_create_location_by_name` under the hood) on both before
   `_plan_transaction` runs — the same helper Add/Edit Unit's Location field
   already used, not a second implementation of "resolve or create a store
   location." From stays optional (falls back to the item's current
   location, as before); To stays required for Transfer, enforced both by
   the `required` attribute (shown/hidden with the row) and by
   `_plan_transaction`'s existing `to_location_id is None` check.

- **Edit Product's field set shrinks to Category, SKU, Name, Spec/Capacity,
  Unit of Measure — Make/Model and Supplier move down to unit level.**
  Reverses part of the original product/unit split: both fields can
  genuinely vary batch-to-batch or unit-to-unit even under one nominal SKU
  (different suppliers at different unit costs; potentially different
  actual brands under one loosely-defined SKU), so editing them "for the
  whole SKU group at once" via `PATCH /inventory/products` was quietly
  wrong. Spec/Capacity stays at product level — it describes what the
  product fundamentally *is* (e.g. "16-Port," "48V"), which doesn't vary
  per unit the way a supplier or brand can. No schema change: `make_model`
  and `supplier` were already unit-level columns (`_TYPE_FIELDS`/
  `_UNIVERSAL_ITEM_FIELDS` in `routers/inventory.py`) — this is purely
  removing them from `ProductUpdate`/`update_product`'s field list and
  adding them to the unit-level forms (Edit Unit, and Add Unit's Step 2 and
  its batch-mode variant) instead. Edit Unit's submit handler now reads
  Supplier fresh from its own form field rather than carrying `item.supplier`
  through unchanged, and reads Make/Model from a new field within its
  Asset-only block rather than carrying `item.make_model` through — the
  same "unedited fields ride along as-is" convention still applies to
  everything else in that form (SKU/Name/Spec/UoM).

## Inventory: Core system & permissions — settled decisions

Everything above this point in Schema choices is a chronological log of each
round of feedback, in the order it happened. This section is the opposite:
a topic-indexed reference to the Inventory domain's settled decisions,
written so a future reader (human or Claude) can look up *why* something
works the way it does without reading the whole history above, and — just
as important — doesn't re-propose something that was already deliberately
rejected. Nothing here is new; it's all already implemented and live on
`phase-3-ops-inventory` as of this writing.

### Core (`tracking_type`) is permanent once a category has items

A category's Core — `asset_serialized` / `inventory_quantity` /
`inventory_length` — can only change while it has **zero** active items.
`PATCH /inventory/categories/{id}` (`routers/inventory.py`) checks this via
`categories_db.count_active_items(category_id)` and returns a 400 with the
exact item count if it's nonzero.

**This is intentional, not a limitation to lift later.** The three Cores
don't just mean different labels — they mean different row granularity and
disjoint column sets: Asset is one row per serial with `asset_status`/
`assigned_to_user_id`, Consumables is one row per batch with
`quantity_on_hand`, Cable is one row per reel with `length_remaining`. A
category that switched Core with existing rows would leave those rows'
columns meaning something they were never validated or entered for — there
is no safe automatic conversion between "one row per physical unit" and
"one row holding a batch quantity." If a category is ever found with the
wrong Core, the fix is the migration pattern below, never a "convert this
category's Core in place" feature.

### An item/product can only move to a category of the same Core

Same reasoning, one level down. Moving a product (a whole SKU/spec group)
between categories is `PATCH /inventory/products` — `update_product` in
`routers/inventory.py` — which 400s with `"Can't move a product to a
category of a different Core"` if `new_category["tracking_type"] !=
old_category["tracking_type"]`. This is enforced at two layers, confirmed
independently:
- **Frontend:** the Edit Product modal's category dropdown
  (`populateEditProductCategoryOptions` in `inventory-common.js`) only ever
  lists categories matching the product's current `tracking_type` — so an
  incompatible target never appears as an option in the first place.
- **Backend:** the same-Core check above, so the dropdown filter isn't the
  only thing stopping a cross-Core move (a direct API call is rejected too).

A single unit's own `PATCH /inventory/items/{id}` doesn't even accept a
category change — `category_id` is present in the request shape but
explicitly ignored (`InventoryItemUpdate`'s comment: "ignored — an item's
category/tracking_type never changes after creation"). Recategorizing is
only ever a bulk, whole-product operation, never a one-off per unit.

### Heuristic: spotting a miscategorized Core

When auditing an existing category, the concrete signal that was used to
catch a real instance of this (see below): **a SKU under an Asset-core
category where quantity > 1 and the individual units aren't meaningfully
serialized** — i.e. nothing about them is ever tracked or reasoned about
per-unit (no real per-unit status history, no individual assignment, the
serial number if any is just an auto-generated placeholder, not a
manufacturer/asset-tag identifier anyone actually looks up). That's a sign
the business treats them as fungible stock, which is what Consumables Core
is for.

- **Real example — ATB (fixed):** ATB enclosures were sitting in an
  Asset-core category as dozens of individually "serialized" rows, but
  they're batteries/enclosures nobody tracks by serial in practice —
  fungible stock. Fixed by creating a new Consumables-core category ("ATB /
  Passive Enclosures"), adding fresh quantity-tracked rows there, and
  soft-deleting the old serialized rows (their original "In" transaction
  history stays intact for audit purposes — soft delete, not hard delete).
- **Counterexample — FAT / Router (correctly Asset Core):** also many
  units under one SKU, but each one genuinely warrants individual tracking
  — a specific router's status, location and assignment history matters on
  its own. Quantity alone isn't the signal; it's quantity *combined with*
  nothing meaningful happening at the per-unit level.

**The migration path, once a miscategorization is confirmed** (this is the
repeatable pattern, not a one-off script): create the new, correctly-Cored
category; add fresh rows there via the normal `POST /inventory/units` path
(so each gets its own "In" transaction, not a bare row with no history);
soft-delete the old rows via the normal `DELETE /inventory/items/{id}`
path. Never attempt to convert a row's Core in place — there is no code
path for that and none should be built, per the section above.

### Inventory permissions hierarchy

The permissions panel (`static/js/roles.js`'s `PERM_SECTIONS`) settled on
this shape:

```
Inventory (master toggle, inventory_items:view)
├── Inventory Items (toggle, inventory_items:view_items)
│   └── Add / Edit / Delete item (checkboxes)
├── Stock (toggle, inventory_items:view_stock)
│   └── Edit Reorder Level / Issue Materials / Return Materials /
│       View Stock History (checkboxes)
├── Inventory Categories (toggle, inventory_categories:view)
│   └── Add / Edit / Delete category (checkboxes)
└── Inventory Log (toggle, inventory_transactions:view)
    └── Add / Reconcile Cut (checkboxes)

Reports (its own top-level master toggle, reports:view — NOT nested
under Inventory)
```

Two decisions worth calling out so they aren't relitigated:

- **"Add Stock" was a separate toggle at one point and was deliberately
  collapsed into "Add item."** Creating a brand-new product (`POST
  /inventory/items`) and adding a physical unit/batch to an existing one
  (`POST /inventory/units`, `/inventory/units/batch`) both check
  `inventory_items:add` now — there is no separate "add stock" permission
  any more, and re-introducing one as its own toggle would be re-litigating
  a decision made specifically because the two-permission version was more
  granularity than this app actually wants.
- **Reports is deliberately independent, not nested under Inventory**,
  even though it only ever shows inventory data. It's a plain top-level
  master toggle — same shape as Users/Roles/Inventory's own master, not a
  child of it — specifically so a role can see Reports without needing
  Inventory Items access, or vice versa. Every report endpoint in
  `routers/inventory.py` (`/inventory/sku-summary`, `/inventory/cable-
  summary` + its drill-down, `/inventory/offcuts` + its drill-down) checks
  `reports:view`, not `inventory_items:view`.

**Every toggle here gates real UI/route access, not just what the
permissions panel shows** — this was a real, previously-existing gap, not
an assumption:
- `common.js`'s `ROUTE_PERMISSION_MAP` maps each route name to the
  `(section, action)` pair that must be `true` to reach it (e.g.
  `inventory: ["inventory_items", "view_items"]`, `stock:
  ["inventory_items", "view_stock"]`, `"inventory-reports": ["reports",
  "view"]`).
- `NAV_GROUP_MASTER_PERMISSION` additionally requires the Inventory
  master's own permission for the *whole* grouped nav section to show at
  all, on top of each sub-item's individual permission — mirrors the
  permissions panel's own master-toggle-collapses-everything cascade.
- Critically, `dispatchRoute()` re-checks `isRouteAllowed(name)` on
  **every** navigation, not just once at login — `applyPermissionVisibility()`
  hiding a nav link is cosmetic on its own; a hash typed directly into the
  address bar (or set via the console) would otherwise still reach a denied
  view. A denied navigation redirects to `cachedFirstAllowedRoute` instead.
  This fixed a real gap found during testing (a Stock-only role could reach
  `#/inventory` via direct hash navigation despite the nav link being
  hidden) — the fix generalizes to every route, including Reports.

### Items page Qty: on-hand, not total-owned

The Items page's Qty column shows `on_hand_qty`, not `total_qty`
(`on_hand_qty + deployed_qty`) — this was a deliberate reversal of an
earlier decision. `db/inventory_reports.py`'s `get_items_summary()`
computes both: for Asset Core, `on_hand_qty` = active rows where
`asset_status != 'Deployed'`, `deployed_qty` = active rows where it
**is** `'Deployed'`. Showing the combined total by default read as the app
under-reporting how much stock had actually been issued out — a deployed
router still "existing" doesn't mean it's available to hand out today, and
"Qty" on an inventory page reads as "how much can I use right now."

**Applies to Asset Core and Cable Core, not Consumables** — Consumables
has no separate deployed bucket at all (issuing a consumable decrements
`quantity_on_hand` in place rather than moving stock to a tracked "out"
state), so `deployed_qty` is hardcoded to `0` for that Core and
`on_hand_qty` already equals the true live total.

The Stock page is **unaffected** — it kept its own explicit Status filter
(All / In Store / Deployed, `stock.js`'s `stockStatusFilter`), which
already let a user choose which number to see; the Items page had no such
filter and was hardcoded to the total, which is what actually needed
fixing. Total Value on the Items page follows the same on-hand basis
(`on_hand_value`, not `total_value`) — showing a Qty of 33 next to a Value
priced for 50 units would have been its own, new inconsistency.

### Lesson: a CSV handed to you may be an export, not new data

Not a code decision, but worth recording so it isn't repeated: asked to
"import" `items.csv`/`stock.csv` into the database, the files turned out to
match the live database exactly, row for row — they were CSV *exports*
(the app has an export feature on the Items/Stock/Reports/Log pages, via
`inventory-common.js`'s `exportRowsToCsv`; it has no CSV *import* feature
anywhere) rather than a new external data source. One of the tells:
`items.csv`'s Location column held values like `"Main store, Ndambaki"` —
literally `get_items_summary()`'s comma-joined `location_names` display
field, not a real single location a row could actually hold.

**Rule going forward:** before writing a CSV's contents into the database,
diff it against the current live state first (matching on category + SKU
is enough, as done here). There is no upsert-by-SKU import path in this
app — blindly inserting rows from what turns out to be a report export
would have created duplicate item rows on top of already-correct data.

## Infra / hosting choices

Backend: FastAPI (`main.py` + `routers/`), served by Uvicorn. Frontend:
plain HTML/CSS/JS (ES modules), no build step, served as static files by
FastAPI's own `StaticFiles` mount — no separate frontend host or bundler.

Database: PostgreSQL. `db/connection.py`'s `get_connection()` picks between
two paths based on whether `DATABASE_URL` is set: if it is, connects to that
(Supabase, per the code comment, with `sslmode="require"` — the production
path on Render) — if not, falls back to a hardcoded local connection
(`battery_tracker` DB on `localhost:5432`) for local dev. No ORM — every
query is raw SQL via `psycopg2`, one connection opened and closed per
function call (no pooling, no shared/long-lived connection) — consistent
with a small, low-concurrency internal tool.

Auth: JWT (`python-jose`), `SECRET_KEY` from an env var with a hardcoded
dev fallback (`routers/auth.py` — the fallback is explicitly flagged in
comments as "change before deploy"). Tokens carry `role`/`role_id` directly
in the payload rather than requiring a DB lookup on every request.

### Monitoring ingest auth — an intentional exception

`POST /monitoring/ingest` (`routers/monitoring.py`) does NOT use the
user-JWT dependency (`get_current_user`) that every other endpoint uses.
This is deliberate, not a hole: the caller is a RouterOS script, which
cannot hold or refresh a JWT. It authenticates with a static shared secret
in an `X-Ingest-Token` header, checked with `hmac.compare_digest` against
the `MONITORING_INGEST_TOKEN` env var (never in the repo). With no token
configured the endpoint returns 503 — it fails closed. Implemented as a
dependency on that one route rather than global middleware, so no other
request path is touched by it.

Every other monitoring endpoint (4.4 onward) is a normal user-facing
route and uses JWT + `user_has_permission` like the rest of the app.

Bad *data* on this endpoint is never a 4xx: unknown VLANs, unknown PPPoE
names and unparseable bodies go to `ingest_quarantine` and return 200,
because the router has no retry and a rejection is silent permanent loss.
Quarantine rows are deduplicated while unresolved, since `/ppp active`
includes home customers that will never be monitored sites.

### Battery-recommendation read — the other intentional isolation exception

`db/monitoring_alerts.py` reads `batteries`/`battery_movements` to pick
which battery to recommend on a down-alert escalation (lowest recent
removal count, confirmed `charge_status`). PHASES.md's isolation
invariant otherwise restricts monitoring to reading `locations` and
nothing else — this is a documented, deliberate amendment (PHASES.md,
2026-09-23), not drift, with the same one-way shape as the revenue
ranking signal below: **read-only, monitoring depends on the battery
domain, never the reverse.** `db/batteries.py`, `routers/batteries.py`
and the battery UI have zero knowledge monitoring exists — no column,
no import, no awareness added on that side. Dropping every monitoring
table still leaves the battery domain fully intact and passing, which is
what the isolation invariant's proof obligation actually protects; this
exception only ever runs the dependency the other way.

Revenue is used as an internal ranking signal for battery priority
across multiple simultaneously-down sites, but per 4.4 never appears as
a figure in an alert body — a message can say a site is "recommended
first", never why in KES terms. Ranking by revenue internally and
printing revenue are two separate rules; this exception touches only
the first.

### Revenue attribution — durable signal first, moment-in-time second, guess last

A hotspot sale (`db/monitoring.py`'s `_record_revenue`) is booked once,
keyed on `(hotspot_username, expiry_seen)` — the router's `users` list is
re-sent in full every 5 minutes and the unique index is what makes that
free. Which site it belongs to is resolved by trying progressively weaker
signals, and whichever resolves is recorded as `origin_vlan_id`
(migration 0010) even when it doesn't map to a `monitored_sites` row yet —
"we knew the VLAN, it just isn't a registered site" is a distinct,
fixable state from "we had no idea", and collapsing the two would make a
`monitored_sites` gap look like a code bug.

1. **The account's own hotspot server VLAN.** Durable — true at 3am with
   nobody connected — because servers here are one per VLAN, named
   `hs-v<N>`, parsed off the router's own `/ip hotspot` server list.
2. **For an account bound to the shared `all` server** (no server VLAN of
   its own — a mix of comped support accounts and some real paying
   customers): **its DHCP lease's VLAN**, matched by the MAC already
   sitting in the account's own `Exp: ... | MAC: ...` comment. A lease
   here lasts up to 24h and outlives the hotspot session itself, and a
   device needs an IP before it can reach the payment page at all, so this
   is almost as durable as tier 1 in practice, just one hop further from
   the account record.
3. **Seen actively sessioned on a VLAN in the same heartbeat.** A
   photograph, not a fact — correct when it fires, but a short pass
   bought and finished between two polls is simply not in it. This was
   the *only* signal before 2026-09-22 and left 53 sales (KES 840) that
   single day permanently unplaced, which is what prompted tiers 1 and 2.
4. **The buyer's last known site.** A guess, marked `attribution =
   'inferred'` so it is never confused with a real match.

**A sale is only booked once, but "unplaced" is not permanent.** Every
heartbeat — not just the ones carrying a fresh `users` list —
`_backfill_unplaced` re-checks anything still unplaced against whatever's
been learned since, run twice with two different windows sized to what
each source of evidence is worth: an active-list sighting (tier 3, a
moment) reaches back 30 minutes; the account's own resolved VLAN (tiers
1/2, a standing fact) reaches back 24h, matched to the DHCP lease
lifetime. The tradeoff this accepts deliberately: an `all`-bound account
that sold at one site and has since moved is backfilled to where it is
*now*, not where it paid — judged better than leaving it unplaced, since
`all` accounts are a minority and fixed-location hotspot customers rarely
move mid-day.

What neither tier nor backfill can recover: an account that expires and
is deleted from the router before ever being placed has no MAC and no
lease left to read — this is written off, not chased, since there is
nothing left on the router describing where it came from.

Time: every connection runs `SET TIME ZONE 'UTC';` right after connecting
(`get_connection()`), so every naive `timestamp without time zone` column
is unambiguous — `utc_iso()` appends a literal "Z" when serializing so the
frontend can tell it's UTC. Business-logic time (Check Sites' 8am–8pm
active window, movement "since" display) compares against East Africa
Time instead, via a fixed-offset `timezone(timedelta(hours=3))`
(`db/connection.py`'s `EAT`/`now_eat`/`to_eat`) rather than the IANA
`Africa/Nairobi` zone — EAT has no DST, so a fixed offset is exact and
doesn't depend on the system's tzdata being present or current.

## Integration approach

**Domain boundary:** `auth`, `permissions`, `sites`, `batteries` (includes
movements — grouped per PHASES.md as one "batteries/assets" domain),
`users`, and `inventory` (Phase 3). Each domain is a `routers/<domain>.py`
+ `db/<domain>.py` pair — except `inventory`, which is one router
(`routers/inventory.py`) over *several* `db/inventory_*.py` files
(`inventory_locations`, `inventory_categories`, `inventory_items`,
`inventory_transactions`, `inventory_reports`), split by concern because
the domain covers six tables and a two-stage workflow — one file would be
unwieldy. It's still a single domain with a single router; the split is
purely a `db/` organization choice, not a second domain. `db/connection.py`'s
`get_connection()` is the one shared piece every `db/*` module imports.

**The one shared permission-check function:** `user_has_permission()` lives
in `routers/permissions.py` and nowhere else — every other router imports it
from there rather than reimplementing the admin-bypass + role-permission-
lookup logic. `routers/auth.py` and `routers/permissions.py` are the two
modules every other router depends on (`get_current_user` from the former,
`user_has_permission` from the latter); no domain router imports another
domain router.

**Cross-domain data access goes through a function call, never raw SQL on
another domain's table.** The one place this mattered in practice:
`confirm_site_online`/`mark_site_still_down` need to flip a location's
`is_online` flag, and call the named accessor in `db/sites.py`
(`set_location_online_status`) instead of writing `locations` directly.
The read-only `JOIN`s against `locations` inside `db/batteries.py`'s
movement queries (to attach the destination's *name*, `is_online`, and
`is_home_base` to a movement row — `get_last_movement`, see Schema
choices above) are a deliberate exception — display/derivation data, not
a write or a business-logic branch, and rewriting them as accessor calls
would mean N+1 queries for no isolation benefit. (`db/sites.py`'s
`is_location_home_base` predates this JOIN and is now dead code — nothing
calls it since `get_last_movement` started reading `is_home_base`
directly; left in place rather than deleted mid-Phase-2, since removing
unused code wasn't the task at hand.)

**Frontend mirrors the same shape, with one Phase 2 exception.** Each view
is an ES module (`static/js/<view>.js`) that imports only from
`static/js/common.js` — never from another view's module — so nothing one
view does can silently reach into another's DOM, cache, or event wiring.
The one exception: `dashboard.js` and `movements.js` import from each
other directly (`dashboard.js` uses `movements.js`'s `MOVEMENT_STATUS_META`
for the stat-card detail modal's status labels; `movements.js` calls
`dashboard.js`'s `refreshData()` after a cancel, so the battery table
doesn't go stale). This is allowed because they're not actually different
domains — both are "batteries" (movements is grouped under it, see Schema
choices below) just split across two files for view-size reasons. A
cross-import between genuinely different domains (say `sites.js` reaching
into `users.js`) would still be the same violation it always was.

**A second, differently-shaped exception from Phase 3:** `inventory.js`,
`inventory-manage.js`, `inventory-log.js`, `issue-materials.js`,
`return-materials.js`, and `inventory-reports.js` all import from
`static/js/inventory-common.js` — a category/location cache, the
tracking-type vocabulary, and (since the two post-implementation
restructures) the shared unit detail and SKU history modals
(`openUnitDetailModal`, `openSkuHistoryModal`), needed identically by
multiple views. This is not the same shape as the
`dashboard.js` ↔ `movements.js` exception above (two views reaching into
each other directly): `inventory-common.js` is a shared module that owns no
view of its own and is never imported back *by* anything it imports from —
a hub, not a pairwise link. It exists because a many-way cross-import web
between the views themselves would be strictly worse than one shared
module they all depend on, and because unlike the dashboard/movements pair
(one domain split across two files for view-size reasons), these are
genuinely separate views inside one domain that each need the same small
set of cross-cutting inventory data.

`common.js` owns cross-cutting
concerns each view needs to plug into without common.js knowing about any
view specifically: a fragment loader (injects each view's HTML from
`static/views/<view>.html` into its mount point at startup), an app-shown
handler registry (a view registers its own initial data load, run once after
login), and a command-palette provider registry (a view registers its own
searchable items + how to jump to one). `static/js/app.js` is the only file
that imports every view module — it's the composition root, wiring them
together, analogous to `main.py` including every router.

## Visual testing — headless Chromium in this sandbox

Not part of the app's own architecture — a tooling note so a future
session doesn't have to rediscover this. Playwright (`pip install
playwright`, already in the venv) and its Chromium build
(`playwright install chromium`, downloaded to `~/.cache/ms-playwright/` —
this download itself needs no root) are both present, but the `chrome`
binary fails to launch out of the box: `ldd` on it reports `libnspr4.so`,
`libnss3.so`, `libnssutil3.so`, `libsmime3.so`, and `libasound.so.2` as
`not found`, and there's no root in this sandbox to `apt-get install`
them system-wide.

**Workaround — fetch the .deb contents without installing them:**

```bash
# 1. Download the three packages that provide those five libs, without
#    installing them (apt-get download needs no root, just writes .debs
#    to the working directory). Package names below are for Ubuntu
#    24.04/noble; if libasound2t64 isn't found on a different base image,
#    check `apt-cache search asound` for the equivalent.
mkdir -p /path/to/scratch/{debs,extracted}
cd /path/to/scratch/debs
apt-get download libnspr4 libnss3 libasound2t64

# 2. Extract (not install) each .deb's file contents into a scratch dir.
for deb in *.deb; do dpkg-deb -x "$deb" ../extracted/; done

# 3. Point the dynamic linker at the extracted libs before launching
#    anything that loads Chromium (a Playwright script, ldd, etc.).
export LD_LIBRARY_PATH=/path/to/scratch/extracted/usr/lib/x86_64-linux-gnu

# Sanity check before trusting a full Playwright launch:
ldd ~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome | grep "not found"
# — should print nothing.
```

With `LD_LIBRARY_PATH` set in the environment a script runs in,
`playwright.sync_api.sync_playwright().chromium.launch()` works normally
— navigate, click, screenshot, read computed styles, same as any other
Playwright environment. Use this instead of declaring a CSS/layout fix
"done" from code-reading or curl/DB checks alone: `page.screenshot()` for
what it looks like, `element.evaluate("e => getComputedStyle(e)...")` for
exact colors/sizes when a screenshot alone is ambiguous at a given zoom
level (this caught a case where a genuinely-red pill briefly looked
orange at full-page screenshot scale — the computed-style check, not the
screenshot, was what actually confirmed it).

The scratch directory only needs to exist for the lifetime of whatever
process sets `LD_LIBRARY_PATH` — nothing here is installed system-wide or
persisted, so this setup has to be redone (30 seconds, no root, no
prompts) in any fresh sandbox instance.

This file rarely changes once written. CLAUDE.md references it rather than
repeating it.
