"""Finance > Performance rules (Phase 7D): the monthly profit statement, as
pure functions (no clock, no database). Tested in
tests/test_finance_performance.py.

Basis (stated on the page): CASH. Income counts when paid, expenses when
paid. Capital purchases (equipment kept) are left out of profit and listed
in the notes; they reach profit later as depreciation, from Assets (7F).

Missing is never zero (spec principle 4):
  - PPPoE and other income have no source yet: their lines are None, and
    every total that needs them is marked incomplete.
  - A month with NO expenses recorded is treated as "expenses not entered",
    not as a free month: its expense lines and its profit are None. A
    month with some expenses is shown, with how many it rests on.
"""
from decimal import Decimal

from db.finance_income_logic import add_months, month_days

ZERO = Decimal("0")
INCOME_LINES = (("pppoe", "PPPoE"), ("hotspot", "Hotspot"), ("other", "Installation & equipment"))


def _sum(values):
    """Sum of the known values, and whether any was unknown."""
    known = [v for v in values if v is not None]
    return (sum(known, ZERO) if known else None), len(known) != len(values)


def _margin(part, income):
    if part is None or not income:
        return None
    return (part / income * 100).quantize(Decimal("0.1"))


def _operating(expenses):
    return [e for e in expenses if e["kind"] == "operating"]


def month_statement(income, expenses, categories, segment="all", depreciation=None):
    """One month's statement. income: {'pppoe','hotspot','other'} -> amount
    or None. expenses: that month's live expense rows (both kinds).
    segment 'all' gives the whole business down to net profit; 'pppoe' or
    'hotspot' gives that segment's income and the direct costs tagged with
    it, ending before the shared costs (no segment profit is invented)."""
    recorded = bool(expenses)
    operating = _operating(expenses)
    if segment != "all":
        operating = [e for e in operating if e.get("segment") == segment]

    def category_total(category):
        if not recorded:
            return None
        return sum((Decimal(e["amount"]) for e in operating if e["category"] == category["name"]), ZERO)

    lines = {}
    wanted_income = INCOME_LINES if segment == "all" else [(k, n) for k, n in INCOME_LINES if k == segment]
    for key, _ in wanted_income:
        lines[f"income.{key}"] = income.get(key)
    income_total, income_incomplete = _sum([income.get(k) for k, _ in wanted_income])

    groups = {"direct_cost": [], "operating": [], "finance_tax": [], "other": []}
    for category in categories:
        groups[category["group"]].append(category)

    def group_total(names):
        values = [category_total(c) for c in names]
        for c, v in zip(names, values):
            lines[f"cost.{c['id']}"] = v
        return sum(values, ZERO) if recorded else None

    direct_total = group_total(groups["direct_cost"])
    result = {
        "segment": segment, "lines": lines, "expenses_recorded": len(expenses),
        "income_total": income_total, "income_incomplete": income_incomplete,
        "direct_total": direct_total,
    }
    gross = None if income_total is None or direct_total is None else income_total - direct_total
    result.update(gross_profit=gross, gross_margin=_margin(gross, income_total))
    if segment != "all":
        # A segment stops here: upstream, salaries, rent are shared.
        result["before_shared_costs"] = gross
        return result

    operating_total = group_total(groups["operating"] + groups["other"])
    charges_total = group_total(groups["finance_tax"])
    # Depreciation comes from Assets: None when no asset has the figures to
    # be depreciated yet (it is then left out, and the page says so).
    lines["depreciation"] = depreciation
    finance_total = None if charges_total is None else charges_total + (depreciation or ZERO)
    operating_profit = None if gross is None or operating_total is None else gross - operating_total
    net = None if operating_profit is None or finance_total is None else operating_profit - finance_total
    result.update(
        operating_total=operating_total, operating_profit=operating_profit,
        operating_margin=_margin(operating_profit, income_total),
        finance_total=finance_total, net_profit=net, net_margin=_margin(net, income_total),
        expenses_total=None if not recorded else sum((Decimal(e["amount"]) for e in operating), ZERO),
    )
    return result


def change(this, last):
    """{'amount', 'pct'} between two known values; None when either is
    unknown. pct is None when last month was 0."""
    if this is None or last is None:
        return None
    pct = None if last == 0 else ((this - last) / abs(last) * 100).quantize(Decimal("0.1"))
    return {"amount": this - last, "pct": pct}


def statement_rows(this, last, categories, items_by_category, is_draft=False,
                   depreciation_note="Straight-line, from Assets"):
    """The statement as display rows, each with this month, last month and
    the change. Expense lines carry their own expenses (drill-down); an
    expense line is shown when either month has money on it. While the month
    is still running (draft) there is no change column: half a month against
    a whole one would read as a collapse."""
    rows = []

    def compare(a, b):
        return None if is_draft else change(a, b)

    def line(key, label, kind="line", **extra):
        a, b = this["lines"].get(key), last["lines"].get(key)
        rows.append(dict({"type": kind, "key": key, "label": label, "this": a, "last": b, "change": compare(a, b)}, **extra))

    def total(label, a, b, kind="total", **extra):
        rows.append(dict({"type": kind, "label": label, "this": a, "last": b, "change": compare(a, b)}, **extra))

    def section(label):
        rows.append({"type": "section", "label": label})

    segment = this["segment"]
    section("Income")
    for key, name in INCOME_LINES:
        if f"income.{key}" in this["lines"]:
            line(f"income.{key}", name, link=key)
    total("Total income", this["income_total"], last["income_total"], incomplete=this["income_incomplete"])

    def cost_lines(group_names):
        for category in categories:
            if category["group"] not in group_names:
                continue
            key = f"cost.{category['id']}"
            a, b = this["lines"].get(key), last["lines"].get(key)
            if not a and not b and category["group"] != "direct_cost":
                continue
            line(key, category["name"], items=items_by_category.get(category["name"], []))

    section("Direct costs")
    cost_lines({"direct_cost"})
    total("Total direct costs", this["direct_total"], last["direct_total"])
    if segment != "all":
        total("Before shared costs", this["before_shared_costs"], last["before_shared_costs"], kind="result",
              margin=this["gross_margin"], incomplete=this["income_incomplete"])
        return rows
    total("Gross profit", this["gross_profit"], last["gross_profit"], kind="result",
          margin=this["gross_margin"], incomplete=this["income_incomplete"])

    section("Operating expenses")
    cost_lines({"operating", "other"})
    total("Total operating expenses", this["operating_total"], last["operating_total"])
    total("Operating profit", this["operating_profit"], last["operating_profit"], kind="result",
          margin=this["operating_margin"], incomplete=this["income_incomplete"])

    section("Depreciation, taxes and charges")
    line("depreciation", "Depreciation", note=depreciation_note)
    cost_lines({"finance_tax"})
    total("Total", this["finance_total"], last["finance_total"])
    total("Net profit", this["net_profit"], last["net_profit"], kind="result", margin=this["net_margin"],
          incomplete=this["income_incomplete"])
    return rows


def break_even(first_day, daily_income, costs_total, today):
    """The day this month's income passed this month's costs (operating
    expenses so far, capital left out). {'day': date} once reached,
    {'to_go': amount} if not yet, None when no costs are recorded."""
    if not costs_total:
        return None
    running = ZERO
    for n in range(1, month_days(first_day) + 1):
        day = first_day.replace(day=n)
        if day > today:
            break
        running += daily_income.get(day, ZERO)
        if running >= costs_total:
            return {"day": day}
    return {"to_go": costs_total - running}


def monthly_trend(first_day, months, income_by_month, expense_months, tracking_start, today):
    """Income, operating expenses and profit for the last `months` months up
    to the chosen one. Income is None for a month records don't fully cover;
    expenses (and so profit) are None for a month with no expenses
    recorded."""
    points = []
    for back in range(months - 1, -1, -1):
        start = add_months(first_day, -back)
        if start > today:
            continue
        end = start.replace(day=month_days(start))
        covered = tracking_start is not None and tracking_start <= start
        income = income_by_month.get(start, ZERO) if covered else None
        recorded = expense_months.get(start)
        expenses = recorded["operating"] if recorded else None
        points.append({
            "month": start,
            "income": income,
            "expenses": expenses,
            "capital": recorded["capital"] if recorded else None,
            "profit": None if income is None or expenses is None else income - expenses,
            "partial": start <= today <= end,
        })
    return points


def where_money_went(this_expenses, last_expenses):
    """This month's operating expenses by category, largest first, with the
    change from last month (None when last month had none recorded)."""
    def by_category(rows):
        out = {}
        for e in _operating(rows):
            out[e["category"]] = out.get(e["category"], ZERO) + Decimal(e["amount"])
        return out
    now, before = by_category(this_expenses), by_category(last_expenses)
    total = sum(now.values(), ZERO)
    rows = [{"category": name, "total": value,
             "share_pct": (value / total * 100).quantize(Decimal("0.1")) if total else None,
             "change": change(value, before.get(name, ZERO)) if last_expenses else None}
            for name, value in now.items()]
    rows.sort(key=lambda r: (-r["total"], r["category"]))
    movers = [r for r in rows if r["change"] and r["change"]["amount"]]
    movers.sort(key=lambda r: -abs(r["change"]["amount"]))
    return {"categories": rows, "biggest_changes": movers[:3]}


def notes(this_expenses, categories):
    """What the statement leaves out or may be missing: capital purchases
    (not in profit), expenses still in 'Other', and direct-cost categories
    with nothing recorded although the month has expenses (an ISP month
    without bandwidth is more likely unrecorded than free)."""
    capital = [e for e in this_expenses if e["kind"] == "capital"]
    other = [e for e in this_expenses if e["kind"] == "operating" and e["group"] == "other"]
    spent_on = {e["category"] for e in this_expenses if e["kind"] == "operating"}
    missing = [c["name"] for c in categories
               if c["group"] == "direct_cost" and c.get("is_active", True) and c["name"] not in spent_on] if this_expenses else []
    return {
        "capital": {"rows": capital, "total": sum((Decimal(e["amount"]) for e in capital), ZERO)},
        "uncategorised": {"count": len(other), "total": sum((Decimal(e["amount"]) for e in other), ZERO)},
        "no_direct_costs": missing,
    }
