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
  backdrop, 360px default width (640px for the one "large" modal — the
  battery detail view), `fadeIn` animation, always paired with a
  `.modal-actions` secondary/primary button row.
- **Tables:** header row uses `--surface-2` background with dim uppercase
  mono labels; body rows highlight `--surface-2` on hover; every table row
  action (edit/delete/move/view) is a 30×32px square icon button with a
  `--surface-2` fill, `--border` outline, turning `--accent` (edit/view) or
  `--danger` (delete) on hover.
- **Row action icons are inline SVG, not an icon font** — each icon is a
  small function returning a raw `<svg>` string (`common.js`'s
  `batteryIconSvg`/`moveIconSvg`/`editIconSvg`/`viewIconSvg`/
  `deleteIconSvg`), so every view draws icons the same way rather than each
  view inventing its own.
- **Toggle switches** (`.perm-toggle`, role permission grid): custom
  checkbox-driven pill toggle, not a native checkbox — accent-colored track
  when checked, sliding thumb.

## Spacing conventions

No formal scale variable, but a consistent rhythm shows up throughout:
**4 / 6 / 8 / 10 / 12 / 16 / 20 / 24 / 32px**, roughly doubling as you move
from tight inline gaps (icon-to-label: 6-8px) to section spacing
(stat-grid margin-bottom: 32px). Border radius is consistently smaller for
small controls (4-6px: buttons, pills, inputs) and larger for containers
(10-12px: cards, modals, the login box).

## Mobile behavior

(deferred until Phase 1 — leave as "not yet determined" until then, that's
expected, not a gap)
