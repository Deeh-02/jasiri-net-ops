import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
import psycopg2
from psycopg2.extras import Json
from db.connection import db_cursor, utc_iso, now_eat

# site_session_counts is written at most once per site per this window. It was
# 5 minutes (0006's header explains the storage reasoning), but that left the
# Status headcount up to 5 minutes behind the router — visibly wrong when
# compared with Winbox. The hotspot is itself 1-2 minutes stale
# (keepalive-timeout=2m), so once per heartbeat is as fresh as the number can
# honestly be. 50s rather than 60s: heartbeats jitter, and a run landing at
# 59s would otherwise be skipped and alternate the gap to 2 minutes.
# The extra rows are paid for by SESSION_RAW_KEEP in monitoring_retention.py.
SESSION_COUNT_INTERVAL = timedelta(seconds=50)

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


def _parse_expiry(text):
    """The `Exp:` value the billing system writes into a hotspot user's
    comment. Its timezone is still unestablished (PHASES.md Correction 5),
    which is why it is only ever used as an IDENTITY for one purchase — a
    renewal is 'the Exp moved forward' — and never as the time a sale is
    counted at. That is first_seen_at, which is Ops' own clock."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _clean_users(users):
    """Malformed user entries are dropped individually rather than failing the
    snapshot: liveness matters more than revenue, and the whole payload is
    quarantined if this raises."""
    if not isinstance(users, list):
        return []
    cleaned = []
    for u in users:
        if not isinstance(u, dict):
            continue
        name = u.get("n")
        profile = u.get("p")
        if not isinstance(name, str) or not name or not isinstance(profile, str):
            continue
        cleaned.append({"name": name, "profile": profile, "expiry": _parse_expiry(u.get("e"))})
    return cleaned


def _record_revenue(cur, users, active_by_vlan, vlan_to_site, snapshot_id, raw_payload):
    """Turns the router's hotspot-user list into revenue_events.

    A user is identified by (username, Exp:). A pair never seen before is
    money: a SALE if that username is new, a RENEWAL if its Exp moved
    forward. The UNIQUE index on the pair is what makes a re-reported user
    free — the same list arrives every few minutes and only changes insert.

    THE FIRST RUN IS THE DANGEROUS ONE. Every one of the 340 existing users
    looks new, which would book a day's worth of fake sales. So while
    revenue_events is empty the whole list is written as 'baseline' at price
    0: it establishes what already existed, and only movement after that is
    revenue. Baseline rows are excluded from every revenue total."""
    if not users:
        return
    cur.execute("SELECT profile_name, price_kes, is_active FROM hotspot_packages")
    packages = {r[0]: {"price": r[1], "is_active": r[2]} for r in cur.fetchall()}

    cur.execute("SELECT EXISTS (SELECT 1 FROM revenue_events)")
    is_baseline = not cur.fetchone()[0]

    names = [u["name"] for u in users]
    cur.execute(
        """
        SELECT hotspot_username, expiry_seen, monitored_site_id
        FROM revenue_events WHERE hotspot_username = ANY(%s)
        ORDER BY first_seen_at DESC, id DESC
        """,
        (names,),
    )
    known_pairs = set()
    seen_before = set()
    last_site = {}
    for username, expiry, site_id in cur.fetchall():
        known_pairs.add((username, expiry))
        seen_before.add(username)
        if site_id is not None:
            last_site.setdefault(username, site_id)

    # username -> vlan, from the same active list the session counts come from.
    name_to_vlan = {}
    for vlan_id, active_names in active_by_vlan.items():
        for name in active_names:
            name_to_vlan[name] = vlan_id

    for user in users:
        if user["expiry"] is None:
            # NULLs are distinct in a UNIQUE index, so a user with no readable
            # Exp would re-insert on every single poll. Skipped, and recorded
            # once so a site billing without comments is visible rather than
            # silently worth nothing. Reason is outside get_inbox()'s two, so
            # this never reaches the Manage Sites inbox.
            _quarantine(cur, "hotspot_user_no_expiry", raw_payload, pppoe_username=user["name"])
            continue
        if (user["name"], user["expiry"]) in known_pairs:
            continue

        package = packages.get(user["profile"])
        if package is None:
            # A profile Ops has never heard of. Recorded at 0 rather than
            # guessed at, and flagged so someone can price it.
            _quarantine(cur, "unknown_hotspot_profile", raw_payload, pppoe_username=user["profile"])
        price = 0 if (is_baseline or package is None or not package["is_active"]) else package["price"]

        site_id = vlan_to_site.get(name_to_vlan.get(user["name"]))
        attribution = "direct"
        if site_id is None:
            site_id = last_site.get(user["name"])
            attribution = "inferred"

        if is_baseline:
            event_type = "baseline"
        else:
            event_type = "renewal" if user["name"] in seen_before else "sale"

        cur.execute(
            """
            INSERT INTO revenue_events
                (monitored_site_id, hotspot_username, profile_name, price_kes,
                 event_type, attribution, expiry_seen, snapshot_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotspot_username, expiry_seen) DO NOTHING
            """,
            (site_id, user["name"], user["profile"], price,
             event_type, attribution, user["expiry"], snapshot_id),
        )


def ingest_snapshot(snapshot, raw_payload, router_ts, router_ts_utc, offset_minutes):
    """Stores one already-validated fleet snapshot in a single transaction.
    Returns "duplicate" for a retry, else "stored"."""
    digest = payload_hash(snapshot)
    sessions_by_vlan = {s["vlan_id"]: s["sessions"] for s in snapshot["sites"]}
    pppoe_online = set(snapshot["pppoe"])
    # Optional (4.7): the heartbeat carries the hotspot user list only every
    # few runs, so an absent key means "nothing to say about revenue this
    # time", never "there are no users".
    raw_users = snapshot.get("users")
    users = _clean_users(raw_users)
    # What the ROUTER sent, not what survived cleaning — this column answers
    # "did a customer list arrive", and a list that arrived full of garbage is
    # a different failure from one that never came. NULL (key absent) is the
    # normal state 4 runs out of 5; only the age of the newest non-NULL
    # matters. See migration 0009.
    users_reported = len(raw_users) if isinstance(raw_users, list) else None
    active_by_vlan = {}
    for entry in snapshot.get("active") or []:
        if isinstance(entry, dict) and isinstance(entry.get("v"), int) and isinstance(entry.get("n"), str):
            active_by_vlan.setdefault(entry["v"], []).append(entry["n"])

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
                (seq, router_ts, router_gmt_offset_minutes, router_ts_utc, sites_reporting,
                 payload_hash, users_reported)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (snapshot.get("seq"), router_ts, offset_minutes, router_ts_utc, len(sessions_by_vlan),
             digest, users_reported),
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

        vlan_to_site = {s["vlan_id"]: s["id"] for s in sites if s["vlan_id"] is not None}
        _record_revenue(cur, users, active_by_vlan, vlan_to_site, snapshot_id, raw_payload)

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


def get_revenue_feed():
    """When a customer list last reached Ops, and how big it was.

    This is the answer to the failure that prompted it: every other signal on
    the Status page stayed green while revenue detection was dead, because
    heartbeats kept arriving — they just stopped carrying the user list. A
    revenue total cannot distinguish "nobody bought anything" from "nobody
    told us"; this can. See migration 0009."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT received_at, users_reported FROM ingest_snapshots
            WHERE users_reported IS NOT NULL
            ORDER BY received_at DESC LIMIT 1
            """
        )
        row = cur.fetchone()
    # Nothing ever, or nothing since the column was added — either way the UI
    # says "waiting" rather than claiming an age it cannot support.
    if row is None:
        return {"last_users_at": None, "users_reported": None}
    return {"last_users_at": utc_iso(row[0]), "users_reported": row[1]}


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
                  AND event_type <> 'baseline'
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
                FROM revenue_events WHERE monitored_site_id = %s AND event_type <> 'baseline'
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


# ---- Packages ----
# The price list is the one piece of revenue that Ops cannot observe. The
# router reports WHICH package was sold; what it is worth lives only here, and
# only because a person typed it. Both ways that can silently go wrong — a
# profile Ops has never heard of, and a price that no longer matches what
# billing charges — are surfaced by list_packages() rather than left to be
# noticed in a total that looks a bit low.


def _price_from_name(profile_name):
    """The price the profile NAME claims: 'Quick Surf10' -> 10, '3day pass70'
    -> 70. None where the name ends in no digits ('default', 'hp support
    users') — that is a name that says nothing about price, not a conflict.
    Only ever compared against the stored price, never used as one: the
    vendor owns these names and could stop encoding prices in them tomorrow."""
    match = re.search(r"(\d+)$", profile_name or "")
    return int(match.group(1)) if match else None


def list_packages():
    """Every package Ops can price, plus every profile the router has actually
    reported that Ops has NO row for. That second list is the point of this
    screen: an unpriced profile is recorded at 0 and quarantined under a
    reason the sites inbox deliberately ignores, so without this it is
    invisible — real sales, counted as free, with no warning anywhere."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT hp.id, hp.profile_name, hp.price_kes, hp.is_comped, hp.is_active, hp.notes,
                   COALESCE(u.sold, 0), u.last_seen_at
            FROM hotspot_packages hp
            LEFT JOIN (
                SELECT profile_name,
                       count(*) FILTER (WHERE event_type <> 'baseline') AS sold,
                       max(first_seen_at) AS last_seen_at
                FROM revenue_events GROUP BY profile_name
            ) u ON u.profile_name = hp.profile_name
            ORDER BY hp.is_active DESC, hp.price_kes DESC, hp.profile_name
            """
        )
        packages = []
        for r in cur.fetchall():
            price = float(r[2] or 0)
            claimed = _price_from_name(r[1])
            packages.append({
                "id": r[0], "profile_name": r[1], "price_kes": price,
                "is_comped": r[3], "is_active": r[4], "notes": r[5],
                "sold": r[6], "last_seen_at": utc_iso(r[7]),
                # Shown as a question, never auto-applied — a comped package
                # priced 0 whose name ends in a number is a legitimate state.
                "name_price_kes": claimed,
                "price_disagrees": claimed is not None and not r[3] and claimed != price,
            })

        # Straight from what was actually recorded, not from the quarantine:
        # revenue_events.profile_name is stored exactly as the router said it,
        # so this catches a rename the moment the first sale lands.
        cur.execute(
            """
            SELECT re.profile_name, count(*), max(re.first_seen_at)
            FROM revenue_events re
            WHERE NOT EXISTS (SELECT 1 FROM hotspot_packages hp WHERE hp.profile_name = re.profile_name)
            GROUP BY re.profile_name
            ORDER BY max(re.first_seen_at) DESC
            """
        )
        unpriced = [
            {"profile_name": r[0], "seen": r[1], "last_seen_at": utc_iso(r[2]),
             "name_price_kes": _price_from_name(r[0])}
            for r in cur.fetchall()
        ]
    return {"packages": packages, "unpriced": unpriced}


_PACKAGE_EDITABLE = ("price_kes", "is_comped", "is_active", "notes")


def create_package(fields):
    """Prices a profile Ops has not seen before. Does NOT retro-price the
    sales already recorded at 0 under that name: revenue_events.price_kes is
    a snapshot of the price at the time of sale (see migration 0006), and
    rewriting history from a later edit is exactly what that snapshot exists
    to prevent. The fix for those rows is a decision, not a side effect."""
    with db_cursor() as (conn, cur):
        try:
            cur.execute(
                """
                INSERT INTO hotspot_packages (profile_name, price_kes, is_comped, notes)
                VALUES (%s, %s, %s, %s) RETURNING id
                """,
                (fields["profile_name"], fields.get("price_kes") or 0,
                 bool(fields.get("is_comped")), fields.get("notes")),
            )
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            raise SiteConflict("A package with that profile name already exists")
        package_id = cur.fetchone()[0]
        conn.commit()
    return package_id


def update_package(package_id, fields):
    """Applies only the whitelisted fields present. Returns False if the
    package does not exist. profile_name is deliberately NOT editable: it is
    the join key to what the router reports, and renaming it here would
    orphan the history rather than move it."""
    changes = {k: v for k, v in fields.items() if k in _PACKAGE_EDITABLE}
    if not changes:
        return True
    assignments = ", ".join(f"{col} = %s" for col in changes)
    with db_cursor() as (conn, cur):
        cur.execute(
            f"UPDATE hotspot_packages SET {assignments} WHERE id = %s",
            (*changes.values(), package_id),
        )
        found = cur.rowcount > 0
        conn.commit()
    return found
