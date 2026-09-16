# TREEVIEW.md — Full Project Tree

A complete, annotated listing of every tracked file, generated from
`git ls-files` — not the abbreviated version in CLAUDE.md's own
"Directory structure" section (that one shows the *shape* of the
convention; this one shows every actual file). Regenerate by hand
whenever a file's added, moved, or removed — there's no script that
keeps this in sync automatically.

```
battery-tracker/
├── main.py                        # FastAPI app: mounts /static, registers every router, serves "/",
│                                    #   forces Cache-Control:no-cache on every /static/* response
├── requirements.txt                # Python deps — no ORM, no test framework
├── schema.sql                      # STALE — see ARCHITECTURE.md's Schema choices section
├── run_test.py                     # ad-hoc manual debug script (prints one battery's movement
│                                    #   history) — not a test suite, no test framework in this repo
├── ask_deepseek.py                 # DeepSeek delegation script — see DELEGATION.md
├── .gitignore
│
├── migrations/                      # plain numbered .sql scripts, run by hand via
│   │                                 #   `psql "$DATABASE_URL" -f migrations/000X_....sql` —
│   │                                 #   no migration framework/runner in this repo
│   ├── 0001_add_in_transit_at.sql
│   ├── 0002_backfill_in_transit_at.sql
│   ├── 0003_close_out_site_still_down.sql
│   ├── 0004_inventory_core_tables.sql # Phase 3: inventory_locations/categories/items/
│   │                                 #   transactions/sku_thresholds — six new tables, zero
│   │                                 #   ALTERs against existing ones (see ARCHITECTURE.md)
│   └── 0005_inventory_custody_type.sql # one additive column: inventory_categories.custody_type
│
├── routers/                        # FastAPI route handlers — one file per domain
│   ├── __init__.py
│   ├── auth.py                       # login, JWT issue/verify, get_current_user dependency
│   ├── permissions.py                # user_has_permission() — THE shared permission check,
│   │                                  #   used by every other router, lives nowhere else
│   ├── sites.py                      # locations CRUD, hourly online/offline verification
│   ├── batteries.py                  # batteries CRUD + movements (create/list/lifecycle actions)
│   ├── users.py                      # users + roles + role_permissions CRUD
│   └── inventory.py                  # the whole Inventory domain's routes — categories,
│                                      #   locations, items/products, issue/return carts,
│                                      #   transaction log, reconciliation, reports/summaries.
│                                      #   One router over five db/ files (see below) — the
│                                      #   domain covers six tables, still one domain either
│                                      #   way (see ARCHITECTURE.md)
│
├── db/                              # raw-SQL data access — one file per domain, psycopg2 only
│   ├── __init__.py
│   ├── connection.py                  # get_connection() — the one shared piece; picks
│   │                                   #   DATABASE_URL (Supabase/prod) vs local Postgres fallback;
│   │                                   #   also EAT/utc_iso time helpers (see ARCHITECTURE.md)
│   ├── sites.py
│   ├── batteries.py                   # includes battery_movements — grouped with batteries,
│   │                                   #   not a separate domain (see ARCHITECTURE.md)
│   ├── permissions.py                  # roles + role_permissions data access
│   ├── users.py                       # users CRUD
│   ├── inventory_locations.py          # inventory_locations CRUD + get_or_create_location_by_name
│   │                                   #   (Issue/Return's free-typed Site field, Add/Edit Unit's
│   │                                   #   Location field) + get_default_store_location (Return's
│   │                                   #   single fixed destination)
│   ├── inventory_categories.py         # category CRUD; count_active_items backs the Core-lock
│   │                                   #   check in routers/inventory.py (see ARCHITECTURE.md)
│   ├── inventory_items.py              # item/unit CRUD at the correct row granularity per Core
│   │                                   #   (one row per serial/batch-lot/cut); update_product
│   │                                   #   moves a whole SKU/spec group between same-Core
│   │                                   #   categories at once
│   ├── inventory_transactions.py       # the append-only log: record_transaction (single-line
│   │                                   #   actions), issue_cart/return_cart (N-line, one commit,
│   │                                   #   one event_group_id), reconcile_cut
│   └── inventory_reports.py            # get_items_summary (Items/Stock's SKU-aggregate rows,
│                                       #   on_hand_qty/deployed_qty/total_qty split — see
│                                       #   ARCHITECTURE.md), get_sku_summary (Reports' on-hand-
│                                       #   only rollup), cable/offcut summaries, sku transaction
│                                       #   history
│
└── static/                          # frontend — plain HTML/CSS/JS, no build step, no framework
    ├── index.html                     # SPA shell: login screen, topbar, sidebar nav (incl. the
    │                                   #   collapsible Inventory group), cmdk palette, per-view
    │                                   #   mount points
    ├── jn-logo.png
    │
    ├── views/                         # one HTML fragment per view, fetched + injected at startup
    │   ├── dashboard.html               # Battery Tracker table + stat cards + move/add-battery
    │   │                                 #   modals + stat-detail click-through modal
    │   ├── movements.html               # Movements tracking table (pending/in-transit/etc.)
    │   ├── sites.html
    │   ├── check-sites.html
    │   ├── users.html
    │   ├── roles.html                   # role list + permission-grid edit form
    │   ├── settings.html                # Profile + Password tabs
    │   ├── inventory.html                # Items: one row per SKU/spec (Qty/Cost/Value), View/
    │   │                                 #   Edit/Delete + Add Item wizard; also hosts the shared
    │   │                                 #   unit-list/unit-detail/sku-history modal markup
    │   │                                 #   (addressable from Stock/Reports too regardless of
    │   │                                 #   which view is active)
    │   ├── stock.html                    # same SKU-aggregate rows as Items, View-only, with
    │   │                                 #   Status/Custody filters + Issue/Return header links
    │   ├── inventory-log.html            # Transaction Log — generic Log Transaction form
    │   │                                 #   (Transfer/Adjustment/Return/Write-off) + reconcile
    │   ├── inventory-manage.html         # Categories only — Locations has no management screen
    │   ├── inventory-reports.html        # SKU/Spec Summary, Cable Type Summary, Offcut Rollup
    │   ├── issue-materials.html          # Issue cart: search+form left column, Cart right column
    │   └── return-materials.html         # Return cart — same layout, no Site/Activity/Issued-To,
    │                                     #   shares issue-materials.css (no CSS file of its own)
    │
    ├── js/                             # one ES module per view, imports only from common.js —
    │   │                                 #   EXCEPT dashboard.js <-> movements.js, and EXCEPT
    │   │                                 #   inventory.js/stock.js/inventory-log.js/
    │   │                                 #   inventory-manage.js/inventory-reports.js/
    │   │                                 #   issue-materials.js/return-materials.js, which all
    │   │                                 #   import from inventory-common.js (a shared domain
    │   │                                 #   module, not a pairwise cross-import — see
    │   │                                 #   ARCHITECTURE.md). Each view module registers a route
    │   │                                 #   with common.js's router (registerRoute) instead of
    │   │                                 #   switching views itself
    │   ├── app.js                        # bootstrap — the only file that imports every view module
    │   ├── common.js                     # shared state, auth, permission checks (can()), fragment
    │   │                                 #   loader, cmdk command palette, app-shown handler
    │   │                                 #   registry, refreshBadges(), hash router (navigate/
    │   │                                 #   registerRoute/registerRouteResetter/dispatchRoute —
    │   │                                 #   re-checks permission on every navigation, not just at
    │   │                                 #   login — URL + browser back/forward reflect the
    │   │                                 #   current view), ROUTE_PERMISSION_MAP +
    │   │                                 #   NAV_GROUP_MASTER_PERMISSION (see ARCHITECTURE.md)
    │   ├── dashboard.js                  # battery table, stat cards + click-through detail, move
    │   │                                 #   modal (incl. "Moved by"/"Move to" typeahead + the
    │   │                                 #   "Reason" custom dropdown), imports MOVEMENT_STATUS_META
    │   │                                 #   from movements.js; battery detail modal is a route
    │   │                                 #   (#/dashboard/battery/:id); refreshData() skips
    │   │                                 #   re-rendering the table when live-sync polling comes
    │   │                                 #   back with unchanged data (avoids a button/hover flicker)
    │   ├── movements.js                  # movements table + lifecycle actions; calls
    │   │                                 #   dashboard.js's refreshData() after a cancel;
    │   │                                 #   refreshMovements() has the same unchanged-data skip
    │   ├── sites.js
    │   ├── check-sites.js
    │   ├── users.js
    │   ├── roles.js                      # permission-grid rendering — flat + nested checkbox
    │   │                                 #   sections (incl. Inventory's Items/Stock/Categories/
    │   │                                 #   Log tree and the independent Reports toggle — see
    │   │                                 #   ARCHITECTURE.md), some remapped to a different
    │   │                                 #   backend section/action than where they're rendered
    │   ├── settings.js                   # Profile/Password tabs, password show/hide toggle
    │   ├── inventory-common.js            # shared Inventory domain module: category/location
    │   │                                 #   caches, TRACKING_TYPE_LABELS ("Core" naming), the
    │   │                                 #   shared unit-list/unit-detail-modal/sku-history View
    │   │                                 #   drill-down (openStockHistory), cartQtyStepperHtml,
    │   │                                 #   assetStatusBadgeHtml, exportRowsToCsv
    │   ├── inventory.js                   # Items page — renderItemsTable off get_items_summary
    │   │                                 #   (Qty defaults to on_hand_qty, not total — see
    │   │                                 #   ARCHITECTURE.md), Add Item wizard (New Product / Add
    │   │                                 #   Unit, incl. Asset batch-add)
    │   ├── stock.js                       # Stock page — same SKU-aggregate rows, Status (All/In
    │   │                                 #   Store/Deployed)/Custody filters switch which of
    │   │                                 #   on_hand/deployed/total displays, client-side, no
    │   │                                 #   re-fetch; click-to-edit Reorder Level
    │   ├── inventory-log.js               # generic Log Transaction form + log table
    │   ├── inventory-manage.js            # Categories CRUD (Add/Edit modals, Core-lock hint
    │   │                                 #   sourced from the API's own 400 detail — see
    │   │                                 #   ARCHITECTURE.md)
    │   ├── inventory-reports.js           # SKU/Spec Summary, Cable Type Summary, Offcut Rollup
    │   │                                 #   panels + CSV export per panel
    │   ├── issue-materials.js             # search+cart, Quick Issue (auto-select N eligible
    │   │                                 #   Active serials, deterministic bottom-of-list order —
    │   │                                 #   see ARCHITECTURE.md), qty stepper for Quantity lines
    │   └── return-materials.js            # search scoped to only Deployed assets (what's
    │                                     #   actually issued out — see ARCHITECTURE.md), Quick
    │                                     #   Return scoped to a chosen site/person source,
    │                                     #   per-line explicit status pick
    │
    └── css/                            # one file per view + common.css for shared chrome
        ├── common.css                    # topbar, sidebar nav (incl. collapsible group/chevron/
        │                                 #   subitems), sidebar/content vertical divider, modals,
        │                                 #   base table styling, stat-grid, shared .qty-stepper
        │                                 #   component, mobile breakpoint (max-width:760px)
        ├── dashboard.css                  # stat cards (incl. .deployed-flagged red pill variant),
        │                                 #   move/charge/reason dropdowns, "Moved by" typeahead
        │                                 #   dropdown, View Battery + stat-detail modals (incl.
        │                                 #   .battery-row-flagged red row accent)
        ├── movements.css
        ├── sites.css
        ├── check-sites.css
        ├── users.css
        ├── roles.css                      # permission-grid layout, slant-tab-free (Roles has no
        │                                 #   tab group — Settings and the View Battery modal do)
        ├── settings.css                   # slant-tab styling (shared visual pattern with
        │                                 #   dashboard.css's View Battery modal tabs, kept as a
        │                                 #   separate class on purpose — see DESIGN.md)
        ├── inventory.css                  # shared by Items, Stock, and Manage (no CSS files of
        │                                 #   their own) — SKU-aggregate table, asset-status pill
        │                                 #   colors (Active/Deployed/Faulty/In Repair/
        │                                 #   Decommissioned — see ARCHITECTURE.md), unit-list/
        │                                 #   unit-detail modal, Add Item wizard
        ├── inventory-log.css
        ├── issue-materials.css            # shared by Issue AND Return Materials — two-column
        │                                 #   layout, Quick Issue/Return controls, expandable
        │                                 #   cart-group serial list
        └── inventory-reports.css
```

## Governance / reference docs (repo root, not shown in the tree above)

| File | What it's for |
|---|---|
| `claude.md` | Read at the start of every session — stack, hard rules, phase discipline, delegation pointer |
| `phase.md` | Live status tracker — active phase's scope, done-when criteria, completed-phase history |
| `architecture.md` | System/data reasoning — schema choices, infra, domain-boundary rules |
| `design.md` | UI/UX reference — color/type tokens, component conventions, mobile behavior |
| `rules.md` | Code quality standards — naming, comments, error handling |
| `delegation.md` | DeepSeek delegation policy — what's safe to hand off, verification steps |
| `readme.md` | This project's front door — what it is, how to run it |
| `changelog.md` | Phase-by-phase summary of what shipped |
| `treeview.md` | This file |
