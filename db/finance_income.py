"""Finance > Income (Phase 7C): gathers the month's income and hands it to the
rules in db/finance_income_logic.py. Income is never typed: hotspot income
comes from the router's sales (read through db/monitoring.py, which owns
revenue_events). PPPoE income has no source yet (owner, 2026-11-15: being
worked out), so the PPPoE view says so instead of showing zeros.
"""
import csv
import io
from datetime import date, datetime
from decimal import Decimal

from db import finance_expenses, monitoring
from db import finance_income_logic as logic
from db.connection import now_eat, to_eat

SEGMENTS = ("all", "pppoe", "hotspot")
PPPOE_NOT_YET = ("PPPoE payments aren't recorded in the app yet, so there is no PPPoE income to show. "
                 "This fills in once their source is decided.")


def _month_last(first_day):
    return date(first_day.year, first_day.month, logic.month_days(first_day))


def income_report(first_day, segment, today):
    """Everything the Income page shows above the transactions table."""
    if segment == "pppoe":
        return {"segment": segment, "has_data": False, "reason": PPPOE_NOT_YET}
    last_day = min(_month_last(first_day), today)
    if last_day < first_day:
        tracking_start = None
        data = {"daily": [], "hourly": [], "packages": [], "buyers": None, "unpriced": [], "unlinked": (0, 0), "last_sale": None}
    else:
        data = monitoring.hotspot_income_for_month(logic.add_months(first_day, -12), first_day, last_day)
        tracking_start = data["first_day"]
    daily = {day: (total, sales) for day, total, sales in data["daily"]}

    summary = logic.month_summary(first_day, daily, today, tracking_start)
    report = {"segment": segment, "has_data": summary["has_data"], "records_since": tracking_start}
    if not summary["has_data"]:
        if last_day < first_day:
            report["reason"] = "This month hasn't started yet."
        elif tracking_start:
            report["reason"] = f"No income records for this month: hotspot sales are recorded from {tracking_start:%-d %b %Y}."
        else:
            report["reason"] = "No hotspot sales have been recorded yet."
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
    report["attention"] = logic.attention(
        first_day, daily, today, tracking_start, now_eat().replace(tzinfo=None),
        to_eat(data["last_sale"]).replace(tzinfo=None) if data["last_sale"] else None,
        data["unpriced"], data["unlinked"])
    if segment == "all":
        report["split"] = logic.segment_split(summary["total"])
    return report


def _target(first_day, segment, summary, vs, daily, today, tracking_start):
    """The month target bar. Break-even is the whole business's costs, so it
    only applies to the All view; the hotspot projection applies to both."""
    previous = logic.add_months(first_day, -1)
    last_month = sum((t for d, (t, _) in daily.items() if previous <= d < first_day), Decimal("0")) \
        if tracking_start and tracking_start <= previous else None
    break_even = None
    if segment == "all":
        try:
            break_even = logic.break_even_line(finance_expenses.recent_monthly_expenses(first_day))
        except finance_expenses.FinanceNotSetUp:
            break_even = None
    projected = logic.projection(first_day, daily, today, tracking_start)
    return logic.month_target(summary["total"], break_even, last_month, projected)


def _shown(row):
    return dict(row, account=logic.mask_account(row["account"]))


def transactions(first_day, segment, today, search=None, day=None, package=None, page=1, per_page=50):
    """One page of the month's income rows, newest first."""
    if segment == "pppoe":
        return {"has_data": False, "reason": PPPOE_NOT_YET, "rows": [], "matching": 0, "matching_total": 0}
    last_day = min(_month_last(first_day), today)
    if last_day < first_day:
        return {"has_data": True, "rows": [], "matching": 0, "matching_total": 0}
    found = monitoring.hotspot_sales(first_day, last_day, search=search, day=day, package=package,
                                     limit=per_page, offset=(page - 1) * per_page)
    return {"has_data": True, "rows": [_shown(r) for r in found["rows"]], "matching": found["matching"],
            "matching_total": found["matching_total"], "page": page, "per_page": per_page}


def transactions_csv(first_day, segment, today, search=None, day=None, package=None):
    """Every matching row as CSV for the accountant. Times are EAT; accounts
    are masked like on screen."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Date (EAT)", "Time (EAT)", "Segment", "Account", "Package", "Site", "Amount (KSh)", "Type"])
    if segment != "pppoe":
        last_day = min(_month_last(first_day), today)
        if last_day >= first_day:
            for row in monitoring.hotspot_sales(first_day, last_day, search=search, day=day, package=package, limit=None)["rows"]:
                at = to_eat(datetime.fromisoformat(row["at"].rstrip("Z")))
                writer.writerow([f"{at:%Y-%m-%d}", f"{at:%H:%M}", "Hotspot", logic.mask_account(row["account"]),
                                 row["package"], row["site"] or "Not linked to a site", f"{row['amount']:.2f}",
                                 "Renewal" if row["kind"] == "renewal" else "New account"])
    return out.getvalue()
