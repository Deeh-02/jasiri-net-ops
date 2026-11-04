"""Network page — the pure rules (no database, no network). Kept apart from
db/network.py so every rule here can be tested with plain values, the same
split as revenue_logic.py / revenue_report.py.

THE ROUTER REMEMBERS NOTHING. On this fleet RouterOS globals did not survive
between scheduled runs (CCR2004, 2026-09-21), so the router's script is
stateless: every message is just "what I see right now". Everything that
needs memory — two failed gateway checks in a row, "this gateway has never
answered ping", "the router restarted", "the router went quiet for 4
minutes" — is worked out here, from the reading before and the reading now,
using the SERVER's receive time.

WHAT THIS DESIGN CAN'T SEE. The router reports through the same Airtel link
it monitors. When that link or the gateway is fully down, nothing arrives,
so the page can only say "Router not reporting since HH:MM:SS". When
reports resume, the router's uptime tells the two causes apart: uptime kept
counting -> it stayed powered and lost its way out (upstream/gateway); uptime
went back near zero -> it lost power or rebooted.
"""
import os
from datetime import timedelta


def _number_setting(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def thresholds():
    """The status thresholds. Environment overrides, defaults from the scope."""
    return {
        "not_reporting_s": _number_setting("NETWORK_NOT_REPORTING_SECONDS", 60),
        "utilization_amber_pct": _number_setting("NETWORK_UTILIZATION_AMBER_PCT", 80),
        "cpu_amber_pct": _number_setting("NETWORK_CPU_AMBER_PCT", 80),
        "gateway_loss_amber_pct": _number_setting("NETWORK_GATEWAY_LOSS_AMBER_PCT", 20),
        "gateway_rtt_amber_ms": _number_setting("NETWORK_GATEWAY_RTT_AMBER_MS", 50),
        "gateway_down_after_fails": 2,
        "counters_window_s": 120,
        "sessions_stale_s": 180,
        "batch_expiry_s": 120,
    }


# ---- Between two readings ----

def is_restart(previous_uptime_s, uptime_s):
    """Uptime going DOWN can only mean the router started again."""
    return previous_uptime_s is not None and uptime_s < previous_uptime_s


def next_gateway_memory(previous, gateway):
    """(fail_streak, seen_alive) after this reading. `previous` is
    {'address', 'fail_streak', 'seen_alive'} from the last reading. A new
    gateway address (a failover) starts over: the old gateway's history says
    nothing about the new one."""
    streak, seen_alive = previous.get("fail_streak", 0), previous.get("seen_alive", False)
    if (gateway or {}).get("address") != previous.get("address"):
        streak, seen_alive = 0, False
    alive = (gateway or {}).get("alive")
    if alive is True:
        return 0, True
    if alive is False:
        return streak + 1, seen_alive
    return streak, seen_alive  # not checked this time: no change either way


def counters_total(live):
    """Errors + drops on the uplink, both directions. None when the router
    sent none of them."""
    uplink = live.get("uplink") or {}
    values = [uplink.get(k) for k in ("rx_errors", "tx_errors", "rx_drops", "tx_drops")]
    values = [v for v in values if v is not None]
    return sum(values) if values else None


def counters_rose(previous_live, live, restarted):
    """Did the error/drop counters go up since the last reading? Not after a
    restart: the counters start again from zero then."""
    if previous_live is None or restarted:
        return False
    before, now = counters_total(previous_live), counters_total(live)
    return before is not None and now is not None and now > before


def gap_seconds(previous_received_at, now):
    return None if previous_received_at is None else (now - previous_received_at).total_seconds()


def quiet_period(previous_received_at, now, restarted, uptime_s, cfg):
    """The 'not reporting' event for a silence that just ended, or None.
    Cause from the uptime: restarted -> power/reboot; otherwise the router
    stayed up and lost its way out."""
    gap = gap_seconds(previous_received_at, now)
    if gap is None or gap <= cfg["not_reporting_s"]:
        return None
    return {
        "type": "not_reporting",
        "started_at": previous_received_at,
        "ended_at": now,
        "details": {
            "cause": "restart" if restarted else "connectivity",
            "router_stayed_up": not restarted,
            "duration_s": int(gap),
            "uptime_s_on_return": uptime_s,
        },
    }


def router_event_times(event, now):
    """Absolute start/end for an event the router reports with relative
    times only ('ended N seconds ago, lasted M')."""
    ended = now - timedelta(seconds=event["ended_ago_s"])
    return ended - timedelta(seconds=event["duration_s"]), ended


# ---- Showing a reading ----

def utilization_pct(bps, capacity_mbps):
    """Share of the CONTRACTED rate (e.g. 350 Mbps), not the port speed."""
    if bps is None or not capacity_mbps:
        return None
    return round(bps / (float(capacity_mbps) * 1_000_000) * 100, 1)


def gateway_view(gateway, fail_streak, seen_alive, cfg):
    """{state, label} for the gateway line. States never look alike:
    alive / missed (one failed check) / not_responding (2+ in a row) /
    no_ping (never answered ICMP but is there in ARP) / not_detectable."""
    gateway = gateway or {}
    if not gateway.get("address"):
        return {"state": "not_detectable",
                "label": "Gateway not detectable (set gateway_ip in the router script)"}
    if gateway.get("alive") is True:
        return {"state": "alive", "label": "Alive"}
    if gateway.get("alive") is None:
        return {"state": "unknown", "label": "Not checked"}
    if not seen_alive and (gateway.get("arp") or "").lower() in ("complete", "reachable", "true", "yes"):
        return {"state": "no_ping", "label": "Doesn't answer ping (seen in ARP)"}
    if fail_streak >= cfg["gateway_down_after_fails"]:
        return {"state": "not_responding", "label": "Not responding"}
    return {"state": "missed", "label": "Missed one check"}


def status(latest, recent_minutes, now, capacity_mbps, cfg):
    """Overall red / amber / green, with every reason. `latest` is the
    network_latest row as a dict; `recent_minutes` are the last complete
    1-minute rollups, newest first, each {'rx_bps_avg', 'tx_bps_avg'}."""
    if not latest or latest.get("live_received_at") is None:
        return {"level": "red", "state": "never_reported",
                "reasons": [{"code": "never_reported", "text": "The router has not reported yet"}]}
    received = latest["live_received_at"]
    live = latest["live"] or {}
    if (now - received).total_seconds() > cfg["not_reporting_s"]:
        return {"level": "red", "state": "not_reporting", "since": received,
                "reasons": [{"code": "not_reporting",
                             "text": ("Router not reporting: possible upstream outage, power loss, "
                                      "or the script stopped")}]}

    red, amber = [], []
    uplink = live.get("uplink") or {}
    if uplink.get("running") is False:
        red.append({"code": "uplink_down", "text": "Uplink not running"})
    gateway = gateway_view(live.get("gateway"), latest.get("gateway_fail_streak", 0),
                           latest.get("gateway_seen_alive", False), cfg)
    if gateway["state"] == "not_responding":
        red.append({"code": "gateway_down", "text": "Gateway not responding"})

    busy = [m for m in recent_minutes[:2]
            if max(utilization_pct(m["rx_bps_avg"], capacity_mbps) or 0,
                   utilization_pct(m["tx_bps_avg"], capacity_mbps) or 0) >= cfg["utilization_amber_pct"]]
    if len(recent_minutes) >= 2 and len(busy) == 2:
        amber.append({"code": "busy", "text": f"Uplink at or above {cfg['utilization_amber_pct']:g}% of "
                                              f"{float(capacity_mbps):g} Mbps for 2+ minutes"})
    rose_at = latest.get("counters_rose_at")
    if rose_at is not None and (now - rose_at).total_seconds() <= cfg["counters_window_s"]:
        amber.append({"code": "errors", "text": "Errors or drops rising on the uplink"})
    cpu = (live.get("system") or {}).get("cpu_pct")
    if cpu is not None and cpu >= cfg["cpu_amber_pct"]:
        amber.append({"code": "cpu", "text": f"Router CPU at {cpu:g}%"})
    gw = live.get("gateway") or {}
    if gateway["state"] in ("alive", "missed"):
        if (gw.get("loss_pct") or 0) >= cfg["gateway_loss_amber_pct"]:
            amber.append({"code": "gateway_loss", "text": f"Gateway losing {gw['loss_pct']:g}% of pings"})
        if (gw.get("rtt_ms_avg") or 0) > cfg["gateway_rtt_amber_ms"]:
            amber.append({"code": "gateway_slow", "text": f"Gateway slow ({gw['rtt_ms_avg']:g} ms)"})
    internet = live.get("internet")
    if internet and internet.get("alive") is False and gateway["state"] == "alive":
        amber.append({"code": "internet", "text": "Internet check failing while the gateway is fine"})

    if red:
        return {"level": "red", "state": red[0]["code"], "reasons": red + amber}
    if amber:
        return {"level": "amber", "state": "degraded", "reasons": amber}
    return {"level": "green", "state": "ok", "reasons": []}


def merge_outages(events):
    """Events for display, newest first. A 'not reporting' gap that overlaps
    a gateway-down the ROUTER reported is the same outage seen twice: keep
    the router's (it knows the cause) and drop the gap."""
    router_reported = [e for e in events if e["source"] == "router"]

    def covered(gap):
        return any(r["started_at"] <= (gap["ended_at"] or gap["started_at"])
                   and (r["ended_at"] or r["started_at"]) >= gap["started_at"] for r in router_reported)

    kept = [e for e in events if not (e["type"] == "not_reporting" and covered(e))]
    return sorted(kept, key=lambda e: e["started_at"], reverse=True)


# ---- Customers ----

def customer_status(customer, now):
    """'active' | 'expired' | 'unknown' for one session's customer record.
    Active needs all three: account Active, not disabled, expiry not passed.
    Anything else on a matched account counts as expired (with why)."""
    if customer is None:
        return "unknown", None
    if customer.get("account_status") != "Active":
        return "expired", f"account {customer.get('account_status') or 'not active'}"
    if not customer.get("enabled", True):
        return "expired", "disabled in billing"
    expiry = customer.get("expiry")
    if expiry is not None and expiry < now:
        return "expired", "past expiry"
    return "active", None


def username_key(username):
    """How a router username is matched to the customer list: the PPPoE
    username, compared without case or surrounding spaces."""
    return (username or "").strip().lower()


def assemble_batch(parts, expected):
    """The full session list from a batch's parts, in order, or None if any
    part is missing."""
    by_part = {p["part"]: p["rows"] for p in parts}
    if sorted(by_part) != list(range(1, expected + 1)):
        return None
    return [row for n in range(1, expected + 1) for row in by_part[n]]


# ---- History ----

# range -> (look-back, bucket size in minutes)
HISTORY_RANGES = {"1h": (timedelta(hours=1), 1), "24h": (timedelta(hours=24), 5), "7d": (timedelta(days=7), 60)}
