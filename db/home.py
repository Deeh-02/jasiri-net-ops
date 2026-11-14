"""Dashboard (2026-11-14): one small summary per area of the app, for the
landing page's charts. Nothing here is new data: every summary is boiled down
from the same function the area's own page already calls, so the Dashboard
and that page can never disagree.

Each summary has two halves. `load` fetches what the area's page fetches;
the function named after the area reduces that to the handful of counts and
series the Dashboard draws. The reducers are pure (no database), so they are
what the tests exercise. Only counts, names of sites/items and series leave
this module: never a customer's name or phone, and money only as the strings
revenue_report already formatted.

Who may see which summary is decided in routers/home.py, by the same
permission that opens the area's own page.
"""
from collections import Counter
from datetime import datetime, timedelta

DOWN_SITES_SHOWN = 6
BUSIEST_SHOWN = 6
PLANS_SHOWN = 6
LOW_STOCK_SHOWN = 6
CATEGORIES_SHOWN = 5
EXPIRING_DAYS = 7

# How a site's stored state reads on the page, and which colour family it
# takes ("good" / "bad" / "warn" / "idle": the page maps these to tokens).
SITE_STATES = [
    ("up", "Up", "good"), ("online", "Up", "good"),
    ("down", "Down", "bad"), ("offline", "Down", "bad"),
    ("flapping", "Unstable", "warn"),
    ("unknown", "Not reporting", "idle"),
]
ONU_TONES = {"Online": "good", "Offline": "idle", "LOS": "bad", "Power fail": "warn"}
SIGNAL_TONES = {"Very good": "good", "Warning": "warn", "Critical": "bad"}
CHARGE_LABELS = {"charged": "Charged", "charging": "Charging", "low": "Low", "unknown": "Not recorded"}
CHARGE_TONES = {"charged": "good", "charging": "info", "low": "warn"}
ACCOUNT_TONES = {"active": "good", "expired": "bad", "inactive": "idle"}


def _parts(counter, order=(), labels=None, tones=None):
    """A count per label as a list the page can draw in a fixed order: the
    ones named in `order` first, anything else after by size. Zero counts are
    left out."""
    labels, tones = labels or {}, tones or {}
    keys = [k for k in order if counter.get(k)] + sorted((k for k in counter if k not in order and counter[k]),
                                                         key=lambda k: (-counter[k], str(k)))
    return [{"key": str(k), "label": labels.get(k, str(k)), "count": counter[k], "tone": tones.get(k, "idle")}
            for k in keys]


# ---- Sites: who is up, who is down, where the people are ----

def sites(rows):
    """`rows` is monitoring.get_site_statuses(False)."""
    tone_of = {state: (label, tone) for state, label, tone in SITE_STATES}
    merged, tones = Counter(), {}
    for r in rows:
        label, tone = tone_of.get(r.get("state"), ("Not reporting", "idle"))
        merged[label] += 1
        tones[label] = tone
    tone = lambda r: tone_of.get(r.get("state"), ("", "idle"))[1]
    down = [r for r in rows if tone(r) == "bad"]
    down.sort(key=lambda r: r.get("state_since") or "")          # longest down first
    # A down site's last count is history, not people online now.
    with_people = [r for r in rows if (r.get("sessions") or 0) > 0 and tone(r) in ("good", "warn")]
    with_people.sort(key=lambda r: (-r["sessions"], r["name"]))
    up = merged.get("Up", 0)
    return {
        "total": len(rows),
        "up": up,
        "states": _parts(merged, order=("Up", "Unstable", "Down", "Not reporting"), tones=tones),
        "down": [{"id": r["id"], "name": r["name"], "since": r.get("state_since")} for r in down[:DOWN_SITES_SHOWN]],
        "down_more": max(0, len(down) - DOWN_SITES_SHOWN),
        "people_online": sum(r["sessions"] for r in with_people),
        "busiest": [{"id": r["id"], "name": r["name"], "people": r["sessions"]} for r in with_people[:BUSIEST_SHOWN]],
    }


def people(fleet):
    """`fleet` is monitoring.get_fleet(False) for the current week: the
    hourly count of people online across every site. Hours nobody measured
    stay in the series as gaps (None), never as zero."""
    counted = {p["hour"]: p.get("avg") for p in fleet.get("people") or []}
    points = []
    if counted:
        # The fleet series only lists hours that were counted; put the missing
        # ones back as None so the chart breaks its line there.
        stamp = "%Y-%m-%dT%H:%M:%SZ"
        try:
            hour, last = (datetime.strptime(h, stamp) for h in (min(counted), max(counted)))
        except ValueError:                      # not whole hours: draw what there is
            hour, last = None, None
            points = [{"t": h, "people": counted[h]} for h in sorted(counted)]
        while hour is not None and hour <= last:
            key = hour.strftime(stamp)
            points.append({"t": key, "people": counted.get(key)})
            hour += timedelta(hours=1)
    measured = [p["people"] for p in points if p["people"] is not None]
    totals = fleet.get("totals") or {}
    return {
        "points": points,
        "average": round(sum(measured) / len(measured)) if measured else None,
        "peak": totals.get("peak_people"),
        "peak_at": totals.get("peak_at"),
        "since": fleet.get("since"),
    }


# ---- Fibre: the ONUs ----

def fibre(listing):
    """`listing` is smartolt.configured_list()."""
    if not listing.get("configured"):
        return {"configured": False}
    onus = listing.get("onus") or []
    status = Counter(listing.get("counts") or Counter(o.get("status") for o in onus))
    signal = Counter(o["signal_quality"] for o in onus if o.get("signal_quality"))
    return {
        "configured": True,
        "total": listing.get("total", len(onus)),
        "online": status.get("Online", 0),
        "status": _parts(status, order=("Online", "Power fail", "LOS", "Offline"), tones=ONU_TONES),
        "signal": _parts(signal, order=("Very good", "Warning", "Critical"), tones=SIGNAL_TONES),
        "weak": listing.get("weak", 0),
        "as_of": listing.get("status_polled_at"),
        "stale": bool(listing.get("status_stale")),
    }


# ---- Customers: accounts by state and package. Counts only. ----

def customers(rows, now=None):
    """`rows` is customers.list_customers(). `expiry` is an ISO string in UTC."""
    now = now or datetime.utcnow()
    stamp = lambda d: d.isoformat()[:19]          # "YYYY-MM-DDTHH:MM:SS": sorts as text, zone suffix or not
    today, soon = stamp(now), stamp(now + timedelta(days=EXPIRING_DAYS))
    status = Counter((r.get("account_status") or "unknown").lower() for r in rows)
    active = [r for r in rows if (r.get("account_status") or "").lower() == "active"]
    plans = Counter((r.get("plan") or "No package").strip() or "No package" for r in active)
    top = plans.most_common(PLANS_SHOWN)
    other = sum(plans.values()) - sum(n for _, n in top)
    by_plan = [{"label": name, "count": n} for name, n in top]
    if other:
        by_plan.append({"label": "Other packages", "count": other})
    return {
        "total": len(rows),
        "active": len(active),
        "status": _parts(status, order=("active", "expired", "inactive"),
                         labels={"active": "Active", "expired": "Expired", "inactive": "Inactive", "unknown": "Not set"},
                         tones=ACCOUNT_TONES),
        "expiring_soon": sum(1 for r in active if r.get("expiry") and today <= r["expiry"][:19] <= soon),
        "expiring_days": EXPIRING_DAYS,
        "switched_off": sum(1 for r in rows if r.get("enabled") is False),
        "by_plan": by_plan,
    }


# ---- Batteries ----

def batteries(rows, moving=None):
    """`rows` is batteries.get_all_batteries(); `moving` is the open-movement
    count, or None when the viewer may not see movements."""
    where = Counter(r.get("status") or "Unknown" for r in rows)
    charge = Counter((r.get("charge_status") or "unknown").lower() for r in rows)
    return {
        "total": len(rows),
        "where": _parts(where, order=("Deployed", "At Base", "In Transit", "Pending"),
                        tones={"Deployed": "good", "At Base": "info", "In Transit": "warn", "Pending": "idle"}),
        "charge": _parts(charge, order=("charged", "charging", "low"), labels=CHARGE_LABELS, tones=CHARGE_TONES),
        "at_dark_sites": sum(1 for r in rows if r.get("status") == "Deployed" and r.get("site_online") is False),
        "moving": moving,
    }


# ---- Technician activity ----

def activity(overview):
    """`overview` is activity.get_overview(start, end, "day")."""
    outcomes = overview.get("outcomes") or {}
    cats = sorted(overview.get("by_category") or [], key=lambda c: (-c["count"], c["category"]))
    return {
        "start": overview.get("start"),
        "end": overview.get("end"),
        "total": overview.get("total", 0),
        "per_day": [{"day": str(p["period"]), "count": p["count"]} for p in overview.get("over_time") or []],
        "by_category": [{"label": c["category"], "count": c["count"]} for c in cats[:CATEGORIES_SHOWN] if c["count"]],
        "still_open": outcomes.get("still_open", 0),
        "technicians": sum(1 for t in overview.get("by_technician") or [] if t.get("count")),
    }


# ---- Stock ----

def stock(rows):
    """`rows` is inventory_reports.get_items_summary(None). An item is low
    when it has a reorder level and what's on hand is at or under it."""
    low = [r for r in rows if r.get("reorder_level") is not None and (r.get("on_hand_qty") or 0) <= r["reorder_level"]]
    low.sort(key=lambda r: ((r.get("on_hand_qty") or 0) / r["reorder_level"] if r["reorder_level"] else 0, r["name"]))
    categories = Counter(r.get("category_name") or "Uncategorised" for r in rows)
    return {
        "items": len(rows),
        "with_level": sum(1 for r in rows if r.get("reorder_level") is not None),
        "out": sum(1 for r in rows if (r.get("on_hand_qty") or 0) <= 0),
        "low_count": len(low),
        "low": [{"name": r["name"], "on_hand": r.get("on_hand_qty") or 0, "reorder_level": r["reorder_level"]}
                for r in low[:LOW_STOCK_SHOWN]],
        "by_category": [{"label": name, "count": n} for name, n in categories.most_common(CATEGORIES_SHOWN)],
    }


# ---- Internet uplink ----

def uplink(overview, history):
    """`overview` is network.overview(slug) (None when the router isn't set
    up); `history` is network.uplink_history(slug, "24h")."""
    if overview is None:
        return {"configured": False}
    status, total = overview.get("status") or {}, overview.get("total") or {}
    reasons = status.get("reasons") or []
    return {
        "configured": True,
        "router": (overview.get("router") or {}).get("name"),
        "level": status.get("level"),
        "reason": reasons[0]["text"] if reasons else None,
        "download_mbps": total.get("download_mbps"),
        "upload_mbps": total.get("upload_mbps"),
        "peak_download_mbps": total.get("peak_today_download_mbps"),
        "received_at": overview.get("received_at"),
        "points": [{"t": p["t"], "download": p.get("download_mbps_avg"), "upload": p.get("upload_mbps_avg")}
                   for p in (history or {}).get("points") or []],
    }


# ---- Revenue ----

def revenue(trend):
    """`trend` is revenue_report.trend("daily_14"). Money stays as the strings
    the report formatted; `value` is only there to size the bars."""
    points = trend.get("points") or []
    today = next((p for p in reversed(points) if p.get("partial")), None)
    return {
        "as_of": trend.get("as_of"),
        "today": today["display"] if today else None,
        "overview": trend.get("overview"),
        "direction": (trend.get("trend") or {}).get("label"),
        "points": [{k: p.get(k) for k in ("start", "label", "name", "value", "display", "partial", "vs_average_pct")}
                   for p in points],
    }
