# DELEGATION.md — DeepSeek Delegation Policy (JASIRI NET OPS)

**Status: built and running in production use.** `ask_deepseek.py` (repo
root — not `scripts/`, despite what this line used to say) covers the
DeepSeek API call, the `--design`/`--rules` context injection, and the
`--patch`/`--error-log` retry flow. `DEEPSEEK_API_KEY` must be set as an
environment variable — same pattern as `DATABASE_URL`, never hardcoded,
never allowed to appear in a delegated file's diff. First exercised for
real in Phase 1 (the five-view table-wrapper replication, all 5 calls
diff-verified clean); used again several times in Phase 2 (see "Patterns
observed" below) with the same track record — every call's diff came back
either clean or with only harmless, accepted deltas (a stripped trailing
newline, restored on apply). Treat that as a real track record now, not a
one-off — but the verification steps below still run on every call
regardless; they're not a first-call-only precaution.

**Cost model.** The owner is on a Claude subscription, so delegating doesn't
save Claude dollars — it saves Claude *usage-window quota*. DeepSeek calls
are billed separately, in real dollars, via DeepSeek's own API. The tradeoff
is "Claude quota" for "DeepSeek dollars," not "quota for free." Keep that in
mind before delegating something trivial enough that writing it yourself
would've been just as fast.

The script hardcodes `deepseek-v4-pro` — there's no flash/pro decision to
make per call, which is deliberate (one less thing that can silently
default wrong). Pro costs more per call than flash would have; the
"delegate generously, mistakes are contained" framing above still holds
structurally, but each individual delegation now costs a bit more DeepSeek-side
than a flash-based setup would. Thinking mode is explicitly disabled on
every call too — deterministic, low-latency output for a precisely-specified
task, not DeepSeek's slower/costlier default reasoning pass.

**What's safe to delegate — judgment vs. mechanical, not phase number:**
- **Judgment tasks** (deciding what code belongs where, how to untangle
  something, what an isolation boundary should be) — Claude does these
  itself, always, every phase, no exceptions.
- **Verbatim execution tasks** (Claude has already fully decided what moves
  where and is handing off the mechanical act) — delegable in any phase,
  including Phase 0, PROVIDED the handover is fully specified: exact source
  lines, exact destination, and an explicit list of what's allowed to
  change. Never delegate an open-ended instruction like "move the auth
  logic into its own file" — that's a judgment task wearing a mechanical
  costume.
- Use your own read of whether a given task is actually straightforward —
  there's no fixed per-phase rule.

**Named pattern worth recognizing: decide once, replicate via delegation.**
When a fix or change needs to be applied the same way across multiple
already-isolated files (e.g. the same mobile layout fix across several
view files, the same field added to several similar router endpoints),
the JUDGMENT is deciding the approach on the first instance. Once that's
decided and fully specified, applying that exact pattern to the remaining
files is verbatim execution — a real delegation candidate, not something
that needs to stay with Claude just because it's spread across many
files. Don't default to doing all N instances yourself out of caution;
recognize this shape when it comes up and delegate the repetition.

**Call-site scope.** A "move this code, only imports may change" allow-list
covers the moved code itself. If the moved code is referenced elsewhere
(call sites in other files), updating those references is a SEPARATE
change with its OWN explicit allow-list — don't fold it silently into the
same delegation, and don't assume DeepSeek will find and fix call sites on
its own.

**Branching.** Per the one-branch-per-phase hard rule above: a DeepSeek
handoff is just another change within the current phase branch. It doesn't
get its own branch, and it doesn't need a fresh "branch before any change"
check — that was already satisfied when the phase branch was created.

**Confirmation checkpoint.** The first time you delegate something in a
given phase, stop and show the owner what you're about to hand off and why
it qualifies as mechanical rather than judgment. Wait for approval before
proceeding. After that first approval, continue delegating similar work in
that same phase without asking again; the requirement resets when a new
phase begins.

Default when unattended: if a delegation checkpoint is hit and there's no
response, STOP AND WAIT. Do not silently fall back to doing it yourself,
and do not silently proceed with the delegation. An unanswered checkpoint
is not a judgment call for you to resolve either way — it's a stop.

**RULES.md applies to delegated code too.** DeepSeek doesn't know your
naming/comment/error-handling standards unless it's told — `--rules` passes
RULES.md as context on every call (required, not optional — the script
rejects a call without it). Verbatim moves are checked by diff regardless,
but for any delegated code that isn't a pure move, don't accept it until
it's been skimmed against RULES.md's naming, comment, and error-handling
sections — "tests pass" is not the same bar as "the owner could open this
cold."

Known tradeoff, accepted deliberately: a quick skim is lighter enforcement
than RULES.md's actual bar. It's realistic to catch bad naming or an
obviously missing error handler on a skim; it's not realistic to reliably
catch something like a missing WHY-comment on a subtle workaround that
way. For a small solo project this is an acceptable gap, not a solved one
— if delegated code volume grows enough that this stops feeling safe,
tighten the check then rather than over-building enforcement now for a
problem that isn't big yet.

**Verification — diff, not just tests.** After DeepSeek returns moved code,
diff its body against the original source region. Anything changed outside
the explicitly allowed list means reject and do it yourself — don't retry
with DeepSeek, since early-phase code often lacks full test coverage to
catch a subtle change downstream. For non-move delegated work, run the
project's build/test command and trust a pass — don't re-read the full
generated file just to eyeball it if tests pass; only read it when tests
fail or the file falls under "never delegate."

**Verification — patch/bugfix rewrites specifically.** A `--patch` call
returns the ENTIRE rewritten file, not just the fix — deliberately, and in
contrast to CLAUDE.md's edit-in-place rule for Claude's own edits. That's
not a contradiction: Claude edits in place because it controls exactly
what it touches; DeepSeek gets the opposite treatment because an LLM
producing a correctly-formatted diff/patch is a real failure mode (wrong
line numbers, malformed context, fails to apply cleanly) — a full rewrite
sidesteps that. The tradeoff is the risk this step exists to catch:
a full rewrite can smuggle in unrelated changes. Tests passing is not
proof DeepSeek only touched the buggy lines — a test suite can pass even
with unrelated changes sitting underneath it. Two-step check, every time:

1. Automatic, free, no AI: run `diff` between the previous file and the
   new one before accepting it. This runs every single `--patch` call,
   no exceptions.
2. Only if step 1 shows anything: if the diff shows ONLY the buggy
   lines changed, accept and move on — nothing else happens. If it shows
   changes outside the bug area too, don't auto-accept. Look specifically
   at those extra lines and judge them: harmless (e.g. a formatting tweak)
   or risky (touching logic that wasn't part of the bug). If it's not
   clearly harmless, flag it for the owner rather than deciding either way
   yourself.

**Retry limit.** If a patch attempt still fails after one retry, write the
file yourself. Don't loop indefinitely on DeepSeek, and tell the owner it
needed a manual fix.

**Never delegate:**
- Anything touching the shared permission-check function
- Schema or infra changes
- Anything crossing a domain boundary (auth, assets, sites, permissions, users)
- Anything outside the current phase's declared scope in PHASES.md
- `ask_deepseek.py` itself — future changes to it are a judgment task, not
  something to hand off to the tool it defines

**Patterns observed (Phase 2) — what this project's delegation actually
looks like in practice, once there was a full phase of real examples to
look back on:**

The single most common, safest, highest-frequency shape wasn't file moves
(Phase 0's original use case) — it was **verbatim CSS/structural pattern
replication**: taking a component whose styling/markup is already fully
decided somewhere in the codebase and reproducing it, unchanged, under a
new selector or in a new file. Phase 2 delegated exactly this shape twice
(3b's Movements-page column, item 5's password-toggle markup before it got
reverted for an unrelated reason) and did it *by hand* at least twice more
where it should have gone to DeepSeek instead — item 1's Roles button
restyle (copy `.edit-user-btn`/`.delete-user-btn` verbatim to
`.edit-role-btn`/`.delete-role-btn`) and several of the new dropdown/modal
components (`.move-by-suggestions` mirroring `.charge-menu`'s look, the
stat-detail modal reusing the View Battery modal's split-header-scroll
table) were all "the source pattern is fully decided, only the target
selector/file changes" — the textbook verbatim-execution case DELEGATION.md
already describes, just not recognized as one in the moment. Next time this
shape comes up — "make X look/behave exactly like the already-built Y,
just under a different name" — delegate it by default rather than doing it
directly; the judgment call (deciding X should match Y, and exactly how)
is what stays with Claude, and it's already done by the time the shape is
recognized.

By contrast, the *backend* changes this phase (the `movements:create`/
`manage` permission split, the badge-count query redefinition, the
`moved_by` field wiring) were correctly kept direct — each one required
deciding what a field or permission should actually mean, not reproducing
an existing decision. That distinction (reproducing vs. deciding) is doing
the real work in "what's safe to delegate," more than any per-phase or
per-file-type rule could.