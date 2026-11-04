"""Network page — data access (migration 0022). Writes only the five
network_* tables; reads the customer list only through
db.customers.match_usernames. The rules (status, outages, gateway memory)
are in db/network_logic.py; this file stores and fetches.

All timestamps are naive UTC (db/connection.py pins the session to UTC) and
are the SERVER's receive time — the router sends no clock.
"""
import logging
from datetime import datetime, timedelta

from psycopg2.extras import Json

from db import customers
from db import network_logic as logic
from db.connection import db_cursor, now_eat, utc_iso

log = logging.getLogger(__name__)

PURGE_EVERY = timedelta(hours=1)
MINUTES_KEEP = timedelta(days=30)
EVENTS_KEEP = timedelta(days=90)
_last_purge = None


def get_router(slug):
    """{slug, name, is_enabled, uplink_capacity_mbps, ppp_router_id} or None."""
    with db_cursor() as (conn, cur):
        cur.execute(
            "SELECT slug, name, is_enabled, uplink_capacity_mbps, ppp_router_id FROM network_routers WHERE slug = %s",
            (slug,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"slug": row[0], "name": row[1], "is_enabled": row[2], "uplink_capacity_mbps": float(row[3]),
            "ppp_router_id": row[4]}


# ---- Writing ----

def _open_event(cur, slug, event_type):
    cur.execute(
        "SELECT id FROM network_events WHERE router = %s AND type = %s AND ended_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (slug, event_type),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _add_event(cur, slug, event_type, started_at, ended_at, details, source="ops", router_event_id=None):
    cur.execute(
        """
        INSERT INTO network_events (router, type, started_at, ended_at, source, router_event_id, details)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (router, router_event_id) DO NOTHING
        """,
        (slug, event_type, started_at, ended_at, source, router_event_id, Json(details)),
    )


def _close_event(cur, event_id, ended_at):
    cur.execute("UPDATE network_events SET ended_at = %s WHERE id = %s", (ended_at, event_id))


def _ensure_latest_row(cur, slug):
    cur.execute("INSERT INTO network_latest (router) VALUES (%s) ON CONFLICT (router) DO NOTHING", (slug,))


def _track_open_events(cur, slug, live, gateway_state, previous_received_at, now, restarted):
    """Opens or closes gateway_down / uplink_down as the reading changes."""
    open_gateway = _open_event(cur, slug, "gateway_down")
    if gateway_state == "not_responding" and open_gateway is None:
        # The first failed check was the reading before this one.
        _add_event(cur, slug, "gateway_down", previous_received_at or now, None,
                   {"address": (live.get("gateway") or {}).get("address")})
    elif gateway_state not in ("not_responding", "unknown") and open_gateway is not None:
        _close_event(cur, open_gateway, now)

    open_uplink = _open_event(cur, slug, "uplink_down")
    running = (live.get("uplink") or {}).get("running")
    if running is False and open_uplink is None:
        _add_event(cur, slug, "uplink_down", now, None, {"interface": live["uplink"].get("interface")})
    elif running is True and open_uplink is not None:
        _close_event(cur, open_uplink, now)

    if restarted:
        # Anything still open before a restart ended, at the latest, when the
        # router went down.
        cur.execute(
            "UPDATE network_events SET ended_at = %s WHERE router = %s AND ended_at IS NULL AND started_at < %s "
            "AND type IN ('gateway_down', 'uplink_down')",
            (now - timedelta(seconds=live["uptime_s"]), slug, now - timedelta(seconds=live["uptime_s"])),
        )


def _add_to_rollup(cur, slug, live, now):
    uplink = live.get("uplink") or {}
    gateway = live.get("gateway") or {}
    cpu = (live.get("system") or {}).get("cpu_pct")
    rtt = gateway.get("rtt_ms_avg")
    cur.execute(
        """
        INSERT INTO network_minutes (router, minute, samples, rx_bps_sum, rx_bps_max, tx_bps_sum, tx_bps_max,
                                     pppoe_online, cpu_pct_sum, cpu_samples, gw_rtt_ms_sum, gw_rtt_samples,
                                     gw_loss_pct_max)
        VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (router, minute) DO UPDATE SET
            samples = network_minutes.samples + 1,
            rx_bps_sum = network_minutes.rx_bps_sum + EXCLUDED.rx_bps_sum,
            rx_bps_max = GREATEST(network_minutes.rx_bps_max, EXCLUDED.rx_bps_max),
            tx_bps_sum = network_minutes.tx_bps_sum + EXCLUDED.tx_bps_sum,
            tx_bps_max = GREATEST(network_minutes.tx_bps_max, EXCLUDED.tx_bps_max),
            pppoe_online = EXCLUDED.pppoe_online,
            cpu_pct_sum = network_minutes.cpu_pct_sum + EXCLUDED.cpu_pct_sum,
            cpu_samples = network_minutes.cpu_samples + EXCLUDED.cpu_samples,
            gw_rtt_ms_sum = network_minutes.gw_rtt_ms_sum + EXCLUDED.gw_rtt_ms_sum,
            gw_rtt_samples = network_minutes.gw_rtt_samples + EXCLUDED.gw_rtt_samples,
            gw_loss_pct_max = GREATEST(network_minutes.gw_loss_pct_max, EXCLUDED.gw_loss_pct_max)
        """,
        (slug, now.replace(second=0, microsecond=0),
         uplink.get("rx_bps") or 0, uplink.get("rx_bps") or 0, uplink.get("tx_bps") or 0, uplink.get("tx_bps") or 0,
         live.get("pppoe_online"),
         cpu or 0, 0 if cpu is None else 1,
         rtt or 0, 0 if rtt is None else 1,
         gateway.get("loss_pct")),
    )


def record_live(slug, live, now=None):
    """Stores one live reading and everything that follows from it: restart
    and quiet-period events, gateway/uplink up-down, router-reported events
    (deduplicated), the error-counter check and the minute rollup. One
    transaction; the latest row is locked so two readings can't interleave."""
    now = now or datetime.utcnow()
    cfg = logic.thresholds()
    with db_cursor() as (conn, cur):
        _ensure_latest_row(cur, slug)
        cur.execute(
            "SELECT live, live_received_at, last_uptime_s, gateway_fail_streak, gateway_seen_alive "
            "FROM network_latest WHERE router = %s FOR UPDATE",
            (slug,),
        )
        prev_live, prev_received, prev_uptime, prev_streak, prev_seen = cur.fetchone()

        restarted = logic.is_restart(prev_uptime, live["uptime_s"])
        if restarted:
            booted = now - timedelta(seconds=live["uptime_s"])
            _add_event(cur, slug, "router_restarted", booted, booted, {"previous_uptime_s": prev_uptime})
        quiet = logic.quiet_period(prev_received, now, restarted, live["uptime_s"], cfg)
        if quiet:
            _add_event(cur, slug, "not_reporting", quiet["started_at"], quiet["ended_at"], quiet["details"])

        streak, seen_alive = logic.next_gateway_memory(
            {"address": ((prev_live or {}).get("gateway") or {}).get("address"),
             "fail_streak": prev_streak, "seen_alive": prev_seen},
            live.get("gateway"))
        gateway_state = logic.gateway_view(live.get("gateway"), streak, seen_alive, cfg)["state"]
        _track_open_events(cur, slug, live, gateway_state, prev_received, now, restarted)

        for event in live.get("events") or []:
            started, ended = logic.router_event_times(event, now)
            _add_event(cur, slug, event["type"], started, ended, {"duration_s": event["duration_s"]},
                       source="router", router_event_id=event["event_id"])

        rose = logic.counters_rose(prev_live, live, restarted)
        cur.execute(
            """
            UPDATE network_latest SET live = %s, live_received_at = %s, last_uptime_s = %s,
                   gateway_fail_streak = %s, gateway_seen_alive = %s,
                   counters_rose_at = CASE WHEN %s THEN %s ELSE counters_rose_at END
            WHERE router = %s
            """,
            (Json(live), now, live["uptime_s"], streak, seen_alive, rose, now, slug),
        )
        _add_to_rollup(cur, slug, live, now)
        conn.commit()
    purge_if_due(now)
    return {"restarted": restarted, "quiet_period": quiet is not None, "gateway": gateway_state}


def record_session_part(slug, message, now=None):
    """Keeps one part of a session list; when every part of the batch is in,
    replaces the router's session list with it in one go. Until then the
    previous full list stays. Parts older than 2 minutes are dropped."""
    now = now or datetime.utcnow()
    cfg = logic.thresholds()
    with db_cursor() as (conn, cur):
        _ensure_latest_row(cur, slug)
        cur.execute("SELECT 1 FROM network_latest WHERE router = %s FOR UPDATE", (slug,))
        cur.execute("DELETE FROM network_session_parts WHERE router = %s AND received_at < %s",
                    (slug, now - timedelta(seconds=cfg["batch_expiry_s"])))
        cur.execute(
            """
            INSERT INTO network_session_parts (router, batch_id, part, parts, rows, received_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (router, batch_id, part) DO UPDATE
                SET parts = EXCLUDED.parts, rows = EXCLUDED.rows, received_at = EXCLUDED.received_at
            """,
            (slug, message["batch_id"], message["part"], message["parts"], Json(message["rows"]), now),
        )
        cur.execute("SELECT part, rows FROM network_session_parts WHERE router = %s AND batch_id = %s",
                    (slug, message["batch_id"]))
        rows = logic.assemble_batch([{"part": r[0], "rows": r[1]} for r in cur.fetchall()], message["parts"])
        if rows is not None:
            cur.execute("UPDATE network_latest SET sessions = %s, sessions_received_at = %s WHERE router = %s",
                        (Json(rows), now, slug))
            # This batch is in; any older, unfinished one is now pointless.
            cur.execute("DELETE FROM network_session_parts WHERE router = %s", (slug,))
        conn.commit()
    return {"complete": rows is not None, "sessions": None if rows is None else len(rows)}


def purge_if_due(now=None):
    """Drops rollups past 30 days and closed events past 90. At most hourly,
    called from ingest itself — no separate scheduler (same approach as
    db/monitoring_retention.py). Never raises: it must not fail an ingest."""
    global _last_purge
    now = now or datetime.utcnow()
    if _last_purge is not None and now - _last_purge < PURGE_EVERY:
        return
    _last_purge = now
    try:
        with db_cursor() as (conn, cur):
            cur.execute("DELETE FROM network_minutes WHERE minute < %s", (now - MINUTES_KEEP,))
            cur.execute("DELETE FROM network_events WHERE ended_at IS NOT NULL AND ended_at < %s",
                        (now - EVENTS_KEEP,))
            conn.commit()
    except Exception:
        log.exception("network: purge of old rollups/events failed; will retry next hour")


# ---- Reading ----

def _latest(cur, slug):
    cur.execute(
        "SELECT live, live_received_at, last_uptime_s, gateway_fail_streak, gateway_seen_alive, counters_rose_at, "
        "sessions, sessions_received_at FROM network_latest WHERE router = %s",
        (slug,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    keys = ("live", "live_received_at", "last_uptime_s", "gateway_fail_streak", "gateway_seen_alive",
            "counters_rose_at", "sessions", "sessions_received_at")
    return dict(zip(keys, row))


def _event_dict(r):
    return {"id": r[0], "type": r[1], "started_at": r[2], "ended_at": r[3], "source": r[4], "details": r[5] or {}}


def _events(cur, slug, since=None, limit=100):
    where, args = "router = %s", [slug]
    if since is not None:
        where += " AND COALESCE(ended_at, now() AT TIME ZONE 'UTC') >= %s"
        args.append(since)
    cur.execute(
        f"SELECT id, type, started_at, ended_at, source, details FROM network_events WHERE {where} "
        "ORDER BY started_at DESC LIMIT %s",
        (*args, limit),
    )
    return [_event_dict(r) for r in cur.fetchall()]


def serialise_event(e, now):
    ended = e["ended_at"]
    duration = int(((ended or now) - e["started_at"]).total_seconds())
    return {
        "type": e["type"], "started_at": utc_iso(e["started_at"]), "ended_at": utc_iso(ended),
        "ongoing": ended is None, "duration_s": duration, "source": e["source"],
        "reported_after_reconnecting": e["source"] == "router",
        "cause": e["details"].get("cause"),
    }


OUTAGE_TYPES = ("not_reporting", "gateway_down", "uplink_down")


def overview(slug, now=None):
    """Everything the page's header and uplink card need, for one router."""
    now = now or datetime.utcnow()
    cfg = logic.thresholds()
    router = get_router(slug)
    if router is None:
        return None
    this_minute = now.replace(second=0, microsecond=0)
    # EAT midnight as UTC: "today's peak" is a Nairobi day.
    eat_midnight_utc = datetime.combine(now_eat().date(), datetime.min.time()) - timedelta(hours=3)
    with db_cursor() as (conn, cur):
        latest = _latest(cur, slug)
        cur.execute(
            "SELECT rx_bps_sum / samples, tx_bps_sum / samples FROM network_minutes "
            "WHERE router = %s AND minute < %s AND samples > 0 ORDER BY minute DESC LIMIT 2",
            (slug, this_minute),
        )
        recent = [{"rx_bps_avg": float(r[0]), "tx_bps_avg": float(r[1])} for r in cur.fetchall()]
        cur.execute("SELECT max(rx_bps_max), max(tx_bps_max) FROM network_minutes WHERE router = %s AND minute >= %s",
                    (slug, eat_midnight_utc))
        peak_rx, peak_tx = cur.fetchone()
        recent_events = _events(cur, slug, since=now - timedelta(days=7), limit=50)

    state = logic.status(latest, recent, now, router["uplink_capacity_mbps"], cfg)
    live = (latest or {}).get("live") or {}
    uplink = live.get("uplink") or {}
    gateway = logic.gateway_view(live.get("gateway"), (latest or {}).get("gateway_fail_streak", 0),
                                 (latest or {}).get("gateway_seen_alive", False), cfg)
    outages = [e for e in logic.merge_outages(recent_events) if e["type"] in OUTAGE_TYPES]
    capacity = router["uplink_capacity_mbps"]
    return {
        "router": {"slug": slug, "name": router["name"], "uplink_capacity_mbps": capacity},
        "status": {**state, "since": utc_iso(state.get("since"))},
        "received_at": utc_iso((latest or {}).get("live_received_at")),
        "live": live,
        "uplink": {
            "download_mbps": None if uplink.get("rx_bps") is None else round(uplink["rx_bps"] / 1e6, 1),
            "upload_mbps": None if uplink.get("tx_bps") is None else round(uplink["tx_bps"] / 1e6, 1),
            "download_utilization_pct": logic.utilization_pct(uplink.get("rx_bps"), capacity),
            "upload_utilization_pct": logic.utilization_pct(uplink.get("tx_bps"), capacity),
            "peak_today_download_mbps": None if peak_rx is None else round(peak_rx / 1e6, 1),
            "peak_today_upload_mbps": None if peak_tx is None else round(peak_tx / 1e6, 1),
        },
        "gateway": gateway,
        "last_outage": serialise_event(outages[0], now) if outages else None,
        "thresholds": cfg,
    }


def pppoe(slug, now=None):
    """The session list joined with the customer list AT READ TIME, so a
    change to a customer's expiry shows on the next refresh. A session with
    no matching customer is kept, as 'unknown', with its raw username."""
    now = now or datetime.utcnow()
    cfg = logic.thresholds()
    router = get_router(slug)
    if router is None:
        return None
    with db_cursor() as (conn, cur):
        latest = _latest(cur, slug)
    sessions = (latest or {}).get("sessions") or []
    matches = customers.match_usernames(router["ppp_router_id"], {logic.username_key(s.get("name")) for s in sessions})
    rows = []
    for s in sessions:
        customer = matches.get(logic.username_key(s.get("name")))
        status, reason = logic.customer_status(customer, now)
        rows.append({
            "display_name": (customer or {}).get("name"),
            "username": s.get("name"),
            "package": (customer or {}).get("plan"),
            "status": status, "status_reason": reason,
            "expiry": utc_iso((customer or {}).get("expiry")),
            "address": s.get("address"),
            "uptime_s": s.get("uptime_s"),
        })
    received = (latest or {}).get("sessions_received_at")
    return {
        "received_at": utc_iso(received),
        "is_stale": received is None or (now - received).total_seconds() > cfg["sessions_stale_s"],
        "customer_list_linked": router["ppp_router_id"] is not None,
        "counts": {
            "online": len(rows),
            "active": sum(1 for r in rows if r["status"] == "active"),
            "expired_but_online": sum(1 for r in rows if r["status"] == "expired"),
            "unknown": sum(1 for r in rows if r["status"] == "unknown"),
        },
        "sessions": rows,
    }


def uplink_history(slug, range_key, now=None):
    """Points for the chart (1h: per minute, 24h: per 5 minutes, 7d: per
    hour) plus the outages in that window, for marking gaps."""
    if range_key not in logic.HISTORY_RANGES:
        raise ValueError("range must be 1h, 24h or 7d")
    now = now or datetime.utcnow()
    look_back, bucket_min = logic.HISTORY_RANGES[range_key]
    since = now - look_back
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT to_timestamp(floor(extract(epoch FROM minute) / %s) * %s) AT TIME ZONE 'UTC' AS bucket,
                   sum(rx_bps_sum) / NULLIF(sum(samples), 0), max(rx_bps_max),
                   sum(tx_bps_sum) / NULLIF(sum(samples), 0), max(tx_bps_max),
                   max(pppoe_online),
                   sum(cpu_pct_sum) / NULLIF(sum(cpu_samples), 0),
                   sum(gw_rtt_ms_sum) / NULLIF(sum(gw_rtt_samples), 0),
                   max(gw_loss_pct_max)
            FROM network_minutes
            WHERE router = %s AND minute >= %s
            GROUP BY bucket ORDER BY bucket
            """,
            (bucket_min * 60, bucket_min * 60, slug, since),
        )
        points = [{
            "t": utc_iso(r[0]),
            "download_mbps_avg": None if r[1] is None else round(float(r[1]) / 1e6, 2),
            "download_mbps_max": None if r[2] is None else round(r[2] / 1e6, 2),
            "upload_mbps_avg": None if r[3] is None else round(float(r[3]) / 1e6, 2),
            "upload_mbps_max": None if r[4] is None else round(r[4] / 1e6, 2),
            "pppoe_online": r[5],
            "cpu_pct_avg": None if r[6] is None else round(float(r[6]), 1),
            "gateway_rtt_ms_avg": None if r[7] is None else round(float(r[7]), 1),
            "gateway_loss_pct_max": None if r[8] is None else float(r[8]),
        } for r in cur.fetchall()]
        outages = [e for e in logic.merge_outages(_events(cur, slug, since=since, limit=200))
                   if e["type"] in OUTAGE_TYPES]
    return {"range": range_key, "bucket_minutes": bucket_min, "from": utc_iso(since), "to": utc_iso(now),
            "points": points, "outages": [serialise_event(e, now) for e in outages]}


def events(slug, limit=50, now=None):
    now = now or datetime.utcnow()
    with db_cursor() as (conn, cur):
        found = _events(cur, slug, limit=min(limit, 100) * 2)
    return [serialise_event(e, now) for e in logic.merge_outages(found)[:limit]]
