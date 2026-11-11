# PHASES.md — Live Status Tracker (JASIRI NET OPS)

Check this file before starting any work. If a request doesn't match the
current phase's declared scope, flag it — don't do it anyway.

Completed phases collapse to one summary line (name + completion date)
once confirmed done — full detail below is only kept for the active phase
and whatever's still upcoming.

---

## ACTIVE PHASE: Phase 6 — SmartOLT Integration

Started at the owner's explicit call, 2026-11-10, after Phase 5 was merged to
`main`. Branch: `phase-6-smartolt` (from `main`). The owner's scope document
called this "Phase 3 of the Technical Activity Log roadmap"; it is numbered 6
here so it doesn't clash with the app's own Phase 3 (Inventory). Its
sub-phases 3A–3D are 6A–6D below. Upcoming phases renumbered: Notifications
6 → 7, Ticketing 7 → 8, CRM 8 → 9, Chat 9 → 10.

### Purpose

Bring SmartOLT's ONU data into OPS so technicians and managers can see fibre
customer health without logging into SmartOLT. Existing stack only (FastAPI,
PostgreSQL, plain JS/CSS), no new services. **Read before write:** nothing in
OPS changes an ONU until the data has been correct and trusted for a while.

SmartOLT is the cloud version at `https://jasiri.smartolt.com`. OPS is on
Render with a public HTTPS address, so it can receive SmartOLT webhooks.

### Sub-phases and gates

Each sub-phase ships on its own; the next starts only when its gate is met.

| Sub-phase | Delivers | Risk | Gate to start |
|---|---|---|---|
| **6A** Read-only ONU health | Polling of ONU status and signal, an `onu_state` table, a Network page panel | None, read only | API key set, sample responses saved |
| **6A-W** Webhooks | SmartOLT pushes ONU status changes to OPS the moment they happen | None, read only | 6A polling has run reliably; webhook format looked up and a sample saved |
| **6B** Customer linking | ONUs linked to customers; ONU status and signal on the customer account; optical signal pre-filled on Customer Installation reports | Medium: a wrong link shows the wrong customer's data | 6A has run reliably for at least a week |
| **6C** Actions | Enable, disable, reboot one ONU from OPS, confirmed and audited | High: a wrong action cuts off the wrong customer | Every active customer's link verified in 6B |
| **6D** Provisioning and configuration | Authorize ONUs, speed profiles from billing packages, WAN/port modes, resync, delete | Highest: changes live OLT configuration | 6C in daily use with no wrong-ONU incident, and a spare ONU to test on |

Billing-driven automation (for example, disabling an ONU when an account is
suspended) is not part of 6C or 6D. It needs its own decision after 6D.

### 6A — Read-only ONU health

**First task: capture real responses.** Before any other code, call each
endpoint once and save the raw JSON as a sample, outside the repo (ONU names
may hold customer names). Confirm against it: the key holding the ONU list,
the format of the signal values, the full list of status values, and any
rate-limit headers. Build on those facts, not assumptions.

- **Config:** `SMARTOLT_URL` and `SMARTOLT_API_KEY` in `.env` and Render's
  Environment. Never in the frontend, never in git. A dedicated OPS key.
- **Client:** one module, `db/smartolt.py` (the scope doc's
  `app/integrations/smartolt.py`; this repo keeps integrations in `db/`, like
  `db/deepseek.py`). Sends the key in the `X-Token` header, raises when the
  response's `status` is false, waits and retries once on HTTP 429 using
  `Retry-After`.
- **Polling:** `GET /api/onu/get_onus_statuses` every 6 minutes,
  `GET /api/onu/get_onus_signals` every 20 minutes, each with a small random
  delay. About 13 calls an hour. No per-ONU endpoints in a loop.
- **What drives the polling (owner's call, 2026-11-10):** no scheduler. The
  router's 15-second heartbeat (Phase 4 ingest) is the clock: on each one, if
  a poll is due, it runs in the background so the heartbeat is never held up
  (same approach as `purge_if_due`). Backup: opening the Network page with
  data older than 6 minutes triggers a fetch, so the data can't go silently
  stale while someone is looking.
- **Table `onu_state`** (schema change, owner approval before it runs):
  `unique_external_id` text primary key, `sn`, `olt_id`, `board`, `port`,
  `name`, `status`, `last_status_change`, `signal_1310`, `signal_1490`
  (text until the format is confirmed), `polled_at` timestamptz not null
  default now(). Both jobs upsert by `unique_external_id`.
- **Network page panel:** counts by status (whatever values the real response
  has); ONUs not online, most recent `last_status_change` first; a
  weak-signal list using **SmartOLT's own grade** (Warning / Critical; the
  owner's call, 2026-11-10, instead of OPS thresholds for now); a "last
  updated" time; shown as **stale** when status is older than 15 minutes
  (signal: 45 minutes, since it's only polled every 20).

**6A progress (2026-11-10):** sample responses captured and confirmed (61
ONUs on 1 OLT; statuses Online / Offline / LOS / Power fail; signals as
`-17.62 dBm` or `-`; times in Nairobi time with no zone). Table
`onu_state` approved by the owner and built as migration 0031 (stores the
ONU's slot, zone, numeric dBm and SmartOLT's grade; no address or phone).
Client, polling, panel and tests written; run locally against the real
sample data. Not yet on production: migration 0031 and the two Render
environment variables are still to do.

### OLT page (owner's call, 2026-11-11)

The owner asked for SmartOLT's own day-to-day screens inside OPS, ahead of
the original order:

- **Sidebar:** a new **Network** section with **Network Status** (moved
  from a button on the Sites page, owner's call the same day), **Network
  Analysis** (the CCR2116 page, renamed) and **OLT**.
- **OLT → Configured** (like SmartOLT's list) and **View** (like SmartOLT's
  ONU page), read only. Action buttons (reboot, resync, disable, delete) stay
  in 6C, as the owner chose.
- **OLT → Unconfigured** with **Authorize**, built and switched on from day
  one at the owner's explicit choice (2026-11-11), instead of waiting for
  6D's gate (6C in daily use, a spare ONU to test on). The 6D safety rules
  that apply to it are in place: kill switch `SMARTOLT_WRITES_ENABLED`,
  confirm step showing what will be sent, read-back with mismatches flagged,
  audit log `onu_actions`, admin only.
- Migration 0032 (approved 2026-11-11): `onu_state.details` jsonb and
  `details_polled_at`, and the `onu_actions` audit table.
- SmartOLT returns PPPoE and Wi-Fi passwords in plain text in its details
  replies; OPS strips them from every reply. Since 2026-11-11 (owner's
  call) an admin can click to see an ONU's PPPoE password: read live from
  SmartOLT on that click, never cached or stored, and each reveal is in
  the ONU's action history.

### 6A-W — Webhooks (added at the owner's call, 2026-11-10)

SmartOLT tells OPS the moment an ONU changes, instead of OPS waiting up to
6 minutes for the next poll.

- **Before building:** look up SmartOLT's webhook events, payload and how it
  proves a request came from SmartOLT. Don't guess them.
- **Looked up 2026-11-10** (from `https://www.smartolt.com/openapi.json`,
  `x-webhooks` and the "Real-time events" tag; no other docs page exists):
  - Set up in SmartOLT under **General > API key > Edit > Webhook**: an
    HTTPS URL, the events wanted, a **Signing secret** (`whsec_…`), and a
    "Send test event" button (`webhook.test`).
  - POST, JSON: `{"type", "timestamp" (UTC), "data": {"onus" | "pons" |
    "olts": [...]}}`, one event type per batch, up to 1,000 rows.
  - Events: `onu.status_changed` (rows shaped like the status poll's),
    `onu.signal_changed` (band change or ≥2 dB), `onu.signal_reading`
    (1310), `onu.rx1490_reading` (1490), `pon.outage_started/ended`,
    `olt.status_stale/fresh`, `onu.authorized/updated/deleted/
    unconfigured_detected`.
  - Signed the **Standard Webhooks** way: headers `webhook-id`,
    `webhook-timestamp`, `webhook-signature: v1,<base64 HMAC-SHA256>` over
    `{id}.{timestamp}.{raw body}`, key = the secret without `whsec_`,
    base64-decoded.
  - Must answer 2xx within 15 s; redirects count as failures. Retries after
    5 s, 5 min, 30 min, 2 h, 5 h, then every 10 h; disabled (with an email)
    after 5 days of failures. A retry resends the same `webhook-id` and body.
  - **Not documented:** source IPs, a timestamp tolerance, whether a retry
    keeps the original `webhook-timestamp`. Check with the test event before
    rejecting old timestamps, or retries could be refused until SmartOLT
    disables the webhook.
- Signals can come by webhook too (`onu.rx1490_reading`,
  `onu.signal_changed`), so this sub-phase can cover both, not status only.
- **Built 2026-11-11, not switched on.** `POST /smartolt/webhook` checks
  the signature with `SMARTOLT_WEBHOOK_SECRET` (no secret = every delivery
  refused), ignores a repeated `webhook-id`, and applies status only when
  it's newer than what OPS has. Handles status changes, 1310 and 1490
  readings, ONU updated/authorized/deleted; PON and OLT events are logged
  only for now. Old timestamps are not refused until a real retry shows
  whether SmartOLT keeps the original one. **To switch on (around
  2026-11-13, if polling has been solid):** add the URL and events in
  SmartOLT, put the Signing secret in Render as `SMARTOLT_WEBHOOK_SECRET`,
  press "Send test event". The OLT page then says "live updates on".
- An inbound endpoint that rejects any request that can't be verified, and
  updates `onu_state` for that one ONU. It never changes anything in SmartOLT.
- **Polling stays.** Webhooks cover status changes only; signal readings
  still come from the 20-minute poll, and the 6-minute status poll stays as
  a safety net for any missed webhook (it can run less often once webhooks
  have proved reliable).
- Later, once trusted: an in-app alert when an ONU goes offline, using the
  existing Alerts settings. Not part of the first webhook release.

### LOS alerts (owner's call, 2026-11-11)

Built ahead of the webhook switch-on: alerts when a customer's or hotspot
site's ONU goes LOS (2 min wait, then 15 min, 30 min, 1 h, 3 h, 6 h, 8 h,
then 08:00 and 16:00 until back; acknowledged = twice a day only), one
alert per PON port when 3+ ONUs go LOS within 2 minutes. Migration 0035
`onu_alerts` (approved). The site-down schedule moved to the same shape
(5, 15, 30 min, 1, 3, 6, 8 h, then twice a day), replacing 16 messages in
12 h, at the owner's call ("feels like crying wolf"). Switch:
ONU_LOS_ALERTS_ENABLED. Every LOS message (in-app, SMS, WhatsApp) is
logged in `alert_deliveries` against its LOS alert, failures included, and
shows on Reports > SMS Status named by customer, hotspot site or PON
port: migration 0036 (approved 2026-11-11) makes `monitored_site_id`
optional and adds `onu_alert_id`.

A hotspot site that is down because its linked ONU is LOS gets only the
LOS messages (owner, 2026-11-11): no site down / still down / twice-daily
/ flapping message, no place in a bundled "sites down" message or its
battery picks (a fibre cut needs no battery), and no "back online" when the
LOS "signal back" message already closed it. The site's episode is still
tracked. If the fibre returns but the site stays down, its own schedule
resumes with one catch-up "still down" (and a "back online" later). A power
cut shows as "Power fail", not LOS, so it keeps the normal site messages.
Off with ONU_LOS_ALERTS_ENABLED=false, or when ONU status is stale.

### 6B — Customer linking

Each fibre customer links to exactly one ONU. A person confirms every link;
the system never links silently.

- `customer_onu`: customer id, `unique_external_id`, who confirmed it and
  when. Unique on both customer and ONU.
- Suggestions from comparing ONU name and `sn` with customer records; accept
  or reject each.
- Unmatched screen: ONUs with no customer, active fibre customers with no ONU.
- Customer page: ONU status, signal, last status change, same stale warning.
- Customer Installation report: optical signal pre-filled from the linked
  ONU; the technician can overwrite it.
- Out of scope: changing any ONU or billing record from the link.
- **Built 2026-11-11** (started ahead of the one-week gate at the owner's
  call). Match check on live data: 45 customers (27 active), 32 of 61 ONUs
  match exactly one customer by PPPoE username, 6 more by name, 23 match
  nobody (15 of them set up via the ONU web page, likely hotspots/sites).
  Migration 0033 `onu_customer_links` (approved): one row per ONU, unique
  customer, or "not a customer". OLT → Customers tab: suggestions with
  Accept / Reject, "Accept all exact username matches" (only unambiguous
  PPPoE-username matches, recomputed on the server), ONUs with no customer
  (link by search, or Not a customer), active customers with no ONU, linked
  list with Unlink. Admin only. Customers page: Fibre ONU column with the
  stale note. ONU View: its customer. Installation report: picking a linked
  customer fills the optical signal from the ONU's 1490 reading. OPS now
  keeps each ONU's PPPoE username (never the password) for the suggestions.
- **Hotspot sites (owner's call, 2026-11-11):** live result after the
  customer pass: 39 ONUs linked to customers, 0 active customers without
  an ONU, 22 ONUs left, which are hotspot sites. Migration 0034 (approved)
  adds `site_id` (unique, one ONU per site) and a rule that each row is
  exactly one of customer / site / not a customer. Suggestions: same PPPoE
  username as the site, else the site's name inside the ONU's name
  (longest site name wins). The tab is renamed **Links**. Network Status
  shows each site's ONU (status, signal) and a down site's card says what
  the fibre suggests (power fail = check power and battery; LOS = fibre
  cut; online = look at the equipment). The site page has a Fibre ONU card.

### 6C — Actions

Enable, disable or reboot one ONU at a time.

- Look up exact paths and parameters in
  `https://www.smartolt.com/openapi.json` first. **Looked up 2026-11-10:**
  `POST /api/onu/enable/{onu_external_id}` and `.../disable/...` (form field
  `async=1` queues it); `POST /api/onu/reboot/{onu_external_id}` (no async
  option: it answers "Device reboot command sent" directly);
  `POST /api/onu/check_bulk_task_status` (form `onus_external_ids`; states
  queued, in_progress, retrying, failed, succeeded, no_task, onu_not_found).
  Async calls have their own 5,000-an-hour budget.
- Admins only to start.
- Confirmation dialog with customer name, ONU serial and the action.
- Sent with `async=1`; shown as pending until `check_bulk_task_status`
  reports succeeded.
- Audit table: who, which ONU, which action, when, final result (failures
  too). No bulk actions.
- **Built 2026-11-11.** Gate met: every active customer linked (6B, 0
  left). On the ONU View page (admin only): Reboot, and Disable or Enable
  depending on the ONU's current state. The confirmation names the
  customer (or hotspot site), the ONU and the serial. Disable/Enable are
  sent with async=1 and followed with check_bulk_task_status every 5 s
  (up to 3 min) on a background thread, then read back from SmartOLT
  (administrative status); "succeeded" only when the read-back agrees.
  Reboot is done when SmartOLT accepts it. All in the existing
  `onu_actions` table (no schema change) with the before and after state;
  the ONU page lists its action history. Switches: SMARTOLT_WRITES_ENABLED
  (all writes) and SMARTOLT_ACTION_REBOOT / _DISABLE / _ENABLE (each on
  unless set to false). Tested end to end against a local fake SmartOLT,
  and then on a real non-customer ONU by the owner (2026-11-11: all good).
  Webhooks confirmed live ("live updates on") and the hotspot-site links
  accepted on OLT > Links the same day.

### 6D — Provisioning and configuration

Authorize a new ONU (from SmartOLT's unconfigured list, named by the customer
account convention so 6B links it); change speed profile through a
`package_speed_profile` mapping table; set WAN and Ethernet port modes;
resync config; delete an ONU (typed serial confirmation, only once unlinked);
bulk changes last. CATV and VoIP not planned. Paths and parameters from the
OpenAPI file first; async changes count as done only on `succeeded`.

- **Built 2026-11-11 at the owner's call** ("finish phase 6D"), ahead of the
  gate, like Authorize. Owner's decisions the same day:
  - **Speed: by hand per ONU, no package mapping.** The sample showed all
    61 ONUs at 1G/1G in SmartOLT; the router's PPPoE plan sets the real
    speed, so a package→profile table would only add a second, riskier
    limit. Admin picks upload/download from SmartOLT's internet profiles.
  - **Actions:** Resync config, Speed, Ethernet port (LAN / Access on an
    attached VLAN / shut down), ONU mode, Delete (typed serial; refused
    while linked to a customer or site). Location and VLANs added below.
  - **On from day one**, admin only. Switches SMARTOLT_ACTION_RESYNC /
    _SPEED / _PORTS / _MODE / _DELETE, plus SMARTOLT_WRITES_ENABLED.
- Every change: live read first, checked against the ONU (port, attached
  VLAN, profile names), before → after preview, confirm with customer and
  serial, audit row in `onu_actions` (no schema change), one synchronous
  call, read back from SmartOLT, `succeeded` / `mismatch` / `failed`. The
  PPPoE password is sent to SmartOLT only, never stored or logged.
- Tested end to end against a local fake SmartOLT (every change, including
  delete); not yet on a real ONU. First real use should be on a
  non-customer ONU: Resync on one of the two ONUs SmartOLT flags as
  "resync failed" is the lowest-risk start.
- **Added the same day, from the owner's SmartOLT screenshots:** Update
  location details (zone, splitter and port, seal number, name, address or
  comment, contact; opened by clicking any of those on the ONU page),
  Update attached VLANs (tick the VLANs; the WAN VLAN can't be removed),
  and one Update ONU mode dialog replacing the separate ONU/WAN mode ones:
  WAN VLAN-ID, Routing/Bridging, WAN mode (Setup via ONU webpage, DHCP,
  Static IP, PPPoE; a new PPPoE username can keep the current password)
  and WAN remote access, sent as up to four calls in SmartOLT's order; if
  one fails, the message says which steps were already done. Address and
  contact are never kept in the audit row ("changed" only). PPPoE password
  reveal for admins (see 6A). Switches SMARTOLT_ACTION_LOCATION, _VLANS.
- Not built: bulk changes (scope said "bulk changes last"), latitude and
  longitude, Wi-Fi settings, restore factory defaults, CATV/VoIP.

**Safety rules for every write (6C and 6D):**
1. Kill switch `SMARTOLT_WRITES_ENABLED`: false refuses every write; reads keep working.
2. One feature flag per action, switched on only after it's been tested.
3. Every new action tested on a spare ONU before customers.
4. Before-and-after preview and a confirm click.
5. Read back from SmartOLT after every write; flag any mismatch.
6. Audit log with before and after values and the result, failures included.
7. Separate view, control and configure permissions; delete and authorize admin only to start.
8. A dedicated OPS API key, revocable on its own.
9. Bulk batches capped at 20 ONUs to start.

### Open questions

- [x] Cloud version? Yes: `https://jasiri.smartolt.com`. How many OLTs and ONUs: comes from the first sample.
- [ ] Is the ONU name in SmartOLT the customer's account name or number? (decides 6B's suggestions)
- [x] Signal levels: use SmartOLT's own grade for now (owner, 2026-11-10). OPS thresholds can come later.
- [ ] In 6C, may technicians reboot an ONU, or only admins? (Admins only for now.)
- [x] Is OPS reachable over HTTPS? Yes (Render), so webhooks are in scope as 6A-W.
- [ ] Chained ONTs: this phase, or the separate TR-069 investigation?
- [x] For 6D: which speed profile matches each billing package? None: speed is set by hand per ONU (owner, 2026-11-11), the router's plan does the real limiting.
- [ ] For 6D: a spare ONU and OLT port to test writes on? (Owner chose on from day one; first tries on a non-customer ONU.)
- [x] For 6D: authorize, delete and speed changes admin only, or a technician role too? Admin only.

### Done when

Each sub-phase is confirmed by the owner on its own, after running it. The
phase is done when the owner confirms the sub-phases they want shipped,
which may stop short of 6D.

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
phase's open question.)*

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
