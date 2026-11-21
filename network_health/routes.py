"""Where content is served from: the `route` records (a traceroute from the
CCR2116 to a few big sites, daily at 21:00 and 04:00 Nairobi time).

Pure code, no database and no clock. The last hop that answered tells
where a site is served from: a local cache answers in a few ms, a server
in Europe in 140+. Nothing here alerts: it's page only, and the history is
what gets compared after the provider switch.
"""
from network_health import config
from network_health.rules import eat_hour

KENYA, AFRICA, SOUTH_AFRICA, ABROAD = "kenya", "africa", "south_africa", "abroad"
# Glyph + word, never colour alone (DESIGN.md). Failed has no verdict.
VERDICT_LABELS = {KENYA: "Kenya", AFRICA: "Elsewhere in Africa / unclear", SOUTH_AFRICA: "South Africa (likely)",
                  ABROAD: "Europe / abroad"}
CONTENT, INTERNATIONAL = "content", "international"
PEAK, NIGHT = "peak", "night"
SLOT_LABELS = {PEAK: "21:00 peak", NIGHT: "04:00 night"}


def where_served(last_ms):
    """Under 15 ms = Kenya; 15 up to 50 = elsewhere in Africa or unclear;
    50 up to 110 = South Africa (likely); 110 up to 140 = unclear again;
    140 and over = Europe / abroad. None (the check failed, or no hop
    answered) = no verdict."""
    if last_ms is None:
        return None
    if last_ms < config.ROUTE_KENYA_UNDER_MS:
        return KENYA
    if config.ROUTE_ZA_FROM_MS <= last_ms < config.ROUTE_ZA_UNDER_MS:
        return SOUTH_AFRICA
    if last_ms < config.ROUTE_ABROAD_FROM_MS:
        return AFRICA
    return ABROAD


def verdict_of(route):
    """A stored route's verdict. Only status "failed" has none: a traceroute
    that stopped at its 20-hop limit before the target still has a last hop
    that answered, and that is what the verdict reads."""
    if not route or route.get("status") == "failed":
        return None
    return where_served(route.get("last_ms"))


def group_of(host):
    """Content sites say where content is served; the international-path
    targets (Frankfurt, 1.1.1.1) are there to catch transit changes."""
    return INTERNATIONAL if host in config.ROUTE_INTERNATIONAL else CONTENT


def host_label(host):
    return config.ROUTE_HOST_LABELS.get(host, host)


def reached_target(route):
    """Did a hop answer from the target's own address? False when the
    traceroute stopped (20-hop limit) on an earlier hop; None when there's
    nothing to compare."""
    hops = (route or {}).get("hops") or []
    ip = (route or {}).get("ip")
    if not ip or not hops:
        return None
    return any(hop.get("address") == ip for hop in hops)


def last_answering_hop(route):
    answered = [hop for hop in (route or {}).get("hops") or [] if hop.get("address")]
    return answered[-1] if answered else None


def slot(ts):
    """Which daily check a record belongs to: the 21:00 peak run or the 04:00
    night run. Split at midday so a late-arriving result keeps its slot."""
    return PEAK if eat_hour(ts) >= 12 else NIGHT


def change(previous_verdict, latest_verdict):
    """{'was', 'now'} when a site moved, else None. Both sides need a verdict:
    a failed check says nothing about where content is served."""
    if previous_verdict is None or latest_verdict is None or previous_verdict == latest_verdict:
        return None
    return {"was": previous_verdict, "now": latest_verdict}


def site_rows(records):
    """One row per site from its route records (any order): the latest check,
    and whether its verdict changed since the previous check OF THE SAME SLOT
    (21:00 against 21:00, 04:00 against 04:00) that had a verdict.

    Each record is {ts, route}. Rows come back content sites first (in the
    order of config.ROUTE_SITES, then any other host alphabetically), then
    the international-path targets in the order of config.ROUTE_INTERNATIONAL;
    each row says which group it is in."""
    by_host = {}
    for record in sorted(records, key=lambda r: r["ts"]):
        host = (record.get("route") or {}).get("host")
        if host:
            by_host.setdefault(host, []).append(record)

    def order(host):
        if host in config.ROUTE_INTERNATIONAL:
            return (2, config.ROUTE_INTERNATIONAL.index(host), host)
        if host in config.ROUTE_SITES:
            return (0, config.ROUTE_SITES.index(host), host)
        return (1, 0, host)

    rows = []
    for host in sorted(by_host, key=order):
        history = by_host[host]
        latest = history[-1]
        same_slot = [r for r in history[:-1] if slot(r["ts"]) == slot(latest["ts"]) and verdict_of(r["route"])]
        previous = same_slot[-1] if same_slot else None
        route = latest["route"]
        last_hop = last_answering_hop(route)
        rows.append({
            "host": host,
            "label": host_label(host),
            "group": group_of(host),
            "reached_target": reached_target(route),
            "last_hop_number": last_hop and last_hop.get("hop"),
            "last_hop_address": last_hop and last_hop.get("address"),
            "checked_at": latest["ts"],
            "slot": slot(latest["ts"]),
            "status": route.get("status"),
            "ip": route.get("ip"),
            "last_ms": route.get("last_ms"),
            "verdict": verdict_of(route),
            "hops": route.get("hops") or [],
            "change": change(previous and verdict_of(previous["route"]), verdict_of(route)),
            "previous_checked_at": previous and previous["ts"],
        })
    return rows
