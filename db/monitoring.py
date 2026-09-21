import hashlib
import json
from datetime import datetime, timedelta, timezone
import psycopg2
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
    row per distinct unknown is the useful signal, one per minute is noise.

    Resolved rows count too: dismissing a home customer must stick, or the
    next heartbeat reopens it. A name that later becomes a site stops being
    reported at all, so resolved rows never hide anything still unknown."""
    with db_cursor() as (conn, cur):
        _quarantine(cur, reason, raw_payload, vlan_id, pppoe_username)
        conn.commit()


def _quarantine(cur, reason, raw_payload, vlan_id=None, pppoe_username=None):
    cur.execute(
        """
        SELECT 1 FROM ingest_quarantine
        WHERE reason = %s
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


# ---- Site management (no code or SQL needed to add a site) ----

LIVENESS_SOURCES = ("pppoe", "ping", "activity")

# Columns update_site may touch. A whitelist, because the field names come
# from a request body and are interpolated into the SET clause.
_EDITABLE = ("name", "location_id", "vlan_id", "pppoe_username", "liveness_source", "notes", "is_active")


class SiteConflict(Exception):
    """A unique or foreign-key rule refused the change; the message is safe to show."""


def get_inbox():
    """Things the router reported that Ops has no site for — the to-do list
    for whoever adds sites. Only the two kinds a person can act on."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT id, reason, vlan_id, pppoe_username, received_at
            FROM ingest_quarantine
            WHERE resolved = false AND reason IN ('unknown_pppoe_user', 'unknown_vlan')
            ORDER BY received_at DESC, id DESC
            """
        )
        return [
            {"id": r[0], "reason": r[1], "vlan_id": r[2], "pppoe_username": r[3], "seen_at": utc_iso(r[4])}
            for r in cur.fetchall()
        ]


def dismiss_inbox_item(item_id):
    """Marks one item as not-a-site (a home customer, say). Returns False if
    it does not exist or was already handled."""
    with db_cursor() as (conn, cur):
        cur.execute(
            "UPDATE ingest_quarantine SET resolved = true, resolved_at = now() WHERE id = %s AND resolved = false",
            (item_id,),
        )
        changed = cur.rowcount
        conn.commit()
    return changed > 0


# A closed inbox item was either dismissed or turned into a site. The two look
# identical in the table (resolved = true), so "dismissed" is defined by what
# is true NOW: nothing in monitored_sites owns that name or VLAN.
_NOT_OWNED = """
    NOT EXISTS (SELECT 1 FROM monitored_sites ms
                WHERE (q.reason = 'unknown_pppoe_user' AND ms.pppoe_username = q.pppoe_username)
                   OR (q.reason = 'unknown_vlan' AND ms.vlan_id = q.vlan_id))
"""


def get_dismissed():
    """Inbox items someone marked 'not a site', newest first, so a mistake can be undone."""
    with db_cursor() as (conn, cur):
        cur.execute(
            f"""
            SELECT DISTINCT ON (q.reason, q.vlan_id, q.pppoe_username)
                   q.id, q.reason, q.vlan_id, q.pppoe_username, q.resolved_at
            FROM ingest_quarantine q
            WHERE q.resolved = true
              AND q.reason IN ('unknown_pppoe_user', 'unknown_vlan')
              AND {_NOT_OWNED}
            ORDER BY q.reason, q.vlan_id, q.pppoe_username, q.resolved_at DESC NULLS LAST
            """
        )
        rows = [
            {"id": r[0], "reason": r[1], "vlan_id": r[2], "pppoe_username": r[3], "dismissed_at": utc_iso(r[4])}
            for r in cur.fetchall()
        ]
    rows.sort(key=lambda r: r["dismissed_at"] or "", reverse=True)
    return rows


def restore_inbox_item(item_id):
    """Puts a dismissed item back in the inbox. False if it is not a
    dismissed item (unknown id, still open, or now owned by a site)."""
    with db_cursor() as (conn, cur):
        cur.execute(
            f"""
            UPDATE ingest_quarantine q SET resolved = false, resolved_at = NULL
            WHERE q.id = %s AND q.resolved = true AND {_NOT_OWNED}
              AND NOT EXISTS (SELECT 1 FROM ingest_quarantine o
                              WHERE o.resolved = false AND o.reason = q.reason
                                AND o.vlan_id IS NOT DISTINCT FROM q.vlan_id
                                AND o.pppoe_username IS NOT DISTINCT FROM q.pppoe_username)
            """,
            (item_id,),
        )
        changed = cur.rowcount
        conn.commit()
    return changed > 0


def list_managed_sites():
    """Every monitored site including switched-off ones — the status view
    hides those, and a manager needs to be able to switch one back on."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT ms.id, ms.name, ms.location_id, l.name, ms.vlan_id, ms.pppoe_username,
                   ms.liveness_source, ms.notes, ms.is_active
            FROM monitored_sites ms LEFT JOIN locations l ON l.id = ms.location_id
            ORDER BY ms.is_active DESC, COALESCE(l.name, ms.name, 'VLAN ' || ms.vlan_id)
            """
        )
        return [
            {"id": r[0], "name": r[1], "location_id": r[2], "location_name": r[3], "vlan_id": r[4],
             "pppoe_username": r[5], "liveness_source": r[6], "notes": r[7], "is_active": r[8]}
            for r in cur.fetchall()
        ]


def _translate(err):
    """Turns a constraint failure into something a person can act on."""
    detail = str(err)
    if isinstance(err, psycopg2.errors.UniqueViolation):
        if "pppoe_username" in detail:
            return "Another site already uses that PPPoE username"
        if "vlan_id" in detail:
            return "Another site already uses that VLAN"
        return "That conflicts with an existing site"
    if isinstance(err, psycopg2.errors.ForeignKeyViolation):
        return "That linked site does not exist"
    return "The database refused that change"


def create_site(fields, inbox_item_id=None):
    """Adds a monitored site. Any unresolved inbox item that this makes
    known (same PPPoE name or VLAN) is closed in the same transaction, so it
    leaves the inbox the moment the site exists."""
    with db_cursor() as (conn, cur):
        try:
            cur.execute(
                """
                INSERT INTO monitored_sites
                    (name, location_id, vlan_id, pppoe_username, liveness_source, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (fields.get("name"), fields.get("location_id"), fields.get("vlan_id"),
                 fields.get("pppoe_username"), fields["liveness_source"], fields.get("notes")),
            )
        except (psycopg2.errors.UniqueViolation, psycopg2.errors.ForeignKeyViolation) as err:
            conn.rollback()
            raise SiteConflict(_translate(err))
        site_id = cur.fetchone()[0]
        cur.execute(
            """
            UPDATE ingest_quarantine SET resolved = true, resolved_at = now()
            WHERE resolved = false AND (
                (reason = 'unknown_pppoe_user' AND pppoe_username = %s)
                OR (reason = 'unknown_vlan' AND vlan_id = %s)
                OR id = %s
            )
            """,
            (fields.get("pppoe_username"), fields.get("vlan_id"), inbox_item_id),
        )
        conn.commit()
    return site_id


def update_site(site_id, fields):
    """Applies only the whitelisted fields present in `fields`. Returns False
    if the site does not exist."""
    changes = {k: v for k, v in fields.items() if k in _EDITABLE}
    if not changes:
        return True
    assignments = ", ".join(f"{col} = %s" for col in changes)
    with db_cursor() as (conn, cur):
        try:
            cur.execute(
                f"UPDATE monitored_sites SET {assignments} WHERE id = %s",
                (*changes.values(), site_id),
            )
        except (psycopg2.errors.UniqueViolation, psycopg2.errors.ForeignKeyViolation) as err:
            conn.rollback()
            raise SiteConflict(_translate(err))
        found = cur.rowcount > 0
        if found:
            # A PPPoE site with no username would report Down forever.
            cur.execute(
                "SELECT 1 FROM monitored_sites WHERE id = %s AND liveness_source = 'pppoe' AND pppoe_username IS NULL",
                (site_id,),
            )
            if cur.fetchone():
                conn.rollback()
                raise SiteConflict("A PPPoE site needs its PPPoE username")
        conn.commit()
    return found


def list_linkable_locations():
    """Sites from the Sites list that a monitored site can be linked to."""
    with db_cursor() as (conn, cur):
        cur.execute("SELECT id, name FROM locations ORDER BY name")
        return [{"id": r[0], "name": r[1]} for r in cur.fetchall()]
