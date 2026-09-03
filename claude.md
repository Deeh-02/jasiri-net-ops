# CLAUDE.md — JASIRI NET OPS

Read at the start of every new session. Not re-read mid-session — instruction
changes only take effect in a fresh session.

## Stack
(fill in once confirmed: backend framework, frontend approach, DB, hosting)

**Pre-Phase-0 status:** not yet separated. Backend is currently one large
file; frontend is loosely separated at best. Don't assume any domain
boundaries exist until PHASES.md marks Phase 0 complete — check PHASES.md
for current status before operating on an assumed structure.

**Update this section when Phase 0 is marked complete** — replace the
placeholder above with the actual confirmed stack, not left as-is.

## Directory structure
(fill in once Phase 0 establishes the isolated layout)

**Pre-Phase-0 status:** no isolated layout exists yet. See PHASES.md for
what Phase 0 is currently doing to get there.

**Update this section when Phase 0 is marked complete** — replace the
placeholder above with the actual isolated layout, not left as-is.

## Hard rules
- One branch per phase. The phase branch (e.g. `phase-0-separate`) is
  created once, at the start of the phase — that single branch creation is
  what satisfies "branch before any change" for everything that happens
  within the phase. Individual tasks inside a phase — a router split, a
  CSS fix, a DeepSeek delegation — do NOT get their own sub-branches; they
  all land inside the same phase branch, which merges once at phase end.
- Never touch files outside the current phase's declared scope (see PHASES.md).
- Edit in place rather than full-file rewrites, where the file already exists
  and the change is incremental.
- One shared permission-check function, used everywhere. Never duplicate
  permission logic per-domain.
- No cross-file naming collisions — this includes CSS classes and JS scope,
  not just file/module names.
- Before any schema or infra change: stop and show the owner what the
  change is and what could go wrong if it's wrong. Wait for approval
  before proceeding — same mechanics as the delegation checkpoint in
  DELEGATION.md. If unattended when this triggers: STOP AND WAIT, same
  default as delegation. Don't proceed on your own judgment and don't
  silently skip the change either.
- Ask before marking a phase complete, even if PHASES.md's "done when"
  criteria look met to you. When Phase 0 specifically is confirmed
  complete, that's also the trigger to fill in the Stack and Directory
  structure placeholders above — don't leave them stale once they're no
  longer true.
- See RULES.md for code quality standards — every file written or edited
  should meet that bar, not just pass tests. This applies to DeepSeek's
  output too, not only Claude's own — see the delegation section below for
  how RULES.md gets enforced on delegated code.

## Phase discipline
Check PHASES.md before starting any work. If a request doesn't match the
current phase's declared scope, say so and ask rather than doing it anyway.
Update PHASES.md only on the owner's confirmation that a phase's "done when"
criteria are met.

## Model
Default to Sonnet for most work. This isn't self-enforcing — a text file
can't force a model switch mid-session, so don't rely on remembering it as
a standalone rule. Instead, it's tied to moments that already stop and
interrupt the flow: the delegation checkpoint, the schema/infra risk flag,
and anything on the "never delegate" list. Every time one of those fires,
state which model you're currently on as part of that stop, and switch to
Opus (`/model opus`) if you're not already on it before actually doing the
judgment work. This makes a missed switch visible to the owner even if you
forget — they'll see "Sonnet" stated at a checkpoint and can catch it.
Switch back to Sonnet (`/model sonnet`) once past that point.

## Delegation to DeepSeek
See DELEGATION.md for the full policy — status, cost model, what's safe to
delegate, verification, and the confirmation checkpoint. Not repeated here
so this file stays small; DELEGATION.md is the one you actually read
before delegating anything.