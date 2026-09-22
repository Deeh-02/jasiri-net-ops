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

Every locally-formatted timestamp the router produces is therefore an hour ahead, and "today" rolls over at 23:00 local.

**DECISION 2026-09-21 (owner): do NOT fix this on the router. Correct it on
the Ops side instead.** The router keeps `Indian/Mauritius` and Phase 4 takes
zero router-config risk on the clock. Consequences, all of which must hold:

- **The router clock stays known-wrong.** Anything else reading this router —
  a person on Winbox, the billing vendor, a future phase — still sees +1h.
  This is now a permanent documented quirk, not a bug being fixed.
- **`received_at` becomes load-bearing, not merely insurance.** All uptime,
  all daily boundaries, all revenue-day attribution compute from
  `received_at`. This was already the design; it is now the *only* correct
  path rather than the safer of two.
- **The heartbeat must send the router's own offset with the payload.** Have
  the 4.3 script include `[/system clock get gmt-offset]` alongside
  `router_ts`, so the payload is self-describing and the correction is data
  rather than a constant hardcoded in Ops. If someone later fixes the router
  clock, a hardcoded `-1h` would silently corrupt every reading; a
  transmitted offset just starts arriving as `+03:00`.
- **Store `router_ts` raw, exactly as reported.** Normalize to UTC on ingest
  using the transmitted offset; never mutate the stored raw value.
- **Do not blanket-correct every router-sourced timestamp.** The `Exp:`
  comments on hotspot users (Correction 2) are written by the *billing
  system* over the `freeisphotspotap` tunnel, not by the router's clock —
  their timezone is that system's, which is **unknown and must be
  established separately before 4.7 prices a renewal.** Applying the −1h
  router correction to them would be wrong. Open question.

Note `db/connection.py` already pins every session to UTC and defines
`EAT = timezone(timedelta(hours=3))` as a fixed offset (EAT has no DST), so
the Ops side already has the right primitives for this.

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

### 4.0b — Infrastructure facts: PARTIALLY ANSWERED

**Answered 2026-09-21:**

- **Migration approach: hand-applied numbered SQL.** No Alembic, no framework
  — `migrations/` holds `0001_`…`0005_`, run by hand. Phase 4's migration is
  therefore `0006_monitoring_core_tables.sql`.
- **M-Pesa data access: MANUAL.** It cannot be pulled programmatically.
  Per 4.7 this means the automatic Ops-side daily revenue log still runs
  daily and costs nothing, but the *comparison* against M-Pesa is a human
  reading statements, so its cadence is Ops' choice — **cadence still to be
  picked** (weekly is the doc's suggested default).

- **Render plan: FREE.** Sleeps after ~15 min idle, cold-starts in 30–60s. The 60s heartbeat keeps it permanently awake, which is the right trade since the router has no retry and every cold start would lose readings. **Consequence:** Render's free tier bills 750 instance-hours/month *across the account*, and a service awake 24/7 is ~730 of them. There is no room for a second free Render service alongside this one.
- **Supabase plan: FREE** — 500 MB cap. This makes retention binding, not hygiene. Measured: `ingest_snapshots` at 1,440 rows/day is 58–100 MB/yr and would eat the whole tier in ~3 years on its own, so it is downsampled raw-30-days-then-hourly via a `granularity` column, the same mechanism as `site_session_counts`. See 0006's header for the full table.

- **Current DB size: 11 MB** (measured 2026-09-21). The application tables are
  tiny — under 1 MB combined, the rest is Postgres overhead. So ~489 MB of the
  500 MB cap is free. With the retention design above, monitoring settles at
  roughly 30–40 MB/year steady-state, which is years of runway. **4.0b is now
  fully answered.**

### 4.0c — The site mapping table: 14 of 21 MAPPED, still the critical path

Owner supplied VLAN→name for 15 VLANs on 2026-09-21. Reconciled here against
the `locations` table (read live 2026-09-21) and the 21 PPPoE session names
from 4.0. `location_id` was **derived, not supplied** — every ✅ row below is
a name match against a real `locations` row and should be spot-checked once.

| VLAN | Gateway | `location_id` | Location name | PPPoE username | Liveness | State |
|---|---|---|---|---|---|---|
| 5 | 10.50.5.1 | 19 | Njeri House | `Njeri_House` | pppoe | ✅ |
| 10 | 10.50.10.1 | 21 | Ndambaki | `Ndambaki` | pppoe | ✅ |
| 15 | 10.50.15.1 | 20 | Kwa Mlima | `Kwamlima` | pppoe | ✅ |
| 20 | 10.50.20.1 | — | "Street 10" | none found | ? | ⚠ no `locations` row; 0 sessions |
| 25 | 10.50.25.1 | 18 | ACK | `ACK` | pppoe | ✅ |
| 30 | 10.50.30.1 | 8 | Catholic Road | `CatholicRd_Hotspot` | pppoe | ✅ |
| 35 | 10.50.35.1 | **6 or 7** | Phase 3 | `Phase3_HOTSPOT` | pppoe | ⚠ duplicate rows |
| 40 | 10.50.40.1 | 12 | Redsoil | `Redsoil_Hotspot` | pppoe | ⚠ 0 sessions |
| 45 | 10.50.45.1 | 17 | Sunton | `Sunton_Hotspot` | pppoe | ✅ |
| 55 | 10.50.55.1 | — | "Monitoring" | none found | ? | ⚠ no `locations` row |
| 60 | 10.50.60.1 | 3 | Maji Mazuri | `Maji_Hotspot` | pppoe | ✅ |
| 65 | 10.50.65.1 | 22 | Lower Sunton | `LowerSunton_Hotspot` | pppoe | ✅ |
| 70 | 10.50.70.1 | 5 | Policeline | `Policelin_Hotspot` | pppoe | ✅ |
| 75 | 10.50.75.1 | ? | — | ? | ? | ✗ unmapped |
| 77 | 10.50.77.1 | — | "Cyber" | none found | ? | ⚠ no `locations` row; 0 sessions |
| 80 | 10.50.80.1 | ? | — | ? | ? | ✗ unmapped |
| 85 | 10.50.85.1 | ? | — | ? | ? | ✗ unmapped; 0 sessions |
| 90 | 10.50.90.1 | ? | — | ? | ? | ✗ unmapped; 0 sessions |
| 105 | 10.50.105.1 | ? | — | ? | ? | ✗ unmapped; 0 sessions |
| 110 | 10.50.110.1 | ? | — | ? | ? | ✗ unmapped; 0 sessions |
| 115 | 10.50.115.1 | ? | — | ? | ? | ✗ unmapped; 0 sessions |
| **99** | ? | — | "Street 9" | `Policelinestreet9` | pppoe | ⚠ **not among the 21 hotspot VLANs in 4.0** |

#### Correction 3 is now almost fully resolved

The eight ambiguous non-`_Hotspot` PPPoE names, checked against `locations`:

| PPPoE name | `locations` row | Verdict |
|---|---|---|
| `ACK` | 18 ACK | **site** |
| `Benbro` | 1 Benbro | **site** (VLAN not yet known) |
| `Kwamlima` | 20 Kwa Mlima | **site** |
| `Ndambaki` | 21 Ndambaki | **site** |
| `Njeri_House` | 19 Njeri House | **site** |
| `Policelinestreet9` | none | **site** (VLAN 99), needs a `locations` row |
| `Prisca` | none | **likely home customer — confirm** |
| `Stage` | none | **likely home customer — confirm** |

#### Sites in `locations` with no VLAN yet

Eight active, non-home-base rows are unassigned — and only **seven** VLANs
remain (75, 80, 85, 90, 105, 110, 115). So at least one of these has no
hotspot VLAN at all, or sits on the legacy `hotspot-bridge`:

`1 Benbro`, `9 Kwa Mafuta`, `10 Kamutini`, `11 Garage`, `13 Stima`,
`14 Maternity`, `15 Garage Escarpments`, `16 Hunters`

Six of the still-unmatched PPPoE names end in `_Hotspot` (`Garage_Hotspot`,
`GarageRd_Hotspot`, `Hunters_Hotspot`, `Kamutini_Hotspot`,
`Maternity_Hotspot`, `Stima_Hotspot`), which lines up with six of those
eight. `Kwa Mafuta` matches no PPPoE name at all.

#### Discrepancies that need an owner answer

1. **VLAN 99 is not in the 21.** 4.0's hotspot-server sweep found no VLAN 99.
   Either it is a PPPoE-only site with no hotspot server, or 4.0's list was
   incomplete. One command settles it: `/interface vlan print`.
2. ~~**VLAN 5's hotspot server is named `PHASE3`**~~ **RESOLVED 2026-09-22.**
   Confirmed a stale name, not a mapping error, and renamed to `hs-v5` on
   the router — VLAN 5 is a real, active, registered site
   (`monitored_sites.id=1`). This stopped being out-of-scope the moment
   4.7's real design started depending on the `hs-v<N>` convention holding
   with no exceptions — see 4.7 below.
3. **`Phase 3` exists twice in `locations`** — id 6 (`is_active=false`) and
   id 7 (`is_active=true`). VLAN 35 must point at one. Presumably 7.
4. **Four named VLANs have no `locations` row**: Street 10 (20),
   Monitoring (55), Cyber (77), Street 9 (99). Each needs a row created, or
   to be declared out of scope. **Is "Monitoring" (VLAN 55) even a customer
   site**, or is it infrastructure? It had active sessions, so something is
   using it.
5. **Redsoil (VLAN 40) is the strongest "site up, hotspot broken" candidate**
   — it has a live PPPoE uplink *and* zero hotspot sessions. Per Correction 6
   this needs classifying before the anomaly detector is calibrated.
6. Of Correction 6's eight empty VLANs, **five are also unnamed**
   (85, 90, 105, 110, 115) — consistent with "provisioned but not yet live".
   Confirm that reading.

#### Still needed before 4.1 can be verified end-to-end

- The seven unmapped VLANs (75, 80, 85, 90, 105, 110, 115).
- `ap_ip` for any site whose liveness must be `ping` rather than `pppoe`.
- `quiet_hours` per site — nothing is known yet for any row.
- Whether `hotspot1` / `hotspot-bridge` is in scope or retired.

**4.1's schema can be written now** — the mapping is `monitored_sites` *data*,
not structure, and the gaps above are rows left unseeded rather than columns
left undesigned.

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

`hp support users` and `default` price at 0 — confirmed genuinely comped, not a misconfiguration.

**Attribution shipped as three tiers, not the one originally planned, after real production data (2026-09-22) showed the plan below left 53 sales (KES 840) that day permanently unplaced:**

1. **The account's own hotspot server VLAN** — durable, true at 3am with nobody connected. Hotspot servers here are one per VLAN, named `hs-v<N>` with no exceptions (see the now-resolved discrepancy 2 above); the VLAN is parsed off that name directly, not resolved via `/interface` (confirmed empirically that these servers aren't bound to an interface literally named `vlan<id>`).
2. **For accounts with no server of their own** (bound to `all` — a mix of the comped support profiles and some real paid customers, e.g. `Monthly pass400`, `10day pass150`): **the account's DHCP lease VLAN**, matched by the MAC already in its `Exp: ... | MAC: ...` comment. Leases here last up to 24h and outlive the hotspot session itself, and a device needs an IP before it can reach the payment page at all — so the lease used to buy the pass is almost always still there when this runs. DHCP servers happen to follow `dhcp-v<N>` too, but the lease's own address is read directly, same reasoning as tier 1.
3. **The user's address subnet where currently active** (the originally-planned signal) — kept as a fallback for the rare case neither of the above resolves.
4. **The buyer's last known site**, marked `inferred` — unchanged from the original plan, last resort only.

Attribution is never a dead end: what tier 1/2 resolved even when it *isn't* a registered site yet is still recorded (`revenue_events.origin_vlan_id`, migration 0010) — "we knew, it just isn't a site in Manage Sites" is a distinguishable, fixable state, not silently discarded.

**A sale is booked once, on `(username, expiry)`, and never re-examined by the booking logic itself — so a genuine backfill runs on every heartbeat, not just users runs, checking anything still unplaced against whatever's been learned since:** an active-list sighting reaches back 30 minutes (a moment, not a durable fact), the account's own tier-1/2 VLAN reaches back 24h (a standing fact, matched to the DHCP lease life). Known and accepted: an `all`-bound account that bought at one site and has since moved is placed where it is *now*, not where it paid — judged better than not placed at all.

**Two gaps remain, deliberately not chased further as of 2026-09-22:** VLANs 99/105/115 have router-side hotspot/DHCP activity but no `monitored_sites` row yet (Ops-owned, pending); and an account that expires and is deleted from the router before any of the above ever resolves it has nothing left to recover from — no MAC, no lease, gone for good. Both are historical-residue problems, not ongoing leaks — same-day placement rate on new sales was verified at 100% (17/17) in production before the last of the tiers above even shipped.

Because this is a *sales* measure, it should reconcile against M-Pesa. Split this into two pieces so the system side costs nothing regardless of how the M-Pesa side turns out:

- **Automatic, always:** an Ops-side scheduled job logs that day's `revenue_events` total per site every day, no human involved. This alone costs nothing and should just always run.
- **Comparison against M-Pesa: MANUAL** (settled by 4.0b, 2026-09-21 — M-Pesa data cannot be pulled programmatically). So do **not** force this to daily; Ops picks the cadence (weekly is the working default, **still to be confirmed**) and the system side does not change either way. Ops-side job: present the stored daily totals for the chosen period so a human comparing a statement has one screen to read, rather than reconstructing days by hand. Either way, a single day's gap alone isn't a bug — settlement timing can shift a sale across the midnight boundary — only a gap that repeats across the compared period is.

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

Phase 4 as scoped touches the router in only two places, each reversed by one
command (the timezone fix is no longer among them — see Correction 5's
2026-09-21 decision):

| Change | Rollback |
|---|---|
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
2. Router clock is left untouched (still `Indian/Mauritius`), and Ops
   normalizes correctly: a reading taken at a known wall-clock instant
   stores a `router_ts` an hour ahead, a `received_at` matching wall clock,
   and a normalized UTC value derived from the *transmitted* `gmt-offset`
   rather than a hardcoded constant — verified by temporarily faking the
   offset in a test payload and seeing the normalized value follow it.
3. All mapped sites reporting via heartbeat for 7 consecutive days with no gap longer than the staleness window.
4. Dropping every monitoring table leaves Phases 0–3 green.
5. A user without `sites/view_revenue` receives responses containing **no revenue field** — verified by reading the raw response, not the UI.
6. Uptime for a deliberately induced 10-minute outage on one test site matches wall-clock within 60 seconds.
7. An Ops restart mid-outage produces *unknown* time, not phantom uptime — verified by killing Ops for 5 minutes during a real outage.
8. Router-side daily revenue totals logged automatically for 7 consecutive days, and compared against M-Pesa at whatever cadence 4.0b settles on (daily if automatable, otherwise Ops' chosen manual cadence); polled revenue matched M-Pesa within a stated tolerance over that comparison, with the delta logged each time and no unexplained repeat gap on any one site.
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
