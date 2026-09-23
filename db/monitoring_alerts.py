"""Phase 4.8 — Alerting. Turns site_status_log transitions into debounced,
flap-aware, quiet-hours-respecting alerts across three channels: in-app
(notifications table), SMS and WhatsApp (db/alert_channels.py).

WHY A SEPARATE PASS, NOT INLINE IN ingest_snapshot(). site_status_log stays
a pure transition record (0006's design) — every state CHANGE gets a row,
immediately, with no notion of "was this real". Alerting needs a different
question: "has this SPECIFIC drop lasted long enough to bother anyone", and
answering it requires looking at elapsed time since a past transition, not
just reacting to the transition itself. site_alert_episodes (0012) is that
missing piece of state: one open row per site for as long as it is down or
flapping, closed the moment it's next seen online. This module is called as
a FastAPI background task right after an ingest POST returns (same pattern
as monitoring_retention.run_if_due) — it runs on its own transaction,
after ingest_snapshot's has already committed, so a slow SMS provider can
never make the router's heartbeat POST hang.

DEBOUNCE + ESCALATION. A site isn't alerted the instant it goes offline —
only once it has stayed offline for SCHEDULE_MINUTES[0] (5 min). Below
that, a blip closes its episode silently on recovery (nothing was ever
sent, so there's nothing to report "back online" from). Past that first
alert, SCHEDULE_MINUTES is a fixed list of "still down" checkpoints
(5, 15, 30, 60min, then 1.5h through 5h, then hourly to 12h) — each one
crossed sends exactly one reminder and advances episode.escalation_step,
so a re-run of this function seconds later never double-sends. Past the
schedule's last entry (12h) the episode goes quiet on its own: still open,
still logged, just nothing left to send. Recovery at any point ends the
schedule automatically — a resolved episode is no longer in open_by_site,
so nothing here has to separately "cancel" a timer.

FLAPPING. FLAP_THRESHOLD transitions within FLAP_WINDOW moves an episode
into the 'flapping' state, which sends exactly one alert and then goes
quiet — see PHASES.md 4.8: "a site bouncing 40 times a night reads green on
any single poll" is the failure mode being avoided, but the fix must not be
40 alerts either. Detection is unchanged and stays trigger-based, not
time-debounced — only its message copy and its rate-limit treatment
(see RATE_LIMITED_KINDS) changed when escalation was added.

MASS EVENTS. If MASS_EVENT_MIN_SITES or more sites cross an alert
checkpoint in the same evaluation pass, they are reported as ONE combined
alert instead of N separate SMSes, while each site's own episode/history
stays fully intact underneath. Owner's call 2026-09-23: until the real
cause of a mass drop can be told apart, it is treated as a POWER FAILURE,
not a router/network event — so the combined alert carries a battery
dispatch plan for every site in it (ranked the same way as below: most
people online first, today's revenue as the tiebreak), at every
checkpoint, not just the 15-minute one. (PHASES.md 4.8's router-restart
marker, which would let a CCR reboot be told apart from a real outage,
still needs a router-side startup script and isn't built.)

QUIET HOURS and ACKNOWLEDGEMENT both suppress SENDING, never the episode
itself — site_status_log and the Status page are unaffected either way.

MONEY. No alert body in this file ever includes a revenue figure — see
PHASES.md 4.4's "gating covers ... alert message bodies". Down/recovered/
flapping messages structurally can't for state-only alerts; the battery
recommendation below DOES read revenue_events, but only ever to rank
sites internally — see "BATTERY RECOMMENDATION" for the line that rule
still draws.

BATTERY RECOMMENDATION (Task 3, 2026-09-23). At escalation step 1 (the
15-minute "still down" reminder — SCHEDULE_MINUTES[1]), a down alert
names which battery to bring: the confirmed-charged, currently-At-Base
battery with the lowest recent removal count. This is the ONE place
monitoring reads outside its own domain — `batteries`/`battery_movements`
— a deliberate, documented amendment to the isolation invariant (see
PHASES.md, 2026-09-23 and ARCHITECTURE.md's matching section), not drift:
read-only, one direction (this file imports FROM db.batteries; that
module has zero awareness this file exists), never joined into a
battery-domain query.

When more than one site hits step 1 in the same pass, they're ranked by
two INTERNAL-ONLY signals — sessions_at_drop (how many people were online
when it fell), then today's revenue as a tiebreaker — and handed out the
best still-available battery in that order, greedily, so two sites
escalating together are never told to send the same physical battery.
Per PHASES.md 4.4, revenue ranks sites but is NEVER the number printed —
a message can say a site is ahead in priority, never by how much money.
The mass-event path (see MASS EVENTS) uses the same ranking for its
dispatch plan.

MESSAGE WORDING lives in db/alert_templates.py (defaults) and the
alert_message_templates table (edits made in the app) — this file only
works out the values that fill each template's {{PLACEHOLDERS}}.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from db.alert_channels import send_sms, send_whatsapp
from db.alert_templates import load_all as load_templates, render as render_template
from db.connection import db_cursor, now_eat
from db.notifications import create as create_notification

log = logging.getLogger(__name__)

# Minutes-since-down at which a "still down" reminder goes out. Index 0
# (5 min) is the original debounce threshold — below it, nothing is ever
# sent. Each later checkpoint crossed sends exactly one reminder (never a
# range, never repeated) and advances site_alert_episodes.escalation_step
# (migration 0013) by one, so this list is also literally "how many sends
# an episode can ever produce": len(SCHEDULE_MINUTES) = 16, i.e. at most 16
# alerts over the life of one continuous outage, tapering from every few
# minutes in hour one to hourly by hour six, and nothing at all past 12h —
# the episode stays open and logged, it just stops paging anyone.
SCHEDULE_MINUTES = [5, 15, 30, 60, 90, 120, 180, 240, 300, 360, 420, 480, 540, 600, 660, 720]

# A site that changes state this many times within this window is flapping,
# not down — one alert, then silence until it settles (leaves the window
# with no further transition).
FLAP_WINDOW = timedelta(minutes=15)
FLAP_THRESHOLD = 4

# SMS/WhatsApp are rate-limited per site per hour (PHASES.md 4.8); in-app
# never is — it costs nothing and a bell icon is not spam.
#
# THIS APPLIES TO FLAPPING ONLY. Down-alert escalation (SCHEDULE_MINUTES
# above) deliberately BYPASSES this limiter — it is already self-limiting by
# construction (~4 sends in hour one, tapering to one an hour, one send per
# checkpoint ever), and running it through the same hourly cap would eat an
# episode's only two sends in its first five minutes and go silent for the
# rest of the hour. A recovery is likewise exempt — it fires at most once
# per episode, never a burst. Flapping is the one kind that CAN legitimately
# burst (a site opening several short flapping episodes back to back), so
# it is the one kind checked against RATE_LIMITED_KINDS below. If a new
# alert kind is ever added, decide explicitly which side of that line it's
# on — don't let it silently inherit either behaviour.
EXTERNAL_RATE_LIMIT_WINDOW = timedelta(hours=1)
EXTERNAL_RATE_LIMIT_MAX = 1
RATE_LIMITED_KINDS = {"flapping"}

# See "MASS EVENTS" above.
MASS_EVENT_MIN_SITES = 5


def evaluate_and_notify():
    """Entry point for the background task. Never raises — an alerting bug
    must never take down the ingest endpoint that calls it."""
    try:
        _evaluate()
    except Exception:
        log.exception("alert evaluation failed")


# ---- Per-user subscription (Settings > Notifications) ----

def get_subscription(user_id):
    """No-row default is opt-out, not opt-in: all three channels start on,
    and Settings > Notifications is where someone switches one off for
    themselves. Applies immediately to every current no-row user too, not
    just future ones — a deliberate choice (reach over cost-control) made
    2026-09-23 when this shipped, not a side effect of how it's coded."""
    with db_cursor() as (conn, cur):
        cur.execute(
            "SELECT in_app_enabled, sms_enabled, whatsapp_enabled FROM monitoring_alert_subscriptions WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    if row is None:
        return {"in_app_enabled": True, "sms_enabled": True, "whatsapp_enabled": True}
    return {"in_app_enabled": row[0], "sms_enabled": row[1], "whatsapp_enabled": row[2]}


def set_subscription(user_id, in_app_enabled, sms_enabled, whatsapp_enabled):
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            INSERT INTO monitoring_alert_subscriptions (user_id, in_app_enabled, sms_enabled, whatsapp_enabled)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE
                SET in_app_enabled = EXCLUDED.in_app_enabled,
                    sms_enabled = EXCLUDED.sms_enabled,
                    whatsapp_enabled = EXCLUDED.whatsapp_enabled,
                    updated_at = now()
            """,
            (user_id, in_app_enabled, sms_enabled, whatsapp_enabled),
        )
        conn.commit()


def admin_set_channels(user_id, sms_enabled, whatsapp_enabled):
    """The admin-facing counterpart to set_subscription (Settings >
    Notifications, self-service, all three channels). This one only ever
    touches sms_enabled/whatsapp_enabled — never in_app_enabled, which
    stays whatever the user themselves has it as. Same table, same
    columns, last write wins between this and the user's own Settings
    page; no locking or conflict handling, same as every other
    admin-edits-a-user's-row action in this app (see users.py PATCH)."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            INSERT INTO monitoring_alert_subscriptions (user_id, in_app_enabled, sms_enabled, whatsapp_enabled)
            VALUES (%s, true, %s, %s)
            ON CONFLICT (user_id) DO UPDATE
                SET sms_enabled = EXCLUDED.sms_enabled,
                    whatsapp_enabled = EXCLUDED.whatsapp_enabled,
                    updated_at = now()
            """,
            (user_id, sms_enabled, whatsapp_enabled),
        )
        conn.commit()


# ---- Message text ----

def _format_duration(td):
    total_minutes = max(0, int(td.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


# Every function below takes `t`, the {kind: body} dict from
# alert_templates.load_all() — the wording itself lives there (editable in
# the app), these only work out the values that go into it.

def _down_message(t, name, sessions_at_drop, opened_at_eat, step, downtime, battery_note=""):
    """step 0 is the original "just went down" alert; every later checkpoint
    is a "still down" reminder — the headcount at the moment it dropped
    stops being the interesting number by then, so it's dropped in favour
    of how long it's actually been. battery_note (step 1 only — see
    "BATTERY RECOMMENDATION" in the file header) fills {{BATTERY_NOTE}}."""
    since = opened_at_eat.strftime("%H:%M")
    if step == 0:
        people = str(sessions_at_drop) if sessions_at_drop is not None else "unknown"
        return render_template(t["down"], {"SITE_NAME": name, "PEOPLE_ONLINE": people, "DOWN_SINCE": since})
    return render_template(t["still_down"], {
        "SITE_NAME": name, "DURATION": _format_duration(downtime), "DOWN_SINCE": since,
        "BATTERY_NOTE": battery_note or "",
    })


def _battery_note(t, assignment):
    """Turns one _battery_recommendations() entry into the sentence that
    fills {{BATTERY_NOTE}}. Priority wording only appears when there was
    something to rank against (of > 1) — a lone escalating site gets a
    plain recommendation, not "priority 1 of 1", which would just be
    noise. Never mentions sessions or revenue, the two signals that
    decided the ranking — only the rank itself, per PHASES.md 4.4."""
    if assignment is None:
        return ""
    battery = assignment["battery"]
    priority = f"(priority {assignment['rank']} of {assignment['of']})" if assignment["of"] > 1 else ""
    if battery is None:
        return render_template(t["battery_none"], {"PRIORITY": priority})
    return render_template(t["battery_recommended"], {"BATTERY": battery["battery_number"], "PRIORITY": priority})


def _recovery_message(t, name, downtime):
    return render_template(t["recovered"], {"SITE_NAME": name, "DURATION": _format_duration(downtime)})


def _flap_message(t, name, flap_count):
    return render_template(t["flapping"], {
        "SITE_NAME": name, "FLAP_COUNT": flap_count,
        "WINDOW_MINUTES": int(FLAP_WINDOW.total_seconds() // 60),
    })


def _name_list(names, limit=6):
    shown = ", ".join(names[:limit])
    return f"{shown} and {len(names) - limit} more" if len(names) > limit else shown


def _battery_plan(ranked):
    """ranked: [(site name, battery dict or None)], highest priority first —
    batteries were handed out greedily in that same order, so the ones that
    got a battery are always a prefix of the list. The rest keep their
    numbering so whoever's dispatching knows which site is next the moment
    another battery is charged or comes back."""
    with_battery = [(n, b) for n, b in ranked if b is not None]
    without = [n for n, b in ranked if b is None]
    if not with_battery:
        numbered = [f"{i}. {n}" for i, n in enumerate(without, 1)]
        return f"No charged battery at base. Priority when one is: {_name_list(numbered)}."
    plan = "Send batteries: " + ", ".join(
        f"{i}. {n} ({b['battery_number']})" for i, (n, b) in enumerate(with_battery, 1)
    ) + "."
    if without:
        start = len(with_battery) + 1
        numbered = [f"{i}. {n}" for i, n in enumerate(without, start)]
        plan += f" Next if more free up: {_name_list(numbered)}."
    return plan


def _mass_message(t, ranked):
    return render_template(t["mass_down"], {
        "SITE_COUNT": len(ranked),
        "SITE_NAMES": _name_list([n for n, _ in ranked]),
        "BATTERY_PLAN": _battery_plan(ranked),
    })


# ---- Recipients ----

def load_recipients(cur):
    """Everyone with sites:receive_alerts (admins always qualify, same rule
    as every other permission check in this codebase), each with their own
    channel opt-in — default is all three channels ON for a user with no
    subscription row yet (opt-out, not opt-in — see get_subscription).
    Public (not underscore-prefixed): also called from
    db/monitoring_reconciliation.py (Phase 4.9) so a confirm-online
    mismatch notifies the same people, not a second hand-picked list."""
    cur.execute(
        """
        SELECT u.id, u.name, u.phone,
               COALESCE(s.in_app_enabled, true), COALESCE(s.sms_enabled, true), COALESCE(s.whatsapp_enabled, true)
        FROM users u
        LEFT JOIN monitoring_alert_subscriptions s ON s.user_id = u.id
        WHERE u.status = 'active' AND (
            u.role = 'admin'
            OR u.role_id IN (
                SELECT role_id FROM role_permissions
                WHERE section = 'sites' AND action = 'receive_alerts' AND allowed
            )
        )
        """
    )
    return [
        {"id": r[0], "name": r[1], "phone": r[2], "in_app": r[3], "sms": r[4], "whatsapp": r[5]}
        for r in cur.fetchall()
    ]


def list_recipients():
    """load_recipients() needs a cursor because both its other callers
    (_evaluate below, monitoring_reconciliation.record_confirmation) are
    already inside one. The admin "Alert Recipients" view (routers/
    monitoring.py) isn't, so it gets this standalone wrapper instead."""
    with db_cursor() as (conn, cur):
        return load_recipients(cur)


# ---- Delivery ----

def _log_delivery(cur, episode_id, site_id, user_id, channel, kind, recipient, status, response):
    cur.execute(
        """
        INSERT INTO alert_deliveries
            (episode_id, monitored_site_id, user_id, channel, kind, recipient, status, provider_response)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (episode_id, site_id, user_id, channel, kind, recipient, status,
         json.dumps(response) if response is not None else None),
    )


def _under_rate_limit(cur, site_id, channel):
    cur.execute(
        """
        SELECT count(*) FROM alert_deliveries
        WHERE monitored_site_id = %s AND channel = %s AND status = 'sent'
          AND sent_at > now() - %s
        """,
        (site_id, channel, EXTERNAL_RATE_LIMIT_WINDOW),
    )
    return cur.fetchone()[0] < EXTERNAL_RATE_LIMIT_MAX


def _deliver(cur, episode_id, site_id, kind, title, message, recipients):
    """One episode, one site, one message — the ordinary (non-mass) path."""
    for r in recipients:
        if r["in_app"]:
            create_notification(r["id"], f"site_{kind}", title, message, link=f"site-detail/{site_id}")
            _log_delivery(cur, episode_id, site_id, r["id"], "in_app", kind, None, "sent", None)
        if r["sms"] and r["phone"]:
            if kind in RATE_LIMITED_KINDS and not _under_rate_limit(cur, site_id, "sms"):
                _log_delivery(cur, episode_id, site_id, r["id"], "sms", kind, r["phone"], "rate_limited", None)
            else:
                ok, resp = send_sms(r["phone"], message)
                _log_delivery(cur, episode_id, site_id, r["id"], "sms", kind, r["phone"], "sent" if ok else "failed", resp)
        if r["whatsapp"] and r["phone"]:
            if kind in RATE_LIMITED_KINDS and not _under_rate_limit(cur, site_id, "whatsapp"):
                _log_delivery(cur, episode_id, site_id, r["id"], "whatsapp", kind, r["phone"], "rate_limited", None)
            else:
                ok, resp = send_whatsapp(r["phone"], message)
                _log_delivery(cur, episode_id, site_id, r["id"], "whatsapp", kind, r["phone"],
                               "sent" if ok else "not_configured", resp)


def _deliver_mass(cur, episodes, message, recipients):
    """Same message, sent ONCE per recipient per channel — a mass event is
    still one thing that happened, not N. One audit row per (site, channel,
    recipient) underneath regardless, so each site's own alert history stays
    accurate even though nothing was actually sent N times. Always kind
    'down' (only the down-escalation path batches into mass events), which
    is outside RATE_LIMITED_KINDS — no rate-limit check here on purpose."""
    title = f"{len(episodes)} sites down — possible power failure"
    for r in recipients:
        if r["in_app"]:
            create_notification(r["id"], "site_down", title, message, link="status")
        sent_sms = send_sms(r["phone"], message) if r["sms"] and r["phone"] else None
        sent_whatsapp = send_whatsapp(r["phone"], message) if r["whatsapp"] and r["phone"] else None
        for e in episodes:
            site_id, episode_id = e["site_id"], e["episode_id"]
            if r["in_app"]:
                _log_delivery(cur, episode_id, site_id, r["id"], "in_app", "down", None, "sent", None)
            if sent_sms is not None:
                ok, resp = sent_sms
                _log_delivery(cur, episode_id, site_id, r["id"], "sms", "down", r["phone"], "sent" if ok else "failed", resp)
            if sent_whatsapp is not None:
                ok, resp = sent_whatsapp
                _log_delivery(cur, episode_id, site_id, r["id"], "whatsapp", "down", r["phone"],
                               "sent" if ok else "not_configured", resp)


# ---- Battery recommendation (Task 3) ----
# Reads batteries/battery_movements — see the file header's "BATTERY
# RECOMMENDATION" note and PHASES.md's 2026-09-23 amendment. Every query in
# this section is read-only and lives here, not in db/batteries.py — the
# dependency runs one direction only.

# How far back a movement counts as a "recent removal". There is no
# explicit removal tag in battery_movements.reason (only 'site_down' /
# 'storage' / blank) — a move AWAY from somewhere (from_location_id IS NOT
# NULL) is the closest proxy this schema has. 30 days: long enough that a
# battery resting after a busy week isn't penalized forever, short enough
# that a battery worked hard six months ago and idle since reads as available.
REMOVAL_COUNT_WINDOW = timedelta(days=30)


def _recent_removal_counts(cur, battery_ids):
    if not battery_ids:
        return {}
    cur.execute(
        """
        SELECT battery_id, count(*) FROM battery_movements
        WHERE battery_id = ANY(%s) AND from_location_id IS NOT NULL
          AND status != 'cancelled' AND created_at > now() - %s
        GROUP BY battery_id
        """,
        (battery_ids, REMOVAL_COUNT_WINDOW),
    )
    return dict(cur.fetchall())


def _available_batteries(cur):
    """Active, confirmed-charged (charge_status='charged' — a human said
    so; there is no live telemetry in this codebase), currently sitting At
    Base (not already pending/in-transit/deployed elsewhere) — sorted by
    lowest recent removal count first, battery_number as a deterministic
    tiebreak. Reuses db.batteries.get_all_batteries()'s own status
    computation rather than re-deriving "At Base" here a second time in a
    way that could quietly drift from the Batteries page's own definition."""
    from db.batteries import get_all_batteries
    candidates = [b for b in get_all_batteries() if b["status"] == "At Base" and b["charge_status"] == "charged"]
    if not candidates:
        return []
    removals = _recent_removal_counts(cur, [b["id"] for b in candidates])
    candidates.sort(key=lambda b: (removals.get(b["id"], 0), b["battery_number"]))
    return candidates


def _eat_day_start_utc():
    """Start of today in EAT, as the naive UTC datetime this schema's
    timestamp columns store — same definition db/monitoring.py's own
    (private) version uses, kept as a small local copy rather than an
    import across module-privacy lines for one helper."""
    start_eat = now_eat().replace(hour=0, minute=0, second=0, microsecond=0)
    return start_eat.astimezone(timezone.utc).replace(tzinfo=None)


def _today_revenue_by_site(cur, site_ids):
    """INTERNAL ranking input only — never appears in an alert body
    (PHASES.md 4.4). Same shape as db/monitoring.py's own today-revenue
    query, kept separate rather than imported since it's three lines and
    this file's amendment is scoped to batteries/battery_movements, not a
    general license to reach into db/monitoring.py's internals either."""
    if not site_ids:
        return {}
    cur.execute(
        """
        SELECT monitored_site_id, COALESCE(sum(price_kes), 0)
        FROM revenue_events
        WHERE monitored_site_id = ANY(%s) AND first_seen_at >= %s AND event_type <> 'baseline'
        GROUP BY monitored_site_id
        """,
        (site_ids, _eat_day_start_utc()),
    )
    return {r[0]: float(r[1]) for r in cur.fetchall()}


def _battery_recommendations(cur, sites_needing):
    """sites_needing: [{'site_id', 'sessions_at_drop'}, ...] — every site
    hitting escalation step 1 (the first "still down" reminder, 15min) in
    this pass, or every site in a mass event. Ranked by two internal-only
    signals, per PHASES.md 4.4:
    sessions_at_drop (people online at the moment it fell) first, today's
    revenue as a tiebreaker — never the figures themselves, just the
    ordering they produce. The best still-available battery is then handed
    out greedily in that order, so two sites escalating together are never
    both told to send the same physical battery.

    Returns {site_id: {'rank': int, 'of': int, 'battery': dict|None}}.
    """
    if not sites_needing:
        return {}
    revenue_by_site = _today_revenue_by_site(cur, [s["site_id"] for s in sites_needing])
    ranked = sorted(
        sites_needing,
        key=lambda s: (-(s["sessions_at_drop"] or 0), -revenue_by_site.get(s["site_id"], 0.0)),
    )
    pool = _available_batteries(cur)
    return {
        s["site_id"]: {"rank": i + 1, "of": len(ranked), "battery": pool[i] if i < len(pool) else None}
        for i, s in enumerate(ranked)
    }


# ---- Core evaluation ----

def _in_quiet_hours(quiet_start, quiet_end):
    if quiet_start is None or quiet_end is None:
        return False
    now = now_eat().time()
    if quiet_start <= quiet_end:
        return quiet_start <= now <= quiet_end
    return now >= quiet_start or now <= quiet_end  # wraps midnight, e.g. 22:00-06:00


def _sessions_before(cur, site_id, before_ts):
    cur.execute(
        """
        SELECT sessions FROM site_session_counts
        WHERE monitored_site_id = %s AND received_at < %s
        ORDER BY received_at DESC, id DESC LIMIT 1
        """,
        (site_id, before_ts),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _count_recent_transitions(cur, site_id, since):
    cur.execute(
        """
        SELECT count(*) FROM site_status_log
        WHERE monitored_site_id = %s AND received_at >= %s AND state IN ('online', 'offline')
        """,
        (site_id, since),
    )
    return cur.fetchone()[0]


def _evaluate():
    now = datetime.utcnow()
    t = load_templates()

    with db_cursor() as (conn, cur):
        # Only 'pppoe' sites can ever report 'offline' today — 'activity'
        # sites can only be online/unknown by design (Correction 6: absence
        # of sessions is never treated as proof of an outage). 'ping' (4.6)
        # isn't implemented in ingest_snapshot yet either, so it can't
        # produce 'offline' rows to react to.
        cur.execute(
            """
            SELECT ms.id, COALESCE(l.name, ms.name, 'VLAN ' || ms.vlan_id),
                   ms.quiet_hours_start, ms.quiet_hours_end
            FROM monitored_sites ms LEFT JOIN locations l ON l.id = ms.location_id
            WHERE ms.is_active AND ms.liveness_source = 'pppoe'
            """
        )
        sites = {r[0]: {"name": r[1], "quiet_start": r[2], "quiet_end": r[3]} for r in cur.fetchall()}
        if not sites:
            conn.commit()
            return

        site_ids = list(sites)
        cur.execute(
            """
            SELECT DISTINCT ON (monitored_site_id) monitored_site_id, state, received_at
            FROM site_status_log
            WHERE monitored_site_id = ANY(%s)
            ORDER BY monitored_site_id, received_at DESC, id DESC
            """,
            (site_ids,),
        )
        latest = {r[0]: {"state": r[1], "since": r[2]} for r in cur.fetchall()}

        cur.execute(
            """
            SELECT id, monitored_site_id, opened_at, state, flap_count,
                   down_alert_sent_at, flapping_alert_sent_at, escalation_step
            FROM site_alert_episodes WHERE resolved_at IS NULL
            """
        )
        open_by_site = {
            r[1]: {"id": r[0], "opened_at": r[2], "state": r[3], "flap_count": r[4],
                   "down_alert_sent_at": r[5], "flapping_alert_sent_at": r[6], "escalation_step": r[7]}
            for r in cur.fetchall()
        }

        cur.execute("SELECT monitored_site_id FROM site_acknowledgements WHERE cleared_at IS NULL")
        acked = {r[0] for r in cur.fetchall()}

        recipients = load_recipients(cur)

        pending_down = []  # sites crossing the debounce threshold this pass

        for site_id, info in sites.items():
            st = latest.get(site_id)
            episode = open_by_site.get(site_id)
            quiet = _in_quiet_hours(info["quiet_start"], info["quiet_end"])

            if st and st["state"] == "offline":
                if episode is None:
                    sessions = _sessions_before(cur, site_id, st["since"])
                    cur.execute(
                        """
                        INSERT INTO site_alert_episodes (monitored_site_id, opened_at, sessions_at_drop)
                        VALUES (%s, %s, %s) RETURNING id
                        """,
                        (site_id, st["since"], sessions),
                    )
                    episode = {"id": cur.fetchone()[0], "opened_at": st["since"], "state": "down",
                               "flap_count": 0, "down_alert_sent_at": None, "flapping_alert_sent_at": None,
                               "escalation_step": 0}

                flap_count = _count_recent_transitions(cur, site_id, now - FLAP_WINDOW)
                if flap_count >= FLAP_THRESHOLD and episode["state"] != "flapping":
                    cur.execute(
                        "UPDATE site_alert_episodes SET state = 'flapping', flap_count = %s WHERE id = %s",
                        (flap_count, episode["id"]),
                    )
                    episode["state"] = "flapping"
                    if episode["flapping_alert_sent_at"] is None and site_id not in acked and not quiet:
                        title = f"{info['name']} is flapping"
                        message = _flap_message(t, info["name"], flap_count)
                        _deliver(cur, episode["id"], site_id, "flapping", title, message, recipients)
                        cur.execute(
                            "UPDATE site_alert_episodes SET flapping_alert_sent_at = now() WHERE id = %s",
                            (episode["id"],),
                        )
                    continue

                if episode["state"] == "flapping":
                    continue  # stays quiet until it settles and is next seen online

                down_for = now - episode["opened_at"]
                step = episode["escalation_step"]
                # Schedule exhausted (12h) — episode stays open and logged,
                # just nothing left to send. Also covers acked/quiet, which
                # suppress sending without ever advancing step, so the
                # reminder due while muted still fires the moment the mute
                # lifts rather than being silently skipped forever.
                if (step < len(SCHEDULE_MINUTES) and down_for >= timedelta(minutes=SCHEDULE_MINUTES[step])
                        and site_id not in acked and not quiet):
                    pending_down.append({
                        "site_id": site_id, "episode_id": episode["id"], "name": info["name"],
                        "sessions_at_drop": _sessions_before(cur, site_id, st["since"]),
                        "opened_at_eat": episode["opened_at"] + timedelta(hours=3),
                        "step": step, "downtime": down_for,
                    })

            elif st and st["state"] == "online" and episode is not None:
                cur.execute(
                    "UPDATE site_alert_episodes SET resolved_at = %s WHERE id = %s",
                    (st["since"], episode["id"]),
                )
                should_notify = episode["down_alert_sent_at"] is not None or episode["state"] == "flapping"
                if should_notify and not quiet:
                    downtime = st["since"] - episode["opened_at"]
                    title = f"{info['name']} is back online"
                    message = _recovery_message(t, info["name"], downtime)
                    _deliver(cur, episode["id"], site_id, "recovered", title, message, recipients)
                    cur.execute(
                        "UPDATE site_alert_episodes SET recovery_alert_sent_at = now() WHERE id = %s",
                        (episode["id"],),
                    )

        if pending_down:
            if len(pending_down) >= MASS_EVENT_MIN_SITES:
                # One combined message regardless of each site's own step —
                # a coincidence this size is itself the news. Each episode's
                # escalation_step still advances individually underneath, so
                # a site that started earlier than the others resumes its
                # own schedule correctly on the next pass. Every site in the
                # batch is ranked for batteries, at every step — this is the
                # likely-power-failure case, where "which site first" is the
                # decision someone actually has to make (see MASS EVENTS).
                assignments = _battery_recommendations(cur, [
                    {"site_id": p["site_id"], "sessions_at_drop": p["sessions_at_drop"]} for p in pending_down
                ])
                ranked = sorted(pending_down, key=lambda p: assignments[p["site_id"]]["rank"])
                message = _mass_message(t, [(p["name"], assignments[p["site_id"]]["battery"]) for p in ranked])
                for p in pending_down:
                    cur.execute(
                        """
                        UPDATE site_alert_episodes
                        SET down_alert_sent_at = now(), escalation_step = escalation_step + 1, mass_event = true
                        WHERE id = %s
                        """,
                        (p["episode_id"],),
                    )
                _deliver_mass(cur, pending_down, message, recipients)
            else:
                # Battery recommendation (Task 3) — on this individual path,
                # only step 1 (the first "still down" reminder) gets one.
                # Computed once for every step-1 site in this pass so they're
                # ranked and allocated distinct batteries together, not one
                # at a time in isolation.
                step1_sites = [p for p in pending_down if p["step"] == 1]
                battery_assignments = _battery_recommendations(cur, [
                    {"site_id": p["site_id"], "sessions_at_drop": p["sessions_at_drop"]} for p in step1_sites
                ])
                for p in pending_down:
                    title = f"{p['name']} is down" if p["step"] == 0 else f"{p['name']} still down"
                    battery_note = _battery_note(t, battery_assignments.get(p["site_id"]))
                    message = _down_message(t, p["name"], p["sessions_at_drop"], p["opened_at_eat"], p["step"], p["downtime"], battery_note)
                    cur.execute(
                        """
                        UPDATE site_alert_episodes
                        SET down_alert_sent_at = now(), escalation_step = escalation_step + 1
                        WHERE id = %s
                        """,
                        (p["episode_id"],),
                    )
                    _deliver(cur, p["episode_id"], p["site_id"], "down", title, message, recipients)

        conn.commit()
