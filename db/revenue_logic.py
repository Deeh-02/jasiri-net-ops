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
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

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
        if mode == "baseline_median":
            # A median over several weeks must only use weeks that exist: a
            # week before history is missing, not zero, and counting it as 0
            # would drag the baseline down and flatter everything.
            kept = [r for r, s in zip(ranges, states) if s == "full"]
            if len(kept) < len(ranges):
                notes.append(f"Baseline uses the {len(kept)} of {len(ranges)} weeks that have data"
                             if kept else "No week in the baseline has data")
            ranges = kept or ranges
            states = ["full"] * len(kept) if kept else ["none"] * len(ranges)
        if all(s == "none" for s in states):
            coverage = "none"
        elif mode == "baseline_median" and len(ranges) < baseline_weeks:
            coverage = "partial"
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
        cmp_label = f"median of the last {len(ranges)} {anchor_date:%A}s" \
            if len(ranges) < baseline_weeks else f"median of the last {baseline_weeks} {anchor_date:%A}s"
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


# ---- Reading totals: which table answers which part of a range ----

def split_range(start, end):
    """Splits a half-open [start, end) range into the part daily_site_revenue
    can answer (whole EAT days) and the part only hourly_site_revenue can
    (the hours since the last midnight). Returns (days, hours): each a
    (from, to) pair or None. days is (first_date, end_date_exclusive)."""
    if end <= start:
        return None, None
    if start != _midnight(start.date()):
        return None, (start, end)  # never produced by resolve(); safe fallback
    day_end = _midnight(end.date())
    days = (start.date(), day_end.date()) if day_end > start else None
    hours = (max(start, day_end), end) if end > day_end else None
    return days, hours


# ---- Trend ----

MIN_BAND_POINTS = 14  # fewer completed days than this and no band is drawn


def rolling_mean(values, window=7):
    """Mean of each trailing `window` values; None until a full window exists."""
    out = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
        else:
            chunk = [_d(v) for v in values[i + 1 - window:i + 1]]
            out.append(sum(chunk, Decimal(0)) / window)
    return out


def mad_band(history, k, min_points=MIN_BAND_POINTS):
    """(low, high, median) = median ± k × MAD of `history` (the trailing
    completed days), or None when there isn't enough history or the days are
    all identical (MAD 0 would flag every point that differs at all)."""
    values = [_d(v) for v in history]
    if len(values) < min_points:
        return None
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    if mad == 0:
        return None
    width = _d(k) * mad
    return med - width, med + width, med


def slope_pct_of_mean(values):
    """Least-squares slope of `values` (one per period, oldest first) as a
    percentage of their mean, per period. None for fewer than 3 points or a
    zero mean."""
    values = [_d(v) for v in values]
    n = len(values)
    if n < 3:
        return None
    mean = sum(values, Decimal(0)) / n
    if mean == 0:
        return None
    x_mean = Decimal(n - 1) / 2
    num = sum(((Decimal(i) - x_mean) * (v - mean) for i, v in enumerate(values)), Decimal(0))
    den = sum(((Decimal(i) - x_mean) ** 2 for i in range(n)), Decimal(0))
    return num / den / mean * 100


def trend_label(slope_pct, flat_band_pct=FLAT_BAND_PCT):
    """Increasing / Decreasing / Stable from slope_pct_of_mean, with the rule
    that produced it (every interpretation says which rule it is)."""
    if slope_pct is None:
        return "Not enough data", "Needs at least 3 completed periods and some revenue"
    flat = _d(flat_band_pct)
    shown = slope_pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if slope_pct >= flat:
        label = "Increasing"
    elif slope_pct <= -flat:
        label = "Decreasing"
    else:
        label = "Stable"
    return label, f"Linear slope {shown:+}% of the window mean per period; ±{flat}% counts as stable"


# ---- Written summary ----
# Templates filled with computed values only — no causal language.

def _pct_text(pct):
    return f"{abs(pct)}%"


def headline_sentence(card):
    """'{period} revenue was KSh X, up/down KSh Y (Z%) vs {comparison}.'"""
    cur = format_kes(card["current"])
    head = f"{card['period_label']} revenue was {cur}"
    if card["previous"] is None:
        return f"{head}; there is no comparable data for {card['comparison_label']}."
    change = card["abs_change"]
    vs = f"vs {card['comparison_label']}"
    if card["label"] == "New":
        return f"{head}, up {format_kes(change)} {vs} (nothing was recorded in the comparison period)."
    if card["label"] == "No activity":
        return f"{head}, with no activity in the comparison period either."
    if change == 0:
        return f"{head}, unchanged {vs}."
    word = "up" if change > 0 else "down"
    return f"{head}, {word} {format_kes(abs(change))} ({_pct_text(card['pct_change'])}) {vs}."


def threshold_sentence(card, red_pct, red_min_abs):
    """'This exceeds / is within the 20% alert threshold.' — declines only.
    Exceeds needs both the % and the absolute floor, same as the red rule."""
    pct, change = card["pct_change"], card["abs_change"]
    if pct is None or change is None or change >= 0:
        return None
    exceeds = pct <= -_d(red_pct) and change <= -_d(red_min_abs)
    return f"This {'exceeds' if exceeds else 'is within'} the {_d(red_pct):g}% alert threshold."


def comparison_sentence(card):
    """'{period} is X% above/below {comparison}.' for the secondary cards."""
    pct = card["pct_change"]
    if pct is None or card["previous"] is None:
        return None
    if card["abs_change"] == 0:
        return f"{card['period_label']} is level with {card['comparison_label']}."
    return (f"{card['period_label']} is {_pct_text(pct)} "
            f"{'above' if card['abs_change'] > 0 else 'below'} {card['comparison_label']}.")


def build_summary(primary, others, red_pct, red_min_abs):
    lines = [headline_sentence(primary)]
    extra = threshold_sentence(primary, red_pct, red_min_abs)
    if extra:
        lines.append(extra)
    for card in others:
        line = comparison_sentence(card)
        if line:
            lines.append(line)
    return lines


# ---- Level 2: contribution, rankings, per-site series rules ----

def _share_pcts(values, total, places=1):
    """Each value's share of `total` as a percentage rounded to `places`,
    adjusted (largest remainder) so the shares add up to exactly 100 — a
    table whose column reads 99.9% or 100.1% looks broken."""
    if not values or total == 0:
        return [Decimal(0) for _ in values]
    step = Decimal(1).scaleb(-places)
    raw = [v / total * 100 for v in values]
    floors = [r.quantize(step, rounding=ROUND_FLOOR) for r in raw]
    missing = int(((Decimal(100) - sum(floors, Decimal(0))) / step).to_integral_value())
    order = sorted(range(len(raw)), key=lambda i: (raw[i] - floors[i], -i), reverse=True)
    for i in order[:max(missing, 0)]:
        floors[i] += step
    return floors


def _side(rows, gross, cutoff_pct):
    """One side (declines or gains) of the contribution breakdown. `rows`
    are every site on that side; the fewest sites whose combined share
    reaches cutoff_pct are listed, the rest grouped as 'Other sites'."""
    if not rows or gross == 0:
        return {"site_count": 0, "gross": Decimal(0), "contributors": [], "other": None,
                "covered_pct": Decimal(0), "cutoff_pct": _d(cutoff_pct)}
    ordered = sorted(rows, key=lambda r: (abs(r["abs_change"]) * -1, r["name"], r["site_id"]))
    shares = _share_pcts([r["abs_change"] for r in ordered], gross)
    exact = Decimal(0)
    listed = 0
    for r in ordered:
        exact += r["abs_change"] / gross * 100
        listed += 1
        if exact >= _d(cutoff_pct):
            break
    contributors = [{"site_id": r["site_id"], "name": r["name"], "abs_change": r["abs_change"], "share": s}
                    for r, s in zip(ordered[:listed], shares[:listed])]
    rest = ordered[listed:]
    other = None
    if rest:
        other = {"count": len(rest), "abs_change": sum((r["abs_change"] for r in rest), Decimal(0)),
                 "share": sum(shares[listed:], Decimal(0))}
    return {"site_count": len(ordered), "gross": gross, "contributors": contributors, "other": other,
            "covered_pct": sum(shares[:listed], Decimal(0)), "cutoff_pct": _d(cutoff_pct),
            "shares": {r["site_id"]: s for r, s in zip(ordered, shares)}}


def contributions(rows, cutoff_pct=70):
    """Each site's share of the network's GROSS movement, not the net — a
    network down KSh 3,220 may be sites down 6,000 and others up 2,780, and
    saying one site caused "all of" the net would be wrong. rows:
    [{site_id, name, abs_change}] with abs_change None for sites that have
    no comparison (left out). Returns the net on its own line plus a
    decline side and a gain side, each summing to 100%."""
    usable = [r for r in rows if r["abs_change"] is not None]
    decliners = [r for r in usable if r["abs_change"] < 0]
    gainers = [r for r in usable if r["abs_change"] > 0]
    gross_decline = sum((r["abs_change"] for r in decliners), Decimal(0))
    gross_gain = sum((r["abs_change"] for r in gainers), Decimal(0))
    return {
        "net_change": gross_decline + gross_gain,
        "gross_decline": gross_decline,
        "gross_gain": gross_gain,
        "decline": _side(decliners, gross_decline, cutoff_pct),
        "gain": _side(gainers, gross_gain, cutoff_pct),
    }


def rankings(rows, pct_rank_min_revenue=1000, top_n=5):
    """The five lists of §5.3. rows: [{site_id, name, current, previous,
    abs_change, pct_change}]. Losses (KSh) and % declines are separate lists
    on purpose, and a % ranking only admits sites whose previous revenue
    reaches the floor — a site going from KSh 40 to KSh 20 is -50% of
    nothing. Ties break on name, so the order never changes between runs."""
    def key(sort_value, r):
        return (sort_value, r["name"], r["site_id"])

    comparable = [r for r in rows if r["abs_change"] is not None]
    eligible = [r for r in comparable
                if r["pct_change"] is not None and r["previous"] >= _d(pct_rank_min_revenue)]
    return {
        "top_revenue": sorted(rows, key=lambda r: key(-r["current"], r))[:top_n],
        "largest_losses": sorted([r for r in comparable if r["abs_change"] < 0],
                                 key=lambda r: key(r["abs_change"], r))[:top_n],
        "largest_gains": sorted([r for r in comparable if r["abs_change"] > 0],
                                key=lambda r: key(-r["abs_change"], r))[:top_n],
        "largest_pct_declines": sorted([r for r in eligible if r["pct_change"] < 0],
                                       key=lambda r: key(r["pct_change"], r))[:top_n],
        "fastest_pct_growth": sorted([r for r in eligible if r["pct_change"] > 0],
                                     key=lambda r: key(-r["pct_change"], r))[:top_n],
    }


def weekday_baseline(series, day, weeks, history_start, step_days=7, min_points=3):
    """Median of this site's values on the same weekday over the previous
    `weeks` weeks — only weeks that exist (on/after history_start, present in
    `series`); None if fewer than min_points of them do. step_days=7 on a
    weekly series gives "the previous N weeks" instead."""
    values = []
    for k in range(1, weeks + 1):
        d = day - timedelta(days=step_days * k)
        if d >= history_start and d in series:
            values.append(_d(series[d]))
    if len(values) < min_points:
        return None
    return statistics.median(values)


def consecutive_vs_baseline(series, last_day, weeks, history_start, below=True, step_days=1,
                            baseline_step_days=7, min_points=3, min_abs=0, flat_pct=0):
    """How many consecutive periods, ending at last_day and walking back
    `step_days` at a time, were below (or above) their OWN baseline — each
    day against the same weekday's median, not against a single fixed
    number, so a quiet Sunday isn't a "decline" every week. Stops at the
    first period that isn't, or that has too little history to judge.

    min_abs / flat_pct are the floor: a period only counts when it is off
    its baseline by more than the flat band AND by at least min_abs KSh, so
    a run of days each KSh 15 under baseline is not a "decline"."""
    count = 0
    day = last_day
    while day >= history_start and day in series:
        base = weekday_baseline(series, day, weeks, history_start, baseline_step_days, min_points)
        if base is None:
            break
        value = _d(series[day])
        gap = max(_d(min_abs), base * _d(flat_pct) / 100)
        if (value < base - gap) if below else (value > base + gap):
            count += 1
            day -= timedelta(days=step_days)
        else:
            break
    return count


def trailing_min_max(series, end_day, days=7, history_start=None):
    """Lowest and highest daily value in the `days` days ending at end_day
    (inclusive), with their dates; None if no day in the window has data."""
    window = []
    for k in range(days):
        d = end_day - timedelta(days=k)
        if (history_start is None or d >= history_start) and d in series:
            window.append((d, _d(series[d])))
    if not window:
        return None
    low = min(window, key=lambda p: (p[1], p[0]))
    high = max(window, key=lambda p: (p[1], -p[0].toordinal()))
    return {"min": low[1], "min_date": low[0], "max": high[1], "max_date": high[0], "days": len(window)}


# ---- Alerts (§6) ----
# One pure function over a site's daily series. Red, amber and green are
# tried in that order and only the first level with a hit is reported
# ("Attention: not red, and any of...") — each alert carries the rule id and
# the values that triggered it, so the UI can say why.

RED, AMBER, GREEN = "red", "amber", "green"


def active_site(series, before_day, history_start, min_daily, days=14):
    """(is_active, average) — mean daily revenue over the `days` completed
    days before `before_day`, counting only days the site has a row for (a
    site added last week isn't averaged against days it didn't exist)."""
    first = max(before_day - timedelta(days=days), history_start)
    values = []
    d = first
    while d < before_day:
        if d in series:
            values.append(_d(series[d]))
        d += timedelta(days=1)
    if not values:
        return False, Decimal(0)
    avg = sum(values, Decimal(0)) / len(values)
    return avg >= _d(min_daily), avg


def _alert(rule_id, severity, text, **values):
    return {"rule_id": rule_id, "severity": severity, "text": text, "values": values}


def _window(series, first, last):
    return sum((_d(series.get(first + timedelta(days=i), 0)) for i in range((last - first).days + 1)), Decimal(0))


def evaluate_day(series, day, history_start, cfg, check_recovery=True):
    """Status of one site on one completed EAT day.

    series: {date: revenue} for the site. Returns {day, value, status,
    alerts, active, avg_14d}; status is 'red' / 'amber' / 'green' / None
    (normal). The comparison is the same weekday last week, the same one
    the report header uses."""
    cfg = {k: _d(v) for k, v in cfg.items()}
    flat, weeks = cfg["flat_band_pct"], int(cfg["baseline_weeks"])
    result = {"day": day, "value": None, "status": None, "alerts": [], "active": False, "avg_14d": Decimal(0)}
    if day < history_start or day not in series:
        return result

    value = _d(series[day])
    result["value"] = value
    active, avg = active_site(series, day, history_start, cfg["active_site_min_daily"])
    result["active"], result["avg_14d"] = active, avg

    week_ago = day - timedelta(days=7)
    c = compare(value, series[week_ago], flat) if week_ago >= history_start and week_ago in series else None
    pct = c["pct_change"] if c else None
    change = c["abs_change"] if c else None
    vs = f"vs {week_ago:%a %d/%m}"

    baseline = weekday_baseline(series, day, weeks, history_start)
    prior = trailing_min_max(series, day - timedelta(days=1), 7, history_start)
    below_days = consecutive_vs_baseline(series, day, weeks, history_start, below=True,
                                         min_abs=cfg["amber_min_abs"], flat_pct=flat)
    above_days = consecutive_vs_baseline(series, day, weeks, history_start, below=False,
                                         min_abs=cfg["green_min_abs"], flat_pct=flat)

    week_pct = week_change = None
    if day - timedelta(days=13) >= history_start:
        wk = compare(_window(series, day - timedelta(days=6), day),
                     _window(series, day - timedelta(days=13), day - timedelta(days=7)), flat)
        week_pct, week_change = wk["pct_change"], wk["abs_change"]

    # ---- red
    red = []
    if pct is not None and pct <= -cfg["red_pct"] and change <= -cfg["red_min_abs"]:
        red.append(_alert("red_pct_drop", RED, f"{pct:+}% ({format_kes(change)}) {vs}",
                          pct_change=pct, abs_change=change, red_pct=cfg["red_pct"], red_min_abs=cfg["red_min_abs"],
                          compared_with=week_ago.isoformat()))
    if prior and baseline is not None and value < prior["min"] and baseline - value >= cfg["red_min_abs"]:
        red.append(_alert("red_below_7d_low", RED,
                          f"{format_kes(value)} is below the 7-day low of {format_kes(prior['min'])} and "
                          f"{format_kes(baseline - value)} under its usual {day:%A}",
                          value=value, seven_day_low=prior["min"], baseline=baseline,
                          below_baseline=baseline - value, red_min_abs=cfg["red_min_abs"]))
    if below_days >= cfg["red_consecutive"]:
        red.append(_alert("red_declining", RED, f"Below its own baseline {below_days} days in a row",
                          consecutive_days=below_days, red_consecutive=cfg["red_consecutive"]))
    if active and value == 0:
        red.append(_alert("red_silent", RED,
                          f"No revenue on {day:%a %d/%m}; averaged {format_kes(avg.quantize(Decimal(1)))}/day "
                          f"over the previous 14 days",
                          avg_14d=avg, active_site_min_daily=cfg["active_site_min_daily"]))
    if red:
        result["status"], result["alerts"] = RED, red
        return result

    # ---- amber
    amber = []
    if pct is not None and pct <= -cfg["amber_pct"] and change <= -cfg["amber_min_abs"]:
        amber.append(_alert("amber_pct_drop", AMBER, f"{pct:+}% ({format_kes(change)}) {vs}",
                            pct_change=pct, abs_change=change, amber_pct=cfg["amber_pct"],
                            amber_min_abs=cfg["amber_min_abs"], compared_with=week_ago.isoformat()))
    if below_days >= cfg["amber_consecutive"]:
        amber.append(_alert("amber_declining", AMBER, f"Below its own baseline {below_days} days in a row",
                            consecutive_days=below_days, amber_consecutive=cfg["amber_consecutive"]))
    # No floor is given for this rule in the spec, but §9.5 says alerts
    # always need one — amber_min_abs is applied to the 7-day total.
    if week_pct is not None and week_pct <= -cfg["amber_pct"] and week_change <= -cfg["amber_min_abs"]:
        amber.append(_alert("amber_7d_vs_7d", AMBER,
                            f"Last 7 days {week_pct:+}% ({format_kes(week_change)}) vs the 7 before",
                            pct_change=week_pct, abs_change=week_change, amber_pct=cfg["amber_pct"],
                            amber_min_abs=cfg["amber_min_abs"]))
    if amber:
        result["status"], result["alerts"] = AMBER, amber
        return result

    # ---- green
    green = []
    if pct is not None and pct >= cfg["green_pct"] and change >= cfg["green_min_abs"]:
        green.append(_alert("green_pct_rise", GREEN, f"{pct:+}% ({format_kes(change)}) {vs}",
                            pct_change=pct, abs_change=change, green_pct=cfg["green_pct"],
                            green_min_abs=cfg["green_min_abs"], compared_with=week_ago.isoformat()))
    if above_days >= cfg["green_consecutive"]:
        green.append(_alert("green_above_baseline", GREEN, f"Above its own baseline {above_days} days in a row",
                            consecutive_days=above_days, green_consecutive=cfg["green_consecutive"]))
    if check_recovery and baseline is not None and day - timedelta(days=1) >= history_start:
        before = evaluate_day(series, day - timedelta(days=1), history_start, cfg, check_recovery=False)
        if before["status"] in (RED, AMBER) and value >= baseline * (1 - flat / 100):
            green.append(_alert("green_recovered", GREEN,
                                f"Was {before['status']} on {day - timedelta(days=1):%a %d/%m}; now "
                                f"{format_kes(value)} against a usual {format_kes(baseline)}",
                                previous_status=before["status"], value=value, baseline=baseline,
                                flat_band_pct=flat))
    if green:
        result["status"], result["alerts"] = GREEN, green
    return result
