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
├── requirements.txt                # Python deps — no ORM; tests use stdlib unittest, nothing to install
├── schema.sql                      # STALE — see ARCHITECTURE.md's Schema choices section
├── run_test.py                     # ad-hoc manual debug script (prints one battery's movement
│                                    #   history) — not a test suite; the real tests are in tests/
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
│   ├── 0005_inventory_custody_type.sql # one additive column: inventory_categories.custody_type
│   ├── 0006_monitoring_core_tables.sql # Phase 4: monitoring tables + seeded hotspot_packages
│   ├── 0007_seed_monitored_sites.sql   # Phase 4: initial VLAN-to-site mapping (editable data)
│   ├── 0008_monitored_sites_name.sql   # Phase 4: display name for sites with no locations row
│   ├── 0009_monitoring_feed_health.sql # Phase 4.7: users_reported + last-seen columns, so a
│   │                                   #   silently-stopped user list (2026-09-21) is visible
│   ├── 0010_revenue_events_origin_vlan.sql # Phase 4.7: origin_vlan_id — the VLAN a sale came
│   │                                   #   from even when it isn't a registered site yet
│   ├── 0011_site_acknowledgements.sql  # Phase 4.5: mute a known-bad site without changing its state
│   ├── 0012_site_alerting.sql          # Phase 4.8: notifications, alert subscriptions, alert episodes,
│   │                                   #   alert_deliveries (the audit log SMS Status reads)
│   ├── 0013_alert_escalation_step.sql  # Phase 4.8: escalation_step on an episode (reminder ladder)
│   ├── 0014_site_confirmations.sql     # Phase 4.9: monitor vs human confirm-online, both answers kept
│   ├── 0015_alert_channels_default_on.sql # SMS/WhatsApp default ON for people with no row
│   ├── 0016_alert_message_templates.sql # editable alert wording — only overrides are rows
│   ├── 0017_ppp_customers_and_broadcasts.sql # PPPoE customer list + manual SMS broadcasts
│   ├── 0018_alert_send_window.sql      # one-row alert_settings: only send alerts between two times (EAT)
│   ├── 0019_daily_site_revenue.sql     # revenue reporting: daily + hourly per-site tables, frozen days,
│   │                                   #   re-freeze log
│   ├── 0020_revenue_report_config.sql  # revenue report thresholds — only overrides are rows
│   └── 0021_revenue_site_status.sql    # revenue alerts judged per site-day, with the thresholds used
│
├── router/                          # RouterOS scripts, pasted onto the CCR by a human — never run by Ops
│   └── ops-heartbeat.rsc             # Phase 4.3: read-only 60s heartbeat POSTing to /monitoring/ingest
│
├── routers/                        # FastAPI route handlers — one file per domain
│   ├── __init__.py
│   ├── auth.py                       # login, JWT issue/verify, get_current_user dependency
│   ├── permissions.py                # user_has_permission() — THE shared permission check,
│   │                                  #   used by every other router, lives nowhere else
│   ├── sites.py                      # locations CRUD, hourly online/offline verification
│   ├── batteries.py                  # batteries CRUD + movements (create/list/lifecycle actions)
│   ├── users.py                      # users + roles + role_permissions CRUD
│   ├── monitoring.py                 # Phase 4: token-auth /monitoring/ingest + status/revenue API,
│   │                                  #   alert wording + send window, /monitoring/revenue-report/*
│   ├── notifications.py              # the in-app bell + Alerts > Notifications inbox
│   ├── customers.py                  # PPPoE customer list upload + manual SMS broadcasts
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
│   ├── monitoring.py                   # Phase 4: ingest_snapshot (site status + revenue
│   │                                   #   attribution, see ARCHITECTURE.md), status/timeline/
│   │                                   #   revenue queries for the Status + site-detail pages
│   ├── monitoring_retention.py         # Phase 4: rolls up raw session/status rows past
│   │                                   #   SESSION_RAW_KEEP; run_if_due called from the ingest
│   │                                   #   request itself, no separate scheduler
│   ├── monitoring_alerts.py            # Phase 4.8: debounced/flap-aware alerts, escalation ladder,
│   │                                   #   bundling + battery plan; mutes on quiet hours, the send
│   │                                   #   window and acknowledgement
│   ├── monitoring_reconciliation.py    # Phase 4.9: store monitor vs human confirm-online answers
│   ├── alert_channels.py               # SMS (HostPinnacle) / WhatsApp senders
│   ├── alert_templates.py              # alert wording: code defaults + editable overrides
│   ├── alert_settings.py               # the global alert send window (fails open)
│   ├── notifications.py                # notifications table access (the bell)
│   ├── sms_log.py                      # alert_deliveries reads for Reports > SMS Status
│   ├── customers.py                    # PPPoE customers + broadcast history
│   ├── customer_import.py              # parses the uploaded customer spreadsheet
│   ├── revenue_daily.py                # revenue reporting: builds/freezes daily + hourly tables from
│   │                                   #   revenue_events; CLI backfill/refresh/refreeze
│   ├── revenue_logic.py                # revenue reporting's PURE rules — period resolver, compare,
│   │                                   #   contributions, rankings, trend, alert rules; no I/O
│   ├── revenue_config.py               # report thresholds: code defaults + editable overrides
│   ├── revenue_report.py               # Level 1/2 report assembly (cards, trend, site table, metrics)
│   ├── revenue_alerts.py               # runs the alert rules per site-day, stores/reads results
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
    │   ├── status.html                  # Sites > Status: live state, known issues, acknowledgements
    │   ├── trends.html                  # Sites > Trends: every site over a day/week/month
    │   ├── site-detail.html             # one site's report (route site-detail/:id/:kind/:value)
    │   ├── manage-sites.html            # monitored-site setup (gated manage_monitoring)
    │   ├── packages.html                # hotspot package prices — what each sale is worth
    │   ├── notifications.html           # Alerts > Notifications inbox
    │   ├── alert-templates.html         # Alerts > SMS Templates: send window + editable wording
    │   ├── customers.html               # Alerts > Customers: PPPoE customer list
    │   ├── broadcast.html               # Alerts > Broadcast: manual SMS to chosen customers
    │   ├── sms-status.html              # Reports > SMS Status: the alert delivery log
    │   ├── revenue.html                 # Reports > Revenue: cards, findings, attention, sites,
    │   │                                 #   rankings, trend
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
    │   ├── status.js                     # Status tab (own module — imports only common.js)
    │   ├── trends.js                     # Trends tab
    │   ├── site-detail.js                # site report: charts, period controls
    │   ├── manage-sites.js
    │   ├── packages.js
    │   ├── alerts.js                     # Notifications inbox + SMS Templates + the send-window card
    │   ├── customers.js
    │   ├── broadcast.js
    │   ├── sms-status.js
    │   ├── revenue.js                    # Reports > Revenue: renders the API's pre-formatted
    │   │                                 #   figures, sorts the site table client-side, SVG trend
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
        ├── status.css                     # Status page + the Status/Trends tab strip
        ├── trends.css                     # Trends tab; .fleet-range switch is reused by Revenue
        ├── site-detail.css
        ├── manage-sites.css
        ├── packages.css
        ├── alerts.css                     # Notifications, SMS Templates, send-window card
        ├── customers.css
        ├── broadcast.css
        ├── sms-status.css
        ├── revenue.css                    # Reports > Revenue (.rev-* classes)
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

`tests/` (not shown above, to keep the tree readable): `__init__.py` plus
`test_revenue_logic.py` (period resolver, compare), `test_revenue_trend.py`
(split, rolling mean, MAD band, slope, summary), `test_revenue_sites.py`
(contributions, rankings, per-site series), `test_revenue_alerts.py` (the
red/amber/green rules) and `test_revenue_config.py`. Standard-library
`unittest`, no database needed — run from the repo root with
`python -m unittest discover -s tests -t .`.

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
