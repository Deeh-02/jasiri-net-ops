"""Every SQL statement Network Health runs, over its own nh_ tables, plus
ONE read of OPS's VLAN list (monitored_sites + locations) for area names:
the owner's call, 2026-11-21 ("use the VLAN/area list OPS already has").

Functions that take `cur` run inside the caller's transaction (service.py
evaluates a whole batch in one connection: Supabase's pooler is capped at
15 connections, see db/connection.py). The others open their own.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from psycopg2.extras import Json, execute_values

from db.connection import db_cursor

from network_health import config


@contextmanager
def transaction():
    """One connection, committed if the block finishes, rolled back if not."""
    with db_cursor() as (conn, cur):
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def now_utc():
    return datetime.now(timezone.utc)


def eat_midnight(now):
    """The start of today in Nairobi, as a UTC moment (the daily SMS cap)."""
    local = now + timedelta(hours=3)
    return datetime(local.year, local.month, local.day, tzinfo=timezone.utc) - timedelta(hours=3)


# ---- Storing a batch ----

READING_COLUMNS = ("ts", "kind", "from_ip", "raw", "load", "probes", "link", "speed", "route", "dns", "olt",
                   "crs_link", "area", "lanlink", "pon", "ont", "ppp")
_SET = ", ".join(f"{c} = EXCLUDED.{c}" for c in READING_COLUMNS)
_OURS = ", ".join(f"nh_readings.{c}" for c in READING_COLUMNS)
_THEIRS = ", ".join(f"EXCLUDED.{c}" for c in READING_COLUMNS)


def save_batch(source, sent_at, records):
    """Stores the records and notes the post on nh_feed, in one transaction.

    UPSERT on key: a key already stored is REPLACED when its values differ
    (the shipper resends corrected lines after a rewind: the loss-scale fix
    of 21 Nov 2026) and left alone when they are the same (an ordinary
    resend). received_at stays the first arrival's.

    Returns (newly stored records, how many stored ones were replaced). Only
    the new ones are judged: a replaced reading is history, and the rules
    have already moved past it."""
    with transaction() as cur:
        stored_keys, replaced = set(), 0
        if records:
            rows = [(r["key"], r["ts"], r["kind"], r["from_ip"], r["raw"], _json(r["load"]), _json(r["probes"]),
                     _json(r["link"]), _json(r["speed"]), _json(r.get("route")), _json(r.get("dns")),
                     _json(r.get("olt")), _json(r.get("crs_link")), _json(r.get("area")), _json(r.get("lanlink")),
                     _json(r.get("pon")), _json(r.get("ont")), _json(r.get("ppp")))
                    for r in records]
            stored = execute_values(
                cur,
                f"""INSERT INTO nh_readings (key, {", ".join(READING_COLUMNS)}) VALUES %s
                    ON CONFLICT (key) DO UPDATE SET {_SET}
                    WHERE ({_OURS}) IS DISTINCT FROM ({_THEIRS})
                    RETURNING key, (xmax = 0) AS inserted""",
                rows, page_size=500, fetch=True)
            stored_keys = {key for key, inserted in stored if inserted}
            replaced = sum(1 for _, inserted in stored if not inserted)
            add_ont_hours(cur, [r for r in records if r["kind"] == "ont" and r["key"] in stored_keys])
        from_ip = next((r["from_ip"] for r in reversed(records) if r["from_ip"]), None)
        cur.execute(
            """UPDATE nh_feed SET last_post_at = now(), last_sent_at = %s, last_source = %s,
                      last_from_ip = COALESCE(%s, last_from_ip) WHERE id = 1""",
            (sent_at, source, from_ip))
    return [r for r in records if r["key"] in stored_keys], replaced


def _json(value):
    return None if value is None else Json(value)


def add_ont_hours(cur, records):
    """Adds new `ont` readings to nh_ont_hourly (per ONT per hour: the sums
    and counts of rx_dbm and olt_rx_dbm), the light history the sparklines
    and the degradation check read. Only NEW readings: a resent one is
    never counted twice."""
    hours = {}
    for record in records:
        ont = record["ont"]
        hour = record["ts"].replace(minute=0, second=0, microsecond=0)
        sums = hours.setdefault((ont["pon"], ont["ont_id"], hour), [0.0, 0, 0.0, 0])
        if ont["rx_dbm"] is not None:
            sums[0] += ont["rx_dbm"]
            sums[1] += 1
        if ont["olt_rx_dbm"] is not None:
            sums[2] += ont["olt_rx_dbm"]
            sums[3] += 1
    rows = [(*key, *sums) for key, sums in hours.items() if sums[1] or sums[3]]
    if rows:
        execute_values(
            cur,
            """INSERT INTO nh_ont_hourly (pon, ont_id, hour, rx_sum, rx_n, olt_rx_sum, olt_rx_n) VALUES %s
               ON CONFLICT (pon, ont_id, hour) DO UPDATE SET
                   rx_sum = nh_ont_hourly.rx_sum + EXCLUDED.rx_sum, rx_n = nh_ont_hourly.rx_n + EXCLUDED.rx_n,
                   olt_rx_sum = nh_ont_hourly.olt_rx_sum + EXCLUDED.olt_rx_sum,
                   olt_rx_n = nh_ont_hourly.olt_rx_n + EXCLUDED.olt_rx_n""",
            rows)


def delete_old_readings():
    """Raw readings older than KEEP_READINGS_DAYS go. `route` rows (the
    before/after of the provider switch) and `crs_link` rows (a few a week)
    are kept; `olt` rows go after KEEP_OLT_DAYS (the DAC swap's before/after
    and the restart list), `ont` rows after KEEP_ONT_DAYS (18,000 a day; the
    light history stays in nh_ont_hourly). The access network's own tables
    are trimmed too."""
    with transaction() as cur:
        cur.execute(
            """DELETE FROM nh_readings
               WHERE (kind NOT IN ('route', 'crs_link', 'olt', 'ont') AND ts < now() - %s * interval '1 day')
                  OR (kind = 'olt' AND ts < now() - %s * interval '1 day')
                  OR (kind = 'ont' AND ts < now() - %s * interval '1 day')""",
            (config.KEEP_READINGS_DAYS, config.KEEP_OLT_DAYS, config.KEEP_ONT_DAYS))
        deleted = cur.rowcount
        forget = (config.ACCESS_FORGET_DAYS,)
        for statement, days in (
                ("DELETE FROM nh_ont_hourly WHERE hour < now() - %s * interval '1 day'", (config.KEEP_ONT_HOURLY_DAYS,)),
                ("DELETE FROM nh_access_events WHERE at < now() - %s * interval '1 day'",
                 (config.KEEP_ACCESS_EVENTS_DAYS,)),
                ("DELETE FROM nh_onts WHERE last_seen_at < now() - %s * interval '1 day'", forget),
                ("DELETE FROM nh_ppp_sessions WHERE last_seen_at < now() - %s * interval '1 day'", forget)):
            cur.execute(statement, days)
            deleted += cur.rowcount
        return deleted


# ---- Reading readings ----

def readings(cur, kinds, since, until):
    """Readings of these kinds with since < ts <= until, oldest first."""
    cur.execute(
        """SELECT ts, kind, load, probes, link, speed, dns FROM nh_readings
           WHERE kind = ANY(%s) AND ts > %s AND ts <= %s ORDER BY ts""",
        (list(kinds), since, until))
    return [{"ts": r[0], "kind": r[1], "load": r[2], "probes": r[3], "link": r[4], "speed": r[5], "dns": r[6]}
            for r in cur.fetchall()]


def previous_reading(cur, kind, before):
    """The latest reading of this kind strictly before a moment, or None."""
    cur.execute(
        "SELECT ts, kind, load, probes, link, speed, dns FROM nh_readings WHERE kind = %s AND ts < %s ORDER BY ts DESC LIMIT 1",
        (kind, before))
    r = cur.fetchone()
    return None if r is None else {"ts": r[0], "kind": r[1], "load": r[2], "probes": r[3], "link": r[4], "speed": r[5],
                                   "dns": r[6]}


def last_received_at(cur, kind):
    """When OPS last stored a reading of this kind (not the reading's ts)."""
    cur.execute("SELECT max(received_at) FROM nh_readings WHERE kind = %s", (kind,))
    return cur.fetchone()[0]


def feed(cur):
    cur.execute("SELECT last_post_at, last_sent_at, last_source, last_from_ip FROM nh_feed WHERE id = 1")
    row = cur.fetchone() or (None, None, None, None)
    return {"last_post_at": row[0], "last_sent_at": row[1], "source": row[2], "from_ip": row[3]}


def route_records(since):
    """Every `route` record since a moment, oldest first: {ts, route}."""
    with transaction() as cur:
        cur.execute("SELECT ts, route FROM nh_readings WHERE kind = 'route' AND route IS NOT NULL AND ts >= %s "
                    "ORDER BY ts", (since,))
        return [{"ts": row[0], "route": row[1]} for row in cur.fetchall()]


# ---- Inside network (`olt`, `crs_link`) ----

def olt_readings(cur, since, until):
    """`olt` readings with since < ts <= until, oldest first: {ts, olt}."""
    cur.execute("SELECT ts, olt FROM nh_readings WHERE kind = 'olt' AND ts > %s AND ts <= %s ORDER BY ts",
                (since, until))
    return [{"ts": row[0], "olt": row[1] or {}} for row in cur.fetchall()]


def last_olt_success_received(cur):
    """When OPS received the newest COMPLETE `olt` reading (every SNMP read
    worked). Received, not its ts: a backfill must not look like silence."""
    cur.execute(
        """SELECT received_at FROM nh_readings
           WHERE kind = 'olt' AND olt->>'uptime_s' IS NOT NULL AND olt->>'up_rx' IS NOT NULL
             AND olt->>'up_crc' IS NOT NULL
           ORDER BY ts DESC LIMIT 1""")
    row = cur.fetchone()
    return row[0] if row else None


def olt_restarts(cur, since):
    """OLT restarts since a moment, newest first. The same rule as
    inside.walk_olt: uptime lower than the last reading that HAD an uptime
    (readings with a failed uptime read are left out before comparing)."""
    cur.execute(
        """SELECT ts, up, prev_up FROM (
               SELECT ts, (olt->>'uptime_s')::float8 AS up,
                      lag((olt->>'uptime_s')::float8) OVER (ORDER BY ts) AS prev_up
               FROM nh_readings WHERE kind = 'olt' AND ts >= %s AND olt->>'uptime_s' IS NOT NULL) x
           WHERE up < prev_up ORDER BY ts DESC""",
        (since,))
    return [{"detected_at": ts, "booted_at": ts - timedelta(seconds=up), "previous_uptime_s": prev_up, "uptime_s": up}
            for ts, up, prev_up in cur.fetchall()]


def olt_last_reads(cur):
    """(ts of the newest `olt` reading, ts of the newest complete one).
    Both walk the (kind, ts) index from the newest end."""
    cur.execute("SELECT max(ts) FROM nh_readings WHERE kind = 'olt'")
    last_poll = cur.fetchone()[0]
    cur.execute(
        """SELECT ts FROM nh_readings
           WHERE kind = 'olt' AND olt->>'uptime_s' IS NOT NULL AND olt->>'up_rx' IS NOT NULL
             AND olt->>'up_crc' IS NOT NULL
           ORDER BY ts DESC LIMIT 1""")
    row = cur.fetchone()
    return last_poll, (row[0] if row else None)


def crs_link_records(cur, since):
    """`crs_link` records since a moment, oldest first: {ts, crs_link}."""
    cur.execute("SELECT ts, crs_link FROM nh_readings WHERE kind = 'crs_link' AND crs_link IS NOT NULL AND ts >= %s "
                "ORDER BY ts", (since,))
    return [{"ts": row[0], "crs_link": row[1]} for row in cur.fetchall()]


# ---- Areas (`area`), inside links (`lanlink`), to-box probes (`hop`) ----

def area_names(cur):
    """{vlan: name} from OPS's own VLAN list: the location's name, else the
    site's own name. Read only; Network Health never writes it."""
    cur.execute(
        """SELECT ms.vlan_id, COALESCE(l.name, ms.name) FROM monitored_sites ms
           LEFT JOIN locations l ON l.id = ms.location_id""")
    return {int(vlan): name for vlan, name in cur.fetchall() if name}


def area_readings(cur, since, until):
    """`area` readings with since < ts <= until, oldest first: {ts, area}."""
    cur.execute("SELECT ts, area FROM nh_readings WHERE kind = 'area' AND area IS NOT NULL AND ts > %s AND ts <= %s "
                "ORDER BY ts", (since, until))
    return [{"ts": row[0], "area": row[1]} for row in cur.fetchall()]


# Download Mbps between consecutive readings of one VLAN, in SQL (the page and
# the baselines). Same rule as areas.rates(): a counter that went down
# (reset) gives no figure.
_AREA_RATES = """
    SELECT vlan, ts, CASE WHEN d_down >= 0 THEN d_down * 8 / secs / 1e6 END AS down_mbps,
                     CASE WHEN d_up >= 0 THEN d_up * 8 / secs / 1e6 END AS up_mbps
    FROM (SELECT (area->>'vlan')::int AS vlan, ts,
                 (area->>'down_bytes')::numeric - lag((area->>'down_bytes')::numeric) OVER w AS d_down,
                 (area->>'up_bytes')::numeric - lag((area->>'up_bytes')::numeric) OVER w AS d_up,
                 extract(epoch FROM ts - lag(ts) OVER w) AS secs
          FROM nh_readings WHERE kind = 'area' AND area IS NOT NULL AND ts >= %(from)s AND ts <= %(until)s
          WINDOW w AS (PARTITION BY area->>'vlan' ORDER BY ts)) x
    WHERE secs > 0"""


def area_baselines(cur, since, until):
    """Per VLAN: {hour (EAT): median download Mbps} over [since, until), and
    the VLAN's first reading kept (for 'at least 2 days of history')."""
    cur.execute(
        f"""SELECT vlan, extract(hour FROM ts AT TIME ZONE 'Africa/Nairobi')::int,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY down_mbps)
            FROM ({_AREA_RATES}) r WHERE ts >= %(since)s AND ts < %(until)s AND down_mbps IS NOT NULL
            GROUP BY 1, 2""",
        {"from": since, "since": since, "until": until})
    medians = {}
    for vlan, hour, median in cur.fetchall():
        medians.setdefault(vlan, {})[hour] = float(median)
    cur.execute("SELECT (area->>'vlan')::int, min(ts) FROM nh_readings WHERE kind = 'area' AND area IS NOT NULL "
                "GROUP BY 1")
    return medians, dict(cur.fetchall())


def area_series(cur, since, bucket_seconds):
    """Per VLAN, download / upload Mbps averaged per bucket (the sparklines)."""
    cur.execute(
        f"""SELECT vlan, to_timestamp(floor(extract(epoch FROM ts) / %(bucket)s) * %(bucket)s) AS b,
                   avg(down_mbps), avg(up_mbps)
            FROM ({_AREA_RATES}) r WHERE ts > %(since)s GROUP BY 1, 2 ORDER BY 1, 2""",
        {"from": since - timedelta(minutes=30), "until": now_utc(), "since": since, "bucket": bucket_seconds})
    series = {}
    for vlan, bucket, down, up in cur.fetchall():
        series.setdefault(vlan, []).append({"t": bucket, "down": None if down is None else float(down),
                                            "up": None if up is None else float(up)})
    return series


def lanlink_readings(cur, since, until):
    """`lanlink` readings with since < ts <= until, oldest first: {ts, lanlink}."""
    cur.execute("SELECT ts, lanlink FROM nh_readings WHERE kind = 'lanlink' AND lanlink IS NOT NULL "
                "AND ts > %s AND ts <= %s ORDER BY ts", (since, until))
    return [{"ts": row[0], "lanlink": row[1]} for row in cur.fetchall()]


def hop_series(cur, since, bucket_seconds):
    """The to-box probes (`hop`), averaged per bucket like the other graphs."""
    cur.execute(
        f"""SELECT {BUCKET} AS b, p->>'name', avg((p->>'rtt_ms')::float8), avg((p->>'loss_pct')::float8), count(*)
            FROM nh_readings r CROSS JOIN LATERAL jsonb_array_elements(r.probes) p
            WHERE r.kind = 'hop' AND r.ts >= %(since)s GROUP BY b, 2 ORDER BY b""",
        {"since": since, "bucket": bucket_seconds})
    return [{"t": r[0], "name": r[1], "rtt": r[2], "loss": r[3], "n": r[4]} for r in cur.fetchall()]


# ---- Access network (`pon`, `ont`, `ppp`; nh_onts, nh_ppp_sessions, nh_access_events) ----

ONT_COLUMNS = ("pon", "ont_id", "descr", "status", "rx_dbm", "olt_rx_dbm", "distance_m", "last_down_cause",
               "last_down_text", "first_seen_at", "last_seen_at", "status_since", "ever_online", "last_online_at")


def load_onts(cur):
    """{(pon, ont_id): row} for every ONT OPS knows."""
    cur.execute(f"SELECT {', '.join(ONT_COLUMNS)} FROM nh_onts")
    return {(row[0], row[1]): dict(zip(ONT_COLUMNS, row)) for row in cur.fetchall()}


def save_onts(cur, onts):
    if not onts:
        return
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in ONT_COLUMNS[2:])
    execute_values(cur, f"""INSERT INTO nh_onts ({', '.join(ONT_COLUMNS)}) VALUES %s
                            ON CONFLICT (pon, ont_id) DO UPDATE SET {updates}""",
                   [tuple(ont[c] for c in ONT_COLUMNS) for ont in onts])


PPP_COLUMNS = ("present", "first_seen_at", "last_seen_at", "missing_since")


def load_ppp_sessions(cur):
    """{dev: {name: row}} for every PPPoE session OPS knows."""
    cur.execute(f"SELECT dev, name, {', '.join(PPP_COLUMNS)} FROM nh_ppp_sessions")
    sessions = {}
    for row in cur.fetchall():
        sessions.setdefault(row[0], {})[row[1]] = dict(zip(PPP_COLUMNS, row[2:]))
    return sessions


def save_ppp_sessions(cur, dev, sessions):
    if not sessions:
        return
    execute_values(cur, f"""INSERT INTO nh_ppp_sessions (dev, name, {', '.join(PPP_COLUMNS)}) VALUES %s
                            ON CONFLICT (dev, name) DO UPDATE SET present = EXCLUDED.present,
                                last_seen_at = EXCLUDED.last_seen_at, missing_since = EXCLUDED.missing_since""",
                   [(dev, name, *(s[c] for c in PPP_COLUMNS)) for name, s in sessions.items()])


def add_access_events(cur, events):
    """Stores ONT / PPPoE events. An ONT going down waits (grouped = false)
    until its poll is judged with the others on its PON."""
    if events:
        execute_values(
            cur,
            "INSERT INTO nh_access_events (at, kind, pon, ont_id, dev, name, detail, grouped) VALUES %s",
            [(e["at"], e["kind"], e.get("pon"), e.get("ont_id"), e.get("dev"), e.get("name"), Json(e.get("detail") or {}),
              e["kind"] != "ont_down") for e in events])


def ungrouped_downs(cur):
    """ONTs gone down whose poll hasn't been judged yet, oldest first."""
    cur.execute("""SELECT id, at, pon, ont_id, name, detail FROM nh_access_events
                   WHERE kind = 'ont_down' AND NOT grouped ORDER BY at""")
    return [{"id": r[0], "at": r[1], "pon": r[2], "ont_id": r[3], "name": r[4], "detail": r[5] or {}}
            for r in cur.fetchall()]


def mark_grouped(cur, marks):
    """marks: [(event id, what to add to its detail)]."""
    for event_id, extra in marks:
        cur.execute("UPDATE nh_access_events SET grouped = true, detail = detail || %s WHERE id = %s",
                    (Json(extra), event_id))


def ont_newest(cur):
    """(ts of the newest `ont` reading, when OPS received it)."""
    cur.execute("SELECT ts, received_at FROM nh_readings WHERE kind = 'ont' ORDER BY ts DESC LIMIT 1")
    row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def poll_counts(cur, pon, since, until):
    """In one poll on one PON: (ONTs online, ONT readings)."""
    cur.execute("""SELECT count(*) FILTER (WHERE ont->>'status' = 'online'), count(*) FROM nh_readings
                   WHERE kind = 'ont' AND ts >= %s AND ts <= %s AND ont->>'pon' = %s""", (since, until, pon))
    online, total = cur.fetchone()
    return online, total


def incident_facts(cur, incident_id):
    cur.execute("SELECT facts, started_at FROM nh_incidents WHERE id = %s", (incident_id,))
    row = cur.fetchone()
    return (row[0] or {}, row[1]) if row else ({}, None)


def ont_hours(cur, since, until):
    """Hourly average rx_dbm per ONT over [since, until): [{pon, ont_id, hour, rx}]."""
    cur.execute("""SELECT pon, ont_id, hour, rx_sum / rx_n FROM nh_ont_hourly
                   WHERE hour >= %s AND hour < %s AND rx_n > 0""", (since, until))
    return [{"pon": r[0], "ont_id": r[1], "hour": r[2], "rx": r[3]} for r in cur.fetchall()]


def ont_matches(cur):
    """The page's manual ONT <-> PPPoE matches: {ont key: ppp name or None}."""
    cur.execute("SELECT pon, ont_id, ppp_name FROM nh_ont_ppp_matches")
    return {f"{pon}:{ont_id}": name for pon, ont_id, name in cur.fetchall()}


def save_ont_match(pon, ont_id, ppp_name, user_id):
    with transaction() as cur:
        cur.execute("""INSERT INTO nh_ont_ppp_matches (pon, ont_id, ppp_name, updated_at, updated_by)
                       VALUES (%s, %s, %s, now(), %s)
                       ON CONFLICT (pon, ont_id) DO UPDATE SET ppp_name = EXCLUDED.ppp_name, updated_at = now(),
                           updated_by = EXCLUDED.updated_by""", (pon, ont_id, ppp_name, user_id))


def delete_ont_match(pon, ont_id):
    with transaction() as cur:
        cur.execute("DELETE FROM nh_ont_ppp_matches WHERE pon = %s AND ont_id = %s", (pon, ont_id))
        return cur.rowcount


def access_page(cur, now):
    """What the Access network section reads, in one go."""
    spark_since = now - timedelta(days=config.LIGHT_SPARK_DAYS)
    lookback = now - timedelta(days=config.ACCESS_LOOKBACK_DAYS)
    onts = load_onts(cur)
    sessions = load_ppp_sessions(cur)
    matches = ont_matches(cur)
    cur.execute("""SELECT DISTINCT ON (pon->>'pon') pon->>'pon', ts, (pon->>'online')::int, (pon->>'offline')::int
                   FROM nh_readings WHERE kind = 'pon' AND pon IS NOT NULL AND ts > %s
                   ORDER BY pon->>'pon', ts DESC""", (now - timedelta(minutes=30),))
    pons = {r[0]: {"ts": r[1], "online": r[2], "offline": r[3]} for r in cur.fetchall()}
    cur.execute("""SELECT DISTINCT ON (ppp->>'dev') ppp->>'dev', ts, (ppp->>'count')::int,
                          jsonb_array_length(ppp->'names')
                   FROM nh_readings WHERE kind = 'ppp' AND ppp IS NOT NULL AND ts > %s
                   ORDER BY ppp->>'dev', ts DESC""", (now - timedelta(days=1),))
    routers = {r[0]: {"ts": r[1], "count": r[2] if r[2] is not None else r[3]} for r in cur.fetchall()}
    cur.execute("""SELECT pon, ont_id, to_timestamp(floor(extract(epoch FROM hour) / %(step)s) * %(step)s),
                          sum(rx_sum) / nullif(sum(rx_n), 0), sum(olt_rx_sum) / nullif(sum(olt_rx_n), 0)
                   FROM nh_ont_hourly WHERE hour >= %(since)s GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""",
                {"step": config.LIGHT_SPARK_BUCKET_SECONDS, "since": spark_since})
    light = {}
    for pon, ont_id, bucket, rx, olt in cur.fetchall():
        light.setdefault(f"{pon}:{ont_id}", []).append((bucket, rx, olt))
    cur.execute("""SELECT at, kind, pon, ont_id, dev, name, detail FROM nh_access_events
                   WHERE at >= %s ORDER BY at DESC, id DESC LIMIT %s""", (lookback, config.ACCESS_EVENTS_LIMIT))
    events = [{"at": r[0], "kind": r[1], "pon": r[2], "ont_id": r[3], "dev": r[4], "name": r[5], "detail": r[6] or {}}
              for r in cur.fetchall()]
    cur.execute("""SELECT DISTINCT ON (pon) pon, at, kind, name, detail FROM nh_access_events
                   WHERE pon IS NOT NULL AND at >= %s ORDER BY pon, at DESC""", (lookback,))
    last_events = {r[0]: {"at": r[1], "kind": r[2], "name": r[3], "detail": r[4] or {}} for r in cur.fetchall()}
    cur.execute("""SELECT pon, ont_id, detail->>'cluster' FROM nh_access_events
                   WHERE kind = 'ont_down' AND at >= %s AND detail ? 'cluster'""", (lookback,))
    downs = [{"pon": r[0], "ont_id": r[1], "cluster": r[2]} for r in cur.fetchall()]
    links = crs_link_records(cur, now - timedelta(days=config.LIGHT_SPARK_DAYS))
    return {"onts": onts, "sessions": sessions, "matches": matches, "pons": pons, "routers": routers,
            "light": light, "spark_since": spark_since, "events": events, "last_events": last_events,
            "downs": downs, "links": links}


# ---- Baselines (the "usual" a reading is compared with) ----

def latency_baseline(cur, probe_name, since):
    """Median rtt per hour of day (EAT) since a moment, and the earliest
    reading in that span (the baseline counts only once it covers 7 days).
    Outages (rtt null) are left out of the median."""
    cur.execute(
        """SELECT extract(hour FROM (r.ts AT TIME ZONE 'UTC') + interval '3 hours')::int,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY (p->>'rtt_ms')::float8)
           FROM nh_readings r CROSS JOIN LATERAL jsonb_array_elements(r.probes) p
           WHERE r.kind = 'probe' AND r.ts >= %s AND p->>'name' = %s AND p->>'rtt_ms' IS NOT NULL
           GROUP BY 1""",
        (since, probe_name))
    medians = {int(hour): float(median) for hour, median in cur.fetchall()}
    cur.execute("SELECT min(ts) FROM nh_readings WHERE kind = 'probe' AND ts >= %s", (since,))
    return medians, cur.fetchone()[0]


def optic_baseline(cur, since, until):
    """Average rx power of the uplink SFP over a span, and how many readings."""
    cur.execute(
        """SELECT avg((link->>'rx_dbm')::float8), count(link->>'rx_dbm') FROM nh_readings
           WHERE kind = 'link' AND ts >= %s AND ts < %s""",
        (since, until))
    average, count = cur.fetchone()
    return (None if average is None else float(average)), count


def night_speed_baseline(cur, since, until):
    """Median of the finished 03:30 speed tests in a span, and how many."""
    cur.execute(
        """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY (speed->>'mbps')::float8), count(*)
           FROM nh_readings
           WHERE kind = 'speed' AND ts >= %s AND ts < %s AND speed->>'status' = 'finished'
             AND speed->>'mbps' IS NOT NULL
             AND extract(hour FROM (ts AT TIME ZONE 'UTC') + interval '3 hours') = %s""",
        (since, until, config.SPEED_JUDGED_HOUR_EAT))
    median, count = cur.fetchone()
    return (None if median is None else float(median)), count


# ---- Settings ----

def settings(cur):
    cur.execute("SELECT key, value FROM nh_settings")
    values = dict(cur.fetchall())
    try:
        capacity = float(values.get("uplink_capacity_mbps") or config.DEFAULT_UPLINK_CAPACITY_MBPS)
    except ValueError:
        capacity = float(config.DEFAULT_UPLINK_CAPACITY_MBPS)
    switched = values.get("provider_switched_at")
    return {"uplink_capacity_mbps": capacity,
            "provider_switched_at": datetime.fromisoformat(switched) if switched else None}


def save_settings(capacity_mbps, provider_switched_at, user_id):
    with transaction() as cur:
        for key, value in (("uplink_capacity_mbps", str(capacity_mbps)),
                           ("provider_switched_at", provider_switched_at.isoformat() if provider_switched_at else None)):
            cur.execute(
                """INSERT INTO nh_settings (key, value, updated_at, updated_by) VALUES (%s, %s, now(), %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(),
                          updated_by = EXCLUDED.updated_by""",
                (key, value, user_id))


# ---- Rule state ----

def load_states(cur):
    """Every rule's state. Call lock_evaluation() first: the tick and an
    ingest could otherwise judge at the same moment."""
    cur.execute(
        """SELECT device, rule, subject, bad_count, good_count, is_active, severity, incident_id, last_checked_at
           FROM nh_rule_state""")
    return {(r[0], r[1], r[2]): {"bad_count": r[3], "good_count": r[4], "is_active": r[5], "severity": r[6],
                                 "incident_id": r[7], "last_checked_at": r[8]} for r in cur.fetchall()}


def save_state(cur, key, state):
    device, rule, subject = key
    cur.execute(
        """INSERT INTO nh_rule_state (device, rule, subject, bad_count, good_count, is_active, severity, incident_id,
                                      last_checked_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (device, rule, subject) DO UPDATE SET
               bad_count = EXCLUDED.bad_count, good_count = EXCLUDED.good_count, is_active = EXCLUDED.is_active,
               severity = EXCLUDED.severity, incident_id = EXCLUDED.incident_id,
               last_checked_at = EXCLUDED.last_checked_at""",
        (device, rule, subject, state["bad_count"], state["good_count"], state["is_active"], state["severity"],
         state["incident_id"], state["last_checked_at"]))


def lock_evaluation(cur):
    """One evaluation at a time across every worker: a transaction-level
    advisory lock (released at commit). The number is this feature's own."""
    cur.execute("SELECT pg_advisory_xact_lock(724100)")


# ---- Incidents ----

def cleared_since(cur, key, since):
    """Did this rule clear at or after `since`? (fires again soon = flapping)"""
    device, rule, subject = key
    cur.execute(
        """SELECT 1 FROM nh_incidents WHERE device = %s AND rule = %s AND subject = %s
           AND ended_at IS NOT NULL AND ended_at >= %s LIMIT 1""",
        (device, rule, subject, since))
    return cur.fetchone() is not None


def open_incident(cur, check, at, is_flapping):
    cur.execute(
        """INSERT INTO nh_incidents (device, rule, subject, severity, started_at, peak_value, last_value, facts,
                                     is_flapping)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (check["device"], check["rule"], check["subject"], check["severity"], at, check["value"], check["value"],
         Json(check["facts"]), is_flapping))
    return cur.fetchone()[0]


def update_incident(cur, incident_id, check, is_lower_worse):
    """A still-bad check: its value is the latest, and the peak if worse.
    LEAST/GREATEST skip NULLs, so a missing value never wipes the peak."""
    cur.execute(
        f"""UPDATE nh_incidents SET last_value = %s, facts = %s,
                   peak_value = {'LEAST' if is_lower_worse else 'GREATEST'}(peak_value, %s)
            WHERE id = %s""",
        (check["value"], Json(check["facts"]), check["value"], incident_id))


def raise_incident_severity(cur, incident_id, severity):
    cur.execute("UPDATE nh_incidents SET severity = %s WHERE id = %s", (severity, incident_id))


def close_incident(cur, incident_id, at):
    """Ends an incident; returns what the clear SMS needs."""
    cur.execute(
        """UPDATE nh_incidents SET ended_at = %s WHERE id = %s
           RETURNING started_at, is_flapping, fire_sms_sent, severity""",
        (at, incident_id))
    row = cur.fetchone()
    if row is None:
        return None
    return {"started_at": row[0], "is_flapping": row[1], "fire_sms_sent": row[2], "severity": row[3]}


def incident_fire_sent(cur, incident_id):
    cur.execute("SELECT fire_sms_sent, is_flapping FROM nh_incidents WHERE id = %s", (incident_id,))
    row = cur.fetchone()
    return (bool(row[0]), bool(row[1])) if row else (False, False)


def mark_fire_sms_sent(cur, incident_ids):
    if incident_ids:
        cur.execute("UPDATE nh_incidents SET fire_sms_sent = true WHERE id = ANY(%s)", (list(incident_ids),))


# ---- The SMS log ----

def alert_context(cur, now):
    """What the cost rules need: SMS sent (or being sent) today, whether the
    'paused' notice went out today, and fires sent per device in the last
    COMBINE_MINUTES."""
    today = eat_midnight(now)
    cur.execute(
        """SELECT count(*) FILTER (WHERE kind <> 'cap'), count(*) FILTER (WHERE kind = 'cap')
           FROM nh_alerts_sent WHERE created_at >= %s AND outcome IN ('pending', 'sent')""",
        (today,))
    sent_today, cap_notices = cur.fetchone()
    cur.execute(
        """SELECT device, coalesce(sum(cardinality(incident_ids)), 0) FROM nh_alerts_sent
           WHERE created_at >= %s AND kind IN ('fire', 'combined') AND rule <> 'CLEARED'
             AND outcome IN ('pending', 'sent') GROUP BY device""",
        (now - timedelta(minutes=config.COMBINE_MINUTES),))
    recent = {device: int(count) for device, count in cur.fetchall()}
    return {"sent_today": sent_today, "cap_notice_sent": cap_notices > 0, "recent_fires": recent}


def log_alerts(cur, rows, recipient_count):
    """Writes the planned rows; returns their ids (same order)."""
    ids = []
    for row in rows:
        cur.execute(
            """INSERT INTO nh_alerts_sent (kind, device, rule, incident_ids, recipient_count, outcome, reason, message)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (row["kind"], row["device"], row["rule"], row["incident_ids"],
             recipient_count if row["outcome"] == "pending" else 0, row["outcome"], row["reason"], row["message"]))
        ids.append(cur.fetchone()[0])
    return ids


def finish_alert(alert_id, outcome, reason):
    with transaction() as cur:
        cur.execute("UPDATE nh_alerts_sent SET outcome = %s, reason = %s WHERE id = %s", (outcome, reason, alert_id))


# ---- Reads for the page ----

def active_rules():
    with db_cursor() as (_, cur):
        cur.execute(
            """SELECT s.device, s.rule, s.subject, s.severity, i.started_at, i.last_value, i.facts, i.is_flapping
               FROM nh_rule_state s LEFT JOIN nh_incidents i ON i.id = s.incident_id
               WHERE s.is_active ORDER BY i.started_at""")
        return [{"device": r[0], "rule": r[1], "subject": r[2], "severity": r[3], "started_at": r[4],
                 "value": r[5], "facts": r[6] or {}, "is_flapping": r[7]} for r in cur.fetchall()]


def page_basics():
    """The feed row, the settings and the newest reading of each kind."""
    with db_cursor() as (_, cur):
        cur.execute("SELECT kind, max(ts) FROM nh_readings GROUP BY kind")
        latest = dict(cur.fetchall())
        return feed(cur), settings(cur), latest


def incidents(limit):
    with db_cursor() as (_, cur):
        cur.execute(
            """SELECT id, device, rule, subject, severity, started_at, ended_at, peak_value, facts, is_flapping,
                      fire_sms_sent
               FROM nh_incidents ORDER BY started_at DESC, id DESC LIMIT %s""",
            (limit,))
        return [{"id": r[0], "device": r[1], "rule": r[2], "subject": r[3], "severity": r[4], "started_at": r[5],
                 "ended_at": r[6], "peak_value": r[7], "facts": r[8] or {}, "is_flapping": r[9],
                 "fire_sms_sent": r[10]} for r in cur.fetchall()]


def alerts_since(since):
    with db_cursor() as (_, cur):
        cur.execute(
            """SELECT id, created_at, kind, device, rule, recipient_count, outcome, reason, message
               FROM nh_alerts_sent WHERE created_at >= %s ORDER BY created_at DESC, id DESC""",
            (since,))
        return [{"id": r[0], "created_at": r[1], "kind": r[2], "device": r[3], "rule": r[4], "recipients": r[5],
                 "outcome": r[6], "reason": r[7], "message": r[8]} for r in cur.fetchall()]


BUCKET = "to_timestamp(floor(extract(epoch FROM ts) / %(bucket)s) * %(bucket)s)"


def chart_series(since, bucket_seconds):
    """Everything the graphs draw, averaged into buckets of bucket_seconds."""
    args = {"since": since, "bucket": bucket_seconds}
    with db_cursor() as (_, cur):
        cur.execute(
            f"""SELECT {BUCKET} AS b, avg((load->>'down_mbps')::float8), avg((load->>'up_mbps')::float8),
                       max((load->>'down_mbps')::float8), avg((load->>'ppp')::float8), avg((load->>'cpu_pct')::float8),
                       avg((load->>'new_down_mbps')::float8)
                FROM nh_readings WHERE kind = 'probe' AND ts >= %(since)s GROUP BY b ORDER BY b""", args)
        load = [{"t": r[0], "down": r[1], "up": r[2], "down_max": r[3], "ppp": r[4], "cpu": r[5], "new_down": r[6]}
                for r in cur.fetchall()]
        cur.execute(
            f"""SELECT {BUCKET} AS b, avg((load->>'cpu_pct')::float8)
                FROM nh_readings WHERE kind = 'inner' AND ts >= %(since)s GROUP BY b ORDER BY b""", args)
        inner_cpu = [{"t": r[0], "cpu": r[1]} for r in cur.fetchall()]
        cur.execute(
            f"""SELECT {BUCKET} AS b, p->>'name', avg((p->>'rtt_ms')::float8), avg((p->>'jitter_ms')::float8),
                       avg((p->>'loss_pct')::float8), count(*)
                FROM nh_readings r CROSS JOIN LATERAL jsonb_array_elements(r.probes) p
                WHERE r.kind IN ('probe', 'inner') AND r.ts >= %(since)s GROUP BY b, 2 ORDER BY b""", args)
        probes = [{"t": r[0], "name": r[1], "rtt": r[2], "jitter": r[3], "loss": r[4], "n": r[5]}
                  for r in cur.fetchall()]
        cur.execute(
            f"""SELECT {BUCKET} AS b, kind, avg((link->>'rx_dbm')::float8), avg((link->>'tx_dbm')::float8)
                FROM nh_readings WHERE kind IN ('link', 'linknew') AND ts >= %(since)s GROUP BY b, kind ORDER BY b""",
            args)
        link = [{"t": r[0], "kind": r[1], "rx": r[2], "tx": r[3]} for r in cur.fetchall()]
        cur.execute(
            """SELECT ts, kind, speed FROM nh_readings WHERE kind IN ('speed', 'speednew') AND ts >= %(since)s
               ORDER BY ts""", args)
        speed = [{"t": r[0], "kind": r[1], **(r[2] or {})} for r in cur.fetchall()]
        # DNS: lookup time averaged over the runs with no failed lookup (a
        # failure is recorded as 10 s and would flatten the graph); failed
        # lookups are counted separately and marked on the graph instead.
        cur.execute(
            f"""SELECT {BUCKET} AS b, x->>'name', max(x->>'server'),
                       avg((x->>'worst_ms')::float8) FILTER (WHERE coalesce((x->>'fails')::float8, 0) = 0),
                       coalesce(sum((x->>'fails')::float8), 0), count(*)
                FROM nh_readings r CROSS JOIN LATERAL jsonb_array_elements(r.dns->'resolvers') x
                WHERE r.kind = 'dns' AND r.ts >= %(since)s GROUP BY b, 2 ORDER BY b""", args)
        dns = [{"t": r[0], "name": r[1], "server": r[2], "ms": r[3], "fails": int(r[4]), "runs": r[5]}
               for r in cur.fetchall()]
    return {"load": load, "inner_cpu": inner_cpu, "probes": probes, "link": link, "speed": speed, "dns": dns}
