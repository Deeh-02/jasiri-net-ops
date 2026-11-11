# CHANGELOG.md

Phase-based, not version-based — this project ships in named phases (see
[phase.md](phase.md)) rather than semver releases. Each entry here is a
concise, user-facing summary; full technical detail lives in the git log
and in `phase.md`'s own per-phase writeups.

## Phase 6 — SmartOLT (on `phase-6-smartolt`)

Started 2026-11-10 at the owner's request, after Phase 5 was merged to `main`.

### The Ask assistant for everyone, with customers and the OLT (2026-11-11)
- **Everyone gets the Ask button**, but it only looks up what that person
  can already see in the app: someone with Batteries gets battery answers,
  someone with the Network page gets network and OLT answers, and so on.
  **Revenue stays admins only.**
- **"Tell me about JS45"**: the assistant describes the customer: plan,
  active or expired, their fibre ONU (online, signal, any LOS), whether
  they're connected right now, and the last technician visits. It points
  out anything that looks wrong.
- **Ask the OLT**: "which customers are on LOS", "who has a weak signal",
  or live details about one customer's ONU straight from SmartOLT (last
  down cause, distance, recent ups and downs, ports).

### LOS alerts, a calmer site-down schedule, and fixes (2026-11-11)
- **LOS alerts:** when a customer's or hotspot site's ONU loses fibre, the
  people who get site alerts are told after 2 minutes, then at 15 min, 30
  min, 1 h, 3 h, 6 h and 8 h, then twice a day (08:00 and 16:00) until it's
  back; acknowledging skips straight to twice a day. When 3 or more ONUs
  on one PON port go LOS within 2 minutes, it's one "likely fibre cut"
  alert for the port instead of one per customer. A "signal back" message
  closes it. Same people, channels and alert hours as site alerts; five new
  editable messages on SMS Templates. The OLT page lists **LOS now** with
  Acknowledge, and the ONU's page shows its LOS. Switch:
  `ONU_LOS_ALERTS_ENABLED`. Needs migration `0035_onu_alerts.sql`.
- **Site down:** reminders are now 15 min, 30 min, 1 h, 3 h, 6 h and 8 h,
  then twice a day (08:00 and 16:00) until the site is back — down from up
  to 16 messages in 12 hours. An acknowledged site now gets the twice-a-day
  reminders instead of none.
- **Battery recommendation:** ties go to the battery that has rested
  longest, so the same battery isn't picked again and again.
- **Settings > Profile** saves again, without ever blanking the phone number.
- The current sidebar section and the OLT back link are green.
- **A hotspot site down because of LOS** now gets the LOS messages only,
  not "site is down" as well: no down / still down reminders, no battery
  suggestion, and no "back online" once "fibre signal back" has been sent.
  If the fibre comes back but the site stays down, the site's own messages
  pick up again. A power cut (Power fail) still gets the normal site
  messages.
- **VLANs dialog** tidied: each VLAN is a neat tick-box with its number.
- **LOS messages on SMS Status:** every LOS message is now recorded like
  site alerts are, failed ones included, and listed on Reports > SMS
  Status by customer, hotspot site or PON port. Needs migration
  `0036_onu_alert_deliveries.sql`.

### 6D: Change an ONU's configuration from OPS (2026-11-11)
- On an ONU's **View** page, admins get a **Configuration** row:
  **Resync config**, **Speed…**, **Ethernet port…**, **ONU mode…**,
  **WAN mode…** and **Delete ONU…**.
- Each shows what changes before you apply it (for example
  "eth_0/1: LAN → Access 51"), asks once more naming the customer and
  serial, then checks SmartOLT really made the change before saying
  **Done** (otherwise **Check** or **Failed**). Every change is listed on
  the ONU's page with who did it.
- **Speed** is picked by hand from SmartOLT's own profiles. Every ONU is at
  1G today and the router's PPPoE plan sets the customer's real speed, so
  OPS doesn't tie speed profiles to packages.
- **WAN mode PPPoE** needs the customer's PPPoE password; it goes to
  SmartOLT only and is never kept in OPS.
- **Delete** only works on an ONU that isn't linked to a customer or site,
  and you type its serial to confirm. It then shows under Unconfigured.
- The ONU page flags **Last resync failed** when SmartOLT says so.
- **Location…** (zone, splitter and port, seal number, name, address or
  comment, contact), **VLANs…** (tick the VLANs the ONU carries) and one
  **ONU mode…** dialog (WAN VLAN-ID, Routing/Bridging, WAN mode including
  Static IP, WAN remote access), laid out like SmartOLT's. Click Zone,
  Splitter, Name, Address, Contact, Attached VLANs, ONU mode or WAN setup
  mode on the ONU page to open the right one.
- **PPPoE password:** admins can click **Show** to see it. It's read from
  SmartOLT at that moment, hides again after 30 seconds, is never kept in
  OPS, and each look is listed in the ONU's history.
- Each change can be switched off on its own (`SMARTOLT_ACTION_RESYNC`,
  `_SPEED`, `_PORTS`, `_LOCATION`, `_VLANS`, `_MODE`, `_DELETE`); all writes stop with
  `SMARTOLT_WRITES_ENABLED=false`. No database change.

### 6C: Reboot, Disable and Enable an ONU (2026-11-11)
- On an ONU's **View** page, admins get **Reboot** and **Disable ONU** (or
  **Enable ONU** when it's disabled). Each asks first, naming the customer
  or hotspot site, the ONU and its serial.
- Disable and Enable wait for SmartOLT to report the job finished, then
  check the ONU really changed before saying **Done**; anything else is
  shown as **Failed** or **Check**. The page shows progress while it waits.
- Every action, successful or not, is listed on the ONU's page with who did
  it and when. The Configured list marks disabled ONUs.
- Each action can be switched off on the server on its own
  (`SMARTOLT_ACTION_REBOOT`, `_DISABLE`, `_ENABLE`), and all of them with
  `SMARTOLT_WRITES_ENABLED=false`.

### 6B: ONUs linked to customers (2026-11-11)
- **OLT → Customers** tab: OPS suggests which customer each ONU belongs to
  (same PPPoE username, or the customer's username in the ONU's name); an
  admin accepts or rejects each one, or links all exact username matches at
  once after a confirmation. ONUs that aren't customers (hotspots, sites)
  can be marked so. ONUs with no customer and active customers with no ONU
  are listed so the gaps are easy to close.
- The **Customers** page has a **Fibre ONU** column: status, signal at the
  ONU and since when, marked "not current" when SmartOLT data is old.
- An ONU's **View** page shows its customer, plan and expiry.
- On an **installation report**, picking a customer whose ONU is linked fills
  in the optical signal reading from SmartOLT; the technician can change it.
- Needs migration `0033_onu_customer_links.sql`.
- **Hotspot sites:** an ONU can be linked to a Network Status site instead
  of a customer (suggested from the site's name in the ONU's name, accepted
  by an admin). Network Status gets a **Fibre ONU** column, a down site's
  card says what its fibre reports (power fail, LOS, or fine), and each
  site's page has a Fibre ONU card. The OLT tab is now called **Links**.
  Needs migration `0034_onu_site_links.sql`.

### OLT page, and authorizing ONUs from OPS (2026-11-11)
- The sidebar has a new **Network** section: **Network Status** (the
  hotspot sites' status and trends, which used to be a button on the Sites
  page), **Network Analysis** (the CCR2116 page, renamed) and **OLT**.
  Batteries and Sites stay under Operations.
- **OLT → Configured:** every ONU, like SmartOLT's list: status, name, serial,
  board/port/ONU, zone, splitter, signal (coloured by SmartOLT's grade),
  Bridge / R: PPPoE / Router, VLANs, ONU type and authorization date. Status
  chips filter it (Online, LOS, Offline, Power fail, Weak signal), plus search,
  zone and PON port. The Fibre ONUs section moved here from Network Analysis.
- **View** opens one ONU, like SmartOLT's View page: where it is, its status
  and distance, both signal levels, TR069, management IP, VLANs, ONU and WAN
  mode, PPPoE username, speed profiles and Ethernet ports, plus a live read
  from the OLT (uptime, last down cause, optical levels, temperature, up and
  down history) and SmartOLT's traffic and signal graphs (hour, day, week,
  month). The PPPoE password is never shown. Reboot, resync, disable and
  delete aren't in OPS yet.
- **OLT → Unconfigured:** ONUs the OLT sees but hasn't authorized. Admins can
  **Authorize** one: ONU type, mode, VLAN, zone, splitter, name, comment and
  speed profiles, all chosen from SmartOLT's own lists, then a confirmation
  showing exactly what will be written. OPS reads the ONU back from SmartOLT
  afterwards and flags anything that differs. Every attempt, failures
  included, is in **Recent authorizations**. `SMARTOLT_WRITES_ENABLED=false`
  switches authorizing off instantly.
- Needs migration `0032_onu_details_and_actions.sql`.
- **Network Analysis** no longer says "CCR2116" in its title.
- **Webhook receiver** (built, off until a signing secret is set): SmartOLT
  can push ONU status and signal changes to OPS the moment they happen.
  Forged deliveries are refused; polling keeps running as the safety net.

### 6A: Fibre ONUs on the Network page (read only)
- New **Fibre ONUs** section on the Network page, from SmartOLT: a count per
  status (Online, LOS, Offline, Power fail), a **Not online** list (newest
  first, with how long each has been down, its board/port/ONU and serial),
  and a **Weak signal** list using SmartOLT's own grade (Critical first),
  showing the reading at the ONU and at the OLT.
- OPS asks SmartOLT for statuses every 6 minutes and signals every 20,
  triggered by the router's 15-second report and by anyone opening the
  panel. No scheduler, no new service, about 13 calls an hour.
- Data older than 15 minutes (status) or 45 minutes (signal) is labelled as
  not current instead of being shown as live.
- SmartOLT's address and phone fields are never stored.
- Needs migration `0031_onu_state.sql` and `SMARTOLT_URL` / `SMARTOLT_API_KEY`
  on the server.
- On phones, the Network page's tables (ONUs and PPPoE clients) show each row
  as a full card again instead of cutting values off with "…".

## Phase 5 — UI Consistency (on `phase-5-ui-consistency`)

Started 2026-11-09 at the owner's request, after Phase 4 was merged to `main`.
Presentation only: no database, API or permission changes, no migration.
- **One page structure:** every page, including Settings, Site detail and the
  Add/Edit User, Role, Site and Customer forms, uses the same header (title,
  optional subtitle, actions on the right). The top bar's edges now line up
  with the page content at every screen width.
- **Sidebar, sorted into sections:** Operations (Batteries, Sites,
  Network), Technical Activity Log, Inventory, Reports, Alerts and System
  (Users, Roles), each under a small title with a fold arrow (no divider
  lines; a title turns white only while you're on one of its pages). Every page has its own icon and hangs off a thin guide
  line; the section you're in has a white title and the current page's
  part of the line is green. Sections stay open; one you
  fold stays folded on that device until you open it or go to one of its
  pages. Unread alerts show an amber count.
- **One set of buttons, tabs and focus rings:** primary / secondary / danger
  buttons look and behave the same on every page (hover, pressed, disabled);
  all tabs are the same underline tabs (no more slanted ones); keyboard
  focus shows the same green ring on every control.
- **Confirmations:** every "are you sure?" is now an in-app dialog instead
  of the browser's pop-up (Enter confirms, Escape cancels). Deletes show a
  red button.
- **Tables:** filters sit in one toolbar above the table (SMS Status,
  Inventory Log, Customers); every main table shows the same loading,
  "nothing here — try…" and error states.
- **Forms:** an error message now stays until you change something (it used
  to disappear after 3 seconds); while a form is saving its button shows a
  spinner and can't be pressed twice.
- **At a glance:** SMS Status (tap Failed to see only failures), Stock
  (low on stock, tap to filter; total value), Movements (waiting / on the
  way / arrived and needing a site check) and Customers show a short summary
  before the table.
- **Reduced motion:** with the phone or computer set to reduce motion,
  nothing slides, fades or lifts.
- **Softer tables (second pass):** every table sits in a rounded panel with
  softer lines, a faint stripe on every other row and a clear hover; rows
  have a little more room (the Inventory Log and SMS Status stay compact);
  numbers line up on the right; dates and categories are quieter so names
  stand out; the column a list is ordered by shows an arrow; Inventory Log
  shows how many transactions the filters leave.
- **Smoother loading:** tables show grey placeholder rows shaped like the
  table while they first load (and the battery cards on the dashboard);
  changing a filter keeps the old rows on screen, slightly dimmed, until the
  new ones arrive instead of flashing; a new page opens at its top.
- **Phones:** every table now keeps one line per row with its first column
  pinned (Batteries, Reports and Alerts included); the pinned column shows a
  shadow once you swipe sideways.
- **Revenue and Network:** more space above each section; the dropdowns and
  sort arrows match the rest of the app.

## Technical Activity Log (on `phase-4-network-monitoring`)

Added at the owner's request (2026-11-06). Needs `migrations/0026_technical_activity_log.sql`
run once; until then only these pages error.
- **Submit Activity Report:** pick a category and a type and tap Submit (3 taps; date, location,
  outcome and "just me" are pre-filled). Optional time spent, extra technicians and remarks.
  A failed send keeps everything so you can retry; the success screen shows the reference
  (TAL-2026-00001) and "Submit another".
- **Activity Reports:** search by reference or description; filter by date, category, type,
  area, technician and outcome; detail view; CSV export. Technicians see their own reports and
  ones they're listed on; roles with "See every report" see all.
- **Activity Overview** (managers): activities over time, by category and type, by area, by
  technician, outcome mix (with the "Unable to resolve" rate) and time spent.
- **Editing:** the submitter for 48 hours, an admin any time. Only an admin deletes, and a
  deleted report is hidden from the lists and every total, never erased.
- **Roles page:** new "Technical Activity Log" section.
- **Where it lives in the sidebar:** all four pages (Activity Overview, Activity Reports, Submit
  Activity Report, Manage Activity Types) sit in the **Technical Activity Log** group. They
  spent a few days under Reports; the owner moved them back (2026-11-09).

**Phase 2** (needs `migrations/0027_activity_log_phase2.sql`; it is additive, so it can run
before or after the code is deployed)
- **Extra questions per activity type:** Customer Installation asks for the customer (search
  by name or username, and the package fills in from their plan), package and optical signal
  (dBm); Network Maintenance asks for the fault and the repair. They show only for those types.
- **Manage Activity Types (admin only):** add, rename, reorder and deactivate categories and
  types, and change what each type asks, without a code change.
- **Duplicate warning:** if the same type, location and date has already been reported, the
  form shows that reference and asks "Submit anyway?". It never blocks.
- **Matched to the WhatsApp reports (migration 0028):** installations ask for client name,
  account, package, contact, address/landmark, signal and a materials list; support asks for
  client name, account, finding at site and solution; hotspot chain restoration asks for the
  chain and routers restored; fibre/FAT jobs ask for signal before and after splitting; most
  types have "Materials used". The materials box is a **list only: nothing is deducted from
  Inventory** (it is replaced when Materials is built). New type: Cable re-tensioning.
- **Follow-ups (migration 0029):** a partial or unresolved visit stays true about that day.
  The visit that finishes the job can say so ("This finishes an earlier report", optional, same
  place first). The earlier report then shows "Finished by TAL-…", the list marks unfinished jobs
  "Still open" or "Followed up", Reports has a "Still open" filter, and the Overview splits
  partials into finished later and still open, with a link to that list.
- **Location is typed, not scrolled:** start typing the area and pick from the matches (tap,
  or arrows and Enter). Only a location on the list is accepted, or a deliberate "Other" with a
  few words; typed text that matches nothing is refused with a hint. Tap the picked area to
  change it.
- **Route (migration 0030):** Fibre repair, Splicing, Cable re-tensioning, New fibre deployment
  and Cable installation ask an optional "Route (if more than one place)", e.g.
  "Kamutini - Sunton - Stage". Location stays the main area or starting point, and that is what
  the Overview counts; search finds any stop on a route.
- **Repeat visits** on the Overview: customers and areas with Customer Support visits on more
  than one day in the 30 days up to the end of the chosen period. A customer is recognised by
  the account typed in (a new customer isn't in the customer list yet) or the one picked.

## Revenue Report live figures, and an admin assistant (on `phase-4-network-monitoring`)

Built outside the phase plan at the owner's request (2026-11-03/04), on
`feature/revenue-insights`, then merged into the Phase 4 branch. Not on
`main` yet.

**Revenue page**
- Refreshes itself every 30 seconds while it's open and the tab is
  visible, and at once when you come back to the tab. Only what changed is
  redrawn: your period, date, comparison and scroll position stay put.
  "updated HH:MM" is the last successful refresh; if one fails, the old
  figures stay and it says "offline, last updated HH:MM".

**Admin assistant (the "Ask" button on every page)**
- A DeepSeek chat, admins only — technicians and custom roles don't see it
  and the server refuses them. It answers from the app's own data through
  seven read-only lookups:
  - revenue headlines, or one day/week/month/year (optionally per site)
  - revenue history over a date range, network or one site, per day, week
    or month, with KSh and % change, best/worst and trend
  - every site ranked by daily revenue, with 14-day totals, share of the
    network, last 7 days vs the 7 before, and the Revenue Report's flag
  - live site status: up/down, people connected, uptime, today's sales
  - a site's contact person, phone and address (from the Sites page)
  - packages and prices
  - PPPoE customer lookup by username, name or phone
- It can't change anything and never writes SQL. Money comes from the
  Revenue Report's own figures. Customer phone numbers are never sent to
  DeepSeek; a site contact's number is, when you ask for it.
- Site names match loosely ("Red Soil" finds "Redsoil").
- It looks figures up again for every answer instead of repeating earlier
  ones, after it once put ACK 7th instead of 4th from memory (2026-11-04).

**Tried and removed**
- An AI-written analysis panel on the Revenue page (DeepSeek wrote the
  words, the server filled in every figure). Replaced by the assistant.

## Admin assistant: every page, and no answers from nowhere (2026-11-04)

- 11 new lookups (18 in all), one per page or report: the Network page
  (status, PPPoE clients online, outages), site uptime reports and a site's
  history, the alert log, batteries and a battery's history, stock levels,
  the inventory log and cable stock.
- It can't give figures it didn't look up: if it tries, the server makes it
  check, and if it still has nothing it says "I couldn't look that up".
- Each answer shows what it checked ("Checked: network status").
- Its instructions are now a file you can read and edit:
  `prompts/assistant.md`.
- Before 21 Sep 2026 there are no sales records: it now says so instead of
  "KSh 0".
- `tools/assistant_eval.py` checks it picks the right lookup for 33 real
  questions (33/33).

## Network page: several internet links (Phase 4, 2026-11-04)

Migration 0023 (two new tables) and a new `ops-live` script. The router has
two default routes (Airtel, Veenet) and the page now knows about both.

- One card per link, found from the router's default routes: name (the route
  comment), Primary/Backup, Carrying traffic/Standby, traffic with a bar, its
  gateway, errors and SFP. A new link appears by itself.
- Capacity is estimated from the port speed until an admin confirms it with
  **Set the real speed**; "busy" warnings only use a confirmed figure.
- A failure of the primary link is red even when customers are online via the
  backup; a dead backup is amber. Events name the link.
- The chart has a link picker, and a cursor readout (time, download, upload).
- **Live** chart (the default): the last 10 minutes, one point per 15-second
  reading, redrawn every 5 s, times to the second. Raw readings are kept for
  an hour in `network_samples` (migration 0025); everything longer still
  comes from the 1-minute rollups. No router change.
- "Peak today" now says when it happened and what it is (the single highest
  15-second reading). The chart's solid lines are averages, so the peak could
  never be found on them: the chart now draws a faint "peak" line above each
  average, and the cursor readout adds "Highest reading".
- Chart: a **6h** range (1h, 6h, 24h, 7d); round axis numbers (0, 100, 200,
  300) that still follow the traffic; and the total chart is only shaded for
  router-wide silences and the PRIMARY link's outages, so an unpaid or dead
  backup link no longer paints the whole chart red (its own chart and the
  Events list still show it).
- **Not in service** switch (migration 0024): an admin can grey out a link
  that is plugged in but not paid for yet (Veenet), so it stops raising
  warnings and events until it is switched back on.
- Fixed from the first dry run: the gateway ping now uses `/ping`
  (`/tool flood-ping` is blocked by the 2116's device-mode and the script
  hid the error, showing a working gateway as 100% lost); SFP power is
  converted from thousandths of a dBm; ARP states `delay`/`stale`/`probe`
  count as "seen".

## Network page — CCR2116 uplink, gateway and PPPoE clients (Phase 4, 2026-11-04)

Added to Phase 4 at the owner's call. Not live until migration 0022 is run,
`NETWORK_INGEST_TOKEN_CCR2116` is set on Render, and the two router scripts
are installed (docs/network-monitor-runbook.md).

- A **Network** page: router status (green / amber / red, with reasons),
  "All traffic" download and upload against the 350 Mbps contract with
  today's peak, gateway alive / not responding / doesn't answer ping, link,
  errors, CPU, temperature, SFP, and a 1h / 24h / 7d chart with outages
  marked.
- Every PPPoE client on the CCR2116 with name, package and Active / Expired
  from the customer list. Expired-but-connected clients are highlighted;
  unknown usernames are kept, never dropped.
- "Router not reporting since HH:MM:SS", "Gateway not responding" and
  "0 clients online" each look different. When reports resume, Ops records
  how long it was quiet and whether the router stayed up or restarted.
- Two read-only RouterOS scripts, a render tool that fills in the token from
  the environment, a one-step uninstall, and a lint test that fails on any
  router command outside the read-only list.
- Technicians need the new Roles switch Sites → Network (CCR2116). No phone
  numbers or payment amounts on the page.

## Phase 4 — Network Monitoring (in progress, on `phase-4-network-monitoring`)

Not merged yet; the entry below is filled in as pieces land. Full detail and
the reasoning behind each decision is in `phase.md` §4.

**Site status and reports**
- Status and Trends pages for every monitored site, driven by a 60-second
  router heartbeat: online/offline/flapping, people connected, uptime, and a
  per-site report for any day, week or month.
- Acknowledge a known-bad site to move it out of the headline without
  hiding that it's down.
- Hotspot sales are recorded per site, with the package price at the time
  of the sale; Packages shows what each package is worth.

**Alerts**
- Debounced down alerts with a reminder ladder, a one-off flapping alert,
  and a back-online message, by in-app notification, SMS and WhatsApp.
- When two or more sites are down at once, one combined message with a
  "which site gets which battery" plan. A site someone has acknowledged is
  left out of that plan (2026-10-14).
- Message wording is editable (Alerts → SMS Templates).
- Alerts can be limited to set hours of the day (2026-10-14); reminders that
  came due overnight go out once when the hours open, not one per poll.
- Reports → SMS Status shows every alert actually sent.
- Customers and Broadcast: upload the PPPoE customer list and send a manual
  SMS to chosen customers.

**Revenue report** (2026-10-14)
- Reports → Revenue: today / yesterday / week / month / year against a
  chosen comparison, partial periods compared to the same point, key
  findings, a plain-language summary, a sortable site table with each site's
  share of the change, rankings, and a trend chart with an "unusual" band.
- Sites needing attention are flagged red / amber / green by stated rules,
  each showing the numbers that triggered it; thresholds are editable
  defaults. Hotspot sales only for now — PPPoE revenue is not included.
- Past days are frozen, so a report for an earlier date is the same every
  time it's opened.

## Phase 3 — Ops Inventory System (completed 2026-09-17, merged to `main`)

A new inventory/asset-tracking domain for field operations, entirely
separate from battery tracking: enclosures, cabling, consumables, and
power/network gear, none of which had any record before this phase.

**Categories, Locations, Items**
- Categories are user-created and tagged with one of three fixed Tracking
  Types (Asset-Serialized, Inventory-Quantity, Inventory-Length) — the type,
  not the category name, drives which fields and behavior apply, so a
  brand-new category (e.g. "Solar Equipment") needs no code change.
- A category's Tracking Type locks once it has items, to stop a switch that
  would silently orphan existing rows' type-specific fields.
- Items are one table with nullable columns per type, at the correct row
  granularity per type (one row per serial / per batch-lot / per cut).

**Transaction Log**
- Every state change (In, Transfer, Adjustment, Return, Write-off) happens
  only as a side-effect of a logged transaction — item records are never
  edited directly by hand.
- A partial-quantity Transfer splits the batch into two linked log rows
  (origin decrement + new destination row) sharing one grouping id, so
  both halves of the split are individually accounted for in the log.
- Admin-only historical-row editing, scoped to only the fields actually
  sent, so correcting one field never silently blanks the rest of the row.

**Unified Issue Cart**
- One screen, one action: a mixed cart (say 1 enclosure + 2 packs of ties
  + 150m of cable) issues as a single event with one log row per line, all
  linked, regardless of which of the three tracking types each line is.

**Two-stage cable reconciliation**
- A cut/reel goes out in full at issue time; actual metres used are only
  confirmed when the job closes. Reconciling records length used + length
  returned, always depletes the original cut, and — only when the returned
  remainder is long enough to be worth re-stocking — spins off a new cut
  row for it; a too-short remainder is logged as scrap against the
  original instead.
- A cut still awaiting reconciliation past 14 days is visually flagged so
  a dragging job doesn't leave stock unaccounted for indefinitely.

**Reporting**
- SKU/Spec Summary: Total On Hand per SKU (Asset/Quantity types) or per
  Spec (Length), a below-reorder-level flag, and an inline way to set the
  reorder level itself.
- Offcut rollup: total usable leftover length per spec, with a drill-down
  to the individual cuts contributing to that total.

**Permissions**
- Every inventory capability (view/add/edit/delete per section, plus a
  separate "Reconcile Cut" action gated at Manager level) is a grantable
  permission through the existing Roles screen — no new permission
  mechanism needed.

**Restructure (owner review pass, still on the same branch)**
- Categories and Locations moved off the Items page onto their own Manage
  page, reached via a header button — the Items page now shows only Items.
- Every item now has a detail view (unit cost, supplier, batch/cut detail,
  and its full transaction history) reachable from a new View action, so
  the main tables — especially cable's, trimmed to Cut/Reel ID, Spec,
  Remaining, and Location — don't need to carry every field as a column.
- New Cable Type Summary report: reels in stock and total remaining length
  per spec, with a drill-down into the individual reels — separate from
  the existing Offcut Rollup, which only covers unusable remainders.
- SKU/Spec Summary now shows a live weighted-average unit cost per SKU for
  Asset and Quantity types (correctly weighted across batches of different
  size and cost, not a plain average).
- Serialized assets now get a "Deployed" status on issue, resetting to
  "Active" on Return — distinct from Faulty/In Repair/Decommissioned, so a
  Return no longer silently clears a condition issue that was found in the
  field.

**Second refinement pass (owner review, still on the same branch) —
supersedes the Restructure pass's nav pattern and item detail modal before
either was committed**
- Inventory is now a collapsible sidebar group (Items / Transaction Log /
  Manage) instead of header-link buttons on the Items page; Reports is now
  its own standalone top-level nav item, no longer reached from inside
  Inventory.
- Tracking Types are now labeled Asset Core / Consumables Core / Cable Core
  everywhere they're shown.
- Locations no longer has a management screen — the Issue/Return Materials
  Site field is a free-typed autocomplete that creates a new site with zero
  pre-configuration, or matches an existing one by name.
- The Items table is now one row per SKU (not per serial/batch/cut), showing
  Qty and Total Value as *total owned* — on-hand plus deployed combined, so
  a deployed asset's value doesn't disappear from the table — with a second
  filter (In Store / Deployed) and a third (Per-Job / Custody, a new
  category-level distinction with no new transaction type or status
  attached to it). Asset unit cost shows a "~"-prefixed average to signal
  it's computed, not a literal per-unit price.
- The View action is reframed as stock history: an Asset or Cable SKU opens
  a list of its individual units, each opening the same tabbed Details/Logs
  view already used for a battery's detail+history; a Consumable SKU (no
  individual units) opens a flat running-balance history instead.
- Return Materials is now a real cart screen (search, add multiple lines,
  one submit) instead of only being reachable through the generic
  transaction-log form — every asset line requires an explicit status pick
  (never inferred from what it was before), and any active asset is
  returnable this way, not just ones currently checked out to someone.
- Issue Materials' Notes field is now required.

**Third refinement pass (owner review, still on the same branch) — Transaction
Log and Reports**
- The standalone Pending Cable Cuts table is gone. A still-open cable cut now
  shows as "Pending" right in the Transaction Log's own Action column
  (computed at display time, not stored — the underlying log row is never
  edited), switching back to "Issue" once it's reconciled; reconciling a cut
  is now done from that row's own detail view instead of a dedicated table.
- The main Transaction Log table is trimmed to Date / Action / SKU/Spec /
  Qty/Length / Movement / By — Site, Activity, Issued To, exact From/To, and
  Notes aren't gone, they're one click away on the row itself. Movement is a
  single adaptive line per row instead of three separate location columns.
- Inventory Reports is now three tabs (SKU Summary / Cable Summary / Offcut
  Rollup) instead of three tables stacked on one page, with a Category filter
  above the tabs that scopes all three.
- SKU Summary now shows Name (not Category) alongside SKU/Spec, plus a Total
  Value column and a standalone Total Value figure for whatever's currently
  filtered. Reorder Level is read-only here now (with a Low badge) — it's
  set/edited from the Items page instead, via a click-to-edit cell rather
  than an always-open input box.

**Fourth refinement pass (owner review, still on the same branch) — Add
Item split into Add Product / Add Unit**
- Add Item is now a two-step wizard instead of one long form: Add Product
  (SKU, Name, Category, and the make/model-or-spec/supplier details that only
  need entering once per product) then Add Unit (serial/batch/cut detail,
  location, cost, notes — the part that repeats every time stock arrives).
  Adding another unit of a product that already exists skips straight to Add
  Unit via a product search, instead of re-asking for its product-level
  details every time.
- After saving one unit, "+ Add another unit" resets straight back to a
  fresh Add Unit step for the same product, so a batch of new stock (48
  enclosures, 5 splicing machines) can be entered in one sitting without
  re-navigating — every unit added that way is grouped as one batch-receiving
  event in the Transaction Log rather than looking like disconnected entries.
- Every unit added this way now automatically generates its own In
  transaction row — no separate manual "log the arrival" step required.
- Assigned To only shows up on Add/Edit for custody-type categories now — a
  per-job asset doesn't have a "who's holding it" concept the same way.

**Fifth refinement pass (owner review, still on the same branch) — Edit/
Delete restored on the Items table, split into product edit vs unit edit**
- The Items table's Actions column is View/Edit/Delete again (was View-only
  for one round). Edit now opens a proper "Edit Product" form scoped to
  SKU, Name, Category, Make/Model, Spec/Capacity, Supplier, and Unit of
  Measure — the same fields Add Item's product step collects — and applies
  the change to every unit under that SKU at once, instead of showing one
  arbitrary unit's serial number/location/status the way the old combined
  form did.
- The per-unit Edit form (opened from View → the unit list) had the same
  product/unit mixing bug the old Add Item form had, and gets the same
  fix: it's unit-level only now — Serial Number, Location, Status, Assigned
  To, Unit Cost, Install Date, Notes — no SKU/Name/Make-Model/Spec.
- Deleting a product from the Items table is a guard, not a cascade: it's
  blocked with "Can't delete — N units still exist under this product,
  remove those first" whenever any unit remains, rather than silently
  removing everything under that SKU.

**Sixth refinement pass (owner review, still on the same branch) — split
Items into two pages: Items (catalog) and Stock (operational)**
- New "Stock" sidebar item alongside Items, Transaction Log, and Manage
  under the collapsible Inventory nav group.
- Items is now catalog-only: Name, SKU, Category, and Edit/Delete on the
  product — no quantities, cost, or stock movement. A low-traffic
  reference page now, the same way Manage is for Categories.
- Stock is the new operational home for everything else: Qty, Unit Cost,
  Total Value, Reorder Level (still click-to-edit), the Category/Status/
  Custody filters, the View action (stock-history drill-down), the Add
  Item wizard, and the Issue Materials / Return Materials buttons — all
  moved here from Items.
- No backend or schema changes — this is a frontend-only reorganization of
  the same data across two pages.

**Seventh refinement pass (owner review, still on the same branch) — final
Items/Stock column spec, shared View, quantity edits removed everywhere**
- Items and Stock now show almost the same row (Name/SKU/Category/Qty/Unit
  Cost/Total Value) but not the same Actions. Items adds a Location
  column, filters by Category only, and has View/Edit/Delete. Stock
  filters by Category/Status/Custody, has no Location column, and is
  **View-only** — no Edit/Delete on Stock. Items keeps the Add Item wizard
  as its header action; Stock keeps Issue Materials/Return Materials as
  its own. (An initial pass also put Edit/Delete on Stock; corrected back
  to View-only right after.)
- View is one shared implementation, called identically from both pages —
  not duplicated.
- The per-unit Edit form no longer offers a way to directly change a
  batch's quantity or a cut's remaining length. Quantity only ever changes
  as the result of a logged transaction now (In, Transfer, Adjustment,
  Issue, Return, Write-off) — a miscount is corrected via an Adjustment
  transaction on the Transaction Log, which leaves an attributed record
  instead of a silent overwrite.
- Reorder Level no longer has an editable UI on Items or Stock (dropped
  from both tables' final column spec). Reports' read-only "Low" badge
  display is unaffected. The underlying endpoint/table are untouched in
  case this needs a home again later.

**Eighth refinement pass (owner review, still on the same branch) —
Reorder Level gets a home again**
- Reorder Level is now an optional field on the Add Item wizard's Step 1
  (Add Product), alongside SKU/Name/Make-Model/Spec-Capacity/Supplier/UoM.
  Can be left blank at creation.
- Reorder Level is back on Stock as an editable column (click-to-edit,
  same affordance as before). Not added to Items.
- Both entry points write to the same underlying value via the same
  upserting endpoint — there's no separate "set it later" flow distinct
  from "edit it now."
- Stock now shows a "Low" badge next to Qty, computed live, whenever a
  row's currently-displayed Qty is at or below its Reorder Level. Rows
  with no threshold set, or above it, show nothing.

**Ninth refinement pass (owner review, still on the same branch) — batch
quantity for Add Unit's Existing-product path**
- Adding units of an already-known Asset Core product no longer requires
  repeating the single-unit form once per unit. Selecting an existing Asset
  Core product now asks "Quantity to add," with Location/Status/Unit
  Cost/Install Date/Notes entered once and applied to the whole batch.
- Each generated unit gets an auto-assigned internal serial placeholder
  (`{SKU}-01`, `{SKU}-02`, ...), guaranteed not to collide with any existing
  unit under that SKU.
- A new Batch Review step lists every unit just created, with the
  manufacturer's real serial number (if known) and per-unit Location/Status
  correction available inline — each edit saves immediately.
- All units in one batch still write their own "In" transaction row and
  share one event group, so the Transaction Log displays the whole arrival
  as a single receiving event.
- Not applied to Consumables Core (its batch total already lives in one
  row's Quantity field) or Cable Core (each cut/reel is its own distinct
  length) — both keep the existing single-unit form. The New-product path is
  unaffected.

**Tenth refinement pass (owner review, still on the same branch) — statuses,
dates, pagination, Edit Product fields**
- Asset status list simplified to five values (Active, Deployed, Faulty, In
  Repair, Decommissioned) — "Spare — In Storage" removed everywhere it
  appeared, since it overlapped almost entirely with Active.
- Every date input (Install Date, Expiry Date) now defaults to today's date
  instead of blank, on Add Unit, the batch-add form, and Edit Unit — still
  fully editable for backdating.
- The unit drill-down list (View on Items/Stock) is now paginated
  (page-size 10/20/50/100 + Prev/Next, same convention used elsewhere),
  height-capped with a real internally-scrolling container (reusing the
  Battery logs table's own scroll box rather than the generic table
  wrapper, which didn't actually cap height on desktop), and renders
  visibly denser rows than the Items/Stock tables — so large batches
  (including ones created by the new batch-add flow) stay manageable
  instead of blowing the modal out to fit every row.
- Edit Product's field set is now Category, SKU, Name, Spec/Capacity, Unit
  of Measure only. Make/Model and Supplier moved down to the unit level
  (Edit Unit, and Add Unit's Step 2 / batch-mode form) instead, since both
  can genuinely vary batch-to-batch or unit-to-unit even under one SKU.

**Eleventh refinement pass (owner review, still on the same branch) —
Location autocomplete, Unit of Measure dropped**
- Add/Edit Unit's Location field is now a free-typed autocomplete (type an
  existing store name to select it, or a new one to create it), identical
  in behavior to Issue/Return Materials' Site field — previously it was a
  fixed dropdown that could only ever show "Main store," with no way to add
  a second store, warehouse, or van anywhere in the app.
- This field only ever matches/creates store locations, never job sites,
  even if a job site happens to share the same name — the two stay in
  separate namespaces.
- Unit of Measure removed entirely from Add Product and Edit Product — not
  needed anywhere right now; can be added back if a real need for it
  surfaces later.

**Twelfth refinement pass (owner review, still on the same branch) — Cable
Core's SKU/Spec conflict, Cable excluded from Return Materials, Pending
row accent**
- Add Product and Edit Product now hide the SKU field entirely for Cable
  Core — Spec is the only identifying field for this Core, matching what
  the backend already treated it as everywhere else. Fixes a real bug
  where the Spec value was getting typed into SKU by mistake, since SKU
  had nothing else to hold. The backend now also guarantees SKU always
  mirrors Spec for Cable Core regardless of what's sent, closing the gap
  for good rather than just hiding the symptom in the form.
- Cable/Length items no longer appear in Return Materials' search at all
  — every outcome for an issued cut (fully used, partially used, or never
  touched) is already handled by Reconciliation's Length Used/Length
  Returned fields on the Transaction Log. Returning a cable item is now
  also rejected server-side with a clear message, closing off the generic
  Log Transaction form as a back door too.
- Transaction Log rows showing "Pending" (an open cable cut awaiting
  reconciliation) now get a thin red left-border accent, so they're
  scannable at a glance while scrolling a long log.

**Thirteenth refinement pass (owner review, still on the same branch) —
depleted reels excluded from Issue Materials**
- A cable reel/cut with zero length remaining (from full usage or a
  fully-reconciled cut) no longer appears as a selectable result when
  searching in Issue Materials — filtered at search time, the same way a
  zero-quantity Consumable is treated as out of stock, rather than left to
  fail on submit.

**Fourteenth refinement pass (owner review, still on the same branch) —
Transaction Log / Item Logs UI fixes**
- Fixed Date-column truncation in the per-unit Details/Logs modal: widened
  the Date column and dropped the year from that one table's date format.
- The View (eye) action icon now gets the same hover highlight as Edit and
  Delete, on both the Items and Stock tables.
- The main Transaction Log page had no pagination at all and could grow
  unbounded — it now paginates with the same page-size/Prev-Next controls
  used elsewhere in this phase (Battery's own `10/20/50/100` options, not
  a fixed-height inner scrollbox — the table renders one page of rows and
  stops, and the surrounding page/modal scrolls normally if needed).
- Corrected within the same pass, before merge: the first version of the
  pagination fix above nested a 360px scrollbox inside both the Transaction
  Log page and the per-unit Logs modal, and used 10/30/100 as the page-size
  options. Owner feedback: no inner scrollbar anywhere here, and match
  Battery's actual 10/20/50/100 convention — both reverted.

**Fifteenth refinement pass (owner review, still on the same branch) —
Log Transaction's Item field is now a search box**
- The generic Log Transaction form's Item field was a flat `<select>` listing
  every individual unit — every serial, every cable reel — which meant
  scrolling a long list to find one as unit counts grow. It's now a
  type-to-filter search input, same interaction and same searchable fields
  (name, SKU/spec, serial, lot, reel id) as Issue/Return Materials' existing
  search box, applied to the same underlying unit data.
- The search-matching logic itself was factored into one shared function
  (`inventoryItemMatchesQuery`) used by Issue Materials, Return Materials,
  and this new Log Transaction search, instead of being duplicated a third
  time.

**Sixteenth refinement pass (owner review, still on the same branch) —
Cable Core: Units view vs Reports drill-down, full split**
- The Units view (Items/Stock → View) now excludes fully-depleted cable
  reels (`length_remaining = 0`) by default — it answers "what do I
  currently have," and a used-up reel isn't part of that. Asset/Consumable
  units are unaffected.
- Reports → Cable Type Summary's drill-down now lists every reel ever
  created for a spec, not just in-stock ones — including depleted reels,
  with their Status and last known Location before they closed out. The
  top-level Cable Type Summary counts are unchanged (still in-stock only).
- Clicking View on a reel in that drill-down opens the same per-unit
  tabbed Details/Logs modal used everywhere else, showing its full history
  regardless of whether it's currently in-stock or depleted (already wired
  up from an earlier round, confirmed still working here).

**Seventeenth refinement pass (owner review, still on the same branch) —
Log Transaction: final field spec per Action, plus two UI fixes**
- Removed "In" as an option from the Log Transaction form entirely, front-
  and back-end — receiving new stock only ever happens through Add Product/
  Add Unit now (`POST /inventory/transactions` rejects `action: "In"` with a
  400, not just a hidden dropdown option).
- The form's field set now changes shape by Action: Transfer shows Item,
  Qty/Length, From/To location, Notes; Adjustment and Write-off show Item,
  Qty/Length, Notes only; Return shows Item, Qty/Length, Notes, plus the
  existing required Status picker for a serialized asset — none of the four
  show a location field for Return, since it always resolves to the single
  default store. Site, Activity, and Issued To are gone from this form
  entirely — those only ever applied to Issue, which has its own dedicated
  screen.
- Qty/Length's number input now has a themed custom up/down stepper instead
  of the browser's plain, unstyled native spinner.
- Transfer's From/To location fields are free-typed autocomplete (type to
  match an existing store or create a new one), matching the same pattern
  already used by Add/Edit Unit's Location field and Issue Materials' Site
  field — no longer a fixed dropdown.

**Legacy data import — `items_2026-09-12.csv` (2026-09-12)**
- Imported the old flat inventory export (70 products, no Core distinction,
  no per-unit tracking, no transaction history) into the new model, entirely
  through the real Add Product/Add Unit flow — no direct table writes. 308
  physical units created across 66 products (4 zero-quantity source rows
  skipped, nothing to import from those), each with its own auto-generated
  ID and its own "In" transaction log row.
- 9 new categories created (Passive Network Material, Access Network
  Equipment (Last-Mile), Network Infrastructure Equipment, Power Equipment —
  all Asset Core; Batteries, Installation Materials & Consumables,
  Transmission & Backhaul Equipment — Consumable Core; CAT 6 Cable, Drop
  Cable — Cable Core), plus the existing "Tools" category reused as-is —
  Core assignment was decided per-product by nature, not mechanically copied
  from the old flat "Category" column, since several old categories mixed
  Cores (e.g. CAT 6 Cable and two Enclosure rows both sat under the old
  "Installation Materials & Consumables" label despite needing Cable Core
  and Asset Core respectively).
- Data-quality fixes applied during import: "Enlosure" → "Enclosure" and
  "Saftey belt" → "Safety belt" typos corrected before product creation;
  "Battery" split into Consumable AA/AAA vs. a separate Asset "Battery —
  Vestwood 1kWh"; Cable ties / Cable ties packet merged under one Name with
  two Spec-driven SKUs; Universal Pole Bracket (UPB) / UPB merged into one
  SKU across two batches (61 @ 250 KES, 121 @ 230 KES — same product,
  different receiving costs); the "drum-used" Drop Cable reel got a Notes
  annotation instead of being silently trusted as new stock, since the
  source Condition column was confirmed unreliable; "Kwa Mlima"/"Kwamlima"
  spelling normalized to one location.
- Owner-confirmed: Hithium Battery and TP Link EAP225 Outdoor AP (both
  listed as Qty 3 across "Njeri House, ACK, Kwamlima" with no breakdown in
  the source) are entirely at the existing "Jasiri Net Office" location, not
  split across those three sites.
- Selling Price and Reorder Level columns dropped per the import brief
  (always 0/meaningless in the source); Condition dropped as unreliable
  except the one annotated case above.

**CSV export — Items, Stock, Transaction Log, Reports**
- Added an Export CSV action to Items, Stock, the Transaction Log, and each
  tab of Reports (SKU Summary / Cable Type Summary / Offcut Rollup) —
  client-side only, no new endpoint. Each button downloads exactly the rows
  already on screen for that page, so it respects whatever Category/Status/
  Custody/Action filter is currently applied rather than always dumping the
  full table.
- Column sets match each page's own on-screen table exactly (Items: Name/
  SKU/Category/Qty/Unit Cost/Total Value/Location; Stock: same minus
  Location, plus Reorder Level; Transaction Log: Date/Action/SKU-Spec/
  Qty-Length/Movement/By; Reports: each tab's own columns) — this was a
  deliberate choice over the original request's suggested column list, since
  that list (e.g. Unit of Measure on Items) named fields since dropped from
  the UI in earlier refinement passes.
- The Transaction Log export uses the full filtered result set, not just
  the current page — pagination narrows what's rendered, not what's
  fetched, so the export isn't limited to whatever page size is selected.
- New shared `exportRowsToCsv()` helper in `inventory-common.js` (RFC
  4180 field quoting, `Blob` + a throwaway `<a download>`) — one
  implementation, called from all four pages instead of four copies.

**Import correction pass (owner review, 2026-09-16) — Tools split, CAT 6
downgrade, ADSS placeholders, dev debris cleanup**
- Along the way, found that the dev database had accumulated leftover
  test/seed data from earlier milestone verification, sitting alongside
  the real September import under the same "Tools" category and a fake
  "ADSS Cable" category (confirmed via `created_at` timestamps and
  absence from the real source file — not real stock). Deactivated all
  of it through the app's own delete endpoints rather than a raw DB
  write.
- "Tools" split into five real categories — PPE / Apparel (Custody),
  Field Tools (Per-Job), Office Furniture (Per-Job — no separate
  "unassignable" custody type exists, so this is the closest fit and
  nothing enables assignment on it in practice), Personal Equipment
  (Custody), Vehicles (Custody) — 21 real products reassigned via
  `PATCH /inventory/products`, not a raw insert.
- CAT 6 Cable downgraded from Cable Core to Consumable Core: the 10
  existing 305m reels retired, the category's tracking type flipped
  (only possible with zero active items under it), and one Consumable
  product created — Qty 10, Unit of Measure "drum". Issue/Return now
  moves whole drums, no partial-length tracking.
- ADSS Cable (24C/48C/96C): real per-reel breakdown isn't available yet
  (reel sizes vary per purchase, and the source only had spec-level
  totals), so this now holds mock placeholder reels instead — 2 per
  spec, clearly marked in both the reel ID and Notes as placeholder,
  pending the owner's real reel-by-reel entry.
- Drop Cable's "used" drum (`DROP-56`) corrected from its imported
  default (full length, just a Notes flag) to its real measured
  remaining length (300m), via a logged Adjustment transaction, not a
  direct column edit.
- The 7 products still genuinely blank-location after the original
  import (Baofeng Radios, Battery — Vestwood 1kWh, Ethernet Adapter,
  Helmet, Splicing Machine, Tension clamps PA-1500, J-Hooks — 13 units
  total) set to Jasiri Net Office.

**Bug fix — Cable Core's Total Value on Items/Stock**
- `get_items_summary()` was pricing Cable Core rows as `qty (metres) x
  unit_cost (per reel)` — the same formula that's correct for Asset/
  Quantity, where qty is a count of individually-priced units, but wrong
  for Cable, where a reel is priced as a whole unit regardless of its
  length. Two 7,000/reel Drop Cable drums summing 2,001m priced out at
  7,000 x 2,001 instead of 7,000 x 2 reels — a difference of orders of
  magnitude, caught by the owner reviewing the exported Items CSV.
- Fixed to `SUM(unit_cost)` across the actual reels instead — Drop
  Cable's two drums now correctly total 14,000. SKU Summary (Reports)
  was never affected — it already deliberately shows "—" for Cable's
  Total Value rather than computing one.

**Eighteenth refinement pass (owner review, 2026-09-17) — Stock table
styling, sidebar divider, Issue/Return Materials layout**
- Stock's Qty column moves the "Low" badge inline next to the number
  instead of stacking it below; Reorder Level renders as plain red/green
  text (red at/below the level, green healthy, uncolored when unset)
  instead of a grey badge.
- Added a vertical divider between the sidebar and main content, matching
  the existing horizontal divider under the header.
- Issue Materials and Return Materials are now a two-column layout: search
  + form fields on the left, Cart (heading, lines, submit) on the right —
  a pure repositioning, no component restyling.

**Nineteenth refinement pass (owner review, 2026-09-17) — Inventory
permissions restructure**
- The Inventory permissions panel is now a real hierarchy: Inventory
  (master) → Inventory Items (toggle, its own Add/Edit/Delete checkboxes)
  and Stock (toggle, Edit Reorder Level/Issue Materials/Return Materials/
  View Stock History checkboxes) as independent nested children, plus
  Inventory Categories and Inventory Log as their own independent toggles.
  Every toggle here gates real nav visibility and route access (a route
  denied by permission now redirects away even via a typed-in hash, not
  just a hidden nav link) — a gap found and fixed during this pass.
- The separate "Add Stock" permission is gone, merged into "Add item" —
  creating a new product and adding a unit/batch to an existing one are
  the same grantable capability now.

**Twentieth refinement pass (owner review, 2026-09-17) — Quick Issue /
Quick Return**
- Asset-core lines in the Issue/Return Materials cart can now be filled by
  typing a quantity ("5") instead of picking every serial individually —
  Quick Issue auto-selects from in-stock/eligible serials, Quick Return
  auto-selects from serials actually checked out to a chosen site or
  person. Manual per-serial picking still works side by side with it. The
  auto-picked serials show up as an expandable list in the cart line so one
  can be reviewed or swapped out before confirming.
- Auto-select order is deterministic (picks from the bottom of a stable
  list order), not effectively random — fixed alongside a real ordering
  bug: `get_all_items()`'s `ORDER BY name` had no tiebreaker, and every
  unit under one SKU shares the same name, so which serial came "first"
  could silently shift between requests.
- Return Materials' search (and Quick Return's pool) is now scoped to only
  what's actually issued out (Deployed assets) — it previously showed the
  same full/available stock list Issue Materials does, which is backwards
  for a Return screen.
- Every place an asset's status (Active/Deployed/Faulty/In Repair/
  Decommissioned) displays as read-only text now shows a color-coded pill
  instead of plain text, consistently across Items, Stock, and Issue/
  Return Materials. The Return cart's status `<select>` also picked up
  real dark-theme styling — it had none before (a plain unstyled browser
  dropdown).

**Reports — independent permission toggle (2026-09-17)**
- Reports now has its own dedicated master permission (`reports:view`),
  separate from Inventory Items — previously it piggybacked on
  `inventory_items:view`, so a role couldn't be granted one without the
  other. Enforced both in nav/route gating and on every report endpoint
  server-side.

**Items page Qty — reverted to on-hand default (2026-09-17)**
- Supersedes the Sixth/Seventh refinement passes' "Qty = total owned
  (on-hand + deployed)" decision above: Items' Qty (and Total Value) now
  default to on-hand/available quantity, not the combined total — showing
  the total by default read as the app under-reporting what had actually
  been issued out. Applies to Asset and Cable Core; Consumables were
  already correct (no separate "deployed" bucket exists for that Core).
  Stock's own Status filter (All/In Store/Deployed) is unchanged.
- Along the way, the Core-lock error (editing a category's Core when it
  still has items) was upgraded from a generic block to a specific message
  stating the exact item count and suggesting the real workaround (create
  a new category with the desired Core) — the inline modal hint and the
  API's own 400 detail share the same wording now, sourced from one place.

**ATB / Passive Network Material Core correction (data migration,
2026-09-17)**
- ATB items (55 units) were sitting in an Asset-core category as
  individually "serialized" rows despite being fungible stock nobody
  tracks by serial — moved into a new Consumables-core category ("ATB /
  Passive Enclosures") as quantity-tracked rows (2 + 53 units); the old
  serialized rows were soft-deleted, not hard-deleted, so their original
  "In" history stays intact.
- "Passive Network Material" was split three ways: Splitter/SFP (46
  units) moved into a new Consumables-core category ("Passive Optical
  Consumables"), same migration pattern as ATB; Enclosure/ODF/Bridge (12
  units) stayed in "Passive Network Material" as Asset Core, correctly
  narrowed; Media converter/8-Port Reverse POE (3 units) moved into the
  existing "Network Infrastructure Equipment" category — a same-Core
  product move, no row recreation needed.

## Phase 2 — Finish Incomplete Functionality (completed 2026-09-06)

One item (inline record detail in global search) was dropped by the
owner's explicit call. Two migrations found necessary along the way
(`0002_backfill_in_transit_at.sql`, `0003_close_out_site_still_down.sql`)
were never run against production — discarded per the owner's explicit
call rather than pursued further; see [phase.md](phase.md) for detail.

**Battery status & movement lifecycle**
- Battery status (At Base/Pending/In Transit/Deployed) is now driven
  end-to-end by the linked movement's lifecycle rather than partly stored,
  partly derived. A movement landing back at home base now resolves to
  "At Base", not "Deployed" — previously any arrived/completed movement
  read as "Deployed" regardless of destination.
- A site-down move that comes back "still down" now closes the movement
  out as `completed` (done, no further status changes expected) instead
  of sitting in the movements list forever under its own status. The
  battery itself stays flagged as needing attention — tracked on the
  destination site's `is_online`, not on the movement — until someone
  confirms the site back online, whether via a later movement or Check
  Sites directly.
- That "needs attention" flag no longer changes the battery's status
  label or the Deployed stat card's count. It shows instead as: the
  status pill itself recoloring to red on the main Battery Tracker table
  (still reads "Deployed"), and a red left-edge accent on the row inside
  the stat-card click-through detail. A battery can't be set to
  "charging" while flagged this way, even after its movement has closed
  out — enforced server-side, not just hidden in the UI.
- Timestamps ("Since" columns, movement history) now compute against East
  Africa Time (fixed UTC+3, no DST) instead of the server's UTC clock —
  fixes Check Sites' 8am–8pm active-check window, which was effectively
  running 11am–11pm Nairobi time before.
- The Move Battery modal's "Reason" field is no longer a bare `<select>`
  — replaced with the same custom dropdown component already used for the
  charge-status picker, so it actually matches "Move to"/"Moved by" in
  color and behavior (a native select's own chevron/box-model, and its
  open option list, can't be reliably restyled to the app's dark theme).
- Live sync between Movements and the Battery Tracker table is now under
  2 seconds (was 5s), and both tables skip re-rendering on a poll tick
  when the fetched data hasn't actually changed — previously every tick
  rebuilt the whole table regardless, which tore down and recreated every
  row's buttons and read as a visible flicker.
- Static assets (`/static/*` — every JS/CSS file) now send
  `Cache-Control: no-cache`, so a plain refresh always revalidates
  against the server instead of serving a stale cached copy — several
  "my change isn't showing up" reports this phase turned out to be this.

**Battery movements**
- Fixed: the person typed into "Moved by" when moving a battery was being
  silently discarded — the logged-in user's name was recorded instead
  regardless of what was typed. The typed name now wins; falls back to
  the logged-in user only when left blank.
- Added: "Moved by" is now a typeahead — free text stays allowed, but a
  filtered, clickable dropdown of active users appears as you type, and a
  non-blocking warning shows if what's typed doesn't match a known user.
- Added: the move modal's "Move to" destination field is now the same
  typeahead — a filtered, clickable dropdown of sites appears as you
  type — but blocking: unlike "Moved by", the move can't be confirmed
  until what's typed matches a known site, since the destination has to
  resolve to a real location id.
- Fixed: cancelling a movement from the Movements page no longer leaves
  the Battery Tracker table showing stale data until a manual reload.
- Split "move authorization" into two separate, independently-grantable
  permissions: starting a new move ("Move Battery") vs. acting on a move
  already in progress ("Manage Movement").
- The topbar/sidebar movement badge now reflects a live count of
  unresolved movements (pending/in-transit/etc.), matching how the Check
  Sites badge already worked — it previously only counted movements stuck
  for 1+ hour, so a fresh move never moved the badge at all.

**Battery Tracker**
- Stat cards (Deployed/Charged/Charging/Low/Unknown) are now clickable —
  opens a detail table of exactly the batteries in that state (battery #,
  location, status, since).

**Settings**
- Password fields (current/new/confirm) now have a show/hide toggle.
- Profile/Password tabs restyled to match the app's existing tab
  convention, replacing a plain, unthemed-looking pill toggle.

**Roles**
- Edit/Delete Role buttons now match the rest of the app's icon-button
  styling (previously unstyled native buttons).

**Global search**
- Search can now reach permission-gated "add" actions (add user, add
  site, add battery, add movement), not just existing records.
- Movements and Check Sites are now reachable from search at all — they
  weren't before (not sidebar nav items, so never indexed).

**Mobile / scrolling**
- Fixed a table-scroll bug affecting both desktop and mobile: on desktop,
  hovering a table silently blocked page scroll even though the table had
  nothing to scroll internally; on mobile, scrolling a table to its
  internal limit didn't hand off to page scroll, it just stopped.

## Phase 1 — Mobile Fixes

- Added the missing viewport meta tag (mobile rendering was effectively
  untested before this).
- Sidebar nav rebuilt as a push-open off-canvas drawer on phones.
- Topbar search collapsed to an icon-only trigger on phones.
- Every view's table wrapped in a horizontally-scrollable container,
  height-capped with a sticky header on phones; Battery Tracker
  additionally gets a frozen first column.
- Stat-card grid tightened to 2 columns on phones.
- Found and fixed along the way: a topbar `overflow:hidden` silently
  clipping the profile dropdown, a `border-collapse` setting silently
  breaking sticky table cells, and a flex default forcing the page wider
  than the viewport.

## Phase 0 — Separate & Polish

- Split a monolithic backend into `routers/` + `db/`, one file per
  domain (auth, permissions, sites, batteries, users).
- Split the frontend into per-view HTML fragments, ES modules, and CSS
  files.
- Consolidated permission-checking into one shared function used
  everywhere, replacing per-domain duplication.
