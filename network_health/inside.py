"""Inside network: the OLT's uplink and the CRS326's ports, as plain logic.

Pure code (no database, no clock), like routes.py: service.py fetches the
readings, tests feed it made-up ones.

`olt` readings (every 5 min, SNMP from the CCR2116) carry three numbers,
any of which can be null when that SNMP read failed:

    uptime_s  how long the OLT has been up
    up_rx     good frames received on the uplink port 0/19/0 (64-bit counter)
    up_crc    errored frames received there (ifInErrors, 32-bit, wraps)

walk_olt() goes through them in time order and, for each reading, says:

  - restart:  uptime went DOWN since the last reading that had an uptime.
              No damaged-frames figure is worked out across a restart.
  - interval: this reading against the one just before it:
                d_rx  = up_rx's increase
                d_crc = up_crc's increase (+2^32 if it went down: a wrap)
                crc_pct = d_crc / (d_rx + d_crc) * 100
              Errored frames are not in up_rx, hence the sum. Only when both
              readings are complete; up_rx going DOWN without a restart is a
              counter reset (someone cleared it), also skipped.
  - failed:   the reading has a null (a failed SNMP poll).
"""
from datetime import datetime, timedelta, timezone

from network_health import config

OLT_FIELDS = ("uptime_s", "up_rx", "up_crc")
GREEN, AMBER, RED = "green", "amber", "red"


def is_complete(olt):
    return isinstance(olt, dict) and all(olt.get(field) is not None for field in OLT_FIELDS)


def is_restart(previous_uptime_s, uptime_s):
    return previous_uptime_s is not None and uptime_s is not None and uptime_s < previous_uptime_s


def crc_interval(previous, current):
    """Two complete readings in a row (no restart between) -> the interval's
    figures, or None for a counter reset."""
    before, after = previous["olt"], current["olt"]
    d_rx = after["up_rx"] - before["up_rx"]
    if d_rx < 0:
        return None
    d_crc = after["up_crc"] - before["up_crc"]
    if d_crc < 0:
        d_crc += config.OLT_ERRORS_COUNTER_WRAP
    frames = d_rx + d_crc
    return {"start": previous["ts"], "end": current["ts"], "d_rx": d_rx, "d_crc": d_crc,
            "crc_pct": d_crc / frames * 100 if frames > 0 else None}


def walk_olt(readings):
    """readings: [{ts, olt}] oldest first -> one step per reading:
    {ts, uptime_s, failed, restart (None or {detected_at, booted_at,
    previous_uptime_s, uptime_s}), interval (None or crc_interval's dict)}."""
    steps, previous, last_uptime = [], None, None
    for reading in readings:
        olt = reading.get("olt") or {}
        uptime = olt.get("uptime_s")
        restart = None
        if is_restart(last_uptime, uptime):
            restart = {"detected_at": reading["ts"], "booted_at": reading["ts"] - timedelta(seconds=uptime),
                       "previous_uptime_s": last_uptime, "uptime_s": uptime}
        interval = None
        if restart is None and previous is not None and is_complete(previous.get("olt")) and is_complete(olt):
            interval = crc_interval(previous, reading)
        steps.append({"ts": reading["ts"], "uptime_s": uptime, "failed": not is_complete(olt),
                      "restart": restart, "interval": interval})
        previous = reading
        if uptime is not None:
            last_uptime = uptime
    return steps


def crc_level(pct):
    """The badge: green under 0.01 %, red over 1 %, amber in between."""
    if pct is None:
        return None
    if pct < config.OLT_CRC_GREEN_UNDER_PCT:
        return GREEN
    return RED if pct > config.OLT_CRC_RED_OVER_PCT else AMBER


def crc_buckets(steps, bucket_seconds):
    """Damaged frames per time bucket (the graph): the bucket's damaged
    frames over all its frames, not an average of percentages, so a quiet
    5 minutes doesn't weigh as much as a busy one. Bucketed on each
    interval's end, like the other graphs' readings."""
    buckets = {}
    for step in steps:
        interval = step["interval"]
        if interval is None or interval["crc_pct"] is None:
            continue
        epoch = interval["end"].timestamp()
        start = datetime.fromtimestamp(epoch - epoch % bucket_seconds, timezone.utc)
        bucket = buckets.setdefault(start, [0, 0])
        bucket[0] += interval["d_crc"]
        bucket[1] += interval["d_rx"] + interval["d_crc"]
    return [{"t": start, "pct": crc / frames * 100, "d_crc": crc, "frames": frames}
            for start, (crc, frames) in sorted(buckets.items())]


def overall_pct(steps):
    """Damaged frames over the whole span, or None."""
    crc = sum(s["interval"]["d_crc"] for s in steps if s["interval"] and s["interval"]["crc_pct"] is not None)
    frames = sum(s["interval"]["d_rx"] + s["interval"]["d_crc"] for s in steps
                 if s["interval"] and s["interval"]["crc_pct"] is not None)
    return crc / frames * 100 if frames else None


def port_label(interface):
    return config.CRS_PORT_LABELS.get(interface, interface)


def link_events(records):
    """crs_link records ([{ts, crs_link}], oldest first) -> one row per
    outage, newest first. Each `down` is paired with the next `up` on the
    same interface:

        back      down then up: outage_s is the time between
        down      the latest event on that port is a down (still down)
        no_up     another down came before any up (an event was missed)
        up_only   an up with no down before it in this list
    """
    rows, open_downs = [], {}
    for record in records:
        link = record["crs_link"]
        interface = link["interface"]
        base = {"interface": interface, "label": port_label(interface), "device": link.get("device"),
                "is_olt": interface == config.OLT_LINK_INTERFACE}
        if link["state"] == "down":
            if interface in open_downs:
                open_downs[interface]["status"] = "no_up"
            row = {**base, "down_at": record["ts"], "up_at": None, "outage_s": None, "speed": None, "status": "down"}
            open_downs[interface] = row
            rows.append(row)
            continue
        row = open_downs.pop(interface, None)
        if row is None:
            rows.append({**base, "down_at": None, "up_at": record["ts"], "outage_s": None,
                         "speed": link.get("speed"), "status": "up_only"})
        else:
            row.update(up_at=record["ts"], outage_s=(record["ts"] - row["down_at"]).total_seconds(),
                       speed=link.get("speed"), status="back")
    return sorted(rows, key=lambda r: r["down_at"] or r["up_at"], reverse=True)
