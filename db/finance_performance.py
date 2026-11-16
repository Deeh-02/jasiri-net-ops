"""Finance > Performance (Phase 7D): the monthly profit statement. Gathers
income (hotspot sales through db/finance_hotspot.py, PPPoE recharges through
db/finance_pppoe.py) and expenses
(db/finance_expenses.py) and hands them to db/finance_performance_logic.py.
Read only: nothing here writes.
"""
from datetime import date
from decimal import Decimal

from db import finance_expenses, finance_hotspot, finance_pppoe
from db import finance_performance_logic as logic
from db.finance_income_logic import add_months, month_days

TREND_MONTHS = 12
BASIS = ("Cash basis: income counts when paid, expenses when paid. Amounts are as paid, with any VAT "
         "included; how VAT and excise should be shown is still to be confirmed with the accountant.")


def _month_end(first_day):
    return date(first_day.year, first_day.month, month_days(first_day))


def _month_total(first_day, daily, tracking_start, today):
    """A segment's income for a month, None when records don't cover the
    whole month or it hasn't started."""
    if tracking_start is None or not tracking_start <= first_day <= today:
        return None
    return sum((v for d, v in daily.items() if first_day <= d <= _month_end(first_day)), Decimal("0"))


def _income(first_day, segments, today):
    """{'pppoe', 'hotspot', 'other'} for a month. segments: {name: (daily,
    tracking_start)}. Other income has no source yet."""
    income = {name: _month_total(first_day, daily, start, today) for name, (daily, start) in segments.items()}
    return dict(income, other=None)


def _pppoe_daily(first_day, last_day):
    try:
        return finance_pppoe.daily_totals(first_day, last_day)
    except finance_pppoe.PppoeNotSetUp:
        return [], None


def report(first_day, segment, today):
    previous = add_months(first_day, -1)
    trend_from = add_months(first_day, -(TREND_MONTHS - 1))
    last_day = min(_month_end(first_day), today)

    hotspot_rows, hotspot_start = finance_hotspot.daily_totals(trend_from, max(last_day, trend_from))
    pppoe_rows, pppoe_start = _pppoe_daily(trend_from, max(last_day, trend_from))
    segments = {"hotspot": ({d: t for d, t in hotspot_rows}, hotspot_start),
                "pppoe": ({d: t for d, t in pppoe_rows}, pppoe_start)}
    # The whole business: both segments added up, over the days both are
    # recorded. Before any PPPoE upload it is hotspot alone (marked so).
    daily = dict(segments["hotspot"][0])
    for d, t in segments["pppoe"][0].items():
        daily[d] = daily.get(d, Decimal("0")) + t
    tracking_start = max(hotspot_start, pppoe_start) if hotspot_start and pppoe_start else hotspot_start or pppoe_start
    expense_rows, monthly, categories = finance_expenses.performance_expenses(trend_from, previous, _month_end(first_day))

    this_expenses = [e for e in expense_rows if e["spent_on"] >= first_day.isoformat()]
    last_expenses = [e for e in expense_rows if e["spent_on"] < first_day.isoformat()]
    this = logic.month_statement(_income(first_day, segments, today), this_expenses, categories, segment)
    last = logic.month_statement(_income(previous, segments, today), last_expenses, categories, segment)

    items = {}
    for e in this_expenses:
        if e["kind"] == "operating" and (segment == "all" or e.get("segment") == segment):
            items.setdefault(e["category"], []).append(e)

    income_by_month, expense_months = {}, {}
    for day, total in daily.items():
        month = day.replace(day=1)
        income_by_month[month] = income_by_month.get(month, Decimal("0")) + total
    for month, kind, total, _count in monthly:
        expense_months.setdefault(month, {"operating": Decimal("0"), "capital": Decimal("0")})[kind] += total

    is_draft = first_day <= today <= _month_end(first_day)
    result = {
        "month": first_day, "segment": segment, "basis": BASIS,
        "is_draft": is_draft,
        "not_started": first_day > today,
        "this": this, "last": last, "previous_month": previous,
        "rows": logic.statement_rows(this, last, categories, items, is_draft=is_draft,
                                     equipment_items=[e for e in this_expenses if e["kind"] == "capital"]),
        "notes": logic.notes(this_expenses, categories),
        "pppoe_recorded_from": pppoe_start,
        "income_comparable": logic.income_comparable(this, last),
    }
    if segment == "all":
        result.update(
            break_even=logic.break_even(first_day, daily, this["expenses_total"], today),
            trend=logic.monthly_trend(first_day, TREND_MONTHS, income_by_month, expense_months, tracking_start, today),
            where=logic.where_money_went(this_expenses, last_expenses),
        )
    return result
