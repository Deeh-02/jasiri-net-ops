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


def port_label(interface, device=None):
    """The CRS326's ports by what they lead to; a router's port like the
    inside links ("CCR2116 → CRS326"), else "CCR2116 sfp-sfpplus1"."""
    device = device or config.CRS_LINK_DEVICE
    if device == config.CRS_LINK_DEVICE:
        return config.CRS_PORT_LABELS.get(interface, interface)
    return config.LANLINK_LABELS.get((device, interface), f"{device.upper()} {interface}")


def link_events(records):
    """crs_link records ([{ts, crs_link}], oldest first) -> one row per
    outage, newest first. Each `down` is paired with the next `up` on the
    same port of the same device (records without a device are the
    CRS326's, from before the routers sent theirs):

        back      down then up: outage_s is the time between
        down      the latest event on that port is a down (still down)
        no_up     another down came before any up (an event was missed)
        up_only   an up with no down before it in this list
    """
    rows, open_downs = [], {}
    for record in records:
        link = record["crs_link"]
        interface = link["interface"]
        device = link.get("device") or config.CRS_LINK_DEVICE
        port = (device, interface)
        base = {"interface": interface, "label": port_label(interface, device), "device": device,
                "device_label": device.upper(),
                "is_olt": device == config.CRS_LINK_DEVICE and interface == config.OLT_LINK_INTERFACE}
        if link["state"] == "down":
            if port in open_downs:
                open_downs[port]["status"] = "no_up"
            row = {**base, "down_at": record["ts"], "up_at": None, "outage_s": None, "speed": None, "status": "down"}
            open_downs[port] = row
            rows.append(row)
            continue
        row = open_downs.pop(port, None)
        if row is None:
            rows.append({**base, "down_at": None, "up_at": record["ts"], "outage_s": None,
                         "speed": link.get("speed"), "status": "up_only"})
        else:
            row.update(up_at=record["ts"], outage_s=(record["ts"] - row["down_at"]).total_seconds(),
                       speed=link.get("speed"), status="back")
    return sorted(rows, key=lambda r: r["down_at"] or r["up_at"], reverse=True)


# ---- Areas (`area`: per-VLAN byte counters on the CCR2004 uplink) ----

def area_name(vlan, names):
    """VLAN 99 is the CCR2004's own link (its total); others are named from
    OPS's VLAN list, or "VLAN n" when it has no name."""
    if vlan == config.AREA_TOTAL_VLAN:
        return config.AREA_TOTAL_LABEL
    return names.get(vlan) or f"VLAN {vlan}"


def _mbps(before, after, seconds):
    if before is None or after is None or seconds <= 0 or after < before:
        return None          # a counter that went down was reset: no figure
    return (after - before) * 8 / seconds / 1e6


def area_rates(readings):
    """[{ts, area}] oldest first -> one step per reading: {ts, vlan,
    down_mbps, up_mbps}, each the change since that VLAN's previous reading
    (None for its first reading, or a reset)."""
    steps, last = [], {}
    for reading in readings:
        area = reading["area"]
        vlan = area["vlan"]
        previous = last.get(vlan)
        down = up = None
        if previous is not None:
            seconds = (reading["ts"] - previous["ts"]).total_seconds()
            down = _mbps(previous["area"].get("down_bytes"), area.get("down_bytes"), seconds)
            up = _mbps(previous["area"].get("up_bytes"), area.get("up_bytes"), seconds)
        steps.append({"ts": reading["ts"], "vlan": vlan, "down_mbps": down, "up_mbps": up})
        last[vlan] = reading
    return steps


# ---- Inside links (`lanlink`: port counters on the CCR2116, CCR2004, CRS326) ----

def lanlink_label(dev, port):
    return config.LANLINK_LABELS.get((dev, port), f"{dev.upper()} {port}")


def _increase(before, after):
    """A counter's increase; a drop is a reset (reboot, cleared): 0. None
    when either reading lacks the counter (the port doesn't report it)."""
    if before is None or after is None:
        return None
    return max(after - before, 0)


def lanlink_steps(readings):
    """[{ts, lanlink}] oldest first -> one step per reading: {ts, dev, port,
    label, totals, increases (None for a port's first reading), gap_minutes}."""
    steps, last = [], {}
    for reading in readings:
        link = reading["lanlink"]
        key = (link["dev"], link["port"])
        previous = last.get(key)
        increases = gap = None
        if previous is not None:
            increases = {c: _increase(previous["lanlink"].get(c), link.get(c)) for c in config.LANLINK_COUNTERS}
            gap = (reading["ts"] - previous["ts"]).total_seconds() / 60
        steps.append({"ts": reading["ts"], "dev": key[0], "port": key[1], "label": lanlink_label(*key),
                      "totals": {c: link.get(c) for c in config.LANLINK_COUNTERS},
                      "increases": increases, "gap_minutes": gap})
        last[key] = reading
    return steps


def lanlink_rows(readings, since):
    """One row per link for the page: its latest totals, the increase at its
    latest reading (the last 5 min) and the increases summed since `since`
    (24 h: resets counted as 0, not as a huge negative). Known links in the
    configured order, then any others."""
    by_link = {}
    for step in lanlink_steps(readings):
        row = by_link.setdefault((step["dev"], step["port"]), {
            "dev": step["dev"], "port": step["port"], "label": step["label"],
            "day": {c: None for c in config.LANLINK_COUNTERS}})
        row.update(totals=step["totals"], last=step["increases"], at=step["ts"])
        if step["increases"] and step["ts"] > since:
            for counter, value in step["increases"].items():
                if value is not None:
                    row["day"][counter] = (row["day"][counter] or 0) + value
    order = list(config.LANLINK_LABELS)
    return sorted(by_link.values(), key=lambda r: (order.index((r["dev"], r["port"]))
                                                   if (r["dev"], r["port"]) in order else len(order), r["label"]))
