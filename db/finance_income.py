"""Finance > Income (Phase 7C): gathers the month's income and hands it to the
rules in db/finance_income_logic.py. Income is never typed: hotspot income
comes from the router's sales, and before the router started from the
billing system's transactions export (db/finance_hotspot.py); PPPoE income
from the new billing system's recharge export, uploaded on the PPPoE view,
and before the move from the old system's payments (db/finance_pppoe.py,
owner's choices 2026-11-16). The All view adds the two together over the
days both are recorded.
"""
import csv
import io
from datetime import date, datetime
from decimal import Decimal

from db import finance_assets, finance_expenses, finance_hotspot, finance_pppoe, pppoe_recharge_logic
from db import finance_income_logic as logic
from db.connection import now_eat, to_eat

SEGMENTS = ("all", "pppoe", "hotspot")
PPPOE_NONE = ("No PPPoE recharges have been uploaded yet. Download the recharge export from the billing "
              "system and upload it above: income fills in from it.")
PPPOE_NOT_SET_UP = ("PPPoE income needs its tables: run migrations/0039_pppoe_recharges.sql on this database, "
                    "then reload.")


def _month_last(first_day):
    return date(first_day.year, first_day.month, logic.month_days(first_day))


def _pppoe(history_from, first_day, last_day):
    """The PPPoE month, or (None, reason) when there's nothing to read."""
    try:
        data = finance_pppoe.income_for_month(history_from, first_day, last_day)
    except finance_pppoe.PppoeNotSetUp:
        return None, PPPOE_NOT_SET_UP
    return (data, None) if data["first_day"] else (None, PPPOE_NONE)


def _merge(parts):
    """Adds the segments' daily, hourly, package and buyer rows together.
    Records start on the day the LAST segment started, so a day the All view
    shows has every segment in it."""
    daily, hourly, packages = {}, {}, []
    for part in parts:
        for day, total, count in part["daily"]:
            t, c = daily.get(day, (Decimal("0"), 0))
            daily[day] = (t + total, c + count)
        for hour, total, count in part["hourly"]:
            t, c = hourly.get(hour, (Decimal("0"), 0))
            hourly[hour] = (t + total, c + count)
        packages += part["packages"]
    buyers = [p["buyers"] for p in parts if p["buyers"]]
    starts = [p["first_day"] for p in parts]
    return {
        "first_day": None if None in starts else max(starts),
        "daily": sorted((d, t, c) for d, (t, c) in daily.items()),
        "hourly": sorted((h, t, c) for h, (t, c) in hourly.items()),
        "packages": sorted(packages, key=lambda p: (-p[1], p[0]))[:8],
        # A count one segment doesn't know (first_time in its first month on
        # record) leaves the sum unknown too.
        "buyers": {k: None if any(b[k] is None for b in buyers) else sum(b[k] for b in buyers)
                   for k in ("buyers", "first_time", "repeat")} if buyers else None,
    }


def _segment_total(first_day, part, today):
    if part is None:
        return None
    daily = {day: (total, count) for day, total, count in part["daily"]}
    summary = logic.month_summary(first_day, daily, today, part["first_day"])
    return summary["total"] if summary["has_data"] and not summary["records_start"] else None


def _segment_packages(first_day, part):
    """One segment's own top packages with each one's share of that segment's
    income for the month; [] when the segment has nothing recorded."""
    if part is None:
        return []
    total = sum((t for d, t, _ in part["daily"] if d >= first_day), Decimal("0"))
    return logic.package_shares(part["packages"][:5], total)


def income_report(first_day, segment, today):
    """Everything the Income page shows above the transactions table."""
    last_day = min(_month_last(first_day), today)
    history_from = logic.add_months(first_day, -12)
    if last_day < first_day:
        return {"segment": segment, "has_data": False, "reason": "This month hasn't started yet."}

    hotspot = finance_hotspot.income_for_month(history_from, first_day, last_day) if segment != "pppoe" else None
    pppoe, pppoe_reason = _pppoe(history_from, first_day, last_day) if segment != "hotspot" else (None, None)
    if segment == "pppoe" and pppoe is None:
        return {"segment": segment, "has_data": False, "reason": pppoe_reason}
    if segment == "hotspot" or (segment == "all" and pppoe is None):
        data = hotspot
    elif segment == "pppoe":
        data = pppoe
    else:
        data = _merge([hotspot, pppoe])
    tracking_start = data["first_day"]
    daily = {day: (total, sales) for day, total, sales in data["daily"]}

    summary = logic.month_summary(first_day, daily, today, tracking_start)
    report = {"segment": segment, "has_data": summary["has_data"], "records_since": tracking_start,
              "sources": logic.source_notes(first_day, last_day, hotspot and hotspot.get("sources"),
                                            pppoe and pppoe["first_day"], pppoe_recharge_logic.MOVE_DAY)}
    if pppoe:
        report["pppoe"] = {"last_upload": pppoe["last_upload"], "latest_recharge": pppoe["latest_recharge"]}
    if not summary["has_data"]:
        what = {"pppoe": "PPPoE recharges", "hotspot": "hotspot sales"}.get(segment, "income")
        report["reason"] = (f"No income records for this month: {what} are recorded from {tracking_start:%-d %b %Y}."
                            if tracking_start else "No hotspot sales have been recorded yet.")
        return report

    report.update({
        "summary": summary,
        "vs_last_month": logic.vs_last_month(first_day, daily, today, tracking_start),
        "trend": {
            "day": logic.day_trend(first_day, daily, today, tracking_start),
            "week": logic.week_trend(first_day, daily, today, tracking_start),
            "month": logic.month_trend(first_day, daily, today, tracking_start),
        },
        "weekdays": logic.weekday_pattern(first_day, daily, today, tracking_start),
        "hours": logic.hour_pattern(data["hourly"]),
        "packages": logic.package_shares(data["packages"], summary["total"]),
        "buyers": data["buyers"],
    })
    report["target"] = _target(first_day, segment, summary, report["vs_last_month"], daily, today, tracking_start)
    now = now_eat().replace(tzinfo=None)
    report["attention"] = []
    if hotspot:
        hotspot_daily = {day: (total, sales) for day, total, sales in hotspot["daily"]}
        report["attention"] += logic.attention(
            first_day, hotspot_daily, today, hotspot["first_day"], now,
            to_eat(hotspot["last_sale"]).replace(tzinfo=None) if hotspot["last_sale"] else None,
            hotspot["unpriced"], hotspot["unlinked"])
    if pppoe:
        report["attention"] += pppoe_recharge_logic.attention(
            pppoe["not_used"], pppoe["held"], finance_pppoe.eat(pppoe["last_upload"]),
            finance_pppoe.eat(pppoe["latest_recharge"]), now)
    if segment == "all":
        report["segment_packages"] = {name: _segment_packages(first_day, part)
                                      for name, part in (("hotspot", hotspot), ("pppoe", pppoe))}
        report["split"] = logic.segment_split(_segment_total(first_day, hotspot, today),
                                              _segment_total(first_day, pppoe, today))
    return report


def _target(first_day, segment, summary, vs, daily, today, tracking_start):
    """The month target bar. Break-even is the whole business's costs, so it
    only applies to the All view. The month-end estimate projects the usual
    weekday, which fits daily hotspot sales but not monthly PPPoE renewals,
    so the PPPoE view has none."""
    previous = logic.add_months(first_day, -1)
    last_month = sum((t for d, (t, _) in daily.items() if previous <= d < first_day), Decimal("0")) \
        if tracking_start and tracking_start <= previous else None
    break_even = None
    if segment == "all":
        try:
            break_even = logic.break_even_line(finance_expenses.recent_monthly_expenses(first_day))
        except finance_expenses.FinanceNotSetUp:
            break_even = None
    projected = logic.projection(first_day, daily, today, tracking_start) if segment != "pppoe" else None
    manual = finance_assets.get_target(first_day) if segment == "all" else None
    return logic.month_target(summary["total"], break_even, last_month, projected, manual=manual)


def _shown(row):
    return dict(row, account=logic.mask_account(row["account"]), segment="hotspot")


def _pppoe_rows(first_day, last_day, limit, **filters):
    try:
        return finance_pppoe.recharges(first_day, last_day, limit=limit, **filters)
    except finance_pppoe.PppoeNotSetUp:
        return {"rows": [], "matching": 0, "matching_total": Decimal("0")}


def _rows(first_day, segment, today, search, day, package, limit, offset=0):
    """Matching income rows, newest first. The All view merges the two
    segments: each gives its newest offset + limit rows, and the page is cut
    from the merged list."""
    last_day = min(_month_last(first_day), today)
    if last_day < first_day:
        return {"rows": [], "matching": 0, "matching_total": Decimal("0")}
    filters = {"search": search, "day": day, "package": package}
    wanted = None if limit is None else offset + limit
    parts = []
    if segment != "pppoe":
        found = finance_hotspot.sales(first_day, last_day, limit=wanted, **filters)
        parts.append(dict(found, rows=[_shown(r) for r in found["rows"]]))
    if segment != "hotspot":
        parts.append(_pppoe_rows(first_day, last_day, wanted, **filters))
    rows = sorted((r for p in parts for r in p["rows"]), key=lambda r: r["at"], reverse=True)
    for r in rows:
        r.pop("sort_at", None)
    return {"rows": rows if limit is None else rows[offset:offset + limit],
            "matching": sum(p["matching"] for p in parts),
            "matching_total": sum((p["matching_total"] for p in parts), Decimal("0"))}


def transactions(first_day, segment, today, search=None, day=None, package=None, page=1, per_page=50):
    """One page of the month's income rows, newest first."""
    found = _rows(first_day, segment, today, search, day, package, per_page, (page - 1) * per_page)
    return dict(found, has_data=True, page=page, per_page=per_page)


def transactions_csv(first_day, segment, today, search=None, day=None, package=None):
    """Every matching row as CSV for the accountant. Times are EAT; hotspot
    accounts are masked like on screen."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Date (EAT)", "Time (EAT)", "Segment", "Account", "Package", "Site / how paid", "Amount (KSh)", "Type"])
    for row in _rows(first_day, segment, today, search, day, package, None)["rows"]:
        at = to_eat(datetime.fromisoformat(row["at"].rstrip("Z")))
        pppoe = row["segment"] == "pppoe"
        writer.writerow([f"{at:%Y-%m-%d}", f"{at:%H:%M}", "PPPoE" if pppoe else "Hotspot", row["account"],
                         row["package"], row["site"] or "Not linked to a site", f"{row['amount']:.2f}",
                         "Renewal" if row["kind"] == "renewal" else ("First recharge" if pppoe else "New account")])
    return out.getvalue()
