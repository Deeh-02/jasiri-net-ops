# PHASES.md — Live Status Tracker (JASIRI NET OPS)

Check this file before starting any work. If a request doesn't match the
current phase's declared scope, flag it — don't do it anyway.

Completed phases collapse to one summary line (name + completion date)
once confirmed done — full detail below is only kept for the active phase
and whatever's still upcoming.

---

## ACTIVE PHASE: Phase 2 — Finish Incomplete Functionality

**Status: all in-scope items resolved (one dropped by owner's explicit
call) — awaiting the owner's local checkout/run before this phase is
confirmed done, per this file's own phase-wide rule below. Not
self-promoted to COMPLETED PHASES; that's the owner's call, not
Claude's, same as every phase before this one.**

Features that exist but aren't fully built get finished individually, one
at a time, each in its own already-isolated file. Kept separate from
Phase 0's structural move on purpose — see Phase 0's completed entry
below on traceability.

Scope below was assembled directly from an owner audit of the live app
(not a codebase read yet — each item still needs its own file/location
confirmed before work starts). Each item is its own unit of work, worked
one at a time per this phase's own rule, each landing in its already-
isolated file.

### Resolved

1. **Roles section — button color consistency.** Done. `.edit-role-btn`/
   `.delete-role-btn` had no CSS at all (unstyled native `<button>`
   chrome) — gave them the exact same styling as `.edit-user-btn`/
   `.delete-user-btn`.

2. **Battery movement — "Moved by" as typeahead search.** Done, as
   Option B: stays a free-text field (a contractor or anyone not in the
   system is still a valid entry) with a filtered, scrollable,
   clickable suggestion dropdown of active users as you type. If what's
   typed doesn't match a known user, a non-blocking warning shows —
   never blocks submission either way. Users are fetched
   permission-gated (`users:view`), so a role without it just gets no
   suggestions, not an error.

3. **Battery movement — "Moved by" not recording.** Root cause was two
   separate bugs, split into 3a and 3b:
   - **3a (kept):** the Move modal's typed name was silently discarded —
     `MovementCreate` never declared a `moved_by` field, so FastAPI
     dropped it, and the handler hardcoded the logged-in user's name
     instead. Fixed: the typed name now wins when given, falling back
     to the logged-in user when left blank. Shows on the dashboard's
     battery table.
   - **3b (reverted):** also added `moved_by` to the standalone
     Movements page, but the owner's scope call was that it belongs
     only on the dashboard table (3a), not the movement-lifecycle
     table — reverted back out.

4. **Battery movement — missing notification.** Done, reframed: "site
   check-in's working notification" turned out to be its topbar/sidebar
   badge count, not a toast. Two bugs fixed to match: the move handler
   never called `refreshBadges()` (site check-in did), and the badge
   query itself only counted movements stuck 1+ hour ("overdue"), so it
   never moved for a fresh move regardless. Redefined to count
   everything not yet fully resolved (pending/in-transit/arrived/
   site-still-down), matching how Check Sites' badge already works.

5. **Settings → Password fields — show/hide toggle.** Done. First pass
   duplicated the login screen's shared `.password-field` component
   with different CSS and would have broken it via cascade order —
   caught before shipping, rebuilt to reuse the shared component
   directly instead.

6. **Profile & Password settings — theme consistency.** Done — but
   scoped down mid-work to just the Settings tabs specifically (Profile/
   Password), which were a plain pill toggle. Restyled to the app's
   existing slant-tab pattern (same as the View Battery modal's
   Details/Logs tabs) — accent-green active label, slanted uppercase
   Space Grotesk, instead of a filled pill.

7. **Global search — reachable actions.** Done — plus a related gap
   found along the way: Movements and Check Sites weren't reachable
   from search *at all* (header quick-links, not sidebar nav items, so
   never picked up). Each of dashboard.js/sites.js/users.js's cmdk
   provider now prepends a permission-gated "Add X" action ahead of its
   entity list; "Add Movement" has no standalone modal (a move always
   starts from a specific battery row) so it jumps to the Battery
   Tracker table instead. Movements/Check Sites now also appear as
   "Go to" items like the sidebar sections.

8. **Status cards — click-through detail.** Done, with owner-agreed
   scope reduction (dropped "who has it" / site contact person — a
   harder cross-domain lookup — in favor of Battery #, Location, Status,
   Since). Clicking a card opens a modal table of exactly the batteries
   in that state, filtered client-side from data already loaded, using
   the same predicate that computes the card's own count. Status reuses
   `movements.js`'s existing status vocabulary (exported for this), with
   "Completed"/"Site Confirmed Online" shown as "On Site" — for this
   modal only, the Movements page itself is unchanged.

9. **Move authorization — role-based.** Done, and clarified mid-work
   into two genuinely separate permissions rather than one: "Move
   Battery" (initiating a move — new `movements:create`) is a flat
   checkbox under Batteries; "Manage Movement" (acting on movements
   already in progress — `movements:manage`, unchanged meaning) stays
   nested under a Movements sub-section, same place it always was. No
   DB migration — `role_permissions.action` is plain text.

10. **Global search — inline record detail.** Dropped — owner's explicit
    call (2026-09-04), not building it this phase.

11. **Battery Tracker page (desktop) — spacing/padding.** Added
    mid-phase by the owner (2026-09-04), then fully reverted at the
    owner's request after comparing against what's live on Render (main)
    — net zero change from `main`'s current look. Not pursued further
    this phase.

12. **Battery status — movement-lifecycle-driven, not partly stored.**
    Added mid-phase by the owner (2026-09-05). Status (At Base/Pending/In
    Transit/Deployed) now derives entirely from the battery's last
    movement (`db/batteries.py`'s `_battery_status`) instead of being
    partly a stored flag. A movement resolving at home base now reads "At
    Base"; anywhere else reads "Deployed" — this needed a real fix
    mid-item, since the first pass ignored the home-base case and showed
    every arrived/completed movement as "Deployed" regardless of
    destination.

13. **Site-down flag — visible without touching status or counts.** A
    site-down move answered "still down" now closes the *movement* out as
    `completed` (its lifecycle is done), while the *battery* stays flagged
    indefinitely via the destination location's `is_online` until someone
    confirms it back online. Iterated through several visual treatments
    per owner feedback before landing on: the main table's status pill
    recolors red (still reads "Deployed", count unchanged), the
    stat-detail modal's row gets a red left-edge accent. That accent
    turned out to not render at all at first — root cause was
    `.dashboard-logs-table`'s `border-collapse: separate`, under which a
    border set on a `<tr>` is never painted by the browser, only on
    `<td>`; moved the border onto the row's first cell instead. A flagged
    battery also can't be set to "charging", enforced server-side.

14. **EAT timestamps.** Added mid-phase by the owner. All business-logic
    time comparisons (Check Sites' 8am–8pm active window, movement
    "since" display) now use a fixed UTC+3 offset (`db/connection.py`'s
    `EAT`/`now_eat`/`to_eat`) instead of the server's raw UTC clock, which
    had Check Sites' window effectively running 11am–11pm Nairobi time.

15. **Move Battery modal — Reason field.** The bare `<select>` couldn't be
    made to visually match "Move to"/"Moved by" next to it — a native
    select keeps its own chevron/box-model, and its open option list is
    an OS-native popup immune to the app's dark theme. Replaced with the
    same button+menu component already used for the charge dropdown.
    Needed a follow-up fix: the button initially inherited
    `.panel-form button`'s green/bold submit-button look (a CSS
    specificity collision), and its placeholder text was a visibly
    different gray from the other two fields' native `::placeholder`
    dimming until that got an explicit color too.

16. **Live sync + render flicker.** Movements-to-Battery-Tracker sync
    interval shortened to under 2s (from 5s), then a second, distinct bug
    surfaced: both tables were rebuilding their entire body on every poll
    tick regardless of whether the data had changed, which tore down and
    recreated every action button and dropped/reapplied `:hover` on
    whatever the mouse was resting on — read as a flicker. Both polls
    (`dashboard.js`, `movements.js`) now diff the fetched data against
    what's cached and skip the render when nothing changed.

17. **Stale static assets.** A recurring "my change isn't showing up"
    pattern this phase turned out to be the browser caching `/static/*`
    JS/CSS between edits. `main.py` now sets `Cache-Control: no-cache` on
    every `/static/*` response, so a plain refresh revalidates against
    the server (still cheap — FastAPI's `StaticFiles` already returns 304
    via ETag/Last-Modified when nothing changed) instead of serving a
    stale cached copy.

### Also fixed this phase (found/requested along the way, not on the
### original numbered list)

- **Local/production schema drift, three rounds.** Item 12/13's work
  surfaced a local dev DB missing `battery_movements.in_transit_at`
  entirely (every `/batteries` call 500'd) and, once added, missing on
  existing rows (movement "Since" blank). A third round: a movement
  answered "still down" *before* item 13's fix landed stayed stuck on the
  legacy literal `site_still_down` status, which kept showing up in
  Movements' active list even though it had actually been answered. Three
  migrations added (`migrations/0001_add_in_transit_at.sql`,
  `0002_backfill_in_transit_at.sql`, `0003_close_out_site_still_down.sql`)
  — run by hand via `psql "$DATABASE_URL" -f migrations/000X_....sql`,
  same as any schema change in this repo (no migration framework).
  **0002 and 0003 still need to be run against production** — 0001 was
  applied there independently already.
- **Cancel move — stale battery table.** Cancelling a movement from the
  Movements page dropped it from `get_last_movement()`'s consideration
  (location/status/moved-by/since can all revert to the prior movement),
  but the dashboard's battery table stayed stale until a manual reload.
  `movements.js` now calls `dashboard.js`'s (newly exported) `refreshData()`
  after a successful cancel.
- **`.table-scroll` blocking page scroll.** Two bugs, one root cause:
  declaring only `overflow-x` drags `overflow-y`'s computed value from
  "visible" to "auto" too, so the table silently became a vertical
  scroll container on desktop despite never overflowing there, and
  `overscroll-behavior-y: contain` blocked page scroll over it with
  nothing to show for it. On phones, the same `contain` was also
  stopping scroll from handing off to the page once the table's own
  internal scroll maxed out. Fixed both: explicit `overflow-y: visible`
  on desktop, `overscroll-behavior-y: contain` removed entirely. See
  DESIGN.md's Mobile Behavior section for the trade-off this accepts
  (a previously-documented rubber-band-bounce visual seam may be very
  slightly more visible — already noted there as a low-priority,
  not-fully-eliminated cosmetic gap even before this change).

### Out of scope (explicitly deferred)

- Anything not on the list above, even if adjacent (e.g. other buttons'
  color grading beyond Roles, other settings fields beyond passwords).
- Any new feature not already partially built (that's Phases 3+).
- Structural/architecture changes — if fixing any item above reveals a
  structural problem, flag it rather than folding a refactor in here.
- Item 10, per the owner's explicit drop above.

### Done-when criteria

- Per-item criteria ended up implicit in each item's resolution above
  rather than written in advance — in practice every item needed real
  investigation before "done" could even be defined (this file's own
  prediction for #3/#4, above, turned out to hold for most of the list).
- The phase-wide rule still applies and is the actual gate: owner checks
  out the phase branch locally and runs it on localhost before
  confirming any item — or the phase — done.

---

## UPCOMING PHASES (order locked, detail not yet expanded)

**Phase 3 — Notifications.** Lowest-effort new addition — SMS templates
already designed, this is mostly wiring them in.

**Phase 4 — Inventory.** Natural extension of the existing battery/asset
tracking domain.

**Phase 5 — Ticketing.** Close in shape to the existing site verification/
check-in flow.

**Phase 6 — Basic CRM.** Likely just views/notes on top of the existing
customers table — to be CONFIRMED, not assumed, once this phase starts.

**Phase 7 — Chat.** Deliberately deferred and flagged for reassessment.
Most technically demanding of the set, and WhatsApp already works as a
contact channel. Confirm this solves a real operational gap before
building anything.

Each phase runs in its own branch (one branch per phase — see CLAUDE.md).
Before confirming any phase done, the owner checks it out locally
(`git checkout <phase-branch>`) and runs it on localhost — not just
Claude's word that "done when" criteria are met. Merges to `main` happen
only after that. Detail (scope, done-when criteria) expands here from a
one-liner when a phase becomes active. The DeepSeek delegation
confirmation checkpoint (see DELEGATION.md) resets at the start of each
new phase.

---

## COMPLETED PHASES
**Phase 1 — Mobile Fixes.** Completed 2026-09-04. Viewport meta tag added
(previously missing entirely). Sidebar nav rebuilt as a push-open
off-canvas drawer (a bottom tab bar was tried and explicitly rejected);
topbar search collapsed to an icon-only trigger; every view's table
wrapped in a `.table-scroll` box, horizontally contained always and
height-capped with a sticky header on phones — Batteries alone additionally
gets a frozen first column, a deliberate per-table decision, not a general
pattern. Stat-card grid tightened to 2 columns on phones. DESIGN.md's
Mobile Behavior section filled in from the resulting code, including
several real bugs found and fixed along the way (a topbar `overflow:hidden`
that was silently clipping the profile dropdown; a `border-collapse`
setting that silently broke `position:sticky` on table cells; a flex
`min-width:auto` default that was forcing the page wider than the
viewport). The five-view table-wrapper replication was delegated to
DeepSeek via `ask_deepseek.py` — its first real run against the live API,
verified clean (diff-checked) on all 5 calls. Owner confirmed through
extensive real-device (not just dev-tools) testing throughout the phase,
per this phase's own "before confirming" requirement.

**Phase 0 — Separate & Polish.** Completed 2026-09-04. Backend split into
`routers/` + `db/` (one file per domain: auth, permissions, sites,
batteries, users); frontend split into per-view HTML fragments, ES
modules, and CSS files; shared permission-check function consolidated into
one place. ARCHITECTURE.md and DESIGN.md drafted from the resulting code.
Owner confirmed by running the `phase-0-separate` branch locally and
clicking through sign-in, batteries, sites, and permissions.