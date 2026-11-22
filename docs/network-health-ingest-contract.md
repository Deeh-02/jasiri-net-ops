# OPS side: what `/network/health/ingest` must accept

The Frankfurt server's `jasiri-shipper` sends every reading the routers log
there. This is the contract OPS has to meet. How OPS stores and shows the
data is up to OPS.

## Request

```
POST /network/health/ingest
Authorization: Bearer <token>        # Render env var, e.g. NETWORK_HEALTH_TOKEN
Content-Type: application/json
```

```json
{
  "source": "fra-1",
  "sent_at": "2026-11-20T15:40:01+00:00",
  "records": [ ...up to 500 records, roughly 400 KB at most... ]
}
```

`records` may be EMPTY. When nothing is new, the shipper still POSTs an empty
batch once a minute: a heartbeat. `sent_at` is UTC.

- No POST at all for 10 minutes = Frankfurt / the shipper is down.
- POSTs arriving but no `probe` record for 10 minutes = the routers are silent.
  Expected during an Airtel outage, because the routers reach Frankfurt over
  that same uplink.
- The silence check must not run only on ingest and `/status`. It is also
  called from the existing `/network/ingest` tick (the way `onu_alerts.tick()`
  is called), so it fires when nobody has the page open, from a second,
  independent clock.

## Records

Every record has:

| Field | Meaning |
|---|---|
| `key` | Unique id of the log line. **Upsert on it**: the shipper resends a batch whenever it didn't get a 2xx, so the same record can arrive twice (stored once). A key that arrives again with DIFFERENT values replaces the stored ones: that is how a rewind corrects history (the loss-scale fix, below). |
| `ts` | When the server received the reading, ISO 8601 with offset (`+03:00`). Store as timestamptz. |
| `from_ip` | Public IP it came from (102.0.27.188 today; changes at the provider switch). |
| `kind` | `probe`, `inner`, `link`, `linknew`, `speed`, `speednew`, `route`, `dns`, `olt`, `crs_link`, `hop`, `area`, `lanlink`, `pon`, `ont`, `ppp` |
| `raw` | The original log line, for audit and re-parsing. |

Plus one of these, by `kind`:

**`probe`** (CCR2116, every minute) and **`inner`** (CCR2004, every minute)
```json
"load":   {"down_mbps": 242, "up_mbps": 27, "new_down_mbps": null, "ppp": 29, "cpu_pct": 3, "conns": 17556},
"probes": [{"name": "p-fra", "rtt_ms": 203.309, "jitter_ms": 0.173, "loss_pct": 0}, ...]
```
- `inner` only has `load.cpu_pct`; its probe names start with `i-`.
- Probe names today: `p-gw` (Airtel gateway), `p-nbo` (Nairobi, UoN via KIXP),
  `p-goog` (8.8.8.8), `p-cf` (1.1.1.1), `p-fra` (Frankfurt), `p-za` (South Africa,
  Vodacom 196.207.45.2, about 72 ms; added 21 Nov 2026). Later: `p-jnb`, `p-far`,
  `p-gw-new`, and `-new` twins. Don't hard-code the list.
- **`rtt_ms: null` with `loss_pct: 100` means no reply at all (an outage).
  Store it as null, never 0**, and show it as an outage.
- **`loss_pct` is a real percentage, 0-100** (10 pings: one lost = 10). Until
  21 Nov 2026 ~17:13 EAT the shipper passed on RouterOS netwatch's tenths of a
  percent (one lost ping = 100, an outage = 1000); a rewind resends those lines
  corrected and the upsert replaces them. A value over 100 is stored as unknown.
- `inner` probes: `i-core` / `i-core-big` (to the CCR2116) and `i-fra` /
  `i-fra-big` (to Frankfurt through the CRS326, CCR2116 and Airtel). `-big`
  = 1300-byte pings. OPS alerts on `i-fra*`: loss 20 % or more in 3 readings
  in a row, or no reply in 2.

**`link` / `linknew`** (every 5 minutes)
```json
"link": {"rx_dbm": -6.324, "tx_dbm": -4.983, "temp_c": 28, "link_downs": 7,
         "rx_fcs": 0, "rx_err_events": 0, "rx_overflow": 0, "tx_drop": 58}
```
Counters only go up, and were zeroed at 17:49 on 20 Nov 2026 (baseline start).
They reset again if the router reboots or someone resets them, so work with
the change between readings and treat a drop as a reset.

**`speed` / `speednew`** (03:30 and 20:30)
```json
"speed": {"status": "finished", "duration_s": 67, "mbps": 29.9, "load_before_mbps": 277}
```
`status: "failed"` comes with nulls.

**`route`** (CCR2116, daily at 21:00 and 04:00, one record per site: www.youtube.com,
www.facebook.com, www.tiktok.com, api.safaricom.co.ke)
```json
"route": {"host": "www.youtube.com", "ip": "142.251.157.4", "status": "ok", "last_ms": 7.2,
          "hops": [{"hop": 1, "address": "102.0.27.189", "ms": 1.0},
                   {"hop": 2, "address": null, "ms": null}, ...]}
```
- address/ms null = that hop didn't answer (normal for traceroute, not loss).
- last_ms = the last hop that answered. status "failed" comes with ip null and no hops.
- From 21 Nov 2026 (21:00 run) also `host` = `1.1.1.1` (Cloudflare DNS) and
  `195.238.122.164` (our Frankfurt monitoring server). These watch the
  international path (transit), not where content is served.
- The traceroute stops at 20 hops, so a far target may end on the last hop that
  answered rather than the target itself. That is not a failure.

**`dns`** (CCR2116, every 5 minutes)
```json
"dns": {"resolvers": [
  {"name": "g8", "server": "8.8.8.8", "worst_ms": 10.4, "fails": 0},
  {"name": "q9", "server": "9.9.9.9", "worst_ms": 1.8, "fails": 0},
  {"name": "rt", "server": "router", "worst_ms": 1.6, "fails": 0}]}
```
- worst_ms = the slower of two lookups (www.youtube.com, api.safaricom.co.ke).
- fails = lookups that failed (0-2). A failed lookup counts as 10 s in worst_ms.

**`olt`** (CCR2116, every 5 minutes: an SNMP read of the Huawei OLT, MA5680T.
The OLT's uplink is its port 0/19/0, which receives from the CRS326.)
```json
"olt": {"uptime_s": 626553.26, "up_rx": 8712156132, "up_crc": 1062606263}
```
- `uptime_s`: the OLT's uptime in seconds. It going DOWN between readings is
  an OLT restart (power switchovers do this, about twice a week).
- `up_rx`: good unicast frames received on 0/19/0 (64-bit counter).
- `up_crc`: errored frames received on 0/19/0 (ifInErrors, 32-bit: wraps at
  2^32). Errored frames are NOT in `up_rx`.
- **Any field can be null: that SNMP read failed.** Store it as null; OPS
  counts the reading as a failed poll.
- OPS works out damaged frames per pair of readings:
  `d_crc / (d_rx + d_crc) * 100` (a negative `d_crc` without a restart gets
  +2^32). Never across a restart, never with a null.

**`crs_link`** (when a CRS326, CCR2116 or CCR2004 port goes up or down: event-driven)
```json
"crs_link": {"device": "crs326", "interface": "sfp-sfpplus3", "state": "down", "speed": null}
"crs_link": {"device": "crs326", "interface": "sfp-sfpplus3", "state": "up", "speed": "10G"}
```
- `state` is `up` or `down` (anything else is skipped). `speed` is null on a down.
- Ports: `sfp-sfpplus1` = CCR2116, `sfp-sfpplus2` = CCR2004,
  `sfp-sfpplus3` = OLT uplink, `sfp-sfpplus4` = Proxmox.
- `device` is `crs326`, `ccr2116` or `ccr2004` (the routers' physical ports
  arrive too, from 22 Nov 2026). A record without a device is the CRS326's.
- OPS pairs each `down` with the next `up` on the same port of the same device
  (the outage). Only the CRS326's `sfp-sfpplus3` alerts; the routers' ports
  (they have an `sfp-sfpplus3` too) are shown, labelled with their device.

**`hop`** (CCR2116, every minute: pings to the inside boxes themselves)
```json
"probes": [{"name": "h-crs", "rtt_ms": 0.25, "jitter_ms": 0.08, "loss_pct": 0.0}, ...]
```
- Same `probes` list as `probe`. Names: `h-crs` (CRS326), `h-2004` / `h-2004-big`
  (CCR2004, small / 1300-byte), `h-olt` / `h-olt-big` (OLT management IP).
  Later `h-cust` / `h-cust-big` (a customer path through the OLT).
- Boxes answer pings at low priority (the OLT especially), so OPS shows these
  but never alerts on them, except `h-cust*` (same rule as `i-fra*`).
  `h-olt-big` shows 10-20 % loss while the CRS326-OLT DAC damages frames.

**`area`** (CCR2004, every 5 minutes, one record per VLAN on its uplink)
```json
"area": {"vlan": 5, "if": "sfp-sfpplus1-v5", "down_bytes": 4487076792201, "up_bytes": 529441235403}
```
- Running byte counters. OPS: Mbps = change * 8 / seconds between readings of
  the same VLAN; a counter that went down was reset, so that pair is skipped.
- Area names come from OPS's VLAN list; an unknown VLAN shows as "VLAN n".
  **VLAN 99 is not an area**: it is the CCR2004's link to the core, shown as
  "CCR2004 total".

**`lanlink`** (every 5 minutes, inside port counters)
```json
"lanlink": {"dev": "crs326", "port": "sfp-sfpplus3", "link_downs": 19, "rx_fcs": 0, "rx_err": 8,
            "rx_overflow": 0, "tx_drop": 0}
```
- Ports: `ccr2116 sfp-sfpplus4` (to the CRS326), `ccr2004 sfp-sfpplus1` (to the
  CRS326), `crs326 sfp-sfpplus1` (to the CCR2116), `sfp-sfpplus2` (to the
  CCR2004), `sfp-sfpplus3` (to the OLT), `sfp-sfpplus4` (to Proxmox).
- A counter the port doesn't report is null. Counters only go up; a drop is a
  reset (OPS counts it as 0).

**`pon`** (every 5 minutes, one per OLT PON port that has ONTs)
```json
"pon": {"pon": "0/3/5", "online": 4, "offline": 0}
```

**`ont`** (every 5 minutes, one per ONT, about 62)
```json
"ont": {"pon": "0/3/5", "ont_id": 4, "status": "online", "rx_dbm": -32.21, "olt_rx_dbm": -36.99,
        "distance_m": 4380, "last_down_cause": 13, "last_down_text": "dying-gasp (power)", "desc": "Stima_Hotspot"}
```
- An ONT is `pon` + `ont_id`. `desc` is the name set on the OLT: a customer
  code (JS01..JS46) and/or a site name.
- `rx_dbm` = light the ONT receives (downstream), `olt_rx_dbm` = light the
  OLT receives from it (upstream). Either can be null (offline). OPS stores a
  value outside -60..+10 dBm as null.
- `status` is `online` or `offline`; anything else is stored but not judged.
- `last_down_text`: "LOSi/LOBi (light loss)" = a fibre problem, "dying-gasp
  (power)" = the ONT lost power, resets = a reboot. OPS classifies by these words.
- A record without `pon` or `ont_id` is skipped. OPS keeps the raw `ont`
  rows 3 days (about 18,000 a day); their light lives on as hourly averages.
- One poll's ONT lines may arrive in two posts: OPS judges a poll once the
  next poll has started arriving, or nothing has come in for 2 minutes.

**`ppp`** (every 5 minutes, one per router)
```json
"ppp": {"dev": "ccr2004", "count": 21, "names": ["JS16", "Stima_Hotspot", "..."]}
```
- `dev` `ccr2116` = home PPPoE customers (JSxx); `ccr2004` = mostly hotspot
  SITE routers (`*_Hotspot` / `*_HOTSPOT`) plus a few customers.
- `names` must be the FULL list of session names: OPS diffs consecutive
  lists (a name missing = that session dropped). Without `dev` or a `names`
  list the record is skipped.

## Response

- Answer **2xx only after the batch is stored**. Anything else makes the
  shipper resend the same batch on its next run (every minute).
- Ignore unknown `kind`s and unknown fields, but still answer 2xx, so the
  routers can add readings later without breaking the feed.
- 401 for a wrong token. Never include the expected token in the error.

## Volume

About 1,440 `probe` + 1,440 `inner` + 1,440 `hop` + 288 `link` + 288 `dns` + 288 `olt` + 288 per VLAN
`area` + 288 per port `lanlink` + 288 per PON `pon` + 288 per ONT `ont` (about 18,000) + 576 `ppp`
+ 2 `speed` + 12 `route` records a day, plus a `crs_link` record whenever a CRS326 or router port changes.
The first run backfills everything since 20 Nov 2026 17:15 in batches of 500.
