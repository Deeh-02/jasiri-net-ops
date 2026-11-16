# PHASES.md — Live Status Tracker (JASIRI NET OPS)

Check this file before starting any work. If a request doesn't match the
current phase's declared scope, flag it — don't do it anyway.

Completed phases collapse to one summary line (name + completion date)
once confirmed done — full detail below is only kept for the active phase
and whatever's still upcoming.

---

## ACTIVE PHASE: Phase 7 — Finance

Started at the owner's explicit call, 2026-11-15, after Phase 6 was merged to
`main`. Branch: `phase-7-finance` (from `main`). The owner's spec is "Jasiri
Net Ops: Finance Module Spec, Draft v1, 15 Nov 2026". Upcoming phases
renumbered: Notifications 7 → 8, Ticketing 8 → 9, CRM 9 → 10, Chat 10 → 11.

### Purpose

Add money tracking to OPS: income, expenses, profit and equipment value.
Existing stack only (FastAPI, raw SQL, PostgreSQL, plain JS/CSS). The spec's
principles hold throughout: income is read from data the app already has,
not typed; every figure opens the rows behind it; projections and estimates
are labelled; missing data shows "no data", never 0; **the AI never decides
an amount** (code reads amounts, codes and dates; DeepSeek only suggests
categories and reads unrecognised messages, and any amount it returns must
appear verbatim in the original text); nothing is saved from a draft without
the user confirming it.

### How the spec maps onto this codebase (read 2026-11-15)

- **Roles.** OPS has no fixed "finance" role; it has custom roles with
  `section:action` permissions, and admin passes every check
  (`routers/permissions.py`'s `user_has_permission`). Finance becomes a new
  `finance` section in Roles (`roles.js`'s `PERM_SECTIONS`), checked on every
  finance endpoint and mapped in `common.js`'s `ROUTE_PERMISSION_MAP`. A
  "Finance" role is then just a role with those switches on.
- **Sidebar.** A new FINANCE group in `static/index.html`, built like the
  existing groups. The spec's JS menu object is not used: the nav is HTML.
- **Hotspot income already exists.** `revenue_events` (sales read off the
  router's user list, priced from `hotspot_packages`) and the frozen
  `daily_site_revenue` / `hourly_site_revenue` tables. Finance reads these
  through `db/revenue_report.py` / `db/revenue_daily.py` functions; it does
  not copy them into a `payments` table, so the Revenue page and Finance can
  never disagree. These are not M-Pesa payments: there is no M-Pesa code or
  payment method per hotspot sale, so the "M-Pesa 92% · Bank 8%" line can
  only cover PPPoE/other income.
- **PPPoE income does not exist in OPS yet.** `ppp_customers` (loaded from
  the FreeISPRadius export) has plan name, expiry and status, but **no price
  and no payments**. A source has to be chosen (open question 1 below) before
  PPPoE income, expected-vs-received or the month target can be built.
- **Assets overlap with Inventory.** Phase 3 already registers serialised
  equipment (`inventory_items`: serial, unit cost, supplier, status,
  location, custody) with a full movement log (`inventory_transactions`).
  Finance should add the money fields (purchase date, useful life, salvage,
  warranty) and the book-value / depreciation view on top of Inventory, not
  build a second asset register. ONU-to-customer links already exist from
  Phase 6 (`onu_customer_links`). **Confirmed by the owner, 2026-11-15.**
- **DeepSeek.** Reuse `db/deepseek.py` (requests, key and model from
  `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL`, default `deepseek-v4-pro`), not a
  second client. Add a `FINANCE_AI_ENABLED` switch so rules-only works.
- **Conventions kept.** Timestamps are UTC `timestamp without time zone`
  (EAT via `db/connection.py`), money is `numeric(12,2)`, migrations are
  numbered SQL files run by hand, no silent deletes (void with a reason, in
  the audit log), pure rules in a `*_logic.py` module with fixed-input tests.

### Sub-phases and gates

| Sub-phase | Delivers | Gate to start |
|---|---|---|
| **7A** Foundation | `finance` permissions, FINANCE sidebar group (empty pages say "coming next"), expense tables, audit log | Owner approves the 7A schema |
| **7B** Expenses | Paste parser (regex), supplier rules, review screen, save; cash one-liners; then DeepSeek assist | 7A merged into the branch; 20–30 real messages to test against |
| **7C** Income | Hotspot from the revenue tables; PPPoE from the chosen source; segment toggle, KPI cards, trend, transactions | PPPoE income source decided |
| **7D** Performance | Read-only monthly statement from income and expenses, cash basis, Draft label | 7B and 7C in use |
| **7E** Income extras | Expected vs received, unmatched payments, month target, Needs attention | PPPoE prices and due dates available |
| **7F** Assets | Finance view over Inventory: book value, depreciation feeding 7D, "Add to assets" from a capital expense, warranty alerts | Asset-fields schema approved |
| **7G** Month-end (optional, deferred) | Closing a month, PDF/CSV exports, M-Pesa statement check | Owner decides to pick it up; 7D trusted for one full month |

### Progress

**7A (2026-11-15):** schema approved by the owner and written as migration
`0037_finance_expenses.sql` (`expense_categories` seeded with the spec's
list, `expenses` with void-not-delete, `supplier_rules`,
`finance_audit_log`). Run on the `.env` database (production) at the
owner's request, 2026-11-15: four tables created, 15 categories seeded,
read back through `db/finance_expenses.py`. Not run on a local dev
Postgres (it refused the password in `db/connection.py`). Roles > Finance (`finance:view`, tick `finance:manage_expenses`),
`routers/finance.py` checking both, `GET /finance/categories`, and the
FINANCE sidebar group (after Reports) with five placeholder pages. Tests:
`tests/test_finance.py`.

**7B, rules first (2026-11-15):** built from the owner's 30 real messages
(kept outside the repo; tests use made-up copies of each format). Reader
`db/finance_messages.py` handles WhatsApp-forwarded paybill, till, send
money, payee-side receipts (`KES … paid to … Ref:`), bank transfers
(`Transaction Ref … Bank Ref`), airtime, the transaction cost when present,
and cash lines (`fuel 2000`); reversals and money received are shown, never
saved; anything else is shown as "Couldn't read". Rules are keyed by payee
plus account first (FREEISPRADIUS `smstopup` vs its subscription account),
then payee. Review groups unknown payees, offers Skip for transfers to the
owner's own accounts, and Save sends only choices: the server reads the
paste again for every figure. Edit (category, type, segment, description)
and void with a reason; both audited. Checked in headless Chromium against
the real database (read only; nothing saved). Still open: "always skip
this payee" (needs a small schema change: a rule kind `skip`).

**7B DeepSeek step (2026-11-15):** `db/finance_ai.py`, one call per paste
from a separate `/finance/expenses/suggest` (the review shows first; the ✨
fill in after, never over a category the user already picked). Sent: new
business payees (paybill, till, receipts) with count and total, unread
messages with phones and balances removed, the optional note. Never sent:
people, bank transfers, cash, phone-number accounts. Categories outside the
list become Other. An unread message is accepted only when DeepSeek quotes
the amount, date and code exactly as they appear; the code reads the date
itself, and the check runs again on save. Off unless `FINANCE_AI_ENABLED`
is true (and the key is set). One live call with made-up messages:
categories right, routers marked capital, the odd message read and checked,
about 2 s. Model is `DEEPSEEK_MODEL` (default `deepseek-v4-pro`, as the
assistant uses); the spec asks to re-check model names and pricing at
api-docs.deepseek.com before switching it on. Found and fixed on the way:
an unknown message on the line right after a known one was being joined
onto it and vanished; now each line starts a new message unless it clearly
continues the one above. **Vendor (owner's call,
2026-11-15):** the payee is the name in the message when there is one,
otherwise blank; the user can write or correct it on the review screen and
when editing. A typed name is not remembered by the supplier rule (the rule
holds category and type only): remembering it would need a `display_name`
column on `supplier_rules`.

**7C Income (2026-11-15):** Finance > Income with the All / PPPoE /
Hotspot toggle and month in the URL (`#/finance-income/2026-11/hotspot`).
Hotspot income is read from `revenue_events` through new functions in
`db/monitoring.py` (its owner), not the frozen daily tables: Finance shows
whole-business totals, and those must include the few sales never linked to
a site, which the daily tables leave out by the owner's 2026-10-14 call
(KSh 910 in September, none since). A sale's price and time never change
after it's recorded, so past totals are stable. October reads KSh 353,400,
the same as the Revenue page. KPI tiles (total, vs last month like for like,
average a day over completed days, best day, today so far), the split bar
(PPPoE and Other say "no data yet"), trend by day with last month dashed /
week / month, weekday averages with n, income by hour, top packages,
accounts that paid (first time, more than once), and a transactions table
(search, 50 a page, CSV). Best day, a day's bar and a package each narrow
the table to their own rows, which add up to the figure. Phone numbers in
accounts are masked (2547****0050). PPPoE shows "not recorded yet", per the
owner. Rules in `db/finance_income_logic.py`, tested. Left for 7E: month
target, expected vs received, Needs attention.

**7D Performance (2026-11-15):** the page keeps the name "Performance"
(owner's call). Monthly statement (this month | last month | change):
income by segment, direct costs, gross profit, operating expenses by
category (each unfolds into its expenses; income lines open Income), operating
profit, depreciation (waits for 7F) with taxes and charges, net profit, with
margins. Tiles: net profit, margin, income, expenses, break-even day. 12-month
income/expenses bars with a profit line, where the money went, and notes
(capital purchases left out, expenses in Other, direct-cost categories with
nothing recorded). PPPoE / Hotspot stop at "before shared costs" and count
only costs tagged with that segment. Cash basis and the unconfirmed VAT
treatment are stated at the top. Missing is never zero: PPPoE income marks
every total "hotspot only"; a month with no expenses recorded has no profit
figure; the current month is Draft with no change column. Rules in
`db/finance_performance_logic.py`, tested. Overview's contents are not in the
spec: its placeholder said so and waited for the owner (filled in on
2026-11-16, see "Design pass and the Snapshot" below).

**7E, the parts that need no new table (2026-11-15):** on Income, a Month
target bar (break-even = average monthly operating expenses over up to 3
recent months that have expenses; last month as a marker; for the running
month a projection: completed days plus each remaining day at the average of
the same weekday over the last 6 weeks, labelled an estimate) and Needs
attention (unpriced packages, no sale for 3+ hours between 07:00 and 22:00,
a day with no sales, a day under 60% of its usual weekday, sales not linked
to a site), each day item opening that day's sales. Waiting: the typed
monthly target needs a table (schema proposal with 7F); expected vs
received and unmatched payments need PPPoE payments.

**7F Assets (2026-11-15):** schema approved and run (migration 0038:
`asset_values`, `asset_category_lives` seeded with the owner's lives,
`finance_month_targets`). Owner's calls: purchase dates option A (no date,
no book value: nothing estimated; a date can be set for a whole category's
undated items at once); Personal Equipment is company property and stays on
the register. Assets page: tiles (cost, book value, this month's
depreciation, deployed, in stock, faulty, warranty ending), Needs attention,
category lives (edit + date-all-undated), register with filters, and an item
dialog (money facts, the capital expense that paid for it, movements from
Inventory's log). Inventory stays the register: Finance reads it through
`db/inventory_items.py` / `db/inventory_transactions.py` and never writes
it. Straight-line depreciation now feeds Performance's Depreciation line
(says how many items were counted or left out). New permission
`finance:manage_assets` (Roles > Finance). Also the 7E typed month target:
"Set your own target" on Income's bar (All view), which replaces break-even
as the target while set. Right now: 125 items, KSh 983,528, none dated.

**Design pass and the Snapshot (2026-11-16, owner's request):** "make
Finance read at a glance, with charts, and feed the Overview with best
practice for now." No new table and no new endpoint.
- **Finance > Overview is now "Snapshot"** (route and files keep the name
  `finance-overview`). It owns no figures: it draws from the Income,
  Performance and Assets reports, each its own request, drawn as it arrives.
  One sentence on where the month stands, five headline figures, then:
  income adding up day by day against last month, break-even and the
  month-end estimate; Needs attention gathered from the three pages; income
  against expenses by month with profit as a line; where the money went;
  where income came from; the steps from income to profit; equipment. Every
  card links to the page behind it. Its contents are the builder's choice
  "for the time being": the owner may still change what it shows.
- **One chart kit for all five pages** (`static/js/finance-common.js`,
  `static/css/finance-common.css`; rules in design.md, "Finance charts"):
  the same tiles, cards, bars, hover label and colours everywhere. Income:
  hover labels, an average line, the busiest hour picked out. Performance: a
  profit bridge beside where the money went. Expenses: tiles, spending by
  category and by day, each narrowing the list to its expenses. Assets:
  cost by category (opens the register on it) and status.
- **Reports are remembered for three minutes in the browser**, so going
  from the Snapshot to Income doesn't wait again; any save, edit, void,
  target or asset change clears them.
- **Sidebar:** three pages were called "Overview". Finance's is "Snapshot"
  (pie icon), Technical Activity Log's is "Summary" (pulse icon; page title,
  Roles tick and the assistant's guide renamed to match); Operations keeps
  "Dashboard". Outside Finance's file scope, done at the owner's request.

**Depreciation taken out (2026-11-16, owner's call):** "too much maths for
lives we only guessed." Book value, depreciation, useful life and salvage
are gone from every page and from the API; the purchase date is optional
and gives an item's age; the "no purchase date" warning is gone. In their
place Performance (and the Snapshot) take equipment off below Net profit:
Net profit − Equipment bought = **Cash left**, with the capital expenses as
its drill-down. No schema change: `asset_category_lives` and the life/salvage
columns stay, unused. Assets keeps cost by category, status, warranties,
"Paid by", and a per-category "Set date".

**Revenue Overview in Finance; expenses spreadsheet import prepared
(2026-11-16, owner's requests):** Income > Hotspot now carries the Revenue
Overview graphs (`static/js/finance-hotspot.js`; the Reports > Revenue
> Overview page is untouched). `tools/import_expenses_csv.py` reads the old
expenses spreadsheet (429 rows, May to Nov 2026, about KSh 3.75M) and files
each row under OUR categories by its title and vendor, not the spreadsheet's
own, deciding running cost vs equipment kept; guesses are marked REVIEW.
Preview only until the owner approves: it writes a review CSV and saves
nothing without `--apply`. Rows that match an expense already in the app,
or repeat another row, are held back. Two categories the old file needed
(Office & supplies, Tools & safety gear) are proposed as data rows, not a
schema change.

**7G Month-end, deferred (2026-11-15):** owner's call: 7G is a "nice to
have", not a must for Phase 7. Nothing built; revisit as the module gets
used. If picked up: exports and the M-Pesa statement check need no new
table; closing a month needs one (proposed for approval first).

### Open questions (from the spec, with what the code already answers)

1. **PPPoE payments: where do they come from?** Not in OPS today. Options:
   a FreeISPRadius payments export uploaded like the customer export; the
   M-Pesa statement CSV; or pasted M-Pesa received messages (reusing the 7B
   parser). Decides 7C and 7E. *Owner, 2026-11-15: being worked out. Until
   then PPPoE income stays blank and the build carries on with everything
   else.*
2. **PPPoE package prices and due dates.** `ppp_customers.expiry` is the
   due date; plan prices are not stored anywhere. Read from billing, or a
   PPPoE price list like the hotspot Packages page?
3. **Hotspot sales:** answered. Router user list → `revenue_events`.
4. **Same Till/Paybill for PPPoE and hotspot?** Still open; matters only if
   PPPoE income comes from M-Pesa data.
5. **Name of the profit page.** Working name "Performance".
6. **Equipment today:** partly answered, Inventory holds it. Still open:
   do customers return ONUs/routers on disconnection? *Owner, 2026-11-15:
   so far no, customers buy their own; left open until fully clarified, so
   the "to recover" list is not built yet.*
7. **VAT / excise treatment.** For the accountant; the Performance page
   states the basis used.
8. **Current package prices:** hotspot prices are on the Packages page;
   PPPoE prices see question 2.

### Done when

The owner checks out `phase-7-finance` locally and confirms: expenses can be
pasted, reviewed and saved with duplicates skipped; Income and Performance
show the month for All / PPPoE / Hotspot with every figure drilling down to
its rows; Assets shows what Inventory's equipment cost, its status and
warranties, and Performance shows the cash left after equipment bought (no
depreciation: owner, 2026-11-16). Closing and
exporting a month (7G) is optional and deferred (owner, 2026-11-15). Anything the owner defers is recorded here.

---

## UPCOMING PHASES (order locked; each stays a one-liner per the usual
## convention until it becomes active, at which point its full brief goes
## here the same way Phase 3's did before it was completed)

**Phase 8 — Notifications.** Lowest-effort new addition — SMS templates
already designed, this is mostly wiring them in. (Originally Phase 3;
renumbered to Phase 4 to make room for the Phase 3 Ops Inventory System
brief, per owner's explicit call 2026-09-06 — see COMPLETED PHASES for
that phase's outcome. Renumbered again to Phase 5 to make room for the
Phase 4 Network Monitoring brief, per owner's explicit call 2026-09-18,
to Phase 6 for the Phase 5 UI Consistency brief, per owner's explicit
call 2026-11-09, and to Phase 7 for the Phase 6 SmartOLT brief, per owner's
explicit call 2026-11-10, and to Phase 8 for the Phase 7 Finance spec,
per owner's explicit call 2026-11-15.)

**Phase 9 — Ticketing.** Close in shape to the existing site verification/
check-in flow.

**Phase 10 — Basic CRM.** Likely just views/notes on top of the existing
customers table — to be CONFIRMED, not assumed, once this phase starts.

**Phase 11 — Chat.** Deliberately deferred and flagged for reassessment.
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
