# PHASES.md — Live Status Tracker (JASIRI NET OPS)

Check this file before starting any work. If a request doesn't match the
current phase's declared scope, flag it — don't do it anyway.

Completed phases collapse to one summary line (name + completion date)
once confirmed done — full detail below is only kept for the active phase
and whatever's still upcoming.

---

## ACTIVE PHASE: Phase 4 — Network Monitoring (Site Status)

Phase 4.0 (router reconnaissance) is **COMPLETE** — its findings are
recorded below and they replace several assumptions in the original
monitoring spec. Phase 4.0b (infrastructure facts) and Phase 4.0c (the
site mapping table) are still open and still block implementation.

### Purpose

Give Ops a machine-measured answer to "is this site up, how many devices
are on it, what has its uptime been, and what did it earn" — sourced from
the MikroTik CCR2004, because the billing/RADIUS system is closed.

---

### 4.0 — Router reconnaissance: COMPLETE

Run against `JS-Core2-CCR2004` on 2026-09-18. **RouterOS 7.24.2**,
CCR2004-16G-2S+, ARM64, 4 cores, 4 GB RAM, 4–5% CPU load, 7-day uptime,
102 MiB of 128 MiB storage free.

#### Verdict on the four goals

| Goal | Status | Source |
|---|---|---|
| 1. Site online/offline | ✅ possible — **but only for ~13 of 21 sites via PPPoE**; the rest need a different signal | `/ppp active`, plus per-VLAN ping |
| 2. Device headcount per site | ✅ possible, cleanly | `/ip hotspot active`, grouped by `10.50.NN.x` |
| 3. Revenue | ✅ **possible, and far more cheaply than the spec assumed** | `/ip hotspot user` + profile name |
| 4. Uptime % | ✅ possible | derived from 1 and the heartbeat |

**Revenue is in.** That was the open question and the answer is yes.

#### What the network actually looks like

One SFP+ trunk (`sfp-sfpplus1`) carries everything, split by VLAN:

- **21 hotspot servers**, one per VLAN sub-interface, each with the CCR holding the gateway on its own `/24`:
  `5, 10, 15, 20, 25, 30, 35, 40, 45, 55, 60, 65, 70, 75, 77, 80, 85, 90, 105, 110, 115`
  → `hs-vNN` on `sfp-sfpplus1-vNN`, gateway `10.50.NN.1/24`, pool `hs-vNN`, all on profile `hsprof1`.
  (VLAN 5's server is named `PHASE3`, not `hs-v5` — the only naming exception.)
- **A 22nd hotspot**, `hotspot1` on `hotspot-bridge` (`192.168.180.1/22`) — separate, legacy-looking, zero active users in both snapshots. **Open question: is this retired?**
- **VLAN 50 is the PPPoE trunk**, not a hotspot. One PPPoE server instance, `service-name="FTTH-HS"`, on `sfp-sfpplus1-v50`.
- **71 active hotspot sessions** at 00:46, spread across 13 VLANs.
- **21 active PPPoE sessions** on `192.168.185.x`, authenticated by RADIUS at `192.168.116.1` over the `l2tp-out2` tunnel (FreeISPRadius).

#### Correction 1 — the spec's addressing premise was wrong, and that is good news

The spec said *"each site's XPON router is `192.168.100.1` on every site, same address reused per-VLAN."* **That is not this network.** Every VLAN already has a unique subnet (`10.50.5.0/24` … `10.50.115.0/24`) and the CCR itself holds `.1` on each.

Consequence: the review's warning that a ping fallback would break the CCR's routing table **does not apply here**. It was correct about the design the spec described; the spec mis-described the network. **Per-VLAN ping/netwatch is viable** and is now the recommended liveness signal for the sites that have no PPPoE session — see Correction 3.

#### Correction 2 — revenue needs no script in the customer login path

`/radius print` shows exactly one entry: `service=ppp`. **There is no RADIUS entry for hotspot.** Hotspot authenticates against the router's *local* user database — **206 local users** — which the billing system writes into the CCR from outside (the `freeisphotspotap` tunnel at `16.0.0.20/16`).

So everything needed for revenue is already sitting on the router in readable form:

**Prices are literally in the profile names.**

| Profile | Price (KES) |
|---|---|
| `Quick Surf10` | 10 |
| `Half day pass20` | 20 |
| `Full day pass30` | 30 |
| `24hr pass40` | 40 |
| `3day pass70` | 70 |
| `5day pass100` | 100 |
| `10day pass150` | 150 |
| `Monthly pass400` | 400 |
| `Monthly pass500` | 500 |
| `hp support users` | **0** — comped (`shared-users=2`) |
| `default` | **0** — confirm intent |

Usernames are `<phone>-<mac fragment>` (e.g. `254796130050-F:D2`) and each carries an `Exp: <datetime>` comment — the billing system's own expiry bookkeeping.

**This kills the riskiest change in the whole phase.** The original spec's "ideal trigger" was a hotspot `on-login` script — code executing inside the login path of every paying customer, on profiles a third-party system owns and can overwrite. **Delete that approach.** Instead:

> Poll `/ip hotspot user` read-only. A username that is new, or whose `Exp:` has moved forward, is a **sale**. Price comes from its profile name.

Three things improve at once:
1. **Zero code in the authentication path.** Nothing Phase 4 does can stall a customer login.
2. **Nothing for the billing vendor to overwrite.** No script on a profile they manage.
3. **It measures *sales*, not *usage*** — which is the number that reconciles against M-Pesa. The spec's unresolved "sales or usage?" question answers itself, in favour of the more useful one. Vouchers sold-and-never-used are now counted correctly instead of being a permanent invisible undercount.

*(Whether `on-login` is currently free is now moot. If anyone wants to know anyway: `/ip hotspot user profile get [find name="Quick Surf10"] on-login` — the recon output omitted the field entirely, which is ambiguous.)*

#### Correction 3 — "one PPPoE session per site" holds for only about 13 of 21 sites

The 21 PPPoE session names:

```
ACK                  Kamutini_Hotspot      Phase3_HOTSPOT       Stage
Benbro               Kwamlima              Policelin_Hotspot    Stima_Hotspot
CatholicRd_Hotspot   LowerSunton_Hotspot   Policelinestreet9    Sunton_Hotspot
GarageRd_Hotspot     Maji_Hotspot          Prisca
Garage_Hotspot       Maternity_Hotspot     Redsoil_Hotspot
Hunters_Hotspot      Ndambaki              Njeri_House
```

Thirteen end in `_Hotspot`/`HOTSPOT`. The other eight (`ACK`, `Benbro`, `Kwamlima`, `Ndambaki`, `Njeri_House`, `Policelinestreet9`, `Prisca`, `Stage`) are **not obviously hotspot-site uplinks** — they may be home PPPoE customers, or sites named after a building.

Meanwhile there are **21 hotspot VLANs**. The counts do not reconcile, and **nothing in the router config links a VLAN number to a PPPoE username.** That mapping exists only in your head.

Two hard consequences:

1. **Site status cannot be "PPPoE session state" for every site.** For VLANs with no PPPoE uplink, liveness must come from something else — per-VLAN ping of the site AP (now viable, see Correction 1), or simply "does this VLAN have hotspot activity".
2. **`monitored_sites` must be populated by hand.** This is Phase 4.0c below and it is now the main blocking task.

#### Correction 4 — keepalive tuning is fleet-wide, and should be skipped

```
0  service-name="FTTH-HS" interface=sfp-sfpplus1-v50 keepalive-timeout=10
   one-session-per-host=yes pppoe-over-vlan-range="" default-profile=php
```

**One** PPPoE server instance, `pppoe-over-vlan-range` empty. So `keepalive-timeout` is a single fleet-wide knob affecting all 21 sessions — **including real home internet customers.** There is no way to stage it on one site. The review flagged this as a risk; it is confirmed.

**Recommendation: do not tune it.** The ~5-second detection target in the original spec was never justified, and on this network it is incoherent:

- Hotspot user profiles run `keepalive-timeout=2m` and `status-autorefresh=1m`, so **headcount is inherently up to two minutes stale** no matter what.
- For a street hotspot site, the response to "down" is to phone someone or drive there. 60 seconds versus 6 seconds changes nothing operationally.
- The default 10s already gives ~20s detection on the PPPoE path.

Chasing 6s means touching session-liveness for paying home customers, for no operational gain. **Leave `keepalive-timeout=10`.** The 60-second heartbeat is the design, and it carries zero production risk.

This also demotes the event-driven `on-up`/`on-down` path (was 4.6) from core work to optional polish.

#### Correction 5 — the router's clock is one hour wrong. Fix this tonight.

```
time-zone-autodetect: yes
time-zone-name: Indian/Mauritius
gmt-offset: +04:00
```

The CCR thinks it is in **Mauritius (UTC+4)**. Nairobi is UTC+3. The router reported `00:46` while local time was `23:46` the previous day — off by exactly one hour, and across a date boundary.

NTP itself is healthy (`synchronized`, stratum 1, `system-offset: 0.873 ms`, drift 6.058 PPM) — UTC is correct. Only the display timezone is wrong, almost certainly because `time-zone-autodetect` geolocated the upstream/tunnel IP.

Every locally-formatted timestamp the router produces is therefore an hour ahead, and "today" rolls over at 23:00 local. Fix before any timestamp is trusted:

```
/system clock set time-zone-autodetect=no time-zone-name=Africa/Nairobi
```

Autodetect must go off in the same command or it will revert. This is read-safe and affects no traffic.

The review's recommendation to store both `router_ts` and `received_at` and compute from `received_at` stands regardless — this is exactly the class of bug it was insurance against, and it was live.

#### Correction 6 — eight VLANs had zero users, and that needs explaining before it gets coded

VLANs with active hotspot sessions at 00:46: `5, 10, 15, 25, 30, 35, 45, 55, 60, 65, 70, 75, 80`.

Zero active sessions: **`20, 40, 77, 85, 90, 105, 110, 115`** — eight of twenty-one — plus `hotspot1` on the bridge.

This is precisely the "site up, hotspot broken" failure the review called out as probably the highest-value alert in the system, and it is showing up in the very first snapshot. Before building the anomaly detector, **you need to say which of those eight are expected to be empty** (new, not yet live, genuinely quiet at 1am) versus actually broken. Otherwise the detector gets calibrated against a baseline that already contains faults.

#### Other facts worth recording

- **PPP profiles: all nine have `on-up=""` and `on-down=""`** — free to use if the event path is ever wanted. But profiles are assigned by FreeISPRadius (note the profile literally named `EXPIRED FREEISPRADIUS expired_pppoe_pool`), and the `Jasiri 5/10/15/20mbps` profiles are shared between site uplinks and home customers. Any `on-up` hook would fire for home customers too — so the script must stay dumb and POST everything, with Ops filtering by username against `monitored_sites`.
- **Storage is fine** — 102 MiB free of 128 MiB. But `write-sect-total` is already 2.13M sectors on ARM flash, so `keep-result=no` stays mandatory for wear, not for space.
- **`/radius print` exposed no secret** in non-detail form. Nothing sensitive was shared.
- **Untracked scripts already on the router:** `jasccr2004.rsc` (83.4 KiB), `mainhotspot.rsc` (5.7 KiB), `mesh.rsc` (modified 2026-09-18 00:35). Phase 4's script must not collide with names or schedulers these define — read them before adding anything.

---

### 4.0b — Infrastructure facts: STILL OPEN

- **Render plan.** Free tier sleeps after ~15 min idle and cold-starts in 30–60s. A 60s heartbeat keeps it permanently awake — a genuine side benefit — but any gap causes a cold start, which causes more gaps.
- **Supabase plan and current DB size.** Drives the retention design in 4.1.
- **Migration approach.** Alembic, or hand-applied SQL? Claude Code can answer this itself from the repo.

### 4.0c — The site mapping table: STILL OPEN, and now the critical path

Per Correction 3, this cannot be derived from the router. Produce one row per monitored site:

| VLAN | Gateway | Ops `location_id` | PPPoE username (if any) | Site AP IP (for ping, if no PPPoE) | Expected quiet hours |
|---|---|---|---|---|---|
| 35 | 10.50.35.1 | ? | `Sunton_Hotspot`? | | |
| … | | | | | |

Also needed:
- Which of the eight empty VLANs are live (Correction 6).
- Whether `hotspot1` / `hotspot-bridge` is in scope or retired.
- Which of the eight non-`_Hotspot` PPPoE names are sites versus home customers.
- Whether every VLAN corresponds to a row that already exists in `locations`, or whether some sites must be created first.

**Nothing in 4.1 onward can be verified without this table.** It is 21 rows and only you can write it.

---

### Isolation invariant (non-negotiable)

Per the domain-isolation principle in RULES.md:

- The monitoring domain **reads** `locations` by `location_id` and nothing else.
- `locations`, `batteries`, `movements`, `inventory`, `users`, `roles` gain **zero** new columns and **zero** awareness of monitoring.
- No monitoring table is joined into an existing domain's queries.
- **Do not add `vlan_id` to `locations`.** A location may be unmonitored (home base, a store); a VLAN may exist before anyone maps it. Separate lifecycles, separate tables.

**Proof obligation:** dropping every monitoring table must leave Phases 0–3 passing unchanged.

---

### Naming decision

The codebase already uses three words for one thing — `locations` (table/routes), "Sites" (nav), `SiteOnlineAnswer` (schemas). **Do not add a fourth.** New domain is `monitoring`, routes under `/monitoring/…`, DB keeps `locations`, UI keeps saying "Sites". No `sites` table, no `/sites` route.

---

### Where it lands in the UI

Nav already has **Sites** → the `locations` directory plus the verification/confirm flow. Same noun, so:

- **Sites** becomes two tabs: **Status** (new, default) and **Directory** (existing, unchanged).
- **Non-goal:** a new top-level nav entry. No "Network", no "Monitoring".

---

### The collision that needs a policy: two answers to one question

Ops already asks a human whether a site is online:

```
GET  /locations/verification          "List Site Verification"
POST /locations/{id}/confirm          SiteVerificationAnswer { is_online }
GET  /locations/unconfirmed-count     (drives a nav badge)
POST /movements/{id}/confirm-online   SiteOnlineAnswer { is_online }
```

After this phase the router answers the same question in ~60 seconds.

**Decision:**

1. Monitoring becomes the system of record for *"is this site's uplink up"*.
2. The human confirmation is **not** removed and **not** auto-closed — "the site is online" and "the battery I just installed is what is powering it" are different claims, and only a person on site can make the second.
3. `confirm-online` becomes a **prefilled one-tap**: *"Monitoring saw Sunton come back online at 14:32, 12m after you marked arrived"* — accept or contradict.
4. **Both answers stored.** A contradiction is a finding: uplink up while the tech says otherwise usually means something else at the site is dead.
5. Re-check whether `unconfirmed-count` still earns its nav badge once most confirmations are pre-answered. The re-check is in scope; changing it is not.

---

### Sub-phases, ordered by blast radius

#### 4.1 — Schema + migration · *Ops only, zero router contact*

| Table | Grain | Purpose |
|---|---|---|
| `monitored_sites` | one row per VLAN | `location_id` FK, `vlan_id` UNIQUE, `gateway_cidr`, `pppoe_username` NULLABLE UNIQUE, `ap_ip` NULLABLE, `liveness_source` enum(`pppoe`,`ping`,`activity`), `quiet_hours`, `active` |
| `site_status_log` | **transitions only** | `location_id`, `state` (online/offline/unknown/flapping), `router_ts`, `received_at`, `source` |
| `ingest_snapshots` | one row per **fleet** snapshot | `seq`, `router_ts`, `received_at`, `sites_reporting` — makes *unknown* time computable |
| `site_session_counts` | per site, downsampled | `location_id`, `sessions`, `received_at` |
| `hotspot_packages` | one row per profile | `profile_name` UNIQUE, `price_kes`, `is_comped` — seeded from the table in Correction 2 |
| `revenue_events` | one row per sale | `location_id`, `hotspot_username`, `profile_name`, `price_kes`, `expiry_seen`, `first_seen_at` |

`pppoe_username` is **nullable** — Correction 3 means not every site has one.

**Retention is mandatory in v1.** One row per site per minute is 30k rows/day → ~1 GB/year, past the Supabase free tier inside six months. By design:

- `site_status_log` — transitions only, keep forever.
- `ingest_snapshots` — one row for the whole fleet per snapshot (1,440/day), enough to prove "we were watching".
- `site_session_counts` — written **every 5 minutes**, since hotspot data is already up to 2 minutes stale (Correction 4); rolled up hourly after 14 days.

Two timestamps everywhere: `router_ts` as reported, `received_at` as Ops saw it. **Compute uptime from `received_at`.** Store UTC; aggregate on Africa/Nairobi boundaries. Correction 5 is exactly why.

#### 4.2 — Ingest endpoint · *Ops only, zero router contact*

`POST /monitoring/ingest`.

**Documented auth exception.** Every existing endpoint uses `HTTPBearer` (user JWT); a RouterOS script cannot hold one. This endpoint uses a static shared-secret token in a custom header on its own middleware, bypassing the user-auth dependency. **Write this into ARCHITECTURE.md as intentional**, or a future session reads it as a hole.

- Long random token in an environment variable, never in the repo.
- Accepts a single reading **or a batch**; idempotent against retries.
- Unknown `vlan_id` or unknown PPPoE username → **quarantine table, return 200.** Never drop silently, never 4xx — the router has no retry and nobody reads its logs.
- Record sequence numbers so gaps are detectable.

#### 4.3 — Read-only heartbeat on the router · *fleet-wide, safe at any hour*

One `/system scheduler` entry, 60s, running a script that reads `/ppp active`, `/ip hotspot active` and `/ip hotspot user`, and POSTs **one** JSON payload for the whole fleet.

Changes nothing. Cannot disconnect anyone. Rollback is disabling one scheduler entry.

Mandatory on every `/tool fetch`:

```
output=none keep-result=no check-certificate=yes
```

`keep-result` defaults to `yes` and would write a file per call — flash wear on ARM (Correction: 2.13M sectors already written). `check-certificate` defaults to `no`, which would make the shared secret interceptable.

Wrap every fetch so it can never block or throw:

```
:onerror e in={ /tool/fetch ... } do={}
```

Check `jasccr2004.rsc`, `mainhotspot.rsc` and `mesh.rsc` for existing scheduler/script name collisions before adding anything.

**After one day of 4.3 alone you have all four goals at 60-second resolution**, with zero production risk taken.

#### 4.4 — Status + revenue API, permission wiring · *Ops only*

Permissions here are `(section, action, allowed)` triples — `PermissionEntry { section, action, allowed }` — **not** flat flags. So `can_view_revenue` becomes:

```
section = "sites"        (confirm against the existing Sites view's string)
action  = "view_revenue"
```

Revenue must be **absent from the payload**, not nulled, for users without it; the client renders the locked placeholder from the permission flag alone. Gating covers the Status tab, per-site detail, exports **and alert message bodies** — an SMS with a revenue figure walks straight past the permission model.

#### 4.5 — Dashboard UI (Status tab) · *Ops only*

Build to the canvas design already produced. Load-bearing parts:

- Hero is a **sentence** ("2 sites down"), largest element, meaning in the words not the colour.
- **Exception-first** — problem cards above fleet totals; both collapse to nothing on a calm day.
- **Acknowledgement** moves a known-bad site into a muted strip. Without it, a site waiting three days for a battery holds the page red permanently and trains everyone to stop seeing red.
- Status = colour **+ shape + text**, always. Screenshots into WhatsApp are how this travels.
- Problem cards carry **sessions-at-drop** — the number that decides whether anyone drives out tonight.
- Revenue-hidden renders as a lock and the word "Hidden", never an absent column.
- **SSE** for live updates (FastAPI async generator, `text/event-stream`, no new dependency). The page must show its own connection state and dim the dots when the stream dies.
- Mobile: hero + problem cards + one line per site.

#### 4.6 — Per-VLAN liveness for sites without PPPoE · *reads only, small config addition*

Required by Correction 3. Because every VLAN has a unique subnet (Correction 1), this is straightforward:

- Where a site AP has a stable IP in `10.50.NN.0/24`, add a netwatch probe per site. `src-address` is unambiguous here — the overlapping-subnet problem in the original spec does not exist on this network.
- Where it does not, fall back to `activity`: a VLAN with recent hotspot sessions is up; a VLAN with none is *unknown*, never *down* (Correction 6 is why — eight VLANs are already empty and we do not yet know if that is a fault).

**Netwatch is available.** In RouterOS 7 the old `advanced-tools` package was merged into the single bundled `routeros` package — which is why the recon lists only `routeros` 7.24.2 while hotspot and PPP (also formerly separate packages) both plainly work. One command confirms if anyone wants certainty: `/tool netwatch print`.

#### 4.7 — Revenue via hotspot-user polling · *reads only*

Per Correction 2. Seed `hotspot_packages` from the price table. In the heartbeat, read `/ip hotspot user` and detect:

- a username not seen before → **a sale**, priced from its profile;
- an existing username whose `Exp:` moved forward → **a renewal**, priced the same way.

Attribute to a site by the user's address subnet where the user is currently active; where it is not, attribute to the site that last saw it, and mark the attribution as inferred.

`hp support users` and `default` price at 0. Confirm `default` is genuinely comped and not a misconfiguration.

Because this is a *sales* measure, it should reconcile against M-Pesa directly. Log the weekly delta; a persistent gap is a bug, not drift.

#### 4.8 — Alerting + external watchdog

- Debounce **alerting**, never logging. Log every transition; alert only on down persisting > N minutes, rate-limited per site per hour.
- **Flapping** is a distinct state — a site bouncing 40 times a night reads green on any single poll.
- **Per-site quiet hours** from `monitored_sites`. Solar/battery sites drop predictably overnight; without this the dashboard is red every morning.
- **Router-restart marker** via a startup-triggered scheduler script, so a reboot is not logged as 21 genuine simultaneous outages.
- **Sessions-dropped-to-zero anomaly** — calibrate only after Correction 6 is resolved.
- External uptime monitor on an Ops health endpoint, alerting distinctly from a site-down alert.

#### 4.9 — Reconcile monitor against human `confirm-online`

Implement the prefill above, store both answers, surface contradictions.

#### 4.10 — OPTIONAL: event-driven fast path · *touches the router*

Deferred by Correction 4 — the operational case for 6s over 60s is weak, and the heartbeat is free. If it is ever wanted:

- Set `on-up`/`on-down` on the PPP profiles the site sessions use. All nine are currently empty.
- Profiles are RADIUS-assigned and shared with home customers, so the script must POST everything and let Ops filter by `monitored_sites`.
- Verified RouterOS facts: variables are `user`, `local-address`, `remote-address`, `caller-id`, `called-id`, `interface`; `$interface` returns an internal ID (`*f00001`), not a name — use `[/interface get $interface name]`; dashed names **must be quoted** (`$"remote-address"`) or they parse as subtraction and silently yield nothing.
- **There is no `/interface` up/down script hook in RouterOS.** The spec's "VLAN interface state" backup signal does not exist; 4.3 and 4.6 replace it.

**Do not tune `keepalive-timeout`.** See Correction 4.

---

### Out of scope

- Tuning `keepalive-timeout` (Correction 4).
- Any hotspot `on-login` / `on-logout` script (Correction 2 removes the need).
- Any change to `locations`, `batteries`, `movements`, `inventory`, `users`, `roles` schemas.
- A new top-level nav entry.
- Per-site subnet re-addressing or VRFs — unnecessary, the network is already uniquely subnetted.
- Customer-facing status pages.
- New-site onboarding automation (follow-on phase once the pattern is proven).

---

### Rollback

**Not** a config re-import — that drops every session and risks a state matching neither old nor new. Keep the export for "the router is bricked" only.

Phase 4 as scoped touches the router in only three places, each reversed by one command:

| Change | Rollback |
|---|---|
| Timezone fix (Correction 5) | `/system clock set time-zone-autodetect=yes` |
| 4.3 heartbeat scheduler | `/system scheduler disable [find name="ops-heartbeat"]` |
| 4.6 netwatch probes | `/tool netwatch disable [find comment~"ops-monitor"]` |
| 4.10 (if ever done) | clear the two PPP profile fields |

Before any of it: `/export file=` **and** `/system backup save`, both pulled off the device and verified openable. Note there are already two `freeispradius_backup_*.backup` files on the router — do not confuse them with yours.

---

### Agent boundaries

Extending DELEGATION.md's rule that an agent shows each command before running it:

**An agent should not run 4.10 against the live router at all**, and should not touch `keepalive-timeout` under any circumstances.

4.3 and 4.6 are read-only and low-risk; an agent may draft the scripts, but a human pastes them. Agents are well suited to 4.1, 4.2, 4.4, 4.5, 4.7's Ops-side logic, and the netwatch rollout repetition in 4.6.

---

### Exit criteria

1. 4.0c mapping table complete — all 21 VLANs mapped, with the eight empty VLANs and the eight ambiguous PPPoE names classified.
2. Router timezone reads `Africa/Nairobi` with autodetect off.
3. All mapped sites reporting via heartbeat for 7 consecutive days with no gap longer than the staleness window.
4. Dropping every monitoring table leaves Phases 0–3 green.
5. A user without `sites/view_revenue` receives responses containing **no revenue field** — verified by reading the raw response, not the UI.
6. Uptime for a deliberately induced 10-minute outage on one test site matches wall-clock within 60 seconds.
7. An Ops restart mid-outage produces *unknown* time, not phantom uptime — verified by killing Ops for 5 minutes during a real outage.
8. One week's polled revenue reconciles against M-Pesa within a stated tolerance, with the delta logged.
9. Every `/tool fetch` carries `output=none keep-result=no check-certificate=yes`, and `/file print` shows no growth after 7 days.
10. `keepalive-timeout` is still `10`.

---

## UPCOMING PHASES (order locked; each stays a one-liner per the usual
## convention until it becomes active, at which point its full brief goes
## here the same way Phase 3's did before it was completed)

**Phase 5 — Notifications.** Lowest-effort new addition — SMS templates
already designed, this is mostly wiring them in. (Originally Phase 3;
renumbered to Phase 4 to make room for the Phase 3 Ops Inventory System
brief, per owner's explicit call 2026-09-06 — see COMPLETED PHASES for
that phase's outcome. Renumbered again to Phase 5 to make room for the
Phase 4 Network Monitoring brief, per owner's explicit call 2026-09-18.)

**Phase 6 — Ticketing.** Close in shape to the existing site verification/
check-in flow.

**Phase 7 — Basic CRM.** Likely just views/notes on top of the existing
customers table — to be CONFIRMED, not assumed, once this phase starts.

**Phase 8 — Chat.** Deliberately deferred and flagged for reassessment.
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
