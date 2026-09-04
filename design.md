# DESIGN.md — UI/UX Reference (JASIRI NET OPS)

**Status: drafted by Claude from the post-Phase-0 codebase, pending owner
review.** Reconstructed from the actual CSS now split across
`static/css/common.css` + one file per view — describes the conventions
already in use, not new proposals.

## Accent / color theme

Dark theme, defined once as CSS custom properties in `common.css`'s `:root`
(every view's CSS reads these, never redefines its own palette):

- `--bg` `#000000`, `--surface` `#0a0a0a`, `--surface-2` `#141414`,
  `--border` `#262626` — background layers, darkest to lightest.
- `--text` `#e9edf2`, `--text-dim` `#8b96a5` — primary vs. secondary text.
- `--accent` `#3ddc97` (green) — the one brand/action color: primary
  buttons, active nav state, "charged"/"at base"/"online" states, focus
  rings.
- `--warn` `#f5a623` (orange) — "deployed", "arrived", "needs check".
- `--danger` `#ef5b5b` (red) — delete actions, "low" charge, "offline"/
  "still down" states.
- `--info` `#4fa8ff` (blue) — "charging", "in transit".

Status colors are consistent cross-view: green always means the good/
resolved state, orange always means in-progress/attention, red always means
bad/destructive, blue always means an active in-flight state — regardless of
which view (battery charge, movement status, site online/offline all reuse
this same four-color vocabulary via `.status-pill` modifier classes).

## Typography

Three families, loaded once via a Google Fonts `@import` in `common.css`:

- **Space Grotesk** (500/700 weight) — headings, page titles, stat values,
  primary buttons, brand name. The "important/structural" font.
- **Inter** (400/500/600) — body text, form inputs, nav links, menu items.
  The default reading font.
- **JetBrains Mono** (400/500) — nav headings, status pills, badges, table
  headers, timestamps, command-palette labels. Used specifically for
  short, uppercase, letter-spaced labels — gives data/metadata a distinct
  "system readout" feel separate from prose.

## Component style conventions

- **Buttons:** 6px border radius everywhere. Primary = solid `--accent`
  fill with dark text (`#10141a`); secondary = `--surface-2` fill with a
  `--border` outline. Hover = opacity 0.9; active = `scale(0.97)`. No
  exceptions found across modal, panel-form, or standalone buttons.
- **Cards/panels** (`.stat-card`, `.perm-section`): `--surface` background,
  1px `--border`, 10px radius, subtle `translateY(-2px)` lift + shadow on
  hover.
- **Pills** (`.status-pill`, `.hour-check-pill`, `.hb-tag`): small,
  uppercase, letter-spaced, colored text on a low-opacity tint of the same
  color (e.g. `rgba(61, 220, 151, 0.12)` background with solid `--accent`
  text) — never a solid-fill pill.
- **Modals** (`.modal-overlay` / `.modal-box`): centered, blurred dark
  backdrop, 360px default width (`.dashboard-modal-box-lg`, 640px, for
  modals with a table inside — the View Battery detail view and, Phase 2,
  the stat-card click-through detail), `fadeIn` animation, always paired
  with a `.modal-actions` secondary/primary button row (the two large
  modals use a close-`×`-button header instead, no bottom action row —
  they're read-only detail views, not forms).
- **Tables:** header row uses `--surface-2` background with dim uppercase
  mono labels; body rows highlight `--surface-2` on hover; every table row
  action (edit/delete/move/view) is a 30×32px square icon button with a
  `--surface-2` fill, `--border` outline, turning `--accent` (edit/view) or
  `--danger` (delete) on hover. No exceptions anymore — Roles' Edit/Delete
  buttons had no styling at all until Phase 2, now match this exactly.
- **Row action icons are inline SVG, not an icon font** — each icon is a
  small function returning a raw `<svg>` string (`common.js`'s
  `batteryIconSvg`/`moveIconSvg`/`editIconSvg`/`viewIconSvg`/
  `deleteIconSvg`), so every view draws icons the same way rather than each
  view inventing its own.
- **Toggle switches** (`.perm-toggle`, role permission grid): custom
  checkbox-driven pill toggle, not a native checkbox — accent-colored track
  when checked, sliding thumb.
- **Tab groups** (`.dashboard-tab-slant`, Settings' `.settings-tab`):
  slanted parallelogram tabs (`clip-path`), uppercase Space Grotesk,
  `--surface-2` background with dim text at rest, active tab drops to
  `--surface` background with `--accent` text — not a filled pill, not an
  underline. Used by the View Battery modal's Details/Logs tabs and (Phase
  2) Settings' Profile/Password tabs; kept as two independently-styled
  classes rather than one shared one, on purpose — each file's own click
  handler wires its own tabs by bare class name, so sharing one class would
  mean each handler also firing on the other's buttons.
- **Inline text-input autocomplete** (`.move-by-field`/`.move-by-suggestions`/
  `.move-by-option`, the "Moved by" field): a positioned dropdown anchored
  to a text input, filtered live as you type, scrollable past a handful of
  matches. Visually mirrors `.charge-dropdown`/`.charge-menu`'s dropdown
  look (`--surface` box, `--border` outline, shadow) but kept as its own
  component — that one's a short fixed list in JetBrains Mono for a status
  picker, this one needs scroll for a potentially long name list and uses
  Inter to match the input it's attached to, not a settings-toggle read.
  The suggestion list is a convenience only — free text past it is always
  accepted; a non-blocking `--warn`-colored note appears if what's typed
  doesn't match a known user, but nothing ever blocks on it.

## Spacing conventions

No formal scale variable, but a consistent rhythm shows up throughout:
**4 / 6 / 8 / 10 / 12 / 16 / 20 / 24 / 32px**, roughly doubling as you move
from tight inline gaps (icon-to-label: 6-8px) to section spacing
(stat-grid margin-bottom: 32px). Border radius is consistently smaller for
small controls (4-6px: buttons, pills, inputs) and larger for containers
(10-12px: cards, modals, the login box).

## Mobile behavior

**Status: decided by Claude during Phase 1, pending owner review** — same
convention as the rest of this file: drafted from what actually shipped,
not a proposal.

**Breakpoint.** One number, `max-width: 760px`, used everywhere — this was
already the convention Phase 0 started (topbar, stat-grid) before Phase 1
existed; Phase 1 just kept using it rather than introducing a second one.

**Shell.** A `<meta name="viewport" content="width=device-width,
initial-scale=1">` was missing entirely — added to `index.html`, and is
the reason nothing below could even be tested correctly until it existed.

**Nav.** The 220px sidebar collapses to a 0-width flex sibling of the
content column, expanding to 240px (`max-width: 82vw`) on tap of a
hamburger button in the topbar — pushing content over via a plain flexbox
width change, not floating over it. A dim backdrop covers the pushed
content only; the topbar and the open drawer itself stay undimmed. A
bottom tab bar was tried first and explicitly rejected — nav stays a side
drawer, just off-canvas by default. Nav rows get 14px vertical padding in
the drawer (up from the desktop row's tighter padding) as the one deliberate
touch-target adjustment.

**Search.** The topbar search bar becomes an icon-only 34×34px trigger
(matching the notification/avatar button size) with the label and ⌘K badge
hidden — it already only opened the full-screen command palette rather than
containing an inline input, so nothing about the interaction changed, only
its resting size.

**Hamburger icon.** Deliberately bare — no box, border, or background like
the other topbar icon buttons; just the icon glyph and normal click padding.

**Tables.** Every view's table sits inside a `.table-scroll` wrapper:
`overflow-x: auto` so the TABLE scrolls sideways in its own contained box,
never the page. On phones the box is additionally height-capped
(`max-height: 55vh; overflow-y: auto`) with a sticky header row
(`position: sticky; top: 0`) — so the topbar/page-header/stat-cards above
never get pushed off-screen by a long list, and column labels stay visible
while rows scroll under them. Scroll cues are the (restyled, thinned)
native scrollbars themselves — a custom gradient/shadow overlay was tried
and read as a heavy visual "line" rather than a subtle hint, so it was
removed. **Frozen first column is Batteries-only** — a deliberate,
per-table decision, not a general pattern: `.col-frozen` pins the Battery #
column (`position: sticky; left: 0`) while the rest of that one table
scrolls underneath it. The other five tables (Sites, Movements, Check
Sites, Users, Roles) scroll as a whole, first column included — this was
explicitly decided against replicating the pin there.

**Touch targets.** Topbar controls (hamburger, search, notifications,
avatar) are ~34px. Table row-action buttons stayed at the existing 30–32px
desktop convention (see Component style conventions above) — considered
for enlarging, deliberately left as-is pending real on-phone feedback
rather than resized preemptively; no complaint surfaced once tested on an
actual device, so it stayed.

**Stat cards.** 2 columns on phones (2/2/1 for the current 5 counters),
down from the desktop 5-column grid — tried 3 first, corrected back to 2
per owner feedback. Compact padding (10px 12px vs desktop's 18px 20px) and
smaller value type (20px vs 28px) so the grid doesn't dominate the screen
above the table.

**iOS-specific quirks worth knowing, not treated as done-for-good:**
`-webkit-overflow-scrolling: touch` is required on `.table-scroll` for real
momentum scrolling on at least one tested device — removing it (tried, to
fix a sticky-header bounce glitch below) made the last rows of a long table
unreachable by flick gesture, confirmed via `scrollTop`/`scrollHeight`
math, not just a look. A `box-shadow` + `transform: translateZ(0)` on the
sticky header reduce, but may not fully eliminate, a brief visual overshoot
during a fast upward flick's rubber-band bounce — an accepted, low-priority
cosmetic gap rather than something chased indefinitely at the cost of
scroll reachability.

**Phase 2 update:** `overscroll-behavior-y: contain` used to sit alongside
that box-shadow/translateZ(0) pair as a third mitigation for the same
bounce seam. Removed in Phase 2 — it was also silently blocking page
scroll on desktop entirely (declaring only `overflow-x` computes
`overflow-y` to `auto` too, so `.table-scroll` became a phantom vertical
scroll container even with nothing to scroll there — same interaction as
the topbar bug documented below), and on phones it was stopping scroll
from handing off to the page once the table's own internal scroll maxed
out, which read as the table just eating scroll input. Losing `contain`
trades a very slightly more visible (already low-priority, already not
fully eliminated) bounce seam for scroll that actually works in both
places — the right trade. If the seam ever needs revisiting, don't reach
for `contain` again without re-checking both effects above.

**A fixed bug worth documenting as a "why," not just a "what":** the
topbar previously used `overflow: hidden` to stop its brand/icon row from
forcing the page wider — but that same rule was silently clipping the
profile dropdown menu, which renders below the topbar's own box on
purpose. Switched to `overflow-x: clip` (leaves `overflow-y` genuinely
`visible` instead of the `hidden`-on-one-axis quirk silently turning the
other axis into `auto`) — don't revert this back to `overflow: hidden` to
"simplify" it; that's the bug, not a stylistic choice.
