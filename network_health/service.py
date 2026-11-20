"""Network Health's flow: store a batch, judge it, text about it.

    ingest()   a shipper batch: store it (2xx only after that), then judge
               every new reading in time order, then the silence checks.
    tick()     the silence checks on their own, at most once a minute. Called
               from the CCR2116's /network/ingest heartbeat (a second clock
               that keeps running when nobody has a page open and when the
               Frankfurt feed itself has stopped).
    status()   the silence checks, then 200 / 503 for an outside uptime
               checker.

There is no background worker: these three are the only clocks. SMS go out
on a short-lived thread after the transaction commits, so a slow or failing
SMS provider never delays or breaks an ingest.
"""
import logging
import threading
import time
from datetime import timedelta

from network_health import alerts, config, feed, messages, routes, rules, store

log = logging.getLogger(__name__)

TICK_EVERY_SECONDS = 60
CLEANUP_EVERY_SECONDS = 3600
_tick_lock = threading.Lock()
_last_tick = 0.0
_ticking = False
_last_cleanup = 0.0


# ---- Ingest ----

def ingest(source, sent_at, records):
    """Stores a batch, then judges it. Returns counts for the shipper's log.
    Judging or texting going wrong is logged, never raised: the batch is
    stored, and a non-2xx would only make the shipper resend it."""
    parsed, ignored, skipped = feed.parse_batch(records)
    if skipped:
        log.warning("network health: skipped %s record(s), first reason: %s", len(skipped), skipped[0])
    new_records = store.save_batch(source, sent_at, parsed)
    try:
        evaluate(new_records)
    except Exception:
        log.exception("network health: judging the batch failed (the batch itself is stored)")
    cleanup_if_due()
    return {"stored": len(new_records), "duplicates": len(parsed) - len(new_records),
            "ignored": ignored, "skipped": len(skipped)}


def evaluate(new_records=()):
    """Judges the new readings (oldest first) and the silence checks, in one
    transaction, then sends whatever SMS that produced."""
    now = store.now_utc()
    with store.transaction() as cur:
        store.lock_evaluation(cur)
        run = Evaluation(cur, now)
        run.judge_readings(new_records)
        run.judge_silence()
        to_send = run.finish()
    send_in_background(to_send)


class Evaluation:
    """One pass of judging, inside one transaction. Collects the fire/clear
    events as it goes; finish() turns them into SMS log rows."""

    def __init__(self, cur, now):
        self.cur = cur
        self.now = now
        self.states = store.load_states(cur)
        self.settings = store.settings(cur)
        self.changed = set()
        self.events = []

    def baseline_start(self, moment):
        """Baselines cover the last 7 days, but never reach back past a
        provider switch: the old provider's 'usual' isn't the new one's."""
        start = moment - timedelta(days=config.BASELINE_DAYS)
        switched = self.settings["provider_switched_at"]
        return max(start, switched) if switched else start

    # -- the readings --

    def judge_readings(self, new_records):
        by_kind = {}
        for record in sorted(new_records, key=lambda r: r["ts"]):
            by_kind.setdefault(record["kind"], []).append(record)
        if "probe" in by_kind:
            self.judge_probes([r["ts"] for r in by_kind["probe"]])
        if "inner" in by_kind:
            self.judge_inner([r["ts"] for r in by_kind["inner"]])
        if "link" in by_kind:
            self.judge_links(by_kind["link"])
        for record in by_kind.get("speed", []):
            self.judge_speed(record)

    def judge_probes(self, times):
        """Every check the CCR2116's per-minute readings drive, once per new
        reading, each over the window ending at that reading."""
        longest = max(config.UPLINK_WINDOW_MINUTES, config.LATENCY_WINDOW_MINUTES, config.LOSS_WINDOW_MINUTES,
                      config.JITTER_WINDOW_MINUTES, config.CPU_WINDOW_MINUTES)
        readings = store.readings(self.cur, ["probe"], times[0] - timedelta(minutes=longest + 1), times[-1])
        medians, earliest = store.latency_baseline(self.cur, config.LATENCY_PROBE, self.baseline_start(self.now))
        capacity = self.settings["uplink_capacity_mbps"]
        for moment in times:
            has_week = earliest is not None and earliest <= moment - timedelta(days=config.BASELINE_DAYS)
            median = medians.get(rules.eat_hour(moment)) if has_week else None
            for check in rules.probe_checks(readings, moment, capacity, median):
                self.apply(check, moment)

    def judge_inner(self, times):
        readings = store.readings(self.cur, ["inner"], times[0] - timedelta(minutes=config.CPU_WINDOW_MINUTES + 1),
                                  times[-1])
        for moment in times:
            self.apply(rules.check_cpu(readings, moment, "ccr2004"), moment)

    def judge_links(self, records):
        """Each 5-minute SFP reading against the one before it (counters) and
        against its 7-day average (optical power)."""
        previous = store.previous_reading(self.cur, "link", records[0]["ts"])
        for record in records:
            self.apply(rules.check_link_down(previous and previous["link"], record["link"]), record["ts"])
            self.apply(rules.check_link_errors(previous and previous["link"], record["link"]), record["ts"])
            average, count = store.optic_baseline(self.cur, self.baseline_start(record["ts"]), record["ts"])
            self.apply(rules.check_optic(record["link"], average, count), record["ts"])
            previous = record

    def judge_speed(self, record):
        median, count = store.night_speed_baseline(self.cur, self.baseline_start(record["ts"]), record["ts"])
        self.apply(rules.check_speed(record["ts"], record["speed"], median, count), record["ts"])

    # -- silence --

    def judge_silence(self):
        last_post = store.feed(self.cur)["last_post_at"]
        self.apply(rules.check_feed_silent(last_post, self.now), self.now)
        for device, kind in (("ccr2116", "probe"), ("ccr2004", "inner")):
            last = store.last_received_at(self.cur, kind)
            self.apply(rules.check_router_silent(device, last, last_post, self.now), self.now)

    # -- state and incidents --

    def apply(self, check, at):
        """Feeds one check through its rule's hysteresis and records what
        happened. A reading older than the rule's last check (arriving late)
        is stored but not judged: the rule has already moved past it."""
        key = (check["device"], check["rule"], check["subject"])
        state = self.states.get(key) or rules.new_state()
        if state["last_checked_at"] is not None and at <= state["last_checked_at"]:
            return
        state, transition = rules.step(state, check)
        state["last_checked_at"] = at
        if transition == "fire":
            is_flapping = store.cleared_since(self.cur, key, at - timedelta(minutes=config.FLAPPING_MINUTES))
            state["incident_id"] = store.open_incident(self.cur, check, at, is_flapping)
            self.events.append(self.event("fire", check, at, at, state["incident_id"], is_flapping, False))
        elif transition in ("worse", "hold") and check["verdict"] and state["incident_id"]:
            store.update_incident(self.cur, state["incident_id"], check, check["rule"] in rules.LOWER_IS_WORSE)
            if transition == "worse":
                self.escalate(check, at, state["incident_id"])
        elif transition == "clear" and state["incident_id"]:
            closed = store.close_incident(self.cur, state["incident_id"], at)
            if closed:
                event = self.event("clear", check, closed["started_at"], at, state["incident_id"],
                                   closed["is_flapping"], closed["fire_sms_sent"])
                event["peak_severity"] = closed["severity"]
                self.events.append(event)
            state["incident_id"] = None
        self.states[key] = state
        self.changed.add(key)

    def escalate(self, check, at, incident_id):
        """AMBER -> RED on an open incident. Any RED texts, so if the AMBER
        fire wasn't texted, this is its fire SMS."""
        store.raise_incident_severity(self.cur, incident_id, check["severity"])
        fire_sent, is_flapping = store.incident_fire_sent(self.cur, incident_id)
        if not fire_sent:
            self.events.append(self.event("fire", check, at, at, incident_id, is_flapping, False))

    def event(self, kind, check, started_at, ended_at, incident_id, is_flapping, fire_sms_sent):
        return {"kind": kind, "device": check["device"], "rule": check["rule"], "subject": check["subject"],
                "severity": check["severity"], "facts": check["facts"], "started_at": started_at,
                "ended_at": ended_at, "record_ts": ended_at, "incident_id": incident_id,
                "is_flapping": is_flapping, "fire_sms_sent": fire_sms_sent}

    def finish(self):
        """Saves rule states and logs the SMS rows. Returns [(id, message)]
        still to send."""
        for key in self.changed:
            store.save_state(self.cur, key, self.states[key])
        if not self.events:
            return []
        phones = alerts.alert_phones()
        context = {"now": self.now, "mode": alerts.sms_mode(), "cap": alerts.daily_cap(),
                   "recipients": len(phones), **store.alert_context(self.cur, self.now)}
        rows = alerts.plan(self.events, context)
        ids = store.log_alerts(self.cur, rows, len(phones))
        sent_fires = [i for row in rows if row["outcome"] == "pending" and row["kind"] in ("fire", "combined")
                      and row["rule"] != "CLEARED" for i in row["incident_ids"]]
        store.mark_fire_sms_sent(self.cur, sent_fires)
        return [(alert_id, row["message"]) for alert_id, row in zip(ids, rows) if row["outcome"] == "pending"]


# ---- Sending ----

def send_in_background(to_send):
    if to_send:
        threading.Thread(target=_send_all, args=(to_send,), name="network-health-sms", daemon=True).start()


def _send_all(to_send):
    phones = alerts.alert_phones()
    for alert_id, message in to_send:
        try:
            outcome, reason = alerts.deliver(message, phones)
            store.finish_alert(alert_id, outcome, reason)
        except Exception:
            log.exception("network health: sending SMS #%s failed", alert_id)


# ---- The other two clocks ----

def tick():
    """The silence checks, at most once a minute, on their own thread. Never
    blocks the caller (the CCR2116 heartbeat), never raises."""
    global _last_tick, _ticking
    try:
        now = time.monotonic()
        with _tick_lock:
            if _ticking or now - _last_tick < TICK_EVERY_SECONDS:
                return
            _ticking, _last_tick = True, now
        threading.Thread(target=_run_tick, name="network-health-tick", daemon=True).start()
    except Exception:
        log.exception("network health: tick failed")


def _run_tick():
    global _ticking
    try:
        evaluate()
        cleanup_if_due()
    except Exception:
        log.exception("network health: silence check failed")
    finally:
        with _tick_lock:
            _ticking = False


def cleanup_if_due():
    """Deletes readings past KEEP_READINGS_DAYS, at most once an hour."""
    global _last_cleanup
    now = time.monotonic()
    if now - _last_cleanup < CLEANUP_EVERY_SECONDS:
        return
    _last_cleanup = now
    try:
        deleted = store.delete_old_readings()
        if deleted:
            log.info("network health: deleted %s old reading(s)", deleted)
    except Exception:
        log.exception("network health: deleting old readings failed")


def status():
    """Runs the silence checks, then (http code, body): 503 when anything is
    RED or OFFLINE. The body names the worst problem and nothing else; the
    caller is an outside uptime checker with only the status key."""
    evaluate()
    problems = [r for r in store.active_rules() if r["severity"] in (rules.RED, rules.OFFLINE)]
    if not problems:
        return 200, {"status": "ok"}
    worst = max(problems, key=lambda r: rules.SEVERITY_RANK[r["severity"]])
    return 503, {"status": "problem", "count": len(problems),
                 "worst": f"{rules.DEVICE_LABELS.get(worst['device'], worst['device'])}: {worst['rule']}"}


# ---- The page ----

def describe(active):
    """One active rule as the page shows it."""
    return {**active, "label": rules.RULE_LABELS.get(active["rule"], active["rule"]),
            "device_label": rules.DEVICE_LABELS.get(active["device"], active["device"]),
            "text": messages.problem_text(active["rule"], active["facts"]) if active["rule"] != "FEED_SILENT"
            else f"no data from the Frankfurt server for {messages.number(active['facts'].get('minutes'))} min"}


def overview():
    feed_row, settings, latest = store.page_basics()
    active = [describe(a) for a in store.active_rules()]
    feed_is_down = any(a["rule"] == "FEED_SILENT" for a in active)
    last_seen = {"feed": feed_row["last_post_at"], "ccr2116": latest.get("probe"), "ccr2004": latest.get("inner")}
    devices = []
    for device in ("feed", "ccr2116", "ccr2004"):
        own = [a for a in active if a["device"] == device]
        status = rules.device_status([a["severity"] for a in own])
        if device != "feed" and feed_is_down:
            status = "UNKNOWN"          # nothing can be said while the feed is down
        if last_seen[device] is None:
            status = "NOT_REPORTING"    # never sent anything: not "all good" (the CCR2004 until its script is in)
        devices.append({"device": device, "label": rules.DEVICE_LABELS[device], "status": status,
                        "last_seen": last_seen[device], "active": own})
    worst = max(active, key=lambda a: (rules.SEVERITY_RANK[a["severity"]],
                                       -(a["started_at"].timestamp() if a["started_at"] else 0)), default=None)
    sms = store.alerts_since(store.eat_midnight(store.now_utc()))
    return {
        "banner": None if worst is None else worst,
        # The shipper has never posted: say so instead of "All good".
        "waiting": feed_row["last_post_at"] is None,
        "devices": devices,
        "feed": {"source": feed_row["source"], "from_ip": feed_row["from_ip"], "last_sent_at": feed_row["last_sent_at"]},
        "settings": settings,
        "sms": {"mode": alerts.sms_mode(), "cap": alerts.daily_cap(), "recipients": len(alerts.alert_phones()),
                "sent_today": sum(1 for a in sms if a["outcome"] in ("sent", "pending") and a["kind"] != "cap"),
                "log": sms},
        "generated_at": store.now_utc(),
    }


def routes_card():
    """'Where content is served from': one row per site, its latest check and
    whether the verdict changed since the previous check of the same slot."""
    since = store.now_utc() - timedelta(days=config.ROUTE_LOOKBACK_DAYS)
    return {"sites": routes.site_rows(store.route_records(since)),
            "labels": routes.VERDICT_LABELS, "slots": routes.SLOT_LABELS,
            "limits": {"kenya_under_ms": config.ROUTE_KENYA_UNDER_MS, "abroad_from_ms": config.ROUTE_ABROAD_FROM_MS}}


def charts(range_name):
    hours, bucket = (24, 300) if range_name == "24h" else (24 * 7, 1800)
    series = store.chart_series(store.now_utc() - timedelta(hours=hours), bucket)
    with store.transaction() as cur:
        settings = store.settings(cur)
    return {"range": range_name, "bucket_seconds": bucket, **series, "settings": settings,
            "thresholds": {"uplink_pct": config.UPLINK_CONGESTED_PCT, "optic_floor_dbm": config.OPTIC_FLOOR_DBM,
                           "loss_amber_pct": config.LOSS_AMBER_PCT, "loss_red_pct": config.LOSS_RED_PCT,
                           "jitter_ms": config.JITTER_HIGH_MS, "cpu_pct": config.CPU_HIGH_PCT}}


def thresholds():
    """config.py's numbers, for the read-only Settings panel."""
    return [{"name": name, "value": getattr(config, name)} for name in dir(config)
            if name.isupper() and not name.startswith("_")]
