# RULES.md — What Good Code Looks Like (JASIRI NET OPS)

This is not generic best practice. It's calibrated to this project's actual
situation: the owner reads code but doesn't write it fluently, has been
burned by tangled/interdependent files before, and needs to be able to
open any file cold — weeks later — and follow what it does without
re-learning it. "Good" here means readable and safe to touch, not clever.

## The core test
Before code is considered done, it should pass this: **the owner could
open this file with no other context and roughly follow what it does,
even if they couldn't have written it themselves.** If that's not true,
it's not done — regardless of whether it works.

## Naming
- Names say what something IS or DOES, not what it technically is.
  `active_battery_count`, not `cnt` or `x`. `send_expiry_reminder()`, not
  `process()`.
- No abbreviations that aren't obvious. `sites`, not `sts`. `permission`,
  not `perm`, unless it's already established as a project-wide convention.
- Booleans read as yes/no questions: `is_active`, `has_permission`, not
  `active_flag` or `status2`.

## Function size and shape
- A function does ONE thing. If describing what it does needs the word
  "and" more than once, it's probably two functions.
- Prefer functions short enough to view on one screen without scrolling.
  Not a hard line count, but if it's sprawling, that's a signal to split it.
- Early returns over deep nesting. A function that's 4 levels of
  if-inside-if-inside-if is a function that's hard to trust is correct.

## Comments
- Comment WHY, not WHAT. `# using session pooler, direct connection fails
  under WSL` is useful. `# connect to database` above a line that obviously
  connects to a database is noise.
- Every function that isn't trivially self-explanatory from its name gets
  one line above it describing what it does, in plain language — written
  as if explaining it to the owner directly, not to another programmer.
- If a piece of code exists because of a past bug or workaround (like the
  route collision, or the WSL pooler issue), say so in a comment. Future
  edits need to know NOT to "simplify" it back into the bug.

## Error handling
- Never fail silently. If something can go wrong (a DB call, an external
  request, a missing value), handle it visibly — log it or raise it, don't
  swallow it in a bare `except: pass`.
- Error messages should say what happened AND what to check, not just
  "an error occurred." Written for the owner to read at 2am when something's
  down, not for a stack trace enthusiast.

## Consistency over cleverness
- If the codebase already does something a certain way (a pattern for
  routes, a pattern for permission checks), match it — don't introduce a
  second way to do the same thing because it's marginally more elegant.
  Two ways to do one thing is itself a bug waiting to happen.
- No premature abstraction. Don't build a generic reusable system for
  something used in one place. Build it plainly; generalize later if a
  second real use case actually shows up.
- Boring and obvious beats clever and compact, always, in this project.

## Isolation (ties back to CLAUDE.md hard rules)
- A file for one domain (auth, batteries, sites, permissions, users) only
  ever touches its own domain's data directly. If it needs something from
  another domain, it calls a function meant for that — it doesn't reach
  into that domain's tables or files itself.
- Shared logic (permission checks, DB connection handling) lives in exactly
  one place. If you're about to write a second version of something that
  already exists elsewhere, stop — that's a sign it should be imported,
  not re-implemented.

## Frontend specifically
- One file per view. A view's JS/CSS never assumes another view's file has
  already run or already defined something.
- CSS class names and JS variable/function names are scoped so nothing
  from `batteries.js` can accidentally collide with `login.js` — prefix
  or namespace where the platform doesn't isolate this automatically.

## What this is NOT
- Not a request for 100% test coverage, exhaustive documentation, or
  enterprise-grade architecture. This is a small, real, actively-used
  system run by one person plus one field tech — the standard is
  "trustworthy and readable," not "impressive."