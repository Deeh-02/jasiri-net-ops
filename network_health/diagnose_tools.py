"""Diagnose's tools: read-only lookups Claude may ask for when the snapshot
isn't enough (a longer history, finer buckets, one ONT, the customer line's
pings). Each one reads what the page already reads, never writes, and never
returns the SMS log, phone numbers or keys.

TOOLS is what Claude sees (kept byte-for-byte the same in every request: a
changed tool list restarts the prompt cache). run(name, input, now) checks
the input itself (Claude's input is never trusted as-is: it may be cut off
or malformed) and returns (label for the page, JSON text for Claude, is_error).
"""
import json
import logging
from datetime import timedelta

from network_health import access, config, inside, service, store

log = logging.getLogger(__name__)

BUCKETS = (5, 15, 30, 60, 180)          # minutes
MAX_POINTS = 300                         # per series: the bucket is widened past this
MAX_RESULT_CHARS = 40_000
MAX_NAMES = 8

_HOURS = {"type": "integer", "minimum": 1, "maximum": 168,
          "description": "How far back, in hours (1 to 168). Raw ONT readings go back 3 days; the rest 10."}
_BUCKET = {"type": "integer", "enum": list(BUCKETS),
           "description": "Average per this many minutes. Widened automatically to keep each series under "
                          f"{MAX_POINTS} points."}


def _tool(name, description, properties, required=()):
    return {"name": name, "description": description, "eager_input_streaming": True,
            "input_schema": {"type": "object", "properties": properties, "required": list(required),
                             "additionalProperties": False}}


TOOLS = [
    _tool("customer_line_history",
          "The probe machine on a real customer line (Kwa Mlima, home-kwamlima): its pings to gw (first router "
          "past the OLT, the CCR2004), core (CCR2116), airtel (Airtel gateway) and fra (Frankfurt), each small and "
          "1400-byte (-big), as loss % and rtt ms per bucket; its 6-second download runs (Cloudflare, Frankfurt, "
          "plan, Mbps); and the owner's markers (changes made on the line, for before/after).",
          {"hours": _HOURS, "bucket_minutes": _BUCKET}, ("hours",)),
    _tool("probe_history",
          "Loss %, rtt ms and jitter ms per bucket for named probes: p-* (from the CCR2116), i-* (from the "
          "CCR2004), h-* (pings to the boxes) and the customer line's gw/core/airtel/fra (and -big twins).",
          {"names": {"type": "array", "items": {"type": "string", "maxLength": 40}, "minItems": 1,
                     "maxItems": MAX_NAMES, "description": "Probe names, e.g. [\"h-olt-big\", \"h-olt\"]."},
           "hours": _HOURS, "bucket_minutes": _BUCKET}, ("names", "hours")),
    _tool("olt_crc_history",
          "The OLT uplink's damaged frames (% of frames from the CRS326 arriving damaged) per bucket, and the "
          "OLT's restarts in the range.",
          {"hours": _HOURS, "bucket_minutes": _BUCKET}, ("hours",)),
    _tool("uplink_history",
          "The Airtel uplink's load per bucket: download Mbps (average and peak), upload Mbps, PPPoE sessions, "
          "CCR2116 CPU %.",
          {"hours": _HOURS, "bucket_minutes": _BUCKET}, ("hours",)),
    _tool("ont_lookup",
          "Find ONTs by name, customer code (JS12), site name, PON (0/3/4) or PON:id (0/3/4:7). Each match: "
          "status and since when, light both ways now and its levels, distance, last down cause, its PPPoE "
          "session, open warnings, its light over the last 7 days, and its recent down/back events.",
          {"query": {"type": "string", "minLength": 1, "maxLength": 64}}, ("query",)),
    _tool("incident_history",
          "Past and open incidents (rule, subject, severity, start, end, worst value), newest first.",
          {"days": {"type": "integer", "minimum": 1, "maximum": 30},
           "rule": {"type": "string", "maxLength": 40, "description": "Only this rule, e.g. OLT_CRC (optional)."}},
          ("days",)),
]
TOOL_NAMES = {t["name"] for t in TOOLS}


class BadInput(ValueError):
    """Claude's input doesn't fit the tool: the message goes back to it."""


def _int(data, key, low, high, default=None):
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BadInput(f"{key} must be a whole number from {low} to {high}")
    return value


def _range(data):
    """(hours, bucket minutes): the bucket widened so a series stays under MAX_POINTS."""
    hours = _int(data, "hours", 1, 168)
    bucket = data.get("bucket_minutes", 30 if hours > 24 else 5)
    if bucket not in BUCKETS:
        raise BadInput(f"bucket_minutes must be one of {list(BUCKETS)}")
    while hours * 60 / bucket > MAX_POINTS and bucket != BUCKETS[-1]:
        bucket = BUCKETS[BUCKETS.index(bucket) + 1]
    return hours, bucket


def _check_keys(data, allowed):
    if not isinstance(data, dict):
        raise BadInput("the input must be an object")
    extra = set(data) - set(allowed)
    if extra:
        raise BadInput(f"unknown field(s): {', '.join(sorted(extra))}")


def _series(rows, columns):
    """[{t, a, b}] -> {"columns": [t, a, b], "rows": [[...], ...]}: fewer tokens than objects."""
    return {"columns": ["t", *columns], "rows": [[r["t"], *(r.get(c) for c in columns)] for r in rows]}


def customer_line_history(data, now):
    _check_keys(data, ("hours", "bucket_minutes"))
    hours, bucket = _range(data)
    since = now - timedelta(hours=hours)
    with store.transaction() as cur:
        devs = store.box_devs(cur, since)
        if not devs:
            return f"customer line {hours}h", {"note": "no readings from the probe machine in this range"}
        dev = devs[0]
        loss = store.box_loss_series(cur, dev, since, bucket * 60)
        speeds = store.box_speeds(cur, dev, since)
        markers = store.box_markers(cur)
    probes = {}
    for row in loss:
        probes.setdefault(row["name"], []).append(row)
    return f"customer line {hours}h", {
        "dev": dev, "name": config.BOX_NAMES.get(dev, dev), "hours": hours, "bucket_minutes": bucket,
        "probes": {name: _series(rows, ("loss", "rtt")) for name, rows in sorted(probes.items())},
        "speed_runs": _series(speeds, ("cf_mbps", "fra_mbps", "plan_mbps")),
        "markers": [{"at": m["at"], "label": m["label"]} for m in markers],
        "alerts": {"big_loss_pct": config.BOX_BIG_LOSS_PCT, "big_loss_in_a_row": config.BOX_BIG_LOSS_IN_A_ROW,
                   "slow_under_pct_of_plan": round(config.BOX_SLOW_FRACTION * 100),
                   "slow_in_a_row": config.BOX_SLOW_IN_A_ROW}}


def probe_history(data, now):
    _check_keys(data, ("names", "hours", "bucket_minutes"))
    names = data.get("names")
    if (not isinstance(names, list) or not 1 <= len(names) <= MAX_NAMES
            or not all(isinstance(n, str) and 0 < len(n.strip()) <= 40 for n in names)):
        raise BadInput(f"names must be a list of 1 to {MAX_NAMES} probe names")
    names = sorted({n.strip() for n in names})
    hours, bucket = _range(data)
    with store.transaction() as cur:
        rows = store.probe_series(cur, names, now - timedelta(hours=hours), bucket * 60)
    found = {}
    for row in rows:
        found.setdefault(row["name"], []).append(row)
    return f"probes {', '.join(names)} {hours}h", {
        "hours": hours, "bucket_minutes": bucket,
        "probes": {name: _series(found[name], ("loss", "rtt", "jitter")) for name in names if name in found},
        "not_found": [name for name in names if name not in found]}


def olt_crc_history(data, now):
    _check_keys(data, ("hours", "bucket_minutes"))
    hours, bucket = _range(data)
    since = now - timedelta(hours=hours)
    with store.transaction() as cur:
        steps = inside.walk_olt(store.olt_readings(cur, since - timedelta(minutes=15), now))
    steps = [s for s in steps if s["ts"] > since]
    return f"OLT damaged frames {hours}h", {
        "hours": hours, "bucket_minutes": bucket,
        "crc": _series(inside.crc_buckets(steps, bucket * 60), ("pct",)),
        "whole_range_pct": inside.overall_pct(steps),
        "restarts": [s["restart"] for s in steps if s.get("restart")],
        "failed_polls": sum(1 for s in steps if s["failed"]), "polls": len(steps),
        "green_under_pct": config.OLT_CRC_GREEN_UNDER_PCT, "red_over_pct": config.OLT_CRC_RED_OVER_PCT}


def uplink_history(data, now):
    _check_keys(data, ("hours", "bucket_minutes"))
    hours, bucket = _range(data)
    series = store.chart_series(now - timedelta(hours=hours), bucket * 60)
    with store.transaction() as cur:
        capacity = store.settings(cur)["uplink_capacity_mbps"]
    return f"uplink load {hours}h", {
        "hours": hours, "bucket_minutes": bucket, "capacity_mbps": capacity,
        "load": _series(series["load"], ("down", "down_max", "up", "ppp", "cpu"))}


def ont_lookup(data, now):
    _check_keys(data, ("query",))
    query = data.get("query")
    if not isinstance(query, str) or not 0 < len(query.strip()) <= 64:
        raise BadInput("query must be a name, customer code, PON or PON:id")
    query = query.strip().lower()
    card = service.access_card()
    every = [o for o in card["onts"] if query in o["name"].lower() or query == o["key"].lower()
             or query == o["pon"].lower()]
    matches = every[:10]
    if not matches:
        return f"ONT {query}", {"note": "no ONT matches", "pons": [p.get("pon") for p in card["pons"]]}
    spark = card["spark"]
    out = []
    for o in matches:
        events = [e for e in card["events"] if e.get("pon") == o["pon"] and e.get("ont_id") == o["ont_id"]][:15]
        light = [{"t": spark["start"] + timedelta(seconds=spark["step_s"] * i), "rx": rx, "olt_rx": olt}
                 for i, (rx, olt) in enumerate(zip(o["spark"]["rx"], o["spark"]["olt"])) if rx is not None or olt is not None]
        out.append({**{k: o.get(k) for k in ("key", "name", "status", "status_since", "rx_dbm", "olt_rx_dbm", "rx_level",
                                             "olt_rx_level", "distance_m", "last_down_text", "stale", "warnings")},
                    "ppp": o.get("ppp"), "same_splitter_group": o.get("group"),
                    "light_7d": _series(light, ("rx", "olt_rx")), "events": events})
    return f"ONT {query}", {"matches": out, "more": len(every) - len(matches),
                            "light_thresholds_dbm": card["thresholds"]}


def incident_history(data, now):
    _check_keys(data, ("days", "rule"))
    days = _int(data, "days", 1, 30)
    rule = data.get("rule")
    if rule is not None and (not isinstance(rule, str) or len(rule) > 40):
        raise BadInput("rule must be a rule name such as OLT_CRC")
    since = now - timedelta(days=days)
    rows = [i for i in store.incidents(500) if i["started_at"] >= since or i["ended_at"] is None]
    if rule:
        rows = [i for i in rows if i["rule"] == rule.strip().upper()]
    return f"incidents {days}d{' ' + rule if rule else ''}", {
        "days": days, "count": len(rows),
        "incidents": [{k: i.get(k) for k in ("device", "rule", "subject", "severity", "started_at", "ended_at",
                                              "peak_value", "is_flapping", "fire_sms_sent")} for i in rows[:150]]}


RUNNERS = {"customer_line_history": customer_line_history, "probe_history": probe_history,
           "olt_crc_history": olt_crc_history, "uplink_history": uplink_history, "ont_lookup": ont_lookup,
           "incident_history": incident_history}


def run(name, data, now, tidy):
    """One tool call -> (label, text, is_error). Never raises: a failure goes
    back to Claude as an error result, so it can answer from what it has."""
    if name not in RUNNERS:
        return name, json.dumps({"error": f"unknown tool {name}"}), True
    try:
        label, result = RUNNERS[name](data, now)
    except BadInput as e:
        return name, json.dumps({"error": f"bad input: {e}"}), True
    except Exception:
        log.exception("network health diagnose: tool %s failed", name)
        return name, json.dumps({"error": "the lookup failed on the server; answer from the snapshot"}), True
    text = json.dumps(tidy(result), separators=(",", ":"), default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = json.dumps({"error": f"the result was too big ({len(text)} characters); ask for fewer hours, "
                                    "a wider bucket or fewer names"})
        return label, text, True
    return label, text, False
