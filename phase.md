# PHASES.md — Live Status Tracker (JASIRI NET OPS)

Check this file before starting any work. If a request doesn't match the
current phase's declared scope, flag it — don't do it anyway.

Completed phases collapse to one summary line (name + completion date)
once confirmed done — full detail below is only kept for the active phase
and whatever's still upcoming.

---

## ACTIVE PHASE: none

Phase 6 (SmartOLT) was confirmed complete by the owner on 2026-11-15; see
COMPLETED PHASES. Phase 7 (Notifications) starts only at the owner's call,
with its own branch from `main` once Phase 6 has been merged.

---

## ALSO ON THIS BRANCH: Network Health (owner's scope, 2026-11-16)

Not part of Finance: the owner asked for it on `phase-7-finance` ("new scope
will continue on this branch", 2026-11-16). Detect degraded speed and quality
on the uplink before customers complain, and text the owner. Not the Network
Analysis / PPPoE page.

**Feed (owner, 2026-11-20):** readings come from the Frankfurt server's
`jasiri-shipper`, not from the routers directly, per
`docs/network-health-ingest-contract.md` (the contract wins on payload, auth
and endpoint). So the brief's two RouterOS scripts, per-device tokens and
token rotation are not built: one `NETWORK_HEALTH_TOKEN`, dedupe on `key`.

**Owner's answers (2026-11-20):** download = rx on sfp-sfpplus1, utilisation =
max(down, up) / capacity (350, its own setting: the Network page's stored
capacity belongs to another domain); `-new` twins dashboard only, with a
"provider switched at" setting that splits history and baselines; loss is an
average over the window (10 pings a test, steps of 10 %); speed: only the
03:30 test is judged (failed, or under half its 7-day median), 20:30 shown,
never compared with 350; SESSIONS_DROP dropped (the feed only counts the
CCR2116's 29 sessions); start in `NH_SMS_MODE=log_only` for a week; the
silence check also runs from the CCR2116 `/network/ingest` heartbeat. CRS326
and OLT pings are not in the feed, so INTERNAL_LINK is not built; the
CCR2004's `i-fra` / `i-nbo` are drawn, not judged.

**Built (2026-11-20):** `network_health/` (router, service, store, rules,
alerts, messages, feed, config), migration `0041_network_health.sql` (six
nh_ tables, **run on the production DB 2026-11-20 at the owner's go**), page Network > Network Health,
Roles > Network Health (view, Admin). Tests: every rule fire/hold/clear,
the flow end to end on a fake store, ingest auth and limits, SMS
de-dupe/flapping/combining/cap/log-only, the 160-character GSM-7 test, the
isolation test. Checked in headless Chromium with made-up data.

**Still to do:** set the env vars on Render
(`.env.example`), point the shipper at `/network/health/ingest`, point an
uptime checker at `/network/health/status?key=…`, switch `NH_SMS_MODE=live`
after the first week. After that week, revisit whether falling SFP power
should text (a slow drop is often the first sign of a damaged fibre or dirty
connector).

**From the switch plan PDF (owner agreed 2026-11-20):** latency is judged on
Frankfurt (`p-fra`, +40 ms over that hour's 7-day median for 10 min; 250 ms
until there's a week of data) instead of 1.1.1.1 at 2x; loss averages over
5 minutes, not 3; a link drop is its own rule, LINK_DOWN, and texts. Amber
loss and falling SFP power stay page only.

**Where content is served from (owner, 2026-11-20):** new `route` record
kind (traceroutes at 21:00 and 04:00 to four sites), stored in
`nh_readings.route` (migration 0042, approved and run 2026-11-20), kept
forever; card on the page with a "changed since the same slot's previous
check" mark, page only. Route records that arrived before this was deployed
were answered 2xx as an unknown kind and not stored.

**Owner, 2026-11-21:** new probe `p-za` (South Africa, Vodacom); new
`dns` record kind in `nh_readings.dns` (migration 0043, approved and run
2026-11-21), its graph and the page-only DNS_ROUTER rule; route band 50-110
ms = South Africa (likely) and the "International path" group (Frankfurt,
1.1.1.1). `dns` records that arrived before this was deployed were answered
2xx as an unknown kind and not stored.

**Inside network (owner, 2026-11-21):** new `olt` (SNMP read of the
MA5680T every 5 min: uptime, uplink 0/19/0 good / errored frames) and
`crs_link` (CRS326 port up/down) record kinds in `nh_readings.olt` /
`.crs_link` (migration 0044, approved and run 2026-11-21). Pure logic in
`network_health/inside.py` (restarts, damaged frames with the 32-bit wrap,
failed polls, down/up pairing). The "Inside network" section: damaged-frames
graph and badge, OLT SNMP status, restarts list, link events table. Rules
OLT_CRC (over 1 %, two readings in a row), OLT_RESTART (no "back" SMS),
OLT_LINK_DOWN (sfp-sfpplus3; the up texts "back after X min Y s") and
OLT_SNMP_SILENT (15 min): all in SMS_RULES, AMBER, so log_only governs them
and /status stays 200. `olt` rows kept 90 days, `crs_link` kept. On 21 Nov
damaged frames measured ~10 % (bad DAC, swap planned).

**Cutover day:** after repointing `p-gw` to the new gateway, set "Provider
switched at" on the Network Health page, and give the Frankfurt report
script the same time as its SWITCH_TIME, so both split history at the same
minute. The new gateway probe must be named `p-gw-new` (the contract's name;
the PDF copy read here said `p-gw2` in step 20).

---

## UPCOMING PHASES (order locked; each stays a one-liner per the usual
## convention until it becomes active, at which point its full brief goes
## here the same way Phase 3's did before it was completed)

**Phase 7 — Notifications.** Lowest-effort new addition — SMS templates
already designed, this is mostly wiring them in. (Originally Phase 3;
renumbered to Phase 4 to make room for the Phase 3 Ops Inventory System
brief, per owner's explicit call 2026-09-06 — see COMPLETED PHASES for
that phase's outcome. Renumbered again to Phase 5 to make room for the
Phase 4 Network Monitoring brief, per owner's explicit call 2026-09-18,
to Phase 6 for the Phase 5 UI Consistency brief, per owner's explicit
call 2026-11-09, and to Phase 7 for the Phase 6 SmartOLT brief, per owner's
explicit call 2026-11-10.)

**Phase 8 — Ticketing.** Close in shape to the existing site verification/
check-in flow.

**Phase 9 — Basic CRM.** Likely just views/notes on top of the existing
customers table — to be CONFIRMED, not assumed, once this phase starts.

**Phase 10 — Chat.** Deliberately deferred and flagged for reassessment.
Most technically demanding of the set, and WhatsApp already works as a
contact channel. Confirm this solves a real operational gap before
building anything. *(2026-11-04: an admin-only, read-only data assistant
— the "Ask" button — was built ahead of this phase at the owner's
request and sits on the Phase 4 branch; see ARCHITECTURE.md's "Admin
assistant". It answers the owner's questions from the app's data; it is
NOT customer chat, which still needs a customer login and remains this
phase's open question. 2026-11-11, owner's call during Phase 6: opened to
all staff with tools following each person's permissions, revenue admins
only, plus customer-profile and OLT lookups.)*

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

**Phase 6 — SmartOLT Integration.** Completed 2026-11-15 (owner's
confirmation), on branch `phase-6-smartolt`. Fibre ONUs in OPS: status and
signal polling (6A), the OLT page, ONUs linked to customers and hotspot
sites (6B), reboot / disable / enable (6C), authorize, speed, port modes,
resync and delete (6D), per-role OLT permissions and LOS alerts; the
SmartOLT webhook (6A-W) is built; switching it on in SmartOLT was never
recorded here. Also in this
phase, at the owner's calls: the Ask assistant for all staff, revenue
reporting, the Dashboard, light mode and animations, and Network Status
renamed Hotspot Status. Left open: whether the SmartOLT ONU name is the
account name or number, chained ONTs (or the TR-069 investigation), and a
spare ONU and OLT port to test writes on. Full brief: this file at commit
`7a34925`; detail in changelog.md's Phase 6 section.

**Phase 5 — UI Consistency.** Completed 2026-11-11 (owner's
confirmation), merged to `main` 2026-11-10. One page header, table, form
and dialog look across the app; the in-app confirmation dialog replaced
`confirm()`; reduced-motion respected; the sidebar sorted into titled
sections. Full brief: this file at commit `224705d`; detail in
changelog.md's Phase 5 section.

**Phase 4 — Network Monitoring (Site Status).** Completed 2026-11-11
(owner's confirmation), merged to `main` 2026-11-09. Router heartbeat
ingest, Network Status with Trends and site pages, alerting and the
external watchdog, revenue reporting, the CCR2116 Network page (now
Network Analysis) and the Technical Activity Log. Full brief, including
the router reconnaissance findings and the site mapping table: this file
at commit `224705d`; detail in changelog.md and
docs/network-monitor-runbook.md.

**Phase 3 — Ops Inventory System.** Completed 2026-09-17, merged to
`main`. Full inventory/asset-tracking domain for field operations
(enclosures, cable, consumables, power/network gear), entirely separate
from battery tracking. User-created categories tagged with one of three
fixed Cores (Asset-Serialized / Consumables-Quantity / Cable-Length) drive
row granularity and which fields apply — locked once a category has
items, since the three Cores use disjoint, non-convertible column sets.
Every item state change happens only as a side effect of a logged
transaction (In/Transfer/Adjustment/Issue/Return/Write-off/Reconciled),
never a direct edit. Delivered: the Categories/Items/Transaction Log core;
a unified Issue/Return Materials cart covering mixed-Core carts in one
action, with Quick Issue/Return auto-selecting eligible serials by
quantity; two-stage cable reconciliation (full cut out, actual usage
reconciled once the job closes, with usable-remainder/offcut split);
SKU/Spec Summary + Cable Type Summary + Offcut Rollup reporting; a full
permissions hierarchy for the domain (Inventory Items/Stock/Categories/
Log, plus an independent Reports toggle), enforced at both the UI/route
layer and the backend, not just the permissions panel. Went through
several owner-review refinement passes after the initial build (nav
restructure, Items/Stock split, Quick Issue/Return, on-hand-vs-total Qty
reporting, among others — see changelog.md's Phase 3 section for the full
list) before this final confirmation. See architecture.md's "Inventory:
Core system & permissions" section for the settled design reasoning.
Production DB migrations (`0004_inventory_core_tables.sql`,
`0005_inventory_custody_type.sql`) confirmed applied ahead of the `main`
merge. Owner confirmed done (2026-09-17).

**Phase 2 — Finish Incomplete Functionality.** Completed 2026-09-06.
Seventeen incomplete-feature items resolved (button styling, movement
"Moved by" typeahead + recording fix, movement notifications/badges,
password show/hide, settings tab theming, global search reach, status-card
click-through detail, role-based move authorization, movement-lifecycle-
driven battery status, site-down flagging, EAT timestamps, Reason dropdown
restyle, live-sync render flicker, stale static assets) plus several
found-along-the-way fixes (schema drift migrations, stale battery table on
cancel, `.table-scroll` blocking page scroll); one item (inline record
detail in global search) dropped by owner's explicit call. Remaining loose
end — migrations `0002_backfill_in_transit_at.sql` and
`0003_close_out_site_still_down.sql` not yet run against production —
discarded per owner's explicit call (2026-09-06), not pursued further.

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
