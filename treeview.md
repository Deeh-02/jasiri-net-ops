# TREEVIEW.md — Full Project Tree

A complete, annotated listing of every tracked file, generated from
`git ls-files` — not the abbreviated version in CLAUDE.md's own
"Directory structure" section (that one shows the *shape* of the
convention; this one shows every actual file). Regenerate by hand
whenever a file's added, moved, or removed — there's no script that
keeps this in sync automatically.

```
battery-tracker/
├── main.py                        # FastAPI app: mounts /static, registers every router, serves "/"
├── requirements.txt                # Python deps — no ORM, no test framework
├── schema.sql                      # STALE — see ARCHITECTURE.md's Schema choices section
├── run_test.py                     # ad-hoc manual debug script (prints one battery's movement
│                                    #   history) — not a test suite, no test framework in this repo
├── ask_deepseek.py                 # DeepSeek delegation script — see DELEGATION.md
├── .gitignore
│
├── routers/                        # FastAPI route handlers — one file per domain
│   ├── __init__.py
│   ├── auth.py                       # login, JWT issue/verify, get_current_user dependency
│   ├── permissions.py                # user_has_permission() — THE shared permission check,
│   │                                  #   used by every other router, lives nowhere else
│   ├── sites.py                      # locations CRUD, hourly online/offline verification
│   ├── batteries.py                  # batteries CRUD + movements (create/list/lifecycle actions)
│   └── users.py                      # users + roles + role_permissions CRUD
│
├── db/                              # raw-SQL data access — one file per domain, psycopg2 only
│   ├── __init__.py
│   ├── connection.py                  # get_connection() — the one shared piece; picks
│   │                                   #   DATABASE_URL (Supabase/prod) vs local Postgres fallback
│   ├── sites.py
│   ├── batteries.py                   # includes battery_movements — grouped with batteries,
│   │                                   #   not a separate domain (see ARCHITECTURE.md)
│   └── users.py                       # users + roles + role_permissions
│
└── static/                          # frontend — plain HTML/CSS/JS, no build step, no framework
    ├── index.html                     # SPA shell: login screen, topbar, sidebar nav, cmdk
    │                                   #   palette, per-view mount points
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
    │   └── settings.html                # Profile + Password tabs
    │
    ├── js/                             # one ES module per view, imports only from common.js —
    │   │                                 #   EXCEPT dashboard.js <-> movements.js (see below)
    │   ├── app.js                        # bootstrap — the only file that imports every view module
    │   ├── common.js                     # shared state, auth, permission checks (can()), fragment
    │   │                                 #   loader, cmdk command palette, app-shown handler
    │   │                                 #   registry, refreshBadges()
    │   ├── dashboard.js                  # battery table, stat cards + click-through detail, move
    │   │                                 #   modal (incl. "Moved by" typeahead), imports
    │   │                                 #   MOVEMENT_STATUS_META from movements.js
    │   ├── movements.js                  # movements table + lifecycle actions; calls
    │   │                                 #   dashboard.js's refreshData() after a cancel
    │   ├── sites.js
    │   ├── check-sites.js
    │   ├── users.js
    │   ├── roles.js                      # permission-grid rendering — flat + nested checkbox
    │   │                                 #   sections, some remapped to a different backend
    │   │                                 #   section/action than where they're rendered
    │   └── settings.js                   # Profile/Password tabs, password show/hide toggle
    │
    └── css/                            # one file per view + common.css for shared chrome
        ├── common.css                    # topbar, sidebar nav, modals, base table styling,
        │                                 #   stat-grid, mobile breakpoint (max-width:760px)
        ├── dashboard.css                  # stat cards, move/charge dropdowns, "Moved by"
        │                                 #   typeahead dropdown, View Battery + stat-detail modals
        ├── movements.css
        ├── sites.css
        ├── check-sites.css
        ├── users.css
        ├── roles.css                      # permission-grid layout, slant-tab-free (Roles has no
        │                                 #   tab group — Settings and the View Battery modal do)
        └── settings.css                   # slant-tab styling (shared visual pattern with
                                            #   dashboard.css's View Battery modal tabs, kept as a
                                            #   separate class on purpose — see DESIGN.md)
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
