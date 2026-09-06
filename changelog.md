# CHANGELOG.md

Phase-based, not version-based — this project ships in named phases (see
[phase.md](phase.md)) rather than semver releases. Each entry here is a
concise, user-facing summary; full technical detail lives in the git log
and in `phase.md`'s own per-phase writeups.

## Phase 2 — Finish Incomplete Functionality (completed 2026-09-06)

One item (inline record detail in global search) was dropped by the
owner's explicit call. Two migrations found necessary along the way
(`0002_backfill_in_transit_at.sql`, `0003_close_out_site_still_down.sql`)
were never run against production — discarded per the owner's explicit
call rather than pursued further; see [phase.md](phase.md) for detail.

**Battery status & movement lifecycle**
- Battery status (At Base/Pending/In Transit/Deployed) is now driven
  end-to-end by the linked movement's lifecycle rather than partly stored,
  partly derived. A movement landing back at home base now resolves to
  "At Base", not "Deployed" — previously any arrived/completed movement
  read as "Deployed" regardless of destination.
- A site-down move that comes back "still down" now closes the movement
  out as `completed` (done, no further status changes expected) instead
  of sitting in the movements list forever under its own status. The
  battery itself stays flagged as needing attention — tracked on the
  destination site's `is_online`, not on the movement — until someone
  confirms the site back online, whether via a later movement or Check
  Sites directly.
- That "needs attention" flag no longer changes the battery's status
  label or the Deployed stat card's count. It shows instead as: the
  status pill itself recoloring to red on the main Battery Tracker table
  (still reads "Deployed"), and a red left-edge accent on the row inside
  the stat-card click-through detail. A battery can't be set to
  "charging" while flagged this way, even after its movement has closed
  out — enforced server-side, not just hidden in the UI.
- Timestamps ("Since" columns, movement history) now compute against East
  Africa Time (fixed UTC+3, no DST) instead of the server's UTC clock —
  fixes Check Sites' 8am–8pm active-check window, which was effectively
  running 11am–11pm Nairobi time before.
- The Move Battery modal's "Reason" field is no longer a bare `<select>`
  — replaced with the same custom dropdown component already used for the
  charge-status picker, so it actually matches "Move to"/"Moved by" in
  color and behavior (a native select's own chevron/box-model, and its
  open option list, can't be reliably restyled to the app's dark theme).
- Live sync between Movements and the Battery Tracker table is now under
  2 seconds (was 5s), and both tables skip re-rendering on a poll tick
  when the fetched data hasn't actually changed — previously every tick
  rebuilt the whole table regardless, which tore down and recreated every
  row's buttons and read as a visible flicker.
- Static assets (`/static/*` — every JS/CSS file) now send
  `Cache-Control: no-cache`, so a plain refresh always revalidates
  against the server instead of serving a stale cached copy — several
  "my change isn't showing up" reports this phase turned out to be this.

**Battery movements**
- Fixed: the person typed into "Moved by" when moving a battery was being
  silently discarded — the logged-in user's name was recorded instead
  regardless of what was typed. The typed name now wins; falls back to
  the logged-in user only when left blank.
- Added: "Moved by" is now a typeahead — free text stays allowed, but a
  filtered, clickable dropdown of active users appears as you type, and a
  non-blocking warning shows if what's typed doesn't match a known user.
- Added: the move modal's "Move to" destination field is now the same
  typeahead — a filtered, clickable dropdown of sites appears as you
  type — but blocking: unlike "Moved by", the move can't be confirmed
  until what's typed matches a known site, since the destination has to
  resolve to a real location id.
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
