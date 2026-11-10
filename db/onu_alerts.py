"""LOS alerts for ONUs (Phase 6, owner's call 2026-11-11).

When an ONU loses fibre signal (SmartOLT status "LOS"), the people who get
site alerts are told: after a 2-minute wait (a patch cord pulled for a
moment shouldn't page anyone), then at 15 min, 30 min, 1 h, 3 h, 6 h and
8 h, then twice a day (08:00 and 16:00 EAT) until it has signal again. Once
someone acknowledges it, only the twice-a-day reminders are left. A "signal
back" message closes it. Same recipients, channels, sending window and
editable wording (Alerts > SMS Templates) as the site alerts.

One cut, one alert: when 3 or more ONUs on the same PON port go LOS within
2 minutes of each other, that's almost certainly one fibre cut, so the port
gets ONE alert listing who's affected instead of one per customer.

The message names whoever the ONU is linked to on OLT > Links (customer or
hotspot site), else the ONU's own name. Disabled ONUs (6C) never alert.

There's no scheduler: tick() runs at most once a minute from the router
heartbeat and the OLT page (the same clock as the SmartOLT polls), and
right after a webhook status change. It never blocks or raises. Nothing is
opened or closed while OPS's ONU status is stale (older than 15 minutes):
silence then means "OPS doesn't know", never "everything is fine".
Switched off with ONU_LOS_ALERTS_ENABLED=false.
"""
import logging
import os
import threading
import time as clock
from datetime import datetime, timedelta, timezone

from db import alert_settings
from db.alert_channels import send_sms, send_whatsapp
from db.alert_templates import load_all as load_templates, render
from db.connection import EAT, db_cursor
from db.notifications import create as create_notification

log = logging.getLogger(__name__)

FAST_SCHEDULE_MIN = [2, 15, 30, 60, 180, 360, 480]
PON_MIN_ONUS = 3
PON_WINDOW = timedelta(minutes=2)
STATUS_STALE = timedelta(minutes=15)
TICK_EVERY_S = 60
LOCK_KEY = 6_0_0_0_1           # pg advisory lock: one evaluation at a time across app copies


def enabled():
    return (os.environ.get("ONU_LOS_ALERTS_ENABLED") or "true").strip().lower() not in ("false", "0", "no", "off")


# ---- Pure rules ----

def checkpoints_crossed(started_at, now):
    elapsed = now - started_at
    return sum(1 for m in FAST_SCHEDULE_MIN if elapsed >= timedelta(minutes=m))


def find_cuts(los):
    """los: [{id, pon, started_at}] for ONUs LOS right now. Returns the set of
    PON ports where 3 or more of them went LOS within any 2-minute span."""
    by_pon = {}
    for o in los:
        if o["pon"]:
            by_pon.setdefault(o["pon"], []).append(o["started_at"])
    cuts = set()
    for pon, times in by_pon.items():
        times.sort()
        for i in range(len(times)):
            j = i
            while j + 1 < len(times) and times[j + 1] - times[i] <= PON_WINDOW:
                j += 1
            if j - i + 1 >= PON_MIN_ONUS:
                cuts.add(pon)
                break
    return cuts


def duration(td):
    minutes = max(0, int(td.total_seconds() // 60))
    days, rest = divmod(minutes, 1440)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def since_text(dt, now):
    """"17:53" today; "9 Nov 17:53" for anything older than a day."""
    local = dt.astimezone(EAT)
    if now - dt < timedelta(hours=24):
        return local.strftime("%H:%M")
    return f"{local.day} {local.strftime('%b %H:%M')}"


def onus(n):
    return f"{n} ONU" if n == 1 else f"{n} ONUs"


# ---- Reading the state ----

def _los_now(cur):
    """ONUs currently LOS (not disabled), with who they belong to."""
    cur.execute("""
        SELECT s.unique_external_id, s.name, s.sn, s.board, s.port, s.onu, s.last_status_change, s.status_polled_at,
               c.name, c.username, COALESCE(m.name, loc.name)
        FROM onu_state s
        LEFT JOIN onu_customer_links l ON l.unique_external_id = s.unique_external_id
        LEFT JOIN ppp_customers c ON c.id = l.customer_id
        LEFT JOIN monitored_sites m ON m.id = l.site_id
        LEFT JOIN locations loc ON loc.id = m.location_id
        WHERE s.status = 'LOS' AND COALESCE(s.details->>'administrative_status', 'Enabled') <> 'Disabled'""")
    out = []
    for r in cur.fetchall():
        who = (f"{r[8] or r[9]} ({r[9]})" if r[9] else None) or (f"hotspot site {r[10]}" if r[10] else None) or (r[1] or r[2] or r[0])
        out.append({"id": r[0], "name": r[1], "sn": r[2], "pon": f"{r[3]}/{r[4]}" if r[3] is not None and r[4] is not None else None,
                    "location": "/".join(str(v) for v in (r[3], r[4], r[5]) if v is not None) or None,
                    "started_at": r[6] or r[7], "who": who})
    return out


def _status_fresh(cur, now):
    cur.execute("SELECT max(status_polled_at) FROM onu_state")
    last = cur.fetchone()[0]
    return last is not None and now - last <= STATUS_STALE


def _open_alerts(cur):
    cur.execute("""SELECT id, unique_external_id, pon, onu_count, started_at, step, last_sent_at, acked_at
                   FROM onu_alerts WHERE ended_at IS NULL""")
    return [{"id": r[0], "onu": r[1], "pon": r[2], "count": r[3], "started_at": r[4], "step": r[5],
             "last_sent_at": r[6], "acked_at": r[7]} for r in cur.fetchall()]


# ---- Sending ----

def _recipients(cur):
    from db.monitoring_alerts import load_recipients
    return load_recipients(cur)


def _send(recipients, title, message, link):
    for r in recipients:
        try:
            if r["in_app"]:
                create_notification(r["id"], "onu_los", title, message, link=link)
            if r["sms"] and r["phone"]:
                send_sms(r["phone"], message)
            if r["whatsapp"] and r["phone"]:
                send_whatsapp(r["phone"], message)
        except Exception:
            log.exception("onu_alerts: couldn't deliver to user %s", r.get("id"))


def _who_list(members, limit=6):
    names = [m["who"] for m in members]
    return ", ".join(names[:limit]) + (f" and {len(names) - limit} more" if len(names) > limit else "")


# ---- The evaluation ----

def evaluate(now=None):
    now = now or datetime.now(timezone.utc)
    if not enabled():
        return
    with db_cursor() as (conn, cur):
        cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK_KEY,))
        if not cur.fetchone()[0]:
            return
        if not _status_fresh(cur, now):
            conn.commit()
            return
        t = load_templates()
        window_open = alert_settings.is_open(alert_settings.get())
        los = [o for o in _los_now(cur) if o["started_at"] is not None]
        los_by_id = {o["id"]: o for o in los}
        cuts = find_cuts(los)
        alerts = _open_alerts(cur)
        recipients = None

        def people():
            nonlocal recipients
            if recipients is None:
                recipients = _recipients(cur)
            return recipients

        # 1. Close what has ended.
        for a in alerts:
            if a["pon"]:
                members = [o for o in los if o["pon"] == a["pon"]]
                still = bool(members)
                who = f"PON {a['pon']} ({onus(a['count'])})"
            else:
                members = []
                pon_alert_open = any(b["pon"] and b["pon"] == (los_by_id.get(a["onu"]) or {}).get("pon") for b in alerts)
                still = a["onu"] in los_by_id and los_by_id[a["onu"]]["pon"] not in cuts and not pon_alert_open
                who = None
            if still:
                # onu_count keeps the most ONUs this cut ever took out.
                if a["pon"] and len(members) > a["count"]:
                    cur.execute("UPDATE onu_alerts SET onu_count = %s WHERE id = %s", (len(members), a["id"]))
                    a["count"] = len(members)
                continue
            merged = (not a["pon"]) and a["onu"] in los_by_id     # now part of a PON cut
            cur.execute("UPDATE onu_alerts SET ended_at = %s WHERE id = %s", (now, a["id"]))
            a["ended"] = True
            if merged or a["step"] == 0 or not window_open:
                continue
            if who is None:
                cur.execute("""SELECT COALESCE(c.name, c.username, 'hotspot site ' || COALESCE(m.name, loc.name), s.name, s.sn, %s)
                               FROM (SELECT %s AS id) x
                               LEFT JOIN onu_state s ON s.unique_external_id = x.id
                               LEFT JOIN onu_customer_links l ON l.unique_external_id = x.id
                               LEFT JOIN ppp_customers c ON c.id = l.customer_id
                               LEFT JOIN monitored_sites m ON m.id = l.site_id
                               LEFT JOIN locations loc ON loc.id = m.location_id""", (a["onu"], a["onu"]))
                who = cur.fetchone()[0]
            message = render(t["onu_los_recovered"], {"WHO": who, "DURATION": duration(now - a["started_at"])})
            _send(people(), f"{who}: fibre signal back", message, f"olt/onu/{a['onu']}")
        alerts = [a for a in alerts if not a.get("ended")]

        # 2. Open new ones: one per cut port, one per other LOS ONU.
        open_pons = {a["pon"] for a in alerts if a["pon"]}
        open_onus = {a["onu"] for a in alerts if not a["pon"]}
        for pon in cuts - open_pons:
            members = sorted((o for o in los if o["pon"] == pon), key=lambda o: o["started_at"])
            alerts.append(_open(cur, members[0]["id"], pon, len(members), members[0]["started_at"], now))
        for o in los:
            # Still covered by its port's alert, even once fewer than 3 are left.
            if o["pon"] in cuts or o["pon"] in open_pons or o["id"] in open_onus:
                continue
            alerts.append(_open(cur, o["id"], None, 1, o["started_at"], now))

        # 3. Send what's due.
        if window_open:
            for a in alerts:
                members = [o for o in los if o["pon"] == a["pon"]] if a["pon"] else [los_by_id.get(a["onu"])]
                members = [m for m in members if m]
                if not members:
                    continue
                crossed = checkpoints_crossed(a["started_at"], now)
                fast = a["acked_at"] is None and a["step"] < len(FAST_SCHEDULE_MIN)
                if fast:
                    if crossed <= a["step"]:
                        continue
                    first = a["step"] == 0 and crossed == 1
                    new_step = crossed
                else:
                    since = max(d for d in (a["last_sent_at"], a["acked_at"], a["started_at"]) if d)
                    if not alert_settings.daily_slot_due(since, now):
                        continue
                    first, new_step = False, a["step"]
                title, message, link = _message(t, a, members, first, now)
                cur.execute("UPDATE onu_alerts SET step = %s, last_sent_at = %s WHERE id = %s", (new_step, now, a["id"]))
                _send(people(), title, message, link)
        conn.commit()


def _open(cur, onu_id, pon, count, started_at, now):
    """A new alert. One that has already been LOS for longer than the whole
    fast schedule (8 h), e.g. a long-dead ONU when this first runs, joins
    the twice-daily round straight away, quietly, instead of announcing
    itself now."""
    old = now - started_at > timedelta(minutes=FAST_SCHEDULE_MIN[-1])
    step = len(FAST_SCHEDULE_MIN) if old else 0
    cur.execute("""INSERT INTO onu_alerts (unique_external_id, pon, onu_count, started_at, step, last_sent_at)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (onu_id, pon, count, started_at, step, now if old else None))
    return {"id": cur.fetchone()[0], "onu": onu_id, "pon": pon, "count": count, "started_at": started_at,
            "step": step, "last_sent_at": now if old else None, "acked_at": None}


def _message(t, a, members, first, now):
    since, lasted = since_text(a["started_at"], now), duration(now - a["started_at"])
    if a["pon"]:
        values = {"PORT": a["pon"], "COUNT": onus(len(members)), "SINCE": since, "DURATION": lasted,
                  "WHO_LIST": _who_list(members)}
        kind = "pon_los" if first else "pon_los_still"
        title = f"PON {a['pon']}: {onus(len(members))} without fibre"
        return title, render(t[kind], values), "olt"
    m = members[0]
    values = {"WHO": m["who"], "ONU": m["name"] or m["sn"] or m["id"], "PORT": m["location"] or "?",
              "SINCE": since, "DURATION": lasted}
    kind = "onu_los" if first else "onu_los_still"
    title = f"{m['who']}: LOS" if first else f"{m['who']} still LOS"
    return title, render(t[kind], values), f"olt/onu/{m['id']}"


# ---- Ticking (no scheduler) ----

_tick_lock = threading.Lock()
_last_tick = 0.0
_ticking = False


def tick(force=False):
    """Runs evaluate() on its own thread, at most once a minute (or now, with
    force). Never blocks, never raises."""
    global _last_tick, _ticking
    try:
        if not enabled():
            return
        now = clock.monotonic()
        with _tick_lock:
            if _ticking or (not force and now - _last_tick < TICK_EVERY_S):
                return
            _ticking, _last_tick = True, now
        threading.Thread(target=_run, name="onu-alerts", daemon=True).start()
    except Exception:
        log.exception("onu_alerts tick failed")


def _run():
    global _ticking
    try:
        evaluate()
    except Exception:
        log.exception("onu_alerts evaluation failed")
    finally:
        with _tick_lock:
            _ticking = False


# ---- Reading and acknowledging (OLT page) ----

def open_alerts():
    with db_cursor() as (_, cur):
        cur.execute("""SELECT a.id, a.unique_external_id, a.pon, a.onu_count, a.started_at, a.step, a.last_sent_at,
                              a.acked_at, u.name, a.ack_note, s.name, s.sn
                       FROM onu_alerts a LEFT JOIN users u ON u.id = a.acked_by
                       LEFT JOIN onu_state s ON s.unique_external_id = a.unique_external_id
                       WHERE a.ended_at IS NULL ORDER BY a.started_at""")
        rows = cur.fetchall()
        los = {o["id"]: o for o in _los_now(cur)}
    out = []
    for r in rows:
        who = f"PON {r[2]}" if r[2] else (los.get(r[1], {}).get("who") or r[10] or r[11] or r[1])
        out.append({"id": r[0], "onu": r[1], "pon": r[2], "count": r[3], "started_at": r[4].isoformat(),
                    "messages_sent": r[5], "last_sent_at": r[6].isoformat() if r[6] else None,
                    "acked_at": r[7].isoformat() if r[7] else None, "acked_by": r[8], "ack_note": r[9], "who": who,
                    "members": [o["who"] for o in los.values() if r[2] and o["pon"] == r[2]]})
    return out


def acknowledge(alert_id, user_id, note=None):
    with db_cursor() as (conn, cur):
        cur.execute("""UPDATE onu_alerts SET acked_by = %s, acked_at = now(), ack_note = %s
                       WHERE id = %s AND ended_at IS NULL AND acked_at IS NULL""", (user_id, note, alert_id))
        done = cur.rowcount
        conn.commit()
    return bool(done)
