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
| **7G** Month-end | Closing a month, PDF/CSV exports, M-Pesa statement check | 7D trusted for one full month |

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
the real database (read only; nothing saved). **Not yet built:** DeepSeek
suggestions and reading the user's note (next), "always skip this payee"
(needs a small schema change: a rule kind `skip`).

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
its rows; Assets shows book value and depreciation from Inventory; a month
can be closed and exported. Anything the owner defers is recorded here.

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
