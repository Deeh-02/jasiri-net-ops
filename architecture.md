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
into `users.js`) would still be the same violation it always was. `common.js`
owns cross-cutting
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
