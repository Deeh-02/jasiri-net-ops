# PHASES.md — Live Status Tracker (JASIRI NET OPS)

Check this file before starting any work. If a request doesn't match the
current phase's declared scope, flag it — don't do it anyway.

Completed phases collapse to one summary line (name + completion date)
once confirmed done — full detail below is only kept for the active phase
and whatever's still upcoming.

---

## ACTIVE PHASE: Phase 1 — Mobile Fixes

Pass over the now-separated views for phone rendering. Runs after
separation so it's touched once, not twice.

Detail (scope, done-when criteria) not yet expanded — per this file's own
convention, that happens when work on the phase actually begins, the same
way Phase 0's one-liner became a full spec before that phase started.

---

## UPCOMING PHASES (order locked, detail not yet expanded)

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
**Phase 0 — Separate & Polish.** Completed 2026-09-04. Backend split into
`routers/` + `db/` (one file per domain: auth, permissions, sites,
batteries, users); frontend split into per-view HTML fragments, ES
modules, and CSS files; shared permission-check function consolidated into
one place. ARCHITECTURE.md and DESIGN.md drafted from the resulting code.
Owner confirmed by running the `phase-0-separate` branch locally and
clicking through sign-in, batteries, sites, and permissions.