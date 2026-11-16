"""Hotspot income for Finance: the old billing history before the router
started recording sales, the router from then on. Finance > Income and
Performance read hotspot money only through here.

The router (db/monitoring.py, revenue_events) records from 21 Sep 2026; it
started mid-day, so that day is partial. The billing system's transactions
export (db/billing_history.py) covers from 1 May 2026 and matched the router
within 1% a day where both exist (22 Sep - 16 Nov 2026: KSh 615,423 billed,
KSh 610,430 seen by the router), so it fills in every day before the
router's first full day. The router stays the source after that: it is live,
and the Revenue page's site figures come from it. Without the history, the
router alone, as before.

Every function returns the shapes monitoring's hotspot functions return.
"""
from datetime import timedelta
from decimal import Decimal

from db import billing_history, monitoring
from db import billing_history_logic as logic
from db.connection import db_cursor

EMPTY = {"rows": [], "matching": 0, "matching_total": Decimal("0")}


def _sources(cur):
    """(router_from, history_first_day, history_last_day): the router covers
    router_from on; the history covers its days before that."""
    first, last = billing_history.coverage(cur)
    return logic.router_from(monitoring.hotspot_records_start(cur), last), first, last


def _add(rows, more):
    """Sums [(key, total, count)] lists by key."""
    out = {}
    for key, total, count in list(rows) + list(more):
        t, c = out.get(key, (Decimal("0"), 0))
        out[key] = (t + total, c + count)
    return out


def income_for_month(history_from, first_day, last_day):
    """monitoring.hotspot_income_for_month's result over both sources, plus
    'sources' {router_from, history_from} for the page's note."""
    with db_cursor() as (conn, cur):
        router_from, hist_first, hist_last = _sources(cur)
        old = billing_history.hotspot_part(cur, history_from, first_day, last_day, router_from)
        # Customers: people can't be told apart across the two sources, so a
        # month that starts before the router counts them from the history,
        # which covers the whole month as long as the export reaches its end.
        old_buyers = billing_history.hotspot_buyers(cur, first_day, last_day, hist_first) \
            if hist_first and router_from and first_day < router_from and hist_last >= last_day else None
    if router_from is None or last_day >= router_from:
        new = monitoring.hotspot_income_for_month(history_from, first_day, last_day, since=router_from if hist_first else None)
    else:
        new = {"first_day": None, "daily": [], "hourly": [], "packages": [], "buyers": None,
               "unpriced": [], "unlinked": (0, Decimal("0")), "last_sale": None}
    if not hist_first:
        return dict(new, sources={"router_from": router_from, "history_from": None})
    hourly = _add(old["hourly"], new["hourly"])
    packages = _add(old["packages"], new["packages"])
    if old_buyers is not None:
        buyers = old_buyers
    elif router_from and first_day >= router_from:
        buyers = new["buyers"]
    else:
        buyers = None
    return dict(
        new,
        first_day=hist_first,
        daily=sorted(list(old["daily"]) + list(new["daily"])),
        hourly=sorted((h, t, c) for h, (t, c) in hourly.items()),
        packages=sorted(((p, t, c) for p, (t, c) in packages.items()), key=lambda p: (-p[1], p[0]))[:8],
        buyers=buyers,
        sources={"router_from": router_from, "history_from": hist_first},
    )


def daily_totals(first_day, last_day):
    """([(EAT day, total)], records start) over both sources."""
    with db_cursor() as (conn, cur):
        router_from, hist_first, _ = _sources(cur)
        old = billing_history.hotspot_daily_totals(cur, first_day, last_day, router_from) if hist_first else []
    router_first = max(first_day, router_from) if hist_first and router_from else first_day
    new, router_start = monitoring.hotspot_daily_totals(router_first, max(last_day, router_first)) \
        if router_from is None or last_day >= router_from else ([], router_from)
    return sorted(list(old) + list(new)), hist_first or router_start


def sales(first_day, last_day, search=None, day=None, package=None, limit=50, offset=0):
    """One page of paid hotspot transactions, newest first, over both
    sources. The router's days are all newer than the history's, so the page
    is the router's rows and then the history's."""
    filters = {"search": search, "day": day, "package": package}
    with db_cursor() as (conn, cur):
        router_from, hist_first, _ = _sources(cur)
        if not hist_first:
            router_from = None
        old_last = last_day if router_from is None else min(last_day, router_from - timedelta(days=1))
        in_router = router_from is None or last_day >= router_from
        new = monitoring.hotspot_sales(max(first_day, router_from) if router_from else first_day, last_day,
                                       limit=limit, offset=offset, **filters) if in_router or not hist_first else EMPTY
        if not hist_first or old_last < first_day:
            return new
        if limit is None:
            old = billing_history.hotspot_sales(cur, first_day, old_last, limit=None, **filters)
            return {"rows": new["rows"] + old["rows"], "matching": new["matching"] + old["matching"],
                    "matching_total": new["matching_total"] + old["matching_total"]}
        wanted = limit - len(new["rows"])
        old = billing_history.hotspot_sales(cur, first_day, old_last, limit=max(wanted, 0),
                                            offset=max(0, offset - new["matching"]), **filters)
    return {"rows": new["rows"] + old["rows"], "matching": new["matching"] + old["matching"],
            "matching_total": new["matching_total"] + old["matching_total"]}
