# DESIGN.md — UI/UX Reference (JASIRI NET OPS)

**Status: drafted by Claude from the post-Phase-0 codebase, pending owner
review.** Reconstructed from the actual CSS now split across
`static/css/common.css` + one file per view — describes the conventions
already in use, not new proposals.

## Accent / color theme

**Two themes since 2026-11-14 (owner's call).** Dark stays the default;
light is `data-theme="light"` on `<html>`, switched with the sun/moon button
in the top bar (`static/js/theme.js`, remembered in `localStorage.theme`,
applied before first paint by an inline script in `index.html`). Both are
one set of token names defined twice at the top of `common.css`; a view
must never write a hex or `rgba()` of its own: use a token, or
`color-mix(in srgb, var(--accent) 12%, transparent)` for a tint. Light uses
darker green/amber/red/blue so they pass as text on white, white cards on a
grey page, and a soft shadow for `--edge` instead of a lit edge. Switching
pours the new theme out from the button as a growing blob with a wobbling,
liquid edge, 1.1s (`blobFrames` in `theme.js`: clip-path polygons on the
View Transition's new snapshot; instant without View Transitions or with
animations off). Shapes are
rounder in both: `--radius` 16px for cards, panels, tables and dialogs,
`--radius-sm` 10px for buttons, inputs and chips. The values listed below
are the dark ones.

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

### Phase 5 shared foundation (`common.css` top + `common.js` bottom)

Use these before inventing a view-specific version; a view's CSS only adds
what is truly its own.

- **Tokens** on `:root`: `--radius-sm` 6px, `--radius` 10px, `--page-pad-x`
  (40px desktop / 16px phone; `.content` and the top bar both use it),
  `--form-max` 760px, `--section-gap`, `--ring` (soft focus ring),
  `--scrollbar` 10px.
- **Page header:** `.page-header-row` with `h1.page-title` on the left
  (optionally wrapped with a `p.page-subtitle` under it) and the actions on
  the right (`.page-header-actions` for several). Every page uses it,
  including form pages (Add User, Add Role …), Settings and Site detail. A
  back link sits above it as `.header-sub-link.page-back`. Section titles
  inside a page: `.section-title`.
- **Buttons:** `.btn-primary` (the page's main action), `.btn-secondary`,
  `.btn-danger` (deletes, irreversible), `.btn-ghost`. Usable anywhere, not
  only inside `.modal-actions`; same hover, pressed (`scale(0.97)`), focus
  and disabled (`opacity .45`, `not-allowed`) behaviour everywhere.
  `.btn-add-site` remains as the older name for a primary header button.
- **Focus:** every control shows `outline: 2px solid var(--accent)` on
  `:focus-visible`; text fields keep the accent border and add `--ring`
  (a box-shadow, so a view's `outline: none` can't remove it). Mouse focus
  on a button shows nothing.
- **Motion is on unless the person turns it off** (owner's call,
  2026-11-14): Settings → Appearance → Animations, saved per browser
  (`localStorage.motion`, `data-motion="off"` on `<html>`, applied before
  first paint). Off turns every animation and transition into an instant
  change and removes lifts/scales. The device's own
  `prefers-reduced-motion` is deliberately NOT followed: Windows has
  "Animation effects" off on many PCs, and the app then showed no motion at
  all. Never gate motion on the media query; use
  `:root[data-motion="off"]` in CSS and `motionOn()` (`theme.js`) in JS.
- **Filters above a table:** `.toolbar` (wraps, sits directly above the
  table or its summary) with `.toolbar-field` (small mono uppercase label
  span + control) and `.control` for inputs/selects (drawn chevron,
  dark date picker). Never put filters inside the page header.
- **Summary before a long table:** `.summary-strip` of `.summary-item`s
  (`<strong>` number + label; `.good`/`.warn`/`.bad` tone the number). A
  `button.summary-item` with `aria-pressed` acts as a quick filter (SMS
  Status by status, Stock's "low on stock"). Put the thing someone must act
  on first.
- **Loading / empty / error:** `stateHtml(kind, text, title)` and, inside a
  `<tbody>`, `stateRow(colspan, kind, text, title)` or
  `showEmptyIfNone(tbody, count, text, title)` (works out the colspan).
  Empty states say what to do next ("Try a wider date range…").
- **Confirmations:** `await confirmDialog({ title, message, confirmLabel,
  danger })`, never `confirm()`. Enter confirms, Escape/outside click
  cancels, focus returns to the button that opened it. `danger: true` for
  anything that deletes or can't be undone.
- **Form feedback:** `showMessage(id, text, isError)` on a `.form-msg`.
  Success fades after 4s; an error is a tinted box that stays until the
  person edits that form. While the request a submit starts is in flight,
  its submit button is disabled with a spinner (`aria-busy`) — automatic,
  see ARCHITECTURE.md; a form that disables its own button is left alone.
- **Tables (visual pass):** a table inside `.table-scroll` sits in one
  rounded panel (the wrapper has the surface, border and radius; the table
  drops its own frame). Dividers are soft (`--rule-soft`), even rows get a
  faint tint (`--row-alt`) and hover a clear one (`--row-hover`); both are
  opaque so the phone's pinned first column covers what slides under it.
  Rows have 14px vertical padding; logs use `.table-dense` (Inventory Log,
  SMS Status). Numbers: `th.num`/`td.num` (right-aligned, tabular figures —
  every table uses tabular figures). Quieter secondary cells: `td.muted`
  (dates, categories, ids) or a `.cell-sub` line under a name. The column a
  table is ordered by carries `aria-sort` and shows an accent ↓/↑. A
  toolbar can end with `.toolbar-count` ("45 transactions").
- **Every table is compact on phones** (`.table-compact` on every
  `.table-scroll`): one line per row, the page scrolls, the first column is
  pinned and casts a soft shadow once swiped (`.is-scrolled-x`).
- **Loading without flashing:** a main table's `<tbody data-skeleton="N">`
  shows N-column skeleton rows on first visit; `stateRow(n, "loading")`
  draws skeletons too; `skeletonCards(n)` for a stat grid. Reloading a table
  that already shows data: `markRefreshing(tbody, n)` keeps it on screen,
  slightly dimmed, until the new rows replace it. Live refreshes redraw only
  what changed. Going to another page starts at its top; staying on a page
  (a filter in the URL) doesn't replay its entrance.
- **Motion:** menus, pop-overs, dialogs and form messages appear with a
  ~150ms fade and 4px drop; buttons, summary filters and cards have a
  pressed state. Nothing moves that isn't changing; animations off turns
  all of it off.
- **Sidebar (sectioned, 2026-11-09):** seven titled sections — Operations
  (Batteries, Sites), Network (Network Status, Network Analysis, OLT;
  added 2026-11-11; Network Status used to be a button on the Sites page),
  Technical Activity Log, Inventory, Reports,
  Alerts, System (Users, Roles). Section titles are 11px (12px in the
  phone drawer) uppercase JetBrains Mono, grey, with a fold chevron; space,
  not a divider line, separates sections. A title (and its chevron) turns green only while
  one of its pages is open — hover just lifts the grey slightly (never on
  touch screens, where hover sticks after a tap). Every page is its own row: a 16px outline icon and a title-case
  JetBrains Mono 13px label, hanging off a thin guide line down the left
  of the section. The current page carries the accent tint and its stretch
  of the guide line turns green; its section's title turns green
  (`.nav-section-title.current`). Sections start open; folding one is remembered per browser
  (`localStorage.navCollapsedGroups`) and landing on a page inside a folded
  section opens it. Short labels in the sidebar (Overview, Submit Report,
  Activity Types, Inventory Report); the full names stay in `data-label` for
  the command palette. Unread alerts use an amber count bubble.


- **Motion (2026-11-14, owner's reference clip):** the page you leave
  blurs away (`.view-ghost`, a stripped copy), then the new one opens by
  blurring in from heavy blur, block after block, then cards and table rows (`.view.is-entering`, set by
  `static/js/motion.js` for ~2.6s after arriving, so live refreshes never
  replay it; no blur on phones); the current item's highlight slides to it
  instead of jumping (sidebar row, tab underline including across pages
  that share a tab bar, the pressed button of a `.fleet-range`/`.sr-range`
  toggle), drawn by one `.slide-indicator` per container; cards lift 5px on
  hover (mouse screens only); headline figures
  (`.stat-value`, `.rev-card-value`, `.rev-tile-value`, `.summary-item
  strong`) count up when their page opens and roll from old to new when a
  refresh changes them; income bars and progress bars grow in. All of it is
  off when animations are switched off in Settings, and without the script
  every item keeps its own highlight.
- **Login card (2026-11-14):** `.login-frame` carries a conic gradient
  (accent, with a fainter blue lobe opposite) turning once every 7s, drawn
  twice from one angle (`--glow-angle`, an `@property`): a 1.5px stroke on
  the card's edge and the same gradient blurred 30px behind the card as the
  bloom. Still when animations are off.
- **Dashboard (2026-11-14, `home.css`):** headline tiles (`.home-kpi`) over
  a 12-column grid of cards (`.home-card`, `--span`). Colour there means
  state only (`.tone-good/warn/bad/info/idle` set `--tone`), always beside
  a written label and a count; a "how many of each" is a split bar with a
  2px gap between slices and its legend; rankings and over-time charts are
  one hue (download and upload keep Network Analysis's green and blue).
  Charts are drawn at the width they are shown at; hovering shows a
  floating label (`.home-tip`), and a line chart a rule with a dot per
  line. A reading nobody took is a gap in the line, never a zero.
- **Polish pass (2026-11-13):** panels carry `--edge` (a lit top edge via
  `box-shadow`, since a drop shadow does nothing on black); the primary
  button has a soft top-to-bottom gradient and glows on hover; the top bar
  is translucent with a blur; the page has a faint accent glow behind the
  dot grid. A pressed toggle (`.fleet-range`, `.sr-range`) is an accent
  tint with an accent ring, never a white pill. Forms use a visible label
  (`label.form-field`, optional `.form-hint`) rather than a placeholder as
  the label. Table cells holding one fact (date, plan, phone) don't wrap.

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
  text) — never a solid-fill pill. A pill's color can flag a sub-state
  without changing its label or count: `.status-pill.deployed-flagged`
  (Phase 2) still reads "Deployed" but recolors to the `--danger` tint for
  a battery sitting at a site confirmed offline — same technique as
  reusing the pill's existing vocabulary rather than inventing a new one.
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
- **Row accent border, and a `border-collapse` gotcha (Phase 2):** a
  flagged row's left-edge accent (`.battery-row-flagged`, `.site-row-
  offline`) only paints reliably when it's a border on the row's first
  `<td>`, not on the `<tr>` itself. A border set directly on `<tr>` is
  only ever painted under `border-collapse: collapse` (Check Sites' table
  uses the default, so `.site-row-offline`'s `<tr>` border "just worked")
  — under `border-collapse: separate` (`.dashboard-logs-table`, needed for
  its sticky header) the browser never paints it. Put the border on the
  cell, not the row, in any table using `separate`.
- **Row action icons are inline SVG, not an icon font** — each icon is a
  small function returning a raw `<svg>` string (`common.js`'s
  `batteryIconSvg`/`moveIconSvg`/`editIconSvg`/`viewIconSvg`/
  `deleteIconSvg`), so every view draws icons the same way rather than each
  view inventing its own.
- **Toggle switches** (`.perm-toggle`, role permission grid): custom
  checkbox-driven pill toggle, not a native checkbox — accent-colored track
  when checked, sliding thumb.
- **Tab groups** (Phase 5): one look everywhere, the shared `.tabs`
  container + `.tab` buttons in `common.css` — an underline under the
  current tab (`.active`, `.is-active` or `aria-selected="true"`), Inter
  text, no fill. The slanted parallelogram tabs are gone. Each view keeps
  its own class (`.settings-tab`, `.dashboard-tab-slant`,
  `.inventory-item-tab`, `.sites-tab`) purely as its JS hook, so no click
  handler fires on another view's tabs; the look comes only from `.tab`.
  Day/Week/Month period switches (`.sr-range`, `.fleet-range`) are a
  different control (a segmented switch) and keep their own look.
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
- **Fixed-choice custom dropdown** (`.move-reason-field`/`.move-reason-btn`/
  `.move-reason-menu`/`.move-reason-option`, the Move Battery modal's
  "Reason" field, Phase 2): same button+menu mechanics as
  `.charge-dropdown` again, this time standing in for a plain `<select>`
  rather than a free-text input — a bare `<select>` can share an input's
  background/border/padding and still look out of place next to it: the
  browser keeps its own chevron and box-model on the closed control, and
  the open option list is an OS-native popup that mostly ignores the
  app's dark theme. Use this pattern instead of `<select>` for any future
  form field with a short fixed list of choices. Two things worth
  remembering if adding another one: a `<button>` inside `.panel-form`
  will otherwise inherit `.panel-form button`'s primary-action look
  (solid `--accent` fill, bold text) — needs its own rule scoped with at
  least two classes (e.g. `.panel-form .move-reason-btn`) to outrank it on
  specificity; and its placeholder-style label needs an explicit
  `var(--text-dim)` color to actually match a real `<input>`'s
  `::placeholder`, which was left to the browser's own default dimming
  (an opacity-faded `var(--text)`, not `var(--text-dim)` itself) until
  Phase 2 added an explicit `.panel-form input::placeholder` rule.

- **Direction and status are glyph + word, never colour alone** (Trends,
  Revenue): `▲ Up` / `▼ Down` / `► Flat`, and `● Investigate` /
  `● Attention` / `● Positive` for the red / amber / green alert levels,
  coloured with the same `--accent` / `--warn` / `--danger` tokens as
  everywhere else. A person who can't tell green from red still reads the
  word. Revenue's alert rows add a 3px left border in the same colour and
  show the rule id (JetBrains Mono, `--text-dim`) beside the numbers that
  triggered it — an interpretation always carries the rule that produced it.
- **Money is shown exactly as the server formats it** (`KSh 10,810`,
  `-KSh 3,220`), never re-formatted or rounded in the browser, so a page
  can't disagree with the stored report. A figure that doesn't exist is
  "No comparable data", never `0` or `—` that could be read as zero.
- **Line charts are inline SVG** (Revenue's trend), drawn with the page's own
  tokens: `--accent` for the series, `--info` dashed for the 7-day average,
  a 12%-opacity `--info` band for the usual range, hollow marker for a
  period still in progress, and a `--danger` / `--warn` ring on an unusual
  point. Every point carries a `<title>` tooltip with its value and, where
  it applies, the rule that flagged it. Bar charts elsewhere (Trends, the
  site report) stay CSS-div bars — SVG is for charts that need a line or a
  band.
- **Native `<select>` on report pages is a known exception** to the
  custom-dropdown rule above: Trends' period picker already uses one, and
  Revenue's comparison and series pickers match it rather than introducing
  a second look on the same report screens. New *form* fields should still
  use the custom dropdown.

- **The assistant widget** (`chat.css`, `.chat-*`) is the one floating
  element: a pill "Ask" button bottom-right (accent outline on
  `--surface`), opening a 390px panel there on desktop and the full screen
  on phones (≤760px). The user's bubbles sit right on a faint accent tint;
  the assistant's sit left on `--surface-2`; an error is a full-width
  `--danger` outlined bubble with a Retry button; a three-dot typing
  indicator shows while waiting. Text is shown as typed (`pre-wrap`), so
  the assistant is told to answer in plain text, not markdown.

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
