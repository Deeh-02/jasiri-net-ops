# Jasiri Net — Battery Tracker

An internal ops tool for Jasiri Net's field operations. Started as a
battery fleet tracker — where each battery physically is, its charge
state, its movement history between sites, and site-verification/check-in
status — and now also covers general inventory/asset tracking (enclosures,
cable, consumables, power/network gear): what's on hand, what went out to
which job, and how much cable is left on a given cut. All of it gated by
one role-based permission system.

## Stack

- **Backend:** FastAPI (Python) on Uvicorn. No ORM — raw SQL via
  `psycopg2`. JWT auth (`python-jose`), passwords hashed with
  `passlib`/bcrypt.
- **Frontend:** plain HTML/CSS/JS (ES modules), no framework, no build
  step — served directly as static files by FastAPI's own `StaticFiles`
  mount.
- **Database:** PostgreSQL — Supabase in production, a local instance for
  dev.
- **Hosting:** Render (backend), Supabase (DB).

Full reasoning behind these choices — including a note that `schema.sql`
is stale relative to the live DB — is in [architecture.md](architecture.md).

## Running locally

```bash
pip install -r requirements.txt
```

Set environment variables (or rely on the local-dev fallback):

- `DATABASE_URL` — if unset, falls back to a local Postgres instance
  (`battery_tracker` DB on `localhost:5432`, see `db/connection.py` for
  the exact fallback credentials).
- `SECRET_KEY` — JWT signing key. Has a hardcoded dev fallback in
  `routers/auth.py`, explicitly flagged there as "change before deploy" —
  don't rely on the fallback outside local dev.
- `DEEPSEEK_API_KEY` — only needed if delegating a task via
  `ask_deepseek.py` (see [delegation.md](delegation.md)); unrelated to
  running the app itself.

Then:

```bash
uvicorn main:app --reload
```

The app serves itself at `http://127.0.0.1:8000/` — `main.py` mounts
`/static` and serves `static/index.html` (the SPA shell) at `/`.

There's no seed script; a local Postgres instance needs its own schema and
at least one `admin`-role user created directly before the app is usable.

## Features

- **Battery Tracker** — fleet table (battery #, model, charge status,
  physical status, current location, who moved it last, when), with
  click-through detail per state via the stat cards.
- **Movements** — the move lifecycle for a battery between sites
  (pending → in-transit → arrived/completed, with a site-down branch for
  confirming the destination is back online), gated by a role-based
  `create` (start a move) vs. `manage` (act on one already in progress)
  permission split. A site confirmed still down closes the movement out
  but flags the battery (red status pill, still reads "Deployed") until
  the site is confirmed back online, independent of the movement record.
- **Sites** — site directory, with network status and trends from
  router monitoring (the manual hourly "Check Sites" list was removed
  2026-09-24 once monitoring covered it).
- **Users / Roles** — user management with a granular,
  section-and-action permission grid per role.
- **Settings** — profile editing, password change with a show/hide
  toggle.
- **Global search** (⌘K) — jump to any section, record, or
  permission-gated "add" action from one command palette.
- **Inventory** — a separate domain from battery tracking, covering
  enclosures, cable, consumables, and power/network gear:
  - **Items / Stock** — one row per SKU (Qty/Unit Cost/Total Value); Items
    defaults Qty to on-hand/available (not on-hand plus deployed), Stock
    adds Status (All/In Store/Deployed) and Custody filters on the same
    data.
  - **Issue / Return Materials** — a single cart for checking multiple
    items out to (or back from) a site or person in one action, regardless
    of whether they're individually-serialized assets, batch-tracked
    consumables, or cut-to-length cable. **Quick Issue/Return** lets an
    Asset-core line be filled by quantity ("5") instead of picking each
    serial by hand, auto-selecting from what's actually eligible (in-stock
    for Issue, checked-out-to-that-source for Return) and showing exactly
    which units were picked so one can be swapped out before confirming.
  - **Categories** are user-created and tagged with one of three fixed
    "Cores" (Asset / Consumables / Cable) that decide row granularity and
    which fields apply — locked once a category has items, since the three
    Cores use disjoint, non-convertible column sets (see
    [architecture.md](architecture.md)).
  - **Cable reconciliation** — a cut goes out whole (Stage 1); once the job
    closes, actual metres used vs. returned are logged (Stage 2), splitting
    off a fresh reel from any usable remainder.
  - **Reports** — SKU/Spec Summary (with reorder-level flagging), Cable
    Type Summary, and Offcut Rollup, each independently permission-gated
    from the rest of Inventory.

## Project docs

This repo tracks its own working process in root-level docs, read by
Claude Code at the start of every session — useful context for a human
picking this up too:

- [claude.md](claude.md) — stack, hard rules, phase discipline
- [phase.md](phase.md) — live status: what's done, what's active, what's next
- [architecture.md](architecture.md) — schema and system-design reasoning
- [design.md](design.md) — UI/UX conventions (color, type, components, mobile)
- [rules.md](rules.md) — code quality standards
- [delegation.md](delegation.md) — DeepSeek delegation policy
- [changelog.md](changelog.md) — phase-by-phase summary of what shipped
- [treeview.md](treeview.md) — full annotated file tree

## Current status

Phases 0–3 are confirmed done and merged to `main` — structural split,
mobile fixes, finish-incomplete-functionality, and Phase 3 ("Ops Inventory
System," the Inventory domain described above). Phase 4 ("Network
Monitoring," site status + hotspot revenue tracking) is active, on the
`phase-4-network-monitoring` branch, not yet merged. See
[phase.md](phase.md) and [changelog.md](changelog.md) for the full
breakdown.
