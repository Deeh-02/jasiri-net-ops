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
| `kind` | `probe`, `inner`, `link`, `linknew`, `speed`, `speednew` |
| `raw` | The original log line, for audit and re-parsing. |

Plus one of these, by `kind`:

**`probe`** (CCR2116, every minute) and **`inner`** (CCR2004, every minute)
```json
"load":   {"down_mbps": 242, "up_mbps": 27, "new_down_mbps": null, "ppp": 29, "cpu_pct": 3, "conns": 17556},
"probes": [{"name": "p-fra", "rtt_ms": 203.309, "jitter_ms": 0.173, "loss_pct": 0}, ...]
```
- `inner` only has `load.cpu_pct`; its probe names start with `i-`.
- Probe names today: `p-gw` (Airtel gateway), `p-nbo` (Nairobi, UoN via KIXP),
  `p-goog` (8.8.8.8), `p-cf` (1.1.1.1), `p-fra` (Frankfurt). Later: `p-jnb`, `p-far`,
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

## Response

- Answer **2xx only after the batch is stored**. Anything else makes the
  shipper resend the same batch on its next run (every minute).
- Ignore unknown `kind`s and unknown fields, but still answer 2xx, so the
  routers can add readings later without breaking the feed.
- 401 for a wrong token. Never include the expected token in the error.

## Volume

About 1,440 `probe` + 1,440 `inner` + 288 `link` + 2 `speed` records a day.
The first run backfills everything since 20 Nov 2026 17:15 in batches of 500.
