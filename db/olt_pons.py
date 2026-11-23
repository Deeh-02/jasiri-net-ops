"""The OLT's PON ports and the site visits they call for, from what SmartOLT
itself says (an ONU's status and its signal grade), never from OPS's own
light thresholds. Pure code: no database, no clock.

SmartOLT grades each ONU's signal "Very good", "Warning" or "Critical" and
gives its status (Online, Offline, LOS = fibre signal lost, Power fail =
a power cut at the customer). A PON port is "board/port" (the ONU's
gpon-onu_0/<board>/<port>:<id>), shown as 0/<board>/<port>.

Input: the ONU dicts of smartolt.build_list (id, name, status, board, port,
signal_1310_dbm, signal_quality, last_status_change, administrative_status).
"""

# A visit for a whole PON when this many of its ONUs say LOS, or this many
# graded Warning/Critical (and at least this fraction of the ONUs that have a
# grade): one ONU alone is its own drop fibre, several is the shared part.
PON_MIN_ONUS = 2
PON_WEAK_FRACTION = 0.5

WEAK = ("Warning", "Critical")


def pon_key(board, port):
    return f"{board}/{port}"


def pon_label(board, port):
    return f"0/{board}/{port}"


def _is_disabled(onu):
    return (onu.get("administrative_status") or "").lower() == "disabled"


def _by_pon(onus):
    ports = {}
    for onu in onus:
        if onu.get("board") is not None and onu.get("port") is not None:
            ports.setdefault((onu["board"], onu["port"]), []).append(onu)
    return dict(sorted(ports.items(), key=lambda item: (item[0][0], item[0][1])))


def pon_rows(onus):
    """One row per PON port that has ONUs: how many, their statuses, and the
    OLT's own signal grades. `level` is the OLT's verdict on the light:
    bad = an ONU graded Critical or reporting LOS, warn = one graded Warning,
    good = all graded Very good, unknown = nothing graded and none online."""
    rows = []
    for (board, port), members in _by_pon(onus).items():
        count = lambda key, value: sum(1 for o in members if o.get(key) == value)   # noqa: E731
        graded = [o for o in members if o.get("signal_quality")]
        critical, warning = count("signal_quality", "Critical"), count("signal_quality", "Warning")
        los = count("status", "LOS")
        if critical or los:
            level = "bad"
        elif warning:
            level = "warn"
        elif graded:
            level = "good"
        else:
            level = "unknown"
        rows.append({
            "key": pon_key(board, port), "label": pon_label(board, port), "board": board, "port": port,
            "total": len(members), "online": count("status", "Online"), "los": los,
            "power_fail": count("status", "Power fail"), "offline": count("status", "Offline"),
            "very_good": count("signal_quality", "Very good"), "warning": warning, "critical": critical,
            "no_reading": len(members) - len(graded), "level": level})
    return rows


def _name(onu):
    return onu.get("name") or onu.get("sn") or onu.get("id")


def _visit(kind, title, detail, port, members, urgency, **extra):
    return {"kind": kind, "title": title, "detail": detail, "urgency": urgency,
            "pon": pon_key(*port), "pon_label": pon_label(*port),
            "onus": [o["id"] for o in members], "names": [_name(o) for o in members], **extra}


def site_visits(onus):
    """The jobs someone has to go out for, most urgent first, from what the
    OLT reports:
      pon_los   several ONUs of one PON with LOS: the shared fibre or splitter
      pon_weak  most graded ONUs of one PON Warning/Critical: the same
      los       one ONU with LOS (not on a pon_los PON): its drop fibre
      critical  one ONU graded Critical (not on a flagged PON)
      warning   one ONU graded Warning (not on a flagged PON)
    A power cut at the customer ("Power fail") and an ONU disabled in
    SmartOLT are not site visits."""
    visits, flagged = [], set()
    for port, members in _by_pon(onus).items():
        members = [o for o in members if not _is_disabled(o)]
        los = [o for o in members if o.get("status") == "LOS"]
        if len(los) >= PON_MIN_ONUS:
            flagged.add(port)
            visits.append(_visit(
                "pon_los", f"PON {pon_label(*port)}: {len(los)} of {len(members)} ONUs have lost light (LOS)",
                "The OLT reports LOS on several ONUs of one PON: check the shared fibre, the splitter and the feeder "
                "first.", port, los, "bad"))
            continue
        graded = [o for o in members if o.get("signal_quality") and o.get("status") == "Online"]
        weak = [o for o in graded if o["signal_quality"] in WEAK]
        if len(weak) >= PON_MIN_ONUS and len(weak) >= len(graded) * PON_WEAK_FRACTION:
            flagged.add(port)
            worst = "Critical" if any(o["signal_quality"] == "Critical" for o in weak) else "Warning"
            visits.append(_visit(
                "pon_weak", f"PON {pon_label(*port)}: {len(weak)} of {len(graded)} ONUs graded Warning or Critical",
                "The OLT grades several ONUs of one PON weak: suspect the shared fibre or the splitter.", port, weak,
                "bad" if worst == "Critical" else "warn"))
    for port, members in _by_pon(onus).items():
        if port in flagged:
            continue
        for onu in members:
            if _is_disabled(onu):
                continue
            extra = {"signal_dbm": onu.get("signal_1310_dbm"), "since": onu.get("last_status_change"),
                     "status": onu.get("status"), "grade": onu.get("signal_quality")}
            if onu.get("status") == "LOS":
                visits.append(_visit("los", f"Lost light at {_name(onu)} (PON {pon_label(*port)})",
                                     "The OLT reports LOS: check the drop fibre and its connectors.",
                                     port, [onu], "bad", **extra))
            elif onu.get("status") == "Online" and onu.get("signal_quality") == "Critical":
                visits.append(_visit("critical", f"Critical signal at {_name(onu)} (PON {pon_label(*port)})",
                                     "The OLT grades its signal Critical: check the drop fibre, the splitter port and "
                                     "the connectors.", port, [onu], "bad", **extra))
            elif onu.get("status") == "Online" and onu.get("signal_quality") == "Warning":
                visits.append(_visit("warning", f"Weak signal at {_name(onu)} (PON {pon_label(*port)})",
                                     "The OLT grades its signal Warning: clean the connectors and watch it.",
                                     port, [onu], "warn", **extra))
    rank = {"pon_los": 0, "pon_weak": 1, "los": 2, "critical": 3, "warning": 4}
    return sorted(visits, key=lambda v: rank[v["kind"]])
