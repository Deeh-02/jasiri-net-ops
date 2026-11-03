"""The admin assistant's tools — the ONLY way the model can see data.

Each tool is one fixed, read-only lookup that calls the app's existing
functions (the Revenue Report, the Site Status page, Packages, the customer
list). The model picks a tool and fills in a few checked arguments; it never
writes SQL, never names a table, and cannot change anything.

WHO GETS WHAT is decided here in code, never by the prompt. Every tool lists
the roles allowed to use it. Today that's admins only — the chat endpoint
itself is admin-only too, and run_tool() checks again before running
anything, so a mistake in one layer is caught by the other.

WHAT LEAVES. DeepSeek receives every tool result, so results are trimmed to
what an answer needs: revenue is totals only (no individual sales), and the
customer lookup returns plan/status/expiry but not phone numbers. The one
phone number that does go out is a SITE's contact (get_site_contact): giving
it is that tool's whole purpose.

Every call is written to the log (time, user, role, tool, allowed or not,
outcome) — never the arguments' values or the result.
"""
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from db import customers, monitoring, revenue_config, revenue_report, sites
from db.connection import EAT, now_eat
from db.revenue_logic import MODES, compare, format_kes, slope_pct_of_mean, trend_label

log = logging.getLogger(__name__)

MAX_ROWS = 20
MAX_DAILY_POINTS = 62
MAX_HISTORY_DAYS = 366
MAX_RESULT_CHARS = 20000


class ToolArgumentError(ValueError):
    """The model sent arguments the tool doesn't accept. The message goes back
    to the model so it can correct itself."""


def role_for(current_user):
    """Maps the app's user to the assistant's role. Only the built-in admin
    is recognised; anything else gets no tools at all (deny by default)."""
    return "admin" if current_user.get("role") == "admin" else None


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

    report = revenue_report.sites_report(period, anchor, mode=compare)
    p = report["period"]
    result = {
        "scope": "hotspot sales only, attributed to a site (not total company revenue)",
        "period": p["period_label"], "compared_with": p["comparison_label"],
        "in_progress": p["is_partial"], "notes": p["notes"],
        **_money_fields(report["network"]["display"]),
    }
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
    sites = monitoring.get_site_statuses(include_revenue=True)
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
            "revenue_today_kes": s.get("revenue_today_kes"),
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


def _schema(name, description, properties=None, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": list(required),
                       "additionalProperties": False}}}


ALL_MODES = sorted({m for modes in MODES.values() for m in modes if m != "custom"})

# name -> (function, roles allowed, schema sent to the model)
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
        "Live status of the monitored hotspot sites: online/offline, people connected, 30-day uptime, "
        "today's revenue per site. Optionally filter by part of a site name.",
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
}


def tools_for(role):
    """The tool list sent to the model for this role — nothing it can't use."""
    return [schema for _fn, roles, schema in TOOLS.values() if role in roles]


def _audit(current_user, role, tool, allowed, outcome):
    log.info("assistant tool audit at=%s user=%s role=%s tool=%s allowed=%s outcome=%s",
             datetime.now(timezone.utc).isoformat(), current_user.get("id"), role, tool, allowed, outcome)


def run_tool(name, raw_arguments, current_user):
    """Run one tool call from the model, as `current_user`. Always returns a
    JSON string for the model — an error is a result too, so the chat goes on."""
    role = role_for(current_user)
    tool = TOOLS.get(name)
    if tool is None or role not in tool[1]:
        _audit(current_user, role, name if tool else "unknown", False, "denied")
        return json.dumps({"error": "not authorized"})
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
