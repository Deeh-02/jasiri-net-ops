"""Access network: the OLT's PON ports and ONTs, and the PPPoE sessions on
the two routers, as plain logic.

Pure code (no database, no clock), like inside.py: service.py fetches the
state and readings, tests feed it made-up ones.

  ONT state      apply_ont() moves one ONT's nh_onts row on by one reading
                 and says whether it went down or came back.
  PON groups     ONTs that went down in the same poll on one PON:
                 classify_group() decides "whole PON", "fibre" (mostly light
                 loss), "power" (mostly dying-gasp) or nothing (one or two).
  PPPoE          apply_ppp() diffs a router's session names against the last
                 reading; mass_drop_check() and site_checks() judge it.
  Matching       match_onts(): ONT name <-> PPPoE session (JS code, else the
                 normalised site name; manual matches win).
  Light          levels, the 24 h vs 7 day drop, field jobs, splitter groups.
"""
import re
from datetime import timedelta

from network_health import config
from network_health.rules import AMBER, RED, make_check

GOOD, WEAK, BAD, OVERLOADED = "good", "weak", "bad", "overloaded"
LEVEL_RANK = {None: 0, GOOD: 1, WEAK: 2, OVERLOADED: 3, BAD: 4}
ONLINE, OFFLINE = "online", "offline"

# The PON group rules, by classify_group()'s answer.
GROUP_RULES = {"whole": "PON_DOWN", "fibre": "PON_FIBRE_FAULT", "power": "PON_POWER_CUT"}
GROUP_SEVERITY = {"PON_DOWN": RED, "PON_FIBRE_FAULT": RED, "PON_POWER_CUT": AMBER}


# ---- Light levels ----

def rx_level(dbm):
    """What the ONT receives (downstream)."""
    if dbm is None:
        return None
    if dbm > config.ONT_RX_OVERLOAD_ABOVE_DBM:
        return OVERLOADED
    if dbm < config.ONT_RX_BAD_BELOW_DBM:
        return BAD
    return WEAK if dbm < config.ONT_RX_WEAK_BELOW_DBM else GOOD


def olt_rx_level(dbm):
    """What the OLT receives from the ONT (upstream)."""
    if dbm is None:
        return None
    if dbm < config.OLT_RX_BAD_BELOW_DBM:
        return BAD
    return WEAK if dbm < config.OLT_RX_WEAK_BELOW_DBM else GOOD


def worst_level(*levels):
    return max(levels, key=lambda level: LEVEL_RANK[level])


# ---- One ONT ----

def ont_key(pon, ont_id):
    return f"{pon}:{ont_id}"


# The OLT's name field often holds "<name>_descr_<description>" (both of the
# ONT's OLT fields joined): matched part by part, shown as "name - description".
DESCR_JOIN = "_descr_"


def ont_name(ont):
    """The name set on the OLT (a JS code and/or a site), else its place."""
    descr = (ont.get("descr") or "").replace(DESCR_JOIN, " - ").strip()
    return descr or f"ONT {ont['pon']}:{ont['ont_id']}"


def down_kind(text):
    """last_down_text -> light / power / reboot / other (None without a text)."""
    if not text:
        return None
    lowered = text.lower()
    for kind, words in (("light", config.DOWN_TEXT_LIGHT), ("power", config.DOWN_TEXT_POWER),
                        ("reboot", config.DOWN_TEXT_REBOOT)):
        if any(word in lowered for word in words):
            return kind
    return "other"


def is_stale(ont):
    """Registered on the OLT but never online since OPS first saw it, and no
    down cause: listed apart, never alerted on."""
    return not ont["ever_online"] and ont["status"] == OFFLINE and ont.get("last_down_cause") is None


def apply_ont(state, reading, ts):
    """state: the ONT's nh_onts row (a dict) or None for a new ONT;
    reading: a parsed `ont` record's object. -> (new state, event or None),
    or (None, None) for a reading to skip: older than the state's (late),
    or a status other than online/offline. A new ONT makes no event."""
    status = reading["status"]
    if status not in (ONLINE, OFFLINE) or (state is not None and ts <= state["last_seen_at"]):
        return None, None
    new = dict(state) if state else {
        "pon": reading["pon"], "ont_id": reading["ont_id"], "descr": None, "distance_m": None,
        "first_seen_at": ts, "status_since": ts, "ever_online": False, "last_online_at": None}
    changed = state is not None and state["status"] != status
    new.update(status=status, rx_dbm=reading["rx_dbm"], olt_rx_dbm=reading["olt_rx_dbm"],
               last_down_cause=reading["last_down_cause"], last_down_text=reading["last_down_text"], last_seen_at=ts)
    if reading.get("desc"):
        new["descr"] = reading["desc"]
    if reading.get("distance_m") is not None:
        new["distance_m"] = reading["distance_m"]
    if changed:
        new["status_since"] = ts
    if status == ONLINE:
        new.update(ever_online=True, last_online_at=ts)
    if not changed:
        return new, None
    base = {"at": ts, "pon": new["pon"], "ont_id": new["ont_id"], "name": ont_name(new)}
    if status == OFFLINE:
        return new, {**base, "kind": "ont_down", "detail": {
            "text": reading["last_down_text"], "cause": reading["last_down_cause"],
            "why": down_kind(reading["last_down_text"]), "distance_m": new["distance_m"]}}
    return new, {**base, "kind": "ont_back", "detail": {
        "outage_s": (ts - state["status_since"]).total_seconds(), "first_time": not state["ever_online"]}}


# ---- ONTs going down together on one PON ----

def poll_clusters(downs):
    """ONT down events -> {pon: [[events of one poll], ...]}, oldest first.
    One poll = events within ACCESS_POLL_GAP_SECONDS of the one before."""
    by_pon = {}
    for event in sorted(downs, key=lambda e: e["at"]):
        clusters = by_pon.setdefault(event["pon"], [])
        if clusters and (event["at"] - clusters[-1][-1]["at"]).total_seconds() <= config.ACCESS_POLL_GAP_SECONDS:
            clusters[-1].append(event)
        else:
            clusters.append([event])
    return by_pon


def classify_group(downs, online_in_poll, readings_in_poll):
    """One PON's ONTs that went down in one poll -> 'whole', 'fibre',
    'power' or None.

    whole: no ONT on the PON was online in that poll (and at least
           WHOLE_PON_MIN_ONTS went down): feeder, OLT port or main splitter.
    fibre: ACCESS_GROUP_MIN_ONTS or more, light loss at least as common as
           power loss.
    power: as many, mostly dying-gasp.
    None:  fewer, or none of them light or power (resets: a reboot).
    A poll with no readings at all (deleted already) can't be "whole"."""
    if readings_in_poll and not online_in_poll and len(downs) >= config.WHOLE_PON_MIN_ONTS:
        return "whole"
    if len(downs) < config.ACCESS_GROUP_MIN_ONTS:
        return None
    reasons = [(event.get("detail") or {}).get("why") for event in downs]
    light, power = reasons.count("light"), reasons.count("power")
    if light and light >= power:
        return "fibre"
    return "power" if power > light else None


def group_check(kind, pon, downs, previous=None):
    """The PON rule's check for a group. previous: the facts of the incident
    already open for this rule on this PON (a second group joins it)."""
    rule = GROUP_RULES[kind]
    previous = previous or {}
    keys, names = list(previous.get("onts") or []), list(previous.get("names") or [])
    light, power = previous.get("light", 0), previous.get("power", 0)
    for event in downs:
        key = ont_key(event["pon"], event["ont_id"])
        if key in keys:
            continue
        keys.append(key)
        names.append(event["name"])
        why = (event.get("detail") or {}).get("why")
        light += why == "light"
        power += why == "power"
    facts = {"pon": pon, "label": f"PON {pon}", "onts": keys, "names": names, "count": len(keys),
             "light": light, "power": power}
    return make_check("olt", rule, True, severity=GROUP_SEVERITY[rule], value=len(keys), subject=pon, facts=facts,
                      immediate=True)


def group_recovery(member_keys, onts, fired_at):
    """An open group incident against the ONTs now -> (still offline,
    clear_at or None). It clears once at most ACCESS_GROUP_CLEAR_LEFT_FRACTION
    of its ONTs (at least one) is still offline, at the moment the ONT that
    got it there came back."""
    total = len(member_keys)
    allowed = max(1, int(total * config.ACCESS_GROUP_CLEAR_LEFT_FRACTION))
    back = sorted(max(onts[key]["status_since"], fired_at) for key in member_keys
                  if key in onts and onts[key]["status"] == ONLINE)
    still = total - len(back)
    if still > allowed:
        return still, None
    needed = total - allowed
    return still, (back[needed - 1] if needed >= 1 else fired_at)


def light_loss_check(event):
    """A lone ONT (not part of a group) that went down with light loss:
    a page warning until it is back."""
    name = event["name"]
    return make_check("olt", "ONT_LIGHT_LOSS", True, subject=ont_key(event["pon"], event["ont_id"]),
                      facts={"name": name, "label": name, "pon": event["pon"], "text": event["detail"].get("text")},
                      immediate=True)


# ---- PPPoE sessions ----

def is_site(name):
    return config.SITE_NAME_WORD in (name or "").lower()


def apply_ppp(sessions, names, ts):
    """sessions: {name: nh_ppp_sessions row} for one router, as OPS knew it
    before this reading; names: the reading's session names.
    -> (new sessions, events, how many were present before, dropped names).
    A name never seen before is just added (no event)."""
    current = set(names)
    before = sum(1 for s in sessions.values() if s["present"])
    new, events, dropped = {}, [], []
    for name, session in sessions.items():
        session = dict(session)
        if name in current:
            if not session["present"]:
                gone_since = session["missing_since"] or session["last_seen_at"]
                events.append({"kind": "ppp_back", "at": ts, "name": name,
                               "detail": {"after_s": (ts - gone_since).total_seconds(), "site": is_site(name)}})
            session.update(present=True, last_seen_at=ts, missing_since=None)
        elif session["present"]:
            dropped.append(name)
            events.append({"kind": "ppp_dropped", "at": ts, "name": name, "detail": {"site": is_site(name)}})
            session.update(present=False, missing_since=ts)
        new[name] = session
    for name in names:
        if name not in new:
            new[name] = {"present": True, "first_seen_at": ts, "last_seen_at": ts, "missing_since": None}
    return new, events, before, dropped


def mass_drop_check(dev, before, now_count, dropped, gap_minutes, open_before=None):
    """PPP_MASS_DROP: PPP_MASS_DROP_FRACTION or more of a router's sessions
    gone in one reading (of at least PPP_MASS_DROP_MIN_SESSIONS; readings
    no more than PPP_MAX_GAP_MINUTES apart). Once open it holds until the
    router is back to (1 - fraction) of the count before the drop."""
    label = f"{dev.upper()} PPPoE"
    if open_before is not None:
        facts = {"label": label, "before": open_before, "now": now_count, "dropped": max(open_before - now_count, 0)}
        return make_check(dev, "PPP_MASS_DROP", now_count < open_before * (1 - config.PPP_MASS_DROP_FRACTION),
                          severity=RED, value=facts["dropped"], facts=facts, immediate=True)
    if gap_minutes is None or gap_minutes > config.PPP_MAX_GAP_MINUTES:
        return make_check(dev, "PPP_MASS_DROP", None, severity=RED)
    if before < config.PPP_MASS_DROP_MIN_SESSIONS:
        return make_check(dev, "PPP_MASS_DROP", False, severity=RED)
    facts = {"label": label, "before": before, "now": now_count, "dropped": len(dropped)}
    return make_check(dev, "PPP_MASS_DROP", len(dropped) / before >= config.PPP_MASS_DROP_FRACTION, severity=RED,
                      value=len(dropped), facts=facts, immediate=True)


def site_checks(dev, sessions, ts):
    """SITE_PPP_DOWN for every site session on this router: missing now is
    bad (it fires after SITE_PPP_MISSING_IN_A_ROW readings). minutes = since
    the first reading it was missing (the SMS's 'Since')."""
    checks = []
    for name, session in sorted(sessions.items()):
        if not is_site(name):
            continue
        missing = not session["present"]
        minutes = (ts - session["missing_since"]).total_seconds() / 60 if missing and session["missing_since"] else 0
        checks.append(make_check(dev, "SITE_PPP_DOWN", missing, value=minutes, subject=name,
                                 facts={"name": name, "label": name, "minutes": minutes}))
    return checks


# ---- Matching ONTs and PPPoE sessions ----

JS_CODE = re.compile(r"(?<![a-z0-9])js[ _-]?0*(\d{1,4})(?![0-9])", re.IGNORECASE)


def js_code(text):
    """'JS26', 'js-5' -> 'JS26', 'JS05'; None without one."""
    match = JS_CODE.search(text or "")
    return f"JS{int(match.group(1)):02d}" if match else None


def site_key(text):
    """'Kwa Mlima Cyber' / 'KwaMlima_HOTSPOT' -> 'kwamlima': lower-case, no
    JS code, only letters and digits, without the noise words."""
    key = re.sub(r"[^a-z0-9]", "", JS_CODE.sub("", (text or "").lower()))
    for word in config.SITE_NAME_NOISE:
        key = key.replace(word, "")
    return key or None


def match_onts(onts, sessions_by_dev, overrides):
    """-> {ont key: {ppp, dev, how}}. how: 'manual' (the page's match),
    'manual_none' (marked as having no PPPoE session), 'code' (JS code),
    'name' (site name), or no entry when nothing matches (or two sessions
    match equally)."""
    by_code, by_site = {}, {}
    for dev, sessions in sessions_by_dev.items():
        for name in sessions:
            code, site = js_code(name), site_key(name)
            if code:
                by_code.setdefault(code, []).append((dev, name))
            elif site:
                by_site.setdefault(site, []).append((dev, name))
    dev_of = {name: dev for dev, sessions in sessions_by_dev.items() for name in sessions}
    matches = {}
    for key, ont in onts.items():
        if key in overrides:
            name = overrides[key]
            matches[key] = {"ppp": name, "dev": dev_of.get(name), "how": "manual" if name else "manual_none"}
            continue
        descr = ont.get("descr") or ""
        parts = [descr, *descr.split(DESCR_JOIN)] if DESCR_JOIN in descr else [descr]
        found = []
        for lookup, how in ((lambda text: by_code.get(js_code(text), []), "code"),
                            (lambda text: by_site.get(site_key(text), []), "name")):
            for part in parts:
                found = lookup(part)
                if len(found) == 1:
                    break
            if len(found) == 1:
                break
        if len(found) == 1:
            dev, name = found[0]
            matches[key] = {"ppp": name, "dev": dev, "how": how}
    return matches


def line_checks(dev, sessions, onts, matches, ts):
    """LINE_NO_INTERNET: the ONT is online but its PPPoE session (on this
    router) is missing. Fires after LINE_NO_INTERNET_IN_A_ROW readings."""
    checks = []
    for key, match in sorted(matches.items()):
        session, ont = sessions.get(match["ppp"] or ""), onts.get(key)
        if match["dev"] != dev or session is None or ont is None:
            continue
        bad = ont["status"] == ONLINE and not session["present"]
        minutes = (ts - session["missing_since"]).total_seconds() / 60 if bad and session["missing_since"] else 0
        name = ont_name(ont)
        checks.append(make_check("olt", "LINE_NO_INTERNET", bad, value=minutes, subject=key,
                                 facts={"name": name, "label": name, "ppp": match["ppp"], "minutes": minutes}))
    return checks


# ---- Light degrading (hourly) ----

def median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def light_drops(rows, hour):
    """rows: [{pon, ont_id, hour, rx}] hourly average rx_dbm over [hour - 8 d,
    hour). -> {key: {pon, ont_id, recent, usual, drop}}: the median over the
    last 24 h against the median over the 7 days before (drop > 0 = weaker
    now). ONTs without LIGHT_RECENT_MIN_HOURS / LIGHT_USUAL_MIN_HOURS hours
    of readings are left out."""
    recent_from = hour - timedelta(hours=24)
    spans = {}
    for row in rows:
        if row["rx"] is None:
            continue
        key = ont_key(row["pon"], row["ont_id"])
        entry = spans.setdefault(key, {"pon": row["pon"], "ont_id": row["ont_id"], "recent": [], "usual": []})
        entry["recent" if row["hour"] >= recent_from else "usual"].append(row["rx"])
    drops = {}
    for key, entry in spans.items():
        if len(entry["recent"]) >= config.LIGHT_RECENT_MIN_HOURS and len(entry["usual"]) >= config.LIGHT_USUAL_MIN_HOURS:
            recent, usual = median(entry["recent"]), median(entry["usual"])
            drops[key] = {"pon": entry["pon"], "ont_id": entry["ont_id"], "recent": recent, "usual": usual,
                          "drop": usual - recent}
    return drops


def degrade_checks(drops, onts):
    """ONT_LIGHT_DEGRADING per ONT (LIGHT_DROP_ONT_DB or more: page) and
    PON_LIGHT_DEGRADING per PON (PON_SHARED_MIN_ONTS ONTs down
    LIGHT_DROP_SHARED_DB or more on the same day: texts)."""
    checks, per_pon = [], {}
    for key, drop in sorted(drops.items()):
        ont = onts.get(key) or {"pon": drop["pon"], "ont_id": drop["ont_id"]}
        name = ont_name(ont)
        facts = {"name": name, "label": name, "pon": drop["pon"], "drop": drop["drop"], "recent": drop["recent"],
                 "usual": drop["usual"]}
        checks.append(make_check("olt", "ONT_LIGHT_DEGRADING", drop["drop"] >= config.LIGHT_DROP_ONT_DB,
                                 value=drop["drop"], subject=key, facts=facts))
        shared = per_pon.setdefault(drop["pon"], [])
        if drop["drop"] >= config.LIGHT_DROP_SHARED_DB:
            shared.append((name, drop["drop"]))
    for pon, shared in sorted(per_pon.items()):
        shared.sort(key=lambda item: -item[1])
        facts = {"pon": pon, "label": f"PON {pon}", "names": [n for n, _ in shared], "drops": [d for _, d in shared]}
        checks.append(make_check("olt", "PON_LIGHT_DEGRADING", len(shared) >= config.PON_SHARED_MIN_ONTS,
                                 value=max((d for _, d in shared), default=0), subject=pon, facts=facts))
    return checks


# ---- The page ----

def pon_sort_key(pon):
    """'0/3/5' sorts as numbers (0/3/10 after 0/3/9)."""
    parts = re.split(r"[/:]", pon or "")
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts)


def ont_view(ont):
    """One ONT as the page shows it (light levels worked out)."""
    online = ont["status"] == ONLINE
    rx, olt = (ont.get("rx_dbm"), ont.get("olt_rx_dbm")) if online else (None, None)
    levels = {"rx_level": rx_level(rx), "olt_rx_level": olt_rx_level(olt)}
    return {"key": ont_key(ont["pon"], ont["ont_id"]), "pon": ont["pon"], "ont_id": ont["ont_id"],
            "name": ont_name(ont), "status": ont["status"], "status_since": ont["status_since"],
            "last_seen_at": ont["last_seen_at"], "first_seen_at": ont["first_seen_at"], "rx_dbm": rx, "olt_rx_dbm": olt,
            **levels, "level": worst_level(*levels.values()), "distance_m": ont.get("distance_m"),
            "last_down_text": ont.get("last_down_text"), "last_down_why": down_kind(ont.get("last_down_text")),
            "stale": is_stale(ont)}


def is_one_way(view):
    return {view["rx_level"], view["olt_rx_level"]} == {GOOD, BAD}


def field_jobs(views, light_loss_keys):
    """The jobs someone has to go out for, most urgent first:
      pon_light   a PON where half or more of its online ONTs are weak/bad
      light_loss  an ONT offline with light loss (a lone one, not a group)
      ont_bad     an ONT with bad light (not already on a pon_light PON)
      one_way     bad in one direction, fine in the other
      attenuator  an ONT receiving too much light."""
    jobs, flagged = [], set()
    by_pon = {}
    for view in views:
        by_pon.setdefault(view["pon"], []).append(view)
    for pon in sorted(by_pon, key=pon_sort_key):
        online = [v for v in by_pon[pon] if v["status"] == ONLINE and v["level"]]
        # Bad one way only is that ONT's own fault (one_way below), not the shared fibre's.
        poor = [v for v in online if v["level"] in (WEAK, BAD) and not is_one_way(v)]
        if (len(poor) >= config.PON_JOB_MIN_POOR and len(online) >= config.PON_JOB_MIN_ONLINE
                and len(poor) >= len(online) * config.PON_JOB_WEAK_FRACTION):
            flagged.add(pon)
            jobs.append({"kind": "pon_light", "pon": pon, "onts": [v["key"] for v in poor],
                         "title": f"PON {pon}: {len(poor)} of {len(online)} online ONTs have weak or bad light",
                         "detail": "Shared fibre or splitter: clean and check the feeder, the splitter and their "
                                   "connectors first.",
                         "names": [v["name"] for v in poor]})
    order = sorted(views, key=lambda v: (pon_sort_key(v["pon"]), v["ont_id"]))
    for view in order:
        if view["key"] in light_loss_keys and view["status"] == OFFLINE:
            jobs.append({"kind": "light_loss", "pon": view["pon"], "onts": [view["key"]], "since": view["status_since"],
                         "title": f"Light loss at {view['name']} (PON {view['pon']})",
                         "detail": "Offline with light loss: check the drop fibre and its connectors."})
    for view in order:
        if view["status"] != ONLINE:
            continue
        rx, olt = view["rx_level"], view["olt_rx_level"]
        base = {"pon": view["pon"], "onts": [view["key"]], "rx_dbm": view["rx_dbm"], "olt_rx_dbm": view["olt_rx_dbm"]}
        if rx == GOOD and olt == BAD:
            jobs.append({**base, "kind": "one_way", "title": f"Upstream only bad at {view['name']}",
                         "detail": "Downstream is fine, the OLT hears it badly: suspect the ONT's transmitter or "
                                   "its connector."})
        elif rx == BAD and olt == GOOD:
            jobs.append({**base, "kind": "one_way", "title": f"Downstream only bad at {view['name']}",
                         "detail": "The OLT hears it fine, it receives badly: suspect the ONT's receiver or its "
                                   "connector."})
        elif view["level"] == BAD and view["pon"] not in flagged:
            jobs.append({**base, "kind": "ont_bad", "title": f"Bad light at {view['name']} (PON {view['pon']})",
                         "detail": "Check the drop fibre, the splitter port and the connectors."})
        if rx == OVERLOADED:
            jobs.append({**base, "kind": "attenuator", "title": f"Too much light at {view['name']}",
                         "detail": "Overloaded receiver: fit an attenuator."})
    rank = {"pon_light": 0, "light_loss": 1, "ont_bad": 2, "one_way": 3, "attenuator": 4}
    return sorted(jobs, key=lambda job: rank[job["kind"]])


def together_counts(downs):
    """ont_down events [{pon, ont_id, cluster}] -> {(key a, key b): times
    they went down in the same poll}, a < b."""
    polls = {}
    for event in downs:
        if event.get("cluster"):
            polls.setdefault((event["pon"], event["cluster"]), set()).add(ont_key(event["pon"], event["ont_id"]))
    counts = {}
    for keys in polls.values():
        ordered = sorted(keys)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                counts[(a, b)] = counts.get((a, b), 0) + 1
    return counts


def splitter_groups(views, counts):
    """ONTs on one PON that likely share a splitter: within
    SPLITTER_DISTANCE_M of the nearest-first ONT of their run, or down
    together SPLITTER_TOGETHER_MIN times or more. -> [{pon, onts (keys,
    nearest first), min_m, max_m, together, by_distance, by_together}]."""
    parent = {v["key"]: v["key"] for v in views}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def join(a, b):
        parent[root(a)] = root(b)

    by_pon = {}
    for view in views:
        by_pon.setdefault(view["pon"], []).append(view)
    near = set()
    for members in by_pon.values():
        placed = sorted((v for v in members if v["distance_m"] is not None), key=lambda v: v["distance_m"])
        start = None
        for view in placed:
            if start is not None and view["distance_m"] - start["distance_m"] <= config.SPLITTER_DISTANCE_M:
                join(view["key"], start["key"])
                near.update((view["key"], start["key"]))
            else:
                start = view
    pon_of = {v["key"]: v["pon"] for v in views}
    together = {}
    for (a, b), times in counts.items():
        if times >= config.SPLITTER_TOGETHER_MIN and a in parent and b in parent and pon_of[a] == pon_of[b]:
            join(a, b)
            together[a] = max(together.get(a, 0), times)
            together[b] = max(together.get(b, 0), times)
    groups = {}
    for view in views:
        groups.setdefault(root(view["key"]), []).append(view)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda v: (v["distance_m"] is None, v["distance_m"] or 0, v["ont_id"]))
        distances = [v["distance_m"] for v in members if v["distance_m"] is not None]
        out.append({"pon": members[0]["pon"], "onts": [v["key"] for v in members],
                    "min_m": min(distances, default=None), "max_m": max(distances, default=None),
                    "together": max((together.get(v["key"], 0) for v in members), default=0),
                    "by_distance": any(v["key"] in near for v in members),
                    "by_together": any(v["key"] in together for v in members)})
    return sorted(out, key=lambda g: (pon_sort_key(g["pon"]), g["min_m"] if g["min_m"] is not None else 1e9))


def pon_rows(views, pon_readings, last_events):
    """One row per PON: the OLT's own online/offline counts (its latest `pon`
    reading) or, without one, the ONTs'; stale registrations; the worst light
    on it and how many ONTs are weak / bad / overloaded; its last event."""
    by_pon = {}
    for view in views:
        by_pon.setdefault(view["pon"], []).append(view)
    rows = []
    for pon in sorted(set(by_pon) | set(pon_readings), key=pon_sort_key):
        members = by_pon.get(pon, [])
        reading = pon_readings.get(pon) or {}
        online = [v for v in members if v["status"] == ONLINE]
        levels = [v["level"] for v in online]
        rows.append({
            "pon": pon, "at": reading.get("ts"),
            "online": reading.get("online") if reading.get("online") is not None else len(online),
            "offline": reading.get("offline") if reading.get("offline") is not None
            else sum(1 for v in members if v["status"] == OFFLINE),
            "stale": sum(1 for v in members if v["stale"]),
            "health": worst_level(*levels) if levels else None,
            "weak": levels.count(WEAK), "bad": levels.count(BAD), "overloaded": levels.count(OVERLOADED),
            "names": [v["name"] for v in sorted(members, key=lambda v: v["ont_id"])],
            "last_event": last_events.get(pon),
        })
    return rows
