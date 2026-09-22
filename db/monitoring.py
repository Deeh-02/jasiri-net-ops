import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
import psycopg2
from psycopg2.extras import Json
from db.connection import EAT, db_cursor, utc_iso, now_eat
# The raw-row horizon is a retention fact, not a monitoring one — imported
# rather than restated so a change to retention can't leave this page
# claiming a peak it no longer has the data for.
from db.monitoring_retention import SESSION_RAW_KEEP

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


# How far past one package's length an expiry may jump before it is worth a
# person's attention. A single purchase moves the expiry by roughly one
# duration; several bought together between two polls move it by several, and
# collapse into ONE revenue_event because (username, expiry) is one pair
# however many payments made it.
#
# 1.8 rather than something tighter because the jump is measured from the
# previous expiry, and a lapsed pass adds dead time on top of the duration.
# That dead time is small in practice — a renewal can only happen to a user
# billing has NOT yet deleted — but it is not zero, and it is measured in a
# timezone (the billing system's) that Ops has never established. Everything
# here stays inside that one timezone, subtracting two of its own timestamps,
# so the unknown offset cancels and never has to be guessed.
STACK_SUSPECT_RATIO = 1.8


def _flag_possible_stack(cur, user, previous_expiry, package, snapshot_id):
    """Records a renewal whose expiry jumped far enough to be several
    purchases, WITHOUT changing what is booked.

    Ops deliberately does not multiply the money here. Inferring "this jump
    is 3 x KES 10" means inventing revenue from an arithmetic guess, and an
    over-count is far more damaging than the under-count it would fix: a
    total that is quietly too high is trusted until something expensive
    depends on it. So the sale is recorded as one, exactly as before, and the
    jump is put in front of a person who can tell three purchases from one
    lapsed pass at a glance. If these turn out to be real and regular, the
    multiplication can be turned on later with evidence behind it rather than
    an assumption."""
    duration = package.get("duration") if package else None
    if not duration or duration <= 0 or previous_expiry is None or user["expiry"] is None:
        return
    jump_minutes = (user["expiry"] - previous_expiry).total_seconds() / 60
    if jump_minutes < duration * STACK_SUSPECT_RATIO:
        return
    # A small dict, not raw_payload: the real payload is ~25 KB of user list,
    # and what makes this reviewable is these six fields.
    _quarantine(cur, "revenue_possible_stack", {
        "hotspot_username": user["name"],
        "profile_name": user["profile"],
        "previous_expiry": previous_expiry.isoformat(),
        "new_expiry": user["expiry"].isoformat(),
        "jump_minutes": round(jump_minutes),
        "package_minutes": duration,
        "implied_purchases": round(jump_minutes / duration),
        "snapshot_id": snapshot_id,
    }, pppoe_username=user["name"])


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
    cur.execute("SELECT profile_name, price_kes, is_active, duration_minutes FROM hotspot_packages")
    packages = {
        r[0]: {"price": r[1], "is_active": r[2], "duration": r[3]}
        for r in cur.fetchall()
    }

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
    latest_expiry = {}
    for username, expiry, site_id in cur.fetchall():
        known_pairs.add((username, expiry))
        seen_before.add(username)
        if site_id is not None:
            last_site.setdefault(username, site_id)
        if expiry is not None and (username not in latest_expiry or expiry > latest_expiry[username]):
            latest_expiry[username] = expiry

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

        # Recorded whether or not it resolves to a site — that is the whole
        # point. An unplaced sale used to be untraceable: the VLAN lived only
        # in this dict, for the length of this request. See migration 0010.
        origin_vlan = name_to_vlan.get(user["name"])
        site_id = vlan_to_site.get(origin_vlan)
        attribution = "direct"
        if site_id is None:
            site_id = last_site.get(user["name"])
            attribution = "inferred"

        if is_baseline:
            event_type = "baseline"
        else:
            event_type = "renewal" if user["name"] in seen_before else "sale"

        if event_type == "renewal":
            _flag_possible_stack(cur, user, latest_expiry.get(user["name"]), package, snapshot_id)

        cur.execute(
            """
            INSERT INTO revenue_events
                (monitored_site_id, hotspot_username, profile_name, price_kes,
                 event_type, attribution, expiry_seen, snapshot_id, origin_vlan_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotspot_username, expiry_seen) DO NOTHING
            """,
            (site_id, user["name"], user["profile"], price,
             event_type, attribution, user["expiry"], snapshot_id, origin_vlan),
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
                # Back online ends any acknowledgement, in the same
                # transaction as the recovery itself: an acknowledgement is
                # for ONE outage, and the next drop must alert fresh.
                if state == "online":
                    cur.execute(
                        """
                        UPDATE site_acknowledgements SET cleared_at = now()
                        WHERE monitored_site_id = %s AND cleared_at IS NULL
                        """,
                        (site["id"],),
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


def _eat_date_to_utc(d):
    """A calendar date's EAT midnight, as the naive UTC datetime the tables
    store — the general form of _eat_day_start_utc (today only), used to
    anchor a specific reporting day/month rather than "now"."""
    return datetime(d.year, d.month, d.day, tzinfo=EAT).astimezone(timezone.utc).replace(tzinfo=None)


def _eat_day_start_utc():
    """Start of today in EAT, as the naive UTC datetime the tables store."""
    return _eat_date_to_utc(now_eat().date())


def _resolve_window(month=None, week=None, day=None):
    """Resolves the one report window a request names into one shape.

    day="YYYY-MM-DD": that EAT calendar day (00:00–24:00 Nairobi).
    week="YYYY-MM-DD": the Monday–Sunday EAT week starting that Monday.
    month="YYYY-MM": that EAT calendar month.
    None of them: the current week.

    Both are fixed calendar periods, not a trailing "last N days" — a period
    you can name is one you can report against and come back to later and
    get the same answer. A period still in progress is capped at today: its
    remaining days must not be zero-filled as if they were empty, which
    would read as "nothing happened" rather than "hasn't happened yet".

    Returns (since, until, first_day, day_count). since/until are UTC
    instants bounding every point-in-time query — until matters as much as
    since: without it a past period quietly picks up everything after it.
    first_day/day_count are the EAT calendar range for daily bucketing."""
    now = datetime.utcnow()
    today = now_eat().date()
    if month:
        year, mon = (int(x) for x in month.split("-"))
        first_day = date(year, mon, 1)
        end_day = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    elif day:
        first_day = date.fromisoformat(day)
        end_day = first_day + timedelta(days=1)
    else:
        first_day = date.fromisoformat(week) if week else today - timedelta(days=today.weekday())
        end_day = first_day + timedelta(days=7)
    since = _eat_date_to_utc(first_day)
    until = min(now, _eat_date_to_utc(end_day))
    day_count = max((min(end_day, today + timedelta(days=1)) - first_day).days, 1)
    return since, until, first_day, day_count


def week_or_none(month, day, first_day):
    """The response's "week" key: the Monday it resolved to, when the
    request was a week (explicit or the current-week default)."""
    return None if (month or day) else first_day.isoformat()


def _data_from(cur, site_id=None):
    """The first EAT day anything was recorded — for one site, or (site_id
    None) the fleet — so the UI only offers days, weeks and months that can
    have something in them. Nothing is ever deleted outright (retention
    folds old rows into hourly ones, see monitoring_retention.py), so this
    stays put. None when nothing has been recorded at all."""
    if site_id is None:
        cur.execute("SELECT min(received_at) FROM ingest_snapshots")
    else:
        cur.execute(
            """
            SELECT least(
                (SELECT min(received_at) FROM site_session_counts WHERE monitored_site_id = %s),
                (SELECT min(received_at) FROM site_status_log WHERE monitored_site_id = %s),
                (SELECT min(first_seen_at) FROM revenue_events WHERE monitored_site_id = %s))
            """,
            (site_id, site_id, site_id),
        )
    first = cur.fetchone()[0]
    return (first + timedelta(hours=3)).date().isoformat() if first else None


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


# The Status table's uptime column. Fixed rather than a parameter: the page
# is "right now", and the site report is where a window gets chosen.
UPTIME_WINDOW = timedelta(days=30)


def get_fleet_activity():
    """When anything last changed, and when the last outage ended — what a
    calm Status page says instead of showing an empty problems block. An
    outage ends at an 'online' row whose previous row was offline or
    flapping, for the same site."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            WITH log AS (
                SELECT sl.state, sl.received_at,
                       lag(sl.state) OVER (PARTITION BY sl.monitored_site_id
                                           ORDER BY sl.received_at, sl.id) AS prev
                FROM site_status_log sl
                JOIN monitored_sites ms ON ms.id = sl.monitored_site_id AND ms.is_active
            )
            SELECT max(received_at),
                   max(received_at) FILTER (WHERE state = 'online' AND prev IN ('offline', 'flapping'))
            FROM log
            """
        )
        last_change, last_closed = cur.fetchone()
    return {"last_change_at": utc_iso(last_change), "last_incident_closed_at": utc_iso(last_closed)}


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
                   sc.sessions, sc.received_at, ms.pppoe_username
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

        # How many people were on a down site at the last count BEFORE it
        # dropped. Its current count is 0 by definition, which says nothing;
        # this is the number that decides whether anyone drives out tonight.
        cur.execute(
            """
            SELECT DISTINCT ON (st.monitored_site_id) st.monitored_site_id, sc.sessions
            FROM (
                SELECT DISTINCT ON (monitored_site_id) monitored_site_id, state, received_at
                FROM site_status_log
                ORDER BY monitored_site_id, received_at DESC, id DESC
            ) st
            JOIN site_session_counts sc
              ON sc.monitored_site_id = st.monitored_site_id AND sc.received_at < st.received_at
            WHERE st.state = 'offline'
            ORDER BY st.monitored_site_id, sc.received_at DESC, sc.id DESC
            """
        )
        sessions_at_drop = dict(cur.fetchall())

        cur.execute(
            """
            SELECT a.monitored_site_id, a.note, u.name, a.acknowledged_at
            FROM site_acknowledgements a LEFT JOIN users u ON u.id = a.acknowledged_by
            WHERE a.cleared_at IS NULL
            """
        )
        acks = {r[0]: {"note": r[1], "by": r[2], "at": utc_iso(r[3])} for r in cur.fetchall()}

        now = datetime.utcnow()
        month_ago = now - UPTIME_WINDOW
        uptime_by_site = {}
        for r in rows:
            if r[4] == "pppoe":
                summary = _uptime_summary(_state_segments(cur, r[0], month_ago, now), r[4])
                uptime_by_site[r[0]] = summary["uptime_pct"]

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
            "pppoe_username": r[10],
            "sessions_at_drop": sessions_at_drop.get(r[0]),
            # None for an activity site: it cannot report Down, so any
            # percentage would be a meaningless 100%.
            "uptime_30d_pct": uptime_by_site.get(r[0]),
            # Present only while acknowledged; the state itself is untouched.
            "ack": acks.get(r[0]),
        }
        if include_revenue:
            today_kes, today_sales = revenue_by_site.get(r[0], (0.0, 0))
            site["revenue_today_kes"] = today_kes
            site["sales_today"] = today_sales
        sites.append(site)
    return sites


class AckConflict(Exception):
    """The acknowledgement can't be made or cleared; the message is safe to show."""


def acknowledge_site(site_id, user_id, note):
    """Marks a Down or Flapping site as known. Refused for a site that is
    online — there is nothing to acknowledge, and a standing acknowledgement
    would silently mute its next outage."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT state FROM site_status_log WHERE monitored_site_id = %s
            ORDER BY received_at DESC, id DESC LIMIT 1
            """,
            (site_id,),
        )
        row = cur.fetchone()
        if row is None or row[0] not in ("offline", "flapping"):
            raise AckConflict("Only a site that is down or flapping can be acknowledged")
        try:
            cur.execute(
                """
                INSERT INTO site_acknowledgements (monitored_site_id, note, acknowledged_by)
                VALUES (%s, %s, %s)
                """,
                (site_id, note, user_id),
            )
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            raise AckConflict("This site is already acknowledged")
        conn.commit()


def clear_acknowledgement(site_id, user_id):
    """Un-acknowledge. False when there was nothing in force to clear."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            UPDATE site_acknowledgements SET cleared_at = now(), cleared_by = %s
            WHERE monitored_site_id = %s AND cleared_at IS NULL
            """,
            (user_id, site_id),
        )
        cleared = cur.rowcount > 0
        conn.commit()
    return cleared


def get_unplaced_revenue_today():
    """Today's money Ops detected but could not place at any site.

    A sale is recorded the moment it is seen, whether or not the site it came
    from can be worked out — monitored_site_id is nullable for exactly that
    reason. The per-site figures above deliberately exclude these rows, and
    must: a site's number has to be its own or it is not a site's number.

    But the HEADLINE total is the answer to "what did we take today", and it
    was built by summing the per-site figures, so every unplaced sale silently
    vanished from it. On 2026-09-22 that read ~250 KES light against the
    billing system, and the only way to see why was to query the table by
    hand. The total now includes these, and the card says how much of it is
    unplaced — a number that is quietly wrong is worse than one that explains
    itself."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT COALESCE(sum(price_kes), 0), count(*)
            FROM revenue_events
            WHERE first_seen_at >= %s AND monitored_site_id IS NULL
              AND event_type <> 'baseline'
            """,
            (_eat_day_start_utc(),),
        )
        row = cur.fetchone()
    return {"kes": float(row[0]), "sales": row[1]}


def _state_segments(cur, site_id, since, now):
    """Walks site_status_log into contiguous (state, start, end) periods
    covering [since, now]. Seeded with the transition immediately before
    `since`, if any, so the segment touching the window's left edge carries
    the state the site was actually in rather than starting from a false
    "unknown" — site_status_log is transitions-only, so without a seed the
    window would open mid-state with no way to say which one."""
    cur.execute(
        """
        SELECT state, received_at FROM site_status_log
        WHERE monitored_site_id = %s AND received_at <= %s
        ORDER BY received_at DESC, id DESC LIMIT 1
        """,
        (site_id, since),
    )
    seed = cur.fetchone()
    cur.execute(
        """
        SELECT state, received_at FROM site_status_log
        WHERE monitored_site_id = %s AND received_at > %s AND received_at < %s
        ORDER BY received_at ASC, id ASC
        """,
        (site_id, since, now),
    )
    rows = cur.fetchall()

    segments = []
    if seed is not None:
        state, start = seed[0], since
    elif rows:
        # No transition before the window at all — the site's first-ever
        # known state falls inside it. The stretch before that is genuinely
        # unrecorded, not "unknown" in the derived-state sense, so it is
        # left out of coverage rather than guessed at.
        state, start = rows[0][0], rows[0][1]
        rows = rows[1:]
    else:
        return []  # No data in or before the window at all.

    for next_state, at in rows:
        segments.append({"state": state, "start": start, "end": at})
        state, start = next_state, at
    segments.append({"state": state, "start": start, "end": now})
    return segments


def _uptime_summary(segments, liveness_source):
    """Uptime% only means something where "offline" is a real signal —
    liveness_source == 'activity' can only ever report online/unknown, so a
    site on that source would show a meaningless 100%. Outages are always
    returned (empty for a non-pppoe site is itself the honest answer).

    Two rules the UI depends on, both about not overstating what we know:
    unknown time is NEVER in the uptime fraction (not-watched is not
    downtime), and flapping counts as up — a bouncing site is on balance
    reachable, and it is called out by its own count rather than being
    folded into the outage total."""
    outages = []
    totals = {"online": 0.0, "offline": 0.0, "flapping": 0.0, "unknown": 0.0}
    flap_count = 0
    last = len(segments) - 1
    for i, seg in enumerate(segments):
        length = (seg["end"] - seg["start"]).total_seconds()
        totals[seg["state"]] = totals.get(seg["state"], 0.0) + length
        if seg["state"] == "flapping":
            flap_count += 1
        elif seg["state"] == "offline":
            outages.append({
                "start": utc_iso(seg["start"]),
                "end": None if i == last else utc_iso(seg["end"]),
                "duration_seconds": int(length),
                "ongoing": i == last,
            })
    outages.reverse()  # most recent first
    up = totals["online"] + totals["flapping"]
    known = up + totals["offline"]
    uptime_pct = None
    if liveness_source == "pppoe" and known > 0:
        uptime_pct = round(up / known * 100, 2)
    return {
        "uptime_pct": uptime_pct,
        "downtime_seconds": int(totals["offline"]),
        "unknown_seconds": int(totals["unknown"]),
        "flapping_seconds": int(totals["flapping"]),
        "flap_count": flap_count,
        "outages": outages,
        "longest_outage_seconds": max((o["duration_seconds"] for o in outages), default=0),
        "timeline": [
            {"state": s["state"], "start": utc_iso(s["start"]), "end": utc_iso(s["end"]),
             "duration_seconds": int((s["end"] - s["start"]).total_seconds())}
            for s in segments
        ],
    }


# A heartbeat is due every 60s, so three missed beats is the same "we have
# stopped being told" threshold the Status page uses (STALE_MS in status.js).
HEARTBEAT_GAP = timedelta(minutes=3)


def _coverage(cur, since, now, watching_since):
    """How much of the window Ops was being told anything at all.

    Sourced from ingest_snapshots, which exists for exactly this — see that
    table's comment in migration 0006: a gap between heartbeats is provably
    time nobody was watching, as distinct from time when everything was up.
    Time before the site was added counts as unwatched too, rather than
    being quietly treated as covered.

    This is deliberately NOT the same thing as a site's derived 'unknown'
    state. That one means the site reported and there was nothing to
    conclude; this one means nothing reported at all."""
    start = max(since, watching_since) if watching_since else since
    total = (now - start).total_seconds()
    if total <= 0:
        return {"watched_seconds": 0, "unwatched_seconds": 0, "pct": None, "since": utc_iso(start)}
    cur.execute(
        """
        WITH beats AS (
            SELECT received_at, lag(received_at) OVER (ORDER BY received_at) AS prev
            FROM ingest_snapshots WHERE received_at >= %s AND received_at < %s
        )
        SELECT COALESCE(sum(EXTRACT(EPOCH FROM (received_at - prev)))
                        FILTER (WHERE received_at - prev > %s), 0),
               min(received_at), max(received_at)
        FROM beats
        """,
        (start, now, HEARTBEAT_GAP),
    )
    gap_s, first_beat, last_beat = cur.fetchone()
    if first_beat is None:
        unwatched = total
    else:
        unwatched = float(gap_s or 0)
        # The edges are gaps too — silence before the first beat and after
        # the last one — but only past the same threshold, or a heartbeat
        # that landed 20 seconds ago would read as a hole.
        lead = (first_beat - start).total_seconds()
        tail = (now - last_beat).total_seconds()
        if lead > HEARTBEAT_GAP.total_seconds():
            unwatched += lead
        if tail > HEARTBEAT_GAP.total_seconds():
            unwatched += tail
    unwatched = min(max(unwatched, 0.0), total)
    watched = total - unwatched
    return {
        "watched_seconds": int(watched),
        "unwatched_seconds": int(unwatched),
        "pct": round(watched / total * 100, 1),
        "since": utc_iso(start),
    }


def _people_hourly(cur, site_id, since, until):
    """One bucket per hour, carrying both the average and the true peak.

    bool_or(granularity='raw') is the honesty flag: past SESSION_RAW_KEEP
    the per-minute rows are gone and only hourly AVERAGES survive (see
    monitoring_retention.py), so 'peak' for those hours is not a peak at
    all. The UI must not draw a peak marker where this is false."""
    cur.execute(
        """
        SELECT date_trunc('hour', received_at) AS hour,
               round(avg(sessions))::int, max(sessions),
               bool_or(granularity = 'raw')
        FROM site_session_counts
        WHERE monitored_site_id = %s AND received_at >= %s AND received_at < %s
        GROUP BY hour ORDER BY hour
        """,
        (site_id, since, until),
    )
    return [
        {"hour": utc_iso(r[0]), "avg": r[1], "peak": r[2], "has_peak": r[3]}
        for r in cur.fetchall()
    ]


def _busiest_hours(cur, site_id, since, until):
    """Average people by hour of the EAT day — the one chart here anyone
    schedules anything around. Fixed +3h shift, same reasoning as the daily
    revenue buckets."""
    cur.execute(
        """
        SELECT EXTRACT(HOUR FROM (received_at + interval '3 hours'))::int AS eat_hour,
               round(avg(sessions))::int
        FROM site_session_counts
        WHERE monitored_site_id = %s AND received_at >= %s AND received_at < %s
        GROUP BY eat_hour
        """,
        (site_id, since, until),
    )
    by_hour = dict(cur.fetchall())
    return [{"hour": h, "avg": by_hour.get(h, 0)} for h in range(24)]


def _revenue_hourly(cur, site_id, since, until, tracked_from):
    """One bucket per hour of a Day report — revenue_daily's one bar per
    day is exactly one bar for a single day, not a chart. site_id None sums
    the whole fleet, placed or not. Zero-filled so a quiet hour still gets
    its own bar; an hour before revenue tracking began is untracked, not
    KES 0. EAT is a whole-hour offset, so UTC hours are EAT hours."""
    site_filter = "" if site_id is None else "monitored_site_id = %s AND"
    cur.execute(
        f"""
        SELECT date_trunc('hour', first_seen_at) AS hour,
               COALESCE(sum(price_kes), 0), count(*)
        FROM revenue_events
        WHERE {site_filter} event_type <> 'baseline'
          AND first_seen_at >= %s AND first_seen_at < %s
        GROUP BY hour ORDER BY hour
        """,
        ((site_id,) if site_id is not None else ()) + (since, until),
    )
    by_hour = {r[0]: (float(r[1]), r[2]) for r in cur.fetchall()}
    hours = []
    cursor = since
    while cursor < until:
        kes, sales = by_hour.get(cursor, (0.0, 0))
        hours.append({
            "hour": utc_iso(cursor), "kes": kes, "sales": sales,
            "tracked": tracked_from is not None and cursor + timedelta(hours=1) > tracked_from,
        })
        cursor += timedelta(hours=1)
    return hours


def _people_daily(cur, site_id, first_day, day_count, watching_since):
    """One bucket per EAT calendar day, from first_day for day_count days —
    the People chart's unit. Same day boundary as
    revenue_daily, so the two line up when read side by side. has_peak
    requires every session-count row that day to still be 'raw'; the moment
    any hour behind the average has rolled up to an hourly average (see
    monitoring_retention.py), the day's true peak is gone, not just
    diminished, and must not be drawn as a whole one."""
    since = _eat_date_to_utc(first_day)
    cur.execute(
        """
        SELECT (received_at + interval '3 hours')::date AS day,
               round(avg(sessions))::int, max(sessions), bool_and(granularity = 'raw')
        FROM site_session_counts
        WHERE monitored_site_id = %s AND received_at >= %s AND received_at < %s
        GROUP BY day ORDER BY day
        """,
        (site_id, since, _eat_date_to_utc(first_day + timedelta(days=day_count))),
    )
    by_day = {r[0].isoformat(): r[1:] for r in cur.fetchall()}
    # A day before the site existed is not a quiet day — nobody was counting.
    watched_from = (watching_since + timedelta(hours=3)).date() if watching_since else None
    out = []
    cursor_day = first_day
    for _ in range(day_count):
        avg_v, peak_v, all_raw = by_day.get(cursor_day.isoformat(), (None, None, False))
        out.append({
            "day": cursor_day.isoformat(),
            "avg": avg_v,
            "peak": peak_v if all_raw else None,
            "has_peak": bool(all_raw and peak_v is not None),
            "watched": watched_from is not None and cursor_day >= watched_from,
        })
        cursor_day += timedelta(days=1)
    return out


def get_site_detail(site_id, include_revenue, month=None, week=None, day=None):
    """One site's status history, session counts and (only with
    include_revenue) revenue, over one EAT calendar `day`, `week` or `month` — see
    _resolve_window. None if the id
    is unknown. See phase.md 4.0: "is this site up, how many devices are on
    it, what has its uptime been, and what did it earn" — this endpoint is
    that question asked of one site."""
    since, until, first_day, day_count = _resolve_window(month, week, day)
    revenue_hourly = None
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT ms.id, ms.location_id, COALESCE(l.name, ms.name, 'VLAN ' || ms.vlan_id),
                   ms.vlan_id, ms.liveness_source, ms.notes, ms.is_active,
                   ms.pppoe_username, ms.created_at,
                   l.contact_name, l.contact_phone, l.address
            FROM monitored_sites ms LEFT JOIN locations l ON l.id = ms.location_id
            WHERE ms.id = %s
            """,
            (site_id,),
        )
        head = cur.fetchone()
        if head is None:
            return None
        liveness_source, watching_since = head[4], head[8]

        segments = _state_segments(cur, site_id, since, until)
        uptime = _uptime_summary(segments, liveness_source)
        state = segments[-1]["state"] if segments else "unknown"
        state_since = utc_iso(segments[-1]["start"]) if segments else None
        coverage = _coverage(cur, since, until, watching_since)
        # A Day report charts these hourly rows directly; a week or a month
        # charts people_daily instead (a bar per hour over that many days is
        # too thin to read) and uses the hourly rows only for average/peak.
        people = _people_hourly(cur, site_id, since, until)
        people_daily = _people_daily(cur, site_id, first_day, day_count, watching_since)
        busiest_hours = _busiest_hours(cur, site_id, since, until)
        data_from = _data_from(cur, site_id)

        cur.execute(
            """
            SELECT state, source, received_at FROM site_status_log
            WHERE monitored_site_id = %s AND received_at > %s AND received_at < %s
            ORDER BY received_at DESC, id DESC LIMIT 200
            """,
            (site_id, since, until),
        )
        history = [{"state": r[0], "source": r[1], "at": utc_iso(r[2])} for r in cur.fetchall()]

        events = None
        revenue_daily = None
        packages = None
        attribution = None
        revenue_tracking_since = None
        if include_revenue:
            # since/until are already whole EAT days (a week or a month
            # always is), so revenue shares the window exactly.
            cur.execute(
                """
                SELECT hotspot_username, profile_name, price_kes, event_type, attribution, first_seen_at
                FROM revenue_events
                WHERE monitored_site_id = %s AND event_type <> 'baseline'
                  AND first_seen_at >= %s AND first_seen_at < %s
                ORDER BY first_seen_at DESC LIMIT 50
                """,
                (site_id, since, until),
            )
            events = [
                {"username": r[0], "profile": r[1], "price_kes": float(r[2]),
                 "event_type": r[3], "attribution": r[4], "at": utc_iso(r[5])}
                for r in cur.fetchall()
            ]
            # EAT is a fixed +3h offset (no DST — see db/connection.py), so a
            # plain interval shift is enough to bucket by the day Ops and the
            # owner actually mean, not the UTC day the row is stored under.
            cur.execute(
                """
                SELECT (first_seen_at + interval '3 hours')::date AS day,
                       COALESCE(sum(price_kes), 0), count(*)
                FROM revenue_events
                WHERE monitored_site_id = %s AND event_type <> 'baseline'
                  AND first_seen_at >= %s AND first_seen_at < %s
                GROUP BY day ORDER BY day
                """,
                (site_id, since, until),
            )
            by_day = {r[0].isoformat(): (float(r[1]), r[2]) for r in cur.fetchall()}
            # When revenue tracking began at all, fleet-wide. A day before
            # this is not a KES 0 day — it is a day nobody was counting, and
            # the chart has to say so rather than draw an empty bar.
            cur.execute("SELECT min(first_seen_at) FROM revenue_events")
            tracked_from = cur.fetchone()[0]
            revenue_tracking_since = utc_iso(tracked_from)
            tracked_day = (tracked_from + timedelta(hours=3)).date() if tracked_from else None
            # Zero-filled so the chart has one bar per day even on days with
            # no sales, rather than compressing gaps out of the timeline.
            revenue_daily = []
            cursor_day = first_day
            for _ in range(day_count):
                key = cursor_day.isoformat()
                kes, sales = by_day.get(key, (0.0, 0))
                revenue_daily.append({
                    "day": key, "kes": kes, "sales": sales,
                    "tracked": tracked_day is not None and cursor_day >= tracked_day,
                })
                cursor_day += timedelta(days=1)
            if day:
                revenue_hourly = _revenue_hourly(cur, site_id, since, until, tracked_from)

            cur.execute(
                """
                SELECT profile_name, COALESCE(sum(price_kes), 0), count(*)
                FROM revenue_events
                WHERE monitored_site_id = %s AND event_type <> 'baseline'
                  AND first_seen_at >= %s AND first_seen_at < %s
                GROUP BY profile_name ORDER BY sum(price_kes) DESC, count(*) DESC
                """,
                (site_id, since, until),
            )
            packages = [{"profile": r[0], "kes": float(r[1]), "sales": r[2]} for r in cur.fetchall()]

            # How much of this site's money it actually watched arrive, versus
            # money assigned from the buyer's last known site. A high inferred
            # share is not an error, but it is a reason to trust the figure
            # less — so it is shown rather than averaged away.
            cur.execute(
                """
                SELECT attribution, count(*), COALESCE(sum(price_kes), 0)
                FROM revenue_events
                WHERE monitored_site_id = %s AND event_type <> 'baseline'
                  AND first_seen_at >= %s AND first_seen_at < %s
                GROUP BY attribution
                """,
                (site_id, since, until),
            )
            attribution = {r[0]: {"sales": r[1], "kes": float(r[2])} for r in cur.fetchall()}

    avgs = [p["avg"] for p in people]
    peaks = [p["peak"] for p in people if p["has_peak"]]
    detail = {
        "id": head[0], "location_id": head[1], "name": head[2], "vlan_id": head[3],
        "liveness_source": liveness_source, "notes": head[5], "is_active": head[6],
        "pppoe_username": head[7], "watching_since": utc_iso(watching_since),
        "contact_name": head[9], "contact_phone": head[10], "address": head[11],
        "month": month, "week": week_or_none(month, day, first_day), "day": day, "days": day_count,
        "since": utc_iso(since), "until": utc_iso(until), "data_from": data_from,
        "state": state, "state_since": state_since,
        "coverage": coverage, "people": people, "people_daily": people_daily, "busiest_hours": busiest_hours,
        "avg_people": round(sum(avgs) / len(avgs)) if avgs else None,
        "peak_people": max(peaks) if peaks else None,
        # Past SESSION_RAW_KEEP only hourly averages survive, so a peak
        # simply does not exist for the older part of a long window. The UI
        # prints an em-dash there rather than passing off an average as one.
        "peak_horizon_days": SESSION_RAW_KEEP.days,
        "history": history,
        **uptime,
    }
    if include_revenue:
        detail["revenue_events"] = events
        detail["revenue_daily"] = revenue_daily
        if revenue_hourly is not None:
            detail["revenue_hourly"] = revenue_hourly
        detail["revenue_packages"] = packages
        detail["revenue_attribution"] = attribution
        detail["revenue_tracking_since"] = revenue_tracking_since
    return detail


def _fleet_people_daily(cur, first_day, day_count):
    """Fleet 'People across the fleet' chart's unit — one
    bucket per EAT day, each site's daily average summed into the fleet
    figure. Same 'sum of site averages' convention the hourly version below
    already uses; has_peak requires every contributing site-day to still be
    raw, same honesty rule as _people_daily."""
    since = _eat_date_to_utc(first_day)
    cur.execute(
        """
        SELECT (received_at + interval '3 hours')::date AS day, monitored_site_id,
               avg(sessions), max(sessions), bool_and(granularity = 'raw')
        FROM site_session_counts
        WHERE received_at >= %s AND received_at < %s
        GROUP BY day, monitored_site_id
        """,
        (since, _eat_date_to_utc(first_day + timedelta(days=day_count))),
    )
    by_day = {}
    for day, _site_id, avg, peak, all_raw in cur.fetchall():
        b = by_day.setdefault(day.isoformat(), {"avg": 0.0, "peak": 0, "has_peak": True})
        b["avg"] += float(avg)
        b["peak"] += peak
        b["has_peak"] = b["has_peak"] and all_raw
    out = []
    cursor_day = first_day
    for _ in range(day_count):
        hit = by_day.get(cursor_day.isoformat())
        out.append({
            "day": cursor_day.isoformat(),
            "avg": round(hit["avg"]) if hit else None,
            "peak": hit["peak"] if hit and hit["has_peak"] else None,
            "has_peak": bool(hit and hit["has_peak"]),
        })
        cursor_day += timedelta(days=1)
    return out


def get_fleet(include_revenue, month=None, week=None, day=None):
    """Every active site over one EAT calendar `day`, `week` or `month` — see
    _resolve_window — for the Trends tab:
    the site report's numbers, asked of the whole fleet at once so sites can
    be ranked against each other. Same rules as get_site_detail throughout:
    unwatched time is never downtime, an activity site gets no uptime
    percentage, a peak exists only inside the raw-row horizon, and revenue
    keys are absent (not null) without include_revenue — on every row AND on
    the unattributed line, which belongs to no row and so is the one most
    likely to leak."""
    since, until, first_day, day_count = _resolve_window(month, week, day)
    with db_cursor() as (conn, cur):
        data_from = _data_from(cur)
        cur.execute(
            """
            SELECT ms.id, COALESCE(l.name, ms.name, 'VLAN ' || ms.vlan_id), ms.vlan_id,
                   ms.liveness_source, ms.created_at
            FROM monitored_sites ms LEFT JOIN locations l ON l.id = ms.location_id
            WHERE ms.is_active
            ORDER BY 2
            """
        )
        heads = cur.fetchall()

        # One coverage figure serves every site watched for the whole window;
        # only a site added part-way through needs its own.
        window_coverage = _coverage(cur, since, until, None)

        cur.execute(
            """
            SELECT monitored_site_id, date_trunc('hour', received_at) AS hour,
                   avg(sessions), max(sessions), bool_or(granularity = 'raw')
            FROM site_session_counts
            WHERE received_at >= %s AND received_at < %s
            GROUP BY monitored_site_id, hour
            """,
            (since, until),
        )
        hourly = cur.fetchall()

        sites = []
        for site_id, name, vlan_id, liveness_source, created_at in heads:
            segments = _state_segments(cur, site_id, since, until)
            uptime = _uptime_summary(segments, liveness_source)
            young = created_at is not None and created_at > since
            coverage = _coverage(cur, since, until, created_at) if young else window_coverage
            online_seconds = sum(
                s["duration_seconds"] for s in uptime["timeline"] if s["state"] == "online")
            site_hours = [h for h in hourly if h[0] == site_id]
            avgs = [float(h[2]) for h in site_hours]
            peaks = [h[3] for h in site_hours if h[4]]
            sites.append({
                "id": site_id, "name": name, "vlan_id": vlan_id,
                "liveness_source": liveness_source,
                "state": segments[-1]["state"] if segments else "unknown",
                "watching_since": utc_iso(created_at),
                "uptime_pct": uptime["uptime_pct"],
                "outages": len(uptime["outages"]),
                "longest_outage_seconds": uptime["longest_outage_seconds"],
                "flap_count": uptime["flap_count"],
                # For an activity site "online" means "had people on it" —
                # the one thing it can honestly report instead of uptime.
                "online_seconds": online_seconds,
                "coverage_pct": coverage["pct"],
                "window_seconds": coverage["watched_seconds"] + coverage["unwatched_seconds"],
                "avg_people": round(sum(avgs) / len(avgs)) if avgs else None,
                "peak_people": max(peaks) if peaks else None,
            })

        # People across the fleet, hour by hour: the sum of each site's hourly
        # average. A peak is only claimed where every contributing row is raw.
        fleet_hours = {}
        for _, hour, avg, peak, is_raw in hourly:
            bucket = fleet_hours.setdefault(hour, {"avg": 0.0, "peak": 0, "has_peak": True})
            bucket["avg"] += float(avg)
            bucket["peak"] += peak
            bucket["has_peak"] = bucket["has_peak"] and is_raw
        people = [
            {"hour": utc_iso(h), "avg": round(b["avg"]),
             "peak": b["peak"] if b["has_peak"] else None, "has_peak": b["has_peak"]}
            for h, b in sorted(fleet_hours.items())
        ]
        # The chart is per day, same as the site report's; the hourly sums
        # above only feed the fleet peak.
        people_daily = _fleet_people_daily(cur, first_day, day_count)

        revenue = None
        if include_revenue:
            cur.execute(
                """
                SELECT monitored_site_id, COALESCE(sum(price_kes), 0), count(*),
                       count(*) FILTER (WHERE attribution = 'direct')
                FROM revenue_events
                WHERE event_type <> 'baseline' AND first_seen_at >= %s AND first_seen_at < %s
                GROUP BY monitored_site_id
                """,
                (since, until),
            )
            by_site = {r[0]: (float(r[1]), r[2], r[3]) for r in cur.fetchall()}
            cur.execute(
                """
                SELECT (first_seen_at + interval '3 hours')::date AS day,
                       COALESCE(sum(price_kes), 0), count(*)
                FROM revenue_events
                WHERE event_type <> 'baseline' AND first_seen_at >= %s AND first_seen_at < %s
                GROUP BY day
                """,
                (since, until),
            )
            by_day = {r[0].isoformat(): (float(r[1]), r[2]) for r in cur.fetchall()}
            cur.execute("SELECT min(first_seen_at) FROM revenue_events")
            tracked_from = cur.fetchone()[0]
            tracked_day = (tracked_from + timedelta(hours=3)).date() if tracked_from else None
            daily = []
            cursor_day = first_day
            for _ in range(day_count):
                kes, sales = by_day.get(cursor_day.isoformat(), (0.0, 0))
                daily.append({"day": cursor_day.isoformat(), "kes": kes, "sales": sales,
                              "tracked": tracked_day is not None and cursor_day >= tracked_day})
                cursor_day += timedelta(days=1)
            revenue = {"by_site": by_site, "daily": daily, "tracking_since": utc_iso(tracked_from),
                       "hourly": _revenue_hourly(cur, None, since, until, tracked_from) if day else None}

    for site in sites:
        if revenue is not None:
            kes, sales, direct = revenue["by_site"].get(site["id"], (0.0, 0, 0))
            site["revenue_kes"] = kes
            site["sales_count"] = sales
            site["direct_sales"] = direct

    measured = [s for s in sites if s["liveness_source"] == "pppoe"]
    up_s = known_s = 0.0
    for s in measured:
        # Rebuilt from the percentage and the watched time, weighting each
        # site by how long it was actually watched rather than 1:1.
        if s["uptime_pct"] is not None:
            watched = s["window_seconds"] * (s["coverage_pct"] or 0) / 100
            up_s += watched * s["uptime_pct"] / 100
            known_s += watched
    total_window = sum(s["window_seconds"] for s in sites)
    watched_total = sum(s["window_seconds"] * (s["coverage_pct"] or 0) / 100 for s in sites)
    peak_hour = max((p for p in people if p["has_peak"]), key=lambda p: p["peak"], default=None)

    result = {
        "month": month,
        "week": week_or_none(month, day, first_day),
        "day": day,
        "days": day_count,
        "since": utc_iso(since),
        "until": utc_iso(until),
        "data_from": data_from,
        "peak_horizon_days": SESSION_RAW_KEEP.days,
        "sites": sites,
        "people": people,
        "people_daily": people_daily,
        "totals": {
            "uptime_pct": round(up_s / known_s * 100, 1) if known_s else None,
            "coverage_pct": round(watched_total / total_window * 100, 1) if total_window else None,
            "thin_coverage_sites": sum(1 for s in sites if (s["coverage_pct"] or 0) < 90),
            "sites_with_outage": sum(1 for s in measured if s["outages"]),
            "measured_sites": len(measured),
            "activity_sites": len(sites) - len(measured),
            "avg_people": sum(s["avg_people"] or 0 for s in sites),
            "peak_people": peak_hour["peak"] if peak_hour else None,
            "peak_at": peak_hour["hour"] if peak_hour else None,
        },
    }
    if revenue is not None:
        unattributed = revenue["by_site"].get(None, (0.0, 0, 0))
        result["unattributed_revenue_kes"] = unattributed[0]
        result["unattributed_sales_count"] = unattributed[1]
        result["revenue_daily"] = revenue["daily"]
        if revenue["hourly"] is not None:
            result["revenue_hourly"] = revenue["hourly"]
        result["revenue_tracking_since"] = revenue["tracking_since"]
        result["totals"]["revenue_kes"] = sum(v[0] for v in revenue["by_site"].values())
        result["totals"]["sales_count"] = sum(v[1] for v in revenue["by_site"].values())
    return result


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
                   COALESCE(u.sold, 0), u.last_seen_at, hp.duration_minutes
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
                # NULL means "not stated". Nothing guesses one from the name:
                # "Full day pass30" and "24hr pass40" are both about a day.
                "duration_minutes": r[8],
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

        # Renewals whose expiry jumped further than one package's length —
        # possibly several purchases booked as one. Recorded, never applied;
        # see _flag_possible_stack.
        cur.execute(
            """
            SELECT id, raw_payload, received_at FROM ingest_quarantine
            WHERE reason = 'revenue_possible_stack' AND resolved = false
            ORDER BY received_at DESC LIMIT 50
            """
        )
        stacks = [
            {"id": r[0], **(r[1] or {}), "seen_at": utc_iso(r[2])}
            for r in cur.fetchall()
        ]
    return {"packages": packages, "unpriced": unpriced, "stacks": stacks}


_PACKAGE_EDITABLE = ("price_kes", "is_comped", "is_active", "notes", "duration_minutes")


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
