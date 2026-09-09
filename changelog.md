# CHANGELOG.md

Phase-based, not version-based — this project ships in named phases (see
[phase.md](phase.md)) rather than semver releases. Each entry here is a
concise, user-facing summary; full technical detail lives in the git log
and in `phase.md`'s own per-phase writeups.

## Phase 3 — Ops Inventory System (implementation complete on branch
`phase-3-ops-inventory`, awaiting the owner's own click-through and merge
per phase.md's process — not yet marked done)

A new inventory/asset-tracking domain for field operations, entirely
separate from battery tracking: enclosures, cabling, consumables, and
power/network gear, none of which had any record before this phase.

**Categories, Locations, Items**
- Categories are user-created and tagged with one of three fixed Tracking
  Types (Asset-Serialized, Inventory-Quantity, Inventory-Length) — the type,
  not the category name, drives which fields and behavior apply, so a
  brand-new category (e.g. "Solar Equipment") needs no code change.
- A category's Tracking Type locks once it has items, to stop a switch that
  would silently orphan existing rows' type-specific fields.
- Items are one table with nullable columns per type, at the correct row
  granularity per type (one row per serial / per batch-lot / per cut).

**Transaction Log**
- Every state change (In, Transfer, Adjustment, Return, Write-off) happens
  only as a side-effect of a logged transaction — item records are never
  edited directly by hand.
- A partial-quantity Transfer splits the batch into two linked log rows
  (origin decrement + new destination row) sharing one grouping id, so
  both halves of the split are individually accounted for in the log.
- Admin-only historical-row editing, scoped to only the fields actually
  sent, so correcting one field never silently blanks the rest of the row.

**Unified Issue Cart**
- One screen, one action: a mixed cart (say 1 enclosure + 2 packs of ties
  + 150m of cable) issues as a single event with one log row per line, all
  linked, regardless of which of the three tracking types each line is.

**Two-stage cable reconciliation**
- A cut/reel goes out in full at issue time; actual metres used are only
  confirmed when the job closes. Reconciling records length used + length
  returned, always depletes the original cut, and — only when the returned
  remainder is long enough to be worth re-stocking — spins off a new cut
  row for it; a too-short remainder is logged as scrap against the
  original instead.
- A cut still awaiting reconciliation past 14 days is visually flagged so
  a dragging job doesn't leave stock unaccounted for indefinitely.

**Reporting**
- SKU/Spec Summary: Total On Hand per SKU (Asset/Quantity types) or per
  Spec (Length), a below-reorder-level flag, and an inline way to set the
  reorder level itself.
- Offcut rollup: total usable leftover length per spec, with a drill-down
  to the individual cuts contributing to that total.

**Permissions**
- Every inventory capability (view/add/edit/delete per section, plus a
  separate "Reconcile Cut" action gated at Manager level) is a grantable
  permission through the existing Roles screen — no new permission
  mechanism needed.

**Restructure (owner review pass, still on the same branch)**
- Categories and Locations moved off the Items page onto their own Manage
  page, reached via a header button — the Items page now shows only Items.
- Every item now has a detail view (unit cost, supplier, batch/cut detail,
  and its full transaction history) reachable from a new View action, so
  the main tables — especially cable's, trimmed to Cut/Reel ID, Spec,
  Remaining, and Location — don't need to carry every field as a column.
- New Cable Type Summary report: reels in stock and total remaining length
  per spec, with a drill-down into the individual reels — separate from
  the existing Offcut Rollup, which only covers unusable remainders.
- SKU/Spec Summary now shows a live weighted-average unit cost per SKU for
  Asset and Quantity types (correctly weighted across batches of different
  size and cost, not a plain average).
- Serialized assets now get a "Deployed" status on issue, resetting to
  "Active" on Return — distinct from Faulty/In Repair/Decommissioned, so a
  Return no longer silently clears a condition issue that was found in the
  field.

**Second refinement pass (owner review, still on the same branch) —
supersedes the Restructure pass's nav pattern and item detail modal before
either was committed**
- Inventory is now a collapsible sidebar group (Items / Transaction Log /
  Manage) instead of header-link buttons on the Items page; Reports is now
  its own standalone top-level nav item, no longer reached from inside
  Inventory.
- Tracking Types are now labeled Asset Core / Consumables Core / Cable Core
  everywhere they're shown.
- Locations no longer has a management screen — the Issue/Return Materials
  Site field is a free-typed autocomplete that creates a new site with zero
  pre-configuration, or matches an existing one by name.
- The Items table is now one row per SKU (not per serial/batch/cut), showing
  Qty and Total Value as *total owned* — on-hand plus deployed combined, so
  a deployed asset's value doesn't disappear from the table — with a second
  filter (In Store / Deployed) and a third (Per-Job / Custody, a new
  category-level distinction with no new transaction type or status
  attached to it). Asset unit cost shows a "~"-prefixed average to signal
  it's computed, not a literal per-unit price.
- The View action is reframed as stock history: an Asset or Cable SKU opens
  a list of its individual units, each opening the same tabbed Details/Logs
  view already used for a battery's detail+history; a Consumable SKU (no
  individual units) opens a flat running-balance history instead.
- Return Materials is now a real cart screen (search, add multiple lines,
  one submit) instead of only being reachable through the generic
  transaction-log form — every asset line requires an explicit status pick
  (never inferred from what it was before), and any active asset is
  returnable this way, not just ones currently checked out to someone.
- Issue Materials' Notes field is now required.

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
