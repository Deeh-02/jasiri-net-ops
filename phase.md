# PHASES.md — Live Status Tracker (JASIRI NET OPS)

Check this file before starting any work. If a request doesn't match the
current phase's declared scope, flag it — don't do it anyway.

Completed phases collapse to one summary line (name + completion date)
once confirmed done — full detail below is only kept for the active phase
and whatever's still upcoming.

---

## ACTIVE PHASE: Phase 0 — Separate & Polish

### In scope
- Split the tangled backend into isolated router files: auth, batteries/
  assets, sites, permissions, users.
- Isolate the frontend per view — own file, own naming, so nothing
  collides between views.
- Bundle in small visual/UX fixes while files are already open for the
  move (not new features — fixes to what's already there).
- Extract the shared permission-check function into exactly one place,
  called by every domain that needs it.

### Explicitly out of scope
- No new features of any kind.
- No mobile work — that's Phase 1, deliberately after separation so it's
  not done twice on code that's about to move.
- No finishing incomplete functionality — that's Phase 2, deliberately
  kept separate so a break is traceable to either "the move" or "new
  logic," never both at once.

### Done when (behavioral tests, not descriptions)
- Deleting any one domain's router file does not break any other domain's
  routes from loading or responding.
- Deleting any one frontend view's file does not affect any other view's
  rendering or behavior.
- Grep the whole frontend for a given CSS class or JS function name used
  in one view — it does not appear, unprefixed/unnamespaced, in any other
  view's file.
- There is exactly one function that performs a permission check, and
  every domain that checks permissions calls that function — grep confirms
  no second implementation exists anywhere.
- The app runs and every existing feature that worked before the split
  still works after it, unchanged in behavior (this phase is a move, not
  a fix — if something was broken before, it's still broken after, just
  in its new isolated location).
- DESIGN.md and ARCHITECTURE.md: every section that currently says "(not
  yet determined)" instead has real content, drafted by Claude from the
  actual resulting codebase, AND the owner has reviewed each section and
  either approved it or corrected it. Check by opening both files — if any
  section still reads "(not yet determined)" (outside DESIGN.md's Mobile
  Behavior section, which is expected to wait for Phase 1), this criterion
  is not met.

### Before confirming
Owner checks out the phase branch locally (`git checkout phase-0-separate`),
runs the app, and clicks through it on localhost — same as any other day,
just looking at the branch's version instead of `main`. Confirmation isn't
just Claude's word that the "done when" criteria are met; the owner has
actually seen it running first.

### On confirmation
When the owner confirms these are met: update CLAUDE.md's Stack and
Directory structure sections with the real, confirmed layout (not left as
placeholders), then collapse this section to a one-line summary below.

---

## UPCOMING PHASES (order locked, detail not yet expanded)

**Phase 1 — Mobile Fixes.** Pass over the now-separated views for phone
rendering. Runs after separation so it's touched once, not twice.

**Phase 2 — Finish Incomplete Functionality.** Features that exist but
aren't fully built get finished individually, one at a time, each in its
own already-isolated file. Kept separate from Phase 0's structural move
on purpose — see "Done when" note above on traceability.

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
(none yet)