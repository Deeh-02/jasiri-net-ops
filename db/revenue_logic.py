"""Revenue reporting — the pure rules: which periods to compare, and how.

No database, no clock, no I/O: everything here takes its inputs as
arguments, so the same inputs always give the same answer and every rule has
a fixed-input test (tests/test_revenue_logic.py). The data layer
(db/revenue_daily.py) and the report queries sit on either side of this.

All times are EAT (UTC+3, no DST) naive wall-clock datetimes — the same
convention hourly_site_revenue.hour_start uses — and ranges are half-open
[start, end).
"""
import calendar
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

EAT = timezone(timedelta(hours=3))

GRANULARITIES = ("day", "week", "month", "year")

# mode -> granularities it applies to. The first mode listed per granularity
# is its default.
MODES = {
    "day": ("same_weekday_last_week", "previous_day", "baseline_median", "custom"),
    "week": ("previous_week", "same_week_last_year", "custom"),
    "month": ("previous_month", "same_month_last_year", "custom"),
    "year": ("previous_year", "custom"),
}

FLAT_BAND_PCT = Decimal("2")


# ---- Time helpers ----

def to_eat_naive(dt):
    """Aware datetime -> EAT wall-clock; a naive one is taken as already EAT."""
    if dt.tzinfo is not None:
        return dt.astimezone(EAT).replace(tzinfo=None)
    return dt


def eat_day_of_utc(utc_naive):
    """The EAT calendar day of a naive-UTC timestamp, as stored in the
    database: 23:30 UTC on the 12th is 02:30 EAT on the 13th. Mirrors the
    `+ interval '3 hours'` in db/revenue_daily.py."""
    return (utc_naive + timedelta(hours=3)).date()


def _floor_hour(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def _midnight(d):
    return datetime(d.year, d.month, d.day)


def _add_months(d, months):
    index = d.year * 12 + (d.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def period_bounds(granularity, anchor):
    """(start, end) datetimes of the whole calendar day/week/month/year that
    contains `anchor`. Weeks run Monday to Sunday."""
    if granularity == "day":
        start = _midnight(anchor)
        return start, start + timedelta(days=1)
    if granularity == "week":
        monday = anchor - timedelta(days=anchor.weekday())
        return _midnight(monday), _midnight(monday) + timedelta(days=7)
    if granularity == "month":
        first = anchor.replace(day=1)
        return _midnight(first), _midnight(_add_months(first, 1))
    if granularity == "year":
        return datetime(anchor.year, 1, 1), datetime(anchor.year + 1, 1, 1)
    raise ValueError(f"Unknown granularity {granularity!r}")


# ---- Formatting ----

def _d(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def format_kes(value):
    """KSh 10,810 / -KSh 3,220 — cents only when there are any."""
    value = _d(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value == value.to_integral_value():
        return f"{sign}KSh {int(value):,}"
    return f"{sign}KSh {value:,.2f}"


def _day_label(d):
    return f"{d:%a} {d:%d/%m/%Y}"


def _period_label(granularity, start, end_exclusive):
    first, last = start.date(), (end_exclusive - timedelta(days=1)).date()
    if granularity == "day":
        return _day_label(first)
    if granularity == "week":
        return f"Week {first:%d/%m}–{last:%d/%m/%Y}"
    if granularity == "month":
        return f"{first:%b %Y}"
    return f"{first:%Y}"


# ---- Period resolver ----

@dataclass(frozen=True)
class Resolution:
    granularity: str
    mode: str
    current_range: tuple          # (start, end) EAT, half-open
    comparison_ranges: object     # list of (start, end), or None = no comparable data
    aggregate: str                # 'sum' (one range) or 'median' (baseline_median)
    is_partial: bool
    equal_length: bool
    coverage: str                 # 'full' / 'partial' / 'none' — against history_start
    current_label: str
    comparison_label: str
    notes: list = field(default_factory=list)


def _comparison_periods(granularity, mode, anchor, custom_anchor, baseline_weeks, notes):
    """Anchor dates (one per comparison period) for this granularity/mode."""
    if mode not in MODES[granularity]:
        raise ValueError(f"Comparison mode {mode!r} doesn't apply to {granularity}")
    if mode == "custom":
        if custom_anchor is None:
            raise ValueError("custom comparison needs a custom_anchor date")
        if period_bounds(granularity, custom_anchor) == period_bounds(granularity, anchor):
            raise ValueError("Can't compare a period with itself")
        return [custom_anchor]
    if mode == "previous_day":
        return [anchor - timedelta(days=1)]
    if mode == "same_weekday_last_week":
        return [anchor - timedelta(days=7)]
    if mode == "baseline_median":
        return [anchor - timedelta(days=7 * k) for k in range(1, baseline_weeks + 1)]
    if mode == "previous_week":
        return [anchor - timedelta(days=7)]
    if mode == "same_week_last_year":
        iso_year, iso_week, _ = anchor.isocalendar()
        try:
            return [date.fromisocalendar(iso_year - 1, iso_week, 1)]
        except ValueError:  # week 53 doesn't exist last year
            notes.append(f"ISO week {iso_week} doesn't exist in {iso_year - 1}; compared with week 52")
            return [date.fromisocalendar(iso_year - 1, 52, 1)]
    if mode == "previous_month":
        return [_add_months(anchor.replace(day=1), -1)]
    if mode == "same_month_last_year":
        return [date(anchor.year - 1, anchor.month, 1)]
    if mode == "previous_year":
        return [date(anchor.year - 1, 1, 1)]
    raise ValueError(f"Unknown mode {mode!r}")


def resolve(granularity, anchor_date, now, mode=None, custom_anchor=None,
            baseline_weeks=6, history_start=None):
    """Which range to report and which to compare it with.

    granularity: 'day' | 'week' | 'month' | 'year'
    anchor_date: any EAT date inside the period being reported
    now:         current time (aware, or naive EAT) — passed in, never read
    mode:        comparison mode (see MODES); None = the granularity's default
    history_start: first EAT date with data; comparison ranges before it are
                 flagged rather than silently read as zero

    PARTIAL PERIODS. If the period hasn't ended, the current range runs from
    its start to the top of the current hour (so the in-progress hour never
    makes the two sides unequal) and each comparison range is cut to the
    same elapsed time — "today at 19:00" against "yesterday 00:00-19:00",
    "MTD Oct 1-13" against "Sep 1-13". A completed period is compared with
    the whole comparison period, and flagged if their lengths differ."""
    if granularity not in GRANULARITIES:
        raise ValueError(f"Unknown granularity {granularity!r}")
    mode = mode or MODES[granularity][0]
    notes = []

    now_eat = to_eat_naive(now)
    full_start, full_end = period_bounds(granularity, anchor_date)
    if full_start > now_eat:
        raise ValueError("Can't report a period that hasn't started yet")

    is_partial = now_eat < full_end
    cutoff = _floor_hour(now_eat) if is_partial else full_end
    elapsed = cutoff - full_start
    current_range = (full_start, cutoff)

    ranges = []
    for cmp_anchor in _comparison_periods(granularity, mode, anchor_date, custom_anchor, baseline_weeks, notes):
        cs, ce = period_bounds(granularity, cmp_anchor)
        if is_partial:
            cut = min(cs + elapsed, ce)
            if cs + elapsed > ce:
                notes.append(
                    f"{_period_label(granularity, cs, ce)} is shorter than the period it's compared "
                    f"with; compared up to its last day"
                )
            ranges.append((cs, cut))
        else:
            ranges.append((cs, ce))

    length = current_range[1] - current_range[0]
    equal_length = all(r[1] - r[0] == length for r in ranges)
    if not equal_length and not any("shorter" in n for n in notes):
        days_cur, days_cmp = length.days, (ranges[0][1] - ranges[0][0]).days
        notes.append(f"Unequal lengths: {days_cur} days compared with {days_cmp} days")

    coverage = "full"
    if history_start is not None:
        h0 = _midnight(history_start)
        states = ["none" if r[1] <= h0 else "partial" if r[0] < h0 else "full" for r in ranges]
        if all(s == "none" for s in states):
            coverage = "none"
        elif any(s != "full" for s in states):
            coverage = "partial"
            notes.append(f"History only starts {_day_label(history_start)}; the comparison isn't fully covered")
        if coverage == "none":
            ranges = None

    # Labels
    cur_label = _period_label(granularity, full_start, full_end)
    if is_partial:
        cur_label += f" (so far, to {cutoff:%H:%M})" if granularity == "day" else " (so far)"
    if ranges is None:
        cmp_label = "no comparable data"
    elif mode == "baseline_median":
        cmp_label = f"median of the last {baseline_weeks} {anchor_date:%A}s"
    else:
        cs, ce = ranges[0]
        if not is_partial:
            cmp_label = _period_label(granularity, cs, ce)
        elif granularity == "day":
            cmp_label = f"{_day_label(cs.date())} to {ce:%H:%M}" if ce > cs else _day_label(cs.date())
        else:
            last = (max(ce, cs + timedelta(seconds=1)) - timedelta(seconds=1))
            cmp_label = f"{cs:%d/%m/%Y} to {last:%d/%m/%Y}" + (f" {ce:%H:%M}" if ce.hour else "")

    return Resolution(
        granularity=granularity, mode=mode, current_range=current_range,
        comparison_ranges=ranges, aggregate="median" if mode == "baseline_median" else "sum",
        is_partial=is_partial, equal_length=equal_length, coverage=coverage,
        current_label=cur_label, comparison_label=cmp_label, notes=notes,
    )


def aggregate_comparison(values, how):
    """One comparison number from the per-range totals: their sum (one
    range) or their median (the baseline)."""
    values = [_d(v) for v in values]
    if not values:
        return None
    if how == "median":
        return statistics.median(values)
    return sum(values, Decimal(0))


# ---- Comparison ----

def compare(current, previous, flat_band_pct=FLAT_BAND_PCT):
    """One current/previous pair -> {current, previous, abs_change,
    pct_change, label, direction, status}.

    previous None means "no comparable data" (resolve() returned no range):
    nothing is computed and label says so, rather than reading it as zero.
    A percentage is never taken from a zero base — that's label 'New' or
    'No activity' with pct_change None. `status` is filled in by the alert
    rules (not built yet); it is always None here.
    """
    current = _d(current)
    if previous is None:
        return {"current": current, "previous": None, "abs_change": None, "pct_change": None,
                "label": "No comparable data", "direction": None, "status": None}
    previous = _d(previous)
    abs_change = current - previous

    label = None
    if previous > 0:
        pct = (abs_change / previous * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    else:
        pct = None
        label = "New" if current > 0 else "No activity"

    if pct is not None:
        direction = "→" if abs(pct) <= _d(flat_band_pct) else ("↑" if abs_change > 0 else "↓")
    elif abs_change > 0:
        direction = "↑"
    elif abs_change < 0:
        direction = "↓"
    else:
        direction = "→"

    return {"current": current, "previous": previous, "abs_change": abs_change,
            "pct_change": pct, "label": label, "direction": direction, "status": None}
