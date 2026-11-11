"""The assistant's tools — the ONLY way the model can see data.

Each tool is one fixed, read-only lookup that calls the same function a page
of the app uses (the Revenue Report, Site Status, Trends, the Network page,
Batteries, Stock, Inventory Log, the SMS log, Packages, the customer list),
so the assistant sees the same numbers the page shows. One tool per page or
report, not per question. The model picks a tool and fills in a few checked arguments; it never
writes SQL, never names a table, and cannot change anything.

WHO GETS WHAT is decided here in code, never by the prompt. Since
2026-11-11 (owner's call) everyone can use the assistant, but each tool
needs the same permission as the page it reads (ACCESS below): batteries
tools need batteries:view, the network and OLT tools sites:view_network,
and so on. The revenue tools are admins only, and get_site_status leaves
today's revenue out for anyone else. The model is only ever given the tools
the person may use, and run_tool() checks again before running anything,
so a mistake in one layer is caught by the other.

WHAT LEAVES. DeepSeek receives every tool result, so results are trimmed to
what an answer needs: revenue is totals only (no individual sales), and the
customer lookup returns plan/status/expiry but not phone numbers, the
customer profile and ONU tools leave out phone, address, contact and every
password, the alert
log leaves out who an SMS went to, and the PPPoE list leaves out customers'
IP addresses and device IDs. The one
phone number that does go out is a SITE's contact (get_site_contact): giving
it is that tool's whole purpose.

Every call is written to the log (time, user, role, tool, allowed or not,
outcome) — never the arguments' values or the result.
"""
import contextvars
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from db import batteries, customers, inventory_reports, inventory_transactions, monitoring, network, smartolt
from db import revenue_config, revenue_daily, revenue_report, sites, sms_log
from db.connection import EAT, db_cursor, now_eat
from db.revenue_logic import MODES, compare, format_kes, slope_pct_of_mean, trend_label

log = logging.getLogger(__name__)

MAX_ROWS = 20
MAX_DAILY_POINTS = 62
MAX_HISTORY_DAYS = 366
MAX_RESULT_CHARS = 20000


class ToolArgumentError(ValueError):
    """The model sent arguments the tool doesn't accept. The message goes back
    to the model so it can correct itself."""


# The person asking, for the few tools whose answer depends on who it is
# (revenue left out of site status, technician visits on the profile).
_viewer = contextvars.ContextVar("assistant_viewer", default=None)

ADMIN_ONLY = "admin"


def is_admin(user):
    return bool(user) and user.get("role") == "admin"


def has_permission(user, section, action):
    if not user:
        return False
    if is_admin(user):
        return True
    from routers.permissions import user_has_permission    # late: routers import this module
    try:
        return user_has_permission(user, section, action)
    except Exception:
        log.exception("assistant: permission check failed")
        return False


def role_for(current_user):
    """A label for the logs: admin, staff (some tools) or None (no tools)."""
    if is_admin(current_user):
        return "admin"
    return "staff" if any(allowed(current_user, n) for n in TOOLS) else None


def allowed(current_user, name):
    """Whether this person may use this tool: admins everything; anyone else
    needs one of the permissions in ACCESS. Unknown tool: no."""
    access = ACCESS.get(name)
    if access is None or not current_user:
        return False
    if is_admin(current_user):
        return True
    if access == ADMIN_ONLY:
        return False
    return any(has_permission(current_user, section, action) for section, action in access)


# ---- Argument checking ----

def _no_extra_keys(args, allowed):
    extra = set(args) - set(allowed)
    if extra:
        raise ToolArgumentError(f"unexpected argument(s): {', '.join(sorted(extra))}")


def _optional_text(args, key, max_length):
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value.strip()) > max_length:
        raise ToolArgumentError(f"{key} must be text of at most {max_length} characters")
    return value.strip() or None


def _optional_date(args, key):
    value = args.get(key)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ToolArgumentError(f"{key} must be a date like 2026-11-03")


def _name_key(text):
    """'Red Soil' / 'Redsoil_Hotspot' -> 'redsoil' / 'redsoilhotspot': site names
    are written every which way, so they're compared without case, spaces or
    punctuation."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# ---- The tools ----

def _nairobi(iso_utc):
    """'2026-11-03T05:00:00Z' -> 'Tue 03/11 08:00' (Nairobi). Done here, not
    left to the model, which was seen quoting the raw UTC time."""
    if not iso_utc:
        return None
    moment = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(EAT).strftime("%a %d/%m/%Y %H:%M")


def _money_fields(display):
    return {"value": display["current"], "compared_value": display["previous"],
            "change": display["abs_change"], "change_pct": display["pct_change"]}


UNRECORDED = ("sales records start on {start}: anything before that day is unrecorded, not zero. "
              "Never report a figure or a change for days before it.")


def _period_end(period, anchor):
    """Last day of the day/week/month/year containing `anchor`."""
    if period == "day":
        return anchor
    if period == "week":
        return anchor + timedelta(days=6 - anchor.weekday())
    if period == "month":
        nxt = date(anchor.year + (anchor.month == 12), anchor.month % 12 + 1, 1)
        return nxt - timedelta(days=1)
    return date(anchor.year, 12, 31)


def _records_start():
    """The first day with any recorded hotspot sale (EAT), or None."""
    with db_cursor() as (conn, cur):
        return revenue_daily.first_sale_date(cur)


def _no_records_before(start, first_day):
    """A result saying the period predates the records, instead of a false KSh 0."""
    return {"no_records": f"There are no sales records for that period: records start on "
                          f"{start.strftime('%a %d %b %Y')}, so {first_day.strftime('%d %b %Y')} is before them. "
                          "It is not KSh 0, it simply wasn't recorded."}


def get_revenue_summary(args):
    """Headline revenue, or one period's totals (optionally per site)."""
    _no_extra_keys(args, ("period", "date", "compare", "by_site"))
    period = args.get("period")
    if period is None:
        return _revenue_overview()
    if period not in MODES:
        raise ToolArgumentError("period must be one of day, week, month, year")
    compare = args.get("compare")
    if compare is not None and (compare not in MODES[period] or compare == "custom"):
        options = ", ".join(m for m in MODES[period] if m != "custom")
        raise ToolArgumentError(f"compare for a {period} must be one of: {options}")
    by_site = args.get("by_site", False)
    if not isinstance(by_site, bool):
        raise ToolArgumentError("by_site must be true or false")
    anchor = _optional_date(args, "date") or now_eat().date()
    records_start = _records_start()
    if records_start and anchor < records_start and _period_end(period, anchor) < records_start:
        return _no_records_before(records_start, anchor)

    report = revenue_report.sites_report(period, anchor, mode=compare)
    p = report["period"]
    result = {
        "scope": "hotspot sales only, attributed to a site (not total company revenue)",
        "period": p["period_label"], "compared_with": p["comparison_label"],
        "in_progress": p["is_partial"], "notes": p["notes"],
        **_money_fields(report["network"]["display"]),
    }
    if records_start:
        result["records_start"] = records_start.isoformat()
        result["about_records"] = UNRECORDED.format(start=records_start.isoformat())
    if by_site:
        rows = sorted(report["sites"], key=lambda r: -r["current"])[:MAX_ROWS]
        result["sites"] = [{"site": r["name"], **_money_fields(r["display"])} for r in rows]
        result["site_count"] = report["site_count"]
    return result


def _revenue_overview():
    overview = revenue_report.overview()
    return {
        "scope": "hotspot sales only, attributed to a site (not total company revenue)",
        "cards": [{"card": c["title"], "period": c["period_label"], "compared_with": c["comparison_label"],
                   "in_progress": c["is_partial"], **_money_fields(c["display"])} for c in overview["cards"]],
        "sites_needing_attention_yesterday": overview["attention_count"],
    }


def _pct_text(change):
    """'+12.5%', or the comparison's own word ('New', 'No activity') when a
    percentage can't be taken from a zero base."""
    if change["pct_change"] is not None:
        return f"{change['pct_change']:+}%"
    return change["label"]


def _bucket_start(day, group_by):
    if group_by == "week":
        return day - timedelta(days=day.weekday())   # weeks run Monday-Sunday, as in the report
    if group_by == "month":
        return day.replace(day=1)
    return day


def _bucket_label(start, group_by):
    if group_by == "week":
        return f"week of {start:%a %d/%m/%Y}"
    if group_by == "month":
        return f"{start:%B %Y}"
    return f"{start:%a %d/%m/%Y}"


def _group_days(days, group_by):
    """Daily rows -> [{start, revenue, days, in_progress}] per day/week/month.
    A bucket is in progress if it holds today or is cut off by the range."""
    buckets = {}
    for d in days:
        b = buckets.setdefault(_bucket_start(d["date"], group_by),
                               {"revenue": Decimal(0), "days": 0, "in_progress": False})
        b["revenue"] += d["revenue"]
        b["days"] += 1
        b["in_progress"] = b["in_progress"] or d["in_progress"]
    full_length = {"day": lambda s: 1, "week": lambda s: 7,
                   "month": lambda s: ((s.replace(day=28) + timedelta(days=4)).replace(day=1) - s).days}
    return [{"start": start, **b, "in_progress": b["in_progress"] or b["days"] < full_length[group_by](start)}
            for start, b in sorted(buckets.items())]


def _resolve_site(name):
    """(site_id, site name) for a name fragment, or a dict to hand back to the
    model when there's no match or more than one."""
    matches = revenue_report.find_sites(name)
    exact = [m for m in matches if _name_key(m[1]) == _name_key(name)]
    if len(exact) == 1 or len(matches) == 1:
        return (exact or matches)[0]
    if not matches:
        return {"error": f"no site matches '{name}'"}
    return {"error": "more than one site matches; ask which one", "candidates": [m[1] for m in matches[:MAX_ROWS]]}


def get_revenue_history(args):
    """Revenue over a date range — the network or one site — per day, week or
    month, with each period's change on the one before, and the overall
    trajectory."""
    _no_extra_keys(args, ("start_date", "end_date", "site", "group_by"))
    today = now_eat().date()
    start = _optional_date(args, "start_date")
    if start is None:
        raise ToolArgumentError("start_date is required")
    end = min(_optional_date(args, "end_date") or today, today)
    if start > end:
        raise ToolArgumentError("start_date must be on or before end_date (and not in the future)")
    if (end - start).days + 1 > MAX_HISTORY_DAYS:
        raise ToolArgumentError(f"the range can be at most {MAX_HISTORY_DAYS} days")
    records_start = _records_start()
    if records_start and end < records_start:
        return _no_records_before(records_start, start)
    clipped_from = None
    if records_start and start < records_start:
        # Days before the records would show as KSh 0 and drag every average
        # and trend down: start where the records start, and say so.
        clipped_from, start = start, records_start
    group_by = args.get("group_by", "day")
    if group_by not in ("day", "week", "month"):
        raise ToolArgumentError("group_by must be day, week or month")
    if group_by == "day" and (end - start).days + 1 > MAX_DAILY_POINTS:
        raise ToolArgumentError(f"more than {MAX_DAILY_POINTS} days: use group_by week or month")

    site_name, site_id = "all sites (network)", None
    name = _optional_text(args, "site", 60)
    if name:
        found = _resolve_site(name)
        if isinstance(found, dict):
            return found
        site_id, site_name = found

    history = revenue_report.daily_history(start, end, site_id=site_id)
    if not history["days"]:
        return {"site": site_name, "error": "no revenue history in that range",
                "history_starts": history["history_start"].isoformat() if history["history_start"] else None}
    flat_band = revenue_config.load()["flat_band_pct"]
    buckets = _group_days(history["days"], group_by)

    points, previous = [], None
    for b in buckets:
        point = {"period": _bucket_label(b["start"], group_by), "revenue": format_kes(b["revenue"]),
                 "in_progress": b["in_progress"]}
        if previous is not None:
            change = compare(b["revenue"], previous, flat_band)
            point["change_vs_previous"] = format_kes(change["abs_change"])
            point["change_pct_vs_previous"] = _pct_text(change)
        points.append(point)
        previous = b["revenue"]

    complete = [b for b in buckets if not b["in_progress"]]
    total = sum((d["revenue"] for d in history["days"]), Decimal(0))
    summary = {
        "total": format_kes(total),
        "days_counted": len(history["days"]),
        "average_per_day": format_kes((total / len(history["days"])).quantize(Decimal("0.01"))),
    }
    if len(complete) >= 2:
        overall = compare(complete[-1]["revenue"], complete[0]["revenue"], flat_band)
        summary["first_to_last_complete"] = {
            "from": _bucket_label(complete[0]["start"], group_by), "to": _bucket_label(complete[-1]["start"], group_by),
            "change": format_kes(overall["abs_change"]), "change_pct": _pct_text(overall)}
        best = max(complete, key=lambda b: b["revenue"])
        worst = min(complete, key=lambda b: b["revenue"])
        summary["best"] = {"period": _bucket_label(best["start"], group_by), "revenue": format_kes(best["revenue"])}
        summary["worst"] = {"period": _bucket_label(worst["start"], group_by), "revenue": format_kes(worst["revenue"])}
    label, rule = trend_label(slope_pct_of_mean([b["revenue"] for b in complete]), flat_band)
    summary["trend"] = {"label": label, "rule": rule, "based_on_complete_periods": len(complete)}

    return {
        "scope": "hotspot sales only, attributed to a site (not total company revenue)",
        "site": site_name, "from": start.isoformat(), "to": end.isoformat(), "group_by": group_by,
        "note": ("periods marked in_progress are incomplete (today, or cut off by the range) and are "
                 "left out of the trend, best/worst and first-to-last figures"),
        "points": points, "summary": summary,
        **({"records_start": records_start.isoformat(),
            "about_records": UNRECORDED.format(start=records_start.isoformat())} if records_start else {}),
        **({"range_clipped": f"you asked from {clipped_from.isoformat()}, but records only start on "
                             f"{records_start.isoformat()}, so the figures start there"} if clipped_from else {}),
    }


# The Revenue Report's own words for its red / amber / green flags.
STATUS_WORDS = {"red": "investigate", "amber": "needs attention", "green": "doing well"}


def get_site_performance(args):
    """Every site ranked by its usual daily revenue, with whether it's
    slipping: the same figures as the Revenue Report's site table."""
    _no_extra_keys(args, ())
    yesterday = now_eat().date() - timedelta(days=1)
    report = revenue_report.sites_report("day", yesterday)
    # Same 14 completed days the average covers (the report's own window).
    window = revenue_report.site_window_totals(yesterday - timedelta(days=13), yesterday)
    totals = window["totals"]
    network_total = sum(totals.values(), Decimal(0))
    rows = sorted(report["sites"], key=lambda r: -Decimal(str(r["avg_daily_14d"])))
    result_sites = []
    for rank, r in enumerate(rows, start=1):
        trend = r.get("trend7")
        site_total = totals.get(r["site_id"], Decimal(0))
        result_sites.append({
            "rank_by_daily_average": rank,
            "site": r["name"],
            "active": r["active"],
            "average_per_day_last_14_days": format_kes(r["avg_daily_14d"]),
            "total_last_14_days": format_kes(site_total),
            "share_of_network_total": (f"{(site_total / network_total * 100).quantize(Decimal('0.1'))}%"
                                       if network_total else None),
            "last_7_days_vs_previous_7": (None if trend is None else
                                          f"{trend['pct_change']:+}%" if trend["pct_change"] is not None
                                          else trend["label"]),
            "yesterday": r["display"]["current"],
            "yesterday_vs_same_weekday_last_week": r["display"]["pct_change"] or r["label"],
            "flag_for_yesterday": STATUS_WORDS.get(r["status"]),
            "why_flagged": [a["text"][:160] for a in (r.get("status_alerts") or [])[:2]],
        })
    return {
        "scope": "hotspot sales only, attributed to a site",
        "flags_are_as_of": report["status_day"],
        "window": {"from": window["first"].isoformat() if window["first"] else None,
                   "to": window["last"].isoformat() if window["last"] else None},
        "network_total_last_14_days": format_kes(network_total),
        "how_to_read": ("ranked by average daily revenue over the last 14 completed days (the ranking by "
                        "total_last_14_days is the same order); 'last_7_days_vs_previous_7' "
                        "is the recent direction; flags are the Revenue Report's alert rules for yesterday "
                        "(investigate / needs attention / doing well)"),
        "sites": result_sites,
    }


def get_site_status(args):
    """Which sites are up or down right now, people connected, 30-day uptime."""
    _no_extra_keys(args, ("site",))
    name_filter = _optional_text(args, "site", 60)
    with_revenue = is_admin(_viewer.get())
    sites = monitoring.get_site_statuses(include_revenue=with_revenue)
    if name_filter:
        sites = [s for s in sites if _name_key(name_filter) in _name_key(s["name"])]
    counts = {}
    for s in sites:
        counts[s["state"]] = counts.get(s["state"], 0) + 1
    return {
        "last_router_report_at": _nairobi(monitoring.get_last_ingest_at()),
        "counts_by_state": counts,
        "sites": [{
            "site": s["name"], "state": s["state"], "state_since": _nairobi(s["state_since"]),
            "people_connected": s["sessions"], "people_when_it_dropped": s["sessions_at_drop"],
            "uptime_30d_pct": s["uptime_30d_pct"], "acknowledged": s["ack"] is not None,
            **({"revenue_today_kes": s.get("revenue_today_kes")} if with_revenue else {}),
        } for s in sites[:MAX_ROWS * 2]],
    }


def get_site_contact(args):
    """The contact person, phone and address saved for a site (Sites page)."""
    _no_extra_keys(args, ("site",))
    name = _optional_text(args, "site", 60)
    if not name or not _name_key(name):
        raise ToolArgumentError("site is required")
    key = _name_key(name)
    locations = sites.get_all_locations()
    matches = [loc for loc in locations if key in _name_key(loc["name"])]
    exact = [loc for loc in matches if _name_key(loc["name"]) == key]
    if exact:
        matches = exact
    if not matches:
        return {"error": f"no site matches '{name}'", "known_sites": [loc["name"] for loc in locations][:MAX_ROWS * 2]}
    if len(matches) > 5:
        return {"error": "more than one site matches; ask which one", "candidates": [loc["name"] for loc in matches]}
    return {"sites": [{"site": loc["name"], "contact_name": loc["contact_name"], "contact_phone": loc["contact_phone"],
                       "address": loc["address"]} for loc in matches]}


def get_packages(args):
    """The hotspot packages on sale and their prices."""
    _no_extra_keys(args, ())
    data = monitoring.list_packages()
    active = [p for p in data["packages"] if p["is_active"]][:MAX_ROWS]
    return {
        "packages": [{"package": p["profile_name"], "price_kes": p["price_kes"], "free": p["is_comped"],
                      "duration_minutes": p["duration_minutes"]} for p in active],
        "unpriced_profiles_seen": len(data["unpriced"]),
    }


def lookup_customer(args):
    """PPPoE customers matching a username, name or phone number."""
    _no_extra_keys(args, ("query",))
    query = _optional_text(args, "query", 60)
    if not query or len(query) < 2:
        raise ToolArgumentError("query must be at least 2 characters")
    matches = [{**m, "expiry": _nairobi(m["expiry"])} for m in customers.search_customers(query, limit=10)]
    return {"matches": matches, "match_count": len(matches),
            "note": "at most 10 matches are returned; ask for something more specific if needed"}


# ---- Customers and fibre (OLT) ----

def _when(dt):
    return _nairobi(smartolt._iso(dt)) if isinstance(dt, datetime) else _nairobi(dt)


def _find_customers(query, limit=6):
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    phone = customers.normalize_phone(query)
    with db_cursor() as (_, cur):
        cur.execute("""SELECT c.id, r.name, c.username, c.name, c.plan, c.expiry, c.account_status, c.enabled
                       FROM ppp_customers c JOIN ppp_routers r ON r.id = c.router_id
                       WHERE c.username ILIKE %s OR c.name ILIKE %s OR c.phone ILIKE %s
                       ORDER BY (lower(c.username) = lower(%s)) DESC, c.username LIMIT %s""",
                    (pattern, pattern, f"%{phone}%" if phone else pattern, query, limit))
        return [{"id": r[0], "router": r[1], "username": r[2], "name": r[3], "plan": r[4], "expiry": r[5],
                 "account_status": r[6], "enabled": r[7]} for r in cur.fetchall()]


ONU_SELECT = """SELECT s.unique_external_id, s.name, s.sn, s.status, s.last_status_change, s.signal_quality,
                       s.signal_1490_dbm, s.signal_1310_dbm, s.board, s.port, s.onu, s.details->>'zone_name',
                       s.details->>'odb_name', COALESCE(s.details->>'administrative_status', 'Enabled'),
                       s.status_polled_at, c.name, c.username, COALESCE(m.name, loc.name), l.not_a_customer
                FROM onu_state s
                LEFT JOIN onu_customer_links l ON l.unique_external_id = s.unique_external_id
                LEFT JOIN ppp_customers c ON c.id = l.customer_id
                LEFT JOIN monitored_sites m ON m.id = l.site_id
                LEFT JOIN locations loc ON loc.id = m.location_id"""


def _onu_row(r, now):
    belongs = (f"customer {r[15] or r[16]} ({r[16]})" if r[16] else f"hotspot site {r[17]}" if r[17]
               else "not a customer" if r[18] else "not linked to anyone yet")
    return {"onu": r[1] or r[2], "serial": r[2], "belongs_to": belongs, "status": r[3],
            "status_since": _when(r[4]), "signal_grade": r[5],
            "rx_dbm_at_onu": float(r[6]) if r[6] is not None else None,
            "rx_dbm_at_olt": float(r[7]) if r[7] is not None else None,
            "pon_location": "/".join(str(v) for v in (r[8], r[9], r[10]) if v is not None) or None,
            "zone": r[11], "splitter": r[12], "admin_state": r[13],
            "status_may_be_out_of_date": r[14] is None or now - r[14] > timedelta(minutes=15)}


def _open_los(cur, onu_id):
    cur.execute("""SELECT a.started_at, a.pon, a.onu_count, a.acked_at IS NOT NULL
                   FROM onu_alerts a JOIN onu_state s ON s.unique_external_id = %s
                   WHERE a.ended_at IS NULL AND (a.unique_external_id = %s OR a.pon = s.board || '/' || s.port)
                   ORDER BY a.started_at LIMIT 1""", (onu_id, onu_id))
    r = cur.fetchone()
    if not r:
        return None
    return {"since": _when(r[0]), "whole_pon_port": r[1], "onus_on_that_port": r[2] if r[1] else None,
            "acknowledged": r[3]}


def get_customer(args):
    """Everything OPS knows about one PPPoE customer, in one place."""
    _no_extra_keys(args, ("customer",))
    query = _optional_text(args, "customer", 60)
    if not query or len(query) < 2:
        raise ToolArgumentError("customer must be at least 2 characters")
    found = _find_customers(query)
    if not found:
        return {"found": 0, "note": "no customer matches that username, name or phone"}
    exact = [c for c in found if (c["username"] or "").lower() == query.lower()]
    if len(found) > 1 and not exact:
        return {"found": len(found), "note": "several customers match; ask which one",
                "matches": [{"username": c["username"], "name": c["name"], "plan": c["plan"]} for c in found]}
    c = exact[0] if exact else found[0]
    now = datetime.now(timezone.utc)
    expiry = c["expiry"]
    profile = {"account": {"name": c["name"], "username": c["username"], "router": c["router"], "plan": c["plan"],
                           "account_status": c["account_status"], "enabled": c["enabled"], "expiry": _when(expiry),
                           "expired": bool(expiry and expiry < datetime.utcnow())}}
    with db_cursor() as (_, cur):
        try:
            cur.execute(ONU_SELECT + " WHERE l.customer_id = %s", (c["id"],))
            r = cur.fetchone()
            if r:
                profile["fibre_onu"] = _onu_row(r, now)
                profile["fibre_onu"]["open_los_alert"] = _open_los(cur, r[0])
            else:
                profile["fibre_onu"] = None
                profile["fibre_note"] = "no ONU linked to this customer on OLT > Links"
        except Exception:
            log.warning("assistant: couldn't read the customer's ONU", exc_info=True)
            cur.connection.rollback()
            profile["fibre_onu"] = None
            profile["fibre_note"] = "the fibre (OLT) information couldn't be read"
        if has_permission(_viewer.get(), "activity_log", "view"):
            cur.execute("""SELECT r.activity_date, t.name, r.outcome, u.name, r.description
                           FROM activity_reports r JOIN activity_types t ON t.id = r.type_id
                           JOIN users u ON u.id = r.submitted_by
                           WHERE NOT r.is_deleted AND r.details @> jsonb_build_object('customer', jsonb_build_object('id', %s))
                           ORDER BY r.activity_date DESC, r.id DESC LIMIT 5""", (c["id"],))
            profile["technician_visits"] = [{"date": str(v[0]), "type": v[1], "outcome": v[2], "by": v[3],
                                             "notes": (v[4] or "")[:200] or None} for v in cur.fetchall()]
    data = network.pppoe(NETWORK_ROUTER)
    if data is not None:
        session = next((x for x in data["sessions"] if (x["username"] or "").lower() == (c["username"] or "").lower()), None)
        profile["online_now"] = {"connected": session is not None,
                                 "connected_for": _duration(session["uptime_s"]) if session else None,
                                 "list_received_at": _nairobi(data["received_at"]), "list_is_stale": data["is_stale"]}
    return profile


ONU_STATES = ("Online", "Offline", "LOS", "Power fail")


def get_onus(args):
    """OPS's copy of the OLT's ONU list (refreshed every few minutes), by state."""
    _no_extra_keys(args, ("status", "search", "weak_signal_only"))
    status = _optional_choice(args, "status", ONU_STATES)
    search = _optional_text(args, "search", 60)
    weak = _optional_bool(args, "weak_signal_only")
    now = datetime.now(timezone.utc)
    with db_cursor() as (_, cur):
        cur.execute(ONU_SELECT + " ORDER BY s.status, s.name")
        rows = [_onu_row(r, now) for r in cur.fetchall()]
    counts = {}
    for r in rows:
        counts[r["status"] or "unknown"] = counts.get(r["status"] or "unknown", 0) + 1
    rows = [r for r in rows if (not status or r["status"] == status)
            and (not weak or (r["signal_grade"] or "").lower() in ("warning", "critical"))
            and (not search or _matches(search, r["onu"], r["serial"], r["belongs_to"], r["zone"], r["splitter"]))]
    return {"total_onus_by_status": counts, "matching": len(rows), "onus": rows[:MAX_ROWS * 2],
            "note": "status and signal are OPS's copy from SmartOLT, refreshed every few minutes; "
                    "get_onu_live asks the OLT directly about one ONU"}


def _resolve_onu(query):
    """ONU id for a customer username/name, hotspot site, ONU name or serial."""
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with db_cursor() as (_, cur):
        cur.execute(ONU_SELECT + """ WHERE s.sn ILIKE %s OR s.name ILIKE %s OR c.username ILIKE %s OR c.name ILIKE %s
                                     OR m.name ILIKE %s OR loc.name ILIKE %s
                                     ORDER BY (lower(c.username) = lower(%s) OR lower(s.sn) = lower(%s)) DESC NULLS LAST, s.name
                                     LIMIT 6""", (pattern,) * 6 + (query, query))
        return cur.fetchall()


def get_onu_live(args):
    """Asks SmartOLT (the OLT) right now about one ONU."""
    _no_extra_keys(args, ("onu",))
    query = _optional_text(args, "onu", 60)
    if not query or len(query) < 2:
        raise ToolArgumentError("onu must be at least 2 characters")
    found = _resolve_onu(query)
    now = datetime.now(timezone.utc)
    if not found:
        return {"found": 0, "note": "no ONU matches that customer, site, ONU name or serial"}
    exact = [r for r in found if query.lower() in ((r[16] or "").lower(), (r[2] or "").lower())]
    if len(found) > 1 and not exact:
        return {"found": len(found), "note": "several ONUs match; ask which one",
                "matches": [_onu_row(r, now) for r in found]}
    r = exact[0] if exact else found[0]
    out = {"onu": _onu_row(r, now)}
    try:
        d = smartolt.onu_details(r[0])
        out["from_smartolt_now"] = {
            "status": d["status"], "status_since": _nairobi(d["last_status_change"]), "signal_grade": d["signal_quality"],
            "rx_dbm_at_onu": d["signal_1490_dbm"], "rx_dbm_at_olt": d["signal_1310_dbm"], "distance_m": d["distance_m"],
            "admin_state": d["administrative_status"], "onu_type": d["onu_type"], "zone": d["zone"], "splitter": d["odb"],
            "onu_mode": d["mode"], "wan_mode": d["wan_mode"], "wan_vlan": d["vlan"], "attached_vlans": d["attached_vlans"],
            "pppoe_username": d["pppoe_username"], "authorized_on": _nairobi(d["authorization_date"]),
            "last_config_resync_failed": d.get("resync_failed"),
            "ethernet_ports": [{"port": p["port"], "admin_state": p["admin_state"], "mode": p["mode"], "vlan": p["vlan"]}
                               for p in d["ethernet_ports"]],
        }
    except smartolt.SmartOLTError as e:
        out["from_smartolt_now"] = {"error": f"SmartOLT didn't answer: {e}"}
    try:
        live = smartolt.onu_live_status(r[0])
        out["from_the_olt_now"] = {"optical": live.get("optical"), "onu": live.get("onu"),
                                   "recent_ups_and_downs": (live.get("history") or [])[:5],
                                   "lan_ports": live.get("lan_ports")}
    except smartolt.SmartOLTError as e:
        out["from_the_olt_now"] = {"error": f"the OLT didn't answer: {e}"}
    return out


# ---- Shared helpers for the page-mapped tools ----

PERIODS = ("today", "yesterday", "this_week", "last_week", "this_month", "last_month")
NETWORK_ROUTER = "ccr2116"     # the one router the Network page watches


def _window(args, default="this_week"):
    """A named period -> the day/week/month the monitoring reports take.
    `day` (YYYY-MM-DD) overrides `period`."""
    today = now_eat().date()
    day = _optional_date(args, "day")
    if day is not None:
        if day > today:
            raise ToolArgumentError("day can't be in the future")
        return {"day": day.isoformat()}, day.isoformat()
    period = args.get("period") or default
    if period not in PERIODS:
        raise ToolArgumentError(f"period must be one of {', '.join(PERIODS)}")
    monday = today - timedelta(days=today.weekday())
    first_of_month = today.replace(day=1)
    window = {
        "today": {"day": today.isoformat()},
        "yesterday": {"day": (today - timedelta(days=1)).isoformat()},
        "this_week": {"week": monday.isoformat()},
        "last_week": {"week": (monday - timedelta(days=7)).isoformat()},
        "this_month": {"month": first_of_month.strftime("%Y-%m")},
        "last_month": {"month": (first_of_month - timedelta(days=1)).strftime("%Y-%m")},
    }[period]
    return window, period


def _duration(seconds):
    """3725 -> '1h 2m'. Done here so the model never does time arithmetic."""
    if seconds is None:
        return None
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m" if minutes else f"{seconds}s"


def _optional_choice(args, key, choices):
    value = args.get(key)
    if value is None:
        return None
    if value not in choices:
        raise ToolArgumentError(f"{key} must be one of {', '.join(choices)}")
    return value


def _optional_bool(args, key):
    value = args.get(key)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ToolArgumentError(f"{key} must be true or false")
    return value


def _optional_int(args, key, low, high, default):
    value = args.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ToolArgumentError(f"{key} must be a whole number from {low} to {high}")
    return value


def _matches(text, *fields):
    key = _name_key(text)
    return any(key in _name_key(f) for f in fields if f)


# ---- Network page ----

def get_network_status(args):
    """The Network page's header and link cards: every internet link (Airtel,
    Veenet), its traffic and gateway, the overall status with reasons."""
    _no_extra_keys(args, ())
    o = network.overview(NETWORK_ROUTER)
    if o is None:
        return {"error": "the network router isn't set up"}
    live, sysinfo = o["live"] or {}, (o["live"] or {}).get("system") or {}
    total = o["total"]
    return {
        "status": o["status"]["level"],
        "reasons": [r["text"] for r in o["status"]["reasons"]],
        "last_reading_at": _nairobi(o["received_at"]),
        "all_traffic_now": {"download_mbps": total["download_mbps"], "upload_mbps": total["upload_mbps"]},
        "peak_today": {"download_mbps": total["peak_today_download_mbps"],
                       "download_at": _nairobi(total.get("peak_today_download_at")),
                       "upload_mbps": total["peak_today_upload_mbps"],
                       "note": "the single highest 15-second reading today; averages are lower"},
        "links": [{
            "link": l["name"], "interface": l["interface"],
            "role": "primary" if l["primary"] else "backup",
            "carrying_traffic": l["active"], "in_service": l["in_service"],
            "still_reported_by_router": l["listed"],
            "up": l["running"], "port_speed": l["link_rate"],
            "download_mbps": l["download_mbps"], "upload_mbps": l["upload_mbps"],
            "capacity_mbps": l["capacity"]["mbps"],
            "capacity_is_estimate": l["capacity"]["estimated"],
            "download_pct_of_capacity": l["download_utilization_pct"],
            "gateway": l["gateway"]["label"], "gateway_address": l["gateway"]["address"],
            "gateway_ping_ms": l["gateway"]["rtt_ms_avg"], "gateway_loss_pct": l["gateway"]["loss_pct"],
            "peak_today_download_mbps": l["peak_today"]["download_mbps"],
            "peak_30_days_download_mbps": l["peak_30d"]["download_mbps"],
        } for l in o["links"]],
        "router": {"cpu_pct": sysinfo.get("cpu_pct"), "temperature_c": sysinfo.get("temp_c"),
                   "uptime": _duration(live.get("uptime_s")), "pppoe_online": live.get("pppoe_online"),
                   "routeros": sysinfo.get("routeros")},
        "last_outage": None if not o["last_outage"] else {
            "what": o["last_outage"]["type"], "link": o["last_outage"].get("link"),
            "started": _nairobi(o["last_outage"]["started_at"]),
            "lasted": _duration(o["last_outage"]["duration_s"]), "ongoing": o["last_outage"]["ongoing"]},
    }


def get_pppoe_clients(args):
    """The PPPoE clients connected to the CCR2116 right now, matched to the
    customer list: Active, Expired-but-connected, or not in the list."""
    _no_extra_keys(args, ("status", "search"))
    status = _optional_choice(args, "status", ("active", "expired", "unknown"))
    search = _optional_text(args, "search", 60)
    data = network.pppoe(NETWORK_ROUTER)
    if data is None:
        return {"error": "the network router isn't set up"}
    rows = [r for r in data["sessions"]
            if (not status or r["status"] == status)
            and (not search or _matches(search, r["display_name"], r["username"], r["package"]))]
    # Expired-but-connected first: that's the list someone acts on.
    order = {"expired": 0, "unknown": 1, "active": 2}
    rows.sort(key=lambda r: (order.get(r["status"], 3), (r["display_name"] or r["username"] or "").lower()))
    return {
        "list_received_at": _nairobi(data["received_at"]), "list_is_stale": data["is_stale"],
        "counts": data["counts"],
        "matching": len(rows),
        "clients": [{"name": r["display_name"], "username": r["username"], "package": r["package"],
                     "status": r["status"], "why": r["status_reason"], "expiry": _nairobi(r["expiry"]),
                     "connected_for": _duration(r["uptime_s"])} for r in rows[:MAX_ROWS * 2]],
        "note": "status 'unknown' means the username isn't in the customer list",
    }


EVENT_WORDS = {"not_reporting": "router not reporting", "gateway_down": "gateway not responding",
               "uplink_down": "link down", "router_restarted": "router restarted"}


def get_network_events(args):
    """Outages and restarts on the Network page, newest first."""
    _no_extra_keys(args, ("limit",))
    limit = _optional_int(args, "limit", 1, 30, 15)
    found = network.events(NETWORK_ROUTER, limit=limit)
    return {"events": [{
        "what": EVENT_WORDS.get(e["type"], e["type"]), "link": e.get("link"),
        "started": _nairobi(e["started_at"]), "ended": _nairobi(e["ended_at"]),
        "lasted": _duration(e["duration_s"]), "ongoing": e["ongoing"],
        "cause": {"restart": "the router restarted or lost power",
                  "connectivity": "the router stayed up but lost its way out (upstream or gateway)"}.get(e.get("cause")),
    } for e in found]}


# ---- Site monitoring (Status / Trends) ----

def get_fleet_report(args):
    """Every monitored site over a period (the Trends tab): uptime, outages,
    longest outage, flapping, people connected. Worst uptime first."""
    _no_extra_keys(args, ("period", "day"))
    window, label = _window(args)
    f = monitoring.get_fleet(False, **window)
    rows = sorted(f["sites"], key=lambda r: (r["uptime_pct"] is None, r["uptime_pct"] if r["uptime_pct"] is not None else 0))
    return {
        "period": label, "from": _nairobi(f["since"]), "to": _nairobi(f["until"]),
        "network": {"uptime_pct": f["totals"].get("uptime_pct"), "sites_with_an_outage": f["totals"].get("sites_with_outage"),
                    "sites_measured": f["totals"].get("measured_sites")},
        "sites": [{"site": r["name"], "state_now": r["state"], "uptime_pct": r["uptime_pct"],
                   "outages": r["outages"], "longest_outage": _duration(r["longest_outage_seconds"]),
                   "flaps": r["flap_count"], "avg_people": r["avg_people"], "peak_people": r["peak_people"]}
                  for r in rows[:MAX_ROWS * 2]],
        "how_to_read": "sorted by uptime, worst first; flaps are quick down-up bounces",
    }


def _monitored_site(name):
    """The one monitored site a name points to, or an error dict."""
    if not name or not _name_key(name):
        raise ToolArgumentError("site is required")
    all_sites = monitoring.get_site_statuses(False)
    key = _name_key(name)
    matches = [s for s in all_sites if key in _name_key(s["name"])]
    exact = [s for s in matches if _name_key(s["name"]) == key]
    if exact:
        matches = exact
    if not matches:
        return None, {"error": f"no monitored site matches '{name}'",
                      "known_sites": [s["name"] for s in all_sites][:MAX_ROWS * 2]}
    if len(matches) > 1:
        return None, {"error": "more than one site matches; ask which one", "candidates": [s["name"] for s in matches]}
    return matches[0], None


def get_site_history(args):
    """One site's outages, uptime and people connected over a period (the
    site's own page). Contact details are left out: get_site_contact has them."""
    _no_extra_keys(args, ("site", "period", "day"))
    site, error = _monitored_site(_optional_text(args, "site", 60))
    if error:
        return error
    window, label = _window(args)
    d = monitoring.get_site_detail(site["id"], False, **window)
    if d is None:
        return {"error": "that site has no monitoring history"}
    busiest = sorted(d.get("busiest_hours") or [], key=lambda h: -(h["avg"] or 0))[:3]
    return {
        "site": d["name"], "period": label, "from": _nairobi(d["since"]), "to": _nairobi(d["until"]),
        "state_now": d["state"], "state_since": _nairobi(d["state_since"]),
        "uptime_pct": d["uptime_pct"], "downtime": _duration(d["downtime_seconds"]),
        "outages": [{"start": _nairobi(o["start"]), "end": _nairobi(o["end"]),
                     "lasted": _duration(o["duration_seconds"]), "ongoing": o["ongoing"]}
                    for o in (d["outages"] or [])[:15]],
        "outage_count": len(d["outages"] or []), "longest_outage": _duration(d["longest_outage_seconds"]),
        "flaps": d["flap_count"], "avg_people": d["avg_people"], "peak_people": d["peak_people"],
        "unique_people": d.get("unique_people"),
        "busiest_hours": [f"{h['hour']:02d}:00" if isinstance(h["hour"], int) else str(h["hour"]) for h in busiest],
    }


def get_alert_log(args):
    """Site-down / recovered alerts the app sent (SMS, WhatsApp, in-app):
    counts by delivery status and the most recent ones. Never who they went to."""
    _no_extra_keys(args, ("days", "status", "site"))
    days = _optional_int(args, "days", 1, 90, 7)
    status = _optional_text(args, "status", 30)
    site = _optional_text(args, "site", 60)
    until = datetime.utcnow()
    data = sms_log.list_deliveries(until - timedelta(days=days), until, None, status)
    rows = [r for r in data["rows"] if not site or _matches(site, r.get("site_name"))]
    return {
        "days": days, "counts_by_status": data["counts"],
        "recent": [{"sent": _nairobi(r["sent_at"].isoformat() + "Z" if isinstance(r["sent_at"], datetime) else r["sent_at"]),
                    "site": r.get("site_name"), "alert": r["kind"], "channel": r["channel"], "status": r["status"]}
                   for r in rows[:MAX_ROWS * 2]],
        "note": "'not_configured' means that channel isn't set up, so nothing was delivered on it",
    }


# ---- Batteries ----

def get_batteries(args):
    """Every battery (the Batteries page): where it is, its status and charge,
    who moved it last; plus the movements still in progress."""
    _no_extra_keys(args, ("search",))
    search = _optional_text(args, "search", 60)
    rows = batteries.get_all_batteries()
    if search:
        rows = [b for b in rows if _matches(search, b["battery_number"], b["model"], b["current_location"])]
    counts = {}
    for b in rows:
        counts[b["status"]] = counts.get(b["status"], 0) + 1
    moving = batteries.get_active_movements()
    return {
        "counts_by_status": counts,
        "batteries": [{"battery": b["battery_number"], "model": b["model"], "capacity": b["capacity"],
                       "status": b["status"], "location": b["current_location"],
                       "site_online": b["site_online"], "charge": b["charge_status"],
                       "last_moved_by": b["moved_by"], "last_moved_at": _nairobi(b["moved_at"])}
                      for b in rows[:MAX_ROWS * 2]],
        "movements_in_progress": [{"battery": m["battery_number"], "from": m["from_location"], "to": m["to_location"],
                                   "status": m["status"], "reason": m["reason"], "started": _nairobi(m["created_at"])}
                                  for m in moving[:MAX_ROWS]],
    }


def get_battery_history(args):
    """Where one battery has been: every movement, newest first."""
    _no_extra_keys(args, ("battery",))
    wanted = _optional_text(args, "battery", 40)
    if not wanted:
        raise ToolArgumentError("battery is required (its number, e.g. BAT-001)")
    every = batteries.get_all_batteries()
    found = [b for b in every if _name_key(b["battery_number"]) == _name_key(wanted)] or \
            [b for b in every if _name_key(wanted) in _name_key(b["battery_number"])]
    if not found:
        return {"error": f"no battery matches '{wanted}'", "known": [b["battery_number"] for b in every][:MAX_ROWS * 2]}
    if len(found) > 1:
        return {"error": "more than one battery matches; ask which one", "candidates": [b["battery_number"] for b in found]}
    b = found[0]
    history = batteries.get_movement_history(b["id"])
    return {
        "battery": b["battery_number"], "now_at": b["current_location"], "status": b["status"],
        "movements": [{"when": _nairobi(h[0].isoformat() + "Z" if isinstance(h[0], datetime) else h[0]),
                       "from": h[1], "to": h[2], "reason": h[3], "moved_by": h[4]} for h in history[:MAX_ROWS]],
        "movement_count": len(history),
    }


# ---- Inventory ----

def _unit(tracking_type):
    return "metres" if tracking_type == "inventory_length" else "units"


def get_stock(args):
    """Stock levels (the Stock page and its reorder report): on hand,
    deployed, reorder level, where it is, value."""
    _no_extra_keys(args, ("search", "below_reorder_only"))
    search = _optional_text(args, "search", 60)
    low_only = _optional_bool(args, "below_reorder_only")
    summary = {(r["category_id"], r["sku_or_spec"]): r for r in inventory_reports.get_items_summary(None)}
    rows = inventory_reports.get_sku_summary(None)
    if search:
        rows = [r for r in rows if _matches(search, r["name"], r["sku_or_spec"], r["category_name"])]
    if low_only:
        rows = [r for r in rows if r["below_threshold"]]
    out = []
    for r in rows[:MAX_ROWS * 2]:
        extra = summary.get((r["category_id"], r["sku_or_spec"]), {})
        out.append({
            "item": r["name"], "sku_or_spec": r["sku_or_spec"], "category": r["category_name"],
            "unit": _unit(r["tracking_type"]),
            "on_hand": r["total_on_hand"], "deployed": extra.get("deployed_qty"),
            "reorder_level": r["reorder_level"], "below_reorder_level": r["below_threshold"],
            "where": extra.get("location_names"),
            "value": None if r["total_value"] is None else format_kes(r["total_value"]),
        })
    return {"items": out, "matching": len(rows),
            "below_reorder_level_count": sum(1 for r in rows if r["below_threshold"])}


def get_stock_movements(args):
    """The Inventory Log: stock received, issued, returned or moved, newest
    first, optionally for one item, site or person."""
    _no_extra_keys(args, ("search", "days"))
    search = _optional_text(args, "search", 60)
    days = _optional_int(args, "days", 1, 180, 30)
    since = datetime.utcnow() - timedelta(days=days)
    rows = []
    log_rows = inventory_transactions.get_transaction_log(None, None, None, 500)
    for t in log_rows:
        when = t["created_at"]
        moment = datetime.fromisoformat(when.replace("Z", "")) if isinstance(when, str) else when
        if moment is not None and moment < since:
            continue
        if search and not _matches(search, t["category_name"], t["sku_or_spec"], t["site_location_name"],
                                   t["issued_to_name"], t["to_location_name"], t["from_location_name"]):
            continue
        rows.append(t)
    newest = log_rows[0]["created_at"] if log_rows else None
    return {
        "days": days, "matching": len(rows),
        # So "nothing in the last 30 days" isn't read as "nothing ever".
        "newest_movement_ever": _nairobi(newest if isinstance(newest, str) or newest is None else newest.isoformat() + "Z"),
        "movements": [{
            "when": _nairobi(t["created_at"] if isinstance(t["created_at"], str) else t["created_at"].isoformat() + "Z"),
            "action": t["action"], "item": t["category_name"], "sku_or_spec": t["sku_or_spec"],
            "quantity_or_length": t["qty_or_length"], "from": t["from_location_name"], "to": t["to_location_name"],
            "site": t["site_location_name"], "issued_to": t["issued_to_name"], "logged_by": t["logged_by_name"],
            "status": t["status"], "length_used": t["length_used"], "length_returned": t["length_returned"],
            "notes": (t["notes"] or "")[:120] or None,
        } for t in rows[:MAX_ROWS * 2]],
    }


def get_cable_stock(args):
    """Cable reels in stock by spec, and offcuts (the Reports page)."""
    _no_extra_keys(args, ())
    return {
        "reels_by_spec": [{"spec": c["spec"], "reels_in_stock": c["reels_in_stock"],
                           "metres_remaining": c["total_length_remaining"]}
                          for c in inventory_reports.get_cable_type_summary(None)],
        "offcuts_by_spec": inventory_reports.get_offcut_summary_by_spec(None)[:MAX_ROWS],
    }


def _schema(name, description, properties=None, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": list(required),
                       "additionalProperties": False}}}


ALL_MODES = sorted({m for modes in MODES.values() for m in modes if m != "custom"})

# name -> (function, access, schema sent to the model); access: ADMIN_ONLY or any of (section, action)
TOOLS = {
    "get_revenue_summary": (get_revenue_summary, {"admin"}, _schema(
        "get_revenue_summary",
        "Hotspot sales revenue (not total company revenue). With no arguments: today so far, yesterday, "
        "this week, this month and this year, each against its usual comparison. With a period: that "
        "day/week/month/year (the one containing `date`, default today), optionally per site.",
        {"period": {"type": "string", "enum": list(MODES)},
         "date": {"type": "string", "description": "YYYY-MM-DD, Nairobi date inside the period"},
         "compare": {"type": "string", "enum": ALL_MODES,
                     "description": "Leave this out unless the user asks for a specific comparison: the "
                                    "default is the one the Revenue Report uses. "
                                    "day: same_weekday_last_week|previous_day|baseline_median; "
                                    "week: previous_week|same_week_last_year; month: previous_month|"
                                    "same_month_last_year; year: previous_year"},
         "by_site": {"type": "boolean", "description": "include each site's figures (top 20 by revenue)"}})),
    "get_revenue_history": (get_revenue_history, {"admin"}, _schema(
        "get_revenue_history",
        "Hotspot sales revenue over a date range, for the whole network or one site: revenue per day, "
        "week or month, each period's change in KSh and % on the one before, plus the total, daily "
        "average, best and worst period, first-to-last change and the trend. Use for trajectories, "
        "growth or decline over time, and comparing a site across dates.",
        {"start_date": {"type": "string", "description": "YYYY-MM-DD (Nairobi), first day of the range"},
         "end_date": {"type": "string", "description": "YYYY-MM-DD, last day; default today"},
         "site": {"type": "string", "description": "part of a site name; leave out for the whole network"},
         "group_by": {"type": "string", "enum": ["day", "week", "month"],
                      "description": f"default day (at most {MAX_DAILY_POINTS} days); use week or month for longer"}},
        required=("start_date",))),
    "get_site_performance": (get_site_performance, {"admin"}, _schema(
        "get_site_performance",
        "Every site ranked by its usual daily revenue (average of the last 14 completed days), with its "
        "14-day total, share of the network total, the network total, "
        "last 7 days vs the 7 before, yesterday vs the same weekday last week, and the Revenue Report's "
        "flag (investigate / needs attention / doing well) with the reason. Use for 'which sites make the "
        "most', 'which are doing badly', 'top earners that are slipping', comparing all sites.")),
    "get_site_status": (get_site_status, {"admin"}, _schema(
        "get_site_status",
        "Live status of the monitored hotspot SITES (locations such as ACK, Red Soil): online/offline, "
        "people connected, 30-day uptime, today's revenue per site. Not for individual PPPoE customers "
        "(usernames like JS32): use get_pppoe_clients for those. Optionally filter by part of a site name.",
        {"site": {"type": "string", "description": "part of a site name"}})),
    "get_site_contact": (get_site_contact, {"admin"}, _schema(
        "get_site_contact",
        "The contact person, phone number and address saved for a site or location (as on the Sites page). "
        "Use for 'who is the contact for X', 'who do I call at X', 'where is X'.",
        {"site": {"type": "string", "description": "the site name, or part of it"}},
        required=("site",))),
    "get_packages": (get_packages, {"admin"}, _schema(
        "get_packages", "The hotspot packages currently on sale, with price and duration.")),
    "lookup_customer": (lookup_customer, {"admin"}, _schema(
        "lookup_customer",
        "Find PPPoE customers by username, name or phone. Returns router, username, name, plan, "
        "account status, enabled, expiry. Phone numbers are not returned.",
        {"query": {"type": "string", "description": "username, name or phone (2-60 characters)"}},
        required=("query",))),

    # ---- Network page (CCR2116) ----
    "get_network_status": (get_network_status, {"admin"}, _schema(
        "get_network_status",
        "The Network page right now: overall status with reasons, all traffic (download/upload Mbps) and "
        "today's peak, and each internet link (Airtel, Veenet, ...): primary or backup, carrying traffic or "
        "standby, in service, up/down, traffic vs capacity, gateway alive/ping/loss, peaks. Also the core "
        "router's CPU, temperature, uptime, PPPoE count and the last outage. Use for bandwidth, 'is the "
        "internet ok', Airtel/Veenet, failover, gateway questions.")),
    "get_pppoe_clients": (get_pppoe_clients, {"admin"}, _schema(
        "get_pppoe_clients",
        "PPPoE clients connected to the core router RIGHT NOW, matched to the customer list: name, "
        "username, package, active / expired-but-connected / not in the list, expiry, how long connected. "
        "Usernames look like JS01, JS32. Use for 'who is online', 'who is expired but still connected', "
        "'is JS32 online / connected'.",
        {"status": {"type": "string", "enum": ["active", "expired", "unknown"]},
         "search": {"type": "string", "description": "part of a name, username or package"}})),
    "get_network_events": (get_network_events, {"admin"}, _schema(
        "get_network_events",
        "Network outages and restarts, newest first: router not reporting (with the likely cause), "
        "gateway not responding or link down (with which link), router restarted.",
        {"limit": {"type": "integer", "description": "how many, 1-30 (default 15)"}})),

    # ---- Site monitoring ----
    "get_fleet_report": (get_fleet_report, {"admin"}, _schema(
        "get_fleet_report",
        "Every monitored hotspot site over a period: uptime %, number of outages, longest outage, flaps, "
        "average and peak people connected, plus the network-wide uptime. Sorted worst uptime first. Use "
        "for 'which sites had the most downtime', 'uptime this week', 'most reliable sites'.",
        {"period": {"type": "string", "enum": list(PERIODS), "description": "default this_week"},
         "day": {"type": "string", "description": "YYYY-MM-DD, one specific day instead of a period"}})),
    "get_site_history": (get_site_history, {"admin"}, _schema(
        "get_site_history",
        "One site's monitoring over a period: state now and since when, uptime %, total downtime, each "
        "outage with start/end/length, flaps, people connected (average, peak, unique) and busiest hours.",
        {"site": {"type": "string", "description": "the site name, or part of it"},
         "period": {"type": "string", "enum": list(PERIODS), "description": "default this_week"},
         "day": {"type": "string", "description": "YYYY-MM-DD, one specific day instead of a period"}},
        required=("site",))),
    "get_alert_log": (get_alert_log, {"admin"}, _schema(
        "get_alert_log",
        "Site down / recovered alerts the app sent by SMS, WhatsApp or in-app: counts by delivery status "
        "and the recent ones (when, site, kind, channel, status). Does not show who they were sent to.",
        {"days": {"type": "integer", "description": "how far back, 1-90 (default 7)"},
         "status": {"type": "string", "description": "e.g. sent, failed, not_configured"},
         "site": {"type": "string", "description": "part of a site name"}})),

    # ---- Batteries ----
    "get_batteries": (get_batteries, {"admin"}, _schema(
        "get_batteries",
        "Every battery: number, model, capacity, status (At Base / Deployed / In Transit / Pending), "
        "current location and whether that site is online, charge status, who moved it last and when; "
        "plus battery movements still in progress.",
        {"search": {"type": "string", "description": "part of a battery number, model or location"}})),
    "get_battery_history": (get_battery_history, {"admin"}, _schema(
        "get_battery_history",
        "Where one battery has been: every movement (when, from, to, reason, who), newest first.",
        {"battery": {"type": "string", "description": "the battery number, e.g. BAT-001"}},
        required=("battery",))),

    # ---- Inventory ----
    "get_stock": (get_stock, {"admin"}, _schema(
        "get_stock",
        "Stock levels: each item's on-hand and deployed quantity (cable in metres), reorder level and "
        "whether it is below it, where it is stored, and value. Use for 'how many X do we have', 'what "
        "needs reordering', 'stock value'.",
        {"search": {"type": "string", "description": "part of an item name, SKU/spec or category"},
         "below_reorder_only": {"type": "boolean", "description": "only items below their reorder level"}})),
    "get_stock_movements": (get_stock_movements, {"admin"}, _schema(
        "get_stock_movements",
        "The inventory log: stock received, issued, returned, transferred or installed, newest first, with "
        "quantity/length, from/to, site, who it was issued to and who logged it. Use for 'what was issued "
        "to X', 'what went to site Y', 'what came in this week'.",
        {"search": {"type": "string", "description": "part of an item, SKU, site, location or person's name"},
         "days": {"type": "integer", "description": "how far back, 1-180 (default 30)"}})),
    "get_cable_stock": (get_cable_stock, {"admin"}, _schema(
        "get_cable_stock",
        "Cable reels in stock per cable spec with metres remaining, and offcuts per spec.")),

    # ---- Customers and fibre (OLT, Phase 6) ----
    "get_customer": (get_customer, {"admin"}, _schema(
        "get_customer",
        "Everything about ONE PPPoE customer: account (plan, status, expiry, expired or not), their fibre ONU "
        "(status, since when, signal grade and dBm, splitter, zone, any open LOS alert), whether they are "
        "connected right now and for how long, and recent technician visits. Use for 'tell me about JS45', "
        "'who is Mark', 'why is X offline', 'what's going on with customer Y'. Find by username, name or phone.",
        {"customer": {"type": "string", "description": "username (e.g. JS45), name or phone"}},
        required=("customer",))),
    "get_onus": (get_onus, {"admin"}, _schema(
        "get_onus",
        "The fibre ONUs on the OLT with who each belongs to (customer, hotspot site or not linked), status "
        "(Online, Offline, LOS = fibre signal lost, Power fail = power cut at the customer), signal grade and "
        "dBm, splitter and zone, plus counts by status. Use for 'who is on LOS', 'which customers are offline "
        "on fibre', 'weak signals', 'ONUs on splitter DRD'.",
        {"status": {"type": "string", "enum": list(ONU_STATES)},
         "search": {"type": "string", "description": "part of a customer, site, ONU name, serial, zone or splitter"},
         "weak_signal_only": {"type": "boolean", "description": "only ONUs SmartOLT grades Warning or Critical"}})),
    "get_onu_live": (get_onu_live, {"admin"}, _schema(
        "get_onu_live",
        "Asks the OLT (SmartOLT) RIGHT NOW about one ONU: live status, optical levels, distance, how long it "
        "has been online, last down cause and time, recent ups and downs, LAN port links, ONU and WAN mode, "
        "VLANs. Slower than get_onus; use it when someone wants live or detailed fibre information about one "
        "customer or site. Find by customer username or name, hotspot site, ONU name or serial.",
        {"onu": {"type": "string", "description": "customer username/name, hotspot site, ONU name or serial"}},
        required=("onu",))),
}

# Who may use each tool. Admins: all. Anyone else needs one of the listed
# permissions, the same one the page behind the tool asks for. Revenue:
# admins only (owner, 2026-11-11).
_STATUS = [("sites", "view_status")]
_NETWORK = [("sites", "view_network")]
ACCESS = {
    "get_revenue_summary": ADMIN_ONLY, "get_revenue_history": ADMIN_ONLY, "get_site_performance": ADMIN_ONLY,
    "get_site_status": _STATUS, "get_fleet_report": _STATUS, "get_site_history": _STATUS,
    "get_site_contact": [("sites", "view")], "get_packages": [("sites", "view")],
    "lookup_customer": [("sites", "send_broadcasts"), ("sites", "view_network")],
    "get_customer": [("sites", "send_broadcasts"), ("sites", "view_network")],
    "get_network_status": _NETWORK, "get_pppoe_clients": _NETWORK, "get_network_events": _NETWORK,
    "get_onus": _NETWORK, "get_onu_live": _NETWORK,
    "get_alert_log": [("reports", "view")],
    "get_batteries": [("batteries", "view")], "get_battery_history": [("batteries", "view")],
    "get_stock": [("inventory_items", "view")], "get_cable_stock": [("inventory_items", "view")],
    "get_stock_movements": [("inventory_transactions", "view")],
}


# How each lookup is named in the "Checked:" line under an answer.
LABELS = {
    "get_revenue_summary": "revenue summary", "get_revenue_history": "revenue history",
    "get_site_performance": "site revenue ranking", "get_site_status": "site status",
    "get_site_contact": "site contacts", "get_packages": "packages", "lookup_customer": "customer list",
    "get_network_status": "network status", "get_pppoe_clients": "PPPoE clients online",
    "get_network_events": "network events", "get_fleet_report": "site uptime report",
    "get_site_history": "site history", "get_alert_log": "alert log", "get_batteries": "batteries",
    "get_battery_history": "battery history", "get_stock": "stock levels",
    "get_stock_movements": "inventory log", "get_cable_stock": "cable stock",
    "get_customer": "customer profile", "get_onus": "OLT ONU list", "get_onu_live": "OLT (live)",
}


def label_for(name):
    return LABELS.get(name) or (name or "lookup").replace("_", " ")


def tools_for(current_user):
    """The tool list sent to the model for this person — nothing they can't use."""
    return [schema for name, (_fn, _access, schema) in TOOLS.items() if allowed(current_user, name)]


def _audit(current_user, role, tool, allowed, outcome):
    log.info("assistant tool audit at=%s user=%s role=%s tool=%s allowed=%s outcome=%s",
             datetime.now(timezone.utc).isoformat(), current_user.get("id"), role, tool, allowed, outcome)


def run_tool(name, raw_arguments, current_user):
    """Run one tool call from the model, as `current_user`. Always returns a
    JSON string for the model — an error is a result too, so the chat goes on."""
    role = "admin" if is_admin(current_user) else "staff"
    tool = TOOLS.get(name)
    if tool is None or not allowed(current_user, name):
        _audit(current_user, role, name if tool else "unknown", False, "denied")
        return json.dumps({"error": "not authorized"})
    token = _viewer.set(current_user)
    try:
        return _run(name, tool, raw_arguments, current_user, role)
    finally:
        _viewer.reset(token)


def _run(name, tool, raw_arguments, current_user, role):
    try:
        args = json.loads(raw_arguments or "{}")
        if not isinstance(args, dict):
            raise ToolArgumentError("arguments must be an object")
        result = tool[0](args)
    except (ToolArgumentError, json.JSONDecodeError) as e:
        _audit(current_user, role, name, True, "bad_arguments")
        return json.dumps({"error": f"invalid arguments: {e}"})
    except Exception:
        # Details to the log for the owner; the model only learns it failed.
        log.exception("assistant tool %s failed", name)
        _audit(current_user, role, name, True, "failed")
        return json.dumps({"error": "lookup failed"})
    text = json.dumps(result, default=str)
    if len(text) > MAX_RESULT_CHARS:
        _audit(current_user, role, name, True, "too_large")
        return json.dumps({"error": "result too large; ask for something narrower"})
    _audit(current_user, role, name, True, "ok")
    return text
