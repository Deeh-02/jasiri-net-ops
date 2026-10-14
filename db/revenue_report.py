"""Revenue reporting, Level 1 — network totals: headline cards, trend and
the written summary. Read-only.

Reads daily_site_revenue / hourly_site_revenue (migration 0019) and nothing
else, so a report for a past date is the same every time; the period and
comparison rules are in db/revenue_logic.py and the thresholds in
db/revenue_config.py. Money leaves this module as JSON numbers plus a
pre-formatted 'KSh 10,810' string, so the client never formats or rounds.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from db import revenue_config
from db.connection import db_cursor, now_eat
import statistics

from db.revenue_logic import (MODES, aggregate_comparison, build_summary, compare, consecutive_vs_baseline,
                              contributions, evaluate_day, format_kes, mad_band, period_bounds, rankings, resolve,
                              rolling_mean, slope_pct_of_mean, split_range, to_eat_naive, trailing_min_max,
                              trend_label, weekday_baseline)

SOURCE = "hotspot"

# key, title, granularity, which day anchors it (0 = today, -1 = yesterday)
CARDS = [
    ("today", "Today (so far)", "day", 0),
    ("yesterday", "Yesterday", "day", -1),
    ("wtd", "This week", "week", 0),
    ("mtd", "This month", "month", 0),
    ("ytd", "This year", "year", 0),
]

TREND_SERIES = {
    "daily_7": ("day", 7), "daily_30": ("day", 30), "daily_90": ("day", 90),
    "weekly_12": ("week", 12), "monthly_12": ("month", 12),
    "year_current": ("year", 0), "year_previous": ("year", -1),
}

BAND_LOOKBACK_DAYS = 28


# ---- Reading ----

def _num(value):
    if value is None:
        return None
    value = Decimal(value)
    return int(value) if value == value.to_integral_value() else float(value)


def history_start(cur):
    """First EAT date the report has complete data for, or None. Sales start
    mid-day on the first day the feed ran, so that day is skipped: a
    comparison against a six-hour day would read as a collapse."""
    cur.execute("SELECT min(hour_start) FROM hourly_site_revenue WHERE source = %s", (SOURCE,))
    first = cur.fetchone()[0]
    if first is None:
        return None
    return first.date() if first.hour == 0 else first.date() + timedelta(days=1)


def data_as_of(cur):
    cur.execute("SELECT max(computed_at) FROM daily_site_revenue WHERE source = %s", (SOURCE,))
    row = cur.fetchone()[0]
    return row.isoformat() if row else None


def range_total(cur, start, end, site_id=None):
    """Revenue in [start, end) — whole days from the daily table, the
    trailing hours from the hourly one (see revenue_logic.split_range). The
    network, or one site when site_id is given."""
    days, hours = split_range(start, end)
    site_sql, site_args = ("AND site_id = %s", (site_id,)) if site_id is not None else ("", ())
    total = Decimal(0)
    if days:
        cur.execute(
            "SELECT COALESCE(sum(revenue), 0) FROM daily_site_revenue "
            f"WHERE source = %s AND date >= %s AND date < %s {site_sql}",
            (SOURCE, days[0], days[1], *site_args),
        )
        total += cur.fetchone()[0]
    if hours:
        cur.execute(
            "SELECT COALESCE(sum(revenue), 0) FROM hourly_site_revenue "
            f"WHERE source = %s AND hour_start >= %s AND hour_start < %s {site_sql}",
            (SOURCE, hours[0], hours[1], *site_args),
        )
        total += cur.fetchone()[0]
    return total


def site_totals(cur, start, end):
    """{site_id: revenue} in [start, end), same table split as range_total."""
    days, hours = split_range(start, end)
    totals = {}
    if days:
        cur.execute(
            "SELECT site_id, sum(revenue) FROM daily_site_revenue "
            "WHERE source = %s AND date >= %s AND date < %s GROUP BY site_id",
            (SOURCE, days[0], days[1]),
        )
        for site_id, value in cur.fetchall():
            totals[site_id] = totals.get(site_id, Decimal(0)) + value
    if hours:
        cur.execute(
            "SELECT site_id, sum(revenue) FROM hourly_site_revenue "
            "WHERE source = %s AND hour_start >= %s AND hour_start < %s GROUP BY site_id",
            (SOURCE, hours[0], hours[1]),
        )
        for site_id, value in cur.fetchall():
            totals[site_id] = totals.get(site_id, Decimal(0)) + value
    return totals


def _daily_totals(cur, first, last):
    cur.execute(
        "SELECT date, sum(revenue) FROM daily_site_revenue "
        "WHERE source = %s AND date BETWEEN %s AND %s GROUP BY date",
        (SOURCE, first, last),
    )
    return {d: v for d, v in cur.fetchall()}


# ---- Cards ----

def _card(cur, key, title, granularity, anchor, now, mode, custom_anchor, hist, cfg):
    res = resolve(granularity, anchor, now, mode=mode, custom_anchor=custom_anchor,
                  baseline_weeks=int(cfg["baseline_weeks"]), history_start=hist)
    current = range_total(cur, *res.current_range)
    previous = None
    if res.comparison_ranges is not None:
        previous = aggregate_comparison([range_total(cur, *r) for r in res.comparison_ranges], res.aggregate)
    result = compare(current, previous, cfg["flat_band_pct"])
    # Kept as Decimals for the summary builder; _serialise() makes JSON.
    result.update({
        "key": key, "title": title, "granularity": granularity, "anchor": anchor.isoformat(),
        "mode": res.mode, "available_modes": list(MODES[granularity]),
        "period_label": res.current_label, "comparison_label": res.comparison_label,
        "is_partial": res.is_partial, "equal_length": res.equal_length,
        "coverage": res.coverage, "notes": res.notes,
        "current_range": [res.current_range[0].isoformat(), res.current_range[1].isoformat()],
        "comparison_ranges": (None if res.comparison_ranges is None
                              else [[a.isoformat(), b.isoformat()] for a, b in res.comparison_ranges]),
    })
    return result


def _serialise(card):
    out = dict(card)
    for field in ("current", "previous", "abs_change", "pct_change"):
        out[field] = _num(card[field])
    out["display"] = {
        "current": format_kes(card["current"]),
        "previous": None if card["previous"] is None else format_kes(card["previous"]),
        "abs_change": None if card["abs_change"] is None else format_kes(card["abs_change"]),
        "pct_change": None if card["pct_change"] is None else f"{card['pct_change']:+}%",
    }
    return out


def overview(now=None, modes=None):
    """The five headline cards plus the written summary. `modes` is
    {card key: comparison mode} for any card the user switched."""
    now = now or now_eat()
    today = to_eat_naive(now).date()
    modes = modes or {}
    cfg = revenue_config.load()
    with db_cursor() as (conn, cur):
        hist = history_start(cur)
        cards = []
        for key, title, granularity, offset in CARDS:
            cards.append(_card(cur, key, title, granularity, today + timedelta(days=offset), now,
                               modes.get(key), None, hist, cfg))
        as_of = data_as_of(cur)

    by_key = {c["key"]: c for c in cards}
    primary = by_key["yesterday"]
    others = [by_key[k] for k in ("today", "wtd", "mtd", "ytd")]
    summary = build_summary(primary, others, cfg["red_pct"], cfg["red_min_abs"])
    return {
        "as_of": as_of,
        "history_start": hist.isoformat() if hist else None,
        "header": (f"{primary['period_label']} vs {primary['comparison_label']} (full day)"
                   if primary["previous"] is not None else f"{primary['period_label']} (no comparable data)"),
        "cards": [_serialise(c) for c in cards],
        "summary": summary,
        # Sites in red for the last completed day (alerts engine).
        "attention_count": _attention_count(now),
    }


def _attention_count(now):
    from db import revenue_alerts
    return revenue_alerts.attention_count(now)


def card(granularity, anchor, now=None, mode=None, custom_anchor=None):
    """One card for any period — a past day, a chosen month, a custom comparison."""
    now = now or now_eat()
    cfg = revenue_config.load()
    with db_cursor() as (conn, cur):
        hist = history_start(cur)
        result = _card(cur, "custom", granularity.title(), granularity, anchor, now, mode,
                       custom_anchor, hist, cfg)
        as_of = data_as_of(cur)
    return {"as_of": as_of, "card": _serialise(result)}


# ---- Trend ----

def _day_points(cur, window_days, today, hist, cfg):
    """Daily points oldest first, today last (partial). The band for each
    point looks back BAND_LOOKBACK_DAYS completed days, which may reach
    before the window — those are fetched but not returned."""
    first_shown = max(today - timedelta(days=window_days - 1), hist)
    first_fetched = max(first_shown - timedelta(days=BAND_LOOKBACK_DAYS), hist)
    totals = _daily_totals(cur, first_fetched, today)
    series = []  # every fetched day, oldest first
    d = first_fetched
    while d <= today:
        series.append((d, Decimal(totals.get(d, 0))))
        d += timedelta(days=1)

    completed = [v for dt, v in series if dt < today]
    rolling_all = rolling_mean([v for _, v in series], 7)

    points = []
    for i, (dt, value) in enumerate(series):
        if dt < first_shown:
            continue
        partial = dt == today
        point = {
            "start": dt.isoformat(), "label": f"{dt:%a %d/%m}", "value": _num(value),
            "display": format_kes(value), "partial": partial,
            "rolling_avg": None if partial or rolling_all[i] is None else _num(rolling_all[i].quantize(Decimal("0.01"))),
            "band": None, "unusual": None, "rule": None,
        }
        if not partial:
            lookback = [v for dt2, v in series if dt - timedelta(days=BAND_LOOKBACK_DAYS) <= dt2 < dt]
            band = mad_band(lookback, cfg["anomaly_k"])
            if band:
                low, high, med = band
                point["band"] = {"low": _num(low.quantize(Decimal("0.01"))), "high": _num(high.quantize(Decimal("0.01")))}
                if value < low or value > high:
                    point["unusual"] = "low" if value < low else "high"
                    point["rule"] = (f"Outside median ± {cfg['anomaly_k']:g}×MAD of the previous "
                                     f"{len(lookback)} completed days ({format_kes(low)} to {format_kes(high)})")
        points.append(point)
    return points, [p for p in points if not p["partial"]]


def _bucket_points(cur, granularity, count, today, hist):
    """Weekly or monthly points: the last `count` periods, each complete and
    covered by history, plus the current one flagged partial."""
    starts = []
    anchor = today
    for _ in range(count):
        start, _end = period_bounds(granularity, anchor)
        starts.append(start.date())
        anchor = start.date() - timedelta(days=1)
    starts.reverse()

    points = []
    for start in starts:
        s, e = period_bounds(granularity, start)
        if s.date() < hist:
            continue  # period begins before history — would understate it
        end_day = min(e.date() - timedelta(days=1), today)
        value = sum(_daily_totals(cur, s.date(), end_day).values(), Decimal(0))
        partial = e.date() > today
        label = f"{s:%d/%m}" if granularity == "week" else f"{s:%b %Y}"
        points.append({"start": s.date().isoformat(), "label": label, "value": _num(value),
                       "display": format_kes(value), "partial": partial, "rolling_avg": None,
                       "band": None, "unusual": None, "rule": None})
    return points


def _year_points(cur, year, today, hist):
    points = []
    for month in range(1, 13):
        s = date(year, month, 1)
        if s > today:
            break
        if s < hist:
            continue
        e = period_bounds("month", s)[1].date()
        end_day = min(e - timedelta(days=1), today)
        value = sum(_daily_totals(cur, s, end_day).values(), Decimal(0))
        points.append({"start": s.isoformat(), "label": f"{s:%b}", "value": _num(value),
                       "display": format_kes(value), "partial": e > today,
                       "rolling_avg": None, "band": None, "unusual": None, "rule": None})
    return points


def trend(series, now=None):
    if series not in TREND_SERIES:
        raise ValueError(f"Unknown series {series!r}")
    granularity, size = TREND_SERIES[series]
    now = now or now_eat()
    today = to_eat_naive(now).date()
    cfg = revenue_config.load()
    with db_cursor() as (conn, cur):
        hist = history_start(cur)
        as_of = data_as_of(cur)
        if hist is None:
            return {"series": series, "granularity": granularity, "as_of": as_of, "points": [],
                    "trend": {"label": "Not enough data", "rule": "No revenue history yet"}}
        if granularity == "day":
            points, completed = _day_points(cur, size, today, hist, cfg)
        elif granularity in ("week", "month"):
            points = _bucket_points(cur, granularity, size, today, hist)
            completed = [p for p in points if not p["partial"]]
        else:
            year = today.year + size  # size is 0 or -1
            points = _year_points(cur, year, today, hist)
            completed = [p for p in points if not p["partial"]]

    slope = slope_pct_of_mean([p["value"] for p in completed])
    label, rule = trend_label(slope, cfg["flat_band_pct"])
    return {
        "series": series, "granularity": granularity, "as_of": as_of, "points": points,
        "trend": {"label": label, "slope_pct_per_period": None if slope is None else float(round(slope, 2)),
                  "rule": rule, "based_on": len(completed)},
        "unusual_count": sum(1 for p in points if p["unusual"]),
    }


# ---- Level 2: sites ----

SERIES_LOOKBACK_DAYS = 120  # covers a 6-week baseline plus 12 weeks of weekly history

SORTS = {
    # name -> (key over a row, reverse)
    "largest_loss": (lambda r: (r["abs_change"] is None, r["abs_change"] if r["abs_change"] is not None else 0), False),
    "largest_gain": (lambda r: (r["abs_change"] is None, -(r["abs_change"] if r["abs_change"] is not None else 0)), False),
    "largest_pct_decline": (lambda r: (r["pct_change"] is None, r["pct_change"] if r["pct_change"] is not None else 0), False),
    "largest_pct_growth": (lambda r: (r["pct_change"] is None, -(r["pct_change"] if r["pct_change"] is not None else 0)), False),
    "revenue": (lambda r: -r["current"], False),
    "name": (lambda r: r["name"].lower(), False),
}


def _site_names(cur, ids):
    if not ids:
        return {}
    cur.execute(
        """
        SELECT ms.id, COALESCE(l.name, ms.name, 'VLAN ' || ms.vlan_id)
        FROM monitored_sites ms LEFT JOIN locations l ON l.id = ms.location_id
        WHERE ms.id = ANY(%s)
        """,
        (list(ids),),
    )
    return {r[0]: r[1] for r in cur.fetchall()}


def _site_series(cur, first, last):
    """{site_id: {date: revenue}} for every site over first..last."""
    cur.execute(
        "SELECT site_id, date, revenue FROM daily_site_revenue "
        "WHERE source = %s AND date BETWEEN %s AND %s",
        (SOURCE, first, last),
    )
    out = {}
    for site_id, day, value in cur.fetchall():
        out.setdefault(site_id, {})[day] = value
    return out


def _window_sum(series, first, last):
    return sum((series.get(first + timedelta(days=i), Decimal(0)) for i in range((last - first).days + 1)),
               Decimal(0))


def _seven_day_trend(series, today, hist, flat_band):
    """Last 7 completed days vs the 7 before them (None if either window
    starts before history)."""
    last = today - timedelta(days=1)
    cur_first, prev_first = last - timedelta(days=6), last - timedelta(days=13)
    if cur_first < hist:
        return None
    current = _window_sum(series, cur_first, last)
    previous = _window_sum(series, prev_first, prev_first + timedelta(days=6)) if prev_first >= hist else None
    return compare(current, previous, flat_band)


def _labelled(c, period_label, comparison_label):
    if c is None:
        return None
    return {**c, "period_label": period_label, "comparison_label": comparison_label,
            "coverage": "full" if c["previous"] is not None else "none", "notes": []}


def _site_link(site_id, granularity, start):
    """Path into the EXISTING site page (no site detail lives in this
    module). Years have no page of their own — they open the site."""
    d = start.date()
    if granularity == "day":
        return f"site-detail/{site_id}/day/{d.isoformat()}"
    if granularity == "week":
        return f"site-detail/{site_id}/week/{d.isoformat()}"
    if granularity == "month":
        return f"site-detail/{site_id}/month/{d:%Y-%m}"
    return f"site-detail/{site_id}"


def _ser_row(row):
    out = dict(row)
    for field in ("current", "previous", "abs_change", "pct_change", "share"):
        if field in out:
            out[field] = _num(out[field])
    out["display"] = {
        "current": format_kes(row["current"]),
        "previous": None if row["previous"] is None else format_kes(row["previous"]),
        "abs_change": None if row["abs_change"] is None else format_kes(row["abs_change"]),
        "pct_change": None if row["pct_change"] is None else f"{row['pct_change']:+}%",
        "share": None if row.get("share") is None else f"{row['share']}%",
    }
    if row.get("trend7"):
        t = row["trend7"]
        out["trend7"] = {"direction": t["direction"], "pct_change": _num(t["pct_change"]),
                         "label": t["label"], "abs_change": _num(t["abs_change"])}
    return out


def _ser_side(side):
    def contributor(c):
        return {"site_id": c["site_id"], "name": c["name"], "abs_change": _num(c["abs_change"]),
                "share": _num(c["share"]), "display": {"abs_change": format_kes(c["abs_change"]),
                                                       "share": f"{c['share']}%"}}
    other = side["other"]
    return {
        "site_count": side["site_count"], "gross": _num(side["gross"]), "gross_display": format_kes(side["gross"]),
        "covered_pct": _num(side["covered_pct"]), "cutoff_pct": _num(side["cutoff_pct"]),
        "contributors": [contributor(c) for c in side["contributors"]],
        "other": None if other is None else {
            "label": "Other sites", "count": other["count"], "abs_change": _num(other["abs_change"]),
            "share": _num(other["share"]),
            "display": {"abs_change": format_kes(other["abs_change"]), "share": f"{other['share']}%"}},
    }


def sites_report(granularity="day", anchor=None, now=None, mode=None, custom_anchor=None, sort="largest_loss"):
    """The site table, contribution to the network change, and the five
    rankings — all for ONE resolved period, the same one the matching
    network card uses, so the sites always add up to the headline."""
    if sort not in SORTS:
        raise ValueError(f"Unknown sort {sort!r}")
    now = now or now_eat()
    today = to_eat_naive(now).date()
    if anchor is None:
        anchor = today - timedelta(days=1)  # yesterday: the header's default
    cfg = revenue_config.load()

    with db_cursor() as (conn, cur):
        hist = history_start(cur)
        res = resolve(granularity, anchor, now, mode=mode, custom_anchor=custom_anchor,
                      baseline_weeks=int(cfg["baseline_weeks"]), history_start=hist)
        as_of = data_as_of(cur)

        current = site_totals(cur, *res.current_range)
        previous = None
        if res.comparison_ranges is not None:
            per_range = [site_totals(cur, *r) for r in res.comparison_ranges]
            ids = set().union(*[set(p) for p in per_range]) if per_range else set()
            previous = {sid: aggregate_comparison([p.get(sid, Decimal(0)) for p in per_range], res.aggregate)
                        for sid in ids}

        # Every site with a row in either period (zero rows exist for active
        # sites, so a silent site is present, not missing).
        first_day = min([res.current_range[0]] + [r[0] for r in (res.comparison_ranges or [])]).date()
        last_day = max([res.current_range[1]] + [r[1] for r in (res.comparison_ranges or [])]).date()
        cur.execute(
            "SELECT DISTINCT site_id FROM daily_site_revenue WHERE source = %s AND date BETWEEN %s AND %s",
            (SOURCE, first_day, last_day),
        )
        site_ids = {r[0] for r in cur.fetchall()} | set(current) | set(previous or {})
        names = _site_names(cur, site_ids)

        series = _site_series(cur, max(hist or today, today - timedelta(days=SERIES_LOOKBACK_DAYS)), today)

    if hist is None:
        hist = today
    min_daily = cfg["active_site_min_daily"]
    elapsed_days = (res.current_range[1] - res.current_range[0]).days

    # Status is always as of the last COMPLETED day against the same weekday
    # last week, whatever period the table is showing, and says so.
    status_day = today - timedelta(days=1)
    status_by_site = {sid: evaluate_day(series.get(sid, {}), status_day, hist, cfg) for sid in site_ids}

    rows = []
    for sid in sorted(site_ids):
        cur_v = current.get(sid, Decimal(0))
        prev_v = None if previous is None else previous.get(sid, Decimal(0))
        c = compare(cur_v, prev_v, cfg["flat_band_pct"])

        # Active: average daily revenue over the trailing 14 completed days.
        s = series.get(sid, {})
        win_first = max(today - timedelta(days=14), hist)
        days_in_window = (today - win_first).days
        avg = _window_sum(s, win_first, today - timedelta(days=1)) / days_in_window if days_in_window > 0 else Decimal(0)
        active = avg >= min_daily

        label, label_rule = c["label"], None
        if prev_v is not None and cur_v == 0 and active:
            if elapsed_days >= 1:
                label = "Silent"
                label_rule = (f"Active site (avg KSh {avg:,.0f}/day over the last 14 completed days "
                              f"≥ {min_daily:g}) with no revenue in the period")
            else:
                label, label_rule = "No sales yet", "Active site with no revenue yet today"
        elif label == "New":
            label_rule = "No revenue in the comparison period, revenue now"

        rows.append({
            "site_id": sid, "name": names.get(sid, f"Site {sid}"),
            "current": cur_v, "previous": prev_v,
            "abs_change": c["abs_change"], "pct_change": c["pct_change"], "direction": c["direction"],
            "label": label, "label_rule": label_rule, "active": active,
            "avg_daily_14d": avg.quantize(Decimal("0.01")),
            "trend7": _seven_day_trend(s, today, hist, cfg["flat_band_pct"]),
            "status": status_by_site[sid]["status"] if sid in status_by_site else None,
            "status_alerts": ([{"rule_id": a["rule_id"], "severity": a["severity"], "text": a["text"]}
                               for a in status_by_site[sid]["alerts"]] if sid in status_by_site else []),
            "share": None, "share_side": None,
            "link": _site_link(sid, granularity, res.current_range[0]),
        })

    contrib = contributions(rows, cfg["contribution_cutoff_pct"])
    for side_name in ("decline", "gain"):
        shares = contrib[side_name].get("shares", {})
        for r in rows:
            if r["site_id"] in shares:
                r["share"], r["share_side"] = shares[r["site_id"]], side_name

    ranks = rankings(rows, cfg["pct_rank_min_revenue"])
    key, reverse = SORTS[sort]
    ordered = sorted(rows, key=lambda r: (key(r), r["name"], r["site_id"]), reverse=reverse)

    net_current = sum((r["current"] for r in rows), Decimal(0))
    net_previous = None if previous is None else sum((r["previous"] for r in rows), Decimal(0))
    network = compare(net_current, net_previous, cfg["flat_band_pct"])

    def slim(r):
        return {"site_id": r["site_id"], "name": r["name"], "link": r["link"], "label": r["label"],
                **{k: _ser_row(r)[k] for k in ("current", "previous", "abs_change", "pct_change", "display")}}

    return {
        "as_of": as_of,
        "period": {
            "granularity": granularity, "mode": res.mode, "available_modes": list(MODES[granularity]),
            "period_label": res.current_label, "comparison_label": res.comparison_label,
            "is_partial": res.is_partial, "equal_length": res.equal_length, "coverage": res.coverage,
            "notes": res.notes,
            "current_range": [res.current_range[0].isoformat(), res.current_range[1].isoformat()],
        },
        "network": _serialise({**network, "key": "network"}),
        "status_day": status_day.isoformat(),
        "site_count": len(rows),
        "active_site_count": sum(1 for r in rows if r["active"]),
        "sort": sort,
        "sites": [_ser_row(r) for r in ordered],
        "contributions": None if previous is None else {
            "net_change": _num(contrib["net_change"]), "net_display": format_kes(contrib["net_change"]),
            "gross_decline": _num(contrib["gross_decline"]), "gross_gain": _num(contrib["gross_gain"]),
            "decline": _ser_side(contrib["decline"]), "gain": _ser_side(contrib["gain"]),
        },
        "rankings": None if previous is None else {name: [slim(r) for r in lst] for name, lst in ranks.items()},
    }


def site_metrics(site_id, now=None):
    """§5.4 — everything the report knows about one site's trend, each
    comparison with the rule that defines it."""
    now = now or now_eat()
    today = to_eat_naive(now).date()
    last = today - timedelta(days=1)
    cfg = revenue_config.load()
    weeks = int(cfg["baseline_weeks"])
    flat = cfg["flat_band_pct"]

    with db_cursor() as (conn, cur):
        hist = history_start(cur)
        names = _site_names(cur, [site_id])
        if site_id not in names:
            return None
        if hist is None:
            return {"site_id": site_id, "name": names[site_id], "as_of": data_as_of(cur), "metrics": None}
        s = _site_series(cur, max(hist, today - timedelta(days=SERIES_LOOKBACK_DAYS)), today).get(site_id, {})

        def period_compare(granularity, anchor, mode):
            res = resolve(granularity, anchor, now, mode=mode, baseline_weeks=weeks, history_start=hist)
            cur_v = range_total(cur, *res.current_range, site_id=site_id)
            prev_v = None
            if res.comparison_ranges is not None:
                prev_v = aggregate_comparison(
                    [range_total(cur, *r, site_id=site_id) for r in res.comparison_ranges], res.aggregate)
            out = compare(cur_v, prev_v, flat)
            out.update({"period_label": res.current_label, "comparison_label": res.comparison_label,
                        "coverage": res.coverage, "notes": res.notes})
            return out

        mtd = period_compare("month", today, None)
        ytd = period_compare("year", today, None)
        # Completed months only: the latest finished month vs the one before.
        last_month_day = today.replace(day=1) - timedelta(days=1)
        month = period_compare("month", last_month_day, "previous_month") if last_month_day >= hist else None
        as_of = data_as_of(cur)

    def day_compare(previous_value, label):
        if last < hist or last not in s:
            return None
        out = compare(s[last], previous_value, flat)
        out.update({"period_label": f"{last:%a %d/%m/%Y}", "comparison_label": label, "coverage": "full", "notes": []})
        return out

    same_weekday_prev = s.get(last - timedelta(days=7)) if last - timedelta(days=7) >= hist else None
    baseline = weekday_baseline(s, last, weeks, hist)

    # Weekly series (Monday-start, complete weeks on/after history) for the
    # week-level streak.
    weekly = {}
    for day, value in s.items():
        monday = day - timedelta(days=day.weekday())
        if monday >= hist and monday + timedelta(days=6) < today:
            weekly[monday] = weekly.get(monday, Decimal(0)) + value
    complete_weekly = {m: v for m, v in weekly.items()
                       if all((m + timedelta(days=i)) in s for i in range(7))}
    last_monday = max(complete_weekly) if complete_weekly else None

    metrics = {
        "day_vs_same_weekday_last_week": day_compare(same_weekday_prev, f"{last - timedelta(days=7):%a %d/%m/%Y}")
            if same_weekday_prev is not None else None,
        "day_vs_baseline": day_compare(baseline, f"median of the last {weeks} {last:%A}s (those with data)")
            if baseline is not None else None,
        "last_7_vs_previous_7": _labelled(_seven_day_trend(s, today, hist, flat),
                                          f"{last - timedelta(days=6):%d/%m}–{last:%d/%m/%Y}",
                                          f"{last - timedelta(days=13):%d/%m}–{last - timedelta(days=7):%d/%m/%Y}"),
        "mtd_vs_comparable": mtd,
        "month_vs_previous_month": month,
        "ytd_vs_comparable": ytd,
        # Same floors the alert rules use: off baseline by more than the
        # flat band and by at least amber_min_abs (declines) / green_min_abs.
        "declining_days": consecutive_vs_baseline(s, last, weeks, hist, below=True,
                                                  min_abs=cfg["amber_min_abs"], flat_pct=flat),
        "above_baseline_days": consecutive_vs_baseline(s, last, weeks, hist, below=False,
                                                       min_abs=cfg["green_min_abs"], flat_pct=flat),
        "declining_weeks": consecutive_vs_baseline(complete_weekly, last_monday, weeks, hist, below=True,
                                                   step_days=7, min_abs=cfg["amber_min_abs"],
                                                   flat_pct=flat) if last_monday else 0,
        "above_baseline_weeks": consecutive_vs_baseline(complete_weekly, last_monday, weeks, hist, below=False,
                                                        step_days=7, min_abs=cfg["green_min_abs"],
                                                        flat_pct=flat) if last_monday else 0,
        "trailing_7d": trailing_min_max(s, last, 7, hist),
        "prior_7d": trailing_min_max(s, last - timedelta(days=1), 7, hist),
    }

    def ser(c):
        if c is None:
            return None
        out = _serialise({**c, "key": "metric"})
        return {k: out[k] for k in ("current", "previous", "abs_change", "pct_change", "direction", "label",
                                    "display", "period_label", "comparison_label", "coverage", "notes")}

    def ser_range(r):
        if r is None:
            return None
        return {"min": _num(r["min"]), "max": _num(r["max"]), "min_date": r["min_date"].isoformat(),
                "max_date": r["max_date"].isoformat(), "days": r["days"],
                "display": {"min": format_kes(r["min"]), "max": format_kes(r["max"])}}

    out = {k: ser(v) for k, v in metrics.items() if isinstance(v, dict) and "current" in v}
    out.update({
        "declining_days": metrics["declining_days"], "above_baseline_days": metrics["above_baseline_days"],
        "declining_weeks": metrics["declining_weeks"], "above_baseline_weeks": metrics["above_baseline_weeks"],
        "trailing_7d": ser_range(metrics["trailing_7d"]), "prior_7d": ser_range(metrics["prior_7d"]),
    })
    for k in ("day_vs_same_weekday_last_week", "day_vs_baseline", "last_7_vs_previous_7",
              "mtd_vs_comparable", "month_vs_previous_month", "ytd_vs_comparable"):
        out.setdefault(k, None)
    return {"site_id": site_id, "name": names[site_id], "as_of": as_of, "last_completed_day": last.isoformat(),
            "metrics": out}
