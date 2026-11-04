# CCR2116 network monitor — install, check, roll back

For the owner, at the router. Two read-only scripts on the CCR2116 report to
Ops over HTTPS: `ops-live` every 15 s (every internet link, its gateway, CPU, PPPoE count) and
`ops-sessions` every 60 s (the PPPoE list). They change nothing on the
router. There is no lab router, so every step starts in **dry run** (the
script prints its message and sends nothing) and the whole thing comes off
with one file.

**Rollback, any time:** paste the three lines of
`router/uninstall-ops-monitor.rsc` into the router terminal. That removes both
schedulers, both scripts and their variables, and nothing else.

---

## 1. Ops side first

1. **Run the migrations** on Supabase, in order (they create the network tables, with RLS on;
   both are safe to run twice):
   `psql "$DATABASE_URL" -f migrations/0022_network_monitor.sql`
   `psql "$DATABASE_URL" -f migrations/0023_network_uplinks.sql`
2. **Make a token** and keep it somewhere safe:
   `python tools/render_router_script.py --new-token`
3. **On Render**, add the environment variable `NETWORK_INGEST_TOKEN_CCR2116` with that token,
   and deploy the branch with the Network page.
4. **Technicians:** Roles → edit the role → Sites → turn on **Network (CCR2116)**. Admins
   always see it.

## 2. Check the router (read-only commands)

```
/system resource print
/interface print where name~"sfp"
/ip route print detail where dst-address=0.0.0.0/0
/interface vlan print
/ppp active print count-only
/system script print where name~"ops-"
/system scheduler print where name~"ops-"
```

- **RouterOS version:** the scripts are written for RouterOS 7 and use what is proven
  on the CCR2004 (7.24.2). If the 2116 is on RouterOS 6, stop and tell Ops first.
- **Links are found, not configured.** Each enabled default route in the main table is one
  internet link (Airtel, Veenet, any future one). The script names it from the route's
  **comment**, reads its gateway and distance, finds the interface it leaves through, and, if
  that is a VLAN, measures the physical port the VLAN rides on. Give each default route a clear
  comment ("airtel Primary"): that is what the page shows.
- **Names:** nothing called `ops-live` or `ops-sessions` should exist yet.

### HTTPS: making the router trust Ops's certificate

The scripts use `check-certificate=yes`, so the token can't be read in transit.
RouterOS ships with no trusted CAs, so check first:

```
/certificate print where trusted=yes
```

If that's empty, import a CA bundle **once**. This one download uses
`check-certificate=no`, which is insecure, but it's a one-time public file and
not the token. It's the same step already done on the CCR2004:

```
/tool fetch url="https://curl.se/ca/cacert.pem" mode=https check-certificate=no
/certificate import file-name=cacert.pem passphrase=""
```

(Newer RouterOS 7 builds may offer built-in trust anchors under `/certificate
settings`. If yours does, that works too; check on your version.) Then
confirm TLS to Ops works:

```
/tool fetch url="https://YOUR-APP.onrender.com/network/ingest" http-method=post http-header-field="Content-Type: application/json" http-data="{}" output=user check-certificate=yes
```

An HTTP **401** here is success: TLS was verified and Ops answered. On 7.24.2 it can
show as "ERROR parsing http: 401 should contain www-authenticate header", which is
still fine. "certificate verification failed" means the CA step isn't done.

## 3. Render the scripts (dry run)

On the computer, from the repo, with the same token as on Render:

```
export NETWORK_INGEST_URL="https://YOUR-APP.onrender.com"
export NETWORK_INGEST_TOKEN_CCR2116="...the token..."
python tools/render_router_script.py            # add --internet-target 1.1.1.1 if wanted
```

This writes `router-rendered/ops-live.rsc` and `router-rendered/ops-sessions.rsc`.
That folder is gitignored because it holds the token. Delete it when you're done.

## 4. Install and dry run

In **Winbox/WebFig**: System → Scripts → Add New. Don't use `/system script add
source=...` in the terminal, which mangles the quotes.

| Script | Name | Policy |
|---|---|---|
| router-rendered/ops-live.rsc | `ops-live` | `read,write,test` |
| router-rendered/ops-sessions.rsc | `ops-sessions` | `read,write,test` |

**Why these policies.** `read` is for reading everything. `test` is for the 3-packet
gateway check (`/ping`; `/tool flood-ping` is blocked by the 2116's device-mode). `write` is there because `/tool fetch` refused to
run without it on the CCR2004, even with `output=none`; the scripts still contain no
write commands (enforced by `tests/test_router_scripts.py`). Try removing `write`; if
fetch still works on the 2116, leave it off. Never grant `sensitive`, `ssh`,
`password` or `policy`.

Run each once by hand:

```
/system script run ops-live
/system script run ops-sessions
```

**Dry-run checklist.** Copy the printed output to Ops (or paste it here) before going on.

- `ops-live` prints one JSON line:
  - `"uptime_s"` is a plain number (e.g. `123456`). If the script stops with "uptime is
    not a number", this RouterOS build needs a change: tell Ops.
  - `"rx_bps"`/`"tx_bps"` are numbers that look like the traffic graph.
  - `"links"`: one entry per default route. For each, check:
    - `"interface"` is the **physical port** (e.g. `sfp-sfpplus1`), not the VLAN's name.
    - `"label"` is the route's comment (e.g. `airtel Primary`).
    - `"active"` is `true` for exactly the route carrying traffic now, `false` for a standby.
    - `"distance"` is the route's distance.
    - `"rx_bps"`/`"tx_bps"` are numbers; a standby link reads about 0.
    - `"gateway"`: `"address"` is that link's next hop, `"alive":true`, and `"rtt_ms_avg"` is a
      number. If it's `null` while alive is true, the RTT format needs a tweak; tell Ops.
    - A link missing from the output means the script couldn't tell which interface its route
      leaves through. Paste `/ip route print detail where dst-address=0.0.0.0/0` here.
  - `"system"`: `cpu_pct`, `routeros`, and ideally `temp_c` are filled in.
  - `"sfp"` (inside each link): values if the SFP reports them, `null` otherwise. Both are fine.
- `ops-sessions` prints one line per 30 clients. The first says `"part":1`. `"parts"` × 30
  is at least `/ppp active print count-only`, and the names match `/ppp active print`.

## 5. Go live

```
python tools/render_router_script.py --live
```

Replace both script bodies in Winbox with the new copies, keeping the same names. Run
each once, and check the **Network** page in Ops: the header says "updated HH:MM:SS"
and the PPPoE table fills. Then add the schedulers:

```
/system scheduler add name=ops-live interval=15s start-time=startup policy=read,write,test on-event="/system script run ops-live"
/system scheduler add name=ops-sessions interval=1m start-time=startup policy=read,write,test on-event="/system script run ops-sessions"
```

A scheduler's policy must include its script's, or the run is silently refused.

## 6. Verify

- **CPU:** `/system resource print` before installing and an hour after. The difference
  should be lost in the noise.
- **No overlapping runs:** `/system script job print` a few times. You should never
  see two `ops-live` jobs at once.
- **Nothing written to flash:** `/file print`. Nothing new appears over a day.
- **Quiet log:** `/log print where message~"ops-live|ops-sessions"`. Empty while
  things work. During an outage, at most one line every 15 minutes.
- **"Not reporting" test:** this replaces pulling the Airtel cable on a production
  router. Run `/system scheduler disable ops-live`, wait 2 minutes, then
  `/system scheduler enable ops-live`. Within about 60 s of disabling, the page turns red
  with "Router not reporting since …". After enabling, Events shows one "Router not
  reporting — it stayed up" of about 2 minutes.

## 7. Rotating the token

Do this if it might have been seen. Anyone who can read scripts on the router can
see it; the worst they can do is post fake graphs.

1. `python tools/render_router_script.py --new-token` → put it in Render's
   `NETWORK_INGEST_TOKEN_CCR2116` and redeploy.
2. Render again with the new token and `--live`, then replace both script bodies.
   Readings stop for the few minutes in between; the page shows that as "not
   reporting".

## 8. Reading problems

| What you see | Meaning |
|---|---|
| Router log: `POST failed: ... certificate verification failed` | CA store missing (step 2) |
| Router log: `POST failed: ... timeout` | No path to Ops (Airtel down?) — normal during outages |
| Render log: `network ingest rejected (401)` | Token on the router ≠ token on Render |
| Render log: `network ingest rejected (403)` | Migration not run, or router disabled in `network_routers` |
| Render log: `network ingest rejected (422): ...` | Message shape: the line says which field |
| Page: "Router has not reported yet" | Nothing has arrived at all: check the three lines above |
| Page: Gateway "Doesn't answer ping (seen in ARP)" | That gateway ignores ping. It's not red; set `--internet-target` for a real check |

## Links and capacity

The Network page shows one card per link: Primary or Backup (the lowest route distance is the
primary), Carrying traffic or Standby (which route is active), its gateway, and its traffic.
A new link appears by itself once it has a default route. Nothing on the router or in Ops
needs editing.

**Capacity** is the contracted speed (e.g. 350 Mbps), and the router can't know it. A link
first seen starts at its **port speed**, marked *estimated*. An admin sets the real figure with
the **Set the real speed** button on the link's card; from then on it is marked *set by an
admin* and the router never overwrites it. **Use port speed** puts it back. "Busy" warnings
(80% or more for 2+ minutes) are only raised against a figure an admin has set, because a
port-speed estimate would call a full link "fine". If the contract changes, press **Edit**.

**What the colours mean with two links**
- Trouble on the **primary** link (port down, gateway not responding) is **red**, even when
  customers are online through the backup.
- Trouble on a **backup** link is **amber**: the internet is fine but there is no failover.
- Traffic on the backup while the primary isn't the active route is flagged amber
  ("Traffic is on the backup link").
- The chart has a picker (All links / each link). Events say which link they belong to.

## What this can't see

The router reports through the same Airtel link it's watching. When that link or the
gateway is completely down, nothing arrives, so the page can only say **"Router not
reporting since …"** and can't promise a real-time "gateway down". When reports
resume, Ops uses the router's uptime to tell the two causes apart:
- **Uptime kept counting:** the router stayed up and lost its way out (upstream or gateway).
- **Uptime reset:** a reboot or power cut.

With a second link (Veenet) the router fails over and keeps reporting, so an Airtel failure
then shows as a real "uplink/gateway down" on the Airtel card instead of silence. The
"Router not reporting" case remains for when every path is down, or the router itself is.
