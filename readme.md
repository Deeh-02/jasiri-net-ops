# Jasiri Net — Battery Tracker

An internal ops tool for tracking Jasiri Net's field battery fleet: where
each battery physically is, its charge state, its movement history between
sites, and site-verification/check-in status — with role-based permissions
gating who can do what.

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
  permission split.
- **Sites** — site directory plus an hourly online/offline
  verification flow ("Check Sites").
- **Users / Roles** — user management with a granular,
  section-and-action permission grid per role.
- **Settings** — profile editing, password change with a show/hide
  toggle.
- **Global search** (⌘K) — jump to any section, record, or
  permission-gated "add" action from one command palette.

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

Phase 2 ("Finish Incomplete Functionality") is code-complete pending the
owner's local checkout and confirmation — see [phase.md](phase.md) for the
full per-item breakdown. Phases 0 and 1 (structural split, mobile fixes)
are confirmed done.
