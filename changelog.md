# CHANGELOG.md

Phase-based, not version-based — this project ships in named phases (see
[phase.md](phase.md)) rather than semver releases. Each entry here is a
concise, user-facing summary; full technical detail lives in the git log
and in `phase.md`'s own per-phase writeups.

## Phase 2 — Finish Incomplete Functionality (in progress, pending owner confirmation)

**Battery movements**
- Fixed: the person typed into "Moved by" when moving a battery was being
  silently discarded — the logged-in user's name was recorded instead
  regardless of what was typed. The typed name now wins; falls back to
  the logged-in user only when left blank.
- Added: "Moved by" is now a typeahead — free text stays allowed, but a
  filtered, clickable dropdown of active users appears as you type, and a
  non-blocking warning shows if what's typed doesn't match a known user.
- Fixed: cancelling a movement from the Movements page no longer leaves
  the Battery Tracker table showing stale data until a manual reload.
- Split "move authorization" into two separate, independently-grantable
  permissions: starting a new move ("Move Battery") vs. acting on a move
  already in progress ("Manage Movement").
- The topbar/sidebar movement badge now reflects a live count of
  unresolved movements (pending/in-transit/etc.), matching how the Check
  Sites badge already worked — it previously only counted movements stuck
  for 1+ hour, so a fresh move never moved the badge at all.

**Battery Tracker**
- Stat cards (Deployed/Charged/Charging/Low/Unknown) are now clickable —
  opens a detail table of exactly the batteries in that state (battery #,
  location, status, since).

**Settings**
- Password fields (current/new/confirm) now have a show/hide toggle.
- Profile/Password tabs restyled to match the app's existing tab
  convention, replacing a plain, unthemed-looking pill toggle.

**Roles**
- Edit/Delete Role buttons now match the rest of the app's icon-button
  styling (previously unstyled native buttons).

**Global search**
- Search can now reach permission-gated "add" actions (add user, add
  site, add battery, add movement), not just existing records.
- Movements and Check Sites are now reachable from search at all — they
  weren't before (not sidebar nav items, so never indexed).

**Mobile / scrolling**
- Fixed a table-scroll bug affecting both desktop and mobile: on desktop,
  hovering a table silently blocked page scroll even though the table had
  nothing to scroll internally; on mobile, scrolling a table to its
  internal limit didn't hand off to page scroll, it just stopped.

## Phase 1 — Mobile Fixes

- Added the missing viewport meta tag (mobile rendering was effectively
  untested before this).
- Sidebar nav rebuilt as a push-open off-canvas drawer on phones.
- Topbar search collapsed to an icon-only trigger on phones.
- Every view's table wrapped in a horizontally-scrollable container,
  height-capped with a sticky header on phones; Battery Tracker
  additionally gets a frozen first column.
- Stat-card grid tightened to 2 columns on phones.
- Found and fixed along the way: a topbar `overflow:hidden` silently
  clipping the profile dropdown, a `border-collapse` setting silently
  breaking sticky table cells, and a flex default forcing the page wider
  than the viewport.

## Phase 0 — Separate & Polish

- Split a monolithic backend into `routers/` + `db/`, one file per
  domain (auth, permissions, sites, batteries, users).
- Split the frontend into per-view HTML fragments, ES modules, and CSS
  files.
- Consolidated permission-checking into one shared function used
  everywhere, replacing per-domain duplication.
