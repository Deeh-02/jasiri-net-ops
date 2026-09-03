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
through `pending → in_transit → arrived → site_confirmed_online` (or
`site_still_down`) for `site_down` moves, or straight to `completed` for any
other reason, with `cancelled` reachable from `pending`/`in_transit`. A NULL
`from_location_id` is legal (first-ever movement of a battery). This table
is also where `battery.charge_status` resets to `unknown` from — leaving
home base means the app can no longer trust the last-known charge reading.

**`users`** — `role` is a free-text label (`admin` is magic — see
Integration approach below); `role_id` optionally points at `roles` for
everyone else. `status` is the soft-delete flag.

**`roles` / `role_permissions`** — `role_permissions` is a sparse
`(role_id, section, action) → allowed` table; a missing row means not
allowed (`check_role_permission` returns `False` on no match, not an error).
Sections in use today: `batteries`, `movements`, `sites`, `site_checks`,
`users`, `roles`.

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

## Integration approach

**Domain boundary:** `auth`, `permissions`, `sites`, `batteries` (includes
movements — grouped per PHASES.md as one "batteries/assets" domain), and
`users`. Each domain is a `routers/<domain>.py` + `db/<domain>.py` pair.
`db/connection.py`'s `get_connection()` is the one shared piece every `db/*`
module imports.

**The one shared permission-check function:** `user_has_permission()` lives
in `routers/permissions.py` and nowhere else — every other router imports it
from there rather than reimplementing the admin-bypass + role-permission-
lookup logic. `routers/auth.py` and `routers/permissions.py` are the two
modules every other router depends on (`get_current_user` from the former,
`user_has_permission` from the latter); no domain router imports another
domain router.

**Cross-domain data access goes through a function call, never raw SQL on
another domain's table.** The one place this mattered in practice:
`db/batteries.py`'s `record_movement` needs to know if a battery's
destination is the home base (to decide whether to reset `charge_status`),
and `confirm_site_online`/`mark_site_still_down` need to flip a location's
`is_online` flag. Both call named accessors in `db/sites.py`
(`is_location_home_base`, `set_location_online_status`) instead of querying
`locations` directly. The read-only `JOIN`s against `locations` inside
`db/batteries.py`'s movement-history queries (to attach from/to location
*names* to a movement row) are a deliberate exception — display data, not a
write or a business-logic read, and rewriting them as accessor calls would
mean N+1 queries for no isolation benefit.

**Frontend mirrors the same shape.** Each view is an ES module
(`static/js/<view>.js`) that imports only from `static/js/common.js` — never
from another view's module — so nothing one view does can silently reach
into another's DOM, cache, or event wiring. `common.js` owns cross-cutting
concerns each view needs to plug into without common.js knowing about any
view specifically: a fragment loader (injects each view's HTML from
`static/views/<view>.html` into its mount point at startup), an app-shown
handler registry (a view registers its own initial data load, run once after
login), and a command-palette provider registry (a view registers its own
searchable items + how to jump to one). `static/js/app.js` is the only file
that imports every view module — it's the composition root, wiring them
together, analogous to `main.py` including every router.

This file rarely changes once written. CLAUDE.md references it rather than
repeating it.
