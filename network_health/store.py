"""Every SQL statement Network Health runs, over its own nh_ tables only.

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

def save_batch(source, sent_at, records):
    """Stores the records (a key already stored is skipped: the shipper
    resends) and notes the post on nh_feed, in one transaction. Returns the
    newly stored records."""
    with transaction() as cur:
        stored_keys = set()
        if records:
            rows = [(r["key"], r["ts"], r["kind"], r["from_ip"], r["raw"], _json(r["load"]), _json(r["probes"]),
                     _json(r["link"]), _json(r["speed"])) for r in records]
            stored = execute_values(
                cur,
                """INSERT INTO nh_readings (key, ts, kind, from_ip, raw, load, probes, link, speed) VALUES %s
                   ON CONFLICT (key) DO NOTHING RETURNING key""",
                rows, page_size=500, fetch=True)
            stored_keys = {row[0] for row in stored}
        from_ip = next((r["from_ip"] for r in reversed(records) if r["from_ip"]), None)
        cur.execute(
            """UPDATE nh_feed SET last_post_at = now(), last_sent_at = %s, last_source = %s,
                      last_from_ip = COALESCE(%s, last_from_ip) WHERE id = 1""",
            (sent_at, source, from_ip))
    return [r for r in records if r["key"] in stored_keys]


def _json(value):
    return None if value is None else Json(value)


def delete_old_readings():
    with transaction() as cur:
        cur.execute("DELETE FROM nh_readings WHERE ts < now() - %s * interval '1 day'", (config.KEEP_READINGS_DAYS,))
        return cur.rowcount


# ---- Reading readings ----

def readings(cur, kinds, since, until):
    """Readings of these kinds with since < ts <= until, oldest first."""
    cur.execute(
        """SELECT ts, kind, load, probes, link, speed FROM nh_readings
           WHERE kind = ANY(%s) AND ts > %s AND ts <= %s ORDER BY ts""",
        (list(kinds), since, until))
    return [{"ts": r[0], "kind": r[1], "load": r[2], "probes": r[3], "link": r[4], "speed": r[5]}
            for r in cur.fetchall()]


def previous_reading(cur, kind, before):
    """The latest reading of this kind strictly before a moment, or None."""
    cur.execute(
        "SELECT ts, kind, load, probes, link, speed FROM nh_readings WHERE kind = %s AND ts < %s ORDER BY ts DESC LIMIT 1",
        (kind, before))
    r = cur.fetchone()
    return None if r is None else {"ts": r[0], "kind": r[1], "load": r[2], "probes": r[3], "link": r[4], "speed": r[5]}


def last_received_at(cur, kind):
    """When OPS last stored a reading of this kind (not the reading's ts)."""
    cur.execute("SELECT max(received_at) FROM nh_readings WHERE kind = %s", (kind,))
    return cur.fetchone()[0]


def feed(cur):
    cur.execute("SELECT last_post_at, last_sent_at, last_source, last_from_ip FROM nh_feed WHERE id = 1")
    row = cur.fetchone() or (None, None, None, None)
    return {"last_post_at": row[0], "last_sent_at": row[1], "source": row[2], "from_ip": row[3]}


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
    return {"load": load, "inner_cpu": inner_cpu, "probes": probes, "link": link, "speed": speed}
