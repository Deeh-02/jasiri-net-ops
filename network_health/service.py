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
from datetime import datetime, timedelta, timezone

from network_health import access, alerts, config, feed, inside, messages, routes, rules, store

log = logging.getLogger(__name__)

TICK_EVERY_SECONDS = 60
CLEANUP_EVERY_SECONDS = 3600
_tick_lock = threading.Lock()
_last_tick = 0.0
_ticking = False
_last_cleanup = 0.0
# The last hour the light-degrading check ran for (each worker keeps its
# own; the rule state's last-checked time stops a second worker repeating it).
_light_checked_hour = None


# ---- Ingest ----

def ingest(source, sent_at, records):
    """Stores a batch, then judges it. Returns counts for the shipper's log.
    Judging or texting going wrong is logged, never raised: the batch is
    stored, and a non-2xx would only make the shipper resend it."""
    parsed, ignored, skipped = feed.parse_batch(records)
    if skipped:
        log.warning("network health: skipped %s record(s), first reason: %s", len(skipped), skipped[0])
    new_records, replaced = store.save_batch(source, sent_at, parsed)
    try:
        evaluate(new_records)
    except Exception:
        log.exception("network health: judging the batch failed (the batch itself is stored)")
    cleanup_if_due()
    return {"stored": len(new_records), "replaced": replaced,
            "duplicates": len(parsed) - len(new_records) - replaced, "ignored": ignored, "skipped": len(skipped)}


def evaluate(new_records=()):
    """Judges the new readings (oldest first) and the silence checks, in one
    transaction, then sends whatever SMS that produced."""
    now = store.now_utc()
    with store.transaction() as cur:
        store.lock_evaluation(cur)
        run = Evaluation(cur, now)
        run.judge_readings(new_records)
        run.judge_silence()
        run.judge_access()
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
        self._onts = self._sessions = self._overrides = None
        self.newest_ont_ts = None

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
        for record in by_kind.get("inner", []):
            self.judge_paths(record, "ccr2004", lambda n: n in config.INNER_PATH_PROBES, "INNER_PATH")
        for record in by_kind.get("box", []):
            check = rules.check_box_big_loss(record["probes"])
            if check:
                self.apply(check, record["ts"])
        for record in by_kind.get("boxspeed", []):
            check = rules.check_box_slow(record["speed"])
            if check:
                self.apply(check, record["ts"])
        for record in by_kind.get("hop", []):
            self.judge_paths(record, "olt", lambda n: n.startswith(config.CUSTOMER_PATH_PREFIX), "CUSTOMER_PATH")
        if "area" in by_kind:
            self.judge_areas(by_kind["area"])
        if "lanlink" in by_kind:
            self.judge_lanlinks(by_kind["lanlink"])
        if "link" in by_kind:
            self.judge_links(by_kind["link"])
        for record in by_kind.get("speed", []):
            self.judge_speed(record)
        for record in by_kind.get("dns", []):
            self.apply(rules.check_dns_router(record["dns"]), record["ts"])
        if "olt" in by_kind:
            self.judge_olt(by_kind["olt"])
        for record in by_kind.get("crs_link", []):
            check = rules.check_olt_link(record["crs_link"])
            if check:
                self.apply(check, record["ts"])
        if "ont" in by_kind:
            self.judge_onts(by_kind["ont"])
        for record in by_kind.get("ppp", []):
            self.judge_ppp(record)

    # -- the access network (`ont`, `ppp`; `pon` is only shown) --

    def onts(self):
        """{(pon, ont_id): nh_onts row}, read once per pass."""
        if self._onts is None:
            self._onts = store.load_onts(self.cur)
        return self._onts

    def onts_by_key(self):
        return {access.ont_key(*key): ont for key, ont in self.onts().items()}

    def sessions(self):
        if self._sessions is None:
            self._sessions = store.load_ppp_sessions(self.cur)
        return self._sessions

    def overrides(self):
        if self._overrides is None:
            self._overrides = store.ont_matches(self.cur)
        return self._overrides

    def judge_onts(self, records):
        """Each new ONT reading against that ONT's state: down / back events.
        Whether a down was one ONT or part of a group is judged later, once
        its whole poll is in (settle_downs)."""
        onts, changed, events = self.onts(), {}, []
        for record in records:
            reading = record["ont"]
            key = (reading["pon"], reading["ont_id"])
            new, event = access.apply_ont(onts.get(key), reading, record["ts"])
            if new is None:
                continue
            onts[key] = changed[key] = new
            if event:
                events.append(event)
            self.newest_ont_ts = max(self.newest_ont_ts or record["ts"], record["ts"])
        store.save_onts(self.cur, list(changed.values()))
        store.add_access_events(self.cur, events)

    def judge_ppp(self, record):
        """One router's session list against its last one: dropped / back
        events, a mass drop, sites gone, lines up with no session. The
        PPP_MASS_DROP state (checked on every reading) is the router's
        last-reading time, so a late reading is skipped."""
        ppp, ts = record["ppp"], record["ts"]
        dev = ppp["dev"]
        guard = self.states.get((dev, "PPP_MASS_DROP", ""))
        last = guard["last_checked_at"] if guard else None
        if last is not None and ts <= last:
            return
        every = self.sessions()
        sessions, events, before, dropped = access.apply_ppp(every.get(dev, {}), ppp["names"], ts)
        every[dev] = sessions
        store.add_access_events(self.cur, [{**event, "dev": dev} for event in events])
        store.save_ppp_sessions(self.cur, dev, sessions)
        open_before = None
        if guard and guard["is_active"] and guard["incident_id"]:
            open_before = store.incident_facts(self.cur, guard["incident_id"])[0].get("before")
        gap = None if last is None else (ts - last).total_seconds() / 60
        self.apply(access.mass_drop_check(dev, before, len(ppp["names"]), dropped, gap, open_before), ts)
        for check in access.site_checks(dev, sessions, ts):
            self.apply_tracked(check, ts)
        onts = self.onts_by_key()
        for check in access.line_checks(dev, sessions, onts, access.match_onts(onts, every, self.overrides()), ts):
            self.apply_tracked(check, ts)

    def judge_access(self):
        """Every pass (ingest and the tick): judge the ONT polls that are
        complete, close group / light-loss incidents whose ONTs are back,
        and once an hour the light-degrading check."""
        self.settle_downs()
        self.reconcile_access()
        if self.newest_ont_ts is not None:
            self.judge_light()

    def settle_downs(self):
        """ONTs gone down, judged per PON per poll once the poll is complete:
        the next poll has started arriving, or nothing new has come in for
        ACCESS_POLL_SETTLE_SECONDS. A group (or the whole PON) is one
        incident on the PON; a lone light loss is a page warning on the ONT;
        a lone power loss is only listed."""
        downs = store.ungrouped_downs(self.cur)
        if not downs:
            return
        newest_ts, newest_received = store.ont_newest(self.cur)
        quiet = (newest_received is None
                 or (self.now - newest_received).total_seconds() >= config.ACCESS_POLL_SETTLE_SECONDS)
        margin = timedelta(seconds=config.ACCESS_POLL_GAP_SECONDS)
        marks = []
        for pon, clusters in access.poll_clusters(downs).items():
            for cluster in clusters:
                start, end = cluster[0]["at"], cluster[-1]["at"]
                if not quiet and (newest_ts is None or newest_ts - end < margin):
                    break           # this poll (and any later one on this PON) may still be arriving
                online, readings = store.poll_counts(self.cur, pon, start - margin, end + margin)
                kind = access.classify_group(cluster, online, readings)
                if kind:
                    rule = access.GROUP_RULES[kind]
                    state = self.states.get(("olt", rule, pon))
                    previous = None
                    if state and state["is_active"] and state["incident_id"]:
                        previous = store.incident_facts(self.cur, state["incident_id"])[0]
                    self.apply(access.group_check(kind, pon, cluster, previous), start, bump=True)
                else:
                    for event in cluster:
                        if event["detail"].get("why") == "light":
                            self.apply(access.light_loss_check(event), event["at"], bump=True)
                together = "single" if len(cluster) == 1 else "few"
                marks += [(event["id"], {"group": kind or together, "cluster": start.isoformat(), "size": len(cluster)})
                          for event in cluster]
        store.mark_grouped(self.cur, marks)

    def reconcile_access(self):
        """Open PON group incidents clear once enough of their ONTs are back;
        a light-loss warning clears when its ONT is back (or forgotten)."""
        open_rules = [(key, state) for key, state in self.states.items() if state["is_active"]
                      and key[1] in (*access.GROUP_RULES.values(), "ONT_LIGHT_LOSS")]
        if not open_rules:
            return
        onts = self.onts_by_key()
        for (device, rule, subject), state in open_rules:
            if rule == "ONT_LIGHT_LOSS":
                ont = onts.get(subject)
                if ont is None or ont["status"] == access.ONLINE:
                    check = rules.make_check(device, rule, False, subject=subject,
                                             facts={"name": access.ont_name(ont) if ont else subject})
                    self.apply(check, ont["status_since"] if ont else self.now, bump=True)
                continue
            if not state["incident_id"]:
                continue
            facts, started = store.incident_facts(self.cur, state["incident_id"])
            still, clear_at = access.group_recovery(facts.get("onts") or [], onts, started or self.now)
            if clear_at is not None:
                check = rules.make_check(device, rule, False, severity=access.GROUP_SEVERITY[rule], subject=subject,
                                         facts={**facts, "still_offline": still})
                self.apply(check, clear_at, bump=True)

    def judge_light(self):
        """Once an hour (the first ONT reading of a new hour): each ONT's
        median light over the last 24 h against the 7 days before."""
        global _light_checked_hour
        hour = self.newest_ont_ts.replace(minute=0, second=0, microsecond=0)
        if _light_checked_hour is not None and hour <= _light_checked_hour:
            return
        rows = store.ont_hours(self.cur, hour - timedelta(days=config.BASELINE_DAYS + 1), hour)
        for check in access.degrade_checks(access.light_drops(rows, hour), self.onts_by_key()):
            self.apply_tracked(check, hour)
        _light_checked_hour = hour

    def judge_paths(self, record, device, is_judged, rule_prefix):
        """Per reading: each judged probe's loss and outage (i-fra* on the
        CCR2004's way out; h-cust* through the OLT). Other probes: shown only."""
        for probe in record.get("probes") or []:
            if is_judged(probe["name"]):
                for check in rules.check_path(probe, device, f"{rule_prefix}_LOSS", f"{rule_prefix}_DOWN"):
                    self.apply(check, record["ts"])

    def judge_areas(self, records):
        """Each new VLAN reading's download rate (against that VLAN's reading
        before it, up to an hour back) for AREA_SILENT. The 'usual' is that
        hour's median over the BASELINE_DAYS before this batch."""
        first, last = records[0]["ts"], records[-1]["ts"]
        new = {(r["ts"], r["area"]["vlan"]) for r in records}
        steps = inside.area_rates(store.area_readings(self.cur, first - timedelta(hours=1), last))
        medians, first_seen = store.area_baselines(self.cur, first - timedelta(days=config.BASELINE_DAYS), first)
        names = store.area_names(self.cur)
        for step in steps:
            if (step["ts"], step["vlan"]) not in new:
                continue
            vlan = step["vlan"]
            usual = medians.get(vlan, {}).get(rules.eat_hour(step["ts"]))
            check = rules.check_area_silent(vlan, inside.area_name(vlan, names), step, usual, first_seen.get(vlan))
            self.apply(check, step["ts"])

    def judge_lanlinks(self, records):
        """Each new inside-port reading against that port's previous one."""
        new = {(r["ts"], r["lanlink"]["dev"], r["lanlink"]["port"]) for r in records}
        readings = store.lanlink_readings(self.cur, records[0]["ts"] - timedelta(hours=1), records[-1]["ts"])
        for step in inside.lanlink_steps(readings):
            if (step["ts"], step["dev"], step["port"]) in new:
                for check in rules.check_lanlink(step):
                    self.apply(check, step["ts"])

    def judge_olt(self, records):
        """Each new OLT reading against the ones before it (a day back is
        plenty to find the last uptime and the previous reading)."""
        new_times = {r["ts"] for r in records}
        readings = store.olt_readings(self.cur, records[0]["ts"] - timedelta(days=1), records[-1]["ts"])
        for step in inside.walk_olt(readings):
            if step["ts"] in new_times:
                self.apply(rules.check_olt_restart(step), step["ts"])
                self.apply(rules.check_olt_crc(step), step["ts"])

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
        last_probe = None
        for device, kind in (("ccr2116", "probe"), ("ccr2004", "inner")):
            last = store.last_received_at(self.cur, kind)
            last_probe = last if kind == "probe" else last_probe
            self.apply(rules.check_router_silent(device, last, last_post, self.now), self.now)
        self.apply(rules.check_olt_snmp(store.last_olt_success_received(self.cur), last_probe, last_post, self.now),
                   self.now)

    # -- state and incidents --

    def apply_tracked(self, check, at):
        """apply(), but a "fine" check on a rule that has never been bad
        writes nothing: the per-site / per-ONT rules would otherwise keep a
        state row for every site and ONT, rewritten every 5 minutes."""
        state = self.states.get((check["device"], check["rule"], check["subject"]))
        if check["verdict"] is False and (state is None or (not state["is_active"] and not state["bad_count"])):
            return
        self.apply(check, at)

    def apply(self, check, at, bump=False):
        """Feeds one check through its rule's hysteresis and records what
        happened. A reading older than the rule's last check (arriving late)
        is stored but not judged: the rule has already moved past it.
        bump: judged exactly once whatever its time (a settled ONT poll), so
        it goes just after the last check instead of being skipped."""
        key = (check["device"], check["rule"], check["subject"])
        state = self.states.get(key) or rules.new_state()
        if state["last_checked_at"] is not None and at <= state["last_checked_at"]:
            if not bump:
                return
            at = state["last_checked_at"] + timedelta(microseconds=1)
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
    # One customer's line (a lone ONT, a missing session) stays in the Access
    # network section; access ALERTS (a PON, a site, a mass drop) may top the
    # banner, but the device cards stay about the devices themselves.
    active = [describe(a) for a in store.active_rules() if a["rule"] not in rules.ACCESS_WARNINGS]
    feed_is_down = any(a["rule"] == "FEED_SILENT" for a in active)
    last_seen = {"feed": feed_row["last_post_at"], "ccr2116": latest.get("probe"), "ccr2004": latest.get("inner")}
    devices = []
    for device in ("feed", "ccr2116", "ccr2004"):
        own = [a for a in active if a["device"] == device and a["rule"] not in rules.ACCESS_RULES]
        status = rules.device_status([a["severity"] for a in own])
        if device != "feed" and feed_is_down:
            status = "UNKNOWN"          # nothing can be said while the feed is down
        if last_seen[device] is None:
            status = "NOT_REPORTING"    # never sent anything: not "all good" (the CCR2004 until its script is in)
        devices.append({"device": device, "label": rules.DEVICE_LABELS[device], "status": status,
                        "last_seen": last_seen[device], "active": own})
    worst = max(active, key=lambda a: (rules.SEVERITY_RANK[a["severity"]],
                                       -(a["started_at"].timestamp() if a["started_at"] else 0)), default=None)
    return {
        "banner": None if worst is None else worst,
        # The shipper has never posted: say so instead of "All good".
        "waiting": feed_row["last_post_at"] is None,
        "devices": devices,
        "feed": {"source": feed_row["source"], "from_ip": feed_row["from_ip"], "last_sent_at": feed_row["last_sent_at"]},
        "settings": settings,
        "sms": sms_card(1),
        "generated_at": store.now_utc(),
    }


def sms_card(days=1):
    """The alert texts Network Health sent (or would have, in log-only mode),
    the last `days` Nairobi days. Shown on the SMS Status page."""
    today = store.eat_midnight(store.now_utc())
    log = store.alerts_since(today - timedelta(days=days - 1))
    return {"mode": alerts.sms_mode(), "cap": alerts.daily_cap(), "recipients": len(alerts.alert_phones()),
            "days": days,
            "sent_today": sum(1 for a in log if a["created_at"] >= today and a["outcome"] in ("sent", "pending")
                              and a["kind"] != "cap"),
            "log": log}


def areas_card():
    """The Areas page under Hotspot Status: each CCR2004 VLAN's download and
    upload now, and its last 24 hours."""
    now = store.now_utc()
    with store.transaction() as cur:
        names = store.area_names(cur)
        sparks = store.area_series(cur, now - timedelta(hours=24), config.AREA_SPARK_BUCKET_SECONDS)
        latest_rates = inside.area_rates(store.area_readings(cur, now - timedelta(hours=1), now))
    return {"areas": area_rows(names, sparks, latest_rates),
            "active": [describe(a) for a in store.active_rules() if a["rule"] == "AREA_SILENT"],
            "thresholds": {"area_silent_mbps": config.AREA_SILENT_MBPS}}


def box_card(dev=None):
    """'Customer line (probe)': the probe machine on a real customer line.
    Its speed (Cloudflare and Frankfurt downloads against the plan) and its
    ping loss, small against 1400-byte packets, over BOX_CHART_DAYS, with the
    owner's manual markers (its alerts are listed with the Inside network's). `dev`: which machine (the newest reporter by default)."""
    now = store.now_utc()
    since = now - timedelta(days=config.BOX_CHART_DAYS)
    with store.transaction() as cur:
        devs = store.box_devs(cur, since)
        dev = dev if dev in devs else (devs[0] if devs else None)
        markers = store.box_markers(cur)
        card = {"dev": dev, "label": config.BOX_NAMES.get(dev, dev) if dev else None,
                "devs": [{"dev": d, "label": config.BOX_NAMES.get(d, d)} for d in devs],
                "days": config.BOX_CHART_DAYS, "bucket_seconds": config.BOX_CHART_BUCKET_SECONDS,
                "since": since, "now": now, "targets": list(config.BOX_TARGETS),
                "loss": [], "speed": [], "latest": None, "speed_now": None,
                "markers": [{"id": m["id"], "at": m["at"], "label": m["label"]} for m in markers],
                "thresholds": {"big_loss_pct": config.BOX_BIG_LOSS_PCT, "big_loss_in_a_row": config.BOX_BIG_LOSS_IN_A_ROW,
                               "slow_pct": round(config.BOX_SLOW_FRACTION * 100),
                               "slow_in_a_row": config.BOX_SLOW_IN_A_ROW}}
        if dev is None:
            return card
        card["loss"] = store.box_loss_series(cur, dev, since, config.BOX_CHART_BUCKET_SECONDS)
        card["speed"] = store.box_speeds(cur, dev, since)
        at, probes = store.box_latest(cur, dev)
    if at is not None:
        card["latest"] = {"at": at, "probes": {p["name"]: {"loss": p.get("loss_pct"), "rtt": p.get("rtt_ms")}
                                               for p in probes}}
    if card["speed"]:
        last = card["speed"][-1]
        pct = None if last["cf_mbps"] is None or not last["plan_mbps"] else last["cf_mbps"] / last["plan_mbps"] * 100
        card["speed_now"] = {"at": last["t"], "cf_mbps": last["cf_mbps"], "fra_mbps": last["fra_mbps"],
                             "plan_mbps": last["plan_mbps"], "pct_of_plan": pct}
    return card


def routes_card():
    """'Where content is served from': one row per site, its latest check and
    whether the verdict changed since the previous check of the same slot."""
    since = store.now_utc() - timedelta(days=config.ROUTE_LOOKBACK_DAYS)
    return {"sites": routes.site_rows(store.route_records(since)),
            "labels": routes.VERDICT_LABELS, "slots": routes.SLOT_LABELS,
            "limits": {"kenya_under_ms": config.ROUTE_KENYA_UNDER_MS, "za_from_ms": config.ROUTE_ZA_FROM_MS,
                       "za_under_ms": config.ROUTE_ZA_UNDER_MS, "abroad_from_ms": config.ROUTE_ABROAD_FROM_MS}}


def inside_card(range_name):
    """'Inside network': the OLT uplink's damaged frames over the range, the
    OLT's restarts and the CRS326's port events over INSIDE_LOOKBACK_DAYS,
    and whether the OLT is still answering SNMP."""
    now = store.now_utc()
    hours, bucket = (24, 300) if range_name == "24h" else (24 * 7, 1800)
    since = now - timedelta(hours=hours)
    lookback = now - timedelta(days=config.INSIDE_LOOKBACK_DAYS)
    with store.transaction() as cur:
        # One reading before the range, so its first 5 minutes have a pair.
        steps = inside.walk_olt(store.olt_readings(cur, since - timedelta(minutes=15), now))
        restarts = store.olt_restarts(cur, lookback)
        links = store.crs_link_records(cur, lookback)
        last_poll, last_success = store.olt_last_reads(cur)
        names = store.area_names(cur)
        day_ago = now - timedelta(hours=24)
        sparks = store.area_series(cur, day_ago, config.AREA_SPARK_BUCKET_SECONDS)
        latest_rates = inside.area_rates(store.area_readings(cur, now - timedelta(hours=1), now))
        lanlinks = inside.lanlink_rows(store.lanlink_readings(cur, day_ago - timedelta(minutes=15), now), day_ago)
        hop = store.hop_series(cur, since, bucket)
    steps = [s for s in steps if s["ts"] > since]
    latest = next((s["interval"] for s in reversed(steps)
                   if s["interval"] and s["interval"]["crc_pct"] is not None), None)
    silent_for = None if last_success is None else (now - last_success).total_seconds() / 60
    active = [describe(a) for a in store.active_rules() if a["rule"] in rules.INSIDE_RULES]
    return {
        "range": range_name, "bucket_seconds": bucket,
        "crc": {"series": inside.crc_buckets(steps, bucket),
                "current": None if latest is None else {"t": latest["end"], "pct": latest["crc_pct"],
                                                        "level": inside.crc_level(latest["crc_pct"])},
                "range_pct": inside.overall_pct(steps),
                "green_under_pct": config.OLT_CRC_GREEN_UNDER_PCT, "red_over_pct": config.OLT_CRC_RED_OVER_PCT},
        "restarts": restarts,
        "link_events": inside.link_events(links)[:config.INSIDE_EVENTS_LIMIT],
        "snmp": {"last_success_at": last_success, "last_poll_at": last_poll,
                 "is_stale": silent_for is not None and silent_for >= config.OLT_SNMP_SILENT_MINUTES,
                 "stale_after_minutes": config.OLT_SNMP_SILENT_MINUTES,
                 "polls": len(steps), "failed_polls": sum(1 for s in steps if s["failed"])},
        "olt_interface": config.OLT_LINK_INTERFACE, "ports": config.CRS_PORT_LABELS,
        "lookback_days": config.INSIDE_LOOKBACK_DAYS,
        "active": active,
        "areas": area_rows(names, sparks, latest_rates),
        "lanlinks": lanlinks,
        "hop": hop,
        "thresholds": {"path_loss_pct": config.PATH_LOSS_PCT, "area_silent_mbps": config.AREA_SILENT_MBPS,
                       "lanlink_damage_over": config.LANLINK_DAMAGE_OVER, "lanlink_drops_over": config.LANLINK_DROPS_OVER},
    }


ROUTER_ROLES = {"ccr2116": "CCR2116 · home customers", "ccr2004": "CCR2004 · sites"}
ONT_RULES = ("ONT_LIGHT_LOSS", "ONT_LIGHT_DEGRADING", "LINE_NO_INTERNET")


def access_card():
    """'Access network': PON ports, ONTs (light, PPPoE match, 7-day light
    sparkline, likely splitter groups), the events, field jobs, stale
    registrations and the PPPoE sessions."""
    now = store.now_utc()
    with store.transaction() as cur:
        data = store.access_page(cur, now)
    active = [describe(a) for a in store.active_rules() if a["rule"] in rules.ACCESS_RULES]
    onts = {access.ont_key(*key): ont for key, ont in data["onts"].items()}
    views = [access.ont_view(ont) for ont in onts.values()]
    matches = access.match_onts(onts, data["sessions"], data["matches"])
    warnings = {}
    for a in active:
        if a["rule"] in ONT_RULES:
            warnings.setdefault(a["subject"], []).append({"rule": a["rule"], "label": a["label"], "text": a["text"]})
    step = config.LIGHT_SPARK_BUCKET_SECONDS
    start = int(data["spark_since"].timestamp()) // step * step
    points = int((now.timestamp() - start) // step) + 1
    groups = access.splitter_groups(views, access.together_counts(data["downs"]))
    group_of = {key: i for i, group in enumerate(groups) for key in group["onts"]}
    every_session = {name: (dev, s) for dev, sessions in data["sessions"].items() for name, s in sessions.items()}
    for view in views:
        rx, olt = [None] * points, [None] * points
        for bucket, rx_value, olt_value in data["light"].get(view["key"], []):
            i = int((bucket.timestamp() - start) // step)
            if 0 <= i < points:
                rx[i] = None if rx_value is None else round(rx_value, 1)
                olt[i] = None if olt_value is None else round(olt_value, 1)
        match = matches.get(view["key"])
        ppp = None
        if match:
            dev, session = every_session.get(match["ppp"] or "", (match["dev"], None))
            ppp = {**match, "dev": dev, "present": None if session is None else session["present"],
                   "missing_since": None if session is None else session["missing_since"]}
        view.update(spark={"rx": rx, "olt": olt}, ppp=ppp, group=group_of.get(view["key"]),
                    warnings=warnings.get(view["key"], []))
    views.sort(key=lambda v: (access.pon_sort_key(v["pon"]), v["ont_id"]))
    light_loss = {a["subject"] for a in active if a["rule"] == "ONT_LIGHT_LOSS"}
    timeline = [{**{k: e[k] for k in ("at", "kind", "pon", "ont_id", "dev", "name")}, **e["detail"]}
                for e in data["events"]]
    for link in inside.link_events(data["links"]):
        base = {"label": link["label"], "device_label": link["device_label"], "interface": link["interface"],
                "is_olt": link["is_olt"]}
        if link["down_at"]:
            timeline.append({**base, "at": link["down_at"], "kind": "link_down"})
        if link["up_at"]:
            timeline.append({**base, "at": link["up_at"], "kind": "link_up", "outage_s": link["outage_s"]})
    timeline.sort(key=lambda e: e["at"], reverse=True)
    routers = []
    for dev in sorted(set(data["sessions"]) | set(data["routers"])):
        sessions = data["sessions"].get(dev, {})
        missing = sorted(({"name": name, "since": s["missing_since"] or s["last_seen_at"], "site": access.is_site(name)}
                          for name, s in sessions.items() if not s["present"]), key=lambda m: m["since"], reverse=True)
        reading = data["routers"].get(dev) or {}
        routers.append({"dev": dev, "label": ROUTER_ROLES.get(dev, dev.upper()), "count": reading.get("count"),
                        "at": reading.get("ts"), "sites": sum(1 for name in sessions if access.is_site(name)),
                        "missing": missing})
    return {
        "pons": access.pon_rows(views, data["pons"], data["last_events"]),
        "onts": views,
        "spark": {"start": datetime.fromtimestamp(start, timezone.utc), "step_s": step, "points": points},
        "groups": groups,
        "events": timeline[:config.ACCESS_EVENTS_LIMIT],
        "jobs": access.field_jobs(views, light_loss),
        "stale": [v for v in views if v["stale"]],
        "routers": routers,
        "matches": [{"key": key, "name": onts[key]["descr"] if key in onts else None, "ppp_name": name}
                    for key, name in sorted(data["matches"].items())],
        "ppp_names": sorted(every_session),
        "active": active,
        "lookback_days": config.ACCESS_LOOKBACK_DAYS,
        "thresholds": {"rx_overload_above": config.ONT_RX_OVERLOAD_ABOVE_DBM, "rx_weak_below": config.ONT_RX_WEAK_BELOW_DBM,
                       "rx_bad_below": config.ONT_RX_BAD_BELOW_DBM, "olt_weak_below": config.OLT_RX_WEAK_BELOW_DBM,
                       "olt_bad_below": config.OLT_RX_BAD_BELOW_DBM, "group_min": config.ACCESS_GROUP_MIN_ONTS,
                       "splitter_m": config.SPLITTER_DISTANCE_M, "splitter_together": config.SPLITTER_TOGETHER_MIN,
                       "drop_ont_db": config.LIGHT_DROP_ONT_DB, "drop_shared_db": config.LIGHT_DROP_SHARED_DB,
                       # For the page's "What's on the OLT" panel, so it never quotes a stale number.
                       "whole_pon_min": config.WHOLE_PON_MIN_ONTS, "shared_min": config.PON_SHARED_MIN_ONTS,
                       "site_polls": config.SITE_PPP_MISSING_IN_A_ROW, "line_polls": config.LINE_NO_INTERNET_IN_A_ROW,
                       "mass_drop_pct": round(config.PPP_MASS_DROP_FRACTION * 100),
                       "mass_drop_min": config.PPP_MASS_DROP_MIN_SESSIONS},
    }


def area_rows(names, sparks, latest_rates):
    """One row per VLAN seen in the last 24 h: its name, latest rates and
    sparkline. Areas by name; the CCR2004 total last."""
    latest = {}
    for step in latest_rates:
        if step["down_mbps"] is not None or step["up_mbps"] is not None:
            latest[step["vlan"]] = step
    rows = []
    for vlan in set(sparks) | set(latest):
        step = latest.get(vlan) or {}
        rows.append({"vlan": vlan, "name": inside.area_name(vlan, names), "is_total": vlan == config.AREA_TOTAL_VLAN,
                     "down_mbps": step.get("down_mbps"), "up_mbps": step.get("up_mbps"), "at": step.get("ts"),
                     "spark": sparks.get(vlan, [])})
    return sorted(rows, key=lambda r: (r["is_total"], r["name"].lower(), r["vlan"]))


def charts(range_name):
    hours, bucket = (24, 300) if range_name == "24h" else (24 * 7, 1800)
    series = store.chart_series(store.now_utc() - timedelta(hours=hours), bucket)
    with store.transaction() as cur:
        settings = store.settings(cur)
    return {"range": range_name, "bucket_seconds": bucket, **series, "settings": settings,
            "thresholds": {"uplink_pct": config.UPLINK_CONGESTED_PCT, "optic_floor_dbm": config.OPTIC_FLOOR_DBM,
                           "loss_amber_pct": config.LOSS_AMBER_PCT, "loss_red_pct": config.LOSS_RED_PCT,
                           "jitter_ms": config.JITTER_HIGH_MS, "cpu_pct": config.CPU_HIGH_PCT,
                           "dns_slow_ms": config.DNS_SLOW_MS}}


def thresholds():
    """config.py's numbers, for the read-only Settings panel."""
    return [{"name": name, "value": getattr(config, name)} for name in dir(config)
            if name.isupper() and not name.startswith("_")]
