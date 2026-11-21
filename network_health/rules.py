"""The detection rules, as plain functions over lists of readings.

No database and no clock here: service.py fetches the readings and calls
these, tests call them with made-up readings. Every check returns a dict:

    {device, rule, subject, verdict, severity, value, facts, immediate}

verdict is True (bad), False (fine) or None (not enough data to say, which
leaves the rule as it was: missing data is never read as "fine").

step() is the hysteresis: a rule fires after FIRE_AFTER bad checks in a
row and clears after CLEAR_AFTER fine ones, per rule (see the two dicts
below), so a reading sitting right on a threshold doesn't flap.
"""
from datetime import timedelta

from network_health import config

AMBER, RED, OFFLINE = "AMBER", "RED", "OFFLINE"
SEVERITY_RANK = {None: 0, AMBER: 1, RED: 2, OFFLINE: 3}

DEVICE_LABELS = {"ccr2116": "CCR2116", "ccr2004": "CCR2004", "feed": "Frankfurt feed", "olt": "OLT"}
# What each rule is called on the page.
RULE_LABELS = {
    "FEED_SILENT": "No data from Frankfurt",
    "ROUTER_SILENT": "No readings",
    "UPLINK_CONGESTED": "Uplink busy",
    "SPEED_LOW": "Night speed test low",
    "LATENCY_HIGH": "Latency high",
    "LOSS": "Packet loss",
    "JITTER_HIGH": "Jitter high",
    "OPTIC_DEGRADING": "SFP signal weak",
    "LINK_DOWN": "Uplink dropped",
    "LINK_ERRORS": "Link errors",
    "CPU_HIGH": "CPU high",
    "DNS_ROUTER": "Router DNS failing or slow",
    "OLT_CRC": "OLT uplink damaging frames",
    "OLT_RESTART": "OLT restarted",
    "OLT_LINK_DOWN": "OLT link down",
    "OLT_SNMP_SILENT": "OLT not answering SNMP",
}
# Checks needed in a row before firing / clearing, where it isn't the default.
FIRE_AFTER = {"OPTIC_DEGRADING": 2, "LINK_ERRORS": config.LINK_ERRORS_FIRE_AFTER, "DNS_ROUTER": config.DNS_FIRE_AFTER,
              "OLT_CRC": config.OLT_CRC_FIRE_AFTER}
# LINK_DOWN clears on the next 5-minute reading with no new drop, OLT_RESTART
# on the next reading with no new restart, OLT_LINK_DOWN on the port's `up`.
CLEAR_AFTER = {"FEED_SILENT": 1, "ROUTER_SILENT": 1, "SPEED_LOW": 1, "OPTIC_DEGRADING": 2, "LINK_DOWN": 1,
               "LINK_ERRORS": config.LINK_ERRORS_FIRE_AFTER, "DNS_ROUTER": config.DNS_CLEAR_AFTER,
               "OLT_CRC": config.OLT_CRC_CLEAR_AFTER, "OLT_RESTART": 1, "OLT_LINK_DOWN": 1, "OLT_SNMP_SILENT": 1}
# For these a LOWER value is worse (an incident's "peak" is its lowest value).
LOWER_IS_WORSE = ("SPEED_LOW", "OPTIC_DEGRADING")


def make_check(device, rule, verdict, severity=AMBER, value=None, subject="", facts=None, immediate=False):
    return {"device": device, "rule": rule, "subject": subject, "verdict": verdict, "severity": severity,
            "value": None if value is None else round(value, 2), "facts": facts or {}, "immediate": immediate}


def average(values):
    """Mean of the values that exist, or None when there are none."""
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def in_window(readings, until, minutes):
    """Readings with until - minutes < ts <= until."""
    start = until - timedelta(minutes=minutes)
    return [r for r in readings if start < r["ts"] <= until]


def probe_values(readings, name, field):
    """One probe's field from every reading that has that probe, in order.
    A missing number stays None (an outage's rtt is None, not 0)."""
    values = []
    for reading in readings:
        for probe in reading.get("probes") or []:
            if probe["name"] == name:
                values.append(probe.get(field))
    return values


def utilisation_pct(load, capacity_mbps):
    """max(download, upload) as a % of capacity. Download (rx from Airtel) is
    the one that fills; upload is included so a stuck upload still shows."""
    if not load or not capacity_mbps:
        return None
    directions = [load.get("down_mbps"), load.get("up_mbps")]
    directions = [mbps for mbps in directions if mbps is not None]
    return max(directions) / capacity_mbps * 100 if directions else None


# ---- Checks on the CCR2116's per-minute `probe` readings ----

def check_uplink(readings, until, capacity_mbps):
    window = in_window(readings, until, config.UPLINK_WINDOW_MINUTES)
    pcts = [utilisation_pct(r.get("load"), capacity_mbps) for r in window]
    pcts = [p for p in pcts if p is not None]
    if len(pcts) < config.MIN_READINGS_IN_WINDOW:
        return make_check("ccr2116", "UPLINK_CONGESTED", None)
    pct = sum(pcts) / len(pcts)
    return make_check("ccr2116", "UPLINK_CONGESTED", pct > config.UPLINK_CONGESTED_PCT, value=pct,
                      facts={"pct": pct, "minutes": config.UPLINK_WINDOW_MINUTES})


def latency_limit(baseline_median_ms):
    """The 7-day median for this hour of day plus 40 ms, or the fixed
    fallback while there isn't a week of readings yet (baseline None)."""
    if baseline_median_ms is None:
        return config.LATENCY_FALLBACK_MS
    return baseline_median_ms + config.LATENCY_OVER_USUAL_MS


def check_latency(readings, until, baseline_median_ms):
    """Average rtt to Frankfurt over 10 minutes. Readings with no reply are
    left out: that's an outage, which LOSS and ROUTER_SILENT report."""
    window = in_window(readings, until, config.LATENCY_WINDOW_MINUTES)
    rtts = [v for v in probe_values(window, config.LATENCY_PROBE, "rtt_ms") if v is not None]
    if len(rtts) < config.MIN_READINGS_IN_WINDOW:
        return make_check("ccr2116", "LATENCY_HIGH", None, subject=config.LATENCY_PROBE)
    rtt = sum(rtts) / len(rtts)
    limit = latency_limit(baseline_median_ms)
    return make_check("ccr2116", "LATENCY_HIGH", rtt > limit, value=rtt, subject=config.LATENCY_PROBE,
                      facts={"ms": rtt, "usual": baseline_median_ms, "limit": limit, "probe": config.LATENCY_PROBE})


def check_loss(readings, until, probe_name):
    """Average loss over 5 minutes. Each reading's loss moves in steps of 10
    (10 pings), so a single lost ping averages 2 % (not over the line), two
    average 4 % (AMBER, no SMS), one full minute of no reply averages 20 %
    (still AMBER) and two full minutes 40 % (RED, texts)."""
    window = in_window(readings, until, config.LOSS_WINDOW_MINUTES)
    losses = [v for v in probe_values(window, probe_name, "loss_pct") if v is not None]
    if len(losses) < config.MIN_LOSS_READINGS_IN_WINDOW:
        return make_check("ccr2116", "LOSS", None, subject=probe_name)
    loss = sum(losses) / len(losses)
    severity = RED if loss > config.LOSS_RED_PCT else AMBER
    is_outage = losses[-1] >= 100
    return make_check("ccr2116", "LOSS", loss > config.LOSS_AMBER_PCT, severity=severity, value=loss,
                      subject=probe_name, facts={"loss": loss, "probe": probe_name, "outage": is_outage})


def check_jitter(readings, until, probe_name):
    window = in_window(readings, until, config.JITTER_WINDOW_MINUTES)
    jitters = [v for v in probe_values(window, probe_name, "jitter_ms") if v is not None]
    if len(jitters) < config.MIN_READINGS_IN_WINDOW:
        return make_check("ccr2116", "JITTER_HIGH", None, subject=probe_name)
    jitter = sum(jitters) / len(jitters)
    return make_check("ccr2116", "JITTER_HIGH", jitter > config.JITTER_HIGH_MS, value=jitter, subject=probe_name,
                      facts={"ms": jitter, "probe": probe_name})


def check_cpu(readings, until, device):
    """CPU on the CCR2116 (`probe`) or the CCR2004 (`inner`)."""
    window = in_window(readings, until, config.CPU_WINDOW_MINUTES)
    loads = [(r.get("load") or {}).get("cpu_pct") for r in window]
    loads = [v for v in loads if v is not None]
    if len(loads) < config.MIN_READINGS_IN_WINDOW:
        return make_check(device, "CPU_HIGH", None)
    cpu = sum(loads) / len(loads)
    return make_check(device, "CPU_HIGH", cpu > config.CPU_HIGH_PCT, value=cpu, facts={"pct": cpu})


def probe_checks(readings, until, capacity_mbps, baseline_median_ms):
    """Every check the CCR2116's probe readings drive, as of `until`."""
    checks = [check_uplink(readings, until, capacity_mbps),
              check_latency(readings, until, baseline_median_ms),
              check_cpu(readings, until, "ccr2116")]
    checks += [check_loss(readings, until, name) for name in config.LOSS_PROBES]
    checks += [check_jitter(readings, until, name) for name in config.JITTER_PROBES]
    return checks


# ---- Checks on the 5-minute `link` readings (the uplink SFP) ----

def counter_increase(previous, current):
    """How much a counter went up between two readings. A counter that went
    DOWN was reset (reboot, or someone cleared it): that's no change, and
    the new value is the next baseline."""
    if previous is None or current is None:
        return None
    return max(current - previous, 0)


def check_link_down(previous_link, link):
    """link_downs went up since the last 5-minute reading: the uplink
    actually dropped. Fires at once and texts; clears on the next reading
    with no new drop."""
    downs = None if previous_link is None else counter_increase(previous_link.get("link_downs"),
                                                               link.get("link_downs"))
    if downs is None:
        return make_check("ccr2116", "LINK_DOWN", None)
    return make_check("ccr2116", "LINK_DOWN", downs > 0, value=downs, facts={"link_downs": downs}, immediate=True)


def check_link_errors(previous_link, link):
    """The error and drop counters going up. Needs LINK_ERRORS_FIRE_AFTER
    readings in a row; page only."""
    if previous_link is None:
        return make_check("ccr2116", "LINK_ERRORS", None)
    increases = [counter_increase(previous_link.get(f), link.get(f)) for f in config.LINK_ERROR_COUNTERS]
    errors = sum(i for i in increases if i is not None)
    return make_check("ccr2116", "LINK_ERRORS", errors > 0, value=errors, facts={"errors": errors})


def check_optic(link, baseline_avg_dbm, baseline_count):
    rx = link.get("rx_dbm")
    if rx is None:
        return make_check("ccr2116", "OPTIC_DEGRADING", None)
    has_baseline = baseline_avg_dbm is not None and baseline_count >= config.OPTIC_BASELINE_MIN_READINGS
    usual = baseline_avg_dbm if has_baseline else None
    is_weak = rx < config.OPTIC_FLOOR_DBM or (usual is not None and usual - rx > config.OPTIC_DROP_DB)
    return make_check("ccr2116", "OPTIC_DEGRADING", is_weak, value=rx, facts={"rx": rx, "usual": usual})


# ---- DNS (the 5-minute `dns` readings) ----

def check_dns_router(dns):
    """One run of the router's own resolver: bad when a lookup failed or the
    slower of its two lookups took over 500 ms. Fires after DNS_FIRE_AFTER
    bad runs in a row (step() counts them). A run without the router
    resolver says nothing."""
    resolver = next((r for r in (dns or {}).get("resolvers") or []
                     if r.get("name") == config.DNS_ROUTER_RESOLVER), None)
    if resolver is None or (resolver.get("worst_ms") is None and resolver.get("fails") is None):
        return make_check("ccr2116", "DNS_ROUTER", None)
    fails = resolver.get("fails") or 0
    worst = resolver.get("worst_ms")
    is_bad = fails > 0 or (worst is not None and worst > config.DNS_SLOW_MS)
    return make_check("ccr2116", "DNS_ROUTER", is_bad, value=worst, facts={"ms": worst, "fails": fails})


# ---- Inside network (`olt` every 5 minutes, `crs_link` on each port change) ----

def check_olt_crc(step):
    """One 5-minute interval of the OLT uplink (inside.walk_olt's step):
    bad over OLT_CRC_RED_OVER_PCT damaged frames. Fires after
    OLT_CRC_FIRE_AFTER bad intervals in a row. No interval (a failed poll,
    a restart, no traffic) says nothing."""
    interval = step.get("interval")
    pct = None if interval is None else interval["crc_pct"]
    if pct is None:
        return make_check("olt", "OLT_CRC", None)
    return make_check("olt", "OLT_CRC", pct > config.OLT_CRC_RED_OVER_PCT, value=pct, facts={"pct": pct})


def check_olt_restart(step):
    """The OLT's uptime went down since the last reading: it restarted
    (power switchovers do this). Fires at once; clears on the next reading
    that has an uptime and no new restart."""
    restart = step.get("restart")
    if restart:
        return make_check("olt", "OLT_RESTART", True, facts={"previous_uptime_s": restart["previous_uptime_s"],
                                                             "uptime_s": restart["uptime_s"]}, immediate=True)
    if step.get("uptime_s") is None:
        return make_check("olt", "OLT_RESTART", None)
    return make_check("olt", "OLT_RESTART", False)


def check_olt_link(link):
    """A CRS326 port event on the OLT uplink (sfp-sfpplus3): down fires at
    once, the next up clears it. Other ports are shown, never judged."""
    if link.get("interface") != config.OLT_LINK_INTERFACE:
        return None
    return make_check("olt", "OLT_LINK_DOWN", link["state"] == "down", subject=link["interface"],
                      facts={"interface": link["interface"]}, immediate=True)


def check_olt_snmp(last_success_received_at, last_probe_received_at, last_post_at, now):
    """No complete OLT reading received for OLT_SNMP_SILENT_MINUTES. Not
    judged while the feed or the CCR2116 itself is silent (those rules
    already say why nothing arrives: the OLT is read BY the CCR2116), nor
    before the first complete reading ever."""
    feed_is_fresh = last_post_at is not None and minutes_since(last_post_at, now) < config.FEED_SILENT_MINUTES
    router_is_fresh = (last_probe_received_at is not None
                       and minutes_since(last_probe_received_at, now) < config.ROUTER_SILENT_MINUTES)
    if last_success_received_at is None or not feed_is_fresh or not router_is_fresh:
        return make_check("olt", "OLT_SNMP_SILENT", None)
    minutes = minutes_since(last_success_received_at, now)
    return make_check("olt", "OLT_SNMP_SILENT", minutes >= config.OLT_SNMP_SILENT_MINUTES, value=minutes,
                      facts={"minutes": minutes}, immediate=True)


# ---- The speed test ----

def eat_hour(moment):
    return (moment + timedelta(hours=3)).hour


def check_speed(test_ts, speed, night_median_mbps, night_test_count):
    """Only the 03:30 test is judged (the 20:30 one runs at peak). It alerts
    on a failed test, or a result under half the 7-day median of earlier
    03:30 results. Never compared with capacity: a single-stream fetch to
    Frankfurt gives about 30 Mbps on a healthy 350 Mbps link."""
    if eat_hour(test_ts) != config.SPEED_JUDGED_HOUR_EAT or speed["status"] == "unknown":
        return make_check("ccr2116", "SPEED_LOW", None)
    facts = {"mbps": speed.get("mbps"), "usual": night_median_mbps, "load": speed.get("load_before_mbps"),
             "failed": speed["status"] == "failed"}
    if facts["failed"] or facts["mbps"] is None:
        facts["failed"] = True
        return make_check("ccr2116", "SPEED_LOW", True, value=0, facts=facts)
    if night_median_mbps is None or night_test_count < config.SPEED_BASELINE_MIN_TESTS:
        return make_check("ccr2116", "SPEED_LOW", False, value=facts["mbps"], facts=facts)
    is_low = facts["mbps"] < night_median_mbps * config.SPEED_LOW_FRACTION
    return make_check("ccr2116", "SPEED_LOW", is_low, value=facts["mbps"], facts=facts)


# ---- Silence (no background worker: run on ingest, /status and the tick) ----

def minutes_since(earlier, now):
    return (now - earlier).total_seconds() / 60


def check_feed_silent(last_post_at, now):
    """No POST at all (not even the empty heartbeat) = Frankfurt or the
    shipper is down. Never posted yet = nothing to say."""
    if last_post_at is None:
        return make_check("feed", "FEED_SILENT", None, severity=OFFLINE)
    minutes = minutes_since(last_post_at, now)
    return make_check("feed", "FEED_SILENT", minutes >= config.FEED_SILENT_MINUTES, severity=OFFLINE,
                      value=minutes, facts={"minutes": minutes}, immediate=True)


def check_router_silent(device, last_received_at, last_post_at, now):
    """POSTs arriving but none of them has carried a reading from this router
    for a while. Measured from when OPS RECEIVED its newest reading, not the
    reading's own `ts`: during a backfill the shipper delivers hours-old
    lines, and those must not look like a silent router. Not judged while
    the feed itself is silent (that's FEED_SILENT's job), nor for a router
    that has never sent anything (the CCR2004 until its script is installed)."""
    feed_is_fresh = last_post_at is not None and minutes_since(last_post_at, now) < config.FEED_SILENT_MINUTES
    if last_received_at is None or not feed_is_fresh:
        return make_check(device, "ROUTER_SILENT", None, severity=OFFLINE)
    minutes = minutes_since(last_received_at, now)
    return make_check(device, "ROUTER_SILENT", minutes >= config.ROUTER_SILENT_MINUTES, severity=OFFLINE,
                      value=minutes, facts={"minutes": minutes}, immediate=True)


# ---- Hysteresis ----

def new_state():
    return {"bad_count": 0, "good_count": 0, "is_active": False, "severity": None, "incident_id": None,
            "last_checked_at": None}


def step(state, check):
    """Applies one check to a rule's state. Returns (new state, transition):
    'fire' (just went bad), 'worse' (already active, now more severe),
    'clear' (just went fine), 'hold' (active and still bad) or None."""
    state = dict(state)
    if check["verdict"] is None:
        return state, None
    rule = check["rule"]
    if check["verdict"]:
        state["bad_count"] += 1
        state["good_count"] = 0
        if not state["is_active"]:
            if check["immediate"] or state["bad_count"] >= FIRE_AFTER.get(rule, config.FIRE_AFTER):
                state["is_active"], state["severity"] = True, check["severity"]
                return state, "fire"
            return state, None
        if SEVERITY_RANK[check["severity"]] > SEVERITY_RANK[state["severity"]]:
            state["severity"] = check["severity"]
            return state, "worse"
        state["severity"] = check["severity"]
        return state, "hold"
    state["good_count"] += 1
    state["bad_count"] = 0
    if state["is_active"] and state["good_count"] >= CLEAR_AFTER.get(rule, config.CLEAR_AFTER):
        state["is_active"], state["severity"] = False, None
        return state, "clear"
    return state, ("hold" if state["is_active"] else None)


def worse_value(rule, current_peak, value):
    """The worse of two values for an incident's peak (lower is worse for
    speed and optical power, higher for everything else)."""
    if value is None:
        return current_peak
    if current_peak is None:
        return value
    return min(current_peak, value) if rule in LOWER_IS_WORSE else max(current_peak, value)


def device_status(active_severities):
    """A device's colour is its worst active rule: GREEN / AMBER / RED / OFFLINE."""
    worst = max(active_severities, key=lambda s: SEVERITY_RANK[s], default=None)
    return worst or "GREEN"
