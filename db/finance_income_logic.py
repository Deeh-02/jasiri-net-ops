"""Finance > Income rules (Phase 7C), as pure functions of their arguments:
no clock, no database. Tested in tests/test_finance_income.py.

Inputs are daily totals {day: (total, sales)} and "today" (EAT). Two rules
run through everything:
  - A day before records began is NO DATA, never zero (spec principle 4).
    `tracking_start` is the first day with any record.
  - Today is still running: it is shown, but never counted in an average or
    a comparison, so a half day can't read as a collapse.
"""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

ZERO = Decimal("0")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def month_days(first_day):
    return monthrange(first_day.year, first_day.month)[1]


def add_months(first_day, months):
    """First day of the month `months` away (negative goes back)."""
    index = first_day.year * 12 + first_day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def has_data(day, tracking_start, today):
    return tracking_start is not None and tracking_start <= day <= today


def _total(daily, days):
    return sum((daily.get(d, (ZERO, 0))[0] for d in days), ZERO)


def _days(first, last):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def month_summary(first_day, daily, today, tracking_start):
    """The KPI cards for one month. Totals cover days with data up to today;
    the average and best day use completed days only."""
    last_day = date(first_day.year, first_day.month, month_days(first_day))
    shown = [d for d in _days(first_day, min(last_day, today)) if has_data(d, tracking_start, today)]
    if not shown:
        return {"has_data": False}
    completed = [d for d in shown if d < today]
    total = _total(daily, shown)
    sales = sum(daily.get(d, (ZERO, 0))[1] for d in shown)
    best = max(completed, key=lambda d: (daily.get(d, (ZERO, 0))[0], d), default=None)
    is_current = first_day <= today <= last_day
    return {
        "has_data": True,
        "total": total,
        "sales": sales,
        "days_with_data": len(shown),
        "completed_days": len(completed),
        "average_per_day": (_total(daily, completed) / len(completed)).quantize(Decimal("1")) if completed else None,
        "best_day": {"day": best, "total": daily.get(best, (ZERO, 0))[0]} if best else None,
        "today": daily.get(today, (ZERO, 0))[0] if is_current else None,
        "is_current_month": is_current,
        # Records began part-way through this month: say so beside the total.
        "records_start": tracking_start if shown[0] == tracking_start and tracking_start > first_day else None,
    }


def vs_last_month(first_day, daily, today, tracking_start):
    """This month against last month, like for like. A finished month is
    compared whole; the current one compares its completed days (1st to
    yesterday) with the same days of last month. None when either side has
    a day without data, so a partial month never looks like a drop."""
    last_day = date(first_day.year, first_day.month, month_days(first_day))
    previous = add_months(first_day, -1)
    previous_last = date(previous.year, previous.month, month_days(previous))
    if today > last_day:
        this_days, day_count = list(_days(first_day, last_day)), None
    else:
        day_count = (today - first_day).days
        if day_count == 0:
            return None
        this_days = list(_days(first_day, today - timedelta(days=1)))
    previous_days = [d for d in _days(previous, previous_last)][: len(this_days) if day_count else None]
    if not all(has_data(d, tracking_start, today) for d in this_days + previous_days):
        return None
    this_total, previous_total = _total(daily, this_days), _total(daily, previous_days)
    change = None if previous_total == 0 else ((this_total - previous_total) / previous_total * 100).quantize(Decimal("0.1"))
    return {"this": this_total, "previous": previous_total, "change_pct": change,
            "days": day_count, "previous_month": previous}


def day_trend(first_day, daily, today, tracking_start):
    """One point per day of the month: its total (None = no data, or not yet)
    and the same day-of-month last month (None when last month had no such
    day or no data for it)."""
    previous = add_months(first_day, -1)
    points = []
    for n in range(1, month_days(first_day) + 1):
        day = date(first_day.year, first_day.month, n)
        before = date(previous.year, previous.month, n) if n <= month_days(previous) else None
        points.append({
            "day": day,
            "value": daily.get(day, (ZERO, 0))[0] if has_data(day, tracking_start, today) else None,
            "last_month": daily.get(before, (ZERO, 0))[0] if before and has_data(before, tracking_start, today) else None,
            "partial": day == today,
        })
    return points


def week_trend(first_day, daily, today, tracking_start, weeks=12):
    """The last `weeks` Monday-to-Sunday weeks up to the end of the month (or
    today). A week with any day before records began is left out; the week
    still running is marked partial."""
    last_day = min(date(first_day.year, first_day.month, month_days(first_day)), today)
    newest_monday = last_day - timedelta(days=last_day.weekday())
    points = []
    for back in range(weeks - 1, -1, -1):
        monday = newest_monday - timedelta(weeks=back)
        days = list(_days(monday, monday + timedelta(days=6)))
        known = [d for d in days if has_data(d, tracking_start, today)]
        complete_history = tracking_start is not None and monday >= tracking_start
        points.append({
            "week_start": monday,
            "value": _total(daily, known) if known and complete_history else None,
            "partial": today <= days[-1],
        })
    return points


def month_trend(first_day, daily, today, tracking_start, months=12):
    """The last `months` calendar months up to the chosen one. A month that
    began before records did is left out (None); the running month is
    marked partial."""
    points = []
    for back in range(months - 1, -1, -1):
        start = add_months(first_day, -back)
        end = date(start.year, start.month, month_days(start))
        if start > today:
            continue
        known = [d for d in _days(start, min(end, today)) if has_data(d, tracking_start, today)]
        complete_history = tracking_start is not None and start >= tracking_start
        points.append({"month": start, "value": _total(daily, known) if known and complete_history else None,
                       "partial": start <= today <= end})
    return points


def weekday_pattern(first_day, daily, today, tracking_start):
    """Average income per weekday over the month's completed days, with how
    many days each average is based on (small samples are shown as such)."""
    last_day = date(first_day.year, first_day.month, month_days(first_day))
    buckets = {n: [] for n in range(7)}
    for day in _days(first_day, min(last_day, today - timedelta(days=1))):
        if has_data(day, tracking_start, today):
            buckets[day.weekday()].append(daily.get(day, (ZERO, 0))[0])
    return [{"weekday": WEEKDAYS[n], "average": (sum(v, ZERO) / len(v)).quantize(Decimal("1")) if v else None,
             "days": len(v)} for n, v in buckets.items()]


def hour_pattern(hourly_rows):
    """Total per hour of the day, all 24 hours present (an hour with no sales
    is a real zero once the month has data)."""
    by_hour = {hour: (total, sales) for hour, total, sales in hourly_rows}
    return [{"hour": h, "total": by_hour.get(h, (ZERO, 0))[0], "sales": by_hour.get(h, (ZERO, 0))[1]} for h in range(24)]


def package_shares(package_rows, month_total):
    return [{"package": name, "total": total, "sales": sales,
             "share_pct": (total / month_total * 100).quantize(Decimal("0.1")) if month_total else None}
            for name, total, sales in package_rows]


def segment_split(hotspot_total):
    """The thin split bar on the All view. Only hotspot income is recorded so
    far: PPPoE and other income are None (no data), not zero."""
    return {"pppoe": None, "hotspot": hotspot_total, "other": None}


def mask_account(account):
    """Hotspot accounts are a phone number plus a device tag
    ('254796130050-F:D2'). On screen and in exports the middle of the number
    is hidden: '2547****0050-F:D2'."""
    if not account:
        return account
    head, sep, tail = account.partition("-")
    if len(head) >= 9 and head.isdigit():
        head = head[:4] + "****" + head[-4:]
    return head + sep + tail


# ---- Month target (Phase 7E) ----

def projection(first_day, daily, today, tracking_start, weeks=6, min_samples=3):
    """Where the running month should land: completed days as they are, plus
    each remaining day (today included) at the average of the same weekday
    over the last `weeks` weeks. Today counts at whichever is higher, its
    sales so far or its usual day. Hotspot is prepaid, so there is no
    "expected" figure, only this projection (spec section 4). None for a
    finished month, or when any weekday has fewer than `min_samples` past
    days to average."""
    last_day = date(first_day.year, first_day.month, month_days(first_day))
    if not first_day <= today <= last_day:
        return None
    usual = {}
    for weekday in range(7):
        samples = [daily.get(d, (ZERO, 0))[0] for d in _days(today - timedelta(weeks=weeks), today - timedelta(days=1))
                   if d.weekday() == weekday and has_data(d, tracking_start, today)]
        if len(samples) < min_samples:
            return None
        usual[weekday] = sum(samples, ZERO) / len(samples)
    completed = _total(daily, [d for d in _days(first_day, today - timedelta(days=1)) if has_data(d, tracking_start, today)])
    today_value = max(daily.get(today, (ZERO, 0))[0], usual[today.weekday()])
    rest = sum((usual[d.weekday()] for d in _days(today + timedelta(days=1), last_day)), ZERO)
    return {"projected": (completed + today_value + rest).quantize(Decimal("1")), "weeks": weeks}


def break_even_line(recent_expenses):
    """Average monthly operating expenses over the recent months that have
    expenses recorded: below this the business loses money. None if none
    are recorded."""
    if not recent_expenses:
        return None
    total = sum((amount for _, amount in recent_expenses), ZERO)
    return {"amount": (total / len(recent_expenses)).quantize(Decimal("1")), "months": len(recent_expenses)}


def month_target(income, break_even, last_month, projected, manual=None):
    """The progress bar: the target is the owner's own figure when set,
    otherwise break-even (spec section 4). Every other line is a marker."""
    if manual is not None:
        target, source = manual, "manual"
    elif break_even is not None:
        target, source = break_even["amount"], "break_even"
    else:
        target, source = None, None
    return {"income": income, "target": target, "target_source": source,
            "progress_pct": (income / target * 100).quantize(Decimal("1")) if target else None,
            "break_even": break_even, "last_month": last_month, "projected": projected}


# ---- Needs attention (Phase 7E) ----

def attention(first_day, daily, today, tracking_start, now_eat, last_sale_eat, unpriced, unlinked,
              quiet_hours=3, low_share=Decimal("0.6"), weeks=4, limit=5):
    """What on the Income page needs someone to look, worst first:
      - a package selling while unpriced (money booked as KSh 0)
      - no paid sale for `quiet_hours` hours during the day (outage or sync)
      - a completed day with no sales at all
      - a completed day below `low_share` of a usual same weekday (the
        average of up to `weeks` earlier same weekdays; needs 3 to judge)
      - sales not linked to a site (counted here, missing from Revenue)
    Each item: level ('bad' | 'warn' | 'info'), title, detail, and day when
    it points at one day's transactions."""
    items = []
    for package, sales in unpriced:
        items.append({"level": "bad", "title": f"“{package}” is selling but has no price",
                      "detail": f"{sales} sale{'s' if sales != 1 else ''} this month counted as KSh 0. Set its price under "
                                "Hotspot Status → Manage sites → Packages."})

    last_day = date(first_day.year, first_day.month, month_days(first_day))
    if first_day <= today <= last_day and last_sale_eat and 7 <= now_eat.hour < 22:
        quiet = now_eat - last_sale_eat
        if quiet >= timedelta(hours=quiet_hours):
            hours = int(quiet.total_seconds() // 3600)
            items.append({"level": "bad", "title": f"No hotspot sale for {hours} hours",
                          "detail": f"The last paid sale was at {last_sale_eat:%H:%M}. Possible outage or a stopped "
                                    "router feed: check Hotspot Status."})

    day_items = []
    for day in _days(first_day, min(last_day, today - timedelta(days=1))):
        if not has_data(day, tracking_start, today):
            continue
        value = daily.get(day, (ZERO, 0))[0]
        if value == 0:
            day_items.append({"level": "bad", "day": day, "title": f"No hotspot sales on {day:%a %-d %b}",
                              "detail": "A whole day without a paid sale: likely an outage or a stopped feed."})
            continue
        before = [daily.get(day - timedelta(weeks=n), (ZERO, 0))[0] for n in range(1, weeks + 1)
                  if has_data(day - timedelta(weeks=n), tracking_start, today)]
        if len(before) < 3:
            continue
        usual = sum(before, ZERO) / len(before)
        if usual and value < usual * low_share:
            drop = int(((usual - value) / usual * 100).quantize(Decimal("1")))
            day_items.append({"level": "warn", "day": day, "title": f"{day:%a %-d %b} was {drop}% below a usual {day:%A}",
                              "detail": f"KSh {value:,.0f} against about KSh {usual:,.0f} on the last {len(before)} {day:%A}s."})
    day_items.sort(key=lambda i: i["day"], reverse=True)
    items += day_items[:limit]

    sales, total = unlinked
    if sales:
        items.append({"level": "info", "title": f"{sales} sale{'s' if sales != 1 else ''} not linked to a site",
                      "detail": f"KSh {total:,.0f} counted here but left out of the Revenue page, which only shows "
                                "sales placed at a site."})
    return items
