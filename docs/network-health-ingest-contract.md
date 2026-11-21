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
| `key` | Unique id of the log line. **Dedupe on it**: the shipper resends a batch whenever it didn't get a 2xx, so the same record can arrive twice. |
| `ts` | When the server received the reading, ISO 8601 with offset (`+03:00`). Store as timestamptz. |
| `from_ip` | Public IP it came from (102.0.27.188 today; changes at the provider switch). |
| `kind` | `probe`, `inner`, `link`, `linknew`, `speed`, `speednew`, `route`, `dns`, `olt`, `crs_link` |
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

**`crs_link`** (when a CRS326 port goes up or down: event-driven)
```json
"crs_link": {"device": "crs326", "interface": "sfp-sfpplus3", "state": "down", "speed": null}
"crs_link": {"device": "crs326", "interface": "sfp-sfpplus3", "state": "up", "speed": "10G"}
```
- `state` is `up` or `down` (anything else is skipped). `speed` is null on a down.
- Ports: `sfp-sfpplus1` = CCR2116, `sfp-sfpplus2` = CCR2004,
  `sfp-sfpplus3` = OLT uplink, `sfp-sfpplus4` = Proxmox.
- OPS pairs each `down` with the next `up` on the same interface (the outage).
  Only `sfp-sfpplus3` alerts.

## Response

- Answer **2xx only after the batch is stored**. Anything else makes the
  shipper resend the same batch on its next run (every minute).
- Ignore unknown `kind`s and unknown fields, but still answer 2xx, so the
  routers can add readings later without breaking the feed.
- 401 for a wrong token. Never include the expected token in the error.

## Volume

About 1,440 `probe` + 1,440 `inner` + 288 `link` + 288 `dns` + 288 `olt` + 2 `speed` + 12 `route` records
a day, plus a `crs_link` record whenever a CRS326 port changes.
The first run backfills everything since 20 Nov 2026 17:15 in batches of 500.
