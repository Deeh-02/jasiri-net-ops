# CLAUDE.md — JASIRI NET OPS

Read at the start of every new session. Not re-read mid-session — instruction
changes only take effect in a fresh session.

## Stack
- **Backend:** FastAPI (Python), served by Uvicorn. No ORM — raw SQL via
  `psycopg2`, one connection opened/closed per function call. JWT auth
  (`python-jose`), passwords hashed with `passlib`/bcrypt.
- **Frontend:** plain HTML/CSS/JS. ES modules for isolation, no framework,
  no build step or bundler — served directly as static files.
- **Database:** PostgreSQL. Supabase in production, a local Postgres
  instance for dev — picked at runtime by whether `DATABASE_URL` is set
  (`db/connection.py`).
- **Hosting:** Render (backend), Supabase (DB).

See ARCHITECTURE.md for the reasoning behind these choices, including a
note that `schema.sql` is currently stale relative to the live DB.

## Directory structure
```
main.py              # creates the app, mounts /static, registers routers, serves "/"
routers/              # one file per domain: auth, permissions, sites, batteries, users
db/                    # connection.py (shared) + one file per domain
static/
  index.html            # SPA shell — login screen, topbar, nav, view mount points
  views/                 # one HTML fragment per view, fetched + injected at startup
  js/
    common.js              # shared state, fragment loader, cmdk, app-shown registry
    app.js                  # bootstrap — the only file that imports every view module
    <view>.js               # one ES module per view, imports only from common.js
  css/
    common.css              # shared chrome/framework (topbar, nav, modals, tables)
    <view>.css               # one file per view
schema.sql            # STALE — see ARCHITECTURE.md's Schema choices section
architecture.md, design.md, phase.md, rules.md, delegation.md, claude.md
ask_deepseek.py        # DeepSeek delegation script (see DELEGATION.md)
```
Domain boundary is `auth` / `permissions` / `sites` / `batteries` (includes
movements) / `users`, both backend and frontend. See ARCHITECTURE.md's
Integration approach for how they're allowed to talk to each other.

## Hard rules
- One branch per phase. The phase branch (e.g. `phase-0-separate`) is
  created once, at the start of the phase — that single branch creation is
  what satisfies "branch before any change" for everything that happens
  within the phase. Individual tasks inside a phase — a router split, a
  CSS fix, a DeepSeek delegation — do NOT get their own sub-branches; they
  all land inside the same phase branch, which merges once at phase end.
- Never touch files outside the current phase's declared scope (see PHASES.md).
- Edit in place rather than full-file rewrites, where the file already exists
  and the change is incremental.
- One shared permission-check function, used everywhere. Never duplicate
  permission logic per-domain.
- No cross-file naming collisions — this includes CSS classes and JS scope,
  not just file/module names.
- Before any schema or infra change: stop and show the owner what the
  change is and what could go wrong if it's wrong. Wait for approval
  before proceeding — same mechanics as the delegation checkpoint in
  DELEGATION.md. If unattended when this triggers: STOP AND WAIT, same
  default as delegation. Don't proceed on your own judgment and don't
  silently skip the change either.
- Ask before marking a phase complete, even if PHASES.md's "done when"
  criteria look met to you. When Phase 0 specifically is confirmed
  complete, that's also the trigger to fill in the Stack and Directory
  structure placeholders above — don't leave them stale once they're no
  longer true.
- See RULES.md for code quality standards — every file written or edited
  should meet that bar, not just pass tests. This applies to DeepSeek's
  output too, not only Claude's own — see the delegation section below for
  how RULES.md gets enforced on delegated code.

## Phase discipline
Check PHASES.md before starting any work. If a request doesn't match the
current phase's declared scope, say so and ask rather than doing it anyway.
Update PHASES.md only on the owner's confirmation that a phase's "done when"
criteria are met.

## Model
Default to Sonnet for most work. This isn't self-enforcing — a text file
can't force a model switch mid-session, so don't rely on remembering it as
a standalone rule. Instead, it's tied to moments that already stop and
interrupt the flow: the delegation checkpoint, the schema/infra risk flag,
and anything on the "never delegate" list. Every time one of those fires,
state which model you're currently on as part of that stop, and switch to
Opus (`/model opus`) if you're not already on it before actually doing the
judgment work. This makes a missed switch visible to the owner even if you
forget — they'll see "Sonnet" stated at a checkpoint and can catch it.
Switch back to Sonnet (`/model sonnet`) once past that point.

## Delegation to DeepSeek
See DELEGATION.md for the full policy — status, cost model, what's safe to
delegate, verification, and the confirmation checkpoint. Not repeated here
so this file stays small; DELEGATION.md is the one you actually read
before delegating anything.