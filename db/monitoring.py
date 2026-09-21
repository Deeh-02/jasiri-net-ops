import hashlib
import json
from datetime import datetime, timedelta, timezone
from psycopg2.extras import Json
from db.connection import db_cursor, utc_iso, now_eat

# site_session_counts is written at most once per site per this window, even
# though the router posts every 60s — see 0006's header for why.
SESSION_COUNT_INTERVAL = timedelta(minutes=5)

# A retried POST is recognised by its payload hash, but only against recent
# snapshots — keeps the lookup on the received_at index instead of the table.
DUPLICATE_WINDOW = timedelta(days=1)


def payload_hash(snapshot):
    """Stable hash of one snapshot. sort_keys so key order in the RouterOS
    script's JSON can't make an identical retry look new."""
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def quarantine(reason, raw_payload, vlan_id=None, pppoe_username=None):
    """Records something ingest couldn't place. Deduplicated on unresolved
    (reason, vlan, pppoe): the router re-sends the same unknown every 60s, and
    /ppp active includes home customers that will never be sites — one open
    row per distinct unknown is the useful signal, one per minute is noise."""
    with db_cursor() as (conn, cur):
        _quarantine(cur, reason, raw_payload, vlan_id, pppoe_username)
        conn.commit()


def _quarantine(cur, reason, raw_payload, vlan_id=None, pppoe_username=None):
    cur.execute(
        """
        SELECT 1 FROM ingest_quarantine
        WHERE resolved = false AND reason = %s
          AND vlan_id IS NOT DISTINCT FROM %s
          AND pppoe_username IS NOT DISTINCT FROM %s
        LIMIT 1
        """,
        (reason, vlan_id, pppoe_username),
    )
    if cur.fetchone():
        return
    cur.execute(
        """
        INSERT INTO ingest_quarantine (reason, vlan_id, pppoe_username, raw_payload)
        VALUES (%s, %s, %s, %s)
        """,
        (reason, vlan_id, pppoe_username, Json(raw_payload)),
    )


def _derive_state(site, sessions, pppoe_online):
    """One signal per site, chosen by its liveness_source. 'activity' can only
    say online or unknown — no sessions is not evidence of an outage. 'ping'
    belongs to 4.6 and is not decided here."""
    if site["liveness_source"] == "pppoe":
        return "online" if site["pppoe_username"] in pppoe_online else "offline"
    if site["liveness_source"] == "activity":
        return "online" if sessions and sessions > 0 else "unknown"
    return None


def ingest_snapshot(snapshot, raw_payload, router_ts, router_ts_utc, offset_minutes):
    """Stores one already-validated fleet snapshot in a single transaction.
    Returns "duplicate" for a retry, else "stored"."""
    digest = payload_hash(snapshot)
    sessions_by_vlan = {s["vlan_id"]: s["sessions"] for s in snapshot["sites"]}
    pppoe_online = set(snapshot["pppoe"])

    with db_cursor() as (conn, cur):
        cur.execute(
            "SELECT 1 FROM ingest_snapshots WHERE payload_hash = %s AND received_at > now() - %s LIMIT 1",
            (digest, DUPLICATE_WINDOW),
        )
        if cur.fetchone():
            return "duplicate"

        cur.execute(
            """
            INSERT INTO ingest_snapshots
                (seq, router_ts, router_gmt_offset_minutes, router_ts_utc, sites_reporting, payload_hash)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (snapshot.get("seq"), router_ts, offset_minutes, router_ts_utc, len(sessions_by_vlan), digest),
        )
        snapshot_id = cur.fetchone()[0]

        cur.execute("SELECT id, vlan_id, pppoe_username, liveness_source FROM monitored_sites")
        sites = [
            {"id": r[0], "vlan_id": r[1], "pppoe_username": r[2], "liveness_source": r[3]}
            for r in cur.fetchall()
        ]
        known_vlans = {s["vlan_id"] for s in sites if s["vlan_id"] is not None}
        known_pppoe = {s["pppoe_username"] for s in sites if s["pppoe_username"] is not None}

        for vlan_id in sessions_by_vlan:
            if vlan_id not in known_vlans:
                _quarantine(cur, "unknown_vlan", raw_payload, vlan_id=vlan_id)
        for username in pppoe_online - known_pppoe:
            _quarantine(cur, "unknown_pppoe_user", raw_payload, pppoe_username=username)

        cur.execute(
            """
            SELECT DISTINCT ON (monitored_site_id) monitored_site_id, state
            FROM site_status_log
            ORDER BY monitored_site_id, received_at DESC, id DESC
            """
        )
        last_state = dict(cur.fetchall())

        cur.execute(
            """
            SELECT DISTINCT monitored_site_id FROM site_session_counts
            WHERE granularity = 'raw' AND received_at > now() - %s
            """,
            (SESSION_COUNT_INTERVAL,),
        )
        recently_counted = {r[0] for r in cur.fetchall()}

        for site in sites:
            sessions = sessions_by_vlan.get(site["vlan_id"])
            state = _derive_state(site, sessions, pppoe_online)
            if state is not None and state != last_state.get(site["id"]):
                cur.execute(
                    """
                    INSERT INTO site_status_log
                        (monitored_site_id, state, source, router_ts, snapshot_id)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (site["id"], state, site["liveness_source"], router_ts, snapshot_id),
                )
            if sessions is not None and site["id"] not in recently_counted:
                cur.execute(
                    """
                    INSERT INTO site_session_counts
                        (monitored_site_id, sessions, router_ts, snapshot_id)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (site["id"], sessions, router_ts, snapshot_id),
                )

        conn.commit()
    return "stored"


def _eat_day_start_utc():
    """Start of today in EAT, as the naive UTC datetime the tables store."""
    start = now_eat().replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def get_last_ingest_at():
    """When the router last reported. The UI needs this: with the router
    silent every site's last state just sits there looking current."""
    with db_cursor() as (conn, cur):
        cur.execute("SELECT max(received_at) FROM ingest_snapshots")
        return utc_iso(cur.fetchone()[0])


def get_site_statuses(include_revenue):
    """One row per active monitored site with its current state. Revenue keys
    are added only when include_revenue — absent, not null, so a caller
    without the permission cannot even tell the field exists."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT ms.id, ms.location_id, COALESCE(l.name, ms.name, 'VLAN ' || ms.vlan_id) AS name,
                   ms.vlan_id, ms.liveness_source, ms.notes,
                   st.state, st.received_at,
                   sc.sessions, sc.received_at
            FROM monitored_sites ms
            LEFT JOIN locations l ON l.id = ms.location_id
            LEFT JOIN LATERAL (
                SELECT state, received_at FROM site_status_log
                WHERE monitored_site_id = ms.id
                ORDER BY received_at DESC, id DESC LIMIT 1
            ) st ON true
            LEFT JOIN LATERAL (
                SELECT sessions, received_at FROM site_session_counts
                WHERE monitored_site_id = ms.id
                ORDER BY received_at DESC, id DESC LIMIT 1
            ) sc ON true
            WHERE ms.is_active
            ORDER BY name
            """
        )
        rows = cur.fetchall()
        revenue_by_site = {}
        if include_revenue:
            cur.execute(
                """
                SELECT monitored_site_id, COALESCE(sum(price_kes), 0), count(*)
                FROM revenue_events
                WHERE first_seen_at >= %s AND monitored_site_id IS NOT NULL
                GROUP BY monitored_site_id
                """,
                (_eat_day_start_utc(),),
            )
            revenue_by_site = {r[0]: (float(r[1]), r[2]) for r in cur.fetchall()}

    sites = []
    for r in rows:
        site = {
            "id": r[0], "location_id": r[1], "name": r[2], "vlan_id": r[3],
            "liveness_source": r[4], "notes": r[5],
            "state": r[6] or "unknown", "state_since": utc_iso(r[7]),
            "sessions": r[8], "sessions_at": utc_iso(r[9]),
        }
        if include_revenue:
            today_kes, today_sales = revenue_by_site.get(r[0], (0.0, 0))
            site["revenue_today_kes"] = today_kes
            site["sales_today"] = today_sales
        sites.append(site)
    return sites


def get_site_detail(site_id, include_revenue, history_limit=50, hours=24):
    """One site's recent transitions and session counts, plus (only with
    include_revenue) its latest revenue events. None if the id is unknown."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT ms.id, ms.location_id, COALESCE(l.name, ms.name, 'VLAN ' || ms.vlan_id),
                   ms.vlan_id, ms.liveness_source, ms.notes, ms.is_active
            FROM monitored_sites ms LEFT JOIN locations l ON l.id = ms.location_id
            WHERE ms.id = %s
            """,
            (site_id,),
        )
        head = cur.fetchone()
        if head is None:
            return None
        cur.execute(
            """
            SELECT state, source, received_at FROM site_status_log
            WHERE monitored_site_id = %s ORDER BY received_at DESC, id DESC LIMIT %s
            """,
            (site_id, history_limit),
        )
        history = [{"state": r[0], "source": r[1], "at": utc_iso(r[2])} for r in cur.fetchall()]
        cur.execute(
            """
            SELECT sessions, received_at FROM site_session_counts
            WHERE monitored_site_id = %s AND received_at > now() - %s
            ORDER BY received_at
            """,
            (site_id, timedelta(hours=hours)),
        )
        sessions = [{"sessions": r[0], "at": utc_iso(r[1])} for r in cur.fetchall()]
        events = None
        if include_revenue:
            cur.execute(
                """
                SELECT hotspot_username, profile_name, price_kes, event_type, attribution, first_seen_at
                FROM revenue_events WHERE monitored_site_id = %s
                ORDER BY first_seen_at DESC LIMIT 50
                """,
                (site_id,),
            )
            events = [
                {"username": r[0], "profile": r[1], "price_kes": float(r[2]),
                 "event_type": r[3], "attribution": r[4], "at": utc_iso(r[5])}
                for r in cur.fetchall()
            ]
    detail = {
        "id": head[0], "location_id": head[1], "name": head[2], "vlan_id": head[3],
        "liveness_source": head[4], "notes": head[5], "is_active": head[6],
        "history": history, "sessions": sessions,
    }
    if include_revenue:
        detail["revenue_events"] = events
    return detail
