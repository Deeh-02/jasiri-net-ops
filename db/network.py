"""Network page — data access (migrations 0022 and 0023). Writes only the
seven network_* tables; reads the customer list only through
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
from db.connection import db_cursor, utc_iso

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

def _open_event(cur, slug, event_type, link=None):
    """The open event of this type (for this link, when given), or None."""
    cur.execute(
        "SELECT id FROM network_events WHERE router = %s AND type = %s AND ended_at IS NULL "
        "AND details->>'link' IS NOT DISTINCT FROM %s ORDER BY started_at DESC LIMIT 1",
        (slug, event_type, link),
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


def _track_link_events(cur, slug, link, gateway_state, previous_received_at, now):
    """Opens or closes gateway_down / uplink_down for ONE link as its reading
    changes. The link is recorded in the event so Airtel's outage and Veenet's
    are told apart."""
    key = link["interface"]
    who = {"link": key, "label": link.get("label")}
    gateway_address = (link.get("gateway") or {}).get("address")
    open_gateway = _open_event(cur, slug, "gateway_down", key)
    if gateway_state == "not_responding" and open_gateway is None:
        # The first failed check was the reading before this one.
        _add_event(cur, slug, "gateway_down", previous_received_at or now, None,
                   {**who, "address": gateway_address})
    elif gateway_state not in ("not_responding", "unknown") and open_gateway is not None:
        _close_event(cur, open_gateway, now)

    open_uplink = _open_event(cur, slug, "uplink_down", key)
    if link.get("running") is False and open_uplink is None:
        _add_event(cur, slug, "uplink_down", now, None, {**who, "interface": key})
    elif link.get("running") is True and open_uplink is not None:
        _close_event(cur, open_uplink, now)


def _close_events_for_restart(cur, slug, live, now):
    """Anything still open before a restart ended, at the latest, when the
    router went down."""
    booted = now - timedelta(seconds=live["uptime_s"])
    cur.execute(
        "UPDATE network_events SET ended_at = %s WHERE router = %s AND ended_at IS NULL AND started_at < %s "
        "AND type IN ('gateway_down', 'uplink_down')",
        (booted, slug, booted),
    )


def _carrying_link(links):
    """The link traffic is using now (its default route is active); failing
    that, the primary. Its gateway is what the router-level rollup records."""
    active = [l for l in links if l.get("active")]
    if active:
        return active[0]
    primary = logic.primary_interface(links)
    return next((l for l in links if l["interface"] == primary), links[0] if links else {})


def _add_to_rollup(cur, slug, live, now):
    """The router-level 1-minute rollup: traffic is the SUM of every link."""
    links = logic.links_of(live)
    rx, tx = logic.total_traffic(links)
    gateway = _carrying_link(links).get("gateway") or {}
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
         rx, rx, tx, tx,
         live.get("pppoe_online"),
         cpu or 0, 0 if cpu is None else 1,
         rtt or 0, 0 if rtt is None else 1,
         gateway.get("loss_pct")),
    )


def _add_to_link_rollup(cur, slug, link, now):
    gateway = link.get("gateway") or {}
    rtt = gateway.get("rtt_ms_avg")
    rx, tx = link.get("rx_bps") or 0, link.get("tx_bps") or 0
    cur.execute(
        """
        INSERT INTO network_uplink_minutes (router, interface, minute, samples, rx_bps_sum, rx_bps_max,
                                            tx_bps_sum, tx_bps_max, gw_rtt_ms_sum, gw_rtt_samples, gw_loss_pct_max)
        VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (router, interface, minute) DO UPDATE SET
            samples = network_uplink_minutes.samples + 1,
            rx_bps_sum = network_uplink_minutes.rx_bps_sum + EXCLUDED.rx_bps_sum,
            rx_bps_max = GREATEST(network_uplink_minutes.rx_bps_max, EXCLUDED.rx_bps_max),
            tx_bps_sum = network_uplink_minutes.tx_bps_sum + EXCLUDED.tx_bps_sum,
            tx_bps_max = GREATEST(network_uplink_minutes.tx_bps_max, EXCLUDED.tx_bps_max),
            gw_rtt_ms_sum = network_uplink_minutes.gw_rtt_ms_sum + EXCLUDED.gw_rtt_ms_sum,
            gw_rtt_samples = network_uplink_minutes.gw_rtt_samples + EXCLUDED.gw_rtt_samples,
            gw_loss_pct_max = GREATEST(network_uplink_minutes.gw_loss_pct_max, EXCLUDED.gw_loss_pct_max)
        """,
        (slug, link["interface"], now.replace(second=0, microsecond=0), rx, rx, tx, tx,
         rtt or 0, 0 if rtt is None else 1, gateway.get("loss_pct")),
    )


def _link_row(cur, slug, interface):
    """The stored row for a link, locked, or None if the link is new."""
    cur.execute(
        "SELECT gateway_address, capacity_mbps, capacity_source, reading, gateway_fail_streak, "
        "gateway_seen_alive, counters_rose_at, in_service FROM network_uplinks "
        "WHERE router = %s AND interface = %s FOR UPDATE",
        (slug, interface),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {"gateway_address": row[0], "capacity_mbps": None if row[1] is None else float(row[1]),
            "capacity_source": row[2], "reading": row[3], "gateway_fail_streak": row[4],
            "gateway_seen_alive": row[5], "counters_rose_at": row[6], "in_service": row[7]}


def _store_link(cur, slug, link, now, restarted, previous_received_at, cfg):
    """Everything that follows from one link's reading; returns its gateway
    state and updated memory."""
    previous = _link_row(cur, slug, link["interface"])
    rate = logic.parse_rate_mbps(link.get("link_rate"))
    capacity, source = logic.next_capacity(previous, rate)
    streak, seen_alive = logic.next_gateway_memory(
        {"address": (previous or {}).get("gateway_address"),
         "fail_streak": (previous or {}).get("gateway_fail_streak", 0),
         "seen_alive": (previous or {}).get("gateway_seen_alive", False)},
        link.get("gateway"))
    gateway_state = logic.gateway_view(link.get("gateway"), streak, seen_alive, cfg)["state"]
    # A link an admin has switched off raises no events (its memory above still
    # follows the readings, so switching it back on starts from the truth).
    if previous is None or previous["in_service"]:
        _track_link_events(cur, slug, link, gateway_state, previous_received_at, now)
    rose = logic.counters_rose((previous or {}).get("reading"), link, restarted)
    cur.execute(
        """
        INSERT INTO network_uplinks (router, interface, label, gateway_address, link_rate_mbps, capacity_mbps,
                                     capacity_source, active, reading, reading_received_at, gateway_fail_streak,
                                     gateway_seen_alive, counters_rose_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (router, interface) DO UPDATE SET
            label = EXCLUDED.label, gateway_address = EXCLUDED.gateway_address,
            link_rate_mbps = EXCLUDED.link_rate_mbps, capacity_mbps = EXCLUDED.capacity_mbps,
            capacity_source = EXCLUDED.capacity_source, active = EXCLUDED.active, reading = EXCLUDED.reading,
            reading_received_at = EXCLUDED.reading_received_at,
            gateway_fail_streak = EXCLUDED.gateway_fail_streak, gateway_seen_alive = EXCLUDED.gateway_seen_alive,
            counters_rose_at = CASE WHEN %s THEN EXCLUDED.reading_received_at
                                    ELSE network_uplinks.counters_rose_at END
        """,
        (slug, link["interface"], link.get("label"), (link.get("gateway") or {}).get("address"), rate, capacity,
         source, link.get("active"), Json(link), now, streak, seen_alive, None, rose),
    )
    _add_to_link_rollup(cur, slug, link, now)
    return {"state": gateway_state, "fail_streak": streak, "seen_alive": seen_alive, "rose": rose}


def record_live(slug, live, now=None):
    """Stores one live reading and everything that follows from it: restart
    and quiet-period events, each link's gateway/uplink up-down, its error
    counters and rollup, router-reported events (deduplicated), and the
    router-level minute rollup. One transaction; the latest row is locked so
    two readings can't interleave."""
    now = now or datetime.utcnow()
    cfg = logic.thresholds()
    links = logic.links_of(live)
    with db_cursor() as (conn, cur):
        _ensure_latest_row(cur, slug)
        cur.execute(
            "SELECT live, live_received_at, last_uptime_s FROM network_latest WHERE router = %s FOR UPDATE",
            (slug,),
        )
        prev_live, prev_received, prev_uptime = cur.fetchone()

        restarted = logic.is_restart(prev_uptime, live["uptime_s"])
        if restarted:
            booted = now - timedelta(seconds=live["uptime_s"])
            _add_event(cur, slug, "router_restarted", booted, booted, {"previous_uptime_s": prev_uptime})
            _close_events_for_restart(cur, slug, live, now)
        quiet = logic.quiet_period(prev_received, now, restarted, live["uptime_s"], cfg)
        if quiet:
            _add_event(cur, slug, "not_reporting", quiet["started_at"], quiet["ended_at"], quiet["details"])

        results = {l["interface"]: _store_link(cur, slug, l, now, restarted, prev_received, cfg) for l in links}
        primary = results.get(logic.primary_interface(links)) or {"state": "unknown", "fail_streak": 0,
                                                                  "seen_alive": False}

        for event in live.get("events") or []:
            started, ended = logic.router_event_times(event, now)
            _add_event(cur, slug, event["type"], started, ended, {"duration_s": event["duration_s"]},
                       source="router", router_event_id=event["event_id"])

        rose = any(r["rose"] for r in results.values())
        cur.execute(
            """
            UPDATE network_latest SET live = %s, live_received_at = %s, last_uptime_s = %s,
                   gateway_fail_streak = %s, gateway_seen_alive = %s,
                   counters_rose_at = CASE WHEN %s THEN %s ELSE counters_rose_at END
            WHERE router = %s
            """,
            (Json(live), now, live["uptime_s"], primary["fail_streak"], primary["seen_alive"], rose, now, slug),
        )
        _add_to_rollup(cur, slug, live, now)
        conn.commit()
    purge_if_due(now)
    return {"restarted": restarted, "quiet_period": quiet is not None,
            "gateway": primary["state"], "links": {k: v["state"] for k, v in results.items()}}


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
    """Drops rollups (router and per-link) past 30 days and closed events past 90. At most hourly,
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
            cur.execute("DELETE FROM network_uplink_minutes WHERE minute < %s", (now - MINUTES_KEEP,))
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
        "link": e["details"].get("label") or e["details"].get("link"),
    }


OUTAGE_TYPES = ("not_reporting", "gateway_down", "uplink_down")


def _mbps(bps):
    return None if bps is None else round(float(bps) / 1e6, 1)


def _link_entries(cur, slug, live, now, this_minute, eat_midnight_utc):
    """One entry per link the router has ever reported, with what the status
    rules and the page need: its stored reading and memory, its last two full
    minutes, its peaks, and whether the newest message still lists it."""
    cur.execute(
        "SELECT interface, label, link_rate_mbps, capacity_mbps, capacity_source, active, reading, "
        "reading_received_at, gateway_fail_streak, gateway_seen_alive, counters_rose_at, in_service "
        "FROM network_uplinks WHERE router = %s ORDER BY first_seen_at, interface", (slug,))
    rows = cur.fetchall()
    cur.execute(
        "SELECT interface, rx_bps_sum / samples, tx_bps_sum / samples FROM network_uplink_minutes "
        "WHERE router = %s AND minute < %s AND minute >= %s AND samples > 0 ORDER BY minute DESC",
        (slug, this_minute, this_minute - timedelta(minutes=5)))
    recent = {}
    for interface, rx, tx in cur.fetchall():
        recent.setdefault(interface, []).append({"rx_bps_avg": float(rx), "tx_bps_avg": float(tx)})
    cur.execute(
        "SELECT interface, max(rx_bps_max) FILTER (WHERE minute >= %s), max(tx_bps_max) FILTER (WHERE minute >= %s), "
        "max(rx_bps_max), max(tx_bps_max) FROM network_uplink_minutes WHERE router = %s GROUP BY interface",
        (eat_midnight_utc, eat_midnight_utc, slug))
    peaks = {r[0]: r[1:] for r in cur.fetchall()}
    # When each link's download peak today happened (its single highest 15-second reading).
    cur.execute(
        "SELECT DISTINCT ON (interface) interface, minute FROM network_uplink_minutes "
        "WHERE router = %s AND minute >= %s ORDER BY interface, rx_bps_max DESC, minute DESC",
        (slug, eat_midnight_utc))
    peak_at = {r[0]: r[1] for r in cur.fetchall()}

    listed = {l["interface"] for l in logic.links_of(live)}
    off = {r[0] for r in rows if not r[11]}
    readings = [r[6] for r in rows if r[6]]
    # The primary is chosen among the links that are listed AND in service.
    live_readings = [x for x in readings if x["interface"] in listed and x["interface"] not in off]
    primary = logic.primary_interface(live_readings or readings)
    entries = []
    for (interface, label, rate, capacity, source, active, reading, received, streak, seen, rose, in_service) in rows:
        peak = peaks.get(interface, (None, None, None, None))
        entries.append({
            "interface": interface, "label": label, "reading": reading or {}, "listed": interface in listed,
            "in_service": in_service,
            "link_rate_mbps": None if rate is None else float(rate),
            "capacity_mbps": None if capacity is None else float(capacity), "capacity_source": source,
            "active": active, "reading_received_at": received,
            "gateway_fail_streak": streak, "gateway_seen_alive": seen, "counters_rose_at": rose,
            "recent_minutes": recent.get(interface, [])[:2], "primary": interface == primary,
            "peaks": {"today_rx": peak[0], "today_tx": peak[1], "all_rx": peak[2], "all_tx": peak[3],
                      "today_rx_at": peak_at.get(interface)},
        })
    return entries


def _link_view(e, cfg):
    """The page's card for one link."""
    reading = e["reading"]
    gateway = logic.gateway_view(reading.get("gateway"), e["gateway_fail_streak"], e["gateway_seen_alive"], cfg)
    gw = reading.get("gateway") or {}
    capacity = e["capacity_mbps"]
    return {
        "interface": e["interface"], "label": e["label"], "name": e["label"] or e["interface"],
        "primary": e["primary"], "active": e["active"], "listed": e["listed"], "in_service": e["in_service"],
        "running": reading.get("running"), "link_rate": reading.get("link_rate"),
        "download_mbps": _mbps(reading.get("rx_bps")), "upload_mbps": _mbps(reading.get("tx_bps")),
        "capacity": {"mbps": capacity, "source": e["capacity_source"], "estimated": e["capacity_source"] != "admin"},
        "download_utilization_pct": logic.utilization_pct(reading.get("rx_bps"), capacity),
        "upload_utilization_pct": logic.utilization_pct(reading.get("tx_bps"), capacity),
        "peak_today": {"download_mbps": _mbps(e["peaks"]["today_rx"]), "upload_mbps": _mbps(e["peaks"]["today_tx"]),
                       "download_at": utc_iso(e["peaks"]["today_rx_at"])},
        "peak_30d": {"download_mbps": _mbps(e["peaks"]["all_rx"]), "upload_mbps": _mbps(e["peaks"]["all_tx"])},
        "gateway": {**gateway, "address": gw.get("address"), "rtt_ms_avg": gw.get("rtt_ms_avg"),
                    "rtt_ms_max": gw.get("rtt_ms_max"), "loss_pct": gw.get("loss_pct")},
        "errors": {k: reading.get(k) for k in ("rx_errors", "tx_errors", "rx_drops", "tx_drops")},
        "sfp": reading.get("sfp"),
    }


def overview(slug, now=None):
    """Everything the page's header, link cards and totals need, for one router."""
    now = now or datetime.utcnow()
    cfg = logic.thresholds()
    router = get_router(slug)
    if router is None:
        return None
    this_minute = now.replace(second=0, microsecond=0)
    # EAT midnight as UTC: "today's peak" is a Nairobi day.
    eat_midnight_utc = datetime.combine((now + timedelta(hours=3)).date(), datetime.min.time()) - timedelta(hours=3)
    with db_cursor() as (conn, cur):
        latest = _latest(cur, slug)
        live = (latest or {}).get("live") or {}
        entries = _link_entries(cur, slug, live, now, this_minute, eat_midnight_utc)
        # Today's highest single reading in each direction, and the minute it happened in.
        peaks = {}
        for column in ("rx_bps_max", "tx_bps_max"):
            cur.execute(f"SELECT {column}, minute FROM network_minutes WHERE router = %s AND minute >= %s "
                        f"ORDER BY {column} DESC, minute DESC LIMIT 1", (slug, eat_midnight_utc))
            peaks[column] = cur.fetchone() or (None, None)
        (peak_rx, peak_rx_at), (peak_tx, peak_tx_at) = peaks["rx_bps_max"], peaks["tx_bps_max"]
        recent_events = _events(cur, slug, since=now - timedelta(days=7), limit=50)

    current = [e for e in entries if e["listed"] and e["in_service"]]
    state = logic.status(latest, current, now, cfg)
    links = logic.links_of(live)
    rx, tx = logic.total_traffic(links)
    outages = [e for e in logic.merge_outages(recent_events) if e["type"] in OUTAGE_TYPES]
    return {
        "router": {"slug": slug, "name": router["name"]},
        "status": {**state, "since": utc_iso(state.get("since"))},
        "received_at": utc_iso((latest or {}).get("live_received_at")),
        "live": live,
        "links": [_link_view(e, cfg) for e in entries],
        "total": {"download_mbps": _mbps(rx) if links else None, "upload_mbps": _mbps(tx) if links else None,
                  "peak_today_download_mbps": _mbps(peak_rx), "peak_today_upload_mbps": _mbps(peak_tx),
                  "peak_today_download_at": utc_iso(peak_rx_at), "peak_today_upload_at": utc_iso(peak_tx_at)},
        "last_outage": serialise_event(outages[0], now) if outages else None,
        "thresholds": cfg,
    }


def set_capacity(slug, interface, capacity_mbps):
    """An admin's figure for a link's contracted speed; None goes back to the
    automatic one (the port speed the router reports). Returns the link's new
    {capacity_mbps, capacity_source}, or None if there is no such link."""
    with db_cursor() as (conn, cur):
        if capacity_mbps is None:
            cur.execute(
                "UPDATE network_uplinks SET capacity_mbps = link_rate_mbps, capacity_source = 'auto' "
                "WHERE router = %s AND interface = %s RETURNING capacity_mbps, capacity_source",
                (slug, interface))
        else:
            cur.execute(
                "UPDATE network_uplinks SET capacity_mbps = %s, capacity_source = 'admin' "
                "WHERE router = %s AND interface = %s RETURNING capacity_mbps, capacity_source",
                (capacity_mbps, slug, interface))
        row = cur.fetchone()
        conn.commit()
    if row is None:
        return None
    return {"capacity_mbps": None if row[0] is None else float(row[0]), "capacity_source": row[1]}


def set_in_service(slug, interface, in_service):
    """An admin switches a link on or off. Off closes the link's open outage
    events (they were about a link that isn't expected to work) and stops new
    ones. Returns {in_service}, or None if there is no such link."""
    now = datetime.utcnow()
    with db_cursor() as (conn, cur):
        cur.execute("UPDATE network_uplinks SET in_service = %s WHERE router = %s AND interface = %s RETURNING in_service",
                    (in_service, slug, interface))
        row = cur.fetchone()
        if row is not None and not in_service:
            cur.execute(
                "UPDATE network_events SET ended_at = %s WHERE router = %s AND ended_at IS NULL "
                "AND details->>'link' = %s AND type IN ('gateway_down', 'uplink_down')",
                (now, slug, interface))
        conn.commit()
    return None if row is None else {"in_service": row[0]}


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


def _primary_interface(cur, slug):
    """The in-service link with the lowest route distance (ties: oldest)."""
    cur.execute(
        "SELECT interface FROM network_uplinks WHERE router = %s AND in_service "
        "ORDER BY (reading->>'distance')::int NULLS LAST, first_seen_at, interface LIMIT 1", (slug,))
    row = cur.fetchone()
    return row[0] if row else None


def uplink_history(slug, range_key, now=None, link=None):
    """Points for the chart (1h: per minute, 24h: per 5 minutes, 7d: per
    hour) plus the outages in that window, for marking gaps. `link` charts one
    link (the interface name); without it, the router's total across links."""
    if range_key not in logic.HISTORY_RANGES:
        raise ValueError("range must be 1h, 6h, 24h or 7d")
    now = now or datetime.utcnow()
    look_back, bucket_min = logic.HISTORY_RANGES[range_key]
    since = now - look_back
    if link is None:
        table, extra, where, args = ("network_minutes", "max(pppoe_online), sum(cpu_pct_sum) / NULLIF(sum(cpu_samples), 0)",
                                     "router = %s", [slug])
    else:
        table, extra, where, args = ("network_uplink_minutes", "NULL, NULL", "router = %s AND interface = %s",
                                     [slug, link])
    with db_cursor() as (conn, cur):
        cur.execute(
            f"""
            SELECT to_timestamp(floor(extract(epoch FROM minute) / %s) * %s) AT TIME ZONE 'UTC' AS bucket,
                   sum(rx_bps_sum) / NULLIF(sum(samples), 0), max(rx_bps_max),
                   sum(tx_bps_sum) / NULLIF(sum(samples), 0), max(tx_bps_max),
                   {extra},
                   sum(gw_rtt_ms_sum) / NULLIF(sum(gw_rtt_samples), 0),
                   max(gw_loss_pct_max)
            FROM {table}
            WHERE {where} AND minute >= %s
            GROUP BY bucket ORDER BY bucket
            """,
            (bucket_min * 60, bucket_min * 60, *args, since),
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
        found = _events(cur, slug, since=since, limit=200)
        if link is not None:
            # This link's own outages, plus the router-wide silences that hide everything.
            found = [e for e in found if e["details"].get("link") in (None, link)]
        else:
            # The total is shaded for router-wide silences and the PRIMARY link's
            # outages only: a backup link that is down (or unpaid) doesn't stop
            # customers, so it must not paint the whole chart red. Its own
            # chart and the Events list still show it.
            primary = _primary_interface(cur, slug)
            found = [e for e in found if e["details"].get("link") in (None, primary)]
        outages = [e for e in logic.merge_outages(found) if e["type"] in OUTAGE_TYPES]
    return {"range": range_key, "bucket_minutes": bucket_min, "from": utc_iso(since), "to": utc_iso(now),
            "link": link, "points": points, "outages": [serialise_event(e, now) for e in outages]}


def events(slug, limit=50, now=None):
    now = now or datetime.utcnow()
    with db_cursor() as (conn, cur):
        found = _events(cur, slug, limit=min(limit, 100) * 2)
    return [serialise_event(e, now) for e in logic.merge_outages(found)[:limit]]
